# 🧾 Rechnungs-App

Eine Web-Applikation zum Schreiben von Rechnungen, Angeboten und Lieferscheinen —
komplett in Docker-Containern, mit PostgreSQL-Datenbank, Rechnungsübersicht, PDF-Download,
E-Mail-Versand und DSGVO-Funktionen.

## ⚠️ Project Status: Not Production Ready

Please note that this web application is experimental and has **not been
audited by a third party**. It ships with a guided production setup script
(`scripts/setup-prod.sh`, see below) that generates strong secrets, creates a
dedicated non-root service user and can wire up real SMTP and a Let's-Encrypt
certificate. CSRF protection, per-IP rate limiting, security response headers
(CSP, HSTS, X-Frame-Options …), hardened session cookies, structured logging
and an automated test suite (211 Backend- + 73 Frontend-Tests) with CI are
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
- **Nach dem Speichern in die Rechnungsübersicht** — ob neu angelegt oder
  fertig bearbeitet: nach dem Speichern wechselt die App in die
  Rechnungsübersicht und bestätigt dort die Nummer.
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
  → `RE-2026-0007`). Danach landet man direkt in der Rechnungsübersicht, in der die
  neue Rechnung schon steht.
- **Lieferscheine** — reiner Liefernachweis (Beschreibung + Menge, ohne Preise),
  frei anlegbar oder direkt aus einer Rechnung erzeugt (`RE-2026-0007` →
  `LS-2026-0007`); als PDF herunterladen oder per E-Mail verschicken.
  Status: *offen*, *abgeschlossen* (grün) oder *storniert*. **Der PDF-Download
  schließt einen offenen Lieferschein ab** — wer ihn ausdruckt, gibt ihn aus der
  Hand. „wieder öffnen" setzt ihn bei Bedarf zurück auf *offen*; ein stornierter
  bleibt storniert. Aus einem
  Lieferschein lässt sich außerdem ein **Angebot** machen („zu Angebot"): Menge
  und Beschreibung kommen aus der Lieferung, die Preise – soweit die Position im
  Artikelstamm steht – aus den Standardpreisen, sonst 0 zum Nachtragen.
- **Keine doppelten Umwandlungen** — jeder Beleg lässt sich nur einmal
  umwandeln: aus einem Angebot entsteht genau eine Rechnung, aus einer
  Rechnung genau ein Lieferschein, aus einem Lieferschein genau ein Angebot.
  Gibt es den Folgebeleg schon, steht in der Liste statt des Knopfes dessen
  Nummer (z. B. `📦 LS-2026-0007`), und die API weist einen zweiten Versuch
  mit einem Hinweis auf den vorhandenen Beleg ab. Wird der Folgebeleg
  gelöscht, ist die Umwandlung wieder möglich.
- Angebote, Rechnungen und Lieferscheine teilen sich **eine fortlaufende
  Belegnummer pro Jahr**, damit eine Umwandlung die Nummer sauber weiterschiebt.

### Gutschriften
- **Gutschriften (Credit Notes)** — eigene Belegart (`GS-<Jahr>-0001`) mit
  Positionen, MwSt., Grund, PDF und E-Mail-Versand; sie teilt sich den
  fortlaufenden Nummernkreis mit Angebot, Rechnung und Lieferschein.
- **Voll- oder Teilgutschrift** — „Gutschrift" in der Rechnungsübersicht
  übernimmt Kunde und Positionen der Rechnung (Rabatt eingerechnet); einzelne
  Zeilen lassen sich streichen oder ändern, dann wird nur der Rest
  gutgeschrieben. Mehrere Gutschriften je Rechnung sind möglich, zusammen
  aber höchstens der offene Betrag — mehr weist die API ab.
- **Wirkung auf die Rechnung** — eine Gutschrift senkt den offenen Betrag
  (`remaining = Gesamt − Zahlungen − Gutschriften`) und den Umsatz im
  Dashboard. Der Rechnungsstatus wird dabei bewusst nicht automatisch auf
  „bezahlt" gedreht: gutgeschrieben ist nicht dasselbe wie bezahlt.
- **Status** — *offen*, *erstattet* oder *storniert*; eine stornierte
  Gutschrift zählt nirgends mehr mit.

### Auswertungen
- **Umsatzsteuer (UStVA-Grundlage)** — Netto, Umsatzsteuer und Brutto je
  Steuersatz für einen frei wählbaren Zeitraum, Gutschriften abgezogen,
  stornierte Belege ausgenommen; Kleinunternehmer landen im 0-%-Topf.
  Gerechnet wird nach Rechnungsdatum (Soll-Versteuerung).
- **Erlöse** — Kennzahlen (Erlös netto/brutto, Gutschriften, bezahlt, offen)
  sowie Aufstellungen je Monat und je Kunde.
- **CSV-Export** — beide Auswertungen als CSV (Semikolon, deutsche
  Dezimalkommas, BOM für Excel).
- ⚠️ **Nur die Erlösseite** — Ausgaben erfasst die App nicht, eine
  vollständige Gewinn-und-Verlust-Rechnung ist damit nicht möglich. Aus
  demselben Grund weist die UStVA-Auswertung keine Vorsteuer aus.

### Kunden & Artikel
- **Stammkunden** — häufige Kunden mit Anschrift, Ansprechpartner, E-Mail,
  **Standard-Zahlungsfrist** und optionaler **Skonto-Vorgabe** anlegen und bearbeiten;
  bei Auswahl werden Fälligkeitsdatum und Skonto automatisch gesetzt.
- **Artikel/Leistungen** — wiederkehrende Posten mit Standardpreis anlegen und
  bearbeiten; beim Erstellen per Auswahl übernehmen (Preis wird gesetzt) oder
  weiterhin frei eintippen.
- **Aktivieren/Deaktivieren** — Kunden und Artikel können statt gelöscht auch nur
  deaktiviert werden (bleiben in der Auswahl ausgeblendet, Rechnungsübersicht/alte
  Belege bleiben unberührt).
- **Kunden-Import (CSV oder JSON)** — eine bestehende Kundenliste lässt sich im
  Reiter „Kunden“ einlesen: CSV (Pflichtspalte `Name`, optional E-Mail,
  Ansprechpartner, Anschrift, Zahlungsfrist und Skonto; `;` oder `,` als
  Trennzeichen, UTF-8 oder Windows-1252) **oder JSON** — inklusive der Datei,
  die der Kunden-Export (📤, DSGVO Art. 15) ausgibt, sodass Export und Import
  zueinander passen. Gleiche Namen werden aktualisiert statt doppelt angelegt,
  fehlerhafte Datensätze einzeln gemeldet. Der Knopf öffnet direkt die
  Dateiauswahl, der Import startet mit der Auswahl. Eine Vorlage gibt es über
  „📄 Beispieldatei herunterladen“: der Knopf fragt in einem kleinen Fenster
  nach dem Format und liefert dann `kunden-vorlage.csv` oder
  `kunden-vorlage.json`.
- **Kundensuche mit Vorauswahl** — wer im Beleg-Formular nach einem Kunden
  sucht, bekommt den besten Treffer sofort ausgewählt und die Stammdaten
  übernommen; eine bereits getroffene Auswahl bleibt dabei stehen.

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
- **Eigene PDF-Vorlagen** — beliebig viele benannte Vorlagen (Layout, Akzent-
  und Kopffarbe, Schrift und -größe, Kopf- und Fußtext, Logo und GiroCode
  an/aus), eine davon als Vorgabe; anlegen und ändern dürfen Admins im Reiter „Firma",
  eine Vorschau als Musterrechnung gibt es je Vorlage. Gibt es mehr als eine,
  fragt jeder PDF-Download in einem kleinen Fenster nach der Vorlage
  (`?template=<id>`), sonst gilt die Vorgabe. Ohne jede Vorlage bleibt das
  gewohnte Standardaussehen.
- **Layout „Formular"** — zweites Layout einer Vorlage: der Firmenvordruck,
  1:1 nach dem alten Excel-Muster (Kopfbalken, Absender rechts oben,
  Ankreuzfelder für Angebot/Bestellung/Lieferschein/Rechnung, Zeile
  „Bestellung / Lieferdatum", Positionskasten mit Menge, Beschreibung,
  Einzelpreis und Euro, darunter Zwischensumme, Mehrwertsteuer und Endsumme).
  Firmenname, Anschrift, Bankverbindung und Steuernummer kommen aus den
  Firmendaten, die Akzentfarbe ist die Druckfarbe des Vordrucks, der Kopftext
  wird zur Branchenzeile, der Fußtext zum Kleingedruckten. Passt eine
  Rechnung nicht auf eine Seite, läuft der Kasten auf der nächsten weiter und
  die Summen stehen auf der letzten (`backend/app/pdf_form.py`).
- **Monitoring (nur Admin)** — Reiter „Monitoring" zeigt Laufzeit, Requests,
  Fehlerquote, Antwortzeiten, Fehlanmeldungen, Alter des jüngsten Backups,
  die letzten Serverfehler und den Bestand. Auf Wunsch aktualisiert sich die
  Ansicht selbst: „Automatisch" wählt 1, 2 oder 10 Sekunden bzw. eine Minute,
  die Wahl bleibt im Browser gespeichert. Der Takt läuft nur, solange die
  Ansicht offen und der Tab im Vordergrund ist. Dieselben Zahlen liefert
  `GET /api/admin/metrics` als JSON und `/api/admin/metrics.prom` im
  Prometheus-Textformat. Die Zähler leben im Prozess und starten mit ihm neu.
- **Alarm-Mails** — bei gehäuften Serverfehlern, auffällig vielen
  Fehlanmeldungen oder einem zu alten Backup geht eine Mail an die
  Firmen-E-Mail aus den Firmendaten. Schwellen, Beobachtungsfenster und
  Sperrfrist stehen in `.env` (`ALERT_*`); standardmäßig sind die Mails aus
  (`ALERTS_ENABLED=false`), `scripts/setup-prod.sh` schaltet sie ein. Ein
  Probealarm lässt sich in der Monitoring-Ansicht auslösen.
- **Automatische Backups** — täglicher `pg_dump` (gzip) in `./backups`, hält die
  letzten 14 Sicherungen.
- **Backup-Verwaltung im Admin-Bereich** — vorhandene Backups auflisten,
  herunterladen oder direkt über die Oberfläche in die laufende Datenbank
  zurückspielen (mit doppelter Sicherheitsabfrage, da destruktiv).
- **Dashboard** — Kennzahlen (Umsatz, offen, überfällig) und ein Umsatz-Diagramm
  der letzten 6 Monate; ist die Startseite nach dem Login.
- **Rechnungsübersicht** (früher „History“) — nach Status/überfällig filtern, nach
  Nummer/Kunde suchen und Spalten sortieren.
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
| Tests / CI | pytest (211 Backend-Tests) + jsdom (73 Frontend-Tests), GitHub Actions |

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
| `GET`   | `/api/invoices?search=` | Rechnungsübersicht / Liste |
| `POST`  | `/api/invoices` | Rechnung anlegen |
| `GET`   | `/api/invoices/{id}` | Einzelne Rechnung |
| `PUT`   | `/api/invoices/{id}` | Rechnung bearbeiten |
| `GET`   | `/api/invoices/{id}/lock` | Bearbeitungssperre abfragen |
| `POST`  | `/api/invoices/{id}/lock` | Bearbeitungssperre setzen/erneuern |
| `DELETE`| `/api/invoices/{id}/lock` | Bearbeitungssperre freigeben |
| `PATCH` | `/api/invoices/{id}/status` | Status setzen (offen/bezahlt/storniert) |
| `DELETE`| `/api/invoices/{id}` | Löschen |
| `POST`  | `/api/invoices/{id}/payment` | Zahlungseingang (auch Teilzahlung) erfassen |
| `GET`   | `/api/invoices/{id}/pdf?template=` | PDF herunterladen (optional mit Vorlage) |
| `POST`  | `/api/invoices/{id}/email` | Rechnung als PDF per E-Mail senden |
| `POST`  | `/api/invoices/{id}/reminder` | Zahlungserinnerung (Mahnung) per E-Mail |
| `POST`  | `/api/invoices/{id}/convert-to-delivery-note` | In Lieferschein umwandeln (nur einmal) |
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
| `POST`  | `/api/quotes/{id}/convert` | In Rechnung umwandeln (nur einmal) |

### Gutschriften
| Methode | Pfad | Zweck |
|---------|------|-------|
| `GET`   | `/api/credit-notes?search=` | Liste |
| `POST`  | `/api/credit-notes` | Gutschrift anlegen |
| `GET`   | `/api/credit-notes/{id}` | Einzelne Gutschrift |
| `PUT`   | `/api/credit-notes/{id}` | Bearbeiten |
| `PATCH` | `/api/credit-notes/{id}/status` | Status (offen/erstattet/storniert) |
| `DELETE`| `/api/credit-notes/{id}` | Löschen |
| `GET`   | `/api/credit-notes/{id}/pdf` | PDF herunterladen |
| `POST`  | `/api/credit-notes/{id}/email` | Per E-Mail senden |
| `POST`  | `/api/invoices/{id}/credit-note` | Gutschrift zur Rechnung (ohne `items` = Vollgutschrift) |

### Auswertungen
| Methode | Pfad | Zweck |
|---------|------|-------|
| `GET`   | `/api/reports/vat?from=&to=` | Umsatzsteuer je Satz (UStVA-Grundlage) |
| `GET`   | `/api/reports/vat.csv?from=&to=` | dieselbe Auswertung als CSV |
| `GET`   | `/api/reports/revenue?from=&to=` | Erlöse je Monat und Kunde |
| `GET`   | `/api/reports/revenue.csv?from=&to=` | dieselbe Auswertung als CSV |

### PDF-Vorlagen
| Methode | Pfad | Zweck |
|---------|------|-------|
| `GET`   | `/api/pdf-templates` | Liste (alle angemeldeten Benutzer) |
| `POST`  | `/api/pdf-templates` | Vorlage anlegen (Admin) |
| `PUT`   | `/api/pdf-templates/{id}` | Bearbeiten (Admin) |
| `POST`  | `/api/pdf-templates/{id}/default` | Als Vorgabe setzen (Admin) |
| `DELETE`| `/api/pdf-templates/{id}` | Löschen (Admin) |
| `GET`   | `/api/pdf-templates/{id}/preview` | Musterrechnung als PDF |

### Monitoring (Admin)
| Methode | Pfad | Zweck |
|---------|------|-------|
| `GET`   | `/api/admin/metrics` | Kennzahlen als JSON |
| `GET`   | `/api/admin/metrics.prom` | Kennzahlen im Prometheus-Textformat |
| `POST`  | `/api/admin/metrics/test-alert` | Probealarm an die Firmen-E-Mail |

### Lieferscheine
| Methode | Pfad | Zweck |
|---------|------|-------|
| `GET`   | `/api/delivery-notes` | Liste |
| `POST`  | `/api/delivery-notes` | Lieferschein anlegen |
| `GET`   | `/api/delivery-notes/{id}` | Einzelner Lieferschein |
| `PUT`   | `/api/delivery-notes/{id}` | Bearbeiten |
| `PATCH` | `/api/delivery-notes/{id}/status` | Status setzen (offen/abgeschlossen/storniert) |
| `DELETE`| `/api/delivery-notes/{id}` | Löschen |
| `GET`   | `/api/delivery-notes/{id}/pdf` | PDF herunterladen (setzt *offen* → *abgeschlossen*) |
| `POST`  | `/api/delivery-notes/{id}/email` | Per E-Mail senden |
| `POST`  | `/api/delivery-notes/{id}/convert-to-quote` | In Angebot umwandeln (nur einmal) |

### Kunden, Artikel, Dashboard, Einstellungen
| Methode | Pfad | Zweck |
|---------|------|-------|
| `GET`   | `/api/stats` | Dashboard-Kennzahlen |
| `GET`   | `/api/customers?active_only=` | Stammkunden auflisten |
| `POST`  | `/api/customers` | Stammkunde anlegen |
| `POST`  | `/api/customers/import` | Kunden aus CSV importieren (multipart, Feld `file`) |
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
