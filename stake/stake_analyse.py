#!/usr/bin/env python3
"""
stake_analyse.py — was der Stake-Fluss wirklich taugt
================================================================================
03.09.2026 (Lucas: „bitte alles umsetzen ... anhand der Daten dort kriegen wir genug kleine
Ligen wo Leute mit guten Infos setzen").

Diese Datei URTEILT — aber nur, wo die Basis es trägt. Sie rechnet nichts selbst, was das
Projekt schon kann: die Wilson-Untergrenze kommt aus `sharp_gate`, die Renditegrenze aus
`freigabe`. Eine rohe Quote ohne Untergrenze wird ausgewiesen, aber nie als Beleg.

## Vier Fragen, die sie beantwortet

1. **Was ist HIER ein grosser Einsatz?** (Liga-Norm)
   Absolute Schwellen sind das Falsche. $9.000 auf La Liga ist Dienstag; $9.000 auf die dritte
   finnische Liga ist ein Ereignis. Die Norm je Liga wird aus unseren EIGENEN Daten gelernt:
   Median und 90%-Punkt der Einsätze. 'Auffällig' heisst dann „das x-fache dessen, was hier
   sonst durchgeht" — nicht 'über einer Zahl, die wir uns ausgedacht haben'.

2. **Trifft dieser Fluss?** (Trefferquote je Schublade)
   Getrennt nach Phase (vor Anpfiff / live), Liga, Markt und Einsatzgrösse. Immer mit
   Wilson-Untergrenze; unter STAKE_MIN_N gibt es kein Urteil, nur die Zählung.

3. **Wo ist die Liga klein und der Einsatz gross?** (die eigentliche Idee)
   Die Schnittmenge aus 'kleine Liga' (wenig Wetten insgesamt) und „weit über der Norm dieser
   Liga". Genau da sitzt das, wonach Lucas sucht — und genau da ist n klein, weshalb es eine
   eigene Zählung braucht statt eines Bauchgefühls.

4. **Wieviel sehen wir überhaupt nicht?** (Abdeckung)
   Stakes Deckel liegt bei 50 Einträgen je Abruf. Der Sammler misst seine Lücken; hier werden
   sie zusammengezählt, damit 'an dem Abend war wenig los' nie unbemerkt „wir haben nicht
   hingeschaut" bedeutet.

## Was sie NICHT tut
Sie sucht sich nicht die beste Schublade aus und erklärt sie zum Signal. Welche Schubladen
zählen, steht vorher in `stake_vorregistrierung.json` — vorwärts angemeldet, bevor die Zahlen
da waren. Alles andere ist beschreibend und als solches markiert.

## Ausgabe
  stake_auswertung.json — Norm, Schubladen, Auffälligkeiten, Abdeckung
"""
from __future__ import annotations

import json
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(os.environ.get("STAKE_DATEN") or Path(__file__).resolve().parent)  # money-radar: Daten in .st/
sys.path.insert(0, str(BASE))

import stake_highroller_fetch as SH
import stake_liga_stufe as LS   # Spielklasse je Liga — eine Tabelle, ein Ort
from stake_seltenheit import seltenheit
from sharp_gate import wilson_lb
from freigabe import untergrenze

LEDGER_FILE = BASE / "stake_bet_ledger.json"
NORM_FILE = BASE / "stake_league_norm.json"
OUT_FILE = BASE / "stake_auswertung.json"
REG_FILE = BASE / "stake_vorregistrierung.json"

Z = 1.645                                              # einseitige 95%-Grenze, wie im Rest
MIN_N = int(os.environ.get("STAKE_MIN_N") or 30)       # darunter kein Urteil
MIN_N_LIGA = int(os.environ.get("STAKE_MIN_N_LIGA") or 20)
NORM_MIN_N = int(os.environ.get("STAKE_NORM_MIN_N") or 15)   # darunter keine Liga-Norm
KLEINE_LIGA_MAX = int(os.environ.get("STAKE_KLEINE_LIGA_MAX") or 25)
AUFFAELLIG_FAKTOR = float(os.environ.get("STAKE_AUFFAELLIG_FAKTOR") or 5.0)
# Ab welcher Seltenheit eine Wette auffaellig heisst — als Wahrscheinlichkeit gegen die
# simulierte Nullverteilung, nicht als Vielfaches. Ein festes Vielfaches ginge nicht: schon
# in einer voellig unauffaelligen Liga liegt das Maximum in ~25 % der Faelle bei „2x ueber
# Erwartung", und die Nullverteilung waechst selbst mit n. 0.10 = hoechstens jede zehnte
# unauffaellige Liga dieser Groesse braechte so etwas hervor.
AUFFAELLIG_ZUFALL = float(os.environ.get("STAKE_AUFFAELLIG_ZUFALL") or 0.10)

# Quotenbänder für die Auswertung. Nicht als Filter gedacht, sondern als Frage: trägt eine
# Wette bei 1,10 genauso viel Information wie eine bei 2,50? Das entscheidet die Abrechnung.
QUOTEN_BAENDER = [
    ("bis_120", 1.0, 1.20),
    ("120_135", 1.20, 1.35),
    ("135_160", 1.35, 1.60),
    ("160_200", 1.60, 2.00),
    ("200_350", 2.00, 3.50),
    ("ab_350", 3.50, 1e9),
]


# ── Hilfen ───────────────────────────────────────────────────────────────────
def _kat(w: dict) -> str:
    """Sportart-Kategorie. Aus dem Feld, sonst nachgerechnet — alte Zeilen haben es nicht."""
    return w.get("kat") or SH.sport_kategorie(w.get("sport"), w.get("liga"))


def _gewinn(w: dict):
    """Was diese Wette gewinnen wuerde. Aus dem Feld, sonst gerechnet — Zeilen von vor dem
    03.09. haben es nicht, und ohne Rueckfall staende in jeder Schublade eine 0. Eine 0 waere
    hier besonders schaedlich: sie sieht aus wie 'nichts zu gewinnen', nicht wie 'nicht erfasst'."""
    if w.get("gewinnUsd") is not None:
        return w["gewinnUsd"]
    e, q = w.get("einsatzUsd"), w.get("quote")
    if e is None or q is None or q <= 1:
        return None
    return round(e * (q - 1), 2)


def _ls_faktor(w: dict, norm: dict, ebmed: dict):
    """Einsatz als Vielfaches des ueblichen Einsatzes — ueber stake_liga_stufe, nicht hier.

    Der Grund fuer die Weiterleitung statt einer zweiten Rechnung: die Schwellen und die
    Wahl der Referenz (eigene Liga, sonst Ebene) stehen an EINER Stelle. Eine Kopie hier
    waere dieselbe Klasse wie die Frontend-Duplikate, die in diesem Repo schon zweimal
    auseinandergelaufen sind.
    """
    return LS.faktor(w, norm, ebmed)


def _erlaubt(w: dict) -> bool:
    # Kategorie UND Liga-Muster (ATP/WTA) — eine Quelle, siehe SH.sperr_grund.
    return SH.sperr_grund(w) is None


def _phase(w: dict) -> str:
    """vor | live | unbekannt.

    Kommt normalerweise aus dem Sammler. Zeilen, die vor dem 03.09.2026 gesammelt wurden,
    haben das Feld noch nicht — die werden hier aus ts und anpfiff nachgerechnet, statt als
    'unbekannt' aus jeder Auswertung zu fallen. Beides steht im Ledger, es ist dieselbe
    Rechnung, nur spaeter.
    """
    if w.get("phase"):
        return w["phase"]
    ts, ko = w.get("ts"), w.get("anpfiff")
    if not ts or not ko:
        return "unbekannt"
    try:
        t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        k = datetime.fromisoformat(str(ko).replace("Z", "+00:00"))
    except Exception:
        return "unbekannt"
    return "live" if t > k else "vor"


def _minute(w: dict):
    """Minuten SEIT ANPFIFF (Wanduhr), auch fuer alte Zeilen ohne das Feld.

    07.09.2026 (Uebersicht-Check) — das Feld heisst `spielminute` und ist keine: gerechnet
    wird ts minus Anpfiff, und darin steckt die Halbzeitpause. Im Bestand standen neun
    Fussball-Wetten ueber der 100. „Minute", die hoechste bei 115 (Cagliari - Lecce, Serie A,
    Anpfiff 16:30, Wette 18:24) — das Spiel war da vorbei.

    Der Name bleibt, weil die vorregistrierten Schubladen (live_frueh <= 30, live_spaet > 60)
    auf genau dieser Groesse angemeldet wurden; sie nachtraeglich umzudefinieren waere eine
    stille Aenderung an einer laufenden Messung. Die Flaechen beschriften sie jetzt als das,
    was sie ist: Zeit seit Anpfiff.
    """
    if w.get("spielminute") is not None:
        return w["spielminute"]
    if _phase(w) != "live":
        return None
    try:
        t = datetime.fromisoformat(str(w["ts"]).replace("Z", "+00:00"))
        k = datetime.fromisoformat(str(w["anpfiff"]).replace("Z", "+00:00"))
    except Exception:
        return None
    return int(round((t - k).total_seconds() / 60.0))


def _beine(w: dict):
    return ((w.get("abrechnung") or {}).get("beine")) or []


def _gewertete_beine(w: dict):
    """Nur Beine mit echtem Ausgang. Annulliert ist kein Treffer und kein Fehlschlag."""
    return [b for b in _beine(w) if b.get("treffer") is not None]


def _quote(treffer: int, n: int) -> dict:
    """Trefferquote mit Wilson-Untergrenze — als BESCHREIBUNG, nicht als Urteil.

    04.09.2026 — hier stand ein echter Denkfehler von mir: `belegt` hing an „Trefferquote
    signifikant über 50%". Das ist aus der Wallet-Logik übernommen, wo Märkte nahe bei
    Münzwurf liegen. Bei Wetten mit unterschiedlichen Quoten sagt es GAR NICHTS.

    Gemessen an den ersten 950 abgerechneten Beinen: Trefferquote 63,9% bei Ø-Quote 1,72 —
    und trotzdem **ROI −6,8%**. Jede Schublade, die nach dem alten Kriterium „BELEGT" hiess,
    verlor in Wirklichkeit Geld. Wer bei Quote 1,20 setzt, braucht 83% zum Nullpunkt.

    Das Urteil hängt seither an der RENDITE-Untergrenze (freigabe.untergrenze, dieselbe wie
    im Rest des Projekts), nicht an der Trefferquote. Die Quote bleibt stehen, weil sie die
    Zahl ist, die man lesen will — sie entscheidet nur nichts mehr.
    """
    if not n:
        return {"n": 0, "treffer": 0, "quote": None, "ug": None, "belegt": False}
    ug = wilson_lb(treffer, n, Z)
    return {"n": n, "treffer": treffer,
            "quote": round(treffer / n, 4),
            "ug": round(ug, 4) if n >= MIN_N else None,
            "belegt": False}          # wird in _schublade aus der Rendite gesetzt


def _schublade(wetten: list, bein_filter=None) -> dict:
    """Eine Schublade zählt ZWEI verschiedene Grundgesamtheiten, und das muss aus den Namen
    hervorgehen — sonst steht eine Zahl neben einer anderen, die etwas anderes meint:

      · einsatzUsd / gewinnUsd  → ALLE Einzelwetten der Schublade (auch offene).
        Was gerade auf dem Tisch liegt und was es gewinnen würde.
      · abgerechnetUsd / roi    → nur die ABGERECHNETEN Einzelwetten.
        Nur darauf lässt sich eine Rendite rechnen.

    03.09.2026: hier hiess beides erst `einsatzUsd` — die eine Zahl zählte abgerechnete
    Wetten, die andere alle. Der Test hat es gefunden, bevor irgendwo eine Rendite auf der
    falschen Basis stand.

    🔴 12.09.2026 (Lucas, Plattform-Audit) — derselbe Fehler eine Ebene tiefer, und diesmal hat
    ihn kein Test gefunden. Die Quotenbänder wählten auf `w["quote"]` aus, also auf der
    GESAMTQUOTE der Wette; gemessen wurde danach je BEIN. Bei einer Kombi sind das zwei
    verschiedene Dinge: von 7.891 Beinen in der Schublade „ab 3,50" lagen **7.094 (89,9 %)
    unter 3,50**, Median-Beinquote 1,56.

    Das war nicht folgenlos. `quote_ab_350` ist eine von genau ZWEI Schubladen mit `belegt: True`
    im ganzen Stake-Buch. Richtig gemessen — nur die Beine, die wirklich ab 3,50 stehen —
    schrumpft sie von n=7.461 auf n=773, die Ø-Quote steigt von 2,04 auf 5,80, und aus +1,8 %
    Rendite wird **−0,3 % mit einer Untergrenze von −12,1 %**. Also kein Beleg, sondern das
    Gegenteil.

    Deshalb nimmt `bein_filter` die Auswahl jetzt dort vor, wo auch gemessen wird. Und weil
    Einsatz/PnL an der WETTE hängen und sich nicht auf ein einzelnes Bein aufteilen lassen,
    bleiben die Geldfelder in einer Bein-Schublade leer statt falsch — `basis` sagt, welche
    Grundgesamtheit gemeint ist.
    """
    tr = n = 0
    renditen = []          # je Bein: (Quote-1) bei Treffer, sonst -1 — flacher Einsatz
    abgerechnet = pnl = 0.0
    einsatz = gewinn = 0.0
    n_einzel = n_offen = 0
    for w in wetten:
        if not w.get("kombi"):
            if w.get("einsatzUsd"):
                n_offen += 1
                einsatz += w["einsatzUsd"]
            g = _gewinn(w)
            if g:
                gewinn += g
        for b in _gewertete_beine(w):
            if bein_filter is not None and not bein_filter(b):
                continue
            n += 1
            tr += 1 if b["treffer"] else 0
            q = b.get("quote")
            if q and q > 1:
                renditen.append((q - 1) if b["treffer"] else -1.0)
        a = w.get("abrechnung") or {}
        if a.get("pnlUsd") is not None and w.get("einsatzUsd"):
            n_einzel += 1
            abgerechnet += w["einsatzUsd"]
            pnl += a["pnlUsd"]
    d = _quote(tr, n)
    d["wetten"] = len(wetten)
    d["einzelN"] = n_offen
    d["einsatzUsd"] = round(einsatz, 2)
    # Der Einsatz allein bevorzugt Favoritenschieber ($264k auf 1,20 für $53k), der mögliche
    # Gewinn allein Lottoscheine ($3.260 auf 298,98). Beide Zahlen stehen nebeneinander.
    d["gewinnUsd"] = round(gewinn, 2)
    d["abgerechnetN"] = n_einzel
    d["abgerechnetUsd"] = round(abgerechnet, 2)
    d["roi"] = round(pnl / abgerechnet, 4) if abgerechnet else None
    # Eine Bein-Schublade hat keine Wett-Ebene: der Einsatz einer Kombi gehört nicht dem einen
    # Bein, das den Filter passiert hat. Lieber leer als zugeordnet.
    d["basis"] = "beine" if bein_filter is not None else "wetten"
    if bein_filter is not None:
        for feld in ("einsatzUsd", "gewinnUsd", "abgerechnetUsd", "roi",
                     "wetten", "einzelN", "abgerechnetN"):
            d[feld] = None

    # Das eigentliche Urteil: Rendite je Bein bei flachem Einsatz, mit einseitiger
    # 95%-Untergrenze. Nur wenn die ÜBER null liegt, trägt die Schublade — eine
    # Trefferquote über 50% tut das nachweislich nicht (s. _quote).
    d["beinN"] = len(renditen)
    d["beinRoi"] = round(sum(renditen) / len(renditen), 4) if renditen else None
    ug_r = untergrenze(renditen) if renditen else None
    d["beinRoiUg"] = round(ug_r, 4) if ug_r is not None else None
    d["belegt"] = bool(ug_r is not None and ug_r > 0)
    d["oQuote"] = (round(sum(r + 1 for r in renditen if r > -1) / max(1, sum(1 for r in renditen if r > -1)), 3)
                   if any(r > -1 for r in renditen) else None)
    return d


# ── 1. Liga-Norm ─────────────────────────────────────────────────────────────
def liga_norm(wetten: list = None) -> dict:
    """Die gelernte Norm je Liga — aus stake_league_norm.json, NICHT aus dem Ledger.

    03.09.2026: anfangs wurde sie hier bei jedem Lauf frisch aus dem Ledger gerechnet. Das
    Ledger ist auf 20.000 Wetten gedeckelt und laeuft bei gemessenen 4,3 Wetten/Minute nach
    rund 3,2 Tagen ueber — die Norm sah also immer nur ein rollendes Drei-Tage-Fenster, und
    eine Liga, die einmal pro Woche spielt, haette darin NIE die 15 Wetten erreicht. Also
    ausgerechnet die kleinen Ligen, um die es geht, waeren dauerhaft ohne Basis geblieben.

    Derselbe Fehler wie im Betfair-Badge am 24.08. („die Basis kam aus dem MOMENT statt aus
    der ZEIT"). Deshalb fuehrt stake_league_norm.py jetzt einen wachsenden Stichprobenstand,
    und hier wird er nur noch gelesen.

    Der Parameter `wetten` bleibt fuer Tests und als Rueckfall: liegt kein Stand vor, wird
    aus dem Uebergebenen gerechnet — mit demselben MIN_N, damit nichts leiser durchrutscht.
    """
    datei = SH._lade(NORM_FILE, {})
    ligen = datei.get("ligen")
    if ligen:
        return ligen
    if wetten is None:
        return {}
    je = defaultdict(list)
    for w in wetten:
        if w.get("einsatzUsd") and w.get("liga") and not w.get("kombi"):
            je[w["liga"]].append(float(w["einsatzUsd"]))
    out = {}
    for liga, betraege in je.items():
        betraege.sort()
        n = len(betraege)
        if n < NORM_MIN_N:
            out[liga] = {"n": n, "basis": "zu duenn", "median": None, "p90": None}
            continue
        out[liga] = {"n": n, "basis": "gelernt",
                     "median": round(statistics.median(betraege), 2),
                     "p90": round(betraege[min(n - 1, int(round(0.9 * (n - 1))))], 2),
                     "max": round(betraege[-1], 2)}
    return out


def auffaellig(w: dict, norm: dict) -> dict:
    """Wie überraschend ist dieser Einsatz für diese Liga?

    -> {"faktor", "ueberErwartung", "erwartetN", "kSpanne", "basis", "n", "median"}

    `faktor` (× Median) bleibt als BESCHREIBUNG erhalten, ist aber kein Urteil mehr — er
    wächst mit der Stichprobengröße (r = +0,68 über 31 Ligen), und die Liga-Mediane liegen
    ohnehin alle um 2.000. Das Urteil ist `ueberErwartung`: wie viele Wetten dieser Größe in
    dem, was wir von dieser Liga gesehen haben, überhaupt zu erwarten waren. Begründung im
    Kopf von `stake_seltenheit.py`.

    Drei unterscheidbare Zustände, und keiner davon rendert als harmlose Zahl:
      basis "n-korrigiert" → gemessenes Urteil (`ueberErwartung` gesetzt)
      basis "nur median"   → Liga hat eine Norm, aber keinen schätzbaren Schwanz (n < 40)
      basis "keine Norm"   → über diese Liga ist nichts bekannt
    """
    liga = w.get("liga")
    e = w.get("einsatzUsd")
    nrm = (norm or {}).get(liga) or {}
    if not e or nrm.get("basis") != "gelernt" or not nrm.get("median"):
        return {"faktor": None, "ueberErwartung": None, "zufallPct": None,
                "basis": "keine Norm", "n": nrm.get("n", 0)}
    out = {"faktor": round(e / nrm["median"], 2), "ueberErwartung": None,
           "erwartetN": None, "zufallPct": None, "kSpanne": None, "basis": "nur median",
           "median": nrm["median"], "n": nrm["n"]}
    sel = seltenheit(e, nrm.get("schwanz"))
    if sel:
        out.update({"ueberErwartung": sel["ueberErwartung"], "erwartetN": sel["erwartetN"],
                    "zufallPct": sel["zufallPct"], "kSpanne": sel["kSpanne"],
                    "basis": "n-korrigiert"})
    return out


def _ueber_norm(a: dict) -> bool:
    """Zählt diese Wette als „über der Liga-Norm"? Gemessenes Urteil schlägt Median-Faktor;
    wo ein Urteil vorliegt, entscheidet NUR das. Sonst stünde eine gemessen unauffällige
    Wette hier drin, bloß weil ihr Median-Faktor groß aussieht."""
    z = a.get("zufallPct")
    if z is not None:
        return z <= AUFFAELLIG_ZUFALL
    if a.get("ueberErwartung") is not None:
        return False                     # gemessen, aber ohne Seltenheitsurteil -> kein Ja
    return a.get("basis") == "nur median" and (a.get("faktor") or 0) >= AUFFAELLIG_FAKTOR


# ── 3. Kleine Liga, grosses Geld ─────────────────────────────────────────────
def kleine_liga_gross(wetten: list, norm: dict) -> list:
    """Die eigentliche Idee: wo eine sonst ruhige Liga plötzlich Geld sieht.

    Drei Wege, in absteigender Beweiskraft — und sie werden NICHT vermischt, sondern jeder
    schreibt seinen eigenen `grund`:
      · n-korrigiert → zufallPct <= AUFFAELLIG_ZUFALL. Das einzige echte Urteil, und es
                       ist eine Wahrscheinlichkeit gegen die Nullverteilung, kein Vielfaches.
      · nur median   → Liga hat eine Norm, aber keinen schätzbaren Schwanz (n < 40).
                       Einsatz >= AUFFAELLIG_FAKTOR × Median. SCHWÄCHER, weil dieser Faktor
                       mit der Stichprobengröße wächst (04.09.: r = +0,68).
      · ohne Norm    → die Liga hat insgesamt <= KLEINE_LIGA_MAX Wetten, der Einsatz liegt
                       über dem globalen 90%-Punkt. Am schwächsten.

    Sortiert wird nach `ueberErwartung`, und Zeilen ohne dieses Urteil stehen dahinter — nicht
    davor, nur weil ihr Median-Faktor zufällig größer ist. Sonst stünde wieder oben, wer am
    längsten gesammelt hat.
    """
    je_liga = defaultdict(int)
    for w in wetten:
        if w.get("liga"):
            je_liga[w["liga"]] += 1
    alle = sorted(float(w["einsatzUsd"]) for w in wetten if w.get("einsatzUsd"))
    global_p90 = alle[min(len(alle) - 1, int(round(0.9 * (len(alle) - 1))))] if alle else None

    out = []
    for w in wetten:
        e = w.get("einsatzUsd")
        if not e or w.get("kombi"):
            continue
        a = auffaellig(w, norm)
        ue, z = a.get("ueberErwartung"), a.get("zufallPct")
        if z is not None and z <= AUFFAELLIG_ZUFALL:
            grund = ("%.1f× über Erwartung — so etwas bringt höchstens %s%% der unauffälligen "
                     "Ligen dieser Größe hervor (n%d)" % (ue, round(z * 100), a["n"]))
        elif ue is not None:
            continue                       # gemessen und unauffällig → raus, nicht durchreichen
        elif a["basis"] == "nur median" and a["faktor"] is not None and a["faktor"] >= AUFFAELLIG_FAKTOR:
            grund = ("%.1f× Median der Liga (n%d, für ein Seltenheitsurteil zu dünn)"
                     % (a["faktor"], a["n"]))
        elif (a["basis"] == "keine Norm" and global_p90 and e >= global_p90
              and je_liga.get(w.get("liga"), 0) <= KLEINE_LIGA_MAX):
            grund = "kleine Liga (%d Wetten), Einsatz über globalem 90%%-Punkt" % je_liga.get(w.get("liga"), 0)
        else:
            continue
        out.append({
            "id": w.get("id"), "ts": w.get("ts"), "liga": w.get("liga"),
            "event": w.get("event"), "eventId": w.get("eventId"),
            "markt": w.get("markt"), "auswahl": w.get("auswahl"),
            "einsatzUsd": round(e, 2), "quote": w.get("quote"), "phase": _phase(w),
            "faktor": a["faktor"], "ueberErwartung": ue, "erwartetN": a.get("erwartetN"),
            "zufallPct": a.get("zufallPct"), "kSpanne": a.get("kSpanne"),
            "basis": a["basis"], "grund": grund,
            "ausgang": ([b.get("status") for b in _beine(w)] or [None])[0],
        })
    # Gemessene Urteile zuerst (nach Überraschung), dahinter die schwächeren Kriterien.
    out.sort(key=lambda x: (0 if x.get("zufallPct") is not None else 1,
                            x.get("zufallPct") if x.get("zufallPct") is not None else 9,
                            -(x.get("ueberErwartung") or 0), -(x["faktor"] or 0)))
    return out


# ── 3b. Die Praemisse der Auffaellig-Ansicht, nachgerechnet ──────────────────
# 07.09.2026 (Lucas, Backlog: „Die 'Auffaellig'-Ansicht ehrlich beschriften" + „Achse
# umstellen auf live x Einsatzgroesse statt auffaellig ja/nein").
#
# Die Ansicht hiess „auffaellig" und war damit eine Behauptung: hier passiert etwas, das
# man beachten sollte. Gemessen stimmt das Gegenteil — im aeussersten Band liegt die
# OBERgrenze unter null. Statt diesen Satz in die Flaeche zu TIPPEN (und ihn dort veralten
# zu lassen, sobald die naechsten hundert Wetten abgerechnet sind), rechnet der Produzent
# das Urteil jedes Mal neu und die Flaeche liest es nur ab.
AUFF_BAENDER = ("3-6x", "6-15x", ">15x")   # das Gebiet, das die Ansicht „auffaellig" nennt


def norm_phase(wetten: list, norm: dict) -> dict:
    """Phase x Einsatzgroesse + das daraus abgeleitete Urteil ueber die eigene Praemisse."""
    kreuz = LS.kreuz_phase(wetten, norm, _phase)
    folgen, gegen = [], []
    # „alle" zaehlt MIT. Die staerkste Aussage ueber das aeusserste Band gibt es naemlich
    # nur gepoolt: getrennt nach Phase hat >15x n=17 und n=54, zusammen n=71 — und erst da
    # kreuzt die Obergrenze null. Die Zeile bleibt als solche erkennbar (phase="alle"),
    # damit niemand sie fuer eine dritte Phase haelt.
    for zeile, spalten in kreuz.items():
        for band, z in spalten.items():
            eintrag = {"phase": zeile, "band": band, "n": z["n"],
                       "flach": z["flach"], "flachUg": z["flachUg"], "flachOg": z["flachOg"]}
            if z["belegt"]:
                folgen.append(eintrag)
            elif z["belegtGegen"]:
                gegen.append(eintrag)
    auff_folgen = [x for x in folgen if x["band"] in AUFF_BAENDER]
    auff_gegen = [x for x in gegen if x["band"] in AUFF_BAENDER]
    # Drei Zustaende, nicht zwei: „offen" ist die ehrlichste Antwort, solange keine der
    # beiden Grenzen null kreuzt — und der haeufigste Zustand bei dieser Datenmenge.
    praemisse = ("gestuetzt" if auff_folgen else
                 "widerlegt" if auff_gegen else "offen")
    return {
        "spalten": [n for _, n in LS.STUFEN_FEIN],
        "zeilen": ["vor", "live", "alle"],
        "auffBaender": list(AUFF_BAENDER),
        "kreuz": kreuz,
        "urteil": {
            "praemisse": praemisse,
            "folgen": sorted(folgen, key=lambda x: -(x["flachUg"] or 0)),
            "gegen": sorted(gegen, key=lambda x: (x["flachOg"] if x["flachOg"] is not None else 0)),
        },
        "warum": ("Die Ansicht behauptet mit ihrem Namen, ein auffaellig grosser Einsatz sei "
                  "beachtenswert. Diese Tabelle prueft genau das — und trennt dabei nach der "
                  "Achse, die gemessen wirklich trennt: live gegen vor Anpfiff. Gewertet wird "
                  "die einseitige 95%-Grenze bei flachem Einsatz; ueber null traegt Folgen, "
                  "unter null traegt Dagegenhalten. Ein Punktschaetzer ist kein Beleg."),
    }


# ── 4. Abdeckung ─────────────────────────────────────────────────────────────
def abdeckung(led: dict) -> dict:
    l = led.get("luecke") or {}
    return {"letzteLuecke": l.get("luecke"), "lueckeMin": l.get("lueckeMin"),
            "abdeckungMin": l.get("abdeckungMin"),
            "hinweis": ("Stakes Deckel liegt bei 50 Eintraegen je Abruf. Deckt ein Abruf "
                        "weniger Zeit ab als der Abstand zum naechsten, fehlt dazwischen "
                        "alles — und zwar am ehesten dann, wenn viel los ist.")}


# ── Vorregistrierung ─────────────────────────────────────────────────────────
def vorregistrieren(jetzt: str) -> dict:
    """Welche Schubladen zaehlen — festgeschrieben, BEVOR die Zahlen da sind.

    Nachtraeglich die beste Variante auszusuchen ist genau der Fehler, den wir ueberall sonst
    rausgeraeumt haben. Diese Datei wird einmal geschrieben und danach nur ergaenzt, nie
    ueberschrieben: ein spaeter angemeldeter Trigger startet bei n=0.
    """
    reg = SH._lade(REG_FILE, {})
    kandidaten = {
        "vor_anpfiff_alle": {
            "signatur": "phase=='vor' | Bein-Trefferquote | Wilson-UG > 50%",
            "warum": "Die Grundfrage: trifft der Fluss vor Anpfiff ueberhaupt?",
            "zielN": 200,
        },
        "vor_anpfiff_gross": {
            "signatur": "phase=='vor' und einsatzUsd >= 10000",
            "warum": "Traegt Groesse allein etwas? Die STAKE-RADAR-Vorlage behauptet ja, ohne Beleg.",
            "zielN": 100,
        },
        "ueber_liga_norm": {
            "signatur": "einsatzUsd >= 5x Median der Liga (Norm aus eigenen Daten, n>=15)",
            "warum": "Die eigentliche These: auffaellig ist relativ zur Liga, nicht absolut.",
            "zielN": 100,
        },
        "kleine_liga": {
            "signatur": "Liga mit <=25 Wetten im Ledger und Einsatz ueber globalem 90%-Punkt",
            "warum": "Lucas: genug kleine Ligen, wo Leute mit guten Infos setzen.",
            "zielN": 60,
        },
        "quote_ab_135": {
            "signatur": "quote >= 1.35 | Bein-Trefferquote | Wilson-UG > 50%",
            "warum": ("Der Projekt-Boden aus der Pick-Engine, hier auf FREMDE Wetten "
                      "angewandt. Offene Frage, kein feststehender Wert."),
            "zielN": 200,
        },
        "quote_unter_135": {
            "signatur": "quote < 1.35",
            "warum": ("Die Gegenprobe. 32% der Wetten und 35% des Einsatzes liegen hier, "
                      "aber nur 3% des moeglichen Gewinns — trotzdem ist unbewiesen, dass "
                      "diese Wetten schlechter INFORMIERT sind."),
            "zielN": 150,
        },
        "randliga_hoher_einsatz": {
            "signatur": ("Fussball, Spielklasse 2 oder tiefer (Tabelle in stake_liga_stufe.py), "
                         "Einsatz >= 3x dem ueblichen Einsatz der Liga (bzw. der Ebene)"),
            "warum": ("Lucas 07.09.: 'ne 50k Wette auf Arsenal sagt 0 / Eine 50k Wette auf ein "
                      "2-3. Liga Team ist zumindest jemand der mehr dran glaubt'. Im Rueckblick "
                      "auf 4 Tage steigt der ROI dort mit der Einsatzgroesse (+7,5% -> +46,7%), "
                      "aber n=37 und n=12 in den oberen Zellen und die Schwelle wurde NACH dem "
                      "Blick gesetzt. Deshalb vorwaerts."),
            "zielN": 150,
        },
        "kleinmarkt_hoher_einsatz": {
            "signatur": ("Fussball, Liga OHNE eigene Norm (unter der MIN_N-Grenze von "
                         "stake_league_norm.py, Referenz faellt auf den Ebenen-Median), "
                         "Einsatz >= 3x dieser Referenz"),
            "warum": ("13.09.2026 (Lucas, zu einem Post eines Stake-Radar-Kanals): 'kein Burst, "
                      "sondern verdaechtige Einsaetze auf so low leagues' — eine EINZELNE Wette "
                      "von $3.486 auf Moldawien, danach als 3:0-Gewinner gefeiert.\n\n"
                      "Das ist eine ANDERE Achse als `randliga_hoher_einsatz`. Die misst die "
                      "SPIELKLASSE; Moldawiens Divizia Nationala ist aber Ebene 1 — oberste "
                      "Klasse eines kleinen Landes. Gemeint ist die MARKTGROESSE, und die faellt "
                      "mit der Spielklasse nicht zusammen.\n\n"
                      "Rueckwirkend gemessen (Fussball-Einzelwetten mit Endstand, Liga-Groesse "
                      "ueber das Feed-Volumen genommen) sieht sie SCHLECHT aus:\n"
                      "    >=3x   kleine Liga n=51 ROI  +4,0 % (UG -14,1 %) · grosse n=942 +7,8 %\n"
                      "    >=6x   kleine Liga n=18 ROI -18,3 %              · grosse n=480 +3,4 %\n"
                      "    >=10x  kleine Liga n= 4 ROI -19,7 %              · grosse n=288 +0,1 %\n"
                      "(alle Fussball-Einzelwetten zum Vergleich: +1,2 %)\n\n"
                      "Sie steht hier trotzdem — gerade WEIL sie rueckwirkend schlecht aussieht. "
                      "Die Schwelle 3x ist nicht neu gewaehlt, sondern die bestehende LS.KAND_AB; "
                      "die Grenze 'kleiner Markt' ist nicht erfunden, sondern die Linie, die "
                      "stake_league_norm.py ohnehin zieht (eine Liga mit zu wenig Bestand bekommt "
                      "keine eigene Norm). Damit ist nichts an dieser Schublade nachtraeglich "
                      "passend gemacht. Vorwaerts gemessen sagt sie, ob an der These etwas ist."),
            "zielN": 150,
        },
        "grossmarkt_hoher_einsatz": {
            "signatur": ("Fussball, Liga MIT eigener Norm, Einsatz >= 3x dem ueblichen Einsatz "
                         "dieser Liga"),
            "warum": ("Die Gegenprobe auf derselben Achse — ohne sie misst die Schublade darueber "
                      "nur, wie gross Einsaetze sind, nicht wo. Dieselbe Schwelle (3x), damit der "
                      "Unterschied wirklich der Markt ist und nicht die Grenze."),
            "zielN": 200,
        },
        "topliga_hoher_einsatz": {
            "signatur": ("Fussball, oberste Spielklasse, Einsatz >= 6x dem ueblichen Einsatz "
                         "der Liga"),
            "warum": ("Die Gegenprobe zur Randliga-These und fuer sich interessant: im "
                      "Rueckblick faellt der ROI hier mit steigendem Einsatz (-2,1% -> -13,9%, "
                      "n=207 in der obersten Zelle). Faellt das vorwaerts genauso aus, ist es "
                      "kein Folge- sondern ein Gegensignal."),
            "zielN": 200,
        },
        "live_frueh": {
            "signatur": "phase=='live' und Spielminute <= 30",
            "warum": ("Live ist 83% des Feeds, aber spaet im Spiel wertlos (Hapoel-Fall). "
                      "Wenn Live etwas taugt, dann frueh."),
            "zielN": 150,
        },
    }
    neu = 0
    for name, k in kandidaten.items():
        if name not in reg:
            reg[name] = dict(k, angemeldet=jetzt, rueckblick=None)
            neu += 1
    if neu:
        SH._schreibe(REG_FILE, reg)
    return reg


# ── Hauptlauf ────────────────────────────────────────────────────────────────
def auswerten(led: dict, jetzt: str) -> dict:
    alle = led.get("wetten") or []
    # 03.09.2026 (Lucas): US-Sport ist gesperrt — aber nur fuer die Anzeige und das Urteil.
    # Gesammelt und ABGERECHNET wird weiter alles, und die gesperrten Sportarten bekommen ihre
    # eigene Schublade. Sonst koennte man nie merken, dass eine davon dreht; genau diese
    # Entscheidung steht im Poly-Fall vom 24.08. schon so im Code.
    wetten = [w for w in alle if _erlaubt(w)]
    gesperrt = [w for w in alle if not _erlaubt(w)]
    norm = liga_norm(wetten)
    # Der Referenzeinsatz je Spielklasse. Einmal gerechnet, nicht je Wette — sonst laeuft
    # der Median ueber jede Filterzeile neu.
    ebmed = LS.ebene_median(wetten)

    def filt(f):
        return [w for w in wetten if f(w)]

    schubladen = {
        "gesamt": _schublade(wetten),
        "vor_anpfiff": _schublade(filt(lambda w: _phase(w) == "vor")),
        "live": _schublade(filt(lambda w: _phase(w) == "live")),
        "live_frueh": _schublade(filt(lambda w: _phase(w) == "live"
                                      and (_minute(w) or 999) <= 30)),
        "live_spaet": _schublade(filt(lambda w: _phase(w) == "live"
                                      and (_minute(w) or 0) > 60)),
        "einsatz_ab_10k": _schublade(filt(lambda w: (w.get("einsatzUsd") or 0) >= 10000)),
        "einsatz_1k_10k": _schublade(filt(lambda w: 1000 <= (w.get("einsatzUsd") or 0) < 10000)),
        "ueber_liga_norm": _schublade(filt(
            lambda w: _ueber_norm(auffaellig(w, norm)))),
        # 07.09.2026 (Lucas: „ne 50k Wette auf Arsenal sagt 0 / Eine 50k Wette auf ein
        # 2-3. Liga Team ist zumindest jemand der mehr dran glaubt"). Zwei Schubladen, weil
        # gemessen zwei GEGENLAEUFIGE Reihen dastehen: auf Ebene 1 wird der Fluss mit
        # steigendem Einsatz schlechter (-2,1% -> -13,9%), auf Ebene 2/3 besser
        # (+7,5% -> +46,7%). Eine gemeinsame „grosser Einsatz"-Schublade mittelt genau
        # diesen Unterschied weg — das ist die Dilutions-Klasse aus CAPABILITIES §7.
        "randliga_hoher_einsatz": _schublade(filt(
            lambda w: _ls_faktor(w, norm, ebmed)[0] is not None
            and LS.randliga(w.get("ligaSlug"), w.get("sport"), w.get("ligaId"))
            and _ls_faktor(w, norm, ebmed)[0] >= LS.KAND_AB)),
        "topliga_hoher_einsatz": _schublade(filt(
            lambda w: _ls_faktor(w, norm, ebmed)[0] is not None
            and LS.stufe(w.get("ligaSlug"), w.get("sport"), w.get("ligaId")) == "1"
            and _ls_faktor(w, norm, ebmed)[0] >= LS.TOP_AB)),
        # 13.09.2026 — die ZWEITE Achse: Marktgroesse statt Spielklasse (s. Vorregistrierung).
        # `basis == "ebene"` heisst: diese Liga hat keine eigene Norm, weil sie zu duenn ist —
        # genau die Linie, die stake_league_norm.py schon zieht. Keine neue Zahl erfunden.
        "kleinmarkt_hoher_einsatz": _schublade(filt(
            lambda w: _ls_faktor(w, norm, ebmed)[0] is not None
            and LS.stufe(w.get("ligaSlug"), w.get("sport"), w.get("ligaId")) is not None
            and _ls_faktor(w, norm, ebmed)[1] == "ebene"
            and _ls_faktor(w, norm, ebmed)[0] >= LS.KAND_AB)),
        "grossmarkt_hoher_einsatz": _schublade(filt(
            lambda w: _ls_faktor(w, norm, ebmed)[0] is not None
            and LS.stufe(w.get("ligaSlug"), w.get("sport"), w.get("ligaId")) is not None
            and _ls_faktor(w, norm, ebmed)[1] == "liga"
            and _ls_faktor(w, norm, ebmed)[0] >= LS.KAND_AB)),
    }

    # 03.09.2026 (Lucas: „glaub Odds-Schwelle sollten wir auch bauen ... wollen wir die 1,35
    # wieder als Minimum?"). Die 1,35 ist im Projekt schon der Boden (pick-engine.js, „Cheap
    # ML filter") — aber DORT geht es um unsere eigenen Wetten, wo bei 1,20 die Marge den Wert
    # frisst. Hier geht es um die Meinung eines ANDEREN, und ob die bei 1,20 weniger wert ist,
    # ist bisher nicht gemessen. Also: Bänder als eigene Schubladen, gemessen statt gesetzt.
    # Zur Einordnung, an 445 Wetten: unter 1,35 liegen 32% der Wetten und 35% des Einsatzes,
    # aber nur 3% des möglichen Gewinns.
    # 12.09.2026: Auswahl je BEIN (s. _schublade). Vorher wurde auf der Gesamtquote der Wette
    # ausgewählt und auf den Beinen gemessen — bei Kombis zwei verschiedene Grundgesamtheiten.
    def _bein_band(lo, hi):
        return lambda b: isinstance(b.get("quote"), (int, float)) and lo <= b["quote"] < hi
    for name, lo, hi in QUOTEN_BAENDER:
        schubladen["quote_" + name] = _schublade(wetten, bein_filter=_bein_band(lo, hi))
    schubladen["quote_ab_135"] = _schublade(wetten, bein_filter=_bein_band(1.35, 1e9))
    schubladen["quote_unter_135"] = _schublade(wetten, bein_filter=_bein_band(1.0, 1.35))

    je_liga = defaultdict(list)
    je_markt = defaultdict(list)
    for w in wetten:
        if w.get("liga"):
            je_liga[w["liga"]].append(w)
        if w.get("markt"):
            je_markt[w["markt"]].append(w)

    def top(d, min_n):
        out = {}
        for k, ws in d.items():
            s = _schublade(ws)
            if s["n"] >= min_n:
                out[k] = s
        return dict(sorted(out.items(), key=lambda kv: -kv[1]["n"])[:25])

    je_gesperrt = defaultdict(list)
    for w in gesperrt:
        je_gesperrt[SH.sperr_grund(w) or _kat(w)].append(w)

    b = led.get("bilanz") or {}
    return {
        "asof": jetzt,
        "seit": led.get("seit"),
        "nWetten": len(wetten),
        "nGesamt": len(alle),
        "gesperrt": sorted(SH.GESPERRT),
        "nGesperrt": len(gesperrt),
        # Weiter mitgeschrieben, nur nicht mitgezaehlt: so faellt auf, wenn eine gesperrte
        # Sportart auf einmal traegt. Ein Wiedereintritt braucht Zahlen, keine Meinung.
        "gesperrteSchubladen": {k: _schublade(v) for k, v in je_gesperrt.items()},
        "bilanz": b,
        "reif": bool(b.get("gewertet", 0) >= MIN_N),
        "urteilAb": MIN_N,
        "schubladen": schubladen,
        "jeLiga": top(je_liga, MIN_N_LIGA),
        "jeMarkt": top(je_markt, MIN_N_LIGA),
        "ligaNorm": dict(sorted(norm.items(), key=lambda kv: -kv[1]["n"])[:60]),
        "auffaellige": kleine_liga_gross(wetten, norm)[:60],
        "randliga": LS.block(wetten, norm),
        "normPhase": norm_phase(wetten, norm),
        "abdeckung": abdeckung(led),
        "hinweis": ("Das Urteil haengt an der RENDITE-Untergrenze, nicht an der Trefferquote: "
                    "gemessen an den ersten 950 Beinen liegt die Trefferquote bei 63,9%%, die "
                    "Durchschnittsquote bei 1,72 — und der ROI trotzdem bei -6,8%%. Wer bei 1,20 "
                    "setzt, braucht 83%% zum Nullpunkt. Eine Schublade traegt nur, wenn ihre "
                    "Rendite-Untergrenze (n >= %d) ueber null liegt. Der Feed ist anonym — es "
                    "gibt aggregierten Fluss, nie einen Track-Record je Konto." % MIN_N),
    }


def main() -> int:
    print("=== stake_analyse.py ===")
    jetzt = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    led = SH._lade(LEDGER_FILE, {})
    if not (led.get("wetten") or []):
        print("  ℹ️  leeres Ledger — nichts auszuwerten.")
        return 0
    vorregistrieren(jetzt)
    a = auswerten(led, jetzt)
    SH._schreibe(OUT_FILE, a)

    b = a["bilanz"]
    print("  Ledger %d Wetten seit %s (%d gesperrt: %s)"
          % (a["nWetten"], (a.get("seit") or "?")[:16], a["nGesperrt"], ", ".join(a["gesperrt"])))
    print("  Beine gewertet: %d (offen %d, unaufloesbar %d)"
          % (b.get("gewertet", 0), b.get("offen", 0), b.get("unaufloesbar", 0)))
    if not a["reif"]:
        print("  ⏳ Noch kein Urteil: unter n=%d wird nur gezaehlt, nicht bewertet." % MIN_N)
    for name in ("vor_anpfiff", "live", "live_frueh", "ueber_liga_norm"):
        s = a["schubladen"][name]
        if s["n"]:
            print("   %-16s n=%-4d Treffer %s  ROI %s  UG %s  %s"
                  % (name, s["n"],
                     ("%.1f%%" % (s["quote"] * 100)) if s["quote"] is not None else "—",
                     ("%+.1f%%" % (s["beinRoi"] * 100)) if s.get("beinRoi") is not None else "—",
                     ("%+.1f%%" % (s["beinRoiUg"] * 100)) if s.get("beinRoiUg") is not None else "—",
                     "TRAEGT" if s["belegt"] else ""))
    n_auf = len(a["auffaellige"])
    print("  Auffaellige Einsaetze (ueber Liga-Norm oder kleine Liga): %d" % n_auf)
    ligen = sum(1 for v in a["ligaNorm"].values() if v["basis"] == "gelernt")
    print("  Liga-Normen gelernt: %d von %d Ligen" % (ligen, len(a["ligaNorm"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
