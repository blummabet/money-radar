#!/usr/bin/env python3
"""
ergebnisse.py — eigene Spielergebnisse + Liga-Buch (Lucas 20.10.2026: „das muss autonom im neuen Projekt laufen,
egal ob das alte noch da ist oder nicht").

Bisher kamen Endstände und das Liga-Buch aus dem Track-Record des alten Projekts. Jetzt führt der Geldradar beides
selbst, aus seinem eigenen Betfair-Abruf (.bf/, Actions-Cache):

  .bf/ergebnisse.json   {matchId: {h, a, l, ko, ht:[x,y], ft:[x,y], q}}   — 30 Tage
      Halbzeit: Live-Stand in der Pause (is_ht) oder der letzte Stand bis zur 45. Minute aus der Historie.
      Endstand: „finished" im Live-Feed, sonst POST /football/results (Betwatch, autoritativ, bis 30 Tage).
  .bf/liga_offen.json   je Spiel und Markt die Seite mit dem meisten Geld bis zum Anpfiff (Stand beim letzten
                        Abruf davor, Quote beim ersten Sehen) — nach dem Ergebnis wandert die Zeile ins Liga-Buch.
  .bf/liga_buch.json    {matchId|Markt: {l, t, q, w, v, a, m}} — dasselbe Format wie bisher, build.py liest es.

Läuft nach fetch_betfair.py, vor build.py und push.py (bilanz.py rechnet mit denselben Ergebnissen ab).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASIS = Path(__file__).resolve().parent / ".bf"
PREISE = BASIS / "betfair_prices.json"
HIST = BASIS / "betfair_history.json"
ERG = BASIS / "ergebnisse.json"
OFFEN = BASIS / "liga_offen.json"
BUCH = BASIS / "liga_buch.json"
HALTEN_TAGE = 30
ABFRAGE_AB_H = 2.3          # ab so viel Stunden nach Anpfiff fehlt ein Endstand → bei Betwatch nachfragen
ABFRAGE_MAX = 50
# dieselben 7 Märkte wie das Track-Record des alten Projekts
MAERKTE = ("Match Odds", "Half Time", "Over/Under 2.5 Goals", "Over/Under 3.5 Goals",
           "Both teams to Score?", "First Half Goals 0.5", "First Half Goals 1.5")


def _ts(s):
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def _laden(p, leer):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return leer


def _schreiben(p, d):
    p.parent.mkdir(exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(p)


def _stand(li):
    g1, g2 = li.get("goal_v1"), li.get("goal_v2")
    return [int(g1), int(g2)] if isinstance(g1, (int, float)) and isinstance(g2, (int, float)) else None


def ergebnisse_fortschreiben(jetzt, abfrage=None):
    """Rein bis auf `abfrage` (fetch_results). Gibt das aktualisierte Ergebnisbuch zurück."""
    erg = _laden(ERG, {})
    preise = _laden(PREISE, {})
    hist = _laden(HIST, {})
    for m in preise.get("matches") or []:
        mid = str(m.get("matchId"))
        e = erg.setdefault(mid, {})
        e.update({"h": m.get("home"), "a": m.get("away"), "l": m.get("league"), "ko": m.get("kickoff")})
        li = m.get("liveInfo") or {}
        st = _stand(li)
        if not st:
            continue
        mi = li.get("time")
        if li.get("is_ht") or (isinstance(mi, (int, float)) and mi <= 45):
            e["ht"] = st                       # in der Pause oder bis zur 45. Minute: der Halbzeitstand (wird überschrieben bis zur Pause)
            e["htSicher"] = bool(li.get("is_ht")) or e.get("htSicher", False)
        if li.get("finished"):
            e["ft"], e["q"] = st, "live"
        elif isinstance(mi, (int, float)):
            e["zuletzt"] = [mi, st]
    # Halbzeit aus der Historie, falls der Lauf die Pause verpasst hat: letzter Stand bis Minute 45
    for mid, pts in (hist or {}).items():
        if mid == "_meta" or not isinstance(pts, list) or mid not in erg or erg[mid].get("htSicher"):
            continue
        bis45 = [p for p in pts if isinstance(p, dict) and isinstance(p.get("min"), (int, float)) and p["min"] <= 45 and isinstance(p.get("sc"), list)]
        spaeter = any(isinstance(p, dict) and isinstance(p.get("min"), (int, float)) and p["min"] >= 46 for p in pts)
        if bis45 and spaeter and not erg[mid].get("ht"):
            erg[mid]["ht"] = bis45[-1]["sc"]
    # Endstand nachfragen
    fehlen = [mid for mid, e in erg.items() if not e.get("ft") and _ts(e.get("ko"))
              and jetzt - _ts(e["ko"]) > timedelta(hours=ABFRAGE_AB_H)]
    fehlen.sort(key=lambda mid: erg[mid].get("ko") or "", reverse=True)
    if fehlen and abfrage:
        try:
            antw = abfrage(fehlen[:ABFRAGE_MAX]) or {}
        except Exception as exc:  # noqa: BLE001
            print(f"  ergebnisse: Abfrage fehlgeschlagen ({str(exc)[:80]})")
            antw = {}
        for mid, r in antw.items():
            st = _stand(r)
            if st and (r.get("finished") is None or r.get("finished")) and mid in erg:
                erg[mid]["ft"], erg[mid]["q"] = st, "results"
                hs = [r.get("ht_v1"), r.get("ht_v2")]
                if all(isinstance(x, (int, float)) for x in hs):
                    erg[mid]["ht"], erg[mid]["htSicher"] = [int(hs[0]), int(hs[1])], True
    # Spiel ganz spät zuletzt gesehen und lange weg: so abrechnen wie das alte Projekt (ab Minute 85, 25 Min weg)
    for mid, e in erg.items():
        z = e.get("zuletzt")
        if not e.get("ft") and z and z[0] >= 85 and _ts(e.get("ko")) and jetzt - _ts(e["ko"]) > timedelta(hours=4):
            e["ft"], e["q"] = z[1], "zuletzt"
    grenze = jetzt - timedelta(days=HALTEN_TAGE)
    erg = {k: v for k, v in erg.items() if (_ts(v.get("ko")) or jetzt) >= grenze}
    _schreiben(ERG, erg)
    return erg


def liga_fortschreiben(jetzt, erg):
    """Seite mit dem meisten Geld je Spiel+Markt bis Anpfiff merken; nach dem Endstand ins Liga-Buch."""
    import bilanz        # gewonnen() — dieselbe Abrechnung wie für die Pushes
    offen = _laden(OFFEN, {})
    buch = _laden(BUCH, {})
    for m in (_laden(PREISE, {}).get("matches") or []):
        ko = _ts(m.get("kickoff"))
        if not ko or jetzt >= ko or (m.get("liveInfo") or {}).get("time"):
            continue                                   # nur vor Anpfiff (der letzte Lauf davor ist der Schlussstand)
        mid = str(m.get("matchId"))
        for name in MAERKTE:
            mk = (m.get("markets") or {}).get(name) or {}
            rs = [r for r in mk.get("runners") or [] if isinstance(r, dict) and (r.get("vol") or 0) > 0]
            ges = sum(r["vol"] for r in rs)
            if ges < 200 or not rs:
                continue
            top = max(rs, key=lambda r: r["vol"])
            k = f"{mid}|{name}"
            alt = offen.get(k) or {}
            erst = alt.get("q") if alt.get("sel") == top.get("name") else None
            offen[k] = {"l": m.get("league"), "ko": m.get("kickoff"), "h": m.get("home"), "g": m.get("away"),
                        "m": name, "sel": top.get("name"), "q": erst or top.get("odd"), "qs": top.get("odd"),
                        "v": round(ges), "a": round(100 * top["vol"] / ges)}
    fertig = []
    for k, z in offen.items():
        e = erg.get(k.split("|")[0]) or {}
        if not e.get("ft"):
            if _ts(z.get("ko")) and jetzt - _ts(z["ko"]) > timedelta(days=4):
                fertig.append(k)                       # nie ein Ergebnis bekommen → verwerfen
            continue
        w = bilanz.gewonnen(z["m"], z["sel"], z.get("h"), z.get("g"), e["ft"], e.get("ht"))
        fertig.append(k)
        if w is None or not z.get("l"):
            continue
        buch[k] = {"l": z["l"], "t": jetzt.strftime("%Y-%m-%d"), "q": z.get("q"), "w": bool(w),
                   "v": z.get("v") or 0, "a": z.get("a") or 0, "m": z["m"]}
    for k in fertig:
        offen.pop(k, None)
    _schreiben(OFFEN, offen)
    _schreiben(BUCH, buch)
    return len(fertig)


def main() -> int:
    jetzt = datetime.now(timezone.utc)
    try:
        from fetch_betfair import fetch_results
    except Exception:  # noqa: BLE001
        fetch_results = None
    erg = ergebnisse_fortschreiben(jetzt, fetch_results)
    n = liga_fortschreiben(jetzt, erg)
    print(f"  ergebnisse: {sum(1 for e in erg.values() if e.get('ft'))} Endstände, "
          f"{sum(1 for e in erg.values() if e.get('ht'))} Halbzeitstände · Liga-Buch +{n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
