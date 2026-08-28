================================================================================
RECHNUNGS-APP - DEVELOPMENT ROADMAP (TODO.md)
================================================================================

+---------------------+
| 📋 DEVELOPMENT TASKS |
+---------------------+

Status re-audited against the actual code in backend/app/ on 2026-08-27.
An item is only marked [X] if it is verifiably implemented — the file name
and, where useful, the function are named next to it so the claim can be
checked in seconds. The 2026-08-27 pass added the security middleware layer
(CSRF/rate limiting/headers), structured logging, API pagination, the
non-root service user, the test suites (139 backend + 27 frontend tests) and
the GitHub Actions pipeline; those lines moved from [ ] to [X] and the docs
were updated to match. It also delivered the four [FEATURE REQUEST] items at
the bottom of this file and both entries from the former IDEAS section (live
presence indicator, payment-confirmation alignment).

The 2026-08-28 pass worked through the NEW IDEAS block at the bottom of this
file: the logo upload no longer wipes unsaved company data, the customer list
no longer runs past the edge of the card, customers can be imported from CSV,
"History" is now "Rechnungsübersicht", the lock/presence/restored-draft
banners can be dismissed, and a customer search now preselects its best hit.
Test counts are up to 155 backend + 41 frontend tests.

Directly afterwards (same day, second round): delivery notes can be turned
into quotes, the empty hint banners no longer show as coloured bars, and the
customer import takes JSON as well as CSV without a separate "choose file"
step.

[SECURITY & PRODUCTION]
[X] PBKDF2 password hashing (200k iterations, per-user salt)
[X] Login brute-force lockout (5 failed attempts -> 5 min lock, per IP+user)
[X] Guided production setup script (scripts/setup-prod.sh): strong secrets,
    optional real SMTP, optional Let's Encrypt cert via certbot
[X] GDPR/DSGVO: privacy notice page (/datenschutz)
[X] GDPR/DSGVO: per-customer data export (Art. 15, admin-only)
[X] GDPR/DSGVO: customer anonymization (Art. 17, admin-only)
[X] GDPR/DSGVO: audit log of access to personal data
[X] Backup path-traversal protection (strict filename validation)
[X] CSRF protection (security.csrf_middleware): per-session token, required
    in the X-CSRF-Token header on every writing request; /login checks the
    csrf_token form field instead. Toggle via CSRF_ENABLED.
[X] General API rate limiting (security.rate_limit_middleware): sliding
    window per IP, 600/60s by default plus a stricter 20/300s bucket for
    POST /login; answers 429 with Retry-After. Configurable via
    RATE_LIMIT_* in .env.
[X] Security response headers (security.apply_security_headers): CSP,
    X-Content-Type-Options, X-Frame-Options, Referrer-Policy,
    Permissions-Policy, Cross-Origin-Opener-Policy, plus HSTS on HTTPS.
[X] Session cookie hardening: SameSite=Strict, max_age (SESSION_MAX_AGE,
    12h default), secure flag via SESSION_HTTPS_ONLY (set by
    scripts/setup-prod.sh); session cleared on login against fixation.
[X] Container/process no longer runs as root (backend/Dockerfile creates and
    switches to user `rechnung`; scripts/lib-common.sh creates the matching
    host user and hands the project directory over to it)
[X] Frontend escapes database values before rendering (app.js: esc()); a
    customer name or article description containing HTML can no longer break
    out of the table markup. Item rows set their values via the DOM.
[ ] Automated security audit / external pen test

[CORE FUNCTIONALITY]
[X] Server-side input validation (Pydantic schemas on every request body)
[X] Collision-free document numbering across invoices/quotes/delivery notes
    (shared per-year sequence, PostgreSQL advisory lock)
[X] Edit lock on invoices (prevents two users editing the same invoice at
    once, auto-expires after 5 minutes)
[X] Live presence indicator ("someone else has this open"): a client with a
    document open sends a heartbeat every 10s to
    POST /api/presence/{doc_type}/{doc_id} and gets back everyone else on
    that document; the form shows a banner and the lists mark those rows.
    Covers invoices, quotes AND delivery notes — the latter two have no edit
    lock at all. Entries expire 45s after the last heartbeat, so a closed
    tab cleans itself up. Polling, not WebSockets (see
    Technical_documentation.md for why). Table `presence`, crud.touch_presence.
[X] Partial payments with automatic status transitions (offen -> teilbezahlt
    -> bezahlt)
[X] Discount (%) and Kleinunternehmer / §19 UStG mode (no VAT)
[X] Skonto (early-payment discount) calculation and PDF display
[X] Idempotent schema migrations on startup (ADD COLUMN IF NOT EXISTS)
[X] Automated test suite: 155 pytest tests in backend/tests/ against a
    temporary SQLite DB (conftest.py) — invoices, quotes, delivery notes,
    customers/products (incl. the CSV/JSON import), admin endpoints, audit log and
    the security layer. Run with `python -m pytest` in backend/. Plus 41
    frontend tests in backend/tests/frontend/ that drive the real index.html +
    app.js in jsdom (customer picker incl. best-match preselection, draft
    cache, dismissible banners, logo upload, customer import, list filters,
    CSRF header, escaping) — `npm install && npm test`, needs Node >= 20.
[X] Centralized/structured error handling & logging (logging_setup.py):
    JSON log lines, per-request X-Request-ID carried into every line and
    every error body, uniform {"detail", "request_id"} responses for HTTP,
    validation and unhandled errors.

[DOCUMENTS: INVOICES / QUOTES / DELIVERY NOTES]
[X] Edit an existing (non-cancelled) invoice
[X] Cancel (storno) an invoice and revert back to "offen"
[X] Quotes (Angebote): create, edit, PDF, email, status, convert to invoice
[X] Delivery notes (Lieferscheine): create, edit, PDF, email, status,
    generate from an existing invoice, and convert into a quote
    (POST /api/delivery-notes/{id}/convert-to-quote, "zu Angebot" in the
    list): quantities from the delivery note, prices from the product
    catalogue where the description matches, otherwise 0. Reuses the running
    number (LS-2026-0007 -> AN-2026-0007) and falls back to the next free one
    if that number is already taken.
[X] Invoice/quote/delivery-note PDF generation incl. GiroCode/EPC-QR
[X] Email sending for invoices, quotes, delivery notes, payment reminders,
    and automatic payment confirmation on full settlement — fires on BOTH
    ways of settling an invoice now (POST .../payment and
    PATCH .../status {"status":"bezahlt"}), only on the transition, and a
    failing SMTP server never breaks the payment (main._confirm_payment_if_settled)
[X] Monthly export as ZIP (PDF per invoice + CSV summary)
[ ] Recurring invoices
[ ] Approval workflow
[ ] Partial refunds / credit notes as a distinct document type (only
    cancel-and-revert exists today)
[ ] Multi-currency support (EUR only)
[ ] Custom PDF templates
[ ] Document attachments on invoices

[CUSTOMER & PRODUCT MANAGEMENT]
[X] Customer master data with default payment term + skonto defaults
[X] Activate/deactivate customers and products (soft-disable, no delete)
[ ] Customer groups
[ ] Credit limits
[ ] Customer notes field
[X] Customer import from CSV *or* JSON (POST /api/customers/import, "Kunden
    importieren" in the customer view): column/key names mapped against German
    and English spellings, `;`/`,`/tab delimiter, UTF-8 or Windows-1252,
    existing customers matched by name are updated instead of duplicated, bad
    records skipped and reported. JSON includes the file that the per-customer
    DSGVO export produces, so export and import fit together. The button opens
    the file dialog directly; picking the file starts the import. CSV template
    via "Beispieldatei".
[ ] Import for products, and a bulk CSV export for both

[REPORTING]
[X] Dashboard KPIs (revenue, open amount, overdue amount, 6-month chart)
[X] History filtering (status/overdue) and column sorting — the tab is now
    called "Rechnungsübersicht" (the view id `view-history` stayed)
[ ] Advanced/custom report builder
[ ] Dedicated tax report / VAT return export
[ ] Profit & loss reporting

[SYSTEM / DEPLOYMENT]
[X] Docker Compose stack (web, db, mailhog, proxy, backup — 5 containers)
[X] Automated daily backups (pg_dump, gzip, 14-day retention)
[X] Backup management UI for admins (list, download, restore with double
    confirmation)
[X] Guided setup scripts (scripts/setup-test.sh, scripts/setup-prod.sh)
[X] CI pipeline (.github/workflows/ci.yml): backend tests, frontend tests,
    static checks (compileall, bash -n/shellcheck, node --check) and a Docker
    image build that asserts the container UID is not 0 and that compose
    config is valid. Runs on every push and pull request. (No CD/deploy
    stage.)
[ ] Application monitoring / observability (structured logs exist, but no
    metrics endpoint, dashboard or alerting)
[ ] Horizontal scaling / load balancing (single `web` container by design;
    in-memory login-lockout state would not survive multiple replicas)
[ ] Response caching layer
[X] API pagination: ?limit=(1..MAX_PAGE_SIZE)&offset= on invoices, quotes,
    delivery notes, customers, products and the audit log; the total always
    comes back in the X-Total-Count header. Without ?limit the full list is
    returned, so older callers keep working.

[DOCUMENTATION]
[X] README.md reflects the actual current feature set
[X] Technical_documentation.md reflects the actual current architecture/API
[X] Dedicated troubleshooting guide (TROUBLESHOOTING.md)
[ ] Diagrams beyond the ASCII architecture sketch in Technical_documentation.md

[KNOWN ISSUES]
[ ] Self-signed certificate by default -> browser warning until
    scripts/setup-prod.sh sets up a real Let's Encrypt cert
[ ] MailHog is the default mail transport (no real delivery) until SMTP_* is
    configured for a real provider
[ ] Default admin/admin and test-user credentials must be changed before any
    real deployment (setup-prod.sh generates strong ones automatically)
[ ] Login-lockout AND rate-limit counters are both in-memory: correct for
    the single `web` container the compose file defines, but they would need
    a shared store (Redis o. ä.) before scaling out

[FEATURE REQUEST]
[X] Customer selection when creating an **Angebot** or **Lieferschein**, same
    as for **Rechnungen** — one shared, searchable customer picker
    (app.js: registerCustomerPicker) on all three forms; only active
    customers are offered and picking one fills in the master data.
[X] **Smart caching** of entered data: invoice/quote/delivery-note forms are
    saved to localStorage as you type (debounced, key rechnung.drafts.v1)
    and restored on reload; drafts expire after 7 days and never leave the
    browser.
[X] **Search and filter** in all relevant views and selection fields:
    invoice/quote/delivery-note lists (search + status filter), customers and
    products (search + active filter), users, audit log, and the customer
    picker on all three document forms.
[X] Dedicated non-root user for the test and production environment:
    scripts/lib-common.sh creates the host user `rechnung` (idempotent, both
    setup scripts use it), hands the project files to it and writes the
    matching APP_UID/APP_GID into .env so the container runs under the same
    identity. Falls back gracefully with a warning if it cannot get root.


---------------------------------------------------------------------------
STILL OPEN (unchanged by the 2026-08-27 and 2026-08-28 passes)
---------------------------------------------------------------------------
The remaining [ ] items are real product/infrastructure work, not oversights:
recurring invoices, approval workflow, credit notes as their own document
type, multi-currency, custom PDF templates, document attachments, customer
groups, credit limits, customer notes, import for products (customers can be
imported from CSV/JSON since 2026-08-28) and bulk CSV export, a custom report
builder, a VAT-return export, P&L reporting, monitoring, a response cache,
horizontal scaling (needs shared state for the login lockout and the
rate-limit counters), an external pen test, and diagrams beyond the ASCII
sketch. The known issues around self-signed certificates, MailHog as the
default transport and the default credentials are all addressed by
scripts/setup-prod.sh but remain the defaults until it is run.

---------------------------------------------------------------------------
IDEAS / NOT YET SCHEDULED
---------------------------------------------------------------------------
Both former entries here were implemented on 2026-08-27 and moved into the
lists above:

  * Live collaborative indicator -> "someone else has this open" now shows as
    a banner over the form and as a marker in the lists, for invoices, quotes
    and delivery notes. What is still NOT there is field-level merge: two
    people editing the same quote or delivery note can still overwrite each
    other, they just see each other now. Invoices remain protected by the
    edit lock. A real lock for quotes/delivery notes, or per-field merging,
    would be the next step if that turns out to hurt in practice.
  * Payment-confirmation email -> now sent from whichever path settles the
    invoice, so the customer gets the same mail whether the clerk records a
    payment or flips the status to "bezahlt".

Nothing else is currently parked here. New ideas go below this line.


NEW IDEAS (all done on 2026-08-28):
[X] Uploading a logo wiped every field under "Firmendaten": the upload handler
    called loadSettings(), which re-filled the form from the server and threw
    away whatever was typed but not yet saved. It now only refreshes the
    preview (app.js: showLogo).
[X] The customer list ran past the edge of the card: the wide lists sit in a
    `.table-scroll` container now (own horizontal scrollbar, the page itself
    never scrolls sideways) and the address column wraps (`.cell-wrap`).
[X] Customer import: CSV upload in the "Kunden" view, see the
    [CUSTOMER & PRODUCT MANAGEMENT] section above.
[X] "History" is now called "Rechnungsübersicht" (label only; ids, routes and
    the server-side search are unchanged).
[X] The hints above the invoice/quote/delivery-note forms — edit lock,
    "someone else is here" and "restored draft" — each have a "✕" now.
    Hiding a draft hint does NOT discard the draft; a hidden banner comes
    back as soon as it has something new to say (app.js: showBanner /
    hideBanner / dismissedBanners).
[X] Searching for a customer now preselects the best hit instead of leaving
    "– neuen Kunden eingeben –" selected, and fills the master data from it.
    An already-picked customer that still matches the search is kept, and the
    data is only re-applied when the best hit actually changes, so typing on
    does not overwrite fields edited by hand.


NEUE PUNKTE (2026-08-28, zweiter Durchgang):
[X] Lieferschein -> Angebot ("zu Angebot" in der Lieferscheinliste). "zu
    Rechnung" gibt es dort bewusst nicht; im Angebot bleibt es unverändert.
[X] Die leeren Hinweisbanner (orange = Sperre/Anwesenheit, blau = Entwurf)
    standen dauerhaft über den Formularen: `.warn-banner`/`.presence-banner`/
    `.draft-banner` setzen `display: flex`, und Autoren-CSS schlägt die
    Browser-Vorgabe `[hidden] { display: none }`. `[hidden] { display: none
    !important; }` am Ende von styles.css blendet sie wieder aus; ein Test
    rechnet dafür mit der echten styles.css statt nur das Attribut zu prüfen.
[X] Import nimmt jetzt CSV und JSON (inkl. der Datei aus dem Kunden-Export),
    und die sichtbare Dateiauswahl ist weg: der Knopf öffnet den Dialog, die
    Auswahl startet den Import.
