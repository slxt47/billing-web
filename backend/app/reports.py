"""Auswertungen: Umsatzsteuer-Voranmeldung (UStVA) und Erlösrechnung.

Beides rechnet auf denselben Grundlagen: Rechnungen mit Belegdatum im
Zeitraum (ohne stornierte) minus Gutschriften mit Belegdatum im Zeitraum
(ohne stornierte). Gerechnet wird nach Soll-Versteuerung, also nach
Rechnungsdatum und nicht nach Zahlungseingang.

Die Erlösrechnung ist bewusst nur die Einnahmenseite: Ausgaben erfasst die
App nicht, eine vollständige Gewinn-und-Verlust-Rechnung ist damit nicht
möglich (siehe TODO.md).
"""
from calendar import monthrange
from datetime import date

from sqlalchemy.orm import Session

from . import models


def _invoices(db: Session, start: date, end: date) -> list[models.Invoice]:
    return (
        db.query(models.Invoice)
        .filter(models.Invoice.status != models.STATUS_CANCELLED)
        .filter(models.Invoice.issue_date >= start)
        .filter(models.Invoice.issue_date <= end)
        .order_by(models.Invoice.issue_date.asc())
        .all()
    )


def _credit_notes(db: Session, start: date, end: date) -> list[models.CreditNote]:
    return (
        db.query(models.CreditNote)
        .filter(models.CreditNote.status != models.CN_CANCELLED)
        .filter(models.CreditNote.issue_date >= start)
        .filter(models.CreditNote.issue_date <= end)
        .order_by(models.CreditNote.issue_date.asc())
        .all()
    )


def _rate_key(doc) -> float:
    """Steuersatz eines Belegs. Kleinunternehmer (§19 UStG) landen bei 0 %,
    weil sie keine Umsatzsteuer ausweisen."""
    return 0.0 if doc.small_business else round(float(doc.tax_rate or 0), 2)


def month_range(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, monthrange(year, month)[1])


def vat_report(db: Session, start: date, end: date) -> dict:
    """UStVA-Grundlage: Netto und Umsatzsteuer je Steuersatz.

    Gutschriften werden abgezogen – sie mindern sowohl die
    Bemessungsgrundlage als auch die Steuer im Zeitraum, in dem sie
    ausgestellt wurden."""
    buckets: dict[float, dict] = {}

    def bucket(rate: float) -> dict:
        return buckets.setdefault(rate, {
            "tax_rate": rate, "net": 0.0, "tax": 0.0, "gross": 0.0,
            "invoice_count": 0, "credit_note_count": 0,
        })

    for inv in _invoices(db, start, end):
        b = bucket(_rate_key(inv))
        b["net"] += inv.net
        b["tax"] += inv.tax_amount
        b["gross"] += inv.total
        b["invoice_count"] += 1

    for cn in _credit_notes(db, start, end):
        b = bucket(_rate_key(cn))
        b["net"] -= cn.net
        b["tax"] -= cn.tax_amount
        b["gross"] -= cn.total
        b["credit_note_count"] += 1

    rows = []
    for rate in sorted(buckets, reverse=True):
        b = buckets[rate]
        rows.append({**b, "net": round(b["net"], 2), "tax": round(b["tax"], 2),
                     "gross": round(b["gross"], 2)})

    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "rows": rows,
        "net_total": round(sum(r["net"] for r in rows), 2),
        "tax_total": round(sum(r["tax"] for r in rows), 2),
        "gross_total": round(sum(r["gross"] for r in rows), 2),
        # Vorsteuer kann die App nicht kennen: Ausgaben werden nicht erfasst.
        "input_tax_known": False,
    }


def revenue_report(db: Session, start: date, end: date) -> dict:
    """Erlösrechnung: Rechnungsbeträge minus Gutschriften, aufgeschlüsselt
    nach Monat und nach Kunde, dazu Zahlungsstand."""
    invoices = _invoices(db, start, end)
    credit_notes = _credit_notes(db, start, end)

    months: dict[str, dict] = {}
    customers: dict[str, dict] = {}

    def month_row(day: date) -> dict:
        key = f"{day.year}-{day.month:02d}"
        return months.setdefault(key, {
            "label": f"{day.month:02d}/{day.year}", "key": key,
            "invoiced_net": 0.0, "invoiced_gross": 0.0,
            "credited_net": 0.0, "credited_gross": 0.0,
        })

    def customer_row(name: str) -> dict:
        return customers.setdefault(name, {
            "customer_name": name, "invoiced_net": 0.0, "invoiced_gross": 0.0,
            "credited_net": 0.0, "credited_gross": 0.0, "invoice_count": 0,
        })

    paid = open_amount = 0.0
    for inv in invoices:
        m, c = month_row(inv.issue_date), customer_row(inv.customer_name)
        for row in (m, c):
            row["invoiced_net"] += inv.net
            row["invoiced_gross"] += inv.total
        c["invoice_count"] += 1
        paid += float(inv.paid_amount or 0)
        open_amount += max(inv.remaining, 0)

    for cn in credit_notes:
        m, c = month_row(cn.issue_date), customer_row(cn.customer_name)
        for row in (m, c):
            row["credited_net"] += cn.net
            row["credited_gross"] += cn.total

    def finish(row: dict) -> dict:
        row = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in row.items()}
        row["net"] = round(row["invoiced_net"] - row["credited_net"], 2)
        row["gross"] = round(row["invoiced_gross"] - row["credited_gross"], 2)
        return row

    month_rows = [finish(months[k]) for k in sorted(months)]
    customer_rows = sorted((finish(c) for c in customers.values()),
                           key=lambda r: r["net"], reverse=True)

    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "months": month_rows,
        "customers": customer_rows,
        "invoiced_net": round(sum(r["invoiced_net"] for r in month_rows), 2),
        "credited_net": round(sum(r["credited_net"] for r in month_rows), 2),
        "net": round(sum(r["net"] for r in month_rows), 2),
        "gross": round(sum(r["gross"] for r in month_rows), 2),
        "paid": round(paid, 2),
        "open_amount": round(open_amount, 2),
        "invoice_count": len(invoices),
        "credit_note_count": len(credit_notes),
        # Ohne Ausgabenerfassung ist das der Rohertrag, nicht der Gewinn.
        "expenses_tracked": False,
    }


def _csv(rows: list[list]) -> str:
    """CSV im hier üblichen Format: Semikolon, deutsche Dezimalkommas, BOM
    für Excel."""
    def cell(value):
        if isinstance(value, float):
            return f"{value:.2f}".replace(".", ",")
        return str(value)
    return "﻿" + "\r\n".join(";".join(cell(c) for c in row) for row in rows)


def vat_report_csv(report: dict) -> str:
    rows = [["UStVA-Auswertung", f"{report['from']} bis {report['to']}"],
            [], ["Steuersatz", "Netto", "Umsatzsteuer", "Brutto",
                 "Rechnungen", "Gutschriften"]]
    for r in report["rows"]:
        rows.append([f"{r['tax_rate']:g}%", r["net"], r["tax"], r["gross"],
                     r["invoice_count"], r["credit_note_count"]])
    rows.append(["Summe", report["net_total"], report["tax_total"],
                 report["gross_total"], "", ""])
    rows.append([])
    rows.append(["Hinweis", "Vorsteuer ist nicht enthalten: Ausgaben werden "
                            "in dieser App nicht erfasst."])
    return _csv(rows)


def revenue_report_csv(report: dict) -> str:
    rows = [["Erlösauswertung", f"{report['from']} bis {report['to']}"],
            [], ["Monat", "Berechnet netto", "Gutschriften netto", "Erlös netto",
                 "Erlös brutto"]]
    for m in report["months"]:
        rows.append([m["label"], m["invoiced_net"], m["credited_net"],
                     m["net"], m["gross"]])
    rows.append(["Summe", report["invoiced_net"], report["credited_net"],
                 report["net"], report["gross"]])
    rows.append([])
    rows.append(["Kunde", "Berechnet netto", "Gutschriften netto", "Erlös netto",
                 "Rechnungen"])
    for c in report["customers"]:
        rows.append([c["customer_name"], c["invoiced_net"], c["credited_net"],
                     c["net"], c["invoice_count"]])
    rows.append([])
    rows.append(["Hinweis", "Erlösseite ohne Ausgaben – kein vollständiges "
                            "Betriebsergebnis."])
    return _csv(rows)
