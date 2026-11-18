"""Public-Topf — dynamische Gewichte (Topf-Regel R31, Lucas 17.11.2026 „ja": die Topf-Punkte sortieren kaum —
10–12 Punkte +14 %, ab 12 Punkten −1,8 % —, also wie beim Top 3 lernen lassen, aber erst nach Prüfung scharf).

Vorab festgelegt (17.11.2026, 14:25 Wien), dasselbe Verfahren wie Top-3-Regel 7 (t3_dynamik.py):
* Jeder Topf-Kandidat (gesendet oder nicht) steht mit seinen Punkte-TEILEN in public.json → liste (ab 17.11.):
  Signale, Quellen, Geld, Kette, Wal, Alter (−3), Pinnacle, Liga-Wertung. Abgerechnet wird er in build.py (Statistik).
* Montag + Donnerstag: Effekt je Teil aus den letzten 28 Tagen (vorsichtig geschätzt), Faktor 0–2, je Schritt ±0,25.
* Scharf erst, wenn der Walk-Forward an ≥ 4 Prüfpunkten mehrheitlich UND zusammen besser sortiert als die festen Punkte.
* Still mitgemessen (keine Punkte, kein Faktor): Dauerzufluss live, Quote ab 2,20, Nachwuchs/Reserve/Frauen —
  die drei Fragen aus dem Hull-City-Fall. Diese Effekte gehen auch rückwirkend, weil sie aus Art/Quote/Liga folgen.
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import t3_dynamik as T

BASIS = Path(__file__).resolve().parent
GEW = BASIS / ".push_state" / "topf_gewichte.json"
TEILE = ("signale", "quellen", "geld", "kette", "wal", "alt", "pinn", "liga")
STILL = ("serieLive", "q22", "jung", "torlinie")   # torlinie: Regel R32 (18.11.)
SIGNALE = TEILE + STILL


def aktive_faktoren():
    g = T._lesen(GEW, {})
    return dict(g.get("faktoren") or {}) if g.get("aktiv") else {}


def rechnen(zeilen, jetzt):
    """zeilen: [{"ts", "p": {teil: punkte}|None, "still": {...}, "live", "r"}]. Rechnet nur am Stichtag neu."""
    g = T._lesen(GEW, {})
    letzte = T._ts(g.get("stand"))
    stich = [t for t in T._stichtage(jetzt - timedelta(days=8), jetzt) if t <= jetzt]
    if letzte and stich and letzte >= stich[-1]:
        mit = [z for z in zeilen if z.get("p") is not None]
        g.update(kandidaten=len(mit), abgerechnet=sum(1 for z in mit if isinstance(z.get("r"), (int, float))))
        T._schreiben(GEW, g)
        return False
    fenster = [z for z in zeilen if T._ts(z.get("ts")) and T._ts(z["ts"]) >= jetzt - timedelta(days=T.FENSTER_TAGE)]
    mit_teilen = [z for z in fenster if z.get("p") is not None]
    eff = T.effekte(mit_teilen, SIGNALE)
    eff_still = T.effekte([{"r": z.get("r"), "live": z.get("live"), "p": {}, "still": z.get("still") or {}} for z in fenster], STILL)
    for k, v in eff_still.items():
        eff.setdefault(k, v)
    alt = g.get("vorschlag") or {s: 1.0 for s in TEILE}
    vorschlag = T.faktoren_neu(alt, {k: v for k, v in eff.items() if k in TEILE}, TEILE, ()) if mit_teilen else dict(alt)
    wf = T.walk_forward([z for z in zeilen if z.get("p") is not None], jetzt, SIGNALE, STILL)
    aktiv = wf.get("urteil") == "trägt"
    T._schreiben(GEW, {"regel": "R31", "stand": jetzt.isoformat(), "aktiv": aktiv,
                       "faktoren": vorschlag if aktiv else {s: 1.0 for s in TEILE}, "vorschlag": vorschlag, "effekte": eff,
                       "kandidaten": len([z for z in zeilen if z.get("p") is not None]),
                       "abgerechnet": len([z for z in zeilen if z.get("p") is not None and isinstance(z.get("r"), (int, float))]),
                       "alleAbgerechnet": len([z for z in fenster if isinstance(z.get("r"), (int, float))]),
                       "fensterTage": T.FENSTER_TAGE, "walkForward": wf})
    return True
