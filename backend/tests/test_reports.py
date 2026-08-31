"""Auswertungen: UStVA-Grundlage und Erlösrechnung."""
from datetime import date

from conftest import make_invoice

YEAR = date.today().year
PERIOD = {"from": f"{YEAR}-01-01", "to": f"{YEAR}-12-31"}


def test_vat_report_groups_by_tax_rate(user_client):
    make_invoice(user_client)                                   # 200 netto, 20 %
    make_invoice(user_client, tax_rate=10,
                 items=[{"description": "Buch", "quantity": 1, "unit_price": 50}])

    report = user_client.get("/api/reports/vat", params=PERIOD).json()
    by_rate = {r["tax_rate"]: r for r in report["rows"]}
    assert by_rate[20.0]["net"] == 200.0
    assert by_rate[20.0]["tax"] == 40.0
    assert by_rate[10.0]["net"] == 50.0
    assert by_rate[10.0]["tax"] == 5.0
    assert report["net_total"] == 250.0
    assert report["tax_total"] == 45.0
    # Vorsteuer kennt die App nicht – das muss die Auswertung auch sagen.
    assert report["input_tax_known"] is False


def test_vat_report_subtracts_credit_notes(user_client):
    invoice = make_invoice(user_client)
    user_client.post(f"/api/invoices/{invoice['id']}/credit-note", json={
        "items": [{"description": "Nachlass", "quantity": 1, "unit_price": 100}]})

    report = user_client.get("/api/reports/vat", params=PERIOD).json()
    row = report["rows"][0]
    assert row["net"] == 100.0          # 200 berechnet − 100 gutgeschrieben
    assert row["tax"] == 20.0
    assert row["credit_note_count"] == 1


def test_small_business_lands_in_the_zero_bucket(user_client):
    make_invoice(user_client, small_business=True)
    report = user_client.get("/api/reports/vat", params=PERIOD).json()
    assert [r["tax_rate"] for r in report["rows"]] == [0.0]
    assert report["tax_total"] == 0.0


def test_cancelled_documents_are_left_out(user_client):
    invoice = make_invoice(user_client)
    user_client.patch(f"/api/invoices/{invoice['id']}/status", json={"status": "storniert"})
    report = user_client.get("/api/reports/vat", params=PERIOD).json()
    assert report["net_total"] == 0.0


def test_period_limits_the_result(user_client):
    make_invoice(user_client)
    empty = user_client.get("/api/reports/vat",
                            params={"from": "2000-01-01", "to": "2000-12-31"}).json()
    assert empty["rows"] == []
    assert empty["net_total"] == 0.0


def test_reversed_period_is_rejected(user_client):
    assert user_client.get("/api/reports/vat",
                           params={"from": f"{YEAR}-12-31",
                                   "to": f"{YEAR}-01-01"}).status_code == 400


def test_revenue_report_by_month_and_customer(user_client):
    make_invoice(user_client, customer_name="Alpha AG")
    make_invoice(user_client, customer_name="Beta GmbH",
                 items=[{"description": "Schulung", "quantity": 1, "unit_price": 300}])

    report = user_client.get("/api/reports/revenue", params=PERIOD).json()
    assert report["invoice_count"] == 2
    assert report["invoiced_net"] == 500.0
    assert report["net"] == 500.0
    assert report["gross"] == 600.0
    assert [c["customer_name"] for c in report["customers"]] == ["Beta GmbH", "Alpha AG"]
    assert len(report["months"]) == 1
    assert report["months"][0]["net"] == 500.0
    # Ohne Ausgabenerfassung ist das kein Betriebsergebnis.
    assert report["expenses_tracked"] is False


def test_revenue_report_counts_payments_and_credits(user_client):
    invoice = make_invoice(user_client)                     # 240 brutto
    user_client.post(f"/api/invoices/{invoice['id']}/payment", json={"amount": 100})
    user_client.post(f"/api/invoices/{invoice['id']}/credit-note", json={
        "items": [{"description": "Nachlass", "quantity": 1, "unit_price": 50}]})

    report = user_client.get("/api/reports/revenue", params=PERIOD).json()
    assert report["paid"] == 100.0
    assert report["credited_net"] == 50.0
    assert report["net"] == 150.0
    assert report["open_amount"] == 80.0                    # 240 − 100 − 60
    assert report["credit_note_count"] == 1


def test_reports_default_to_the_current_year(user_client):
    make_invoice(user_client)
    report = user_client.get("/api/reports/revenue").json()
    assert report["from"] == f"{YEAR}-01-01"
    assert report["net"] == 200.0


def test_csv_exports(user_client):
    make_invoice(user_client)
    vat = user_client.get("/api/reports/vat.csv", params=PERIOD)
    assert vat.headers["content-type"].startswith("text/csv")
    assert "UStVA" in vat.headers["content-disposition"]
    text = vat.content.decode("utf-8")
    assert text.startswith("﻿")            # BOM für Excel
    assert "200,00" in text                     # deutsches Dezimalkomma
    assert "Vorsteuer" in text

    revenue = user_client.get("/api/reports/revenue.csv", params=PERIOD)
    assert "Erloese" in revenue.headers["content-disposition"]
    assert "Muster GmbH" in revenue.content.decode("utf-8")


# --------------------------- Freier Report-Builder -------------------------
def test_custom_report_lists_documents_without_grouping(user_client):
    make_invoice(user_client, customer_name="Alpha AG")
    make_invoice(user_client, customer_name="Beta GmbH",
                 items=[{"description": "B", "quantity": 1, "unit_price": 50}])

    report = user_client.get("/api/reports/custom",
                             params={**PERIOD, "doc_type": "invoice"}).json()
    assert report["group_by"] == "none"
    assert report["has_amounts"] is True
    assert len(report["rows"]) == 2
    assert {r["customer_name"] for r in report["rows"]} == {"Alpha AG", "Beta GmbH"}
    assert report["totals"]["count"] == 2
    assert report["totals"]["net"] == 250.0


def test_custom_report_groups_by_customer(user_client):
    make_invoice(user_client, customer_name="Alpha AG")
    make_invoice(user_client, customer_name="Alpha AG",
                 items=[{"description": "B", "quantity": 1, "unit_price": 50}])
    make_invoice(user_client, customer_name="Beta GmbH")

    report = user_client.get("/api/reports/custom", params={
        **PERIOD, "doc_type": "invoice", "group_by": "customer"}).json()
    by_customer = {r["group"]: r for r in report["rows"]}
    assert by_customer["Alpha AG"]["count"] == 2
    assert by_customer["Alpha AG"]["net"] == 250.0
    assert by_customer["Beta GmbH"]["count"] == 1


def test_custom_report_groups_by_month_and_by_status(user_client):
    make_invoice(user_client)
    inv = make_invoice(user_client)
    user_client.patch(f"/api/invoices/{inv['id']}/status", json={"status": "bezahlt"})

    by_month = user_client.get("/api/reports/custom", params={
        **PERIOD, "doc_type": "invoice", "group_by": "month"}).json()
    assert len(by_month["rows"]) == 1              # beide im selben Monat
    assert by_month["rows"][0]["count"] == 2

    by_status = user_client.get("/api/reports/custom", params={
        **PERIOD, "doc_type": "invoice", "group_by": "status"}).json()
    counts = {r["group"]: r["count"] for r in by_status["rows"]}
    assert counts["offen"] == 1
    assert counts["bezahlt"] == 1


def test_custom_report_status_filter(user_client):
    inv = make_invoice(user_client)
    make_invoice(user_client)
    user_client.patch(f"/api/invoices/{inv['id']}/status", json={"status": "bezahlt"})

    report = user_client.get("/api/reports/custom", params={
        **PERIOD, "doc_type": "invoice", "status": "bezahlt"}).json()
    assert len(report["rows"]) == 1
    assert report["rows"][0]["status"] == "bezahlt"


def test_custom_report_covers_quotes_delivery_notes_and_credit_notes(user_client):
    invoice = make_invoice(user_client)
    user_client.post("/api/quotes", json={
        "customer_name": "Angebot AG",
        "items": [{"description": "Konzept", "quantity": 1, "unit_price": 300}]})
    user_client.post("/api/delivery-notes", json={
        "customer_name": "Liefer GmbH",
        "items": [{"description": "Kiste Schrauben", "quantity": 3}]})
    user_client.post(f"/api/invoices/{invoice['id']}/credit-note", json={"reason": "Kulanz"})

    quotes = user_client.get("/api/reports/custom",
                             params={**PERIOD, "doc_type": "quote"}).json()
    assert quotes["has_amounts"] is True
    assert quotes["totals"]["count"] == 1

    notes = user_client.get("/api/reports/custom",
                            params={**PERIOD, "doc_type": "delivery_note"}).json()
    assert notes["has_amounts"] is False
    assert "net" not in notes["rows"][0]
    assert notes["totals"]["net"] is None
    assert notes["totals"]["item_count"] == 1

    credits = user_client.get("/api/reports/custom",
                              params={**PERIOD, "doc_type": "credit_note"}).json()
    assert credits["totals"]["count"] == 1
    assert credits["totals"]["gross"] == 240.0


def test_custom_report_rejects_unknown_doc_type_or_group_by(user_client):
    bad_type = user_client.get("/api/reports/custom",
                               params={**PERIOD, "doc_type": "expense"})
    assert bad_type.status_code == 400

    bad_group = user_client.get("/api/reports/custom", params={
        **PERIOD, "doc_type": "invoice", "group_by": "planet"})
    assert bad_group.status_code == 400


def test_custom_report_csv_export(user_client):
    make_invoice(user_client)
    response = user_client.get("/api/reports/custom.csv",
                               params={**PERIOD, "doc_type": "invoice"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    text = response.content.decode("utf-8-sig")
    assert "Freier Report" in text
    assert "Muster GmbH" in text
    assert "200,00" in text
