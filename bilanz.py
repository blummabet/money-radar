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
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASIS = Path(__file__).resolve().parent
BUCH = BASIS / ".push_state" / "buch.json"
BILANZ = BASIS / ".push_state" / "bilanz.json"
PREISE = BASIS / ".bf" / "betfair_prices.json"
ERGEBNISSE = BASIS / ".bf" / "ergebnisse.json"
KOMMISSION = 0.02
VERFALL_TAGE = 4          # nach 4 Tagen ohne Ergebnis: „unaufgelöst"
BEHALTEN = 3000
ARTEN = {"serie": "Dauerzufluss", "pserie": "Dauerzufluss Polymarket", "wal": "Wal-Einstieg Polymarket", "zufluss": "Zufluss vor Anpfiff", "live": "Live-Zufluss", "ht": "Halbzeit-Geld", "fix": "Fix-Verdacht"}


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
    """matchId -> (ft, ht) aus dem EIGENEN Ergebnisbuch (.bf/ergebnisse.json, ergebnisse.py) — Lucas 20.10.:
    unabhängig vom alten Projekt."""
    if not noetig:
        return {}
    try:
        erg = json.loads(ERGEBNISSE.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"  bilanz: Ergebnisbuch nicht lesbar ({str(exc)[:80]})")
        return {}
    return {mid: (e.get("ft"), e.get("ht")) for mid, e in erg.items() if mid in noetig and e.get("ft")}


def _hz_ungueltig(b):
    """20.10. (Liverpool – Villarreal, HZ-Push nach der Pause @ 100,00): HZ-Pushes, die nach der 1. HZ rausgingen oder auf
    einen praktisch entschiedenen Markt zeigten, waren Fehlalarme — sie zählen nicht in die Bilanz."""
    if b.get("art") not in ("ht", "fix"):
        return False
    q, t, ko = b.get("quote"), _ts(b.get("ts")), _ts(b.get("anpfiff"))
    return (isinstance(q, (int, float)) and q > 20) or bool(t and ko and (t - ko) > timedelta(minutes=52))


def aktualisieren(buch, jetzt):
    try:
        preise = json.loads(PREISE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        preise = {}
    spiele = {str(m.get("matchId")): m for m in preise.get("matches") or []}

    for b in buch:
        if b.get("status") in ("offen", "abgerechnet") and _hz_ungueltig(b):
            b["status"] = "ungültig"
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
    warten = {str(b.get("id")) for b in buch if b.get("status") == "offen" and b.get("art") != "pserie"
              and _ts(b.get("anpfiff")) and jetzt - _ts(b["anpfiff"]) > timedelta(hours=2)}
    erg = _ergebnisse(warten)
    for b in buch:
        if b.get("status") != "offen" or b.get("art") == "pserie" or str(b.get("id")) not in warten:
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
    poly_abrechnen(buch, jetzt)
    return buch[-BEHALTEN:]


# ── Polymarket (Lucas 20.10.: Dauerzufluss auf Polymarket ins Buch) ───────────────────────────────────────
def _poly_ausgang(event, auswahl):
    """True/False, wenn der Markt der Auswahl entschieden ist; None = noch offen / nicht zuordenbar; 'void' = 50:50.
    Etiketten wie in poly_fluss.seiten(): Ja-Nein-Märkte → Titel / „gegen Titel", sonst „Ausgang (Titel)" oder Ausgang."""
    mks = event.get("markets") or []
    for mk in mks:
        try:
            outs = json.loads(mk.get("outcomes") or "[]")
            preise = [float(x) for x in json.loads(mk.get("outcomePrices") or "[]")]
        except Exception:  # noqa: BLE001
            continue
        titel = (mk.get("groupItemTitle") or "").strip()
        if [o.lower() for o in outs] == ["yes", "no"] and titel:
            lab = {"Yes": titel, "No": f"gegen {titel}"}
        elif titel and len(mks) > 1:
            lab = {o: f"{o} ({titel})" for o in outs}
        else:
            lab = {o: o for o in outs}
        for i, o in enumerate(outs):
            if lab.get(o) != auswahl or i >= len(preise):
                continue
            if not mk.get("closed"):
                return None
            if max(preise) >= 0.99:
                return preise[i] >= 0.99
            if preise and abs(preise[i] - 0.5) < 0.02:
                return "void"
            return None
    return None


POLY_EREIGNISSE = BASIS / ".pm" / "poly_ereignisse.json"   # vom Mac-Lauf (poly_markt.py) — Polymarket blockt GitHub


def poly_abrechnen(buch, jetzt):
    """Polymarket-Pushes (Dauerzufluss „pserie", Wal-Einstiege „wal") über die nachgeschlagenen Ereignisse abrechnen."""
    try:
        evs = json.loads(POLY_EREIGNISSE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        evs = {}
    for b in buch:
        if b.get("art") not in ("pserie", "wal") or b.get("status") != "offen" or not _ts(b.get("anpfiff")):
            continue
        ev = evs.get(str(b.get("id")))
        w = None
        if ev:
            if b["art"] == "pserie":
                w = _poly_ausgang(ev, b.get("auswahl"))
            else:
                import poly_markt
                preise = poly_markt.aufloesung_ev(ev)
                if preise:
                    gew = poly_markt.sieger(preise)
                    w = "void" if gew is None else (gew == b.get("auswahl") if b.get("auswahl") in preise else None)
        if w == "void":
            b["status"] = "nicht abrechenbar"
        elif w is not None:
            b["status"], b["win"] = "abgerechnet", w
            q = b.get("quote") or 0
            b["r"] = round(q - 1, 4) if w else -1.0          # Polymarket: keine Kommission auf den Gewinn
        elif jetzt - _ts(b["anpfiff"]) > timedelta(days=VERFALL_TAGE):
            b["status"] = "unaufgelöst"


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
    aus_teile = {"serie": teile([b for b in buch if b.get("art") == "serie"]),
                 "pserie": teile([b for b in buch if b.get("art") == "pserie"]),
                 "wal": teile([b for b in buch if b.get("art") == "wal"])}
    letzte = [{k: b.get(k) for k in ("art", "heim", "gast", "titel", "anzeige", "eur", "liga", "markt", "auswahl", "quote", "schluss", "ts",
                                    "status", "win", "ft", "live", "top5")}
              for b in reversed(buch) if b.get("status") in ("abgerechnet", "offen")][:40]
    # Lucas 20.10. („Übersichtskurve auf die neuen Betfair-Pushes, weil das zählt"): jeder Push als
    # [Zeit UTC, Rendite je Einheit, 1/0] — offen mit None. build.py baut daraus die Bilanz-Kurve.
    grenze = datetime.now(timezone.utc) - timedelta(days=60)
    verlauf = []
    for b in buch:
        t = _ts(b.get("ts"))
        if not t or t < grenze or b.get("status") not in ("abgerechnet", "offen") or b.get("art") in ("pserie", "wal"):
            continue
        ab = b.get("status") == "abgerechnet"
        verlauf.append([t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M"), b.get("r") if ab else None,
                        (1 if b.get("win") else 0) if ab else None])
    # Wal-Pushes: eigener Verlauf mit Sportart (die Übersichtskurve zeigt sie als „Wale")
    wale = []
    for b in buch:
        t = _ts(b.get("ts"))
        if b.get("art") != "wal" or not t or t < grenze or b.get("status") not in ("abgerechnet", "offen"):
            continue
        ab = b.get("status") == "abgerechnet"
        wale.append({"zeit": t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M"), "r": b.get("r") if ab else None,
                     "w": (1 if b.get("win") else 0) if ab else None, "sport": b.get("sport"), "key": b.get("id"),
                     "seite": b.get("auswahl"), "anzeige": b.get("anzeige"), "titel": b.get("titel"), "preis": b.get("preis"),
                     "usd": b.get("usd"), "wallet": b.get("wallet"), "status": "won" if ab and b.get("win") else "lost" if ab else "pending"})
    return {"walVerlauf": wale, "arten": aus, "teile": aus_teile, "letzte": letzte, "seit": buch[0]["ts"] if buch else None, "verlauf": verlauf}


def _kennz(ab):
    n = len(ab)
    if not n:
        return None
    return {"n": n, "treffer": round(100 * sum(1 for b in ab if b["win"]) / n),
            "erwartet": round(100 * sum(1 / b["quote"] for b in ab if b.get("quote")) / n),
            "roi": round(100 * sum(b["r"] for b in ab) / n, 1)}


def teile(alle):
    """Lucas 19.10. („kannst du die Dauerfluss-Spiele auswerten? Ich spiel die ur oft"): dieselbe Bilanz in Teilen.
    Jede Stufe (3×, 5×, 8×, 12×) ist ein eigener Push — „je Spiel" zählt nur den ERSTEN Push je Spiel+Auswahl."""
    ab = [b for b in alle if b.get("status") == "abgerechnet" and b.get("quote")]
    erst, gesehen = [], set()
    for b in sorted(ab, key=lambda b: b.get("ts") or ""):
        k = (b.get("id"), b.get("markt"), b.get("auswahl"))
        b["_folge"] = k in gesehen
        if k not in gesehen:
            gesehen.add(k)
            erst.append(b)
    tore = lambda b: "Goals" in str(b.get("markt") or "")
    gruppen = [
        ("Alle Pushes", ab), ("Je Spiel (nur der erste Push)", erst), ("Folge-Pushes (5×, 8×, 12×)", [b for b in ab if b["_folge"]]),
        ("Vor Anpfiff", [b for b in ab if not b.get("live")]), ("Live", [b for b in ab if b.get("live")]),
        ("Sieg-Markt", [b for b in ab if b.get("markt") == "Match Odds"]), ("Tore (Über/Unter)", [b for b in ab if tore(b)]),
        ("Über", [b for b in ab if tore(b) and str(b.get("auswahl") or "").startswith("Over")]),
        ("Unter", [b for b in ab if tore(b) and str(b.get("auswahl") or "").startswith("Under")]),
        ("Quote unter 1,60", [b for b in ab if b["quote"] < 1.6]), ("Quote 1,60 bis 2,00", [b for b in ab if 1.6 <= b["quote"] <= 2.0]),
        ("Quote über 2,00", [b for b in ab if b["quote"] > 2.0]),
        ("Top-5-Ligen", [b for b in ab if b.get("top5")]), ("Andere Ligen", [b for b in ab if not b.get("top5")]),
    ]
    if any(b.get("sport") for b in ab):          # Polymarket: dazu je Sportart
        gruppen += [(sp, [b for b in ab if (b.get("sport") or "Fußball") == sp]) for sp in ("Fußball", "E-Sport", "Tennis")]
    for b in ab:
        b.pop("_folge", None)
    return [{"name": n, **k} for n, g in gruppen if (k := _kennz(g))]


def schreiben(buch, jetzt):
    BUCH.parent.mkdir(parents=True, exist_ok=True)
    BUCH.write_text(json.dumps(buch, ensure_ascii=False), encoding="utf-8")
    BILANZ.write_text(json.dumps({"stand": jetzt.isoformat(), **zusammenfassen(buch)}, ensure_ascii=False), encoding="utf-8")
