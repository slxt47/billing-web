# Arbeitsanweisungen

Gilt für jede Session an diesem Projekt, zusätzlich zum jeweiligen Prompt.
Bei Widerspruch zwischen einer Regel hier und dem Prompt gilt der Prompt –
sag dann aber dazu, welche Regel du übergehst und warum.


## 1. Schreibweise

Nur Zeichen verwenden, die auch ein Mensch beim Tippen so verwendet. Erlaubt
und in Gebrauch sind: Halbgeviertstrich –, deutsche Anführungszeichen „…“,
Umlaute, Emoji in Bedienoberflächen. Nicht verwenden: Geviertstrich (—),
typografische Pfeile (→, schreib `->`), schmale Leerzeichen und alles andere,
was in den bestehenden Dateien nicht schon vorkommt.

Referenz für den richtigen Ton und die richtige Zeichenwahl ist TODO.md. Im
Zweifel dort nachsehen, wie eine ähnliche Stelle geschrieben ist. Deutsch ist
die Sprache für App, TODO.md und README.md; Technical_documentation.md ist
englisch und bleibt es.


## 2. Dokumentation nachziehen

Jede inhaltliche Änderung an der App gehört in Technical_documentation.md –
nicht nur in TODO.md oder README.md. Die drei Dateien haben verschiedene
Leser:

* README.md: was die App kann, aus Sicht von jemandem, der sie benutzt.
* Technical_documentation.md: wie es gebaut ist, mit Dateinamen und
  Funktionsnamen, damit sich eine Aussage in Sekunden nachprüfen lässt.
* TODO.md: was erledigt ist und was offen ist, plus CHANGELOG.

Beim Ändern von Code auch prüfen, ob eine bestehende Stelle in der
Dokumentation dadurch falsch wird (eine umbenannte Funktion, ein Knopf, den es
so nicht mehr gibt). Falsche Doku ist schlimmer als keine.


## 3. TODO.md mitlesen

Bei jeder Aufgabe zusätzlich zum Prompt in TODO.md nachsehen, ob dort etwas
Offenes liegt, das zur Aufgabe gehört oder sich gleich miterledigen lässt.
Offene Punkte stehen als `[ ]`, erledigte als `[X]`.


## 4. Testen, bevor etwas als fertig gilt

Beide Testsuiten müssen grün sein:

* Backend: `python3 -m pytest` in `backend/`. Die Abhängigkeiten stehen in
  `requirements-dev.txt`. Gibt es sie im System nicht, ein venv anlegen
  (`python3 -m venv <pfad>`, `<pfad>/bin/pip install -r requirements.txt -r
  requirements-dev.txt`) und daraus starten. Das venv gehört nicht ins Repo.
* Frontend: `npm test` in `backend/tests/frontend/` (jsdom, braucht Node >=
  20, beim ersten Mal `npm install`).

Für alles Neue nach Möglichkeit einen Test ergänzen. Ist etwas nicht testbar,
schreib hin, warum. Nie "fertig" melden, ohne die Suiten laufen gelassen zu
haben, und nie ein Ergebnis beschönigen: rote Tests werden genannt, samt
Ausgabe.

Zwei Fallen, die in dieser Testumgebung schon mehrfach zugeschlagen haben:
jsdom rechnet kein Layout, `scrollWidth`/`clientWidth` sind dort immer 0 und
`scrollIntoView` gibt es nicht – Code, der davon abhängt, muss ohne diese
Dinge sinnvoll weiterlaufen und lässt sich nur über `window.innerWidth`
testen.


## 5. Neue Aufgaben und ihr Weg durch TODO.md

Der Abschnitt „TODO's“ in TODO.md – der letzte nummerierte Abschnitt, direkt
vor dem CHANGELOG – ist der Eingang für neue Aufgaben. Nicht nach der Nummer
suchen, die ändert sich; nach der Überschrift suchen.

Ist eine Aufgabe von dort erledigt:

1. Den Eintrag umschreiben: eine fertige Beschreibung dessen, was jetzt da
   ist, statt des Stichworts, mit dem er hereinkam. Datei- und Funktionsnamen
   dazu.
2. Auf `[X]` setzen.
3. In den fachlich passenden Abschnitt verschieben. Passt er inhaltlich zu
   einem Eintrag, der dort schon steht, den bestehenden Eintrag umschreiben,
   statt einen zweiten danebenzustellen.
4. Gibt es noch keinen passenden Abschnitt: einen neuen anlegen, nummeriert
   an der fachlich richtigen Stelle einsortieren und alle nachfolgenden
   Abschnitte hochzählen – „TODO's“ und „CHANGELOG“ eingeschlossen.
5. Einen CHANGELOG-Eintrag schreiben (Datum, was sich geändert hat, in
   welchem Abschnitt es jetzt steht, Teststand).

Der Abschnitt „TODO's“ bleibt danach leer, bis die nächste Aufgabe dort
ankommt.


## 6. Agenten

Für jeden Teil der Arbeit, bei dem es sich lohnt, einen eigenen Agenten
starten – Suchen quer über viele Dateien, ein zweiter Blick auf fertigen
Code, ein Durchgang durch die Dokumentation. Mehrere Agenten, die nichts
voneinander brauchen, gleichzeitig starten und nicht nacheinander.

Was ein Agent meldet, ist ein Hinweis und kein Beweis: jeden Befund selbst
im Code nachprüfen, bevor du ihn weitergibst oder danach handelst.
