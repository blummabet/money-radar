"""wal.py — „Wal des Tages" als Instagram-Story (Lucas 05.11.2026: „ich finde die Idee sehr geil, vor allem das mit den Stories").

Abends ~18:15: die größte einzelne Polymarket-Position auf ein Fußballspiel, das in den nächsten 5 Stunden anpfeift.
  · ab $10.000, nur Spiele, die wir auch bei Betfair führen (sonst keine Auflösung)
  · kein Markt, der praktisch schon entschieden ist (ein Ausgang über 90 %; 05.11.: $82k auf Remis bei 99 % in der
    Saudi-Liga war eine Verschiebung, kein Wal)
Morgens: Auflösung mit Endstand (aus den Endständen der Seite).
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone

MIN_USD = 10000
FENSTER_H = float(os.environ.get("WAL_FENSTER_H") or 5)   # nur für Probeläufe änderbar
MAX_PREIS = 0.90


def _ts(s):
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def _seite(name, teams):
    n = str(name or "")
    if n.lower().startswith("draw"):
        return "remis"
    if teams and n == teams[0]:
        return "heim"
    if len(teams) > 1 and n == teams[1]:
        return "gast"
    return None


def _bewiesen(d, jetzt, toepfe, maerkte):
    """Lucas 05.11. 19:45: ein Ranglisten-Wallet (poly.setzen — nachweislich gut bei Fußball) mit ≥ $10k geht vor."""
    beste = None
    for o in (d.get("poly") or {}).get("setzen") or []:
        if (o.get("sport") or "Fußball") != "Fußball" or float(o.get("usd") or 0) < MIN_USD:
            continue
        ko = _ts(o.get("anpfiff"))
        m = maerkte.get(o.get("markt"))
        g = toepfe.get(o.get("markt"))
        if not ko or not m or not g or not (jetzt < ko <= jetzt + timedelta(hours=FENSTER_H)):
            continue
        if beste is None or float(o["usd"]) > beste[0]:
            beste = (float(o["usd"]), o, m, g)
    return beste


def _bauen(usd, seite_name, wallet, m, g, rang=None):
    preise = m.get("preise") or {}
    teams = [n for n in preise if not str(n).lower().startswith("draw")]
    k = _seite(seite_name, teams)
    p = preise.get(seite_name)
    if not k or not p or p <= 0.03 or max(preise.values()) > MAX_PREIS:
        return None
    name = {"heim": g["heim"], "gast": g["gast"], "remis": "Remis"}[k]
    pr = {(g["heim"] if _seite(n, teams) == "heim" else g["gast"] if _seite(n, teams) == "gast" else "Remis"): v
          for n, v in preise.items()}
    return {"usd": round(usd), "seite": name, "k": k, "spiel": f"{g['heim']} – {g['gast']}",
            "liga": re.sub(r"^UEFA |^English Sky Bet ", "", str(g.get("liga") or "")), "anpfiff": m.get("anpfiff"),
            "preis": p, "preise": pr, "wallet": wallet, "bfId": str(g["id"]), "polyKey": m.get("key"),
            "bewiesen": rang is not None, "rang": rang}


def _toepfe(d):
    t = {}
    for g in d.get("toepfe") or []:
        k = ((g.get("q") or {}).get("poly") or {}).get("key")
        if k and not str(g.get("id", "")).startswith(("pm:", "st:")):
            t[k] = g
    return t


def auswahl(d, jetzt):
    """Hauptstory = immer der größte Einsatz (Lucas 05.11. 19:46: „sonst siehst du die Riesen-Einsätze nicht mehr").
    Ist das Wallet auf unserer Rangliste, bekommt die Story den ⭐-Hinweis."""
    w = _groesster(d, jetzt)
    if w:
        rang = {x.get("wallet"): i + 1 for i, x in enumerate((d.get("poly") or {}).get("wallets") or []) if x.get("bewiesen")}
        if w["wallet"] in rang:
            w.update(bewiesen=True, rang=rang[w["wallet"]])
    return w


def profi(d, jetzt, haupt=None):
    """Zweite Story „Profi des Abends": größter Einsatz (≥ $10k) eines Ranglisten-Wallets — nur wenn es nicht
    ohnehin die Hauptstory ist (Lucas 05.11. 19:45/19:46)."""
    maerkte = {m.get("key"): m for m in (d.get("poly") or {}).get("maerkte") or []}
    b = _bewiesen(d, jetzt, _toepfe(d), maerkte)
    if not b:
        return None
    w = _bauen(b[0], b[1].get("seite"), b[1].get("wallet"), b[2], b[3], b[1].get("rang"))
    if not w or (haupt and haupt.get("wallet") == w["wallet"] and haupt.get("polyKey") == w["polyKey"]):
        return None
    return w


def _groesster(d, jetzt):
    toepfe = {}
    for g in d.get("toepfe") or []:
        k = ((g.get("q") or {}).get("poly") or {}).get("key")
        if k and not str(g.get("id", "")).startswith(("pm:", "st:")):
            toepfe[k] = g
    beste = None
    for m in (d.get("poly") or {}).get("maerkte") or []:
        if (m.get("sport") or "Fußball") != "Fußball":
            continue
        ko = _ts(m.get("anpfiff"))
        if not ko or not (jetzt < ko <= jetzt + timedelta(hours=FENSTER_H)):
            continue
        preise = m.get("preise") or {}
        if not preise or max(preise.values()) > MAX_PREIS:
            continue
        g = toepfe.get(m.get("key"))
        if not g:
            continue
        teams = [n for n in preise if not str(n).lower().startswith("draw")]
        for w in m.get("wale") or []:
            usd = float(w.get("usd") or 0)
            k = _seite(w.get("seite"), teams)
            p = preise.get(w.get("seite"))
            if usd < MIN_USD or not k or not p or p <= 0.03:
                continue
            if beste is None or usd > beste["usd"]:
                name = {"heim": g["heim"], "gast": g["gast"], "remis": "Remis"}[k]
                pr = {(g["heim"] if _seite(n, teams) == "heim" else g["gast"] if _seite(n, teams) == "gast" else "Remis"): v
                      for n, v in preise.items()}
                beste = {"usd": round(usd), "seite": name, "k": k, "spiel": f"{g['heim']} – {g['gast']}",
                         "liga": re.sub(r"^UEFA |^English Sky Bet ", "", str(g.get("liga") or "")), "anpfiff": m.get("anpfiff"),
                         "preis": p, "preise": pr, "wallet": w.get("wallet"), "bfId": str(g["id"]), "polyKey": m.get("key"),
                         "bewiesen": bool(w.get("bewiesen"))}
    return beste


def ergebnis(d, w):
    """→ (gewonnen|verloren, '2:1') oder None, solange kein Endstand da ist."""
    ft = ((d.get("endstaende") or {}).get(w["bfId"]) or {}).get("ft")
    if not ft or len(ft) != 2:
        return None
    h, a = ft
    sieger = "heim" if h > a else "gast" if a > h else "remis"
    return ("gewonnen" if sieger == w["k"] else "verloren"), f"{h}:{a}"
