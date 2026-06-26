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
                   ↑
               ┌─────────┐
               │ Backup  │
               └─────────┘

TECHNOLOGY STACK:
+------------+-------------------+-----------------------------+
| Component  | Technology        | Purpose                     |
+------------+-------------------+-----------------------------+
| Backend    | Python/FastAPI    | Business logic, API         |
| Database   | PostgreSQL 16     | Data persistence            |
| Frontend   | Vanilla JS/HTML   | User interface              |
| PDF        | ReportLab         | Invoice generation          |
| Email      | MailHog           | Email testing               |
| Proxy      | Nginx             | Reverse proxy, HTTPS        |
| Container  | Docker            | Deployment                  |
+------------+-------------------+-----------------------------+

DEPLOYMENT:
1. Generate SSL cert:
   openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
     -keyout nginx/certs/localhost.key \
     -out nginx/certs/localhost.crt \
     -subj "/CN=localhost"

2. Configure .env:
   POSTGRES_USER=rechnung
   POSTGRES_PASSWORD=securepassword
   APP_USERS=admin:admin,anna:passwort

3. Start services:
   docker compose up --build

ACCESS POINTS:
+----------------------------+-------------------+------+---------------------+
| URL                        | Service           | Port | Notes               |
+----------------------------+-------------------+------+---------------------+
| http://rechnungen.localhost| Main Application  | 80   |                     |
| https://rechnungen.localhost| Main Application | 443  | Self-signed cert    |
| http://mail.localhost      | MailHog           | 80   | Email testing       |
+----------------------------+-------------------+------+---------------------+

DATABASE SCHEMA:
+---------------+---------------------------------------------------+
| Table         | Key Fields                                        |
+---------------+---------------------------------------------------+
| users         | id, username, password_hash, is_admin             |
| customers     | id, name, address, payment_terms, discount        |
| products      | id, name, description, price                      |
| invoices      | id, number, customer_id, date, status, total      |
| invoice_items | id, invoice_id, product_id, quantity, unit_price  |
| payments      | id, invoice_id, amount, date, method              |
| settings      | id, name, address, tax_id, iban, logo             |
+---------------+---------------------------------------------------+

API ENDPOINTS:
+--------+--------------------------------+-----------------------------+
| Method | Endpoint                      | Description                 |
+--------+--------------------------------+-----------------------------+
| POST   | /api/login                    | User authentication         |
| GET    | /api/invoices                 | List invoices               |
| POST   | /api/invoices                 | Create invoice              |
| GET    | /api/invoices/{id}            | Get invoice                 |
| PATCH  | /api/invoices/{id}/status     | Update status               |
| GET    | /api/invoices/{id}/pdf        | Download PDF                |
| POST   | /api/invoices/{id}/email      | Send email                  |
+--------+--------------------------------+-----------------------------+

SECURITY:
- PBKDF2 password hashing
- Session-based authentication
- Brute-force protection
- Secure session cookies
- HTTPS with TLS 1.2+
- Input validation

BACKUP:
- Daily automated pg_dump backups
- Stored in ./backups
- 14-day retention
- Manual backup: docker exec -t db pg_dump -U user -F c -b -v -f backup.dump dbname