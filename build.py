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
OUT = Path(__file__).resolve().parent / "site" / "data.json"

JETZT = datetime.now(timezone.utc)

# Lucas, 27.09./04.10./08.10.2026: Fussball, E-Sport, Tennis reichen. US-Sport, Kampfsport und
# Cricket „interessieren mich ueberhaupt nicht"; bei Stake auch kein ATP/WTA (ITF bleibt).
SPORTARTEN = {"Fußball", "E-Sport", "Tennis"}


def sport_ok(sport) -> bool:
    return (sport or "Fußball") in SPORTARTEN


def poly_key_ok(key) -> bool:
    """Lucas 17.10.: auch bei Polymarket kein ATP/WTA, ITF bleibt."""
    return not str(key or "").startswith(("atp-", "wta-"))


def stake_ok(z) -> bool:
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
BF_EIGEN = {"an": False, "pin": False}


def laden_bf(name: str, ordner: str = ".bf"):
    """Aus dem EIGENEN 5-Min-Abruf (.bf/ Betfair, .pin/ Pinnacle), sonst die Datei des alten Projekts."""
    lokal = Path(__file__).resolve().parent / ordner / name
    try:
        if lokal.exists() and (datetime.now().timestamp() - lokal.stat().st_mtime) < 40 * 60:
            d = json.loads(lokal.read_text(encoding="utf-8"))
            # Ein Pinnacle-Lauf ohne einen einzigen Anker (Schlüssel fehlt, API weg) ist kein Ergebnis.
            if name == "betfair_consensus.json" and not (d or {}).get("covered"):
                raise ValueError("eigener Pinnacle-Lauf ohne Anker — nehme das alte Projekt")
            BF_EIGEN["an" if ordner == ".bf" else "pin"] = True
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


# ── Polymarket ───────────────────────────────────────────────────────────────────
MIN_N = 12           # wie im alten Projekt (PW_RANK_MIN_N): darunter adelt Zufall Verlierer
MIN_SCHNITT_USD = 500
MIN_FENSTER = 8     # Aufloesungen im Messfenster, ab denen eine Untergrenze etwas sagt


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
                       "sport": p.get("sport")} for p in offen_jetzt[:5]],
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
                      "ft": e.get("ft"), "wetten": {k: bool((w or {}).get("win")) for k, w in (e.get("wetten") or {}).items()}})
    heute.sort(key=lambda r: r["zeit"] or "", reverse=True)
    bericht = []
    for k, b in (f.get("bericht") or {}).items():
        if isinstance(b, dict):
            bericht.append({"wette": b.get("text") or k, "n": b.get("n"), "treffer": b.get("trefferPct"),
                            "erwartet": b.get("erwartetPct"), "roi": b.get("roi"), "ug": b.get("ug"),
                            "urteil": b.get("urteil")})
    return {"heute": heute[:30], "bericht": bericht, "regel": f.get("regel")}


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
                        "marktNeu": round(ges)})
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
                           key=lambda kv: -float(kv[1].get("vol") or 0))[:6]:
        vol = float(mk.get("vol") or 0)
        if vol < 200:
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
        spiele.append({
            "id": str(m.get("matchId")), "heim": m.get("home"), "gast": m.get("away"),
            "liga": m.get("league"), "land": m.get("country"), "anpfiff": m.get("kickoff"),
            "live": ({"min": li.get("time"), "stand": [li.get("goal_v1"), li.get("goal_v2")],
                      "ht": bool(li.get("is_ht"))} if dt_h < 0 else None),
            # Lucas 17.10. (Everton–Chelsea 90' 0:2, €1,6 Mio „Zufluss"): Geld auf ein entschiedenes
            # Spiel ist Abrechnungs-/Trading-Rauschen bei Quote 1,01–1,05, kein Signal.
            "entschieden": bool(dt_h < 0 and isinstance(li.get("time"), (int, float)) and (li.get("time") >= 70 or (
                li.get("time") >= 30 and min([q for q in (mo.get("hw"), mo.get("dr"), mo.get("aw")) if q] or [9]) <= 1.10))),
            "eur": round(vol), "xnorm": xnorm, "normEur": b.get("med"), "phase": ph, "rang": rang,
            "d60": round(vol - vor60) if vor60 is not None else None,
            # nur wenn der Vergleichspunkt nicht uralt ist (sonst wäre es kein 30-Min-Zufluss)
            "d30": round(vol - vor30) if vor30 is not None and t30 and t30 >= JETZT - timedelta(minutes=50) else None,
            "d15": round(vol - vor15) if vor15 is not None and t15 and t15 >= JETZT - timedelta(minutes=30) else None,
            # In welchen Markt floss das Geld zuletzt? (die Historie führt das Markt-Volumen der letzten 2 Abrufe)
            "marktZufluss": markt_zufluss(snaps),
            "maerkteDetail": maerkte_detail(m, snaps, 30 if dt_h > 0 else 15),
            "fensterMin": 30 if dt_h > 0 else 15,
            "flussVorher": auswahl_zufluss(snaps, 30, 3000) if dt_h > 0 else [],
            "flussLive": auswahl_zufluss(snaps, 15, 1500) if dt_h <= 0 else [],
            "top5": bool(TOP5_RX.search(str(m.get("league") or ""))),
            "quoten30": ({"heim": mo30.get("hw"), "remis": mo30.get("dr"), "gast": mo30.get("aw")} if mo30 else None),
            "kurve": kurve,
            "quoten": {"heim": mo.get("hw"), "remis": mo.get("dr"), "gast": mo.get("aw")},
            "wahrsch": ({"heim": fair.get("home"), "remis": fair.get("draw"), "gast": fair.get("away")} if fair else None),
            "verteilung": vert,
            "maerkte": [{"name": k, "eur": round(v)} for k, v in maerkte[:4] if v > 0],
        })
    spiele.sort(key=lambda s: -(s["xnorm"] or 0))
    std = [{"h": iso(h), "eur": round(v)} for h, v in sorted(stunden.items())][-12:]
    ligen = len({s["liga"] for s in spiele})
    return {"spiele": spiele[:120], "stunden": std, "ligen": ligen,
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


def zufluss_liste(radar, consensus):
    """Frischer Zufluss aus der eigenen Historie: wo kam in den letzten 30 Min am meisten Geld dazu.

    Lucas 17.10.: oben stand oft nur 1–3 Spiele (die Liste des alten Projekts vergleicht nur zwei
    Abrufe). Jetzt die größten 30-Min-Zuflüsse über alle Ligen, entschiedene Spiele raus.
    """
    kons = {str(g.get("matchId")): g for g in (consensus or {}).get("games") or []}
    out = []
    for sp in radar.get("spiele") or []:
        fl = sp.get("flussVorher") or []
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


def live_zufluss(radar):
    """Lucas 17.10.: „vor allem in den restlichen Ligen, wenn da inplay viel kommt". Live-Spiele,
    in die in den letzten 15 Min Geld floss — ohne entschiedene (ab 70' / Quote unter 1,10)."""
    out = []
    for sp in radar.get("spiele") or []:
        fl = sp.get("flussLive") or []
        d = fl[0]["eur"] if fl else None
        if not sp.get("live") or sp.get("entschieden") or not d:
            continue
        out.append({k: sp.get(k) for k in ("id", "heim", "gast", "liga", "land", "anpfiff", "live", "eur", "xnorm",
                                            "quoten", "verteilung", "marktZufluss", "top5", "rang")} | {"zuflussEur": d, "fluss": fl})
    out.sort(key=lambda x: -x["zuflussEur"])
    return out[:20]


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
    burst = laden("stake_burst_ledger.json", [])
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
        },
        "betfair": betfair(prices, overview, consensus),
        "radar": (radar := betfair_radar(prices, bf_hist, bf_norm, bf_norm_state)),
        "zufluss30": zufluss_liste(radar, consensus),
        "liveZufluss": live_zufluss(radar),
        "polyFluss": poly_fluss(),
        "bfEigen": BF_EIGEN["an"], "pinEigen": BF_EIGEN["pin"],
        "bfPushes": betfair_pushes(bf_led, bf_rec),
        "halbzeit": halbzeit(prices),
        "sharp": sharp(consensus, history),
        "pinnAlle": pinn_alle(consensus, history),
        "poly": poly(track, upcoming, wale, offen_m, tr_led, tr_rec),
        "stake": stake(burst),
        "hz": hz(finder),
        "fehler": FEHLER,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(daten, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"data.json: {OUT.stat().st_size // 1024} KB · Fehler: {len(FEHLER)}")
    for f in FEHLER:
        print("  ⚠️ ", f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
