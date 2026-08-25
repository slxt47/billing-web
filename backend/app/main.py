"""FastAPI-App: Rechnungs-Web-Applikation."""
import io
import zipfile
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException, Form, Request, UploadFile, File
from fastapi.responses import Response, FileResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session

from . import crud, models, schemas, pdf, auth, email_service, config, backup
from .database import get_db, init_db, SessionLocal

app = FastAPI(title="Rechnungs-App")

STATIC_DIR = Path(__file__).parent / "static"
VALID_STATUS = {models.STATUS_OPEN, models.STATUS_PAID, models.STATUS_CANCELLED}
VALID_QUOTE_STATUS = {models.QUOTE_OPEN, models.QUOTE_ACCEPTED, models.QUOTE_DECLINED}
VALID_DN_STATUS = {models.DN_OPEN, models.DN_CANCELLED}


@app.on_event("startup")
def on_startup():
    init_db()
    db = SessionLocal()
    try:
        crud.seed_users(db)
    finally:
        db.close()


def require_admin(request: Request, db: Session = Depends(get_db)) -> models.User:
    """Dependency: stellt sicher, dass der angemeldete Benutzer ein Admin ist."""
    username = auth.current_user(request)
    user = crud.get_user(db, username) if username else None
    if not user or not user.is_admin:
        raise HTTPException(403, "Nur für Administratoren")
    return user


# --------------------------- Auth-Schutz --------------------------------
# Reihenfolge wichtig: SessionMiddleware wird zuletzt hinzugefügt und läuft
# damit zuerst, sodass request.session in der Auth-Prüfung verfügbar ist.
@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if auth.is_public(path) or auth.current_user(request):
        return await call_next(request)
    if path.startswith("/api"):
        return JSONResponse({"detail": "Nicht angemeldet"}, status_code=401)
    return RedirectResponse("/login")


app.add_middleware(SessionMiddleware, secret_key=config.SESSION_SECRET)


# --------------------------- Login / Logout -----------------------------
@app.get("/login")
def login_page():
    return FileResponse(STATIC_DIR / "login.html")


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...),
          db: Session = Depends(get_db)):
    key = (request.client.host if request.client else "?") + "|" + username
    wait = auth.is_locked(key)
    if wait:
        return RedirectResponse(f"/login?locked={wait}", status_code=303)
    if crud.authenticate(db, username, password):
        auth.reset_failures(key)
        request.session["user"] = username
        return RedirectResponse("/", status_code=303)
    auth.register_failure(key)
    return RedirectResponse("/login?error=1", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/api/me")
def me(request: Request, db: Session = Depends(get_db)):
    user = crud.get_user(db, auth.current_user(request))
    if not user:
        return {"user": None, "is_admin": False}
    return {"user": user.username, "is_admin": user.is_admin}


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
@app.get("/api/invoices", response_model=list[schemas.InvoiceOut])
def list_invoices(search: str | None = None, db: Session = Depends(get_db)):
    return crud.list_invoices(db, search)


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
    return crud.set_status(db, invoice, body.status)


@app.delete("/api/invoices/{invoice_id}", status_code=204)
def delete_invoice(invoice_id: int, db: Session = Depends(get_db)):
    invoice = crud.get_invoice(db, invoice_id)
    if not invoice:
        raise HTTPException(404, "Rechnung nicht gefunden")
    crud.delete_invoice(db, invoice)
    return Response(status_code=204)


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
    if invoice.status == models.STATUS_PAID and not was_paid:
        customer = db.query(models.Customer).filter(
            models.Customer.name == invoice.customer_name).first()
        if customer and customer.email:
            try:
                email_service.send_payment_confirmation(invoice, customer.email, crud.get_settings(db))
            except OSError:
                pass
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
def list_customers(active_only: bool = False, db: Session = Depends(get_db)):
    return crud.list_customers(db, active_only)


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
def list_products(active_only: bool = False, db: Session = Depends(get_db)):
    return crud.list_products(db, active_only)


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
def list_quotes(db: Session = Depends(get_db)):
    return crud.list_quotes(db)


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
def list_delivery_notes(db: Session = Depends(get_db)):
    return crud.list_delivery_notes(db)


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


@app.post("/api/invoices/{invoice_id}/convert-to-delivery-note",
          response_model=schemas.DeliveryNoteOut, status_code=201)
def convert_invoice(invoice_id: int, db: Session = Depends(get_db)):
    invoice = crud.get_invoice(db, invoice_id)
    if not invoice:
        raise HTTPException(404, "Rechnung nicht gefunden")
    return crud.convert_invoice_to_delivery_note(db, invoice)


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
def get_audit_log(admin: models.User = Depends(require_admin), db: Session = Depends(get_db)):
    return crud.list_audit_log(db)


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
