"""Druck-Radar — stille Messung (Lucas 25.10.2026: „Spiele, wo es wirklich zur Sache geht … hoher xG-Wert und wenig Tore").

Vorab festgelegt (25.10., 12:15 Wien), damit die Auswertung nicht nachträglich passend geschnitten wird:
  • Ins Buch kommt jedes Live-Spiel beim ERSTEN Mal, wenn sein Druck-Index (live_stats.druck_von) 70 erreicht —
    bis zur 80. Minute, einmal je Spiel.
  • Gewettet (auf dem Papier) wird „noch ein Tor": Über (Tore jetzt + 0,5) im Betfair-Markt, zur Quote in diesem
    Moment, abgerechnet wie die Pushes (2 % Kommission). Ohne Quote (Markt fehlt / unter 1,20 / über 6) kein Eintrag.
  • Ziel 100 abgerechnete. Getrennt ausgewertet: Index 70–84 / ab 85, xG gemessen / geschätzt, Druck-Team hinten /
    gleich / vorn, mit / ohne frisches Geld, 1. / 2. Halbzeit, Ligagröße.
Kein Push — nur Seite und Buch.
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
                     "ts": jetzt.isoformat(), "status": "offen", "regel": 1})
        schon.add(i)
        neu += 1
    return neu
