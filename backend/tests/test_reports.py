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
