#!/usr/bin/env python3
"""build.py — Geldradar: verdichtet die EIGENEN Abrufe zu EINER kleinen Datei (site/data.json).

Seit 20.10.2026 eigenständig (Lucas: „muss autonom im neuen Projekt laufen, egal ob das alte noch da ist"):
    .bf/   Betfair (fetch_betfair, ergebnisse, liga_norm, hz_finder, betfair_direction) — dieser Workflow
    .pin/  Pinnacle-Abgleich (pinnacle_konsens)                                         — dieser Workflow
    .pm/ .pf/  Polymarket (poly_markt, poly_fluss)                                       — Mac-Runner, poly-fluss.yml
    .st/   Stake (stake/)                                                                — Mac-Runner, stake.yml
    .push_state/  Push-Buch (bilanz.py)                                                  — push.py
Aus dem alten Projekt wird nichts mehr gelesen (25.10.: auch der Notbehelf RADAR_ALT ist raus).

Regeln:
- Keine Picks, keine Empfehlung. Gezeigt wird, wo Geld liegt und wer es setzt.
- Jede Zahl bleibt bei ihrer Quelle. Was die Quelle nicht hat, steht als null da, nicht geraten.
- Jede Quelle trägt ihren Stand, damit die Seite zeigen kann, wie frisch sie ist.
"""
from __future__ import annotations

import json
import math
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASIS = Path(__file__).resolve().parent
OUT = BASIS / "site" / "data.json"

JETZT = datetime.now(timezone.utc)

# Lucas, 27.09./04.10./08.10.2026: Fussball, E-Sport, Tennis reichen. US-Sport, Kampfsport und
# Cricket „interessieren mich ueberhaupt nicht"; bei Stake auch kein ATP/WTA (ITF bleibt).
SPORTARTEN = {"Fußball", "E-Sport", "Tennis"}


def sport_ok(sport) -> bool:
    return (sport or "Fußball") in SPORTARTEN


# Lucas 29.10. (Wal-Push „$17k auf Over · Over – Under" — war in Wahrheit ECKEN bei Frosinone – Lecce): Polymarket
# hängt Nebenmärkte als Suffix hinter das Datum („…-2026-10-29-total-corners"). Nur „-more-markets"/„-exact-score"
# waren bekannt; alles andere wurde als eigenes Spiel mit den Teams „Over"/„Under" geführt.
POLY_NEBEN = {"more-markets": "Tore/Handicap", "exact-score": "Ergebnis", "total-corners": "Ecken",
              "halftime-result": "Halbzeit", "btts": "Beide treffen", "total-goals": "Tore", "spread": "Handicap"}


def poly_neben(key):
    """(Basis-Schlüssel bis zum Datum, Nebenmarkt-Name oder None)."""
    m = re.match(r"^(.*?\d{4}-\d{2}-\d{2})-(.+)$", str(key or ""))
    if not m:
        return str(key or ""), None
    suf = m.group(2)
    return m.group(1), POLY_NEBEN.get(suf) or next((v for k, v in POLY_NEBEN.items() if suf.startswith(k)), suf.replace("-", " "))


def poly_key_ok(key) -> bool:
    """Lucas 17.10.: auch bei Polymarket kein ATP/WTA, ITF bleibt."""
    return not str(key or "").startswith(("atp-", "wta-"))


ESOCCER_RX = re.compile(r"valhalla|valkyrie|e-?soccer|esports? battle|gt (sports )?league|\bfifa\b|volta|cyber ?(football|live)|adriatic league", re.I)


def stake_ok(z) -> bool:
    # Lucas 19.10. („Spurs (Arlo) – Chelsea (Dzen) … diese eSoccer-Spiele braucht kein Mensch"): FIFA-Simulationen
    # (Stake-Sport „fifa", Ligen „Valhalla/Valkyrie Cup") laufen bei Stake als E-Sport mit — raus, überall.
    if str(z.get("sport") or "").lower() in ("fifa", "efootball", "esoccer", "ebasketball") \
       or ESOCCER_RX.search(str(z.get("liga") or "")) or ESOCCER_RX.search(str(z.get("ligaSlug") or "")):
        return False
    if not sport_ok(z.get("kat")):
        return False
    liga = str(z.get("liga") or "").upper()
    return not (z.get("kat") == "Tennis" and (liga.startswith("ATP") or liga.startswith("WTA")))


FEHLER: list[str] = []
# Lucas 20.10.: „das muss autonom im neuen Projekt laufen". 25.10. („neues Projekt sauber halten"): der Notbehelf
# RADAR_ALT (Rückfall aufs alte Projekt) ist ganz raus — es gibt keinen Weg mehr, alte Dateien zu lesen.


def eigen(name: str, ordner: str = ".bf", default=None, max_min: float | None = None):
    """Eine Datei, die dieses Repo selbst führt (Actions-Cache). Fehlt sie: default + Notiz."""
    p = Path(__file__).resolve().parent / ordner / name
    try:
        if max_min is not None and (datetime.now().timestamp() - p.stat().st_mtime) > max_min * 60:
            FEHLER.append(f"{ordner}/{name}: älter als {max_min:.0f} Min")
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        FEHLER.append(f"{ordner}/{name}: {str(exc)[:100]}")
        return default


BF_EIGEN = {"an": False, "pin": False, "st": False}


def laden_bf(name: str, ordner: str = ".bf"):
    """Aus dem EIGENEN Abruf (.bf/ Betfair, .pin/ Pinnacle, .st/ Stake). Älter als 40 Min: trotzdem der eigene Stand + Notiz."""
    lokal = Path(__file__).resolve().parent / ordner / name
    try:
        if lokal.exists() and (datetime.now().timestamp() - lokal.stat().st_mtime) < 40 * 60:
            d = json.loads(lokal.read_text(encoding="utf-8"))
            # Ein Pinnacle-Lauf ohne einen einzigen Anker (Schlüssel fehlt, API weg) ist kein Ergebnis.
            if name == "betfair_consensus.json" and not (d or {}).get("covered"):
                raise ValueError("eigener Pinnacle-Lauf ohne Anker")
            BF_EIGEN[{".bf": "an", ".pin": "pin"}.get(ordner, ordner.strip("."))] = True
            return d
    except Exception as exc:  # noqa: BLE001
        FEHLER.append(f".bf/{name}: {str(exc)[:120]}")
    try:                            # lieber der eigene ältere Stand als gar nichts
        return json.loads(lokal.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def ts(s):
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def iso(d):
    return d.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if d else None


def stand(obj, *felder):
    """Zeitstempel einer Quelle aus dem ersten vorhandenen Feld."""
    if not isinstance(obj, dict):
        return None
    for f in felder:
        cur = obj
        for teil in f.split("."):
            cur = cur.get(teil) if isinstance(cur, dict) else None
        if cur:
            return iso(ts(cur))
    return None


def zukunft(kickoff, nach_h=0.0, bis_h=48.0):
    k = ts(kickoff)
    if not k:
        return False
    dt = (k - JETZT).total_seconds() / 3600
    return -nach_h <= dt <= bis_h


# ── Betfair ──────────────────────────────────────────────────────────────────────
def betfair(prices, overview, consensus):
    matches = {str(m.get("matchId")): m for m in (prices or {}).get("matches") or []}
    kons = {str(g.get("matchId")): g for g in (consensus or {}).get("games") or []}

    def verteilung(m):
        """Geld je Ausgang im Match-Odds-Markt (gematchtes Volumen)."""
        mk = ((m or {}).get("markets") or {}).get("Match Odds") or {}
        rs = [r for r in mk.get("runners") or [] if isinstance(r, dict)]
        ges = sum(float(r.get("vol") or 0) for r in rs)
        if ges <= 0:
            return None
        return [{"name": r.get("name"), "quote": r.get("odd"),
                 "eur": round(float(r.get("vol") or 0)),
                 "anteil": round(float(r.get("vol") or 0) / ges * 100)} for r in rs]

    zufluss = []
    for f in (overview or {}).get("flow") or []:
        if not zukunft(f.get("kickoff"), nach_h=0.1, bis_h=30):
            continue
        mid = str(f.get("matchId"))
        m = matches.get(mid)
        k = kons.get(mid) or {}
        zufluss.append({
            "id": mid, "heim": f.get("home"), "gast": f.get("away"), "liga": f.get("league"),
            "land": f.get("country"), "anpfiff": f.get("kickoff"),
            "markt": f.get("market"), "zuflussEur": f.get("deltaEur"), "marktEur": f.get("nowEur"),
            "meistesGeldAuf": f.get("sideName"), "quote": f.get("odd"),
            "quoteRichtung": {"in": "faellt", "out": "steigt"}.get(f.get("dir"), "ruhig"),
            "duenn": bool(f.get("thin")),
            "verteilung": verteilung(m),
            "pinnacle": ({"fav": k.get("pinn", {}).get("fav"), "einig": k.get("agree")} if k.get("pinn") else None),
        })

    groesste = []
    for mid, m in matches.items():
        if not zukunft(m.get("kickoff"), nach_h=0, bis_h=30):
            continue
        v = verteilung(m)
        if not v:
            continue
        top = max(v, key=lambda r: r["eur"])
        groesste.append({"id": mid, "heim": m.get("home"), "gast": m.get("away"), "liga": m.get("league"),
                         "anpfiff": m.get("kickoff"), "volEur": round(float(m.get("totalVol") or 0)),
                         "moEur": sum(r["eur"] for r in v), "verteilung": v,
                         "meistesGeldAuf": top["name"], "anteil": top["anteil"]})
    groesste.sort(key=lambda r: -r["volEur"])

    bewegung = []
    for s in (overview or {}).get("steam") or []:
        if not zukunft(s.get("kickoff"), nach_h=0, bis_h=30):
            continue
        bewegung.append({"id": str(s.get("matchId")), "heim": s.get("home"), "gast": s.get("away"),
                         "liga": s.get("league"), "anpfiff": s.get("kickoff"),
                         "auf": s.get("sideName"), "pp": s.get("pp"), "quote": s.get("odd")})
    return {"zufluss": zufluss[:25], "groesste": groesste[:25], "bewegung": bewegung[:20]}


# ── Sharp Radar (Pinnacle) ───────────────────────────────────────────────────────
def sharp(consensus, history):
    """Pinnacle-Verlauf je Spiel: stetiger LAUF oder einmaliger SPRUNG?

    Sprung = der größte einzelne Schritt macht ≥ 60 % der Gesamtbewegung aus (typisch: News,
    danach flach, eingepreist). Lauf = die Bewegung verteilt sich auf mehrere Schritte.
    Unter 1,5 pp gilt ein Spiel als ruhig und wird nicht gezeigt.
    """
    out = []
    namen = ("Heim", "Remis", "Gast")
    for g in (consensus or {}).get("games") or []:
        if not zukunft(g.get("kickoff"), nach_h=0, bis_h=48):
            continue
        h = [p for p in (history or {}).get(str(g.get("matchId"))) or [] if isinstance(p.get("pinn"), list)]
        if len(h) < 3:
            continue
        erst, letzt = h[0]["pinn"], h[-1]["pinn"]
        diffs = [(letzt[i] - erst[i]) * 100 for i in range(3)]
        i = max(range(3), key=lambda j: diffs[j])          # die Seite, auf die Pinnacle sich bewegt hat
        gesamt = diffs[i]
        if gesamt < 1.5:
            continue
        schritte = [(h[n]["pinn"][i] - h[n - 1]["pinn"][i]) * 100 for n in range(1, len(h))]
        groesster = max(schritte) if schritte else 0
        art = "sprung" if gesamt and groesster / gesamt >= 0.6 else "lauf"
        letzte3 = sum(schritte[-3:])
        # Wann kam der grosse Schritt? Ein Sprung im letzten oder vorletzten Punkt ist FRISCH —
        # da ist der Markt evtl. noch nicht ueberall nachgezogen. Ein alter Sprung ist eingepreist.
        idx = schritte.index(groesster) if schritte else 0
        frisch = idx >= len(schritte) - 2
        po = g.get("poly") or {}
        seite = [g.get("home"), "Remis", g.get("away")][i]
        out.append({
            "id": str(g.get("matchId")), "heim": g.get("home"), "gast": g.get("away"),
            "liga": g.get("league"), "anpfiff": g.get("kickoff"),
            "auf": seite, "ausgang": namen[i],
            "vonPct": round(erst[i] * 100, 1), "nachPct": round(letzt[i] * 100, 1), "pp": round(gesamt, 1),
            "art": art, "laeuftNoch": art == "lauf" and letzte3 >= 0.5, "frisch": art == "sprung" and frisch,
            "fairQuote": round(1 / letzt[i], 2) if letzt[i] else None,
            "vonQuote": round(1 / erst[i], 2) if erst[i] else None,
            "betfairGeld": {"auf": g.get("moneyName"), "anteil": g.get("moneySharePct"),
                            "quote": g.get("moneyOdd")} if g.get("moneyName") else None,
            "poly": {"anteil": po.get("sharePct"), "quote": po.get("odd"), "usd": po.get("vol"), "auf": po.get("sideKey")} if po.get("sharePct") is not None else None,
            "verlauf": [round(p["pinn"][i] * 100, 1) for p in h][-24:],
        })
    out.sort(key=lambda r: (-int(r["laeuftNoch"] or r["frisch"]), -r["pp"]))
    return out[:20]


def pinn_alle(consensus, history):
    """Jedes Spiel mit Pinnacle-Quote: faire Quote jetzt und beim ersten Abruf, je Ausgang."""
    out = []
    for g in (consensus or {}).get("games") or []:
        pn = g.get("pinn") or {}
        if not pn.get("home") or not zukunft(g.get("kickoff"), nach_h=2.2, bis_h=48):
            continue
        jetzt = [pn.get("home"), pn.get("draw"), pn.get("away")]
        h = [p["pinn"] for p in (history or {}).get(str(g.get("matchId"))) or [] if isinstance(p.get("pinn"), list)]
        erst = h[0] if h else jetzt
        q = lambda p: round(1 / p, 2) if p else None
        namen = [g.get("home"), "Remis", g.get("away")]
        aus = []
        for i in range(3):
            aus.append({"name": namen[i], "quote": q(jetzt[i]), "vorher": q(erst[i]),
                        "pp": round((jetzt[i] - erst[i]) * 100, 1) if jetzt[i] and erst[i] else 0,
                        "kurve": [round(x[i] * 100, 1) for x in h][-24:]})
        po = g.get("poly") or {}
        out.append({
            "id": str(g.get("matchId")), "heim": g.get("home"), "gast": g.get("away"), "liga": g.get("league"),
            "land": g.get("country"), "anpfiff": g.get("kickoff"), "live": bool(g.get("live")),
            "ausgaenge": aus, "bewegung": max((abs(a["pp"]) for a in aus), default=0),
            "geld": ({"auf": g.get("moneyName"), "anteil": g.get("moneySharePct"), "eur": g.get("totVol"),
                      "betfair": g.get("moneyOdd"), "pinnacle": g.get("pinnOdd"), "buchmacher": g.get("softOdd"),
                      "nBuecher": g.get("softN"), "poly": po.get("odd")} if g.get("moneyName") else None),
            "polyGeld": ({"auf": po.get("sideKey"), "anteil": po.get("sharePct"), "usd": po.get("vol"),
                          "quelle": po.get("shareSrc")} if po.get("sharePct") is not None else None),
            "einig": g.get("agree"),
        })
    out.sort(key=lambda r: (r["live"], -r["bewegung"]))
    return out


# ── Pinnacle-Scan über alle Spiele (Lucas 18.10.) ───────────────────────────────────
PINN_TOP5_KEYS = {"soccer_epl", "soccer_germany_bundesliga", "soccer_spain_la_liga", "soccer_italy_serie_a",
                  "soccer_france_ligue_one", "soccer_uefa_champs_league", "soccer_uefa_nations_league"}   # 12.11. NL = Top-5
PINN_VOR_MIN_PP = {True: 1.5, False: 2.5}     # vor Anpfiff, seit Eröffnung (Top-5 ist effizienter: kleiner Ausschlag zählt)
PINN_FRISCH_PP = {True: 1.0, False: 1.5}      # davon in der letzten Stunde → „frisch"
PINN_LIVE_MIN_PP = {True: 3.0, False: 4.0}    # live in ~15 Min, nur für ein Team, das NICHT führt
PINN_STAND = BASIS / ".pin" / "pinn_stand.json"


def _bf_zug(m, bf_hist, i, seit):
    """11.11.: Betfair-Sieg-Quote derselben Seite zum Drop-Beginn und jetzt."""
    if not m or not bf_hist:
        return None
    f = ("hw", "dr", "aw")[i]
    sn = [x for x in (bf_hist.get(str(m.get("matchId"))) or []) if isinstance(x, dict) and (x.get("mo") or {}).get(f)]
    if not sn:
        return None
    t0 = ts(seit)
    a = next((x for x in sn if t0 and (ts(x.get("ts")) or JETZT) >= t0), sn[0])
    q_a, q_j = a["mo"][f], sn[-1]["mo"][f]
    if not (q_a and q_j and q_a > 1 and q_j > 1):
        return None
    return {"von": round(q_a, 2), "jetzt": round(q_j, 2), "pp": round((1 / q_j - 1 / q_a) * 100, 1)}


def pinn_drops(prices, bf_hist=None):
    """Pinnacle-Drops aus .pin/pinnacle_alle.json — unabhängig davon, ob Betfair das Spiel groß handelt.

    Vor Anpfiff: Wahrscheinlichkeit jetzt gegen den ersten Stand (bis ~12 h zurück).
    Live: 15-Min-Fenster, und NUR für ein Team, das nicht führt. Die Zeit allein senkt dessen Chance;
    steigt sie trotzdem deutlich, hat Pinnacle eine Information (Rote Karte, Druckphase, Verletzung).
    Remis und Führende fallen raus — bei ihnen steigt die Chance schon durch den Zeitablauf.
    Tor im Fenster (Stand aus dem Betfair-Feed) → raus. Ohne Stand → live gar nicht gezeigt."""
    try:
        roh = json.loads((BASIS / ".pin" / "pinnacle_alle.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"stand": None, "n": 0, "vor": [], "live": []}
    try:
        stand_hist = json.loads(PINN_STAND.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        stand_hist = {}
    bf = [m for m in (prices or {}).get("matches") or [] if isinstance(m, dict)]

    def bf_spiel(e):
        ko = ts(e["anpfiff"])
        for m in bf:
            k2 = ts(m.get("kickoff"))
            if not ko or not k2 or abs((ko - k2).total_seconds()) > 20 * 60:
                continue
            if (_gleich(e["heim"], m.get("home")) and _gleich(e["gast"], m.get("away"), locker=True)) or \
               (_gleich(e["heim"], m.get("home"), locker=True) and _gleich(e["gast"], m.get("away"))):
                return m
        return None

    vor, live, neu_stand = [], [], {}
    for sid, e in (roh.get("spiele") or {}).items():
        jetzt_p = e.get("jetzt")
        ko = ts(e.get("anpfiff"))
        if not jetzt_p or not ko or JETZT - (ts(jetzt_p[0]) or JETZT) > timedelta(minutes=20):
            continue                                   # kein frischer Stand → nichts behaupten
        top5 = e.get("key") in PINN_TOP5_KEYS
        m = bf_spiel(e)
        li = (m or {}).get("liveInfo") or {}
        namen = [e["heim"], "Remis", e["gast"]]
        if e.get("key") == "soccer_uefa_nations_league" and (m or {}).get("league"):
            top5 = bool(TOP5_RX.search(str(m.get("league"))))   # 17.11.: nur Liga A zählt zu den Top-5 (wie bei Betfair)
        basis = {"id": sid, "heim": e["heim"], "gast": e["gast"], "liga": (m or {}).get("league") or e.get("liga"),
                 "land": (m or {}).get("country"), "anpfiff": e["anpfiff"], "top5": top5,
                 "bfId": str(m.get("matchId")) if m else None}
        if ko > JETZT:
            grob = e.get("grob") or []
            if len(grob) < 2:
                continue
            erst = grob[0]
            d = [(jetzt_p[1 + i] - erst[1 + i]) * 100 for i in range(3)]
            i = max(range(3), key=lambda j: d[j])
            if d[i] < PINN_VOR_MIN_PP[top5]:
                continue
            fein = e.get("fein") or []
            vor60 = next((x for x in fein if JETZT - ts(x[0]) <= timedelta(minutes=65)), None)
            pp60 = round((jetzt_p[1 + i] - vor60[1 + i]) * 100, 1) if vor60 else None
            # Lucas 11.11. („Quotendrops auf Betfair mit einbauen — wenn Pinnacle und Betfair auf derselben Seite sind, seh
            # ich das nicht"): Betfair-Sieg-Quote derselben Seite zum Drop-Beginn und jetzt (aus betfair_history)
            bfz = None
            try:
                bfz = _bf_zug(m, bf_hist, i, erst[0])
            except Exception:  # noqa: BLE001
                bfz = None
            _qq = [x[4 + i] for x in grob + fein + [jetzt_p] if isinstance(x[4 + i], (int, float)) and x[4 + i] > 1]
            vor.append({**basis, "auf": namen[i], "ausgang": ("heim", "remis", "gast")[i], "bf": bfz,
                        "tag": [min(_qq), max(_qq)] if _qq else None,
                        "vonPct": round(erst[1 + i] * 100, 1), "nachPct": round(jetzt_p[1 + i] * 100, 1), "pp": round(d[i], 1),
                        "vonQuote": erst[4 + i], "quote": jetzt_p[4 + i], "seit": erst[0], "pp60": pp60,
                        "frisch": bool(pp60 is not None and pp60 >= PINN_FRISCH_PP[top5]),
                        "verlauf": [round(x[1 + i] * 100, 1) for x in grob] + [round(jetzt_p[1 + i] * 100, 1)]})
            continue
        # live
        if not m or li.get("goal_v1") is None or li.get("finished"):
            continue
        sc = [int(li.get("goal_v1") or 0), int(li.get("goal_v2") or 0)]
        sh = (stand_hist.get(sid) or [])
        if not sh or sh[-1][1:] != sc:
            sh = sh + [[JETZT.isoformat(), *sc]]
        neu_stand[sid] = sh[-10:]
        fein = e.get("fein") or []
        ref = next((x for x in fein if timedelta(minutes=10) <= JETZT - ts(x[0]) <= timedelta(minutes=20)), None)
        if not ref:
            continue
        # Lucas 18.10. (Racing – Independiente 89' 1:1: „Live-Drop 12,8 → 6,5" war der Ausgleich selbst): drei Sperren.
        # 1) Ab der 75. Minute nichts mehr — da ist jeder Schritt Spielstand-Mechanik, keine Information.
        if not isinstance(li.get("time"), (int, float)) or li["time"] > 75:
            continue
        # 2) Der Stand muss schon VOR dem Vergleichspunkt bekannt gewesen sein (sonst sehen wir ein Tor im Fenster nicht),
        #    und er darf sich seitdem nicht geändert haben.
        if not sh or not ts(sh[0][0]) or ts(sh[0][0]) > ts(ref[0]):
            continue
        if len(sh) >= 2 and ts(sh[-1][0]) and ts(sh[-1][0]) >= ts(ref[0]) - timedelta(minutes=3):
            continue
        # 3) Pinnacles eigenes Remis verrät ein Tor, auch wenn der Betfair-Stand hinterherhinkt: springt die
        #    Remis-Chance im Fenster um mehr als 12 pp, war es ein Tor (eine Rote Karte bewegt sie viel weniger).
        if abs(jetzt_p[2] - ref[2]) * 100 > 12:
            continue
        for i in (0, 2):
            eigen, gegner = (sc[0], sc[1]) if i == 0 else (sc[1], sc[0])
            if eigen > gegner:
                continue                               # führt → Chance steigt schon durch die Zeit
            d = (jetzt_p[1 + i] - ref[1 + i]) * 100
            if d < PINN_LIVE_MIN_PP[top5]:
                continue
            fmin = round((JETZT - ts(ref[0])).total_seconds() / 60)
            live.append({**basis, "auf": namen[i], "ausgang": ("heim", "remis", "gast")[i],
                         "vonPct": round(ref[1 + i] * 100, 1), "nachPct": round(jetzt_p[1 + i] * 100, 1), "pp": round(d, 1),
                         "vonQuote": ref[4 + i], "quote": jetzt_p[4 + i], "fensterMin": fmin,
                         "min": li.get("time"), "stand": sc,
                         "verlauf": [round(x[1 + i] * 100, 1) for x in fein][-36:]})
    try:
        PINN_STAND.parent.mkdir(parents=True, exist_ok=True)
        PINN_STAND.write_text(json.dumps(neu_stand), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    vor.sort(key=lambda x: (-int(x["frisch"]), -x["pp"]))
    live.sort(key=lambda x: -x["pp"])
    return {"stand": roh.get("stand"), "n": len(roh.get("spiele") or {}), "vor": vor[:40], "live": live[:20]}


# ── 17.11. Pinnacle Asiatisches Handicap + Über/Unter (Lucas: „HD-Märkte checken, ob da bei Pinnacle was passiert,
# beziehungsweise Over-Under genauso") ─────────────────────────────────────────────────────────────────────────────
# Vor Anpfiff, gegen den ersten Stand im 12-h-Fenster (wie die 1X2-Drops):
#   Linie verschoben — die Hauptlinie wandert (z. B. Heim −0,5 → −0,75, Über 2,5 → 2,75). Jeder Viertel-Schritt zählt.
#   Quote fällt      — gleiche Linie, de-viggte Chance einer Seite steigt um die 1X2-Schwelle (2,5 pp, Top-5 1,5 pp).
# Live noch nicht: dort verschieben Tore und Zeit die Linien ohnehin.
def _p2(a, b):
    try:
        x, y = 1 / float(a), 1 / float(b)
        return x / (x + y)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _lz(x):
    return ("+" if x > 0 else "") + (f"{x:g}".replace("-", "−"))


# 17.11. (Lucas: „Flagge des Landes der Liga dazu"): Odds-API-Schlüssel → Ländercode (wie Betfair „country")
_KEY_LAND = (("epl", "GB"), ("efl", "GB"), ("england", "GB"), ("spl", "GB"), ("germany", "DE"), ("spain", "ES"),
             ("italy", "IT"), ("france", "FR"), ("netherlands", "NL"), ("portugal", "PT"), ("belgium", "BE"),
             ("turkey", "TR"), ("greece", "GR"), ("austria", "AT"), ("switzerland", "CH"), ("denmark", "DK"),
             ("norway", "NO"), ("sweden", "SE"), ("poland", "PL"), ("russia", "RU"), ("brazil", "BR"),
             ("argentina", "AR"), ("chile", "CL"), ("mexico", "MX"), ("usa", "US"), ("japan", "JP"), ("korea", "KR"),
             ("australia", "AU"), ("china", "CN"), ("uefa", "International"), ("conmebol", "International"))


def key_land(k):
    k = str(k or "")
    return next((c for w, c in _KEY_LAND if w in k), None)


def _move_info(pts, o, i, l1, art):
    """17.11. (Lucas, aufgeklappte Ansicht): wann kam der Move, Sprung oder Drift, Verlauf, Quote direkt nach dem Move
    gegen jetzt, Gegenseite. pts = Stände [ts, …] zeitlich sortiert, o = Offset der Linie, i = Drop-Seite (0/1)."""
    pts = [x for x in pts if None not in x[o:o + 3]]
    if len(pts) < 2:
        return {}
    jetzt = pts[-1]
    info = {"gegen": jetzt[o + 2 - i], "kurve": [], "typ": None, "moveTs": None, "nachMove": None}
    if art == "linie":
        k = len(pts) - 1
        while k > 0 and pts[k - 1][o] == l1:
            k -= 1
        info["moveTs"] = pts[k][0]
        info["nachMove"] = [pts[k][o + 1 + i], jetzt[o + 1 + i]]
        info["kurve"] = [x[o] for x in pts]
        schritte = [abs(pts[j][o] - pts[j - 1][o]) for j in range(1, len(pts))]
    else:
        auf = [x for x in pts if x[o] == l1]
        pr = [(_p2(x[o + 1], x[o + 2]) if i == 0 else 1 - _p2(x[o + 1], x[o + 2])) for x in auf]
        if len(pr) < 2:
            return info
        schritte = [pr[j] - pr[j - 1] for j in range(1, len(pr))]
        j = max(range(len(schritte)), key=lambda q: schritte[q])
        info["moveTs"] = auf[j + 1][0]
        info["nachMove"] = [auf[j + 1][o + 1 + i], jetzt[o + 1 + i]]
        info["kurve"] = [round(x * 100, 1) for x in pr]
        info["p"] = [round(pr[0] * 100, 1), round(pr[-1] * 100, 1)]
    ges = sum(abs(x) for x in schritte) or 0
    if ges:
        info["typ"] = "Sprung" if max(abs(x) for x in schritte) >= 0.6 * ges else "Drift"
    info["kurve"] = info["kurve"][-60:]
    return info


def pinn_linien(prices=None):
    bf = [m for m in (prices or {}).get("matches") or [] if isinstance(m, dict)]

    def bf_id(e):
        ko = ts(e["anpfiff"])
        for m in bf:
            k2 = ts(m.get("kickoff"))
            if not ko or not k2 or abs((ko - k2).total_seconds()) > 20 * 60:
                continue
            if (_gleich(e["heim"], m.get("home")) and _gleich(e["gast"], m.get("away"), locker=True)) or \
               (_gleich(e["heim"], m.get("home"), locker=True) and _gleich(e["gast"], m.get("away"))):
                return str(m.get("matchId"))
        return None
    try:
        roh = json.loads((BASIS / ".pin" / "pinnacle_alle.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"n": 0, "vor": []}
    out, n, n_ht = [], 0, 0
    # (Prefix, Frische-Grenze, [(Markt, Offset)], 1X2-Offset): Hauptlinien alle 5 Min, Halbzeit je Spiel alle ~15 Min
    SPECS = (("l", 20, (("AH", 1), ("OU", 4)), None), ("h", 35, (("HZ-AH", 4), ("HZ-OU", 7)), 1))
    for sid, e in (roh.get("spiele") or {}).items():
        ko = ts(e.get("anpfiff"))
        if not ko or ko <= JETZT:
            continue
        top5 = e.get("key") in PINN_TOP5_KEYS
        for pre, frisch_min, maerkte, x_off in SPECS:
            lj, lg = e.get(pre + "jetzt"), e.get(pre + "grob") or []
            if not lj or JETZT - (ts(lj[0]) or JETZT) > timedelta(minutes=frisch_min):
                continue
            if pre == "l":
                n += 1
            else:
                n_ht += 1
            if len(lg) < 2:
                continue
            ref = lg[0]
            fein = e.get(pre + "fein") or []
            pts = sorted({x[0]: x for x in lg + fein + [lj]}.values(), key=lambda x: x[0])
            v60 = next((x for x in fein if JETZT - ts(x[0]) <= timedelta(minutes=65)), None)
            basis = {"id": sid, "heim": e["heim"], "gast": e["gast"], "liga": e.get("liga"), "anpfiff": e["anpfiff"],
                     "top5": top5, "seit": ref[0], "land": key_land(e.get("key"))}
            if x_off:                                  # Halbzeit-1X2: Chance jetzt gegen den ersten Stand
                try:
                    p_ = lambda pt: (lambda a: [x / sum(a) for x in a])([1 / pt[x_off + k] for k in range(3)])  # noqa: E731
                    p0, p1 = p_(ref), p_(lj)
                    d = [(p1[k] - p0[k]) * 100 for k in range(3)]
                    k = max(range(3), key=lambda q: d[q])
                    if d[k] >= PINN_VOR_MIN_PP[top5]:
                        pp60 = round((p1[k] - p_(v60)[k]) * 100, 1) if v60 and v60[x_off] else None
                        out.append({**basis, "markt": "HZ", "art": "quote", "auf": [e["heim"], "Remis", e["gast"]][k], "seite": k,
                                    "linie": None, "vonQuote": ref[x_off + k], "quote": lj[x_off + k], "pp": round(d[k], 1),
                                    "pp60": pp60, "frisch": bool(pp60 is not None and pp60 >= PINN_FRISCH_PP[top5]),
                                    "verlauf": [round(p_(x)[k] * 100, 1) for x in lg if None not in x[x_off:x_off + 3]]})
                except (TypeError, ZeroDivisionError, IndexError):
                    pass
            for markt, o in maerkte:
                l0, a0, b0 = ref[o:o + 3]
                l1, a1, b1 = lj[o:o + 3]
                if None in (l0, a0, b0, l1, a1, b1):
                    continue
                ah = markt.endswith("AH")
                seiten = (e["heim"], e["gast"]) if ah else ("Über", "Unter")
                verlauf = [x[o] for x in lg if x[o] is not None] + [l1]
                if l1 != l0:
                    # AH: Linie aus Heimsicht; sinkt sie (−0,5 → −0,75), traut Pinnacle dem Heimteam mehr zu.
                    # O/U: steigt sie, mehr Tore erwartet.
                    i = (0 if l1 < l0 else 1) if ah else (0 if l1 > l0 else 1)
                    frisch = bool(v60 and v60[o] is not None and v60[o] != l1)
                    out.append({**basis, "markt": markt, "art": "linie", "auf": seiten[i], "seite": i,
                                "von": l0, "nach": l1, "schritte": round(abs(l1 - l0) / 0.25),
                                "linie": (l1 if i == 0 else -l1) if ah else l1, "quote": (a1, b1)[i],
                                "frisch": frisch, "verlauf": verlauf})
                    out[-1].update(_move_info(pts, o, i, l1, "linie"))
                    continue
                p0, p1 = _p2(a0, b0), _p2(a1, b1)
                if p0 is None or p1 is None:
                    continue
                d = (p1 - p0) * 100
                i = 0 if d > 0 else 1
                if abs(d) < PINN_VOR_MIN_PP[top5]:
                    continue
                pp60 = None
                if v60 and v60[o] == l1 and _p2(v60[o + 1], v60[o + 2]) is not None:
                    pp60 = round((p1 - _p2(v60[o + 1], v60[o + 2])) * 100 * (1 if i == 0 else -1), 1)
                out.append({**basis, "markt": markt, "art": "quote", "auf": seiten[i], "seite": i,
                            "linie": (l1 if i == 0 else -l1) if ah else l1,
                            "vonQuote": (a0, b0)[i], "quote": (a1, b1)[i], "pp": round(abs(d), 1), "pp60": pp60,
                            "frisch": bool(pp60 is not None and pp60 >= PINN_FRISCH_PP[top5]),
                            "verlauf": [round((_p2(x[o + 1], x[o + 2]) if i == 0 else 1 - _p2(x[o + 1], x[o + 2])) * 100, 1)
                                        for x in lg if x[o] == l1 and _p2(x[o + 1], x[o + 2]) is not None]})
                out[-1].update(_move_info(pts, o, i, l1, "quote"))
    out.sort(key=lambda x: (-int(x["frisch"]), x["art"] != "linie", -(x.get("schritte") or 0), -(x.get("pp") or 0)))
    out = out[:60]
    for x in out:                                  # Betfair-Spiel nur für die gezeigten (Abrechnung der stillen Messung)
        try:
            x["bfId"] = bf_id(x)
        except Exception:  # noqa: BLE001
            x["bfId"] = None
    return {"n": n, "nHz": n_ht, "vor": out}


# ── 17.11. Betfair-Börse Back/Lay aus der Odds API (Lucas: „wir würden back und lays dann sehen") ──────────────────
# Je Betfair-Spiel (matchId) die beste Back- und Lay-Quote aller drei Ausgänge und der letzte Stand vor Anpfiff.
# Die Spanne Lay/Back zeigt, wie dünn ein Markt ist; die Mitte vor Anpfiff ist eine zweite, saubere Schlussquote.
# 17.11. (Lucas, St Kitts – Bonaire: „Pinnacle hat sie nicht … könnten wir es trotzdem in die Liste reinnehmen?
# Betfair ist ein super Indikator"). Regel BD1, vorab festgelegt: Betfair-Drops vor Anpfiff für Spiele, die Pinnacle
# (über die Odds API) NICHT hat. Nur mit echtem Geld und kein Ein-Abruf-Ausreißer:
BD_MAERKTE = re.compile(r"^(Match Odds|Half Time|Over/Under \d\.5 Goals|First Half Goals \d\.5|Both teams to Score\?)$")
BD_MIN_PP = 6.0          # Chance der Auswahl seit dem ersten Stand (bis ~6 h zurück) um ≥ 6 Prozentpunkte gestiegen
BD_MIN_REL = 0.12        # und die Quote um ≥ 12 % gefallen
# Regel BD2 (17.11. 20:20, Lucas: „das Geld ist nicht die Priorität, sondern der Quotendrop"): Geld nur noch als
# Echtheits-Prüfung — €500 auf der Auswahl, €2k im Markt (BD1: €2k / €5k). Dafür zusätzlich: der Drop muss in den
# letzten DREI Abrufen stehen (≈ 10 Min), nicht nur in zweien — eine einzelne Wette verbiegt die Quote nicht so lange.
BD_MIN_EUR_AUSWAHL = 500
BD_MIN_EUR_MARKT = 2000
BD_FRISCH_PP = 3.0       # ≥ 3 pp davon in der letzten Stunde


def _pinn_bf_ids(prices):
    """Betfair-Spiel-IDs, zu denen Pinnacle (pinnacle_alle.json) ein Spiel hat — gleiche Zuordnung wie pinn_drops."""
    try:
        roh = json.loads((BASIS / ".pin" / "pinnacle_alle.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    bf = [m for m in (prices or {}).get("matches") or [] if isinstance(m, dict) and ts(m.get("kickoff"))]
    ids = set()
    for e in (roh.get("spiele") or {}).values():
        ko = ts(e.get("anpfiff"))
        if not ko:
            continue
        for m in bf:
            if abs((ko - ts(m["kickoff"])).total_seconds()) > 20 * 60:
                continue
            if (_gleich(e.get("heim"), m.get("home")) and _gleich(e.get("gast"), m.get("away"), locker=True)) or \
               (_gleich(e.get("heim"), m.get("home"), locker=True) and _gleich(e.get("gast"), m.get("away"))):
                ids.add(str(m.get("matchId")))
                break
    return ids


def bf_drops(prices, bf_hist):
    """→ [{… wie ein Pinnacle-Drop, quelle „betfair"}], ein Eintrag je Spiel (stärkster Drop), stärkste zuerst."""
    pinn = _pinn_bf_ids(prices)
    if pinn is None:
        return []
    aus = []
    _diag = {"vorAnpfiff": 0, "mitVerlauf": 0, "pp3": 0, "pp6": 0}
    for m in (prices or {}).get("matches") or []:
        mid, ko = str(m.get("matchId")), ts(m.get("kickoff"))
        li = m.get("liveInfo") or {}
        # 17.11. Fix: Betwatch meldet auch VOR Anpfiff goal_v1=0 (Status NOT_STARTED) — „live" nur nach der Anpfiffzeit
        if not ko or ko <= JETZT or ko > JETZT + timedelta(hours=24) or mid in pinn or li.get("finished"):
            continue
        _diag["vorAnpfiff"] += 1
        pts = [x for x in (bf_hist or {}).get(mid) or [] if isinstance(x, dict) and isinstance(x.get("sel"), dict)
               and ts(x.get("ts")) and ts(x["ts"]) < ko]
        if len(pts) < 6 or ts(pts[-1]["ts"]) - ts(pts[0]["ts"]) < timedelta(minutes=60):
            continue
        _diag["mitVerlauf"] += 1
        letzt, vorl, vorv = pts[-1]["sel"], pts[-2]["sel"], pts[-3]["sel"]
        vor60 = next((x for x in pts if JETZT - ts(x["ts"]) <= timedelta(minutes=65)), None)
        best = None
        for mk, sel in letzt.items():
            if not BD_MAERKTE.match(mk):
                continue
            mk_eur = sum((v[0] or 0) for v in sel.values() if isinstance(v, list))
            if mk_eur < BD_MIN_EUR_MARKT:
                continue
            for n, v in sel.items():
                if not (isinstance(v, list) and len(v) > 1 and isinstance(v[1], (int, float)) and 1.15 <= v[1] <= 10):
                    continue
                if (v[0] or 0) < BD_MIN_EUR_AUSWAHL:
                    continue
                reihe = [(x["ts"], x["sel"].get(mk, {}).get(n)) for x in pts]
                reihe = [(t, w[1]) for t, w in reihe if isinstance(w, list) and len(w) > 1 and isinstance(w[1], (int, float)) and w[1] > 1]
                if len(reihe) < 6:
                    continue
                q0, q1 = reihe[0][1], v[1]
                qv = ((vorl.get(mk) or {}).get(n) or [None, None])[1]
                qvv = ((vorv.get(mk) or {}).get(n) or [None, None])[1]
                pp = (1 / q1 - 1 / q0) * 100
                _diag["pp3"] += pp >= 3
                _diag["pp6"] += pp >= BD_MIN_PP
                # kein Ein-Abruf-Ausreißer: auch der vorletzte Stand liegt schon deutlich unter dem ersten
                if pp < BD_MIN_PP or q1 > q0 * (1 - BD_MIN_REL) or not isinstance(qv, (int, float)) or qv > q0 * (1 - BD_MIN_REL / 2) \
                        or not isinstance(qvv, (int, float)) or qvv > q0 * (1 - BD_MIN_REL / 2):
                    continue
                q60 = (((vor60 or {}).get("sel") or {}).get(mk, {}).get(n) or [None, None])[1] if vor60 else None
                pp60 = round((1 / q1 - 1 / q60) * 100, 1) if isinstance(q60, (int, float)) and q60 > 1 else None
                e60 = (((vor60 or {}).get("sel") or {}).get(mk, {}).get(n) or [None])[0] if vor60 else None
                if best is None or pp > best["pp"]:
                    best = {"id": mid, "bfId": mid, "heim": m.get("home"), "gast": m.get("away"), "liga": m.get("league"),
                            "land": m.get("country"), "anpfiff": m.get("kickoff"), "top5": bool(TOP5_RX.search(str(m.get("league") or ""))),
                            "quelle": "betfair", "regel": "BD2", "markt": mk, "auf": n, "pp": round(pp, 1), "pp60": pp60,
                            "frisch": bool(pp60 is not None and pp60 >= BD_FRISCH_PP), "vonQuote": q0, "quote": q1,
                            "seit": reihe[0][0], "tag": [min(q for _, q in reihe), max(q for _, q in reihe)],
                            "eur": round(v[0] or 0), "marktEur": round(mk_eur),
                            "neu60": round((v[0] or 0) - e60) if isinstance(e60, (int, float)) else None,
                            "verlauf": [round(100 / q, 1) for _, q in reihe][-36:]}
        if best:
            aus.append(best)
    aus.sort(key=lambda x: (not x["frisch"], -x["pp"]))
    print(f"  bf-drops: {len(aus)} Spiele mit Drop · {len(pinn)} Betfair-Spiele hat Pinnacle · {_diag}")
    return aus[:40]


# ── Regel LD1 (Lucas 18.11. 11:27, „ja kannst du so bauen … im Betfair-Tab als eigener Bereich, oder im Liveticker";
# Anlass Kasem Bundit U21 77' 1:0, Über 1,5 gegen die Uhr gedrückt): Betfair LIVE, die Quote fällt gegen die Uhr —
# auch ohne Geldschwelle. Vorab festgelegt (18.11.2026, 11:35 Wien):
# * Laufendes Spiel, Sieg-Markt / Über-Unter x,5 / HZ-Tore x,5 (HZ nur bis zur Pause), ≥ €2k im Markt, Quote jetzt 1,40–6.
# * Fenster ~15 Min (Stand 12–20 Min alt) OHNE Tor. Erwartung (LD1b, 18.11. 14:30 nach Lucas' Einwand „ein Unter fällt
#   mit der Zeit sowieso"): aus der Quote damals die Tor-Rate der Restzeit (Poisson; Sieg-Markt: beide Teams aus den drei
#   Sieg-Quoten) und auf die Restzeit jetzt übertragen, in Spielminuten (markout.uhr_erwartung). Vorher Ø-Drift je Eimer —
#   der lag bei steilen Linien daneben (Unter 0,5: 3,48 statt ~2,71). Signal, wenn die Quote jetzt ≥ 8 % unter der Erwartung UND
#   absolut gefallen ist, und schon im Abruf davor ≥ 5 % darunter lag (kein Einzel-Ausreißer).
# * Rote Karte im Spiel wird markiert, nicht ausgeschlossen (Lucas: „deine Risiken sind in Wahrheit top Indikatoren").
# * Je Spiel die stärkste Auswahl. Still ins Buch (art „bfUhr"), Markout 10 Min minus Grunddrift wie der Live-CLV. Kein Push.
LD_MAERKTE = re.compile(r"^(Match Odds|Over/Under \d\.5 Goals|First Half Goals \d\.5)$")
LD_UNTER, LD_UNTER_VOR, LD_MIN_MARKT = 0.08, 0.05, 2000


def bf_uhr(prices, bf_hist):
    import markout as _m
    aus = []
    for m in (prices or {}).get("matches") or []:
        mid, ko, li = str(m.get("matchId")), ts(m.get("kickoff")), m.get("liveInfo") or {}
        if not ko or ko > JETZT or li.get("finished"):
            continue
        pts = [x for x in (bf_hist or {}).get(mid) or [] if isinstance(x, dict) and isinstance(x.get("sel"), dict) and ts(x.get("ts"))]
        if len(pts) < 4:
            continue
        jetzt_p, vor_p = pts[-1], pts[-2]
        if JETZT - ts(jetzt_p["ts"]) > timedelta(minutes=12):
            continue
        alt = [x for x in pts if timedelta(minutes=12) <= ts(jetzt_p["ts"]) - ts(x["ts"]) <= timedelta(minutes=20)]
        if not alt:
            continue
        p0 = alt[-1]
        sc = lambda x: tuple(x.get("sc") or ())  # noqa: E731
        if not sc(jetzt_p) or sc(p0) != sc(jetzt_p) or sc(vor_p) != sc(jetzt_p) or ts(p0["ts"]) < ko:
            continue
        # LD3 (18.11. 17:46, Al Buqaa 61' → 76', Lucas: „das doch wohl eher, weil Ausgleich gemacht"): der Stand springt
        # oft einen Abruf VOR den Quoten um — p0 zeigte schon 1:1, die Preise noch 0:1 (Al Buqaa 6,60, danach 2,36).
        # Darum muss der jetzige Stand schon im Abruf VOR p0 gegolten haben.
        i0 = pts.index(p0)
        if i0 == 0 or sc(pts[i0 - 1]) != sc(jetzt_p):
            continue
        mn = jetzt_p.get("min") if isinstance(jetzt_p.get("min"), (int, float)) else li.get("time")
        m0, mv = p0.get("min"), vor_p.get("min")
        # 18.11. 14:30 LD1b: Erwartung in SPIELminuten (Tor-Rate), nicht Ø-Drift je Echtzeit — Pause zählt nicht
        if not all(isinstance(x, (int, float)) for x in (mn, m0, mv)) or mn - m0 < 5 or mv <= m0:
            continue
        dt = (ts(jetzt_p["ts"]) - ts(p0["ts"])).total_seconds() / 60
        mo0 = {k: v[1] for k, v in ((p0.get("sel") or {}).get("Match Odds") or {}).items() if isinstance(v, list) and isinstance(v[1], (int, float)) and v[1] > 1}
        best = None
        for mk, sel in jetzt_p["sel"].items():
            if not LD_MAERKTE.match(mk) or (mk.startswith("First Half") and (li.get("is_ht") or (isinstance(mn, (int, float)) and mn > 45))):
                continue
            if _linie_durch(mk, list(sc(jetzt_p))):
                continue
            mk_eur = sum((v[0] or 0) for v in sel.values() if isinstance(v, list))
            if mk_eur < LD_MIN_MARKT:
                continue
            for n, v in sel.items():
                w0 = ((p0.get("sel") or {}).get(mk) or {}).get(n)
                wv = ((vor_p.get("sel") or {}).get(mk) or {}).get(n)
                if not (isinstance(v, list) and isinstance(w0, list) and isinstance(wv, list)):
                    continue
                q1, q0, qv = v[1], w0[1], wv[1]
                if not all(isinstance(x, (int, float)) and x > 1 for x in (q1, q0, qv)) or not 1.40 <= q1 <= 6 or q1 >= q0:
                    continue
                st_ = list(sc(jetzt_p))
                erw = _m.uhr_erwartung(mk, n, q0, m0, mn, st_, mo0, m.get("home"), m.get("away"))
                erw_v = _m.uhr_erwartung(mk, n, q0, m0, mv, st_, mo0, m.get("home"), m.get("away"))
                if not erw or not erw_v:
                    continue
                # LD4 (19.11. 12:26, Moik Baku Unter 0,5 5,10 → 2,36, Lucas: „wie kann da immer noch Unter kommen? sinnlos"):
                # nur Auswahlen, deren Quote die Uhr allein STEIGEN ließe (Über, nicht führendes Team …). Unter, Remis bei
                # Gleichstand und Führende fallen mit der Zeit ohnehin — schneller fallen heißt dort nicht „Geld gegen die Uhr".
                if erw <= q0 * 1.03:
                    continue
                unter = 1 - q1 / erw
                if unter < LD_UNTER or qv > erw_v * (1 - LD_UNTER_VOR):
                    continue
                if best is None or unter > best["unter"]:
                    best = {"id": mid, "heim": m.get("home"), "gast": m.get("away"), "liga": m.get("league"), "land": m.get("country"),
                            "anpfiff": m.get("kickoff"), "top5": bool(TOP5_RX.search(str(m.get("league") or ""))), "regel": "LD1",
                            "min": mn, "stand": list(sc(jetzt_p)), "markt": mk, "auswahl": n, "q0": q0, "quote": q1,
                            "erwartet": round(erw, 2), "unter": round(unter, 3), "fensterMin": round(dt),
                            "ohneUhr": False, "spielMin": [m0, mn], "neu": round((v[0] or 0) - (w0[0] or 0)), "marktEur": round(mk_eur),
                            "rot": [li.get("red_v1") or 0, li.get("red_v2") or 0] if (li.get("red_v1") or li.get("red_v2")) else None,
                            "seit": p0["ts"]}
        if best:
            best["unter"] = round(100 * best["unter"], 1)
            aus.append(best)
    aus.sort(key=lambda x: -x["unter"])
    print(f"  bf-uhr: {len(aus)} live gegen die Uhr")
    return aus[:30]


def bf_spanne(prices):
    try:
        roh = json.loads((BASIS / ".pin" / "pinnacle_alle.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    bf = [m for m in (prices or {}).get("matches") or [] if isinstance(m, dict) and ts(m.get("kickoff"))]
    out = {}
    for e in (roh.get("spiele") or {}).values():
        x = e.get("bfx")
        ko = ts(e.get("anpfiff"))
        if not x or not ko:
            continue
        for m in bf:
            if abs((ko - ts(m["kickoff"])).total_seconds()) > 20 * 60:
                continue
            if (_gleich(e["heim"], m.get("home")) and _gleich(e["gast"], m.get("away"), locker=True)) or \
               (_gleich(e["heim"], m.get("home"), locker=True) and _gleich(e["gast"], m.get("away"))):
                out[str(m.get("matchId"))] = {"ts": x[0], "back": x[1:4], "lay": x[4:7], "schluss": e.get("bfxSchluss"), "quelle": e.get("bfxQuelle")}
                break
    return out


# ── Polymarket ───────────────────────────────────────────────────────────────────
MIN_N = 12           # wie im alten Projekt (PW_RANK_MIN_N): darunter adelt Zufall Verlierer
MIN_SCHNITT_USD = 500
MIN_FENSTER = 15    # Aufloesungen im Messfenster, ab denen eine Untergrenze etwas sagt (10.11.: 8 → 15)
# 10.11. (Lucas: „Messlatte höher, dass Glückstreffer weg sind"): bei ~200 geprüften Wallets wirkten mit 90 % (z 1,645)
# rund 10 nur durch Zufall „bewährt" — jetzt 99 % (z 2,33). Stand 10.11.: 32 → 17 bewährte.
BEWAEHRT_Z = 2.33
LIVE_ZEIGEN_N, LIVE_MIN_N, LIVE_Z = 10, 30, 1.645    # Regel WL1 (21.11.)


POLY_TOP5 = {"epl", "bun", "lal", "sea", "fl1", "lig1", "ucl", "unl"}   # 12.11. (Lucas): Nations League wie Top-5   # 19.10. (Lucas): Champions League zählt zu den Top-5     # Polymarket-Schlüssel-Präfixe der Top-5-Ligen


def zeitraum_bilanz(s, tage):
    """Gewinn, Einsatz, Treffer der letzten `tage` Tage aus dem Tages-Gedächtnis einer Wallet (+ Nachtrag des alten
    Projekts). None, wenn nichts dasteht. Unterstellt Halten bis zur Auflösung."""
    t, tn = (s or {}).get("tage") or {}, (s or {}).get("tageNachtrag") or {}
    if not isinstance(t, dict) or (not t and not tn):
        return None
    ab = (JETZT.date() - timedelta(days=tage - 1)).isoformat()
    n = wins = n_geld = 0
    clv = gewinn = einsatz = 0.0
    for k, v in t.items():
        if str(k) < ab or not isinstance(v, (list, tuple)) or len(v) < 4:
            continue
        n += int(v[0]); clv += float(v[1]); wins += int(v[2])
        if len(v) >= 7:
            gewinn += float(v[4]); einsatz += float(v[5]); n_geld += int(v[6])
    for k, v in (tn if isinstance(tn, dict) else {}).items():
        if str(k) < ab or not isinstance(v, (list, tuple)) or len(v) < 3:
            continue
        n_geld += int(v[0]); gewinn += float(v[1]); einsatz += float(v[2])
    if not n and not n_geld:
        return None
    return {"n": n, "wins": wins, "treffer": round(100 * wins / n) if n else None, "clv": round(clv / n, 2) if n else None,
            "nGeld": n_geld, "gewinn": round(gewinn, 2) if n_geld else None, "einsatz": round(einsatz, 2) if n_geld else None,
            "roi": round(100 * gewinn / einsatz, 1) if n_geld and einsatz > 0 else None,
            "seit": min([str(k) for k in t] + [str(k) for k in (tn or {})]) if (t or tn) else None}


def poly(track, upcoming, whale_ledger, offen_maerkte=None, trades_ledger=None, trades_record=None):
    scores = (track or {}).get("scores") or {}
    offen = (track or {}).get("open") or {}

    pos_je = {}
    for p in offen.values():
        if not isinstance(p, dict) or not zukunft(p.get("koTs"), nach_h=0, bis_h=72) or not sport_ok(p.get("sport")):
            continue
        pos_je.setdefault(p.get("wallet"), []).append(p)

    wallets = []
    for w, s in scores.items():
        n = int(s.get("n") or 0)
        if n < MIN_N or not s.get("usd") or float(s["usd"]) / n < MIN_SCHNITT_USD:
            continue
        # Die Untergrenze NUR auf dem geschlossenen Fenster (clvFenN/clvFenSum/clvSqSum): die
        # Quadratsumme wird erst seit dem 02.09. mitgeschrieben, `n` zaehlt alles seit jeher.
        # Beides in eine Formel zu stecken ergibt „null Streuung" = maximale Sicherheit fuer die
        # Wallets mit den wenigsten Daten (im alten Projekt am 02.09. so gemessen und behoben).
        haupt = max((s.get("bySport") or {"Fußball": {"n": 0}}).items(), key=lambda kv: kv[1].get("n", 0))[0]
        if not sport_ok(haupt):
            continue
        fn, fs, fq = int(s.get("clvFenN") or 0), float(s.get("clvFenSum") or 0), float(s.get("clvSqSum") or 0)
        if fn < MIN_FENSTER:
            continue
        fm = fs / fn
        var = max(0.0, (fq - fn * fm * fm) / (fn - 1))
        ug = fm - BEWAEHRT_Z * math.sqrt(var / fn)
        offen_jetzt = sorted(pos_je.get(w) or [], key=lambda p: -float(p.get("usd") or 0))
        wallets.append({
            "wallet": w, "n": n, "clv": round(float(s.get("clvSumPP") or 0) / n, 2),
            "fensterN": fn, "fensterClv": round(fm, 2), "clvUg": round(ug, 2),
            "pnl": s.get("pnl"), "schnittUsd": round(float(s["usd"]) / n), "zuletzt": s.get("lastTs"),
            "sport": haupt,
            "offen": [{"markt": p.get("key"), "seite": p.get("side"), "usd": p.get("usd"),
                       "preis": p.get("lastPrice") or p.get("firstPrice"), "anpfiff": p.get("koTs"),
                       "einstieg": p.get("entryPrice") or p.get("firstPrice"), "einTs": p.get("firstTs"),
                       "marktUsd": p.get("marktUsd"), "sport": p.get("sport")} for p in offen_jetzt[:5]],
        })
    # Lucas 22.10.: „es könnten Wallets ja trotzdem in 7 bzw. 30 Tagen vorn sein, ohne Vorsprung auf den Schlusskurs"
    # — zweite und dritte Rangliste nach GEWINN im Zeitraum, aus dem Tages-Gedächtnis (Sportwetten, die wir sahen).
    gewinn_listen = {}
    for tg, min_n in ((7, 5), (30, 10)):
        zeilen = []
        for w, sc in scores.items():
            f = zeitraum_bilanz(sc, tg)
            if not f or f["nGeld"] < min_n or f["gewinn"] is None:
                continue
            haupt = max((sc.get("bySport") or {"Fußball": {"n": 0}}).items(), key=lambda kv: kv[1].get("n", 0))[0]
            if not sport_ok(haupt):
                continue
            zeilen.append({"wallet": w, "sport": haupt, **f, "pnlGesamt": sc.get(f"pnl{tg}"), "pnl": sc.get("pnl"),
                           "offen": len(pos_je.get(w) or [])})
        zeilen.sort(key=lambda r: -r["gewinn"])
        gewinn_listen[str(tg)] = zeilen[:15]
    # Regel WL1 (21.11., Lucas „Live ist mir wichtiger — Yield, wie viel vom Wallet gestaked wird"): Live-Rangliste.
    live_wallets = []
    for w, sc in scores.items():
        ln = int(sc.get("liveN") or 0)
        if ln < LIVE_ZEIGEN_N:
            continue
        bs = sc.get("liveSport") or {}
        haupt = max(bs.items(), key=lambda kv: kv[1][0])[0] if bs else "?"
        if bs and not sport_ok(haupt):
            continue
        ym = float(sc.get("liveRSum") or 0) / ln
        yv = max(0.0, (float(sc.get("liveRSq") or 0) - ln * ym * ym) / max(1, ln - 1))
        mn = int(sc.get("moN") or 0)
        mm = float(sc.get("moSum") or 0) / mn if mn else None
        mv = max(0.0, (float(sc.get("moSq") or 0) - mn * mm * mm) / max(1, mn - 1)) if mn else None
        mo_ug = mm - LIVE_Z * math.sqrt(mv / mn) if mn >= 2 else None
        schnitt = float(sc.get("liveUsd") or 0) / ln
        pf = sc.get("portfolio")
        live_wallets.append({
            "wallet": w, "sport": haupt, "n": ln, "treffer": round(100 * int(sc.get("liveWins") or 0) / ln),
            "yield": round(100 * ym, 1), "yieldUg": round(100 * (ym - LIVE_Z * math.sqrt(yv / ln)), 1),
            "moN": mn, "markout": round(mm, 2) if mm is not None else None, "markoutUg": round(mo_ug, 2) if mo_ug is not None else None,
            "schnittUsd": round(schnitt), "portfolio": pf, "anteil": round(100 * schnitt / pf, 1) if pf else None,
            "bewaehrt": bool(ln >= LIVE_MIN_N and mn >= LIVE_MIN_N and mo_ug is not None and mo_ug > 0 and ym > 0),
            "offen": len(pos_je.get(w) or []), "zuletzt": max((sc.get("liveTage") or {"": 0}).keys()),
        })
    live_wallets.sort(key=lambda r: (not r["bewaehrt"], -(r["markoutUg"] if r["markoutUg"] is not None else -99), -r["yieldUg"]))
    live_wallets = live_wallets[:25]
    for w in wallets:                                      # Folge-Rendite neben dem Schlusskurs
        sc = scores.get(w["wallet"]) or {}
        yn = int(sc.get("yN") or 0)
        w["folgeN"], w["folgeRendite"] = yn, (round(100 * float(sc.get("yRSum") or 0) / yn, 1) if yn else None)
        pf = sc.get("portfolio")
        w["anteil"] = round(100 * w["schnittUsd"] / pf, 1) if pf else None
    # Sortiert nach der UNTERGRENZE: wer oben steht, ist nicht nur im Schnitt gut, sondern belegt.
    wallets.sort(key=lambda r: -r["clvUg"])
    wallets = wallets[:20]
    bewiesen = {w["wallet"] for w in wallets if w["clvUg"] > 0}

    # Aktuelle Grosshalter je Markt (nah am Anpfiff, mit Geld-Split): poly_money_broad_offen.
    # Daraus „setzt gerade auf" fuer die Rangliste — der Track allein kennt nur, was er schon
    # einmal beim Einstieg gesehen hat.
    halter = {}
    for key, m in (offen_maerkte or {}).items():
        if not isinstance(m, dict):
            continue
        for wh in m.get("whales") or []:
            if isinstance(wh, dict) and wh.get("wallet") in bewiesen and sport_ok(m.get("sport")):
                halter.setdefault(wh["wallet"], []).append(
                    {"markt": key, "seite": wh.get("side"), "usd": wh.get("usd"),
                     "preis": wh.get("avgPrice"), "anpfiff": m.get("koTs"), "sport": m.get("sport")})
    for w in wallets:
        bekannt = {(o["markt"], o["seite"]) for o in w["offen"]}
        for h in sorted(halter.get(w["wallet"]) or [], key=lambda x: -float(x.get("usd") or 0)):
            if (h["markt"], h["seite"]) not in bekannt:
                w["offen"].append(h)
        w["offen"] = sorted(w["offen"], key=lambda x: -float(x.get("usd") or 0))[:5]
        w["bewiesen"] = w["wallet"] in bewiesen

    maerkte = []
    quelle = dict(upcoming or {})
    for key, m in (offen_maerkte or {}).items():      # offen ist naeher am Anpfiff und hat die Wale
        if isinstance(m, dict):
            quelle[key] = {**(quelle.get(key) or {}), **m}
    for key, m in quelle.items():
        if not isinstance(m, dict) or key.endswith(("-more-markets", "-exact-score")) or not sport_ok(m.get("sport")) or not poly_key_ok(key):
            continue
        # 21.10.: Nebenmärkte wie „…-total-corners" / „…-halftime-result" („Over vs Under") sind keine Spiele
        if not re.search(r"\d{4}-\d{2}-\d{2}$", key):
            continue
        h = m.get("hoursToKickoff")
        k = ts(m.get("koTs"))
        if k and not (0 <= (k - JETZT).total_seconds() / 3600 <= 30):
            continue
        preise = m.get("prices") or {}
        teams = [n for n in preise if not str(n).lower().startswith("draw")]
        maerkte.append({"key": key, "liga": m.get("league"), "sport": m.get("sport"),
                        "top5": key.split("-")[0] in POLY_TOP5,
                        "spiel": " vs ".join(teams[:2]) if teams else key,
                        "usd": round(float(m.get("totalUsd") or 0)), "anpfiff": m.get("koTs"),
                        "preise": {n: p for n, p in preise.items()},
                        # Nur ein BELASTBARER Split sagt etwas: bei Zwei-Wege-Maerkten spiegelt er
                        # den Preis (`preis_echo`), abgeschnittene Halterlisten verzerren ihn.
                        "geld": ({n: round(float(v)) for n, v in (m.get("shares") or {}).items()} or None)
                                if (m.get("splitGuete") or {}).get("art") == "belastbar" else None,
                        "wale": [{"wallet": wh.get("wallet"), "seite": wh.get("side"), "usd": wh.get("usd"),
                                  "bewiesen": wh.get("wallet") in bewiesen}
                                 for wh in (m.get("whales") or [])[:5] if isinstance(wh, dict)]})
    maerkte.sort(key=lambda r: -r["usd"])
    # Lucas 17.10.: nicht nur Fußball — E-Sport und Tennis bekommen eigene Plätze, sonst verdrängt
    # das Fußball-Volumen sie komplett. Je Sport die größten Märkte, Kleckermärkte raus.
    je_sport = {"Fußball": 18, "E-Sport": 12, "Tennis": 10}
    zaehl, auswahl = {}, []
    for mk in maerkte:
        sp = mk["sport"] or "Fußball"
        if mk["usd"] < 1000 or zaehl.get(sp, 0) >= je_sport.get(sp, 0):
            continue
        zaehl[sp] = zaehl.get(sp, 0) + 1
        auswahl.append(mk)
    for mk in auswahl:
        mk["nWale"] = len(mk["wale"])
        mk["nBelegt"] = sum(1 for w in mk["wale"] if w["bewiesen"])
    titel = {mk["key"]: mk["spiel"] for mk in maerkte}

    def name_fuer(key):
        key = str(key or "")
        basis, neben = poly_neben(key)
        if neben:
            return name_fuer(basis) + ", " + neben
        if key in titel:
            return titel[key]
        q = quelle.get(key) or quelle.get(str(key).replace("-more-markets", "")) or {}
        tn = [n for n in (q.get("prices") or {}) if not str(n).lower().startswith("draw")]
        return " vs ".join(tn[:2]) if tn else str(key)

    # Was die belegten Wallets JETZT halten — das Kernstück: gutes Timing, nachgewiesen, mit Geld drin.
    setzen = []
    for w in wallets:
        if not w["bewiesen"]:
            continue
        for o in w["offen"]:
            if float(o.get("usd") or 0) < 500 or not poly_key_ok(o.get("markt")) or not zukunft(o.get("anpfiff"), nach_h=0, bis_h=72) or not sport_ok(o.get("sport")):
                continue
            setzen.append({**o, "wallet": w["wallet"], "clv": w["fensterClv"], "clvUg": w["clvUg"],
                           "n": w["fensterN"], "pnl": w.get("pnl"), "schnittUsd": w.get("schnittUsd"),
                           "rang": wallets.index(w) + 1, "rangVon": sum(1 for x in wallets if x["bewiesen"]),
                           "spiel": name_fuer(o["markt"]), "jetzt": ((quelle.get(o["markt"]) or {}).get("prices") or {}).get(o.get("seite"))})
    setzen.sort(key=lambda x: -float(x.get("usd") or 0))

    # Wal-Pushes aus dem Trades-Channel (eigene Abrechnung im alten Projekt), letzte 36 h
    tp = []
    for z in trades_ledger or []:
        t = ts(z.get("sentAt"))
        if not t or t < JETZT - timedelta(hours=36) or not sport_ok(z.get("cat")) or not poly_key_ok(z.get("key")):
            continue
        tp.append({"wallet": z.get("wallet"), "markt": z.get("key"), "spiel": z.get("titel") or name_fuer(z.get("key")), "seite": z.get("side"),
                   "usd": z.get("usd"), "preis": z.get("pushPrice"), "sport": z.get("cat"), "zeit": z.get("sentAt"),
                   "status": {"win": "won", "loss": "lost"}.get(z.get("result"), "pending" if z.get("status") == "pending" else None)})
    tp.sort(key=lambda x: x["zeit"] or "", reverse=True)
    rec = (trades_record or {}).get("byCat") or {}
    bil = {sp: {"n": b.get("n"), "treffer": round((b.get("hit") or 0) * 100, 1), "roi": round((b.get("roi") or 0) * 100, 1),
                "roiUg": round((b.get("roiUg") or 0) * 100, 1) if b.get("roiUg") is not None else None, "belegt": b.get("belegt")}
           for sp, b in rec.items() if sp in SPORTARTEN}

    grenze = JETZT - timedelta(hours=24)
    wale = []
    for z in whale_ledger or []:
        t = ts(z.get("sentAt"))
        if not t or t < grenze or not sport_ok(z.get("cat")) or not poly_key_ok(z.get("key")):
            continue
        wale.append({"wallet": z.get("wallet"), "markt": z.get("key"), "seite": z.get("side"),
                     "usd": z.get("usd"), "preis": z.get("whaleEntry"), "anteilSeite": z.get("seitenAnteil"),
                     "liga": z.get("league"), "zeit": z.get("sentAt"), "status": z.get("status")})
    wale.sort(key=lambda r: r["zeit"] or "", reverse=True)
    return {"wallets": wallets, "liveWallets": live_wallets, "gewinnListen": gewinn_listen, "maerkte": auswahl, "walPushes": wale[:20], "setzen": setzen[:20],
            "tradesPushes": tp[:40], "tradesBilanz": bil, "tradesSeit": (trades_record or {}).get("startAb"),
            "regel": {"minN": MIN_N, "minSchnittUsd": MIN_SCHNITT_USD, "minFenster": MIN_FENSTER}}


# ── Stake ────────────────────────────────────────────────────────────────────────
ARTEN = {None: "Burst", "spiel": "Spiel-Burst", "tore": "Torlinien-Burst", "klein": "Kleine Liga"}


def _norm_name(t):
    """„Atlético de Madrid" und „Atletico Madrid" sind dieselbe Auswahl."""
    import unicodedata
    t = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(w for w in t.replace("-", " ").split() if w not in {"de", "fc", "cf", "sc", "the", "club"})


def _tor_linien(linien, richtung):
    """[„Over 2.5", „Over 3.5"] → „Over 2.5 / 3.5"."""
    zahlen = []
    for l in linien or []:
        m = re.search(r"\d+(?:[.,]\d+)?", str(l))
        if m and m.group(0).replace(",", ".") not in zahlen:
            zahlen.append(m.group(0).replace(",", "."))
    r = str(richtung or (linien or [""])[0].split(" ")[0]).capitalize()
    return f"{r} {' / '.join(sorted(zahlen, key=float)[:3])}" if zahlen else r


def stake(ledger):
    zeilen = [z for z in (ledger or []) if isinstance(z, dict)]
    grenze = JETZT - timedelta(hours=24)
    heute = []
    for z in zeilen:
        t = ts(z.get("sentAt"))
        if not t or t < grenze or not z.get("push") or not stake_ok(z):
            continue
        heute.append({"art": ARTEN.get(z.get("art"), z.get("art")), "spiel": z.get("event"), "liga": z.get("liga"),
                      # 17.11. (Rundum-Check): Torlinien-Bursts standen als nacktes „Over" da — die Linien stehen im Buch
                      "auswahl": z.get("auswahl") or z.get("seite") or (_tor_linien(z.get("linien"), z.get("richtung")) if z.get("linien") else z.get("richtung")),
                      "markt": z.get("markt") or ", ".join(z.get("maerkte") or []) or None,
                      "usd": z.get("summeUsd"), "wetten": z.get("nWetten"), "quote": z.get("quote"),
                      "phase": z.get("phase"), "minute": z.get("spielminute"), "faktor": z.get("faktor"),
                      "anpfiff": z.get("anpfiff"), "kat": z.get("kat"),
                      "zeit": z.get("sentAt"), "status": z.get("status"), "rendite": z.get("rendite"),
                      # 10.11. (Stake-Check): damit push.py den gesendeten Tipp selbst abrechnen kann
                      "betIds": z.get("betIds") or [], "eventId": z.get("eventId"), "sport": z.get("sport")})
    heute.sort(key=lambda r: r["zeit"] or "", reverse=True)
    # Lucas 17.10.: dieselbe Wette (Spiel + Auswahl) steht als Burst UND Spiel-Burst da → zusammenfassen.
    # Betrag = der größte Einzelwert, nicht die Summe: die Signale überlappen und zählen dieselben Wetten.
    gruppen = {}
    for z in heute:
        k = (_norm_name(z["spiel"]), _norm_name(z["auswahl"]))
        g = gruppen.get(k)
        if not g:
            gruppen[k] = g = dict(z, arten=[], signale=0, ersteZeit=z["zeit"])
        g["signale"] += 1
        if z["art"] not in g["arten"]:
            g["arten"].append(z["art"])
        g["ersteZeit"] = min(g["ersteZeit"] or "", z["zeit"] or "")
        if (z["usd"] or 0) > (g["usd"] or 0):
            g.update(usd=z["usd"], wetten=z["wetten"], quote=z["quote"] or g["quote"], betIds=z.get("betIds") or g.get("betIds"))
        if z["faktor"] and (z["faktor"] > (g["faktor"] or 0)):
            g["faktor"] = z["faktor"]
        if z["status"] == "abgerechnet" and g["status"] != "abgerechnet":
            g["status"], g["rendite"] = z["status"], z["rendite"]
    zusammen = sorted(gruppen.values(), key=lambda r: r["zeit"] or "", reverse=True)

    bilanz = {}
    for z in zeilen:
        if z.get("status") != "abgerechnet" or not isinstance(z.get("rendite"), (int, float)) or not z.get("push") or not stake_ok(z):
            continue
        b = bilanz.setdefault(ARTEN.get(z.get("art"), z.get("art")), {"n": 0, "summe": 0.0, "qs": 0.0, "gew": 0})
        r = float(z["rendite"])
        b["n"] += 1
        b["summe"] += r
        b["qs"] += r * r
        b["gew"] += int(r > 0)
    for b in bilanz.values():
        n, m = b["n"], b["summe"] / b["n"]
        var = max(0.0, (b.pop("qs") - n * m * m) / (n - 1)) if n > 1 else 0.0
        b["roi"] = round(100 * m, 1)
        # Einseitige 95-%-Untergrenze; unter 30 Meldungen gibt es keine (Streuung zu unsicher).
        b["ug"] = round(100 * (m - 1.645 * math.sqrt(var / n)), 1) if n >= 30 else None
        b.pop("summe")
        b["treffer"] = round(100 * b.pop("gew") / n)
    return {"heute": heute[:40], "gruppen": zusammen[:40], "bilanz": bilanz}


# ── HZ 0:0 ───────────────────────────────────────────────────────────────────────
def hz(finder):
    f = finder or {}
    grenze = JETZT - timedelta(hours=24)
    heute = []
    for e in f.get("eintraege") or []:
        t = ts(e.get("gebuchtAt"))
        if not t or t < grenze:
            continue
        heute.append({"spiel": f"{e.get('home')} – {e.get('away')}", "liga": e.get("league"),
                      "land": e.get("country"), "gruppen": e.get("gruppen"), "quoten": e.get("quoten"),
                      "zeit": e.get("gebuchtAt"), "status": e.get("status"), "anpfiff": e.get("kickoff"),
                      "ft": e.get("ft"), "wetten": {k: bool((w or {}).get("win")) for k, w in (e.get("wetten") or {}).items()},
                      # für den HZ-Push im alten Format (Lucas 17.10.: „die waren sehr wichtig für mich")
                      "heim": e.get("home"), "gast": e.get("away"), "vor": e.get("vor"), "phase": e.get("phase"),
                      "minute": e.get("minute"), "zufluss": e.get("zufluss"),
                      "statistik": (e.get("apif") or {}).get("statistik"),
                      "chancen": hz_chancen((e.get("apif") or {}).get("statistik")),
                      "xg": _hz_summe((e.get("apif") or {}).get("statistik"), "xg"),
                      "sot": _hz_summe((e.get("apif") or {}).get("statistik"), "aufsTor"),
                      "serie": {**(e.get("serie") or {}), **((e.get("apif") or {}).get("serie") or {})}})
    heute.sort(key=lambda r: r["zeit"] or "", reverse=True)
    bericht = []
    for k, b in (f.get("bericht") or {}).items():
        if isinstance(b, dict):
            bericht.append({"wette": b.get("text") or k, "n": b.get("n"), "treffer": b.get("trefferPct"),
                            "erwartet": b.get("erwartetPct"), "roi": b.get("roi"), "ug": b.get("ug"),
                            "urteil": b.get("urteil")})
    return {"heute": heute[:30], "bericht": bericht, "regel": f.get("regel"), "chancenBuch": hz_chancen_buch(f)}


HZ_XG_HOCH = 1.0          # Summe xG beider Teams in der 1. HZ
HZ_SOT_HOCH = 5           # Ersatz ohne xG: Schüsse aufs Tor beider Teams


def hz_chancen(st):
    """Lucas 18.10.: „in der 1. HZ hohe xG → Tore erwartet, aber nicht geschossen". 'hoch' / 'niedrig' / None."""
    h, g = (st or {}).get("heim") or {}, (st or {}).get("gast") or {}
    if not h or not g:
        return None
    if h.get("xg") is not None and g.get("xg") is not None:
        return "hoch" if (h["xg"] + g["xg"]) >= HZ_XG_HOCH else "niedrig"
    if h.get("aufsTor") is not None and g.get("aufsTor") is not None:
        return "hoch" if (h["aufsTor"] + g["aufsTor"]) >= HZ_SOT_HOCH else "niedrig"
    return None


def _hz_gruppe(rows):
    """n, Tor-%, Quote-erwartet-% und ROI für Over 0,5 / Over 1,5 (2. HZ) über abgerechnete Funde."""
    b = {"n": len(rows), "tor": sum(int(sum(e["ft"]) > 0) for e in rows)}
    b["torPct"] = round(100 * b["tor"] / b["n"]) if b["n"] else None
    for w, feld in (("over05", "o05"), ("over15", "o15")):
        v = [((e.get("wetten") or {}).get(w) or {}) for e in rows]
        v = [x for x in v if isinstance(x.get("r"), (int, float)) and x.get("quote")]
        b[feld + "N"] = len(v)
        b[feld + "Roi"] = round(100 * sum(x["r"] for x in v) / len(v), 1) if v else None
        b[feld + "Erw"] = round(100 * sum(1 / x["quote"] for x in v) / len(v)) if v else None
        b[feld + "Pct"] = round(100 * sum(bool(x.get("win")) for x in v) / len(v)) if v else None
    return b


def _hz_summe(st, feld):
    h, g = (st or {}).get("heim") or {}, (st or {}).get("gast") or {}
    return round(h[feld] + g[feld], 2) if h.get(feld) is not None and g.get(feld) is not None else None


def hz_chancen_buch(f):
    """Lucas 19.10.: „will die Spiele sehen, wo wir die xG-Daten haben, damit ich seh, ob die besser performen".
    Über ALLE abgerechneten Funde im Buch des alten HZ-Finders (wächst mit jedem Spieltag):
      hoch / niedrig / ohne — Chancen in der 1. HZ; 'ohne' nur ab dem ersten Spiel mit Statistik, sonst
      vergleicht man alte Wochen mit neuen.
      segmente — Schatten-Auswahl für die Tor-Gruppe (nur beobachtet, filtert nichts)."""
    fertig = [e for e in (f or {}).get("eintraege") or [] if e.get("ft") and len(e["ft"]) == 2]
    mit = [e for e in fertig if (e.get("apif") or {}).get("statistik")]
    ab = min((e.get("gebuchtAt") or "" for e in mit), default="")
    ch = lambda e: hz_chancen((e.get("apif") or {}).get("statistik"))
    out = {"hoch": _hz_gruppe([e for e in fertig if ch(e) == "hoch"]),
           "niedrig": _hz_gruppe([e for e in fertig if ch(e) == "niedrig"]),
           "ohne": _hz_gruppe([e for e in fertig if ch(e) is None and (e.get("gebuchtAt") or "") >= ab]),
           "seit": ab or None}
    tore = [e for e in fertig if "tore" in (e.get("gruppen") or [])]
    vor = lambda e: ((e.get("vor") or {}).get("over25"))
    q05 = lambda e: ((e.get("quoten") or {}).get("over05"))
    seg = [("Alle Tor-Spiele", tore),
           ("vor Anpfiff Über 2,5 bis 1,55", [e for e in tore if vor(e) and vor(e) <= 1.55]),
           ("vor Anpfiff Über 2,5 über 1,55", [e for e in tore if vor(e) and vor(e) > 1.55]),
           ("Pausen-Quote Tor unter 1,20", [e for e in tore if q05(e) and q05(e) < 1.20]),
           ("Pausen-Quote Tor ab 1,20", [e for e in tore if q05(e) and q05(e) >= 1.20]),
           ("Chancen da + vor Anpfiff bis 1,55", [e for e in tore if ch(e) == "hoch" and vor(e) and vor(e) <= 1.55])]
    out["segmente"] = [{"name": n, **_hz_gruppe(r)} for n, r in seg if r]
    spiele = []
    for e in sorted(mit, key=lambda e: e.get("gebuchtAt") or "", reverse=True)[:60]:
        st = (e.get("apif") or {}).get("statistik")
        w = e.get("wetten") or {}
        spiele.append({"spiel": f"{e.get('home')} – {e.get('away')}", "liga": e.get("league"), "land": e.get("country"),
                       "zeit": e.get("gebuchtAt"), "ft": e.get("ft"), "chancen": ch(e),
                       "xg": _hz_summe(st, "xg"), "sot": _hz_summe(st, "aufsTor"), "vor": vor(e),
                       "quoten": {k: (e.get("quoten") or {}).get(k) for k in ("over05", "over15")},
                       "gewonnen": {k: (w.get(k) or {}).get("win") for k in ("over05", "over15") if k in w}})
    out["spiele"] = spiele
    return out


# ── Betfair über ALLE Ligen: Geld gegen die Liga-Norm ────────────────────────────
def _phase(jetzt, kick, minute=None):
    """p0 >3h vor Anpfiff · p1 letzte 3h · l1 1. HZ inkl. Pause · l2 2. HZ — wie liga_norm.stage_of.
    25.10.: nach der Spielminute (vorher nach der Uhr: ab 45 Min nach Anpfiff schon gegen den Endstand gemessen)."""
    d = (jetzt - kick).total_seconds()
    if d >= 0:
        if d > 3.5 * 3600:
            return None
        if isinstance(minute, (int, float)):
            return "l2" if minute > 45 else "l1"
        return "l2" if d > 63 * 60 else "l1"
    return "p1" if -d <= 3 * 3600 else "p0"


# 12.11. (Lucas, Italy – France: „$47k … 5-mal so viel wie üblich" — „die Nations League müssen wir als Top-5 handhaben,
# da werden sehr hohe Einsätze gespielt"): UEFA Nations League A (Betfair-Name) zählt zu den Top-5. B/C/D bleiben, wie sie sind.
TOP5_RX = re.compile(r"^(english premier league|german bundesliga|spanish la liga|italian serie a|french ligue 1|uefa champions league|uefa nations league( a)?)$", re.I)


def auswahl_zufluss(snaps, minuten, min_eur, min_anteil=0.65, seit_tor=False):
    """Zufluss JE AUSWAHL: wohin ging das neue Geld innerhalb eines Marktes?

    Vergleicht die Auswahl-Volumina jetzt mit dem Punkt vor `minuten`. Gemeldet wird eine Auswahl,
    wenn sie mindestens `min_eur` neu bekam UND den Großteil (`min_anteil`) des neuen Geldes in
    ihrem Markt — sonst ist es ein gleichmäßig verteilter Markt, der nichts aussagt.
    """
    pts = [s for s in snaps if isinstance(s, dict) and isinstance(s.get("sel"), dict) and ts(s.get("ts"))]
    if len(pts) < 2:
        return []
    jetzt = pts[-1]
    t_jetzt = ts(jetzt["ts"])
    vor = None
    for s in pts[:-1]:
        if ts(s["ts"]) <= t_jetzt - timedelta(minutes=minuten - 2):
            vor = s
    if not vor or ts(vor["ts"]) < t_jetzt - timedelta(minutes=minuten + 15):
        return []
    # Lucas 14.11. („Team geht in Führung, kriegt den Ausgleich — alte Einsätze bei 0:0, neue bei 1:1"): live wie beim
    # Dauerzufluss (REGEL S3) nur das Geld SEIT DEM LETZTEN TOR. Fiel im Fenster ein Tor, beginnt es beim ersten Abruf
    # mit dem neuen Stand.
    if seit_tor:
        sc = lambda x: tuple(x["sc"]) if isinstance(x.get("sc"), list) and None not in x["sc"] else None   # noqa: E731
        i_vor = pts.index(vor)
        for i in range(len(pts) - 1, i_vor, -1):
            if sc(pts[i]) and sc(pts[i - 1]) and sc(pts[i]) != sc(pts[i - 1]):
                vor = pts[i]
                break
        if vor is jetzt:
            return []
    out = []
    for markt, sel in jetzt["sel"].items():
        alt = (vor.get("sel") or {}).get(markt) or {}
        d = {n: v[0] - (alt.get(n) or [0])[0] for n, v in sel.items()}
        ges = sum(x for x in d.values() if x > 0)
        if ges <= 0:
            continue
        name, dv = max(d.items(), key=lambda kv: kv[1])
        q = sel[name][1]
        # Lucas 17.10.: Quote unter 1,20 raus — praktisch entschieden oder Absicherungsgeld auf den klaren Favoriten (z. B. Unter 4,5 bei 0:0 in der 80.) — Geld darauf ist kein Signal
        if dv >= min_eur and dv / ges >= min_anteil and not (isinstance(q, (int, float)) and q < 1.20):
            out.append({"markt": markt, "auswahl": name, "eur": round(dv), "anteil": round(100 * dv / ges),
                        "quote": sel[name][1], "quoteVorher": (alt.get(name) or [None, None])[1],
                        "marktNeu": round(ges),
                        # Lucas 18.10.: „wie viel gesamt oben liegt, damit die 87 % greifbar sind"
                        "marktGesamt": round(sum(v[0] for v in sel.values())), "auswahlGesamt": round(sel[name][0]),
                        "marktVerteilung": [[n, round(v[0]), v[1], round(d.get(n) or 0)] for n, v in
                                            sorted(sel.items(), key=lambda kv: -kv[1][0])][:4]})
    out.sort(key=lambda x: -x["eur"])
    return out[:4]


def sel_delta(snaps, minuten):
    """{Markt: {Auswahl: neues Geld}} gegenüber dem Punkt vor `minuten` (leer, wenn zu wenig Historie)."""
    pts = [s for s in snaps if isinstance(s, dict) and isinstance(s.get("sel"), dict) and ts(s.get("ts"))]
    if len(pts) < 2:
        return {}
    jetzt, t_jetzt, vor = pts[-1], ts(pts[-1]["ts"]), None
    for s in pts[:-1]:
        if ts(s["ts"]) <= t_jetzt - timedelta(minutes=minuten - 2):
            vor = s
    if not vor:
        vor = pts[0]            # weniger Historie als das Fenster: ab dem ältesten Punkt
    return {mk: {n: round(v[0] - ((vor["sel"].get(mk) or {}).get(n) or [0])[0]) for n, v in sel.items()}
            for mk, sel in jetzt["sel"].items()}


def quote_vor(snaps, minuten=60):
    """{Markt: {Auswahl: Quote}} vom Schnappschuss etwa `minuten` vor dem letzten (für die Pfeile, Lucas 23.10.)."""
    pts = [s for s in snaps if isinstance(s, dict) and isinstance(s.get("sel"), dict) and ts(s.get("ts"))]
    if len(pts) < 2:
        return {}
    t_jetzt, vor = ts(pts[-1]["ts"]), None
    for s in pts[:-1]:
        if ts(s["ts"]) <= t_jetzt - timedelta(minutes=minuten):
            vor = s
    vor = vor or pts[0]
    if t_jetzt - ts(vor["ts"]) < timedelta(minutes=15):
        return {}
    return {mk: {n: v[1] for n, v in sel.items() if isinstance(v, list) and len(v) > 1} for mk, sel in vor["sel"].items()}


def quote_geld(snaps, minuten=60):
    """16.11. (Lucas: „wenn wir bei Betfair die Kohle sehen, die gematcht wird — die Quote, zu der gematcht wurde, auch
    protokollieren, damit man sieht, wenn's dagegen läuft"): Betwatch liefert keine Preisleiter, nur Volumen + Quote je
    Abruf (~5 Min). Je Abstand zweier Abrufe zählt das neue Geld zur mittleren Quote der beiden → gewichteter Schnitt
    der letzten `minuten`. Abstände mit Tor (Stand ändert sich) zählen nicht — da springt die Quote.
    → {Markt: {Auswahl: [Ø Quote des Geldes, € gezählt]}}. REIN."""
    pts = [s for s in snaps if isinstance(s, dict) and isinstance(s.get("sel"), dict) and ts(s.get("ts"))]
    if len(pts) < 2:
        return {}
    grenze = ts(pts[-1]["ts"]) - timedelta(minutes=minuten + 2)
    summe = {}
    for a, b in zip(pts, pts[1:]):
        if ts(b["ts"]) < grenze or ts(a["ts"]) < grenze - timedelta(minutes=10):
            continue
        if a.get("sc") is not None and b.get("sc") is not None and a.get("sc") != b.get("sc"):
            continue
        for mk, sel in b["sel"].items():
            for n, v in (sel or {}).items():
                w = ((a["sel"].get(mk) or {}).get(n))
                if not (isinstance(v, list) and isinstance(w, list) and len(v) > 1 and len(w) > 1):
                    continue
                dv = (v[0] or 0) - (w[0] or 0)
                qa, qb = w[1], v[1]
                if dv <= 0 or not all(isinstance(q, (int, float)) and q > 1 for q in (qa, qb)):
                    continue
                z = summe.setdefault(mk, {}).setdefault(n, [0.0, 0.0])
                z[0] += dv * (qa + qb) / 2
                z[1] += dv
    return {mk: {n: [round(z[0] / z[1], 3), round(z[1])] for n, z in sel.items() if z[1] >= 500}
            for mk, sel in summe.items()}


def quote_spanne(snaps, stunden=24):
    """17.11. (Lucas, „Wo steht die Quote heute"): {Markt: {Auswahl: [Tief, Hoch]}} über die letzten `stunden`.
    Läuft das Spiel, nur die Live-Stände (vor Anpfiff war ein anderer Markt), sonst nur die vor Anpfiff."""
    pts = [x for x in snaps if isinstance(x, dict) and isinstance(x.get("sel"), dict) and ts(x.get("ts"))]
    if len(pts) < 3:
        return {}
    # 17.11. Fix: „sc" steht auch vor Anpfiff auf [0, 0] (Betwatch) — vor/live nach der Anpfiffzeit trennen
    ko = ts(pts[-1].get("kickoff"))
    vor_ = lambda x: (ts(x["ts"]) < ko) if ko else (x.get("sc") is None)
    live = not vor_(pts[-1])
    grenze = ts(pts[-1]["ts"]) - timedelta(hours=stunden)
    out = {}
    for x in pts:
        if ts(x["ts"]) < grenze or (not vor_(x)) != live:
            continue
        for mk, sel in x["sel"].items():
            for n, v in (sel or {}).items():
                if isinstance(v, list) and len(v) > 1 and isinstance(v[1], (int, float)) and v[1] > 1:
                    z = out.setdefault(mk, {}).setdefault(n, [v[1], v[1]])
                    z[0], z[1] = min(z[0], v[1]), max(z[1], v[1])
    return out


def maerkte_detail(m, snaps, minuten, nur_abs=False):
    """Die geldstärksten Märkte eines Spiels mit Geld + Quote je Auswahl und dem Zufluss je Auswahl.
    nur_abs=True: stattdessen die Märkte, die hier wegen Kleinstquote (< 1,10) rausfallen — Lucas 28.10. („da kamen
    20k rein, man sieht aber nur eine Kachel"): die Wand zeigt sie grau als Absicherung, damit kein Geld verschwindet."""
    dl = sel_delta(snaps, minuten)
    qv = quote_vor(snaps)
    qg = quote_geld(snaps)
    qs = quote_spanne(snaps)
    out = []
    # Lucas 24.10. („Halbzeit seh ich gar nicht, das muss auch angezeigt werden"): die 8 geldstärksten Märkte UND
    # immer die Halbzeit-Märkte — die sind meist kleiner und fielen bisher aus den Top 6.
    hz = {"Half Time", "First Half Goals 0.5", "First Half Goals 1.5", "First Half Goals 2.5"}
    allg = 0
    for name, mk in sorted(((k, v) for k, v in (m.get("markets") or {}).items() if isinstance(v, dict)),
                           key=lambda kv: -float(kv[1].get("vol") or 0)):
        if name not in hz and allg >= 8:
            continue
        vol = float(mk.get("vol") or 0)
        if vol < 200:
            continue
        # Lucas 18.10.: „Über/Unter 7,5 @1,01 — wertlos, brauch ich nicht". Ein Markt, dessen meistgesetzte
        # Auswahl unter 1,10 steht (oder schon entschieden ist), sagt nichts mehr → nicht zeigen.
        quoten = [r.get("odd") for r in mk.get("runners") or [] if isinstance(r, dict) and isinstance(r.get("odd"), (int, float)) and r.get("odd") > 1]
        if (bool(quoten) and min(quoten) < 1.10) != nur_abs:
            continue
        rs = sorted((r for r in mk.get("runners") or [] if isinstance(r, dict) and (r.get("vol") or 0) > 0),
                    key=lambda r: -(r.get("vol") or 0))
        if name not in ("Match Odds", "Half Time", "Draw no Bet") and len(rs) > 4:   # Ergebnis-Märkte: Top 4 + Rest
            rest = sum(r.get("vol") or 0 for r in rs[4:])
            rs = rs[:4] + ([{"name": "Rest", "vol": rest, "odd": None}] if rest > 0 else [])
        d = dl.get(name) or {}
        if name not in hz:
            allg += 1
        out.append({"name": name, "eur": round(vol), "neu": round(sum(max(0, x) for x in d.values())),
                    "auswahl": [{"name": r.get("name"), "eur": round(r.get("vol") or 0), "quote": r.get("odd"),
                                 "neu": d.get(r.get("name")), "q60": (qv.get(name) or {}).get(r.get("name")),
                                 "qGeld": (qg.get(name) or {}).get(r.get("name")),
                                 "tag": (qs.get(name) or {}).get(r.get("name"))} for r in rs]})
    return out


# ── Tormärkte auf einen Blick (Lucas 14.11.: „Over/Under-Märkte zusammen, wie viel Geld da oben liegt … in einer coolen
# Grafik, Over oben, Under unten" — live wichtig, die natürliche Quotenbewegung rausrechnen) ───────────────────────────
OU_RX = re.compile(r"^Over/Under (\d+)\.5 Goals$")


def _poisson_ueber(lam, k):
    """P(X > k) für X ~ Poisson(lam), k ganzzahlig ≥ 0. REIN."""
    p, summe = math.exp(-lam), 0.0
    for i in range(k + 1):
        summe += p
        p *= lam / (i + 1)
    return max(0.0, 1.0 - summe)


def _lam_aus(p_ueber, k):
    """λ, bei dem P(Rest > k) = p_ueber (Bisektion). REIN."""
    lo, hi = 0.0, 12.0
    for _ in range(28):
        mid = (lo + hi) / 2
        if _poisson_ueber(mid, k) < p_ueber:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def tor_rate(sel, tore, minute):
    """Torerwartung des Marktes als Tore je 90 Minuten (rechnet die natürliche Bewegung live heraus): aus jeder Über/Unter-
    Linie die erwarteten restlichen Tore (Poisson), nach Geld gewichtet, geteilt durch die Restspielzeit. None = keine
    brauchbare Linie. REIN."""
    if minute is not None and float(minute) >= 85:
        return None                          # Restzeit zu kurz — kleine Quotenschritte würden die Rate wild ausschlagen lassen
    rest = 95.0 if minute is None else 95.0 - float(minute)
    lams, gew = [], []
    for mk, d in (sel or {}).items():
        mm = OU_RX.match(str(mk))
        if not mm or not isinstance(d, dict):
            continue
        linie = int(mm.group(1))
        o = next((v for n, v in d.items() if str(n).startswith("Over")), None)
        u = next((v for n, v in d.items() if str(n).startswith("Under")), None)
        if not (isinstance(o, list) and isinstance(u, list) and len(o) > 1 and len(u) > 1):
            continue
        oq, uq = o[1], u[1]
        if not (isinstance(oq, (int, float)) and isinstance(uq, (int, float)) and oq > 1 and uq > 1):
            continue
        k = linie - (tore or 0)              # wie viele Tore noch fallen müssen, damit Über gewinnt: > k
        if k < 0:
            continue                         # Linie schon entschieden
        pu = (1 / oq) / (1 / oq + 1 / uq)
        if not 0.06 <= pu <= 0.94:
            continue                         # zu nah an 0/1 — die Quote sagt dort nichts mehr über die Rate
        lams.append(_lam_aus(pu, k))
        gew.append((o[0] or 0) + (u[0] or 0) + 100)
    if not lams:
        return None
    lam = sum(l * g for l, g in zip(lams, gew)) / sum(gew)
    return round(lam / rest * 90, 2)


def tor_fluss(snaps, bis=36):
    """Je Abruf: neues Geld auf Über und auf Unter (alle Linien 0,5–6,5, ohne Absicherung unter 1,15) + Torrate je 90 Min.
    {bl: [[minute_epoch, über_neu, unter_neu, rate, tore]], linien, o, u, o30, u30, rate, rate60} oder None. REIN."""
    pts = [x for x in snaps if isinstance(x, dict) and isinstance(x.get("sel"), dict) and ts(x.get("ts"))]
    if len(pts) < 2:
        return None

    def seiten(sel):
        o = u = 0.0
        for mk, d in (sel or {}).items():
            if not OU_RX.match(str(mk)) or not isinstance(d, dict):
                continue
            for n, v in d.items():
                if isinstance(v, list) and v and isinstance(v[0], (int, float)):
                    if str(n).startswith("Over"):
                        o += v[0]
                    elif str(n).startswith("Under"):
                        u += v[0]
        return o, u

    def neu(a, b):           # Zuwachs je Auswahl, Absicherung (Quote < 1,15) und Rückgänge ausgenommen
        o = u = 0.0
        for mk, d in (b or {}).items():
            if not OU_RX.match(str(mk)) or not isinstance(d, dict):
                continue
            alt = (a or {}).get(mk) or {}
            for n, v in d.items():
                if not (isinstance(v, list) and v and isinstance(v[0], (int, float))):
                    continue
                if len(v) > 1 and isinstance(v[1], (int, float)) and v[1] < 1.15:
                    continue
                w = (alt.get(n) or [0])[0] or 0
                dz = max(0.0, v[0] - w)
                if str(n).startswith("Over"):
                    o += dz
                elif str(n).startswith("Under"):
                    u += dz
        return o, u
    bl = []
    for a, b in zip(pts[:-1], pts[1:]):
        if (ts(b["ts"]) - ts(a["ts"])) > timedelta(minutes=20):
            continue
        o, u = neu(a["sel"], b["sel"])
        sc = b.get("sc") if isinstance(b.get("sc"), list) and None not in b.get("sc") else None
        sa = a.get("sc") if isinstance(a.get("sc"), list) and None not in a.get("sc") else None
        tore = sum(sc) if sc else 0
        # gerade ein Tor: Stand schon neu, Quoten oft noch alt → keine Rate für diesen Abruf
        rate = None if (sc and sa and sum(sa) != tore) else tor_rate(b["sel"], tore, b.get("min") if sc else None)
        bl.append([int(ts(b["ts"]).timestamp() // 60), round(o), round(u), rate, tore if sc else None])
    bl = bl[-bis:]
    if not bl or not any(x[1] or x[2] for x in bl) and not any(x[3] for x in bl):
        return None
    letzt = pts[-1]["sel"]
    linien = []
    for mk, d in sorted(letzt.items(), key=lambda kv: int(OU_RX.match(kv[0]).group(1)) if OU_RX.match(str(kv[0])) else 99):
        mm = OU_RX.match(str(mk))
        if not mm or not isinstance(d, dict):
            continue
        o = next((v for n, v in d.items() if str(n).startswith("Over")), None) or [0, None]
        u = next((v for n, v in d.items() if str(n).startswith("Under")), None) or [0, None]
        if (o[0] or 0) + (u[0] or 0) < 300:
            continue
        linien.append({"l": int(mm.group(1)) + 0.5, "o": round(o[0] or 0), "u": round(u[0] or 0),
                       "oq": o[1] if len(o) > 1 else None, "uq": u[1] if len(u) > 1 else None})
    o_ges, u_ges = seiten(letzt)
    t_jetzt = ts(pts[-1]["ts"])
    j30 = [x for x in bl if x[0] >= int((t_jetzt - timedelta(minutes=30)).timestamp() // 60)]
    raten = [x for x in bl if x[3] is not None]
    r60 = next((x[3] for x in raten if x[0] >= int((t_jetzt - timedelta(minutes=60)).timestamp() // 60)), None)
    return {"bl": bl, "linien": linien, "o": round(o_ges), "u": round(u_ges),
            "o30": sum(x[1] for x in j30), "u30": sum(x[2] for x in j30),
            "rate": raten[-1][3] if raten else None, "rate60": r60}


def _sicher(f, *a):
    try:
        return f(*a)
    except Exception as exc:  # noqa: BLE001
        print(f"  {getattr(f, '__name__', f)}: {exc}")
        return None


def markt_zufluss(snaps):
    mk = [s.get("mkv") for s in snaps if isinstance(s, dict) and isinstance(s.get("mkv"), dict)]
    if len(mk) < 2:
        return None
    a, b = mk[-2], mk[-1]
    d = sorted(((k, (b.get(k) or 0) - (a.get(k) or 0)) for k in b), key=lambda x: -x[1])
    return [{"name": k, "eur": round(v)} for k, v in d[:3] if v > 0] or None


def _ist_hz_markt(name) -> bool:
    n = str(name or "")
    return n in ("Half Time", "Half Time Score") or n.startswith("First Half Goals")


def _norm_pruefung(norm_state):
    eigen_ab = datetime(2026, 10, 20, tzinfo=timezone.utc).timestamp() * 1000
    je, ligen = {}, {}
    for k, rows in ((norm_state or {}).get("samples") or {}).items():
        liga, ph = k.rsplit("|", 1)
        ts = [r[0] for r in rows if isinstance(r, list) and r and isinstance(r[0], (int, float))]
        z = je.setdefault(ph, {"n": 0, "seit20": 0})
        z["n"] += len(ts)
        z["seit20"] += sum(1 for t in ts if t >= eigen_ab)
        if ts:
            ligen.setdefault(liga, {})[ph] = [len(ts), sum(1 for t in ts if t >= eigen_ab),
                                              datetime.fromtimestamp(min(ts) / 1000, timezone.utc).strftime("%d.%m.")]
    gross = sorted(ligen, key=lambda l: -sum(v[0] for v in ligen[l].values()))[:12]
    return {"phasen": je, "ligen": {l: ligen[l] for l in gross}}


def betfair_radar(prices, bf_hist, norm, norm_state=None):
    """Jedes Spiel im Betfair-Abruf (auch Slowakei 2, Finnland, China …), gemessen an seiner Liga.

    ×Norm = gematchtes Geld jetzt / Median dieser Liga in derselben Phase (letzte 60 Tage, nur
    abgeschlossene Phasen). Eine laufende Phase ist noch nicht fertig — ×Norm ist also eher zu niedrig.
    """
    medi = (norm or {}).get("byLeagueStage") or {}
    # Lucas 25.10. („wichtig, dass die Liga-Norm richtig funktioniert und weiter lernt"): normEur ist der Median der
    # PHASE, in der das Spiel gerade steht (p0 = Stand 3 h vor Anpfiff … l2 = Endstand). Für „wie groß ist diese Liga"
    # taugt das nicht — dieselbe Liga wäre vor Anpfiff „klein" und live „groß". Ligagröße = Median des ENDSTANDS (l2):
    # wie viel Geld ein ganzes Spiel dieser Liga üblicherweise sieht. Gleiche Quelle, lernt mit (60 Tage rollierend).
    liga_eur = {k.rsplit("|", 1)[0]: v for k, v in medi.items() if k.endswith("|l2") and (v.get("n") or 0) >= 5}
    # Lucas 25.10. (Strømmen – Strømsgodset, €100k in der 17. Minute, fehlte in „Über der Liga-Norm"): hat eine Liga für
    # die aktuelle Phase noch keine 5 Spiele (z. B. weil die 1.-HZ-Werte heute neu gemessen werden), wird die Phasen-Norm
    # aus ihrem Endstand geschätzt — mit dem typischen Verhältnis Phase/Endstand über alle Ligen, die beides haben.
    def _verh(ph):
        r = sorted(medi[f"{lg}|{ph}"]["med"] / v["med"] for lg, v in liga_eur.items()
                   if (medi.get(f"{lg}|{ph}") or {}).get("n", 0) >= 5 and v.get("med"))
        return r[len(r) // 2] if len(r) >= 10 else None
    phase_verh = {ph: _verh(ph) for ph in ("p0", "p1", "l1")}
    # Lucas 17.10.: „ob da deine Daten wirklich stimmen". Neben dem Median die ganze Stichprobe:
    # wie viele Spiele dieser Liga hatten in derselben Phase WENIGER Geld als dieses jetzt.
    proben = {k: sorted(x[1] for x in v if isinstance(x, list) and len(x) > 1)
              for k, v in ((norm_state or {}).get("samples") or {}).items()}
    spiele, stunden = [], {}
    grenze = JETZT - timedelta(hours=12)
    for eid, snaps in (bf_hist or {}).items():
        if not isinstance(snaps, list):
            continue
        prev = None
        for s in snaps:
            t, v = ts(s.get("ts")), s.get("totalVol")
            if t and isinstance(v, (int, float)) and prev and t >= grenze:
                dv = v - prev
                if 0 < dv < 5e6:
                    h = t.replace(minute=0, second=0, microsecond=0)
                    stunden[h] = stunden.get(h, 0) + dv
            if isinstance(v, (int, float)):
                prev = v
    for m in (prices or {}).get("matches") or []:
        kick = ts(m.get("kickoff"))
        li = m.get("liveInfo") or {}
        if not kick or li.get("finished"):
            continue
        dt_h = (kick - JETZT).total_seconds() / 3600
        if not (-2.2 <= dt_h <= 30):
            continue
        vol = float(m.get("totalVol") or 0)
        if vol < 300:
            continue
        # Lucas 22.10. (Persik Kediri ×3,9 — die €30k lagen fast alle auf „Über 0,5" @ 1,05): Geld zu Quoten unter
        # 1,15 ist Absicherung/Einsammeln, kein Interesse am Spiel → zählt nicht gegen die Liga-Norm.
        tot, top_r = 0.0, None
        for mn, mk in (m.get("markets") or {}).items():
            for r in (mk or {}).get("runners") or []:
                if not isinstance(r, dict) or not (r.get("vol") or 0) > 0:
                    continue
                if isinstance(r.get("odd"), (int, float)) and r["odd"] < 1.15:
                    tot += r["vol"]
                if not top_r or r["vol"] > top_r[1]["vol"]:
                    top_r = (mn, r)
        geld_auf = {"markt": top_r[0], "auswahl": top_r[1].get("name"), "quote": top_r[1].get("odd"),
                    "anteil": round(100 * top_r[1]["vol"] / vol)} if top_r and vol else None
        vol_norm = max(0.0, vol - min(tot, vol))
        ph = _phase(JETZT, kick, li.get("time") if not li.get("is_ht") else 45)
        b = medi.get(f"{m.get('league')}|{ph}") or {}
        if (b.get("n") or 0) < 5 and phase_verh.get(ph) and (liga_eur.get(m.get("league")) or {}).get("med"):
            b = {"med": round(liga_eur[m.get("league")]["med"] * phase_verh[ph]), "n": b.get("n") or 0, "geschaetzt": True}
        xnorm = round(vol_norm / b["med"], 1) if b.get("med") and ((b.get("n") or 0) >= 5 or b.get("geschaetzt")) else None
        pr = proben.get(f"{m.get('league')}|{ph}") or []
        rang = {"weniger": sum(1 for x in pr if x < vol), "n": len(pr),
                "max": pr[-1] if pr else None} if len(pr) >= 5 else None
        snaps = [s for s in (bf_hist or {}).get(str(m.get("matchId"))) or [] if isinstance(s, dict)]
        kurve, vor60, vor30, mo30, t30, vor15, t15 = [], None, None, None, None, None, None
        for s in snaps:
            t, v = ts(s.get("ts")), s.get("totalVol")
            if not t or not isinstance(v, (int, float)):
                continue
            if t >= JETZT - timedelta(hours=10):
                kurve.append(round(v))
            if t <= JETZT - timedelta(minutes=60):
                vor60 = v
            if t <= JETZT - timedelta(minutes=30):
                vor30, mo30, t30 = v, s.get("mo") or {}, t
            if t <= JETZT - timedelta(minutes=15):
                vor15, t15 = v, t
        # Lucas 19.10. („mehr Graphen, auch in den Details"): Verlauf Geld + Quoten der letzten 10 h für das Spiel-Detail
        reihe = []
        for s in snaps:
            t, v = ts(s.get("ts")), s.get("totalVol")
            if t and t >= JETZT - timedelta(hours=10) and isinstance(v, (int, float)):
                mo_s = s.get("mo") or {}
                reihe.append([t.strftime("%H:%M"), round(v), mo_s.get("hw"), mo_s.get("dr"), mo_s.get("aw"), int(t.timestamp() // 60)])
        # Lucas 25.10. (Druck-Radar): live das neue Geld je 5 Spielminuten — liegt unter der Druck-Grafik
        geld_min = None
        if dt_h < 0:
            bins, vorher = {}, None
            for s in snaps:
                mi, v = s.get("min"), s.get("totalVol")
                if not isinstance(v, (int, float)):
                    continue
                if vorher is not None and isinstance(mi, (int, float)) and mi >= 0:
                    fach = int(min(mi, 90) // 5) * 5
                    bins[fach] = bins.get(fach, 0) + max(0.0, v - vorher)
                vorher = v
            geld_min = [[k, round(x)] for k, x in sorted(bins.items())] or None
        # Lucas 20.10. („Balken statt Linie, kürzere Zeitachse, damit man sieht, wann Geld reingepumpt wurde"):
        # neues Geld je 10 Min über die letzten 6 h, dazu der Anpfiff und die Tore als Marken.
        saeulen = None
        pts = [(ts(s_.get("ts")), s_.get("totalVol"), s_.get("sc")) for s_ in snaps]
        pts = [(t, v, sc) for t, v, sc in pts if t and isinstance(v, (int, float))]
        if len(pts) >= 3:
            start = JETZT - timedelta(hours=6)
            grenzen = [start + timedelta(minutes=10 * k) for k in range(37)]

            def stand_bis(g):
                vv = None
                for t, v, _ in pts:
                    if t <= g:
                        vv = v
                    else:
                        break
                return vv
            st = [stand_bis(g) for g in grenzen]
            werte = [round(max(0, st[k + 1] - st[k])) if st[k] is not None and st[k + 1] is not None else None for k in range(36)]
            erster = next((i for i, w in enumerate(werte) if w is not None), None)
            tore, alt = [], None
            for t, v, sc in pts:
                if isinstance(sc, list) and len(sc) == 2:
                    if alt is not None and sc != alt and t >= start:
                        tore.append({"i": min(35, int((t - start).total_seconds() // 600)), "stand": sc})
                    alt = sc
            ko_t = ts(m.get("kickoff"))
            # Lucas 23.10.: je Säule auch, wie viel davon auf die einzelnen Auswahlen ging (die 5 mit dem meisten neuen
            # Geld in 6 h) — die Seite färbt die Auswahl der Meldung kräftig ein, der Rest des Spiels bleibt grau
            sp_sel = [(ts(s_.get("ts")), s_.get("sel")) for s_ in snaps]
            sp_sel = [(t, sl) for t, sl in sp_sel if t and isinstance(sl, dict)]
            teile = []
            if len(sp_sel) >= 3:
                def sel_bis(g):
                    letzt = None
                    for t, sl in sp_sel:
                        if t <= g:
                            letzt = sl
                        else:
                            break
                    return letzt
                stände = [sel_bis(g) for g in grenzen]

                def sel_vol(sl, mk, nm):
                    v = ((sl or {}).get(mk) or {}).get(nm)
                    return float(v[0]) if isinstance(v, list) and v else 0.0
                letzt = next((x for x in reversed(stände) if x), None) or {}
                erst_ = next((x for x in stände if x), None) or {}
                def quote_ok(d, nm):         # Geld zu Quoten unter 1,15 ist Absicherung (wie bei der Liga-Norm)
                    v = d.get(nm)
                    return not (isinstance(v, list) and len(v) > 1 and isinstance(v[1], (int, float)) and v[1] < 1.15)
                kand = sorted(((sel_vol(letzt, mk, nm) - sel_vol(erst_, mk, nm), mk, nm) for mk, d in letzt.items() if isinstance(d, dict)
                               for nm in d if quote_ok(d, nm)), reverse=True)[:5]
                for gesamt, mk, nm in kand:
                    if gesamt < 300:
                        continue
                    w = [round(max(0, sel_vol(stände[k + 1], mk, nm) - sel_vol(stände[k], mk, nm))) if stände[k] and stände[k + 1] else None
                         for k in range(36)]
                    teile.append({"markt": mk, "auswahl": nm, "w": w})
            if erster is not None:
                saeulen = {"w": werte, "start": int(start.timestamp() // 60), "schritt": 10,
                           "ko": (int((ko_t - start).total_seconds() // 600) if ko_t and start <= ko_t <= JETZT else None),
                           "tore": tore[-6:], "teile": teile}
        if len(reihe) > 30:
            schritt = len(reihe) / 30
            reihe = [reihe[int(i * schritt)] for i in range(30)] + [reihe[-1]]
        if len(kurve) > 18:
            schritt = len(kurve) / 18
            kurve = [kurve[int(i * schritt)] for i in range(18)] + [kurve[-1]]
        mo = m.get("mo") or {}
        fair = mo.get("fair") or {}
        mk = ((m.get("markets") or {}).get("Match Odds") or {})
        rs = [r for r in mk.get("runners") or [] if isinstance(r, dict)]
        gv = sum(float(r.get("vol") or 0) for r in rs)
        if gv > 0:
            vert = [{"name": r.get("name"), "eur": round(float(r.get("vol") or 0)), "quote": r.get("odd")} for r in rs]
        elif fair:
            vert = None
        else:
            vert = None
        maerkte = sorted(((k, float(v.get("vol") or 0)) for k, v in (m.get("markets") or {}).items()
                          if isinstance(v, dict)), key=lambda x: -x[1])
        hz_vorbei = bool(dt_h < 0 and not li.get("is_ht") and isinstance(li.get("time"), (int, float)) and li.get("time") > 45)
        spiele.append({
            "id": str(m.get("matchId")), "heim": m.get("home"), "gast": m.get("away"),
            "liga": m.get("league"), "land": m.get("country"), "anpfiff": m.get("kickoff"),
            "live": ({"min": li.get("time"), "stand": [li.get("goal_v1"), li.get("goal_v2")],
                      "ht": bool(li.get("is_ht")), "minGeschaetzt": bool(li.get("minGeschaetzt"))} if dt_h < 0 else None),
            # Lucas 17.10. (Everton–Chelsea 90' 0:2, €1,6 Mio „Zufluss"): Geld auf ein entschiedenes
            # Spiel ist Abrechnungs-/Trading-Rauschen bei Quote 1,01–1,05, kein Signal.
            # Lucas 24.10. (Gil Vicente – Porto 75' 0:1, €1 Mio, „find das nirgends"): ab der 70. Minute galt JEDES Spiel als
            # entschieden und verschwand aus Wand, Hitparade und Top 3 — auch bei 0:1. Jetzt nur, wenn die Quote es sagt
            # (eine Seite bei 1,10 oder darunter, ab der 30.) oder ab der 85. Minute.
            "entschieden": bool(dt_h < 0 and isinstance(li.get("time"), (int, float)) and (li.get("time") >= 85 or (
                li.get("time") >= 30 and min([q for q in (mo.get("hw"), mo.get("dr"), mo.get("aw")) if q] or [9]) <= 1.10))),
            # Lucas 18.10. (Salzburg 14' 1:0 @1,21 im Live-Zufluss): führt ein Team und steht es unter 1,40, ist das
            # Spiel für alle Live-Listen und -Pushes durch — dieselbe Grenze wie „Wo am meisten liegt".
            "klarVorn": bool(dt_h < 0 and li.get("goal_v1") is not None and li.get("goal_v2") is not None
                             and li.get("goal_v1") != li.get("goal_v2")
                             and isinstance(mo.get("hw" if li.get("goal_v1") > li.get("goal_v2") else "aw"), (int, float))
                             and mo.get("hw" if li.get("goal_v1") > li.get("goal_v2") else "aw") < 1.40),
            "eur": round(vol), "xnorm": xnorm, "normEur": b.get("med"), "normN": b.get("n"), "normGeschaetzt": bool(b.get("geschaetzt")),
            "geldMin": geld_min,
            "ligaEur": (liga_eur.get(m.get("league")) or {}).get("med"), "ligaN": (liga_eur.get(m.get("league")) or {}).get("n"), "geldAuf": geld_auf, "totEur": round(tot), "phase": ph, "rang": rang,
            "d60": round(vol - vor60) if vor60 is not None else None,
            # nur wenn der Vergleichspunkt nicht uralt ist (sonst wäre es kein 30-Min-Zufluss)
            "d30": round(vol - vor30) if vor30 is not None and t30 and t30 >= JETZT - timedelta(minutes=50) else None,
            "d15": round(vol - vor15) if vor15 is not None and t15 and t15 >= JETZT - timedelta(minutes=30) else None,
            # In welchen Markt floss das Geld zuletzt? (die Historie führt das Markt-Volumen der letzten 2 Abrufe)
            "marktZufluss": markt_zufluss(snaps),
            "torFluss": _sicher(tor_fluss, snaps),
            "maerkteDetail": [x for x in maerkte_detail(m, snaps, 30 if dt_h > 0 else 15)
                              if not (hz_vorbei and _ist_hz_markt(x.get("name")))],
            "maerkteAbs": [x for x in maerkte_detail(m, snaps, 30 if dt_h > 0 else 15, nur_abs=True)
                           if not (hz_vorbei and _ist_hz_markt(x.get("name")))][:4],
            "fensterMin": 30 if dt_h > 0 else 15,
            "flussVorher": auswahl_zufluss(snaps, 30, 3000) if dt_h > 0 else [],
            # Lucas 18.10. (Valladolid 54' 1:2: „+€21k auf Über 0,5 HZ-Tore @ 2,02"): ein Halbzeit-Markt ist nach der
            # Pause abgerechnet — was dort noch „neu" erscheint, ist Abrechnung, kein Geld. Ab der 46. Minute raus.
            "flussLive": [f for f in auswahl_zufluss(snaps, 15, 1500, seit_tor=True) if not (hz_vorbei and _ist_hz_markt(f.get("markt")))] if dt_h <= 0 else [],
            "top5": bool(TOP5_RX.search(str(m.get("league") or ""))),
            "quoten30": ({"heim": mo30.get("hw"), "remis": mo30.get("dr"), "gast": mo30.get("aw")} if mo30 else None),
            "kurve": kurve,
            "reihe": reihe,
            "saeulen": saeulen,
            "quoten": {"heim": mo.get("hw"), "remis": mo.get("dr"), "gast": mo.get("aw")},
            "wahrsch": ({"heim": fair.get("home"), "remis": fair.get("draw"), "gast": fair.get("away")} if fair else None),
            "verteilung": vert,
            "maerkte": [{"name": k, "eur": round(v)} for k, v in maerkte[:4] if v > 0],
        })
    spiele.sort(key=lambda s: -(s["xnorm"] or 0))
    std = [{"h": iso(h), "eur": round(v)} for h, v in sorted(stunden.items())][-12:]
    ligen = len({s["liga"] for s in spiele})
    # Lucas 19.10. (PAOK B – Apollon: Dauerzufluss-Push, Karte da, beim Anpfiff weg): die Liste war auf die 120 Spiele
    # mit der höchsten ×Norm gekappt. Beim Anpfiff misst ×Norm gegen die Live-Norm (viel mehr Geld üblich) → das
    # Spiel rutschte unter die 120 und verschwand mit Karte, Serie und Detail. Spiele mit frischem Geld bleiben immer.
    aktiv = lambda s: bool(s.get("flussVorher") or s.get("flussLive") or (s.get("d30") or 0) >= 1500 or (s.get("d15") or 0) >= 1000)
    oben = spiele[:120]
    drin = {id(s) for s in oben}
    spiele = oben + [s for s in spiele[120:] if aktiv(s)][:80]
    return {"spiele": spiele, "stunden": std, "ligen": ligen,
            "summeEur": round(sum(s["eur"] for s in spiele)),
            # die gelernte Ligagröße aller Ligen (Endstand-Median, n ≥ 5) — für die Auswertung nach Ligagröße
            "ligaGroesse": {k: {"eur": v.get("med"), "n": v.get("n")} for k, v in sorted(liga_eur.items(), key=lambda x: -(x[1].get("med") or 0))},
            "normStand": {"stichproben": (norm or {}).get("n"), "buckets": (norm or {}).get("buckets"),
                          "nutzbar": (norm or {}).get("usable"), "stand": (norm or {}).get("generatedAt"),
                          # Lucas 25.10. („schau dir das mit EPL an, ob das noch wo so ist"): je Phase wie viele Spiele,
                          # das älteste und wie viele seit dem eigenen Abruf (20.10.) — so sieht man, wo Spiele verloren gehen
                          "pruefung": _norm_pruefung(norm_state)}}


HZ_MAERKTE = ("Half Time", "First Half Goals 0.5", "First Half Goals 1.5", "First Half Goals 2.5", "Half Time Score")


def halbzeit(prices):
    """Geld auf die ERSTE HALBZEIT (HZ-1X2, HZ-Tore, HZ-Ergebnis) — vor Anpfiff und solange die 1. HZ läuft.

    Mehr Geld auf den HZ-Sieg-Markt als auf den Spiel-Sieg-Markt ist ungewöhnlich (im alten Projekt
    der „Fix-Verdacht"); das Verhältnis steht als quote/hzAnteil dabei.
    """
    out = []
    for m in (prices or {}).get("matches") or []:
        kick = ts(m.get("kickoff"))
        li = m.get("liveInfo") or {}
        if not kick or li.get("finished"):
            continue
        dt_min = (kick - JETZT).total_seconds() / 60
        minute = li.get("time")
        if dt_min > 30 * 60 or dt_min < -52 or li.get("is_ht") or (dt_min < 0 and isinstance(minute, (int, float)) and minute > 45):
            continue                      # −52 statt −75: die Minute hängt in der Pause manchmal bei 45 (20.10.)
        mk = m.get("markets") or {}
        maerkte = []
        tore = sum(x or 0 for x in (li.get("goal_v1"), li.get("goal_v2"))) if dt_min < 0 else 0
        for name in HZ_MAERKTE:
            # Lucas 05.09.: ein HZ-Tormarkt, der durch die gefallenen Tore schon entschieden ist, ist kein Signal.
            if name.startswith("First Half Goals") and tore > float(name.split()[-1]):
                continue
            v = mk.get(name) or {}
            vol = float(v.get("vol") or 0)
            if vol < 200:
                continue
            rs = [r for r in v.get("runners") or [] if isinstance(r, dict) and (r.get("vol") or 0) > 0]
            top = max(rs, key=lambda r: r.get("vol") or 0) if rs else None
            # Lucas 23.10. (Widzew – Górnik 31' 1:0, „HZ Unter 2,5 @ 1,10"): liegt das meiste Geld auf einer Quote unter
            # 1,20, ist das Absicherung/Einsammeln, kein Halbzeit-Signal — der Markt zählt weder in Karte noch Summe.
            if top and isinstance(top.get("odd"), (int, float)) and top["odd"] < 1.20:
                continue
            maerkte.append({"name": name, "eur": round(vol),
                            "runner": [{"name": r.get("name"), "eur": round(r.get("vol") or 0), "quote": r.get("odd")} for r in rs][:8],
                            "auf": top.get("name") if top else None, "quote": top.get("odd") if top else None,
                            "anteil": round(100 * (top.get("vol") or 0) / vol) if top else None})
        hz_eur = sum(x["eur"] for x in maerkte)
        if hz_eur < 1500:
            continue
        mo = float((mk.get("Match Odds") or {}).get("vol") or 0)
        ht = float((mk.get("Half Time") or {}).get("vol") or 0)
        maerkte.sort(key=lambda x: -x["eur"])
        out.append({"id": str(m.get("matchId")), "heim": m.get("home"), "gast": m.get("away"), "liga": m.get("league"),
                    "land": m.get("country"), "anpfiff": m.get("kickoff"),
                    "live": ({"min": minute, "stand": [li.get("goal_v1"), li.get("goal_v2")]} if dt_min < 0 else None),
                    "eur": round(hz_eur), "moEur": round(mo), "htZuMo": round(ht / mo, 2) if mo > 0 else None,
                    "maerkte": maerkte})
    # Lucas 20.10. (Dortmund und Fenerbahçe, beide Anpfiff 18:45, fehlten genau im Lauf nach Anpfiff): beim Wechsel
    # auf „in-play" setzt Betfair die Märkte kurz aus, der Abruf liefert dann keine HZ-Volumina. Ein Spiel, das im
    # letzten Lauf drin war und jetzt nur deshalb fehlt, behält bis zu 15 Min seinen letzten Stand (Live-Stand frisch).
    try:
        alt = json.loads(HZ_LETZT.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        alt = {}
    drin = {x["id"] for x in out}
    noch = {str(m.get("matchId")): m for m in (prices or {}).get("matches") or []}
    for mid, e in (alt.get("spiele") or {}).items():
        m = noch.get(mid)
        t = ts(e.get("_t"))
        if mid in drin or not m or not t or JETZT - t > timedelta(minutes=15):
            continue
        li = m.get("liveInfo") or {}
        minute = li.get("time")
        kick = ts(m.get("kickoff"))
        if li.get("finished") or li.get("is_ht") or (isinstance(minute, (int, float)) and minute > 45) \
                or (kick and (JETZT - kick).total_seconds() > 52 * 60):
            continue
        e = {k: v for k, v in e.items() if k != "_t"}
        if kick and kick < JETZT:
            e["live"] = {"min": minute, "stand": [li.get("goal_v1"), li.get("goal_v2")]}
        e["gehalten"] = True
        out.append(e)
        drin.add(mid)
    try:
        HZ_LETZT.write_text(json.dumps({"spiele": {**{k: v for k, v in (alt.get("spiele") or {}).items() if k in drin and k not in {x["id"] for x in out if not x.get("gehalten")}},
                                                   **{x["id"]: {**x, "_t": iso(JETZT)} for x in out if not x.get("gehalten")}}},
                                       separators=(",", ":")), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    out.sort(key=lambda x: -x["eur"])
    return out[:40]


HZ_LETZT = BASIS / ".bf" / "hz_letzt.json"


# ── Betfair-HZ-Pushes wie im alten Projekt (betfair_alerts.py, Lucas 17.10.: „die sollen auch rein") ──
# 🔵 Halbzeit-Geld (einseitig): HZ-1X2 oder HZ Über/Unter 1,5 mit ≥ Schwelle gematcht, ≥85 % auf EINEM
#    Ausgang, Quote ≥1,30. Schwellen wie dort: Top-5 + internationale Bewerbe 15k, Rest 10k.
# ⚫ Fix-Verdacht: ein HZ-Markt hat mind. doppelt so viel Geld wie der größte Spiel-Markt, ≥€2k,
#    ≥65 % einseitig, Quote ≥1,15, nur vor Anpfiff oder bis Minute 30.
# Beide nur, solange die erste Halbzeit läuft und die Torlinie nicht schon gerissen ist.
BF_HT_MAERKTE = ("Half Time", "First Half Goals 1.5")
BF_FIX_HT = ("Half Time", "First Half Goals 0.5", "First Half Goals 1.5")
BF_FIX_FT = ("Match Odds", "Over/Under 2.5 Goals", "Over/Under 3.5 Goals", "Both teams to Score?")
_UEFA_RX = re.compile(r"(champions league|europa league|europa conference|conference league|uefa)", re.I)


def _bf_top(m):
    return bool(TOP5_RX.search(str(m.get("league") or "").strip())) or bool(_UEFA_RX.search(str(m.get("league") or ""))) \
        or bool(re.match(r"^(int|international|eu|europe)$", str(m.get("country") or ""), re.I))


def _bf_vol(mk):
    return sum((r.get("vol") or 0.0) for r in (mk.get("runners") or []) if isinstance(r, dict))


def _bf_hz_offen(m, name=None):
    li = m.get("liveInfo") or {}
    if li.get("finished") or li.get("is_ht"):
        return False
    # Lucas 20.10. (Liverpool – Villarreal: HZ-Push um 22:07, Pause war 20 Min vorbei, Betwatch meldete weiter „45'"):
    # die Minute hängt in der Pause manchmal. Mehr als 52 Min nach Anpfiff ist die 1. HZ sicher vorbei.
    ko = ts(m.get("kickoff"))
    if ko and (JETZT - ko).total_seconds() > 52 * 60:
        return False
    t = li.get("time")
    if t is not None:
        try:
            if float(t) > 45:
                return False
        except (TypeError, ValueError):
            return False
    if name:
        mm = re.search(r"(\d+(?:\.\d+)?)\s*$", name)
        g1, g2 = li.get("goal_v1"), li.get("goal_v2")
        if mm and t is not None and g1 is not None and g2 is not None and (g1 + g2) > float(mm.group(1)):
            return False
    return True


def _hz_liga(norm, liga, markt, eur):
    """Lucas 25.10.: HZ-Geld nicht nur mit dem eigenen Spiel vergleichen, auch mit der Liga — üblicher Stand dieses
    HZ-Markts bis Minute 30 (liga_norm.py, ab 5 Spielen). → {"ligaNorm", "xLiga", "ligaN"} oder {}."""
    b = ((norm or {}).get("byLeagueStage") or {}).get(f"{liga}|hz:{markt}") or {}
    if not b.get("med") or (b.get("n") or 0) < 5:
        return {}
    return {"ligaNorm": b["med"], "ligaN": b["n"], "xLiga": round(eur / b["med"], 1)}


def bf_ht_pushes(prices, norm=None):
    out = []
    for m in (prices or {}).get("matches") or []:
        mk = m.get("markets") or {}
        li = m.get("liveInfo") or {}
        top = _bf_top(m)
        basis = {"id": str(m.get("matchId")), "heim": m.get("home"), "gast": m.get("away"), "liga": m.get("league"),
                 "land": m.get("country"), "anpfiff": m.get("kickoff"), "top": top,
                 "live": ({"min": li.get("time"), "stand": [li.get("goal_v1"), li.get("goal_v2")]} if li.get("time") is not None else None)}
        if not _bf_hz_offen(m):
            continue
        # 🔵 einseitiges HZ-Geld
        best = None
        for name in BF_HT_MAERKTE:
            x = mk.get(name)
            if not x or not _bf_hz_offen(m, name):
                continue
            tot = _bf_vol(x)
            if tot < (15000 if top else 10000):
                continue
            rs = [r for r in x.get("runners") or [] if isinstance(r, dict)]
            lead = max(rs, key=lambda r: r.get("vol") or 0, default=None)
            if not lead or (lead.get("vol") or 0) / tot < 0.85:
                continue
            if isinstance(lead.get("odd"), (int, float)) and (lead["odd"] < 1.35 or lead["odd"] > 20):   # Lucas 23.10.: ab 1,35
                continue                  # über 20 (z. B. 100,00) ist der Markt praktisch entschieden — kein Signal
            if not best or tot > best["eur"]:
                best = {**basis, "art": "ht", "markt": name, "eur": round(tot), "auf": lead.get("name"), "quote": lead.get("odd"),
                        **_hz_liga(norm, m.get("league"), name, tot),
                        "anteil": round(100 * (lead.get("vol") or 0) / tot),
                        "seiten": [{"name": r.get("name"), "anteil": round(100 * (r.get("vol") or 0) / tot)} for r in rs]}
        if best:
            out.append(best)
        # ⚫ Fix-Verdacht
        t = li.get("time")
        if isinstance(t, (int, float)) and t > 30:
            continue
        ft_max, ft_name = 0.0, None
        for name in BF_FIX_FT:
            if mk.get(name):
                v = _bf_vol(mk[name])
                if v > ft_max:
                    ft_max, ft_name = v, name
        if ft_max <= 0:
            continue
        fb = None
        for name in BF_FIX_HT:
            x = mk.get(name)
            if not x or not _bf_hz_offen(m, name):
                continue
            tot = _bf_vol(x)
            if tot < 2000:
                continue
            lead = max((r for r in x.get("runners") or [] if isinstance(r, dict)), key=lambda r: r.get("vol") or 0, default=None)
            if not lead or (lead.get("vol") or 0) / tot < 0.65:
                continue
            if isinstance(lead.get("odd"), (int, float)) and lead["odd"] < 1.15:
                continue
            if not fb or (lead.get("vol") or 0) > fb[0]:
                fb = (lead.get("vol") or 0, name, tot, lead)
        if fb and fb[2] >= 2 * ft_max:
            _, name, tot, lead = fb
            out.append({**basis, "art": "fix", "markt": name, "eur": round(tot), "ftEur": round(ft_max), "ftMarkt": ft_name,
                        **_hz_liga(norm, m.get("league"), name, tot),
                        "faktor": round(tot / ft_max, 1), "auf": lead.get("name"), "quote": lead.get("odd"),
                        "anteil": round(100 * (lead.get("vol") or 0) / tot)})
    # beides am selben Spiel → nur der Fix-Verdacht (der ist die schärfere Aussage)
    fix_ids = {x["id"] for x in out if x["art"] == "fix"}
    out = [x for x in out if x["art"] == "fix" or x["id"] not in fix_ids]
    out.sort(key=lambda x: -x["eur"])
    return out


def zufluss_liste(radar, consensus):
    """Frischer Zufluss aus der eigenen Historie: wo kam in den letzten 30 Min am meisten Geld dazu.

    Lucas 17.10.: oben stand oft nur 1–3 Spiele (die Liste des alten Projekts vergleicht nur zwei
    Abrufe). Jetzt die größten 30-Min-Zuflüsse über alle Ligen, entschiedene Spiele raus.
    """
    kons = {str(g.get("matchId")): g for g in (consensus or {}).get("games") or []}
    out = []
    for sp in radar.get("spiele") or []:
        # wie live: steigt die Quote der Auswahl, auf die das Geld „floss", ist es meist Lay-Geld → nicht zählen
        # vor Anpfiff gibt es keinen Zeiteffekt: steigt die Quote der Auswahl überhaupt (>1 %), war es Lay-Geld
        fl = [f for f in sp.get("flussVorher") or []
              if not (f.get("quote") and f.get("quoteVorher") and f["quote"] > f["quoteVorher"] * 1.01)]
        d = fl[0]["eur"] if fl else None
        # Live-Spiele raus: dort fließt laufend Geld, weil sich mit jedem Angriff die Quote dreht —
        # das ist Handel, kein frisches Urteil. Wie im alten Projekt nur vor Anpfiff.
        if not d or sp.get("entschieden") or sp.get("live"):
            continue
        vt = sp.get("verteilung") or []
        top = max(vt, key=lambda v: v.get("eur") or 0) if vt else None
        q = sp.get("quoten") or {}
        if top:
            seite, quote = top["name"], top.get("quote")
            idx = [n for n in ("heim", "remis", "gast")][[sp["heim"], "The Draw", sp["gast"]].index(seite)] if seite in (sp["heim"], "The Draw", sp["gast"]) else None
        else:
            # ohne Runner-Volumen: der Favorit laut Quote (bei kleinen Ligen liefert Betwatch keine Verteilung)
            idx = min((k for k in ("heim", "gast") if q.get(k)), key=lambda k: q[k], default=None)
            seite, quote = ({"heim": sp["heim"], "gast": sp["gast"]}.get(idx), q.get(idx) if idx else None)
        vor = (sp.get("quoten30") or {}).get(idx) if idx else None
        richtung = "ruhig"
        if vor and quote and abs(quote - vor) >= 0.02:
            richtung = "faellt" if quote < vor else "steigt"
        k = kons.get(sp["id"]) or {}
        pin = None
        if k.get("pinn") and idx:
            pin = {"fav": k["pinn"].get("fav"), "einig": {"heim": "home", "remis": "draw", "gast": "away"}[idx] == k["pinn"].get("fav")}
        out.append({"id": sp["id"], "heim": sp["heim"], "gast": sp["gast"], "liga": sp["liga"], "land": sp.get("land"), "top5": sp.get("top5"),
                    "anpfiff": sp["anpfiff"], "live": sp.get("live"), "markt": "alle Märkte", "zuflussEur": d,
                    "marktEur": sp["eur"], "xnorm": sp.get("xnorm"), "meistesGeldAuf": seite, "quote": quote,
                    "quoteVorher": vor, "quoteRichtung": richtung, "duenn": sp["eur"] < 3000,
                    "verteilung": [{"name": v["name"], "quote": v.get("quote"), "eur": v["eur"]} for v in vt] or None,
                    "pinnacle": pin, "fluss": fl})
    out.sort(key=lambda x: -x["zuflussEur"])
    return out[:20]


LIVE_ANZEIGE_MIN = {True: 20000, False: 3000}   # Anzeige auf der Seite (Push: 100k / 15k)


def live_zufluss(radar):
    """Lucas 17.10.: „vor allem in den restlichen Ligen, wenn da inplay viel kommt". Live-Spiele,
    in die in den letzten 15 Min Geld floss — ohne entschiedene (ab 70' / Quote unter 1,10)."""
    out = []
    for sp in radar.get("spiele") or []:
        # Lucas 17.10. (Pisa Über 2,5 @1,32 ↑ von 1,24, €2,9k): live ist €1,5k zu wenig, und Geld auf eine
        # Auswahl, deren Quote dabei STEIGT, ist auf Betfair meist Lay-Geld (gematcht wird beidseitig) oder
        # schlicht der Zeitablauf — kein Urteil für diese Seite. Nur fallende oder ruhige Quoten zählen.
        boden = LIVE_ANZEIGE_MIN[bool(sp.get("top5"))]
        # Lucas 18.10.: live steigt eine Quote oft schlicht mit der Zeit (Über ohne Tor, Außenseiter) —
        # darum KEIN Steigt-Filter live, nur Sprünge durch ein Spielereignis (Tor/Rot, ±40 %) fallen raus.
        fl = [f for f in sp.get("flussLive") or [] if f["eur"] >= boden
              and not (f.get("quote") and f.get("quoteVorher")
                       and not (1 / 1.4 <= f["quote"] / f["quoteVorher"] <= 1.4))]
        if sp.get("entschieden") or sp.get("klarVorn"):          # 24.10.: nur die Sieg-Märkte sind dann gesperrt
            fl = [f for f in fl if f.get("markt") not in SIEG_MAERKTE]
        fl = [f for f in fl if not _linie_durch(f.get("markt"), (sp.get("live") or {}).get("stand"))]
        d = fl[0]["eur"] if fl else None
        if not sp.get("live") or not d:
            continue
        out.append({k: sp.get(k) for k in ("id", "heim", "gast", "liga", "land", "anpfiff", "live", "eur", "xnorm",
                                            "quoten", "verteilung", "marktZufluss", "top5", "rang")} | {"zuflussEur": d, "fluss": fl})
    out.sort(key=lambda x: -x["zuflussEur"])
    return out[:20]


# ── Serien: läuft wiederholt Geld auf dieselbe Auswahl? (Lucas 17.10.: „wichtiger KPI in meinen Augen") ──
# Jeder Lauf schreibt je Spiel und Markt, welche Auswahl seit dem letzten Abruf (~5 Min) den Großteil des
# neuen Geldes bekam, in ein schlankes Protokoll (.bf/takt.json, 6 h, lebt im Actions-Cache). Daraus:
#   Serie = mind. 3 Abrufe mit derselben Auswahl vorn (≥60 % des neuen Geldes, mind. ein Boden-Betrag),
#           Lücken bis 15 Min zählen nicht als Abbruch, der letzte Treffer höchstens 20 Min alt.
TAKT_DATEI = Path(__file__).resolve().parent / ".bf" / "takt.json"
TAKT_H = 6
# Lucas 17.10. abends: „sehr viele Meldungen von kleinen Beträgen" → hochgesetzt (vorher 400/3k bzw. 4k/30k)
SERIE_BODEN = {True: 10000, False: 1000}     # neues Geld je Abruf, ab dem er als Treffer zählt (Top-5 / Rest)
SERIE_SUMME = {True: 75000, False: 10000}    # Summe der Serie, ab der sie gezeigt wird
SERIE_NORM_ANTEIL = 0.25                     # … und mind. ein Viertel dessen, was ein Spiel dieser Liga üblicherweise insgesamt hat
SERIE_MIN_N = 3
SERIEN_RUHIG = []                            # REGEL S4: abgeebbte Serien, nur zum Messen
SAEULEN = 48                                 # 4 h in 5-Min-Schritten


def takt_fortschreiben(bf_hist):
    try:
        takt = json.loads(TAKT_DATEI.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        takt = {}
    grenze = JETZT - timedelta(hours=TAKT_H)
    for mid, arr in (bf_hist or {}).items():
        if mid == "_meta" or not isinstance(arr, list):
            continue
        pts = [p for p in arr if isinstance(p, dict) and isinstance(p.get("sel"), dict) and ts(p.get("ts"))]
        if len(pts) < 2:
            continue
        a, b = pts[-2], pts[-1]
        if not (0 < (ts(b["ts"]) - ts(a["ts"])).total_seconds() <= 12 * 60):
            continue
        liste = takt.setdefault(mid, [])
        if liste and liste[-1].get("t") == b["ts"]:
            continue                                  # dieser Abruf steht schon drin
        h = {}
        for markt, sel in b["sel"].items():
            alt = (a.get("sel") or {}).get(markt) or {}
            d = {n: v[0] - (alt.get(n) or [0])[0] for n, v in sel.items()}
            ges = sum(x for x in d.values() if x > 0)
            if ges < 100:
                continue
            name, dv = max(d.items(), key=lambda kv: kv[1])
            h[f"{markt}|{name}"] = [round(dv), round(100 * dv / ges), sel[name][1]]
        if h:
            e_ = {"t": b["ts"], "h": h}
            if isinstance(b.get("sc"), list):
                e_["sc"] = b["sc"]          # Spielstand zum Abruf → ob in einer Serie ein Tor fiel
            liste.append(e_)
    takt = {mid: [x for x in liste if (ts(x.get("t")) or grenze) > grenze] for mid, liste in takt.items()}
    takt = {mid: liste for mid, liste in takt.items() if liste}
    try:
        TAKT_DATEI.parent.mkdir(exist_ok=True)
        TAKT_DATEI.write_text(json.dumps(takt, separators=(",", ":")), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        FEHLER.append(f"takt: {str(exc)[:100]}")
    return takt


PHASEN_META: dict = {}


def phasen(takt, radar):
    """Lucas 10.11. 12:18 (Phasenband, „auch für Instagram"): je Betfair-Spiel der Verlauf in 5-Min-Stücken —
    [Zeit, Seite des neuen Sieg-Geldes h/x/a, € dorthin, Anteil %, Tore-Geld netto (+ Über / − Unter), Stand]."""
    spiele = {str(sp.get("id")): sp for sp in (radar or {}).get("spiele") or []}
    # 10.11. (X-Bilder „How the money moved"): auch beendete Spiele, die nicht mehr im Radar stehen — Namen/Endstand
    # aus dem Ergebnisbuch; PHASEN_META sammelt Anpfiff, Liga und Endstand dazu
    try:
        erg = json.loads((BASIS / ".bf" / "ergebnisse.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        erg = {}
    out = {}
    PHASEN_META.clear()
    for mid, liste in (takt or {}).items():
        sp = spiele.get(str(mid))
        e_ = erg.get(str(mid)) or {}
        if not sp and e_.get("h") and e_.get("a"):
            sp = {"id": str(mid), "heim": e_["h"], "gast": e_["a"], "anpfiff": e_.get("ko"), "liga": e_.get("l")}
        if not sp:
            continue
        PHASEN_META[str(mid)] = {"heim": sp.get("heim"), "gast": sp.get("gast"), "anpfiff": sp.get("anpfiff") or e_.get("ko"),
                                 "liga": sp.get("liga") or e_.get("l"), "land": sp.get("land"), "ft": e_.get("ft"),
                                 "imRadar": str(mid) in spiele}
        z = []
        for x in liste:
            mo = [(k.split("|", 1)[1], v) for k, v in (x.get("h") or {}).items() if k.startswith("Match Odds|")]
            seite = eur = anteil = None
            if mo:
                n, v = mo[0]
                seite = "h" if n == sp.get("heim") else "a" if n == sp.get("gast") else "x" if n == "The Draw" else None
                eur, anteil = v[0], v[1]
            tore = sum((v[0] if k.split("|", 1)[1].startswith("Over") else -v[0])
                       for k, v in (x.get("h") or {}).items() if k.startswith("Over/Under ") and "Goals" in k)
            if seite is None and not tore:
                continue
            t = ts(x.get("t"))
            z.append([t.strftime("%Y-%m-%dT%H:%M") if t else None, seite, eur, anteil, round(tore), x.get("sc")])
        if len(z) >= 3:
            out[str(mid)] = z
    return out


def serien(takt, radar):
    spiele = {sp["id"]: sp for sp in (radar or {}).get("spiele") or []}
    out = []
    for mid, liste in takt.items():
        sp = spiele.get(str(mid))
        if not sp:
            continue
        # Lucas 24.10. („der Sieg-Markt ist entschieden, aber auf Über 1,5 kann noch Geld kommen — das sollte man sehen"):
        # entschieden/klar vorn sperrt nur noch die Sieg-Märkte, nicht das ganze Spiel. Tor-Linien, die schon durch sind,
        # und Quoten unter 1,20 fallen je Markt raus.
        sieg_tot = bool(sp.get("entschieden") or sp.get("klarVorn"))
        top5 = bool(sp.get("top5"))
        letzte_t = ts(liste[-1]["t"])
        if not letzte_t or JETZT - letzte_t > timedelta(minutes=25):
            continue
        for key in {k for x in liste for k in x["h"]}:
            treffer = [(ts(x["t"]), *x["h"][key], tuple(x.get("sc") or ())) for x in liste if key in x["h"]
                       and x["h"][key][0] >= SERIE_BODEN[top5] and x["h"][key][1] >= 60]
            if len(treffer) < SERIE_MIN_N:
                continue
            kette = [treffer[-1]]
            for tr in reversed(treffer[:-1]):          # rückwärts, solange die Lücken klein bleiben
                if (kette[-1][0] - tr[0]).total_seconds() <= 16 * 60:
                    kette.append(tr)
                else:
                    break
            kette.reverse()
            if len(kette) < SERIE_MIN_N or JETZT - kette[-1][0] > timedelta(minutes=20):
                continue
            summe = sum(k[1] for k in kette)
            if summe < max(SERIE_SUMME[top5], SERIE_NORM_ANTEIL * (sp.get("normEur") or 0)):
                continue
            # Lucas 18.10.: fiel während der Serie ein Tor, muss das dabeistehen — Geld nach einem Tor ist
            # oft Nachlegen auf den Führenden, nicht das frühe Urteil
            # 03.11. (Kobe – Ratchaburi: Serie ab vor Anpfiff, 1:0, dann 1:1 — „Tor während der Serie" fehlte): vor Anpfiff
            # gibt es keinen Stand → als 0:0 zählen, sobald das Spiel läuft. Sonst sah eine Serie über zwei Tore torlos aus.
            nsc = lambda s_: tuple(s_) if s_ else ((0, 0) if kette[-1][4] else ())
            tor = None
            st0, st1 = nsc(kette[0][4]), nsc(kette[-1][4])
            if st0 and st1 and st0 != st1:
                tor = {"von": list(st0), "nach": list(st1),
                       "vorTor": round(sum(k[1] for k in kette if nsc(k[4]) == st0)),
                       "nachTor": round(sum(k[1] for k in kette if nsc(k[4]) != st0)),
                       # Geld seit dem LETZTEN Stand (nach dem letzten Tor) und zu welcher Quote das Geld davor kam
                       "seitStand": round(sum(k[1] for k in kette if nsc(k[4]) == st1)),
                       "nSeitStand": sum(1 for k in kette if nsc(k[4]) == st1),
                       "anteilSeitStand": round(sum(k[2] for k in kette if nsc(k[4]) == st1) / max(1, sum(1 for k in kette if nsc(k[4]) == st1))),
                       "quoteVorher": round(sum(k[1] * k[3] for k in kette if nsc(k[4]) != st1 and isinstance(k[3], (int, float)))
                                            / max(1, sum(k[1] for k in kette if nsc(k[4]) != st1 and isinstance(k[3], (int, float)))), 2)}
            markt, auswahl = key.split("|", 1)
            if (sieg_tot and markt in SIEG_MAERKTE) or _linie_durch(markt, (sp.get("live") or {}).get("stand")):
                continue
            lv = sp.get("live") or {}
            if _ist_hz_markt(markt) and not lv.get("ht") and isinstance(lv.get("min"), (int, float)) and lv["min"] > 45:
                continue                               # Halbzeit-Markt nach der Pause: abgerechnet
            if isinstance(kette[-1][3], (int, float)) and kette[-1][3] < 1.20:
                continue                               # wie beim Zufluss: praktisch entschieden
            q0, q1 = kette[0][3], kette[-1][3]
            if not sp.get("live") and isinstance(q0, (int, float)) and isinstance(q1, (int, float)) and q1 > q0 * 1.01:
                continue                               # vor Anpfiff: Quote stieg während der Serie → eher Lay-Geld
            saeulen = [0] * SAEULEN
            for x in liste:
                v = x["h"].get(key)
                if not v:
                    continue
                i = SAEULEN - 1 - int((JETZT - ts(x["t"])).total_seconds() // 300)
                if 0 <= i < SAEULEN:
                    saeulen[i] = max(saeulen[i], v[0])
            # Lucas 18.10.: Tore auch im Säulenbild markieren — an welcher Säule sich der Stand änderte
            tore, vor_sc = [], None
            for x in liste:
                sc = tuple(x.get("sc") or ())
                if sc and vor_sc and sc != vor_sc:
                    i = SAEULEN - 1 - int((JETZT - ts(x["t"])).total_seconds() // 300)
                    if 0 <= i < SAEULEN:
                        tore.append({"i": i, "stand": list(sc)})
                if sc:
                    vor_sc = sc
            out.append({"id": str(mid), "heim": sp["heim"], "gast": sp["gast"], "liga": sp["liga"], "land": sp.get("land"),
                        "anpfiff": sp["anpfiff"], "live": sp.get("live"), "top5": top5, "quelle": "betfair", "tore": tore,
                        "markt": markt, "auswahl": auswahl, "n": len(kette), "eur": round(summe),
                        "anteil": round(sum(k[2] for k in kette) / len(kette)),
                        "quote": kette[-1][3], "quoteStart": kette[0][3],
                        "seit": iso(kette[0][0]), "dauerMin": round((kette[-1][0] - kette[0][0]).total_seconds() / 60) + 5,
                        "letzteVorMin": round((JETZT - kette[-1][0]).total_seconds() / 60), "saeulen": saeulen,
                        # Lucas 07.11. (Boca 45': „€57k in 75 Min" las sich wie gerade eben — kam aber großteils um Anpfiff):
                        # wie viel davon in den letzten 15 Min kam und wann der größte Abruf war (seit dem letzten Stand)
                        "letzte15": round(sum(k[1] for k in kette if JETZT - k[0] <= timedelta(minutes=15))),
                        "spitze": (lambda kk: [round(kk[1]), iso(kk[0])])(max((k for k in kette if nsc(k[4]) == st1), key=lambda k: k[1])),
                        "tor": tor,
                        # für den Push: wie der Markt jetzt insgesamt liegt (Lucas 18.10.)
                        "marktVerteilung": next(([[a["name"], a["eur"], a.get("quote"), round(summe) if a["name"] == auswahl else 0]
                                                  for a in m["auswahl"]] for m in sp.get("maerkteDetail") or [] if m["name"] == markt), None)})
            # Lucas 03.11. (Port FC – Beijing 72' 3:4, Top 3 „15× in Folge" über sieben Tore): REGEL S3 auch an der Quelle —
            # live zählt nur das Geld seit dem letzten Tor. Reicht das (3 Abrufe, Mindestsumme) nicht, gibt es live keine Serie.
            x = out[-1]
            if sp.get("live") and tor and tor.get("seitStand") is not None:
                kt = [k for k in kette if nsc(k[4]) == st1]
                if len(kt) < SERIE_MIN_N or tor["seitStand"] < max(SERIE_SUMME[top5], SERIE_NORM_ANTEIL * (sp.get("normEur") or 0)):
                    out.pop()
                    continue
                x.update({"nGesamt": x["n"], "eurGesamt": x["eur"], "n": len(kt), "eur": tor["seitStand"],
                          "anteil": round(sum(k[2] for k in kt) / len(kt)), "quoteStart": kt[0][3], "seit": iso(kt[0][0]),
                          # LD3: der erste Abruf nach dem Tor kann noch Vor-Tor-Preise tragen → für „gegen die Uhr" den zweiten
                          "quoteStartUhr": kt[1][3] if len(kt) >= 2 else None,
                          "dauerMin": round((kt[-1][0] - kt[0][0]).total_seconds() / 60) + 5})
                if x.get("marktVerteilung"):
                    x["marktVerteilung"] = [[v[0], v[1], v[2], tor["seitStand"] if v[3] else 0] for v in x["marktVerteilung"]]
    # REGEL S4 (Lucas 07.11., Boca – Defensa 45': „Dauerzufluss sollte halt wirklich was sein in einem gewissen Zeitfenster,
    # wo Geld aktiv reinkommt"): die Kette lief mit ~€1k je Abruf weiter, obwohl der Fluss längst abgeebbt war.
    # Eine Serie zählt nur, solange in den letzten 15 Min mind. die Hälfte ihres eigenen Schnitts je 15 Min kommt
    # (und mind. zwei Treffer-Böden). Die anderen gehen still nach „serienRuhig" (Messung, kein Push, nicht auf der Seite).
    SERIEN_RUHIG.clear()
    aktiv = []
    for x in out:
        schnitt15 = x["eur"] / max(15, x.get("dauerMin") or 15) * 15
        x["schnitt15"] = round(schnitt15)
        if (x.get("letzte15") or 0) >= max(2 * SERIE_BODEN[bool(x.get("top5"))], 0.5 * schnitt15):
            aktiv.append(x)
        else:
            SERIEN_RUHIG.append({**x, "regel": "S4"})
    out = aktiv
    # je Spiel nur die stärkste Serie (sonst steht ein Spiel mit drei Märkten dreimal da)
    best = {}
    for x in sorted(out, key=lambda x: -x["eur"]):
        best.setdefault(x["id"], x)
    return sorted(best.values(), key=lambda x: (-x["n"], -x["eur"]))[:20]


def poly_serien(pf):
    """Dieselbe Idee für Polymarket, aus den 10-Min-Säulen von poly_fluss.py (Trades mit Zeitstempel)."""
    out = []
    pf_stand = ts((pf or {}).get("generatedAt") or (pf or {}).get("stand"))
    pf_alt = (JETZT - pf_stand).total_seconds() / 60 if pf_stand else 99
    for m in (pf or {}).get("maerkte") or []:
        if m.get("live") and pf_alt > 12:
            continue                    # 11.11.: live bewegt sich der Preis nach jeder Map/jedem Tor — nur frische Daten
        if m.get("entschieden"):
            continue                    # 10.11. (Butterfly – Honvéd): Markt praktisch entschieden → kein Dauerzufluss
        takt, summe_b = m.get("takt") or {}, m.get("taktSumme") or []
        for lab, werte in takt.items():
            n = len(werte)
            hits = [i for i, v in enumerate(werte) if v >= 1000 and summe_b and summe_b[i] > 0 and v / summe_b[i] >= 0.6]
            if len(hits) < SERIE_MIN_N or hits[-1] < n - 2:
                continue
            kette = [hits[-1]]
            for i in reversed(hits[:-1]):
                if kette[-1] - i <= 2:
                    kette.append(i)
                else:
                    break
            if len(kette) < SERIE_MIN_N:
                continue
            summe = sum(werte[i] for i in kette)
            if summe < (25000 if m.get("top5") else 6000):
                continue
            schnitt = (m.get("preise") or {}).get(lab)
            zl = (m.get("zuletzt") or {}).get(lab)
            jetzt_p = (m.get("jetzt") or {}).get(lab)
            # 11.11. (Lucas, GZG – Frank Esports LoL BO5: Push „@ 1,83", beim Buchmacher 1 Min später 1,41 — Frank hatte
            # gerade Map 1 gewonnen): der aktuelle Preis = jüngster Kauf der letzten 10 Min, sonst outcomePrices (bei
            # dünnen Büchern oft der alte letzte Trade). Liegt er mehr als 8 ¢ vom Preis, zu dem das Geld kam, hat der
            # Markt neu bewertet — das Signal ist überholt → kein Dauerzufluss.
            preis = (zl[0] if zl and zl[1] <= 10 else None) or jetzt_p or schnitt
            if preis and schnitt and abs(preis - schnitt) > 0.08:
                continue
            if preis and jetzt_p and abs(preis - jetzt_p) > 0.08:
                continue
            ko = ts(m.get("anpfiff"))
            # Lucas 17.10. (Betis–Barça 5:2, Push 10 Min nach Abpfiff): gut 2 h nach Anpfiff ist das Spiel aus
            if ko and (m.get("sport") or "Fußball") == "Fußball" and JETZT - ko > timedelta(minutes=115):
                continue
            if preis and preis > 1 / 1.20:          # 23.10.: wie Betfair und Stake — unter Quote 1,20 kein Dauerzufluss
                continue
            out.append({"preis": preis, "quote": round(1 / preis, 2) if preis else None, "id": m.get("key"), "titel": m.get("titel"), "liga": m.get("liga"), "sport": m.get("sport"),
                        "anpfiff": m.get("anpfiff"), "live": m.get("live"), "top5": m.get("top5"), "quelle": "poly",
                        "auswahl": lab, "n": len(kette), "usd": round(summe),
                        "anteil": round(100 * summe / max(1, sum(summe_b[i] for i in kette))),
                        "dauerMin": (max(kette) - min(kette) + 1) * 10, "saeulen": werte})
    best = {}
    for x in sorted(out, key=lambda x: -x["usd"]):
        best.setdefault(x["id"], x)
    return sorted(best.values(), key=lambda x: (-x["n"], -x["usd"]))[:12]



def stake_serien(hr):
    """Dieselbe Idee für Stake (Lucas 17.10.: „Ja"), aus den einzelnen Highroller-Wetten mit Zeitstempel
    (stake_highroller.json vom Mac, Wetten ab $1000). Nur Einzelwetten — bei Kombis ist unklar, welches Bein
    das Geld trägt. 10-Min-Abschnitte über 4 h; Serie wie bei Polymarket."""
    asof = ts((hr or {}).get("asof"))
    if not asof or JETZT - asof > timedelta(minutes=45):
        return []
    top5_slugs = {"premier-league", "la-liga", "laliga", "bundesliga", "serie-a", "ligue-1", "uefa-champions-league"}
    gruppen = {}
    for w in (hr or {}).get("wetten") or []:
        if not isinstance(w, dict) or w.get("kombi") or not stake_ok(w):
            continue
        t = ts(w.get("ts"))
        if not t:
            continue
        b = int((asof - t).total_seconds() // 600)
        if not 0 <= b < 24:
            continue
        i = 23 - b
        ev = (w.get("eventId"), w.get("markt"))
        g = gruppen.setdefault(ev, {"summe": [0.0] * 24, "sel": {}, "w": w})
        g["summe"][i] += w.get("einsatzUsd") or 0
        sel = g["sel"].setdefault(w.get("auswahl"), {"werte": [0.0] * 24, "quote": None, "n": 0, "letzte": None})
        sel["werte"][i] += w.get("einsatzUsd") or 0
        if not sel["letzte"] or t >= sel["letzte"]:
            sel["letzte"] = t
            sel["quote"] = w.get("beinQuote") or w.get("quote") or sel["quote"]
        sel["n"] += 1
    out = []
    for (eid, markt), g in gruppen.items():
        w0 = g["w"]
        top5 = str(w0.get("ligaSlug") or "") in top5_slugs or str(w0.get("liga") or "") == "UEFA Nations League"
        for auswahl, sel in g["sel"].items():
            v = sel["werte"]
            hits = [i for i in range(24) if v[i] >= 1000 and g["summe"][i] > 0 and v[i] / g["summe"][i] >= 0.6]
            if len(hits) < SERIE_MIN_N or hits[-1] < 22:
                continue
            kette = [hits[-1]]
            for i in reversed(hits[:-1]):
                if kette[-1] - i <= 2:
                    kette.append(i)
                else:
                    break
            if len(kette) < SERIE_MIN_N:
                continue
            summe = sum(v[i] for i in kette)
            if summe < (50000 if top5 else 15000):
                continue
            # Lucas 23.10. („No @ 1,01 — dachte, sowas gibt's nicht mehr"): wie bei Betfair, unter 1,20 ist das Einsammeln
            # oder Absicherung, kein Dauerzufluss — auch nicht auf der Seite
            if isinstance(sel.get("quote"), (int, float)) and sel["quote"] < 1.20:
                continue
            # Lucas 18.10. (Forest–Arsenal „Over 0,5 Halbzeit @1,55" kam in der Pause): die Abschnitte zählten ab dem
            # Stand der Stake-Datei, nicht ab jetzt — eine Serie konnte bis ~1 h nach der letzten Wette noch melden.
            # Jetzt: letzte Wette höchstens 15 Min her, und Märkte der 1. Halbzeit nur bis zur 45. Minute.
            if not sel["letzte"] or JETZT - sel["letzte"] > timedelta(minutes=15):
                continue
            ko = ts(w0.get("anpfiff"))
            if re.search(r"half.?time|1st half|first half|halbzeit", str(markt or ""), re.I) and ko and JETZT - ko > timedelta(minutes=45):
                continue
            out.append({"letzteVorMin": round((JETZT - sel["letzte"]).total_seconds() / 60), "id": eid, "titel": w0.get("event"), "liga": w0.get("liga"), "sport": w0.get("kat"),
                        "anpfiff": w0.get("anpfiff"), "live": w0.get("phase") == "live", "top5": top5, "quelle": "stake",
                        "markt": markt, "auswahl": auswahl, "quote": sel["quote"], "n": len(kette), "usd": round(summe),
                        "anteil": round(100 * summe / max(1, sum(g["summe"][i] for i in kette))),
                        "wetten": sel["n"], "dauerMin": (max(kette) - min(kette) + 1) * 10, "saeulen": [round(x) for x in v],
                        "zeit": iso(asof)})
    best = {}
    for x in sorted(out, key=lambda x: -x["usd"]):
        best.setdefault(x["id"], x)
    return sorted(best.values(), key=lambda x: (-x["n"], -x["usd"]))[:12]


# ── Wo am meisten liegt: alle Quellen je Spiel (Lucas 17.10.: „Übersicht anders als die Betfair-Seite") ──
# Dasselbe Spiel heißt überall anders („Real Betis – Barcelona", „Real Betis Balompié vs. FC Barcelona",
# „Real Betis - Barcelona"). Zuordnung über bereinigte Teamnamen + Anpfiff (±3 h). Im Zweifel NICHT
# zusammenlegen — dann steht das Spiel nur mit den sicher zugeordneten Quellen da.
USD_EUR = 0.86          # grobe Umrechnung nur fürs Sortieren und die ≈-Summe; jede Quelle zeigt ihre eigene Währung
_FUELL = {"fc", "cf", "sc", "ac", "as", "afc", "club", "de", "del", "la", "the", "calcio", "sv", "cd", "ud", "sd", "rc",
          "fk", "sk", "if", "bk", "kv", "ssc", "us", "vs", "balompie", "1", "cfc", "saudi", "football"}


_GENERISCH = {"real", "united", "city", "atletico", "athletic", "sporting", "deportivo", "racing", "inter", "internazionale",
              "saint", "dinamo", "club", "union", "nacional", "independiente", "universidad", "town", "rovers", "wanderers",
              "olimpia", "esporte", "clube", "sport", "america", "mineiro", "paris", "new", "york", "san", "santa"}
_ALIAS = {"utd": "united", "man": "manchester", "atl": "atletico", "atletico": "atletico", "mg": "mineiro",
          "rennais": "rennes", "mgladbach": "gladbach", "monchengladbach": "gladbach", "borussia": "", "nottm": "nottingham",
          "wolves": "wolverhampton", "spurs": "tottenham", "psg": "paris", "munchen": "munich", "koln": "cologne",
          "inter": "internazionale", "athletic": "athletic", "bilbao": "bilbao", "sporting": "sporting", "olympique": "",
          "stade": "", "dep": "deportivo", "athletico": "paranaense", "pr": "paranaense", "dynamo": "dinamo", "fcsb": "steaua", "st": "saint", "ath": "athletic"}


def _tokens(name):
    import unicodedata
    t = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"\(.*?\)", " ", t)
    aus = set()
    for w in re.split(r"[^a-z0-9]+", t):
        w = _ALIAS.get(w, w)
        if w and len(w) >= 3 and not w.isdigit() and w not in _FUELL:
            aus.add(w)
    # Allerweltswörter („Real", „United", „Atletico") tragen allein keine Zuordnung — sonst wäre
    # Real Madrid = Real Sociedad. Nur wenn der Name aus nichts anderem besteht, bleiben sie.
    return aus


def _gleich(a, b, locker=False):
    """Gleiches Team? Gemeinsames Kernwort (ohne Allerweltswörter wie „Real", „Atletico"), oder (locker)
    gleicher Wortanfang ab 4 Buchstaben („Rennes"/„Rennais"). Besteht ein Name NUR aus Allerweltswörtern
    („Atlético Nacional", „Real Racing Club"), reicht ein gemeinsames Wort davon."""
    va, vb = _tokens(a), _tokens(b)
    if not va or not vb:
        return False
    ka, kb = va - _GENERISCH, vb - _GENERISCH
    if ka & kb:
        return True
    if (not ka or not kb) and (va & vb):
        return True
    if not ka or not kb:
        return False
    n = 4 if locker else 5
    return any(len(x) >= n and len(y) >= n and x[:n] == y[:n] for x in ka for y in kb)


def _teams(titel):
    for sep in (" vs. ", " vs ", " - ", " – ", " v "):
        if sep in str(titel or ""):
            h, g = str(titel).split(sep, 1)
            return h.strip(), g.strip()
    return None, None


def _seite(name, heim, gast):
    n = str(name or "")
    if re.match(r"^(the draw|draw|x|remis|unentschieden)\b", n, re.I):
        return "remis"
    if _gleich(n, heim) and not _gleich(n, gast):
        return "heim"
    if _gleich(n, gast) and not _gleich(n, heim):
        return "gast"
    return None


def belegte_positionen(wallets, track, *quellen):
    """Polymarket-Schlüssel -> offene Positionen BELEGTER Wallets (Untergrenze des CLV > 0) (Lucas 18.10.).

    Quellen: die offenen Positionen aus dem Wallet-Track (alle, nicht nur die Top 5 je Wallet) und die
    Großhalter-Listen der Markt-Scans (upcoming / live / offen). Je Wallet + Markt + Seite nur einmal."""
    bew = {w["wallet"]: w for w in wallets or [] if w.get("bewiesen")}
    out = {}

    def add(key, wallet, seite, usd, preis=None):
        if wallet not in bew or not key or not usd or float(usd) < 1000:
            return                      # Kleinstpositionen ($118) sagen nichts
        liste = out.setdefault(key, [])
        for x in liste:
            if x["wallet"] == wallet and x["seite"] == seite:
                x["usd"] = max(x["usd"], round(float(usd)))
                return
        w = bew[wallet]
        liste.append({"wallet": wallet, "seite": seite, "usd": round(float(usd)), "preis": preis,
                      "clv": w.get("fensterClv"), "clvUg": w.get("clvUg"), "n": w.get("fensterN")})

    for p in ((track or {}).get("open") or {}).values():
        if isinstance(p, dict) and zukunft(p.get("koTs"), nach_h=2.2, bis_h=72):
            add(p.get("key"), p.get("wallet"), p.get("side"), p.get("usd"), p.get("lastPrice") or p.get("firstPrice"))
    for q in quellen:
        for key, m in (q or {}).items():
            if not isinstance(m, dict):
                continue
            for wh in m.get("whales") or []:
                if isinstance(wh, dict):
                    add(key, wh.get("wallet"), wh.get("side"), wh.get("usd"), wh.get("avgPrice"))
    for liste in out.values():
        liste.sort(key=lambda x: -x["usd"])
    return out


# Regel PK1 (Lucas 20.11. 18:50 „ja, bitte"): Konsens bewährter Wallets, still gemessen. Vorab festgelegt:
# * ≥ 2 verschiedene bewährte Wallets (CLV-Untergrenze > 0, je ≥ $1k) auf DERSELBEN Seite, zusammen ≥ $5k,
# * bewährte Wallets auf allen anderen Seiten zusammen ≤ $2k,
# * vor Anpfiff (bis 72 h), Preis der Seite 5–95 ¢, Sportarten und Märkte wie überall.
# Zum Vergleich „pk1Streit": dieselbe Mehrheit, aber > $2k bewährtes Geld dagegen (Lucas' E-Sport-Frage).
# Gebucht einmal je Markt + Seite beim ersten Erkennen; Abrechnung wie Wal-Pushes (Schlusskurs + Ergebnis).
# Urteil nach 100 abgerechneten Fällen: trägt, wenn CLV im Mittel > +1 % UND ROI > 0.
PK1_MIN_WALE, PK1_MIN_USD, PK1_MAX_GEGEN = 2, 5000, 2000


def pk1(belegt, maerkte):
    out = []
    for key, liste in (belegt or {}).items():
        m = (maerkte or {}).get(key)
        if not m or m.get("live") or not poly_key_ok(key) or not sport_ok(m.get("sport")) or not zukunft(m.get("koTs"), nach_h=0, bis_h=72):
            continue
        seiten = {}
        for x in liste:
            seiten.setdefault(x["seite"], []).append(x)
        for seite, wale in seiten.items():
            if len({x["wallet"] for x in wale}) < PK1_MIN_WALE:
                continue
            usd = sum(x["usd"] for x in wale)
            gegen = sum(x["usd"] for s2, l2 in seiten.items() if s2 != seite for x in l2)
            p = (m.get("prices") or {}).get(seite)
            if usd < PK1_MIN_USD or not isinstance(p, (int, float)) or not 0.05 <= p <= 0.95:
                continue
            out.append({"key": key, "titel": m.get("titel"), "liga": m.get("league"), "sport": m.get("sport"), "anpfiff": m.get("koTs"),
                        "seite": seite, "preis": p, "quote": round(1 / p, 3), "usd": usd, "nWale": len(wale), "gegenUsd": gegen,
                        "streit": gegen > PK1_MAX_GEGEN, "marktUsd": m.get("totalUsd"),
                        "wale": [{"w": x["wallet"][-4:], "usd": x["usd"], "clvUg": x.get("clvUg")} for x in wale]})
    out.sort(key=lambda x: (x["streit"], -x["usd"]))
    return out


# Regel CM1 „Claude-Master" (Lucas 20.11. 19:13: „eine Push von dir mit allen Kriterien, die dir einfallen … zum Testen").
# Vorab festgelegt 20.11. 19:40 Wien, aus dem Push-Buch 20.10.–20.11. abgeleitet (CLV ohne Vorgriff gemessen):
# * Einziges Signal mit belastbarem Vorsprung auf den Schlusskurs vor Anpfiff: eine bewährte Wallet setzt mindestens
#   ihren üblichen Einsatz (Faktor ≥ 1): CLV +4,8 % ± 3,2 (n = 87). Alles andere vor Anpfiff liegt bei 0 bis +1 %;
#   „Pinnacle + Betfair-Geld" sah mit Vorgriff nach +4,7 % aus, ab dem Moment der Erfüllung −0,2 %.
#   Live-Signale mit hohem ROI (Polymarket-Dauerzufluss live +15 %) kommen vom Preis im Push-Moment: 15 Min später
#   gekauft ±0 % — für einen Push nicht erreichbar. Darum nur vor Anpfiff.
# * Bedingungen: bewährte Wallet (CLV-Untergrenze > 0) hält ≥ $3k und ≥ 1× ihren Schnitt auf einer Seite; Preis 20–80 ¢;
#   Anpfiff in 20 Min bis 24 h; bewährtes Geld auf den anderen Seiten zusammen ≤ $2k (PK1: kein Streit der Guten).
# * Je Markt höchstens ein Push. Messung: CLV gegen den Polymarket-Schlusskurs + Ergebnis.
#   Urteil nach 60 Fällen: trägt bei Ø-CLV > +2 % (die halbe Rückblick-Kante) UND ROI > 0.
CM1_MIN_USD, CM1_FAKTOR, CM1_GEGEN_MAX = 3000, 1.0, 2000


def cm1(belegt, maerkte, wallets):
    schnitt = {w["wallet"]: w for w in wallets or []}
    out = []
    for key, liste in (belegt or {}).items():
        m = (maerkte or {}).get(key)
        if not m or m.get("live") or not poly_key_ok(key) or not sport_ok(m.get("sport")):
            continue
        ko = ts(m.get("koTs"))
        if not ko or not timedelta(minutes=20) <= ko - JETZT <= timedelta(hours=24):
            continue
        for x in liste:
            w = schnitt.get(x["wallet"]) or {}
            su = w.get("schnittUsd")
            p = (m.get("prices") or {}).get(x["seite"])
            if x["usd"] < CM1_MIN_USD or not su or x["usd"] / su < CM1_FAKTOR or not isinstance(p, (int, float)) or not 0.2 <= p <= 0.8:
                continue
            mit = [y for y in liste if y["seite"] == x["seite"] and y["wallet"] != x["wallet"]]
            gegen = [y for y in liste if y["seite"] != x["seite"]]
            if sum(y["usd"] for y in gegen) > CM1_GEGEN_MAX:
                continue
            out.append({"key": key, "titel": m.get("titel"), "liga": m.get("league"), "sport": m.get("sport"), "anpfiff": m.get("koTs"),
                        "seite": x["seite"], "preis": p, "quote": round(1 / p, 3), "usd": x["usd"], "faktor": round(x["usd"] / su, 2),
                        "schnittUsd": su, "wallet": x["wallet"], "clv": w.get("fensterClv"), "clvUg": w.get("clvUg"), "n": w.get("fensterN"),
                        "rang": next((i + 1 for i, v in enumerate(wallets or []) if v["wallet"] == x["wallet"]), None),
                        "pnl": w.get("pnl"), "mit": [{"w": y["wallet"][-4:], "usd": y["usd"]} for y in mit],
                        "gegenUsd": sum(y["usd"] for y in gegen), "marktUsd": m.get("totalUsd"),
                        "geldSeite": (m.get("shares") or {}).get(x["seite"]), "geldAlle": sum((m.get("shares") or {}).values()) or None})
    out.sort(key=lambda c: -c["faktor"] * c["usd"])
    return out


# Lucas 19.10.: „Jetzt fließt Geld rein" bleibt reines Betfair — bekommt dasselbe Spiel in den letzten 30 Min auch auf
# Polymarket oder Stake Geld, zeigt die Zeile einen kleinen Chip. geld_toepfe füllt diese Karte nebenbei (Betfair-ID → Quelle).
AUCH_WOANDERS = {}


def geld_toepfe(radar, poly_alle, hr, pf=None, belegt=None):
    spiele = []
    AUCH_WOANDERS.clear()
    # 1) Betfair als Grundgerüst (hat Land, Liga-Namen, Live-Stand)
    for sp in (radar or {}).get("spiele") or []:
        seiten = {}
        for v in sp.get("verteilung") or []:
            k = _seite(v["name"], sp["heim"], sp["gast"])
            if k:
                seiten[k] = seiten.get(k, 0) + (v.get("eur") or 0)
        spiele.append({"id": sp["id"], "heim": sp["heim"], "gast": sp["gast"], "liga": sp["liga"], "land": sp.get("land"),
                       "anpfiff": sp["anpfiff"], "live": sp.get("live"), "top5": bool(sp.get("top5")), "sport": "Fußball",
                       "entschieden": bool(sp.get("entschieden")),
                       "q": {"betfair": {"betrag": sp["eur"], "seiten": seiten, "quoten": sp.get("quoten")}}})

    def finden(heim, gast, anpfiff):
        """Beide Teams passen (Anpfiff ±3 h) — oder Anpfiff fast gleich (±20 Min) und ein Team sicher, das andere locker."""
        t = ts(anpfiff)
        for g in spiele:
            tg = ts(g["anpfiff"])
            dt = abs((t - tg).total_seconds()) if t and tg else 0
            if dt > 3 * 3600:
                continue
            for a, b in ((heim, gast), (gast, heim)):
                if _gleich(a, g["heim"]) and _gleich(b, g["gast"]):
                    return g
                if dt <= 20 * 60 and ((_gleich(a, g["heim"]) and _gleich(b, g["gast"], locker=True))
                                      or (_gleich(a, g["heim"], locker=True) and _gleich(b, g["gast"]))):
                    return g
        return None

    # 2) Polymarket — ALLE Märkte (upcoming + laufend), nicht nur die ausgewählten der Poly-Seite.
    #    Die Schlüssel tragen nur Kürzel („mls-nyr-tor-…"); die Teamnamen stehen in den Preis-Schlüsseln.
    #    „…-more-markets" (Tore, BTTS, Handicap) zählt zum Volumen desselben Spiels.
    pf_takt = {m.get("key"): m.get("takt") or {} for m in (pf or {}).get("maerkte") or [] if m.get("takt")}
    # 03.11.: letzte Käufe je Seite (bis 500 Trades je Markt) — auch für Märkte ohne Käufe in 30 Min
    pf_alle = {m.get("key"): (m.get("alle"), m.get("alleReichweiteMin")) for m in (pf or {}).get("maerkte") or [] if m.get("alle")}
    pf_alle.update({k: (v.get("alle"), v.get("reichweiteMin")) for k, v in ((pf or {}).get("seitenAlle") or {}).items() if v.get("alle")})
    pf_30 = {}
    for m in (pf or {}).get("maerkte") or []:
        su = sum((m.get("taktSumme") or [])[-3:])       # 3 × 10 Min = die letzten 30 Min
        if su > 0:
            seite = max(((n, sum((w or [])[-3:])) for n, w in (m.get("takt") or {}).items()), key=lambda kv: kv[1], default=(None, 0))
            pf_30[m.get("key")] = (su, seite[0])
    mm_vol = {}
    for key, m in (poly_alle or {}).items():
        if isinstance(m, dict) and key.endswith("-more-markets"):
            mm_vol[key[:-len("-more-markets")]] = float(m.get("totalUsd") or 0)
    for key, m in (poly_alle or {}).items():
        if not isinstance(m, dict) or poly_neben(key)[1] or not poly_key_ok(key):
            continue
        if not sport_ok(m.get("sport")):
            continue
        teams = [n for n in (m.get("prices") or {}) if not re.match(r"^draw", n, re.I)]
        if len(teams) != 2:
            continue
        h, gst = teams
        g = finden(h, gst, m.get("koTs"))
        if not g:
            g = {"id": "pm:" + key, "heim": h, "gast": gst, "liga": m.get("league"), "land": None,
                 "anpfiff": m.get("koTs"), "live": None, "top5": key.split("-")[0] in POLY_TOP5,
                 "sport": m.get("sport") or "Fußball", "q": {}}
            spiele.append(g)
        betrag = float(m.get("totalUsd") or 0) + mm_vol.get(key, 0)
        if (g["q"].get("poly") or {}).get("betrag", -1) >= betrag:
            continue                    # dasselbe Spiel unter zwei Schlüsseln (alter Schlüssel neu terminiert) → der größere zählt
        seiten, herkunft = {}, "Positionen"
        for n, v in (m.get("shares") or {}).items():
            k = _seite(n, g["heim"], g["gast"])
            if k:
                seiten[k] = seiten.get(k, 0) + float(v or 0)
        if not seiten and key in pf_takt:
            # Lucas 18.10. (Aurora – Falcons $173k, „keine Aufteilung"): das alte Projekt holt die Positionen nicht
            # für jeden Markt. Dann die Käufe der letzten 4 h je Seite vom Mac-Runner (poly_fluss) — nur Ja-Käufe,
            # „gegen X" lässt sich keiner einzelnen Seite zuordnen.
            for n, werte in pf_takt[key].items():
                if str(n).startswith("gegen "):
                    continue
                k = _seite(n, g["heim"], g["gast"])
                if k:
                    seiten[k] = seiten.get(k, 0) + sum(werte or [])
            herkunft = "Käufe 4 h"
        if not seiten and key in pf_alle:
            for n, v in (pf_alle[key][0] or {}).items():
                if str(n).startswith("gegen "):
                    continue
                k = _seite(n, g["heim"], g["gast"])
                if k:
                    seiten[k] = seiten.get(k, 0) + float(v or 0)
            rw = pf_alle[key][1] or 0
            herkunft = "letzte Käufe " + (f"{rw / 1440:.0f} T" if rw >= 2880 else f"{rw / 60:.0f} h" if rw >= 60 else f"{rw:.0f} Min")
        wl = [dict(x, k=_seite(x["seite"], g["heim"], g["gast"])) for x in
              ((belegt or {}).get(key) or []) + ((belegt or {}).get(key + "-more-markets") or [])]
        if wl:
            g["wale"] = wl[:6]
        if key in pf_30 and not str(g["id"]).startswith(("pm:", "st:")):
            alt = AUCH_WOANDERS.setdefault(g["id"], {}).get("poly")
            if not alt or alt["usd"] < pf_30[key][0]:
                auf_ = pf_30[key][1]
                # 10.11. (Top-3-Regel 5): auf welche Seite (heim/gast/remis) das 30-Min-Geld ging — „gegen X" = keine Seite
                AUCH_WOANDERS[g["id"]]["poly"] = {"usd": round(pf_30[key][0]), "auf": auf_,
                                                  "k": None if str(auf_ or "").startswith("gegen ") else _seite(auf_, g["heim"], g["gast"])}
        g["q"]["poly"] = {"betrag": round(betrag), "seiten": seiten, "key": key, "seitenAus": herkunft if seiten else None,
                          "preise": {(_seite(n, g["heim"], g["gast"]) or n): p for n, p in (m.get("prices") or {}).items()},
                          # Lucas 08.11. (Polymarket-Schattenbuch): bester Ask und Liquidität bis +2 Cent je Seite
                          "ask": {(_seite(n, g["heim"], g["gast"]) or n): p for n, p in (m.get("ask") or {}).items()},
                          "tiefe": {(_seite(n, g["heim"], g["gast"]) or n): p for n, p in (m.get("tiefe") or {}).items()},
                          "buchTs": m.get("buchTs"),
                          "labels": {(_seite(n, g["heim"], g["gast"]) or n): n for n in (m.get("prices") or {})}}

    # Lucas 08.11. (Polymarket-Schattenbuch): ALLE Spiele mit Polymarket-Orderbuch für push.py — auch die, die der
    # Topf später wegkürzt. Nur Laufzeit-Datei, nicht auf der Seite.
    try:
        (BASIS / "poly_buch.json").write_text(json.dumps(
            [{"heim": g["heim"], "gast": g["gast"], "anpfiff": g.get("anpfiff"), "key": g["q"]["poly"]["key"],
              "ask": g["q"]["poly"].get("ask"), "tiefe": g["q"]["poly"].get("tiefe"), "buchTs": g["q"]["poly"].get("buchTs"),
              "labels": g["q"]["poly"].get("labels"), "seiten": g["q"]["poly"].get("seiten"),
              "seitenAus": g["q"]["poly"].get("seitenAus"), "betrag": g["q"]["poly"].get("betrag")}
             for g in spiele if (g.get("q") or {}).get("poly")], ensure_ascii=False), encoding="utf-8")
        print(f"  poly_buch: {sum(1 for g in spiele if (g.get('q') or {}).get('poly'))} Spiele, "
              f"{sum(1 for g in spiele if ((g.get('q') or {}).get('poly') or {}).get('ask'))} mit Orderbuch")
    except Exception as exc:  # noqa: BLE001
        print(f"  poly_buch: {exc}")

    # 3) Stake: große Einzelwetten der letzten Stunden (Highroller ab $1000)
    je_event = {}
    for w in (hr or {}).get("wetten") or []:
        if not isinstance(w, dict) or w.get("kombi") or not stake_ok(w):
            continue
        t = ts(w.get("ts"))
        if not t or JETZT - t > timedelta(hours=12):
            continue
        e = je_event.setdefault(w.get("eventId"), {"w": w, "summe": 0.0, "n": 0, "seiten": {}})
        e["summe"] += w.get("einsatzUsd") or 0
        e["n"] += 1
        jung = JETZT - t <= timedelta(minutes=30)
        if jung:
            e["s30"] = e.get("s30", 0.0) + (w.get("einsatzUsd") or 0)
            e["n30"] = e.get("n30", 0) + 1
        mk = str(w.get("markt") or "").lower()
        # Seite aus Sieg-Märkten, aber auch aus Handicap / Draw No Bet (Auswahl = Teamname, ggf. mit Linie)
        if mk in ("1x2", "match result", "winner", "match winner", "moneyline", "winner - twoway") \
                or ("handicap" in mk and "half" not in mk and "1st" not in mk) or "draw no bet" in mk \
                or ("winner" in mk and "set" not in mk and "game" not in mk):
            e["seiten"].setdefault(w.get("auswahl"), 0.0)
            e["seiten"][w.get("auswahl")] += w.get("einsatzUsd") or 0
            if jung:
                e.setdefault("seiten30", {}).setdefault(w.get("auswahl"), 0.0)
                e["seiten30"][w.get("auswahl")] += w.get("einsatzUsd") or 0
    for e in je_event.values():
        w = e["w"]
        h, gst = _teams(w.get("event"))
        if not h:
            continue
        g = finden(h, gst, w.get("anpfiff"))
        if not g:
            g = {"id": "st:" + str(w.get("eventId")), "heim": h, "gast": gst, "liga": w.get("liga"), "land": None,
                 "anpfiff": w.get("anpfiff"), "live": None, "top5": str(w.get("ligaSlug") or "") in {"premier-league", "la-liga", "bundesliga", "serie-a", "ligue-1", "uefa-champions-league"},
                 "sport": w.get("kat") or "Fußball", "q": {}}
            spiele.append(g)
        seiten = {}
        for n, v in e["seiten"].items():
            k = _seite(n, g["heim"], g["gast"])
            if k:
                seiten[k] = seiten.get(k, 0) + v
        g["q"]["stake"] = {"betrag": round(e["summe"]), "seiten": seiten, "wetten": e["n"]}
        if e.get("s30") and not str(g["id"]).startswith(("pm:", "st:")):
            s30k = {}
            for n, v in (e.get("seiten30") or {}).items():
                k = _seite(n, g["heim"], g["gast"])
                if k:
                    s30k[k] = s30k.get(k, 0) + v
            # 10.11. (Top-3-Regel 5): Seite des 30-Min-Geldes (nur aus Sieg-/Handicap-Märkten; Tore-Geld hat keine Seite)
            AUCH_WOANDERS.setdefault(g["id"], {})["stake"] = {"usd": round(e["s30"]), "n": e["n30"],
                                                              "k": max(s30k, key=s30k.get) if s30k else None}

    # Lucas 18.10. (Kasimpasa 55' 2:1 mit „79 % auf Kasimpasa" oben in der Liste): live verstopfen führende Teams die
    # Liste, und ihr Betfair-Anteil wächst nur, weil nach dem Tor zu Mini-Quoten auf den Führenden gehandelt wird.
    # Darum: führt ein Team und steht es bei Betfair unter 1,40 → raus. Alle anderen laufenden Spiele kommen zugeklappt
    # ans Ende, mit der Aufteilung ZUM ANPFIFF (vor dem Spiel gespeichert), nicht der verzerrten Live-Summe.
    split_datei = BASIS / ".bf" / "anpfiff_split.json"
    try:
        splits = json.loads(split_datei.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        splits = {}
    out = []
    for g in spiele:
        k = ts(g["anpfiff"])
        # entschiedene Spiele dienten nur als Anker, damit Polymarket/Stake sich nicht als eigenes Spiel listen
        if g.get("entschieden") or not k or not (-2.2 <= (k - JETZT).total_seconds() / 3600 <= 30):
            continue
        bfq = (g["q"].get("betfair") or {})
        if k > JETZT:
            if bfq.get("seiten"):
                splits[g["id"]] = {"seiten": {kk: round(v) for kk, v in bfq["seiten"].items()}, "t": iso(JETZT)}
        else:
            g["laeuft"] = True
            st = (g.get("live") or {}).get("stand") or []
            if len(st) == 2 and None not in st and st[0] != st[1]:
                fuehrt = "heim" if st[0] > st[1] else "gast"
                qf = (bfq.get("quoten") or {}).get(fuehrt)
                if isinstance(qf, (int, float)) and qf < 1.40:
                    continue
            sp0 = (splits.get(g["id"]) or {}).get("seiten")
            if sp0:
                g["splitAnpfiff"] = sp0
        ges, seiten_ges, meinungen = 0.0, {}, {}
        for qn, q in g["q"].items():
            f = 1.0 if qn == "betfair" else USD_EUR
            ges += (q.get("betrag") or 0) * f
            summe = sum(q["seiten"].values())
            for sk, v in q["seiten"].items():
                seiten_ges[sk] = seiten_ges.get(sk, 0) + v * f
            if summe > 0:
                top, tv = max(q["seiten"].items(), key=lambda kv: kv[1])
                q["auf"], q["anteil"] = top, round(100 * tv / summe)
                q["seitenBetrag"] = round(tv)
                if tv / summe >= 0.55 and tv * f >= 5000:      # eine $1000-Wette ist keine Meinung der Quelle
                    meinungen[qn] = top
                    q["meinung"] = True
        if ges < 1000:
            continue
        einig = None
        if len(meinungen) >= 2:
            einig = len(set(meinungen.values())) == 1
        out.append({**g, "gesamt": round(ges), "seiten": {k: round(v) for k, v in seiten_ges.items()},
                    "einig": einig, "meinungen": meinungen})
    try:
        grenze = JETZT - timedelta(hours=6)
        splits = {kk: v for kk, v in splits.items() if (ts(v.get("t")) or JETZT) >= grenze - timedelta(hours=24)}
        split_datei.parent.mkdir(parents=True, exist_ok=True)
        split_datei.write_text(json.dumps(splits), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    # Lucas 18.10. (Lyon, Real, Juve liefen und fehlten): erst sortieren, dann kappen schnitt die laufenden Spiele
    # ab, sobald 40 Spiele vor Anpfiff da waren. Jetzt getrennte Deckel.
    vor = sorted((x for x in out if not x.get("laeuft")), key=lambda x: -x["gesamt"])[:40]
    lauf = sorted((x for x in out if x.get("laeuft")), key=lambda x: -x["gesamt"])[:25]
    return vor + lauf


# ── Liga-Bilanz: trägt hohes Geld in dieser Liga? (Lucas 19.10.: „Bilanz nach Liga wäre ein Traum … es gibt Ligen,
# wo man dem Geld einfacher nachspielt als in anderen, ändert sich auch immer wieder") ─────────────────────────────
# Quelle: das Tracking des alten Projekts (betfair_track_results.json, rollierend 40.000 Märkte ≈ 1 Monat): je Spiel und
# Markt die Seite, auf der das Geld lag, ihr Anteil, das Marktvolumen, die Quote beim Signal und das Ergebnis. Damit die
# Historie über das rollierende Fenster hinaus wächst (und das alte Projekt einmal abgeschaltet werden kann), führt der
# Geldradar ein eigenes Buch (.bf/liga_buch.json, Actions-Cache) und hängt jeden Lauf die neuen Zeilen an.
LIGA_BUCH = BASIS / ".bf" / "liga_buch.json"
LIGA_FENSTER_TAGE = 90           # ändert sich immer wieder → nur die letzten 90 Tage zählen
LIGA_HOCH_ANTEIL = 65            # „Geld liegt klar auf einer Seite"
LIGA_MIN_N = 25


def _liga_stat(rr):
    rr = [r for r in rr if isinstance(r.get("q"), (int, float)) and r["q"] > 1 and r.get("w") is not None]
    n = len(rr)
    if not n:
        return None
    gew = [((r["q"] - 1) * 0.98 if r["w"] else -1.0) for r in rr]
    m = sum(gew) / n
    sd = (sum((g - m) ** 2 for g in gew) / (n - 1)) ** 0.5 if n > 1 else 0
    return {"n": n, "treffer": round(100 * sum(1 for r in rr if r["w"]) / n), "erwartet": round(100 * sum(1 / r["q"] for r in rr) / n),
            # z = 2,33 statt 1,645: es werden ~140 Ligen gleichzeitig geprüft — bei 90 % wären allein durch Zufall
            # ~7 Ligen „trägt"; bei 99 % ist es gut eine.
            "roi": round(100 * m, 1), "ug": round(100 * (m - 2.33 * sd / n ** 0.5), 1), "og": round(100 * (m + 2.33 * sd / n ** 0.5), 1)}


def liga_bilanz():
    try:
        buch = json.loads(LIGA_BUCH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        buch = {}
    # 20.10.: die Zeilen schreibt ergebnisse.py aus dem eigenen Betfair-Abruf — kein Track-Record des alten Projekts mehr
    grenze = (JETZT - timedelta(days=LIGA_FENSTER_TAGE)).strftime("%Y-%m-%d")
    buch = {k: v for k, v in buch.items() if (v.get("t") or "9") >= grenze}
    try:
        LIGA_BUCH.parent.mkdir(parents=True, exist_ok=True)
        LIGA_BUCH.write_text(json.dumps(buch, separators=(",", ":")), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    je = {}
    for v in buch.values():
        je.setdefault(v["l"], []).append(v)
    out = {}
    for liga, rr in je.items():
        vols = sorted(r.get("v") or 0 for r in rr)
        if len(vols) < 40:
            continue
        p67 = vols[int(len(vols) * 0.67)]          # „hohe Einsätze" = oberes Drittel DIESER Liga
        hoch = [r for r in rr if (r.get("v") or 0) >= p67 and (r.get("a") or 0) >= LIGA_HOCH_ANTEIL]
        sh = _liga_stat(hoch)
        if not sh or sh["n"] < LIGA_MIN_N:
            continue
        urteil = "trägt" if sh["ug"] > 0 else "trägt nicht" if sh["og"] < 0 else "offen"
        out[liga] = {"hoch": sh, "alle": _liga_stat(rr), "urteil": urteil, "abEur": round(p67),
                     "seit": min(r["t"] for r in rr if r.get("t"))}
    lw2 = _lw2_gewichte(out)
    try:
        lw2["pruefung"] = _lw2_pruefung(out, buch)
    except Exception as exc:  # noqa: BLE001
        FEHLER.append(f"LW2-Prüfung: {str(exc)[:100]}")
    return {"stand": iso(JETZT), "fensterTage": LIGA_FENSTER_TAGE, "ligen": out,
            "zeilen": len(buch), "lw2": lw2}


# ── Liga-Regel LW2 (Lucas 18.11. „ja, kannst du so bauen"): Liga-Gewicht als geschrumpfte Schätzung ──────────────────
# Vorab festgelegt (18.11.2026, 10:50 Wien):
# * Je Liga der ROI bei hohem, einseitigem Geld (wie oben), mit Standardfehler. Wie stark sich Ligen WIRKLICH
#   unterscheiden (τ), wird jedes Mal aus allen Ligen geschätzt (DerSimonian-Laird). Liga-Gewicht = Ø aller Ligen
#   + τ²/(τ² + SE²) × (Liga-ROI − Ø): viele Märkte → zählt fast das eigene Ergebnis, wenige → nahe am Schnitt.
#   18.11.: τ ≈ 7 pp bei typisch ±13 pp je Liga — die Unterschiede sind echt, je Liga aber noch unscharf.
# * Stufe +1 („Liga gut"), wenn das Gewicht ≥ 3 pp über dem Schnitt liegt, −1 („Liga schwach") bei ≥ 3 pp darunter.
#   Top 3: +1 Punkt bzw. −1 (statt bisher nur bei 99 %-Urteil). Topf: Liga-Teil = (Gewicht − Ø) / 3, höchstens ±4.
# * Prüfung: jeden Montag werden die Stufen eingefroren; die Woche danach wird je Stufe abgerechnet (hohes Geld,
#   ab der Grenze der Liga, ≥ 65 % einseitig). Urteil nach 6 Wochen: trägt, wenn „gut" in ≥ 4 Wochen besser lief als
#   „schwach" UND über alle Wochen zusammen. Sonst zurück auf das alte 99 %-Urteil.
LW2_STUFE_PP = 3.0
LW2_DATEI = BASIS / ".push_state" / "lw2.json"
LW2_WOCHEN = 6


def _lw2_gewichte(ligen):
    import math
    st = {}
    for l, v in ligen.items():
        h = v.get("hoch") or {}
        se = (h.get("og", 0) - h.get("ug", 0)) / (2 * 2.33)
        if se > 0 and isinstance(h.get("roi"), (int, float)):
            st[l] = (h["roi"], se)
    if len(st) < 10:
        return {"ligen": len(st)}
    w = {l: 1 / s ** 2 for l, (_, s) in st.items()}
    sw = sum(w.values())
    m = sum(w[l] * st[l][0] for l in st) / sw
    q = sum(w[l] * (st[l][0] - m) ** 2 for l in st)
    c = sw - sum(x * x for x in w.values()) / sw
    tau2 = max(0.0, (q - (len(st) - 1)) / c)
    gut = schwach = 0
    for l, (roi, se) in st.items():
        g = m + tau2 / (tau2 + se * se) * (roi - m)
        d = g - m
        stufe = 1 if d >= LW2_STUFE_PP else -1 if d <= -LW2_STUFE_PP else 0
        ligen[l].update(gewicht=round(g, 1), lw2Diff=round(d, 1), stufe=stufe)
        gut += stufe == 1
        schwach += stufe == -1
    return {"mittel": round(m, 1), "tau": round(math.sqrt(tau2), 1), "ligen": len(st), "gut": gut, "schwach": schwach,
            "streuungZufall": round(sorted(s for _, s in st.values())[len(st) // 2], 1)}


def _lw2_pruefung(ligen, buch):
    """Montags Stufen einfrieren, die Woche danach je Stufe abrechnen. → Zusammenfassung für die Seite."""
    try:
        p = json.loads(LW2_DATEI.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        p = {}
    stich = p.setdefault("stichtage", [])
    montag = (JETZT - timedelta(days=JETZT.weekday())).replace(hour=6, minute=0, second=0, microsecond=0)
    if JETZT >= montag and (not stich or stich[-1]["ab"] < montag.strftime("%Y-%m-%d")):
        # erster Stichtag sofort (18.11.), danach jeden Montag
        ab = (JETZT if not stich else montag).strftime("%Y-%m-%d")
        stich.append({"ab": ab, "stufen": {l: [v.get("stufe", 0), v.get("abEur") or 0] for l, v in ligen.items() if "stufe" in v}})
    heute = JETZT.strftime("%Y-%m-%d")
    for i_, s in enumerate(stich):
        if s.get("ergebnis"):
            continue
        bis = min((datetime.fromisoformat(s["ab"]) + timedelta(days=7)).strftime("%Y-%m-%d"),
                  stich[i_ + 1]["ab"] if i_ + 1 < len(stich) else "9999")
        if heute < bis:
            continue
        je = {1: [], 0: [], -1: []}
        for r in buch.values():
            t = r.get("t") or ""
            x = s["stufen"].get(r.get("l"))
            if not x or not (s["ab"] <= t < bis) or (r.get("v") or 0) < x[1] or (r.get("a") or 0) < LIGA_HOCH_ANTEIL:
                continue
            if isinstance(r.get("q"), (int, float)) and r["q"] > 1 and r.get("w") is not None:
                je[x[0]].append((r["q"] - 1) * 0.98 if r["w"] else -1.0)
        s["ergebnis"] = {str(k): [len(v), round(100 * sum(v) / len(v), 1) if v else None] for k, v in je.items()}
    LW2_DATEI.parent.mkdir(parents=True, exist_ok=True)
    LW2_DATEI.write_text(json.dumps(p, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    fertig = [s for s in stich if s.get("ergebnis")]
    wochen = [{"ab": s["ab"], "gut": s["ergebnis"]["1"], "mitte": s["ergebnis"]["0"], "schwach": s["ergebnis"]["-1"]} for s in fertig]
    besser = sum(1 for w in wochen if w["gut"][1] is not None and w["schwach"][1] is not None and w["gut"][1] > w["schwach"][1])
    urteil = None
    if len(wochen) >= LW2_WOCHEN:
        sg = sum(w["gut"][0] * (w["gut"][1] or 0) for w in wochen) / max(1, sum(w["gut"][0] for w in wochen))
        ss = sum(w["schwach"][0] * (w["schwach"][1] or 0) for w in wochen) / max(1, sum(w["schwach"][0] for w in wochen))
        urteil = "trägt" if besser >= 4 and sg > ss else "trägt nicht"
    return {"wochen": wochen, "besser": besser, "ziel": LW2_WOCHEN, "urteil": urteil, "seit": stich[0]["ab"] if stich else None}


def endstaende():
    """Lucas 24.10. (Merkliste): Endstände der letzten 3 Tage je Betfair-Spiel — damit gemerkte Spiele nach Abpfiff
    abgerechnet werden, auch wenn sie längst aus dem Abruf gefallen sind."""
    try:
        erg = json.loads((Path(__file__).resolve().parent / ".bf" / "ergebnisse.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for mid, e in erg.items():
        k = ts(e.get("ko"))
        if e.get("ft") and k and JETZT - k < timedelta(days=3):
            out[mid] = {"ft": e["ft"], "ht": e.get("ht")}
    return out


def liga_wert(liga_bilanz_cache=None):
    """Lucas 07.11. 16:47 (Liga-Wertung als Chip in den Top 3): {Liga roh: [Punkte, n, Treffer %, erwartet %]} —
    dieselbe Rechnung wie Public-Regel 13 (public.liga_werte), je Ligenname, wie er in den Daten steht."""
    try:
        import bilanz as _b
        import public as _p
        buch = _b.laden()
        lb = (liga_bilanz_cache or {}).get("ligen")
        w = _p.liga_werte(buch, lb)
        out = {}
        for l in [x.get("liga") for x in buch] + list(lb or {}):
            if l and l not in out:
                v = w.get(_p.liga_schluessel(l))
                if v:
                    out[l] = list(v)
        return out
    except Exception as exc:  # noqa: BLE001
        print("liga_wert:", exc)
        return {}


def top3_erst():
    """10.11. (Lucas: „merk ich das, ob da was gewechselt hat?"): je Spiel der ERSTE Top-3-Einzug der letzten 48 h —
    {id: [Markt, Auswahl, Quote, Zeit]}. Steht das Spiel jetzt mit einer anderen Auswahl oben, zeigt die Seite den Wechsel."""
    try:
        buch = json.loads((BASIS / ".push_state" / "buch.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    grenze = JETZT - timedelta(hours=48)
    out = {}
    for b in sorted((x for x in buch if x.get("art") in ("top3", "top3p")), key=lambda x: x.get("ts") or ""):
        t = ts(b.get("ts"))
        if t and t >= grenze and str(b.get("id")) not in out:
            out[str(b.get("id"))] = [b.get("markt"), b.get("auswahl"), b.get("quote"), b.get("ts")]
    return out


def push_bilanz():
    """Auswertung der neuen Pushes (bilanz.py schreibt sie nach jedem Push-Lauf in den Cache)."""
    try:
        return json.loads((Path(__file__).resolve().parent / ".push_state" / "bilanz.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def poly_fluss():
    """Käufe je Seite auf Polymarket (vom Mac-Runner, .pf/poly_fluss.json). Älter als 40 Min → weglassen."""
    p = Path(__file__).resolve().parent / ".pf" / "poly_fluss.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"stand": None, "maerkte": []}
    t = ts(d.get("generatedAt"))
    if not t or (JETZT - t).total_seconds() > 40 * 60:
        return {"stand": d.get("generatedAt"), "maerkte": [], "alt": True}
    rows = []
    for m in d.get("maerkte") or []:
        pre = str(m.get("key") or "").split("-")[0]
        m["top5"] = pre in POLY_TOP5
        rows.append(m)
    return {"stand": d.get("generatedAt"), "maerkte": rows, "seitenAlle": d.get("seitenAlle") or {}}


def betfair_pushes(buch):
    """Lucas 20.10.: die EIGENEN Betfair-Pushes aus dem Push-Buch (bilanz.py) — nicht mehr die des alten Projekts."""
    buch = buch or {}
    status = {"abgerechnet": None, "offen": "pending"}
    out = []
    for e in buch.get("letzte") or []:
        t = ts(e.get("ts"))
        if e.get("art") in ("pserie", "wal", "cm1") or not t or t < JETZT - timedelta(hours=36):   # Polymarket-Pushes stehen unter „Wale"/Poly
            continue
        out.append({"zeit": e.get("ts"), "art": ((buch.get("arten") or {}).get(e.get("art")) or {}).get("name"),
                    "markt": e.get("markt"), "liga": e.get("liga"), "heim": e.get("heim"), "gast": e.get("gast"),
                    "auf": e.get("auswahl"), "quote": e.get("quote"), "eur": round(e.get("eur") or 0), "anteil": None,
                    "status": ("won" if e.get("win") else "lost") if e.get("status") == "abgerechnet" else status.get(e.get("status"))})
    v = [z for z in buch.get("verlauf") or [] if isinstance(z, list) and len(z) == 3 and z[1] is not None]
    n = len(v)
    bil = {"n": n, "treffer": round(100 * sum(1 for z in v if z[2]) / n, 1) if n else None,
           "roi": round(100 * sum(z[1] for z in v) / n, 1) if n else None, "roiUg": None, "schlaegtPinnacle": None}
    return {"liste": out[:40], "bilanz": bil}


def eigene_wal_pushes(buch):
    """Wal-Pushes aus dem eigenen Push-Buch im Format, das poly() und verlauf() kennen (Lucas 20.10.)."""
    led = []
    for z in (buch or {}).get("walVerlauf") or []:
        led.append({"sentAt": z.get("zeit") + "+00:00" if len(str(z.get("zeit") or "")) == 16 else z.get("zeit"),
                    "key": z.get("key"), "side": z.get("seite"), "usd": z.get("usd"), "pushPrice": z.get("preis"),
                    "cat": z.get("sport"), "wallet": z.get("wallet"), "titel": z.get("titel"),
                    "result": {"won": "win", "lost": "loss"}.get(z.get("status")), "status": z.get("status")})
    je = {}
    for z in led:
        if z["result"] and isinstance(z.get("pushPrice"), (int, float)) and 0 < z["pushPrice"] < 1:
            b = je.setdefault(z.get("cat") or "Fußball", [])
            b.append(1 / z["pushPrice"] - 1 if z["result"] == "win" else -1.0)
    rec = {"byCat": {sp: {"n": len(r), "hit": sum(1 for x in r if x > 0) / len(r), "roi": sum(r) / len(r), "roiUg": None,
                          "belegt": None} for sp, r in je.items()},
           "startAb": min((z["sentAt"] for z in led), default=None)}
    return led, rec


# ── Verlauf aller Push-Ergebnisse (Lucas 19.10.: „zu wenig mit Graphen gearbeitet") ─────────────────────────
VERLAUF_TAGE = 60



# Lucas 30.10. („Statistiken optisch besser aufbereiten — nach Kategorien, Woche, Monat einstellbar"): jede gepushte
# Wette seit Start als kompakte Zeile in einer EIGENEN Datei (site/statistik.json, wird erst beim Öffnen der Seite geladen).
STAKE_EIGEN_AB = "2026-11-10T09:30"   # ab hier bucht push.py jeden gesendeten Stake-Push selbst (Stake-Check 10.11.)
WDH_ARTEN = ("serie", "zufluss", "live", "pinn", "pserie", "wal", "stake")
# 16.11. (Lucas: „Top 3 und Druckradar messen wir, aber in der Statistik ist es nicht drin"): beide als eigene Arten.
# Top 3 wie top3.py misst: je Spiel nur der ERSTE Einzug („top3"/„top3p"), jede spätere Auswahl im selben Spiel „top3Spaeter".
UEBERSICHT_ARTEN = ("top3", "top3p", "druck", "druckVorn")
STAT_ARTEN = {*UEBERSICHT_ARTEN, "pinnLinie", "ki", "kiModell", "kiTop3", "stake", "serie", "zufluss", "live", "ht", "fix", "pinn", "pserie", "wal", "serieFuehrt", "serieRuhig", "pserieGegen", "htGegen05", "htGegen15", "liveFuehrt", "stakeFuehrt", "stakeTeam", "layGegen", "walKlein", "ueberQuote", "ueberQuoteTop3", "bfDrop", "bfUhr", "pk1", "pk1Streit", "cm1", "mm1", "mm1Bein", "mm2", "mmA", "mmABein", "mmA2"}


# 09.11. (Lucas: Barcelona „offen"): von Hand nachgeschlagene Ergebnisse für Public-Signale, die vor dem eigenen
# Abrechnen (Ergebnisbuch / Polymarket) lagen und die Nachbarzeile falsch zuordnete. Quelle jeweils dabei.
PUBLIC_ERGEBNIS = {
    ("2026-11-03", "furiaesports"): True,     # FURIA schlägt NaVi, IEM Beijing 2026 (rg.org, esports.ru)
    ("2026-11-04", "natusvincere"): False,    # NaVi schlägt Falcons → Public „Team Falcons" verloren (rg.org)
    ("2026-11-10", "innercircle"): True,      # 11.11. Lucas: Inner Circle x Insanity schlägt HULIGANI 2:0 (hawk.live)
}


def _pub_hand(tabelle, tag, spiel_nz):
    """11.11.: Handeinträge (Datum, Teil des Spielnamens) — der Name darf irgendwo im Spieltitel stehen."""
    for (d, k), v in tabelle.items() if isinstance(tabelle, dict) else ((x, True) for x in tabelle):
        if d == tag and k and k in spiel_nz:
            return v
    return None


def _public_ergebnisse():
    """Ergebnisbuch (.bf/ergebnisse.json): [(t0-Team Heim, Gast, Anpfiff, ft)] nur fertige Spiele."""
    try:
        erg = json.loads((BASIS / ".bf" / "ergebnisse.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    import bilanz as _b
    return [(_b._team(e.get("h")), _b._team(e.get("a")), ts(e.get("ko")), e.get("ft"), e.get("h"), e.get("a"))
            for e in erg.values() if isinstance(e, dict) and e.get("ft") and ts(e.get("ko"))]


def _public_gewonnen(p, t0, erg):
    """True/False für ein Public-Signal aus dem eigenen Ergebnis, None wenn nicht sicher zuordenbar."""
    import bilanz as _b
    teile = re.split(r"\s+[–-]\s+", str(p.get("spiel") or ""), maxsplit=1)
    if len(teile) != 2 or not t0:
        return None
    th, tg = _b._team(teile[0]), _b._team(teile[1])
    if not th or not tg:
        return None
    treffer = [e for e in erg if e[0] == th and e[1] == tg and timedelta(hours=-6) <= (e[2] - t0) <= timedelta(hours=36)]
    if not treffer and p.get("live"):
        treffer = [e for e in erg if e[0] == th and e[1] == tg and timedelta(hours=-3) <= (e[2] - t0) <= timedelta(hours=1)]
    # 17.11.2026 (Rundum-Check): „Man Utd – Roma" fand auch „Man Utd U19 – Roma U19" (gleicher Tag, erstes Wort gleich)
    # → zwei Treffer → offen. Nachwuchs/Frauen nur mit Nachwuchs/Frauen paaren.
    if len(treffer) > 1:
        def _jung(n):
            return bool(re.search(r"\b(u\s?1\d|u\s?2[0-3]|ii|b|women|w|frauen|reserves?)\b", str(n or "").lower()))
        j = _jung(p.get("spiel"))
        treffer = [e for e in treffer if _jung(f"{e[4]} {e[5]}") == j]
    if len(treffer) != 1:
        return None
    _, _, _, ft, heim, gast = treffer[0]
    a = str(p.get("auswahl") or "")
    m = re.match(r"^(Over|Under|Über|Unter)\s+(\d+)[.,]5", a, re.I)
    mg = re.match(r"^(Over|Under|Über|Unter)\s+(\d+)(?:\s+Goals)?$", a, re.I)
    if mg and isinstance(ft, (list, tuple)) and len(ft) == 2:
        # ganze Linie (z. B. „Under 3"): genau getroffen = Einsatz zurück → offen lassen statt falsch werten
        tore, lin = sum(ft), int(mg.group(2))
        if tore == lin:
            return None
        return (tore > lin) if mg.group(1).lower() in ("over", "über") else (tore < lin)
    if m:
        markt, a = f"Over/Under {m.group(2)}.5 Goals", ("Over" if m.group(1).lower() in ("over", "über") else "Under") + f" {m.group(2)}.5 Goals"
    elif re.match(r"^(remis|unentschieden|draw|the draw|x)$", a, re.I):
        markt, a = "Match Odds", "The Draw"
    else:
        markt = "Match Odds"
        if _b._team(a) == th:
            a = heim
        elif _b._team(a) == tg:
            a = gast
        else:
            # „AFC Ajax" → erstes Wort „afc"; dann reicht, dass genau ein Teamschlüssel im Auswahlnamen steht
            an = re.sub(r"[^a-z0-9 ]", " ", str(a).lower()).split()
            if (th in an) != (tg in an):
                a = heim if th in an else gast
            else:
                return None
    if p.get("markt") and not str(p.get("markt")).startswith(("Match", "Sieg")):
        return None                     # Handicap, 1. Halbzeit … nicht aus dem Endstand
    return _b.gewonnen(markt, a, heim, gast, ft, None)


# 15.11. (Lucas: „die Spiele sollen nicht auf Polymarket gewesen sein, das ist unmöglich" — Segunda-Spiele, Brasilien …):
# vom 12.11. ~11:30 bis 14.11. 20:20 UTC lieferte der Polymarket-Abruf 0 Märkte (Gamma verlangte gzip, HTTP 406).
# Ein Public-Signal aus dieser Zeit war nicht „nicht auf Polymarket", wir hatten schlicht keine Daten.
POLY_AUSFAELLE = (("2026-11-12T11:30", "2026-11-14T20:20"),)


def _poly_ausfall(pp, t0):
    if not isinstance(pp, dict) or pp.get("quote") or pp.get("fehlt") not in ("Spiel nicht auf Polymarket", "kein Orderbuch"):
        return pp
    t = t0.strftime("%Y-%m-%dT%H:%M")
    if any(v <= t <= b for v, b in POLY_AUSFAELLE):
        return {**pp, "fehlt": "Polymarket-Ausfall (keine Daten)"}
    return pp


def _pub_zeile(p, zeilen, buch, erg_pub, erg_poly, nz):
    for _ in (0,):
        t0 = ts(p.get("zeit") + "+00:00") if len(str(p.get("zeit") or "")) == 16 else ts(p.get("zeit"))
        if not t0:
            continue
        # Public-Regel 4 (02.11.): gesendet wird zeitversetzt aus dem Topf → derselbe Tipp (Spiel + Auswahl) aus irgendeiner
        # Art bis 36 h davor; Rendite neu zur gesendeten Quote. Nicht gesendete Kandidaten als eigene Art „publicNein".
        # ab R4 aus dem Topf (zeitversetzt). 09.11. (Lucas: Barcelona stand „offen", obwohl gewonnen): der Vergleich lief
        # als Text — „R17" < „R4" —, seit Regel 10 fiel jedes Signal in den alten Zweig (nur dieselbe Art, ±20 Min)
        r4 = int((re.search(r"\d+", str(p.get("regel") or "")) or [0])[0] or 0) >= 4
        art_p = "publicNein" if p.get("gesendet") is False else "public"
        if p.get("sperre"):                 # 14.11. Public-Regel 24/25: eigene Zeile, damit sich die Sperre messen lässt
            sp_ = str(p.get("sperre"))
            art_p = "publicGegenwind" if "R25" in sp_ else "publicQuote" if ("R27" in sp_ or "R28" in sp_) else "publicNL"
        best, bd = None, None
        for z in zeilen:
            if z[1] in ("public", "publicNein", "publicGegenwind", "publicNL", "publicQuote") or (not r4 and z[1] != p.get("art")):
                continue
            dt = abs((ts(z[0] + "+00:00") - t0).total_seconds())
            if dt > (36 * 3600 if r4 else 20 * 60):
                continue
            titel_ok = nz(z[9])[:10] and nz(z[9])[:10] == nz(p.get("spiel"))[:10]
            ausw_ok = nz(z[10]) and nz(p.get("auswahl")) and (nz(z[10]) in nz(p.get("auswahl")) or nz(p.get("auswahl")) in nz(z[10]))
            if str(z[10] or "").lower().startswith("gegen ") != str(p.get("auswahl") or "").lower().startswith("gegen "):
                ausw_ok = False                 # „gegen Barcelona" ist nicht „Barcelona"
            # 09.11.: dieselbe Auswahl schlägt bloß dasselbe Spiel (ein Stake-Paket im selben Spiel ist ein anderer Tipp)
            if titel_ok and not ausw_ok and nz(z[10]) and nz(p.get("auswahl")):
                continue                        # selbes Spiel, andere Auswahl (z. B. Atlético statt Barcelona)
            rang = (0 if ausw_ok else 1, dt)
            if (titel_ok or ausw_ok) and (bd is None or rang < bd):
                best, bd = z, rang
        # 09.11. (Lucas: Barcelona „offen", FURIA/Falcons falsch): Fußball-Signale direkt am eigenen Ergebnis abrechnen
        # (Ergebnisbuch .bf/ergebnisse.json, Teamnamen + Anpfiff) — die Nachbarzeile ist nur noch der Notnagel.
        w_eigen = _public_gewonnen(p, t0, erg_pub)
        if w_eigen is None:
            w_eigen = erg_poly.get((str(p.get("zeit"))[:16], nz(p.get("spiel"))[:12]))
        if w_eigen is None:
            w_eigen = _pub_hand(PUBLIC_ERGEBNIS, t0.strftime("%Y-%m-%d"), nz(p.get("spiel")) + nz(p.get("auswahl")))
        # 17.11.2026 (Rundum-Check): die Public-Signale der Regeln 1–3 (31.10.–02.11.) standen zu einem Drittel „offen",
        # weil das eigene Ergebnis nur ab Regel 4 galt. Das eigene Ergebnis gilt jetzt immer, die Nachbarzeile bleibt Notnagel.
        if w_eigen is not None:
            if not best:
                best = [t0.strftime("%Y-%m-%dT%H:%M"), "x", None, None, p.get("quote"), None, p.get("liga") or "", 0,
                        1 if p.get("live") else 0, str(p.get("spiel") or "")[:60], str(p.get("auswahl") or "")[:40], None]
            best = list(best)
            best[2], best[3] = (1.0 if w_eigen else -1.0), int(w_eigen)
        if best and (r4 or w_eigen is not None):
            q_p = p.get("quote") if isinstance(p.get("quote"), (int, float)) else best[4]
            w_ = best[3]
            # 03.11. (Lucas: HellNight hat gewonnen, Stake hat die Wetten annulliert): Rendite 0 = void → bleibt 0, kein Verlust
            void = best[2] == 0 and w_ != 1
            # 05.11. (Lucas: Sparta – Bournemouth kam mit „45' · 1:0", da stand es schon 1:1 — „muss in der Statistik richtig
            # gewertet werden"): Public-Signale, die mit überholtem Spielstand rausgingen, zählen als void
            if _pub_hand(PUBLIC_VOID, t0.strftime("%Y-%m-%d"), nz(p.get("spiel"))):
                void, w_ = True, None
            r_ = 0.0 if void else (round(q_p - 1, 3) if w_ == 1 else -1.0) if w_ is not None and q_p else None
            zeilen.append([t0.strftime("%Y-%m-%dT%H:%M"), art_p, r_, w_,
                           q_p, None, best[6], best[7], 1 if p.get("live") else 0, best[9],
                           str(p.get("auswahl") or "")[:40] or best[10], best[11] if len(best) > 11 else None])
            # Lucas 08.11.: Polymarket-Schattenbuch — derselbe Tipp zum Polymarket-Preis im Moment des Posts.
            # „publicPoly": zur Poly-Quote abgerechnet, usd = Liquidität bis +2 Cent, faktor = gepostete Quote.
            # „publicPolyNein": gab es auf Polymarket nicht — Grund in Spalte „lage", Ergebnis zur geposteten Quote.
            pp = _poly_ausfall(p.get("poly"), t0) if art_p == "public" else None
            if isinstance(pp, dict):
                kopf_ = [t0.strftime("%Y-%m-%dT%H:%M")]
                rest_ = [best[6], best[7], 1 if p.get("live") else 0, best[9], best[10]]
                if pp.get("quote"):
                    qpm = float(pp["quote"])
                    r_pm = 0.0 if void else (round(qpm - 1, 3) if w_ == 1 else -1.0) if w_ is not None else None
                    zeilen.append(kopf_ + ["publicPoly", r_pm, w_, qpm, None] + rest_ + [pp.get("seite"), pp.get("tiefe"), q_p])
                else:
                    zeilen.append(kopf_ + ["publicPolyNein", r_, w_, q_p, None] + rest_ + [pp.get("fehlt"), None, q_p])
        elif best:
            zeilen.append([best[0], art_p, *best[2:]])
        else:
            # 12.11. (Lucas: Butterfly und GZG standen „offen"): die Void-Liste galt nur für Zeilen mit Ergebnis — ein im Buch
            # annullierter Poly-Push hat aber nie eins. Void = Rendite 0 ohne Treffer, wie oben.
            r_v = 0.0 if _pub_hand(PUBLIC_VOID, t0.strftime("%Y-%m-%d"), nz(p.get("spiel"))) else None
            zeilen.append([t0.strftime("%Y-%m-%dT%H:%M"), art_p, r_v, None, p.get("quote"), None, p.get("liga") or "", 0,
                           1 if p.get("live") else 0, str(p.get("spiel") or "")[:60], str(p.get("auswahl") or "")[:40], None])
            pp = _poly_ausfall(p.get("poly"), t0) if art_p == "public" else None
            if isinstance(pp, dict):
                zeilen.append([t0.strftime("%Y-%m-%dT%H:%M"), "publicPoly" if pp.get("quote") else "publicPolyNein", r_v, None,
                               pp.get("quote") or p.get("quote"), None, p.get("liga") or "", 0, 1 if p.get("live") else 0,
                               str(p.get("spiel") or "")[:60], str(p.get("auswahl") or "")[:40],
                               pp.get("seite") if pp.get("quote") else pp.get("fehlt"), pp.get("tiefe"), p.get("quote")])


SPEICHER_GRENZEN = {"datei": 23e6, "gedaechtnis": 150e6, "statistik": 4e6}   # Sicherung nimmt Dateien < 25 MB mit
# betfair_history.json ist größer, baut sich aber in 72 h selbst neu auf (fällt bewusst aus der Sicherung);
# .st/stake_bet_ledger.json hält rollierend 20.000 Wetten (~22 MB) — knapp unter der Grenze, darum 23 MB als Warnschwelle.
SPEICHER_OHNE = {"betfair_history.json"}


def speicher_check():
    """Größen im Blick: Gedächtnis-Ordner, größte Datei, Statistik-Datei. Über einer Grenze → Hinweis in FEHLER
    (steht oben auf der Seite und im Wochencheck), lange bevor die Tagessicherung (25 MB je Datei) oder der Cache kippt."""
    aus, gross = {}, []
    gesamt = 0
    for d in (".bf", ".pin", ".push_state", ".pm", ".pf", ".st", ".rp"):
        n = 0
        for f in (BASIS / d).rglob("*") if (BASIS / d).exists() else []:
            if f.is_file():
                g = f.stat().st_size
                n += g
                if g > SPEICHER_GRENZEN["datei"] and f.name not in SPEICHER_OHNE:
                    gross.append(f"{d}/{f.name} {g / 1e6:.0f} MB")
        aus[d] = round(n / 1e6, 1)
        gesamt += n
    st = BASIS / "site" / "statistik.json"
    aus["statistikMB"] = round(st.stat().st_size / 1e6, 2) if st.exists() else None
    aus["gesamtMB"] = round(gesamt / 1e6, 1)
    if gross:
        FEHLER.append("Speicher: große Datei(en) — " + ", ".join(gross) + " (Tagessicherung nimmt nur < 25 MB)")
    if gesamt > SPEICHER_GRENZEN["gedaechtnis"]:
        FEHLER.append(f"Speicher: Gedächtnis {gesamt / 1e6:.0f} MB — aufräumen/verdichten")
    return aus


def _frueh_bewegt():
    """18.11. Regel FB1: Pinnacle zieht ≥ 3 pp, mindestens 24 h vor Anpfiff — Liste + stille Messung (fruehbewegt.py)."""
    try:
        import fruehbewegt
        r = fruehbewegt.lauf(BASIS / ".pin", JETZT)
        print(f"  früh bewegt: {r['n']} Spiele · FB1 {r['mess'].get('erkannt')} erkannt, {r['mess'].get('abgerechnet')} abgerechnet")
        return r
    except Exception as exc:  # noqa: BLE001
        FEHLER.append(f"Früh bewegt: {str(exc)[:120]}")
        return None


STAT_ARCHIV = BASIS / ".push_state" / "stat_archiv"


def _stat_schluessel(z):
    art = re.sub(r"(Wdh|Spaeter|Klar)$", "", str(z[1]))
    return f"{z[0]}|{art}|{z[9]}|{z[10]}"


def _stat_archiv(buch_zeilen):
    """Abgerechnete Buch-Zeilen ins Monats-Archiv schreiben (nur neue), dann alle archivierten Zeilen ergänzen,
    die im aktuellen Buch fehlen. Gibt die vollständigen Buch-Zeilen zurück."""
    STAT_ARCHIV.mkdir(parents=True, exist_ok=True)
    jetzt_keys = {_stat_schluessel(z) for z in buch_zeilen}
    monate = {}
    for f in sorted(STAT_ARCHIV.glob("*.json")):
        try:
            monate[f.stem] = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            monate[f.stem] = {}
    geaendert = set()
    for z in buch_zeilen:
        if z[2] is None:
            continue
        m, k = z[0][:7], _stat_schluessel(z)
        if k not in monate.setdefault(m, {}) or monate[m][k] != z:
            monate[m][k] = z
            geaendert.add(m)
    for m in geaendert:
        (STAT_ARCHIV / f"{m}.json").write_text(json.dumps(monate[m], ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return buch_zeilen + [z for mon in monate.values() for k, z in mon.items() if k not in jetzt_keys]


def _topf_still(p):
    """Merkmale alter Topf-Einträge (vor 17.11. ohne „still") aus Art, Quote, Liga — wie public.merkmale."""
    import public as _pub
    return _pub.merkmale({"m": {"art": p.get("art"), "live": p.get("live"), "quote": p.get("quote"),
                                "liga": p.get("liga"), "spiel": p.get("spiel"), "auswahl": p.get("auswahl"),
                                "auswahlRoh": p.get("auswahlRoh"), "markt": p.get("markt")}})


def statistik(burst, finder):
    zeilen = []
    import markout as _mo_mod
    global _mo, _gd, _stake_mo, _hz_mo
    _mo = _mo_mod
    _ps = BASIS / ".push_state"
    _gd = _mo.grunddrift(_mo._lesen(_ps / "live_drift.json", {}))
    _stake_mo = _mo._lesen(_ps / "stake_markout.json", {})
    _hz_mo = _mo._lesen(_ps / "hz_markout.json", {})
    try:
        buch = json.loads((BASIS / ".push_state" / "buch.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        buch = []
    # 17.11. (Rundum-Check, Lucas: „Speicher und Performance immer im Hinterkopf"): das Push-Buch hält nur die letzten
    # BEHALTEN Einträge (~12 Tage) — alles davor fiel still aus der Statistik (alle Betfair-Arten begannen am 05.11.).
    # Abhilfe 1: einmalig aus den Tagessicherungen geholte ältere Einträge (buch_alt.json, build.yml) mitlesen.
    try:
        _alt = json.loads((BASIS / ".push_state" / "buch_alt.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        _alt = []
    if _alt:
        _bk = lambda x: (str(x.get("ts"))[:16], x.get("art"), str(x.get("id")), x.get("markt"), x.get("auswahl"))
        _da = {_bk(x) for x in buch}
        _erst = min((str(x.get("ts")) for x in buch), default="9")
        buch = [x for x in _alt if isinstance(x, dict) and str(x.get("ts")) < _erst and _bk(x) not in _da] + buch
    n_buch0 = len(zeilen)
    doppelt = set()
    for b in buch:
        if b.get("art") not in STAT_ARTEN or b.get("status") not in ("abgerechnet", "offen") or not ts(b.get("ts")):
            continue
        art_ = b["art"]
        k_ = (art_, str(b.get("id")), b.get("markt"), b.get("auswahl"))
        if art_ in ("ht", "fix"):             # 07.11.: alte Mehrfachbuchungen (Neumeldung bei +50 % Geld) nur einmal zählen
            if k_ in doppelt:
                continue
            doppelt.add(k_)
        elif art_ in WDH_ARTEN:               # 10.11. (Betfair-Check, Lucas „ja"): nur der erste Push zu Spiel+Auswahl zählt,
            if k_ in doppelt:                 # jeder weitere wird eigens gemessen („…Wdh")
                art_ = art_ + "Wdh"
            doppelt.add(k_)
        if art_ in ("top3", "top3p"):
            if ("t3", str(b.get("id"))) in doppelt:
                art_ = "top3Spaeter"
            doppelt.add(("t3", str(b.get("id"))))
        if art_ == "pinnLinie":                        # 17.11. PL1: je Markt und Art eine eigene Zeile
            art_ = f"pinn{b.get('lmarkt') or ''}{(b.get('lart') or '').capitalize()}"
        if b.get("klar") and not art_.endswith("Wdh"):   # Regel T1 (16.11.): Tor-Linie im entschiedenen Spiel, nur Money
            art_ = art_ + "Klar"
        ab = b.get("status") == "abgerechnet"
        q, sl = b.get("quote"), b.get("schluss")
        clv = None
        if (not b.get("live") and isinstance(q, (int, float)) and isinstance(sl, (int, float)) and sl > 1
                and ts(b.get("schlussTs")) and ts(b.get("anpfiff")) and ts(b["schlussTs"]) >= ts(b["anpfiff"]) - timedelta(minutes=20)):
            clv = round(100 * (q / sl - 1), 1)
        if clv is None and b.get("live") and b.get("nach10"):      # 17.11.: Live-CLV = Markout 10 Min minus Grunddrift
            clv = _mo.bereinigt(q, b.get("nach10"), b.get("stand"), b.get("minute") if b.get("minute") is not None else b.get("min"),
                                b.get("markt"), b.get("auswahl"), _gd)
        titel = b.get("anzeige") or b.get("titel") or (f"{b.get('heim')} – {b.get('gast')}" if b.get("heim") else "")
        zeilen.append([ts(b["ts"]).strftime("%Y-%m-%dT%H:%M"), art_,
                       round(float(b["r"]), 3) if ab and isinstance(b.get("r"), (int, float)) else None,
                       (1 if b.get("win") else 0) if ab else None,
                       round(float(q), 2) if isinstance(q, (int, float)) else None, clv,
                       b.get("liga") or b.get("sport") or "", 1 if b.get("top5") else 0, 1 if b.get("live") else 0,
                       str(titel)[:60], str(b.get("auswahl") or "")[:40], b.get("lage"),
                       b.get("usd") if isinstance(b.get("usd"), (int, float)) else None, b.get("faktor")])   # 02.11.: Wal-Betrag + × üblicher Einsatz
        # 16.11.: Quote beim Push ÷ Ø Quote, zu der das Geld der letzten Stunde gematcht wurde (> 1 = läuft gegen das Geld)
        qg_ = b.get("qGeld")
        if isinstance(qg_, list) and len(qg_) == 2 and isinstance(qg_[0], (int, float)) and qg_[0] > 1 and isinstance(q, (int, float)):
            zeilen[-1].extend([None, round(q / qg_[0], 3)])
        # 17.11.: Spanne Back/Lay beim Push (Odds API) und CLV gegen die Börsen-Mitte vor Anpfiff
        sx, clvx = b.get("spanne"), None
        if (not b.get("live") and isinstance(q, (int, float)) and isinstance(b.get("schlussX"), (int, float))
                and b["schlussX"] > 1 and ts(b.get("schlussXTs")) and ts(b.get("anpfiff"))
                and ts(b["schlussXTs"]) >= ts(b["anpfiff"]) - timedelta(minutes=20)):
            clvx = round(100 * (q / b["schlussX"] - 1), 1)
        if isinstance(sx, (int, float)) or clvx is not None:
            zeilen[-1].extend([None] * (16 - len(zeilen[-1])))
            zeilen[-1].extend([sx, clvx])
    # Abhilfe 2: jede abgerechnete Zeile aus dem Push-Buch landet in einem Monats-Archiv (kompakt, ~120 Byte je Zeile);
    # was aus dem Buch gefallen ist, kommt von dort zurück. ~1–2 MB je Monat, in der Tagessicherung enthalten.
    try:
        zeilen[n_buch0:] = _stat_archiv(zeilen[n_buch0:])
    except Exception as exc:  # noqa: BLE001
        FEHLER.append(f"Statistik-Archiv: {exc}")
    for z in burst or []:
        if not isinstance(z, dict) or not z.get("push") or not stake_ok(z) or not ts(z.get("sentAt")):
            continue
        r = z.get("rendite")
        ab = z.get("status") == "abgerechnet" and isinstance(r, (int, float))
        if not ab and z.get("status") != "pending":
            continue
        # 10.11. (Stake-Check, Lucas „ja"): das ist das Ergebnis des WALS über alle seine Tickets im Burst (auch Ecken,
        # Totals …) und jede vom Stake-Skript markierte Zeile — nicht dein Tipp. Darum eigene Zeile „stakeWal";
        # „stake" sind ab jetzt die tatsächlich gesendeten Pushes mit Auswahl und Quote aus dem Push (bilanz.py).
        _sm = _stake_mo.get(f"{str(z.get('sentAt'))[:16]}|{z.get('event')}") or {}
        _sclv = (_mo.clv_vor(_sm.get("q0"), _sm.get("schluss"), _sm.get("schlussTs"), _sm.get("anpfiff")) if not _sm.get("live")
                 else _mo.bereinigt(_sm.get("q0"), _sm.get("nach10"), _sm.get("stand"), _sm.get("min"), _sm.get("markt"), _sm.get("auswahl"), _gd)) if _sm else None
        zeilen.append([ts(z["sentAt"]).strftime("%Y-%m-%dT%H:%M"), "stakeWal", round(float(r), 3) if ab else None, int(r > 0) if ab else None,
                       z.get("quote") if isinstance(z.get("quote"), (int, float)) else None, _sclv, z.get("liga") or z.get("kat") or "",
                       0, 1 if z.get("phase") == "live" else 0, str(z.get("event") or "")[:60], str(z.get("seite") or "")[:40]])
    for e in (finder or {}).get("eintraege") or []:
        if not ts(e.get("gebuchtAt")):
            continue
        w = e.get("wetten") or {}
        for gruppe, wette, name in (("tore", "over05", "Tor in der 2. HZ"), ("heim", "heim", "Heimsieg")):
            if gruppe not in (e.get("gruppen") or []):
                continue
            x = w.get(wette) or {}
            ab = isinstance(x.get("r"), (int, float))
            if not ab and e.get("ft"):
                continue
            _hm = _hz_mo.get(e.get("k") or f"hz:{e.get('matchId')}") or {}
            _q0, _qs = (_hm.get("q0") or {}).get(wette), _hm.get(wette)
            _hclv = round(100 * (_q0 / _qs - 1), 1) if isinstance(_q0, (int, float)) and isinstance(_qs, (int, float)) and _qs > 1 else None
            zeilen.append([ts(e["gebuchtAt"]).strftime("%Y-%m-%dT%H:%M"), "hz", round(float(x["r"]), 3) if ab else None,
                           int(bool(x.get("win"))) if ab else None, x.get("quote") if isinstance(x.get("quote"), (int, float)) else None,
                           _hclv, e.get("league") or e.get("liga") or "", 0, 1, f"{e.get('home') or ''} – {e.get('away') or ''}"[:60], name])
    # Lucas 31.10.: Public-Regel 1 — jedes Public-Signal als eigene Art „public", Ergebnis vom gleichen Tipp oben
    try:
        pl = json.loads((BASIS / ".push_state" / "public.json").read_text(encoding="utf-8")).get("liste") or []
    except Exception:  # noqa: BLE001
        pl = []
    nz = lambda s: re.sub(r"[^a-z0-9]", "", str(s or "").lower())
    erg_pub = _public_ergebnisse()
    # 09.11.: Public-Signale, die über Polymarket abgerechnet werden (E-Sport, Tennis, alles mit Polymarket-Markt)
    erg_poly = {(str(b.get("pubZeit")), nz(b.get("spiel"))[:12]): bool(b.get("win")) for b in buch
                if b.get("art") == "publicErg" and b.get("status") == "abgerechnet"}
    stake_von = {}            # 14.11. Staking-Regel S1: Stake 1–3 je Public-Zeile (still, Flat bleibt die Hauptrechnung)
    topf_z = []                # 17.11. Topf-Regel R31: jeder Kandidat mit Punkte-Teilen und Ergebnis → topf_dynamik
    for p in pl:
        n_vor = len(zeilen)
        _pub_zeile(p, zeilen, buch, erg_pub, erg_poly, nz)
        for z in zeilen[n_vor:]:
            if z[1] in ("public", "publicNein", "publicGegenwind", "publicNL", "publicQuote"):
                topf_z.append({"ts": (str(p.get("zeit") or "")[:16] + ":00+00:00"), "p": p.get("p"),
                               "still": {**_topf_still(p), **(p.get("still") or {})},   # 18.11.: neue stille Merkmale rückwirkend "live": bool(p.get("live")),
                               "r": z[2] if isinstance(z[2], (int, float)) else None})
                break
        if p.get("stake"):
            for z in zeilen[n_vor:]:
                if z[1] in ("public", "publicNein"):
                    stake_von[id(z)] = p["stake"]
    for z in zeilen:
        if id(z) in stake_von:
            z.extend([None] * (14 - len(z)))
            z.append(stake_von[id(z)])
    try:
        import topf_dynamik
        if topf_dynamik.rechnen(topf_z, JETZT):
            print("  topf-dynamik: Stichtag gerechnet")
    except Exception as exc:  # noqa: BLE001
        print(f"  topf-dynamik: {exc}")
    zeilen.sort(key=lambda z: z[0])
    (BASIS / "site" / "statistik.json").write_text(json.dumps({"stand": iso(JETZT), "felder": ["zeit", "art", "r", "w", "quote", "clv", "liga", "top5", "live", "titel", "auswahl", "lage", "usd", "faktor", "stake", "qg", "sp", "clvx"], "zeilen": zeilen}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    _sg = (BASIS / "site" / "statistik.json").stat().st_size
    if _sg > SPEICHER_GRENZEN["statistik"]:
        FEHLER.append(f"Speicher: statistik.json {_sg / 1e6:.1f} MB — ältere Monate verdichten")
    return len(zeilen)

# Public-Signale, die nachweislich mit überholtem Stand gesendet wurden (Datum UTC, Spiel normalisiert, 12 Zeichen) → void
PUBLIC_VOID = {("2026-11-05", "spartaprague"),
               ("2026-11-10", "butterflyhonv"),          # 10.11. Lucas: Push @ 1,62, Polymarket stand schon bei 99 %
               ("2026-11-11", "groundzerogaming"),       # 11.11. Lucas: Push @ 1,83, Polymarket stand bei 64 % (1,56)
               ("2026-11-12", "germanyu19"),             # 12.11. Über 0,5 @ 1,74 „72' 0:0" — das 0:1 fiel um 15:29, Push kam ~15:31
               ("2026-11-19", "corinthians")}            # 19.11. Corinthians @ 1,59 „26' 0:0" — das 1:0 fiel genau beim Push (Lucas)


def verlauf(bf_buch, burst, finder, tr_led):
    """Jede gepushte Wette der letzten 60 Tage als [zeit, quelle, rendite, ergebnis] — Grundlage für die
    Bilanz-Kurve und den Strichcode im Frontend. rendite: Gewinn je 1 Einheit Einsatz (None = offen),
    ergebnis: 1 gewonnen, 0 verloren, None offen."""
    grenze = JETZT - timedelta(days=VERLAUF_TAGE)
    out = []

    def dazu(t, q, r, w):
        tt = ts(t)
        if tt and tt >= grenze:
            out.append([tt.strftime("%Y-%m-%dT%H:%M"), q, None if r is None else round(float(r), 3), w])

    # Lucas 20.10.: „Betfair" in der Bilanz sind die EIGENEN Geldradar-Pushes (Push-Buch aus bilanz.py), nicht mehr
    # die des alten Projekts — „weil das zählt". Gibt es das Buch (noch) nicht, bleibt Betfair leer statt alt.
    for z in (bf_buch or {}).get("verlauf") or []:
        if isinstance(z, list) and len(z) == 3:
            dazu(z[0] + "+00:00" if len(z[0]) == 16 else z[0], "betfair", z[1], z[2])
    # 10.11.: Stake ab STAKE_EIGEN_AB aus dem eigenen Buch (gesendete Pushes, dein Tipp), davor wie bisher das Wal-Ergebnis
    for z in burst or []:
        if not isinstance(z, dict) or not z.get("push") or not stake_ok(z) or str(z.get("sentAt") or "") >= STAKE_EIGEN_AB:
            continue
        r = z.get("rendite")
        if z.get("status") == "abgerechnet" and isinstance(r, (int, float)):
            dazu(z.get("sentAt"), "stake", r, int(r > 0))
        elif z.get("status") == "pending":
            dazu(z.get("sentAt"), "stake", None, None)
    try:
        for b_ in json.loads((BASIS / ".push_state" / "buch.json").read_text(encoding="utf-8")):
            if b_.get("art") != "stake" or str(b_.get("ts") or "") < STAKE_EIGEN_AB:
                continue
            if b_.get("status") == "abgerechnet" and isinstance(b_.get("r"), (int, float)):
                dazu(b_["ts"], "stake", b_["r"], int(bool(b_.get("win"))))
            elif b_.get("status") == "offen":
                dazu(b_["ts"], "stake", None, None)
    except Exception:  # noqa: BLE001
        pass
    for e in (finder or {}).get("eintraege") or []:
        w = e.get("wetten") or {}
        for gruppe, wette in (("tore", "over05"), ("heim", "heim")):
            if gruppe not in (e.get("gruppen") or []):
                continue
            x = w.get(wette) or {}
            if isinstance(x.get("r"), (int, float)):
                dazu(e.get("gebuchtAt"), "hz", x["r"], int(bool(x.get("win"))))
            elif not e.get("ft"):
                dazu(e.get("gebuchtAt"), "hz", None, None)
    for z in tr_led or []:
        if not sport_ok(z.get("cat")) or not poly_key_ok(z.get("key")):
            continue
        p = z.get("pushPrice")
        if z.get("result") == "win" and isinstance(p, (int, float)) and 0 < p < 1:
            dazu(z.get("sentAt"), "wale", 1 / p - 1, 1)
        elif z.get("result") == "loss":
            dazu(z.get("sentAt"), "wale", -1.0, 0)
        elif z.get("status") == "pending":
            dazu(z.get("sentAt"), "wale", None, None)
    out.sort(key=lambda x: x[0])
    return out


# ── Markt-Charts (Lucas 19.10.: „die Märkte mit dem meisten Geld, aber ganz eigene Darstellung") ──
# Geld-Wand (Treemap) + Hitparade (Rang jetzt gegen vor 1 h). Jeder Lauf schreibt das Geld je Markt in ein
# schlankes Protokoll (.bf/markt_charts.json, 4 h, Actions-Cache); daraus kommen „vor 1 h" und die Spur.
MC_DATEI = BASIS / ".bf" / "markt_charts.json"
MC_H = 4
MC_MIN_EUR = 1500


def _linie_durch(markt, stand):
    m = re.match(r"^(?:Over/Under|First Half Goals) (\d+)\.5", str(markt or ""))
    if not m or not isinstance(stand, list) or None in stand[:2]:
        return False
    return (stand[0] or 0) + (stand[1] or 0) > int(m.group(1))


SIEG_MAERKTE = ("Match Odds", "Half Time", "Draw no Bet")


# Regel MM2 „Angriffs-Bild" (Lucas 20.11. 23:49, ersetzt MM1): spannend ist nicht irgendein Markt-Mix, sondern
# Team Sieg FT · Team Sieg HZ · HZ Über 0,5 · HZ Über 1,5 · Über 1,5 · Über 2,5 · Über 3,5 — dazu Lay-Geld gegen Unter
# auf denselben Linien (zählt als Über). „Aus dem heraus 3–4 gleichzeitig — das geil." Vorab festgelegt:
# * Bein = Geld in den letzten 30 Min (vor Anpfiff) bzw. 15 Min (live) auf eine dieser Auswahlen, ≥ 60 % des neuen
#   Geldes im Markt, ≥ €2k (Top 5: ≥ €10k), Quote ≥ 1,20. Rückwärts-Geld erkennt die Quote: vor Anpfiff steigt die Quote
#   der Auswahl um > 1 % → Lay. Über/Team mit Lay-Geld zählt nicht; Unter mit Lay-Geld zählt als Über-Bein. Live
#   zählt Unter als Lay, wenn die Quote trotz Zeitablauf steigt; Sprünge durch Tor/Rot (±40 %) und schon durchgegangene
#   Linien fallen raus, Sieg-Märkte in entschiedenen Spielen auch.
# * Widerspruch sperrt: Geld auf beide Teams, oder echtes Unter-Geld (Back) auf einer der Linien.
# * Push ab 3 Beinen, still gebucht ab 2 (Vergleich). Je Spiel vor Anpfiff einmal, live je Spielstand einmal.
MM2_LINIEN = ("First Half Goals 0.5", "First Half Goals 1.5", "Over/Under 1.5 Goals", "Over/Under 2.5 Goals", "Over/Under 3.5 Goals")
MM2_TEAM = ("Match Odds", "Half Time")


def mm2_angriff(radar):
    out = []
    for sp in radar.get("spiele") or []:
        live = bool(sp.get("live"))
        st = (sp.get("live") or {}).get("stand")
        boden = 10000 if sp.get("top5") else 2000
        beine, contra, teams = [], [], set()
        for f in (sp.get("flussLive") if live else sp.get("flussVorher")) or []:
            mk, aw, q, qv = f.get("markt"), str(f.get("auswahl") or ""), f.get("quote"), f.get("quoteVorher")
            if mk not in MM2_LINIEN + MM2_TEAM or (f.get("eur") or 0) < boden or (f.get("anteil") or 0) < 60:
                continue
            if live and q and qv and not (1 / 1.4 <= q / qv <= 1.4):
                continue                                   # Tor/Rot dazwischen
            if live and _linie_durch(mk, st):
                continue
            steigt = bool(q and qv and q > qv * 1.01)
            if mk in MM2_TEAM:
                if aw == "The Draw" or (live and (sp.get("entschieden") or sp.get("klarVorn"))):
                    continue
                if steigt and not live:
                    continue                               # Lay auf das Team — kein Bein
                teams.add(aw)
                beine.append(dict(f, art="team"))
            elif aw.startswith("Over"):
                if steigt and not live:
                    continue                               # Lay auf Über — vor Anpfiff kein Bein
                if not q or q < 1.2:
                    continue
                beine.append(dict(f, art="ueber"))
            elif aw.startswith("Under"):
                if steigt:
                    beine.append(dict(f, art="layUnter"))  # gegen Unter = für Tore
                else:
                    contra.append(f)
        if len(teams) > 1 or contra or len({b["markt"] for b in beine}) < 2:
            continue
        beine.sort(key=lambda b: -(b.get("eur") or 0))
        out.append({k: sp.get(k) for k in ("id", "heim", "gast", "liga", "land", "anpfiff", "live", "top5")}
                   | {"team": next(iter(teams), None), "beine": beine, "n": len({b["markt"] for b in beine})})
    out.sort(key=lambda x: (-x["n"], -sum(b.get("eur") or 0 for b in x["beine"])))
    return out


def markt_charts(radar):
    jetzt = {}
    zeilen = []
    for s in (radar or {}).get("spiele") or []:
        sieg_tot = bool(s.get("entschieden") or s.get("klarVorn"))     # 24.10.: sperrt nur noch die Sieg-Märkte
        stand = (s.get("live") or {}).get("stand")
        for m in s.get("maerkteDetail") or []:
            if (m.get("eur") or 0) < MC_MIN_EUR or _linie_durch(m.get("name"), stand) or (sieg_tot and m.get("name") in SIEG_MAERKTE):
                continue
            k = f"{s['id']}|{m['name']}"
            jetzt[k] = m["eur"]
            aus = [a for a in m.get("auswahl") or [] if a.get("name") != "Rest"]
            top = max(aus, key=lambda a: a.get("eur") or 0) if aus else None
            zeilen.append({"k": k, "id": s["id"], "heim": s["heim"], "gast": s["gast"], "liga": s.get("liga"),
                           "land": s.get("land"), "top5": s.get("top5"), "anpfiff": s.get("anpfiff"), "live": s.get("live"),
                           "markt": m["name"], "eur": m["eur"], "neu": m.get("neu"), "fenster": s.get("fensterMin"),
                           "fuehrt": ({"name": top.get("name"), "anteil": round(100 * (top.get("eur") or 0) / m["eur"]),
                                       "quote": top.get("quote")} if top and m["eur"] else None),
                           "auswahl": [[a.get("name"), a.get("eur"), a.get("quote"), a.get("q60")] for a in m.get("auswahl") or []]})
    try:
        buch = json.loads(MC_DATEI.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        buch = []
    grenze = JETZT - timedelta(hours=MC_H)
    buch = [b for b in buch if isinstance(b, dict) and (ts(b.get("t")) or grenze) > grenze]
    if jetzt:
        buch.append({"t": iso(JETZT), "m": jetzt})
    try:
        MC_DATEI.parent.mkdir(exist_ok=True)
        MC_DATEI.write_text(json.dumps(buch, separators=(",", ":")), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        FEHLER.append(f"markt_charts: {str(exc)[:100]}")
    # Punkt „vor 1 h": der nächste Eintrag an JETZT − 60 Min (±12 Min), sonst keiner → Bewegung noch nicht messbar
    pts = [(ts(b["t"]), b["m"]) for b in buch]
    vor = min((p for p in pts if abs((p[0] - (JETZT - timedelta(minutes=60))).total_seconds()) <= 12 * 60),
              key=lambda p: abs((p[0] - (JETZT - timedelta(minutes=60))).total_seconds()), default=None)
    # Spur: alle 20 Min ein Punkt über die letzten 4 h
    raster, letzte = [], None
    for t, m in pts:
        if letzte is None or (t - letzte).total_seconds() >= 19 * 60:
            raster.append(m); letzte = t
    if pts and (not raster or raster[-1] is not pts[-1][1]):
        raster.append(pts[-1][1])                     # der letzte Punkt ist immer „jetzt"
    for z in zeilen:
        z["vor60"] = (vor[1].get(z["k"]) if vor else None)
        z["spur"] = [m.get(z["k"]) for m in raster][-13:]
    zeilen.sort(key=lambda z: -z["eur"])
    return {"messbar": bool(vor), "seit": buch[0]["t"] if buch else None, "maerkte": zeilen[:120]}


def minuten_pruefen(prices, bf_hist):
    """Lucas 26.10. (Zakynthos – Pyrgos: 53 Min nach Anpfiff stand „13'"): Betwatch meldet die Spielminute manchmal
    nicht weiter. Steht die Minute seit ≥ 10 Min still, obwohl laut Uhr Zeit vergangen ist, gilt die Uhr: bis 47 Min
    nach Anpfiff = Minute, bis 62 = Pause (45'), danach Uhr − 15 (max. 90). Läuft sie nur hinterher, aber weiter
    (später angepfiffen), bleibt die gemeldete Minute. Korrigierte Spiele tragen liveInfo.minGeschaetzt."""
    n = 0
    for m in (prices or {}).get("matches") or []:
        li = m.get("liveInfo") or {}
        t, ko = li.get("time"), ts(m.get("kickoff"))
        if not isinstance(t, (int, float)) or not ko or li.get("finished") or li.get("is_ht"):
            continue
        uhr = (JETZT - ko).total_seconds() / 60
        schaetz = uhr if uhr <= 47 else 45 if uhr <= 62 else min(90, uhr - 15)
        pts = [p for p in (bf_hist or {}).get(str(m.get("matchId"))) or [] if isinstance(p, dict) and ts(p.get("ts"))]
        # Lucas 01.11. (Bohemians – Sparta: um 20:04 noch „89' · 0:0", Anpfiff 18:00): Betwatch meldet das Ende manchmal
        # nicht, die Minute bleibt bei 89 hängen — und weil die Uhr-Schätzung bei 90 deckelt, griff die Korrektur nie.
        # Ab der 85. Minute, seit ≥ 15 Min keine Bewegung und ≥ 110 Min nach Anpfiff → als beendet behandeln.
        # (Verlängerung bewegt die Minute weiter über 90 → bleibt live.)
        alt15 = [p for p in pts if JETZT - ts(p["ts"]) >= timedelta(minutes=15)]
        if t >= 85 and uhr >= 110 and alt15 and isinstance(alt15[-1].get("min"), (int, float)) and alt15[-1]["min"] >= t:
            li["finished"], li["endeGeschaetzt"] = True, True
            n += 1
            continue
        if schaetz - t < 10:
            continue
        alt = [p for p in pts if JETZT - ts(p["ts"]) >= timedelta(minutes=10)]
        if alt and isinstance(alt[-1].get("min"), (int, float)) and alt[-1]["min"] >= t:    # seit 10 Min keine Bewegung
            li["timeBetwatch"], li["time"], li["minGeschaetzt"] = t, int(schaetz), True
            if 47 < uhr <= 62:
                li["is_ht"] = True
            n += 1
    if n:
        FEHLER.append(f"Spielminute bei {n} Spiel(en) nach der Uhr geschätzt (Betwatch-Minute stand still)")


def main() -> int:
    prices = laden_bf("betfair_prices.json")
    bf_hist = laden_bf("betfair_history.json")
    minuten_pruefen(prices, bf_hist)
    bf_norm = eigen("betfair_league_norm.json", default={})            # liga_norm.py
    bf_norm_state = eigen("betfair_league_norm_state.json", default={})
    consensus = laden_bf("betfair_consensus.json", ".pin")
    history = laden_bf("betfair_consensus_history.json", ".pin")
    # 20.10.: Polymarket aus dem EIGENEN Mac-Lauf (poly_markt.py → .pm/), Wal-Pushes aus dem eigenen Push-Buch
    track = eigen("poly_wallet_track.json", ".pm", default={}, max_min=60)
    pm = (eigen("poly_maerkte.json", ".pm", default={}, max_min=60) or {}).get("maerkte") or {}
    upcoming = {k: v for k, v in pm.items() if not v.get("live")}
    live_m = {k: v for k, v in pm.items() if v.get("live")}
    offen_m = {k: v for k, v in upcoming.items() if v.get("whales")}
    wale = []                                     # öffentliche Wal-Pushes des alten Projekts gibt es nicht mehr
    tr_led, tr_rec = eigene_wal_pushes(push_bilanz())
    # 18.10.: Stake kommt aus dem eigenen Mac-Lauf (.st/, stake.yml)
    burst = laden_bf("stake_burst_ledger.json", ".st") or []
    hr = laden_bf("stake_highroller.json", ".st")      # einzelne Highroller-Wetten mit Zeitstempel (für die Serien)
    finder = eigen("hz_finder.json", default={})            # hz_finder.py (eigener Lauf)

    daten = {
        "generatedAt": iso(JETZT),
        "quellen": {
            "betfair": stand(prices, "_meta.generatedAt"),
            "zufluss": stand(prices, "_meta.generatedAt"),   # 20.10.: Zufluss rechnet der Geldradar selbst aus dem eigenen Abruf
            "pinnacle": stand(consensus, "generatedAt"),
            "polymarket": stand(track, "updatedAt"),
            # Stand = letzter Abruf (asof), nicht letzte Meldung — sonst wirkt Stake in ruhigen Phasen „alt"
            "stake": (laden_bf("stake_highroller.json", ".st") or {}).get("asof")
                     or max((z.get("sentAt") or "" for z in burst or [] if isinstance(z, dict)), default=None) or None,
            "hz": stand(finder, "updatedAt"),
            "stakeAbruf": (hr or {}).get("asof"),
        },
        "betfair": betfair(prices, {}, consensus),          # die Übersicht des alten Projekts gibt es nicht mehr
        "radar": (radar := betfair_radar(prices, bf_hist, bf_norm, bf_norm_state)),
        "zufluss30": zufluss_liste(radar, consensus),
        "liveZufluss": live_zufluss(radar),
        "mmAngriff": mm2_angriff(radar),           # 20.11. Regel MM2
        "polyFluss": (pfl := poly_fluss()),
        "serien": serien((takt_ := takt_fortschreiben(bf_hist)), radar),
        "phasen": phasen(takt_, radar),
        "phasenMeta": PHASEN_META,
        "serienRuhig": SERIEN_RUHIG[:20],
        "polySerien": poly_serien(pfl),
        "stakeSerien": stake_serien(hr),
        "bfEigen": BF_EIGEN["an"], "pinEigen": BF_EIGEN["pin"], "stakeEigen": BF_EIGEN["st"],
        "bfPushes": betfair_pushes(push_bilanz()),
        "endstaende": endstaende(),
        "halbzeit": halbzeit(prices),
        "bfHtPushes": bf_ht_pushes(prices, bf_norm),
        "pushBilanz": push_bilanz(),
        "top3Erst": top3_erst(),
        "ligaWert": liga_wert(lbz := liga_bilanz()),
        "verlauf": verlauf(push_bilanz(), burst, finder, tr_led),
        "ligaBilanz": lbz,
        "sharp": sharp(consensus, history),
        "pinnAlle": pinn_alle(consensus, history),
        "pinnDrops": pinn_drops(prices, bf_hist),
        "pinnLinien": pinn_linien(prices),
        "bfDrops": bf_drops(prices, bf_hist),   # 17.11. Regel BD1
        "fruehBewegt": _frueh_bewegt(),          # 18.11. Regel FB1 (Beobachtung)
        "bfUhr": bf_uhr(prices, bf_hist),        # 18.11. Regel LD1 (Beobachtung)
        "speicher": speicher_check(),           # 17.11. (Lucas: Speicher/Performance immer im Hinterkopf)
        "t3Dyn": eigen("t3_gewichte.json", ".push_state", default=None),   # 17.11. Top-3-Regel 7
        "topfDyn": eigen("topf_gewichte.json", ".push_state", default=None),   # 17.11. Topf-Regel R31
        "bfSpanne": bf_spanne(prices),
        "poly": (pmd := poly(track, upcoming, wale, offen_m, tr_led, tr_rec)),
        "polyBelegt": (pbel := belegte_positionen(pmd.get("wallets"), track, upcoming, live_m, offen_m)),
        "pk1": pk1(pbel, upcoming),             # 20.11. Regel PK1 (Beobachtung)
        "cm1": cm1(pbel, upcoming, pmd.get("wallets")),   # 20.11. Regel CM1 (Claude-Master, Test-Push)
        "marktCharts": markt_charts(radar),
        "toepfe": geld_toepfe(radar, {**(upcoming or {}), **(live_m or {})}, hr, pfl, pbel),
        "auchWoanders": dict(AUCH_WOANDERS),
        "stake": stake(burst),
        "hz": hz(finder),
        "fehler": FEHLER,
    }
    try:                                   # 18.11. Ticker-Gedächtnis (Lucas: Ideen 1–6 für den Live-Ticker)
        import ticker
        daten["ticker"] = ticker.lauf(daten, prices, daten.get("endstaende"), JETZT)
        print(f"  ticker: {len(daten['ticker'])} Meldungen (24 h)")
    except Exception as exc:  # noqa: BLE001
        FEHLER.append(f"Ticker: {str(exc)[:120]}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(daten, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    try:
        print("statistik:", statistik(burst, finder), "Zeilen")
    except Exception as exc:  # noqa: BLE001 — die Statistik darf den Bau nie kippen
        print("statistik:", exc)
    # kleine Kennzahlen-Datei: zum schnellen Prüfen von außen, ohne die ganze data.json zu laden
    pdz = daten.get("pinnDrops") or {}
    (OUT.parent / "stand.json").write_text(json.dumps({
        "generatedAt": daten["generatedAt"], "quellen": daten["quellen"],
        # Lucas 26.10. (Odds API kostet >100 €/Monat — ersetzbar durch API-Football?): Verbrauch sichtbar machen
        "oddsKontingent": {**((consensus or {}).get("oddsKontingent") or {}), "stand": (consensus or {}).get("generatedAt")},
        "pinnScan": {"stand": pdz.get("stand"), "spiele": pdz.get("n"), "vor": len(pdz.get("vor") or []),
                     "live": len(pdz.get("live") or []),
                     "beispiele": [f"{x['liga']}: {x['auf']} +{x['pp']} pp" for x in (pdz.get("live") or []) + (pdz.get("vor") or [])][:6]},
        "pinnRadar": len(daten.get("pinnAlle") or []), "serien": len(daten.get("serien") or []),
        "eigen": {"betfair": daten.get("bfEigen"), "pinnacle": daten.get("pinEigen"), "stake": daten.get("stakeEigen")},
        "fehler": daten.get("fehler"),
    }, ensure_ascii=False), encoding="utf-8")
    print(f"data.json: {OUT.stat().st_size // 1024} KB · Fehler: {len(FEHLER)}")
    for f in FEHLER:
        print("  ⚠️ ", f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
