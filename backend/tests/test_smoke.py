"""Grundlegender Durchstich: Start, Login-Zwang, Login, erste Rechnung."""
from conftest import ADMIN, USER, do_login, make_invoice


def test_health_is_public(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_api_requires_login(client):
    assert client.get("/api/invoices").status_code == 401


def test_page_redirects_to_login(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/login"


def test_login_and_me(client):
    response = do_login(client, *USER)
    assert response.status_code == 303
    assert response.headers["location"] == "/"

    me = client.get("/api/me").json()
    assert me["user"] == "tester"
    assert me["is_admin"] is False
    assert me["csrf_token"]


def test_admin_flag(client):
    do_login(client, *ADMIN)
    assert client.get("/api/me").json()["is_admin"] is True


def test_wrong_password_does_not_log_in(client):
    response = do_login(client, "tester", "falsch")
    assert response.headers["location"] == "/login?error=1"
    assert client.get("/api/invoices").status_code == 401


def test_create_and_read_invoice(user_client):
    invoice = make_invoice(user_client)
    assert invoice["number"].startswith("RE-")
    assert invoice["total"] == 240.0
    assert invoice["status"] == "offen"

    listing = user_client.get("/api/invoices")
    assert listing.status_code == 200
    assert [i["id"] for i in listing.json()] == [invoice["id"]]
    assert listing.headers["X-Total-Count"] == "1"


def test_logout_ends_session(user_client):
    user_client.get("/logout", follow_redirects=False)
    assert user_client.get("/api/invoices").status_code == 401
