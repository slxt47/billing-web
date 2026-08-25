"""Datenbank-Operationen für Rechnungen."""
from calendar import monthrange
from datetime import datetime, date, timedelta

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import models, schemas, auth, config

# Fester Schlüssel für den Advisory-Lock der Beleg-Nummernvergabe. Angebot
# (AN), Rechnung (RE) und Lieferschein (LS) teilen sich eine fortlaufende
# Nummer je Jahr (z. B. AN-2026-0007 -> RE-2026-0007 -> LS-2026-0007), damit
# eine Umwandlung die Nummer immer weiterschieben kann (siehe convert_*).
_DOC_NUMBER_LOCK_KEY = 815470

# Bearbeitungssperren laufen nach dieser Zeit ohne Aktivität automatisch ab
LOCK_TIMEOUT = timedelta(minutes=5)


def _table_max_suffix(db: Session, model_cls, prefix: str, year: int) -> int:
    p = f"{prefix}-{year}-"
    last = (
        db.query(model_cls.number)
        .filter(model_cls.number.like(f"{p}%"))
        .order_by(model_cls.number.desc())
        .first()
    )
    return int(last[0][len(p):]) if last else 0


def _next_doc_suffix(db: Session, year: int) -> int:
    """Höchste vergebene laufende Nummer über Angebote/Rechnungen/Lieferscheine
    des Jahres + 1 – gemeinsamer Zähler für alle drei Belegarten."""
    return 1 + max(
        _table_max_suffix(db, models.Quote, "AN", year),
        _table_max_suffix(db, models.Invoice, "RE", year),
        _table_max_suffix(db, models.DeliveryNote, "LS", year),
    )


def _build_invoice(data: schemas.InvoiceIn, number: str) -> models.Invoice:
    invoice = models.Invoice(
        number=number,
        customer_name=data.customer_name,
        customer_address=data.customer_address,
        customer_contact_person=data.customer_contact_person,
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
    return invoice


def create_invoice(db: Session, data: schemas.InvoiceIn) -> models.Invoice:
    # Mehrere Benutzer können gleichzeitig speichern. Ein Transaktions-Advisory-
    # Lock serialisiert nur die Nummernvergabe, sodass keine zwei Belege
    # dieselbe Nummer erhalten. Der Lock wird mit dem Commit/Rollback freigegeben.
    for _ in range(10):
        db.execute(text("SELECT pg_advisory_xact_lock(:k)"),
                   {"k": _DOC_NUMBER_LOCK_KEY})
        year = date.today().year
        number = f"RE-{year}-{_next_doc_suffix(db, year):04d}"
        invoice = _build_invoice(data, number)
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
    db.query(models.Quote).filter(
        models.Quote.converted_invoice_id == invoice.id
    ).update({
        models.Quote.converted_invoice_id: None,
        models.Quote.status: models.QUOTE_OPEN,
    })
    db.delete(invoice)
    db.commit()


def update_invoice(db: Session, invoice: models.Invoice,
                   data: schemas.InvoiceUpdate) -> models.Invoice:
    """Bestehende Rechnung bearbeiten (Positionen werden ersetzt)."""
    invoice.customer_name = data.customer_name
    invoice.customer_address = data.customer_address
    invoice.customer_contact_person = data.customer_contact_person
    invoice.due_date = data.due_date
    invoice.tax_rate = data.tax_rate
    invoice.notes = data.notes
    invoice.skonto_percent = data.skonto_percent
    invoice.skonto_days = data.skonto_days
    invoice.discount_percent = data.discount_percent
    invoice.small_business = data.small_business
    invoice.items = [
        models.InvoiceItem(description=it.description, quantity=it.quantity,
                           unit_price=it.unit_price)
        for it in data.items
    ]
    db.commit()
    db.refresh(invoice)
    return invoice


# --------------------------- Bearbeitungssperre --------------------------
def _lock_active(invoice: models.Invoice) -> bool:
    return bool(
        invoice.locked_by and invoice.locked_at
        and datetime.utcnow() - invoice.locked_at < LOCK_TIMEOUT
    )


def lock_status(invoice: models.Invoice, username: str) -> dict:
    active = _lock_active(invoice)
    return {
        "locked": active,
        "locked_by": invoice.locked_by if active else None,
        "locked_at": invoice.locked_at if active else None,
        "editable": (not active) or invoice.locked_by == username,
    }


def acquire_lock(db: Session, invoice: models.Invoice, username: str) -> bool:
    """Versucht, die Bearbeitungssperre für `username` zu setzen/erneuern.
    Gibt False zurück, wenn ein anderer Benutzer die Rechnung aktiv sperrt."""
    if _lock_active(invoice) and invoice.locked_by != username:
        return False
    invoice.locked_by = username
    invoice.locked_at = datetime.utcnow()
    db.commit()
    return True


def release_lock(db: Session, invoice: models.Invoice, username: str) -> None:
    if invoice.locked_by == username:
        invoice.locked_by = None
        invoice.locked_at = None
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
def list_customers(db: Session, active_only: bool = False) -> list[models.Customer]:
    q = db.query(models.Customer)
    if active_only:
        q = q.filter(models.Customer.active.is_(True))
    return q.order_by(models.Customer.name.asc()).all()


def create_customer(db: Session, data: schemas.CustomerIn) -> models.Customer:
    customer = models.Customer(
        name=data.name,
        address=data.address,
        contact_person=data.contact_person,
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


def update_customer(db: Session, customer: models.Customer,
                    data: schemas.CustomerIn) -> models.Customer:
    customer.name = data.name
    customer.address = data.address
    customer.contact_person = data.contact_person
    customer.email = data.email
    customer.payment_term_days = data.payment_term_days
    customer.skonto_percent = data.skonto_percent
    customer.skonto_days = data.skonto_days
    db.commit()
    db.refresh(customer)
    return customer


def set_customer_active(db: Session, customer: models.Customer, active: bool) -> models.Customer:
    customer.active = active
    db.commit()
    db.refresh(customer)
    return customer


def delete_customer(db: Session, customer: models.Customer) -> None:
    db.delete(customer)
    db.commit()


def export_customer_data(db: Session, customer: models.Customer) -> dict:
    """DSGVO Art. 15: alle gespeicherten Daten zu einem Kunden (Stammdaten +
    Rechnungen, anhand des Namens zugeordnet, da Rechnungen keine feste
    Fremdschlüsselbeziehung zum Kundenstamm haben)."""
    invoices = (
        db.query(models.Invoice)
        .filter(models.Invoice.customer_name == customer.name)
        .order_by(models.Invoice.id.desc())
        .all()
    )
    return {
        "customer": schemas.CustomerOut.model_validate(customer).model_dump(mode="json"),
        "invoices": [
            {
                "number": inv.number,
                "issue_date": inv.issue_date.isoformat(),
                "status": inv.status,
                "total": inv.total,
                "customer_address": inv.customer_address,
            }
            for inv in invoices
        ],
        "exported_at": datetime.utcnow().isoformat(),
    }


def anonymize_customer(db: Session, customer: models.Customer) -> models.Customer:
    """DSGVO Art. 17: personenbezogene Daten löschen, Kundendatensatz (id)
    aus Referenzgründen behalten. Bereits ausgestellte Rechnungen bleiben aus
    steuerrechtlichen Aufbewahrungspflichten (GoBD) unverändert."""
    customer.name = f"Gelöschter Kunde #{customer.id}"
    customer.address = ""
    customer.email = ""
    customer.active = False
    db.commit()
    db.refresh(customer)
    return customer


# --------------------------- Artikel / Leistungen -----------------------
def list_products(db: Session, active_only: bool = False) -> list[models.Product]:
    q = db.query(models.Product)
    if active_only:
        q = q.filter(models.Product.active.is_(True))
    return q.order_by(models.Product.name.asc()).all()


def create_product(db: Session, data: schemas.ProductIn) -> models.Product:
    product = models.Product(name=data.name, unit_price=data.unit_price)
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


def get_product(db: Session, product_id: int) -> models.Product | None:
    return db.get(models.Product, product_id)


def update_product(db: Session, product: models.Product,
                   data: schemas.ProductIn) -> models.Product:
    product.name = data.name
    product.unit_price = data.unit_price
    db.commit()
    db.refresh(product)
    return product


def set_product_active(db: Session, product: models.Product, active: bool) -> models.Product:
    product.active = active
    db.commit()
    db.refresh(product)
    return product


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


# --------------------------- DSGVO-Audit-Log -----------------------------
def log_action(db: Session, username: str, action: str, target_type: str,
              target_id: int | None = None, detail: str = "") -> None:
    db.add(models.AuditLog(
        username=username, action=action, target_type=target_type,
        target_id=target_id, detail=detail,
    ))
    db.commit()


def list_audit_log(db: Session, limit: int = 200) -> list[models.AuditLog]:
    return (
        db.query(models.AuditLog)
        .order_by(models.AuditLog.id.desc())
        .limit(limit)
        .all()
    )


# --------------------------- Angebote (Quotes) ---------------------------
def create_quote(db: Session, data: schemas.QuoteIn) -> models.Quote:
    for _ in range(10):
        db.execute(text("SELECT pg_advisory_xact_lock(:k)"),
                   {"k": _DOC_NUMBER_LOCK_KEY})
        year = date.today().year
        number = f"AN-{year}-{_next_doc_suffix(db, year):04d}"
        quote = models.Quote(
            number=number,
            customer_name=data.customer_name,
            customer_address=data.customer_address,
            customer_contact_person=data.customer_contact_person,
            issue_date=date.today(),
            valid_until=data.valid_until,
            tax_rate=data.tax_rate,
            discount_percent=data.discount_percent,
            small_business=data.small_business,
            notes=data.notes,
        )
        for it in data.items:
            quote.items.append(models.QuoteItem(
                description=it.description, quantity=it.quantity,
                unit_price=it.unit_price,
            ))
        db.add(quote)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            continue
        db.refresh(quote)
        return quote
    raise RuntimeError("Konnte keine eindeutige Angebotsnummer vergeben")


def list_quotes(db: Session) -> list[models.Quote]:
    return db.query(models.Quote).order_by(models.Quote.id.desc()).all()


def get_quote(db: Session, quote_id: int) -> models.Quote | None:
    return db.get(models.Quote, quote_id)


def update_quote(db: Session, quote: models.Quote, data: schemas.QuoteIn) -> models.Quote:
    quote.customer_name = data.customer_name
    quote.customer_address = data.customer_address
    quote.customer_contact_person = data.customer_contact_person
    quote.valid_until = data.valid_until
    quote.tax_rate = data.tax_rate
    quote.discount_percent = data.discount_percent
    quote.small_business = data.small_business
    quote.notes = data.notes
    quote.items = [
        models.QuoteItem(description=it.description, quantity=it.quantity,
                         unit_price=it.unit_price)
        for it in data.items
    ]
    db.commit()
    db.refresh(quote)
    return quote


def set_quote_status(db: Session, quote: models.Quote, status: str) -> models.Quote:
    quote.status = status
    db.commit()
    db.refresh(quote)
    return quote


def delete_quote(db: Session, quote: models.Quote) -> None:
    db.delete(quote)
    db.commit()


def convert_quote_to_invoice(db: Session, quote: models.Quote) -> models.Invoice:
    """Erstellt aus einem Angebot eine Rechnung mit denselben Positionen. Die
    Rechnung übernimmt die laufende Nummer des Angebots (nur das Präfix
    wechselt von AN auf RE), damit die Nummer "weitergeschoben" wird."""
    invoice_data = schemas.InvoiceIn(
        customer_name=quote.customer_name,
        customer_address=quote.customer_address,
        customer_contact_person=quote.customer_contact_person,
        tax_rate=quote.tax_rate,
        discount_percent=quote.discount_percent,
        small_business=quote.small_business,
        notes=quote.notes,
        items=[
            schemas.ItemIn(description=it.description, quantity=it.quantity,
                           unit_price=it.unit_price)
            for it in quote.items
        ],
    )
    _, year, suffix = quote.number.split("-")
    number = f"RE-{year}-{suffix}"
    db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _DOC_NUMBER_LOCK_KEY})
    invoice = _build_invoice(invoice_data, number)
    db.add(invoice)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise RuntimeError(f"Rechnungsnummer {number} ist bereits vergeben")
    db.refresh(invoice)
    quote.status = models.QUOTE_CONVERTED
    quote.converted_invoice_id = invoice.id
    db.commit()
    return invoice


# --------------------------- Lieferscheine (Delivery Notes) --------------
def _build_delivery_note(data: schemas.DeliveryNoteIn, number: str,
                         source_invoice_id: int | None = None) -> models.DeliveryNote:
    dn = models.DeliveryNote(
        number=number,
        customer_name=data.customer_name,
        customer_address=data.customer_address,
        customer_contact_person=data.customer_contact_person,
        issue_date=date.today(),
        notes=data.notes,
        source_invoice_id=source_invoice_id,
    )
    for it in data.items:
        dn.items.append(models.DeliveryNoteItem(
            description=it.description, quantity=it.quantity,
        ))
    return dn


def create_delivery_note(db: Session, data: schemas.DeliveryNoteIn) -> models.DeliveryNote:
    for _ in range(10):
        db.execute(text("SELECT pg_advisory_xact_lock(:k)"),
                   {"k": _DOC_NUMBER_LOCK_KEY})
        year = date.today().year
        number = f"LS-{year}-{_next_doc_suffix(db, year):04d}"
        dn = _build_delivery_note(data, number)
        db.add(dn)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            continue
        db.refresh(dn)
        return dn
    raise RuntimeError("Konnte keine eindeutige Lieferscheinnummer vergeben")


def list_delivery_notes(db: Session) -> list[models.DeliveryNote]:
    return db.query(models.DeliveryNote).order_by(models.DeliveryNote.id.desc()).all()


def get_delivery_note(db: Session, delivery_note_id: int) -> models.DeliveryNote | None:
    return db.get(models.DeliveryNote, delivery_note_id)


def update_delivery_note(db: Session, dn: models.DeliveryNote,
                         data: schemas.DeliveryNoteIn) -> models.DeliveryNote:
    dn.customer_name = data.customer_name
    dn.customer_address = data.customer_address
    dn.customer_contact_person = data.customer_contact_person
    dn.notes = data.notes
    dn.items = [
        models.DeliveryNoteItem(description=it.description, quantity=it.quantity)
        for it in data.items
    ]
    db.commit()
    db.refresh(dn)
    return dn


def set_delivery_note_status(db: Session, dn: models.DeliveryNote, status: str) -> models.DeliveryNote:
    dn.status = status
    db.commit()
    db.refresh(dn)
    return dn


def delete_delivery_note(db: Session, dn: models.DeliveryNote) -> None:
    db.delete(dn)
    db.commit()


def convert_invoice_to_delivery_note(db: Session, invoice: models.Invoice) -> models.DeliveryNote:
    """Erstellt aus einer Rechnung einen Lieferschein (nur Beschreibung +
    Menge je Position, keine Preise). Übernimmt die laufende Nummer der
    Rechnung (Präfix wechselt von RE auf LS), damit die Nummer
    weitergeschoben wird."""
    dn_data = schemas.DeliveryNoteIn(
        customer_name=invoice.customer_name,
        customer_address=invoice.customer_address,
        customer_contact_person=invoice.customer_contact_person,
        notes=invoice.notes,
        items=[
            schemas.DeliveryNoteItemIn(description=it.description, quantity=it.quantity)
            for it in invoice.items
        ],
    )
    _, year, suffix = invoice.number.split("-")
    number = f"LS-{year}-{suffix}"
    db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _DOC_NUMBER_LOCK_KEY})
    dn = _build_delivery_note(dn_data, number, source_invoice_id=invoice.id)
    db.add(dn)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise RuntimeError(f"Lieferscheinnummer {number} ist bereits vergeben")
    db.refresh(dn)
    return dn
