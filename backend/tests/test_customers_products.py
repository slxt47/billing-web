"""Kunden- und Artikelstammdaten inkl. DSGVO-Funktionen."""
from conftest import make_invoice

CUSTOMER = {
    "name": "Stamm GmbH",
    "address": "Hauptstr. 1\n12345 Ort",
    "email": "info@stamm.example",
    "contact_person": "Frau Stamm",
    "payment_term_days": 30,
    "skonto_percent": 2,
    "skonto_days": 7,
}


# --------------------------- Kunden ---------------------------------------
def test_create_and_list_customer(user_client):
    created = user_client.post("/api/customers", json=CUSTOMER).json()
    assert created["active"] is True
    assert created["payment_term_days"] == 30

    listing = user_client.get("/api/customers")
    assert [c["name"] for c in listing.json()] == ["Stamm GmbH"]
    assert listing.headers["X-Total-Count"] == "1"


def test_customer_needs_a_name(user_client):
    assert user_client.post("/api/customers", json={"name": ""}).status_code == 422


def test_edit_customer(user_client):
    customer = user_client.post("/api/customers", json=CUSTOMER).json()
    updated = user_client.put(f"/api/customers/{customer['id']}",
                              json={**CUSTOMER, "email": "neu@stamm.example"}).json()
    assert updated["email"] == "neu@stamm.example"


def test_deactivate_and_reactivate(user_client):
    customer = user_client.post("/api/customers", json=CUSTOMER).json()
    off = user_client.patch(f"/api/customers/{customer['id']}/active",
                            json={"active": False}).json()
    assert off["active"] is False
    assert user_client.get("/api/customers?active_only=true").json() == []
    assert len(user_client.get("/api/customers").json()) == 1

    on = user_client.patch(f"/api/customers/{customer['id']}/active",
                           json={"active": True}).json()
    assert on["active"] is True


def test_customer_search(user_client):
    user_client.post("/api/customers", json={**CUSTOMER, "name": "Alpha"})
    user_client.post("/api/customers", json={**CUSTOMER, "name": "Beta",
                                             "email": "kontakt@beta.example"})
    assert len(user_client.get("/api/customers?search=alpha").json()) == 1
    assert len(user_client.get("/api/customers?search=beta.example").json()) == 1
    assert len(user_client.get("/api/customers?search=Frau").json()) == 2   # Ansprechpartner


def test_delete_customer(user_client):
    customer = user_client.post("/api/customers", json=CUSTOMER).json()
    assert user_client.delete(f"/api/customers/{customer['id']}").status_code == 204
    assert user_client.get("/api/customers").json() == []
    assert user_client.delete(f"/api/customers/{customer['id']}").status_code == 404


# --------------------------- DSGVO ----------------------------------------
def test_export_is_admin_only(user_client):
    customer = user_client.post("/api/customers", json=CUSTOMER).json()
    response = user_client.get(f"/api/customers/{customer['id']}/export")
    assert response.status_code == 403


def test_admin_can_export_customer_data(admin_client):
    customer = admin_client.post("/api/customers", json=CUSTOMER).json()
    make_invoice(admin_client, customer_name=CUSTOMER["name"])

    export = admin_client.get(f"/api/customers/{customer['id']}/export").json()
    assert export["customer"]["name"] == CUSTOMER["name"]
    assert len(export["invoices"]) == 1
    assert export["exported_at"]

    log = admin_client.get("/api/audit-log").json()
    assert log[0]["action"] == "export"
    assert log[0]["username"] == "admin"


def test_anonymize_keeps_invoices(admin_client):
    customer = admin_client.post("/api/customers", json=CUSTOMER).json()
    invoice = make_invoice(admin_client, customer_name=CUSTOMER["name"])

    anonymized = admin_client.post(f"/api/customers/{customer['id']}/anonymize").json()
    assert CUSTOMER["name"] not in anonymized["name"]
    assert anonymized["email"] == ""
    # Rechnung bleibt aus steuerrechtlichen Gründen unverändert
    assert admin_client.get(f"/api/invoices/{invoice['id']}").json()["customer_name"] \
        == CUSTOMER["name"]
    assert any(e["action"] == "anonymize" for e in admin_client.get("/api/audit-log").json())


def test_anonymize_is_admin_only(user_client):
    customer = user_client.post("/api/customers", json=CUSTOMER).json()
    assert user_client.post(f"/api/customers/{customer['id']}/anonymize").status_code == 403


# --------------------------- Artikel --------------------------------------
def test_product_crud(user_client):
    product = user_client.post("/api/products",
                               json={"name": "Stundensatz", "unit_price": 95}).json()
    assert product["unit_price"] == 95.0
    assert product["active"] is True

    updated = user_client.put(f"/api/products/{product['id']}",
                              json={"name": "Stundensatz", "unit_price": 105}).json()
    assert updated["unit_price"] == 105.0

    off = user_client.patch(f"/api/products/{product['id']}/active",
                            json={"active": False}).json()
    assert off["active"] is False
    assert user_client.get("/api/products?active_only=true").json() == []

    assert user_client.delete(f"/api/products/{product['id']}").status_code == 204
    assert user_client.get("/api/products").json() == []


def test_product_search(user_client):
    user_client.post("/api/products", json={"name": "Schraube", "unit_price": 1})
    user_client.post("/api/products", json={"name": "Mutter", "unit_price": 2})
    assert len(user_client.get("/api/products?search=schrau").json()) == 1
    assert user_client.get("/api/products").headers["X-Total-Count"] == "2"


def test_product_price_must_not_be_negative(user_client):
    assert user_client.post("/api/products",
                            json={"name": "X", "unit_price": -1}).status_code == 422
