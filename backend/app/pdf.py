"""PDF-Erzeugung für Rechnung, Angebot, Lieferschein und Gutschrift.

Das Aussehen kommt aus einer PDF-Vorlage (models.PdfTemplate): Akzentfarbe,
Kopffarbe, Schrift, Schriftgröße, Kopf- und Fußtext sowie Logo/GiroCode an
oder aus. Ohne Vorlage gelten die Werte in DEFAULTS – das ist exakt das
Aussehen, das die App vorher fest verdrahtet hatte.

Zwei Layouts stehen zur Wahl (LAYOUTS): "standard" ist das hier gebaute,
"formular" bildet den Firmenvordruck nach und steckt in pdf_form.py.

Die vier Belegarten teilen sich Kopf, Kundenblock, Positionstabelle und
Fußzeile; nur die Teile dazwischen unterscheiden sich.
"""
from datetime import date
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

# Vorgaben, wenn keine Vorlage gewählt ist.
DEFAULTS = {
    "accent_color": "#2d6cdf",
    "header_color": "#2d3748",
    "font_family": "Helvetica",
    "font_size": 10.0,
    "header_note": "",
    "footer_text": "",
    "show_logo": True,
    "show_qr": True,
    "layout": "standard",
}

# Auswählbare Layouts: Schlüssel (so steht er in der Datenbank) -> Beschriftung.
LAYOUTS = {
    "standard": "Standard",
    "formular": "Formular (Vordruck)",
}

# Nur die Standardschriften von PDF – die braucht kein Font-Embedding und
# funktioniert in jedem Viewer.
FONT_FAMILIES = {
    "Helvetica": ("Helvetica", "Helvetica-Bold"),
    "Times": ("Times-Roman", "Times-Bold"),
    "Courier": ("Courier", "Courier-Bold"),
}


class _Layout:
    """Aufgelöste Vorlage: fertige Farben, Schriften und Schalter."""

    def __init__(self, template=None):
        def value(key):
            got = getattr(template, key, None) if template is not None else None
            return DEFAULTS[key] if got in (None, "") else got

        self.accent = colors.HexColor(_hex(value("accent_color"), DEFAULTS["accent_color"]))
        self.header = colors.HexColor(_hex(value("header_color"), DEFAULTS["header_color"]))
        family = value("font_family")
        self.font, self.font_bold = FONT_FAMILIES.get(
            family, FONT_FAMILIES[DEFAULTS["font_family"]])
        self.size = float(value("font_size") or DEFAULTS["font_size"])
        self.header_note = str(value("header_note") or "")
        self.footer_text = str(value("footer_text") or "")
        # Schalter dürfen ausdrücklich False sein, deshalb nicht über value().
        self.show_logo = _flag(template, "show_logo")
        self.show_qr = _flag(template, "show_qr")
        self.name = getattr(template, "name", None) or "Standard"
        key = str(value("layout") or DEFAULTS["layout"])
        self.layout = key if key in LAYOUTS else DEFAULTS["layout"]
        self.form = self.layout == "formular"


def _hex(value, fallback: str) -> str:
    text = str(value or "").strip()
    if len(text) == 7 and text.startswith("#"):
        try:
            int(text[1:], 16)
            return text
        except ValueError:
            pass
    return fallback


def _flag(template, key: str) -> bool:
    got = getattr(template, key, None) if template is not None else None
    return DEFAULTS[key] if got is None else bool(got)


def _euro(value) -> str:
    return f"{float(value):,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def _date(value) -> str:
    return value.strftime("%d/%m/%Y")


def _styles(layout: _Layout) -> dict:
    base = getSampleStyleSheet()
    normal = ParagraphStyle("normal", parent=base["Normal"], fontName=layout.font,
                            fontSize=layout.size, leading=layout.size * 1.4)
    return {
        "normal": normal,
        "body": ParagraphStyle("body", parent=normal, leftIndent=4 * mm,
                               leading=layout.size * 1.5),
        "h1": ParagraphStyle("h1", parent=base["Title"], fontName=layout.font_bold,
                             fontSize=layout.size + 14, textColor=layout.header,
                             spaceAfter=2),
        "section": ParagraphStyle("section", parent=base["Heading2"],
                                  fontName=layout.font_bold, fontSize=layout.size + 2,
                                  textColor=layout.accent, spaceBefore=2, spaceAfter=6),
        "note": ParagraphStyle("note", parent=normal, fontSize=layout.size - 1,
                               textColor=colors.grey),
    }


def _giro_qr(number: str, amount: float, settings):
    """EPC-/GiroCode-QR als PNG-BytesIO, oder None wenn Daten fehlen."""
    if not settings or not settings.iban or not settings.company_name or amount <= 0:
        return None
    payload = "\n".join([
        "BCD", "002", "1", "SCT",
        (settings.bic or ""),
        settings.company_name[:70],
        settings.iban.replace(" ", ""),
        f"EUR{amount:.2f}",
        "", "",
        f"Rechnung {number}",
        "",
    ])
    img = qrcode.make(payload)
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def _footer_factory(settings, layout: _Layout):
    def _footer(canvas, doc):
        canvas.saveState()
        y = MARGIN
        canvas.setStrokeColor(colors.HexColor("#cbd5e0"))
        canvas.setLineWidth(0.5)
        canvas.line(MARGIN, y + 6 * mm, PAGE_W - MARGIN, y + 6 * mm)
        canvas.setFont(layout.font, 8)
        canvas.setFillColor(colors.grey)
        if layout.footer_text:
            line = layout.footer_text.replace("\n", "  ·  ")
        else:
            # Bankverbindung / Steuerdaten als Vorgabe
            bits = []
            if settings and settings.iban:
                bits.append(f"IBAN {settings.iban}")
            if settings and settings.bic:
                bits.append(f"BIC {settings.bic}")
            if settings and settings.vat_id:
                bits.append(f"USt-IdNr. {settings.vat_id}")
            elif settings and settings.tax_id:
                bits.append(f"St.-Nr. {settings.tax_id}")
            line = "  ·  ".join(bits) or "Vielen Dank für Ihren Auftrag."
        canvas.drawString(MARGIN, y, line[:150])
        canvas.drawRightString(PAGE_W - MARGIN, y, f"Seite {doc.page}")
        canvas.restoreState()
    return _footer


def _doc(buf, title: str) -> SimpleDocTemplate:
    return SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=MARGIN + 12 * mm,
        title=title,
    )


def _company_header(settings, layout: _Layout, st: dict) -> list:
    """Firmenkopf: Absender links, Logo rechts (wenn die Vorlage es zulässt)."""
    if not settings or not (settings.company_name or settings.logo):
        return []
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

    logo_cell = ""
    if settings.logo and layout.show_logo:
        try:
            logo_cell = Image(BytesIO(settings.logo), hAlign="RIGHT")
            logo_cell._restrictSize(45 * mm, 25 * mm)
        except Exception:
            logo_cell = ""

    header = Table([[Paragraph("<br/>".join(comp) or "", st["normal"]), logo_cell]],
                   colWidths=[110 * mm, 50 * mm])
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return [header, Spacer(1, 8 * mm)]


def _title_block(title: str, layout: _Layout, st: dict) -> list:
    out = []
    if layout.header_note:
        out.append(Paragraph(layout.header_note.replace("\n", "<br/>"), st["note"]))
        out.append(Spacer(1, 4 * mm))
    out.append(Paragraph(title, st["h1"]))
    out.append(Spacer(1, 8 * mm))
    return out


def _head_table(rows: list, st: dict) -> Table:
    """Zweispaltiger Kopfblock (Nummer, Datum, …)."""
    data = [[Paragraph(f"<b>{label}</b>", st["normal"]), value] for label, value in rows]
    tbl = Table(data, colWidths=[42 * mm, 80 * mm])
    tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (0, -1), 0),
        ("LEFTPADDING", (1, 0), (1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ]))
    return tbl


def _customer_block(doc_obj, heading: str, st: dict) -> list:
    out = [Paragraph(heading, st["section"]),
           Paragraph(f"<b>{doc_obj.customer_name}</b>", st["body"])]
    if doc_obj.customer_contact_person:
        out.append(Paragraph(f"z. Hd. {doc_obj.customer_contact_person}", st["body"]))
    for line in (doc_obj.customer_address or "").splitlines():
        out.append(Paragraph(line, st["body"]))
    out.append(Spacer(1, 10 * mm))
    return out


def _priced_items_table(doc_obj, layout: _Layout, st: dict) -> Table:
    """Positionstabelle mit Preisen und Summenblock (Rechnung, Angebot,
    Gutschrift)."""
    data = [["Beschreibung", "Menge", "Einzelpreis", "Summe"]]
    for it in doc_obj.items:
        data.append([it.description, f"{float(it.quantity):g}",
                     _euro(it.unit_price), _euro(it.line_total)])

    def total_row(label, value, bold=False):
        lab = f"<b>{label}</b>" if bold else label
        val = f"<b>{_euro(value)}</b>" if bold else _euro(value)
        return ["", Paragraph(lab, st["normal"]), "", Paragraph(val, st["normal"])]

    first_total = len(data)
    data.append(total_row("Zwischensumme", doc_obj.subtotal))
    if float(getattr(doc_obj, "discount_percent", 0) or 0) > 0:
        data.append(total_row(f"abzgl. {float(doc_obj.discount_percent):g}% Rabatt",
                              -doc_obj.discount_amount))
        data.append(total_row("Nettobetrag", doc_obj.net))
    if doc_obj.small_business:
        data.append(["", Paragraph("<i>Gemäß §19 UStG wird keine Umsatzsteuer "
                                   "berechnet.</i>", st["normal"]), "", ""])
    else:
        data.append(total_row(f"zzgl. {float(doc_obj.tax_rate):g}% MwSt.",
                              doc_obj.tax_amount))
    data.append(total_row("Gesamt", doc_obj.total, bold=True))

    tbl = Table(data, colWidths=[80 * mm, 35 * mm, 20 * mm, 30 * mm])
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), layout.header),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), layout.font_bold),
        ("FONTNAME", (0, 1), (-1, -1), layout.font),
        ("FONTSIZE", (0, 0), (-1, -1), layout.size),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, first_total - 1),
         [colors.white, colors.HexColor("#f5f7fa")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (0, -1), 6),
        ("RIGHTPADDING", (-1, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LINEABOVE", (1, first_total), (-1, first_total), 0.5, colors.grey),
        ("LINEABOVE", (1, -1), (-1, -1), 1, layout.header),
    ]
    for r in range(first_total, len(data)):  # Summenzeilen-Label über 2 Spalten
        style.append(("SPAN", (1, r), (2, r)))
    tbl.setStyle(TableStyle(style))
    return tbl


def _notes_block(doc_obj, st: dict, heading: str = "Hinweise") -> list:
    text = getattr(doc_obj, "notes", "") or ""
    if not text:
        return []
    return [Spacer(1, 8 * mm), Paragraph(heading, st["section"]),
            Paragraph(text, st["body"])]


def _form(doc_obj, kind: str, settings, layout: _Layout, title: str) -> bytes:
    """Weiche auf das Vordruck-Layout. Der Import steht hier, weil pdf_form
    seinerseits _euro/_date aus diesem Modul holt."""
    from . import pdf_form
    return pdf_form.render(doc_obj, kind, settings, layout, title)


def _build(buf, title: str, story: list, settings, layout: _Layout) -> bytes:
    doc = _doc(buf, title)
    footer = _footer_factory(settings, layout)
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


# --------------------------- Rechnung ------------------------------------
def invoice_pdf(invoice: models.Invoice, settings=None, template=None) -> bytes:
    layout = _Layout(template)
    if layout.form:
        return _form(invoice, "invoice", settings, layout,
                     f"Rechnung {invoice.number}")
    st = _styles(layout)
    story = _company_header(settings, layout, st)
    story += _title_block("RECHNUNG", layout, st)

    if invoice.status == models.STATUS_CANCELLED:
        story.append(Paragraph(
            "<b>STORNIERT</b> – diese Rechnung ist ungültig.",
            ParagraphStyle("storno", parent=st["normal"], textColor=colors.red,
                           fontSize=layout.size + 3)))
        story.append(Spacer(1, 6 * mm))

    head = [("Rechnungsnummer:", invoice.number), ("Datum:", _date(invoice.issue_date))]
    if invoice.due_date:
        head.append(("Fällig am:", _date(invoice.due_date)))
    head.append(("Status:", invoice.status))
    story.append(_head_table(head, st))
    story.append(Spacer(1, 10 * mm))

    story += _customer_block(invoice, "Rechnung an", st)
    story.append(Paragraph("Positionen", st["section"]))
    story.append(_priced_items_table(invoice, layout, st))

    if float(invoice.paid_amount or 0) > 0 and invoice.status != models.STATUS_CANCELLED:
        story.append(Spacer(1, 5 * mm))
        story.append(Paragraph(
            f"Bereits gezahlt: {_euro(invoice.paid_amount)} – "
            f"<b>Offener Betrag: {_euro(invoice.remaining)}</b>", st["body"]))

    if invoice.credited_amount > 0:
        numbers = ", ".join(c.number for c in invoice.credit_notes
                            if c.status != models.CN_CANCELLED)
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph(
            f"Gutgeschrieben: {_euro(invoice.credited_amount)} ({numbers}) – "
            f"<b>Offener Betrag: {_euro(invoice.remaining)}</b>", st["body"]))

    if invoice.due_date:
        days = (invoice.due_date - invoice.issue_date).days
        within = f" innerhalb von {days} Tagen" if days > 0 else ""
        story.append(Spacer(1, 6 * mm))
        story.append(Paragraph(
            f"<b>Zahlbar{within} bis zum {_date(invoice.due_date)} ohne Abzug.</b>",
            st["body"]))

    if invoice.has_skonto:
        story.append(Spacer(1, 2 * mm if invoice.due_date else 6 * mm))
        story.append(Paragraph(
            f"<b>Skonto:</b> Bei Zahlung bis {_date(invoice.skonto_date)} "
            f"(innerhalb {int(invoice.skonto_days)} Tagen) gewähren wir "
            f"{float(invoice.skonto_percent):g}% Skonto "
            f"(−{_euro(invoice.skonto_amount)}) – Zahlbetrag dann "
            f"<b>{_euro(invoice.skonto_total)}</b>.", st["body"]))

    if layout.show_qr:
        amount = invoice.remaining if invoice.remaining > 0 else invoice.total
        qr = _giro_qr(invoice.number, amount, settings)
        if qr is not None:
            story.append(Spacer(1, 8 * mm))
            caption = Paragraph(
                "<b>Bequem per QR-Code bezahlen (GiroCode)</b><br/>"
                "Scannen Sie den Code mit Ihrer Banking-App, um die Überweisung "
                "mit allen Daten vorausgefüllt zu starten.", st["body"])
            qr_tbl = Table([[Image(qr, width=28 * mm, height=28 * mm), caption]],
                           colWidths=[34 * mm, 126 * mm])
            qr_tbl.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (0, -1), 0),
            ]))
            story.append(qr_tbl)

    story += _notes_block(invoice, st)
    return _build(BytesIO(), f"Rechnung {invoice.number}", story, settings, layout)


# --------------------------- Angebot -------------------------------------
def quote_pdf(quote, settings=None, template=None) -> bytes:
    """PDF für ein Angebot – wie die Rechnung, aber ohne Zahlungsstatus und
    GiroCode, dafür mit Gültigkeitsdatum."""
    layout = _Layout(template)
    if layout.form:
        return _form(quote, "quote", settings, layout, f"Angebot {quote.number}")
    st = _styles(layout)
    story = _company_header(settings, layout, st)
    story += _title_block("ANGEBOT", layout, st)

    head = [("Angebotsnummer:", quote.number), ("Datum:", _date(quote.issue_date))]
    if quote.valid_until:
        head.append(("Gültig bis:", _date(quote.valid_until)))
    story.append(_head_table(head, st))
    story.append(Spacer(1, 10 * mm))

    story += _customer_block(quote, "Angebot an", st)
    story.append(Paragraph("Positionen", st["section"]))
    story.append(_priced_items_table(quote, layout, st))
    story += _notes_block(quote, st)

    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph(
        "Dieses Angebot ist freibleibend und stellt keine Rechnung dar.", st["body"]))
    return _build(BytesIO(), f"Angebot {quote.number}", story, settings, layout)


# --------------------------- Lieferschein --------------------------------
def delivery_note_pdf(delivery_note, settings=None, template=None) -> bytes:
    """PDF für einen Lieferschein – reiner Liefernachweis: nur Beschreibung
    und Menge je Position, keine Preise/Summen."""
    layout = _Layout(template)
    if layout.form:
        return _form(delivery_note, "delivery_note", settings, layout,
                     f"Lieferschein {delivery_note.number}")
    st = _styles(layout)
    story = _company_header(settings, layout, st)
    story += _title_block("LIEFERSCHEIN", layout, st)

    story.append(_head_table([("Lieferscheinnummer:", delivery_note.number),
                              ("Datum:", _date(delivery_note.issue_date))], st))
    story.append(Spacer(1, 10 * mm))
    story += _customer_block(delivery_note, "Lieferung an", st)

    story.append(Paragraph("Gelieferte Positionen", st["section"]))
    data = [["Beschreibung", "Menge"]]
    for it in delivery_note.items:
        data.append([it.description, f"{float(it.quantity):g}"])
    tbl = Table(data, colWidths=[135 * mm, 30 * mm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), layout.header),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), layout.font_bold),
        ("FONTNAME", (0, 1), (-1, -1), layout.font),
        ("FONTSIZE", (0, 0), (-1, -1), layout.size),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f7fa")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(tbl)
    story += _notes_block(delivery_note, st)

    story.append(Spacer(1, 10 * mm))
    story.append(Paragraph(
        "Bitte Empfang der oben aufgeführten Positionen durch Unterschrift "
        "bestätigen.", st["body"]))
    story.append(Spacer(1, 14 * mm))
    sig = Table([["", ""]], colWidths=[70 * mm, 70 * mm])
    sig.setStyle(TableStyle([
        ("LINEABOVE", (0, 0), (0, 0), 0.5, colors.grey),
        ("LINEABOVE", (1, 0), (1, 0), 0.5, colors.grey),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(sig)
    story.append(Table([["Ort, Datum", "Unterschrift"]], colWidths=[70 * mm, 70 * mm],
                       style=TableStyle([("TEXTCOLOR", (0, 0), (-1, -1), colors.grey),
                                         ("FONTNAME", (0, 0), (-1, -1), layout.font),
                                         ("FONTSIZE", (0, 0), (-1, -1), 8)])))
    return _build(BytesIO(), f"Lieferschein {delivery_note.number}", story,
                  settings, layout)


# --------------------------- Gutschrift ----------------------------------
def credit_note_pdf(credit_note, settings=None, template=None) -> bytes:
    """PDF für eine Gutschrift. Kein GiroCode: hier fließt Geld zurück, der
    Betrag wird erstattet oder mit der nächsten Rechnung verrechnet."""
    layout = _Layout(template)
    if layout.form:
        return _form(credit_note, "credit_note", settings, layout,
                     f"Gutschrift {credit_note.number}")
    st = _styles(layout)
    story = _company_header(settings, layout, st)
    story += _title_block("GUTSCHRIFT", layout, st)

    if credit_note.status == models.CN_CANCELLED:
        story.append(Paragraph(
            "<b>STORNIERT</b> – diese Gutschrift ist ungültig.",
            ParagraphStyle("storno", parent=st["normal"], textColor=colors.red,
                           fontSize=layout.size + 3)))
        story.append(Spacer(1, 6 * mm))

    head = [("Gutschriftsnummer:", credit_note.number),
            ("Datum:", _date(credit_note.issue_date))]
    if credit_note.invoice_number:
        head.append(("Zur Rechnung:", credit_note.invoice_number))
    head.append(("Status:", credit_note.status))
    story.append(_head_table(head, st))
    story.append(Spacer(1, 10 * mm))

    story += _customer_block(credit_note, "Gutschrift für", st)
    story.append(Paragraph("Gutgeschriebene Positionen", st["section"]))
    story.append(_priced_items_table(credit_note, layout, st))

    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(
        f"<b>Der Betrag von {_euro(credit_note.total)} wird erstattet bzw. mit "
        f"der nächsten Rechnung verrechnet.</b>", st["body"]))

    if credit_note.reason:
        story.append(Spacer(1, 8 * mm))
        story.append(Paragraph("Grund", st["section"]))
        story.append(Paragraph(credit_note.reason, st["body"]))
    return _build(BytesIO(), f"Gutschrift {credit_note.number}", story,
                  settings, layout)


# --------------------------- Vorschau ------------------------------------
def preview_pdf(settings=None, template=None) -> bytes:
    """Musterrechnung, um eine Vorlage zu beurteilen, ohne echte Daten
    anzufassen. Das Objekt wird nur im Speicher gebaut (keine Datenbank)."""
    sample = models.Invoice(
        number="RE-0000-0000",
        customer_name="Musterkunde GmbH",
        customer_address="Musterweg 1\n12345 Musterstadt",
        customer_contact_person="Frau Muster",
        issue_date=date.today(),
        due_date=date.today(),
        tax_rate=20,
        notes="Beispielbeleg zur Vorschau der Vorlage "
              f"„{getattr(template, 'name', None) or 'Standard'}“.",
        status=models.STATUS_OPEN,
        skonto_percent=2,
        skonto_days=7,
        discount_percent=0,
        small_business=False,
        paid_amount=0,
    )
    sample.items = [
        models.InvoiceItem(description="Beratung", quantity=3, unit_price=120),
        models.InvoiceItem(description="Einrichtung", quantity=1, unit_price=450),
    ]
    return invoice_pdf(sample, settings, template)
