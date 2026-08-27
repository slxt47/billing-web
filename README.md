# 🧾 Rechnungs-App

Eine Web-Applikation zum Schreiben von Rechnungen, Angeboten und Lieferscheinen —
komplett in Docker-Containern, mit PostgreSQL-Datenbank, History, PDF-Download,
E-Mail-Versand und DSGVO-Funktionen.

## ⚠️ Project Status: Not Production Ready

Please note that this web application is experimental and has **not been
audited by a third party**. It ships with a guided production setup script
(`scripts/setup-prod.sh`, see below) that generates strong secrets, creates a
dedicated non-root service user and can wire up real SMTP and a Let's-Encrypt
certificate. CSRF protection, per-IP rate limiting, security response headers
(CSP, HSTS, X-Frame-Options …), hardened session cookies, structured logging
and an automated test suite (139 Backend- + 27 Frontend-Tests) with CI are
in place.

Still open before you point this at real customer data: no external
penetration test, the login-lockout and rate-limit counters live in process
memory (so a single `web` replica only), and MailHog/self-signed certificates
remain the defaults until `scripts/setup-prod.sh` replaces them. Review the
code and `TODO.md` first.

🤖 **Development:** The code for this project was written and generated with the assistance of a **Mistral AI Agent**.

## Funktionen

### Rechnungen
- **Rechnungen erstellen** — Kunde, Adresse, Ansprechpartner, Positionen, MwSt., Hinweise;
  Summen werden live berechnet, Rechnungsnummern (`RE-<Jahr>-0001`) automatisch vergeben
  (kollisionsfrei über einen PostgreSQL-Advisory-Lock, auch bei gleichzeitigen Benutzern).
  Das **Rechnungsdatum ist immer der heutige Tag**.
- **Rechnungen bearbeiten** — bestehende, nicht stornierte Rechnungen können nachträglich
  geändert werden. Eine **Bearbeitungssperre** verhindert, dass zwei Benutzer gleichzeitig
  dieselbe Rechnung bearbeiten (läuft nach 5 Minuten Inaktivität automatisch ab).
- **Standard-MwSt. 20 %** voreingestellt; Datumsanzeige im Format `TT/MM/JJJJ`.
- **Skonto** — pro Rechnung bei Bedarf ein Skonto („X % bei Zahlung innerhalb Y
  Tagen"); erscheint mit Frist, Betrag und reduziertem Zahlbetrag auf dem PDF.
- **Rabatt & Kleinunternehmer** — Rabatt in % auf die Zwischensumme; §19-UStG-Modus
  ohne MwSt.
- **Zahlungen** — Zahlungseingänge (auch Teilzahlungen) erfassen; Status wird
  automatisch *teilbezahlt*/*bezahlt*; offener Betrag wird ausgewiesen. Bei
  vollständiger Zahlung per Zahlungserfassung wird automatisch eine
  Zahlungsbestätigung per E-Mail verschickt (falls der Kunde eine E-Mail-Adresse hat).
- **Rückfunktion / Storno** — Rechnung stornieren und per „zurück" wieder auf
  *offen* setzen; als *bezahlt* markieren; endgültig löschen.
- **Mahnwesen** — überfällige Rechnungen werden erkannt und farblich markiert;
  Zahlungserinnerung per E-Mail versendbar.
- **PDF-Download** — jede Rechnung als PDF herunterladen (inkl. GiroCode/EPC-QR).
- **E-Mail-Versand** — Rechnung als PDF per E-Mail verschicken, optional automatisch
  direkt beim Anlegen (`auto_email`).
- **Monats-Export** — alle Rechnungen eines Monats als ZIP (je ein PDF plus eine
  CSV-Übersicht) herunterladen.

### Angebote & Lieferscheine
- **Angebote (Quotes)** — eigener Belegtyp (`AN-<Jahr>-0001`) mit Positionen, MwSt.,
  Rabatt, Gültigkeitsdatum und Status (offen/angenommen/abgelehnt/umgewandelt); als
  PDF herunterladen oder per E-Mail verschicken.
- **Angebot → Rechnung** — ein Angebot lässt sich mit einem Klick in eine Rechnung
  umwandeln; die laufende Belegnummer wird dabei weitergereicht (z. B. `AN-2026-0007`
  → `RE-2026-0007`).
- **Lieferscheine** — reiner Liefernachweis (Beschreibung + Menge, ohne Preise),
  frei anlegbar oder direkt aus einer Rechnung erzeugt (`RE-2026-0007` →
  `LS-2026-0007`); als PDF herunterladen oder per E-Mail verschicken.
- Angebote, Rechnungen und Lieferscheine teilen sich **eine fortlaufende
  Belegnummer pro Jahr**, damit eine Umwandlung die Nummer sauber weiterschiebt.

### Kunden & Artikel
- **Stammkunden** — häufige Kunden mit Anschrift, Ansprechpartner, E-Mail,
  **Standard-Zahlungsfrist** und optionaler **Skonto-Vorgabe** anlegen und bearbeiten;
  bei Auswahl werden Fälligkeitsdatum und Skonto automatisch gesetzt.
- **Artikel/Leistungen** — wiederkehrende Posten mit Standardpreis anlegen und
  bearbeiten; beim Erstellen per Auswahl übernehmen (Preis wird gesetzt) oder
  weiterhin frei eintippen.
- **Aktivieren/Deaktivieren** — Kunden und Artikel können statt gelöscht auch nur
  deaktiviert werden (bleiben in der Auswahl ausgeblendet, History/alte Belege
  bleiben unberührt).

### DSGVO
- **Datenschutzerklärung** — öffentlich erreichbare Seite unter `/datenschutz`.
- **Auskunftsrecht (Art. 15 DSGVO)** — Admins können alle zu einem Kunden
  gespeicherten Daten (Stammdaten + zugehörige Rechnungen) als Export abrufen.
- **Recht auf Löschung (Art. 17 DSGVO)** — Kundendaten lassen sich anonymisieren
  (Name/Adresse/E-Mail werden entfernt); bereits ausgestellte Rechnungen bleiben
  aus steuerrechtlichen Aufbewahrungspflichten (GoBD) unverändert erhalten.
- **Audit-Log** — protokolliert admin-seitige Zugriffe auf personenbezogene Daten
  (Export, Anonymisierung, Backup-Downloads), einsehbar im Reiter „Audit-Log".

### Verwaltung & Betrieb
- **Anmeldung** — Login mit mehreren Benutzern, die sich **dieselben Daten teilen** und
  **gleichzeitig** Belege erstellen können (Nummernvergabe per DB-Lock kollisionsfrei).
- **Benutzerverwaltung** — ein **Admin-Account** kann Benutzer anlegen, löschen und
  Passwörter zurücksetzen (Reiter „Benutzer", nur für Admins sichtbar). Passwörter
  werden als PBKDF2-Hash gespeichert. Der letzte verbleibende Admin kann sich
  weder selbst löschen noch entfernen.
- **Firmendaten & Logo** — Absender, Steuernummer/USt-IdNr., IBAN/BIC und Logo
  erscheinen auf dem PDF (Reiter „Firma", nur Admin).
- **Automatische Backups** — täglicher `pg_dump` (gzip) in `./backups`, hält die
  letzten 14 Sicherungen.
- **Backup-Verwaltung im Admin-Bereich** — vorhandene Backups auflisten,
  herunterladen oder direkt über die Oberfläche in die laufende Datenbank
  zurückspielen (mit doppelter Sicherheitsabfrage, da destruktiv).
- **Dashboard** — Kennzahlen (Umsatz, offen, überfällig) und ein Umsatz-Diagramm
  der letzten 6 Monate; ist die Startseite nach dem Login.
- **History-Filter** — nach Status/überfällig filtern, nach Nummer/Kunde suchen und Spalten sortieren.
- **Hell-/Dunkel-Modus** — umschaltbar, Auswahl wird gespeichert.
- **Sicherheit** — Login-Sperre nach zu vielen Fehlversuchen (Brute-Force-Schutz,
  5 Min. Sperre nach 5 Fehlversuchen), **CSRF-Schutz** für alle schreibenden
  Requests, **Rate-Limit** je IP (Standard 600 Requests/Min., strenger für den
  Login), **Sicherheits-Header** (CSP, HSTS, X-Frame-Options, Referrer-Policy …)
  und ein Sitzungs-Cookie mit `SameSite=Strict` und 12 Stunden Laufzeit. Alles
  über `.env` einstellbar (siehe `.env.example`).
- **Dienstbenutzer statt root** — die Setup-Skripte legen den Host-Benutzer
  `rechnung` an und übergeben Dateien an ihn; der `web`-Container läuft unter
  derselben UID/GID (`APP_UID`/`APP_GID`), nicht als root.
- **Logging** — jede Anfrage bekommt eine Request-ID; Logzeilen sind JSON und
  jede Fehlermeldung enthält die ID, sodass ein Screenshot direkt zum Logeintrag
  führt (`docker compose logs web`).
- **Live-Anzeige „jemand ist auch hier"** — hat ein Kollege denselben Beleg
  offen, steht das als Hinweis über dem Formular; in den Listen markiert ein
  👀 die Belege, an denen gerade jemand sitzt. Ergänzt die Bearbeitungssperre
  (die nur das gleichzeitige Speichern verhindert) und gilt auch für Angebote
  und Lieferscheine, die gar keine Sperre haben.
- **Entwurfs-Speicher** — begonnene Rechnungen, Angebote und Lieferscheine
  überleben ein Neuladen der Seite: der Entwurf liegt lokal im Browser
  (localStorage, 7 Tage) und wird beim Öffnen wieder eingesetzt. Es geht nichts
  davon an den Server.
- **Suchen & Filtern** — Such- und Filterfelder in allen Listen (Rechnungen,
  Angebote, Lieferscheine, Kunden, Artikel, Benutzer, Audit-Log) und in der
  Kundenauswahl beim Anlegen eines Belegs.
- **HTTPS** — der Proxy bedient zusätzlich Port 443 (selbstsigniertes Zertifikat per Default,
  optional echtes Let's-Encrypt-Zertifikat über `scripts/setup-prod.sh`).
- **Datenbank** — alles wird in PostgreSQL gespeichert.

## Technik

| Komponente | Technologie |
|------------|-------------|
| Backend    | Python, FastAPI, SQLAlchemy |
| Datenbank  | PostgreSQL 16 (Container) |
| PDF        | reportlab + qrcode (GiroCode/EPC-QR) |
| Frontend   | HTML / CSS / Vanilla JS (von FastAPI ausgeliefert) |
| Login      | Sitzungs-Cookie (Starlette SessionMiddleware), PBKDF2-Passworthash |
| E-Mail     | SMTP; standardmäßig an MailHog-Testserver, in Produktion konfigurierbar |
| Proxy      | nginx (Reverse-Proxy nach Hostname, HTTP + HTTPS) |
| Backup     | postgres `pg_dump` (täglich, in `./backups`, 14 Tage Aufbewahrung) |
| Betrieb    | Docker Compose (5 Container: `web` + `db` + `mailhog` + `proxy` + `backup`), `web` läuft als Benutzer `rechnung` |
| Sicherheit | CSRF-Token je Sitzung, Rate-Limit je IP, CSP/HSTS/X-Frame-Options, PBKDF2 |
| Tests / CI | pytest (139 Backend-Tests) + jsdom (27 Frontend-Tests), GitHub Actions |

## Starten

### Schnellstart über die Setup-Skripte

Für eine schnelle Test-Instanz mit selbstsigniertem Zertifikat und zufälligen
Zugangsdaten:

```bash
./scripts/setup-test.sh
```

Für eine geführte Produktions-Einrichtung (starke Geheimnisse, optional echter
SMTP-Versand und ein Let's-Encrypt-Zertifikat via certbot):

```bash
./scripts/setup-prod.sh
docker compose up -d --build
```

### Manuelles Setup

Vor der ersten Inbetriebnahme ein lokales Zertifikat erstellen:
```
cd nginx/
mkdir -p certs
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout certs/localhost.key \
  -out certs/localhost.crt \
  -subj "/CN=localhost"
```

Voraussetzung: Docker & Docker Compose.

```bash
cp .env.example .env
docker compose up --build
```

Danach im Browser öffnen (über den Reverse-Proxy):

| Adresse | Ziel |
|---------|------|
| **http://rechnungen.localhost** | Rechnungs-App |
| **http://mail.localhost** | MailHog (versendete E-Mails) |
| **https://rechnungen.localhost** | App über HTTPS (selbstsigniert – Browserwarnung bestätigen) |

Die direkten Ports bleiben zusätzlich erreichbar: App auf `http://localhost:8000`,
MailHog auf `http://localhost:8025`.

> Die meisten Browser lösen `*.localhost` automatisch auf 127.0.0.1 auf. Falls bei
> dir nicht, ergänze in `/etc/hosts`:
> `127.0.0.1  rechnungen.localhost mail.localhost`
>
> Der Proxy belegt **Port 80**. Lief dort schon etwas (z. B. Traefik), muss es
> gestoppt werden oder du setzt `PROXY_PORT` in `.env` auf einen freien Port.

**Anmeldung** (Standard-Benutzer, in `.env` änderbar über `APP_USERS`):

| Benutzer | Passwort | Rolle |
|----------|----------|-------|
| `admin`  | `admin`     | Administrator (Benutzerverwaltung, Firma, Audit-Log, Backup) |
| `anna`   | `passwort` | Benutzer |
| `bernd`  | `passwort` | Benutzer |
| `clara`  | `passwort` | Benutzer |

> Benutzer werden nur beim **ersten Start** aus `.env` (`APP_USERS`, `ADMIN_USER`,
> `ADMIN_PASSWORD`) in die Datenbank übernommen. Danach erfolgt die Verwaltung über
> den Admin-Account in der App. **Passwörter in Produktion unbedingt ändern!**
> (`scripts/setup-prod.sh` erledigt das automatisch mit starken Zufallswerten.)

Versendete E-Mails ansehen: **http://localhost:8025** (MailHog). Für echten
Versand siehe `scripts/setup-prod.sh` bzw. `SMTP_*`/`MAIL_FROM` in `.env`.

Konfiguration (Ports, DB-Zugangsdaten, Benutzer, SMTP) in der Datei `.env`.

## Tests

Die Testsuite läuft ohne Container gegen eine temporäre SQLite-Datenbank:

```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest
```

Abgedeckt sind der Lebenszyklus von Rechnungen, Angeboten und Lieferscheinen,
Kunden/Artikel, die Admin-Endpunkte samt Audit-Log sowie die Sicherheitsschicht
selbst (CSRF, Rate-Limit, Header, Login-Sperre).

Die Oberfläche hat eine eigene Testsuite: sie lädt die echte `index.html` samt
`app.js` in ein jsdom-Fenster und prüft Kundenauswahl, Entwurfs-Speicher,
Such-/Filterfelder und den CSRF-Header (Node ≥ 20 nötig):

```bash
cd backend/tests/frontend
npm install
npm test
```

Beide Suiten plus Syntax-Prüfungen und ein Image-Build laufen bei jedem Push
über `.github/workflows/ci.yml`.

## API-Überblick

> Alle Listen-Endpunkte akzeptieren optional `?limit=` (max. 500) und
> `?offset=`; ohne `limit` kommt weiterhin die vollständige Liste. Die
> Gesamtzahl steht immer im Header `X-Total-Count`.
>
> Schreibende Requests (`POST`/`PUT`/`PATCH`/`DELETE`) brauchen den Header
> `X-CSRF-Token`. Den passenden Wert liefert `GET /api/me` bzw. das Cookie
> `csrftoken`.

### Rechnungen
| Methode | Pfad | Zweck |
|---------|------|-------|
| `GET`   | `/api/invoices?search=` | History / Liste |
| `POST`  | `/api/invoices` | Rechnung anlegen |
| `GET`   | `/api/invoices/{id}` | Einzelne Rechnung |
| `PUT`   | `/api/invoices/{id}` | Rechnung bearbeiten |
| `GET`   | `/api/invoices/{id}/lock` | Bearbeitungssperre abfragen |
| `POST`  | `/api/invoices/{id}/lock` | Bearbeitungssperre setzen/erneuern |
| `DELETE`| `/api/invoices/{id}/lock` | Bearbeitungssperre freigeben |
| `PATCH` | `/api/invoices/{id}/status` | Status setzen (offen/bezahlt/storniert) |
| `DELETE`| `/api/invoices/{id}` | Löschen |
| `POST`  | `/api/invoices/{id}/payment` | Zahlungseingang (auch Teilzahlung) erfassen |
| `GET`   | `/api/invoices/{id}/pdf` | PDF herunterladen |
| `POST`  | `/api/invoices/{id}/email` | Rechnung als PDF per E-Mail senden |
| `POST`  | `/api/invoices/{id}/reminder` | Zahlungserinnerung (Mahnung) per E-Mail |
| `POST`  | `/api/invoices/{id}/convert-to-delivery-note` | In Lieferschein umwandeln |
| `GET`   | `/api/export?month=JJJJ-MM` | Alle Rechnungen eines Monats als ZIP |

### Angebote
| Methode | Pfad | Zweck |
|---------|------|-------|
| `GET`   | `/api/quotes` | Liste |
| `POST`  | `/api/quotes` | Angebot anlegen |
| `GET`   | `/api/quotes/{id}` | Einzelnes Angebot |
| `PUT`   | `/api/quotes/{id}` | Angebot bearbeiten |
| `PATCH` | `/api/quotes/{id}/status` | Status setzen |
| `DELETE`| `/api/quotes/{id}` | Löschen |
| `GET`   | `/api/quotes/{id}/pdf` | PDF herunterladen |
| `POST`  | `/api/quotes/{id}/email` | Per E-Mail senden |
| `POST`  | `/api/quotes/{id}/convert` | In Rechnung umwandeln |

### Lieferscheine
| Methode | Pfad | Zweck |
|---------|------|-------|
| `GET`   | `/api/delivery-notes` | Liste |
| `POST`  | `/api/delivery-notes` | Lieferschein anlegen |
| `GET`   | `/api/delivery-notes/{id}` | Einzelner Lieferschein |
| `PUT`   | `/api/delivery-notes/{id}` | Bearbeiten |
| `PATCH` | `/api/delivery-notes/{id}/status` | Status setzen |
| `DELETE`| `/api/delivery-notes/{id}` | Löschen |
| `GET`   | `/api/delivery-notes/{id}/pdf` | PDF herunterladen |
| `POST`  | `/api/delivery-notes/{id}/email` | Per E-Mail senden |

### Kunden, Artikel, Dashboard, Einstellungen
| Methode | Pfad | Zweck |
|---------|------|-------|
| `GET`   | `/api/stats` | Dashboard-Kennzahlen |
| `GET`   | `/api/customers?active_only=` | Stammkunden auflisten |
| `POST`  | `/api/customers` | Stammkunde anlegen |
| `PUT`   | `/api/customers/{id}` | Stammkunde bearbeiten |
| `PATCH` | `/api/customers/{id}/active` | Aktiv/inaktiv setzen |
| `DELETE`| `/api/customers/{id}` | Stammkunde löschen |
| `GET`   | `/api/customers/{id}/export` | DSGVO Art. 15 Datenauskunft (nur Admin) |
| `POST`  | `/api/customers/{id}/anonymize` | DSGVO Art. 17 Anonymisierung (nur Admin) |
| `GET`   | `/api/products?active_only=` | Artikel/Leistungen auflisten |
| `POST`  | `/api/products` | Artikel/Leistung anlegen |
| `PUT`   | `/api/products/{id}` | Artikel/Leistung bearbeiten |
| `PATCH` | `/api/products/{id}/active` | Aktiv/inaktiv setzen |
| `DELETE`| `/api/products/{id}` | Artikel/Leistung löschen |
| `GET`/`PUT` | `/api/settings` | Firmendaten lesen/speichern (Schreiben nur Admin) |
| `POST`/`GET` | `/api/settings/logo` | Logo hochladen (nur Admin)/abrufen |

### Live-Anzeige (wer hat gerade was offen)
| Methode | Pfad | Zweck |
|---------|------|-------|
| `POST`  | `/api/presence/{typ}/{id}` | Lebenszeichen; liefert die *anderen* auf diesem Beleg |
| `DELETE`| `/api/presence/{typ}/{id}` | Beleg verlassen (verfällt sonst nach 45 s von selbst) |
| `GET`   | `/api/presence` | Alle Belege, auf denen gerade jemand anderes sitzt |

`{typ}` ist `invoice`, `quote` oder `delivery_note`. Das Frontend meldet sich
alle 10 Sekunden, solange ein Beleg im Formular offen ist.

### Benutzer, Audit-Log, Backups (nur Admin)
| Methode | Pfad | Zweck |
|---------|------|-------|
| `GET`   | `/api/me` | Angemeldeten Benutzer + Admin-Status abfragen |
| `GET`   | `/api/users` | Benutzer auflisten |
| `POST`  | `/api/users` | Benutzer anlegen |
| `POST`  | `/api/users/{id}/password` | Passwort zurücksetzen |
| `DELETE`| `/api/users/{id}` | Benutzer löschen |
| `GET`   | `/api/audit-log` | DSGVO-Protokoll einsehen |
| `GET`   | `/api/admin/backups` | Vorhandene Backups auflisten |
| `GET`   | `/api/admin/backups/{name}/download` | Backup herunterladen |
| `POST`  | `/api/admin/backups/{name}/restore` | Backup einspielen (überschreibt die DB!) |

## Stoppen

```bash
docker compose down          # Container stoppen
docker compose down -v       # inkl. Datenbank-Volume löschen
```
