#!/usr/bin/env python3
"""
team_archiv.py — Team-Ergebnisse aus dem Betfair-Ledger, gesammelt statt gerollt. 04.10.2026.

Lucas: „woher krieg ich in kleinen Ligen Serien". Die Serien-Seite (compute_streaks.py) kennt nur
die Ligen der Odds-/Fixture-Feeds — Armenien, Georgien, Usbekistan, Reserveligen fehlen dort.
Betfair dagegen rechnet genau diese Spiele ab: betfair_track_results.json traegt Halbzeit- und
Endstand je Spiel. Nur rollt dieses Ledger nach 40.000 Zeilen (~34 Tage) heraus.

Deshalb wird hier je Spiel EINE Zeile behalten (matchId -> Datum, Liga, Heim, Gast, HZ, Ende),
ARCHIV_TAGE lang. Eine Serie aus 4-5 Spielen ist duenn — sie wird mit jeder Woche laenger, und
die Anzeige nennt immer, aus wie vielen Spielen sie besteht.

Teamnamen sind die BETFAIR-Namen — dieselben, die betfair_prices.json im Live-Spiel traegt.
Damit passt die Serie ohne Namens-Abgleich an das laufende Spiel.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

# money-radar 20.10.2026: Kopie aus dem alten Projekt; Quelle ist jetzt das eigene Ergebnisbuch (.bf/ergebnisse.json)
BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".bf")
ARCHIV_FILE = "team_archiv.json"
ARCHIV_TAGE = 180


def _laden(pfad, leer=None):
    try:
        with open(pfad, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return leer


def aufnehmen(spiele, zeilen, jetzt=None) -> dict:
    """Spiele aus Ledger-Zeilen uebernehmen (je matchId einmal), alte herausnehmen. REIN."""
    jetzt = jetzt or datetime.now(timezone.utc)
    aus = dict(spiele or {})
    for z in zeilen or []:
        if not isinstance(z, dict):
            continue
        mid, ft, ht = z.get("matchId"), z.get("ft"), z.get("ht")
        if not mid or not (isinstance(ft, list) and len(ft) == 2) or not z.get("home") or not z.get("away"):
            continue
        mid = str(mid)
        if mid in aus:
            continue
        aus[mid] = {"d": str(z.get("settledAt") or "")[:10], "l": z.get("league"),
                    "h": z.get("home"), "a": z.get("away"), "ft": ft,
                    "ht": ht if isinstance(ht, list) and len(ht) == 2 else None}
    grenze = (jetzt - timedelta(days=ARCHIV_TAGE)).date().isoformat()
    return {k: v for k, v in aus.items() if (v.get("d") or "") >= grenze}


def spiele_von(spiele, team) -> list:
    """Spiele eines Teams, neueste zuerst, aus SEINER Sicht (tore/gegen/heim). REIN."""
    aus = []
    for mid, s in (spiele or {}).items():
        if team not in (s.get("h"), s.get("a")):
            continue
        heim = s["h"] == team
        ft = s["ft"]
        aus.append({"matchId": mid, "d": s.get("d"), "heim": heim, "gegner": s["a"] if heim else s["h"],
                    "tore": ft[0] if heim else ft[1], "gegen": ft[1] if heim else ft[0],
                    "ht": s.get("ht"), "l": s.get("l")})
    aus.sort(key=lambda x: (x["d"] or "", x["matchId"]), reverse=True)
    return aus


def serie(spiele, team, n=6) -> dict | None:
    """Kennzahlen der letzten n Spiele. None, wenn das Team nicht im Archiv steht. REIN."""
    letzte = spiele_von(spiele, team)[:n]
    if not letzte:
        return None
    k = len(letzte)
    return {"n": k,
            "over25": sum(1 for s in letzte if s["tore"] + s["gegen"] >= 3),
            "btts": sum(1 for s in letzte if s["tore"] > 0 and s["gegen"] > 0),
            "ht00": sum(1 for s in letzte if s["ht"] == [0, 0]),
            "siege": sum(1 for s in letzte if s["tore"] > s["gegen"]),
            "toreSchnitt": round(sum(s["tore"] + s["gegen"] for s in letzte) / k, 1),
            # 10.10.2026 (Lucas: „sind die 4.1 und 4.6 Tore gesamt?"): „Ø 4.1 Tore" las sich wie
            # eigene Tore. Geschossen und kassiert getrennt — der Push zeigt „Ø 2.6:1.5".
            "toreFuer": round(sum(s["tore"] for s in letzte) / k, 1),
            "toreGegen": round(sum(s["gegen"] for s in letzte) / k, 1),
            "form": "".join("S" if s["tore"] > s["gegen"] else "U" if s["tore"] == s["gegen"] else "N"
                            for s in letzte)}


def aktualisieren(base_dir=BASE, jetzt=None) -> dict:
    """Archiv auf Platte fortschreiben und zurueckgeben."""
    alt = (_laden(os.path.join(base_dir, ARCHIV_FILE), {}) or {}).get("spiele") or {}
    erg = _laden(os.path.join(base_dir, "ergebnisse.json"), {}) or {}
    zeilen = [{"matchId": mid, "ft": e.get("ft"), "ht": e.get("ht"), "home": e.get("h"), "away": e.get("a"),
               "league": e.get("l"), "settledAt": e.get("ko")} for mid, e in erg.items() if isinstance(e, dict)]
    spiele = aufnehmen(alt, zeilen, jetzt)
    pfad = os.path.join(base_dir, ARCHIV_FILE)
    with open(pfad + ".tmp", "w", encoding="utf-8") as f:
        json.dump({"updatedAt": (jetzt or datetime.now(timezone.utc)).isoformat(),
                   "quelle": "ergebnisse.json (eigener Betfair-Abruf)", "spiele": spiele}, f, ensure_ascii=False)
    os.replace(pfad + ".tmp", pfad)
    return spiele


if __name__ == "__main__":
    s = aktualisieren()
    print(f"[team_archiv] {len(s)} Spiele")
