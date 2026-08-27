"""CSRF-Schutz, Sicherheits-Header, Rate-Limit, Passwort-Hashing, Login-Sperre."""
import importlib

import pytest

from app import auth, config, security
from conftest import USER, do_login


# --------------------------- CSRF ---------------------------------------
def test_write_without_csrf_header_is_rejected(user_client):
    user_client.headers.pop(security.CSRF_HEADER, None)
    response = user_client.post("/api/customers", json={"name": "Ohne Token"})
    assert response.status_code == 403
    assert "CSRF" in response.json()["detail"]


def test_write_with_wrong_csrf_header_is_rejected(user_client):
    user_client.headers[security.CSRF_HEADER] = "voellig-falsches-token"
    response = user_client.post("/api/customers", json={"name": "Falsches Token"})
    assert response.status_code == 403


def test_write_with_valid_csrf_header_passes(user_client):
    assert user_client.post("/api/customers", json={"name": "Mit Token"}).status_code == 201


def test_read_needs_no_csrf_token(user_client):
    user_client.headers.pop(security.CSRF_HEADER, None)
    assert user_client.get("/api/customers").status_code == 200


def test_login_without_csrf_token_is_rejected(client):
    client.get("/login")
    response = client.post("/login", data={"username": USER[0], "password": USER[1]},
                           follow_redirects=False)
    assert response.headers["location"] == "/login?csrf=1"
    assert client.get("/api/invoices").status_code == 401


def test_csrf_token_changes_after_login(client):
    client.get("/login")
    before = client.cookies.get(security.CSRF_COOKIE)
    do_login(client, *USER)
    assert client.cookies.get(security.CSRF_COOKIE) != before


# --------------------------- Header --------------------------------------
@pytest.mark.parametrize("header,expected", [
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
])
def test_security_headers_present(client, header, expected):
    assert client.get("/login").headers[header] == expected


def test_csp_forbids_inline_scripts(client):
    csp = client.get("/login").headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp
    assert "unsafe-inline" not in csp
    assert "frame-ancestors 'none'" in csp


def test_hsts_only_on_https(client):
    assert "Strict-Transport-Security" not in client.get("/login").headers
    forwarded = client.get("/login", headers={"X-Forwarded-Proto": "https"})
    assert "max-age=" in forwarded.headers["Strict-Transport-Security"]


def test_every_response_carries_a_request_id(client):
    assert client.get("/health").headers["X-Request-ID"]


def test_request_id_from_proxy_is_kept(client):
    response = client.get("/health", headers={"X-Request-ID": "abc123"})
    assert response.headers["X-Request-ID"] == "abc123"


# --------------------------- Rate-Limit ----------------------------------
def test_rate_limit_blocks_after_the_configured_number(client, monkeypatch):
    security.reset_rate_limits()
    monkeypatch.setattr(config, "RATE_LIMIT_REQUESTS", 3)
    monkeypatch.setattr(config, "RATE_LIMIT_WINDOW", 60)

    for _ in range(3):
        assert client.get("/api/me").status_code in (200, 401)
    blocked = client.get("/api/me")
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) >= 1


def test_rate_limit_ignores_static_and_health(client, monkeypatch):
    security.reset_rate_limits()
    monkeypatch.setattr(config, "RATE_LIMIT_REQUESTS", 1)
    for _ in range(5):
        assert client.get("/health").status_code == 200


# --------------------------- Passwörter / Sperre --------------------------
def test_password_hash_is_salted_and_verifiable():
    first = auth.hash_password("geheim")
    second = auth.hash_password("geheim")
    assert first != second                       # eigener Salt je Hash
    assert first.startswith("pbkdf2_sha256$200000$")
    assert auth.verify_password("geheim", first)
    assert not auth.verify_password("Geheim", first)


def test_verify_password_survives_broken_hashes():
    assert not auth.verify_password("x", "kein-hash")
    assert not auth.verify_password("x", "md5$1$a$b")


def test_lockout_after_five_failures():
    auth._fails.clear()
    key = "1.2.3.4|anna"
    for _ in range(4):
        auth.register_failure(key)
        assert auth.is_locked(key) == 0
    auth.register_failure(key)
    assert 0 < auth.is_locked(key) <= 300
    auth.reset_failures(key)
    assert auth.is_locked(key) == 0


def test_login_lockout_is_enforced_by_the_endpoint(client):
    auth._fails.clear()
    for _ in range(5):
        do_login(client, "tester", "falsch")
    response = do_login(client, *USER)   # jetzt gesperrt, auch mit korrektem Passwort
    assert response.headers["location"].startswith("/login?locked=")


def test_public_paths():
    assert auth.is_public("/login")
    assert auth.is_public("/static/app.js")
    assert auth.is_public("/datenschutz")
    assert not auth.is_public("/api/invoices")
    assert not auth.is_public("/")


def test_config_flag_parsing(monkeypatch):
    monkeypatch.setenv("SESSION_HTTPS_ONLY", "TRUE")
    monkeypatch.setenv("CSRF_ENABLED", "no")
    reloaded = importlib.reload(config)
    assert reloaded.SESSION_HTTPS_ONLY is True
    assert reloaded.CSRF_ENABLED is False
    monkeypatch.undo()
    importlib.reload(config)   # Ausgangszustand für die übrigen Tests
