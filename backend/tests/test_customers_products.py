"""Kunden- und Artikelstammdaten inkl. DSGVO-Funktionen."""
import json

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


# --------------------------- CSV-Import -----------------------------------
def upload_csv(client, content: str, name: str = "kunden.csv", encoding: str = "utf-8"):
    return client.post("/api/customers/import",
                       files={"file": (name, content.encode(encoding), "text/csv")})


def test_import_creates_customers(user_client):
    csv_content = (
        "Name;E-Mail;Ansprechpartner;Anschrift;Zahlungsfrist;Skonto;Skonto_Tage\r\n"
        "Import GmbH;info@import.example;Frau Import;Importweg 1, 12345 Ort;30;2,5;7\r\n"
        "Zweite AG;;;;;;\r\n"
    )
    result = upload_csv(user_client, csv_content).json()
    assert (result["created"], result["updated"], result["skipped"]) == (2, 0, 0)

    customers = {c["name"]: c for c in user_client.get("/api/customers").json()}
    assert customers["Import GmbH"]["email"] == "info@import.example"
    assert customers["Import GmbH"]["contact_person"] == "Frau Import"
    assert customers["Import GmbH"]["payment_term_days"] == 30
    assert customers["Import GmbH"]["skonto_percent"] == 2.5
    assert customers["Import GmbH"]["skonto_days"] == 7
    # Ohne Angaben gelten die Vorgabewerte aus CustomerIn
    assert customers["Zweite AG"]["payment_term_days"] == 14


def test_import_updates_existing_customer_instead_of_duplicating(user_client):
    user_client.post("/api/customers", json=CUSTOMER)
    result = upload_csv(user_client,
                        "name,email\nstamm gmbh,neu@stamm.example\n").json()
    assert (result["created"], result["updated"]) == (0, 1)

    listing = user_client.get("/api/customers").json()
    assert len(listing) == 1
    assert listing[0]["email"] == "neu@stamm.example"


def test_import_accepts_english_headers_and_comma_delimiter(user_client):
    result = upload_csv(user_client,
                        "customer_name,address,payment_term_days\n"
                        "Comma Ltd,\"Some Street 1, 12345 Town\",21\n").json()
    assert result["created"] == 1
    customer = user_client.get("/api/customers").json()[0]
    assert customer["address"] == "Some Street 1, 12345 Town"
    assert customer["payment_term_days"] == 21


def test_import_skips_rows_without_name_and_reports_bad_values(user_client):
    csv_content = ("name;zahlungsfrist\n"
                   ";30\n"
                   "Krumme GmbH;minus drei\n"
                   "Gute GmbH;10\n")
    result = upload_csv(user_client, csv_content).json()
    assert result["created"] == 1
    assert result["skipped"] == 2
    assert any("Krumme GmbH" in e for e in result["errors"])
    assert [c["name"] for c in user_client.get("/api/customers").json()] == ["Gute GmbH"]


def test_import_reads_windows_encoded_files(user_client):
    result = upload_csv(user_client, "name\nMüller & Söhne KG\n",
                        encoding="cp1252").json()
    assert result["created"] == 1
    assert user_client.get("/api/customers").json()[0]["name"] == "Müller & Söhne KG"


def test_import_reads_the_json_export_of_a_customer(admin_client):
    """Was der DSGVO-Export ausgibt, muss der Import auch wieder annehmen."""
    customer = admin_client.post("/api/customers", json=CUSTOMER).json()
    exported = admin_client.get(f"/api/customers/{customer['id']}/export").json()
    admin_client.delete(f"/api/customers/{customer['id']}")
    assert admin_client.get("/api/customers").json() == []

    result = admin_client.post(
        "/api/customers/import",
        files={"file": ("kunde-1-export.json", json.dumps(exported).encode(),
                        "application/json")},
    ).json()
    assert (result["created"], result["updated"]) == (1, 0)

    restored = admin_client.get("/api/customers").json()[0]
    assert restored["name"] == CUSTOMER["name"]
    assert restored["email"] == CUSTOMER["email"]
    assert restored["payment_term_days"] == 30
    assert restored["skonto_percent"] == 2


def test_import_reads_a_json_list_and_updates_by_name(user_client):
    user_client.post("/api/customers", json=CUSTOMER)
    payload = [
        {"name": "Stamm GmbH", "email": "neu@stamm.example"},
        {"Name": "Neu AG", "Zahlungsfrist": 21},
    ]
    result = user_client.post(
        "/api/customers/import",
        files={"file": ("kunden.json", json.dumps(payload).encode(), "application/json")},
    ).json()
    assert (result["created"], result["updated"]) == (1, 1)

    customers = {c["name"]: c for c in user_client.get("/api/customers").json()}
    assert customers["Stamm GmbH"]["email"] == "neu@stamm.example"
    assert customers["Neu AG"]["payment_term_days"] == 21


def test_import_accepts_the_json_example_file(user_client):
    """Die Vorlage, die das Frontend anbietet (app.js: EXAMPLE_CUSTOMERS),
    muss sich ohne Nacharbeit wieder einlesen lassen."""
    payload = {"customers": [
        {"name": "Muster GmbH", "email": "info@muster.example",
         "contact_person": "Frau Muster", "address": "Musterweg 1, 12345 Musterstadt",
         "payment_term_days": 30, "skonto_percent": 2, "skonto_days": 7},
        {"name": "Beispiel AG", "email": "kontakt@beispiel.example",
         "contact_person": "Herr Beispiel", "address": "Beispielstr. 2, 54321 Beispielstadt",
         "payment_term_days": 14, "skonto_percent": 0, "skonto_days": 0},
    ]}
    result = user_client.post(
        "/api/customers/import",
        files={"file": ("kunden-vorlage.json", json.dumps(payload).encode(),
                        "application/json")},
    ).json()
    assert (result["created"], result["updated"], result["skipped"]) == (2, 0, 0)

    customers = {c["name"]: c for c in user_client.get("/api/customers").json()}
    assert customers["Muster GmbH"]["payment_term_days"] == 30
    assert customers["Muster GmbH"]["skonto_days"] == 7
    assert customers["Beispiel AG"]["email"] == "kontakt@beispiel.example"


def test_import_accepts_the_csv_example_file(user_client):
    """Gegenstück zur JSON-Vorlage: dieselben Daten als CSV (app.js:
    EXAMPLE_CSV_HEADER)."""
    csv_content = (
        "Name;E-Mail;Ansprechpartner;Anschrift;Zahlungsfrist;Skonto;Skonto_Tage\r\n"
        "Muster GmbH;info@muster.example;Frau Muster;"
        "Musterweg 1, 12345 Musterstadt;30;2;7\r\n"
        "Beispiel AG;kontakt@beispiel.example;Herr Beispiel;"
        "Beispielstr. 2, 54321 Beispielstadt;14;0;0\r\n"
    )
    result = upload_csv(user_client, csv_content, name="kunden-vorlage.csv").json()
    assert (result["created"], result["updated"], result["skipped"]) == (2, 0, 0)


def test_import_rejects_broken_json(user_client):
    response = user_client.post(
        "/api/customers/import",
        files={"file": ("kunden.json", b'{"name": ', "application/json")})
    assert response.status_code == 400
    assert "JSON" in response.json()["detail"]


def test_import_rejects_json_without_a_name(user_client):
    response = user_client.post(
        "/api/customers/import",
        files={"file": ("kunden.json", b'[{"stadt": "Wien"}]', "application/json")})
    assert response.status_code == 400
    assert "Kundennamen" in response.json()["detail"]


def test_import_without_name_column_is_rejected(user_client):
    response = upload_csv(user_client, "spalte1;spalte2\na;b\n")
    assert response.status_code == 400
    assert "Kundennamen" in response.json()["detail"]


def test_import_rejects_empty_file(user_client):
    assert upload_csv(user_client, "").status_code == 400


def test_import_is_written_to_the_audit_log(admin_client):
    upload_csv(admin_client, "name\nProtokoll GmbH\n")
    entries = admin_client.get("/api/audit-log").json()
    assert any(e["action"] == "import" and e["target_type"] == "customer"
               for e in entries)


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


# --------------------------- Massenexport (CSV) ----------------------------
def test_export_customers_csv_round_trips_through_import(user_client):
    user_client.post("/api/customers", json=CUSTOMER)
    user_client.post("/api/customers", json={**CUSTOMER, "name": "Zweite GmbH",
                                              "email": "", "skonto_percent": 0,
                                              "skonto_days": 0})
    exported = user_client.get("/api/customers/export.csv")
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("text/csv")
    text = exported.content.decode("utf-8-sig")
    assert "Stamm GmbH" in text and "Zweite GmbH" in text
    assert "Status" in text.splitlines()[0]

    for c in user_client.get("/api/customers").json():
        user_client.delete(f"/api/customers/{c['id']}")
    assert user_client.get("/api/customers").json() == []

    result = user_client.post(
        "/api/customers/import",
        files={"file": ("export.csv", exported.content, "text/csv")}).json()
    assert (result["created"], result["updated"]) == (2, 0)
    names = {c["name"] for c in user_client.get("/api/customers").json()}
    assert names == {"Stamm GmbH", "Zweite GmbH"}


def test_export_customers_csv_escapes_special_characters(user_client):
    """Ein Semikolon im Namen darf die Spalten nicht verschieben (echtes
    CSV-Quoting statt manuellem Zusammenkleben)."""
    user_client.post("/api/customers", json={**CUSTOMER, "name": "A; B GmbH",
                                              "contact_person": 'Herr "Chef"'})
    exported = user_client.get("/api/customers/export.csv")
    row = exported.content.decode("utf-8-sig").splitlines()[1]
    assert row.startswith('"A; B GmbH"')


def test_export_products_csv(user_client):
    user_client.post("/api/products", json={"name": "Schraube", "unit_price": 1.5})
    exported = user_client.get("/api/products/export.csv")
    assert exported.status_code == 200
    text = exported.content.decode("utf-8-sig")
    assert "Schraube" in text
    assert "1.5" in text or "1,5" in text


# --------------------------- Artikelimport ---------------------------------
def test_import_creates_and_updates_products(user_client):
    user_client.post("/api/products", json={"name": "Stundensatz", "unit_price": 80})
    csv_content = ("Name;Standardpreis\r\n"
                   "Stundensatz;95\r\n"
                   "Anfahrt;25,5\r\n")
    result = user_client.post(
        "/api/products/import",
        files={"file": ("artikel.csv", csv_content.encode(), "text/csv")}).json()
    assert (result["created"], result["updated"], result["skipped"]) == (1, 1, 0)

    products = {p["name"]: p for p in user_client.get("/api/products").json()}
    assert products["Stundensatz"]["unit_price"] == 95.0
    assert products["Anfahrt"]["unit_price"] == 25.5


def test_import_products_accepts_json_and_english_headers(user_client):
    payload = [{"product_name": "Beratung", "price": "120"}]
    result = user_client.post(
        "/api/products/import",
        files={"file": ("artikel.json", json.dumps(payload).encode(),
                        "application/json")}).json()
    assert result["created"] == 1
    assert user_client.get("/api/products").json()[0]["unit_price"] == 120.0


def test_import_products_without_name_column_is_rejected(user_client):
    response = user_client.post(
        "/api/products/import",
        files={"file": ("artikel.csv", b"spalte1;spalte2\na;b\n", "text/csv")})
    assert response.status_code == 400
    assert "Artikelbezeichnung" in response.json()["detail"]


def test_import_products_skips_invalid_rows(user_client):
    csv_content = "name;preis\n;10\nGut;5\nSchlecht;minus fünf\n"
    result = user_client.post(
        "/api/products/import",
        files={"file": ("artikel.csv", csv_content.encode(), "text/csv")}).json()
    assert result["created"] == 1
    assert result["skipped"] == 2
    assert [p["name"] for p in user_client.get("/api/products").json()] == ["Gut"]


def test_import_products_round_trips_through_export(user_client):
    product = user_client.post("/api/products",
                               json={"name": "Material", "unit_price": 42}).json()
    exported = user_client.get("/api/products/export.csv")
    user_client.delete(f"/api/products/{product['id']}")
    assert user_client.get("/api/products").json() == []

    result = user_client.post(
        "/api/products/import",
        files={"file": ("export.csv", exported.content, "text/csv")}).json()
    assert result["created"] == 1
    assert user_client.get("/api/products").json()[0]["name"] == "Material"


def test_product_import_is_written_to_the_audit_log(admin_client):
    admin_client.post(
        "/api/products/import",
        files={"file": ("artikel.csv", b"name\nProtokoll-Artikel\n", "text/csv")})
    entries = admin_client.get("/api/audit-log").json()
    assert any(e["action"] == "import" and e["target_type"] == "product"
              for e in entries)
