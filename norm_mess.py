"""„Über der Liga-Norm" — stille Messung (Lucas 25.10.2026: „wenn in einer Liga über der Norm Geld fließt, wie oft folgt
das Ergebnis dem Geld? Die Trefferquote wäre interessant").

Vorab festgelegt (25.10., 17:30 Wien):
  • Ins Buch kommt jedes Spiel beim ERSTEN Mal, wenn es ×2 seiner Liga-Norm erreicht (dieselbe Zahl wie im Block
    „Über der Liga-Norm"), vor Anpfiff oder live bis zur 75. Minute, einmal je Spiel.
  • Zwei Lesarten, zwei Bücher — weil „das meiste Geld" auf Betfair nicht dasselbe ist wie „die Richtung":
      norm       Sieg-Markt: die Auswahl, die MEHR Geld hat, als ihre Quote erwarten lässt (Geldanteil − Quotenanteil
                 am größten). Das ist die Richtung des Geldes.
      normMeist  die Auswahl mit dem meisten Geld im ganzen Spiel (wie „meist …" im Block), Quote ab 1,20.
  • Zur Betfair-Quote in dem Moment, abgerechnet wie die Pushes (2 % Kommission), vor Anpfiff mit Schlussquote.
  • Ziel je 100 abgerechnet. Getrennt: ×2–3 / ×3–5 / ab ×5, vor Anpfiff / live, Ligagröße, Überhang ab 10 Punkten.
Kein Push — nur Buch und Auswertung.
"""
from __future__ import annotations

import json
from pathlib import Path

PREISE = Path(__file__).resolve().parent / ".bf" / "betfair_prices.json"
AB_X = 2.0
BIS_MIN = 75


def _richtung(m):
    """Sieg-Markt: (Auswahl, Quote, Geldanteil %, Überhang in Prozentpunkten) der überproportional bespielten Auswahl."""
    rs = [r for r in (((m.get("markets") or {}).get("Match Odds") or {}).get("runners") or [])
          if isinstance(r, dict) and isinstance(r.get("odd"), (int, float)) and r["odd"] > 1]
    ges = sum(r.get("vol") or 0 for r in rs)
    imp = sum(1 / r["odd"] for r in rs)
    if len(rs) < 2 or ges < 500 or imp <= 0:
        return None
    best = max(rs, key=lambda r: (r.get("vol") or 0) / ges - (1 / r["odd"]) / imp)
    anteil = (best.get("vol") or 0) / ges
    return best.get("name"), best["odd"], round(100 * anteil), round(100 * (anteil - (1 / best["odd"]) / imp), 1)


def eintragen(d, buch, jetzt):
    try:
        preise = json.loads(PREISE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return 0
    pm = {str(m.get("matchId")): m for m in preise.get("matches") or []}
    schon = {(b.get("art"), str(b.get("id"))) for b in buch if b.get("art") in ("norm", "normMeist")}
    neu = 0
    for s in (d.get("radar") or {}).get("spiele") or []:
        i, x = str(s.get("id")), s.get("xnorm")
        lv = s.get("live") or None
        if not isinstance(x, (int, float)) or x < AB_X or s.get("entschieden") or s.get("klarVorn"):
            continue
        if lv and (not isinstance(lv.get("min"), (int, float)) or lv["min"] > BIS_MIN):
            continue
        basis = {"id": i, "heim": s.get("heim"), "gast": s.get("gast"), "liga": s.get("liga"), "land": s.get("land"),
                 "anpfiff": s.get("anpfiff"), "live": bool(lv), "min": (lv or {}).get("min"), "top5": bool(s.get("top5")),
                 "xnorm": x, "normGeschaetzt": bool(s.get("normGeschaetzt")), "phase": s.get("phase"),
                 "normEur": s.get("normEur"), "ligaEur": s.get("ligaEur"), "eur": s.get("eur"),
                 "ts": jetzt.isoformat(), "status": "offen", "regel": 1}
        r = _richtung(pm.get(i) or {})
        if ("norm", i) not in schon and r and r[1] >= 1.20:
            buch.append({**basis, "art": "norm", "markt": "Match Odds", "auswahl": r[0], "quote": r[1],
                         "anteil": r[2], "ueberhang": r[3]})
            schon.add(("norm", i))
            neu += 1
        g = s.get("geldAuf") or {}
        if ("normMeist", i) not in schon and g.get("markt") and isinstance(g.get("quote"), (int, float)) and g["quote"] >= 1.20 \
                and g.get("markt") not in ("Correct Score",):
            buch.append({**basis, "art": "normMeist", "markt": g["markt"], "auswahl": g.get("auswahl"), "quote": g["quote"],
                         "anteil": g.get("anteil")})
            schon.add(("normMeist", i))
            neu += 1
    return neu
