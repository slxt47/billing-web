# Arbeitsanweisungen

Gilt für jede Session an diesem Projekt, zusätzlich zum jeweiligen Prompt.

1. Nur Zeichen verwenden, die auch ein Mensch beim Tippen so verwenden
   würde: kein Geviertstrich (—) und keine sonstigen typografischen
   Sonderzeichen, die in den bestehenden Dateien nicht schon vorkommen.
   TODO.md ist die Referenz für die richtige Schreibweise (Halbgeviertstrich
   –, „…“, `->` statt `→`).

2. Jede inhaltliche Änderung an der App in Technical_documentation.md
   nachziehen – nicht nur in TODO.md oder README.md.

3. Bei jeder Aufgabe zusätzlich zum Prompt in TODO.md nachsehen, ob dort
   etwas Offenes liegt, das dazugehört oder sich gleich mit erledigen lässt.

4. Jede Änderung testen, bevor sie als fertig gilt: bestehende Tests laufen
   lassen (`python -m pytest` in backend/, `npm test` in
   backend/tests/frontend/) und, wo sinnvoll, neue Tests für das Neue
   ergänzen.

5. Abschnitt 10 „TODO's“ in TODO.md ist der Eingang für neue Aufgaben. Ist
   eine davon erledigt: den Eintrag umschreiben (fertige Beschreibung statt
   Stichwort), auf `[X]` setzen und in den fachlich passenden Abschnitt
   verschieben. Gibt es dafür noch keinen passenden Abschnitt, einen neuen
   anlegen – nummeriert einsortieren, nachfolgende Abschnitte (samt „TODO's“
   und „CHANGELOG“) entsprechend hochzählen. Abschnitt 10 bleibt danach leer,
   bis die nächste Aufgabe dort ankommt.
