"""FastAPI-App: Rechnungs-Web-Applikation."""
import csv
import io
import json
import zipfile
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException, Form, Query, Request, UploadFile, File
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from fastapi.responses import Response, FileResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session

from . import (crud, models, schemas, pdf, auth, email_service, config, backup,
               security, logging_setup)
from .database import get_db, init_db, SessionLocal

app = FastAPI(title="Rechnungs-App")

STATIC_DIR = Path(__file__).parent / "static"
VALID_STATUS = {models.STATUS_OPEN, models.STATUS_PAID, models.STATUS_CANCELLED}
VALID_QUOTE_STATUS = {models.QUOTE_OPEN, models.QUOTE_ACCEPTED, models.QUOTE_DECLINED}
VALID_DN_STATUS = {models.DN_OPEN, models.DN_CANCELLED}


@app.on_event("startup")
def on_startup():
    logging_setup.setup_logging()
    init_db()
    db = SessionLocal()
    try:
        crud.seed_users(db)
    finally:
        db.close()
    logging_setup.log.info("startup complete", extra={"fields": {
        "csrf": config.CSRF_ENABLED,
        "rate_limit": config.RATE_LIMIT_REQUESTS,
        "https_only_cookies": config.SESSION_HTTPS_ONLY,
    }})


def require_admin(request: Request, db: Session = Depends(get_db)) -> models.User:
    """Dependency: stellt sicher, dass der angemeldete Benutzer ein Admin ist."""
    username = auth.current_user(request)
    user = crud.get_user(db, username) if username else None
    if not user or not user.is_admin:
        raise HTTPException(403, "Nur für Administratoren")
    return user


# --------------------------- Auth-Schutz --------------------------------
# Innerste Middleware: läuft erst, wenn Session und CSRF-Prüfung durch sind,
# sodass request.session hier bereits verfügbar ist.
@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if auth.is_public(path) or auth.current_user(request):
        return await call_next(request)
    if path.startswith("/api"):
        return JSONResponse({"detail": "Nicht angemeldet"}, status_code=401)
    return RedirectResponse("/login")


# Von innen nach außen: Login-Prüfung -> CSRF -> Session -> Rate-Limit ->
# Sicherheits-Header -> Request-ID/Logging. Starlette führt die zuletzt
# registrierte Middleware zuerst aus, deshalb ist die Reihenfolge hier
# genau umgekehrt zur Durchlaufreihenfolge.
app.middleware("http")(security.csrf_middleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=config.SESSION_SECRET,
    same_site="strict",
    https_only=config.SESSION_HTTPS_ONLY,
    max_age=config.SESSION_MAX_AGE,
)
app.middleware("http")(security.rate_limit_middleware)
app.middleware("http")(security.security_headers_middleware)
app.middleware("http")(logging_setup.request_context_middleware)

app.add_exception_handler(StarletteHTTPException, logging_setup.http_exception_handler)
app.add_exception_handler(RequestValidationError, logging_setup.validation_exception_handler)
app.add_exception_handler(Exception, logging_setup.unhandled_exception_handler)


# --------------------------- Login / Logout -----------------------------
@app.get("/login")
def login_page():
    return FileResponse(STATIC_DIR / "login.html")


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...),
          csrf_token: str = Form(""), db: Session = Depends(get_db)):
    # Der Login läuft als klassisches Formular, das Token steckt deshalb im
    # Formularfeld statt im Header (die Middleware lässt /login durch).
    if config.CSRF_ENABLED and not security.csrf_token_valid(request, csrf_token):
        return RedirectResponse("/login?csrf=1", status_code=303)
    key = (request.client.host if request.client else "?") + "|" + username
    wait = auth.is_locked(key)
    if wait:
        return RedirectResponse(f"/login?locked={wait}", status_code=303)
    if crud.authenticate(db, username, password):
        auth.reset_failures(key)
        # Session-Fixation vermeiden: alles Alte verwerfen, frisches CSRF-Token
        request.session.clear()
        request.session["user"] = username
        security.ensure_csrf_token(request)
        logging_setup.log.info("login", extra={"fields": {"user": username}})
        return RedirectResponse("/", status_code=303)
    auth.register_failure(key)
    logging_setup.log.warning("login failed", extra={"fields": {"user": username}})
    return RedirectResponse("/login?error=1", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/api/me")
def me(request: Request, db: Session = Depends(get_db)):
    token = security.ensure_csrf_token(request)
    user = crud.get_user(db, auth.current_user(request))
    if not user:
        return {"user": None, "is_admin": False, "csrf_token": token}
    return {"user": user.username, "is_admin": user.is_admin, "csrf_token": token}


# --------------------------- Benutzerverwaltung (nur Admin) -------------
@app.get("/api/users", response_model=list[schemas.UserOut])
def list_users(admin: models.User = Depends(require_admin),
               db: Session = Depends(get_db)):
    return crud.list_users(db)


@app.post("/api/users", response_model=schemas.UserOut, status_code=201)
def create_user(data: schemas.UserCreate,
                admin: models.User = Depends(require_admin),
                db: Session = Depends(get_db)):
    if crud.get_user(db, data.username):
        raise HTTPException(409, "Benutzername bereits vergeben")
    return crud.create_user(db, data.username.strip(), data.password, data.is_admin)


@app.post("/api/users/{user_id}/password")
def reset_password(user_id: int, data: schemas.PasswordReset,
                   admin: models.User = Depends(require_admin),
                   db: Session = Depends(get_db)):
    user = db.get(models.User, user_id)
    if not user:
        raise HTTPException(404, "Benutzer nicht gefunden")
    crud.set_user_password(db, user, data.password)
    return {"ok": True}


@app.delete("/api/users/{user_id}", status_code=204)
def delete_user(user_id: int, admin: models.User = Depends(require_admin),
                db: Session = Depends(get_db)):
    user = db.get(models.User, user_id)
    if not user:
        raise HTTPException(404, "Benutzer nicht gefunden")
    if user.id == admin.id:
        raise HTTPException(400, "Sie können sich nicht selbst löschen")
    if user.is_admin and crud.count_admins(db) <= 1:
        raise HTTPException(400, "Der letzte Administrator kann nicht gelöscht werden")
    crud.delete_user(db, user)
    return Response(status_code=204)


# --------------------------- API ----------------------------------------
# Listen-Endpunkte unterstützen ?limit=&offset=. Ohne Parameter kommt weiterhin
# die vollständige Liste – die Gesamtzahl steht immer im Header X-Total-Count.
Limit = Query(None, ge=1, description="Maximale Anzahl Zeilen (optional)")
Offset = Query(0, ge=0, description="Zu überspringende Zeilen")


def _with_total(response: Response, rows_and_total: tuple[list, int]) -> list:
    rows, total = rows_and_total
    response.headers["X-Total-Count"] = str(total)
    return rows


@app.get("/api/invoices", response_model=list[schemas.InvoiceOut])
def list_invoices(response: Response, search: str | None = None,
                  limit: int | None = Limit, offset: int = Offset,
                  db: Session = Depends(get_db)):
    return _with_total(response, crud.list_invoices(db, search, limit, offset))


@app.post("/api/invoices", response_model=schemas.InvoiceOut, status_code=201)
def create_invoice(data: schemas.InvoiceIn, db: Session = Depends(get_db)):
    invoice = crud.create_invoice(db, data)
    if data.auto_email:
        customer = db.query(models.Customer).filter(
            models.Customer.name == invoice.customer_name).first()
        if customer and customer.email:
            try:
                email_service.send_invoice_email(invoice, customer.email, crud.get_settings(db))
            except OSError:
                pass  # Versand ist ein Komfort-Extra, darf das Speichern nicht blockieren
    return invoice


@app.get("/api/invoices/{invoice_id}", response_model=schemas.InvoiceOut)
def get_invoice(invoice_id: int, db: Session = Depends(get_db)):
    invoice = crud.get_invoice(db, invoice_id)
    if not invoice:
        raise HTTPException(404, "Rechnung nicht gefunden")
    return invoice


@app.get("/api/invoices/{invoice_id}/lock", response_model=schemas.LockOut)
def get_lock(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    invoice = crud.get_invoice(db, invoice_id)
    if not invoice:
        raise HTTPException(404, "Rechnung nicht gefunden")
    return crud.lock_status(invoice, auth.current_user(request))


@app.post("/api/invoices/{invoice_id}/lock", response_model=schemas.LockOut)
def acquire_lock(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    """Wird beim Öffnen einer Rechnung zum Bearbeiten aufgerufen. Solange der
    Bearbeitende die Sperre regelmäßig erneuert, kann kein zweiter Benutzer
    gleichzeitig dieselbe Rechnung bearbeiten."""
    invoice = crud.get_invoice(db, invoice_id)
    if not invoice:
        raise HTTPException(404, "Rechnung nicht gefunden")
    username = auth.current_user(request)
    crud.acquire_lock(db, invoice, username)
    return crud.lock_status(invoice, username)


@app.delete("/api/invoices/{invoice_id}/lock", response_model=schemas.LockOut)
def release_lock(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    invoice = crud.get_invoice(db, invoice_id)
    if not invoice:
        raise HTTPException(404, "Rechnung nicht gefunden")
    username = auth.current_user(request)
    crud.release_lock(db, invoice, username)
    return crud.lock_status(invoice, username)


@app.put("/api/invoices/{invoice_id}", response_model=schemas.InvoiceOut)
def edit_invoice(invoice_id: int, data: schemas.InvoiceUpdate, request: Request,
                 db: Session = Depends(get_db)):
    invoice = crud.get_invoice(db, invoice_id)
    if not invoice:
        raise HTTPException(404, "Rechnung nicht gefunden")
    if invoice.status == models.STATUS_CANCELLED:
        raise HTTPException(400, "Stornierte Rechnung kann nicht bearbeitet werden")
    username = auth.current_user(request)
    if not crud.lock_status(invoice, username)["editable"]:
        raise HTTPException(409, f"Wird gerade von {invoice.locked_by} bearbeitet")
    return crud.update_invoice(db, invoice, data)


@app.patch("/api/invoices/{invoice_id}/status", response_model=schemas.InvoiceOut)
def update_status(invoice_id: int, body: schemas.StatusUpdate,
                  db: Session = Depends(get_db)):
    """Status ändern – inkl. Storno und Rück-/Reaktivierung (zurück auf 'offen')."""
    invoice = crud.get_invoice(db, invoice_id)
    if not invoice:
        raise HTTPException(404, "Rechnung nicht gefunden")
    if body.status not in VALID_STATUS:
        raise HTTPException(400, f"Ungültiger Status: {body.status}")
    was_paid = invoice.status == models.STATUS_PAID
    invoice = crud.set_status(db, invoice, body.status)
    _confirm_payment_if_settled(db, invoice, was_paid)
    return invoice


@app.delete("/api/invoices/{invoice_id}", status_code=204)
def delete_invoice(invoice_id: int, db: Session = Depends(get_db)):
    invoice = crud.get_invoice(db, invoice_id)
    if not invoice:
        raise HTTPException(404, "Rechnung nicht gefunden")
    crud.delete_invoice(db, invoice)
    return Response(status_code=204)


def _confirm_payment_if_settled(db: Session, invoice: models.Invoice,
                                was_paid: bool) -> None:
    """Zahlungsbestätigung verschicken, sobald eine Rechnung vollständig
    beglichen ist – unabhängig davon, ob das über die Zahlungserfassung oder
    durch direktes Setzen des Status auf "bezahlt" passiert ist. Vorher hing
    die Mail nur am Zahlungs-Endpunkt, sodass der Kunde je nach Klickweg eine
    Bestätigung bekam oder eben nicht.

    Best effort: ein fehlgeschlagener Versand darf den Vorgang nicht
    abbrechen, wird aber protokolliert.
    """
    if was_paid or invoice.status != models.STATUS_PAID:
        return
    customer = db.query(models.Customer).filter(
        models.Customer.name == invoice.customer_name).first()
    if not (customer and customer.email):
        return
    try:
        email_service.send_payment_confirmation(invoice, customer.email,
                                                crud.get_settings(db))
        logging_setup.log.info("payment confirmation sent", extra={"fields": {
            "invoice": invoice.number, "to": customer.email}})
    except OSError as err:
        logging_setup.log.warning("payment confirmation failed", extra={"fields": {
            "invoice": invoice.number, "error": str(err)}})


@app.post("/api/invoices/{invoice_id}/payment", response_model=schemas.InvoiceOut)
def add_payment(invoice_id: int, body: schemas.PaymentRequest,
                db: Session = Depends(get_db)):
    invoice = crud.get_invoice(db, invoice_id)
    if not invoice:
        raise HTTPException(404, "Rechnung nicht gefunden")
    if invoice.status == models.STATUS_CANCELLED:
        raise HTTPException(400, "Stornierte Rechnung kann nicht bezahlt werden")
    was_paid = invoice.status == models.STATUS_PAID
    invoice = crud.add_payment(db, invoice, body.amount)
    _confirm_payment_if_settled(db, invoice, was_paid)
    return invoice


@app.get("/api/invoices/{invoice_id}/pdf")
def download_pdf(invoice_id: int, db: Session = Depends(get_db)):
    invoice = crud.get_invoice(db, invoice_id)
    if not invoice:
        raise HTTPException(404, "Rechnung nicht gefunden")
    data = pdf.invoice_pdf(invoice, crud.get_settings(db))
    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{invoice.number}.pdf"'
        },
    )


def _valid_email(addr: str) -> str:
    addr = addr.strip()
    if "@" not in addr or "." not in addr.split("@")[-1]:
        raise HTTPException(400, "Ungültige E-Mail-Adresse")
    return addr


@app.post("/api/invoices/{invoice_id}/email")
def email_invoice(invoice_id: int, body: schemas.EmailRequest,
                  db: Session = Depends(get_db)):
    invoice = crud.get_invoice(db, invoice_id)
    if not invoice:
        raise HTTPException(404, "Rechnung nicht gefunden")
    to = _valid_email(body.to)
    try:
        email_service.send_invoice_email(invoice, to, crud.get_settings(db))
    except OSError as err:
        raise HTTPException(502, f"E-Mail konnte nicht gesendet werden: {err}")
    return {"sent": True, "to": to, "number": invoice.number}


@app.post("/api/invoices/{invoice_id}/reminder")
def remind_invoice(invoice_id: int, body: schemas.EmailRequest,
                   db: Session = Depends(get_db)):
    invoice = crud.get_invoice(db, invoice_id)
    if not invoice:
        raise HTTPException(404, "Rechnung nicht gefunden")
    to = _valid_email(body.to)
    try:
        email_service.send_reminder_email(invoice, to, crud.get_settings(db))
    except OSError as err:
        raise HTTPException(502, f"E-Mail konnte nicht gesendet werden: {err}")
    return {"sent": True, "to": to, "number": invoice.number}


@app.get("/api/export")
def export_month(month: str, db: Session = Depends(get_db)):
    """Exportiert alle Rechnungen eines Monats (Format YYYY-MM) als ZIP:
    je Rechnung ein PDF plus eine CSV-Übersicht."""
    try:
        year, mon = (int(p) for p in month.split("-"))
        if not 1 <= mon <= 12:
            raise ValueError
    except ValueError:
        raise HTTPException(400, "Monat muss im Format JJJJ-MM angegeben werden")

    invoices = crud.invoices_in_month(db, year, mon)
    if not invoices:
        raise HTTPException(404, "Keine Rechnungen in diesem Monat")

    settings = crud.get_settings(db)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        rows = ["Nummer;Datum;Kunde;Status;Netto;MwSt;Gesamt"]
        for inv in invoices:
            zf.writestr(f"{inv.number}.pdf", pdf.invoice_pdf(inv, settings))
            rows.append(
                f"{inv.number};{inv.issue_date.strftime('%d/%m/%Y')};"
                f"{inv.customer_name};{inv.status};"
                f"{inv.subtotal:.2f};{inv.tax_amount:.2f};{inv.total:.2f}"
            )
        # CSV mit BOM, damit Excel die Umlaute korrekt anzeigt
        zf.writestr("uebersicht.csv", "﻿" + "\r\n".join(rows))

    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="Rechnungen_{year}-{mon:02d}.zip"'
        },
    )


# --------------------------- Kunden -------------------------------------
@app.get("/api/customers", response_model=list[schemas.CustomerOut])
def list_customers(response: Response, active_only: bool = False,
                   search: str | None = None, limit: int | None = Limit,
                   offset: int = Offset, db: Session = Depends(get_db)):
    return _with_total(response,
                       crud.list_customers(db, active_only, search, limit, offset))


@app.post("/api/customers", response_model=schemas.CustomerOut, status_code=201)
def create_customer(data: schemas.CustomerIn, db: Session = Depends(get_db)):
    return crud.create_customer(db, data)


@app.put("/api/customers/{customer_id}", response_model=schemas.CustomerOut)
def edit_customer(customer_id: int, data: schemas.CustomerIn, db: Session = Depends(get_db)):
    customer = crud.get_customer(db, customer_id)
    if not customer:
        raise HTTPException(404, "Kunde nicht gefunden")
    return crud.update_customer(db, customer, data)


@app.patch("/api/customers/{customer_id}/active", response_model=schemas.CustomerOut)
def set_customer_active(customer_id: int, body: schemas.ActiveUpdate,
                        db: Session = Depends(get_db)):
    customer = crud.get_customer(db, customer_id)
    if not customer:
        raise HTTPException(404, "Kunde nicht gefunden")
    return crud.set_customer_active(db, customer, body.active)


# Spalten- bzw. Schlüsselnamen eines Imports: deutsche wie englische
# Schreibweisen, damit sowohl ein Excel-Export der eigenen Kundenliste als
# auch der JSON-Export dieser App (DSGVO Art. 15) ohne Nacharbeit passt.
CSV_COLUMNS = {
    "name": ("name", "kunde", "kundenname", "firma", "customer", "customer_name"),
    "email": ("email", "e-mail", "mail", "e_mail", "emailadresse"),
    "contact_person": ("ansprechpartner", "kontakt", "contact", "contact_person"),
    "address": ("anschrift", "adresse", "address", "strasse", "straße"),
    "payment_term_days": ("zahlungsfrist", "zahlungsziel", "zahlungsfrist_tage",
                          "payment_term_days", "payment_term"),
    "skonto_percent": ("skonto", "skonto_prozent", "skonto_percent", "skonto%"),
    "skonto_days": ("skonto_tage", "skontotage", "skonto_days", "skonto_frist"),
}
FIELD_BY_ALIAS = {alias: field for field, aliases in CSV_COLUMNS.items()
                  for alias in aliases}
NUMBER_FIELDS = ("payment_term_days", "skonto_percent", "skonto_days")
IMPORT_MAX_BYTES = 1_000_000
IMPORT_MAX_ROWS = 5_000
IMPORT_MAX_ERRORS = 20


def _import_text(raw: bytes) -> str:
    """Import-Bytes dekodieren. Excel schreibt hierzulande gern cp1252 statt UTF-8."""
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise HTTPException(400, "Datei ist nicht lesbar (weder UTF-8 noch Windows-1252)")


def _field_for(key: str) -> str | None:
    """Spaltenüberschrift bzw. JSON-Schlüssel auf ein Feld von CustomerIn abbilden."""
    return FIELD_BY_ALIAS.get(str(key or "").strip().lstrip("\ufeff").lower())


def _map_customer_row(raw: dict) -> dict:
    """Rohdatensatz (CSV-Zeile oder JSON-Objekt) auf CustomerIn-Felder
    eindampfen. Unbekannte Schlüssel (id, active, invoices …) fallen weg."""
    values: dict[str, str] = {}
    for key, value in raw.items():
        field = _field_for(key)
        if not field or field in values or value is None:
            continue
        values[field] = str(value).strip()
    for field in NUMBER_FIELDS:
        if field in values:
            # "12,5" (deutsche Schreibweise) -> "12.5"; leer = Vorgabewert
            values[field] = values[field].replace(",", ".")
            if not values[field]:
                values.pop(field)
    return values


def _rows_from_csv(text_content: str) -> list[dict]:
    first_line = text_content.splitlines()[0] if text_content.strip() else ""
    delimiter = ";" if first_line.count(";") > first_line.count(",") else ","
    if "\t" in first_line and first_line.count("\t") > first_line.count(delimiter):
        delimiter = "\t"
    reader = csv.DictReader(io.StringIO(text_content), delimiter=delimiter)
    if not any(_field_for(h) == "name" for h in reader.fieldnames or []):
        raise HTTPException(400, "Es fehlt eine Spalte mit dem Kundennamen "
                                 "(z. B. \"name\") in der Kopfzeile")
    return list(reader)


def _rows_from_json(text_content: str) -> list[dict]:
    """Kunden aus einer JSON-Datei ziehen. Akzeptiert den Export dieser App
    ({"customer": {...}, "invoices": [...]}), eine Liste von Kunden, ein
    einzelnes Kundenobjekt und {"customers": [...]}."""
    try:
        data = json.loads(text_content)
    except json.JSONDecodeError as err:
        raise HTTPException(400, f"Die JSON-Datei ist fehlerhaft: {err.msg} "
                                 f"(Zeile {err.lineno})")
    if isinstance(data, dict):
        if isinstance(data.get("customer"), dict):
            data = [data["customer"]]
        elif isinstance(data.get("customers"), list):
            data = data["customers"]
        else:
            data = [data]
    if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
        raise HTTPException(400, "Unerwarteter Aufbau: erwartet wird ein Kundenobjekt "
                                 "oder eine Liste von Kundenobjekten")
    if not any(_field_for(key) == "name" for row in data for key in row):
        raise HTTPException(400, "Es fehlt ein Feld mit dem Kundennamen "
                                 "(z. B. \"name\")")
    return data


def _looks_like_json(filename: str, text_content: str) -> bool:
    if (filename or "").lower().endswith(".json"):
        return True
    return text_content.lstrip()[:1] in ("{", "[")


@app.post("/api/customers/import", response_model=schemas.CustomerImportResult)
async def import_customers(request: Request, file: UploadFile = File(...),
                           db: Session = Depends(get_db)):
    """Kundenstamm aus einer CSV- oder JSON-Datei übernehmen.

    JSON schließt den Kundenexport dieser App ein (DSGVO Art. 15), sodass
    Export und Import zueinander passen. Pflichtangabe ist der Name, alles
    andere ist optional. Ein bereits vorhandener Kunde (gleicher Name) wird
    aktualisiert statt doppelt angelegt. Fehlerhafte Datensätze werden
    übersprungen und einzeln gemeldet – ein Tippfehler im 20. Datensatz soll
    die anderen 19 nicht verhindern."""
    raw = await file.read()
    if not raw.strip():
        raise HTTPException(400, "Die Datei ist leer")
    if len(raw) > IMPORT_MAX_BYTES:
        raise HTTPException(400, "Datei ist zu groß (max. 1 MB)")

    text_content = _import_text(raw)
    raw_rows = (_rows_from_json(text_content)
                if _looks_like_json(file.filename or "", text_content)
                else _rows_from_csv(text_content))
    if len(raw_rows) > IMPORT_MAX_ROWS:
        raise HTTPException(400, f"Zu viele Datensätze (max. {IMPORT_MAX_ROWS})")

    rows: list[schemas.CustomerIn] = []
    errors: list[str] = []
    skipped = 0
    for number, raw_row in enumerate(raw_rows, start=1):
        values = _map_customer_row(raw_row)
        if not values.get("name"):
            skipped += 1
            continue
        try:
            rows.append(schemas.CustomerIn(**values))
        except ValidationError:
            skipped += 1
            if len(errors) < IMPORT_MAX_ERRORS:
                errors.append(f"Datensatz {number} ({values['name']}): ungültige Werte")

    created, updated = crud.import_customers(db, rows)
    crud.log_action(db, auth.current_user(request) or "", "import", "customer", None,
                    f"Import: {created} neu, {updated} aktualisiert, "
                    f"{skipped} übersprungen")
    return schemas.CustomerImportResult(created=created, updated=updated,
                                        skipped=skipped, errors=errors)


@app.get("/api/customers/{customer_id}/export", response_model=schemas.CustomerExportOut)
def export_customer(customer_id: int, request: Request,
                    admin: models.User = Depends(require_admin),
                    db: Session = Depends(get_db)):
    """DSGVO Art. 15: Auskunft über gespeicherte Daten eines Kunden."""
    customer = crud.get_customer(db, customer_id)
    if not customer:
        raise HTTPException(404, "Kunde nicht gefunden")
    data = crud.export_customer_data(db, customer)
    crud.log_action(db, auth.current_user(request), "export", "customer",
                    customer_id, customer.name)
    return data


@app.post("/api/customers/{customer_id}/anonymize", response_model=schemas.CustomerOut)
def anonymize_customer(customer_id: int, request: Request,
                       admin: models.User = Depends(require_admin),
                       db: Session = Depends(get_db)):
    """DSGVO Art. 17: personenbezogene Daten des Kunden löschen. Bereits
    ausgestellte Rechnungen bleiben aus steuerrechtlichen Gründen unverändert."""
    customer = crud.get_customer(db, customer_id)
    if not customer:
        raise HTTPException(404, "Kunde nicht gefunden")
    name = customer.name
    result = crud.anonymize_customer(db, customer)
    crud.log_action(db, auth.current_user(request), "anonymize", "customer",
                    customer_id, name)
    return result


@app.delete("/api/customers/{customer_id}", status_code=204)
def delete_customer(customer_id: int, db: Session = Depends(get_db)):
    customer = crud.get_customer(db, customer_id)
    if not customer:
        raise HTTPException(404, "Kunde nicht gefunden")
    crud.delete_customer(db, customer)
    return Response(status_code=204)


# --------------------------- Artikel / Leistungen -----------------------
@app.get("/api/products", response_model=list[schemas.ProductOut])
def list_products(response: Response, active_only: bool = False,
                  search: str | None = None, limit: int | None = Limit,
                  offset: int = Offset, db: Session = Depends(get_db)):
    return _with_total(response,
                       crud.list_products(db, active_only, search, limit, offset))


@app.post("/api/products", response_model=schemas.ProductOut, status_code=201)
def create_product(data: schemas.ProductIn, db: Session = Depends(get_db)):
    return crud.create_product(db, data)


@app.put("/api/products/{product_id}", response_model=schemas.ProductOut)
def edit_product(product_id: int, data: schemas.ProductIn, db: Session = Depends(get_db)):
    product = crud.get_product(db, product_id)
    if not product:
        raise HTTPException(404, "Artikel nicht gefunden")
    return crud.update_product(db, product, data)


@app.patch("/api/products/{product_id}/active", response_model=schemas.ProductOut)
def set_product_active(product_id: int, body: schemas.ActiveUpdate,
                       db: Session = Depends(get_db)):
    product = crud.get_product(db, product_id)
    if not product:
        raise HTTPException(404, "Artikel nicht gefunden")
    return crud.set_product_active(db, product, body.active)


@app.delete("/api/products/{product_id}", status_code=204)
def delete_product(product_id: int, db: Session = Depends(get_db)):
    product = crud.get_product(db, product_id)
    if not product:
        raise HTTPException(404, "Artikel nicht gefunden")
    crud.delete_product(db, product)
    return Response(status_code=204)


# --------------------------- Angebote (Quotes) ---------------------------
@app.get("/api/quotes", response_model=list[schemas.QuoteOut])
def list_quotes(response: Response, search: str | None = None,
                limit: int | None = Limit, offset: int = Offset,
                db: Session = Depends(get_db)):
    return _with_total(response, crud.list_quotes(db, search, limit, offset))


@app.post("/api/quotes", response_model=schemas.QuoteOut, status_code=201)
def create_quote(data: schemas.QuoteIn, db: Session = Depends(get_db)):
    return crud.create_quote(db, data)


@app.get("/api/quotes/{quote_id}", response_model=schemas.QuoteOut)
def get_quote(quote_id: int, db: Session = Depends(get_db)):
    quote = crud.get_quote(db, quote_id)
    if not quote:
        raise HTTPException(404, "Angebot nicht gefunden")
    return quote


@app.put("/api/quotes/{quote_id}", response_model=schemas.QuoteOut)
def edit_quote(quote_id: int, data: schemas.QuoteIn, db: Session = Depends(get_db)):
    quote = crud.get_quote(db, quote_id)
    if not quote:
        raise HTTPException(404, "Angebot nicht gefunden")
    if quote.status == models.QUOTE_CONVERTED:
        raise HTTPException(400, "Umgewandeltes Angebot kann nicht mehr bearbeitet werden")
    return crud.update_quote(db, quote, data)


@app.patch("/api/quotes/{quote_id}/status", response_model=schemas.QuoteOut)
def update_quote_status(quote_id: int, body: schemas.QuoteStatusUpdate,
                        db: Session = Depends(get_db)):
    quote = crud.get_quote(db, quote_id)
    if not quote:
        raise HTTPException(404, "Angebot nicht gefunden")
    if body.status not in VALID_QUOTE_STATUS:
        raise HTTPException(400, f"Ungültiger Status: {body.status}")
    return crud.set_quote_status(db, quote, body.status)


@app.delete("/api/quotes/{quote_id}", status_code=204)
def delete_quote(quote_id: int, db: Session = Depends(get_db)):
    quote = crud.get_quote(db, quote_id)
    if not quote:
        raise HTTPException(404, "Angebot nicht gefunden")
    crud.delete_quote(db, quote)
    return Response(status_code=204)


@app.get("/api/quotes/{quote_id}/pdf")
def download_quote_pdf(quote_id: int, db: Session = Depends(get_db)):
    quote = crud.get_quote(db, quote_id)
    if not quote:
        raise HTTPException(404, "Angebot nicht gefunden")
    data = pdf.quote_pdf(quote, crud.get_settings(db))
    return Response(
        content=data, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{quote.number}.pdf"'},
    )


@app.post("/api/quotes/{quote_id}/email")
def email_quote(quote_id: int, body: schemas.EmailRequest, db: Session = Depends(get_db)):
    quote = crud.get_quote(db, quote_id)
    if not quote:
        raise HTTPException(404, "Angebot nicht gefunden")
    to = _valid_email(body.to)
    try:
        email_service.send_quote_email(quote, to, crud.get_settings(db))
    except OSError as err:
        raise HTTPException(502, f"E-Mail konnte nicht gesendet werden: {err}")
    return {"sent": True, "to": to, "number": quote.number}


@app.post("/api/quotes/{quote_id}/convert", response_model=schemas.InvoiceOut, status_code=201)
def convert_quote(quote_id: int, db: Session = Depends(get_db)):
    quote = crud.get_quote(db, quote_id)
    if not quote:
        raise HTTPException(404, "Angebot nicht gefunden")
    if quote.status == models.QUOTE_CONVERTED:
        raise HTTPException(400, "Angebot wurde bereits umgewandelt")
    return crud.convert_quote_to_invoice(db, quote)


# --------------------------- Lieferscheine (Delivery Notes) --------------
@app.get("/api/delivery-notes", response_model=list[schemas.DeliveryNoteOut])
def list_delivery_notes(response: Response, search: str | None = None,
                        limit: int | None = Limit, offset: int = Offset,
                        db: Session = Depends(get_db)):
    return _with_total(response,
                       crud.list_delivery_notes(db, search, limit, offset))


@app.post("/api/delivery-notes", response_model=schemas.DeliveryNoteOut, status_code=201)
def create_delivery_note(data: schemas.DeliveryNoteIn, db: Session = Depends(get_db)):
    return crud.create_delivery_note(db, data)


@app.get("/api/delivery-notes/{dn_id}", response_model=schemas.DeliveryNoteOut)
def get_delivery_note(dn_id: int, db: Session = Depends(get_db)):
    dn = crud.get_delivery_note(db, dn_id)
    if not dn:
        raise HTTPException(404, "Lieferschein nicht gefunden")
    return dn


@app.put("/api/delivery-notes/{dn_id}", response_model=schemas.DeliveryNoteOut)
def edit_delivery_note(dn_id: int, data: schemas.DeliveryNoteIn, db: Session = Depends(get_db)):
    dn = crud.get_delivery_note(db, dn_id)
    if not dn:
        raise HTTPException(404, "Lieferschein nicht gefunden")
    return crud.update_delivery_note(db, dn, data)


@app.patch("/api/delivery-notes/{dn_id}/status", response_model=schemas.DeliveryNoteOut)
def update_delivery_note_status(dn_id: int, body: schemas.DeliveryNoteStatusUpdate,
                                db: Session = Depends(get_db)):
    dn = crud.get_delivery_note(db, dn_id)
    if not dn:
        raise HTTPException(404, "Lieferschein nicht gefunden")
    if body.status not in VALID_DN_STATUS:
        raise HTTPException(400, f"Ungültiger Status: {body.status}")
    return crud.set_delivery_note_status(db, dn, body.status)


@app.delete("/api/delivery-notes/{dn_id}", status_code=204)
def delete_delivery_note(dn_id: int, db: Session = Depends(get_db)):
    dn = crud.get_delivery_note(db, dn_id)
    if not dn:
        raise HTTPException(404, "Lieferschein nicht gefunden")
    crud.delete_delivery_note(db, dn)
    return Response(status_code=204)


@app.get("/api/delivery-notes/{dn_id}/pdf")
def download_delivery_note_pdf(dn_id: int, db: Session = Depends(get_db)):
    dn = crud.get_delivery_note(db, dn_id)
    if not dn:
        raise HTTPException(404, "Lieferschein nicht gefunden")
    data = pdf.delivery_note_pdf(dn, crud.get_settings(db))
    return Response(
        content=data, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{dn.number}.pdf"'},
    )


@app.post("/api/delivery-notes/{dn_id}/email")
def email_delivery_note(dn_id: int, body: schemas.EmailRequest, db: Session = Depends(get_db)):
    dn = crud.get_delivery_note(db, dn_id)
    if not dn:
        raise HTTPException(404, "Lieferschein nicht gefunden")
    to = _valid_email(body.to)
    try:
        email_service.send_delivery_note_email(dn, to, crud.get_settings(db))
    except OSError as err:
        raise HTTPException(502, f"E-Mail konnte nicht gesendet werden: {err}")
    return {"sent": True, "to": to, "number": dn.number}


@app.post("/api/delivery-notes/{dn_id}/convert-to-quote",
          response_model=schemas.QuoteOut, status_code=201)
def convert_delivery_note(dn_id: int, db: Session = Depends(get_db)):
    """Lieferschein -> Angebot. Die Gegenrichtung zu Angebot -> Rechnung ->
    Lieferschein: aus einer Lieferung wird ein Angebot für die nächste."""
    dn = crud.get_delivery_note(db, dn_id)
    if not dn:
        raise HTTPException(404, "Lieferschein nicht gefunden")
    if dn.status == models.DN_CANCELLED:
        raise HTTPException(400, "Ein stornierter Lieferschein lässt sich nicht umwandeln")
    return crud.convert_delivery_note_to_quote(db, dn)


@app.post("/api/invoices/{invoice_id}/convert-to-delivery-note",
          response_model=schemas.DeliveryNoteOut, status_code=201)
def convert_invoice(invoice_id: int, db: Session = Depends(get_db)):
    invoice = crud.get_invoice(db, invoice_id)
    if not invoice:
        raise HTTPException(404, "Rechnung nicht gefunden")
    return crud.convert_invoice_to_delivery_note(db, invoice)


# --------------------------- Anwesenheit (Live-Anzeige) ------------------
# Kein WebSocket: die Clients fragen im Sekundentakt-Raster nach (Heartbeat
# alle ~10 s, Timeout 45 s). Das reicht für "jemand anderes ist auch hier",
# kommt ohne dauerhafte Verbindungen aus und funktioniert unverändert hinter
# dem nginx-Proxy.
def _check_doc_type(doc_type: str) -> str:
    if doc_type not in models.PRESENCE_TYPES:
        raise HTTPException(400, f"Unbekannte Belegart: {doc_type}")
    return doc_type


@app.post("/api/presence/{doc_type}/{doc_id}", response_model=schemas.PresenceOut)
def heartbeat_presence(doc_type: str, doc_id: int, request: Request,
                       db: Session = Depends(get_db)):
    """Meldet den angemeldeten Benutzer auf diesem Beleg an und liefert
    zurück, wer sonst gerade darauf ist."""
    _check_doc_type(doc_type)
    others = crud.touch_presence(db, doc_type, doc_id, auth.current_user(request))
    return {"others": others}


@app.delete("/api/presence/{doc_type}/{doc_id}", status_code=204)
def clear_presence(doc_type: str, doc_id: int, request: Request,
                   db: Session = Depends(get_db)):
    """Wird beim Verlassen des Formulars aufgerufen. Bleibt der Aufruf aus
    (Tab hart geschlossen), verfällt der Eintrag von selbst."""
    _check_doc_type(doc_type)
    crud.leave_presence(db, doc_type, doc_id, auth.current_user(request))
    return Response(status_code=204)


@app.get("/api/presence", response_model=list[schemas.PresenceDocOut])
def list_presence(request: Request, db: Session = Depends(get_db)):
    """Belege, auf denen gerade *andere* Benutzer sind – damit die Listen
    zeigen können, wo jemand drin sitzt, bevor man selbst hineinklickt.

    Der eigene Eintrag bleibt außen vor: „hier bist du gerade selbst" ist
    keine Information, die eine Markierung wert wäre (gleiche Semantik wie
    das `others` des Heartbeats)."""
    me = auth.current_user(request)
    grouped: dict[tuple[str, int], list] = {}
    for row in crud.active_presence(db):
        if row.username == me:
            continue
        grouped.setdefault((row.doc_type, row.doc_id), []).append(row)
    return [{"doc_type": doc_type, "doc_id": doc_id, "users": users}
            for (doc_type, doc_id), users in grouped.items()]


# --------------------------- Dashboard / Statistik ----------------------
@app.get("/api/stats")
def stats(db: Session = Depends(get_db)):
    return crud.dashboard_stats(db)


# --------------------------- Firmen-Einstellungen -----------------------
@app.get("/api/settings", response_model=schemas.SettingsOut)
def get_settings(db: Session = Depends(get_db)):
    s = crud.get_settings(db)
    out = schemas.SettingsOut.model_validate(s)
    out.has_logo = s.logo is not None
    return out


@app.put("/api/settings", response_model=schemas.SettingsOut)
def put_settings(data: schemas.SettingsIn,
                 admin: models.User = Depends(require_admin),
                 db: Session = Depends(get_db)):
    s = crud.save_settings(db, data)
    out = schemas.SettingsOut.model_validate(s)
    out.has_logo = s.logo is not None
    return out


@app.post("/api/settings/logo")
async def upload_logo(request: Request, file: UploadFile = File(...),
                      admin: models.User = Depends(require_admin),
                      db: Session = Depends(get_db)):
    content = await file.read()
    if len(content) > 2_000_000:
        raise HTTPException(400, "Logo ist zu groß (max. 2 MB)")
    if file.content_type not in ("image/png", "image/jpeg", "image/gif"):
        raise HTTPException(400, "Nur PNG, JPEG oder GIF erlaubt")
    crud.set_logo(db, content, file.content_type)
    return {"ok": True}


@app.get("/api/settings/logo")
def get_logo(db: Session = Depends(get_db)):
    s = crud.get_settings(db)
    if not s.logo:
        raise HTTPException(404, "Kein Logo hinterlegt")
    return Response(content=s.logo, media_type=s.logo_mime or "image/png")


# --------------------------- DSGVO-Audit-Log (nur Admin) ----------------
@app.get("/api/audit-log", response_model=list[schemas.AuditLogOut])
def get_audit_log(response: Response, limit: int = Query(200, ge=1),
                  offset: int = Offset,
                  admin: models.User = Depends(require_admin),
                  db: Session = Depends(get_db)):
    return _with_total(response, crud.list_audit_log(db, limit, offset))


# --------------------------- Backup-Wiederherstellung (nur Admin) -------
# Nutzt die automatischen täglichen pg_dump-Backups aus dem `backup`-Service
# in docker-compose.yml (Ordner ./backups, 14 Tage Aufbewahrung).
@app.get("/api/admin/backups", response_model=list[schemas.BackupFileOut])
def list_backups(admin: models.User = Depends(require_admin)):
    return backup.list_backups()


@app.get("/api/admin/backups/{filename}/download")
def download_backup(filename: str, request: Request,
                    admin: models.User = Depends(require_admin),
                    db: Session = Depends(get_db)):
    try:
        content = backup.read_backup(filename)
    except (ValueError, FileNotFoundError):
        raise HTTPException(404, "Backup nicht gefunden")
    crud.log_action(db, auth.current_user(request), "download", "backup", None, filename)
    return Response(
        content=content, media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/admin/backups/{filename}/restore")
def restore_backup(filename: str, request: Request,
                   admin: models.User = Depends(require_admin),
                   db: Session = Depends(get_db)):
    """ACHTUNG: überschreibt den aktuellen Datenbankinhalt mit dem Stand des
    gewählten Backups. Nur für Administratoren, mit Bestätigung im Frontend."""
    username = auth.current_user(request)
    crud.log_action(db, username, "restore_attempt", "backup", None, filename)
    try:
        backup.restore_backup(filename)
    except (ValueError, FileNotFoundError):
        raise HTTPException(404, "Backup nicht gefunden")
    except RuntimeError as err:
        raise HTTPException(500, f"Wiederherstellung fehlgeschlagen: {err}")
    return {"restored": filename}


@app.get("/health")
def health():
    return {"status": "ok"}


# --------------------------- Frontend -----------------------------------
@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/datenschutz")
def privacy_page():
    return FileResponse(STATIC_DIR / "datenschutz.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
