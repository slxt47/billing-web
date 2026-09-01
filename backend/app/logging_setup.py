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
from logging.handlers import RotatingFileHandler
from pathlib import Path

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


# Stufen, die sich in der Weboberfläche umschalten lassen. CRITICAL fehlt
# bewusst: eine App, die nur noch Abstürze meldet, ist keine sinnvolle
# Einstellung für den laufenden Betrieb (die Testsuite setzt sie über
# LOG_LEVEL trotzdem, deshalb prüft nur die API gegen diese Liste).
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


def _file_handler() -> logging.Handler | None:
    """Zweites Ziel neben der Standardausgabe: eine Datei unter LOG_DIR.

    Nur wenn LOG_DIR gesetzt ist (docker-compose hängt dort ein Verzeichnis
    vom Host ein). Lässt sich nicht hineinschreiben – fehlende Rechte auf dem
    eingehängten Verzeichnis sind der wahrscheinlichste Fall –, läuft die App
    weiter und meldet es auf der Standardausgabe. Ein nicht beschreibbares
    Log-Verzeichnis darf den Dienst nicht aufhalten.
    """
    if not config.LOG_DIR:
        return None
    try:
        path = Path(config.LOG_DIR)
        path.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path / "app.log", maxBytes=config.LOG_MAX_BYTES,
            backupCount=config.LOG_BACKUP_COUNT, encoding="utf-8")
    except OSError as err:
        print(json.dumps({"level": "WARNING", "logger": "rechnung",
                          "message": "log directory not writable",
                          "log_dir": config.LOG_DIR, "error": str(err)}),
              flush=True)
        return None
    handler.setFormatter(JsonFormatter())
    return handler


def setup_logging() -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    file_handler = _file_handler()
    if file_handler:
        handlers.append(file_handler)
    for handler in handlers:
        handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = handlers
    root.setLevel(config.LOG_LEVEL)
    # uvicorns eigener Access-Log doppelt unseren – wir loggen selbst mehr.
    logging.getLogger("uvicorn.access").disabled = True
    # Die uvicorn-Logger bekommen keinen eigenen Handler, sonst erscheint
    # jede Zeile doppelt (einmal dort, einmal über den Root-Logger).
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True


def current_level() -> str:
    """Stufe, mit der die App gerade läuft – als Name, nicht als Zahl."""
    return logging.getLevelName(logging.getLogger().level)


def set_level(level: str) -> str:
    """Stufe der laufenden App umstellen. Wirkt sofort, ohne Neustart.

    Gibt die gesetzte Stufe zurück; unbekannte Namen lösen ValueError aus,
    damit die API mit 400 antworten kann statt still nichts zu tun.
    """
    name = (level or "").strip().upper()
    if name not in LEVELS:
        raise ValueError(f"Unbekannte Log-Stufe: {level}")
    logging.getLogger().setLevel(name)
    return name


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
