#!/usr/bin/env python3
"""build.py — Geld-Radar, Phase 1: verdichtet die Daten der alten Pipeline zu EINER kleinen Datei.

Keine eigenen API-Abfragen. Quelle sind die JSON-Dateien, die blummabet/betting-dashboard
ohnehin schreibt (öffentliches Repo). Ausgabe: site/data.json, die die Website liest.

    RADAR_SRC=/pfad/zum/alten/repo python3 build.py     # lokal testen
    python3 build.py                                    # holt per HTTPS (GitHub Actions)

Regeln:
- Keine Picks, keine Empfehlung. Gezeigt wird, wo Geld liegt und wer es setzt.
- Jede Zahl bleibt bei ihrer Quelle. Was die Quelle nicht hat, steht als null da, nicht geraten.
- Jede Quelle trägt ihren Stand, damit die Seite zeigen kann, wie frisch sie ist.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAW = "https://raw.githubusercontent.com/blummabet/betting-dashboard/main/"
SRC = os.environ.get("RADAR_SRC", "").strip()
BASIS = Path(__file__).resolve().parent
OUT = BASIS / "site" / "data.json"

JETZT = datetime.now(timezone.utc)

# Lucas, 27.09./04.10./08.10.2026: Fussball, E-Sport, Tennis reichen. US-Sport, Kampfsport und
# Cricket „interessieren mich ueberhaupt nicht"; bei Stake auch kein ATP/WTA (ITF bleibt).
SPORTARTEN = {"Fußball", "E-Sport", "Tennis"}


def sport_ok(sport) -> bool:
    return (sport or "Fußball") in SPORTARTEN


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


def laden(name: str, default=None):
    """Eine Datei der alten Pipeline. Fehlt sie oder ist sie kaputt: default + Notiz."""
    try:
        if SRC:
            return json.loads((Path(SRC) / name).read_text(encoding="utf-8"))
        with urllib.request.urlopen(RAW + name, timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        FEHLER.append(f"{name}: {str(exc)[:120]}")
        return default


FEHLER: list[str] = []
BF_EIGEN = {"an": False, "pin": False, "st": False}


def laden_bf(name: str, ordner: str = ".bf"):
    """Aus dem EIGENEN 5-Min-Abruf (.bf/ Betfair, .pin/ Pinnacle), sonst die Datei des alten Projekts."""
    lokal = Path(__file__).resolve().parent / ordner / name
    try:
        if lokal.exists() and (datetime.now().timestamp() - lokal.stat().st_mtime) < 40 * 60:
            d = json.loads(lokal.read_text(encoding="utf-8"))
            # Ein Pinnacle-Lauf ohne einen einzigen Anker (Schlüssel fehlt, API weg) ist kein Ergebnis.
            if name == "betfair_consensus.json" and not (d or {}).get("covered"):
                raise ValueError("eigener Pinnacle-Lauf ohne Anker — nehme das alte Projekt")
            BF_EIGEN[{".bf": "an", ".pin": "pin"}.get(ordner, ordner.strip("."))] = True
            return d
    except Exception as exc:  # noqa: BLE001
        FEHLER.append(f".bf/{name}: {str(exc)[:120]}")
    return laden(name, {})


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
                  "soccer_france_ligue_one"}
PINN_VOR_MIN_PP = {True: 1.5, False: 2.5}     # vor Anpfiff, seit Eröffnung (Top-5 ist effizienter: kleiner Ausschlag zählt)
PINN_FRISCH_PP = {True: 1.0, False: 1.5}      # davon in der letzten Stunde → „frisch"
PINN_LIVE_MIN_PP = {True: 3.0, False: 4.0}    # live in ~15 Min, nur für ein Team, das NICHT führt
PINN_STAND = BASIS / ".pin" / "pinn_stand.json"


def pinn_drops(prices):
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
            vor.append({**basis, "auf": namen[i], "ausgang": ("heim", "remis", "gast")[i],
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


# ── Polymarket ───────────────────────────────────────────────────────────────────
MIN_N = 12           # wie im alten Projekt (PW_RANK_MIN_N): darunter adelt Zufall Verlierer
MIN_SCHNITT_USD = 500
MIN_FENSTER = 8     # Aufloesungen im Messfenster, ab denen eine Untergrenze etwas sagt


POLY_TOP5 = {"epl", "bun", "lal", "sea", "fl1", "lig1"}     # Polymarket-Schlüssel-Präfixe der Top-5-Ligen


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
        ug = fm - 1.645 * math.sqrt(var / fn)
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
        for suf, zusatz in (("-more-markets", ", Tore/Handicap"), ("-exact-score", ", Ergebnis")):
            if key.endswith(suf):
                return name_fuer(key[: -len(suf)]) + zusatz
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
        tp.append({"wallet": z.get("wallet"), "markt": z.get("key"), "spiel": name_fuer(z.get("key")), "seite": z.get("side"),
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
    return {"wallets": wallets, "maerkte": auswahl, "walPushes": wale[:20], "setzen": setzen[:20],
            "tradesPushes": tp[:40], "tradesBilanz": bil, "tradesSeit": (trades_record or {}).get("startAb"),
            "regel": {"minN": MIN_N, "minSchnittUsd": MIN_SCHNITT_USD, "minFenster": MIN_FENSTER}}


# ── Stake ────────────────────────────────────────────────────────────────────────
ARTEN = {None: "Burst", "spiel": "Spiel-Burst", "tore": "Torlinien-Burst", "klein": "Kleine Liga"}


def _norm_name(t):
    """„Atlético de Madrid" und „Atletico Madrid" sind dieselbe Auswahl."""
    import unicodedata
    t = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(w for w in t.replace("-", " ").split() if w not in {"de", "fc", "cf", "sc", "the", "club"})


def stake(ledger):
    zeilen = [z for z in (ledger or []) if isinstance(z, dict)]
    grenze = JETZT - timedelta(hours=24)
    heute = []
    for z in zeilen:
        t = ts(z.get("sentAt"))
        if not t or t < grenze or not z.get("push") or not stake_ok(z):
            continue
        heute.append({"art": ARTEN.get(z.get("art"), z.get("art")), "spiel": z.get("event"), "liga": z.get("liga"),
                      "auswahl": z.get("auswahl") or z.get("seite") or z.get("richtung"),
                      "markt": z.get("markt") or ", ".join(z.get("maerkte") or []) or None,
                      "usd": z.get("summeUsd"), "wetten": z.get("nWetten"), "quote": z.get("quote"),
                      "phase": z.get("phase"), "minute": z.get("spielminute"), "faktor": z.get("faktor"),
                      "zeit": z.get("sentAt"), "status": z.get("status"), "rendite": z.get("rendite")})
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
            g.update(usd=z["usd"], wetten=z["wetten"], quote=z["quote"] or g["quote"])
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
def _phase(jetzt, kick):
    """p0 >3h vor Anpfiff · p1 letzte 3h · l1 1. HZ · l2 ab 45' — wie betfair_league_norm.py."""
    d = (jetzt - kick).total_seconds()
    if d >= 0:
        if d > 3.5 * 3600:
            return None
        return "l2" if d > 45 * 60 else "l1"
    return "p1" if -d <= 3 * 3600 else "p0"


TOP5_RX = re.compile(r"^(english premier league|german bundesliga|spanish la liga|italian serie a|french ligue 1)$", re.I)


def auswahl_zufluss(snaps, minuten, min_eur, min_anteil=0.65):
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


def maerkte_detail(m, snaps, minuten):
    """Die geldstärksten Märkte eines Spiels mit Geld + Quote je Auswahl und dem Zufluss je Auswahl."""
    dl = sel_delta(snaps, minuten)
    out = []
    for name, mk in sorted(((k, v) for k, v in (m.get("markets") or {}).items() if isinstance(v, dict)),
                           key=lambda kv: -float(kv[1].get("vol") or 0)):
        if len(out) >= 6:
            break
        vol = float(mk.get("vol") or 0)
        if vol < 200:
            continue
        # Lucas 18.10.: „Über/Unter 7,5 @1,01 — wertlos, brauch ich nicht". Ein Markt, dessen meistgesetzte
        # Auswahl unter 1,10 steht (oder schon entschieden ist), sagt nichts mehr → nicht zeigen.
        quoten = [r.get("odd") for r in mk.get("runners") or [] if isinstance(r, dict) and isinstance(r.get("odd"), (int, float)) and r.get("odd") > 1]
        if quoten and min(quoten) < 1.10:
            continue
        rs = sorted((r for r in mk.get("runners") or [] if isinstance(r, dict) and (r.get("vol") or 0) > 0),
                    key=lambda r: -(r.get("vol") or 0))
        if name not in ("Match Odds", "Half Time", "Draw no Bet") and len(rs) > 4:   # Ergebnis-Märkte: Top 4 + Rest
            rest = sum(r.get("vol") or 0 for r in rs[4:])
            rs = rs[:4] + ([{"name": "Rest", "vol": rest, "odd": None}] if rest > 0 else [])
        d = dl.get(name) or {}
        out.append({"name": name, "eur": round(vol), "neu": round(sum(max(0, x) for x in d.values())),
                    "auswahl": [{"name": r.get("name"), "eur": round(r.get("vol") or 0), "quote": r.get("odd"),
                                 "neu": d.get(r.get("name"))} for r in rs]})
    return out


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


def betfair_radar(prices, bf_hist, norm, norm_state=None):
    """Jedes Spiel im Betfair-Abruf (auch Slowakei 2, Finnland, China …), gemessen an seiner Liga.

    ×Norm = gematchtes Geld jetzt / Median dieser Liga in derselben Phase (letzte 60 Tage, nur
    abgeschlossene Phasen). Eine laufende Phase ist noch nicht fertig — ×Norm ist also eher zu niedrig.
    """
    medi = (norm or {}).get("byLeagueStage") or {}
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
        ph = _phase(JETZT, kick)
        b = medi.get(f"{m.get('league')}|{ph}") or {}
        xnorm = round(vol / b["med"], 1) if b.get("med") and (b.get("n") or 0) >= 5 else None
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
                      "ht": bool(li.get("is_ht"))} if dt_h < 0 else None),
            # Lucas 17.10. (Everton–Chelsea 90' 0:2, €1,6 Mio „Zufluss"): Geld auf ein entschiedenes
            # Spiel ist Abrechnungs-/Trading-Rauschen bei Quote 1,01–1,05, kein Signal.
            "entschieden": bool(dt_h < 0 and isinstance(li.get("time"), (int, float)) and (li.get("time") >= 70 or (
                li.get("time") >= 30 and min([q for q in (mo.get("hw"), mo.get("dr"), mo.get("aw")) if q] or [9]) <= 1.10))),
            # Lucas 18.10. (Salzburg 14' 1:0 @1,21 im Live-Zufluss): führt ein Team und steht es unter 1,40, ist das
            # Spiel für alle Live-Listen und -Pushes durch — dieselbe Grenze wie „Wo am meisten liegt".
            "klarVorn": bool(dt_h < 0 and li.get("goal_v1") is not None and li.get("goal_v2") is not None
                             and li.get("goal_v1") != li.get("goal_v2")
                             and isinstance(mo.get("hw" if li.get("goal_v1") > li.get("goal_v2") else "aw"), (int, float))
                             and mo.get("hw" if li.get("goal_v1") > li.get("goal_v2") else "aw") < 1.40),
            "eur": round(vol), "xnorm": xnorm, "normEur": b.get("med"), "phase": ph, "rang": rang,
            "d60": round(vol - vor60) if vor60 is not None else None,
            # nur wenn der Vergleichspunkt nicht uralt ist (sonst wäre es kein 30-Min-Zufluss)
            "d30": round(vol - vor30) if vor30 is not None and t30 and t30 >= JETZT - timedelta(minutes=50) else None,
            "d15": round(vol - vor15) if vor15 is not None and t15 and t15 >= JETZT - timedelta(minutes=30) else None,
            # In welchen Markt floss das Geld zuletzt? (die Historie führt das Markt-Volumen der letzten 2 Abrufe)
            "marktZufluss": markt_zufluss(snaps),
            "maerkteDetail": [x for x in maerkte_detail(m, snaps, 30 if dt_h > 0 else 15)
                              if not (hz_vorbei and _ist_hz_markt(x.get("name")))],
            "fensterMin": 30 if dt_h > 0 else 15,
            "flussVorher": auswahl_zufluss(snaps, 30, 3000) if dt_h > 0 else [],
            # Lucas 18.10. (Valladolid 54' 1:2: „+€21k auf Über 0,5 HZ-Tore @ 2,02"): ein Halbzeit-Markt ist nach der
            # Pause abgerechnet — was dort noch „neu" erscheint, ist Abrechnung, kein Geld. Ab der 46. Minute raus.
            "flussLive": [f for f in auswahl_zufluss(snaps, 15, 1500) if not (hz_vorbei and _ist_hz_markt(f.get("markt")))] if dt_h <= 0 else [],
            "top5": bool(TOP5_RX.search(str(m.get("league") or ""))),
            "quoten30": ({"heim": mo30.get("hw"), "remis": mo30.get("dr"), "gast": mo30.get("aw")} if mo30 else None),
            "kurve": kurve,
            "reihe": reihe,
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
            "summeEur": round(sum(s["eur"] for s in spiele))}


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
        if dt_min > 30 * 60 or dt_min < -75 or li.get("is_ht") or (dt_min < 0 and isinstance(minute, (int, float)) and minute > 45):
            continue
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
    out.sort(key=lambda x: -x["eur"])
    return out[:40]


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


def bf_ht_pushes(prices):
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
            if isinstance(lead.get("odd"), (int, float)) and lead["odd"] < 1.30:
                continue
            if not best or tot > best["eur"]:
                best = {**basis, "art": "ht", "markt": name, "eur": round(tot), "auf": lead.get("name"), "quote": lead.get("odd"),
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
        d = fl[0]["eur"] if fl else None
        if not sp.get("live") or sp.get("entschieden") or sp.get("klarVorn") or not d:
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


def serien(takt, radar):
    spiele = {sp["id"]: sp for sp in (radar or {}).get("spiele") or []}
    out = []
    for mid, liste in takt.items():
        sp = spiele.get(str(mid))
        if not sp or sp.get("entschieden") or sp.get("klarVorn"):
            continue
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
            tor = None
            st0, st1 = kette[0][4], kette[-1][4]
            if st0 and st1 and st0 != st1:
                tor = {"von": list(st0), "nach": list(st1),
                       "vorTor": round(sum(k[1] for k in kette if k[4] == st0)),
                       "nachTor": round(sum(k[1] for k in kette if k[4] != st0))}
            markt, auswahl = key.split("|", 1)
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
                        "tor": tor,
                        # für den Push: wie der Markt jetzt insgesamt liegt (Lucas 18.10.)
                        "marktVerteilung": next(([[a["name"], a["eur"], a.get("quote"), round(summe) if a["name"] == auswahl else 0]
                                                  for a in m["auswahl"]] for m in sp.get("maerkteDetail") or [] if m["name"] == markt), None)})
    # je Spiel nur die stärkste Serie (sonst steht ein Spiel mit drei Märkten dreimal da)
    best = {}
    for x in sorted(out, key=lambda x: -x["eur"]):
        best.setdefault(x["id"], x)
    return sorted(best.values(), key=lambda x: (-x["n"], -x["eur"]))[:20]


def poly_serien(pf):
    """Dieselbe Idee für Polymarket, aus den 10-Min-Säulen von poly_fluss.py (Trades mit Zeitstempel)."""
    out = []
    for m in (pf or {}).get("maerkte") or []:
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
            preis = (m.get("preise") or {}).get(lab)
            ko = ts(m.get("anpfiff"))
            # Lucas 17.10. (Betis–Barça 5:2, Push 10 Min nach Abpfiff): gut 2 h nach Anpfiff ist das Spiel aus
            if ko and (m.get("sport") or "Fußball") == "Fußball" and JETZT - ko > timedelta(minutes=115):
                continue
            if preis and preis >= 0.90:
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
    top5_slugs = {"premier-league", "la-liga", "laliga", "bundesliga", "serie-a", "ligue-1"}
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
        top5 = str(w0.get("ligaSlug") or "") in top5_slugs
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


def geld_toepfe(radar, poly_alle, hr, pf=None, belegt=None):
    spiele = []
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
    mm_vol = {}
    for key, m in (poly_alle or {}).items():
        if isinstance(m, dict) and key.endswith("-more-markets"):
            mm_vol[key[:-len("-more-markets")]] = float(m.get("totalUsd") or 0)
    for key, m in (poly_alle or {}).items():
        if not isinstance(m, dict) or key.endswith(("-more-markets", "-exact-score")) or not poly_key_ok(key):
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
                 "anpfiff": m.get("koTs"), "live": None, "top5": key.split("-")[0] in {"epl", "bun", "lal", "sea", "fl1", "lig1"},
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
        wl = [dict(x, k=_seite(x["seite"], g["heim"], g["gast"])) for x in
              ((belegt or {}).get(key) or []) + ((belegt or {}).get(key + "-more-markets") or [])]
        if wl:
            g["wale"] = wl[:6]
        g["q"]["poly"] = {"betrag": round(betrag), "seiten": seiten, "key": key, "seitenAus": herkunft if seiten else None,
                          "preise": {(_seite(n, g["heim"], g["gast"]) or n): p for n, p in (m.get("prices") or {}).items()}}

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
        mk = str(w.get("markt") or "").lower()
        # Seite aus Sieg-Märkten, aber auch aus Handicap / Draw No Bet (Auswahl = Teamname, ggf. mit Linie)
        if mk in ("1x2", "match result", "winner", "match winner", "moneyline", "winner - twoway") \
                or ("handicap" in mk and "half" not in mk and "1st" not in mk) or "draw no bet" in mk \
                or ("winner" in mk and "set" not in mk and "game" not in mk):
            e["seiten"].setdefault(w.get("auswahl"), 0.0)
            e["seiten"][w.get("auswahl")] += w.get("einsatzUsd") or 0
    for e in je_event.values():
        w = e["w"]
        h, gst = _teams(w.get("event"))
        if not h:
            continue
        g = finden(h, gst, w.get("anpfiff"))
        if not g:
            g = {"id": "st:" + str(w.get("eventId")), "heim": h, "gast": gst, "liga": w.get("liga"), "land": None,
                 "anpfiff": w.get("anpfiff"), "live": None, "top5": str(w.get("ligaSlug") or "") in {"premier-league", "la-liga", "bundesliga", "serie-a", "ligue-1"},
                 "sport": w.get("kat") or "Fußball", "q": {}}
            spiele.append(g)
        seiten = {}
        for n, v in e["seiten"].items():
            k = _seite(n, g["heim"], g["gast"])
            if k:
                seiten[k] = seiten.get(k, 0) + v
        g["q"]["stake"] = {"betrag": round(e["summe"]), "seiten": seiten, "wetten": e["n"]}

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
    try:
        roh = laden("betfair_track_results.json", None)
        if roh:
            import betfair_track_store
            for z in betfair_track_store.entpacken(roh):
                if not isinstance(z, dict) or z.get("win") is None or not z.get("league"):
                    continue
                k = f"{z.get('matchId')}|{z.get('market')}"
                buch[k] = {"l": z["league"], "t": (z.get("settledAt") or "")[:10], "q": z.get("entryOdd") or z.get("odd"),
                           "w": bool(z["win"]), "v": z.get("mktVol") or 0, "a": z.get("sharePct") or 0, "m": z.get("market")}
    except Exception as exc:  # noqa: BLE001
        FEHLER.append(f"Liga-Bilanz: {str(exc)[:100]}")
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
    return {"stand": iso(JETZT), "fensterTage": LIGA_FENSTER_TAGE, "ligen": out,
            "zeilen": len(buch)}


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
        m["top5"] = pre in {"epl", "bun", "lal", "sea", "fl1", "lig1"}
        rows.append(m)
    return {"stand": d.get("generatedAt"), "maerkte": rows}


def betfair_pushes(ledger, record):
    out = []
    for e in ledger or []:
        t = ts(e.get("sentAt"))
        if not t or t < JETZT - timedelta(hours=36):
            continue
        out.append({"zeit": e.get("sentAt"), "art": {"fresh": "Frisches Geld", "ht": "Halbzeit"}.get(e.get("scenario"), e.get("scenario")),
                    "markt": e.get("market"), "liga": e.get("league"), "heim": e.get("home"), "gast": e.get("away"),
                    "auf": e.get("leadName"), "quote": e.get("leadOdd"), "eur": round(e.get("value") or 0),
                    "anteil": round((e.get("leadShare") or 0) * 100), "status": e.get("status"),
                    "pinnacleEinig": (e.get("consensus") or {}).get("agree")})
    out.sort(key=lambda x: x["zeit"], reverse=True)
    r = record or {}
    bil = {"n": r.get("n"), "treffer": round((r.get("hitRate") or 0) * 100, 1), "roi": round((r.get("roi") or 0) * 100, 1),
           "roiUg": round((r.get("roiUg") or 0) * 100, 1) if r.get("roiUg") is not None else None,
           "schlaegtPinnacle": round((r.get("pctBeatPinn") or 0) * 100) if r.get("pctBeatPinn") is not None else None}
    return {"liste": out[:40], "bilanz": bil}


# ── Verlauf aller Push-Ergebnisse (Lucas 19.10.: „zu wenig mit Graphen gearbeitet") ─────────────────────────
VERLAUF_TAGE = 60


def verlauf(bf_led, burst, finder, tr_led):
    """Jede gepushte Wette der letzten 60 Tage als [zeit, quelle, rendite, ergebnis] — Grundlage für die
    Bilanz-Kurve und den Strichcode im Frontend. rendite: Gewinn je 1 Einheit Einsatz (None = offen),
    ergebnis: 1 gewonnen, 0 verloren, None offen."""
    grenze = JETZT - timedelta(days=VERLAUF_TAGE)
    out = []

    def dazu(t, q, r, w):
        tt = ts(t)
        if tt and tt >= grenze:
            out.append([tt.strftime("%Y-%m-%dT%H:%M"), q, None if r is None else round(float(r), 3), w])

    for e in bf_led or []:
        st, odd = e.get("status"), e.get("leadOdd")
        if st == "won" and isinstance(odd, (int, float)):
            dazu(e.get("sentAt"), "betfair", (odd - 1) * 0.98, 1)
        elif st == "lost":
            dazu(e.get("sentAt"), "betfair", -1.0, 0)
        elif st == "pending":
            dazu(e.get("sentAt"), "betfair", None, None)
    for z in burst or []:
        if not isinstance(z, dict) or not z.get("push") or not stake_ok(z):
            continue
        r = z.get("rendite")
        if z.get("status") == "abgerechnet" and isinstance(r, (int, float)):
            dazu(z.get("sentAt"), "stake", r, int(r > 0))
        elif z.get("status") == "pending":
            dazu(z.get("sentAt"), "stake", None, None)
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


def main() -> int:
    prices = laden_bf("betfair_prices.json")
    bf_hist = laden_bf("betfair_history.json")
    bf_norm = laden("betfair_league_norm.json", {})
    bf_norm_state = laden("betfair_league_norm_state.json", {})
    bf_led = laden("betfair_public_ledger.json", [])
    bf_rec = laden("betfair_public_record.json", {})
    overview = laden("betfair_overview.json", {})
    consensus = laden_bf("betfair_consensus.json", ".pin")
    history = laden_bf("betfair_consensus_history.json", ".pin")
    track = laden("poly_wallet_track.json", {})
    upcoming = laden("poly_money_upcoming.json", {})
    wale = laden("poly_whale_public_ledger.json", [])
    offen_m = laden("poly_money_broad_offen.json", {})
    tr_led = laden("poly_whale_trades_ledger.json", [])
    tr_rec = laden("poly_whale_trades_record.json", {})
    # 18.10.: Stake kommt aus dem eigenen Mac-Lauf (.st/, stake.yml); ist der älter als 40 Min, aus dem alten Projekt
    burst = laden_bf("stake_burst_ledger.json", ".st") or []
    hr = laden_bf("stake_highroller.json", ".st")      # einzelne Highroller-Wetten mit Zeitstempel (für die Serien)
    finder = laden("hz_finder.json", {})

    daten = {
        "generatedAt": iso(JETZT),
        "quellen": {
            "betfair": stand(prices, "_meta.generatedAt"),
            "zufluss": stand(overview, "generatedAt"),
            "pinnacle": stand(consensus, "generatedAt"),
            "polymarket": stand(track, "updatedAt"),
            "stake": max((z.get("sentAt") or "" for z in burst or [] if isinstance(z, dict)), default=None) or None,
            "hz": stand(finder, "updatedAt"),
            "stakeAbruf": (hr or {}).get("asof"),
        },
        "betfair": betfair(prices, overview, consensus),
        "radar": (radar := betfair_radar(prices, bf_hist, bf_norm, bf_norm_state)),
        "zufluss30": zufluss_liste(radar, consensus),
        "liveZufluss": live_zufluss(radar),
        "polyFluss": (pfl := poly_fluss()),
        "serien": serien(takt_fortschreiben(bf_hist), radar),
        "polySerien": poly_serien(pfl),
        "stakeSerien": stake_serien(hr),
        "bfEigen": BF_EIGEN["an"], "pinEigen": BF_EIGEN["pin"], "stakeEigen": BF_EIGEN["st"],
        "bfPushes": betfair_pushes(bf_led, bf_rec),
        "halbzeit": halbzeit(prices),
        "bfHtPushes": bf_ht_pushes(prices),
        "pushBilanz": push_bilanz(),
        "verlauf": verlauf(bf_led, burst, finder, tr_led),
        "ligaBilanz": liga_bilanz(),
        "sharp": sharp(consensus, history),
        "pinnAlle": pinn_alle(consensus, history),
        "pinnDrops": pinn_drops(prices),
        "poly": (pmd := poly(track, upcoming, wale, offen_m, tr_led, tr_rec)),
        "polyBelegt": (pbel := belegte_positionen(pmd.get("wallets"), track, upcoming, laden("poly_money_broad_live.json", {}), offen_m)),
        "toepfe": geld_toepfe(radar, {**(upcoming or {}), **(laden("poly_money_broad_live.json", {}) or {})}, hr, pfl, pbel),
        "stake": stake(burst),
        "hz": hz(finder),
        "fehler": FEHLER,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(daten, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    # kleine Kennzahlen-Datei: zum schnellen Prüfen von außen, ohne die ganze data.json zu laden
    pdz = daten.get("pinnDrops") or {}
    (OUT.parent / "stand.json").write_text(json.dumps({
        "generatedAt": daten["generatedAt"], "quellen": daten["quellen"],
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
