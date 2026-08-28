"""SQLAlchemy-Modelle: Rechnung + Rechnungsposten."""
from datetime import datetime, date, timedelta

from sqlalchemy import (
    Column, Integer, String, Date, DateTime, Numeric, ForeignKey, Text, Boolean,
    LargeBinary, UniqueConstraint
)
from sqlalchemy.orm import relationship

from .database import Base

# Mögliche Status einer Rechnung
STATUS_OPEN = "offen"
STATUS_PARTIAL = "teilbezahlt"
STATUS_PAID = "bezahlt"
STATUS_CANCELLED = "storniert"


class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, index=True)
    number = Column(String(32), unique=True, nullable=False, index=True)

    customer_name = Column(String(200), nullable=False)
    customer_address = Column(Text, default="")
    customer_contact_person = Column(String(200), default="")

    issue_date = Column(Date, nullable=False, default=date.today)
    due_date = Column(Date, nullable=True)

    tax_rate = Column(Numeric(5, 2), nullable=False, default=20)  # Prozent
    notes = Column(Text, default="")

    status = Column(String(20), nullable=False, default=STATUS_OPEN)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    cancelled_at = Column(DateTime, nullable=True)

    # Skonto (optional): X % Nachlass bei Zahlung innerhalb Y Tagen
    skonto_percent = Column(Numeric(5, 2), nullable=False, default=0)
    skonto_days = Column(Integer, nullable=False, default=0)

    # Rabatt auf die Zwischensumme + Kleinunternehmer (ohne MwSt., §19 UStG)
    discount_percent = Column(Numeric(5, 2), nullable=False, default=0)
    small_business = Column(Boolean, nullable=False, default=False)

    # Zahlungseingang (für Teilzahlungen)
    paid_amount = Column(Numeric(12, 2), nullable=False, default=0)

    # Bearbeitungssperre: verhindert, dass zwei Benutzer gleichzeitig dieselbe
    # Rechnung bearbeiten. Läuft nach LOCK_TIMEOUT automatisch ab (siehe crud.py).
    locked_by = Column(String(80), nullable=True)
    locked_at = Column(DateTime, nullable=True)

    items = relationship(
        "InvoiceItem",
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="InvoiceItem.id",
    )

    # Lieferscheine, die aus dieser Rechnung entstanden sind. Gibt es einen,
    # lehnt die API eine zweite Umwandlung ab (keine Dubletten). selectin
    # statt lazy: eine Zusatzabfrage je Liste, nicht je Zeile.
    delivery_notes = relationship(
        "DeliveryNote",
        back_populates="source_invoice",
        order_by="DeliveryNote.id",
        lazy="selectin",
    )

    @property
    def delivery_note_number(self):
        """Nummer des bereits erzeugten Lieferscheins, sonst None."""
        return self.delivery_notes[0].number if self.delivery_notes else None

    # Gutschriften zu dieser Rechnung. Anders als der Lieferschein darf es
    # mehrere geben: eine Rechnung kann in Teilen gutgeschrieben werden.
    credit_notes = relationship(
        "CreditNote",
        back_populates="invoice",
        order_by="CreditNote.id",
        lazy="selectin",
    )

    # --- berechnete Werte -------------------------------------------------
    @property
    def subtotal(self):
        return round(sum((it.line_total for it in self.items), 0), 2)

    @property
    def discount_amount(self):
        return round(self.subtotal * float(self.discount_percent or 0) / 100, 2)

    @property
    def net(self):
        """Nettobetrag nach Rabatt (Bemessungsgrundlage für MwSt.)."""
        return round(self.subtotal - self.discount_amount, 2)

    @property
    def tax_amount(self):
        if self.small_business:
            return 0.0
        return round(self.net * float(self.tax_rate) / 100, 2)

    @property
    def total(self):
        return round(self.net + self.tax_amount, 2)

    # --- Zahlung / Verzug -------------------------------------------------
    @property
    def credited_amount(self):
        """Summe der Gutschriften zu dieser Rechnung (ohne stornierte)."""
        return round(sum(c.total for c in self.credit_notes
                         if c.status != CN_CANCELLED), 2)

    @property
    def remaining(self):
        """Offener Betrag: Gesamt minus Zahlungen minus Gutschriften."""
        return round(self.total - float(self.paid_amount or 0)
                     - self.credited_amount, 2)

    @property
    def is_overdue(self):
        if self.status in (STATUS_PAID, STATUS_CANCELLED) or not self.due_date:
            return False
        return self.due_date < date.today() and self.remaining > 0

    # --- Skonto -----------------------------------------------------------
    @property
    def has_skonto(self):
        return float(self.skonto_percent or 0) > 0 and int(self.skonto_days or 0) > 0

    @property
    def skonto_amount(self):
        return round(self.total * float(self.skonto_percent or 0) / 100, 2)

    @property
    def skonto_total(self):
        """Reduzierter Zahlbetrag bei Skonto-Nutzung."""
        return round(self.total - self.skonto_amount, 2)

    @property
    def skonto_date(self):
        if self.has_skonto:
            return self.issue_date + timedelta(days=int(self.skonto_days))
        return None


class Customer(Base):
    """Stammkunde, der wiederholt Rechnungen bekommt."""
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    address = Column(Text, default="")
    contact_person = Column(String(200), default="")
    email = Column(String(200), default="")
    # Standard-Zahlungsfrist in Tagen + optionale Skonto-Vorgabe
    payment_term_days = Column(Integer, nullable=False, default=14)
    skonto_percent = Column(Numeric(5, 2), nullable=False, default=0)
    skonto_days = Column(Integer, nullable=False, default=0)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class User(Base):
    """Benutzerkonto für die Anmeldung. Admins dürfen Benutzer verwalten."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(80), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    is_admin = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class Product(Base):
    """Vordefinierter Artikel / vordefinierte Leistung mit Standardpreis."""
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(300), nullable=False)
    unit_price = Column(Numeric(12, 2), nullable=False, default=0)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class Settings(Base):
    """Firmen-/Absenderdaten für die Rechnungen (eine Zeile, id=1)."""
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True)
    company_name = Column(String(200), default="")
    address = Column(Text, default="")
    tax_id = Column(String(80), default="")      # Steuernummer
    vat_id = Column(String(80), default="")      # USt-IdNr.
    iban = Column(String(40), default="")
    bic = Column(String(20), default="")
    email = Column(String(200), default="")
    phone = Column(String(80), default="")
    logo = Column(LargeBinary, nullable=True)
    logo_mime = Column(String(50), nullable=True)


class InvoiceItem(Base):
    __tablename__ = "invoice_items"

    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False)

    description = Column(String(300), nullable=False)
    quantity = Column(Numeric(10, 2), nullable=False, default=1)
    unit_price = Column(Numeric(12, 2), nullable=False, default=0)

    invoice = relationship("Invoice", back_populates="items")

    @property
    def line_total(self):
        return round(float(self.quantity) * float(self.unit_price), 2)


# Belegarten, für die eine Anwesenheitsanzeige geführt wird
PRESENCE_INVOICE = "invoice"
PRESENCE_QUOTE = "quote"
PRESENCE_DELIVERY_NOTE = "delivery_note"
PRESENCE_TYPES = (PRESENCE_INVOICE, PRESENCE_QUOTE, PRESENCE_DELIVERY_NOTE)

class Presence(Base):
    """Wer hat gerade welchen Beleg offen – Grundlage der Live-Anzeige
    „jemand anderes ist auch hier".

    Ergänzt die Bearbeitungssperre auf Rechnungen (die verhindert, dass zwei
    Leute gleichzeitig speichern) um die fehlende Rückmeldung *währenddessen*
    – und deckt zusätzlich Angebote und Lieferscheine ab, die gar keine
    Sperre haben.

    Die Zeilen sind kurzlebig: ein Client meldet sich alle paar Sekunden,
    Einträge ohne Lebenszeichen gelten nach PRESENCE_TIMEOUT als weg und
    werden beim nächsten Zugriff aufgeräumt. Bewusst in der Datenbank statt
    im Prozessspeicher, damit die Anzeige auch bei mehreren `web`-Repliken
    stimmt.
    """
    __tablename__ = "presence"

    id = Column(Integer, primary_key=True, index=True)
    doc_type = Column(String(20), nullable=False, index=True)
    doc_id = Column(Integer, nullable=False, index=True)
    username = Column(String(80), nullable=False)
    last_seen = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("doc_type", "doc_id", "username", name="uq_presence_doc_user"),
    )


class AuditLog(Base):
    """DSGVO-Protokoll: wer hat wann auf personenbezogene Daten zugegriffen."""
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    username = Column(String(80), nullable=False)
    action = Column(String(80), nullable=False)
    target_type = Column(String(40), nullable=False)
    target_id = Column(Integer, nullable=True)
    detail = Column(String(300), default="")


# Mögliche Status eines Angebots
QUOTE_OPEN = "offen"
QUOTE_ACCEPTED = "angenommen"
QUOTE_DECLINED = "abgelehnt"
QUOTE_CONVERTED = "umgewandelt"


class Quote(Base):
    """Angebot – kann später in eine Rechnung umgewandelt werden."""
    __tablename__ = "quotes"

    id = Column(Integer, primary_key=True, index=True)
    number = Column(String(32), unique=True, nullable=False, index=True)

    customer_name = Column(String(200), nullable=False)
    customer_address = Column(Text, default="")
    customer_contact_person = Column(String(200), default="")

    issue_date = Column(Date, nullable=False, default=date.today)
    valid_until = Column(Date, nullable=True)

    tax_rate = Column(Numeric(5, 2), nullable=False, default=20)
    discount_percent = Column(Numeric(5, 2), nullable=False, default=0)
    small_business = Column(Boolean, nullable=False, default=False)
    notes = Column(Text, default="")

    status = Column(String(20), nullable=False, default=QUOTE_OPEN)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    converted_invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=True)
    # Herkunft: aus diesem Lieferschein entstanden (verhindert, dass derselbe
    # Lieferschein zweimal zu einem Angebot wird).
    source_delivery_note_id = Column(Integer, ForeignKey("delivery_notes.id"),
                                     nullable=True)

    items = relationship(
        "QuoteItem",
        back_populates="quote",
        cascade="all, delete-orphan",
        order_by="QuoteItem.id",
    )

    converted_invoice = relationship("Invoice", lazy="selectin")
    source_delivery_note = relationship("DeliveryNote",
                                        back_populates="converted_quotes",
                                        lazy="selectin")

    @property
    def converted_invoice_number(self):
        """Nummer der Rechnung, in die dieses Angebot umgewandelt wurde."""
        return self.converted_invoice.number if self.converted_invoice else None

    @property
    def subtotal(self):
        return round(sum((it.line_total for it in self.items), 0), 2)

    @property
    def discount_amount(self):
        return round(self.subtotal * float(self.discount_percent or 0) / 100, 2)

    @property
    def net(self):
        return round(self.subtotal - self.discount_amount, 2)

    @property
    def tax_amount(self):
        if self.small_business:
            return 0.0
        return round(self.net * float(self.tax_rate) / 100, 2)

    @property
    def total(self):
        return round(self.net + self.tax_amount, 2)


class QuoteItem(Base):
    __tablename__ = "quote_items"

    id = Column(Integer, primary_key=True, index=True)
    quote_id = Column(Integer, ForeignKey("quotes.id"), nullable=False)

    description = Column(String(300), nullable=False)
    quantity = Column(Numeric(10, 2), nullable=False, default=1)
    unit_price = Column(Numeric(12, 2), nullable=False, default=0)

    quote = relationship("Quote", back_populates="items")

    @property
    def line_total(self):
        return round(float(self.quantity) * float(self.unit_price), 2)


# Mögliche Status eines Lieferscheins. "abgeschlossen" setzt der PDF-Download:
# wer den Lieferschein ausdruckt, hat ihn aus der Hand gegeben.
DN_OPEN = "offen"
DN_DONE = "abgeschlossen"
DN_CANCELLED = "storniert"


class DeliveryNote(Base):
    """Lieferschein – reiner Liefernachweis (Beschreibung + Menge, keine
    Preise), kann aus einer Rechnung erzeugt werden."""
    __tablename__ = "delivery_notes"

    id = Column(Integer, primary_key=True, index=True)
    number = Column(String(32), unique=True, nullable=False, index=True)

    customer_name = Column(String(200), nullable=False)
    customer_address = Column(Text, default="")
    customer_contact_person = Column(String(200), default="")

    issue_date = Column(Date, nullable=False, default=date.today)
    notes = Column(Text, default="")

    status = Column(String(20), nullable=False, default=DN_OPEN)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    source_invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=True)

    items = relationship(
        "DeliveryNoteItem",
        back_populates="delivery_note",
        cascade="all, delete-orphan",
        order_by="DeliveryNoteItem.id",
    )

    source_invoice = relationship("Invoice", back_populates="delivery_notes")
    converted_quotes = relationship(
        "Quote",
        back_populates="source_delivery_note",
        order_by="Quote.id",
        lazy="selectin",
    )

    @property
    def converted_quote_number(self):
        """Nummer des bereits erzeugten Angebots, sonst None."""
        return self.converted_quotes[0].number if self.converted_quotes else None


class DeliveryNoteItem(Base):
    __tablename__ = "delivery_note_items"

    id = Column(Integer, primary_key=True, index=True)
    delivery_note_id = Column(Integer, ForeignKey("delivery_notes.id"), nullable=False)

    description = Column(String(300), nullable=False)
    quantity = Column(Numeric(10, 2), nullable=False, default=1)

    delivery_note = relationship("DeliveryNote", back_populates="items")


# --------------------------- Gutschriften --------------------------------
# Eigene Belegart (GS-JJJJ-NNNN) statt "nur Storno": eine Rechnung kann ganz
# oder in Teilen gutgeschrieben werden, mehrfach, und jede Gutschrift ist ein
# eigener Beleg mit Nummer, PDF und E-Mail-Versand.
CN_OPEN = "offen"          # ausgestellt, noch nicht erstattet/verrechnet
CN_SETTLED = "erstattet"   # ausgezahlt oder verrechnet
CN_CANCELLED = "storniert"  # zurückgenommen, zählt nirgends mehr mit


class CreditNote(Base):
    """Gutschrift – optional zu einer Rechnung, sonst freistehend."""
    __tablename__ = "credit_notes"

    id = Column(Integer, primary_key=True, index=True)
    number = Column(String(32), unique=True, nullable=False, index=True)

    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=True)

    customer_name = Column(String(200), nullable=False)
    customer_address = Column(Text, default="")
    customer_contact_person = Column(String(200), default="")

    issue_date = Column(Date, nullable=False, default=date.today)
    reason = Column(Text, default="")

    tax_rate = Column(Numeric(5, 2), nullable=False, default=20)
    small_business = Column(Boolean, nullable=False, default=False)

    status = Column(String(20), nullable=False, default=CN_OPEN)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    items = relationship(
        "CreditNoteItem",
        back_populates="credit_note",
        cascade="all, delete-orphan",
        order_by="CreditNoteItem.id",
    )
    invoice = relationship("Invoice", back_populates="credit_notes")

    @property
    def invoice_number(self):
        return self.invoice.number if self.invoice else None

    @property
    def subtotal(self):
        return round(sum((it.line_total for it in self.items), 0), 2)

    @property
    def net(self):
        return self.subtotal

    @property
    def tax_amount(self):
        if self.small_business:
            return 0.0
        return round(self.net * float(self.tax_rate) / 100, 2)

    @property
    def total(self):
        return round(self.net + self.tax_amount, 2)


class CreditNoteItem(Base):
    __tablename__ = "credit_note_items"

    id = Column(Integer, primary_key=True, index=True)
    credit_note_id = Column(Integer, ForeignKey("credit_notes.id"), nullable=False)

    description = Column(String(300), nullable=False)
    quantity = Column(Numeric(10, 2), nullable=False, default=1)
    unit_price = Column(Numeric(12, 2), nullable=False, default=0)

    credit_note = relationship("CreditNote", back_populates="items")

    @property
    def line_total(self):
        return round(float(self.quantity) * float(self.unit_price), 2)


# --------------------------- PDF-Vorlagen --------------------------------
class PdfTemplate(Base):
    """Aussehen der erzeugten PDFs. Eine Vorlage ist die Vorgabe
    (is_default); beim Download lässt sich eine andere wählen. Die Werte
    entsprechen pdf.DEFAULTS – eine leere Vorlage sieht aus wie bisher."""
    __tablename__ = "pdf_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(80), unique=True, nullable=False)

    accent_color = Column(String(7), nullable=False, default="#2d6cdf")
    header_color = Column(String(7), nullable=False, default="#2d3748")
    font_family = Column(String(20), nullable=False, default="Helvetica")
    font_size = Column(Numeric(4, 1), nullable=False, default=10)

    header_note = Column(Text, default="")
    footer_text = Column(Text, default="")

    show_logo = Column(Boolean, nullable=False, default=True)
    show_qr = Column(Boolean, nullable=False, default=True)

    is_default = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
