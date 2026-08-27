"""Gemeinsame Test-Fixtures.

Die Tests laufen gegen eine frische SQLite-Datei statt gegen PostgreSQL,
damit sie ohne laufende Container (und damit auch in der CI) durchlaufen.
Die einzigen PostgreSQL-spezifischen Stellen der App – der Advisory-Lock für
die Belegnummern und die ADD-COLUMN-Migrationen – sind dafür in crud.py bzw.
database.py an den Dialekt gebunden.

Wichtig: die Umgebungsvariablen müssen gesetzt sein, BEVOR app.config und
app.database importiert werden – deshalb stehen sie hier ganz oben.
"""
import os
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

_TMPDIR = tempfile.mkdtemp(prefix="rechnung-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMPDIR}/test.db"
os.environ["BACKUP_DIR"] = f"{_TMPDIR}/backups"
os.environ["SESSION_SECRET"] = "test-secret-not-used-in-production"
os.environ["SESSION_HTTPS_ONLY"] = "false"   # TestClient spricht http
os.environ["ADMIN_USER"] = "admin"
os.environ["ADMIN_PASSWORD"] = "admin-pw"
os.environ["APP_USERS"] = "tester:tester-pw"
os.environ["RATE_LIMIT_REQUESTS"] = "0"      # Rate-Limit im Test standardmäßig aus
os.environ["RATE_LIMIT_LOGIN"] = "0"
os.environ["LOG_LEVEL"] = "CRITICAL"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import auth, security  # noqa: E402
from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402

ADMIN = ("admin", "admin-pw")
USER = ("tester", "tester-pw")


@pytest.fixture
def client():
    """Leere Datenbank + frischer TestClient (startup legt die Benutzer an)."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    security.reset_rate_limits()
    auth._fails.clear()
    with TestClient(app) as test_client:
        yield test_client


def do_login(client, username: str, password: str):
    """Login inkl. CSRF-Token aus dem Cookie – wie es das Formular tut."""
    client.get("/login")
    token = client.cookies.get(security.CSRF_COOKIE)
    response = client.post(
        "/login",
        data={"username": username, "password": password, "csrf_token": token},
        follow_redirects=False,
    )
    # Nach erfolgreichem Login gilt ein neues Token – für die API-Aufrufe merken
    fresh = client.cookies.get(security.CSRF_COOKIE)
    if fresh:
        client.headers[security.CSRF_HEADER] = fresh
    return response


@pytest.fixture
def user_client(client):
    do_login(client, *USER)
    return client


@pytest.fixture
def admin_client(client):
    do_login(client, *ADMIN)
    return client


def make_invoice(client, **overrides):
    payload = {
        "customer_name": "Muster GmbH",
        "customer_address": "Musterweg 1\n12345 Musterstadt",
        "tax_rate": 20,
        "items": [{"description": "Beratung", "quantity": 2, "unit_price": 100}],
    }
    payload.update(overrides)
    response = client.post("/api/invoices", json=payload)
    assert response.status_code == 201, response.text
    return response.json()
