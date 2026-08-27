"""Rechnungen: Berechnung, Nummernkreis, Bearbeiten, Zahlungen, Storno, Export."""
import zipfile
from datetime import date, timedelta
from io import BytesIO

from app import models
from conftest import make_invoice


# --------------------------- Berechnung ----------------------------------
def test_totals_with_tax(user_client):
    invoice = make_invoice(user_client, tax_rate=19,
                           items=[{"description": "A", "quantity": 3, "unit_price": 10}])
    assert invoice["subtotal"] == 30.0
    assert invoice["tax_amount"] == 5.7
    assert invoice["total"] == 35.7


def test_discount_reduces_the_tax_base(user_client):
    invoice = make_invoice(user_client, tax_rate=20, discount_percent=10,
                           items=[{"description": "A", "quantity": 1, "unit_price": 100}])
    assert invoice["discount_amount"] == 10.0
    assert invoice["net"] == 90.0
    assert invoice["tax_amount"] == 18.0
    assert invoice["total"] == 108.0


def test_small_business_has_no_tax(user_client):
    invoice = make_invoice(user_client, tax_rate=20, small_business=True)
    assert invoice["tax_amount"] == 0.0
    assert invoice["total"] == invoice["net"] == 200.0


def test_skonto_is_calculated(user_client):
    invoice = make_invoice(user_client, tax_rate=0, skonto_percent=2, skonto_days=10,
                           items=[{"description": "A", "quantity": 1, "unit_price": 500}])
    assert invoice["skonto_amount"] == 10.0
    assert invoice["skonto_total"] == 490.0
    assert invoice["skonto_date"] == str(date.today() + timedelta(days=10))


def test_issue_date_is_always_today(user_client):
    invoice = make_invoice(user_client, issue_date="2020-01-01")
    assert invoice["issue_date"] == str(date.today())


def test_invalid_payload_is_rejected_with_a_readable_message(user_client):
    response = user_client.post("/api/invoices", json={"customer_name": "", "items": []})
    assert response.status_code == 422
    assert "Ungültige Eingabe" in response.json()["detail"]
    assert response.json()["request_id"]


def test_negative_price_is_rejected(user_client):
    response = user_client.post("/api/invoices", json={
        "customer_name": "X",
        "items": [{"description": "A", "quantity": 1, "unit_price": -5}]})
    assert response.status_code == 422


# --------------------------- Nummernkreis --------------------------------
def test_numbers_count_up(user_client):
    year = date.today().year
    first = make_invoice(user_client)
    second = make_invoice(user_client)
    assert first["number"] == f"RE-{year}-0001"
    assert second["number"] == f"RE-{year}-0002"


def test_document_types_share_one_sequence(user_client):
    """AN/RE/LS zählen gemeinsam hoch, damit eine Umwandlung die Nummer
    weiterschieben kann."""
    year = date.today().year
    assert make_invoice(user_client)["number"] == f"RE-{year}-0001"
    quote = user_client.post("/api/quotes", json={
        "customer_name": "K", "items": [{"description": "A", "quantity": 1, "unit_price": 1}]})
    assert quote.json()["number"] == f"AN-{year}-0002"
    note = user_client.post("/api/delivery-notes", json={
        "customer_name": "K", "items": [{"description": "A", "quantity": 1}]})
    assert note.json()["number"] == f"LS-{year}-0003"


# --------------------------- Bearbeiten & Sperre --------------------------
def test_edit_invoice(user_client):
    invoice = make_invoice(user_client)
    response = user_client.put(f"/api/invoices/{invoice['id']}", json={
        "customer_name": "Neu GmbH", "tax_rate": 0,
        "items": [{"description": "B", "quantity": 1, "unit_price": 50}]})
    assert response.status_code == 200
    assert response.json()["customer_name"] == "Neu GmbH"
    assert response.json()["total"] == 50.0
    assert response.json()["number"] == invoice["number"]   # Nummer bleibt


def test_cancelled_invoice_cannot_be_edited(user_client):
    invoice = make_invoice(user_client)
    user_client.patch(f"/api/invoices/{invoice['id']}/status", json={"status": "storniert"})
    response = user_client.put(f"/api/invoices/{invoice['id']}",
                               json={"customer_name": "X",
                                     "items": [{"description": "B", "quantity": 1, "unit_price": 1}]})
    assert response.status_code == 400


def test_lock_blocks_a_second_user(client):
    from conftest import ADMIN, USER, do_login
    do_login(client, *USER)
    invoice = make_invoice(client)
    lock = client.post(f"/api/invoices/{invoice['id']}/lock").json()
    assert lock["editable"] is True and lock["locked_by"] == "tester"

    do_login(client, *ADMIN)          # anderer Benutzer, gleiche Rechnung
    status = client.get(f"/api/invoices/{invoice['id']}/lock").json()
    assert status["locked"] is True and status["editable"] is False

    blocked = client.put(f"/api/invoices/{invoice['id']}",
                         json={"customer_name": "X",
                               "items": [{"description": "B", "quantity": 1, "unit_price": 1}]})
    assert blocked.status_code == 409


def test_lock_is_released_again(user_client):
    invoice = make_invoice(user_client)
    user_client.post(f"/api/invoices/{invoice['id']}/lock")
    released = user_client.delete(f"/api/invoices/{invoice['id']}/lock").json()
    assert released["locked"] is False


def test_expired_lock_does_not_block(user_client):
    from datetime import datetime
    from app.database import SessionLocal
    invoice = make_invoice(user_client)
    with SessionLocal() as db:
        row = db.get(models.Invoice, invoice["id"])
        row.locked_by = "jemand-anderes"
        row.locked_at = datetime.utcnow() - timedelta(minutes=10)
        db.commit()
    assert user_client.get(f"/api/invoices/{invoice['id']}/lock").json()["editable"] is True


# --------------------------- Zahlungen -----------------------------------
def test_partial_then_full_payment(user_client):
    invoice = make_invoice(user_client, tax_rate=0)      # 200,00
    partial = user_client.post(f"/api/invoices/{invoice['id']}/payment",
                               json={"amount": 50}).json()
    assert partial["status"] == "teilbezahlt"
    assert partial["remaining"] == 150.0

    full = user_client.post(f"/api/invoices/{invoice['id']}/payment",
                            json={"amount": 150}).json()
    assert full["status"] == "bezahlt"
    assert full["remaining"] == 0.0


def test_payment_on_cancelled_invoice_fails(user_client):
    invoice = make_invoice(user_client)
    user_client.patch(f"/api/invoices/{invoice['id']}/status", json={"status": "storniert"})
    response = user_client.post(f"/api/invoices/{invoice['id']}/payment", json={"amount": 10})
    assert response.status_code == 400


def test_zero_payment_is_rejected(user_client):
    invoice = make_invoice(user_client)
    assert user_client.post(f"/api/invoices/{invoice['id']}/payment",
                            json={"amount": 0}).status_code == 422


def test_cancel_and_reopen(user_client):
    invoice = make_invoice(user_client)
    cancelled = user_client.patch(f"/api/invoices/{invoice['id']}/status",
                                  json={"status": "storniert"}).json()
    assert cancelled["status"] == "storniert" and cancelled["cancelled_at"]

    reopened = user_client.patch(f"/api/invoices/{invoice['id']}/status",
                                 json={"status": "offen"}).json()
    assert reopened["status"] == "offen"
    assert reopened["cancelled_at"] is None
    assert reopened["paid_amount"] == 0.0


def test_unknown_status_is_rejected(user_client):
    invoice = make_invoice(user_client)
    response = user_client.patch(f"/api/invoices/{invoice['id']}/status",
                                 json={"status": "vielleicht"})
    assert response.status_code == 400


def test_overdue_flag(user_client):
    yesterday = str(date.today() - timedelta(days=1))
    invoice = make_invoice(user_client, due_date=yesterday)
    assert invoice["is_overdue"] is True
    paid = user_client.post(f"/api/invoices/{invoice['id']}/payment",
                            json={"amount": invoice["total"]}).json()
    assert paid["is_overdue"] is False


# --------------------------- Suche, Paginierung, Löschen ------------------
def test_search_by_customer_and_number(user_client):
    make_invoice(user_client, customer_name="Alpha AG")
    beta = make_invoice(user_client, customer_name="Beta GmbH")
    assert len(user_client.get("/api/invoices?search=Alpha").json()) == 1
    assert len(user_client.get("/api/invoices?search=alpha").json()) == 1   # ohne Groß/Klein
    hits = user_client.get(f"/api/invoices?search={beta['number']}").json()
    assert [h["id"] for h in hits] == [beta["id"]]


def test_pagination(user_client):
    for i in range(5):
        make_invoice(user_client, customer_name=f"Kunde {i}")
    page = user_client.get("/api/invoices?limit=2&offset=1")
    assert len(page.json()) == 2
    assert page.headers["X-Total-Count"] == "5"
    assert len(user_client.get("/api/invoices").json()) == 5


def test_limit_is_capped(user_client, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "MAX_PAGE_SIZE", 2)
    for i in range(4):
        make_invoice(user_client, customer_name=f"Kunde {i}")
    assert len(user_client.get("/api/invoices?limit=100").json()) == 2


def test_delete_invoice(user_client):
    invoice = make_invoice(user_client)
    assert user_client.delete(f"/api/invoices/{invoice['id']}").status_code == 204
    assert user_client.get(f"/api/invoices/{invoice['id']}").status_code == 404


def test_missing_invoice_returns_404(user_client):
    response = user_client.get("/api/invoices/999999")
    assert response.status_code == 404
    assert response.json()["detail"] == "Rechnung nicht gefunden"


# --------------------------- PDF & Export ---------------------------------
def test_pdf_download(user_client):
    invoice = make_invoice(user_client)
    response = user_client.get(f"/api/invoices/{invoice['id']}/pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    assert invoice["number"] in response.headers["content-disposition"]


def test_month_export_zip(user_client):
    invoice = make_invoice(user_client)
    month = date.today().strftime("%Y-%m")
    response = user_client.get(f"/api/export?month={month}")
    assert response.status_code == 200
    with zipfile.ZipFile(BytesIO(response.content)) as zf:
        names = zf.namelist()
        assert f"{invoice['number']}.pdf" in names
        assert "uebersicht.csv" in names
        csv = zf.read("uebersicht.csv").decode("utf-8")
        assert invoice["number"] in csv


def test_month_export_rejects_bad_format(user_client):
    assert user_client.get("/api/export?month=2026-13").status_code == 400
    assert user_client.get("/api/export?month=quatsch").status_code == 400


def test_month_export_without_invoices(user_client):
    assert user_client.get("/api/export?month=1999-01").status_code == 404


# --------------------------- E-Mail ---------------------------------------
def test_email_address_is_validated(user_client):
    invoice = make_invoice(user_client)
    response = user_client.post(f"/api/invoices/{invoice['id']}/email",
                                json={"to": "keine-mail"})
    assert response.status_code == 400
    assert "E-Mail" in response.json()["detail"]


def test_email_reports_smtp_failure(user_client, monkeypatch):
    from app import email_service
    invoice = make_invoice(user_client)

    def boom(*args, **kwargs):
        raise OSError("Verbindung abgelehnt")

    monkeypatch.setattr(email_service, "send_invoice_email", boom)
    response = user_client.post(f"/api/invoices/{invoice['id']}/email",
                                json={"to": "kunde@example.com"})
    assert response.status_code == 502
    assert "Verbindung abgelehnt" in response.json()["detail"]


def test_email_is_sent_via_the_service(user_client, monkeypatch):
    from app import email_service
    invoice = make_invoice(user_client)
    sent = {}

    def record(inv, to, settings):
        sent["number"], sent["to"] = inv.number, to

    monkeypatch.setattr(email_service, "send_invoice_email", record)
    response = user_client.post(f"/api/invoices/{invoice['id']}/email",
                                json={"to": " kunde@example.com "})
    assert response.status_code == 200
    assert sent == {"number": invoice["number"], "to": "kunde@example.com"}


def test_full_payment_triggers_confirmation_mail(user_client, monkeypatch):
    from app import email_service
    user_client.post("/api/customers", json={"name": "Mail AG", "email": "a@b.de"})
    invoice = make_invoice(user_client, customer_name="Mail AG", tax_rate=0)
    calls = []
    monkeypatch.setattr(email_service, "send_payment_confirmation",
                        lambda inv, to, s: calls.append(to))
    user_client.post(f"/api/invoices/{invoice['id']}/payment", json={"amount": 100})
    assert calls == []                       # Teilzahlung: noch keine Bestätigung
    user_client.post(f"/api/invoices/{invoice['id']}/payment", json={"amount": 100})
    assert calls == ["a@b.de"]


# --------------------------- Dashboard ------------------------------------
def test_dashboard_stats(user_client):
    paid = make_invoice(user_client, tax_rate=0)
    user_client.post(f"/api/invoices/{paid['id']}/payment", json={"amount": 200})
    make_invoice(user_client, tax_rate=0, due_date=str(date.today() - timedelta(days=3)))

    stats = user_client.get("/api/stats").json()
    assert stats["invoice_count"] == 2
    assert stats["paid_count"] == 1
    assert stats["overdue_count"] == 1
    assert stats["open_amount"] == 200.0
    assert stats["overdue_amount"] == 200.0
    assert len(stats["months"]) == 6
