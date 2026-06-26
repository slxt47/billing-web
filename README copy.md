# 🧾 Rechnungs-App

Eine Web-Applikation zum Schreiben von Rechnungen — komplett in Docker-Containern,
mit PostgreSQL-Datenbank, History, PDF-Download und Storno-/Rückfunktion.

## ⚠️ Project Status: Not Production Ready

Please note that this web application is experimental and **not production-ready**. It has not been fully tested for edge cases or security vulnerabilities, and bugs are to be expected. Do not use this in a live or production environment.

🤖 **Development:** The code for this project was written and generated with the assistance of a **Mistral AI Agent**.


## Funktionen

- **Rechnungen erstellen** — Kunde, Adresse, Positionen, MwSt., Hinweise; Summen
  werden live berechnet, Rechnungsnummern (`RE-<Jahr>-0001`) automatisch vergeben.
  Das **Rechnungsdatum ist immer der heutige Tag**.
- **Standard-MwSt. 20 %** voreingestellt; Datumsanzeige im Format `TT/MM/JJJJ`.
- **Stammkunden** — häufige Kunden mit Anschrift, **Standard-Zahlungsfrist** und
  optionaler **Skonto-Vorgabe** anlegen; bei Auswahl werden Fälligkeitsdatum und
  Skonto automatisch gesetzt.
- **Skonto** — pro Rechnung bei Bedarf ein Skonto („X % bei Zahlung innerhalb Y
  Tagen"); erscheint mit Frist, Betrag und reduziertem Zahlbetrag auf dem PDF.
- **Artikel/Leistungen** — wiederkehrende Posten mit Standardpreis hinterlegen; beim
  Erstellen per Auswahl übernehmen (Preis wird gesetzt) oder weiterhin frei eintippen.
- **History** — alle Rechnungen mit Suche nach Nummer oder Kunde.
- **PDF-Download** — jede Rechnung als PDF herunterladen.
- **Monats-Export** — alle Rechnungen eines Monats als ZIP (je ein PDF plus eine
  CSV-Übersicht) herunterladen.
- **Rückfunktion / Storno** — Rechnung stornieren und per „zurück" wieder auf
  *offen* setzen; als *bezahlt* markieren; endgültig löschen.
- **Anmeldung** — Login mit mehreren Benutzern, die sich **dieselben Daten teilen** und
  **gleichzeitig** Rechnungen erstellen können (Nummernvergabe per DB-Lock kollisionsfrei).
- **Benutzerverwaltung** — ein **Admin-Account** kann Benutzer anlegen, löschen und
  Passwörter zurücksetzen (Reiter „Benutzer", nur für Admins sichtbar). Passwörter
  werden als PBKDF2-Hash gespeichert.
- **E-Mail-Versand** — Rechnung als PDF per E-Mail verschicken; im Test über einen
  **MailHog-Container** (Web-UI unter http://localhost:8025, kein echter Versand).
- **Firmendaten & Logo** — Absender, Steuernummer/USt-IdNr., IBAN/BIC und Logo
  erscheinen auf dem PDF (Reiter „Firma", nur Admin).
- **GiroCode/EPC-QR** — QR-Code aufs PDF; Kunde scannt und die Überweisung ist
  vorausgefüllt (sobald IBAN hinterlegt ist).
- **Rabatt & Kleinunternehmer** — Rabatt in % auf die Zwischensumme; §19-UStG-Modus
  ohne MwSt.
- **Zahlungen** — Zahlungseingänge (auch Teilzahlungen) erfassen; Status wird
  automatisch *teilbezahlt*/*bezahlt*; offener Betrag wird ausgewiesen.
- **Mahnwesen** — überfällige Rechnungen werden erkannt und farblich markiert;
  Zahlungserinnerung per E-Mail versendbar.
- **Dashboard** — Kennzahlen (Umsatz, offen, überfällig) und ein Umsatz-Diagramm
  der letzten 6 Monate.
- **History-Filter** — nach Status/überfällig filtern und Spalten sortieren.
- **Hell-/Dunkel-Modus** — umschaltbar, Auswahl wird gespeichert.
- **Sicherheit** — Login-Sperre nach zu vielen Fehlversuchen (Brute-Force-Schutz).
- **Automatische Backups** — täglicher `pg_dump` (gzip) in `./backups`, hält die
  letzten 14 Sicherungen.
- **HTTPS** — der Proxy bedient zusätzlich Port 443 (selbstsigniertes Zertifikat).
- **Datenbank** — alles wird in PostgreSQL gespeichert.

## Technik

| Komponente | Technologie |
|------------|-------------|
| Backend    | Python, FastAPI, SQLAlchemy |
| Datenbank  | PostgreSQL 16 (Container) |
| PDF        | reportlab |
| Frontend   | HTML / CSS / Vanilla JS (von FastAPI ausgeliefert) |
| Login      | Sitzungs-Cookie (Starlette SessionMiddleware) |
| E-Mail     | SMTP an MailHog-Testserver |
| PDF/QR     | reportlab + qrcode (GiroCode) |
| Proxy      | nginx (Reverse-Proxy nach Hostname, HTTP + HTTPS) |
| Backup     | postgres `pg_dump` (täglich, in `./backups`) |
| Betrieb    | Docker Compose (5 Container: `web` + `db` + `mailhog` + `proxy` + `backup`) |

## Starten

### Vor der ersten Inbertiebnahme ein lokales Zertifikat erstellen
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
| `admin`  | `admin`     | Administrator (Benutzerverwaltung) |
| `anna`   | `passwort` | Benutzer |
| `bernd`  | `passwort` | Benutzer |
| `clara`  | `passwort` | Benutzer |

> Benutzer werden nur beim **ersten Start** aus `.env` (`APP_USERS`, `ADMIN_USER`,
> `ADMIN_PASSWORD`) in die Datenbank übernommen. Danach erfolgt die Verwaltung über
> den Admin-Account in der App. **Passwörter in Produktion unbedingt ändern!**

Versendete E-Mails ansehen: **http://localhost:8025** (MailHog).

Konfiguration (Ports, DB-Zugangsdaten, Benutzer, SMTP) in der Datei `.env`.

## API-Überblick

| Methode | Pfad | Zweck |
|---------|------|-------|
| `GET`   | `/api/invoices?search=` | History / Liste |
| `POST`  | `/api/invoices` | Rechnung anlegen |
| `GET`   | `/api/invoices/{id}` | Einzelne Rechnung |
| `PATCH` | `/api/invoices/{id}/status` | Status setzen (offen/bezahlt/storniert) |
| `DELETE`| `/api/invoices/{id}` | Löschen |
| `GET`   | `/api/invoices/{id}/pdf` | PDF herunterladen |
| `POST`  | `/api/invoices/{id}/email` | Rechnung als PDF per E-Mail senden |
| `POST`  | `/api/invoices/{id}/reminder` | Zahlungserinnerung (Mahnung) per E-Mail |
| `POST`  | `/api/invoices/{id}/payment` | Zahlungseingang (auch Teilzahlung) erfassen |
| `GET`   | `/api/export?month=JJJJ-MM` | Alle Rechnungen eines Monats als ZIP |
| `GET`   | `/api/stats` | Dashboard-Kennzahlen |
| `GET`/`PUT` | `/api/settings` | Firmendaten lesen/speichern |
| `POST`/`GET` | `/api/settings/logo` | Logo hochladen/abrufen |
| `GET`   | `/api/customers` | Stammkunden auflisten |
| `POST`  | `/api/customers` | Stammkunde anlegen |
| `DELETE`| `/api/customers/{id}` | Stammkunde löschen |
| `GET`   | `/api/products` | Artikel/Leistungen auflisten |
| `POST`  | `/api/products` | Artikel/Leistung anlegen |
| `DELETE`| `/api/products/{id}` | Artikel/Leistung löschen |
| `GET`   | `/api/users` | Benutzer auflisten (nur Admin) |
| `POST`  | `/api/users` | Benutzer anlegen (nur Admin) |
| `POST`  | `/api/users/{id}/password` | Passwort zurücksetzen (nur Admin) |
| `DELETE`| `/api/users/{id}` | Benutzer löschen (nur Admin) |
| `GET`   | `/api/me` | Angemeldeten Benutzer + Admin-Status abfragen |

## Stoppen

```bash
docker compose down          # Container stoppen
docker compose down -v       # inkl. Datenbank-Volume löschen
```