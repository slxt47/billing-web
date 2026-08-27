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
| Tests/CI   | pytest + httpx, node:test + jsdom, GitHub Actions | 139 backend + 27 frontend tests, static checks, image build |
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
| presence         | id, doc_type, doc_id, username, last_seen                 |
|                  | (unique per doc_type+doc_id+username; short-lived)        |
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

LIVE PRESENCE ("someone else is here"):
The lock stops two people from saving over each other, but says nothing
while you are typing — and quotes/delivery notes have no lock at all. The
`presence` table closes that gap: a client with a document open in a form
POSTs to `/api/presence/{doc_type}/{doc_id}` every 10 seconds
(`crud.PRESENCE_HEARTBEAT`) and gets back everyone *else* currently on that
document, which the frontend renders as a banner above the form. Rows without
a heartbeat for 45 seconds (`crud.PRESENCE_TIMEOUT`) count as gone and are
deleted on the next write, so a closed tab cleans itself up; leaving a form
also sends an explicit DELETE. A heartbeat additionally drops that user's rows
on *other* documents, so one user is only ever shown in one place.
`GET /api/presence` returns the same information grouped by document (again
excluding the caller) so the invoice/quote/delivery-note lists can mark rows
where someone is already sitting.

Deliberately polling, not WebSockets: ~10 s latency is plenty for "someone
else is here", it needs no long-lived connections or proxy changes, and it is
straightforward to test. `doc_type` is validated against
`models.PRESENCE_TYPES`. State lives in the database rather than in process
memory, so unlike the login lockout this part would survive a second replica.

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
  `SessionMiddleware` (HMAC with `SESSION_SECRET`). The cookie is set with
  `SameSite=Strict` and a lifetime of `SESSION_MAX_AGE` (default 12 h); the
  `secure` flag follows `SESSION_HTTPS_ONLY` (default false so that the direct
  `http://localhost:8000` port still works — `scripts/setup-prod.sh` turns it
  on). The payload is signed & tamper-evident, not encrypted.
- Session fixation: `POST /login` clears the session before storing the user,
  so a pre-login cookie value cannot be carried into the authenticated
  session.
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
- CSRF (`security.py`): a per-session token (`secrets.token_urlsafe(32)`)
  stored in the signed session and mirrored into a readable `csrftoken`
  cookie. Every non-safe method (anything but GET/HEAD/OPTIONS/TRACE) must
  echo it back in the `X-CSRF-Token` header; `/login` is exempt from the
  middleware and checks the `csrf_token` form field in the endpoint instead.
  `/api/me` returns the current token so the SPA can bootstrap. Comparison is
  `hmac.compare_digest`. Toggle with `CSRF_ENABLED` (debugging only).
- Rate limiting (`security.py`): sliding-window counter per client IP
  (`X-Forwarded-For` first hop, else peer address). Default 600 requests /
  60 s for everything, plus a stricter 20 / 300 s bucket for `POST /login` on
  top of the existing per-(IP, username) lockout. Over the limit returns 429
  with a `Retry-After` header. `/static` and `/health` are exempt; a limit of
  0 disables the bucket. State is in-process, like the login lockout.
- Security response headers (`security.py`), set on every response including
  the error paths: `Content-Security-Policy` (self-only; no inline scripts or
  styles), `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: no-referrer`, `Permissions-Policy` (geolocation,
  microphone, camera all denied), `Cross-Origin-Opener-Policy: same-origin`,
  and `Strict-Transport-Security` when `HSTS_ENABLED` and the request arrived
  over HTTPS.
- Non-root runtime: the image creates a `rechnung` user (UID/GID from the
  `APP_UID`/`APP_GID` build args, default 10001) and drops to it via `USER`;
  the setup scripts create a matching host user through
  `scripts/lib-common.sh` and chown the project directory to it, so neither
  the files on disk nor the process in the container belong to root.
- GDPR/DSGVO: privacy notice page (`/datenschutz`), per-customer data export
  (Art. 15) and anonymization (Art. 17) restricted to admins, and an
  append-only audit log of who accessed/exported/anonymized/downloaded what
  and when.

MIDDLEWARE ORDER:
Starlette runs the most recently registered middleware first, so the order in
`main.py` is written back-to-front. Actual request order is:

  request-ID/logging -> security headers -> rate limit -> session ->
  CSRF -> login check -> route

The session has to be established before CSRF (the token lives in it) and
before the login check (it reads `request.session["user"]`); rate limiting and
the header middleware sit outside so they still apply to requests that are
rejected before ever reaching a route.

LOGGING & ERROR HANDLING:
`logging_setup.py` replaces the default uvicorn text output with one JSON
object per line (`ts`, `level`, `logger`, `request_id`, `message`, plus any
`extra={"fields": {...}}`), so `docker compose logs` is machine-readable.
Every request gets a request ID — an incoming `X-Request-ID` from the proxy is
reused, otherwise one is generated — which is echoed in the response header,
carried in a `ContextVar` into every log line of that request, and included in
every error body. All errors (HTTP exceptions, Pydantic validation failures,
and unhandled exceptions) are normalized to
`{"detail": ..., "request_id": ...}`; validation errors are flattened from
FastAPI's nested list into a single readable string. Unhandled exceptions log
the traceback and return a generic 500 without leaking internals. `/static`,
`/health` and `/favicon.ico` are excluded from the access log. Level via
`LOG_LEVEL`.

API PAGINATION:
List endpoints (`/api/invoices`, `/api/quotes`, `/api/delivery-notes`,
`/api/customers`, `/api/products`, `/api/audit-log`) accept optional
`?limit=` (1..`MAX_PAGE_SIZE`, default 500) and `?offset=`. Without `limit`
the full list is returned as before, so existing callers are unaffected. The
unpaginated total always comes back in the `X-Total-Count` response header.

FRONTEND (backend/app/static/app.js):
No framework and no build step — one script, loaded at the end of index.html,
with the views as `<section>` elements that are shown/hidden. Three parts are
worth knowing about:

- Customer picker: invoice, quote and delivery-note forms each pair a search
  input with a `<select>`. `registerCustomerPicker()` wires them up and
  `fillPicker()` re-renders the options from `customersCache`, matching on
  name, contact person, email and address; only active customers are offered.
  A `pending` value lets a restored draft re-select a customer before the
  customer list has finished loading.
- Draft cache: unsent input in the three document forms is written to
  `localStorage` under `rechnung.drafts.v1` (debounced 400 ms, plus a flush on
  `beforeunload`) and restored on the next load, with a banner and a "discard"
  button. Drafts are per-browser, never sent to the server, skipped entirely
  while editing an existing document (the server state and the edit lock win
  there), dropped after a successful save, and expire after 7 days.
- List filtering happens client-side over the already-loaded cache for quotes,
  delivery notes, customers, products, users and the audit log; only the
  invoice history searches server-side (`GET /api/invoices?search=`), because
  that list is the one that grows.

The `fetch` wrapper at the top of the file attaches the `X-CSRF-Token` header
to every non-safe request, redirects to `/login` on 401, and reloads once on a
CSRF 403 (expired token). Values coming from the database are escaped with
`esc()` before being interpolated into `innerHTML`, and item rows set input
values via the DOM instead of the markup.

TESTING & CI:
`backend/tests/` holds 139 pytest tests driven through `httpx`/FastAPI's
`TestClient` against a temporary SQLite database (`conftest.py`), covering the
invoice/quote/delivery-note lifecycles, customers and products, admin-only
endpoints and the audit log, and the security layer itself (CSRF rejection,
rate limiting, headers, login lockout). Run with `python -m pytest` in
`backend/` after `pip install -r requirements-dev.txt`.

The two PostgreSQL-specific pieces are dialect-guarded so the suite can run on
SQLite: `crud._lock_doc_numbers()` only issues `pg_advisory_xact_lock` on
PostgreSQL, and `database._migrate()` skips the `ADD COLUMN IF NOT EXISTS`
statements (on SQLite `create_all()` already produces the current schema).

`backend/tests/frontend/` holds 27 frontend tests that load the real
`index.html` and `app.js` into a jsdom window with a stubbed API and exercise
the customer picker, the draft cache, the list filters, the CSRF header and
the HTML escaping (`npm install && npm test`, needs Node >= 20).

`.github/workflows/ci.yml` runs four jobs on every push and PR: the backend
suite, the frontend suite, static checks (`compileall`, `bash -n` and
shellcheck on `scripts/*.sh`, `node --check` on the frontend JS), and a Docker
image build that asserts the container's UID is not 0 and that
`docker compose config` validates.

EMAIL:
`email_service.py` sends via `smtplib` to `SMTP_HOST`/`SMTP_PORT` from
config (defaults to the bundled MailHog container, unauthenticated, no
TLS — dev/test only). Setting `SMTP_USE_TLS=true` plus `SMTP_USER`/
`SMTP_PASSWORD` (e.g. via `scripts/setup-prod.sh`) switches to STARTTLS with
login for real delivery. Five message types are generated: invoice email,
quote email, delivery-note email, payment reminder, and payment confirmation.
The confirmation is sent by `main._confirm_payment_if_settled()` whenever an
invoice moves into "bezahlt" — both via `POST /api/invoices/{id}/payment` and
via `PATCH /api/invoices/{id}/status`, which previously stayed silent. It
fires only on the transition (never twice for an already-paid invoice) and is
best effort: a failing SMTP server is logged but never breaks the payment or
status change.

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
horizontal scaling / load balancing (the login-lockout and rate-limit counters
are per-process and would need Redis or similar first), response caching, DB
connection pooling beyond SQLAlchemy defaults, application monitoring /
observability, multi-currency, recurring invoices, credit notes as a distinct
document type, approval workflows, customer groups/credit limits,
customer/product CSV import-export, custom PDF templates, document
attachments, a custom report builder, and a dedicated VAT/tax-return export.
