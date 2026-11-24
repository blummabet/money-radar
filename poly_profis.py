"""Regel W3 „Sport-Profis" (24.11., Lucas: „du kannst mir nicht erzählen, dass wir auf einem der größten Prediction-
Märkte der Welt nur 5 Wallets finden, die nachweislich im Plus sind").

Die W2-Liste kennt nur Wallets, die wir selbst als große Halter in unseren Märkten gesehen haben, und nur deren Wetten
seit Ende Oktober. Hier der Weg von der anderen Seite:
  1. Kandidaten: Polymarket-Bestenliste /v2/leaderboard (Kategorie sports und esports, Zeitraum Monat und gesamt,
     nach Gewinn) — alle 12 h, je bis 500 Plätze.
  2. Je Kandidat die abgeschlossenen Positionen der letzten 90 Tage (/v2/positions, CLOSED + REDEEMABLE +
     REDEEMABLE_LOST — sonst fehlen nicht eingelöste Verluste und die Rendite sähe zu gut aus), nur unsere Sportarten
     (Fußball, E-Sport, Tennis ohne ATP/WTA). Je Lauf höchstens PROFI_JE_LAUF Wallets, jede alle 3 Tage neu.
  3. Belegt (W3): mindestens 50 Positionen in unseren Sportarten, Rendite (Gewinn ÷ Einsatz) über null UND die
     Untergrenze der Rendite je Wette (z = 1,645) über null.
Die Sportart einer Position kommt aus dem Slug-Präfix: E-Sport/Tennis fest, Fußball-Präfixe lernt die Datei aus den
Märkten, die poly_markt sieht (z. B. „den-", „tur-", „ukr1-"). Unbekannte Präfixe zählen nicht.
"""
from __future__ import annotations

import json
import math
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import poly_v2

DATEI = Path(__file__).resolve().parent / ".pm" / "poly_profis.json"
JETZT = datetime.now(timezone.utc)
BOARD_H = 12
NEU_TAGE = 3
PROFI_JE_LAUF = 10
FENSTER_TAGE = 90
MIN_N = 50
Z = 1.645
ESPORT = ("cs2", "csgo", "lol", "dota2", "dota", "val", "valorant", "ow", "r6", "rl", "cod", "mlbb", "hok")
TENNIS = ("itf", "chall", "wtt")
RAUS = ("atp", "wta", "nba", "nfl", "mlb", "nhl", "ncaa", "cbb", "cfb", "wnba", "ufc", "mma", "f1", "nascar", "pga", "golf")
_PRAEFIX = re.compile(r"^([a-z0-9]+)-")


def praefix(slug):
    m = _PRAEFIX.match(str(slug or "").lower())
    return m.group(1) if m else None


def sportart(slug, fuss):
    p = praefix(slug)
    if not p or p in RAUS:
        return None
    if p in ESPORT:
        return "E-Sport"
    if p in TENNIS:
        return "Tennis"
    return "Fußball" if p in fuss else None


def wertung(zeilen, fuss, ab_ts):
    """Positionen → {n, einsatz, gewinn, wins, uSum, uSq, bySport}. Je Bedingung (condition_id+outcome) einmal. REIN."""
    gesehen, out = set(), {"n": 0, "einsatz": 0.0, "gewinn": 0.0, "wins": 0, "uSum": 0.0, "uSq": 0.0, "bySport": {}}
    for z in zeilen or []:
        if not isinstance(z, dict):
            continue
        k = (z.get("condition_id") or z.get("conditionId"), z.get("outcome_index", z.get("outcomeIndex")))
        if k in gesehen:
            continue
        gesehen.add(k)
        t = z.get("last_event_at") or z.get("first_entry_at")
        if isinstance(t, (int, float)) and t < ab_ts:
            continue
        sp = sportart(z.get("event_slug") or z.get("eventSlug") or z.get("slug"), fuss)
        if not sp:
            continue
        kosten = z.get("total_cost_usdc", z.get("entry_cost_usdc"))
        g = z.get("total_pnl", z.get("realized_pnl"))
        if not isinstance(kosten, (int, float)) or not isinstance(g, (int, float)) or kosten < 5:
            continue
        u = max(-1.0, min(20.0, g / kosten))
        out["n"] += 1
        out["einsatz"] += kosten
        out["gewinn"] += g
        out["wins"] += g > 0
        out["uSum"] += u
        out["uSq"] += u * u
        b = out["bySport"].setdefault(sp, [0, 0.0, 0.0])
        b[0] += 1
        b[1] += kosten
        b[2] += g
    for k in ("einsatz", "gewinn", "uSum", "uSq"):
        out[k] = round(out[k], 3)
    return out


def urteil(w):
    """→ (belegt, rendite %, untergrenze %) für eine Wertung. REIN."""
    n = w.get("n") or 0
    if n < 2 or not w.get("einsatz"):
        return False, None, None
    rendite = 100 * w["gewinn"] / w["einsatz"]
    m = w["uSum"] / n
    v = max(0.0, (w["uSq"] - n * m * m) / (n - 1))
    ug = 100 * (m - Z * math.sqrt(v / n))
    return bool(n >= MIN_N and rendite > 0 and ug > 0), round(rendite, 1), round(ug, 1)


def lauf(get, maerkte, stat, zeit_ok=lambda: True):
    """Ein Schritt je poly_markt-Lauf. get = poly_markt._get. → Zahl der Abrufe."""
    try:
        d = json.loads(DATEI.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        d = {}
    fuss = set(d.get("fussPraefixe") or [])
    for k, m in (maerkte or {}).items():          # Fußball-Präfixe lernen
        if (m or {}).get("sport") == "Fußball" and praefix(k):
            fuss.add(praefix(k))
    abrufe = 0
    board = d.get("board") or {}
    alt = lambda t, h: not t or JETZT - datetime.fromisoformat(t) > timedelta(hours=h)   # noqa: E731
    if alt(board.get("ts"), BOARD_H):
        kand = {}
        for cat in ("sports", "esports"):
            for per in ("month", "all"):
                rows = poly_v2.seiten(get, "leaderboard", 1, category=cat, time_period=per, sort_by="PNL", limit=500)
                abrufe += 1
                for r in rows or []:
                    w = str(r.get("user_id") or r.get("proxyWallet") or "").lower()
                    if not w.startswith("0x") or not isinstance(r.get("pnl"), (int, float)) or r["pnl"] <= 0:
                        continue
                    e = kand.setdefault(w, {"name": r.get("user_name") or r.get("userName")})
                    e[f"{cat[:2]}_{per}"] = round(r["pnl"])
        if kand:
            board = {"ts": JETZT.isoformat(), "kandidaten": kand}
        stat["profiBoard"] = len(kand)
    kand = board.get("kandidaten") or {}
    wallets = d.get("wallets") or {}
    reihe = sorted(kand, key=lambda w: (wallets.get(w) or {}).get("ts") or "")
    ab_ts = (JETZT - timedelta(days=FENSTER_TAGE)).timestamp()
    gemacht = 0
    for w in reihe:
        if gemacht >= PROFI_JE_LAUF or not zeit_ok():
            break
        if not alt((wallets.get(w) or {}).get("ts"), NEU_TAGE * 24):
            continue
        zeilen, fehler = [], False
        for st in ("CLOSED", "REDEEMABLE", "REDEEMABLE_LOST"):
            r = poly_v2.seiten(get, "positions", 3, user=w, status=st, sort_by="TIMESTAMP", limit=500,
                               start=int(ab_ts))
            abrufe += 1
            if r is None:
                fehler = True
                break
            zeilen += r
        if fehler:
            continue
        if not stat.get("profiProbe") and zeilen:
            stat["profiProbe"] = sorted(zeilen[0].keys())[:40]
        wt = wertung(zeilen, fuss, ab_ts)
        bel, ren, ug = urteil(wt)
        wallets[w] = {**wt, "ts": JETZT.isoformat(), "rendite": ren, "renditeUg": ug, "belegt": bel,
                      "name": (kand.get(w) or {}).get("name"), "board": {k: v for k, v in (kand.get(w) or {}).items() if k != "name"},
                      "roh": len(zeilen)}
        gemacht += 1
    # Wallets, die aus der Bestenliste fallen, nach 30 Tagen vergessen
    wallets = {w: v for w, v in wallets.items() if w in kand or not alt(v.get("ts"), 30 * 24)}
    d = {"board": board, "wallets": wallets, "fussPraefixe": sorted(fuss)[:2000], "ts": JETZT.isoformat()}
    DATEI.parent.mkdir(exist_ok=True)
    DATEI.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    stat["profiGeprueft"] = gemacht
    stat["profiBelegt"] = sum(1 for v in wallets.values() if v.get("belegt"))
    stat["profiBekannt"] = len(wallets)
    return abrufe
