"""PDF-Erzeugung für eine Rechnung (reportlab) inkl. Firmenkopf, Logo,
Rabatt, Kleinunternehmer-Hinweis, Zahlungsstatus und GiroCode (EPC-QR)."""
from io import BytesIO

import qrcode
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
)

from . import models

PAGE_W, PAGE_H = A4
MARGIN = 20 * mm


def _euro(value) -> str:
    return f"{float(value):,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def _date(value) -> str:
    return value.strftime("%d/%m/%Y")


def _giro_qr(invoice, settings):
    """EPC-/GiroCode-QR als PNG-BytesIO, oder None wenn Daten fehlen."""
    if not settings or not settings.iban or not settings.company_name:
        return None
    amount = invoice.remaining if invoice.remaining > 0 else invoice.total
    if amount <= 0:
        return None
    payload = "\n".join([
        "BCD", "002", "1", "SCT",
        (settings.bic or ""),
        settings.company_name[:70],
        settings.iban.replace(" ", ""),
        f"EUR{amount:.2f}",
        "", "",
        f"Rechnung {invoice.number}",
        "",
    ])
    img = qrcode.make(payload)
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def _footer_factory(settings):
    def _footer(canvas, doc):
        canvas.saveState()
        y = MARGIN
        canvas.setStrokeColor(colors.HexColor("#cbd5e0"))
        canvas.setLineWidth(0.5)
        canvas.line(MARGIN, y + 6 * mm, PAGE_W - MARGIN, y + 6 * mm)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.grey)
        # Bankverbindung / Steuerdaten in der Fußzeile
        bits = []
        if settings and settings.iban:
            bits.append(f"IBAN {settings.iban}")
        if settings and settings.bic:
            bits.append(f"BIC {settings.bic}")
        if settings and settings.vat_id:
            bits.append(f"USt-IdNr. {settings.vat_id}")
        elif settings and settings.tax_id:
            bits.append(f"St.-Nr. {settings.tax_id}")
        canvas.drawString(MARGIN, y, "  ·  ".join(bits) or "Vielen Dank für Ihren Auftrag.")
        canvas.drawRightString(PAGE_W - MARGIN, y, f"Seite {doc.page}")
        canvas.restoreState()
    return _footer


def invoice_pdf(invoice: models.Invoice, settings=None) -> bytes:
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=MARGIN + 12 * mm,
        title=f"Rechnung {invoice.number}",
    )

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Title"], fontSize=24,
                        textColor=colors.HexColor("#2d3748"), spaceAfter=2)
    section = ParagraphStyle("section", parent=styles["Heading2"], fontSize=12,
                             textColor=colors.HexColor("#2d6cdf"),
                             spaceBefore=2, spaceAfter=6)
    normal = styles["Normal"]
    normal.leading = 14
    body = ParagraphStyle("body", parent=normal, leftIndent=4 * mm, leading=15)
    right = ParagraphStyle("right", parent=normal, alignment=2, leading=13)

    story = []

    # ---- Firmenkopf (Logo rechts, Absender links) -----------------------
    if settings and (settings.company_name or settings.logo):
        comp = []
        if settings.company_name:
            comp.append(f"<b>{settings.company_name}</b>")
        for line in (settings.address or "").splitlines():
            comp.append(line)
        contact = []
        if settings.phone:
            contact.append(f"Tel. {settings.phone}")
        if settings.email:
            contact.append(settings.email)
        if contact:
            comp.append("  ·  ".join(contact))
        comp_para = Paragraph("<br/>".join(comp) or "", normal)

        logo_cell = ""
        if settings.logo:
            try:
                logo_cell = Image(BytesIO(settings.logo), hAlign="RIGHT")
                logo_cell._restrictSize(45 * mm, 25 * mm)
            except Exception:
                logo_cell = ""
        header = Table([[comp_para, logo_cell]], colWidths=[110 * mm, 50 * mm])
        header.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ]))
        story.append(header)
        story.append(Spacer(1, 8 * mm))

    # ---- Titel ----------------------------------------------------------
    story.append(Paragraph("RECHNUNG", h1))
    story.append(Spacer(1, 8 * mm))

    if invoice.status == models.STATUS_CANCELLED:
        story.append(Paragraph(
            "<b>STORNIERT</b> – diese Rechnung ist ungültig.",
            ParagraphStyle("storno", parent=normal, textColor=colors.red, fontSize=13),
        ))
        story.append(Spacer(1, 6 * mm))

    # ---- Kopf-Infos -----------------------------------------------------
    head = [
        [Paragraph("<b>Rechnungsnummer:</b>", normal), invoice.number],
        [Paragraph("<b>Datum:</b>", normal), _date(invoice.issue_date)],
    ]
    if invoice.due_date:
        head.append([Paragraph("<b>Fällig am:</b>", normal), _date(invoice.due_date)])
    head.append([Paragraph("<b>Status:</b>", normal), invoice.status])
    head_tbl = Table(head, colWidths=[42 * mm, 80 * mm])
    head_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (0, -1), 0),
        ("LEFTPADDING", (1, 0), (1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ]))
    story.append(head_tbl)
    story.append(Spacer(1, 10 * mm))

    # ---- Kunde ----------------------------------------------------------
    story.append(Paragraph("Rechnung an", section))
    story.append(Paragraph(f"<b>{invoice.customer_name}</b>", body))
    for line in (invoice.customer_address or "").splitlines():
        story.append(Paragraph(line, body))
    story.append(Spacer(1, 10 * mm))

    # ---- Positionen -----------------------------------------------------
    story.append(Paragraph("Positionen", section))
    data = [["Beschreibung", "Menge", "Einzelpreis", "Summe"]]
    for it in invoice.items:
        data.append([
            it.description, f"{float(it.quantity):g}",
            _euro(it.unit_price), _euro(it.line_total),
        ])

    def total_row(label, value, bold=False):
        lab = f"<b>{label}</b>" if bold else label
        val = f"<b>{_euro(value)}</b>" if bold else _euro(value)
        return ["", Paragraph(lab, normal), "", Paragraph(val, normal)]

    first_total = len(data)
    data.append(total_row("Zwischensumme", invoice.subtotal))
    if float(invoice.discount_percent or 0) > 0:
        data.append(total_row(f"abzgl. {float(invoice.discount_percent):g}% Rabatt",
                              -invoice.discount_amount))
        data.append(total_row("Nettobetrag", invoice.net))
    if invoice.small_business:
        data.append(["", Paragraph("<i>Gemäß §19 UStG wird keine Umsatzsteuer berechnet.</i>", normal), "", ""])
    else:
        data.append(total_row(f"zzgl. {float(invoice.tax_rate):g}% MwSt.", invoice.tax_amount))
    data.append(total_row("Gesamt", invoice.total, bold=True))

    n_total_rows = len(data) - first_total
    tbl = Table(data, colWidths=[80 * mm, 35 * mm, 20 * mm, 30 * mm])
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2d3748")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, first_total - 1), [colors.white, colors.HexColor("#f5f7fa")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (0, -1), 6),
        ("RIGHTPADDING", (-1, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LINEABOVE", (1, first_total), (-1, first_total), 0.5, colors.grey),
        ("LINEABOVE", (1, -1), (-1, -1), 1, colors.HexColor("#2d3748")),
    ]
    for r in range(first_total, len(data)):  # Summenzeilen-Label über 2 Spalten
        style.append(("SPAN", (1, r), (2, r)))
    tbl.setStyle(TableStyle(style))
    story.append(tbl)

    # ---- Zahlungsstatus (Teilzahlung) -----------------------------------
    if float(invoice.paid_amount or 0) > 0 and invoice.status != models.STATUS_CANCELLED:
        story.append(Spacer(1, 5 * mm))
        story.append(Paragraph(
            f"Bereits gezahlt: {_euro(invoice.paid_amount)} – "
            f"<b>Offener Betrag: {_euro(invoice.remaining)}</b>", body))

    # ---- Zahlungsziel ---------------------------------------------------
    if invoice.due_date:
        days = (invoice.due_date - invoice.issue_date).days
        within = f" innerhalb von {days} Tagen" if days > 0 else ""
        story.append(Spacer(1, 6 * mm))
        story.append(Paragraph(
            f"<b>Zahlbar{within} bis zum {_date(invoice.due_date)} ohne Abzug.</b>", body))

    # ---- Skonto ---------------------------------------------------------
    if invoice.has_skonto:
        story.append(Spacer(1, 2 * mm if invoice.due_date else 6 * mm))
        story.append(Paragraph(
            f"<b>Skonto:</b> Bei Zahlung bis {_date(invoice.skonto_date)} "
            f"(innerhalb {int(invoice.skonto_days)} Tagen) gewähren wir "
            f"{float(invoice.skonto_percent):g}% Skonto "
            f"(−{_euro(invoice.skonto_amount)}) – Zahlbetrag dann "
            f"<b>{_euro(invoice.skonto_total)}</b>.", body))

    # ---- GiroCode (QR) --------------------------------------------------
    qr = _giro_qr(invoice, settings)
    if qr is not None:
        story.append(Spacer(1, 8 * mm))
        qr_img = Image(qr, width=28 * mm, height=28 * mm)
        caption = Paragraph(
            "<b>Bequem per QR-Code bezahlen (GiroCode)</b><br/>"
            "Scannen Sie den Code mit Ihrer Banking-App, um die Überweisung "
            "mit allen Daten vorausgefüllt zu starten.", body)
        qr_tbl = Table([[qr_img, caption]], colWidths=[34 * mm, 126 * mm])
        qr_tbl.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (0, -1), 0),
        ]))
        story.append(qr_tbl)

    # ---- Hinweise -------------------------------------------------------
    if invoice.notes:
        story.append(Spacer(1, 8 * mm))
        story.append(Paragraph("Hinweise", section))
        story.append(Paragraph(invoice.notes, body))

    doc.build(story, onFirstPage=_footer_factory(settings),
              onLaterPages=_footer_factory(settings))
    return buf.getvalue()
