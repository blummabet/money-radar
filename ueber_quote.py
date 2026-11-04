"""ueber_quote.py — „Geld über der Quote" still mitmessen + die Abend-Top-3 für Instagram (Lucas 04.11.2026:
„Probier es mal, ist irgendwie gute Gelegenheit, was Zusätzliches zu messen").

VORAB FESTGELEGT (04.11., Regel 1):
  · Spiel vor Anpfiff, Anpfiff in den nächsten 6 h, Betfair-Spiel (sonst nicht abrechenbar)
  · Geld mit bekannter Seite (Betfair + Polymarket + Stake zusammen, aus den Geld-Töpfen) ≥ €20.000
  · Anteil der stärksten Seite − Wahrscheinlichkeit laut Quote ≥ 25 Prozentpunkte
  · gebucht wird diese Seite im Sieg-Markt (Match Odds) zur Betfair-Quote in dem Moment, je Spiel einmal
  · Gruppen: Abstand 25–35 / ab 35 pp, eine / mehrere Quellen, Instagram-Top-3 ja/nein
Instagram-Top-3: am ersten Lauf ab 17:00 Wien die drei mit dem größten Abstand, Anpfiff noch heute (bis 06:00) →
eigene Art „ueberQuoteTop3" + site/ig_top3.json (die Vorlage liest diese Datei).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

WIEN = ZoneInfo("Europe/Vienna")
MIN_EUR = 20000
MIN_PP = 25
VOR_H = 6
REGEL = 1
TOP3_AB = (17, 0)
QNAME = {"betfair": "Betfair", "poly": "Polymarket", "stake": "Stake"}


def _ts(s):
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def kandidaten(d, jetzt, bis_h=VOR_H):
    """Alle Spiele, die die Regel erfüllen (sortiert nach Abstand). REIN."""
    out = []
    for g in d.get("toepfe") or []:
        if g.get("laeuft") or g.get("live") or str(g.get("id", "")).startswith(("pm:", "st:")):
            continue
        ko = _ts(g.get("anpfiff"))
        if not ko or not (jetzt < ko <= jetzt + timedelta(hours=bis_h)):
            continue
        keys = ["heim", "remis", "gast"] if (g.get("sport") or "Fußball") == "Fußball" else ["heim", "gast"]
        sw = g.get("seiten") or {}
        ges = sum(float(sw.get(k) or 0) for k in keys)
        if ges < MIN_EUR:
            continue
        bq = ((g.get("q") or {}).get("betfair") or {}).get("quoten") or {}
        if any(not isinstance(bq.get(k), (int, float)) or bq[k] <= 1 for k in keys):
            continue
        inv = {k: 1 / bq[k] for k in keys}
        su = sum(inv.values())
        top = max(keys, key=lambda k: float(sw.get(k) or 0))
        anteil = 100 * float(sw.get(top) or 0) / ges
        impl = 100 * inv[top] / su
        if anteil - impl < MIN_PP:
            continue
        quellen = []
        for qn, q in (g.get("q") or {}).items():
            se = q.get("seiten") or {}
            s = sum(float(se.get(k) or 0) for k in keys)
            if s > 0:
                quellen.append({"name": QNAME.get(qn, qn), "betrag": q.get("betrag"), "anteil": round(100 * float(se.get(top) or 0) / s, 1),
                                "w": "€" if qn == "betfair" else "$"})
        out.append({"id": str(g["id"]), "heim": g["heim"], "gast": g["gast"], "liga": g.get("liga"), "anpfiff": g.get("anpfiff"),
                    "top5": bool(g.get("top5")), "seite": top, "auswahl": g["heim"] if top == "heim" else g["gast"] if top == "gast" else "The Draw",
                    "anteil": round(anteil, 1), "impl": round(impl, 1), "abstand": round(anteil - impl, 1), "quote": bq[top],
                    "gesamt": g.get("gesamt"), "mitSeite": round(ges), "quellen": quellen})
    return sorted(out, key=lambda x: -x["abstand"])


def _beleg(k, art):
    return {"art": art, "id": k["id"], "heim": k["heim"], "gast": k["gast"], "liga": k["liga"], "anpfiff": k["anpfiff"],
            "top5": k["top5"], "live": False, "markt": "Match Odds", "auswahl": k["auswahl"], "quote": k["quote"],
            "eur": k["mitSeite"], "anteil": k["anteil"], "impl": k["impl"], "abstand": k["abstand"],
            "nQuellen": len(k["quellen"]), "regel": REGEL}


def eintragen(d, buch, jetzt, site: Path | None = None):
    import bilanz
    schon = {(b.get("art"), str(b.get("id"))) for b in buch if b.get("art") in ("ueberQuote", "ueberQuoteTop3")}
    neu = 0
    for k in kandidaten(d, jetzt):
        if ("ueberQuote", k["id"]) not in schon:
            bilanz.eintragen(buch, _beleg(k, "ueberQuote"), jetzt)
            neu += 1
    # Instagram-Top-3: einmal am Tag, erster Lauf ab 17:00 Wien
    w = jetzt.astimezone(WIEN)
    heute = w.strftime("%Y-%m-%d")
    schon_heute = any(b.get("art") == "ueberQuoteTop3" and (_ts(b.get("ts")) or jetzt).astimezone(WIEN).strftime("%Y-%m-%d") == heute
                      for b in buch)
    if (w.hour, w.minute) >= TOP3_AB and not schon_heute:
        nacht = (w + timedelta(days=1)).replace(hour=6, minute=0, second=0, microsecond=0)
        bis_h = (nacht - w).total_seconds() / 3600
        top = kandidaten(d, jetzt, bis_h)[:3]
        for k in top:
            bilanz.eintragen(buch, _beleg(k, "ueberQuoteTop3"), jetzt)
            neu += 1
        if site is not None:
            (site / "ig_top3.json").write_text(json.dumps({"tag": heute, "stand": jetzt.isoformat(), "spiele": top},
                                                         ensure_ascii=False), encoding="utf-8")
    return neu
