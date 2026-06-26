"""Datenbank-Operationen für Rechnungen."""
from calendar import monthrange
from datetime import datetime, date

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import models, schemas, auth, config

# Fester Schlüssel für den Advisory-Lock der Rechnungsnummern-Vergabe
_NUMBER_LOCK_KEY = 815471


def _next_number(db: Session) -> str:
    """Nächste fortlaufende Rechnungsnummer (RE-<Jahr>-<Nr>).

    Nutzt die höchste vorhandene Nummer + 1 (robust auch bei gelöschten
    Rechnungen). Die Nummern sind gleich lang, daher = numerische Reihenfolge.
    """
    year = date.today().year
    prefix = f"RE-{year}-"
    last = (
        db.query(models.Invoice.number)
        .filter(models.Invoice.number.like(f"{prefix}%"))
        .order_by(models.Invoice.number.desc())
        .first()
    )
    nxt = (int(last[0][len(prefix):]) + 1) if last else 1
    return f"{prefix}{nxt:04d}"


def create_invoice(db: Session, data: schemas.InvoiceIn) -> models.Invoice:
    # Mehrere Benutzer können gleichzeitig speichern. Ein Transaktions-Advisory-
    # Lock serialisiert nur die Nummernvergabe, sodass keine zwei Rechnungen
    # dieselbe Nummer erhalten. Der Lock wird mit dem Commit/Rollback freigegeben.
    for _ in range(10):
        db.execute(text("SELECT pg_advisory_xact_lock(:k)"),
                   {"k": _NUMBER_LOCK_KEY})
        invoice = models.Invoice(
            number=_next_number(db),
            customer_name=data.customer_name,
            customer_address=data.customer_address,
            issue_date=date.today(),  # Rechnungsdatum ist immer der heutige Tag
            due_date=data.due_date,
            tax_rate=data.tax_rate,
            notes=data.notes,
            skonto_percent=data.skonto_percent,
            skonto_days=data.skonto_days,
            discount_percent=data.discount_percent,
            small_business=data.small_business,
        )
        for it in data.items:
            invoice.items.append(
                models.InvoiceItem(
                    description=it.description,
                    quantity=it.quantity,
                    unit_price=it.unit_price,
                )
            )
        db.add(invoice)
        try:
            db.commit()
        except IntegrityError:  # Sicherheitsnetz, sollte mit Lock nicht auftreten
            db.rollback()
            continue
        db.refresh(invoice)
        return invoice
    raise RuntimeError("Konnte keine eindeutige Rechnungsnummer vergeben")


def list_invoices(db: Session, search: str | None = None) -> list[models.Invoice]:
    """History: alle Rechnungen, neueste zuerst, optional gefiltert."""
    query = db.query(models.Invoice)
    if search:
        like = f"%{search}%"
        query = query.filter(
            models.Invoice.number.ilike(like)
            | models.Invoice.customer_name.ilike(like)
        )
    return query.order_by(models.Invoice.id.desc()).all()


def get_invoice(db: Session, invoice_id: int) -> models.Invoice | None:
    return db.get(models.Invoice, invoice_id)


def set_status(db: Session, invoice: models.Invoice, status: str) -> models.Invoice:
    invoice.status = status
    invoice.cancelled_at = (
        datetime.utcnow() if status == models.STATUS_CANCELLED else None
    )
    # Zahlungsbetrag konsistent halten
    if status == models.STATUS_PAID:
        invoice.paid_amount = invoice.total
    elif status == models.STATUS_OPEN:
        invoice.paid_amount = 0
    db.commit()
    db.refresh(invoice)
    return invoice


def add_payment(db: Session, invoice: models.Invoice, amount: float) -> models.Invoice:
    """Zahlungseingang verbuchen und Status automatisch anpassen."""
    invoice.paid_amount = float(invoice.paid_amount or 0) + amount
    if invoice.status != models.STATUS_CANCELLED:
        if invoice.remaining <= 0:
            invoice.status = models.STATUS_PAID
        elif float(invoice.paid_amount) > 0:
            invoice.status = models.STATUS_PARTIAL
    db.commit()
    db.refresh(invoice)
    return invoice


def delete_invoice(db: Session, invoice: models.Invoice) -> None:
    db.delete(invoice)
    db.commit()


def dashboard_stats(db: Session) -> dict:
    """Kennzahlen + Monatsumsatz der letzten 6 Monate (nur nicht stornierte)."""
    invoices = (
        db.query(models.Invoice)
        .filter(models.Invoice.status != models.STATUS_CANCELLED)
        .all()
    )
    today = date.today()
    total_revenue = sum(i.total for i in invoices)
    open_amount = sum(i.remaining for i in invoices if i.status != models.STATUS_PAID)
    overdue_amount = sum(i.remaining for i in invoices if i.is_overdue)
    paid_count = sum(1 for i in invoices if i.status == models.STATUS_PAID)

    # letzte 6 Monate
    months = []
    y, m = today.year, today.month
    seq = []
    for _ in range(6):
        seq.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    for (yy, mm) in reversed(seq):
        rev = sum(i.total for i in invoices
                  if i.issue_date.year == yy and i.issue_date.month == mm)
        months.append({"label": f"{mm:02d}/{yy}", "revenue": round(rev, 2)})

    return {
        "total_revenue": round(total_revenue, 2),
        "open_amount": round(open_amount, 2),
        "overdue_amount": round(overdue_amount, 2),
        "invoice_count": len(invoices),
        "paid_count": paid_count,
        "overdue_count": sum(1 for i in invoices if i.is_overdue),
        "months": months,
    }


def invoices_in_month(db: Session, year: int, month: int) -> list[models.Invoice]:
    """Alle Rechnungen mit Rechnungsdatum im angegebenen Monat."""
    start = date(year, month, 1)
    end = date(year, month, monthrange(year, month)[1])
    return (
        db.query(models.Invoice)
        .filter(models.Invoice.issue_date >= start)
        .filter(models.Invoice.issue_date <= end)
        .order_by(models.Invoice.id.asc())
        .all()
    )


# --------------------------- Kunden -------------------------------------
def list_customers(db: Session) -> list[models.Customer]:
    return db.query(models.Customer).order_by(models.Customer.name.asc()).all()


def create_customer(db: Session, data: schemas.CustomerIn) -> models.Customer:
    customer = models.Customer(
        name=data.name,
        address=data.address,
        email=data.email,
        payment_term_days=data.payment_term_days,
        skonto_percent=data.skonto_percent,
        skonto_days=data.skonto_days,
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


def get_customer(db: Session, customer_id: int) -> models.Customer | None:
    return db.get(models.Customer, customer_id)


def delete_customer(db: Session, customer: models.Customer) -> None:
    db.delete(customer)
    db.commit()


# --------------------------- Artikel / Leistungen -----------------------
def list_products(db: Session) -> list[models.Product]:
    return db.query(models.Product).order_by(models.Product.name.asc()).all()


def create_product(db: Session, data: schemas.ProductIn) -> models.Product:
    product = models.Product(name=data.name, unit_price=data.unit_price)
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


def get_product(db: Session, product_id: int) -> models.Product | None:
    return db.get(models.Product, product_id)


def delete_product(db: Session, product: models.Product) -> None:
    db.delete(product)
    db.commit()


# --------------------------- Benutzer -----------------------------------
def get_user(db: Session, username: str) -> models.User | None:
    return db.query(models.User).filter(models.User.username == username).first()


def list_users(db: Session) -> list[models.User]:
    return db.query(models.User).order_by(models.User.username.asc()).all()


def authenticate(db: Session, username: str, password: str) -> models.User | None:
    user = get_user(db, username)
    if user and auth.verify_password(password, user.password_hash):
        return user
    return None


def create_user(db: Session, username: str, password: str,
                is_admin: bool = False) -> models.User:
    user = models.User(
        username=username,
        password_hash=auth.hash_password(password),
        is_admin=is_admin,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def set_user_password(db: Session, user: models.User, password: str) -> None:
    user.password_hash = auth.hash_password(password)
    db.commit()


def delete_user(db: Session, user: models.User) -> None:
    db.delete(user)
    db.commit()


def count_admins(db: Session) -> int:
    return db.query(models.User).filter(models.User.is_admin.is_(True)).count()


# --------------------------- Einstellungen (Firmendaten) ----------------
def get_settings(db: Session) -> models.Settings:
    s = db.get(models.Settings, 1)
    if not s:
        s = models.Settings(id=1)
        db.add(s)
        db.commit()
        db.refresh(s)
    return s


def save_settings(db: Session, data: schemas.SettingsIn) -> models.Settings:
    s = get_settings(db)
    for field, value in data.model_dump().items():
        setattr(s, field, value)
    db.commit()
    db.refresh(s)
    return s


def set_logo(db: Session, content: bytes, mime: str) -> None:
    s = get_settings(db)
    s.logo = content
    s.logo_mime = mime
    db.commit()


def seed_users(db: Session) -> None:
    """Beim ersten Start: Admin + die in APP_USERS genannten Benutzer anlegen."""
    if db.query(models.User).count() > 0:
        return
    db.add(models.User(
        username=config.ADMIN_USER,
        password_hash=auth.hash_password(config.ADMIN_PASSWORD),
        is_admin=True,
    ))
    for name, pw in config.USERS.items():
        if name == config.ADMIN_USER:
            continue
        db.add(models.User(
            username=name,
            password_hash=auth.hash_password(pw),
            is_admin=False,
        ))
    db.commit()
