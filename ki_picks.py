#!/usr/bin/env python3
"""ki_picks.py — AI-Picks (TEST, Lucas 10.11.2026 09:26).

Lucas: „schau dir zuerst nur die Live-Statistik an, mach daraus einen Tipp — daraus kann man ja Quoten berechnen — und
schau erst dann, ob das Geld übereinstimmt. Dann ist es ein Pick." Dazu: „nimm die Startquoten auf jeden Fall mit".
„Nur ein Test — nichts verschlechtern, langsamer machen oder kaputt machen." → eigener Schritt nach live_stats.py,
Fehler werden geschluckt, nichts wird gesendet, kein Public.

REGEL A1 (vorab festgelegt, 10.11.2026 ~10:00 Wien):
  Modell (blind fürs Geld):
    • Vorwissen = Startquote: letzte Betfair-Quote vor Anpfiff (Sieg + Über/Unter 2,5) → erwartete Tore je Team (Poisson).
    • Live: Rest-Erwartung = Mischung aus Vorwissen (anteilig Restzeit) und Live-xG-Rate; Gewicht der Live-Daten
      w = Minute/(Minute+45), bei geschätztem xG (aus Schüssen) ×0,6.
    • Spielstand: wer hinten liegt ×1,12 / Gegner ×0,95; wer führt ×0,92 / Gegner ×1,05. Rote Karte: ×0,75 / Gegner ×1,2.
    • Daraus faire Quoten für Sieg/Remis/Niederlage (Match Odds) und alle offenen Über/Unter-Linien.
  Pick (art „ki"): 15.–80. Minute, Live-Statistik ≤ 15 Min alt, Startquote vorhanden, Spiel nicht entschieden,
    Marktquote 1,25–8,0, Wert = Modell-Wahrscheinlichkeit × Quote − 1 ≥ 8 % und ≥ 3 Prozentpunkte über der Quote,
    UND das Geld geht mit: im letzten Fenster ≥ €500 neues Geld auf genau diese Auswahl und ≥ 50 % des neuen Geldes
    im Markt. Je Spiel höchstens ein Pick (der erste zählt), der mit dem größten Wert.
  Mitgemessen (still, art „kiModell"): jeder Modell-Pick, egal ob Geld mitgeht — zeigt, ob das Geld etwas dazugibt.
  Gebucht zur Marktquote beim Pick, Abrechnung wie die Betfair-Pushes (bilanz.py). Ziel 100 abgerechnete „ki".
  „Nächstes Tor" gibt es bei Betfair in unseren Daten nicht → als „noch ein Tor" = Über (Tore + 0,5).
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bilanz

BASIS = Path(__file__).resolve().parent
DATEN = BASIS / "site" / "data.json"
PREISE = BASIS / ".bf" / "betfair_prices.json"
START = BASIS / ".push_state" / "ki_start.json"
JETZT = datetime.now(timezone.utc)

REGEL = "A1"
MIN_VON, MIN_BIS = 15, 80
WERT_MIN, PP_MIN = 0.08, 0.03
Q_MIN, Q_MAX = 1.25, 8.0
GELD_MIN, GELD_ANTEIL = 500, 0.50
STATS_MAX_MIN = 15
SPIELZEIT = 94                      # inkl. Nachspielzeit


def _ts(s):
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def _laden(p, default):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


# ── Poisson ──────────────────────────────────────────────────────────────────────
def _pois(lam, n=12):
    lam = max(lam, 1e-6)
    p, out = math.exp(-lam), []
    for k in range(n):
        out.append(p)
        p = p * lam / (k + 1)
    return out


def verteilung(lh, la):
    """Wahrscheinlichkeiten für (weitere Heimtore, weitere Gasttore). REIN."""
    ph, pa = _pois(lh), _pois(la)
    return [[ph[i] * pa[j] for j in range(len(pa))] for i in range(len(ph))]


def lam_aus_ou25(p_over):
    """Erwartete Tore gesamt, sodass P(≥3 Tore) = p_over. REIN."""
    lo, hi = 0.2, 6.0
    for _ in range(50):
        m = (lo + hi) / 2
        pz = _pois(m, 3)
        if 1 - sum(pz) < p_over:
            lo = m
        else:
            hi = m
    return (lo + hi) / 2


def split_aus_sieg(lam, p_heim, p_gast):
    """Anteil Heim an den erwarteten Toren, sodass P(Heim) − P(Gast) wie am Markt. REIN."""
    ziel = p_heim - p_gast
    lo, hi = 0.05, 0.95
    for _ in range(40):
        s = (lo + hi) / 2
        v = verteilung(lam * s, lam * (1 - s))
        ph = sum(v[i][j] for i in range(len(v)) for j in range(len(v)) if i > j)
        pg = sum(v[i][j] for i in range(len(v)) for j in range(len(v)) if j > i)
        if ph - pg < ziel:
            lo = s
        else:
            hi = s
    return (lo + hi) / 2


def _fair(quoten):
    inv = {k: 1 / q for k, q in quoten.items() if isinstance(q, (int, float)) and q > 1}
    s = sum(inv.values())
    return {k: v / s for k, v in inv.items()} if s else {}


def vorwissen(st):
    """Startquoten → (λ Heim, λ Gast) fürs ganze Spiel. REIN."""
    mo = _fair({"h": st.get("hw"), "x": st.get("dr"), "a": st.get("aw")})
    ou = _fair({"o": st.get("o25"), "u": st.get("u25")})
    if len(mo) != 3:
        return None
    lam = lam_aus_ou25(ou["o"]) if len(ou) == 2 else 2.6
    s = split_aus_sieg(lam, mo["h"], mo["a"])
    return lam * s, lam * (1 - s)


def modell(st, minute, stand, xg, xg_echt, rot):
    """Restliche erwartete Tore je Team. REIN."""
    pr = vorwissen(st)
    if not pr:
        return None
    rest = max(2.0, SPIELZEIT - minute) / 90.0
    lam = [pr[0] * rest, pr[1] * rest]
    if xg and all(isinstance(x, (int, float)) for x in xg) and minute >= 10:
        w = minute / (minute + 45.0) * (1.0 if xg_echt else 0.6)
        restmin = max(2.0, SPIELZEIT - minute)
        live = [xg[0] / minute * restmin, xg[1] / minute * restmin]
        lam = [(1 - w) * lam[i] + w * live[i] for i in (0, 1)]
    d = (stand[0] or 0) - (stand[1] or 0)
    if d > 0:
        lam = [lam[0] * 0.92, lam[1] * 1.12]
    elif d < 0:
        lam = [lam[0] * 1.12, lam[1] * 0.92]
    if rot and all(isinstance(r, (int, float)) for r in rot) and rot[0] != rot[1]:
        if rot[0] > rot[1]:
            lam = [lam[0] * 0.75, lam[1] * 1.2]
        else:
            lam = [lam[0] * 1.2, lam[1] * 0.75]
    return lam


def wahrscheinlichkeiten(lam, stand):
    """{('Match Odds','heim'|'remis'|'gast'): p, ('OU', linie, 'over'|'under'): p}. REIN."""
    v = verteilung(*lam)
    h0, a0 = stand[0] or 0, stand[1] or 0
    out = {("mo", "heim"): 0.0, ("mo", "remis"): 0.0, ("mo", "gast"): 0.0}
    tore = {}
    for i in range(len(v)):
        for j in range(len(v)):
            p = v[i][j]
            H, A = h0 + i, a0 + j
            out[("mo", "heim" if H > A else "gast" if A > H else "remis")] += p
            tore[H + A] = tore.get(H + A, 0.0) + p
    for linie in [x + 0.5 for x in range(0, 9)]:
        if linie < h0 + a0:
            continue
        po = sum(p for t, p in tore.items() if t > linie)
        out[("ou", linie, "over")] = po
        out[("ou", linie, "under")] = 1 - po
    return out


# ── Daten ────────────────────────────────────────────────────────────────────────
def startquoten_fortschreiben(preise, start):
    """Vor Anpfiff: letzte Sieg- und Über/Unter-2,5-Quote je Spiel merken (wird überschrieben bis zum Anpfiff)."""
    for m in preise.get("matches") or []:
        ko = _ts(m.get("kickoff"))
        li = m.get("liveInfo") or {}
        if not ko or JETZT >= ko or li.get("time") or li.get("goal_v1") is not None:
            continue
        mo = m.get("mo") or {}
        ou = {r.get("name"): r.get("odd") for r in ((m.get("markets") or {}).get("Over/Under 2.5 Goals") or {}).get("runners") or []}
        if not all(isinstance(mo.get(k), (int, float)) for k in ("hw", "dr", "aw")):
            continue
        start[str(m.get("matchId"))] = {"hw": mo["hw"], "dr": mo["dr"], "aw": mo["aw"], "o25": ou.get("Over 2.5 Goals"),
                                        "u25": ou.get("Under 2.5 Goals"), "ko": m.get("kickoff"), "t": JETZT.isoformat()}
    grenze = JETZT - timedelta(days=2)
    return {k: v for k, v in start.items() if (_ts(v.get("ko")) or JETZT) >= grenze}


def _quoten(m, heim, gast):
    """Marktquoten {schlüssel: (Markt, Auswahl, Quote)} aus dem Betfair-Abruf."""
    out = {}
    mk = m.get("markets") or {}
    for r in (mk.get("Match Odds") or {}).get("runners") or []:
        n, q = r.get("name"), r.get("odd")
        k = "heim" if n == heim else "gast" if n == gast else "remis" if n == "The Draw" else None
        if k and isinstance(q, (int, float)):
            out[("mo", k)] = ("Match Odds", n, q)
    for name, v in mk.items():
        if not name.startswith("Over/Under ") or not name.endswith(" Goals"):
            continue
        try:
            linie = float(name.split()[1])
        except (IndexError, ValueError):
            continue
        for r in v.get("runners") or []:
            n, q = str(r.get("name") or ""), r.get("odd")
            if isinstance(q, (int, float)) and n.lower().startswith(("over", "under")):
                out[("ou", linie, "over" if n.lower().startswith("over") else "under")] = (name, n, q)
    return out


def _geld(sp, markt, auswahl):
    """Neues Geld (letztes Fenster) auf genau diese Auswahl und ihr Anteil am neuen Geld im Markt."""
    for m in sp.get("maerkteDetail") or []:
        if m.get("name") != markt:
            continue
        aa = [a for a in m.get("auswahl") or [] if a.get("name") != "Rest"]
        ges = sum(max(0, a.get("neu") or 0) for a in aa)
        a = next((x for x in aa if x.get("name") == auswahl), None)
        if a:
            neu = max(0, a.get("neu") or 0)
            return neu, (neu / ges if ges else 0.0)
    return 0.0, 0.0


AUSW_DE = {"heim": None, "gast": None, "remis": "Remis"}


def _text(sp, k, auswahl):
    if k[0] == "mo":
        return f"{sp.get('heim') if k[1] == 'heim' else sp.get('gast') if k[1] == 'gast' else 'Remis'} gewinnt" if k[1] != "remis" else "Remis"
    return f"{'Über' if k[2] == 'over' else 'Unter'} {str(k[1]).replace('.', ',')} Tore"


def kandidaten(d, preise, start):
    """Alle Live-Spiele bewerten. Gibt (Kandidaten, Info) zurück — Kandidaten mit Feld 'geldOk'."""
    pm = {str(m.get("matchId")): m for m in preise.get("matches") or []}
    ls = d.get("liveStats") or {}
    info = {"live": 0, "mitStatistik": 0, "mitStartquote": 0, "imFenster": 0, "bewertet": 0}
    aus, blick = [], []
    for sp in (d.get("radar") or {}).get("spiele") or []:
        lv = sp.get("live") or {}
        if not lv:
            continue
        info["live"] += 1
        sid = str(sp.get("id"))
        e = ls.get(sid) or {}
        st = start.get(sid)
        if st:
            info["mitStartquote"] += 1
        if e:
            info["mitStatistik"] += 1
        minute, stand = lv.get("min"), lv.get("stand")
        if lv.get("ht"):
            minute = 45
        if not isinstance(minute, (int, float)) or not stand or None in stand:
            continue
        kopf = {"id": sid, "heim": sp.get("heim"), "gast": sp.get("gast"), "liga": sp.get("liga"), "land": sp.get("land"),
                "minute": int(minute), "stand": list(stand)}
        if sp.get("entschieden") or minute > MIN_BIS:
            continue
        stats_ok = bool(e and _ts(e.get("ts")) and JETZT - _ts(e["ts"]) <= timedelta(minutes=STATS_MAX_MIN))
        if minute < MIN_VON or not st or not stats_ok:
            # 10.11. (Lucas: „mitfiebern, bevor er eingetütet wird"): auch zeigen, was noch fehlt
            blick.append({**kopf, "fehlt": [x for x, ok in (("ab der 15. Minute", minute >= MIN_VON),
                                                           ("Startquote (Spiel lief schon vor dem Test)", bool(st)),
                                                           ("frische Live-Statistik", stats_ok)) if not ok]})
            continue
        info["imFenster"] += 1
        dr = e.get("druck") or {}
        tm = dr.get("teams") or {}
        xg = [(tm.get("heim") or {}).get("xg"), (tm.get("gast") or {}).get("xg")]
        stat = e.get("statistik") or {}
        rot = [(stat.get("heim") or {}).get("rot"), (stat.get("gast") or {}).get("rot")]
        lam = modell(st, float(minute), stand, xg, dr.get("quelle") == "xG", rot)
        m = pm.get(sid)
        if not lam or not m:
            continue
        info["bewertet"] += 1
        ps = wahrscheinlichkeiten(lam, stand)
        qs = _quoten(m, sp.get("heim"), sp.get("gast"))
        best = None
        naechster = None                               # bester Wert, auch unter der Schwelle — für „Im Blick"
        for k, p in ps.items():
            if k not in qs:
                continue
            markt, auswahl, q = qs[k]
            if not (Q_MIN <= q <= Q_MAX) or p <= 0:
                continue
            wert = p * q - 1
            if not naechster or wert > naechster[0]:
                naechster = (wert, k, markt, auswahl, q, p)
            if wert < WERT_MIN or p - 1 / q < PP_MIN:
                continue
            if not best or wert > best["wert"]:
                neu, anteil = _geld(sp, markt, auswahl)
                best = {"id": sid, "heim": sp.get("heim"), "gast": sp.get("gast"), "liga": sp.get("liga"), "land": sp.get("land"),
                        "anpfiff": sp.get("anpfiff"), "top5": bool(sp.get("top5")), "live": True,
                        "markt": markt, "auswahl": auswahl, "tipp": _text(sp, k, auswahl), "quote": q,
                        "p": round(p, 4), "fair": round(1 / p, 2), "mindest": round(1.05 / p, 2), "wert": round(wert, 4),
                        "minute": int(minute), "stand": list(stand), "xg": xg, "xgQuelle": dr.get("quelle"), "rot": rot,
                        "lamRest": [round(lam[0], 2), round(lam[1], 2)],
                        "startTore": round(sum(vorwissen(st)), 2), "geldEur": round(neu), "geldAnteil": round(100 * anteil),
                        "geldOk": neu >= GELD_MIN and anteil >= GELD_ANTEIL}
        if best:
            aus.append(best)
        if naechster:
            wert, k, markt, auswahl, q, p = naechster
            neu, anteil = _geld(sp, markt, auswahl)
            blick.append({**kopf, "tipp": _text(sp, k, auswahl), "quote": q, "fair": round(1 / p, 2), "wert": round(wert, 4),
                          "pp": round(100 * (p - 1 / q), 1), "geldEur": round(neu), "geldAnteil": round(100 * anteil),
                          "wertOk": wert >= WERT_MIN and p - 1 / q >= PP_MIN,
                          "geldOk": neu >= GELD_MIN and anteil >= GELD_ANTEIL,
                          "xg": xg, "xgQuelle": dr.get("quelle"), "lamRest": [round(lam[0], 2), round(lam[1], 2)]})
    blick.sort(key=lambda x: (x.get("wert") is None, -(x.get("wert") or 0)))
    info["blick"] = blick[:10]
    return aus, info


def buchen(buch, kands):
    """Je Spiel nur der erste Pick je Art. Gibt die neu gebuchten zurück."""
    da = {(b.get("art"), str(b.get("id"))) for b in buch if b.get("art") in ("ki", "kiModell")}
    neu = []
    for k in kands:
        felder = {x: v for x, v in k.items() if x != "geldOk"}
        for art in (["kiModell", "ki"] if k["geldOk"] else ["kiModell"]):
            if (art, k["id"]) in da:
                continue
            b = {**felder, "art": art, "regel": REGEL}
            bilanz.eintragen(buch, b, JETZT)
            da.add((art, k["id"]))
            if art == "ki":
                neu.append(b)
    return neu


def _bilanz(buch, art):
    ab = [b for b in buch if b.get("art") == art and b.get("status") == "abgerechnet" and isinstance(b.get("r"), (int, float))]
    n = len(ab)
    return {"n": sum(1 for b in buch if b.get("art") == art), "abgerechnet": n,
            "offen": sum(1 for b in buch if b.get("art") == art and b.get("status") == "offen"),
            "roi": round(100 * sum(b["r"] for b in ab) / n, 1) if n else None,
            "treffer": round(100 * sum(1 for b in ab if b.get("win")) / n) if n else None,
            "erwartet": round(100 * sum(1 / b["quote"] for b in ab if b.get("quote")) / n) if n else None}


def main() -> int:
    d = _laden(DATEN, None)
    if not d:
        print("ki_picks: keine data.json")
        return 0
    preise = _laden(PREISE, {})
    start = startquoten_fortschreiben(preise, _laden(START, {}))
    START.parent.mkdir(parents=True, exist_ok=True)
    START.write_text(json.dumps(start, separators=(",", ":")), encoding="utf-8")
    kands, info = kandidaten(d, preise, start)
    buch = bilanz.laden()
    neu = buchen(buch, kands)
    bilanz.schreiben(buch, JETZT)
    grenze = JETZT - timedelta(hours=24)
    heute = [{k: b.get(k) for k in ("id", "heim", "gast", "liga", "land", "markt", "auswahl", "tipp", "quote", "fair", "mindest", "wert",
                                     "minute", "stand", "xg", "xgQuelle", "startTore", "lamRest", "geldEur", "geldAnteil", "ts",
                                     "status", "win", "ft")}
             for b in reversed(buch) if b.get("art") == "ki" and _ts(b.get("ts")) and _ts(b["ts"]) >= grenze][:12]
    blick = info.pop("blick", [])
    d["kiPicks"] = {"stand": JETZT.isoformat(), "regel": REGEL, "info": info, "heute": heute, "blick": blick,
                    "schwelle": {"wert": WERT_MIN, "geld": GELD_MIN, "anteil": GELD_ANTEIL},
                    "kandidaten": [{k: v for k, v in x.items()} for x in kands][:8],
                    "bilanz": {"ki": _bilanz(buch, "ki"), "kiModell": _bilanz(buch, "kiModell")}}
    DATEN.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"ki_picks: {info} · Kandidaten {len(kands)} · neu gebucht {len(neu)} · Startquoten {len(start)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001  — Test: darf nie etwas anderes aufhalten
        print(f"ki_picks: Fehler {exc}")
        raise SystemExit(0)
