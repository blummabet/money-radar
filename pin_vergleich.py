#!/usr/bin/env python3
"""pin_vergleich.py — Test 26./27.10.2026: Pinnacle bei API-Football gegen Pinnacle bei der Odds API.

Lucas 26.10.: „die Odds API kostet über 100 € im Monat — können wir Pinnacle stattdessen aus API-Football nehmen?
Wenn es nicht so oft aktualisiert, ist auch okay, aber Quotendrops wollen wir mitkriegen."

Für bis zu 25 Spiele der nächsten 24 h, die unser Pinnacle-Abruf (Odds API, .pin/betfair_consensus.json) kennt, wird in
jedem 5-Min-Lauf auch die Pinnacle-Quote bei API-Football geholt (/odds?fixture=…&bookmaker=4, Sieg-Markt). Beide
Reihen landen in .bf/pin_vergleich.json. Ausgewertet wird:
  • Abdeckung: wie viele dieser Spiele haben bei API-Football überhaupt Pinnacle-Quoten
  • Frische: wie alt ist der Stand bei API-Football („update"), wie oft ändert er sich
  • Verzug: bewegt sich Pinnacle bei der Odds API um ≥ 1,5 Prozentpunkte, wie viele Minuten später zeigt API-Football
    denselben Stand (± 0,75 Punkte) — oder gar nicht bis zum Anpfiff
Läuft 30 h ab dem ersten Lauf, danach nur noch die Auswertung. Kostet ~25 Abrufe je Lauf (≈ 7.000 am Tag).
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASIS = Path(__file__).resolve().parent
KONSENS = BASIS / ".pin" / "betfair_consensus.json"
STAND = BASIS / ".bf" / "pin_vergleich.json"
DATEN = BASIS / "site" / "data.json"
URL = "https://v3.football.api-sports.io"
JETZT = datetime.now(timezone.utc)
MAX_SPIELE = 25
DAUER_H = 30
DROP_PP = 1.5
TREFFER_PP = 0.75


def _zeit(s):
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def get(pfad):
    key = os.environ.get("APISPORTS_KEY", "")
    if not key:
        return None
    try:
        req = urllib.request.Request(URL + pfad, headers={"x-apisports-key": key})
        with urllib.request.urlopen(req, timeout=12) as r:
            return json.loads(r.read())
    except Exception as exc:  # noqa: BLE001
        print("pin_vergleich:", pfad[:40], type(exc).__name__)
        return None


def _probs(odds):
    if not odds or any(not isinstance(o, (int, float)) or o <= 1 for o in odds):
        return None
    inv = [1 / o for o in odds]
    s = sum(inv)
    return [round(100 * x / s, 2) for x in inv]


def _apif_quote(fid):
    r = ((get(f"/odds?fixture={fid}&bookmaker=4") or {}).get("response") or [])
    if not r:
        return None, None
    upd = r[0].get("update")
    for bk in r[0].get("bookmakers") or []:
        for bet in bk.get("bets") or []:
            if bet.get("name") == "Match Winner":
                v = {str(x.get("value")): x.get("odd") for x in bet.get("values") or []}
                try:
                    return [float(v["Home"]), float(v["Draw"]), float(v["Away"])], upd
                except (KeyError, TypeError, ValueError):
                    return None, upd
    return None, upd


def zuordnen(spiel, fixtures):
    from betfair_name_bridge import pair_matches
    ko = _zeit(spiel.get("kickoff"))
    treffer = [fx for fx in fixtures
               if ko and _zeit((fx.get("fixture") or {}).get("date"))
               and abs((_zeit(fx["fixture"]["date"]) - ko).total_seconds()) <= 20 * 60
               and pair_matches(spiel.get("home"), spiel.get("away"),
                                ((fx.get("teams") or {}).get("home") or {}).get("name"),
                                ((fx.get("teams") or {}).get("away") or {}).get("name"))]
    return (treffer[0].get("fixture") or {}).get("id") if len(treffer) == 1 else None


def auswerten(st):
    reihen = st.get("reihen") or {}
    verzug, verpasst, abd, aend_o, aend_a, alter = [], 0, 0, 0, 0, []
    for mid, r in reihen.items():
        mit = [p for p in r if p[2]]
        if mit:
            abd += 1
        for p in r:
            if p[2] and p[3]:
                u = _zeit(p[3])
                if u:
                    alter.append((_zeit(p[0]) - u).total_seconds() / 60)
        # Änderungen zählen (je Quelle, nur echte Sprünge ≥ 0,5 Punkte)
        lo = la = None
        for p in r:
            if p[1] and (lo is None or max(abs(a - b) for a, b in zip(p[1], lo)) >= 0.5):
                aend_o += lo is not None
                lo = p[1]
            if p[2] and (la is None or max(abs(a - b) for a, b in zip(p[2], la)) >= 0.5):
                aend_a += la is not None
                la = p[2]
        # Drops der Odds API und wann API-Football nachzieht
        basis = None
        for k, p in enumerate(r):
            if not p[1]:
                continue
            if basis is None:
                basis = p[1]
                continue
            d = max(abs(a - b) for a, b in zip(p[1], basis))
            if d >= DROP_PP:
                t0, ziel = _zeit(p[0]), p[1]
                gef = next((q for q in r[k:] if q[2] and max(abs(a - b) for a, b in zip(q[2], ziel)) <= TREFFER_PP), None)
                if gef:
                    verzug.append(round((_zeit(gef[0]) - t0).total_seconds() / 60))
                else:
                    verpasst += 1
                basis = p[1]
    verzug.sort()
    alter.sort()
    med = lambda xs: xs[len(xs) // 2] if xs else None
    return {"spiele": len(reihen), "mitApifPinnacle": abd, "zugeordnet": sum(1 for v in (st.get("zuordnung") or {}).values() if v),
            "aenderungenOddsApi": aend_o, "aenderungenApif": aend_a,
            "drops": len(verzug) + verpasst, "nachgezogen": len(verzug), "verpasst": verpasst,
            "verzugMedianMin": med(verzug), "verzugMax": verzug[-1] if verzug else None, "verzug": verzug[:60],
            "apifAlterMedianMin": round(med(alter)) if alter else None}


def main() -> int:
    try:
        st = json.loads(STAND.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        st = {"start": JETZT.isoformat(), "zuordnung": {}, "reihen": {}, "fixtures": {}}
    aktiv = JETZT - (_zeit(st.get("start")) or JETZT) < timedelta(hours=DAUER_H)
    if aktiv and os.environ.get("APISPORTS_KEY"):
        try:
            kons = json.loads(KONSENS.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            kons = {}
        spiele = {str(g.get("matchId")): g for g in kons.get("games") or []
                  if g.get("pinn") and not g.get("live") and _zeit(g.get("kickoff"))
                  and timedelta(0) < _zeit(g["kickoff"]) - JETZT < timedelta(hours=24)}
        reihen = st.setdefault("reihen", {})
        # Auswahl: laufende Spiele behalten (bis Anpfiff), dann auffüllen — gemischt über Ligen
        auswahl = [m for m in reihen if m in spiele][:MAX_SPIELE]
        ligen = {spiele[m].get("league") for m in auswahl}
        for m, g in sorted(spiele.items(), key=lambda x: x[1].get("kickoff") or ""):
            if len(auswahl) >= MAX_SPIELE:
                break
            if m not in auswahl and (g.get("league") not in ligen or len(auswahl) >= MAX_SPIELE // 2):
                auswahl.append(m)
                ligen.add(g.get("league"))
        # Fixtures je Tag (6 h gültig)
        fxc = st.setdefault("fixtures", {})
        for tag in {(_zeit(spiele[m]["kickoff"])).strftime("%Y-%m-%d") for m in auswahl}:
            e = fxc.get(tag) or {}
            if not e or JETZT - (_zeit(e.get("ts")) or JETZT) > timedelta(hours=6):
                fxc[tag] = {"ts": JETZT.isoformat(), "liste": [
                    {"fixture": {"id": (f.get("fixture") or {}).get("id"), "date": (f.get("fixture") or {}).get("date")},
                     "teams": {"home": {"name": ((f.get("teams") or {}).get("home") or {}).get("name")},
                               "away": {"name": ((f.get("teams") or {}).get("away") or {}).get("name")}}}
                    for f in (get(f"/fixtures?date={tag}") or {}).get("response") or []]}
        zu = st.setdefault("zuordnung", {})
        for m in auswahl:
            if m not in zu:
                tag = _zeit(spiele[m]["kickoff"]).strftime("%Y-%m-%d")
                zu[m] = zuordnen(spiele[m], (fxc.get(tag) or {}).get("liste") or [])
            p = spiele[m]["pinn"]
            oa = [p.get("home"), p.get("draw"), p.get("away")]
            oa = [round(100 * x, 2) if isinstance(x, (int, float)) and x <= 1 else x for x in oa]
            af, upd = (_apif_quote(zu[m]) if zu[m] else (None, None))
            reihen.setdefault(m, []).append([JETZT.isoformat(), oa if all(isinstance(x, (int, float)) for x in oa) else None,
                                             _probs(af), upd])
            reihen[m] = reihen[m][-400:]
        # Spiele, die seit über 3 h angepfiffen sind, bleiben für die Auswertung, werden aber nicht mehr abgefragt
        st["meta"] = {"spiele": spiele and len(spiele), "auswahl": len(auswahl), "liga": {spiele[m].get("league") for m in auswahl}.__len__()}
    for tag in list((st.get("fixtures") or {}).keys()):
        if tag < (JETZT - timedelta(days=2)).strftime("%Y-%m-%d"):
            del st["fixtures"][tag]
    STAND.parent.mkdir(exist_ok=True)
    STAND.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    try:
        d = json.loads(DATEN.read_text(encoding="utf-8"))
        d["pinVergleich"] = {"start": st.get("start"), "aktiv": aktiv, **auswerten(st), "meta": st.get("meta")}
        DATEN.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print("pin_vergleich: data.json", exc)
    print("pin_vergleich:", json.dumps(auswerten(st))[:300])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
