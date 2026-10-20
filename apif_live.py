#!/usr/bin/env python3
"""
apif_live.py — Live-Statistik zur Halbzeit + Team-Serien rueckwirkend aus API-Football. 04.10.2026.

Lucas: „die Football-App hat Live-Stats — schau mal, ob du da was rauskriegst" und „die Serie ist
keine Bedingung — kann man das noch herleiten oder erst ab jetzt eine Serie aufbauen?"

Beides kommt aus derselben Quelle, mit wenigen Aufrufen je HZ-0:0-Fund:
    /fixtures?live=all                 EIN Aufruf je Lauf: alle Live-Spiele (Stand, Minute, Teams)
    /fixtures/statistics?fixture=ID    je Fund: Schuesse, aufs Tor, Ecken, Ballbesitz, xG (je Liga)
    /fixtures?team=ID&last=N           je Team: die letzten N Spiele mit HZ- und Endstand
Die Serie gibt es damit RUECKWIRKEND — nicht erst ab dem Tag, an dem unser Archiv anfing.

Zuordnung Betfair -> API-Football bewusst ENG (betfair_name_bridge.pair_matches + Anpfiff
+-20 Min): ein falsch zugeordnetes Spiel waere schlimmer als keine Statistik.

Was die Statistik in kleinen Ligen liefert, weiss vorher niemand — API-Football fuehrt die
Abdeckung je Liga. Deshalb zaehlt hz_finder mit (gematcht / mit Statistik / mit Serie), statt es
zu behaupten. Reine Anreicherung: faellt hier etwas aus, laeuft der Finder unveraendert weiter.
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone

BASE_URL = "https://v3.football.api-sports.io"
SERIE_N = 8
ANPFIFF_TOLERANZ_MIN = 20
STAT_FELDER = {"Shots on Goal": "aufsTor", "Total Shots": "schuesse", "Corner Kicks": "ecken",
               "Ball Possession": "ballbesitz", "expected_goals": "xg", "Dangerous Attacks": "gefAngriffe"}


def http_get(pfad, key=None, timeout=12):
    """GET gegen API-Football -> dict oder None (nie Exception nach aussen)."""
    key = key or os.environ.get("APISPORTS_KEY", "")
    if not key:
        return None
    try:
        req = urllib.request.Request(BASE_URL + pfad, headers={"x-apisports-key": key})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read())
        if isinstance(d, dict) and d.get("errors") and not d.get("response"):
            print("[apif_live] API-Fehler:", str(d.get("errors"))[:160])
            return None
        return d
    except Exception as e:  # noqa: BLE001
        print("[apif_live] Abruf fehlgeschlagen:", pfad[:60], type(e).__name__)
        return None


def _zeit(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def zuordnen(spiel, live_fixtures):
    """Betfair-Spiel -> API-Football-Fixture (oder None). REIN.

    Erst streng: beide Teams passen (in einer Richtung) UND der Anpfiff +-20 Min.

    12.10.2026 (Lucas: „schau, ob wir bei Football-API die richtigen Ligen haben"). 53 von 138
    HZ-Funden blieben ohne API-Football — darunter „Man Utd – Tottenham", „Liverpool – Man City",
    „FC Nordsjaelland – OB". API-Football fuehrt diese Ligen; gescheitert ist der Namensabgleich
    an EINER Seite („Man Utd"/„Manchester United", „OB"/„Odense", „Skovde"/„Skövde AIK").
    Deshalb zweite Stufe: EIN Team passt, der Anpfiff passt, und unter allen gerade laufenden
    Spielen ist es das EINZIGE solche. Ein Team spielt nicht zweimal zur selben Zeit — die
    Eindeutigkeit ist der Schutz, nicht die Schreibweise.
    """
    from betfair_name_bridge import pair_matches, compatible
    ko = _zeit(spiel.get("kickoff"))
    streng, locker = [], []
    for fx in live_fixtures or []:
        t = fx.get("teams") or {}
        h, a = (t.get("home") or {}).get("name"), (t.get("away") or {}).get("name")
        if not (h and a):
            continue
        fko = _zeit((fx.get("fixture") or {}).get("date"))
        if not (ko and fko) or abs((fko - ko).total_seconds()) > ANPFIFF_TOLERANZ_MIN * 60:
            # ohne beide Anpfiffzeiten nur die strenge Stufe (wie bisher)
            if ko and fko:
                continue
            if pair_matches(spiel.get("home"), spiel.get("away"), h, a):
                streng.append(fx)
            continue
        if pair_matches(spiel.get("home"), spiel.get("away"), h, a):
            streng.append(fx)
        elif any(compatible(x, y) for x in (spiel.get("home"), spiel.get("away")) for y in (h, a)):
            locker.append(fx)
    if streng:
        return streng[0] if len(streng) == 1 else None    # zwei Kandidaten = nicht raten
    return locker[0] if len(locker) == 1 else None


def statistik(stat_response, heim_id):
    """/fixtures/statistics -> {"heim": {...}, "gast": {...}}. REIN."""
    aus = {}
    for block in stat_response or []:
        tid = ((block or {}).get("team") or {}).get("id")
        seite = "heim" if tid == heim_id else "gast"
        werte = {}
        for s in (block.get("statistics") or []):
            feld = STAT_FELDER.get(s.get("type"))
            v = s.get("value")
            if not feld or v in (None, ""):
                continue
            if isinstance(v, str):
                v = v.replace("%", "").strip()
                try:
                    v = float(v)
                except ValueError:
                    continue
            werte[feld] = v
        if werte:
            aus[seite] = werte
    return aus if len(aus) == 2 else None


def serie_aus_fixtures(fixtures, team_id):
    """Letzte Spiele eines Teams -> Serie aus SEINER Sicht. REIN."""
    spiele = []
    for fx in fixtures or []:
        sc = fx.get("score") or {}
        ft, ht = sc.get("fulltime") or {}, sc.get("halftime") or {}
        if ft.get("home") is None or ft.get("away") is None:
            continue
        heim = ((fx.get("teams") or {}).get("home") or {}).get("id") == team_id
        tore, gegen = (ft["home"], ft["away"]) if heim else (ft["away"], ft["home"])
        h1 = (ht.get("home") or 0) + (ht.get("away") or 0) if ht.get("home") is not None else None
        spiele.append({"d": str((fx.get("fixture") or {}).get("date") or "")[:10], "tore": tore,
                       "gegen": gegen, "h1": h1, "h2": (tore + gegen - h1) if h1 is not None else None})
    spiele.sort(key=lambda s: s["d"], reverse=True)
    if not spiele:
        return None
    n = len(spiele)
    mit_h = [s for s in spiele if s["h2"] is not None]
    return {"n": n, "quelle": "api-football",
            "over25": sum(1 for s in spiele if s["tore"] + s["gegen"] >= 3),
            "btts": sum(1 for s in spiele if s["tore"] > 0 and s["gegen"] > 0),
            "ht00": sum(1 for s in mit_h if s["h1"] == 0),
            "torIn2hz": sum(1 for s in mit_h if s["h2"] > 0), "nHz": len(mit_h),
            "siege": sum(1 for s in spiele if s["tore"] > s["gegen"]),
            "toreSchnitt": round(sum(s["tore"] + s["gegen"] for s in spiele) / n, 1),
            "toreFuer": round(sum(s["tore"] for s in spiele) / n, 1),      # 10.10.2026, s. team_archiv
            "toreGegen": round(sum(s["gegen"] for s in spiele) / n, 1),
            "form": "".join("S" if s["tore"] > s["gegen"] else "U" if s["tore"] == s["gegen"] else "N"
                            for s in spiele)}


def anreichern(funde, get=None, max_aufrufe=25):
    """Je Fund: Live-Statistik + rueckwirkende Serie beider Teams. Schreibt in die Funde. -> Zaehler.

    `get(pfad)` injizierbar (Tests). Gedeckelt: hoechstens `max_aufrufe` Aufrufe je Lauf."""
    get = get or http_get
    z = {"funde": len(funde), "gematcht": 0, "mitStatistik": 0, "mitSerie": 0, "aufrufe": 0}
    if not funde:
        return z
    live = get("/fixtures?live=all")
    z["aufrufe"] += 1
    fixtures = (live or {}).get("response") or []
    if not fixtures:
        return z
    for f in funde:
        if z["aufrufe"] >= max_aufrufe:
            break
        fx = zuordnen(f, fixtures)
        if not fx:
            continue
        z["gematcht"] += 1
        fid = (fx.get("fixture") or {}).get("id")
        t = fx.get("teams") or {}
        hid, aid = (t.get("home") or {}).get("id"), (t.get("away") or {}).get("id")
        f["apif"] = {"fixture": fid, "liga": (fx.get("league") or {}).get("name"),
                     "stand": [(fx.get("goals") or {}).get("home"), (fx.get("goals") or {}).get("away")]}
        st = get("/fixtures/statistics?fixture=%s" % fid)
        z["aufrufe"] += 1
        stat = statistik((st or {}).get("response"), hid)
        if stat:
            f["apif"]["statistik"] = stat
            z["mitStatistik"] += 1
        serien = {}
        for seite, tid in (("heim", hid), ("gast", aid)):
            if not tid or z["aufrufe"] >= max_aufrufe:
                continue
            r = get("/fixtures?team=%s&last=%d" % (tid, SERIE_N))
            z["aufrufe"] += 1
            s = serie_aus_fixtures((r or {}).get("response"), tid)
            if s:
                serien[seite] = s
        if serien:
            f["apif"]["serie"] = serien
            z["mitSerie"] += 1
    return z
