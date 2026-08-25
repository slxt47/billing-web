================================================================================
RECHNUNGS-APP - TECHNICAL DOCUMENTATION
================================================================================

+---------------------+
| 🏗 SYSTEM OVERVIEW  |
+---------------------+

ARCHITECTURE:
┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐
│ Client  │ →  │ Nginx   │ →  │ FastAPI │ →  │ Postgres│    │ MailHog │
└─────────┘ ←  └─────────┘ ←  └─────────┘ ←  └─────────┘    └─────────┘
                                    │
                                    └────────── SMTP ────────→ (or real
                                                                mail provider
                                                                in prod)
               ┌─────────┐
               │ Backup  │ (pg_dump, daily, → ./backups, read by web
               └─────────┘  container for admin list/download/restore)

The `web` container serves both the JSON API and the static frontend
(vanilla JS/HTML/CSS under `backend/app/static`) from the same FastAPI app.
There is no separate frontend build step or framework.

TECHNOLOGY STACK:
+------------+-------------------+-----------------------------------------+
| Component  | Technology        | Purpose                                  |
+------------+-------------------+-------------------------------------------+
| Backend    | Python 3.12/FastAPI/SQLAlchemy | Business logic, REST API   |
| Database   | PostgreSQL 16     | Data persistence                         |
| Frontend   | Vanilla JS/HTML/CSS | UI, served as static files by FastAPI  |
| PDF        | ReportLab + qrcode | Invoice/quote/delivery-note PDFs + GiroCode |
| Email      | smtplib -> MailHog (dev) or real SMTP (prod) | Sending documents/reminders |
| Proxy      | Nginx             | Host-based reverse proxy, HTTP + HTTPS   |
| Container  | Docker Compose    | 5 services: web, db, mailhog, proxy, backup |
+------------+-------------------+-------------------------------------------+

DEPLOYMENT:

Guided scripts (recommended):
  scripts/setup-test.sh   -> quick local test instance, self-signed cert,
                              random test credentials, no real email
  scripts/setup-prod.sh   -> interactive: generates strong SESSION_SECRET
                              and DB password, optionally configures real
                              SMTP credentials and requests a Let's Encrypt
                              certificate via certbot (falls back to the
                              existing self-signed cert if certbot is
                              unavailable or no domain is given)

Manual steps:
1. Generate a self-signed dev cert:
   openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
     -keyout nginx/certs/localhost.key \
     -out nginx/certs/localhost.crt \
     -subj "/CN=localhost"

2. Configure .env (see .env.example):
   POSTGRES_USER=rechnung
   POSTGRES_PASSWORD=securepassword
   APP_USERS=admin:admin,anna:passwort
   SESSION_SECRET=<random, change in production>

3. Start services:
   docker compose up --build

On startup, `on_startup()` in `main.py` calls `init_db()`, which waits for
Postgres to become reachable, creates all tables via SQLAlchemy metadata, and
runs a list of idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`
statements (`database.py::_MIGRATIONS`) so existing deployments pick up new
columns without a dedicated migration tool (no Alembic). `seed_users()` then
creates the admin + APP_USERS accounts, but only if the `users` table is
still empty (i.e. only on the very first start).

ACCESS POINTS:
+----------------------------+-------------------+------+---------------------+
| URL                        | Service           | Port | Notes               |
+----------------------------+-------------------+------+---------------------+
| http://rechnungen.localhost| Main Application  | 80   | via nginx            |
| https://rechnungen.localhost| Main Application | 443  | self-signed by default, or Let's Encrypt via setup-prod.sh |
| http://mail.localhost      | MailHog           | 80   | via nginx, dev email testing |
| http://localhost:8000      | Main Application  | 8000 | direct, bypasses proxy |
| http://localhost:8025      | MailHog           | 8025 | direct, bypasses proxy |
+----------------------------+-------------------+------+---------------------+

DATABASE SCHEMA (SQLAlchemy models in backend/app/models.py):
+------------------+---------------------------------------------------------+
| Table            | Key Fields                                               |
+------------------+---------------------------------------------------------+
| users            | id, username, password_hash (PBKDF2), is_admin          |
| customers        | id, name, address, contact_person, email,               |
|                  | payment_term_days, skonto_percent, skonto_days, active   |
| products         | id, name, unit_price, active                             |
| invoices         | id, number (RE-YYYY-NNNN), customer_name/address/contact,|
|                  | issue_date, due_date, tax_rate, status, paid_amount,     |
|                  | skonto_percent/days, discount_percent, small_business,   |
|                  | locked_by, locked_at, cancelled_at                       |
| invoice_items    | id, invoice_id, description, quantity, unit_price        |
| quotes           | id, number (AN-YYYY-NNNN), customer_*, valid_until,      |
|                  | tax_rate, discount_percent, small_business, status,      |
|                  | converted_invoice_id                                     |
| quote_items      | id, quote_id, description, quantity, unit_price          |
| delivery_notes   | id, number (LS-YYYY-NNNN), customer_*, status,           |
|                  | source_invoice_id                                        |
| delivery_note_items | id, delivery_note_id, description, quantity            |
| settings         | id (=1, single row), company_name, address, tax_id,      |
|                  | vat_id, iban, bic, email, phone, logo, logo_mime          |
| audit_log        | id, timestamp, username, action, target_type, target_id, |
|                  | detail                                                    |
+------------------+---------------------------------------------------------+

Invoices, quotes and delivery notes share ONE per-year sequence number
(`crud._next_doc_suffix`), taking the max suffix across all three tables so a
quote converted to an invoice, or an invoice converted to a delivery note,
can carry the same running number forward with just the prefix changed
(e.g. AN-2026-0007 -> RE-2026-0007 -> LS-2026-0007). Number assignment is
serialized with a PostgreSQL advisory transaction lock
(`pg_advisory_xact_lock`, fixed key) so concurrent users never collide.

Computed values (subtotal, discount, net, tax, total, remaining, overdue,
skonto amount/date, line totals) are Python `@property` methods on the
SQLAlchemy models, not stored columns — they are recalculated on every read.

CONCURRENCY / EDIT LOCKING:
Invoices carry `locked_by` / `locked_at`. A user opening an invoice for
editing calls `POST /api/invoices/{id}/lock`; the lock is considered active
for 5 minutes (`crud.LOCK_TIMEOUT`) from the last acquire/renew call and
auto-expires after that. `PUT /api/invoices/{id}` is rejected with 409 if
another user currently holds an active lock. Quotes and delivery notes do
not currently have this locking mechanism.

API ENDPOINTS (see README.md for the full table grouped by resource):
Invoices, quotes, delivery notes, customers, products, users, settings,
dashboard stats, month export, audit log, and backup management are all
exposed under `/api/*`. Authentication endpoints (`/login`, `/logout`) and
the SPA/static assets (`/`, `/static/*`, `/datenschutz`, `/health`) are
outside `/api`.

SECURITY:
- Password hashing: PBKDF2-HMAC-SHA256, 200,000 iterations, random 16-byte
  salt per user (backend/app/auth.py, stdlib only).
- Authentication: server-side session, signed cookie via Starlette
  `SessionMiddleware` (HMAC with `SESSION_SECRET`). The cookie is NOT
  configured with `https_only`/`secure` flags, so treat this as "signed &
  tamper-evident", not "encrypted"; serve over HTTPS in production.
- All routes require login except the `PUBLIC_PREFIXES` in `auth.py`
  (`/login`, `/logout`, `/static`, `/health`, `/favicon`, `/datenschutz`).
  Enforced by a global `require_login` HTTP middleware in `main.py`.
- Admin-only endpoints use a `require_admin` FastAPI dependency (user
  management, settings writes, logo upload, customer DSGVO export/anonymize,
  audit log, backup list/download/restore).
- Brute-force protection: login lockout after 5 failed attempts per
  (client IP, username) for 5 minutes, in-memory only (`auth.py`) — resets on
  app restart and is not shared across multiple web replicas.
- Input validation: Pydantic schemas (`schemas.py`) validate all request
  bodies (string lengths, numeric ranges, required fields).
- Backup filenames are validated against a strict regex before being read
  from disk (`backup._resolve`) to prevent path traversal.
- Not implemented: CSRF tokens, security-response-headers middleware (CSP,
  X-Frame-Options, HSTS, etc.), general API rate limiting, and there is no
  automated test suite.
- GDPR/DSGVO: privacy notice page (`/datenschutz`), per-customer data export
  (Art. 15) and anonymization (Art. 17) restricted to admins, and an
  append-only audit log of who accessed/exported/anonymized/downloaded what
  and when.

EMAIL:
`email_service.py` sends via `smtplib` to `SMTP_HOST`/`SMTP_PORT` from
config (defaults to the bundled MailHog container, unauthenticated, no
TLS — dev/test only). Setting `SMTP_USE_TLS=true` plus `SMTP_USER`/
`SMTP_PASSWORD` (e.g. via `scripts/setup-prod.sh`) switches to STARTTLS with
login for real delivery. Four message types are generated: invoice email,
quote email, delivery-note email, payment reminder, and payment confirmation
(sent automatically when a payment fully settles an invoice via
`POST /api/invoices/{id}/payment`, not when status is set to "bezahlt"
directly via the status endpoint).

PDF GENERATION:
`pdf.py` builds invoice, quote, and delivery-note PDFs with ReportLab,
including company letterhead/logo, item table, tax/discount/skonto
breakdown, and — for invoices with a valid IBAN — a GiroCode/EPC-QR payment
code generated with the `qrcode` library.

BACKUP:
- The `backup` Docker Compose service runs `pg_dump` on a loop (`sleep
  86400`) against the `db` container, gzips the dump into `./backups`, and
  prunes anything beyond the newest 14 files. Failures are logged to
  `./backups/last_error.log`.
- The `web` container mounts `./backups` read-only and exposes it to admins
  via `/api/admin/backups` (list), `/api/admin/backups/{name}/download`, and
  `/api/admin/backups/{name}/restore`. Restore shells out to
  `gunzip -c <file> | psql "$DATABASE_URL"`, which overwrites current data —
  the frontend requires two confirmation dialogs before calling it, and every
  restore attempt (and download) is written to the audit log.
- Manual backup: `docker exec -t rechnung_db pg_dump -U <user> -F c -b -v -f backup.dump <db>`

NOT IMPLEMENTED (do not assume these exist; see TODO.md for the full audit):
horizontal scaling / load balancing, response caching, DB connection
pooling beyond SQLAlchemy defaults, CI/CD pipeline, application monitoring,
API pagination (list endpoints return everything; only the audit log has an
internal `limit`), multi-currency, recurring invoices, credit notes as a
distinct document type, approval workflows, customer groups/credit limits,
customer/product CSV import-export, custom PDF templates, and an automated
test suite.
