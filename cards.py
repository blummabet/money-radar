#!/usr/bin/env python3
"""
cards.py — die Top-5-Cards aus dem alten Projekt als eigene, kleine Datei für die Seite (Lucas 31.10.2026).

„Komplett separat vom Geldradar, aber auf der Seite anzeigen" — die Rechnung (Pick-Engine, 14 Signale,
Abrechnung) bleibt im alten Repo blummabet/Betting-Dashboard. Dieses Skript holt dessen öffentliche
liga-data.json + liga-odds-history.json (zusammen ~20 MB), nimmt nur, was die Seite braucht, und schreibt
site/cards.json (~50 kB). Damit der Bau nicht alle 5 Min 20 MB lädt: Zwischenstand in .push_state/cards.json,
neu geholt höchstens alle 20 Min.

Inhalt 1:1 aus den Cards: nur BET/ABWÄGEN, Top-5-Ligen, heute + morgen (Wiener Zeit), dazu die Bilanz aller
abgerechneten BET/ABWÄGEN-Picks (je Monat, je Markt-Art, je Überzeugung).
"""
from __future__ import annotations

import ast
import json
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BASIS = Path(__file__).resolve().parent
QUELLE = "https://raw.githubusercontent.com/blummabet/Betting-Dashboard/main/"
CACHE = BASIS / ".push_state" / "cards.json"
ZIEL = BASIS / "site" / "cards.json"
WIEN = ZoneInfo("Europe/Vienna")
JETZT = datetime.now(timezone.utc)
FRISCH_MIN = 20
LIGA = {"ENG": "Premier League", "ESP": "La Liga", "GER": "Bundesliga", "ITA": "Serie A", "FRA": "Ligue 1"}
SIGNAL_DE = {"xg_strength": "xG", "h2h_pattern": "Direkte Duelle", "injury": "Ausfälle", "chance_creation": "Chancen",
             "topscorer_momentum": "Torjäger", "transfer_shift": "Transfers", "form_trend": "Form", "venue_form": "Heim/Auswärts",
             "sharp_money": "Sharp-Geld", "steam": "Steam", "move_following": "Kursbewegung", "lineup_signal": "Aufstellung",
             "league_pressure": "Tabellendruck", "smart_money": "Polymarket-Geld", "betfair_flow": "Betfair-Geld",
             "apif_predictions": "2. Modell", "coach_change": "Trainerwechsel", "rest_days": "Pause", "travel_burden": "Reise"}


def _holen(name: str):
    with urllib.request.urlopen(QUELLE + name, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def _ts(s):
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def _lit(v):
    if isinstance(v, str):
        try:
            return ast.literal_eval(v)
        except Exception:  # noqa: BLE001
            return None
    return v


def _chance(r, markt):
    """Pinnacle-Chance (entvigt) für genau diesen Tipp aus einer Quoten-Zeile — oder None."""
    try:
        if markt in ("Heimsieg", "Auswärtssieg") or markt.startswith("Doppelte"):
            a = {"h": 1 / r["hw"], "d": 1 / r["dr"], "a": 1 / r["aw"]}
            s = sum(a.values())
            a = {k: v / s for k, v in a.items()}
            return {"Heimsieg": a["h"], "Auswärtssieg": a["a"], "Doppelte Chance — 1X": a["h"] + a["d"],
                    "Doppelte Chance — X2": a["a"] + a["d"], "Doppelte Chance — 12": a["h"] + a["a"]}.get(markt)
        m = re.match(r"(Über|Unter) (\d)\.5 Tore", markt)
        if m:
            o, u = r.get(f"o{m.group(2)}5"), r.get(f"u{m.group(2)}5")
            if not o or not u:
                return None
            po = (1 / o) / ((1 / o) + (1 / u))
            return po if m.group(1) == "Über" else 1 - po
    except Exception:  # noqa: BLE001
        return None
    return None


def _einheiten(p):
    f = p.get("resultStakeFactor") or 1
    if p.get("result") == "WIN":
        return (p["odds"] - 1) * f
    if p.get("result") == "LOSS":
        return -1.0 * f
    return 0.0


def _markt_art(m):
    m = str(m or "")
    if m.startswith("AH"):
        return "Handicap"
    if m.startswith("Doppelte"):
        return "Doppelte Chance"
    if "Tore" in m:
        return "Über/Unter"
    if m.startswith("Beide"):
        return "Beide treffen"
    return "Sieg"


def bauen() -> dict:
    d = _holen("liga-data.json")
    hist = _holen("liga-odds-history.json")
    teams = {}
    for g, gd in (d.get("groups") or {}).items():
        for t in gd.get("teams") or []:
            teams[str(t["id"])] = t
    heute = JETZT.astimezone(WIEN).date()
    tage = {heute: "heute", heute + timedelta(days=1): "morgen"}
    karten, bilanz = [], []
    for g, gd in (d.get("groups") or {}).items():
        if g not in LIGA:
            continue
        for fx in gd.get("fixtures") or []:
            key = f"{g}-{fx['matchday']}-{fx['home']}-{fx['away']}"
            ko = _ts(fx.get("kickoff"))
            for p in (d.get("picks") or {}).get(key) or []:
                if p.get("verdict") not in ("BET", "ABWÄGEN"):
                    continue
                if p.get("result") in ("WIN", "LOSS", "VOID") and ko:
                    bilanz.append([ko.astimezone(WIEN).strftime("%Y-%m-%d"), g, _markt_art(p.get("market")), p.get("convictionScore"),
                                   p.get("verdict"), p["result"], round(_einheiten(p), 3), p.get("odds")])
                if not ko or ko.astimezone(WIEN).date() not in tage:
                    continue
                h, a = teams.get(str(fx["home"]), {}), teams.get(str(fx["away"]), {})
                sig = _lit(p.get("signals")) or []
                verlauf = []
                for r in hist.get(f"{fx['home']}-{fx['away']}") or []:
                    if r.get("bk") == "pinnacle" and (c := _chance(r, p["market"])) is not None:
                        verlauf.append([r["ts"][:16], round(100 * c, 1)])
                res = fx.get("result") or {}
                karten.append({
                    "tag": tage[ko.astimezone(WIEN).date()], "liga": LIGA[g], "land": g, "spieltag": fx.get("matchday"),
                    "anpfiff": fx.get("kickoff"), "heim": h.get("name") or fx.get("homeName"), "gast": a.get("name") or fx.get("awayName"),
                    "logoH": h.get("logo"), "logoG": a.get("logo"),
                    "markt": p["market"], "quote": p.get("odds"), "urteil": p["verdict"], "score": p.get("convictionScore"),
                    "schwelle": p.get("convBetSchwelle") or 8, "fam": p.get("convictionFamilies") or {},
                    "dafuer": p.get("signalCountPos"), "dagegen": p.get("signalCountNeg"), "einsatz": p.get("stake"),
                    "ergebnis": p.get("result"), "stand": [res.get("home_score"), res.get("away_score")] if res.get("status") == "FT" else None,
                    "signale": sorted([{"n": SIGNAL_DE.get(s.get("name"), s.get("name")), "w": s.get("weighted_score"), "e": s.get("evidence")}
                                       for s in sig if isinstance(s, dict) and s.get("weighted_score")], key=lambda s: -abs(s["w"])),
                    "verlauf": verlauf[-40:],
                })
    karten.sort(key=lambda k: (k["anpfiff"] or "", k["heim"] or ""))
    bilanz.sort()
    return {"stand": JETZT.isoformat(), "quelleStand": (d.get("_meta") or {}).get("picksUpdatedAt"),
            "karten": karten, "bilanz": bilanz,
            "bilanzFelder": ["tag", "liga", "art", "score", "urteil", "ergebnis", "einheiten", "quote"]}


def main() -> int:
    alt = None
    try:
        alt = json.loads(CACHE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    neu = alt
    t = _ts((alt or {}).get("stand"))
    if not alt or not t or (JETZT - t).total_seconds() > FRISCH_MIN * 60 or "--neu" in sys.argv:
        try:
            neu = bauen()
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            CACHE.write_text(json.dumps(neu, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            print(f"cards: {len(neu['karten'])} Cards, {len(neu['bilanz'])} abgerechnete Picks")
        except Exception as exc:  # noqa: BLE001 — die Cards dürfen den Seitenbau nie aufhalten
            print(f"cards: Abruf fehlgeschlagen ({exc}) — {'alter Stand' if alt else 'keine Datei'}")
    else:
        print(f"cards: Zwischenstand von {t:%H:%M} UTC")
    if neu:
        ZIEL.parent.mkdir(parents=True, exist_ok=True)
        ZIEL.write_text(json.dumps(neu, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
