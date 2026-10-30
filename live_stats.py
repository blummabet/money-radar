#!/usr/bin/env python3
"""
live_stats.py — Live-Statistik (xG, Schüsse aufs Tor, Ecken) aus API-Football für genau die Live-Spiele,
die der Geldradar zeigt oder pusht (Lucas 19.10.2026: „Secret ist drin").

Läuft im Seitenbau NACH build.py und VOR push.py: liest site/data.json, holt für die wichtigsten
Live-Spiele die Statistik und schreibt sie als "liveStats" {Betfair-ID: {...}} zurück. push.py und die
Seite lesen von dort.

Kontingent: der Schlüssel ist derselbe wie im alten Projekt (HZ-Finder). /status kostet nichts und sagt,
wie viel heute schon verbraucht ist. Je Lauf gönnen wir uns höchstens 60 % dessen, was bis Mitternacht
(UTC) pro Lauf übrig ist, und nie mehr als 40 Abrufe (25.10.: vorher 20, solange der HZ-Finder des alten Projekts mitlief).

Zuordnung Betfair → API-Football bewusst ENG (betfair_name_bridge, Anpfiff ±20 Min, bei zwei Kandidaten
nichts): eine falsche Statistik wäre schlimmer als keine. Fällt hier etwas aus, läuft alles andere weiter.
"""
from __future__ import annotations

import json
import math
import os
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASIS = Path(__file__).resolve().parent
DATEN = BASIS / "site" / "data.json"
CACHE = BASIS / ".bf" / "live_stats.json"
BASE_URL = "https://v3.football.api-sports.io"
MAX_JE_LAUF = 40         # 25.10.: HZ-Finder im alten Projekt abgedreht → 40 × 288 ≈ 11.500 von 75.000/Tag (60-%-Regel bremst weiter)
ANTEIL = 0.6
FRISCH_MIN = 9          # jünger als das → nicht neu abrufen
GUELTIG_MIN = 25        # älter als das → nicht mehr zeigen
ANPFIFF_TOLERANZ_MIN = 20
STAT_FELDER = {"Shots on Goal": "aufsTor", "Total Shots": "schuesse", "Corner Kicks": "ecken",
               "Ball Possession": "ballbesitz", "expected_goals": "xg", "Dangerous Attacks": "gefAngriffe",
               # 30.10. (Lucas: „Schüsse im Strafraum — schau, ob die geliefert werden"): erst messen, wie oft sie da sind
               "Shots insidebox": "strafraum", "Shots outsidebox": "draussen",
               # 30.10. (Lucas: „alle relevanten Daten"): Rote Karten (Überzahl ändert alles), geblockte Schüsse,
               # Paraden des Gegner-Torwarts (= Schüsse, die wirklich gefährlich waren)
               "Red Cards": "rot", "Blocked Shots": "geblockt", "Goalkeeper Saves": "paraden"}
JETZT = datetime.now(timezone.utc)
TICKER = BASIS / ".bf" / "druck_ticker.json"


# ── Druck-Radar (Lucas 25.10.: „Spiele, wo es wirklich zur Sache geht — hoher xG-Wert und wenig Tore, auch wenn
#    ein Team schon 1:0 führt und das andere viel xG hat") ─────────────────────────────────────────────────────
# Je Team: xG minus eigene Tore (Druck ohne Ertrag) + was in den letzten ~15 Min dazukam (xG, Schüsse aufs Tor,
# Ecken). Index 0–100 = 100 · (1 − e^(−Wert/1,2)): 1,0 Überhang + 0,4 xG in 15 Min ≈ 70. Das Spiel bekommt den
# höheren Team-Wert. Ohne xG (nicht jede Liga hat es) ein Ersatz aus Schüssen: 0,06 je Schuss + 0,22 je Schuss
# aufs Tor — so gekennzeichnet („geschätzt").
def _xg(w):
    if isinstance(w.get("xg"), (int, float)):
        return float(w["xg"]), True
    s, a = w.get("schuesse"), w.get("aufsTor")
    if isinstance(s, (int, float)) or isinstance(a, (int, float)):
        return round(0.06 * float(s or 0) + 0.22 * float(a or 0), 2), False
    return None, False


def _punkt(e):
    st = e.get("statistik") or {}
    h, g = st.get("heim") or {}, st.get("gast") or {}
    xh, echt_h = _xg(h)
    xa, echt_a = _xg(g)
    return {"m": e.get("min"), "st": e.get("stand"), "xg": [xh, xa], "echt": bool(echt_h and echt_a),
            "at": [h.get("aufsTor"), g.get("aufsTor")], "eck": [h.get("ecken"), g.get("ecken")],
            "sch": [h.get("schuesse"), g.get("schuesse")]}


def _zahl(v):
    return float(v) if isinstance(v, (int, float)) else 0.0


def _proxy(q, i):
    s, a = (q.get("sch") or [None, None])[i], (q.get("at") or [None, None])[i]
    if not isinstance(s, (int, float)) and not isinstance(a, (int, float)):
        return None
    return round(0.06 * _zahl(s) + 0.22 * _zahl(a), 2)


def einheitlich(vl):
    """Lucas 30.10. (Druck-Check): liefert API-Football xG nicht in jedem Abruf, mischte die Reihe echte xG mit dem
    Schuss-Schätzer (CSKA: 0,28 → 0,06 → 0,40). Ein Sprung zwischen den beiden Maßstäben ist kein Druck. Ist auch nur ein
    Punkt geschätzt, rechnet die ganze Reihe mit dem Schätzer. REIN."""
    if all(q.get("echt") for q in vl):
        return vl, True
    aus = []
    for q in vl:
        x = [_proxy(q, 0), _proxy(q, 1)]
        aus.append({**q, "xg": [x[0] if x[0] is not None else q["xg"][0], x[1] if x[1] is not None else q["xg"][1]], "echt": False})
    return aus, False


def druck_von(e):
    """Index je Team und fürs Spiel aus dem jüngsten Punkt + dem Punkt ~15 Min davor. REIN."""
    vl, echt = einheitlich(e.get("verlauf") or [])
    if not vl:
        return None
    p = vl[-1]
    m = p.get("m")
    if not isinstance(m, (int, float)) or p["xg"][0] is None or p["xg"][1] is None:
        return None
    vor = [q for q in vl[:-1] if isinstance(q.get("m"), (int, float)) and 8 <= m - q["m"] <= 25]
    q = min(vor, key=lambda q: abs(m - 15 - q["m"])) if vor else None
    f = 15 / (m - q["m"]) if q else 0
    stand = p.get("st") or [0, 0]
    teams = {}
    for i, seite in enumerate(("heim", "gast")):
        xg = p["xg"][i]
        ueber = max(0.0, xg - _zahl(stand[i]))
        # Lucas 25.10. (Porto B 1:0, Index 75 mit xG 0,9): das Tor selbst steckte im xG und im Schuss aufs Tor der
        # letzten 15 Min — Druck, der schon „bezahlt" ist. Tore im Fenster ziehen deshalb je 1 xG und 1 Schuss aufs Tor ab.
        tore_fenster = max(0.0, _zahl(stand[i]) - _zahl((q.get("st") or [0, 0])[i])) if q else 0.0
        dxg = max(0.0, xg - _zahl(q["xg"][i]) - tore_fenster) * f if q and q["xg"][i] is not None else 0.0
        dat = max(0.0, _zahl(p["at"][i]) - _zahl(q["at"][i]) - tore_fenster) * f if q else 0.0
        deck = max(0.0, _zahl(p["eck"][i]) - _zahl(q["eck"][i])) * f if q else 0.0
        wert = ueber + 1.2 * dxg + 0.12 * dat + 0.04 * deck
        teams[seite] = {"xg": round(xg, 2), "tore": stand[i], "aufsTor": p["at"][i], "ecken": p["eck"][i],
                        "dxg15": round(dxg, 2), "dat15": round(dat, 1), "deck15": round(deck, 1),
                        "index": round(100 * (1 - math.exp(-wert / 1.2)))}
    seite = max(teams, key=lambda k: teams[k]["index"])
    return {"index": teams[seite]["index"], "seite": seite, "teams": teams, "min": m, "stand": stand,
            "quelle": "xG" if echt else "geschätzt", "frisch15": bool(q)}


def _tick(t, sp, art, text, seite=None):
    return {"t": JETZT.isoformat(), "m": t, "id": sp["id"], "art": art, "seite": seite, "text": text,
            "spiel": f"{sp.get('heim')} – {sp.get('gast')}"}


def ereignisse(sp, alt, neu, idx_alt, idx_neu, verlauf=None):
    """Ticker-Zeilen aus zwei aufeinanderfolgenden Punkten desselben Spiels. REIN.
    30.10. (El Gounah „xG +0,8 in 8 Min" — direkt nach dem eigenen 0:1): API-Football trägt das xG eines Tors oft erst
    im nächsten Abruf nach. Tore der letzten ~15 Min ziehen deshalb je 1 xG ab, wie im Index."""
    aus = []
    vor15 = [q for q in (verlauf or []) if isinstance(q.get("m"), (int, float)) and isinstance(neu.get("m"), (int, float))
             and 0 < neu["m"] - q["m"] <= 25]
    basis_st = (min(vor15, key=lambda q: q["m"]).get("st") if vor15 else None) or (alt or {}).get("st") or [0, 0]
    if not alt or not isinstance(neu.get("m"), (int, float)) or not isinstance(alt.get("m"), (int, float)):
        return aus
    dm = neu["m"] - alt["m"]
    if dm < 3 or dm > 30:
        return aus
    for i, seite in enumerate(("heim", "gast")):
        team = sp.get(seite)
        dat = _zahl(neu["at"][i]) - _zahl(alt["at"][i])
        tore15 = max(0.0, _zahl((neu.get("st") or [0, 0])[i]) - _zahl(basis_st[i]))
        dxg = (neu["xg"][i] or 0) - (alt["xg"][i] or 0) - tore15 if neu.get("echt") and alt.get("echt") else 0
        deck = _zahl(neu["eck"][i]) - _zahl(alt["eck"][i])
        if (neu.get("st") or [0, 0])[i] is not None and (alt.get("st") or [0, 0])[i] is not None \
                and _zahl(neu["st"][i]) > _zahl(alt["st"][i]):
            aus.append(_tick(neu["m"], sp, "tor", f"Tor {team} · {neu['st'][0]}:{neu['st'][1]}", seite))
        teile = ([f"{int(dat)} Schüsse aufs Tor"] if dat >= 3 else []) \
            + ([f"xG +{dxg:.1f}".replace(".", ",")] if dxg >= 0.5 else []) + ([f"{int(deck)} Ecken"] if deck >= 3 else [])
        if teile:
            aus.append(_tick(neu["m"], sp, "druck", f"{team}: {' · '.join(teile)} in {int(dm)} Min", seite))
    if idx_neu and idx_neu["index"] >= 70 and (not idx_alt or idx_alt.get("index", 0) < 70):
        s = idx_neu["seite"]
        t = idx_neu["teams"][s]
        aus.append(_tick(neu["m"], sp, "index", f"Druck-Index {idx_neu['index']} · {sp.get(s)} xG {t['xg']:.1f} bei "
                         f"{idx_neu['stand'][0]}:{idx_neu['stand'][1]}".replace(".", ","), s))
    return aus


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


PRUEF = BASIS / ".bf" / "apif_pruefung.json"


def apif_pruefung(fixtures):
    """Lucas 25.10.: „welche Ligen sind bei uns dabei, und bietet API-Football Pinnacle-Quoten an?" — alle 6 h:
    /leagues?current=true (Abdeckung je Liga), /odds/bookmakers, und für ein paar laufende/heutige Spiele /odds mit
    Pinnacle. Kostet ~5 Abrufe je 6 h."""
    try:
        alt = json.loads(PRUEF.read_text(encoding="utf-8"))
        if JETZT - (_zeit(alt.get("stand")) or JETZT - timedelta(days=1)) < timedelta(hours=6):
            return alt
    except Exception:  # noqa: BLE001
        pass
    ligen = (get("/leagues?current=true", timeout=30) or {}).get("response") or []
    zeilen = []
    for x in ligen:
        lg, land = x.get("league") or {}, (x.get("country") or {}).get("name")
        sz = next((s for s in x.get("seasons") or [] if s.get("current")), {}) or {}
        cv = sz.get("coverage") or {}
        fx = cv.get("fixtures") or {}
        if lg.get("type") != "League" and lg.get("type") != "Cup":
            continue
        zeilen.append({"id": lg.get("id"), "name": lg.get("name"), "land": land, "typ": lg.get("type"),
                       "statistik": bool(fx.get("statistics_fixtures")), "events": bool(fx.get("events")),
                       "aufstellung": bool(fx.get("lineups")), "quoten": bool(cv.get("odds"))})
    books = (get("/odds/bookmakers") or {}).get("response") or []
    pin = next((b for b in books if "pinnacle" in str(b.get("name") or "").lower()), None)
    probe = []
    if pin:
        for fx in (fixtures or [])[:3]:
            fid = (fx.get("fixture") or {}).get("id")
            r = (get(f"/odds?fixture={fid}&bookmaker={pin['id']}") or {}).get("response") or []
            probe.append({"spiel": f"{((fx.get('teams') or {}).get('home') or {}).get('name')} – {((fx.get('teams') or {}).get('away') or {}).get('name')}",
                          "liga": (fx.get("league") or {}).get("name"), "pinnacleQuoten": bool(r),
                          "maerkte": [b.get("name") for bk in (r[0].get("bookmakers") or []) for b in bk.get("bets") or []][:12] if r else []})
    aus = {"stand": JETZT.isoformat(), "ligen": len(zeilen), "mitStatistik": sum(1 for z in zeilen if z["statistik"]),
           "mitQuoten": sum(1 for z in zeilen if z["quoten"]), "ohneStatistik": [f"{z['land']}: {z['name']}" for z in zeilen if not z["statistik"]][:400],
           "buchmacher": [b.get("name") for b in books], "pinnacle": pin, "probe": probe}
    PRUEF.write_text(json.dumps(aus, ensure_ascii=False), encoding="utf-8")
    return aus


def druck_radar(d, cache, live_ids, neue_ticks):
    """Für die Seite: die Live-Spiele nach Druck-Index, mit Verlauf (Grafik), Geld je 5 Min und dem Ticker."""
    spiele = {s["id"]: s for s in (d.get("radar") or {}).get("spiele") or []}
    zufl = {str(z.get("id")): z for z in d.get("liveZufluss") or []}
    try:
        ticker = json.loads(TICKER.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        ticker = {"zeilen": [], "geld": []}
    aus = []
    for i, e in cache.items():
        sp = spiele.get(i)
        dr = e.get("druck")
        if i not in live_ids or not sp or not dr or JETZT - _zeit(e["ts"]) > timedelta(minutes=GUELTIG_MIN):
            continue
        z = zufl.get(i)
        aus.append({"id": i, "heim": sp.get("heim"), "gast": sp.get("gast"), "liga": sp.get("liga"), "land": sp.get("land"),
                    "top5": sp.get("top5"), **dr,
                    "verlauf": [[q["m"], q["xg"][0], q["xg"][1], q.get("st")] for q in einheitlich(e.get("verlauf") or [])[0]
                                if isinstance(q.get("m"), (int, float)) and q["xg"][0] is not None],
                    "geldMin": sp.get("geldMin"), "eur": sp.get("eur"), "xnorm": sp.get("xnorm"),
                    "zufluss": ({"eur": z.get("zuflussEur"), "markt": (z.get("fluss") or [{}])[0].get("markt"),
                                 "auswahl": (z.get("fluss") or [{}])[0].get("auswahl"),
                                 "quote": (z.get("fluss") or [{}])[0].get("quote")} if z else None)})
        # Geld an einem Druck-Spiel → eigene Ticker-Zeile (je Spiel + Auswahl höchstens alle 15 Min)
        if z and dr["index"] >= 40:
            f0 = (z.get("fluss") or [{}])[0]
            k = f"{i}|{f0.get('auswahl')}"
            zuletzt = max((t for kk, t in ticker.get("geld") or [] if kk == k), default=None)
            if not zuletzt or JETZT - _zeit(zuletzt) > timedelta(minutes=15):
                # 30.10. („+€4,3k auf CSKA · Druck 79" — die 79 waren Spartaks Druck): der Index der Seite, auf die das Geld geht
                aw = str(f0.get("auswahl") or "")
                k_s = "heim" if aw == sp.get("heim") else "gast" if aw == sp.get("gast") else None
                if k_s:
                    i_s = dr["teams"][k_s]["index"]
                    zusatz = f"Druck {sp.get(k_s)} {i_s}" if i_s >= 40 else f"gegen den Druck ({sp.get(dr['seite'])} {dr['index']})"
                elif aw.lower().startswith("over"):
                    i_s, zusatz = dr["index"], f"Druck {dr['index']}"
                else:
                    i_s, zusatz = dr["index"], f"gegen den Druck ({sp.get(dr['seite'])} {dr['index']})"
                tk = _tick(dr["min"], sp, "geld", f"+€{round((z.get('zuflussEur') or 0) / 1000, 1):g}k auf "
                           f"{f0.get('auswahl')} @ {f0.get('quote')} · {zusatz}".replace(".", ","))
                tk.update({"eur": z.get("zuflussEur"), "auswahl": f0.get("auswahl"), "quote": f0.get("quote"), "index": i_s})
                neue_ticks.append(tk)
                ticker.setdefault("geld", []).append([k, JETZT.isoformat()])
    aus.sort(key=lambda x: -x["index"])
    grenze = JETZT - timedelta(minutes=120)
    zeilen = [t for t in (ticker.get("zeilen") or []) + neue_ticks if (_zeit(t.get("t")) or grenze) > grenze]
    ticker = {"zeilen": zeilen[-60:], "geld": [g for g in ticker.get("geld") or [] if (_zeit(g[1]) or grenze) > grenze]}
    TICKER.write_text(json.dumps(ticker, ensure_ascii=False), encoding="utf-8")
    return {"stand": JETZT.isoformat(), "spiele": aus[:12], "ticker": list(reversed(zeilen[-25:]))}


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
    neue_ticks = []
    diag = {"kandidaten": len(kand), "faellig": 0, "ohneZuordnung": [], "ohneStatistik": [], "abgerufen": 0, "budget": None}
    if kand and os.environ.get("APISPORTS_KEY"):
        n, info = budget()
        # 25.10.: Ligen ohne Statistik bei API-Football (z. B. 3. Ligen, Reserven, Hongkong-Pokal) nicht jeden Lauf neu
        # fragen — 30 Min Pause, dann noch einmal (manche liefern erst ab einer gewissen Minute)
        ohne = (cache.get("_ohneStatistik") or {}).get("ids") or {}
        ohne = {k: t for k, t in ohne.items() if JETZT - (_zeit(t) or JETZT) < timedelta(minutes=30)}
        offen = [s for s in kand if s["id"] not in ohne
                 and JETZT - (_zeit((cache.get(s["id"]) or {}).get("ts")) or JETZT - timedelta(days=1)) > timedelta(minutes=FRISCH_MIN)]
        diag["faellig"], diag["budget"] = len(offen), n
        if n >= 2 and offen:
            fixtures = (get("/fixtures?live=all") or {}).get("response") or []
            n -= 1
            try:
                d["apifPruefung"] = apif_pruefung(fixtures)
            except Exception as exc:  # noqa: BLE001
                print("live_stats: Prüfung", exc)
            for sp in offen:
                if n <= 0:
                    break
                fx = zuordnen(sp, fixtures)
                if not fx:
                    diag["ohneZuordnung"].append(f"{sp.get('heim')} – {sp.get('gast')}")
                    continue
                fid = (fx.get("fixture") or {}).get("id")
                hid = ((fx.get("teams") or {}).get("home") or {}).get("id")
                r = get("/fixtures/statistics?fixture=%s" % fid)
                n -= 1
                diag["abgerufen"] += 1
                st = statistik((r or {}).get("response"), hid)
                if not st:
                    diag["ohneStatistik"].append(f"{sp.get('heim')} – {sp.get('gast')}")
                    ohne[sp["id"]] = JETZT.isoformat()
                if st:
                    # Abdeckung: in wie vielen Abrufen gibt es echtes xG, Strafraum-Schüsse, beides (48 h)
                    ab = cache.get("_abdeckung") or {"z": []}
                    hat = lambda f: all(isinstance((st.get(k) or {}).get(f), (int, float)) for k in ("heim", "gast"))
                    ab["z"] = [z for z in ab.get("z") or [] if JETZT - (_zeit(z[0]) or JETZT) < timedelta(hours=48)] \
                        + [[JETZT.isoformat(), int(hat("xg")), int(hat("strafraum")), int(hat("gefAngriffe")), int(hat("rot") or hat("paraden"))]]
                    ab["ts"] = JETZT.isoformat()
                    cache["_abdeckung"] = ab
                    alt_e = cache.get(sp["id"]) or {}
                    e = {"ts": JETZT.isoformat(), "fixture": fid, "statistik": st,
                         "min": ((fx.get("fixture") or {}).get("status") or {}).get("elapsed"),
                         "stand": [(fx.get("goals") or {}).get("home"), (fx.get("goals") or {}).get("away")],
                         "verlauf": list(alt_e.get("verlauf") or [])[-30:]}
                    p = _punkt(e)
                    vorher = e["verlauf"][-1] if e["verlauf"] else None
                    if not vorher or vorher.get("m") != p.get("m"):
                        e["verlauf"].append(p)
                    e["druck"] = druck_von(e)
                    neue_ticks.extend(ereignisse(sp, vorher, p, alt_e.get("druck"), e["druck"], e["verlauf"][:-1]))
                    cache[sp["id"]] = e
    if kand and os.environ.get("APISPORTS_KEY"):
        cache["_ohneStatistik"] = {"ts": JETZT.isoformat(), "ids": ohne}
    CACHE.parent.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    live_ids = {s["id"] for s in kand}
    d["liveStats"] = {k: v for k, v in cache.items() if k in live_ids and JETZT - _zeit(v["ts"]) < timedelta(minutes=GUELTIG_MIN)}
    abz = (cache.get("_abdeckung") or {}).get("z") or []
    d["liveStatsInfo"] = {"stand": JETZT.isoformat(), "kontingent": info, "n": len(d["liveStats"]), "diag": diag,
                          "abdeckung48h": {"abrufe": len(abz), "xg": sum(z[1] for z in abz), "strafraum": sum(z[2] for z in abz),
                                           "strafraumOhneXg": sum(1 for z in abz if z[2] and not z[1]),
                                           "gefAngriffe": sum(z[3] for z in abz if len(z) > 3), "karten": sum(z[4] for z in abz if len(z) > 4)}}
    d["druck"] = druck_radar(d, cache, live_ids, neue_ticks)
    DATEN.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"live_stats: {len(d['liveStats'])} Spiele mit Statistik, Kandidaten {len(kand)}, Kontingent {info}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
