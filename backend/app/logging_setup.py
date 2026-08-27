"""Zentrales Logging und einheitliche Fehlerantworten.

Vorher hat sich die App auf die Standardausgabe von uvicorn/FastAPI verlassen:
Tracebacks landeten unformatiert im Container-Log, und ein Fehler war im
Nachhinein keiner konkreten Anfrage zuzuordnen.

Hier passiert dreierlei:

* JSON-Zeilen statt Freitext, damit `docker compose logs` maschinell
  auswertbar ist (jede Zeile ein Objekt).
* Jede Anfrage bekommt eine Request-ID (Header ``X-Request-ID`` – vorhandene
  Werte vom Reverse-Proxy werden übernommen). Sie steht in jeder Logzeile und
  in jeder Fehlerantwort, sodass ein Nutzer-Screenshot direkt zum Log führt.
* Alle Fehler kommen als ``{"detail": ..., "request_id": ...}`` zurück – auch
  Validierungsfehler, die FastAPI sonst als verschachtelte Liste ausliefert,
  mit der das Frontend nichts anfangen kann.
"""
import json
import logging
import time
import uuid
from contextvars import ContextVar

from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import config, security

_request_id: ContextVar[str] = ContextVar("request_id", default="-")

log = logging.getLogger("rechnung")

_SKIP_ACCESS_LOG = ("/static", "/health", "/favicon.ico")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": _request_id.get(),
            "message": record.getMessage(),
        }
        extra = getattr(record, "fields", None)
        if extra:
            payload.update(extra)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(config.LOG_LEVEL)
    # uvicorns eigener Access-Log doppelt unseren – wir loggen selbst mehr.
    logging.getLogger("uvicorn.access").disabled = True
    # Die uvicorn-Logger bekommen keinen eigenen Handler, sonst erscheint
    # jede Zeile doppelt (einmal dort, einmal über den Root-Logger).
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True


def current_request_id() -> str:
    return _request_id.get()


async def request_context_middleware(request: Request, call_next):
    """Vergibt die Request-ID und schreibt eine Logzeile je Anfrage."""
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    token = _request_id.set(rid)
    started = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        log.exception("unhandled error", extra={"fields": {
            "method": request.method, "path": request.url.path}})
        raise
    finally:
        _request_id.reset(token)

    took_ms = round((time.monotonic() - started) * 1000, 1)
    response.headers["X-Request-ID"] = rid
    if not request.url.path.startswith(_SKIP_ACCESS_LOG):
        log_at = logging.WARNING if response.status_code >= 400 else logging.INFO
        log.log(log_at, "request", extra={"fields": {
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "duration_ms": took_ms,
            "user": (request.scope.get("session") or {}).get("user"),
            "request_id": rid,
        }})
    return response


# --------------------------- Fehler-Handler ------------------------------
def _error(request: Request, status: int, detail) -> JSONResponse:
    body = {"detail": detail, "request_id": _request_id.get()}
    return security.apply_security_headers(
        request, JSONResponse(body, status_code=status))


async def http_exception_handler(request: Request, exc: HTTPException):
    response = _error(request, exc.status_code, exc.detail)
    for key, value in (getattr(exc, "headers", None) or {}).items():
        response.headers[key] = value
    return response


async def validation_exception_handler(request: Request,
                                       exc: RequestValidationError):
    """422 in einen für das Frontend lesbaren Satz übersetzen."""
    parts = []
    for err in exc.errors():
        field = ".".join(str(p) for p in err.get("loc", ()) if p != "body")
        parts.append(f"{field}: {err.get('msg', '')}" if field
                     else str(err.get("msg", "")))
    detail = "Ungültige Eingabe – " + "; ".join(parts) if parts else "Ungültige Eingabe"
    log.warning("validation error", extra={"fields": {
        "path": request.url.path, "errors": exc.errors()}})
    return _error(request, 422, detail)


async def unhandled_exception_handler(request: Request, exc: Exception):
    log.exception("unhandled error", extra={"fields": {
        "method": request.method, "path": request.url.path}})
    return _error(request, 500,
                  "Interner Serverfehler. Bitte die Request-ID an den "
                  "Administrator melden.")
