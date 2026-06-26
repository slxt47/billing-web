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
