# 🧾 Rechnungs-App

Eine Web-Applikation zum Schreiben von Rechnungen, Angeboten und Lieferscheinen —
komplett in Docker-Containern, mit PostgreSQL-Datenbank, History, PDF-Download,
E-Mail-Versand und DSGVO-Funktionen.

## ⚠️ Project Status: Not Production Ready

Please note that this web application is experimental and **not fully
production-hardened**. There is a guided production setup script
(`scripts/setup-prod.sh`, see below) that generates strong secrets and can wire
up real SMTP and a Let's-Encrypt certificate, but there is no automated test
suite, no CSRF protection, no security-header middleware and no rate limiting
beyond login brute-force protection. Review the code before using this with
real customer data.

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
- **Sicherheit** — Login-Sperre nach zu vielen Fehlversuchen (Brute-Force-Schutz, 5 Min. Sperre nach 5 Fehlversuchen).
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
| Betrieb    | Docker Compose (5 Container: `web` + `db` + `mailhog` + `proxy` + `backup`) |

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

## API-Überblick

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
