#!/usr/bin/env python3
"""
apif_pinn.py — Pinnacle-Quoten aus API-Football für Ligen, die die Odds API nicht führt. 28.11.2026.

Lucas 28.11.: „Wieso finden wir die bolivianische erste Liga nicht? Riesendrop auf Nacional Potosí seit ein paar Tagen —
das müsste doch im Früh-Drop drin sein … sowas ist schon mächtig." Befund: Pinnacle kommt über die Odds API, und die
führt in Südamerika nur Argentinien, Brasilien, Chile. Betfair holen wir außerhalb der Top-Ligen erst 26 h vorher.

API-Football führt Pinnacle (bookmaker=4) für viel mehr Ligen. Test 26./27.10. (pin_vergleich.py): Stand im Median
~3 h alt — für schnelle Drops zu träge, für die FRÜHE Bewegung über Tage genau richtig.

Ablauf (läuft im Ordner .pin, höchstens alle 3 h):
  1. Ligen je Land einmal pro Woche nachschlagen (/leagues?country=…&current=true, type League, Quoten-Abdeckung).
  2. Je Liga /odds?league=…&season=…&bookmaker=4 (alle Seiten) → 1X2 + Über/Unter 2,5 je Spiel.
  3. Teamnamen und Anpfiff über /fixtures?ids=… (bis 20 je Abruf), nur Spiele in den nächsten 7 Tagen.
  4. Weiter wie die Odds API: pinnacle_scan.fortschreiben → pinnacle_alle.json (Schlüssel „apif_<land>_<Liga-Id>").
     Damit landen sie ohne Sonderweg in der Pinnacle-Seite und im Früh-Drop.
Kosten: ~2–3 Abrufe je Liga und Lauf, 8 Läufe am Tag (≈ 300–500 Abrufe; Kontingent 75.000).
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

URL = "https://v3.football.api-sports.io"
STAND = Path("apif_pinn_stand.json")         # im Ordner .pin
TAKT = timedelta(hours=3)
LIGEN_TAKT = timedelta(days=7)
HORIZONT = timedelta(days=7)
JE_LAND = 3
PINNACLE = 4
# Länder ohne Odds-API-Abdeckung, in denen Betfair/Polymarket Geld zeigen (API-Football-Schreibweise)
LAENDER = ["Bolivia", "Peru", "Colombia", "Ecuador", "Paraguay", "Uruguay", "Venezuela",
           "Costa-Rica", "Honduras", "Guatemala", "El-Salvador"]


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
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception as exc:  # noqa: BLE001
        print("apif_pinn:", pfad[:60], type(exc).__name__)
        return None


def _devig(qs):
    if not qs or any(not isinstance(q, (int, float)) or q <= 1 for q in qs):
        return None
    inv = [1 / q for q in qs]
    s = sum(inv)
    return [x / s for x in inv]


def ligen_waehlen(antwort, je_land=JE_LAND):
    """/leagues-Antwort → [(id, saison, name)] — Typ League, aktuelle Saison mit Quoten, niedrigste Ids zuerst. REIN."""
    out = []
    for x in (antwort or {}).get("response") or []:
        lg = x.get("league") or {}
        if str(lg.get("type") or "").lower() != "league":
            continue
        akt = next((s for s in x.get("seasons") or [] if s.get("current")), None)
        if not akt or not ((akt.get("coverage") or {}).get("odds")):
            continue
        out.append((lg.get("id"), akt.get("year"), f"{(x.get('country') or {}).get('name')}: {lg.get('name')}"))
    return sorted(out, key=lambda t: t[0] or 0)[:je_land]


def quoten_lesen(odds_resp):
    """/odds-Antwort → {fixture_id: {"x": [h, d, a], "ou": [2.5, über, unter] | None}} nur Pinnacle. REIN."""
    out = {}
    for r in odds_resp or []:
        fid = (r.get("fixture") or {}).get("id")
        for bk in r.get("bookmakers") or []:
            if bk.get("id") != PINNACLE:
                continue
            x, ou = None, None
            for bet in bk.get("bets") or []:
                vals = {str(v.get("value")): v.get("odd") for v in bet.get("values") or []}
                try:
                    if bet.get("id") == 1 and all(k in vals for k in ("Home", "Draw", "Away")):
                        x = [float(vals["Home"]), float(vals["Draw"]), float(vals["Away"])]
                    elif bet.get("id") == 5 and "Over 2.5" in vals and "Under 2.5" in vals:
                        ou = [2.5, float(vals["Over 2.5"]), float(vals["Under 2.5"])]
                except (TypeError, ValueError):
                    continue
            if fid and x:
                out[fid] = {"x": x, "ou": ou}
    return out


def events_bauen(quoten, fixtures, jetzt):
    """Quoten + /fixtures-Antwort → Events im Format von pinnacle_konsens.parse_event (nur was fortschreiben braucht). REIN."""
    evs = []
    for f in fixtures or []:
        fx, t = f.get("fixture") or {}, f.get("teams") or {}
        q = quoten.get(fx.get("id"))
        ko = _zeit(fx.get("date"))
        if not q or not ko or ko <= jetzt or ko - jetzt > HORIZONT or ((fx.get("status") or {}).get("short") not in (None, "NS", "TBD")):
            continue
        p = _devig(q["x"])
        if not p:
            continue
        ev = {"home": (t.get("home") or {}).get("name"), "away": (t.get("away") or {}).get("name"),
              "commence": ko.isoformat(), "eid": f"apif{fx.get('id')}", "pinn": p, "pinnOdds": q["x"], "quelle": "API-Football"}
        if q.get("ou"):
            ev["ln"] = {"ah": [None, None, None], "ou": q["ou"]}
        evs.append(ev)
    return evs


def lauf(jetzt=None):
    jetzt = jetzt or datetime.now(timezone.utc)
    if not os.environ.get("APISPORTS_KEY"):
        print("apif_pinn: kein APISPORTS_KEY")
        return
    try:
        st = json.loads(STAND.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        st = {}
    if jetzt - (_zeit(st.get("ts")) or jetzt - TAKT * 2) < TAKT:
        print("apif_pinn: noch nicht dran (alle 3 h)")
        return
    abrufe = 0
    ligen = st.get("ligen") or {}
    for land in LAENDER:
        l_ = ligen.get(land) or {}
        if jetzt - (_zeit(l_.get("ts")) or jetzt - LIGEN_TAKT * 2) >= LIGEN_TAKT:
            r = get(f"/leagues?country={land}&current=true")
            abrufe += 1
            if r is not None:
                ligen[land] = {"ts": jetzt.isoformat(), "ligen": ligen_waehlen(r)}
    events_by_key, sports, diag = {}, [], {}
    for land, l_ in ligen.items():
        for lid, saison, name in l_.get("ligen") or []:
            quoten, seite, seiten = {}, 1, 1
            while seite <= min(seiten, 5):
                r = get(f"/odds?league={lid}&season={saison}&bookmaker={PINNACLE}&page={seite}")
                abrufe += 1
                if not r:
                    break
                quoten.update(quoten_lesen(r.get("response")))
                seiten = int(((r.get("paging") or {}).get("total")) or 1)
                seite += 1
            ids = list(quoten)
            fixtures = []
            for i in range(0, len(ids), 20):
                r = get("/fixtures?ids=" + "-".join(str(x) for x in ids[i:i + 20]))
                abrufe += 1
                fixtures += (r or {}).get("response") or []
            key = f"apif_{land.lower()}_{lid}"
            evs = events_bauen(quoten, fixtures, jetzt)
            diag[name] = len(evs)
            if evs:
                events_by_key[key] = evs
                sports.append({"key": key, "title": name})
    st.update({"ts": jetzt.isoformat(), "ligen": ligen, "diag": diag, "abrufe": abrufe})
    STAND.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    print(f"apif_pinn: {sum(diag.values())} Spiele mit Pinnacle aus {len(events_by_key)} Ligen · {abrufe} Abrufe · {diag}")
    if not events_by_key:
        return
    try:
        bf = json.loads(Path("betfair_prices.json").read_text(encoding="utf-8")).get("matches") or []
    except Exception:  # noqa: BLE001
        bf = []
    import pinnacle_scan
    from pinnacle_konsens import _name_score
    pinnacle_scan.fortschreiben(events_by_key, sports, jetzt, bf_matches=bf, namen=_name_score)


if __name__ == "__main__":
    lauf()
