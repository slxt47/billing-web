================================================================================
RECHNUNGS-APP – ROADMAP (TODO.md)
================================================================================

Stand: 2026-08-31

KONVENTIONEN
------------
[X] = umgesetzt und im Code nachprüfbar. Dahinter stehen Datei und, wo es
      hilft, die Funktion, damit sich die Aussage in Sekunden prüfen lässt.
[ ] = offen.

Alles in dieser Datei ist deutsch. Neue Punkte kommen als [ ] in den
passenden Bereich unten – nicht als neuer Block ans Dateiende. Ist ein Punkt
erledigt, wird er dort auf [X] gesetzt und im CHANGELOG mit Datum vermerkt.


--------------------------------------------------------------------------------
1. SICHERHEIT & BETRIEB
--------------------------------------------------------------------------------
[X] PBKDF2-Passwort-Hashing (200.000 Iterationen, Salt je Benutzer)
[X] Login-Sperre gegen Brute Force (5 Fehlversuche -> 5 min Sperre, je IP
    und Benutzer)
[X] Geführtes Produktiv-Setup (scripts/setup-prod.sh): starke Geheimnisse,
    optional echtes SMTP, optional Let's-Encrypt-Zertifikat über certbot
[X] DSGVO: Datenschutzhinweis (/datenschutz)
[X] DSGVO: Datenexport je Kunde (Art. 15, nur Admin)
[X] DSGVO: Anonymisierung eines Kunden (Art. 17, nur Admin)
[X] DSGVO: Audit-Log über Zugriffe auf personenbezogene Daten
[X] Backup-Zugriff gegen Path Traversal geschützt (strenge Dateinamensprüfung)
[X] CSRF-Schutz (security.csrf_middleware): Token je Session, Pflicht im
    Header X-CSRF-Token bei jedem schreibenden Request; /login prüft
    stattdessen das Formularfeld csrf_token. Schalter: CSRF_ENABLED.
[X] Rate Limit für die API (security.rate_limit_middleware): gleitendes
    Fenster je IP, standardmäßig 600/60s, dazu ein strengerer Topf mit
    20/300s für POST /login; antwortet mit 429 und Retry-After.
    Konfigurierbar über RATE_LIMIT_* in .env.
[X] Security-Header (security.apply_security_headers): CSP,
    X-Content-Type-Options, X-Frame-Options, Referrer-Policy,
    Permissions-Policy, Cross-Origin-Opener-Policy, dazu HSTS bei HTTPS
[X] Gehärtetes Session-Cookie: SameSite=Strict, max_age (SESSION_MAX_AGE,
    12 h Vorgabe), secure-Flag über SESSION_HTTPS_ONLY (setzt
    scripts/setup-prod.sh); die Session wird beim Login geleert, gegen
    Session Fixation
[X] Der Dienst läuft nicht als root (backend/Dockerfile legt den Benutzer
    `rechnung` an und wechselt zu ihm; scripts/lib-common.sh legt den
    passenden Host-Benutzer an und übergibt ihm das Projektverzeichnis)
[X] Das Frontend maskiert Datenbankwerte vor der Ausgabe (app.js: esc()):
    HTML in einem Kundennamen oder einer Artikelbeschreibung kann die
    Tabelle nicht mehr aufbrechen, Positionszeilen werden über das DOM
    gefüllt


--------------------------------------------------------------------------------
2. KERNFUNKTIONEN
--------------------------------------------------------------------------------
[X] Serverseitige Validierung (Pydantic-Schemas für jeden Request-Body)
[X] Kollisionsfreie Belegnummern über Rechnungen, Angebote und
    Lieferscheine hinweg (gemeinsame Jahressequenz, PostgreSQL Advisory Lock)
[X] Bearbeitungssperre für Rechnungen, Angebote UND Lieferscheine
    (verhindert, dass zwei Personen denselben Beleg gleichzeitig bearbeiten,
    läuft nach 5 Minuten ohne Erneuerung automatisch ab). Ursprünglich nur für
    Rechnungen, seit Abschnitt 9 für alle drei Belegarten über dieselben
    crud.py-Funktionen (acquire_lock/release_lock/lock_status, Typ Lockable).
[X] Anwesenheitsanzeige („jemand anderes hat das offen“): ein Client mit
    offenem Beleg meldet sich alle 10 s per
    POST /api/presence/{doc_type}/{doc_id} und bekommt alle anderen auf
    diesem Beleg zurück; das Formular zeigt ein Banner, die Listen
    markieren die betroffenen Zeilen. Gilt für Rechnungen, Angebote UND
    Lieferscheine – ergänzt dort die Bearbeitungssperre um die Rückmeldung
    schon *während* jemand tippt, nicht erst beim Speichern.
    Einträge verfallen 45 s nach dem letzten Lebenszeichen, ein
    geschlossener Tab räumt sich also selbst auf. Polling statt WebSockets
    (Begründung in Technical_documentation.md). Tabelle `presence`,
    crud.touch_presence.
[X] Teilzahlungen mit automatischem Statuswechsel (offen -> teilbezahlt ->
    bezahlt)
[X] Rabatt (%) und Kleinunternehmer-/§-19-UStG-Modus (ohne Umsatzsteuer)
[X] Skonto-Berechnung samt Ausweis im PDF
[X] Idempotente Migrationen beim Start (ADD COLUMN IF NOT EXISTS)
[X] Zwischenspeichern der Eingaben: Rechnungs-, Angebots- und
    Lieferscheinformulare werden beim Tippen in den localStorage geschrieben
    (entprellt, Schlüssel rechnung.drafts.v1) und beim Neuladen
    wiederhergestellt; Entwürfe verfallen nach 7 Tagen und verlassen den
    Browser nie
[X] Suchen und Filtern in allen relevanten Ansichten und Auswahlfeldern:
    Rechnungs-, Angebots- und Lieferscheinlisten (Suche + Statusfilter),
    Kunden und Artikel (Suche + Aktiv-Filter), Benutzer, Audit-Log und die
    Kundenauswahl in allen drei Belegformularen
[X] Hinweisbanner über den Formularen – Sperre, Anwesenheit,
    wiederhergestellter Entwurf – lassen sich mit „✕“ schließen (app.js:
    showBanner / hideBanner / dismissedBanners). Ein weggeklicktes
    Entwurfsbanner verwirft den Entwurf nicht und kommt zurück, sobald es
    etwas Neues zu melden hat. Leere Banner stehen nicht mehr als farbige
    Balken über den Formularen: `[hidden] { display: none !important; }` am
    Ende von styles.css schlägt das `display: flex` der Banner-Klassen.
[X] Zentrales Fehler- und Logging-Konzept (logging_setup.py): JSON-Logzeilen,
    eine X-Request-ID je Request in jeder Zeile und jedem Fehlerkörper,
    einheitliche Antworten {"detail", "request_id"} für HTTP-, Validierungs-
    und unbehandelte Fehler
[X] Automatisierte Tests: 249 pytest-Tests in backend/tests/ gegen eine
    temporäre SQLite-Datenbank (conftest.py) – Rechnungen, Angebote,
    Lieferscheine, Kunden/Artikel (inkl. CSV-/JSON-Import und -Export),
    Admin-Endpunkte, Auswertungen (inkl. freiem Report-Builder), Response-Cache,
    Audit-Log und die Sicherheitsschicht. Start mit `python -m pytest` in
    backend/. Dazu 95 Frontend-Tests in backend/tests/frontend/, die die
    echte index.html und app.js in jsdom fahren (Kundenauswahl inkl.
    Vorauswahl des besten Treffers, Entwurfsspeicher, schließbare Banner,
    Logo-Upload, Kunden-/Artikelimport und -export, Beispieldateien als
    CSV/JSON, Listenfilter, Lieferscheinliste, Umwandlung Angebot ->
    Rechnung, gesperrte Doppelumwandlungen, Bearbeitungssperre auf Angebot
    und Lieferschein, Sprung in die Rechnungsübersicht nach dem Speichern,
    Gutschriften, Auswertungen samt Report-Builder, Monitoring, PDF-Vorlagen,
    CSRF-Header, Maskierung) –
    `npm install && npm test`, braucht Node >= 20


--------------------------------------------------------------------------------
3. BELEGE: RECHNUNGEN / ANGEBOTE / LIEFERSCHEINE
--------------------------------------------------------------------------------
[X] Bestehende (nicht stornierte) Rechnung bearbeiten
[X] Nach dem Speichern einer Rechnung – neu angelegt wie fertig bearbeitet –
    geht es in die Rechnungsübersicht; die Nummer wird dort bestätigt
    (app.js: historyNotice, Absenden von #invoice-form). Die Meldung
    verschwindet beim nächsten Ansichtswechsel.
[X] Rechnung stornieren und zurück auf „offen“ setzen
[X] Angebote: anlegen, bearbeiten, PDF, E-Mail, Status, in eine Rechnung
    umwandeln. Nach dem Umwandeln landet man direkt in der
    Rechnungsübersicht, in der die neue Rechnung schon steht; die Meldung
    nennt ihre Nummer (app.js: quoteAction, act === "convert").
[X] Lieferscheine: anlegen, bearbeiten, PDF, E-Mail, Status, aus einer
    bestehenden Rechnung erzeugen und in ein Angebot umwandeln
    (POST /api/delivery-notes/{id}/convert-to-quote, „zu Angebot“ in der
    Liste): Mengen aus dem Lieferschein, Preise aus dem Artikelstamm, wo die
    Beschreibung passt, sonst 0. Die laufende Nummer wird übernommen
    (LS-2026-0007 -> AN-2026-0007), bei Kollision die nächste freie. Ein
    „zu Rechnung“ gibt es beim Lieferschein bewusst nicht.
[X] Lieferschein-Status: offen, abgeschlossen (grüne Plakette) oder storniert
    (models.DN_OPEN/DN_DONE/DN_CANCELLED). Der PDF-Download schließt einen
    offenen Lieferschein ab – wer ihn ausdruckt, gibt ihn aus der Hand –, ein
    stornierter bleibt storniert (main.download_delivery_note_pdf). „wieder
    öffnen“ setzt zurück auf offen, stornieren bleibt möglich, und der
    Statusfilter der Liste kennt den neuen Wert.
[X] Jede Umwandlung nur einmal: aus einem Angebot entsteht genau eine
    Rechnung, aus einer Rechnung genau ein Lieferschein, aus einem
    Lieferschein genau ein Angebot. Verknüpft wird über
    quotes.converted_invoice_id, delivery_notes.source_invoice_id und
    quotes.source_delivery_note_id (neu, samt Migration); die API weist den
    zweiten Versuch mit 400 ab und nennt den vorhandenen Beleg, die Listen
    zeigen statt des Knopfes dessen Nummer (app.js: convertedMarker). Wird
    der Folgebeleg gelöscht, ist die Umwandlung wieder möglich – das Löschen
    einer Rechnung lässt ihren Lieferschein stehen und nullt nur den Verweis.
[X] Gemeinsame, durchsuchbare Kundenauswahl in allen drei Formularen
    (app.js: registerCustomerPicker): nur aktive Kunden, die Auswahl füllt
    die Stammdaten. Die Suche wählt den besten Treffer vor, behält einen
    bereits gewählten Kunden, der weiter passt, und übernimmt die Daten nur
    dann neu, wenn sich der beste Treffer wirklich ändert – Weitertippen
    überschreibt also keine von Hand geänderten Felder.
[X] PDF für Rechnung, Angebot und Lieferschein inkl. GiroCode/EPC-QR
[X] E-Mail-Versand für Rechnungen, Angebote, Lieferscheine,
    Zahlungserinnerungen und die automatische Zahlungsbestätigung beim
    vollständigen Ausgleich – ausgelöst über BEIDE Wege (POST .../payment und
    PATCH .../status {"status":"bezahlt"}), nur beim Übergang, und ein
    ausgefallener SMTP-Server bricht die Zahlung nie ab
    (main._confirm_payment_if_settled)
[X] Monatsexport als ZIP (ein PDF je Rechnung plus CSV-Zusammenfassung)
[X] Gutschriften als eigene Belegart (GS-JJJJ-NNNN, models.CreditNote): mit
    Positionen, Grund, PDF und E-Mail-Versand, im gemeinsamen Nummernkreis.
    „Gutschrift“ in der Rechnungsübersicht übernimmt Kunde und Positionen der
    Rechnung (Rabatt eingerechnet); einzelne Zeilen streichen oder ändern
    ergibt eine Teilgutschrift. Mehrere je Rechnung sind erlaubt, zusammen
    aber höchstens der offene Betrag. Eine Gutschrift senkt
    remaining (= Gesamt − Zahlungen − Gutschriften) und den Umsatz im
    Dashboard; der Rechnungsstatus wird bewusst nicht auf „bezahlt“ gedreht.
    Status: offen, erstattet, storniert.
[X] Eigene PDF-Vorlagen (models.PdfTemplate): beliebig viele benannte
    Vorlagen mit Akzent-/Kopffarbe, Schrift und -größe, Kopf- und Fußtext,
    Logo und GiroCode an/aus, eine davon als Vorgabe. Verwaltung in den
    Firmendaten (nur Admins) samt Vorschau als Musterrechnung; jeder
    PDF-Download nimmt ?template=<id>, und ab der ersten Vorlage fragt die
    Liste beim Klick nach (früher erst ab zweien – wer nur eine Vorlage
    hatte, sah beim Beleg nie eine Auswahl). Ohne Vorlage gilt pdf.DEFAULTS –
    das bisherige Aussehen. pdf.py rendert alle vier Belegarten aus
    gemeinsamen Bausteinen.
[X] Layout „Formular“ als zweites Aussehen einer Vorlage (pdf_form.py, Spalte
    pdf_templates.layout): der Firmenvordruck 1:1 nach dem alten
    Excel-Muster – Kopfbalken, Absender rechts oben, die vier Ankreuzfelder
    (Angebot / Bestellung / Lieferschein Nr. / Rechnung-Nr., bei einer
    Gutschrift heißt die letzte Zeile „Gutschrift-Nr.“), die Zeile
    „Bestellung / Lieferdatum“, der Positionskasten mit Menge, Beschreibung,
    Einzelpreis und Euro sowie die Kästen für Zwischensumme, Mehrwertsteuer
    und Endsumme. Alle Maße stehen als Pixel der Vorlage im Code, damit sich
    jede Linie am Original nachmessen lässt. Firmenname, Anschrift,
    Bankverbindung und Steuernummer kommen aus den Firmendaten, die
    Akzentfarbe ist die Druckfarbe, der Kopftext wird zur Branchenzeile, der
    Fußtext zum Kleingedruckten. Lange Belege laufen auf weiteren Seiten
    weiter, die Summen stehen auf der letzten. Einen GiroCode gibt es in
    diesem Layout nicht – dafür stehen Zahlungsziel, Skonto und der
    Zahlungsstand unten im Kleingedruckten. Eine fertige Vorlage dafür
    („Mechatronik Neubauer e.U.“) legt der Start an (crud.seed_pdf_templates),
    solange es keine Vordruck-Vorlage gibt – sonst müsste sie jeder erst von
    Hand anlegen, um den Vordruck überhaupt wählen zu können.



--------------------------------------------------------------------------------
4. KUNDEN & ARTIKEL
--------------------------------------------------------------------------------
[X] Kundenstammdaten mit Zahlungsziel und Skonto-Vorgaben
[X] Kunden und Artikel aktiv/inaktiv schalten (kein Löschen)
[X] Kundenimport aus CSV *oder* JSON (POST /api/customers/import, „Kunden
    importieren“ in der Kundenansicht): Spalten- und Schlüsselnamen werden
    gegen deutsche und englische Schreibweisen gemappt, Trennzeichen
    `;`/`,`/Tab, UTF-8 oder Windows-1252, vorhandene Kunden werden über den
    Namen erkannt und aktualisiert statt doppelt angelegt, fehlerhafte
    Datensätze werden übersprungen und gemeldet. JSON versteht auch die
    Datei aus dem DSGVO-Export je Kunde, Export und Import passen also
    zusammen. Der Knopf öffnet direkt den Dateidialog, die Auswahl startet
    den Import; eine sichtbare Dateiauswahl gibt es nicht mehr.
[X] Vorlage für den Import in beiden Formaten: „📄 Beispieldatei
    herunterladen“ öffnet ein kleines Auswahlfenster (CSV oder JSON) und
    liefert kunden-vorlage.csv bzw. kunden-vorlage.json. Beide entstehen aus
    denselben Beispieldaten (app.js: EXAMPLE_CUSTOMERS), und je ein
    Backend-Test liest sie wieder ein, damit die Vorlage nicht vom Import
    abdriftet. Klick daneben oder Escape schließt das Fenster.
[X] Die Kundenliste läuft nicht mehr über den Kartenrand hinaus: die breiten
    Listen sitzen in einem `.table-scroll`-Container mit eigener
    Querscrollleiste, die Adressspalte bricht um (`.cell-wrap`)
[X] Ein Logo-Upload löscht keine ungespeicherten Firmendaten mehr: der
    Handler frischt nur noch die Vorschau auf, statt das Formular neu vom
    Server zu füllen (app.js: showLogo)
[X] Artikelimport und ein CSV-Massenexport für Kunden und Artikel: Artikelimport
    (POST /api/products/import, „Datei wählen & importieren“ in der
    Artikelansicht) spiegelt den Kundenimport – CSV oder JSON, Pflichtfeld nur
    die Bezeichnung, ein vorhandener Artikel (gleicher Name) wird im Preis
    aktualisiert statt doppelt angelegt. Die Import-Hilfsfunktionen in main.py
    sind dafür generisch über eine Aliaskarte statt fest auf Kunden
    zugeschnitten (_field_for, _map_row, _rows_from_csv/_json,
    _read_import_rows). Dazu „⬇️ Alle als CSV“ bei Kunden UND Artikeln
    (GET /api/customers/export.csv, /api/products/export.csv) – dieselben
    Spalten wie der Import, ein exportiertes File lässt sich also ohne
    Nacharbeit wieder einlesen. Export nutzt jetzt csv.writer (main._write_csv)
    statt manuellem String-Zusammenkleben, ein Semikolon oder Anführungszeichen
    im Kundennamen verschiebt die Spalten also nicht mehr.


--------------------------------------------------------------------------------
5. AUSWERTUNGEN
--------------------------------------------------------------------------------
[X] Dashboard-Kennzahlen (Umsatz, offener Betrag, überfälliger Betrag,
    6-Monats-Diagramm)
[X] Rechnungsübersicht mit Status-/Überfällig-Filter und Spaltensortierung.
    Der Reiter heißt „Rechnungsübersicht“ (früher „History“); die View-ID
    `view-history`, die Routen und die serverseitige Suche blieben
    unverändert.
[X] Steuerbericht / UStVA-Grundlage (reports.vat_report, Reiter
    „Auswertungen“): Netto, Umsatzsteuer und Brutto je Steuersatz für einen
    frei wählbaren Zeitraum, nach Rechnungsdatum (Soll-Versteuerung),
    Gutschriften abgezogen, stornierte Belege ausgenommen, Kleinunternehmer im
    0-%-Topf. Dazu ein CSV-Export. Ohne Vorsteuer – siehe nächster Punkt.
[X] Gewinn-und-Verlust-Auswertung – halb erledigt: die Erlösseite steht
    (reports.revenue_report: Erlös netto/brutto, Gutschriften, bezahlt, offen,
    je Monat und je Kunde, mit CSV-Export). Was fehlt, ist die Ausgabenseite:
    ohne erfasste Ausgaben gibt es weder ein Betriebsergebnis noch die
    Vorsteuer für die UStVA. Nächster Schritt wäre eine Belegart „Ausgabe“
    (Datum, Kategorie, Beschreibung, Netto, MwSt.) mit eigener Ansicht.
[X] Freier Report-Builder (reports.custom_report, Reiter „Auswertungen“ unter
    UStVA/Erlöse): Belegart frei wählbar (Rechnungen, Angebote, Lieferscheine,
    Gutschriften), derselbe Zeitraum wie die beiden festen Auswertungen,
    optionaler Statusfilter, Gruppierung nach nichts (Belegliste), Kunde,
    Monat oder Status – inklusive CSV-Export (/api/reports/custom(.csv)).
    Anders als UStVA und Erlöse blendet er nichts von sich aus aus (auch
    stornierte Belege zählen mit, sofern nicht per Statusfilter
    ausgeschlossen) – „frei“ heißt hier: der Nutzer entscheidet, nicht die
    Auswertung. Lieferscheine kennen keine Preise, dort liefert die Antwort
    `has_amounts: false` und die Geldspalten fehlen ganz, das Frontend blendet
    sie dafür aus. Liegt unter /api/reports/ und damit automatisch im
    Response-Cache (Abschnitt 6).


--------------------------------------------------------------------------------
6. SYSTEM & DEPLOYMENT
--------------------------------------------------------------------------------
[X] Docker-Compose-Stack (web, db, mailhog, proxy, backup – 5 Container)
[X] Tägliche automatische Backups (pg_dump, gzip, 14 Tage Aufbewahrung)
[X] Backup-Verwaltung für Admins (auflisten, herunterladen, wiederherstellen
    mit doppelter Bestätigung)
[X] Geführte Setup-Skripte (scripts/setup-test.sh, scripts/setup-prod.sh)
[X] Eigener Nicht-root-Benutzer für Test- und Produktivumgebung:
    scripts/lib-common.sh legt den Host-Benutzer `rechnung` an (idempotent,
    beide Setup-Skripte nutzen es), übergibt ihm die Projektdateien und
    schreibt APP_UID/APP_GID in die .env, damit der Container unter
    derselben Identität läuft. Ohne root-Rechte bricht es nicht ab, sondern
    warnt.
[X] CI-Pipeline (.github/workflows/ci.yml): Backend-Tests, Frontend-Tests,
    statische Prüfungen (compileall, bash -n/shellcheck, node --check) und
    ein Docker-Image-Build, der prüft, dass die Container-UID nicht 0 ist
    und die Compose-Konfiguration gültig ist. Läuft bei jedem Push und
    Pull Request. (Keine Deploy-Stufe.)
[X] API-Pagination: ?limit=(1..MAX_PAGE_SIZE)&offset= für Rechnungen,
    Angebote, Lieferscheine, Kunden, Artikel und das Audit-Log; die
    Gesamtzahl kommt immer im Header X-Total-Count. Ohne ?limit kommt die
    ganze Liste, ältere Aufrufer laufen also weiter.
[X] Monitoring / Observability (monitoring.py): eine Middleware zählt
    Requests, Statusklassen, langsame Anfragen, Antwortzeiten und die letzten
    20 Serverfehler, dazu Fehlanmeldungen und das Alter des jüngsten Backups.
    Ausgelesen wird das im Reiter „Monitoring“ (nur Admins, kein eigener
    Monitoring-Benutzer), als JSON über /api/admin/metrics und im
    Prometheus-Textformat über /api/admin/metrics.prom. Alarme bei gehäuften
    Serverfehlern, vielen Fehlanmeldungen oder zu altem Backup gehen per
    E-Mail an die Firmenadresse aus den Firmendaten, gedrosselt über eine
    Sperrfrist je Alarmart und schaltbar über ALERTS_ENABLED (Vorgabe aus,
    scripts/setup-prod.sh schaltet ein). Probealarm über die Oberfläche.
    Die Zähler liegen wie Login-Sperre und Rate-Limit im Prozess.
[X] Monitoring aktualisiert sich auf Wunsch selbst (app.js,
    startMonitoringAuto): Auswahl „Automatisch“ mit 1, 2 oder 10 Sekunden
    bzw. einer Minute, Vorgabe bleibt „aus“, direkt neben dem Knopf
    „Aktualisieren“. Die Wahl steht im localStorage
    (rechnung.monitoring.interval). Der Takt ist eine Kette aus setTimeout:
    der nächste Abruf wird erst gestellt, wenn der vorige durch ist – so
    stapelt sich nichts, und ein Tab, der im Hintergrund ausgesetzt hat,
    läuft beim Zurückkommen weiter (visibilitychange holt sofort nach).
    Daneben steht, wann die Zahlen zuletzt kamen, in welchem Takt sie
    nachkommen und ob ein Abruf fehlgeschlagen ist.
[X] Horizontale Skalierung / Lastverteilung (bewusst ein einzelner
    `web`-Container; die Login-Sperre lebt im Prozessspeicher und würde
    mehrere Repliken nicht überstehen)
[X] Response-Cache (cache.py, response_cache_middleware): hält die Antwort von
    /api/stats und allem unter /api/reports/ (UStVA, Erlöse, freier
    Report-Builder, je als JSON und CSV) für CACHE_TTL_SECONDS (Vorgabe 30 s)
    im Prozessspeicher vor, Antwort-Header X-Cache: HIT/MISS. Beleg- und
    Stammdatenlisten bleiben bewusst ungecacht – dort arbeiten mehrere
    Benutzer live zusammen (Anwesenheitsanzeige, Bearbeitungssperre), eine
    veraltete Antwort stört dort mehr, als sie an Rechenzeit spart. Ein
    schreibender Request auf Rechnungen, Gutschriften oder eine
    Backup-Wiederherstellung leert den gesamten Cache (die Bearbeitungssperre
    /lock ist davon ausgenommen, sonst würde ihr 90-Sekunden-Heartbeat den
    Cache dauernd neu leeren). Schaltbar über CACHE_ENABLED, TTL über
    CACHE_TTL_SECONDS. Zustand liegt wie Login-Sperre, Rate-Limit und
    Monitoring im Prozessspeicher des einen `web`-Containers.


--------------------------------------------------------------------------------
7. DOKUMENTATION
--------------------------------------------------------------------------------
[X] README.md beschreibt den tatsächlichen Funktionsumfang
[X] Technical_documentation.md beschreibt die tatsächliche Architektur/API
[X] Eigener Leitfaden zur Fehlersuche (TROUBLESHOOTING.md)
[X] Diagramme über die ASCII-Skizze in Technical_documentation.md hinaus: sechs
    Mermaid-Diagramme neben der bestehenden ASCII-Skizze (die bleibt stehen,
    als schneller Textüberblick) – Architektur als Flowchart, ein
    ER-Diagramm des Datenmodells samt der drei Umwandlungs- und der
    Gutschrift-Beziehung, ein Sequenzdiagramm der Umwandlungskette
    Angebot -> Rechnung -> Lieferschein -> Angebot, je ein Zustandsdiagramm
    für Rechnungs- und Lieferschein-Status, und ein Sequenzdiagramm, das
    Bearbeitungssperre und Anwesenheitsanzeige nebeneinander an zwei
    Benutzern zeigt. GitHub rendert ```mermaid-Blöcke in .md-Dateien nativ,
    es braucht also keine zusätzliche Bibliothek oder einen Renderschritt.


--------------------------------------------------------------------------------
8. BEKANNTE EINSCHRÄNKUNGEN
--------------------------------------------------------------------------------
[ ] Standardmäßig ein selbstsigniertes Zertifikat -> Browserwarnung, bis
    scripts/setup-prod.sh ein echtes Let's-Encrypt-Zertifikat einrichtet
[ ] MailHog ist der Standard-Mailtransport (keine echte Zustellung), bis
    SMTP_* auf einen echten Anbieter zeigt
[ ] Die Zugänge admin/admin und der Testbenutzer müssen vor jedem echten
    Einsatz geändert werden (setup-prod.sh erzeugt automatisch starke)
[ ] Login-Sperre und Rate-Limit-Zähler liegen beide im Prozessspeicher:
    richtig für den einen `web`-Container aus der Compose-Datei, vor einer
    Skalierung bräuchten sie einen gemeinsamen Speicher (Redis o. Ä.)


--------------------------------------------------------------------------------
9. IDEEN / NOCH NICHT EINGEPLANT
--------------------------------------------------------------------------------
[X] Feldweises Zusammenführen oder eine echte Sperre für Angebote und
    Lieferscheine: echte Sperre statt Zusammenführen – dieselbe
    Bearbeitungssperre wie bei Rechnungen (Abschnitt 2), nur eben auch für
    Angebot und Lieferschein. Quote und DeliveryNote tragen jetzt dieselben
    zwei Spalten locked_by/locked_at wie Invoice; crud.py-Funktionen
    (acquire_lock/release_lock/lock_status) sind dafür generisch über einen
    Lockable-Typ (Invoice | Quote | DeliveryNote) statt Invoice-spezifisch.
    Neue Routen GET/POST/DELETE /api/quotes/{id}/lock und
    .../delivery-notes/{id}/lock, PUT prüft die Sperre wie bei der Rechnung
    (409 „wird gerade von … bearbeitet“). Frontend: „bearbeiten“ holt vorher
    die Sperre, bei „editable: false“ bricht es mit Hinweis ab statt das
    Formular zu füllen; „Abbrechen“ und ein Wechsel in eine andere Ansicht
    geben sie wieder frei. Feldweises Zusammenführen wurde bewusst nicht
    gebaut – eine Sperre ist einfacher, konsistent mit Rechnungen, und die
    Anwesenheitsanzeige zeigt ohnehin schon, wer gerade mitliest.
Sonst ist hier nichts geparkt.


--------------------------------------------------------------------------------
10. CHANGELOG
--------------------------------------------------------------------------------
2026-08-31, neunter Durchgang
  * Alle offenen Punkte bis auf Abschnitt 8 abgeschlossen:
  * Artikelimport (CSV/JSON) und CSV-Massenexport für Kunden UND Artikel
    (Abschnitt 4). main.py-Importhilfen dafür generisch über eine Aliaskarte
    statt Invoice-/Customer-spezifisch; Export nutzt jetzt csv.writer statt
    manuellem String-Zusammenkleben.
  * Freier Report-Builder: Belegart, Zeitraum, Statusfilter und Gruppierung
    (Kunde/Monat/Status) frei wählbar, mit CSV-Export (Abschnitt 5).
  * Response-Cache für Dashboard und alle drei Auswertungen, TTL 30 s,
    geleert bei Schreibzugriffen auf Rechnungen/Gutschriften/Backup-Restore
    (Abschnitt 6).
  * Sechs Mermaid-Diagramme in Technical_documentation.md: Architektur,
    ER-Diagramm, Umwandlungskette, zwei Zustandsdiagramme (Rechnung,
    Lieferschein), Sperre+Anwesenheit im Sequenzdiagramm (Abschnitt 7).
  * Echte Bearbeitungssperre jetzt auch für Angebote und Lieferscheine, nicht
    nur Rechnungen – dieselben crud.py-Funktionen, generisch über einen
    Lockable-Typ (Abschnitt 9, vormals unter „Ideen“).
  * Teststand: 249 Backend- und 95 Frontend-Tests.

2026-08-31, achter Durchgang
  * Die Vordruck-Vorlage „Mechatronik Neubauer e.U.“ legt der Start selbst an
    und die Auswahl beim Beleg erscheint schon ab einer Vorlage – vorher war
    der Vordruck bei der Rechnung nirgends zu sehen (Abschnitt 3).
  * Das selbsttätige Monitoring steht jetzt neben „Aktualisieren“, hält den
    Takt über eine setTimeout-Kette durch und zeigt Stand, Takt und
    fehlgeschlagene Abrufe an (Abschnitt 6).
  * Knopfleisten sind wieder so breit wie die Karte: die Klasse .bar galt
    zugleich für die Balken im Dashboard-Diagramm (width: 70 %), weshalb
    rechts stehende Bedienelemente umgebrochen sind. Der Diagrammbalken heißt
    jetzt .chart-bar – und hat mit background: var(--primary) wieder eine
    Farbe (bisher stand dort das unvollständige var()).
  * Teststand: 218 Backend- und 80 Frontend-Tests.

2026-08-31, siebter Durchgang
  * Monitoring aktualisiert sich auf Wunsch selbst – 1, 2, 10 Sekunden oder
    eine Minute, gemerkt im Browser (Abschnitt 6).
  * Zweites PDF-Layout „Formular“: der Firmenvordruck aus der alten
    Excel-Datei, nachgezeichnet in pdf_form.py und je Vorlage wählbar
    (Abschnitt 3).
  * Teststand: 216 Backend- und 76 Frontend-Tests.

2026-08-28, sechster Durchgang
  * Gutschriften als eigene Belegart, voll oder in Teilen zur Rechnung
    (Abschnitt 3).
  * Eigene PDF-Vorlagen samt Auswahl beim Download und Vorschau
    (Abschnitt 3); pdf.py auf gemeinsame Bausteine umgebaut.
  * Auswertungen: UStVA-Grundlage und Erlösrechnung mit CSV-Export
    (Abschnitt 5). Die Ausgabenseite fehlt weiterhin und bleibt offen.
  * Monitoring mit Kennzahlen, Prometheus-Ausgabe und Alarm-Mails
    (Abschnitt 6).
  * Teststand: 211 Backend- und 73 Frontend-Tests.

2026-08-28, fünfter Durchgang
  * Umwandlungen lassen sich nicht mehr doppeln (Abschnitt 3): die API
    lehnt den zweiten Versuch ab und nennt den vorhandenen Beleg, die Listen
    zeigen dort dessen Nummer statt des Knopfes.
  * Teststand: 166 Backend- und 56 Frontend-Tests.

2026-08-28, vierter Durchgang
  * Beispieldatei für den Kundenimport gibt es jetzt als CSV und als JSON,
    das Format wird in einem kleinen Fenster abgefragt (Abschnitt 4).
  * Lieferscheine kennen den Status „abgeschlossen“ (grün); der PDF-Download
    setzt ihn (Abschnitt 3).
  * Nach dem Speichern einer Rechnung geht es in die Rechnungsübersicht
    (Abschnitt 3).
  * Teststand: 161 Backend- und 51 Frontend-Tests.

2026-08-28, dritter Durchgang
  * Angebot -> Rechnung führt jetzt in die Rechnungsübersicht, die Meldung
    nennt die neue Rechnungsnummer (Abschnitt 3). Dazu ein Frontend-Test,
    Stand nun 42 Frontend-Tests.
  * Diese Datei aufgeräumt: durchgehend deutsch, einheitliche Abschnitte und
    [X]/[ ]-Schreibweise, die angehängten Blöcke „NEW IDEAS“, „NEUE PUNKTE“
    und „FEATURE REQUEST“ in die Sachabschnitte einsortiert, Erledigtes hier
    im Changelog statt in Fließtext oben.

2026-08-28, zweiter Durchgang
  * Lieferschein -> Angebot („zu Angebot“ in der Lieferscheinliste).
  * Leere Hinweisbanner stehen nicht mehr als farbige Balken über den
    Formularen (`[hidden] { display: none !important; }` in styles.css); ein
    Test rechnet dafür mit der echten styles.css statt nur das Attribut zu
    prüfen.
  * Der Kundenimport nimmt CSV und JSON (inkl. der Datei aus dem
    Kunden-Export), die sichtbare Dateiauswahl ist weg.

2026-08-28, erster Durchgang
  * Logo-Upload löscht keine ungespeicherten Firmendaten mehr.
  * Kundenliste läuft nicht mehr über den Kartenrand hinaus.
  * Kundenimport aus CSV.
  * „History“ heißt „Rechnungsübersicht“.
  * Sperr-, Anwesenheits- und Entwurfsbanner sind schließbar.
  * Die Kundensuche wählt den besten Treffer vor.
  * Teststand: 155 Backend- und 41 Frontend-Tests.

2026-08-27
  * Status gegen den tatsächlichen Code in backend/app/ geprüft; seitdem
    gilt die [X]-Regel aus den Konventionen oben.
  * Neu: Sicherheits-Middleware (CSRF, Rate Limit, Header), strukturiertes
    Logging, API-Pagination, Nicht-root-Dienstbenutzer, die Testsuiten
    (damals 139 Backend- und 27 Frontend-Tests) und die GitHub-Actions-
    Pipeline.
  * Die vier Feature-Requests umgesetzt: gemeinsame Kundenauswahl auf allen
    drei Formularen, Entwurfsspeicher, Suche/Filter überall, eigener
    Nicht-root-Benutzer.
  * Die beiden geparkten Ideen umgesetzt: Anwesenheitsanzeige und
    Zahlungsbestätigung aus beiden Zahlwegen.
