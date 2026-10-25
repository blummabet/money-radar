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
import math
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
BEHALTEN = 6000           # 23.10.: Top-3-Messung kommt dazu
POLY_ARTEN = ("pserie", "wal", "top3p")
ARTEN = {"serie": "Dauerzufluss", "pserie": "Dauerzufluss Polymarket", "wal": "Wal-Einstieg Polymarket", "zufluss": "Zufluss vor Anpfiff", "live": "Live-Zufluss", "ht": "Halbzeit-Geld", "fix": "Fix-Verdacht", "pinn": "Pinnacle + Geld"}


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


_STOP = {"al", "el", "fc", "sc", "cf", "ac", "as", "ud", "cd", "club", "de", "la", "le", "the", "real", "u19", "u21", "u23", "w", "team"}


def _team(n):
    """Erstes markantes Wort eines Teamnamens (23.10.: Betfair schreibt „FIRST VIENNA FC", das Spiel heißt „First Vienna Fc 1894")."""
    import unicodedata
    t = unicodedata.normalize("NFD", str(n or "")).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"\(.*?\)", " ", t)
    w = [x for x in re.split(r"[^a-z0-9]+", t) if len(x) >= 3 and x not in _STOP]
    return w[0] if w else ""


# ── Abrechnung ───────────────────────────────────────────────────────────────────
def gewonnen(markt, auswahl, heim, gast, ft, ht):
    """True/False, oder None wenn der Markt aus dem Stand nicht sicher abrechenbar ist. REIN."""
    m, a = str(markt or ""), str(auswahl or "")
    stand = ht if m in ("Half Time", "Half Time Score") or m.startswith("First Half Goals") else ft
    if not stand or len(stand) != 2 or None in stand:
        return None
    h, g = stand
    if m in ("Match Odds", "Half Time"):
        if a == heim or (_team(a) and _team(a) == _team(heim) and _team(a) != _team(gast)):
            return h > g
        if a == gast or (_team(a) and _team(a) == _team(gast) and _team(a) != _team(heim)):
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
    warten = {str(b.get("id")) for b in buch if b.get("status") == "offen" and b.get("art") not in POLY_ARTEN
              and _ts(b.get("anpfiff")) and jetzt - _ts(b["anpfiff"]) > timedelta(hours=2)}
    erg = _ergebnisse(warten)
    for b in buch:
        if b.get("status") != "offen" or b.get("art") in POLY_ARTEN or str(b.get("id")) not in warten:
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
    poly_kurse(buch, jetzt)
    poly_abrechnen(buch, jetzt)
    return buch[-BEHALTEN:]


# ── Polymarket (Lucas 20.10.: Dauerzufluss auf Polymarket ins Buch) ───────────────────────────────────────
def _poly_ausgang(event, auswahl, key=None):
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
        # 24.10. (E-Sport: 25 Pushes seit 22.10., keiner abgerechnet): der Sieger-Markt eines E-Sport-Ereignisses heißt
        # „Match Winner" und steht neben vielen Spielmärkten — das Etikett wurde „Team (Match Winner)", der Push trägt
        # aber den blanken Teamnamen. Der Hauptmarkt (Slug = Ereignis, oder Match Winner) zählt auch mit blankem Namen.
        haupt = (key and mk.get("slug") == key) or titel.lower() in ("match winner", "moneyline", "winner")
        for i, o in enumerate(outs):
            if (lab.get(o) != auswahl and not (haupt and o == auswahl)) or i >= len(preise):
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


POLY_MAERKTE = BASIS / ".pm" / "poly_maerkte.json"
MESSUNG_AB = "2026-10-22T06:00:00+00:00"     # Lucas 22.10.: ab hier vorwärts gemessen, Regel vorher festgelegt
MESSUNG_PREIS = (0.34, 0.59)                 # = Quote 1,70 bis 2,94
MESSUNG_ZIEL = 60


def _poly_preis(m, auswahl):
    """Aktueller Preis der Auswahl im Markt-Scan; „gegen X" = 1 − Preis von X."""
    pr = (m or {}).get("prices") or {}
    a = str(auswahl or "")
    if a in pr:
        return pr[a]
    if a.startswith("gegen ") and a[6:] in pr:
        return 1 - pr[a[6:]]
    return None


def poly_kurse(buch, jetzt):
    """Lucas 22.10.: Kursmessung für Polymarket-Pushes — die Messlatte, die bei Betfair die Schlussquote ist.
    Vor Anpfiff: letzter Preis vor dem Anpfiff (als Quote in „schluss", gleiche CLV-Formel wie Betfair).
    Für alle (auch live): der Preis 15 Min nach dem Push („nach15") — steigt er, lag der Push vor dem Markt."""
    try:
        d = json.loads(POLY_MAERKTE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return
    t = _ts(d.get("generatedAt"))
    if not t or jetzt - t > timedelta(minutes=20):
        return                                    # veralteter Scan misst nichts
    mk = d.get("maerkte") or {}
    for b in buch:
        if b.get("art") not in POLY_ARTEN or b.get("status") != "offen":
            continue
        p = _poly_preis(mk.get(str(b.get("id"))), b.get("auswahl"))
        if not p or not 0.01 < p < 0.99:
            continue
        ko, ts = _ts(b.get("anpfiff")), _ts(b.get("ts"))
        if not b.get("live") and ko and t < ko:
            b["schluss"], b["schlussTs"] = round(1 / p, 4), t.isoformat()
        if ts and "nach15" not in b and timedelta(minutes=13) <= t - ts <= timedelta(minutes=35):
            b["nach15"] = round(1 / p, 4)


WAL_MESSUNG_AB = "2026-10-24T20:30:00+00:00"   # Lucas 24.10. („messen wir auch die guten Wallets mit?") — ab hier vorwärts
WAL_MESSUNG_PREIS = (0.20, 0.80)              # = Quote 1,25 bis 5,0: keine Fast-Sicheren, keine Lottoscheine


def _kachel(sp, z, ziel):
    """Kennzahlen + Urteil einer Gruppe (z = Buchzeilen dieser Gruppe)."""
    k = _gruppe(z)
    urteil = None
    if k["abgerechnet"] >= ziel:
        urteil = "bestanden" if k["_roi"] - (k["_se"] or 0) > 0 and k["_kante"] else "nicht bestanden"
    return {"sport": sp, **{x: v for x, v in k.items() if not x.startswith("_")}, "urteil": urteil}


def _erste_und_folge(z):
    """Lucas 24.10.: fürs Urteil zählt nur der ERSTE Push je Spiel + Seite (sonst zählt ein Spiel mit 3×/5×/8× dreifach).
    Folge-Pushes werden extra geführt — und die ersten Pushes danach getrennt, ob noch einer kam („mehr kam = Aussage")."""
    z = sorted(z, key=lambda b: b.get("ts") or "")
    erst, folge, n_je = [], [], {}
    for b in z:
        k = (b.get("id"), b.get("auswahl"))
        n_je[k] = n_je.get(k, 0) + 1
        (folge if n_je[k] > 1 else erst).append(b)
    mit = [b for b in erst if n_je[(b.get("id"), b.get("auswahl"))] > 1]
    ohne = [b for b in erst if n_je[(b.get("id"), b.get("auswahl"))] == 1]
    return erst, folge, mit, ohne


def messung(buch):
    """Die vorab festgelegten Regeln für Polymarket, je Sport — Fortschritt bis 60 und Urteil.
    Dauerzufluss: Preis 34–59 ¢ ab 22.10. · Wal-Einstieg (belegte Wallets): Preis 20–80 ¢ ab 24.10. abends.
    Gewertet wird je Spiel + Seite nur der erste Push."""
    ab = _ts(MESSUNG_AB)
    regel = [b for b in buch if b.get("art") == "pserie" and _ts(b.get("ts")) and _ts(b["ts"]) >= ab
             and b.get("preis") and MESSUNG_PREIS[0] <= b["preis"] <= MESSUNG_PREIS[1]]
    aus = []
    for sp in ("Fußball", "E-Sport", "Tennis"):
        z = [b for b in regel if (b.get("sport") or "Fußball") == sp]
        erst, folge, mit, ohne = _erste_und_folge(z)
        k = _kachel(sp, erst, MESSUNG_ZIEL)
        k["teile"] = [{"name": n, **{x: v for x, v in _gruppe(g).items() if not x.startswith("_")}}
                      for n, g in (("Spiel bekam später noch einen Push", mit), ("nur ein Push", ohne), ("Folge-Pushes selbst", folge)) if g]
        aus.append(k)
    wab = _ts(WAL_MESSUNG_AB)
    wr = [b for b in buch if b.get("art") == "wal" and _ts(b.get("ts")) and _ts(b["ts"]) >= wab
          and b.get("preis") and WAL_MESSUNG_PREIS[0] <= b["preis"] <= WAL_MESSUNG_PREIS[1]]
    wal = []
    for sp in ("Fußball", "E-Sport", "Tennis"):
        erst, _, _, _ = _erste_und_folge([b for b in wr if (b.get("sport") or "Fußball") == sp])
        wal.append(_kachel(sp, erst, MESSUNG_ZIEL))
    return {"ab": MESSUNG_AB, "preis": MESSUNG_PREIS, "ziel": MESSUNG_ZIEL, "sport": aus,
            "wal": {"ab": WAL_MESSUNG_AB, "preis": WAL_MESSUNG_PREIS, "sport": wal}}


TOP3_AB = "2026-10-23T17:45:00+00:00"        # Lucas 23.10. 19:43: „ja bitte" — ab hier vorwärts
TOP3_ZIEL = 60


def _gruppe(z):
    """Kennzahlen einer Gruppe aus dem Buch: Treffer gegen Quote, Rendite mit Fehler, Schlussquote, Kurs 15 Min danach."""
    fertig = [b for b in z if b.get("status") == "abgerechnet"]
    n = len(fertig)
    r = [b["r"] for b in fertig]
    roi = sum(r) / n if n else None
    se = (math.sqrt(sum((x - roi) ** 2 for x in r) / (n - 1)) / math.sqrt(n)) if n > 1 else None
    clv = [b["quote"] / b["schluss"] - 1 for b in z if not b.get("live") and b.get("schluss") and b.get("quote")
           and _ts(b.get("schlussTs")) and _ts(b.get("anpfiff")) and _ts(b["schlussTs"]) >= _ts(b["anpfiff"]) - timedelta(minutes=20)]
    mo = [b["quote"] / b["nach15"] - 1 for b in z if b.get("nach15") and b.get("quote")]
    return {"gesendet": len(z), "abgerechnet": n, "offen": sum(1 for b in z if b.get("status") == "offen"),
            "treffer": round(100 * sum(1 for b in fertig if b["win"]) / n) if n else None,
            "erwartet": round(100 * sum(1 / b["quote"] for b in fertig) / n) if n else None,
            "roi": round(100 * roi, 1) if roi is not None else None, "fehler": round(100 * se, 1) if se is not None else None,
            "clv": round(100 * sum(clv) / len(clv), 1) if clv else None, "clvN": len(clv),
            "kurs15": round(100 * sum(mo) / len(mo), 1) if mo else None, "kurs15N": len(mo),
            "_roi": roi, "_se": se, "_kante": bool((clv and sum(clv) > 0) or (mo and sum(mo) > 0))}


def top3_messung(buch):
    """Top 3 jetzt: jeder erste Einzug zur Quote in dem Moment — gesamt, je Platz, je Punkte, Betfair/Polymarket."""
    ab = _ts(TOP3_AB)
    z = [b for b in buch if b.get("art") in ("top3", "top3p") and _ts(b.get("ts")) and _ts(b["ts"]) >= ab]
    gruppen = [("Alle", z), ("Platz 1 beim Einzug", [b for b in z if b.get("rang") == 1]),
               ("Platz 2–3 beim Einzug", [b for b in z if (b.get("rang") or 9) > 1]),
               ("Irgendwann Platz 1", [b for b in z if b.get("bestRang") == 1]),
               ("ab 6 Punkte", [b for b in z if (b.get("punkte") or 0) >= 6]), ("unter 6 Punkte", [b for b in z if (b.get("punkte") or 0) < 6]),
               ("Vor Anpfiff", [b for b in z if not b.get("live")]), ("Live", [b for b in z if b.get("live")]),
               ("Betfair", [b for b in z if b.get("art") == "top3"]), ("Polymarket", [b for b in z if b.get("art") == "top3p"]),
               # Lucas 25.10.: mehrere Märkte eines Spiels gleichgerichtet bespielt — ab Regel 2 erfasst
               ("🧩 Mehrere Märkte gleichgerichtet", [b for b in z if "maerkte" in (b.get("signale") or [])]),
               ("📊 Druck stützt die Richtung", [b for b in z if "druck" in (b.get("signale") or [])]),
               ("ohne Druck-Signal (ab Regel 3)", [b for b in z if b.get("art") == "top3" and (b.get("regel") or 1) >= 3
                                                   and "druck" not in (b.get("signale") or [])]),
               ("ohne mehrere Märkte (ab 25.10.)", [b for b in z if b.get("art") == "top3" and (b.get("regel") or 1) >= 2
                                                    and "maerkte" not in (b.get("signale") or [])]),
               *[(LIGA_NAME[n], [b for b in z if b.get("art") == "top3" and ligagroesse(b) == n]) for n in LIGA_STUFEN]]
    aus = []
    for name, g in gruppen:
        k = _gruppe(g)
        if name == "Alle":
            gesamt = k
        if k["gesendet"]:
            aus.append({"name": name, **{x: v for x, v in k.items() if not x.startswith("_")}})
    urteil = None
    if gesamt["abgerechnet"] >= TOP3_ZIEL:
        urteil = "bestanden" if gesamt["_roi"] - (gesamt["_se"] or 0) > 0 and gesamt["_kante"] else "nicht bestanden"
    letzte = [{k: b.get(k) for k in ("art", "heim", "gast", "markt", "auswahl", "quote", "rang", "punkte", "signale", "status", "win", "ts", "live", "sport")}
              for b in reversed(z)][:15]
    return {"ab": TOP3_AB, "ziel": TOP3_ZIEL, "urteil": urteil, "gruppen": aus, "letzte": letzte}


def poly_abrechnen(buch, jetzt):
    """Polymarket-Pushes (Dauerzufluss „pserie", Wal-Einstiege „wal") über die nachgeschlagenen Ereignisse abrechnen."""
    try:
        evs = json.loads(POLY_EREIGNISSE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        evs = {}
    for b in buch:
        if b.get("art") not in POLY_ARTEN or b.get("status") != "offen" or not _ts(b.get("anpfiff")):
            continue
        ev = evs.get(str(b.get("id")))
        w = None
        if ev:
            if b["art"] in ("pserie", "top3p"):
                w = _poly_ausgang(ev, b.get("auswahl"), str(b.get("id")))
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


DRUCK_ZIEL = 100


def druck_messung(buch):
    """Druck-Radar (Lucas 25.10.): Index ≥ 70 → „noch ein Tor" zur Quote in dem Moment. Gruppen vorab festgelegt (druck.py)."""
    z = [b for b in buch if b.get("art") == "druck"]
    gruppen = [("Alle", z), ("ab Regel 2 (Tore im Fenster abgezogen)", [b for b in z if (b.get("regel") or 1) >= 2]),
               ("Index 70–84", [b for b in z if (b.get("index") or 0) < 85]),
               ("Index ab 85", [b for b in z if (b.get("index") or 0) >= 85]),
               ("xG gemessen", [b for b in z if b.get("quelle") == "xG"]), ("xG geschätzt", [b for b in z if b.get("quelle") != "xG"]),
               ("Druck-Team liegt hinten", [b for b in z if b.get("lage") == "hinten"]),
               ("Unentschieden", [b for b in z if b.get("lage") == "gleich"]),
               ("Druck-Team führt", [b for b in z if b.get("lage") == "vorn"]),
               ("mit frischem Geld", [b for b in z if b.get("geld")]), ("ohne frisches Geld", [b for b in z if not b.get("geld")]),
               ("1. Halbzeit", [b for b in z if (b.get("min") or 0) <= 45]), ("2. Halbzeit", [b for b in z if (b.get("min") or 0) > 45]),
               *[(LIGA_NAME[n], [b for b in z if ligagroesse(b) == n]) for n in LIGA_STUFEN]]
    aus = []
    gesamt = _gruppe(z)
    for name, g in gruppen:
        k = _gruppe(g)
        if k["gesendet"]:
            aus.append({"name": name, **{x: v for x, v in k.items() if not x.startswith("_")}})
    urteil = None
    if gesamt["abgerechnet"] >= DRUCK_ZIEL:
        urteil = "bestanden" if gesamt["_roi"] - (gesamt["_se"] or 0) > 0 else "nicht bestanden"
    letzte = [{k: b.get(k) for k in ("heim", "gast", "auswahl", "quote", "index", "min", "stand", "lage", "status", "win", "ts")}
              for b in reversed(z)][:15]
    return {"ziel": DRUCK_ZIEL, "urteil": urteil, "gruppen": aus, "letzte": letzte}


NORM_ZIEL = 100


def norm_messung(buch):
    """Über der Liga-Norm (Lucas 25.10.): ab ×2 der Liga — folgt das Ergebnis dem Geld? Zwei Lesarten (norm_mess.py)."""
    aus = {}
    for art in ("norm", "normMeist"):
        z = [b for b in buch if b.get("art") == art]
        x = lambda b: b.get("xnorm") or 0
        gruppen = [("Alle", z), ("×2 bis 3", [b for b in z if x(b) < 3]), ("×3 bis 5", [b for b in z if 3 <= x(b) < 5]),
                   ("ab ×5", [b for b in z if x(b) >= 5]),
                   ("vor Anpfiff", [b for b in z if not b.get("live")]), ("live", [b for b in z if b.get("live")]),
                   *[(LIGA_NAME[n], [b for b in z if ligagroesse(b) == n]) for n in LIGA_STUFEN],
                   ("Top-5-Ligen", [b for b in z if b.get("top5")]), ("andere Ligen", [b for b in z if not b.get("top5")])]
        if art == "norm":
            gruppen.append(("Überhang ab 10 Punkten", [b for b in z if (b.get("ueberhang") or 0) >= 10]))
        g_aus = []
        gesamt = _gruppe(z)
        for name, g in gruppen:
            k = _gruppe(g)
            if k["gesendet"]:
                g_aus.append({"name": name, **{kk: v for kk, v in k.items() if not kk.startswith("_")}})
        urteil = None
        if gesamt["abgerechnet"] >= NORM_ZIEL:
            urteil = "bestanden" if gesamt["_roi"] - (gesamt["_se"] or 0) > 0 and gesamt["_kante"] else "nicht bestanden"
        aus[art] = {"ziel": NORM_ZIEL, "urteil": urteil, "gruppen": g_aus}
    return aus


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
              for b in reversed(buch) if b.get("status") in ("abgerechnet", "offen") and b.get("art") in ARTEN][:40]
    # Lucas 20.10. („Übersichtskurve auf die neuen Betfair-Pushes, weil das zählt"): jeder Push als
    # [Zeit UTC, Rendite je Einheit, 1/0] — offen mit None. build.py baut daraus die Bilanz-Kurve.
    grenze = datetime.now(timezone.utc) - timedelta(days=60)
    verlauf = []
    for b in buch:
        t = _ts(b.get("ts"))
        if not t or t < grenze or b.get("status") not in ("abgerechnet", "offen") or b.get("art") in ("pserie", "wal", "top3", "top3p", "druck", "norm", "normMeist"):
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
    return {"messung": messung(buch), "top3": top3_messung(buch), "druck": druck_messung(buch), "norm": norm_messung(buch), "walVerlauf": wale, "arten": aus, "teile": aus_teile, "letzte": letzte, "seit": buch[0]["ts"] if buch else None, "verlauf": verlauf}


def _kennz(ab):
    n = len(ab)
    if not n:
        return None
    return {"n": n, "treffer": round(100 * sum(1 for b in ab if b["win"]) / n),
            "erwartet": round(100 * sum(1 / b["quote"] for b in ab if b.get("quote")) / n),
            "roi": round(100 * sum(b["r"] for b in ab) / n, 1)}


# Grenzen 25.10. aus der gelernten Verteilung (181 Ligen, Endstand-Median): groß ≈ 15 Ligen (EPL €6,1M, CL, Serie A,
# La Liga, Bundesliga, Ligue 1, EL, Brasilien, 2. Bundesliga, Portugal, Türkei, Eredivisie), mittel bis zum Median
# aller Ligen (€50k: Championship, MLS, Schweiz, Norwegen …), klein = untere Hälfte (Ungarn, Faröer, Reserven …).
# Lucas 25.10.: nicht pauschal über alle Ligen urteilen. Ohne Ligagröße (weniger als 5 fertige Spiele mit ≥ €3k in
# 60 Tagen) → klein: fast immer Ligen, in denen kaum Geld liegt.
LIGA_STUFEN = ("groß", "mittel", "klein")
LIGA_NAME = {"groß": "Große Ligen (≥ €400k je Spiel)", "mittel": "Mittlere Ligen (€50k–400k)", "klein": "Kleine Ligen (< €50k)"}


def ligagroesse(b):
    # 25.10. nachgeschärft: nach der Ligagröße (Endstand-Median der Liga), NICHT nach normEur — das ist der Median der
    # Phase, in der gepusht wurde, und machte dieselbe Liga vor Anpfiff „klein" und live „groß".
    if "ligaEur" not in b:
        return None                      # vor dem 25.10. ~10:30 mitgeschrieben — Ligagröße unbekannt
    n = b.get("ligaEur")
    if not n:
        return "klein"
    return "groß" if n >= 400000 else "mittel" if n >= 50000 else "klein"


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
        *[(LIGA_NAME[n], [b for b in ab if ligagroesse(b) == n]) for n in LIGA_STUFEN],
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
