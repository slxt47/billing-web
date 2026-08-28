"""Gutschriften: eigene Belegart, Voll- und Teilgutschrift zu einer Rechnung."""
from conftest import make_invoice

CREDIT = {
    "customer_name": "Gutschrift GmbH",
    "customer_address": "Gutweg 3",
    "customer_contact_person": "Frau Gut",
    "reason": "Ware beschädigt",
    "tax_rate": 20,
    "items": [{"description": "Rückgabe Beratung", "quantity": 1, "unit_price": 100}],
}


def test_create_credit_note(user_client):
    cn = user_client.post("/api/credit-notes", json=CREDIT).json()
    assert cn["number"].startswith("GS-")
    assert cn["status"] == "offen"
    assert cn["total"] == 120.0          # 100 netto + 20 % USt.
    assert cn["invoice_id"] is None
    assert cn["reason"] == "Ware beschädigt"


def test_credit_note_needs_items(user_client):
    assert user_client.post("/api/credit-notes",
                            json={**CREDIT, "items": []}).status_code == 422


def test_credit_note_shares_the_document_number_sequence(user_client):
    invoice = make_invoice(user_client)
    cn = user_client.post("/api/credit-notes", json=CREDIT).json()
    assert int(cn["number"].split("-")[-1]) == int(invoice["number"].split("-")[-1]) + 1


def test_full_credit_note_from_invoice(user_client):
    invoice = make_invoice(user_client)          # 2 × 100 netto + 20 % = 240
    cn = user_client.post(f"/api/invoices/{invoice['id']}/credit-note",
                          json={"reason": "Auftrag storniert"}).json()

    assert cn["invoice_id"] == invoice["id"]
    assert cn["invoice_number"] == invoice["number"]
    assert cn["total"] == invoice["total"]
    assert [it["description"] for it in cn["items"]] == ["Beratung"]

    # Die Rechnung ist damit vollständig gutgeschrieben.
    after = user_client.get(f"/api/invoices/{invoice['id']}").json()
    assert after["credited_amount"] == invoice["total"]
    assert after["remaining"] == 0


def test_partial_credit_note_from_invoice(user_client):
    invoice = make_invoice(user_client)
    cn = user_client.post(f"/api/invoices/{invoice['id']}/credit-note", json={
        "reason": "eine Stunde zu viel berechnet",
        "items": [{"description": "Beratung (anteilig)", "quantity": 1,
                   "unit_price": 100}],
    }).json()

    assert cn["total"] == 120.0
    after = user_client.get(f"/api/invoices/{invoice['id']}").json()
    assert after["credited_amount"] == 120.0
    assert after["remaining"] == 120.0


def test_credit_notes_may_not_exceed_the_invoice(user_client):
    invoice = make_invoice(user_client)
    user_client.post(f"/api/invoices/{invoice['id']}/credit-note", json={})

    too_much = user_client.post(f"/api/invoices/{invoice['id']}/credit-note", json={
        "items": [{"description": "noch mal alles", "quantity": 2, "unit_price": 100}],
    })
    assert too_much.status_code == 400
    assert "übersteigt" in too_much.json()["detail"]
    # Die abgewiesene Gutschrift darf nicht stehen bleiben.
    assert len(user_client.get("/api/credit-notes").json()) == 1


def test_credit_note_takes_the_invoice_discount_into_account(user_client):
    invoice = make_invoice(user_client, discount_percent=10)   # 200 - 10 % = 180 netto
    cn = user_client.post(f"/api/invoices/{invoice['id']}/credit-note", json={}).json()
    assert cn["items"][0]["unit_price"] == 90.0
    assert cn["total"] == invoice["total"]


def test_cancelled_credit_note_does_not_count(user_client):
    invoice = make_invoice(user_client)
    cn = user_client.post(f"/api/invoices/{invoice['id']}/credit-note", json={}).json()
    user_client.patch(f"/api/credit-notes/{cn['id']}/status",
                      json={"status": "storniert"})

    after = user_client.get(f"/api/invoices/{invoice['id']}").json()
    assert after["credited_amount"] == 0
    assert after["remaining"] == invoice["total"]


def test_credit_note_status_is_validated(user_client):
    cn = user_client.post("/api/credit-notes", json=CREDIT).json()
    assert user_client.patch(f"/api/credit-notes/{cn['id']}/status",
                             json={"status": "erstattet"}).json()["status"] == "erstattet"
    assert user_client.patch(f"/api/credit-notes/{cn['id']}/status",
                             json={"status": "quatsch"}).status_code == 400


def test_edit_and_delete_credit_note(user_client):
    cn = user_client.post("/api/credit-notes", json=CREDIT).json()
    edited = user_client.put(f"/api/credit-notes/{cn['id']}",
                             json={**CREDIT, "reason": "Kulanz"}).json()
    assert edited["reason"] == "Kulanz"

    user_client.patch(f"/api/credit-notes/{cn['id']}/status", json={"status": "storniert"})
    assert user_client.put(f"/api/credit-notes/{cn['id']}",
                           json=CREDIT).status_code == 400

    assert user_client.delete(f"/api/credit-notes/{cn['id']}").status_code == 204
    assert user_client.get(f"/api/credit-notes/{cn['id']}").status_code == 404


def test_credit_note_pdf_and_search(user_client):
    cn = user_client.post("/api/credit-notes", json=CREDIT).json()
    assert user_client.get(f"/api/credit-notes/{cn['id']}/pdf").content.startswith(b"%PDF")

    user_client.post("/api/credit-notes", json={**CREDIT, "customer_name": "Andere AG"})
    assert len(user_client.get("/api/credit-notes?search=andere").json()) == 1
    assert user_client.get("/api/credit-notes").headers["X-Total-Count"] == "2"


def test_credit_note_lowers_the_open_amount_in_the_dashboard(user_client):
    invoice = make_invoice(user_client)
    before = user_client.get("/api/stats").json()
    assert before["total_revenue"] == invoice["total"]

    user_client.post(f"/api/invoices/{invoice['id']}/credit-note", json={})
    after = user_client.get("/api/stats").json()
    assert after["total_revenue"] == 0
    assert after["open_amount"] == 0


def test_cancelled_invoice_cannot_be_credited(user_client):
    invoice = make_invoice(user_client)
    user_client.patch(f"/api/invoices/{invoice['id']}/status", json={"status": "storniert"})
    response = user_client.post(f"/api/invoices/{invoice['id']}/credit-note", json={})
    assert response.status_code == 400


def test_credit_note_for_an_unknown_invoice_is_404(user_client):
    assert user_client.post("/api/invoices/999/credit-note", json={}).status_code == 404
    assert user_client.post("/api/credit-notes",
                            json={**CREDIT, "invoice_id": 999}).status_code == 404
