"""Live-Anwesenheitsanzeige und die Zahlungsbestätigung beim Statuswechsel."""
from datetime import datetime, timedelta

from app import crud, models
from app.database import SessionLocal
from conftest import ADMIN, USER, do_login, make_invoice


def _age_presence(minutes: float) -> None:
    """Schiebt alle Einträge künstlich in die Vergangenheit."""
    with SessionLocal() as db:
        for row in db.query(models.Presence).all():
            row.last_seen = datetime.utcnow() - timedelta(minutes=minutes)
        db.commit()


# --------------------------- Heartbeat ------------------------------------
def test_first_visitor_is_alone(user_client):
    invoice = make_invoice(user_client)
    response = user_client.post(f"/api/presence/invoice/{invoice['id']}")
    assert response.status_code == 200
    assert response.json()["others"] == []


def test_second_user_shows_up(client):
    do_login(client, *USER)
    invoice = make_invoice(client)
    client.post(f"/api/presence/invoice/{invoice['id']}")

    do_login(client, *ADMIN)
    others = client.post(f"/api/presence/invoice/{invoice['id']}").json()["others"]
    assert [o["username"] for o in others] == ["tester"]
    assert others[0]["last_seen"]

    # ... und umgekehrt sieht der erste Benutzer jetzt den zweiten
    do_login(client, *USER)
    others = client.post(f"/api/presence/invoice/{invoice['id']}").json()["others"]
    assert [o["username"] for o in others] == ["admin"]


def test_you_never_see_yourself(user_client):
    """Weder im Heartbeat noch in der Listenübersicht taucht man selbst auf."""
    invoice = make_invoice(user_client)
    for _ in range(3):
        response = user_client.post(f"/api/presence/invoice/{invoice['id']}")
    assert response.json()["others"] == []
    assert user_client.get("/api/presence").json() == []


def test_stale_entries_disappear(client):
    do_login(client, *USER)
    invoice = make_invoice(client)
    client.post(f"/api/presence/invoice/{invoice['id']}")
    _age_presence(minutes=5)          # länger als PRESENCE_TIMEOUT

    do_login(client, *ADMIN)
    alone = client.post(f"/api/presence/invoice/{invoice['id']}").json()
    assert alone["others"] == []

    # admin sieht sich selbst nicht; tester ist abgelaufen -> nichts übrig
    assert client.get("/api/presence").json() == []


def test_leaving_removes_the_entry(client):
    do_login(client, *USER)
    invoice = make_invoice(client)
    client.post(f"/api/presence/invoice/{invoice['id']}")
    assert client.delete(f"/api/presence/invoice/{invoice['id']}").status_code == 204
    do_login(client, *ADMIN)
    assert client.get("/api/presence").json() == []


def test_switching_documents_moves_the_user(client):
    do_login(client, *USER)
    first = make_invoice(client)
    second = make_invoice(client)
    client.post(f"/api/presence/invoice/{first['id']}")
    client.post(f"/api/presence/invoice/{second['id']}")

    do_login(client, *ADMIN)
    listing = client.get("/api/presence").json()
    assert len(listing) == 1, "tester ist nur noch auf einem Beleg"
    assert listing[0]["doc_id"] == second["id"]


def test_quotes_and_delivery_notes_are_covered(user_client):
    quote = user_client.post("/api/quotes", json={
        "customer_name": "K",
        "items": [{"description": "A", "quantity": 1, "unit_price": 1}]}).json()
    note = user_client.post("/api/delivery-notes", json={
        "customer_name": "K", "items": [{"description": "A", "quantity": 1}]}).json()

    assert user_client.post(f"/api/presence/quote/{quote['id']}").status_code == 200
    assert user_client.post(f"/api/presence/delivery_note/{note['id']}").status_code == 200


def test_unknown_document_type_is_rejected(user_client):
    response = user_client.post("/api/presence/rechnung/1")
    assert response.status_code == 400
    assert "Belegart" in response.json()["detail"]
    assert user_client.delete("/api/presence/rechnung/1").status_code == 400


def test_presence_requires_login(client):
    assert client.get("/api/presence").status_code == 401
    # Der schreibende Heartbeat scheitert schon eine Schicht früher am
    # CSRF-Token – ohne Sitzung gibt es keins.
    assert client.post("/api/presence/invoice/1").status_code == 403


def test_presence_needs_a_csrf_token(user_client):
    from app import security
    user_client.headers.pop(security.CSRF_HEADER, None)
    assert user_client.post("/api/presence/invoice/1").status_code == 403


def test_listing_groups_by_document(client):
    do_login(client, *USER)
    first = make_invoice(client)
    second = make_invoice(client)
    client.post(f"/api/presence/invoice/{first['id']}")

    do_login(client, *ADMIN)
    client.post(f"/api/presence/invoice/{second['id']}")

    # admin fragt ab: sieht tester auf dem ersten Beleg, sich selbst nicht
    listing = {row["doc_id"]: row for row in client.get("/api/presence").json()}
    assert set(listing) == {first["id"]}
    assert [u["username"] for u in listing[first["id"]]["users"]] == ["tester"]


def test_crud_purges_stale_rows_from_the_table(user_client):
    """Alte Zeilen werden nicht nur ausgeblendet, sondern gelöscht."""
    invoice = make_invoice(user_client)
    user_client.post(f"/api/presence/invoice/{invoice['id']}")
    _age_presence(minutes=5)

    with SessionLocal() as db:
        assert db.query(models.Presence).count() == 1
        crud.touch_presence(db, "invoice", invoice["id"], "jemand")
        assert [p.username for p in db.query(models.Presence).all()] == ["jemand"]


# ---------------- Zahlungsbestätigung: beide Wege verhalten sich gleich ----
def test_status_bezahlt_also_sends_the_confirmation(user_client, monkeypatch):
    """Vorher kam die Bestätigung nur über den Zahlungs-Endpunkt."""
    from app import email_service
    user_client.post("/api/customers", json={"name": "Mail AG", "email": "a@b.de"})
    invoice = make_invoice(user_client, customer_name="Mail AG", tax_rate=0)

    sent = []
    monkeypatch.setattr(email_service, "send_payment_confirmation",
                        lambda inv, to, s: sent.append((inv.number, to)))

    paid = user_client.patch(f"/api/invoices/{invoice['id']}/status",
                             json={"status": "bezahlt"}).json()
    assert paid["status"] == "bezahlt"
    assert paid["remaining"] == 0.0
    assert sent == [(invoice["number"], "a@b.de")]


def test_confirmation_is_not_sent_twice(user_client, monkeypatch):
    from app import email_service
    user_client.post("/api/customers", json={"name": "Mail AG", "email": "a@b.de"})
    invoice = make_invoice(user_client, customer_name="Mail AG", tax_rate=0)

    sent = []
    monkeypatch.setattr(email_service, "send_payment_confirmation",
                        lambda inv, to, s: sent.append(to))

    user_client.patch(f"/api/invoices/{invoice['id']}/status", json={"status": "bezahlt"})
    user_client.patch(f"/api/invoices/{invoice['id']}/status", json={"status": "bezahlt"})
    assert sent == ["a@b.de"]


def test_reopening_and_paying_again_confirms_again(user_client, monkeypatch):
    from app import email_service
    user_client.post("/api/customers", json={"name": "Mail AG", "email": "a@b.de"})
    invoice = make_invoice(user_client, customer_name="Mail AG", tax_rate=0)

    sent = []
    monkeypatch.setattr(email_service, "send_payment_confirmation",
                        lambda inv, to, s: sent.append(to))

    user_client.patch(f"/api/invoices/{invoice['id']}/status", json={"status": "bezahlt"})
    user_client.patch(f"/api/invoices/{invoice['id']}/status", json={"status": "offen"})
    user_client.patch(f"/api/invoices/{invoice['id']}/status", json={"status": "bezahlt"})
    assert sent == ["a@b.de", "a@b.de"]


def test_status_change_without_customer_email_is_harmless(user_client):
    invoice = make_invoice(user_client, customer_name="Unbekannt GmbH")
    response = user_client.patch(f"/api/invoices/{invoice['id']}/status",
                                 json={"status": "bezahlt"})
    assert response.status_code == 200


def test_failing_confirmation_does_not_break_the_status_change(user_client, monkeypatch):
    from app import email_service
    user_client.post("/api/customers", json={"name": "Mail AG", "email": "a@b.de"})
    invoice = make_invoice(user_client, customer_name="Mail AG", tax_rate=0)

    def boom(*args, **kwargs):
        raise OSError("SMTP nicht erreichbar")

    monkeypatch.setattr(email_service, "send_payment_confirmation", boom)
    response = user_client.patch(f"/api/invoices/{invoice['id']}/status",
                                 json={"status": "bezahlt"})
    assert response.status_code == 200
    assert response.json()["status"] == "bezahlt"


def test_cancelling_does_not_send_a_confirmation(user_client, monkeypatch):
    from app import email_service
    user_client.post("/api/customers", json={"name": "Mail AG", "email": "a@b.de"})
    invoice = make_invoice(user_client, customer_name="Mail AG", tax_rate=0)

    sent = []
    monkeypatch.setattr(email_service, "send_payment_confirmation",
                        lambda inv, to, s: sent.append(to))
    user_client.patch(f"/api/invoices/{invoice['id']}/status", json={"status": "storniert"})
    assert sent == []
