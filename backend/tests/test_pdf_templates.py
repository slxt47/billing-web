"""Eigene PDF-Vorlagen: verwalten, Vorgabe setzen, beim Download wählen."""
from conftest import make_invoice

TEMPLATE = {
    "name": "Grün",
    "accent_color": "#2f9e44",
    "header_color": "#1f2733",
    "font_family": "Times",
    "font_size": 11,
    "header_note": "Musterfirma – Beleg",
    "footer_text": "Musterfirma · Musterweg 1 · 12345 Musterstadt",
    "show_logo": True,
    "show_qr": False,
}


def test_first_template_becomes_the_default(admin_client):
    tpl = admin_client.post("/api/pdf-templates", json=TEMPLATE).json()
    assert tpl["is_default"] is True
    second = admin_client.post("/api/pdf-templates",
                               json={**TEMPLATE, "name": "Blau"}).json()
    assert second["is_default"] is False


def test_default_can_be_moved(admin_client):
    first = admin_client.post("/api/pdf-templates", json=TEMPLATE).json()
    second = admin_client.post("/api/pdf-templates",
                               json={**TEMPLATE, "name": "Blau"}).json()

    admin_client.post(f"/api/pdf-templates/{second['id']}/default")
    by_id = {t["id"]: t for t in admin_client.get("/api/pdf-templates").json()}
    assert by_id[second["id"]]["is_default"] is True
    assert by_id[first["id"]]["is_default"] is False


def test_deleting_the_default_hands_it_on(admin_client):
    first = admin_client.post("/api/pdf-templates", json=TEMPLATE).json()
    second = admin_client.post("/api/pdf-templates",
                               json={**TEMPLATE, "name": "Blau"}).json()

    assert admin_client.delete(f"/api/pdf-templates/{first['id']}").status_code == 204
    left = admin_client.get("/api/pdf-templates").json()
    assert [t["id"] for t in left] == [second["id"]]
    assert left[0]["is_default"] is True


def test_names_stay_unique(admin_client):
    admin_client.post("/api/pdf-templates", json=TEMPLATE)
    assert admin_client.post("/api/pdf-templates", json=TEMPLATE).status_code == 400


def test_colors_and_fonts_are_validated(admin_client):
    assert admin_client.post("/api/pdf-templates",
                             json={**TEMPLATE, "accent_color": "grün"}).status_code == 422
    assert admin_client.post("/api/pdf-templates",
                             json={**TEMPLATE, "font_size": 40}).status_code == 422
    assert admin_client.post("/api/pdf-templates",
                             json={**TEMPLATE, "font_family": "Comic"}).status_code == 400


def test_only_admins_may_change_templates(admin_client, client):
    tpl = admin_client.post("/api/pdf-templates", json=TEMPLATE).json()
    admin_client.get("/logout")

    from conftest import USER, do_login
    do_login(client, *USER)
    # Lesen ja (für die Auswahl beim Download), ändern nein.
    assert client.get("/api/pdf-templates").status_code == 200
    assert client.post("/api/pdf-templates",
                       json={**TEMPLATE, "name": "Heimlich"}).status_code == 403
    assert client.put(f"/api/pdf-templates/{tpl['id']}", json=TEMPLATE).status_code == 403
    assert client.delete(f"/api/pdf-templates/{tpl['id']}").status_code == 403


def test_every_document_pdf_accepts_a_template(admin_client):
    tpl = admin_client.post("/api/pdf-templates", json=TEMPLATE).json()
    invoice = make_invoice(admin_client)
    quote = admin_client.post("/api/quotes", json={
        "customer_name": "Angebot AG", "tax_rate": 20,
        "items": [{"description": "Konzept", "quantity": 1, "unit_price": 100}]}).json()
    dn = admin_client.post("/api/delivery-notes", json={
        "customer_name": "Liefer GmbH",
        "items": [{"description": "Kiste", "quantity": 1}]}).json()
    cn = admin_client.post(f"/api/invoices/{invoice['id']}/credit-note", json={}).json()

    for url in (f"/api/invoices/{invoice['id']}/pdf",
                f"/api/quotes/{quote['id']}/pdf",
                f"/api/delivery-notes/{dn['id']}/pdf",
                f"/api/credit-notes/{cn['id']}/pdf"):
        assert admin_client.get(f"{url}?template={tpl['id']}").content.startswith(b"%PDF")


def test_unknown_template_is_404(admin_client):
    invoice = make_invoice(admin_client)
    assert admin_client.get(f"/api/invoices/{invoice['id']}/pdf?template=999"
                            ).status_code == 404


def test_preview_needs_no_real_document(admin_client):
    tpl = admin_client.post("/api/pdf-templates", json=TEMPLATE).json()
    preview = admin_client.get(f"/api/pdf-templates/{tpl['id']}/preview")
    assert preview.content.startswith(b"%PDF")
    assert preview.headers["content-type"] == "application/pdf"
    # Die Vorschau legt nichts an.
    assert admin_client.get("/api/invoices").json() == []


# ---------------------------------------------------- Vordruck ("formular")
FORM_TEMPLATE = {**TEMPLATE, "name": "Vordruck", "layout": "formular",
                 "font_family": "Helvetica"}


def test_layout_defaults_to_standard(admin_client):
    tpl = admin_client.post("/api/pdf-templates", json=TEMPLATE).json()
    assert tpl["layout"] == "standard"


def test_unknown_layout_is_rejected(admin_client):
    assert admin_client.post("/api/pdf-templates",
                             json={**TEMPLATE, "layout": "papyrus"}).status_code == 400


def test_form_layout_renders_every_document(admin_client):
    """Der Vordruck muss alle vier Belegarten können – auch den Lieferschein,
    der gar keine Preise hat."""
    tpl = admin_client.post("/api/pdf-templates", json=FORM_TEMPLATE).json()
    assert tpl["layout"] == "formular"
    invoice = make_invoice(admin_client)
    quote = admin_client.post("/api/quotes", json={
        "customer_name": "Angebot AG", "tax_rate": 20,
        "items": [{"description": "Konzept", "quantity": 1, "unit_price": 100}]}).json()
    dn = admin_client.post("/api/delivery-notes", json={
        "customer_name": "Liefer GmbH",
        "items": [{"description": "Kiste", "quantity": 1}]}).json()
    cn = admin_client.post(f"/api/invoices/{invoice['id']}/credit-note", json={}).json()

    for url in (f"/api/invoices/{invoice['id']}/pdf",
                f"/api/quotes/{quote['id']}/pdf",
                f"/api/delivery-notes/{dn['id']}/pdf",
                f"/api/credit-notes/{cn['id']}/pdf"):
        assert admin_client.get(f"{url}?template={tpl['id']}").content.startswith(b"%PDF")


def test_form_layout_previews_and_switches_back(admin_client):
    form = admin_client.post("/api/pdf-templates", json=FORM_TEMPLATE).json()
    assert admin_client.get(f"/api/pdf-templates/{form['id']}/preview"
                            ).content.startswith(b"%PDF")
    back = admin_client.put(f"/api/pdf-templates/{form['id']}",
                            json={**FORM_TEMPLATE, "layout": "standard"}).json()
    assert back["layout"] == "standard"


def test_form_layout_breaks_long_documents_into_pages():
    """Mehr Positionen als Zeilen im Kasten -> weitere Seiten, Summen nur
    auf der letzten."""
    from datetime import date

    from app import models, pdf, pdf_form

    invoice = models.Invoice(number="RE-2026-0001", customer_name="Viel GmbH",
                             customer_address="Weg 1\n12345 Ort", issue_date=date.today(),
                             tax_rate=20, status=models.STATUS_OPEN, paid_amount=0,
                             discount_percent=0, small_business=False,
                             skonto_percent=0, skonto_days=0)
    invoice.items = [models.InvoiceItem(description=f"Position {i}", quantity=1,
                                        unit_price=10)
                     for i in range(pdf_form.ROWS_PER_PAGE + 5)]

    class Formular:
        name = "Vordruck"
        layout = "formular"
        accent_color = "#0021c6"
        header_color = "#2d3748"
        font_family = "Helvetica"
        font_size = 10
        header_note = ""
        footer_text = ""
        show_logo = True
        show_qr = True

    def pages(document: bytes) -> int:
        return document.count(b"/Type /Page\n")

    data = pdf.invoice_pdf(invoice, None, Formular())
    assert data.startswith(b"%PDF")
    assert pages(data) == 2

    invoice.items = invoice.items[:1]
    assert pages(pdf.invoice_pdf(invoice, None, Formular())) == 1


def test_pdf_still_works_without_any_template(user_client):
    invoice = make_invoice(user_client)
    assert user_client.get(f"/api/invoices/{invoice['id']}/pdf").content.startswith(b"%PDF")
