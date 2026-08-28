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


def test_pdf_still_works_without_any_template(user_client):
    invoice = make_invoice(user_client)
    assert user_client.get(f"/api/invoices/{invoice['id']}/pdf").content.startswith(b"%PDF")
