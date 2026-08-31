"""Datenbank-Operationen für Rechnungen."""
from calendar import monthrange
from datetime import datetime, date, timedelta

from sqlalchemy import func, text
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


def _lock_doc_numbers(db: Session) -> None:
    """Serialisiert die Vergabe der Belegnummern über alle gleichzeitigen
    Anfragen hinweg. Der Advisory-Lock ist PostgreSQL-spezifisch; die
    Testsuite läuft gegen SQLite (Single-Writer, kein paralleles Schreiben),
    dort ist er weder nötig noch verfügbar."""
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:k)"),
                   {"k": _DOC_NUMBER_LOCK_KEY})


def _page(query, limit: int | None, offset: int = 0) -> tuple[list, int]:
    """Schneidet eine Ergebnisliste auf ein Fenster zu.

    Rückgabe: (Zeilen, Gesamtzahl vor der Begrenzung). Ohne `limit` kommen
    weiterhin alle Zeilen zurück – das Frontend hält kleine Listen komplett
    im Speicher und filtert dort. `limit` deckelt zusätzlich hart bei
    config.MAX_PAGE_SIZE, damit ein API-Client den Server nicht mit
    ?limit=999999 belasten kann."""
    total = query.order_by(None).count()
    if offset:
        query = query.offset(offset)
    if limit:
        query = query.limit(min(limit, config.MAX_PAGE_SIZE))
    return query.all(), total


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
        _table_max_suffix(db, models.CreditNote, "GS", year),
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
        _lock_doc_numbers(db)
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


def list_invoices(db: Session, search: str | None = None,
                  limit: int | None = None, offset: int = 0) -> tuple[list, int]:
    """History: alle Rechnungen, neueste zuerst, optional gefiltert."""
    query = db.query(models.Invoice)
    if search:
        like = f"%{search}%"
        query = query.filter(
            models.Invoice.number.ilike(like)
            | models.Invoice.customer_name.ilike(like)
        )
    return _page(query.order_by(models.Invoice.id.desc()), limit, offset)


def get_invoice(db: Session, invoice_id: int) -> models.Invoice | None:
    return db.get(models.Invoice, invoice_id)


def set_status(db: Session, invoice: models.Invoice, status: str) -> models.Invoice:
    invoice.status = status
    invoice.cancelled_at = (
        datetime.utcnow() if status == models.STATUS_CANCELLED else None
    )
    # Zahlungsbetrag konsistent halten
    if status == models.STATUS_PAID:
        # Gutgeschriebenes ist nicht zu zahlen: nur der Rest zählt als Zahlung.
        invoice.paid_amount = round(invoice.total - invoice.credited_amount, 2)
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
# Ursprünglich nur für Invoice, inzwischen auch für Quote und DeliveryNote:
# alle drei Modelle tragen dieselben zwei Spalten (locked_by/locked_at), diese
# Funktionen arbeiten rein über die Spalten und kennen den konkreten Typ nicht.
Lockable = models.Invoice | models.Quote | models.DeliveryNote


def _lock_active(doc: Lockable) -> bool:
    return bool(
        doc.locked_by and doc.locked_at
        and datetime.utcnow() - doc.locked_at < LOCK_TIMEOUT
    )


def lock_status(doc: Lockable, username: str) -> dict:
    active = _lock_active(doc)
    return {
        "locked": active,
        "locked_by": doc.locked_by if active else None,
        "locked_at": doc.locked_at if active else None,
        "editable": (not active) or doc.locked_by == username,
    }


def acquire_lock(db: Session, doc: Lockable, username: str) -> bool:
    """Versucht, die Bearbeitungssperre für `username` zu setzen/erneuern.
    Gibt False zurück, wenn ein anderer Benutzer den Beleg aktiv sperrt."""
    if _lock_active(doc) and doc.locked_by != username:
        return False
    doc.locked_by = username
    doc.locked_at = datetime.utcnow()
    db.commit()
    return True


def release_lock(db: Session, doc: Lockable, username: str) -> None:
    if doc.locked_by == username:
        doc.locked_by = None
        doc.locked_at = None
        db.commit()


# --------------------------- Anwesenheit (Live-Anzeige) ------------------
# Ein Client meldet sich alle PRESENCE_HEARTBEAT Sekunden; wer sich länger
# als PRESENCE_TIMEOUT nicht gemeldet hat, gilt als weg (Tab geschlossen,
# Rechner zugeklappt). Der Puffer ist bewusst großzügig, damit ein kurzer
# Netzaussetzer niemanden aus der Anzeige wirft.
PRESENCE_HEARTBEAT = timedelta(seconds=10)
PRESENCE_TIMEOUT = timedelta(seconds=45)


def _purge_stale_presence(db: Session) -> None:
    cutoff = datetime.utcnow() - PRESENCE_TIMEOUT
    db.query(models.Presence).filter(models.Presence.last_seen < cutoff).delete(
        synchronize_session=False)


def touch_presence(db: Session, doc_type: str, doc_id: int,
                   username: str) -> list[models.Presence]:
    """Meldet den Benutzer als anwesend und liefert die *anderen* Anwesenden.

    Beim Wechsel auf einen anderen Beleg räumt die Funktion die alten
    Einträge desselben Benutzers gleich mit weg – sonst würde er auf mehreren
    Belegen gleichzeitig angezeigt, wenn er nur weitergeklickt hat.
    """
    _purge_stale_presence(db)
    now = datetime.utcnow()

    db.query(models.Presence).filter(
        models.Presence.username == username,
        (models.Presence.doc_type != doc_type) | (models.Presence.doc_id != doc_id),
    ).delete(synchronize_session=False)

    row = db.query(models.Presence).filter_by(
        doc_type=doc_type, doc_id=doc_id, username=username).one_or_none()
    if row:
        row.last_seen = now
    else:
        db.add(models.Presence(doc_type=doc_type, doc_id=doc_id,
                               username=username, last_seen=now))
    try:
        db.commit()
    except IntegrityError:
        # Zwei parallele Heartbeats desselben Benutzers – der andere war
        # zuerst da, sein Eintrag ist genauso gut wie unserer.
        db.rollback()

    return [p for p in active_presence(db, doc_type, doc_id)
            if p.username != username]


def leave_presence(db: Session, doc_type: str, doc_id: int, username: str) -> None:
    db.query(models.Presence).filter_by(
        doc_type=doc_type, doc_id=doc_id, username=username).delete(
            synchronize_session=False)
    db.commit()


def active_presence(db: Session, doc_type: str | None = None,
                    doc_id: int | None = None) -> list[models.Presence]:
    """Alle aktuell Anwesenden – optional auf einen Beleg eingegrenzt."""
    cutoff = datetime.utcnow() - PRESENCE_TIMEOUT
    q = db.query(models.Presence).filter(models.Presence.last_seen >= cutoff)
    if doc_type:
        q = q.filter(models.Presence.doc_type == doc_type)
    if doc_id is not None:
        q = q.filter(models.Presence.doc_id == doc_id)
    return q.order_by(models.Presence.username.asc()).all()


def dashboard_stats(db: Session) -> dict:
    """Kennzahlen + Monatsumsatz der letzten 6 Monate (nur nicht stornierte)."""
    invoices = (
        db.query(models.Invoice)
        .filter(models.Invoice.status != models.STATUS_CANCELLED)
        .all()
    )
    today = date.today()
    # Gutgeschriebenes ist kein Umsatz.
    total_revenue = sum(i.total - i.credited_amount for i in invoices)
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
        rev = sum(i.total - i.credited_amount for i in invoices
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
def list_customers(db: Session, active_only: bool = False, search: str | None = None,
                   limit: int | None = None, offset: int = 0) -> tuple[list, int]:
    q = db.query(models.Customer)
    if active_only:
        q = q.filter(models.Customer.active.is_(True))
    if search:
        like = f"%{search}%"
        q = q.filter(models.Customer.name.ilike(like)
                     | models.Customer.email.ilike(like)
                     | models.Customer.contact_person.ilike(like))
    return _page(q.order_by(models.Customer.name.asc()), limit, offset)


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


def get_customer_by_name(db: Session, name: str) -> models.Customer | None:
    """Kunde anhand des Namens (ohne Rücksicht auf Groß-/Kleinschreibung) –
    die Zuordnung beim CSV-Import, der keine IDs kennt."""
    return (db.query(models.Customer)
            .filter(func.lower(models.Customer.name) == name.strip().lower())
            .first())


def import_customers(db: Session, rows: list[schemas.CustomerIn]) -> tuple[int, int]:
    """Kunden aus einem Import übernehmen: gleicher Name = aktualisieren,
    sonst neu anlegen. Rückgabe: (angelegt, aktualisiert)."""
    created = updated = 0
    for data in rows:
        existing = get_customer_by_name(db, data.name)
        if existing:
            existing.name = data.name
            existing.address = data.address
            existing.contact_person = data.contact_person
            existing.email = data.email
            existing.payment_term_days = data.payment_term_days
            existing.skonto_percent = data.skonto_percent
            existing.skonto_days = data.skonto_days
            updated += 1
        else:
            db.add(models.Customer(
                name=data.name,
                address=data.address,
                contact_person=data.contact_person,
                email=data.email,
                payment_term_days=data.payment_term_days,
                skonto_percent=data.skonto_percent,
                skonto_days=data.skonto_days,
            ))
            # Ohne Flush fände eine Datei mit zwei gleichen Namen den eben
            # angelegten Kunden nicht (die Session flusht nicht automatisch).
            db.flush()
            created += 1
    db.commit()
    return created, updated


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
def list_products(db: Session, active_only: bool = False, search: str | None = None,
                  limit: int | None = None, offset: int = 0) -> tuple[list, int]:
    q = db.query(models.Product)
    if active_only:
        q = q.filter(models.Product.active.is_(True))
    if search:
        q = q.filter(models.Product.name.ilike(f"%{search}%"))
    return _page(q.order_by(models.Product.name.asc()), limit, offset)


def create_product(db: Session, data: schemas.ProductIn) -> models.Product:
    product = models.Product(name=data.name, unit_price=data.unit_price)
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


def get_product(db: Session, product_id: int) -> models.Product | None:
    return db.get(models.Product, product_id)


def get_product_by_name(db: Session, name: str) -> models.Product | None:
    """Artikel anhand der Bezeichnung (ohne Rücksicht auf Groß-/Kleinschreibung)
    – die Zuordnung beim Import, der keine IDs kennt."""
    return (db.query(models.Product)
            .filter(func.lower(models.Product.name) == name.strip().lower())
            .first())


def import_products(db: Session, rows: list[schemas.ProductIn]) -> tuple[int, int]:
    """Artikel aus einem Import übernehmen: gleiche Bezeichnung = Preis
    aktualisieren, sonst neu anlegen. Rückgabe: (angelegt, aktualisiert).
    Spiegelbild von import_customers."""
    created = updated = 0
    for data in rows:
        existing = get_product_by_name(db, data.name)
        if existing:
            existing.name = data.name
            existing.unit_price = data.unit_price
            updated += 1
        else:
            db.add(models.Product(name=data.name, unit_price=data.unit_price))
            # Ohne Flush fände eine Datei mit zweimal derselben Bezeichnung den
            # eben angelegten Artikel nicht (die Session flusht nicht automatisch).
            db.flush()
            created += 1
    db.commit()
    return created, updated


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


def list_audit_log(db: Session, limit: int | None = 200,
                   offset: int = 0) -> tuple[list, int]:
    return _page(db.query(models.AuditLog).order_by(models.AuditLog.id.desc()),
                 limit, offset)


# --------------------------- Angebote (Quotes) ---------------------------
def _build_quote(data: schemas.QuoteIn, number: str) -> models.Quote:
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
    return quote


def create_quote(db: Session, data: schemas.QuoteIn) -> models.Quote:
    for _ in range(10):
        _lock_doc_numbers(db)
        year = date.today().year
        number = f"AN-{year}-{_next_doc_suffix(db, year):04d}"
        quote = _build_quote(data, number)
        db.add(quote)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            continue
        db.refresh(quote)
        return quote
    raise RuntimeError("Konnte keine eindeutige Angebotsnummer vergeben")


def list_quotes(db: Session, search: str | None = None,
                limit: int | None = None, offset: int = 0) -> tuple[list, int]:
    q = db.query(models.Quote)
    if search:
        like = f"%{search}%"
        q = q.filter(models.Quote.number.ilike(like)
                     | models.Quote.customer_name.ilike(like))
    return _page(q.order_by(models.Quote.id.desc()), limit, offset)


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
    _lock_doc_numbers(db)
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
        _lock_doc_numbers(db)
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


def list_delivery_notes(db: Session, search: str | None = None,
                        limit: int | None = None, offset: int = 0) -> tuple[list, int]:
    q = db.query(models.DeliveryNote)
    if search:
        like = f"%{search}%"
        q = q.filter(models.DeliveryNote.number.ilike(like)
                     | models.DeliveryNote.customer_name.ilike(like))
    return _page(q.order_by(models.DeliveryNote.id.desc()), limit, offset)


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


def convert_delivery_note_to_quote(db: Session, dn: models.DeliveryNote) -> models.Quote:
    """Erstellt aus einem Lieferschein ein Angebot. Beschreibung und Menge
    kommen aus dem Lieferschein, der Preis – den ein Lieferschein nicht kennt
    – aus dem Artikelstamm, sofern die Beschreibung dort steht; sonst 0, dann
    trägt man ihn im Angebot nach.

    Die laufende Nummer wird wie bei den anderen Umwandlungen
    weitergeschoben (LS-2026-0007 -> AN-2026-0007). Anders als dort kann die
    Zielnummer aber schon belegt sein: der Lieferschein kann aus einer
    Rechnung stammen, die wiederum aus genau diesem Angebot entstanden ist.
    In dem Fall bekommt das Angebot die nächste freie Nummer."""
    prices = {p.name: p.unit_price for p in db.query(models.Product).all()}
    data = schemas.QuoteIn(
        customer_name=dn.customer_name,
        customer_address=dn.customer_address,
        customer_contact_person=dn.customer_contact_person,
        notes=dn.notes,
        items=[
            schemas.QuoteItemIn(description=it.description, quantity=it.quantity,
                                unit_price=prices.get(it.description, 0))
            for it in dn.items
        ],
    )
    _, year, suffix = dn.number.split("-")
    _lock_doc_numbers(db)
    number = f"AN-{year}-{suffix}"
    if db.query(models.Quote).filter(models.Quote.number == number).first():
        this_year = date.today().year
        number = f"AN-{this_year}-{_next_doc_suffix(db, this_year):04d}"
    quote = _build_quote(data, number)
    quote.source_delivery_note_id = dn.id
    db.add(quote)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise RuntimeError(f"Angebotsnummer {number} ist bereits vergeben")
    db.refresh(quote)
    return quote


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
    _lock_doc_numbers(db)
    dn = _build_delivery_note(dn_data, number, source_invoice_id=invoice.id)
    db.add(dn)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise RuntimeError(f"Lieferscheinnummer {number} ist bereits vergeben")
    db.refresh(dn)
    return dn


# --------------------------- Gutschriften --------------------------------
def _build_credit_note(data: schemas.CreditNoteIn, number: str) -> models.CreditNote:
    cn = models.CreditNote(
        number=number,
        invoice_id=data.invoice_id,
        customer_name=data.customer_name,
        customer_address=data.customer_address,
        customer_contact_person=data.customer_contact_person,
        issue_date=date.today(),
        reason=data.reason,
        tax_rate=data.tax_rate,
        small_business=data.small_business,
    )
    for it in data.items:
        cn.items.append(models.CreditNoteItem(
            description=it.description, quantity=it.quantity,
            unit_price=it.unit_price,
        ))
    return cn


def create_credit_note(db: Session, data: schemas.CreditNoteIn) -> models.CreditNote:
    for _ in range(10):
        _lock_doc_numbers(db)
        year = date.today().year
        number = f"GS-{year}-{_next_doc_suffix(db, year):04d}"
        cn = _build_credit_note(data, number)
        db.add(cn)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            continue
        db.refresh(cn)
        return cn
    raise RuntimeError("Konnte keine eindeutige Gutschriftsnummer vergeben")


def list_credit_notes(db: Session, search: str | None = None,
                      limit: int | None = None, offset: int = 0) -> tuple[list, int]:
    q = db.query(models.CreditNote)
    if search:
        like = f"%{search}%"
        q = q.filter(models.CreditNote.number.ilike(like)
                     | models.CreditNote.customer_name.ilike(like))
    return _page(q.order_by(models.CreditNote.id.desc()), limit, offset)


def get_credit_note(db: Session, credit_note_id: int) -> models.CreditNote | None:
    return db.get(models.CreditNote, credit_note_id)


def update_credit_note(db: Session, cn: models.CreditNote,
                       data: schemas.CreditNoteIn) -> models.CreditNote:
    cn.customer_name = data.customer_name
    cn.customer_address = data.customer_address
    cn.customer_contact_person = data.customer_contact_person
    cn.invoice_id = data.invoice_id
    cn.reason = data.reason
    cn.tax_rate = data.tax_rate
    cn.small_business = data.small_business
    cn.items = [
        models.CreditNoteItem(description=it.description, quantity=it.quantity,
                              unit_price=it.unit_price)
        for it in data.items
    ]
    db.commit()
    db.refresh(cn)
    return cn


def set_credit_note_status(db: Session, cn: models.CreditNote,
                           status: str) -> models.CreditNote:
    cn.status = status
    db.commit()
    db.refresh(cn)
    return cn


def delete_credit_note(db: Session, cn: models.CreditNote) -> None:
    db.delete(cn)
    db.commit()


def credit_note_from_invoice(db: Session, invoice: models.Invoice,
                             data: schemas.CreditNoteFromInvoice) -> models.CreditNote:
    """Gutschrift zu einer Rechnung. Ohne Positionen wird alles
    gutgeschrieben (Vollgutschrift), sonst nur die übergebenen Zeilen
    (Teilgutschrift). Rabatt und Kleinunternehmer-Modus der Rechnung werden
    übernommen, der Rabatt dabei in die Einzelpreise eingerechnet."""
    factor = 1 - float(invoice.discount_percent or 0) / 100
    items = data.items or [
        schemas.CreditNoteItemIn(
            description=it.description,
            quantity=float(it.quantity),
            unit_price=round(float(it.unit_price) * factor, 2),
        )
        for it in invoice.items
    ]
    return create_credit_note(db, schemas.CreditNoteIn(
        customer_name=invoice.customer_name,
        customer_address=invoice.customer_address or "",
        customer_contact_person=invoice.customer_contact_person or "",
        invoice_id=invoice.id,
        reason=data.reason,
        tax_rate=float(invoice.tax_rate),
        small_business=bool(invoice.small_business),
        items=items,
    ))


def credit_notes_in_period(db: Session, start: date, end: date) -> list[models.CreditNote]:
    """Nicht stornierte Gutschriften mit Belegdatum im Zeitraum."""
    return (
        db.query(models.CreditNote)
        .filter(models.CreditNote.status != models.CN_CANCELLED)
        .filter(models.CreditNote.issue_date >= start)
        .filter(models.CreditNote.issue_date <= end)
        .order_by(models.CreditNote.issue_date.asc())
        .all()
    )


# --------------------------- PDF-Vorlagen --------------------------------
def list_pdf_templates(db: Session) -> list[models.PdfTemplate]:
    return (db.query(models.PdfTemplate)
            .order_by(models.PdfTemplate.is_default.desc(),
                      models.PdfTemplate.name.asc())
            .all())


def get_pdf_template(db: Session, template_id: int) -> models.PdfTemplate | None:
    return db.get(models.PdfTemplate, template_id)


def get_pdf_template_by_name(db: Session, name: str) -> models.PdfTemplate | None:
    return (db.query(models.PdfTemplate)
            .filter(models.PdfTemplate.name == name).first())


def default_pdf_template(db: Session) -> models.PdfTemplate | None:
    """Vorgabe-Vorlage, oder None – dann gilt pdf.DEFAULTS."""
    return (db.query(models.PdfTemplate)
            .filter(models.PdfTemplate.is_default.is_(True)).first())


def _apply_template(tpl: models.PdfTemplate, data: schemas.PdfTemplateIn) -> None:
    for field in ("name", "accent_color", "header_color", "font_family",
                  "font_size", "header_note", "footer_text", "show_logo", "show_qr",
                  "layout"):
        setattr(tpl, field, getattr(data, field))


def create_pdf_template(db: Session, data: schemas.PdfTemplateIn) -> models.PdfTemplate:
    tpl = models.PdfTemplate()
    _apply_template(tpl, data)
    # Die erste Vorlage ist automatisch die Vorgabe, sonst hätte sie niemand.
    tpl.is_default = db.query(models.PdfTemplate).count() == 0
    db.add(tpl)
    db.commit()
    db.refresh(tpl)
    return tpl


def update_pdf_template(db: Session, tpl: models.PdfTemplate,
                        data: schemas.PdfTemplateIn) -> models.PdfTemplate:
    _apply_template(tpl, data)
    db.commit()
    db.refresh(tpl)
    return tpl


def set_default_pdf_template(db: Session, tpl: models.PdfTemplate) -> models.PdfTemplate:
    db.query(models.PdfTemplate).update({models.PdfTemplate.is_default: False})
    tpl.is_default = True
    db.commit()
    db.refresh(tpl)
    return tpl


# Zwei fertige Vorlagen mit dem Standard-Layout, in Farbe und Schrift bewusst
# unterschiedlich, damit eine frische Installation nicht nur eine einzige
# Vorlage zur Auswahl hat. "Klassisch Blau" entspricht pdf.DEFAULTS – dem
# bisherigen festverdrahteten Aussehen – und wird dadurch bei einer leeren
# Tabelle automatisch die Vorgabe (create_pdf_template: die erste zählt).
_BUILTIN_STANDARD_TEMPLATES = [
    schemas.PdfTemplateIn(
        name="Klassisch Blau", layout="standard",
        accent_color="#2d6cdf", header_color="#2d3748",
        font_family="Helvetica", font_size=10,
        header_note="", footer_text="", show_logo=True, show_qr=True,
    ),
    schemas.PdfTemplateIn(
        name="Modern Dunkel", layout="standard",
        accent_color="#0f766e", header_color="#111827",
        font_family="Times", font_size=10,
        header_note="", footer_text="", show_logo=True, show_qr=True,
    ),
]


def _seed_standard_templates(db: Session) -> None:
    """Legt die beiden Standard-Layout-Vorlagen an, sofern noch keine Vorlage
    mit demselben Namen existiert – wer eine schon umbenannt oder gelöscht
    hat, bekommt also keine zweite hinterhergeschoben, außer der Name ist
    wieder frei."""
    for data in _BUILTIN_STANDARD_TEMPLATES:
        if get_pdf_template_by_name(db, data.name):
            continue
        create_pdf_template(db, data)


# Der Firmenvordruck ist der Grund, warum es das Layout "formular" gibt.
# Damit er unter "PDF-Vorlagen" und in der Auswahl beim Beleg auftaucht, legt
# der Start eine fertige Vorlage dafür an, statt sie jedem von Hand
# nachbauen zu lassen.
FORM_TEMPLATE_NAME = "Mechatronik Neubauer e.U."


def _seed_form_template(db: Session) -> None:
    """Beim Start: sorgt dafür, dass es eine Vordruck-Vorlage gibt.

    Nur, wenn noch keine Vorlage mit dem Layout "formular" existiert – wer
    seine eigene angelegt hat, bekommt keine zweite dazu.
    """
    if (db.query(models.PdfTemplate)
          .filter(models.PdfTemplate.layout == "formular").first()):
        return
    name = FORM_TEMPLATE_NAME
    if get_pdf_template_by_name(db, name):
        # Der Name ist schon für eine Standard-Vorlage vergeben.
        name = f"{FORM_TEMPLATE_NAME} (Vordruck)"
        if get_pdf_template_by_name(db, name):
            return
    create_pdf_template(db, schemas.PdfTemplateIn(
        name=name,
        layout="formular",
        # Die Akzentfarbe ist im Vordruck die Druckfarbe (blau), einen
        # GiroCode kennt das Layout nicht.
        accent_color="#2d6cdf",
        header_color="#2d3748",
        font_family="Helvetica",
        font_size=10,
        header_note="",
        footer_text="",
        show_logo=True,
        show_qr=False,
    ))


def seed_pdf_templates(db: Session) -> None:
    """Beim Start: sorgt dafür, dass mindestens drei Vorlagen zur Auswahl
    stehen, statt dass jede erst von Hand angelegt werden muss – zwei mit
    dem Standard-Layout in unterschiedlicher Farb- und Schriftwahl, dazu die
    Vordruck-Vorlage. Jede der drei prüft für sich, ob sie schon existiert
    (siehe _seed_standard_templates/_seed_form_template), eine bestehende
    Installation bekommt also nichts doppelt."""
    _seed_standard_templates(db)
    _seed_form_template(db)


def delete_pdf_template(db: Session, tpl: models.PdfTemplate) -> None:
    was_default = tpl.is_default
    db.delete(tpl)
    db.commit()
    if was_default:
        # Ohne Vorgabe stünde die App ohne Vorlage da: die nächste übernimmt.
        nxt = db.query(models.PdfTemplate).order_by(models.PdfTemplate.id.asc()).first()
        if nxt:
            nxt.is_default = True
            db.commit()
