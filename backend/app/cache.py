"""Response-Cache für die wenigen rechenintensiven, oft abgefragten
GET-Endpunkte (Dashboard-Kennzahlen, Auswertungen): statt bei jedem Klick auf
„Aktualisieren" neu aus der Datenbank zu rechnen, kommt innerhalb von
CACHE_TTL_SECONDS dieselbe Antwort noch einmal aus dem Prozessspeicher.

Wie Login-Sperre, Rate-Limit und Monitoring liegt der Zustand im Speicher des
einen `web`-Containers, den docker-compose.yml startet (siehe TODO.md,
horizontale Skalierung) – bei mehreren Repliken bräuchte er wie die anderen
einen gemeinsamen Speicher (Redis o. Ä.).

Gecacht werden nur /api/stats und alles unter /api/reports/ (siehe
_CACHEABLE_EXACT/_CACHEABLE_PREFIX). Belegs- und Stammdatenlisten bleiben
bewusst außen vor: dort arbeiten mehrere Benutzer live mit (Anwesenheitsanzeige,
Bearbeitungssperre), und eine auch nur kurz veraltete Antwort stört dort mehr,
als die paar Millisekunden Rechenzeit einer einfachen Datenbankabfrage
einbringen.

Jeder erfolgreiche schreibende Request auf einen Pfad, der in diese Kennzahlen
einfließt – Rechnungen, Gutschriften, eine Backup-Wiederherstellung – leert
den gesamten Cache (_invalidating). Bei der Handvoll Einträge, die hier je
zustande kommen, ist das einfacher und sicherer als eine feingranulare
Invalidierung je Auswertung: niemand soll nach dem Anlegen einer Rechnung erst
CACHE_TTL_SECONDS lang den alten Umsatz sehen.
"""
import time
from collections import OrderedDict

from starlette.requests import Request
from starlette.responses import Response

from . import config

_CACHEABLE_EXACT = {"/api/stats"}
_CACHEABLE_PREFIX = "/api/reports/"

# Diese Pfadpräfixe hängen (direkt oder über die Kennzahlen, die sie
# verändern) an dem, was gecacht wird.
_INVALIDATES_PREFIXES = ("/api/invoices", "/api/credit-notes", "/api/admin/backups")
# Ausnahme innerhalb dieser Präfixe: die Bearbeitungssperre auf Rechnungen
# ändert nichts an Umsatz oder Steuerlast, ihr Heartbeat (alle 90 s, solange
# jemand eine Rechnung offen hat) würde den Cache sonst ständig neu leeren.
_INVALIDATES_EXEMPT_SUFFIXES = ("/lock",)

_MAX_ENTRIES = 200  # Sicherheitsnetz gegen unbegrenztes Wachstum, falls doch
                    # einmal viele verschiedene Zeiträume abgefragt werden.

# Schlüssel (Pfad + Query) -> (Ablaufzeit, Status, Rohkörper, Content-Type,
# zusätzliche Header als Tupel-Paare – z. B. Content-Disposition der CSVs).
_store: "OrderedDict[str, tuple[float, int, bytes, str, tuple]]" = OrderedDict()


def _cacheable(path: str) -> bool:
    return path in _CACHEABLE_EXACT or path.startswith(_CACHEABLE_PREFIX)


def _invalidating(path: str) -> bool:
    if path.endswith(_INVALIDATES_EXEMPT_SUFFIXES):
        return False
    return any(path.startswith(p) for p in _INVALIDATES_PREFIXES)


def _key(request: Request) -> str:
    return f"{request.url.path}?{request.url.query}"


def reset() -> None:
    """Kompletten Cache leeren – bei jedem relevanten Schreibzugriff und in Tests
    (jeder Test startet mit einer frischen Datenbank, siehe conftest.py)."""
    _store.clear()


async def response_cache_middleware(request: Request, call_next):
    path = request.url.path

    if not config.CACHE_ENABLED or request.method != "GET" or not _cacheable(path):
        response = await call_next(request)
        if (request.method != "GET" and response.status_code < 400
                and _invalidating(path)):
            reset()
        return response

    key = _key(request)
    hit = _store.get(key)
    if hit and hit[0] > time.monotonic():
        _, status, body, media_type, headers = hit
        response = Response(content=body, status_code=status, media_type=media_type,
                            headers=dict(headers))
        response.headers["X-Cache"] = "HIT"
        return response

    response = await call_next(request)
    # call_next liefert innerhalb einer @app.middleware("http")-Funktion keine
    # normale Response, sondern Starlettes eigene _StreamingResponse (der
    # eigentliche Körper ist schon durchs ASGI-Protokoll gelaufen) – ohne
    # .body, nur über body_iterator einsammelbar. Also selbst neu bauen.
    body = b"".join([section async for section in response.body_iterator])
    headers = {k: v for k, v in response.headers.items()
              if k.lower() not in ("content-length", "x-cache")}
    if response.status_code < 400 and config.CACHE_TTL_SECONDS > 0:
        _store[key] = (time.monotonic() + config.CACHE_TTL_SECONDS, response.status_code,
                      body, response.media_type, tuple(headers.items()))
        _store.move_to_end(key)
        while len(_store) > _MAX_ENTRIES:
            _store.popitem(last=False)  # ältesten Eintrag verdrängen
    fresh = Response(content=body, status_code=response.status_code,
                     media_type=response.media_type, headers=headers)
    fresh.headers["X-Cache"] = "MISS"
    return fresh
