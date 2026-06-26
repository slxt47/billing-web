"""SQLAlchemy-Modelle: Rechnung + Rechnungsposten."""
from datetime import datetime, date, timedelta

from sqlalchemy import (
    Column, Integer, String, Date, DateTime, Numeric, ForeignKey, Text, Boolean,
    LargeBinary
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

    items = relationship(
        "InvoiceItem",
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="InvoiceItem.id",
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
    def remaining(self):
        return round(self.total - float(self.paid_amount or 0), 2)

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
    email = Column(String(200), default="")
    # Standard-Zahlungsfrist in Tagen + optionale Skonto-Vorgabe
    payment_term_days = Column(Integer, nullable=False, default=14)
    skonto_percent = Column(Numeric(5, 2), nullable=False, default=0)
    skonto_days = Column(Integer, nullable=False, default=0)
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
