#!/usr/bin/env python3
"""
pinnacle_scan.py — eigener Pinnacle-Scan über ALLE Spiele der Odds API (Lucas 18.10.2026:
„Pinnacle kommt so wenig — keine Inplay-Drops, keine Ligen außerhalb der Top 5. Gibt's doch nicht an
einem Sonntag").

Befund: pinnacle_konsens.py wertet Pinnacle nur für die Spiele aus, die schon auf Betfair die große
Volumenschwelle schaffen (am 18.10. mittags: 34 Spiele), obwohl derselbe Abruf 554 Spiele aus
49 Wettbewerben mit Pinnacle-Quote liefert. Angepfiffene Spiele wurden gar nicht gemessen, und
Stände wurden nur alle 20 Min gespeichert.

Hier: jede Pinnacle-Quote jedes Abrufs wird mitgeschrieben — unabhängig von Betfair, vor Anpfiff
UND live. Kostet keine zusätzlichen Credits (die Events sind schon abgerufen).

  fein  — jeder Abruf (≥ 4 Min Abstand), die letzten 3 Stunden: für Live und frische Sprünge
  grob  — alle ≥ 30 Min, 24 Stände (~12 h): für die Bewegung seit Eröffnung vor Anpfiff

11.11.2026 (Lucas, nach dem Call mit einem Betfair-Tippster, der Tage vorher CLV holt: „in der
Pinnacle-Ansicht könnte man wesentlich früher schauen, wo sich quotenmäßig was anpasst"):
  - Horizont 48 h → 7 Tage. Kostet keine Credits, die Events kommen im selben Abruf mit.
  - fein/grob nur in den letzten 48 h vor Anpfiff (wie bisher) — weiter weg wäre es nur Ballast.
  - erst   — der erste Pinnacle-Stand, den wir je gesehen haben. Bleibt für immer.
  - frueh  — alle ≥ 3 h ein Stand bis Anpfiff (max. 64 = 8 Tage): die frühe Bewegung.
  - bf     — Betfair-Match-Odds desselben Spiels im selben 3-h-Takt [ts, hw, dr, aw, vol], bfErst
             = erster Betfair-Stand. betfair_history hält nur ~20 h, darum hier ein eigener Verlauf.
             Betfair kommt nur so früh, wie der Feed das Spiel holt (Top/UEFA/Int 72 h, Rest 26 h).
  Noch keine Anzeige: erst sammeln, dann messen, wer zuerst zieht.

Datei: pinnacle_alle.json im aktuellen Ordner (.pin/, lebt im Actions-Cache).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATEI = Path("pinnacle_alle.json")
FEIN_MIN_ABSTAND = 4 * 60
FEIN_FENSTER = timedelta(hours=3)
GROB_MIN_ABSTAND = 30 * 60
GROB_N = 24
HORIZONT = timedelta(days=7)
NAH = timedelta(hours=48)                          # fein/grob nur so nah am Anpfiff
FRUEH_ABSTAND = 3 * 3600
FRUEH_N = 64
NACH_ANPFIFF = timedelta(hours=2, minutes=30)     # danach ist das Spiel vorbei


def _ts(s):
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def _bf_index(bf_matches, jetzt):
    """Betfair-Spiele vor Anpfiff mit vollständigen Match Odds → [(anpfiff, m)]. REIN."""
    out = []
    for m in bf_matches or []:
        if not isinstance(m, dict) or (m.get("liveInfo") or {}).get("finished"):
            continue
        mo = m.get("mo") or {}
        ko = _ts(m.get("kickoff"))
        if not ko or ko <= jetzt or not all(isinstance(mo.get(f), (int, float)) and mo.get(f) > 1 for f in ("hw", "dr", "aw")):
            continue
        out.append((ko, m))
    return out


def _bf_finden(ev, ko, bf_idx, namen, schwelle=0.60):
    """Betfair-Spiel zum Pinnacle-Event: Anpfiff ±30 Min und beide Teams teilen ein Kern-Wort
    (dieselbe Regel wie match_event im Konsens). → [hw, dr, aw, vol] in PINNACLE-Reihenfolge oder None."""
    if not namen:
        return None
    best, best_sc = None, schwelle
    for k2, m in bf_idx:
        if abs((k2 - ko).total_seconds()) > 30 * 60:
            continue
        hh, aa = namen(ev.get("home"), m.get("home")), namen(ev.get("away"), m.get("away"))
        ha, ah = namen(ev.get("home"), m.get("away")), namen(ev.get("away"), m.get("home"))
        mo = m["mo"]
        if hh > 0 and aa > 0 and hh + aa >= ha + ah and hh + aa > best_sc:
            best, best_sc = [mo["hw"], mo["dr"], mo["aw"], mo.get("vol")], hh + aa
        elif ha > 0 and ah > 0 and ha + ah > hh + aa and ha + ah > best_sc:
            best, best_sc = [mo["aw"], mo["dr"], mo["hw"], mo.get("vol")], ha + ah
    return best


def _anhaengen(liste, punkt, abstand, jetzt):
    letzt = _ts(liste[-1][0]) if liste else None
    if not letzt or (jetzt - letzt).total_seconds() >= abstand:
        liste.append(punkt)
    return liste


def fortschreiben(events_by_key, sports, jetzt=None, bf_matches=None, namen=None):
    """events_by_key: {sport_key: [parse_event(...)+{key}]}. Schreibt pinnacle_alle.json. Fehler bleiben hier.
    bf_matches: Betfair-Spiele (betfair_prices.json „matches"), namen: Namens-Score (a, b) → 0..1."""
    try:
        jetzt = jetzt or datetime.now(timezone.utc)
        titel = {s.get("key"): s.get("title") for s in sports or [] if isinstance(s, dict)}
        try:
            alt = json.loads(DATEI.read_text(encoding="utf-8")).get("spiele") or {}
        except Exception:  # noqa: BLE001
            alt = {}
        bf_idx = _bf_index(bf_matches, jetzt)
        neu, n_bf, n_weit = {}, 0, 0
        for key, evs in (events_by_key or {}).items():
            for ev in evs or []:
                p, o = ev.get("pinn"), ev.get("pinnOdds")
                ko = _ts(ev.get("commence"))
                if not p or not o or not ko or jetzt - ko > NACH_ANPFIFF or ko - jetzt > HORIZONT:
                    continue
                sid = f"{key}|{ev.get('home')}|{ev.get('away')}|{ko:%Y-%m-%dT%H:%M}"
                e = alt.get(sid) or {"key": key, "liga": titel.get(key) or key, "heim": ev.get("home"),
                                     "gast": ev.get("away"), "anpfiff": ko.isoformat(), "fein": [], "grob": []}
                punkt = [jetzt.isoformat(), *[round(x, 4) for x in p], *[round(float(x), 3) for x in o]]
                if ko - jetzt <= NAH:
                    for feld, abstand in (("fein", FEIN_MIN_ABSTAND), ("grob", GROB_MIN_ABSTAND)):
                        e[feld] = _anhaengen(e.get(feld) or [], punkt, abstand, jetzt)
                    e["fein"] = [x for x in e["fein"] if jetzt - (_ts(x[0]) or jetzt) <= FEIN_FENSTER]
                    e["grob"] = e["grob"][-GROB_N:]
                else:
                    n_weit += 1
                if ko > jetzt:
                    e.setdefault("erst", punkt)
                    e["frueh"] = _anhaengen(e.get("frueh") or [], punkt, FRUEH_ABSTAND, jetzt)[-FRUEH_N:]
                    try:
                        b = _bf_finden(ev, ko, bf_idx, namen)
                    except Exception:  # noqa: BLE001
                        b = None
                    if b:
                        n_bf += 1
                        bp = [jetzt.isoformat(), *[round(float(x), 3) for x in b[:3]],
                              round(float(b[3])) if isinstance(b[3], (int, float)) else None]
                        e.setdefault("bfErst", bp)
                        e["bf"] = _anhaengen(e.get("bf") or [], bp, FRUEH_ABSTAND, jetzt)[-FRUEH_N:]
                e["jetzt"] = punkt
                neu[sid] = e
        roh = json.dumps({"stand": jetzt.isoformat(), "n": len(neu), "spiele": neu},
                         ensure_ascii=False, separators=(",", ":"))
        DATEI.write_text(roh, encoding="utf-8")
        print(f"pinnacle_scan: {len(neu)} Spiele mit Pinnacle-Quote fortgeschrieben "
              f"({n_weit} > 48 h voraus, {n_bf} mit Betfair-Stand, {len(roh) / 1e6:.1f} MB)")
    except Exception as exc:  # noqa: BLE001
        print(f"pinnacle_scan: Fehler ({str(exc)[:120]}) — Konsens läuft weiter")
