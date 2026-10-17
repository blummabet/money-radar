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
            "betfairGeld": {"auf": g.get("moneyName"), "anteil": g.get("moneySharePct"),
                            "quote": g.get("moneyOdd")} if g.get("moneyName") else None,
            "poly": {"anteil": po.get("sharePct"), "quote": po.get("odd"), "usd": po.get("vol")} if po else None,
            "verlauf": [round(p["pinn"][i] * 100, 1) for p in h][-24:],
        })
    out.sort(key=lambda r: (-int(r["laeuftNoch"] or r["frisch"]), -r["pp"]))
    return out[:20]


# ── Polymarket ───────────────────────────────────────────────────────────────────
MIN_N = 12           # wie im alten Projekt (PW_RANK_MIN_N): darunter adelt Zufall Verlierer
MIN_SCHNITT_USD = 500
MIN_FENSTER = 8     # Aufloesungen im Messfenster, ab denen eine Untergrenze etwas sagt


def poly(track, upcoming, whale_ledger, offen_maerkte=None):
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
        if not isinstance(m, dict) or key.endswith(("-more-markets", "-exact-score")) or not sport_ok(m.get("sport")):
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

    grenze = JETZT - timedelta(hours=24)
    wale = []
    for z in whale_ledger or []:
        t = ts(z.get("sentAt"))
        if not t or t < grenze or not sport_ok(z.get("cat")):
            continue
        wale.append({"wallet": z.get("wallet"), "markt": z.get("key"), "seite": z.get("side"),
                     "usd": z.get("usd"), "preis": z.get("whaleEntry"), "anteilSeite": z.get("seitenAnteil"),
                     "liga": z.get("league"), "zeit": z.get("sentAt"), "status": z.get("status")})
    wale.sort(key=lambda r: r["zeit"] or "", reverse=True)
    return {"wallets": wallets, "maerkte": maerkte[:25], "walPushes": wale[:20],
            "regel": {"minN": MIN_N, "minSchnittUsd": MIN_SCHNITT_USD, "minFenster": MIN_FENSTER}}


# ── Stake ────────────────────────────────────────────────────────────────────────
ARTEN = {None: "Burst", "spiel": "Spiel-Burst", "tore": "Torlinien-Burst", "klein": "Kleine Liga"}


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
    return {"heute": heute[:40], "bilanz": bilanz}


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
                      "zeit": e.get("gebuchtAt"), "status": e.get("status")})
    heute.sort(key=lambda r: r["zeit"] or "", reverse=True)
    bericht = []
    for k, b in (f.get("bericht") or {}).items():
        if isinstance(b, dict):
            bericht.append({"wette": b.get("text") or k, "n": b.get("n"), "treffer": b.get("trefferPct"),
                            "erwartet": b.get("erwartetPct"), "roi": b.get("roi"), "ug": b.get("ug"),
                            "urteil": b.get("urteil")})
    return {"heute": heute[:30], "bericht": bericht, "regel": f.get("regel")}


def main() -> int:
    prices = laden("betfair_prices.json", {})
    overview = laden("betfair_overview.json", {})
    consensus = laden("betfair_consensus.json", {})
    history = laden("betfair_consensus_history.json", {})
    track = laden("poly_wallet_track.json", {})
    upcoming = laden("poly_money_upcoming.json", {})
    wale = laden("poly_whale_public_ledger.json", [])
    offen_m = laden("poly_money_broad_offen.json", {})
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
        "sharp": sharp(consensus, history),
        "poly": poly(track, upcoming, wale, offen_m),
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
