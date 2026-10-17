# Geld-Radar

Wo liegt heute großes Geld – und kommt es von Leuten, die nachweislich treffen?
Keine Picks, keine Empfehlung.

**Phase 1:** liest die Daten des alten Projekts (`blummabet/betting-dashboard`), keine eigenen API-Abfragen.
`build.py` verdichtet sie zu `site/data.json`, der Workflow veröffentlicht `site/` alle 15 Minuten über GitHub Pages.

Lokal: `RADAR_SRC=/pfad/zu/betting-dashboard python3 build.py` und `site/index.html` über einen lokalen Server öffnen.

Plan und Regeln: siehe Projekt-Dokument „neues-projekt-plan“.
