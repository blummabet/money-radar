#!/usr/bin/env python3
"""
report_sammler.py — Tageswerte für den Report (Lucas 10.11.2026)
================================================================================
„Auswertung, was die großen Wale gemacht haben, wieviel gesetzt, wieviel gewonnen … und auf Betfair, wieviel Kohle
da am Tag gematched wird. Im worst case speichern wir mehr Daten extra dafür — nicht für unsere Picks und Pushes."

Strikt getrennt vom Rest: liest nur, schreibt nur .rp/report.json (Gedächtnis) und site/report.json (Anzeige).
Keine Regel, kein Push, keine Messung hängt daran.

Quellen
  Stake      .st/stake_bet_ledger.json  — ROLLIERENDES Fenster (20.000 Wetten ≈ 4 Tage). Deshalb wird hier jede Wette
             beim ersten Sehen (Einsatz) und beim Endstand (Gewinn/Verlust) in feste Tageswerte übernommen, bevor sie
             aus dem Fenster fällt. Tag = Wiener Datum der Abgabe. Geld nur über Einzelwetten (wie stake_settle.bilanz).
  Betfair    .bf/liga_buch.json         — dauerhaft. Je Markt: gematchtes Volumen vor Anpfiff (v), Anteil der Seite mit
             dem meisten Geld (a), ob diese Seite gewann (w). Wird je Lauf komplett neu gezählt; Spielnamen werden aus
             .bf/ergebnisse.json übernommen und hier festgehalten.
  Polymarket .pm/poly_wallet_track.json — Tageswerte je Wallet [n, clvSum, wins, clvSq, gewinn, einsatz, nGeld],
             60 Tage. Abgeschlossene Tage werden hier dauerhaft festgehalten.

Sportarten wie überall im Geldradar: Fußball, Tennis, E-Sport. Gesammelt wird je Kategorie, angezeigt nur diese drei.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BASIS = Path(__file__).resolve().parent
WIEN = ZoneInfo("Europe/Vienna")
JETZT = datetime.now(timezone.utc)
HEUTE = JETZT.astimezone(WIEN).date().isoformat()
ZIEL = BASIS / ".rp" / "report.json"
SEITE = BASIS / "site" / "report.json"
ERLAUBT = ("Fußball", "Tennis", "E-Sport")
TOP = 6


def _laden(p, d):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return d


def _tag(ts):
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(WIEN).date().isoformat()
    except Exception:  # noqa: BLE001
        return None


def _top(liste, neu, schluessel, n=TOP, umgekehrt=True):
    liste = [x for x in liste if x.get("id") != neu.get("id")] + [neu]
    return sorted(liste, key=schluessel, reverse=umgekehrt)[:n]


# ── Stake ───────────────────────────────────────────────────────────────────────────────────────────────────
def stake(alt):
    led = _laden(BASIS / ".st" / "stake_bet_ledger.json", {})
    wetten = [w for w in led.get("wetten") or [] if isinstance(w, dict) and w.get("id")]
    s = alt.get("stake") or {}
    tage = s.get("tage") or {}
    gesehen, fertig = set(s.get("gesehen") or []), set(s.get("abgerechnet") or [])
    im_fenster = set()
    for w in wetten:
        i, tag = w["id"], _tag(w.get("ts"))
        if not tag:
            continue
        im_fenster.add(i)
        kat = w.get("kat") or "Andere"
        t = tage.setdefault(tag, {"n": 0, "einsatz": 0.0, "kombiN": 0, "kat": {}, "gewinner": [], "verlierer": [], "gross": []})
        k = t["kat"].setdefault(kat, {"n": 0, "einsatz": 0.0, "eN": 0, "eEinsatz": 0.0, "pnl": 0.0, "won": 0, "lost": 0, "cashout": 0})
        e = float(w.get("einsatzUsd") or 0)
        kombi = bool(w.get("kombi")) or (w.get("nBeine") or 1) > 1
        zeile = {"id": i, "event": w.get("event"), "liga": w.get("liga"), "kat": kat, "markt": w.get("markt"),
                 "auswahl": w.get("auswahl"), "quote": w.get("quote"), "einsatz": round(e), "phase": w.get("phase"),
                 "kombi": kombi, "nBeine": w.get("nBeine") or 1}
        if i not in gesehen:
            gesehen.add(i)
            t["n"] += 1
            t["einsatz"] = round(t["einsatz"] + e, 2)
            t["kombiN"] += 1 if kombi else 0
            k["n"] += 1
            k["einsatz"] = round(k["einsatz"] + e, 2)
            if kat in ERLAUBT:
                t["gross"] = _top(t["gross"], zeile, lambda x: x["einsatz"])
        a = w.get("abrechnung") or {}
        if i in fertig or not a.get("endstand"):
            continue
        fertig.add(i)
        pnl = a.get("pnlUsd")
        if pnl is None or kombi or not e:
            continue                                   # Geld nur über Einzelwetten (Kombi-Einsatz keinem Spiel zurechenbar)
        st = (a.get("status") or "").lower()
        k["eN"] += 1
        k["eEinsatz"] = round(k["eEinsatz"] + e, 2)
        k["pnl"] = round(k["pnl"] + pnl, 2)
        if st == "cashout":
            k["cashout"] += 1
        elif pnl > 0:
            k["won"] += 1
        elif pnl < 0:
            k["lost"] += 1
        if kat in ERLAUBT:
            z = {**zeile, "pnl": round(pnl), "status": st}
            if pnl > 0:
                t["gewinner"] = _top(t["gewinner"], z, lambda x: x["pnl"])
            elif pnl < 0:
                t["verlierer"] = _top(t["verlierer"], z, lambda x: x["pnl"], umgekehrt=False)
    # Wetten, die aus dem Fenster gefallen sind, kommen nie wieder → aus den Merk-Listen nehmen (bleiben klein)
    s["gesehen"] = sorted(gesehen & im_fenster)
    s["abgerechnet"] = sorted(fertig & im_fenster)
    s["tage"] = tage
    s["seit"] = s.get("seit") or min((_tag(w.get("ts")) for w in wetten if _tag(w.get("ts"))), default=HEUTE)
    s["stand"] = led.get("aktualisiert")
    return s


# ── Betfair ─────────────────────────────────────────────────────────────────────────────────────────────────
def betfair(alt):
    buch = _laden(BASIS / ".bf" / "liga_buch.json", {})
    erg = _laden(BASIS / ".bf" / "ergebnisse.json", {})
    b = alt.get("betfair") or {}
    namen = b.get("namen") or {}
    tage = {}
    for key, z in buch.items():
        if not isinstance(z, dict) or not z.get("t"):
            continue
        mid = key.split("|")[0]
        v = float(z.get("v") or 0)
        t = tage.setdefault(z["t"], {"v": 0.0, "n": 0, "moV": 0.0, "moN": 0, "favN": 0, "favW": 0, "favV": 0.0,
                                     "favVW": 0.0, "ligen": {}, "spiele": {}})
        t["v"] += v
        t["n"] += 1
        if z.get("m") == "Match Odds" and v > 0:
            t["moV"] += v
            t["moN"] += 1
            t["ligen"][z.get("l") or "?"] = t["ligen"].get(z.get("l") or "?", 0) + v
            t["spiele"][mid] = (v, z.get("l"), z.get("q"), bool(z.get("w")), z.get("a"))
            # „Hatte das große Geld recht?" — nur wo es klar war (≥ 60 % des Volumens auf einer Seite)
            if (z.get("a") or 0) >= 60:
                t["favN"] += 1
                t["favW"] += 1 if z.get("w") else 0
                t["favV"] += v
                t["favVW"] += v if z.get("w") else 0
    for tag, t in tage.items():
        top = sorted(t.pop("spiele").items(), key=lambda kv: -kv[1][0])[:TOP]
        t["top"] = []
        for mid, (v, liga, q, w, a) in top:
            e = erg.get(mid) or {}
            if e.get("h"):
                namen[mid] = {"h": e.get("h"), "a": e.get("a"), "ft": e.get("ft")}
            n = namen.get(mid) or {}
            t["top"].append({"id": mid, "spiel": f"{n['h']} – {n['a']}" if n.get("h") else None, "liga": liga,
                             "ft": n.get("ft"), "v": round(v), "quote": q, "geldRecht": w, "anteil": a})
        t["ligen"] = {k: round(v) for k, v in sorted(t["ligen"].items(), key=lambda kv: -kv[1])[:12]}
        for f in ("v", "moV", "favV", "favVW"):
            t[f] = round(t[f])
    alt_tage = b.get("tage") or {}
    alt_tage.update(tage)                              # neu gezählt; Tage, die aus dem Buch fielen, bleiben
    behalten = {t["id"] for x in alt_tage.values() for t in x.get("top") or []}
    return {"tage": alt_tage, "namen": {k: v for k, v in namen.items() if k in behalten},
            "seit": min(alt_tage) if alt_tage else None}


# ── Polymarket ──────────────────────────────────────────────────────────────────────────────────────────────
def poly(alt):
    track = _laden(BASIS / ".pm" / "poly_wallet_track.json", {})
    p = alt.get("poly") or {}
    tage = p.get("tage") or {}
    neu = {}
    for wal, s in (track.get("scores") or {}).items():
        for tag, v in (s.get("tage") or {}).items():
            if not isinstance(v, list) or len(v) < 3:
                continue
            t = neu.setdefault(tag, {"n": 0, "wins": 0, "nGeld": 0, "einsatz": 0.0, "gewinn": 0.0, "wallets": {}})
            t["n"] += int(v[0])
            t["wins"] += int(v[2])
            if len(v) >= 7 and v[6]:
                t["nGeld"] += int(v[6])
                t["einsatz"] += float(v[5])
                t["gewinn"] += float(v[4])
                if float(v[5]) >= 500:              # nur Wallets mit echtem Geld am Tag (hält die Datei klein)
                    t["wallets"][wal] = [int(v[0]), int(v[2]), round(float(v[4])), round(float(v[5]))]
    for tag, t in neu.items():
        t["einsatz"], t["gewinn"] = round(t["einsatz"]), round(t["gewinn"])
        tage[tag] = t                                   # Tage im 60-Tage-Fenster neu; ältere bleiben wie festgehalten
    return {"tage": tage, "seit": min(tage) if tage else None}


# ── Anzeige: fertige Kennzahlen je Zeitraum ───────────────────────────────────────────────────────────────────
def _summe_stake(tage, von, bis, nur=ERLAUBT):
    r = {"n": 0, "einsatz": 0.0, "eN": 0, "eEinsatz": 0.0, "pnl": 0.0, "won": 0, "lost": 0, "kat": {}, "tage": [],
         "gewinner": [], "verlierer": [], "gross": []}
    for tag in sorted(tage):
        if not (von <= tag <= bis):
            continue
        t = tage[tag]
        tz = {"tag": tag, "einsatz": 0.0, "pnl": 0.0}
        for kat, k in (t.get("kat") or {}).items():
            if kat not in nur:
                continue
            rk = r["kat"].setdefault(kat, {"n": 0, "einsatz": 0.0, "eEinsatz": 0.0, "pnl": 0.0})
            for f in ("n", "einsatz", "eEinsatz", "pnl"):
                rk[f] += k.get(f) or 0
            r["n"] += k["n"]
            r["einsatz"] += k["einsatz"]
            r["eN"] += k["eN"]
            r["eEinsatz"] += k["eEinsatz"]
            r["pnl"] += k["pnl"]
            r["won"] += k["won"]
            r["lost"] += k["lost"]
            tz["einsatz"] += k["einsatz"]
            tz["pnl"] += k["pnl"]
        r["tage"].append({k: (round(v) if isinstance(v, float) else v) for k, v in tz.items()})
        for f, rev in (("gewinner", True), ("verlierer", False), ("gross", True)):
            sk = (lambda x: x["pnl"]) if f != "gross" else (lambda x: x["einsatz"])
            r[f] = sorted(r[f] + [{**x, "tag": tag} for x in t.get(f) or []], key=sk, reverse=rev)[:5]
    r["roi"] = round(r["pnl"] / r["eEinsatz"], 4) if r["eEinsatz"] else None
    for f in ("einsatz", "eEinsatz", "pnl"):
        r[f] = round(r[f])
    r["kat"] = {k: {f: round(v) for f, v in x.items()} for k, x in r["kat"].items()}
    return r


def _summe_betfair(tage, von, bis):
    r = {"v": 0, "moV": 0, "moN": 0, "favN": 0, "favW": 0, "favV": 0, "favVW": 0, "ligen": {}, "tage": [], "top": []}
    for tag in sorted(tage):
        if not (von <= tag <= bis):
            continue
        t = tage[tag]
        for f in ("v", "moV", "moN", "favN", "favW", "favV", "favVW"):
            r[f] += t.get(f) or 0
        for l, v in (t.get("ligen") or {}).items():
            r["ligen"][l] = r["ligen"].get(l, 0) + v
        r["tage"].append({"tag": tag, "v": t.get("moV") or 0, "n": t.get("moN") or 0})
        r["top"] = sorted(r["top"] + [{**x, "tag": tag} for x in t.get("top") or [] if x.get("spiel")],
                          key=lambda x: -x["v"])[:5]
    r["ligen"] = dict(sorted(r["ligen"].items(), key=lambda kv: -kv[1])[:8])
    r["favQuote"] = round(r["favW"] / r["favN"], 4) if r["favN"] else None
    return r


def _summe_poly(tage, von, bis):
    r = {"n": 0, "wins": 0, "nGeld": 0, "einsatz": 0, "gewinn": 0, "tage": [], "wallets": {}}
    for tag in sorted(tage):
        if not (von <= tag <= bis):
            continue
        t = tage[tag]
        for f in ("n", "wins", "nGeld", "einsatz", "gewinn"):
            r[f] += t.get(f) or 0
        r["tage"].append({"tag": tag, "einsatz": t.get("einsatz") or 0, "gewinn": t.get("gewinn") or 0})
        for w, (n, wins, g, e) in (t.get("wallets") or {}).items():
            x = r["wallets"].setdefault(w, [0, 0, 0, 0])
            x[0] += n
            x[1] += wins
            x[2] += g
            x[3] += e
    ws = [{"wallet": w[:6] + "…" + w[-4:], "n": x[0], "wins": x[1], "gewinn": x[2], "einsatz": x[3]}
          for w, x in r.pop("wallets").items()]
    r["nWallets"] = len(ws)
    r["besten"] = sorted(ws, key=lambda x: -x["gewinn"])[:5]
    r["schlechtesten"] = sorted(ws, key=lambda x: x["gewinn"])[:5]
    r["imPlus"] = sum(1 for x in ws if x["gewinn"] > 0)
    r["quote"] = round(r["wins"] / r["n"], 4) if r["n"] else None
    r["roi"] = round(r["gewinn"] / r["einsatz"], 4) if r["einsatz"] else None
    return r


def zeitraeume():
    """Feste Report-Zeiträume: laufender Monat, Vormonat, je Monatshälfte, letzte 7 Tage."""
    from datetime import date, timedelta
    h = date.fromisoformat(HEUTE)
    gestern = (h - timedelta(days=1)).isoformat()
    erster = h.replace(day=1)
    vm_ende = erster - timedelta(days=1)
    vm_start = vm_ende.replace(day=1)
    z = [{"id": "7t", "name": "Letzte 7 Tage", "von": (h - timedelta(days=7)).isoformat(), "bis": gestern},
         {"id": f"{h:%Y-%m}", "name": f"{_monat(h)} (bisher)", "von": erster.isoformat(), "bis": gestern}]
    if h.day > 15:
        z.append({"id": f"{h:%Y-%m}-h1", "name": f"1.–15. {_monat(h)}", "von": erster.isoformat(),
                  "bis": h.replace(day=15).isoformat()})
    z.append({"id": f"{vm_start:%Y-%m}", "name": f"{_monat(vm_start)} {vm_start:%Y}", "von": vm_start.isoformat(),
              "bis": vm_ende.isoformat()})
    return z


def _monat(d):
    return ["Jänner", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
            "November", "Dezember"][d.month - 1]


def main():
    alt = _laden(ZIEL, {})
    r = {"version": 1, "stand": JETZT.isoformat(), "stake": stake(alt), "betfair": betfair(alt), "poly": poly(alt)}
    ZIEL.parent.mkdir(parents=True, exist_ok=True)
    ZIEL.write_text(json.dumps(r, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    anzeige = {"stand": r["stand"], "seit": {"stake": r["stake"].get("seit"), "betfair": r["betfair"].get("seit"),
                                              "poly": r["poly"].get("seit")}, "zeitraeume": []}
    for z in zeitraeume():
        anzeige["zeitraeume"].append({**z,
                                      "stake": _summe_stake(r["stake"]["tage"], z["von"], z["bis"]),
                                      "betfair": _summe_betfair(r["betfair"]["tage"], z["von"], z["bis"]),
                                      "poly": _summe_poly(r["poly"]["tage"], z["von"], z["bis"])})
    if SEITE.parent.exists():
        SEITE.write_text(json.dumps(anzeige, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    s = anzeige["zeitraeume"][0]
    print(f"report: Stake {len(r['stake']['tage'])} Tage · Betfair {len(r['betfair']['tage'])} Tage · "
          f"Poly {len(r['poly']['tage'])} Tage | 7T: Stake ${s['stake']['einsatz']:,} Einsatz, P&L ${s['stake']['pnl']:,} · "
          f"Betfair ${s['betfair']['moV']:,} MO · Poly ${s['poly']['einsatz']:,} → ${s['poly']['gewinn']:,}")


if __name__ == "__main__":
    main()
