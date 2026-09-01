# 🔧 Troubleshooting

Sammlung der Fehlerbilder, die beim Betrieb der Rechnungs-App tatsächlich
auftreten – jeweils mit Ursache und konkretem Befehl.

Erste Anlaufstelle ist immer:

```bash
docker compose ps                 # welcher Container läuft (nicht)?
docker compose logs -f app        # Anwendungs-Log (JSON, eine Zeile je Request)
tail -f logs/app/app.log          # dasselbe Log als Datei auf der Platte
```

Jede Antwort der App trägt den Header `X-Request-ID`, und jede Fehlermeldung
enthält dieselbe ID im Feld `request_id`. Damit lässt sich ein Screenshot
direkt im Log wiederfinden:

```bash
docker compose logs app | grep 9a23489ad116
grep 9a23489ad116 logs/app/app.log      # oder direkt in der Logdatei
```

### Wo liegen die Logs?
Jeder Container schreibt zusätzlich zum Docker-Journal in ein eigenes
Verzeichnis unter `./logs/`:

```
logs/app/app.log                   Anwendung (JSON)
logs/web/access.log, error.log     nginx vor der App
logs/proxy/access.log, error.log   nginx ganz aussen
logs/db/postgresql-*.log           PostgreSQL
logs/mailhog/mailhog.log
logs/backup/backup.log
```

Fehlt ein Verzeichnis oder bleibt eine Datei leer, hat der Container kein
Schreibrecht. Nachziehen mit `scripts/prepare-logs.sh` und den betroffenen
Container neu starten. Die App hält das nicht auf – sie meldet
`log directory not writable` und loggt weiter auf die Standardausgabe.

### Mehr oder weniger im Log sehen
Die Stufe der App lässt sich im Reiter „Monitoring" unter „Log-Stufe"
umschalten (DEBUG/INFO/WARNING/ERROR). Sie wirkt sofort und übersteht einen
Neustart. Die übrigen Container ziehen ihre Stufe beim Start aus `LOG_LEVEL`
bzw. `PG_LOG_LEVEL` in der `.env` – dort ändern und neu starten.

---

## Anmeldung

### „Benutzername oder Passwort falsch“, obwohl beides stimmt
Die Zugangsdaten aus `.env` (`ADMIN_USER`, `ADMIN_PASSWORD`, `APP_USERS`)
werden **nur beim allerersten Start** in die Datenbank übernommen
(`crud.seed_users` legt nur an, wenn die Tabelle `users` leer ist). Wer die
`.env` später ändert, ändert damit kein bestehendes Passwort.

```bash
# Passwort eines Benutzers zurücksetzen (als Admin in der Oberfläche:
# Reiter „Benutzer“ -> 🔑). Ohne Admin-Zugang bleibt nur der Neuaufbau der DB:
docker compose down -v && docker compose up -d --build   # ACHTUNG: löscht alle Daten
```

### „Zu viele Fehlversuche. Bitte in ca. X Minuten erneut versuchen.“
Brute-Force-Schutz: 5 Fehlversuche je (IP + Benutzername) sperren für 5
Minuten. Die Sperre liegt nur im Arbeitsspeicher – ein Neustart des
`web`-Containers hebt sie sofort auf:

```bash
docker compose restart web
```

### „Die Sitzung ist abgelaufen. Bitte erneut anmelden.“ (`/login?csrf=1`)
Das CSRF-Token aus dem Formular passt nicht zur Sitzung. Übliche Ursachen:

* Die Login-Seite lag lange offen und die Sitzung ist abgelaufen
  (`SESSION_MAX_AGE`, Standard 12 Stunden) -> einfach neu laden.
* `SESSION_HTTPS_ONLY=true`, aber der Zugriff erfolgt über **http**. Dann
  schickt der Browser das Cookie nicht mit. Entweder https benutzen oder für
  eine reine Testumgebung `SESSION_HTTPS_ONLY=false` setzen.
* Cookies sind im Browser blockiert.

### Login funktioniert, aber jede Aktion meldet 403 „CSRF-Token fehlt“
Das Frontend liest das Token aus dem Cookie `csrftoken`. Fehlt das Cookie,
läuft der Zugriff meist über einen Proxy, der Cookies verschluckt, oder die
Seite wurde aus dem Cache geladen. Einmal hart neu laden (Strg+Shift+R). Zum
Ausschließen der Ursache lässt sich der Schutz temporär abschalten:

```bash
# nur zur Fehlersuche, nie dauerhaft!
echo "CSRF_ENABLED=false" >> .env && docker compose up -d web
```

### Nach dem Login sofort wieder auf der Login-Seite
`SESSION_SECRET` ändert sich bei jedem Start (z. B. weil die Variable nicht
gesetzt ist und ein Zufallswert verwendet wird) -> alle Sitzungs-Cookies
werden ungültig. Einen festen Wert in `.env` hinterlegen:

```bash
grep SESSION_SECRET .env    # muss einen festen, langen Wert enthalten
```

---

## HTTP-Fehler

### 429 „Zu viele Anfragen“
Das Rate-Limit greift (Standard: 600 Requests je IP und Minute, für
`POST /login` 20 je 5 Minuten). Hinter einem Proxy, der **kein**
`X-Forwarded-For` setzt, zählen alle Benutzer auf dieselbe IP – dann entweder
den Proxy korrigieren (das mitgelieferte `nginx/default.conf` setzt den
Header) oder die Grenzen anheben:

```bash
# in .env
RATE_LIMIT_REQUESTS=2000
RATE_LIMIT_WINDOW=60
# 0 schaltet das Limit ganz ab
```

### 409 „Wird gerade von … bearbeitet“
Die Bearbeitungssperre eines Belegs – gilt für Rechnungen, Angebote und
Lieferscheine gleichermaßen. Sie läuft 5 Minuten nach der letzten Aktivität
automatisch ab. Wer den Tab einfach schließt, hält sie also höchstens
5 Minuten. Sofort freigeben (als Admin, direkt in der DB; Tabelle je nach
Belegart `invoices`, `quotes` oder `delivery_notes`):

```bash
docker exec -it rechnung_db psql -U rechnung -d rechnung \
  -c "UPDATE invoices SET locked_by = NULL, locked_at = NULL WHERE number = 'RE-2026-0001';"
# analog für ein Angebot: UPDATE quotes SET locked_by = NULL, ... WHERE number = 'AN-...'
# oder einen Lieferschein: UPDATE delivery_notes SET locked_by = NULL, ... WHERE number = 'LS-...'
```

### 500 „Interner Serverfehler“
Die Antwort enthält eine `request_id`. Damit den Traceback im Log suchen:

```bash
docker compose logs app | grep '"level": "ERROR"' | tail -5
grep '"level": "ERROR"' logs/app/app.log | tail -5
```

### 502 „E-Mail konnte nicht gesendet werden“
Siehe Abschnitt *E-Mail*.

---

## Oberfläche / Browser

### Seite bleibt weiß, in der Konsole steht „Refused to execute inline script“
Die Content-Security-Policy erlaubt keine Inline-Skripte und keine
Inline-Styles. Wer die Oberfläche erweitert, muss JavaScript in eine Datei
unter `backend/app/static/` auslegen und Styles über CSS-Klassen bzw.
`element.style.xyz = …` setzen (das ist erlaubt, ein `style="…"`-Attribut im
Markup nicht). Die Policy steht in `backend/app/security.py::_CSP`.

### Änderungen an app.js / styles.css kommen nicht an
Browser-Cache. Strg+Shift+R, oder den Container neu starten – die Dateien
werden ins Image kopiert, ein `docker compose up -d` ohne `--build` liefert
also weiterhin den alten Stand:

```bash
docker compose up -d --build app
```

### „Wiederhergestellt: nicht gespeicherte Eingaben vom …“ – wie werde ich das los?
Das ist der Entwurfs-Zwischenspeicher: nicht abgeschickte Formulareingaben
überstehen ein Neuladen. Der Knopf **„Entwurf verwerfen“** neben dem Hinweis
leert Formular und Speicher. Entwürfe liegen ausschließlich im Browser
(`localStorage`, Schlüssel `rechnung.drafts.v1`) und verfallen nach 7 Tagen.

### „👀 anna hat diesen Beleg gerade ebenfalls geöffnet“ – stimmt das noch?
Die Anzeige beruht auf einem Lebenszeichen alle 10 Sekunden. Wer den Tab hart
schließt, verschwindet erst nach 45 Sekunden aus der Anzeige. Bleibt ein
Eintrag darüber hinaus stehen, hat der Browser noch eine offene Seite (z. B.
in einem anderen Fenster). Nachsehen:

```bash
docker exec -it rechnung_db psql -U rechnung -d rechnung \
  -c "SELECT doc_type, doc_id, username, last_seen FROM presence ORDER BY last_seen DESC;"
```

Die Tabelle ist reiner Laufzeitzustand – sie lässt sich jederzeit gefahrlos
leeren (`DELETE FROM presence;`).

### Der gespeicherte Kunde taucht in der Auswahl nicht auf
In der Auswahlliste stehen nur **aktive** Kunden. Deaktivierte Kunden
erscheinen weiter im Reiter „Kunden“ (Filter „nur inaktive“) und lassen sich
dort mit ▶️ wieder aktivieren.

---

## Container & Datenbank

### `web` startet nicht: „connection to server at "db" … failed“
Die Datenbank war noch nicht bereit. `init_db()` wartet 10 × 2 Sekunden;
dauert der DB-Start länger, hilft ein Neustart:

```bash
docker compose restart web
docker compose logs db | tail -20
```

### `permission denied` beim Schreiben in `./backups`
Nach `scripts/setup-prod.sh` gehören die Dateien dem Dienstbenutzer
`rechnung`, und der `web`-Container läuft unter dessen UID
(`APP_UID`/`APP_GID` in `.env`). Wer die Dateien manuell anfasst, sollte das
als dieser Benutzer oder über die Gruppe tun:

```bash
id rechnung                                  # UID/GID prüfen
grep APP_ .env                               # muss dazu passen
sudo chown -R rechnung:rechnung .            # Eigentümer zurücksetzen
sudo usermod -aG rechnung "$USER"            # eigenen Account in die Gruppe (neu anmelden!)
```

### Container läuft als root, obwohl er es nicht soll
Prüfen:

```bash
docker compose exec app id      # erwartet: uid=10001(rechnung) …
```

Steht dort `uid=0(root)`, fehlt `APP_UID`/`APP_GID` in der `.env` **und** der
`user:`-Eintrag in `docker-compose.yml` wurde entfernt. Neu bauen:

```bash
docker compose up -d --build app
```

### Backup-Wiederherstellung schlägt fehl
Der Restore ruft `gunzip -c <datei> | psql "$DATABASE_URL"` auf. Häufigste
Ursache ist ein Dump aus einer anderen Datenbank oder ein abgebrochener
`pg_dump` (0 Byte). Prüfen:

```bash
ls -la backups/
gunzip -t backups/rechnung_20260101_020000.sql.gz && echo "Archiv ok"
cat backups/last_error.log
```

### Alles zurücksetzen (löscht alle Daten!)
```bash
docker compose down -v
docker compose up -d --build
```

---

## E-Mail

### Mails kommen nicht an
Standardmäßig läuft der Versand gegen **MailHog** – das ist ein Testserver,
der Mails annimmt und nur anzeigt, aber nichts zustellt:

```
http://localhost:8025      (oder http://mail.localhost über den Proxy)
```

Für echten Versand `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` und
`SMTP_USE_TLS=true` setzen – `scripts/setup-prod.sh` fragt das ab.

### 502 „E-Mail konnte nicht gesendet werden: …“
Der SMTP-Server war nicht erreichbar oder hat die Anmeldung abgelehnt. Die
Originalmeldung steht in der Antwort und im Log. Von Hand testen:

```bash
docker compose exec app python -c "
import smtplib, os
s = smtplib.SMTP(os.getenv('SMTP_HOST'), int(os.getenv('SMTP_PORT')))
s.ehlo(); print(s.esmtp_features); s.quit()"
```

Wichtig: Ein fehlgeschlagener Versand verhindert **nie** das Speichern eines
Belegs – die automatische Mail beim Anlegen einer Rechnung ist bewusst
„best effort“.

---

## Zertifikate

### Browser warnt vor unsicherer Verbindung
Standard ist ein selbstsigniertes Zertifikat. Für ein echtes:

```bash
./scripts/setup-prod.sh        # fragt nach der Domain und ruft certbot auf
```

### Let's-Encrypt-Zertifikat ist abgelaufen
Zertifikate laufen nach 90 Tagen ab; der Proxy nutzt eine **Kopie** unter
`nginx/certs/`. Nach der Erneuerung muss die Kopie aktualisiert werden:

```bash
sudo certbot renew
sudo cp /etc/letsencrypt/live/<domain>/fullchain.pem nginx/certs/localhost.crt
sudo cp /etc/letsencrypt/live/<domain>/privkey.pem  nginx/certs/localhost.key
docker compose restart proxy
```

---

## Tests schlagen fehl

```bash
cd backend && pip install -r requirements-dev.txt && python -m pytest
```

Die Testsuite läuft gegen eine temporäre SQLite-Datei, **nicht** gegen die
laufende PostgreSQL-Instanz – ein laufender Container ist also weder nötig
noch störend. Schlägt bereits das Importieren fehl, fehlen meist die
Abhängigkeiten aus `requirements-dev.txt`.

Die Frontend-Tests brauchen zusätzlich Node ≥ 20:

```bash
cd backend/tests/frontend && npm install && npm test
```
