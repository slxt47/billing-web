"""Konfiguration aus Umgebungsvariablen."""
import os


def _parse_users(raw: str) -> dict[str, str]:
    """'anna:pw1,bernd:pw2' -> {'anna': 'pw1', 'bernd': 'pw2'}"""
    users: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        name, pw = pair.split(":", 1)
        users[name.strip()] = pw
    return users


# Wird nur beim ersten Start zum Anlegen der Benutzer in der DB verwendet.
USERS = _parse_users(os.getenv("APP_USERS", "anna:passwort1,bernd:passwort2,clara:passwort3"))
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")

SESSION_SECRET = os.getenv("SESSION_SECRET", "change-me-in-production")

SMTP_HOST = os.getenv("SMTP_HOST", "mailhog")
SMTP_PORT = int(os.getenv("SMTP_PORT", "1025"))
MAIL_FROM = os.getenv("MAIL_FROM", "rechnung@example.com")
# Für echten Versand in Produktion (z.B. über einen SMTP-Provider): Benutzername/
# Passwort setzen. Bleiben sie leer, wird weiter unauthentifiziert an MailHog
# gesendet (nur für lokale Entwicklung geeignet).
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "false").lower() in ("1", "true", "yes")

# Ordner der automatischen täglichen DB-Backups (siehe docker-compose.yml)
BACKUP_DIR = os.getenv("BACKUP_DIR", "/backups")


def _flag(name: str, default: str) -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes", "on")


# --- Sitzung / CSRF -------------------------------------------------------
# SESSION_HTTPS_ONLY setzt das Secure-Flag auf Sitzungs- und CSRF-Cookie.
# Standard ist false, damit auch der direkte HTTP-Zugriff auf
# http://localhost:8000 (docker-compose: WEB_PORT) funktioniert. Sobald die App
# ausschließlich über den HTTPS-Proxy erreichbar ist – so richtet es
# scripts/setup-prod.sh ein – gehört das auf true.
SESSION_HTTPS_ONLY = _flag("SESSION_HTTPS_ONLY", "false")
SESSION_MAX_AGE = int(os.getenv("SESSION_MAX_AGE", str(12 * 3600)))  # 12 Stunden
CSRF_ENABLED = _flag("CSRF_ENABLED", "true")

# --- Rate-Limiting (0 = aus) ---------------------------------------------
RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "600"))
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))
RATE_LIMIT_LOGIN = int(os.getenv("RATE_LIMIT_LOGIN", "20"))
RATE_LIMIT_LOGIN_WINDOW = int(os.getenv("RATE_LIMIT_LOGIN_WINDOW", "300"))

# --- Sicherheits-Header ---------------------------------------------------
HSTS_ENABLED = _flag("HSTS_ENABLED", "true")
HSTS_MAX_AGE = int(os.getenv("HSTS_MAX_AGE", str(180 * 24 * 3600)))

# --- Logging --------------------------------------------------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# --- API-Paginierung ------------------------------------------------------
# Obergrenze für ?limit=… bei Listen-Endpunkten. Ohne limit liefert die API
# weiterhin alle Zeilen (das Frontend filtert/sortiert clientseitig).
MAX_PAGE_SIZE = int(os.getenv("MAX_PAGE_SIZE", "500"))

# --- Monitoring / Alarme --------------------------------------------------
# Kennzahlen (/api/admin/metrics) laufen immer mit; sie kosten nur einen
# Zähler je Request. Alarm-Mails gehen an die Firmen-E-Mail aus den
# Firmendaten und sind standardmäßig aus, damit eine frische Installation
# nicht ungefragt Mails verschickt (scripts/setup-prod.sh schaltet sie ein).
ALERTS_ENABLED = _flag("ALERTS_ENABLED", "false")
# Ab so vielen Serverfehlern (HTTP 5xx) im Beobachtungsfenster gibt es Alarm.
ALERT_ERROR_THRESHOLD = int(os.getenv("ALERT_ERROR_THRESHOLD", "10"))
# Ab so vielen fehlgeschlagenen Anmeldungen im Fenster ebenfalls.
ALERT_LOGIN_THRESHOLD = int(os.getenv("ALERT_LOGIN_THRESHOLD", "20"))
ALERT_WINDOW = int(os.getenv("ALERT_WINDOW", "300"))
# Alter des jüngsten Backups, ab dem gewarnt wird (0 = diese Prüfung aus).
ALERT_BACKUP_MAX_AGE_HOURS = int(os.getenv("ALERT_BACKUP_MAX_AGE_HOURS", "36"))
# Dieselbe Art Alarm höchstens einmal pro Sperrfrist – kein Mailsturm.
ALERT_COOLDOWN = int(os.getenv("ALERT_COOLDOWN", "3600"))
# Antwortzeit, ab der ein Request als langsam gezählt wird (Sekunden).
SLOW_REQUEST_SECONDS = float(os.getenv("SLOW_REQUEST_SECONDS", "2"))
