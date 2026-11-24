"""Offene Prüfungen (24.11., Lucas: „vergiss das alles nicht auszuwerten, wenn wir genug Fälle haben").

EIN Register aller vorab festgelegten Prüfungen mit Ziel. push.py meldet einmal im Money-Kanal, wenn eine reif ist;
der Montags-Check wertet die reifen aus (python3 pruefungen.py <statistik.json> zeigt den Stand).
Gezählt wird aus der Statistik (site/statistik.json bzw. d/<zeit>-s.json im Zweig live): abgerechnete, nicht annullierte
Zeilen der genannten Arten ab dem Startzeitpunkt. Datums-Prüfungen werden am Datum reif.

Neue Regel mit Prüfung → hier eintragen. Ausgewertet → Eintrag löschen (das Ergebnis steht in der Regel auf der Seite).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

# id, Frage (vorab festgelegt), Arten, ab (UTC, wie die Statistik-Zeit), Ziel (abgerechnet) oder Datum, Vergleich-Arten
REGISTER = [
    {"id": "T10", "frage": "Top 3 (nach den drei Filtern) mehr Rendite als die herausgefilterten (top3Aus) und über null?",
     "arten": ["top3", "top3p"], "ab": "2026-11-24T12:20", "ziel": 100, "vergleich": ["top3Aus", "top3pAus"]},
    {"id": "W7", "frage": "Wal-Pushes ab $5k/$10k besser als die still gebuchten darunter (walUnter)?",
     "arten": ["wal"], "ab": "2026-11-24T09:00", "ziel": 50, "vergleich": ["walUnter"]},
    {"id": "W8", "frage": "Wal mit Gegen-Wal (walStreit) wirklich ±0 — also zu Recht kein Push?",
     "arten": ["walStreit"], "ab": "2026-11-24T10:00", "ziel": 50, "vergleich": ["wal"]},
    {"id": "W9", "frage": "Profis unter der Top-Grenze (walProfiB) schlechter als die gepushten Wal-Einstiege?",
     "arten": ["walProfiB"], "ab": "2026-11-24T12:00", "ziel": 50, "vergleich": ["wal"]},
    {"id": "R39", "frage": "Polymarket-Dauerzufluss mit bewährtem Geld dagegen im Minus? Dann wieder sperren.",
     "arten": ["pserieGegen"], "ab": "2026-11-24T10:00", "ziel": 30, "vergleich": ["pserie"]},
    {"id": "PK1", "frage": "Konsens bewährter Wallets: Ø-CLV > +1 % und ROI > 0?",
     "arten": ["pk1"], "ab": "2026-11-20T18:00", "ziel": 100, "vergleich": ["pk1Streit"]},
    {"id": "PK1-Profi", "frage": "Konsens, der erst mit Sport-Profis entsteht — besser als PK1?",
     "arten": ["pk1Profi"], "ab": "2026-11-24T09:00", "ziel": 100, "vergleich": ["pk1"]},
    {"id": "CM1", "frage": "Claude-Master: Ø-CLV > +2 % und ROI > 0?",
     "arten": ["cm1"], "ab": "2026-11-20T18:00", "ziel": 60, "vergleich": ["wal"]},
    {"id": "CM1-Profi", "frage": "Claude-Master-Lage nur durch Sport-Profis — besser als CM1?",
     "arten": ["cm1Profi"], "ab": "2026-11-24T09:00", "ziel": 60, "vergleich": ["cm1"]},
    {"id": "TP1", "frage": "Torprofil „torreich – erst ≤ 1 Tor“: Treffer über Quote und Rendite über Druck-Radar allein?",
     "arten": ["torHinweis"], "ab": "2026-11-22T18:00", "ziel": 100, "vergleich": ["druck"]},
    {"id": "CPO1", "frage": "CPO (Cash · Pressure · Odds): Rendite über Druck-Radar allein?",
     "arten": ["cpo"], "ab": "2026-11-22T09:30", "ziel": 100, "vergleich": ["druck"]},
    {"id": "MM2", "frage": "Angriffs-Bild ≥ 3 Märkte besser als Frischer Zufluss/Live-Zufluss im selben Zeitraum?",
     "arten": ["mmA"], "ab": "2026-11-20T22:55", "ziel": 100, "vergleich": ["zufluss", "live"]},
    {"id": "P25", "frage": "Gegenwind-Sperre: laufen die gesperrten schlechter als die gesendeten Public-Picks?",
     "arten": ["publicGegenwind"], "ab": "2026-11-14T00:00", "ziel": 30, "vergleich": ["public"]},
    {"id": "P28", "frage": "Quote läuft gegen das Geld: laufen die gesperrten schlechter als die gesendeten Public-Picks?",
     "arten": ["publicQuote"], "ab": "2026-11-15T00:00", "ziel": 30, "vergleich": ["public"]},
    {"id": "S7", "frage": "Stake unter 5 % des Betfair-Volumens: ist 5 % die richtige Grenze?",
     "arten": ["stake", "stakeKlein"], "ab": "2026-11-22T17:00", "datum": "2026-11-29"},
    {"id": "D4", "frage": "Druck-Team in Überzahl: neu prüfen bei 40 Fällen (Seitendaten pushBilanz.druck.gruppen).",
     "arten": [], "ab": "2026-11-01T00:00", "datum": "2026-12-08"},
    {"id": "LW3", "frage": "Liga-Stufen: „gut“ in ≥ 4 von 6 Wochen vor „schwach“ und zusammen besser? (Seite Ligen)",
     "arten": [], "ab": "2026-11-24T00:00", "datum": "2027-01-05"},
]


def _void(z):
    return z[2] == 0 and z[3] != 1


def stand(zeilen, jetzt=None):
    """→ [{id, frage, n, ziel|datum, reif, vergleichN}] aus Statistik-Zeilen. REIN."""
    jetzt = jetzt or datetime.now(timezone.utc)
    heute = jetzt.strftime("%Y-%m-%d")
    aus = []
    for p in REGISTER:
        zahl = lambda arten: sum(1 for z in zeilen if z[1] in arten and z[0] >= p["ab"] and z[2] is not None and not _void(z))  # noqa: E731
        n = zahl(p["arten"]) if p["arten"] else None
        reif = (n is not None and "ziel" in p and n >= p["ziel"]) or ("datum" in p and heute >= p["datum"])
        aus.append({"id": p["id"], "frage": p["frage"], "n": n, "ziel": p.get("ziel"), "datum": p.get("datum"), "reif": bool(reif),
                    "vergleichN": zahl(p["vergleich"]) if p.get("vergleich") else None})
    return aus


if __name__ == "__main__":
    d = json.loads(open(sys.argv[1], encoding="utf-8").read())
    for x in stand(d["zeilen"]):
        print(("REIF  " if x["reif"] else "      ") + f"{x['id']:10} {x['n'] if x['n'] is not None else '–':>4} / "
              f"{x['ziel'] or x['datum']}  {x['frage']}")
