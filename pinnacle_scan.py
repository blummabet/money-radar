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
NACH_ANPFIFF = timedelta(hours=2, minutes=30)     # danach ist das Spiel vorbei


def _ts(s):
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def fortschreiben(events_by_key, sports, jetzt=None):
    """events_by_key: {sport_key: [parse_event(...)+{key}]}. Schreibt pinnacle_alle.json. Fehler bleiben hier."""
    try:
        jetzt = jetzt or datetime.now(timezone.utc)
        titel = {s.get("key"): s.get("title") for s in sports or [] if isinstance(s, dict)}
        try:
            alt = json.loads(DATEI.read_text(encoding="utf-8")).get("spiele") or {}
        except Exception:  # noqa: BLE001
            alt = {}
        neu = {}
        for key, evs in (events_by_key or {}).items():
            for ev in evs or []:
                p, o = ev.get("pinn"), ev.get("pinnOdds")
                ko = _ts(ev.get("commence"))
                if not p or not o or not ko or jetzt - ko > NACH_ANPFIFF or ko - jetzt > timedelta(hours=48):
                    continue
                sid = f"{key}|{ev.get('home')}|{ev.get('away')}|{ko:%Y-%m-%dT%H:%M}"
                e = alt.get(sid) or {"key": key, "liga": titel.get(key) or key, "heim": ev.get("home"),
                                     "gast": ev.get("away"), "anpfiff": ko.isoformat(), "fein": [], "grob": []}
                punkt = [jetzt.isoformat(), *[round(x, 4) for x in p], *[round(float(x), 3) for x in o]]
                for feld, abstand in (("fein", FEIN_MIN_ABSTAND), ("grob", GROB_MIN_ABSTAND)):
                    liste = e.get(feld) or []
                    letzt = _ts(liste[-1][0]) if liste else None
                    if not letzt or (jetzt - letzt).total_seconds() >= abstand:
                        liste.append(punkt)
                    e[feld] = liste
                e["fein"] = [x for x in e["fein"] if jetzt - (_ts(x[0]) or jetzt) <= FEIN_FENSTER]
                e["grob"] = e["grob"][-GROB_N:]
                e["jetzt"] = punkt
                neu[sid] = e
        DATEI.write_text(json.dumps({"stand": jetzt.isoformat(), "n": len(neu), "spiele": neu},
                                    ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"pinnacle_scan: {len(neu)} Spiele mit Pinnacle-Quote fortgeschrieben")
    except Exception as exc:  # noqa: BLE001
        print(f"pinnacle_scan: Fehler ({str(exc)[:120]}) — Konsens läuft weiter")
