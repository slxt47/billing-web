"""Sicherheits-Middleware: CSRF-Schutz, Rate-Limiting, Response-Header.

Ergänzt den bestehenden Login-Schutz (auth.py) um die drei Bausteine, die
eine öffentlich erreichbare Instanz braucht:

* CSRF: pro Sitzung ein Token, das bei jedem schreibenden Request wieder
  mitgeschickt werden muss (Header ``X-CSRF-Token`` bzw. Formularfeld
  ``csrf_token`` beim Login).
* Rate-Limiting: einfache gleitende Zählung je IP – schützt die API vor
  Massen-Requests, unabhängig von der Login-Sperre in auth.py.
* Sicherheits-Header: CSP, HSTS, X-Frame-Options & Co. auf jeder Antwort.

Der Zustand (Zählerfenster) liegt bewusst im Prozess-Speicher: die App läuft
laut docker-compose.yml als eine einzelne `web`-Instanz. Bei mehreren Repliken
müsste das – wie die Login-Sperre – in Redis o. ä. wandern.
"""
import hmac
import secrets
import time
from collections import deque

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from . import config

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

# Der Login ist vom Middleware-Check ausgenommen: dort steckt das Token im
# Formular und wird direkt im Endpoint geprüft (siehe main.login).
CSRF_EXEMPT = ("/login", "/health")

CSRF_SESSION_KEY = "csrf"
CSRF_COOKIE = "csrftoken"
CSRF_HEADER = "x-csrf-token"


# --------------------------- CSRF ---------------------------------------
def ensure_csrf_token(request: Request) -> str:
    """Liefert das Token der Sitzung und legt es beim ersten Aufruf an."""
    token = request.session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[CSRF_SESSION_KEY] = token
    return token


def csrf_token_valid(request: Request, candidate: str | None) -> bool:
    expected = request.session.get(CSRF_SESSION_KEY)
    if not expected or not candidate:
        return False
    return hmac.compare_digest(str(expected), str(candidate))


def _csrf_exempt(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in CSRF_EXEMPT)


async def csrf_middleware(request: Request, call_next):
    """Double-Submit mit Sitzungsbindung: das Token liegt in der (signierten)
    Session und muss bei schreibenden Requests im Header wiederkommen."""
    # Statische Dateien brauchen weder Token noch Sitzung – sonst bekäme
    # jeder anonyme Abruf eines Stylesheets ein Session-Cookie.
    if not config.CSRF_ENABLED or request.url.path.startswith("/static"):
        return await call_next(request)

    token = ensure_csrf_token(request)
    if request.method not in SAFE_METHODS and not _csrf_exempt(request.url.path):
        if not csrf_token_valid(request, request.headers.get(CSRF_HEADER)):
            return _plain_error(
                request, 403,
                "CSRF-Token fehlt oder ist ungültig. Bitte die Seite neu laden.")

    response = await call_next(request)
    # Nach dem Login legt der Endpoint eine frische Sitzung an – deshalb hier
    # den aktuellen Stand lesen und nicht das Token von oben.
    # Das Cookie ist bewusst nicht httpOnly: das Frontend liest es aus, um den
    # Header zu setzen. Der Wert allein nützt einem Angreifer nichts, weil er
    # wegen SameSite/Same-Origin nicht an ihn herankommt.
    response.set_cookie(
        CSRF_COOKIE, request.session.get(CSRF_SESSION_KEY) or token,
        samesite="strict", secure=config.SESSION_HTTPS_ONLY,
        httponly=False, path="/",
    )
    return response


# --------------------------- Rate-Limiting -------------------------------
_hits: dict[str, deque] = {}


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "?"


def _too_many(bucket: str, limit: int, window: int) -> int:
    """Registriert einen Treffer. Rückgabe: Wartezeit in Sekunden (0 = ok)."""
    if limit <= 0:
        return 0
    now = time.monotonic()
    hits = _hits.setdefault(bucket, deque())
    while hits and now - hits[0] > window:
        hits.popleft()
    if len(hits) >= limit:
        return max(1, int(window - (now - hits[0])))
    hits.append(now)
    return 0


def reset_rate_limits() -> None:
    """Nur für Tests / Neustart-Situationen."""
    _hits.clear()


async def rate_limit_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith("/static") or path == "/health":
        return await call_next(request)

    ip = _client_ip(request)
    if path == "/login" and request.method == "POST":
        wait = _too_many(f"login:{ip}", config.RATE_LIMIT_LOGIN,
                         config.RATE_LIMIT_LOGIN_WINDOW)
    else:
        wait = _too_many(f"all:{ip}", config.RATE_LIMIT_REQUESTS,
                         config.RATE_LIMIT_WINDOW)
    if wait:
        resp = _plain_error(request, 429,
                            "Zu viele Anfragen. Bitte kurz warten.")
        resp.headers["Retry-After"] = str(wait)
        return resp
    return await call_next(request)


# --------------------------- Sicherheits-Header --------------------------
_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "object-src 'none'"
)


def _is_https(request: Request) -> bool:
    return (request.headers.get("x-forwarded-proto", request.url.scheme)
            .split(",")[0].strip() == "https")


def apply_security_headers(request: Request, response: Response) -> Response:
    response.headers.setdefault("Content-Security-Policy", _CSP)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if config.HSTS_ENABLED and _is_https(request):
        response.headers.setdefault(
            "Strict-Transport-Security",
            f"max-age={config.HSTS_MAX_AGE}; includeSubDomains")
    return response


async def security_headers_middleware(request: Request, call_next):
    return apply_security_headers(request, await call_next(request))


def _plain_error(request: Request, status: int, detail: str) -> Response:
    return apply_security_headers(request, JSONResponse({"detail": detail},
                                                        status_code=status))
