"""Anmeldung & Passwörter.

Benutzer liegen in der Datenbank (Tabelle `users`). Passwörter werden mit
PBKDF2-HMAC-SHA256 gehasht (nur Standardbibliothek, keine Extra-Abhängigkeit).
Alle Benutzer greifen auf dieselben Daten zu – der Login dient als Zugangsschutz,
Admins dürfen zusätzlich Benutzer verwalten.
"""
import base64
import hashlib
import hmac
import os
import time

from starlette.requests import Request

# Pfade, die ohne Anmeldung erreichbar sind
PUBLIC_PREFIXES = ("/login", "/logout", "/static", "/health", "/favicon")

_ITERATIONS = 200_000

# --- Brute-Force-Schutz: Login-Sperre nach zu vielen Fehlversuchen --------
_MAX_FAILS = 5
_LOCK_SECONDS = 300  # 5 Minuten
_fails: dict[str, list] = {}  # key -> [fail_count, locked_until_ts]


def is_locked(key: str) -> int:
    """Sekunden bis zur Entsperrung (0 = nicht gesperrt)."""
    entry = _fails.get(key)
    if entry and entry[1] > time.time():
        return int(entry[1] - time.time())
    return 0


def register_failure(key: str) -> None:
    entry = _fails.get(key, [0, 0.0])
    entry[0] += 1
    if entry[0] >= _MAX_FAILS:
        entry[1] = time.time() + _LOCK_SECONDS
        entry[0] = 0
    _fails[key] = entry


def reset_failures(key: str) -> None:
    _fails.pop(key, None)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        _ITERATIONS,
        base64.b64encode(salt).decode(),
        base64.b64encode(dk).decode(),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_b64, hash_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iters))
        return hmac.compare_digest(dk, expected)
    except (ValueError, TypeError):
        return False


def current_user(request: Request) -> str | None:
    return request.session.get("user")


def is_public(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") or path.startswith(p)
               for p in PUBLIC_PREFIXES)
