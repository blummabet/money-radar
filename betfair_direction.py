#!/usr/bin/env python3
"""
betfair_direction.py -- Richtungs-Signal fuer Betfair-Geld (08.08.2026, Lucas).

## Blinder Fleck
Matched-Volumen je Runner sagt NICHT, ob das Geld als Back oder Lay reinkam -- jeder gematchte Euro hat
BEIDE Seiten, und der Volumen-Favorit ist oft nur der kurze Preis-Favorit (auf die kurze Seite fliesst
mechanisch mehr). Beispiel Lucas: "viel Geld auf Over 2.5" heisst dann nur "Over ist Favorit", nicht
"jemand backt Over scharf". Die RICHTUNG steckt in der Quotenbewegung:
  · Quote wird KUERZER  -> Runner wird netto GEBACKT   (dir = 'in')
  · Quote DRIFTET raus  -> Runner wird gelayt/abgestossen (dir = 'out')

## Ablauf
Laeuft im betfair.yml direkt NACH fetch_betfair_betwatch.py (vor Track/Overview/Alerts). Vergleicht die
aktuelle Quote je Runner mit der aus dem letzten Lauf und schreibt eine schlanke Join-Datei:
  betfair_direction.json = { matchId: { market: { runner: {dir, prev, odd} } } }
Die grosse betfair_prices.json bleibt unangetastet. Konsumenten (Track-Record, Alerts, Radar) joinen ueber
(matchId, market, runner). Die Datei ist zugleich die Referenz fuer den naechsten Lauf (odd je Runner).
Reiner Kern (annotate) ist netz-frei/testbar.
"""
import json
from pathlib import Path

# money-radar 20.10.2026: Kopie aus dem alten Projekt, arbeitet in .bf/ (Actions-Cache)
BASE = Path(__file__).resolve().parent / ".bf"
PRICES_FILE = BASE / "betfair_prices.json"
DIR_FILE = BASE / "betfair_direction.json"
REL_MIN = 0.005  # 14.08.2026 (Lucas): 3% in 15min war zu hart (fast alles flat) -> 0.5%. Relative Quotenbewegung ab der eine Richtung gilt (darunter = flat/Rauschen)


def classify(cur, prev):
    """Quotenbewegung -> Richtung. 'in' = kuerzer (Back), 'out' = laenger (Lay/Drift), 'flat' sonst. REIN."""
    if not isinstance(cur, (int, float)) or not isinstance(prev, (int, float)) or prev <= 0 or cur <= 0:
        return "flat"
    rel = (cur - prev) / prev
    if rel <= -REL_MIN:
        return "in"       # Quote kuerzer geworden -> gebackt
    if rel >= REL_MIN:
        return "out"      # Quote laenger geworden -> gedriftet/gelayt
    return "flat"


def annotate(prices, prev):
    """Aus aktuellen Preisen + letztem Stand die Richtungs-Map bauen. prev/return gleiche Struktur:
    { matchId: { market: { runner: {dir, prev, odd} } } }. REIN/testbar."""
    prev = prev if isinstance(prev, dict) else {}
    out = {}
    for m in (prices.get("matches") or []):
        mid = str(m.get("matchId"))
        if mid in ("None", ""):
            continue
        pm = prev.get(mid) or {}
        cur_m = {}
        for mkid, mk in (m.get("markets") or {}).items():
            pmk = pm.get(mkid) or {}
            cur_mk = {}
            for r in (mk.get("runners") or []):
                name = r.get("name")
                odd = r.get("odd")
                if name is None or not isinstance(odd, (int, float)):
                    continue
                prev_odd = (pmk.get(name) or {}).get("odd") if isinstance(pmk.get(name), dict) else None
                cur_mk[name] = {"dir": classify(odd, prev_odd) if prev_odd is not None else "flat",
                                "prev": prev_odd, "odd": odd}
            if cur_mk:
                cur_m[mkid] = cur_mk
        if cur_m:
            out[mid] = cur_m
    return out


def look(direction, matchId, market, runner):
    """Bequemer Join fuer Konsumenten: dir-Eintrag oder None."""
    try:
        return direction[str(matchId)][market][runner]
    except (KeyError, TypeError):
        return None


def _load(p, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def main():
    print("=== betfair_direction.py ===")
    prices = _load(PRICES_FILE, {})
    if not prices.get("matches"):
        print("  keine betfair_prices.json -- uebersprungen (kein Wipe).")
        return 0
    prev = _load(DIR_FILE, {})
    direction = annotate(prices, prev if isinstance(prev, dict) else {})
    DIR_FILE.write_text(json.dumps(direction, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    n_in = n_out = 0
    for mk in direction.values():
        for rr in mk.values():
            for e in rr.values():
                if e.get("dir") == "in":
                    n_in += 1
                elif e.get("dir") == "out":
                    n_out += 1
    print("  %d Runner gebackt (Quote kuerzer) - %d gedriftet (Quote laenger)" % (n_in, n_out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
