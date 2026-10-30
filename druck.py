"""Druck-Radar — stille Messung (Lucas 25.10.2026: „Spiele, wo es wirklich zur Sache geht … hoher xG-Wert und wenig Tore").

Vorab festgelegt (25.10., 12:15 Wien), damit die Auswertung nicht nachträglich passend geschnitten wird:
  • Ins Buch kommt jedes Live-Spiel beim ERSTEN Mal, wenn sein Druck-Index (live_stats.druck_von) 70 erreicht —
    bis zur 80. Minute, einmal je Spiel.
  • Gewettet (auf dem Papier) wird „noch ein Tor": Über (Tore jetzt + 0,5) im Betfair-Markt, zur Quote in diesem
    Moment, abgerechnet wie die Pushes (2 % Kommission). Ohne Quote (Markt fehlt / unter 1,20 / über 6) kein Eintrag.
  • Ziel 100 abgerechnete. Getrennt ausgewertet: Index 70–84 / ab 85, xG gemessen / geschätzt, Druck-Team hinten /
    gleich / vorn, mit / ohne frisches Geld, 1. / 2. Halbzeit, Ligagröße.
Kein Push — nur Seite und Buch.

30.10. (Lucas: „Druck ist gut vor allem für Next Goal — ich spiele nicht immer klassisch 1X2"): zusätzlich wird je
Eintrag mitgeschrieben, WER das nächste Tor macht — das Druck-Team, der Gegner oder keiner (bis Abpfiff). Bestimmt aus
dem Live-Stand je Lauf (alle ~5 Min); ändern sich beide Seiten im selben Intervall, ist es „unklar" und zählt nicht.
Ohne Next-Goal-Quote (Betwatch führt den Markt nicht): ausgewiesen wird die Trefferquote, keine Rendite.
"""
from __future__ import annotations

import json
from pathlib import Path

PREISE = Path(__file__).resolve().parent / ".bf" / "betfair_prices.json"
AB_INDEX = 70
BIS_MIN = 80


def _quote(preise, mid, linie):
    m = next((x for x in (preise or {}).get("matches") or [] if str(x.get("matchId")) == str(mid)), None)
    mk = ((m or {}).get("markets") or {}).get(f"Over/Under {linie} Goals") or {}
    r = next((r for r in mk.get("runners") or [] if isinstance(r, dict) and r.get("name") == f"Over {linie} Goals"), None)
    q = (r or {}).get("odd")
    return q if isinstance(q, (int, float)) and 1.20 <= q <= 6 else None


def eintragen(d, buch, jetzt):
    try:
        preise = json.loads(PREISE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return 0
    radar = {str(s.get("id")): s for s in (d.get("radar") or {}).get("spiele") or []}
    schon = {str(b.get("id")) for b in buch if b.get("art") == "druck"}
    neu = 0
    for x in (d.get("druck") or {}).get("spiele") or []:
        i = str(x.get("id"))
        if i in schon or (x.get("index") or 0) < AB_INDEX or not isinstance(x.get("min"), (int, float)) or x["min"] > BIS_MIN:
            continue
        st = x.get("stand") or [0, 0]
        if st[0] is None or st[1] is None:
            continue
        linie = f"{st[0] + st[1]}.5"
        q = _quote(preise, i, linie)
        if not q:
            continue
        sp = radar.get(i) or {}
        s = x["seite"]
        eig, geg = st[0 if s == "heim" else 1], st[1 if s == "heim" else 0]
        buch.append({"art": "druck", "id": i, "heim": x.get("heim"), "gast": x.get("gast"), "liga": x.get("liga"),
                     "land": x.get("land"), "anpfiff": sp.get("anpfiff"), "live": True, "top5": bool(x.get("top5")),
                     "normEur": sp.get("normEur"), "ligaEur": sp.get("ligaEur"),
                     "markt": f"Over/Under {linie} Goals", "auswahl": f"Over {linie} Goals", "quote": q,
                     "index": x["index"], "seite": s, "min": x["min"], "stand": st, "quelle": x.get("quelle"),
                     "lage": "hinten" if eig < geg else "gleich" if eig == geg else "vorn",
                     "geld": bool(x.get("zufluss")), "xgTeam": x["teams"][s]["xg"],
                     "ts": jetzt.isoformat(), "status": "offen", "regel": 3})   # Regel 2 ab 25.10. 17:20: Tore im Fenster abgezogen · Regel 3 ab 30.10.: xG-Reihe einheitlich (nie echt und geschätzt gemischt)
        schon.add(i)
        neu += 1
    return neu


def naechstes_tor(d, buch):
    """Für offene Druck-Einträge: erstes Tor nach dem Eintrag aus dem aktuellen Live-Stand. REIN bis auf das Buch."""
    radar = {str(s.get("id")): s for s in (d.get("radar") or {}).get("spiele") or []}
    n = 0
    for b in buch:
        if b.get("art") != "druck" or b.get("naechstes"):
            continue
        st0 = b.get("stand") or [0, 0]
        sp = radar.get(str(b.get("id")))
        jetzt = ((sp or {}).get("live") or {}).get("stand")
        if not jetzt and b.get("status") == "abgerechnet":
            ft = b.get("ft") if isinstance(b.get("ft"), list) else None
            if ft and (ft[0] or 0) + (ft[1] or 0) == (st0[0] or 0) + (st0[1] or 0):
                b["naechstes"] = "keins"
                n += 1
            elif ft:
                d0, d1 = (ft[0] or 0) - (st0[0] or 0), (ft[1] or 0) - (st0[1] or 0)
                if (d0 > 0) != (d1 > 0):   # nur eine Seite hat danach getroffen → eindeutig, auch ohne Zwischenstand
                    k = "heim" if d0 > 0 else "gast"
                    b["naechstes"] = "eigen" if k == b.get("seite") else "gegner"
                    n += 1
                elif b.get("status") == "abgerechnet":
                    b["naechstes"] = "unklar"
                    n += 1
            continue
        if not jetzt or jetzt[0] is None or jetzt[1] is None:
            continue
        d0, d1 = jetzt[0] - (st0[0] or 0), jetzt[1] - (st0[1] or 0)
        if d0 <= 0 and d1 <= 0:
            continue
        if d0 > 0 and d1 > 0:
            b["naechstes"] = "unklar"
        else:
            k = "heim" if d0 > 0 else "gast"
            b["naechstes"] = "eigen" if k == b.get("seite") else "gegner"
        b["naechstesMin"] = ((sp or {}).get("live") or {}).get("min")
        n += 1
    return n
