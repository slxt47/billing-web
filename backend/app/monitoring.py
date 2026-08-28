"""Monitoring: Kennzahlen im Prozess sammeln und bei Auffälligkeiten warnen.

Bewusst ohne Zusatzdienst: die Zähler liegen im Speicher des einen
`web`-Containers, den docker-compose.yml startet (dieselbe Einschränkung wie
bei Login-Sperre und Rate-Limit – siehe TODO.md, horizontale Skalierung).
Ausgelesen werden sie über /api/admin/metrics (JSON, nur Admins) und
/api/admin/metrics.prom (Prometheus-Textformat, ebenfalls nur Admins).

Alarme gehen per E-Mail an die Firmenadresse aus den Firmendaten, gedrosselt
über eine Sperrfrist je Alarmart, und sind über ALERTS_ENABLED schaltbar.
"""
import threading
import time
from collections import deque
from datetime import datetime

from . import config
from .logging_setup import log

# Zeitpunkt des Prozessstarts – daraus wird die Laufzeit berechnet.
_started_at = time.time()
_lock = threading.Lock()

_counts = {"requests": 0, "2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0, "slow": 0}
_duration_sum = 0.0
_duration_max = 0.0

# Zeitstempel der letzten Serverfehler bzw. Fehlanmeldungen für die
# Alarmprüfung; älteres als ALERT_WINDOW fliegt beim Zählen raus.
_error_times: deque[float] = deque(maxlen=1000)
_login_fail_times: deque[float] = deque(maxlen=1000)

# Die letzten Fehler für die Monitoring-Ansicht (klein halten!).
_recent_errors: deque[dict] = deque(maxlen=20)

# Wann welche Alarmart zuletzt gemeldet wurde.
_alert_sent: dict[str, float] = {}
_last_alert_check = 0.0


def reset() -> None:
    """Alle Zähler zurücksetzen (Tests)."""
    global _started_at, _duration_sum, _duration_max, _last_alert_check
    with _lock:
        _started_at = time.time()
        for key in _counts:
            _counts[key] = 0
        _duration_sum = _duration_max = 0.0
        _error_times.clear()
        _login_fail_times.clear()
        _recent_errors.clear()
        _alert_sent.clear()
        _last_alert_check = 0.0


def record_request(method: str, path: str, status: int, seconds: float,
                   request_id: str = "") -> None:
    global _duration_sum, _duration_max
    with _lock:
        _counts["requests"] += 1
        _counts[f"{status // 100}xx"] = _counts.get(f"{status // 100}xx", 0) + 1
        _duration_sum += seconds
        _duration_max = max(_duration_max, seconds)
        if seconds >= config.SLOW_REQUEST_SECONDS:
            _counts["slow"] += 1
        if status >= 500:
            _error_times.append(time.time())
            _recent_errors.append({
                "at": datetime.utcnow().isoformat(timespec="seconds"),
                "method": method, "path": path, "status": status,
                "request_id": request_id,
            })


def record_login_failure(username: str) -> None:
    with _lock:
        _login_fail_times.append(time.time())


def _within_window(stamps: deque) -> int:
    cutoff = time.time() - config.ALERT_WINDOW
    return sum(1 for t in stamps if t >= cutoff)


def _backup_age_hours() -> float | None:
    """Alter des jüngsten Backups in Stunden, oder None wenn es keines gibt."""
    from . import backup  # spät, damit monitoring importierbar bleibt
    try:
        files = backup.list_backups()
    except OSError:
        return None
    if not files:
        return None
    newest = max(f["modified"] for f in files)
    return round((datetime.now() - newest).total_seconds() / 3600, 2)


def snapshot(db=None) -> dict:
    """Aktuelle Kennzahlen. Mit `db` zusätzlich die Bestandszahlen."""
    with _lock:
        counts = dict(_counts)
        duration_sum, duration_max = _duration_sum, _duration_max
        errors_in_window = _within_window(_error_times)
        logins_in_window = _within_window(_login_fail_times)
        recent = list(_recent_errors)
        alerts_sent = {k: datetime.utcfromtimestamp(v).isoformat(timespec="seconds")
                       for k, v in _alert_sent.items()}
        uptime = time.time() - _started_at

    requests = counts["requests"] or 1
    data = {
        "uptime_seconds": round(uptime, 1),
        "requests_total": counts["requests"],
        "responses": {k: counts[k] for k in ("2xx", "3xx", "4xx", "5xx")},
        "server_errors_total": counts["5xx"],
        "error_rate": round(counts["5xx"] / requests, 4),
        "slow_requests_total": counts["slow"],
        "avg_response_seconds": round(duration_sum / requests, 4),
        "max_response_seconds": round(duration_max, 4),
        "errors_in_window": errors_in_window,
        "failed_logins_in_window": logins_in_window,
        "alert_window_seconds": config.ALERT_WINDOW,
        "recent_errors": recent,
        "backup_age_hours": _backup_age_hours(),
        "alerts": {
            "enabled": config.ALERTS_ENABLED,
            "error_threshold": config.ALERT_ERROR_THRESHOLD,
            "login_threshold": config.ALERT_LOGIN_THRESHOLD,
            "backup_max_age_hours": config.ALERT_BACKUP_MAX_AGE_HOURS,
            "cooldown_seconds": config.ALERT_COOLDOWN,
            "last_sent": alerts_sent,
        },
    }
    if db is not None:
        from . import models
        data["documents"] = {
            "invoices": db.query(models.Invoice).count(),
            "quotes": db.query(models.Quote).count(),
            "delivery_notes": db.query(models.DeliveryNote).count(),
            "credit_notes": db.query(models.CreditNote).count(),
            "customers": db.query(models.Customer).count(),
            "users": db.query(models.User).count(),
        }
    return data


def prometheus(data: dict) -> str:
    """Kennzahlen im Prometheus-Textformat (für einen Scraper)."""
    lines = [
        "# HELP rechnung_uptime_seconds Laufzeit des Prozesses",
        "# TYPE rechnung_uptime_seconds gauge",
        f"rechnung_uptime_seconds {data['uptime_seconds']}",
        "# HELP rechnung_requests_total Beantwortete Requests",
        "# TYPE rechnung_requests_total counter",
        f"rechnung_requests_total {data['requests_total']}",
        "# HELP rechnung_responses_total Antworten je Statusklasse",
        "# TYPE rechnung_responses_total counter",
    ]
    for klass, value in data["responses"].items():
        lines.append(f'rechnung_responses_total{{class="{klass}"}} {value}')
    lines += [
        "# HELP rechnung_response_seconds_avg Mittlere Antwortzeit",
        "# TYPE rechnung_response_seconds_avg gauge",
        f"rechnung_response_seconds_avg {data['avg_response_seconds']}",
        "# HELP rechnung_slow_requests_total Requests über der Schwelle",
        "# TYPE rechnung_slow_requests_total counter",
        f"rechnung_slow_requests_total {data['slow_requests_total']}",
        "# HELP rechnung_failed_logins Fehlanmeldungen im Beobachtungsfenster",
        "# TYPE rechnung_failed_logins gauge",
        f"rechnung_failed_logins {data['failed_logins_in_window']}",
    ]
    if data.get("backup_age_hours") is not None:
        lines += [
            "# HELP rechnung_backup_age_hours Alter des jüngsten Backups",
            "# TYPE rechnung_backup_age_hours gauge",
            f"rechnung_backup_age_hours {data['backup_age_hours']}",
        ]
    for name, value in (data.get("documents") or {}).items():
        lines.append(f'rechnung_documents{{kind="{name}"}} {value}')
    return "\n".join(lines) + "\n"


# --------------------------- Alarme --------------------------------------
def pending_alerts() -> list[tuple[str, str]]:
    """Offene Alarme als (Art, Text). Reine Auswertung, ohne Versand."""
    alerts = []
    errors = _within_window(_error_times)
    if config.ALERT_ERROR_THRESHOLD and errors >= config.ALERT_ERROR_THRESHOLD:
        alerts.append(("errors",
                       f"{errors} Serverfehler (HTTP 5xx) in den letzten "
                       f"{config.ALERT_WINDOW // 60} Minuten."))
    logins = _within_window(_login_fail_times)
    if config.ALERT_LOGIN_THRESHOLD and logins >= config.ALERT_LOGIN_THRESHOLD:
        alerts.append(("logins",
                       f"{logins} fehlgeschlagene Anmeldungen in den letzten "
                       f"{config.ALERT_WINDOW // 60} Minuten – möglicher "
                       f"Angriffsversuch."))
    if config.ALERT_BACKUP_MAX_AGE_HOURS:
        age = _backup_age_hours()
        if age is None:
            alerts.append(("backup", "Es liegt kein Datenbank-Backup vor."))
        elif age > config.ALERT_BACKUP_MAX_AGE_HOURS:
            alerts.append(("backup",
                           f"Das jüngste Backup ist {age:.0f} Stunden alt "
                           f"(erlaubt: {config.ALERT_BACKUP_MAX_AGE_HOURS})."))
    return alerts


def _due(kind: str) -> bool:
    """Sperrfrist je Alarmart, damit aus einem Vorfall kein Mailsturm wird."""
    last = _alert_sent.get(kind, 0)
    return time.time() - last >= config.ALERT_COOLDOWN


def check_and_notify(db) -> list[str]:
    """Offene Alarme prüfen und – falls fällig – per E-Mail melden.
    Rückgabe: die Arten, zu denen eine Mail rausging."""
    if not config.ALERTS_ENABLED:
        return []
    from . import crud, email_service
    settings = crud.get_settings(db)
    to = (settings.email or "").strip() if settings else ""
    sent = []
    for kind, text in pending_alerts():
        if not _due(kind):
            continue
        with _lock:
            _alert_sent[kind] = time.time()
        log.warning("alert", extra={"fields": {"kind": kind, "text": text}})
        if not to:
            continue  # ohne Firmen-E-Mail bleibt nur der Logeintrag
        try:
            email_service.send_alert_email(to, kind, text, settings)
            sent.append(kind)
        except OSError as err:
            # Ein toter SMTP-Server darf den laufenden Request nicht stören.
            log.warning("alert mail failed", extra={"fields": {"error": str(err)}})
    return sent


def maybe_check_alerts(session_factory) -> None:
    """Wird aus der Middleware aufgerufen: prüft höchstens einmal pro Minute
    und niemals, wenn Alarme abgeschaltet sind."""
    global _last_alert_check
    if not config.ALERTS_ENABLED:
        return
    now = time.time()
    with _lock:
        if now - _last_alert_check < 60:
            return
        _last_alert_check = now
    if not pending_alerts():
        return
    db = session_factory()
    try:
        check_and_notify(db)
    finally:
        db.close()


async def metrics_middleware(request, call_next):
    """Zählt jeden Request mit und stößt (gedrosselt) die Alarmprüfung an."""
    from .database import SessionLocal

    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        record_request(request.method, request.url.path, 500,
                       time.perf_counter() - started)
        raise
    record_request(request.method, request.url.path, response.status_code,
                   time.perf_counter() - started,
                   response.headers.get("X-Request-ID", ""))
    maybe_check_alerts(SessionLocal)
    return response
