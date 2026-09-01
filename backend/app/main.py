"""FastAPI-App: Rechnungs-Web-Applikation."""
import csv
import io
import json
import zipfile
from datetime import date
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
               security, logging_setup, reports, monitoring, cache)
from .database import get_db, init_db, SessionLocal

app = FastAPI(title="Rechnungs-App")

STATIC_DIR = Path(__file__).parent / "static"
VALID_STATUS = {models.STATUS_OPEN, models.STATUS_PAID, models.STATUS_CANCELLED}
VALID_QUOTE_STATUS = {models.QUOTE_OPEN, models.QUOTE_ACCEPTED, models.QUOTE_DECLINED}
VALID_DN_STATUS = {models.DN_OPEN, models.DN_DONE, models.DN_CANCELLED}
VALID_CN_STATUS = {models.CN_OPEN, models.CN_SETTLED, models.CN_CANCELLED}


# Schlüssel in app_settings, unter dem die in der Oberfläche gewählte
# Log-Stufe liegt (siehe /api/admin/log-level).
LOG_LEVEL_KEY = "log_level"


@app.on_event("startup")
def on_startup():
    logging_setup.setup_logging()
    init_db()
    db = SessionLocal()
    try:
        crud.seed_users(db)
        crud.seed_pdf_templates(db)
        # In der Oberfläche gewählte Stufe schlägt die Umgebungsvariable –
        # sonst wäre die Auswahl nach jedem Neustart wieder weg. Ein
        # unbrauchbarer Wert in der Datenbank darf den Start nicht verhindern.
        stored = crud.get_app_setting(db, LOG_LEVEL_KEY)
        if stored:
            try:
                logging_setup.set_level(stored)
            except ValueError:
                logging_setup.log.warning("ignoring stored log level",
                                          extra={"fields": {"value": stored}})
    finally:
        db.close()
    logging_setup.log.info("startup complete", extra={"fields": {
        "csrf": config.CSRF_ENABLED,
        "rate_limit": config.RATE_LIMIT_REQUESTS,
        "https_only_cookies": config.SESSION_HTTPS_ONLY,
        "log_level": logging_setup.current_level(),
        "log_dir": config.LOG_DIR or "-",
    }})


def pdf_template(db: Session, template_id: int | None):
    """Vorlage für einen PDF-Download: ausdrücklich gewählte, sonst die
    Vorgabe, sonst None (dann gelten die Werte in pdf.DEFAULTS)."""
    if template_id is None:
        return crud.default_pdf_template(db)
    tpl = crud.get_pdf_template(db, template_id)
    if not tpl:
        raise HTTPException(404, "PDF-Vorlage nicht gefunden")
    return tpl


def require_admin(request: Request, db: Session = Depends(get_db)) -> models.User:
    """Dependency: stellt sicher, dass der angemeldete Benutzer ein Admin ist."""
    username = auth.current_user(request)
    user = crud.get_user(db, username) if username else None
    if not user or not user.is_admin:
        raise HTTPException(403, "Nur für Administratoren")
    return user


# --------------------------- Response-Cache ------------------------------
# Innerste Middleware überhaupt: läuft erst, nachdem die Login-Prüfung durch
# ist, direkt vor der Route (siehe cache.py). Ein Cache-Treffer soll nicht an
# der Anmeldung vorbeigehen, deshalb die Registrierung noch vor require_login.
app.middleware("http")(cache.response_cache_middleware)


# --------------------------- Auth-Schutz --------------------------------
# Läuft erst, wenn Session und CSRF-Prüfung durch sind, sodass
# request.session hier bereits verfügbar ist.
@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if auth.is_public(path) or auth.current_user(request):
        return await call_next(request)
    if path.startswith("/api"):
        return JSONResponse({"detail": "Nicht angemeldet"}, status_code=401)
    return RedirectResponse("/login")


# Von innen nach außen: Response-Cache -> Login-Prüfung -> CSRF -> Session ->
# Rate-Limit -> Sicherheits-Header -> Request-ID/Logging. Starlette führt die
# zuletzt registrierte Middleware zuerst aus, deshalb ist die Reihenfolge hier
# genau umgekehrt zur Durchlaufreihenfolge.
app.middleware("http")(security.csrf_middleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=config.SESSION_SECRET,
    same_site="strict",
    https_only=config.SESSION_HTTPS_ONLY,
    max_age=config.SESSION_MAX_AGE,
)
app.middleware("http")(monitoring.metrics_middleware)
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
    monitoring.record_login_failure(username)
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
def download_pdf(invoice_id: int, template: int | None = None,
                 db: Session = Depends(get_db)):
    invoice = crud.get_invoice(db, invoice_id)
    if not invoice:
        raise HTTPException(404, "Rechnung nicht gefunden")
    data = pdf.invoice_pdf(invoice, crud.get_settings(db), pdf_template(db, template))
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

# Artikelimport: Bezeichnung + Standardpreis, sonst nichts – dieselbe Idee wie
# beim Kundenimport, nur mit deutlich weniger Spalten.
PRODUCT_CSV_COLUMNS = {
    "name": ("name", "bezeichnung", "artikel", "artikelname", "leistung",
             "product", "product_name"),
    "unit_price": ("preis", "einzelpreis", "standardpreis", "unit_price",
                   "price", "netto"),
}
PRODUCT_FIELD_BY_ALIAS = {alias: field for field, aliases in PRODUCT_CSV_COLUMNS.items()
                          for alias in aliases}
PRODUCT_NUMBER_FIELDS = ("unit_price",)

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


def _field_for(key: str, alias_map: dict[str, str]) -> str | None:
    """Spaltenüberschrift bzw. JSON-Schlüssel auf ein Feld des Ziel-Schemas
    abbilden (Kunde oder Artikel, je nach übergebener Aliaskarte)."""
    return alias_map.get(str(key or "").strip().lstrip("\ufeff").lower())


def _map_row(raw: dict, alias_map: dict[str, str],
            number_fields: tuple[str, ...]) -> dict:
    """Rohdatensatz (CSV-Zeile oder JSON-Objekt) auf Schema-Felder eindampfen.
    Unbekannte Schlüssel (id, active, invoices …) fallen weg."""
    values: dict[str, str] = {}
    for key, value in raw.items():
        field = _field_for(key, alias_map)
        if not field or field in values or value is None:
            continue
        values[field] = str(value).strip()
    for field in number_fields:
        if field in values:
            # "12,5" (deutsche Schreibweise) -> "12.5"; leer = Vorgabewert
            values[field] = values[field].replace(",", ".")
            if not values[field]:
                values.pop(field)
    return values


def _rows_from_csv(text_content: str, alias_map: dict[str, str],
                   required_label: str) -> list[dict]:
    first_line = text_content.splitlines()[0] if text_content.strip() else ""
    delimiter = ";" if first_line.count(";") > first_line.count(",") else ","
    if "\t" in first_line and first_line.count("\t") > first_line.count(delimiter):
        delimiter = "\t"
    reader = csv.DictReader(io.StringIO(text_content), delimiter=delimiter)
    if not any(_field_for(h, alias_map) == "name" for h in reader.fieldnames or []):
        raise HTTPException(400, f"Es fehlt eine Spalte mit {required_label} "
                                 "in der Kopfzeile")
    return list(reader)


def _rows_from_json(text_content: str, alias_map: dict[str, str], required_label: str,
                    list_key: str, single_key: str) -> list[dict]:
    """Datensätze aus einer JSON-Datei ziehen. Akzeptiert den Export dieser App
    (bei Kunden: {"customer": {...}, "invoices": [...]}), eine Liste von
    Objekten, ein einzelnes Objekt und {list_key: [...]}."""
    try:
        data = json.loads(text_content)
    except json.JSONDecodeError as err:
        raise HTTPException(400, f"Die JSON-Datei ist fehlerhaft: {err.msg} "
                                 f"(Zeile {err.lineno})")
    if isinstance(data, dict):
        if isinstance(data.get(single_key), dict):
            data = [data[single_key]]
        elif isinstance(data.get(list_key), list):
            data = data[list_key]
        else:
            data = [data]
    if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
        raise HTTPException(400, f"Unerwarteter Aufbau: erwartet wird ein "
                                 f"{single_key}-Objekt oder eine Liste davon")
    if not any(_field_for(key, alias_map) == "name" for row in data for key in row):
        raise HTTPException(400, f"Es fehlt ein Feld mit {required_label}")
    return data


def _looks_like_json(filename: str, text_content: str) -> bool:
    if (filename or "").lower().endswith(".json"):
        return True
    return text_content.lstrip()[:1] in ("{", "[")


async def _read_import_rows(file: UploadFile, alias_map: dict[str, str],
                            required_label: str, list_key: str,
                            single_key: str) -> list[dict]:
    """Gemeinsamer Einstieg für Kunden- und Artikelimport: Datei lesen,
    dekodieren, je nach Inhalt als CSV oder JSON in Rohdatensätze zerlegen."""
    raw = await file.read()
    if not raw.strip():
        raise HTTPException(400, "Die Datei ist leer")
    if len(raw) > IMPORT_MAX_BYTES:
        raise HTTPException(400, "Datei ist zu groß (max. 1 MB)")

    text_content = _import_text(raw)
    raw_rows = (_rows_from_json(text_content, alias_map, required_label, list_key, single_key)
                if _looks_like_json(file.filename or "", text_content)
                else _rows_from_csv(text_content, alias_map, required_label))
    if len(raw_rows) > IMPORT_MAX_ROWS:
        raise HTTPException(400, f"Zu viele Datensätze (max. {IMPORT_MAX_ROWS})")
    return raw_rows


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
    raw_rows = await _read_import_rows(
        file, FIELD_BY_ALIAS, 'dem Kundennamen (z. B. "name")', "customers", "customer")

    rows: list[schemas.CustomerIn] = []
    errors: list[str] = []
    skipped = 0
    for number, raw_row in enumerate(raw_rows, start=1):
        values = _map_row(raw_row, FIELD_BY_ALIAS, NUMBER_FIELDS)
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


def _write_csv(header: list[str], rows: list[list]) -> str:
    """CSV mit Semikolon-Trennung und korrektem Quoting (Python-csv-Modul statt
    manuellem Zusammenkleben, damit ein Semikolon oder Anführungszeichen in
    einem Kundennamen die Spalten nicht verschiebt). BOM voran, sonst zeigt
    Excel die Umlaute falsch an."""
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    writer.writerow(header)
    writer.writerows(rows)
    return "\ufeff" + buf.getvalue()


@app.get("/api/customers/export.csv")
def export_customers_csv(db: Session = Depends(get_db)):
    """Massenexport aller Kunden als CSV – Gegenstück zum Import, dieselben
    Spalten, damit sich die Datei ohne Nacharbeit wieder einlesen lässt. Anders
    als /api/customers/{id}/export (DSGVO Art. 15, nur Admin) ist das hier ein
    formloser Arbeitsexport für alle angemeldeten Benutzer, wie die Liste
    selbst auch."""
    customers = db.query(models.Customer).order_by(models.Customer.name.asc()).all()
    text_content = _write_csv(
        ["Name", "E-Mail", "Ansprechpartner", "Anschrift", "Zahlungsfrist",
         "Skonto", "Skonto_Tage", "Status"],
        [[c.name, c.email, c.contact_person, c.address, c.payment_term_days,
          c.skonto_percent, c.skonto_days, "aktiv" if c.active else "inaktiv"]
         for c in customers],
    )
    return _csv_response(text_content, "kunden-export.csv")


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


@app.post("/api/products/import", response_model=schemas.ProductImportResult)
async def import_products(request: Request, file: UploadFile = File(...),
                          db: Session = Depends(get_db)):
    """Artikelstamm aus einer CSV- oder JSON-Datei übernehmen – das
    Gegenstück zum Kundenimport, nur mit den zwei Feldern, die ein Artikel
    hat. Pflichtangabe ist die Bezeichnung; ein bereits vorhandener Artikel
    (gleiche Bezeichnung) wird im Preis aktualisiert statt doppelt angelegt."""
    raw_rows = await _read_import_rows(
        file, PRODUCT_FIELD_BY_ALIAS, 'der Artikelbezeichnung (z. B. "name")',
        "products", "product")

    rows: list[schemas.ProductIn] = []
    errors: list[str] = []
    skipped = 0
    for number, raw_row in enumerate(raw_rows, start=1):
        values = _map_row(raw_row, PRODUCT_FIELD_BY_ALIAS, PRODUCT_NUMBER_FIELDS)
        if not values.get("name"):
            skipped += 1
            continue
        try:
            rows.append(schemas.ProductIn(**values))
        except ValidationError:
            skipped += 1
            if len(errors) < IMPORT_MAX_ERRORS:
                errors.append(f"Datensatz {number} ({values['name']}): ungültige Werte")

    created, updated = crud.import_products(db, rows)
    crud.log_action(db, auth.current_user(request) or "", "import", "product", None,
                    f"Import: {created} neu, {updated} aktualisiert, "
                    f"{skipped} übersprungen")
    return schemas.ProductImportResult(created=created, updated=updated,
                                       skipped=skipped, errors=errors)


@app.get("/api/products/export.csv")
def export_products_csv(db: Session = Depends(get_db)):
    """Massenexport aller Artikel als CSV – Gegenstück zum Import."""
    products = db.query(models.Product).order_by(models.Product.name.asc()).all()
    text_content = _write_csv(
        ["Name", "Standardpreis", "Status"],
        [[p.name, p.unit_price, "aktiv" if p.active else "inaktiv"] for p in products],
    )
    return _csv_response(text_content, "artikel-export.csv")


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


@app.get("/api/quotes/{quote_id}/lock", response_model=schemas.LockOut)
def get_quote_lock(quote_id: int, request: Request, db: Session = Depends(get_db)):
    quote = crud.get_quote(db, quote_id)
    if not quote:
        raise HTTPException(404, "Angebot nicht gefunden")
    return crud.lock_status(quote, auth.current_user(request))


@app.post("/api/quotes/{quote_id}/lock", response_model=schemas.LockOut)
def acquire_quote_lock(quote_id: int, request: Request, db: Session = Depends(get_db)):
    quote = crud.get_quote(db, quote_id)
    if not quote:
        raise HTTPException(404, "Angebot nicht gefunden")
    username = auth.current_user(request)
    crud.acquire_lock(db, quote, username)
    return crud.lock_status(quote, username)


@app.delete("/api/quotes/{quote_id}/lock", response_model=schemas.LockOut)
def release_quote_lock(quote_id: int, request: Request, db: Session = Depends(get_db)):
    quote = crud.get_quote(db, quote_id)
    if not quote:
        raise HTTPException(404, "Angebot nicht gefunden")
    username = auth.current_user(request)
    crud.release_lock(db, quote, username)
    return crud.lock_status(quote, username)


@app.put("/api/quotes/{quote_id}", response_model=schemas.QuoteOut)
def edit_quote(quote_id: int, data: schemas.QuoteIn, request: Request,
               db: Session = Depends(get_db)):
    quote = crud.get_quote(db, quote_id)
    if not quote:
        raise HTTPException(404, "Angebot nicht gefunden")
    if quote.status == models.QUOTE_CONVERTED:
        raise HTTPException(400, "Umgewandeltes Angebot kann nicht mehr bearbeitet werden")
    username = auth.current_user(request)
    if not crud.lock_status(quote, username)["editable"]:
        raise HTTPException(409, f"Wird gerade von {quote.locked_by} bearbeitet")
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
def download_quote_pdf(quote_id: int, template: int | None = None,
                       db: Session = Depends(get_db)):
    quote = crud.get_quote(db, quote_id)
    if not quote:
        raise HTTPException(404, "Angebot nicht gefunden")
    data = pdf.quote_pdf(quote, crud.get_settings(db), pdf_template(db, template))
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
    if quote.status == models.QUOTE_CONVERTED or quote.converted_invoice_id:
        # Kein zweites Mal: sonst stünden zwei Rechnungen über dieselbe Leistung.
        target = quote.converted_invoice_number
        raise HTTPException(400, "Angebot wurde bereits umgewandelt"
                                 + (f" (Rechnung {target})" if target else ""))
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


@app.get("/api/delivery-notes/{dn_id}/lock", response_model=schemas.LockOut)
def get_delivery_note_lock(dn_id: int, request: Request, db: Session = Depends(get_db)):
    dn = crud.get_delivery_note(db, dn_id)
    if not dn:
        raise HTTPException(404, "Lieferschein nicht gefunden")
    return crud.lock_status(dn, auth.current_user(request))


@app.post("/api/delivery-notes/{dn_id}/lock", response_model=schemas.LockOut)
def acquire_delivery_note_lock(dn_id: int, request: Request, db: Session = Depends(get_db)):
    dn = crud.get_delivery_note(db, dn_id)
    if not dn:
        raise HTTPException(404, "Lieferschein nicht gefunden")
    username = auth.current_user(request)
    crud.acquire_lock(db, dn, username)
    return crud.lock_status(dn, username)


@app.delete("/api/delivery-notes/{dn_id}/lock", response_model=schemas.LockOut)
def release_delivery_note_lock(dn_id: int, request: Request, db: Session = Depends(get_db)):
    dn = crud.get_delivery_note(db, dn_id)
    if not dn:
        raise HTTPException(404, "Lieferschein nicht gefunden")
    username = auth.current_user(request)
    crud.release_lock(db, dn, username)
    return crud.lock_status(dn, username)


@app.put("/api/delivery-notes/{dn_id}", response_model=schemas.DeliveryNoteOut)
def edit_delivery_note(dn_id: int, data: schemas.DeliveryNoteIn, request: Request,
                       db: Session = Depends(get_db)):
    dn = crud.get_delivery_note(db, dn_id)
    if not dn:
        raise HTTPException(404, "Lieferschein nicht gefunden")
    username = auth.current_user(request)
    if not crud.lock_status(dn, username)["editable"]:
        raise HTTPException(409, f"Wird gerade von {dn.locked_by} bearbeitet")
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
def download_delivery_note_pdf(dn_id: int, template: int | None = None,
                               db: Session = Depends(get_db)):
    dn = crud.get_delivery_note(db, dn_id)
    if not dn:
        raise HTTPException(404, "Lieferschein nicht gefunden")
    data = pdf.delivery_note_pdf(dn, crud.get_settings(db), pdf_template(db, template))
    # Der Ausdruck ist der Abschluss: ein offener Lieferschein gilt danach als
    # abgeschlossen. Ein stornierter bleibt storniert, ein bereits
    # abgeschlossener ändert sich nicht.
    if dn.status == models.DN_OPEN:
        crud.set_delivery_note_status(db, dn, models.DN_DONE)
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
    if dn.converted_quote_number:
        raise HTTPException(400, "Aus diesem Lieferschein wurde bereits das Angebot "
                                 f"{dn.converted_quote_number} erstellt")
    return crud.convert_delivery_note_to_quote(db, dn)


@app.post("/api/invoices/{invoice_id}/convert-to-delivery-note",
          response_model=schemas.DeliveryNoteOut, status_code=201)
def convert_invoice(invoice_id: int, db: Session = Depends(get_db)):
    invoice = crud.get_invoice(db, invoice_id)
    if not invoice:
        raise HTTPException(404, "Rechnung nicht gefunden")
    if invoice.delivery_note_number:
        raise HTTPException(400, "Zu dieser Rechnung gibt es bereits den Lieferschein "
                                 f"{invoice.delivery_note_number}")
    return crud.convert_invoice_to_delivery_note(db, invoice)


# --------------------------- Gutschriften --------------------------------
@app.get("/api/credit-notes", response_model=list[schemas.CreditNoteOut])
def list_credit_notes(response: Response, search: str | None = None,
                      limit: int | None = Limit, offset: int = Offset,
                      db: Session = Depends(get_db)):
    return _with_total(response, crud.list_credit_notes(db, search, limit, offset))


@app.post("/api/credit-notes", response_model=schemas.CreditNoteOut, status_code=201)
def create_credit_note(data: schemas.CreditNoteIn, db: Session = Depends(get_db)):
    if data.invoice_id is not None and not crud.get_invoice(db, data.invoice_id):
        raise HTTPException(404, "Rechnung nicht gefunden")
    return crud.create_credit_note(db, data)


@app.get("/api/credit-notes/{cn_id}", response_model=schemas.CreditNoteOut)
def get_credit_note(cn_id: int, db: Session = Depends(get_db)):
    cn = crud.get_credit_note(db, cn_id)
    if not cn:
        raise HTTPException(404, "Gutschrift nicht gefunden")
    return cn


@app.put("/api/credit-notes/{cn_id}", response_model=schemas.CreditNoteOut)
def edit_credit_note(cn_id: int, data: schemas.CreditNoteIn,
                     db: Session = Depends(get_db)):
    cn = crud.get_credit_note(db, cn_id)
    if not cn:
        raise HTTPException(404, "Gutschrift nicht gefunden")
    if cn.status == models.CN_CANCELLED:
        raise HTTPException(400, "Eine stornierte Gutschrift lässt sich nicht bearbeiten")
    if data.invoice_id is not None and not crud.get_invoice(db, data.invoice_id):
        raise HTTPException(404, "Rechnung nicht gefunden")
    return crud.update_credit_note(db, cn, data)


@app.patch("/api/credit-notes/{cn_id}/status", response_model=schemas.CreditNoteOut)
def update_credit_note_status(cn_id: int, body: schemas.CreditNoteStatusUpdate,
                              db: Session = Depends(get_db)):
    cn = crud.get_credit_note(db, cn_id)
    if not cn:
        raise HTTPException(404, "Gutschrift nicht gefunden")
    if body.status not in VALID_CN_STATUS:
        raise HTTPException(400, f"Ungültiger Status: {body.status}")
    return crud.set_credit_note_status(db, cn, body.status)


@app.delete("/api/credit-notes/{cn_id}", status_code=204)
def delete_credit_note(cn_id: int, db: Session = Depends(get_db)):
    cn = crud.get_credit_note(db, cn_id)
    if not cn:
        raise HTTPException(404, "Gutschrift nicht gefunden")
    crud.delete_credit_note(db, cn)
    return Response(status_code=204)


@app.get("/api/credit-notes/{cn_id}/pdf")
def download_credit_note_pdf(cn_id: int, template: int | None = None,
                             db: Session = Depends(get_db)):
    cn = crud.get_credit_note(db, cn_id)
    if not cn:
        raise HTTPException(404, "Gutschrift nicht gefunden")
    data = pdf.credit_note_pdf(cn, crud.get_settings(db), pdf_template(db, template))
    return Response(
        content=data, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{cn.number}.pdf"'},
    )


@app.post("/api/credit-notes/{cn_id}/email")
def email_credit_note(cn_id: int, body: schemas.EmailRequest,
                      db: Session = Depends(get_db)):
    cn = crud.get_credit_note(db, cn_id)
    if not cn:
        raise HTTPException(404, "Gutschrift nicht gefunden")
    to = _valid_email(body.to)
    try:
        email_service.send_credit_note_email(cn, to, crud.get_settings(db))
    except OSError as err:
        raise HTTPException(502, f"E-Mail konnte nicht gesendet werden: {err}")
    return {"sent": True, "to": to, "number": cn.number}


@app.post("/api/invoices/{invoice_id}/credit-note",
          response_model=schemas.CreditNoteOut, status_code=201)
def credit_invoice(invoice_id: int, body: schemas.CreditNoteFromInvoice,
                   db: Session = Depends(get_db)):
    """Gutschrift zu einer Rechnung – ohne Positionen im Body eine
    Vollgutschrift, mit Positionen eine Teilgutschrift. Mehrere Gutschriften
    zu derselben Rechnung sind erlaubt, zusammen aber höchstens der noch
    offene Betrag."""
    invoice = crud.get_invoice(db, invoice_id)
    if not invoice:
        raise HTTPException(404, "Rechnung nicht gefunden")
    if invoice.status == models.STATUS_CANCELLED:
        raise HTTPException(400, "Eine stornierte Rechnung lässt sich nicht gutschreiben")
    cn = crud.credit_note_from_invoice(db, invoice, body)
    db.refresh(invoice)
    if invoice.remaining < -0.005:  # Rundungsluft von einem halben Cent
        crud.delete_credit_note(db, cn)
        raise HTTPException(400, "Die Gutschrift übersteigt den offenen Betrag "
                                 f"der Rechnung ({invoice.number})")
    return cn


# --------------------------- Monitoring ----------------------------------
@app.get("/api/admin/metrics")
def metrics(admin: models.User = Depends(require_admin),
            db: Session = Depends(get_db)):
    """Kennzahlen des laufenden Prozesses plus Bestandszahlen. Nur für
    Administratoren – ein eigener Monitoring-Benutzer existiert nicht."""
    return monitoring.snapshot(db)


@app.get("/api/admin/metrics.prom")
def metrics_prometheus(admin: models.User = Depends(require_admin),
                       db: Session = Depends(get_db)):
    """Dieselben Zahlen im Prometheus-Textformat zum Abholen."""
    return Response(content=monitoring.prometheus(monitoring.snapshot(db)),
                    media_type="text/plain; version=0.0.4; charset=utf-8")


@app.get("/api/admin/log-level")
def get_log_level(admin: models.User = Depends(require_admin)):
    """Stufe, mit der die App gerade läuft, plus die wählbaren Stufen."""
    return {"level": logging_setup.current_level(),
            "levels": list(logging_setup.LEVELS),
            "boot_level": config.LOG_LEVEL}


@app.post("/api/admin/log-level")
def set_log_level(body: schemas.LogLevelIn,
                  admin: models.User = Depends(require_admin),
                  db: Session = Depends(get_db)):
    """Log-Stufe umschalten: wirkt sofort und wird gespeichert.

    Gespeichert wird in app_settings, damit die Auswahl einen Neustart
    übersteht – beim Start liest on_startup() sie wieder ein.
    """
    try:
        level = logging_setup.set_level(body.level)
    except ValueError as err:
        raise HTTPException(400, str(err))
    crud.set_app_setting(db, LOG_LEVEL_KEY, level)
    crud.log_action(db, admin.username, "log_level", "app", detail=level)
    logging_setup.log.warning("log level changed", extra={"fields": {
        "level": level, "user": admin.username}})
    return {"level": level}


@app.post("/api/admin/metrics/test-alert")
def send_test_alert(admin: models.User = Depends(require_admin),
                    db: Session = Depends(get_db)):
    """Probealarm an die Firmen-E-Mail – damit sich prüfen lässt, ob die
    Benachrichtigung ankommt, bevor es ernst wird."""
    settings = crud.get_settings(db)
    to = (settings.email or "").strip() if settings else ""
    if not to:
        raise HTTPException(400, "In den Firmendaten ist keine E-Mail-Adresse "
                                 "hinterlegt – dorthin gehen die Alarme.")
    try:
        email_service.send_alert_email(
            to, "test", "Probealarm: Die Benachrichtigung funktioniert.", settings)
    except OSError as err:
        raise HTTPException(502, f"E-Mail konnte nicht gesendet werden: {err}")
    return {"sent": True, "to": to}


# --------------------------- Auswertungen --------------------------------
# Zeitraum als ?from=JJJJ-MM-TT&to=JJJJ-MM-TT. "from" ist in Python ein
# Schlüsselwort, deshalb der Alias.
FromDate = Query(None, alias="from")
ToDate = Query(None, alias="to")


def _period(start: date | None, end: date | None) -> tuple[date, date]:
    """Zeitraum auflösen. Ohne Angabe: das laufende Jahr."""
    today = date.today()
    start = start or date(today.year, 1, 1)
    end = end or date(today.year, 12, 31)
    if end < start:
        raise HTTPException(400, "Das Ende des Zeitraums liegt vor dem Anfang")
    return start, end


def _csv_response(text: str, filename: str) -> Response:
    return Response(
        content=text.encode("utf-8"), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/reports/vat")
def vat_report(start: date | None = FromDate, end: date | None = ToDate,
               db: Session = Depends(get_db)):
    """Umsatzsteuer je Steuersatz im Zeitraum (Soll-Versteuerung), Gutschriften
    abgezogen. Vorsteuer fehlt: Ausgaben erfasst die App nicht."""
    return reports.vat_report(db, *_period(start, end))


@app.get("/api/reports/vat.csv")
def vat_report_csv(start: date | None = FromDate, end: date | None = ToDate,
                   db: Session = Depends(get_db)):
    a, b = _period(start, end)
    return _csv_response(reports.vat_report_csv(reports.vat_report(db, a, b)),
                         f"UStVA_{a.isoformat()}_{b.isoformat()}.csv")


@app.get("/api/reports/revenue")
def revenue_report(start: date | None = FromDate, end: date | None = ToDate,
                   db: Session = Depends(get_db)):
    """Erlöse je Monat und Kunde im Zeitraum, Gutschriften abgezogen."""
    return reports.revenue_report(db, *_period(start, end))


@app.get("/api/reports/revenue.csv")
def revenue_report_csv(start: date | None = FromDate, end: date | None = ToDate,
                       db: Session = Depends(get_db)):
    a, b = _period(start, end)
    return _csv_response(reports.revenue_report_csv(reports.revenue_report(db, a, b)),
                         f"Erloese_{a.isoformat()}_{b.isoformat()}.csv")


def _custom_report_args(doc_type: str, group_by: str) -> None:
    if doc_type not in reports.DOC_TYPES:
        raise HTTPException(400, f"Unbekannte Belegart: {doc_type}")
    if group_by not in reports.GROUP_BY_OPTIONS:
        raise HTTPException(400, f"Unbekannte Gruppierung: {group_by}")


@app.get("/api/reports/custom")
def custom_report(doc_type: str, start: date | None = FromDate, end: date | None = ToDate,
                  group_by: str = "none", status: str | None = None,
                  db: Session = Depends(get_db)):
    """Freier Report-Builder: eine Belegart (invoice/quote/delivery_note/
    credit_note), ein Zeitraum, ein optionaler Statusfilter, gruppiert nach
    nichts/Kunde/Monat/Status."""
    _custom_report_args(doc_type, group_by)
    return reports.custom_report(db, doc_type, *_period(start, end), group_by, status)


@app.get("/api/reports/custom.csv")
def custom_report_csv(doc_type: str, start: date | None = FromDate, end: date | None = ToDate,
                      group_by: str = "none", status: str | None = None,
                      db: Session = Depends(get_db)):
    _custom_report_args(doc_type, group_by)
    a, b = _period(start, end)
    report = reports.custom_report(db, doc_type, a, b, group_by, status)
    return _csv_response(reports.custom_report_csv(report),
                         f"Report_{doc_type}_{a.isoformat()}_{b.isoformat()}.csv")


# --------------------------- PDF-Vorlagen --------------------------------
@app.get("/api/pdf-templates", response_model=list[schemas.PdfTemplateOut])
def list_pdf_templates(db: Session = Depends(get_db)):
    """Lesen darf jeder angemeldete Benutzer: die Vorlagen stehen beim
    Download zur Auswahl. Ändern dürfen nur Administratoren."""
    return crud.list_pdf_templates(db)


@app.post("/api/pdf-templates", response_model=schemas.PdfTemplateOut, status_code=201)
def create_pdf_template(data: schemas.PdfTemplateIn,
                        admin: models.User = Depends(require_admin),
                        db: Session = Depends(get_db)):
    if data.font_family not in pdf.FONT_FAMILIES:
        raise HTTPException(400, f"Unbekannte Schrift: {data.font_family}")
    if data.layout not in pdf.LAYOUTS:
        raise HTTPException(400, f"Unbekanntes Layout: {data.layout}")
    if crud.get_pdf_template_by_name(db, data.name):
        raise HTTPException(400, f"Es gibt schon eine Vorlage namens {data.name}")
    return crud.create_pdf_template(db, data)


@app.put("/api/pdf-templates/{template_id}", response_model=schemas.PdfTemplateOut)
def edit_pdf_template(template_id: int, data: schemas.PdfTemplateIn,
                      admin: models.User = Depends(require_admin),
                      db: Session = Depends(get_db)):
    tpl = crud.get_pdf_template(db, template_id)
    if not tpl:
        raise HTTPException(404, "PDF-Vorlage nicht gefunden")
    if data.font_family not in pdf.FONT_FAMILIES:
        raise HTTPException(400, f"Unbekannte Schrift: {data.font_family}")
    if data.layout not in pdf.LAYOUTS:
        raise HTTPException(400, f"Unbekanntes Layout: {data.layout}")
    other = crud.get_pdf_template_by_name(db, data.name)
    if other and other.id != tpl.id:
        raise HTTPException(400, f"Es gibt schon eine Vorlage namens {data.name}")
    return crud.update_pdf_template(db, tpl, data)


@app.post("/api/pdf-templates/{template_id}/default",
          response_model=schemas.PdfTemplateOut)
def set_default_pdf_template(template_id: int,
                             admin: models.User = Depends(require_admin),
                             db: Session = Depends(get_db)):
    tpl = crud.get_pdf_template(db, template_id)
    if not tpl:
        raise HTTPException(404, "PDF-Vorlage nicht gefunden")
    return crud.set_default_pdf_template(db, tpl)


@app.delete("/api/pdf-templates/{template_id}", status_code=204)
def delete_pdf_template(template_id: int,
                        admin: models.User = Depends(require_admin),
                        db: Session = Depends(get_db)):
    tpl = crud.get_pdf_template(db, template_id)
    if not tpl:
        raise HTTPException(404, "PDF-Vorlage nicht gefunden")
    crud.delete_pdf_template(db, tpl)
    return Response(status_code=204)


@app.get("/api/pdf-templates/{template_id}/preview")
def preview_pdf_template(template_id: int, db: Session = Depends(get_db)):
    """Musterrechnung mit dieser Vorlage – ohne echte Daten anzufassen."""
    tpl = crud.get_pdf_template(db, template_id)
    if not tpl:
        raise HTTPException(404, "PDF-Vorlage nicht gefunden")
    data = pdf.preview_pdf(crud.get_settings(db), tpl)
    return Response(
        content=data, media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="vorschau.pdf"'},
    )


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
