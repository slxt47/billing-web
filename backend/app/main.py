"""FastAPI-App: Rechnungs-Web-Applikation."""
import io
import zipfile
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException, Form, Request, UploadFile, File
from fastapi.responses import Response, FileResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session

from . import crud, models, schemas, pdf, auth, email_service, config
from .database import get_db, init_db, SessionLocal

app = FastAPI(title="Rechnungs-App")

STATIC_DIR = Path(__file__).parent / "static"
VALID_STATUS = {models.STATUS_OPEN, models.STATUS_PAID, models.STATUS_CANCELLED}


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
    return crud.create_invoice(db, data)


@app.get("/api/invoices/{invoice_id}", response_model=schemas.InvoiceOut)
def get_invoice(invoice_id: int, db: Session = Depends(get_db)):
    invoice = crud.get_invoice(db, invoice_id)
    if not invoice:
        raise HTTPException(404, "Rechnung nicht gefunden")
    return invoice


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
    return crud.add_payment(db, invoice, body.amount)


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
def list_customers(db: Session = Depends(get_db)):
    return crud.list_customers(db)


@app.post("/api/customers", response_model=schemas.CustomerOut, status_code=201)
def create_customer(data: schemas.CustomerIn, db: Session = Depends(get_db)):
    return crud.create_customer(db, data)


@app.delete("/api/customers/{customer_id}", status_code=204)
def delete_customer(customer_id: int, db: Session = Depends(get_db)):
    customer = crud.get_customer(db, customer_id)
    if not customer:
        raise HTTPException(404, "Kunde nicht gefunden")
    crud.delete_customer(db, customer)
    return Response(status_code=204)


# --------------------------- Artikel / Leistungen -----------------------
@app.get("/api/products", response_model=list[schemas.ProductOut])
def list_products(db: Session = Depends(get_db)):
    return crud.list_products(db)


@app.post("/api/products", response_model=schemas.ProductOut, status_code=201)
def create_product(data: schemas.ProductIn, db: Session = Depends(get_db)):
    return crud.create_product(db, data)


@app.delete("/api/products/{product_id}", status_code=204)
def delete_product(product_id: int, db: Session = Depends(get_db)):
    product = crud.get_product(db, product_id)
    if not product:
        raise HTTPException(404, "Artikel nicht gefunden")
    crud.delete_product(db, product)
    return Response(status_code=204)


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


@app.get("/health")
def health():
    return {"status": "ok"}


# --------------------------- Frontend -----------------------------------
@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
