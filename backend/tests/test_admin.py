"""Admin-Bereich: Benutzerverwaltung, Firmendaten, Logo, Backups."""
import gzip
from pathlib import Path

import pytest

from app import backup, config


# --------------------------- Benutzerverwaltung ---------------------------
def test_user_endpoints_are_admin_only(user_client):
    assert user_client.get("/api/users").status_code == 403
    assert user_client.post("/api/users", json={"username": "x", "password": "1234"}) \
        .status_code == 403


def test_seeded_users_exist(admin_client):
    names = {u["username"] for u in admin_client.get("/api/users").json()}
    assert {"admin", "tester"} <= names


def test_create_user_and_log_in(client):
    from conftest import ADMIN, do_login
    do_login(client, *ADMIN)
    created = client.post("/api/users",
                          json={"username": "neu", "password": "geheim123"}).json()
    assert created["is_admin"] is False

    client.get("/logout")
    assert do_login(client, "neu", "geheim123").headers["location"] == "/"


def test_duplicate_username_is_rejected(admin_client):
    admin_client.post("/api/users", json={"username": "doppelt", "password": "1234"})
    again = admin_client.post("/api/users", json={"username": "doppelt", "password": "1234"})
    assert again.status_code == 409


def test_short_password_is_rejected(admin_client):
    assert admin_client.post("/api/users",
                             json={"username": "kurz", "password": "12"}).status_code == 422


def test_password_reset(client):
    from conftest import ADMIN, do_login
    do_login(client, *ADMIN)
    user = client.post("/api/users", json={"username": "wechsel", "password": "alt12345"}).json()
    assert client.post(f"/api/users/{user['id']}/password",
                       json={"password": "neu12345"}).status_code == 200

    client.get("/logout")
    assert do_login(client, "wechsel", "alt12345").headers["location"] == "/login?error=1"
    assert do_login(client, "wechsel", "neu12345").headers["location"] == "/"


def test_admin_cannot_delete_themselves(admin_client):
    me = next(u for u in admin_client.get("/api/users").json() if u["username"] == "admin")
    response = admin_client.delete(f"/api/users/{me['id']}")
    assert response.status_code == 400
    assert "selbst" in response.json()["detail"]


def test_last_admin_cannot_be_deleted(admin_client):
    """Ein zweiter Admin darf weg – der letzte nicht."""
    second = admin_client.post("/api/users", json={
        "username": "admin2", "password": "geheim123", "is_admin": True}).json()
    assert admin_client.delete(f"/api/users/{second['id']}").status_code == 204

    users = admin_client.get("/api/users").json()
    other = next(u for u in users if u["username"] == "tester")
    assert admin_client.delete(f"/api/users/{other['id']}").status_code == 204


def test_delete_unknown_user(admin_client):
    assert admin_client.delete("/api/users/999999").status_code == 404


# --------------------------- Firmendaten / Logo ---------------------------
def test_settings_are_readable_for_everyone_but_writable_only_for_admins(user_client):
    assert user_client.get("/api/settings").status_code == 200
    assert user_client.put("/api/settings", json={"company_name": "X"}).status_code == 403


def test_admin_saves_settings(admin_client):
    saved = admin_client.put("/api/settings", json={
        "company_name": "Muster GmbH", "iban": "DE02120300000000202051",
        "email": "info@muster.example"}).json()
    assert saved["company_name"] == "Muster GmbH"
    assert saved["has_logo"] is False
    assert admin_client.get("/api/settings").json()["iban"] == "DE02120300000000202051"


def test_logo_upload_and_download(admin_client):
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
        "1f15c4890000000a49444154789c6360000002000100ffff03000006000557bfabd4"
        "0000000049454e44ae426082")
    upload = admin_client.post("/api/settings/logo",
                               files={"file": ("logo.png", png, "image/png")})
    assert upload.status_code == 200
    assert admin_client.get("/api/settings").json()["has_logo"] is True

    download = admin_client.get("/api/settings/logo")
    assert download.headers["content-type"] == "image/png"
    assert download.content == png


def test_logo_rejects_wrong_type(admin_client):
    response = admin_client.post("/api/settings/logo",
                                 files={"file": ("x.txt", b"kein bild", "text/plain")})
    assert response.status_code == 400


def test_logo_rejects_oversized_file(admin_client):
    big = b"x" * 2_000_001
    response = admin_client.post("/api/settings/logo",
                                 files={"file": ("big.png", big, "image/png")})
    assert response.status_code == 400
    assert "groß" in response.json()["detail"]


def test_missing_logo_returns_404(admin_client):
    assert admin_client.get("/api/settings/logo").status_code == 404


# --------------------------- Backups --------------------------------------
@pytest.fixture
def backup_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BACKUP_DIR", str(tmp_path))
    return tmp_path


def test_backup_listing_is_admin_only(user_client, backup_dir):
    assert user_client.get("/api/admin/backups").status_code == 403


def test_backup_listing(admin_client, backup_dir):
    (backup_dir / "rechnung_20260101_020000.sql.gz").write_bytes(gzip.compress(b"SELECT 1;"))
    files = admin_client.get("/api/admin/backups").json()
    assert [f["name"] for f in files] == ["rechnung_20260101_020000.sql.gz"]
    assert files[0]["size"] > 0


def test_backup_download_is_logged(admin_client, backup_dir):
    name = "rechnung_20260101_020000.sql.gz"
    (backup_dir / name).write_bytes(gzip.compress(b"SELECT 1;"))
    response = admin_client.get(f"/api/admin/backups/{name}/download")
    assert response.status_code == 200
    assert gzip.decompress(response.content) == b"SELECT 1;"
    assert any(e["action"] == "download" and e["detail"] == name
               for e in admin_client.get("/api/audit-log").json())


@pytest.mark.parametrize("bad_name", [
    "../../etc/passwd",
    "..%2Fetc%2Fpasswd",
    "rechnung.sql",             # falsche Endung
    "/etc/passwd",
    "rechnung_$(whoami).sql.gz",
])
def test_path_traversal_is_blocked(admin_client, backup_dir, bad_name):
    response = admin_client.get(f"/api/admin/backups/{bad_name}/download")
    assert response.status_code in (404, 400)
    assert b"root:" not in response.content


def test_backup_resolve_rejects_bad_names(backup_dir):
    for bad in ("../x.sql.gz", "x.sql", "a/b.sql.gz", ""):
        with pytest.raises((ValueError, FileNotFoundError)):
            backup.read_backup(bad)


def test_restore_reports_failure(admin_client, backup_dir, monkeypatch):
    name = "rechnung_20260101_020000.sql.gz"
    (backup_dir / name).write_bytes(gzip.compress(b"SELECT 1;"))

    def failing(*args, **kwargs):
        raise RuntimeError("psql: Verbindung fehlgeschlagen")

    monkeypatch.setattr(backup, "restore_backup", failing)
    response = admin_client.post(f"/api/admin/backups/{name}/restore")
    assert response.status_code == 500
    assert "psql" in response.json()["detail"]
    # Der Versuch wird trotzdem protokolliert
    assert any(e["action"] == "restore_attempt"
               for e in admin_client.get("/api/audit-log").json())


def test_restore_success_path(admin_client, backup_dir, monkeypatch):
    name = "rechnung_20260101_020000.sql.gz"
    (backup_dir / name).write_bytes(gzip.compress(b"SELECT 1;"))
    monkeypatch.setattr(backup, "restore_backup", lambda filename: None)
    assert admin_client.post(f"/api/admin/backups/{name}/restore").json() == {"restored": name}


def test_empty_backup_dir(admin_client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BACKUP_DIR", str(tmp_path / "gibt-es-nicht"))
    assert admin_client.get("/api/admin/backups").json() == []


# --------------------------- Audit-Log ------------------------------------
def test_audit_log_is_admin_only(user_client):
    assert user_client.get("/api/audit-log").status_code == 403


def test_audit_log_pagination(admin_client):
    customer = admin_client.post("/api/customers", json={"name": "Log AG"}).json()
    for _ in range(3):
        admin_client.get(f"/api/customers/{customer['id']}/export")
    page = admin_client.get("/api/audit-log?limit=2")
    assert len(page.json()) == 2
    assert page.headers["X-Total-Count"] == "3"


def test_static_assets_are_served(client):
    for path in ("/static/app.js", "/static/styles.css", "/static/login.js"):
        assert client.get(path).status_code == 200, path


def test_privacy_page_is_public(client):
    response = client.get("/datenschutz")
    assert response.status_code == 200
    assert Path("app/static/datenschutz.html").exists()
