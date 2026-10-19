#!/usr/bin/env python3
"""
live_stats.py — Live-Statistik (xG, Schüsse aufs Tor, Ecken) aus API-Football für genau die Live-Spiele,
die der Geldradar zeigt oder pusht (Lucas 19.10.2026: „Secret ist drin").

Läuft im Seitenbau NACH build.py und VOR push.py: liest site/data.json, holt für die wichtigsten
Live-Spiele die Statistik und schreibt sie als "liveStats" {Betfair-ID: {...}} zurück. push.py und die
Seite lesen von dort.

Kontingent: der Schlüssel ist derselbe wie im alten Projekt (HZ-Finder). /status kostet nichts und sagt,
wie viel heute schon verbraucht ist. Je Lauf gönnen wir uns höchstens 60 % dessen, was bis Mitternacht
(UTC) pro Lauf übrig ist, und nie mehr als 20 Abrufe — der Rest bleibt fürs alte Projekt.

Zuordnung Betfair → API-Football bewusst ENG (betfair_name_bridge, Anpfiff ±20 Min, bei zwei Kandidaten
nichts): eine falsche Statistik wäre schlimmer als keine. Fällt hier etwas aus, läuft alles andere weiter.
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASIS = Path(__file__).resolve().parent
DATEN = BASIS / "site" / "data.json"
CACHE = BASIS / ".bf" / "live_stats.json"
BASE_URL = "https://v3.football.api-sports.io"
MAX_JE_LAUF = 20         # Kontingent 75.000/Tag (Stand 19.10.) — 20 × 288 Läufe ≈ 5.800
ANTEIL = 0.6
FRISCH_MIN = 9          # jünger als das → nicht neu abrufen
GUELTIG_MIN = 25        # älter als das → nicht mehr zeigen
ANPFIFF_TOLERANZ_MIN = 20
STAT_FELDER = {"Shots on Goal": "aufsTor", "Total Shots": "schuesse", "Corner Kicks": "ecken",
               "Ball Possession": "ballbesitz", "expected_goals": "xg", "Dangerous Attacks": "gefAngriffe"}
JETZT = datetime.now(timezone.utc)


def get(pfad, timeout=12):
    key = os.environ.get("APISPORTS_KEY", "")
    if not key:
        return None
    try:
        req = urllib.request.Request(BASE_URL + pfad, headers={"x-apisports-key": key})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read())
        if isinstance(d, dict) and d.get("errors") and not d.get("response"):
            print("live_stats: API-Fehler", str(d.get("errors"))[:160])
            return None
        return d
    except Exception as exc:  # noqa: BLE001
        print("live_stats: Abruf fehlgeschlagen", pfad[:50], type(exc).__name__)
        return None


def _zeit(s):
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def budget():
    """Wie viele Abrufe dieser Lauf machen darf (inkl. live=all)."""
    st = (get("/status") or {}).get("response") or {}
    req = st.get("requests") or {}
    limit, akt = req.get("limit_day"), req.get("current")
    if not isinstance(limit, int) or not isinstance(akt, int):
        return 3, None
    rest = max(0, limit - akt)
    mitternacht = (JETZT + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    laeufe = max(1, int((mitternacht - JETZT).total_seconds() // 300))
    return max(0, min(MAX_JE_LAUF, int(rest * ANTEIL / laeufe))), {"limit": limit, "verbraucht": akt}


def zuordnen(sp, fixtures):
    from betfair_name_bridge import pair_matches, compatible
    ko = _zeit(sp.get("anpfiff"))
    streng, locker = [], []
    for fx in fixtures:
        t = fx.get("teams") or {}
        h, a = (t.get("home") or {}).get("name"), (t.get("away") or {}).get("name")
        fko = _zeit((fx.get("fixture") or {}).get("date"))
        if not (h and a and ko and fko) or abs((fko - ko).total_seconds()) > ANPFIFF_TOLERANZ_MIN * 60:
            continue
        if pair_matches(sp.get("heim"), sp.get("gast"), h, a):
            streng.append(fx)
        elif any(compatible(x, y) for x in (sp.get("heim"), sp.get("gast")) for y in (h, a)):
            locker.append(fx)
    if streng:
        return streng[0] if len(streng) == 1 else None
    return locker[0] if len(locker) == 1 else None


def statistik(resp, heim_id):
    aus = {}
    for block in resp or []:
        seite = "heim" if ((block or {}).get("team") or {}).get("id") == heim_id else "gast"
        werte = {}
        for s in block.get("statistics") or []:
            feld, v = STAT_FELDER.get(s.get("type")), s.get("value")
            if not feld or v in (None, ""):
                continue
            if isinstance(v, str):
                try:
                    v = float(v.replace("%", "").strip())
                except ValueError:
                    continue
            werte[feld] = v
        if werte:
            aus[seite] = werte
    return aus if len(aus) == 2 else None


def kandidaten(d):
    """Live-Spiele in der Reihenfolge, in der eine Statistik am meisten hilft."""
    spiele = {s["id"]: s for s in (d.get("radar") or {}).get("spiele") or [] if s.get("live") and not s.get("entschieden")}
    rang = []
    for quelle in (d.get("serien") or [], d.get("liveZufluss") or [], d.get("halbzeit") or []):
        for x in quelle:
            i = str(x.get("id") or "")
            if i in spiele and i not in rang:
                rang.append(i)
    for t in sorted((d.get("toepfe") or []), key=lambda t: -(t.get("gesamt") or 0)):
        i = str(t.get("id") or "")
        if t.get("laeuft") and i in spiele and i not in rang:
            rang.append(i)
    for s in sorted(spiele.values(), key=lambda s: -(s.get("d30") or 0)):
        if s["id"] not in rang:
            rang.append(s["id"])
    return [spiele[i] for i in rang]


def main() -> int:
    try:
        d = json.loads(DATEN.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print("live_stats: keine data.json", exc)
        return 0
    try:
        cache = json.loads(CACHE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        cache = {}
    cache = {k: v for k, v in cache.items() if JETZT - (_zeit(v.get("ts")) or JETZT) < timedelta(hours=3)}
    kand = kandidaten(d)
    info = None
    if kand and os.environ.get("APISPORTS_KEY"):
        n, info = budget()
        offen = [s for s in kand if JETZT - (_zeit((cache.get(s["id"]) or {}).get("ts")) or JETZT - timedelta(days=1)) > timedelta(minutes=FRISCH_MIN)]
        if n >= 2 and offen:
            fixtures = (get("/fixtures?live=all") or {}).get("response") or []
            n -= 1
            for sp in offen:
                if n <= 0:
                    break
                fx = zuordnen(sp, fixtures)
                if not fx:
                    continue
                fid = (fx.get("fixture") or {}).get("id")
                hid = ((fx.get("teams") or {}).get("home") or {}).get("id")
                r = get("/fixtures/statistics?fixture=%s" % fid)
                n -= 1
                st = statistik((r or {}).get("response"), hid)
                if st:
                    cache[sp["id"]] = {"ts": JETZT.isoformat(), "fixture": fid, "statistik": st,
                                       "min": ((fx.get("fixture") or {}).get("status") or {}).get("elapsed"),
                                       "stand": [(fx.get("goals") or {}).get("home"), (fx.get("goals") or {}).get("away")]}
    CACHE.parent.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    live_ids = {s["id"] for s in kand}
    d["liveStats"] = {k: v for k, v in cache.items() if k in live_ids and JETZT - _zeit(v["ts"]) < timedelta(minutes=GUELTIG_MIN)}
    d["liveStatsInfo"] = {"stand": JETZT.isoformat(), "kontingent": info, "n": len(d["liveStats"])}
    DATEN.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"live_stats: {len(d['liveStats'])} Spiele mit Statistik, Kandidaten {len(kand)}, Kontingent {info}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
