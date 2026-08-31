"""Vordruck-Layout für die PDFs ("Formular").

Bildet den Firmenvordruck nach, der bisher als Excel-Tabelle mit
hinterlegtem Scan ausgefüllt wurde: blauer Kopfbalken, Absenderblock rechts,
die vier Ankreuzfelder (Angebot / Bestellung / Lieferschein Nr. /
Rechnung-Nr.), die Zeile "Bestellung / Lieferdatum", der Positionskasten
mit den vier Spalten und der Summenblock aus Zwischensumme, Mehrwertsteuer
und Endsumme.

Die Maße stammen 1:1 aus der Vorlage. Deren Scan ist 641 x 1088 Pixel groß
und deckt die A4-Seite ab, jeweils 17,5 mm Rand links und rechts – daher
rechnen _x()/_y() direkt in Pixeln dieser Vorlage. Wer eine Linie prüfen
will, misst sie im Scan aus und vergleicht die Zahl hier.

Gezeichnet wird direkt auf die Canvas (kein Platypus): Der Vordruck hat
feste Kästen, in die der Text hinein muss – nicht umgekehrt.
"""
import re

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas as pdfcanvas

from . import models
from .pdf import _date

PAGE_W, PAGE_H = A4

# Umrechnung Vorlagen-Pixel -> Seite.
PX = 297.0 / 1088.0 * mm          # Pixelhöhe der Vorlage in Punkten
LEFT = 17.5 * mm                  # linker Rand des Vordrucks

# Senkrechte Linien des Positionskastens (Pixel-x der Vorlage).
COL_QTY, COL_TEXT, COL_PRICE, COL_SUM, COL_END = 0, 74, 461, 535, 639

# Waagerechte Anker (Pixel-y der Vorlage, von oben).
BANNER_TOP, BANNER_BOTTOM = 16, 40
CHECK_ROWS = (149, 180, 211, 241)     # Angebot, Bestellung, Lieferschein, Rechnung
DATE_ROW = 272                        # "Ort, am ..."
ORDER_ROW = 364                       # "Bestellung: ... / Lieferdatum: ..."
TABLE_TOP, TABLE_BOTTOM = 394, 942
SUM_ROWS = (942, 974, 1005, 1035)     # drei Kästen: Zwischen-, MwSt-, Endsumme
FOOTER_TOP = 1018
LINE = 16.5                           # Zeilenhöhe im Positionskasten
ROWS_PER_PAGE = int((TABLE_BOTTOM - TABLE_TOP - 12) / LINE)

CHECKBOXES = ("Angebot", "Bestellung", "Lieferschein Nr.", "Rechnung-Nr.")

# Belegart -> angekreuztes Feld, abweichende Beschriftung, Preise ja/nein.
# Gutschriften kennt der Vordruck nicht; sie übernehmen die Zeile der Rechnung,
# damit die Nummer nicht als Rechnungsnummer missverstanden wird.
KINDS = {
    "invoice": {"box": 3, "label": None, "priced": True},
    "quote": {"box": 0, "label": None, "priced": True},
    "delivery_note": {"box": 2, "label": None, "priced": False},
    "credit_note": {"box": 3, "label": "Gutschrift-Nr.", "priced": True},
}

DEFAULT_TERMS = (
    "Reklamationen können nur innerhalb von 8 Tagen berücksichtigt werden.",
    "Die Ware bleibt bis zur vollständigen Bezahlung unser Eigentum.",
)


def _x(px: float) -> float:
    return LEFT + px * PX


def _y(px: float) -> float:
    return PAGE_H - px * PX


def _num(value) -> str:
    """Betrag ohne Währungszeichen – das „€“ steht im Vordruck schon da."""
    return f"{float(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _city(settings) -> str:
    """Ort für die Zeile "Ort, am ..." – die letzte Adresszeile ohne PLZ."""
    lines = [ln.strip() for ln in (getattr(settings, "address", "") or "").splitlines()]
    lines = [ln for ln in lines if ln]
    if not lines:
        return ""
    return re.sub(r"^[A-Z]{0,2}-?\s*\d{4,5}\s+", "", lines[-1]).strip()


def _wrap(text: str, font: str, size: float, width: float) -> list[str]:
    """Text auf die Spaltenbreite umbrechen (Pixelmaß der Vorlage)."""
    out = []
    for raw in str(text or "").splitlines() or [""]:
        line = ""
        for word in raw.split():
            probe = f"{line} {word}".strip()
            if pdfmetrics.stringWidth(probe, font, size) <= width or not line:
                line = probe
            else:
                out.append(line)
                line = word
        out.append(line)
    return out or [""]


class _Form:
    """Eine Seite des Vordrucks – kennt Canvas, Vorlage und Firmendaten."""

    def __init__(self, c: pdfcanvas.Canvas, settings, layout):
        self.c = c
        self.settings = settings
        self.layout = layout
        self.ink = layout.accent          # Vordruckfarbe (gedruckt: blau)
        self.font = layout.font
        self.bold = layout.font_bold
        self.size = layout.size

    # ----------------------------------------------------------- Bausteine
    def text(self, px, py, value, size=None, bold=False, color=colors.black,
             align="left"):
        c = self.c
        c.setFont(self.bold if bold else self.font, size or self.size)
        c.setFillColor(color)
        draw = {"left": c.drawString, "right": c.drawRightString,
                "center": c.drawCentredString}[align]
        draw(_x(px), _y(py), str(value))

    def line(self, px1, py1, px2, py2, width=0.7, color=None):
        c = self.c
        c.setStrokeColor(color or self.ink)
        c.setLineWidth(width)
        c.line(_x(px1), _y(py1), _x(px2), _y(py2))

    def dotted(self, px1, px2, py):
        c = self.c
        c.saveState()
        c.setDash(1, 2)
        self.line(px1, py + 2, px2, py + 2, width=0.5)
        c.restoreState()

    # -------------------------------------------------------------- Aufbau
    def header(self):
        """Kopfbalken, Logo, Absender und Branchenzeile."""
        c, s = self.c, self.settings
        c.setFillColor(self.ink)
        c.rect(_x(COL_QTY), _y(BANNER_BOTTOM), _x(COL_END) - _x(COL_QTY),
               (BANNER_BOTTOM - BANNER_TOP) * PX, stroke=0, fill=1)
        name = (getattr(s, "company_name", "") or "").upper()
        if name:
            self.text((COL_END - COL_QTY) / 2, BANNER_BOTTOM - 7, name,
                      size=self.size + 5, bold=True, color=colors.white,
                      align="center")

        if self.layout.show_logo and getattr(s, "logo", None):
            self._logo()

        right = []
        address = " · ".join(ln.strip() for ln in
                             (getattr(s, "address", "") or "").splitlines() if ln.strip())
        if address:
            right.append(address)
        contact = [bit for bit in (f"Telefon {s.phone}" if getattr(s, "phone", "") else "",
                                   getattr(s, "email", "") or "") if bit]
        if contact:
            right.append(" · ".join(contact))
        for i, line in enumerate(right):
            self.text(COL_END, 60 + i * 13, line, size=self.size - 1,
                      color=self.ink, align="right")

        note = (self.layout.header_note or "").splitlines()
        for i, line in enumerate(note[:2]):
            self.text(COL_END, 95 + i * 12, line.strip(), size=self.size - 1,
                      bold=True, color=self.ink, align="right")

    def _logo(self):
        from io import BytesIO
        from reportlab.lib.utils import ImageReader
        try:
            img = ImageReader(BytesIO(self.settings.logo))
            iw, ih = img.getSize()
            height = 42 * PX
            width = min(height * iw / ih, 150 * PX)
            self.c.drawImage(img, _x((COL_END - width / PX) / 2), _y(90),
                             width=width, height=height, mask="auto")
        except Exception:
            pass          # ein kaputtes Logo darf den Beleg nicht kosten

    def checkboxes(self, kind: str, number: str):
        """Die vier Ankreuzfelder; angekreuzt wird die Belegart."""
        info = KINDS[kind]
        for i, label in enumerate(CHECKBOXES):
            ticked = i == info["box"]
            if ticked and info["label"]:
                label = info["label"]
            self._checkbox(CHECK_ROWS[i], label, ticked=ticked,
                           value=number if ticked else "")

    def _checkbox(self, row, label, ticked, value):
        c = self.c
        box = 8 * PX
        c.setStrokeColor(self.ink)
        c.setLineWidth(0.7)
        c.rect(_x(450), _y(row) - 0.5 * PX, box, box, stroke=1, fill=0)
        if ticked:
            c.setFont(self.bold, self.size - 1)
            c.setFillColor(colors.black)
            c.drawCentredString(_x(450) + box / 2,
                                _y(row) + 3.5 * PX - 0.35 * (self.size - 1), "X")
        self.text(465, row, label, color=self.ink)
        start = 465 + pdfmetrics.stringWidth(label, self.font, self.size) / PX + 4
        self.dotted(start, COL_END, row)
        if value:
            self.text(start + 4, row, value, bold=True)

    def date_line(self, issue_date):
        city = _city(self.settings)
        label = f"{city}, am" if city else "Datum:"
        self.text(465, DATE_ROW, label, color=self.ink)
        start = 465 + pdfmetrics.stringWidth(label, self.font, self.size) / PX + 4
        self.dotted(start, COL_END, DATE_ROW)
        self.text(start + 4, DATE_ROW, _date(issue_date), bold=True)

    def customer(self, doc_obj):
        """Anschriftenfeld links, wo im Vordruck das Fenster sitzt."""
        row = 150
        lines = [doc_obj.customer_name]
        if getattr(doc_obj, "customer_contact_person", ""):
            lines.append(f"z. Hd. {doc_obj.customer_contact_person}")
        lines += [ln for ln in (doc_obj.customer_address or "").splitlines() if ln.strip()]
        for i, line in enumerate(lines[:6]):
            self.text(8, row + i * 15, line, bold=(i == 0))

    def order_line(self, doc_obj):
        """Die Zeile "Bestellung: ... / Lieferdatum: ..." über dem Kasten."""
        self.text(0, ORDER_ROW - 3, "Bestellung:", bold=True, color=self.ink)
        self.dotted(70, 440, ORDER_ROW - 3)
        # Eine eigene Bestellnummer führt die App nicht – in den alten Belegen
        # stand hier, wer bestellt hat.
        reference = getattr(doc_obj, "customer_contact_person", "") or ""
        if reference:
            self.text(76, ORDER_ROW - 3, reference)

        label = "Lieferdatum:"
        self.text(460, ORDER_ROW - 3, label, bold=True, color=self.ink)
        start = 460 + pdfmetrics.stringWidth(label, self.bold, self.size) / PX + 4
        self.dotted(start, COL_END, ORDER_ROW - 3)
        self.text(start + 4, ORDER_ROW - 3, _date(doc_obj.issue_date))

    def table_frame(self, priced: bool):
        """Kasten samt Spaltentrennern und Spaltenköpfen."""
        c = self.c
        c.setStrokeColor(self.ink)
        c.setLineWidth(0.9)
        c.rect(_x(COL_QTY), _y(TABLE_BOTTOM), _x(COL_END) - _x(COL_QTY),
               (TABLE_BOTTOM - TABLE_TOP) * PX, stroke=1, fill=0)
        for col in (COL_TEXT, COL_PRICE, COL_SUM):
            self.line(col, TABLE_TOP, col, TABLE_BOTTOM, width=0.9)

        head = TABLE_TOP - 6
        self.text(4, head, "Menge", color=self.ink)
        self.text(COL_TEXT + 6, head, "Beschreibung", color=self.ink)
        if priced:
            self.text(COL_SUM - 4, head, "Einzelpreis", size=self.size - 2,
                      color=self.ink, align="right")
        self.text((COL_SUM + COL_END) / 2, head, "Euro", bold=True, color=self.ink,
                  align="center")

    def rows(self, rows: list[dict]):
        """Positionszeilen in den Kasten setzen."""
        for i, row in enumerate(rows):
            py = TABLE_TOP + 12 + i * LINE
            color = row.get("color", colors.black)
            bold = row.get("bold", False)
            if row.get("qty"):
                self.text(COL_TEXT - 6, py, row["qty"], align="right", color=color)
            self.text(COL_TEXT + 6, py, row["text"], bold=bold, color=color)
            if row.get("price"):
                self.text(COL_SUM - 6, py, row["price"], align="right", color=color)
            if row.get("sum"):
                self.text(COL_END - 6, py, row["sum"], align="right", bold=bold,
                          color=color)

    def sums(self, rows: list[tuple[str, str]]):
        """Die drei vorgedruckten Summenkästen rechts unten."""
        c = self.c
        c.setStrokeColor(self.ink)
        c.setLineWidth(0.9)
        for py in SUM_ROWS[1:]:
            self.line(COL_PRICE, py, COL_END, py, width=0.9)
        for px in (COL_PRICE, COL_SUM, COL_END):
            self.line(px, SUM_ROWS[0], px, SUM_ROWS[-1], width=0.9)

        for i, (label, value) in enumerate(rows):
            py = SUM_ROWS[i] + 21
            self.text(COL_PRICE - 6, py, label, bold=True, color=self.ink,
                      align="right")
            self.text(COL_SUM + 6, py, "€", color=self.ink)
            if value:
                self.text(COL_END - 6, py, value, bold=(i == 2), align="right")

    def footer(self, extra: list[str]):
        """Kleingedrucktes unten links, Steuernummer unten rechts."""
        s = self.settings
        lines = []
        bank = [bit for bit in (getattr(s, "iban", "") and f"IBAN {s.iban}",
                                getattr(s, "bic", "") and f"BIC {s.bic}") if bit]
        if bank:
            lines.append(("Bankverbindung: " + "  ·  ".join(bank), True))
        custom = [ln.strip() for ln in (self.layout.footer_text or "").splitlines()
                  if ln.strip()]
        for line in (custom or list(DEFAULT_TERMS)):
            lines.append((line, False))
        for line in extra:
            lines.append((line, False))

        for i, (line, bold) in enumerate(lines[:6]):
            self.text(0, FOOTER_TOP + i * 10, line, size=self.size - 4, bold=bold,
                      color=self.ink)

        tax = (getattr(s, "vat_id", "") and f"UID-Nr. {s.vat_id}") or \
              (getattr(s, "tax_id", "") and f"St.-Nr. {s.tax_id}")
        if tax:
            self.text(COL_END, FOOTER_TOP + 48, tax, size=self.size - 4,
                      color=self.ink, align="right")

    def page_marker(self, page: int, total: int):
        if total < 2:
            return
        self.text(COL_END, ORDER_ROW - 24, f"Seite {page} von {total}",
                  size=self.size - 2, color=self.ink, align="right")


# ------------------------------------------------------------ Inhaltszeilen
def _item_rows(doc_obj, kind: str, layout) -> list[dict]:
    """Positionen (mit Umbruch) und die Zusatzzeilen darunter."""
    priced = KINDS[kind]["priced"]
    width = (COL_PRICE - COL_TEXT - 12) * PX
    rows: list[dict] = []

    cancelled = getattr(doc_obj, "status", "") in (models.STATUS_CANCELLED,
                                                   models.CN_CANCELLED)
    if cancelled:
        rows.append({"text": "STORNIERT – dieser Beleg ist ungültig.",
                     "bold": True, "color": colors.red})
        rows.append({"text": ""})

    for item in doc_obj.items:
        lines = _wrap(item.description, layout.font, layout.size, width)
        rows.append({
            "qty": f"{float(item.quantity):g}",
            "text": lines[0],
            "price": _num(item.unit_price) if priced else "",
            "sum": _num(item.line_total) if priced else "",
        })
        rows += [{"text": line} for line in lines[1:]]

    if priced and float(getattr(doc_obj, "discount_percent", 0) or 0) > 0:
        rows.append({"text": f"abzüglich {float(doc_obj.discount_percent):g} % Rabatt",
                     "sum": _num(-doc_obj.discount_amount)})

    notes = getattr(doc_obj, "notes", "") or getattr(doc_obj, "reason", "") or ""
    if notes:
        rows.append({"text": ""})
        rows += [{"text": line} for line in _wrap(notes, layout.font, layout.size, width)]
    return rows


def _sum_rows(doc_obj, kind: str) -> list[tuple[str, str]]:
    if not KINDS[kind]["priced"]:
        return [("Zwischensumme", ""), ("Mehrwertsteuer", ""), ("Endsumme", "")]
    if getattr(doc_obj, "small_business", False):
        tax = ("ohne USt. (§ 19 UStG)", "")
    else:
        tax = (f"+ {float(doc_obj.tax_rate):g} % Mehrwertsteuer",
               _num(doc_obj.tax_amount))
    return [("Zwischensumme", _num(doc_obj.net)), tax,
            ("Endsumme", _num(doc_obj.total))]


def _footer_extra(doc_obj, kind: str) -> list[str]:
    """Zahlungsbedingungen und Zahlungsstand – im Vordruck steht das unten."""
    out = []
    if kind == "invoice":
        terms = []
        if doc_obj.due_date:
            terms.append(f"zahlbar bis {_date(doc_obj.due_date)} ohne Abzug")
        if getattr(doc_obj, "has_skonto", False):
            terms.append(f"bei Zahlung bis {_date(doc_obj.skonto_date)} "
                         f"{float(doc_obj.skonto_percent):g} % Skonto "
                         f"({_num(doc_obj.skonto_total)})")
        if terms:
            out.append("Zahlungskonditionen: " + ", ".join(terms) + ".")
        if float(doc_obj.paid_amount or 0) > 0 or doc_obj.credited_amount > 0:
            out.append(f"Bereits ausgeglichen: "
                       f"{_num(float(doc_obj.total) - float(doc_obj.remaining))} – "
                       f"offener Betrag: {_num(doc_obj.remaining)}.")
    if kind == "credit_note" and getattr(doc_obj, "invoice_number", ""):
        out.append(f"Gutschrift zur Rechnung {doc_obj.invoice_number}. Der Betrag "
                   f"wird erstattet bzw. mit der nächsten Rechnung verrechnet.")
    if kind == "quote" and getattr(doc_obj, "valid_until", None):
        out.append(f"Dieses Angebot gilt bis {_date(doc_obj.valid_until)} "
                   f"und ist freibleibend.")
    return out


# ----------------------------------------------------------------- Aufbau
def render(doc_obj, kind: str, settings, layout, title: str = "") -> bytes:
    """Beleg im Vordruck-Layout erzeugen. `kind` ist ein Schlüssel aus KINDS."""
    from io import BytesIO

    buf = BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=A4)
    c.setTitle(title or f"{kind} {doc_obj.number}")

    rows = _item_rows(doc_obj, kind, layout)
    pages = [rows[i:i + ROWS_PER_PAGE] for i in range(0, len(rows), ROWS_PER_PAGE)] \
        or [[]]

    for index, page_rows in enumerate(pages, start=1):
        form = _Form(c, settings, layout)
        form.header()
        form.checkboxes(kind, doc_obj.number)
        form.date_line(doc_obj.issue_date)
        form.customer(doc_obj)
        form.order_line(doc_obj)
        form.table_frame(KINDS[kind]["priced"])
        form.rows(page_rows)
        if index == len(pages):
            form.sums(_sum_rows(doc_obj, kind))
        form.footer(_footer_extra(doc_obj, kind))
        form.page_marker(index, len(pages))
        c.showPage()

    c.save()
    return buf.getvalue()
