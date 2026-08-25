================================================================================
RECHNUNGS-APP - DEVELOPMENT ROADMAP (TODO.md)
================================================================================

+---------------------+
| 📋 DEVELOPMENT TASKS |
+---------------------+

Status re-audited against the actual code in backend/app/ on 2026-08-25 —
the previous version of this file had almost everything checked off,
including several items (CI/CD, load balancing, multi-currency, a custom
report builder, a full test suite...) that do not exist anywhere in the
repo. This version only marks an item [X] if it is verifiably implemented.

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
[ ] CSRF protection for forms
[ ] General API rate limiting (only login has brute-force protection)
[ ] Security response headers (CSP, X-Frame-Options, HSTS, ...)
[ ] Session cookie hardening (https_only/secure flag not set explicitly)
[ ] Automated security audit / pen test

[CORE FUNCTIONALITY]
[X] Server-side input validation (Pydantic schemas on every request body)
[X] Collision-free document numbering across invoices/quotes/delivery notes
    (shared per-year sequence, PostgreSQL advisory lock)
[X] Edit lock on invoices (prevents two users editing the same invoice at
    once, auto-expires after 5 minutes)
[X] Partial payments with automatic status transitions (offen -> teilbezahlt
    -> bezahlt)
[X] Discount (%) and Kleinunternehmer / §19 UStG mode (no VAT)
[X] Skonto (early-payment discount) calculation and PDF display
[X] Idempotent schema migrations on startup (ADD COLUMN IF NOT EXISTS)
[ ] Automated test suite (none found in the repo)
[ ] Centralized/structured error handling & logging beyond FastAPI defaults

[DOCUMENTS: INVOICES / QUOTES / DELIVERY NOTES]
[X] Edit an existing (non-cancelled) invoice
[X] Cancel (storno) an invoice and revert back to "offen"
[X] Quotes (Angebote): create, edit, PDF, email, status, convert to invoice
[X] Delivery notes (Lieferscheine): create, edit, PDF, email, status,
    generate from an existing invoice
[X] Invoice/quote/delivery-note PDF generation incl. GiroCode/EPC-QR
[X] Email sending for invoices, quotes, delivery notes, payment reminders,
    and automatic payment confirmation on full settlement
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
[ ] CSV import/export of customers/products (DSGVO export is JSON, per
    customer, not a bulk CSV feature)

[REPORTING]
[X] Dashboard KPIs (revenue, open amount, overdue amount, 6-month chart)
[X] History filtering (status/overdue) and column sorting
[ ] Advanced/custom report builder
[ ] Dedicated tax report / VAT return export
[ ] Profit & loss reporting

[SYSTEM / DEPLOYMENT]
[X] Docker Compose stack (web, db, mailhog, proxy, backup — 5 containers)
[X] Automated daily backups (pg_dump, gzip, 14-day retention)
[X] Backup management UI for admins (list, download, restore with double
    confirmation)
[X] Guided setup scripts (scripts/setup-test.sh, scripts/setup-prod.sh)
[ ] CI/CD pipeline
[ ] Application monitoring / observability
[ ] Horizontal scaling / load balancing (single `web` container by design;
    in-memory login-lockout state would not survive multiple replicas)
[ ] Response caching layer
[ ] API pagination (list endpoints return all rows; only the audit log has
    an internal limit)

[DOCUMENTATION]
[X] README.md reflects the actual current feature set
[X] Technical_documentation.md reflects the actual current architecture/API
[ ] Dedicated troubleshooting guide
[ ] Diagrams beyond the ASCII architecture sketch in Technical_documentation.md

[KNOWN ISSUES]
[ ] Self-signed certificate by default -> browser warning until
    scripts/setup-prod.sh sets up a real Let's Encrypt cert
[ ] MailHog is the default mail transport (no real delivery) until SMTP_* is
    configured for a real provider
[ ] Default admin/admin and test-user credentials must be changed before any
    real deployment (setup-prod.sh generates strong ones automatically)
[ ] Login-lockout state is in-memory and per-process — resets on restart,
    won't work correctly if `web` is ever scaled beyond one replica
[ ] No automated tests to catch regressions


---------------------------------------------------------------------------
IDEAS / NOT YET SCHEDULED
---------------------------------------------------------------------------
Live/real-time collaborative editing indicator on invoices (today: a
5-minute edit lock prevents conflicting edits, but there is no live
"someone else is viewing this" indicator or field-level merge). Automatic
payment-confirmation email also only fires via the payment endpoint, not
when an invoice is marked "bezahlt" directly through the status endpoint —
worth aligning if that gap turns out to matter in practice.
