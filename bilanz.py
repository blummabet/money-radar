#!/usr/bin/env python3
"""
bilanz.py — Auswertung der neuen Pushes (Lucas 17.10.2026: „du kannst gern die Push auswerten").

Jede gesendete Meldung der Arten Zufluss (vor Anpfiff), Live-Zufluss, Halbzeit-Geld und Fix-Verdacht
wird mit Auswahl und Quote zum Push-Zeitpunkt ins Buch geschrieben (.push_state/buch.json, lebt im
Actions-Cache, nicht im Repo). Danach misst jeder Lauf zwei Dinge:

  * Schlussquote (nur vor Anpfiff): die letzte Betfair-Quote derselben Auswahl vor dem Anpfiff.
    CLV = Push-Quote / Schlussquote − 1. Das ist die ehrliche Messlatte — ein Push, der regelmäßig
    eine bessere Quote erwischt als den Schlusskurs, hat eine Kante, egal wie die ersten Spiele ausgehen.
  * Ergebnis: aus dem End- und Halbzeitstand (Ergebnisbuch des alten Projekts, sonst der Betfair-Feed).
    Gewinn mit 2 % Kommission, wie im HZ-Buch.

Märkte, die sich aus dem Stand nicht sicher abrechnen lassen (z. B. Asiatische Linien), bekommen nur
die Schlussquote. bilanz.json fasst je Art zusammen; build.py liest sie im nächsten Lauf.
"""
from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASIS = Path(__file__).resolve().parent
BUCH = BASIS / ".push_state" / "buch.json"
BILANZ = BASIS / ".push_state" / "bilanz.json"
PREISE = BASIS / ".bf" / "betfair_prices.json"
ERGEBNISSE = "https://raw.githubusercontent.com/blummabet/betting-dashboard/main/betfair_track_results.json"
KOMMISSION = 0.02
VERFALL_TAGE = 4          # nach 4 Tagen ohne Ergebnis: „unaufgelöst"
BEHALTEN = 3000
ARTEN = {"zufluss": "Zufluss vor Anpfiff", "live": "Live-Zufluss", "ht": "Halbzeit-Geld", "fix": "Fix-Verdacht"}


def _ts(s):
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def laden():
    try:
        return json.loads(BUCH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []


def eintragen(buch, beleg, jetzt):
    buch.append({**beleg, "ts": jetzt.isoformat(), "status": "offen"})


# ── Abrechnung ───────────────────────────────────────────────────────────────────
def gewonnen(markt, auswahl, heim, gast, ft, ht):
    """True/False, oder None wenn der Markt aus dem Stand nicht sicher abrechenbar ist. REIN."""
    m, a = str(markt or ""), str(auswahl or "")
    stand = ht if m in ("Half Time", "Half Time Score") or m.startswith("First Half Goals") else ft
    if not stand or len(stand) != 2 or None in stand:
        return None
    h, g = stand
    if m in ("Match Odds", "Half Time"):
        if a == heim:
            return h > g
        if a == gast:
            return g > h
        if a.lower() in ("the draw", "draw"):
            return h == g
        return None
    lm = re.match(r"^(Over/Under (\d+\.5) Goals|First Half Goals (\d+\.5))$", m)
    if lm:
        linie = float(lm.group(2) or lm.group(3))
        if a.lower().startswith("over"):
            return h + g > linie
        if a.lower().startswith("under"):
            return h + g < linie
        return None
    if m == "Both teams to Score?":
        if a.lower() == "yes":
            return h > 0 and g > 0
        if a.lower() == "no":
            return not (h > 0 and g > 0)
        return None
    if m in ("Correct Score", "Half Time Score"):
        cs = re.match(r"^(\d+)\s*-\s*(\d+)$", a)
        return (h, g) == (int(cs.group(1)), int(cs.group(2))) if cs else None
    return None


def _ergebnisse(noetig):
    """matchId -> (ft, ht). Nur laden, wenn ein Eintrag wirklich auf ein Ergebnis wartet."""
    if not noetig:
        return {}
    try:
        with urllib.request.urlopen(ERGEBNISSE, timeout=40) as r:
            roh = json.loads(r.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"  bilanz: Ergebnisbuch nicht erreichbar ({str(exc)[:80]})")
        return {}
    import betfair_track_store      # Kopie aus dem alten Projekt: das Ergebnisbuch ist gepackt gespeichert
    zeilen = betfair_track_store.entpacken(roh)
    out = {}
    for z in zeilen:
        if isinstance(z, dict) and z.get("ft") and str(z.get("matchId")) in noetig:
            out[str(z["matchId"])] = (z.get("ft"), z.get("ht"))
    return out


def aktualisieren(buch, jetzt):
    try:
        preise = json.loads(PREISE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        preise = {}
    spiele = {str(m.get("matchId")): m for m in preise.get("matches") or []}

    # 1) Schlussquote: solange vor Anpfiff, die aktuelle Quote derselben Auswahl nachziehen
    for b in buch:
        if b.get("status") != "offen" or b.get("live"):
            continue
        ko = _ts(b.get("anpfiff"))
        m = spiele.get(str(b.get("id")))
        if not m or not ko or jetzt >= ko:
            continue
        mk = (m.get("markets") or {}).get(b.get("markt")) or {}
        for r in mk.get("runners") or []:
            if isinstance(r, dict) and r.get("name") == b.get("auswahl") and isinstance(r.get("odd"), (int, float)) and r["odd"] > 1:
                b["schluss"] = r["odd"]
                b["schlussTs"] = jetzt.isoformat()

    # 2) Ergebnis: ab ~2 h nach Anpfiff
    warten = {str(b.get("id")) for b in buch if b.get("status") == "offen"
              and _ts(b.get("anpfiff")) and jetzt - _ts(b["anpfiff"]) > timedelta(hours=2)}
    erg = _ergebnisse(warten)
    for b in buch:
        if b.get("status") != "offen" or str(b.get("id")) not in warten:
            continue
        ft, ht = erg.get(str(b["id"]), (None, None))
        if not ft:
            m = spiele.get(str(b["id"]))
            li = (m or {}).get("liveInfo") or {}
            if li.get("finished") and li.get("goal_v1") is not None:
                ft = [li["goal_v1"], li["goal_v2"]]
        if ft:
            w = gewonnen(b.get("markt"), b.get("auswahl"), b.get("heim"), b.get("gast"), ft, ht)
            b["ft"], b["ht"] = ft, ht
            if w is None:
                b["status"] = "nicht abrechenbar"
            else:
                b["status"] = "abgerechnet"
                b["win"] = w
                q = b.get("quote") or 0
                b["r"] = round((q - 1) * (1 - KOMMISSION), 4) if w else -1.0
        elif jetzt - _ts(b["anpfiff"]) > timedelta(days=VERFALL_TAGE):
            b["status"] = "unaufgelöst"
    return buch[-BEHALTEN:]


def zusammenfassen(buch):
    aus = {}
    for art, name in ARTEN.items():
        alle = [b for b in buch if b.get("art") == art]
        ab = [b for b in alle if b.get("status") == "abgerechnet"]
        clv = [b["quote"] / b["schluss"] - 1 for b in alle
               if not b.get("live") and b.get("schluss") and b.get("quote") and _ts(b.get("anpfiff")) and _ts(b.get("schlussTs"))
               and _ts(b["schlussTs"]) >= _ts(b["anpfiff"]) - timedelta(minutes=20)]   # nur echte Schlussquoten
        n = len(ab)
        aus[art] = {"name": name, "n": len(alle), "offen": sum(1 for b in alle if b.get("status") == "offen"),
                    "abgerechnet": n,
                    "treffer": round(100 * sum(1 for b in ab if b["win"]) / n) if n else None,
                    "erwartet": round(100 * sum(1 / b["quote"] for b in ab if b.get("quote")) / n) if n else None,
                    "roi": round(100 * sum(b["r"] for b in ab) / n, 1) if n else None,
                    "clvN": len(clv), "clv": round(100 * sum(clv) / len(clv), 1) if clv else None}
    letzte = [{k: b.get(k) for k in ("art", "heim", "gast", "liga", "markt", "auswahl", "quote", "schluss", "ts",
                                    "status", "win", "ft", "live", "top5")}
              for b in reversed(buch) if b.get("status") in ("abgerechnet", "offen")][:40]
    return {"arten": aus, "letzte": letzte, "seit": buch[0]["ts"] if buch else None}


def schreiben(buch, jetzt):
    BUCH.parent.mkdir(parents=True, exist_ok=True)
    BUCH.write_text(json.dumps(buch, ensure_ascii=False), encoding="utf-8")
    BILANZ.write_text(json.dumps({"stand": jetzt.isoformat(), **zusammenfassen(buch)}, ensure_ascii=False), encoding="utf-8")
