"""Angebote und Lieferscheine inkl. Umwandlung."""
from datetime import date

from conftest import make_invoice

QUOTE = {
    "customer_name": "Angebot AG",
    "customer_address": "Weg 2",
    "customer_contact_person": "Frau Muster",
    "tax_rate": 20,
    "items": [{"description": "Konzept", "quantity": 2, "unit_price": 250}],
}
NOTE = {
    "customer_name": "Liefer GmbH",
    "customer_contact_person": "Herr Muster",
    "items": [{"description": "Kiste Schrauben", "quantity": 3}],
}


# --------------------------- Angebote ------------------------------------
def test_create_quote(user_client):
    quote = user_client.post("/api/quotes", json=QUOTE).json()
    assert quote["number"].startswith("AN-")
    assert quote["status"] == "offen"
    assert quote["total"] == 600.0
    assert quote["customer_contact_person"] == "Frau Muster"


def test_quote_needs_items(user_client):
    assert user_client.post("/api/quotes",
                            json={**QUOTE, "items": []}).status_code == 422


def test_edit_quote(user_client):
    quote = user_client.post("/api/quotes", json=QUOTE).json()
    updated = user_client.put(f"/api/quotes/{quote['id']}", json={
        **QUOTE, "customer_name": "Anders AG",
        "items": [{"description": "X", "quantity": 1, "unit_price": 10}]}).json()
    assert updated["customer_name"] == "Anders AG"
    assert updated["total"] == 12.0


def test_quote_status_changes(user_client):
    quote = user_client.post("/api/quotes", json=QUOTE).json()
    accepted = user_client.patch(f"/api/quotes/{quote['id']}/status",
                                 json={"status": "angenommen"}).json()
    assert accepted["status"] == "angenommen"
    assert user_client.patch(f"/api/quotes/{quote['id']}/status",
                             json={"status": "unbekannt"}).status_code == 400


def test_convert_quote_to_invoice(user_client):
    quote = user_client.post("/api/quotes", json=QUOTE).json()
    invoice = user_client.post(f"/api/quotes/{quote['id']}/convert").json()

    assert invoice["number"].startswith("RE-")
    assert invoice["customer_name"] == QUOTE["customer_name"]
    assert invoice["total"] == quote["total"]
    assert [i["description"] for i in invoice["items"]] == ["Konzept"]

    after = user_client.get(f"/api/quotes/{quote['id']}").json()
    assert after["status"] == "umgewandelt"
    assert after["converted_invoice_id"] == invoice["id"]


def test_converted_quote_is_locked_for_edits(user_client):
    quote = user_client.post("/api/quotes", json=QUOTE).json()
    user_client.post(f"/api/quotes/{quote['id']}/convert")
    assert user_client.put(f"/api/quotes/{quote['id']}", json=QUOTE).status_code == 400
    assert user_client.post(f"/api/quotes/{quote['id']}/convert").status_code == 400


def test_quote_pdf_and_delete(user_client):
    quote = user_client.post("/api/quotes", json=QUOTE).json()
    pdf = user_client.get(f"/api/quotes/{quote['id']}/pdf")
    assert pdf.content.startswith(b"%PDF")
    assert user_client.delete(f"/api/quotes/{quote['id']}").status_code == 204
    assert user_client.get(f"/api/quotes/{quote['id']}").status_code == 404


def test_quote_search_and_pagination(user_client):
    user_client.post("/api/quotes", json={**QUOTE, "customer_name": "Alpha"})
    user_client.post("/api/quotes", json={**QUOTE, "customer_name": "Beta"})
    assert len(user_client.get("/api/quotes?search=alpha").json()) == 1
    page = user_client.get("/api/quotes?limit=1")
    assert len(page.json()) == 1
    assert page.headers["X-Total-Count"] == "2"


# --------------------------- Lieferscheine --------------------------------
def test_create_delivery_note(user_client):
    note = user_client.post("/api/delivery-notes", json=NOTE).json()
    assert note["number"].startswith("LS-")
    assert note["status"] == "offen"
    assert note["items"][0]["quantity"] == 3.0
    assert note["issue_date"] == str(date.today())


def test_edit_delivery_note(user_client):
    note = user_client.post("/api/delivery-notes", json=NOTE).json()
    updated = user_client.put(f"/api/delivery-notes/{note['id']}", json={
        **NOTE, "notes": "Bitte an der Rampe abgeben"}).json()
    assert updated["notes"] == "Bitte an der Rampe abgeben"


def test_delivery_note_status(user_client):
    note = user_client.post("/api/delivery-notes", json=NOTE).json()
    cancelled = user_client.patch(f"/api/delivery-notes/{note['id']}/status",
                                  json={"status": "storniert"}).json()
    assert cancelled["status"] == "storniert"
    assert user_client.patch(f"/api/delivery-notes/{note['id']}/status",
                             json={"status": "quatsch"}).status_code == 400


def test_delivery_note_from_invoice(user_client):
    invoice = make_invoice(user_client)
    note = user_client.post(
        f"/api/invoices/{invoice['id']}/convert-to-delivery-note").json()
    assert note["number"].startswith("LS-")
    assert note["source_invoice_id"] == invoice["id"]
    assert note["customer_name"] == invoice["customer_name"]
    assert [i["description"] for i in note["items"]] == ["Beratung"]


def test_delivery_note_pdf_and_delete(user_client):
    note = user_client.post("/api/delivery-notes", json=NOTE).json()
    assert user_client.get(f"/api/delivery-notes/{note['id']}/pdf").content.startswith(b"%PDF")
    assert user_client.delete(f"/api/delivery-notes/{note['id']}").status_code == 204
    assert user_client.get(f"/api/delivery-notes/{note['id']}").status_code == 404


def test_delivery_note_search(user_client):
    user_client.post("/api/delivery-notes", json={**NOTE, "customer_name": "Nord"})
    user_client.post("/api/delivery-notes", json={**NOTE, "customer_name": "Süd"})
    assert len(user_client.get("/api/delivery-notes?search=nord").json()) == 1
    assert user_client.get("/api/delivery-notes").headers["X-Total-Count"] == "2"


# --------------------------- Lieferschein -> Angebot ----------------------
def test_delivery_note_converts_to_quote(user_client):
    user_client.post("/api/products", json={"name": "Beratung", "unit_price": 120})
    dn = user_client.post("/api/delivery-notes", json={
        "customer_name": "Wandel GmbH",
        "customer_address": "Wandelweg 1",
        "customer_contact_person": "Frau Wandel",
        "notes": "Lieferung vom Dienstag",
        "items": [{"description": "Beratung", "quantity": 3},
                  {"description": "Sonderposten", "quantity": 1}],
    }).json()

    quote = user_client.post(f"/api/delivery-notes/{dn['id']}/convert-to-quote")
    assert quote.status_code == 201, quote.text
    quote = quote.json()

    assert quote["number"] == dn["number"].replace("LS-", "AN-")
    assert quote["customer_name"] == "Wandel GmbH"
    assert quote["customer_contact_person"] == "Frau Wandel"
    assert quote["status"] == "offen"
    # Menge kommt aus dem Lieferschein, der Preis – wenn bekannt – aus dem
    # Artikelstamm; sonst 0 zum Nachtragen.
    prices = {it["description"]: it["unit_price"] for it in quote["items"]}
    assert prices == {"Beratung": 120.0, "Sonderposten": 0.0}
    assert [it["quantity"] for it in quote["items"]] == [3.0, 1.0]


def test_delivery_note_conversion_avoids_a_taken_number(user_client):
    """Angebot -> Rechnung -> Lieferschein -> Angebot: die ursprüngliche
    Angebotsnummer ist vergeben, also muss eine neue vergeben werden."""
    quote = user_client.post("/api/quotes", json=QUOTE).json()
    invoice = user_client.post(f"/api/quotes/{quote['id']}/convert").json()
    dn = user_client.post(
        f"/api/invoices/{invoice['id']}/convert-to-delivery-note").json()
    assert dn["number"].endswith(quote["number"].split("-")[-1])

    second = user_client.post(f"/api/delivery-notes/{dn['id']}/convert-to-quote").json()
    assert second["number"] != quote["number"]
    assert second["number"].startswith("AN-")
    assert len(user_client.get("/api/quotes").json()) == 2


def test_cancelled_delivery_note_cannot_be_converted(user_client):
    dn = user_client.post("/api/delivery-notes", json={
        "customer_name": "Storno GmbH",
        "items": [{"description": "Ware", "quantity": 1}],
    }).json()
    user_client.patch(f"/api/delivery-notes/{dn['id']}/status",
                      json={"status": "storniert"})
    response = user_client.post(f"/api/delivery-notes/{dn['id']}/convert-to-quote")
    assert response.status_code == 400


def test_converting_an_unknown_delivery_note_is_404(user_client):
    assert user_client.post("/api/delivery-notes/999/convert-to-quote").status_code == 404
