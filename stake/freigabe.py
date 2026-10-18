#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
freigabe.py — 29.08.2026 (Lucas: „eine Sektion auf der Seite, die ich blind nehmen kann,
waere mir das Wichtigste").

## Warum das anders funktioniert als eine Bestenliste

Eine Sektion, der man blind folgt, darf NICHT „die staerksten Plays von heute" zeigen. Die
staerksten Plays von heute gibt es jeden Tag — auch an Tagen, an denen nichts taugt. Was sie
zeigen muss, sind Plays aus einer SCHUBLADE, die sich bewiesen hat.

Schublade heisst je Strom das, was dort natuerlich ist: bei Poly der Conviction-Bucket bzw. der
Signal-Mix, bei den Cards Verdict x Datensatz, bei Betfair Liga x Markt. Nicht der einzelne Play
wird freigegeben, sondern die Schublade, aus der er kommt. Ein Play aus einer freigegebenen
Schublade ist blind spielbar; derselbe Play aus einer ungeprueften ist es nicht.

## Der Stand am Tag, an dem das hier entstand

Gerechnet ueber alle drei Ledger war GENAU EINE Schublade belegt positiv: Poly Conviction 9
(n=14, ROI +40,8%, Untergrenze +10,9%). Alles andere — inklusive der Public-Kandidaten (n=190,
ROI +2,4%, Untergrenze -6,7%) — ist von null nicht unterscheidbar. Das ist der ehrliche Stand,
und diese Datei ist gebaut, um ihn auszuhalten: sie darf leer ausgehen, und dann sagt sie, wie
weit jede Schublade noch ist. Leer ist ein Ergebnis.

## Warum CLV als zweites Kriterium, nicht nur ROI

Um +2% ROI statistisch zu belegen, braucht es auf diesen Maerkten viele hundert Plays — die
Streuung der Einstiegspreise frisst das Signal. CLV hat ein Zehntel der Varianz und steht schon
beim Anpfiff fest, ist also die schnelle Spur.

Wichtiger noch: CLV ist der Gluecks-Filter. Die Cards standen bei ROI +3,2% und sahen damit
spielbar aus — ihr CLV lag aber bei -2,01pp mit Untergrenze -2,43 ueber 216 Picks. Positiver ROI
bei sicher negativem CLV heisst: wir kaufen systematisch schlechtere Preise als der Markt am Ende
sagt, und der Gewinn kam aus der Varianz. Ohne CLV-Bedingung waere genau das freigegeben worden.

## Was hier NICHT passiert

Nichts wird gesetzt, gesendet oder veraendert. Diese Datei rechnet und schreibt freigabe.json.
Netzfrei, rein, testbar.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sharp_gate as SG


def _row_cat(r):
    """Kategorie einer Zeile — ueber den kanonischen Helfer, nicht ueber das rohe Feld.

    Nicht jede Zeile traegt einen `cat`-Stempel; `poly_shortlist_track._row_cat` leitet ihn dann
    aus der Liga ab. Ohne ihn landete eine ungestempelte UFC-Zeile im bespielbaren Topf — genau
    der Fehler, gegen den `tests/test_shortlist_kategorie.py` einen Waechter haelt. Faellt der
    Import aus, bleibt das rohe Feld die Notloesung; das ist schlechter als der Helfer, aber
    besser als gar kein Filter.
    """
    try:
        from poly_shortlist_track import _row_cat as _rc
    except Exception:
        return r.get("cat")
    return _rc(r)

BASE = Path(os.environ.get("STAKE_DATEN") or Path(__file__).resolve().parent)  # money-radar: Daten in .st/
OUT_FILE = "freigabe.json"

# ── Die Freigabe-Bedingungen ────────────────────────────────────────────────────────────
# Alle vier muessen gelten. Faellt eine, faellt die Freigabe — automatisch, ohne Zutun.
MIN_N        = int(os.environ.get("FREIGABE_MIN_N") or 30)    # genug Historie
Z            = 1.645                                          # einseitige 95%-Grenze
NOETIG_MIN_N = 10     # so viele Plays braucht schon die HOCHRECHNUNG — aus 2 Werten gibt es
                      # keine Streuung, und ohne Streuung ist die Zahl frei erfunden
MIN_ROI_LB   = 0.0                                            # ROI-Untergrenze ueber null
MIN_CLV_LB   = 0.0                                            # CLV-Untergrenze nicht negativ
# Ab so vielen Plays wird eine Schublade ueberhaupt als „Kandidat" gefuehrt (darunter: sammelt).
KANDIDAT_N   = int(os.environ.get("FREIGABE_KANDIDAT_N") or 10)
# 29.08.2026 (Lucas: „Was heisst bis WM BET? Was WM?") — DIE LEBENDIG-BEDINGUNG.
# Erste Fassung fuehrte „WM · BET" als Kandidat mit „noch 5 Plays". Die WM ist seit dem 28.06.
# durch: die fuenf Plays kommen nie, und selbst freigegeben waere die Schublade nicht spielbar.
# Freigegeben muss heissen „das kannst du MORGEN spielen", nicht „das hat mal funktioniert".
# Also: der juengste abgerechnete Play einer Schublade darf nicht aelter sein als das hier.
# Aeltere Schubladen bekommen den Status „ruht" — sie verschwinden nicht (die Historie bleibt
# lesbar), sie stehen nur nicht mehr in der Warteschlange auf eine Freigabe.
MAX_ALTER_TAGE = int(os.environ.get("FREIGABE_MAX_ALTER_TAGE") or 21)

# ── 08.09.2026: „Freigabe locker" ───────────────────────────────────────────────────────
# Lucas, seit Wochen und heute noch einmal ausdruecklich: er haelt nichts vom CLV als Kriterium.
# Sein Massstab ist die Trefferquote und der ROI, der daraus folgt. Das Tor folgt ihm — aber nur
# so weit, wie die Daten es zulassen, und der Rest steht dabei.
#
# WAS SICH AENDERT: das Tor ist ab jetzt die ROI-Untergrenze (plus Mindestzahl und Lebendigkeit).
# Der CLV blockiert nicht mehr, er BESCHREIBT — als eigenes Feld `clvUrteil`, das auf jeder
# freigegebenen Zeile mitlaeuft.
#
# WAS DAS HEUTE BEWIRKT, gemessen am Stand vom 08.09.:
#   74 reife Schubladen · 2 mit belegter ROI-Untergrenze · 0 davon ohne CLV-Wert
#   → freigegeben werden genau diese zwei: Public-Kandidaten (n=35, ROI-UG +2,3 %)
#     und Liga · ABWAEGEN (n=91, ROI-UG +0,8 %).
#
# ⚠️ UND DER EINWAND, DER DAZUGEHOERT: beide haben eine NEGATIV BELEGTE CLV-Untergrenze
# (−2,53 pp und −2,01 pp). In unseren eigenen Daten ist der CLV monoton praediktiv (n=2.651:
# CLV>0 → ROI +7,5 %, Untergrenze +1,9 %; CLV<0 → ROI −6,8 %). Diese zwei Schubladen sind also
# genau der Fall, vor dem der CLV warnt. Das ist Lucas' Entscheidung und sie ist vertretbar —
# ein CLV-Kriterium, das seit Wochen jeden Tag alles blockiert, beantwortet die Frage auch nicht.
# Aber verschwiegen wird es nicht: die Warnung steht auf der Zeile, nicht in einer Fussnote.
#
# `FREIGABE_CLV_BLOCKT=1` stellt das alte, strenge Tor wieder her — ohne Code-Aenderung.
CLV_BLOCKT = str(os.environ.get("FREIGABE_CLV_BLOCKT") or "").lower() in ("1", "ja", "true", "on")


def _now():
    return datetime.now(timezone.utc)


def _load(name, default=None):
    try:
        return json.loads((BASE / name).read_text(encoding="utf-8"))
    except Exception:
        return {} if default is None else default


UG_MIN_N = 30   # unter so vielen Werten geben wir gar keine Untergrenze aus — siehe unten


def untergrenze(werte, z: float = Z, min_n: int = None):
    """Einseitige 95%-Untergrenze des Mittelwerts (Normalapproximation).

    Bewusst simpel: bei n>=30 ist die Approximation gutmuetig genug, und die Alternative
    (Bootstrap) macht das Ergebnis nicht belastbarer, nur schwerer nachzurechnen.

    🔴 03.09.2026 (Lucas-Checkup der Uebersicht). Die Grenze stand bei drei Werten, und was
    darueber herauskam, war keine Untergrenze, sondern der Mittelwert mit einem Etikett:

        untergrenze([1.35, 1.35, 1.35]) -> +1.35

    Drei aehnliche Ergebnisse ergeben eine Streuung nahe null, und ohne Streuung faellt die
    Schranke auf den Punktschaetzer zusammen. In der Uebersicht stand deshalb
    „Mix bf+money+sharp 3/30 · ROI +135% (UG +74%)" — 74% Rendite „belegt" aus drei Plays.
    Dieselbe Krankheit wie die geklemmte Varianz in der Whale-Rangliste am 02.09.: eine kleine
    Stichprobe gibt sich als Gewissheit aus. Ein Punktschaetzer ist kein Beleg, und ein
    Punktschaetzer mit „UG" davor ist schlimmer als einer ohne.

    Ab jetzt ist die Grenze dort, wo die Approximation laut eigenem Anspruch traegt: n>=30 —
    dieselbe Zahl, die auch das Freigabe-Gate verlangt. Die 17 Schubladen unter 30 zeigen
    weiterhin ihren ROI, aber „UG —" statt einer Zahl, hinter der nichts steht.

    Am GATE aendert das nichts: `bewerte` fragt die Untergrenze erst ab n>=MIN_N (30) ueberhaupt
    ab. Was sich aendert, ist die CLV-Seite — ein clvLb aus 10 CLV-Werten neben 40 Renditen gibt
    es nicht mehr, und ohne clvLb bleibt die Schublade „geprueft". Auch das ist gewollt: die
    CLV-Bedingung existiert genau deshalb, weil sie Glueck von Kante trennt.

    `min_n` ist ueberschreibbar, damit Tests die Grenze benennen koennen statt sie zu umgehen.
    """
    n = len(werte)
    grenze = UG_MIN_N if min_n is None else min_n
    if n < max(3, grenze):
        return None
    m = sum(werte) / n
    var = sum((x - m) ** 2 for x in werte) / (n - 1)
    return m - z * math.sqrt(var) / math.sqrt(n)


def obergrenze(werte, z: float = Z, min_n: int = None):
    """Einseitige 95%-OBERgrenze desselben Mittelwerts — das Spiegelbild von `untergrenze`.

    08.09.2026. Sie wird gebraucht, seit der CLV nicht mehr blockiert, sondern beschreibt:
    „negativ belegt" heisst ab jetzt, was es heissen muss — die OBERgrenze liegt unter null.
    Vorher stand dieses Etikett an jeder Schublade, deren Untergrenze unter null lag, und das
    ist etwas voellig anderes: eine Schublade mit CLV ±0,0 und breiter Streuung hat eine
    Untergrenze unter null, ohne dass irgendetwas gegen sie gemessen waere. Solange der CLV das
    Tor war, fiel das nicht auf (beide Faelle wurden gleich behandelt); als Auskunft AUF der
    Zeile waere es eine Behauptung ueber Daten, die es nicht gibt.

    Dieselbe Doktrin wie ueberall hier, nur nach oben: Folgen beweist die Untergrenze ueber
    null, Faden die Obergrenze unter null. Dazwischen liegt „gemessen, nicht belegt".
    """
    n = len(werte)
    grenze = UG_MIN_N if min_n is None else min_n
    if n < max(3, grenze):
        return None
    m = sum(werte) / n
    var = sum((x - m) ** 2 for x in werte) / (n - 1)
    return m + z * math.sqrt(var) / math.sqrt(n)


def _mittel(werte):
    return (sum(werte) / len(werte)) if werte else None


def _rnd(v, n):
    """Runden, aber None bleibt None. Eine fehlende Kennzahl ist keine 0."""
    return round(v, n) if isinstance(v, (int, float)) else None


def _alter_tage(letzter, now):
    """Tage seit dem juengsten abgerechneten Play. None, wenn kein Zeitstempel da ist —
    und None wird wie „unbekannt alt" behandelt, nicht wie „frisch"."""
    if not letzter:
        return None
    try:
        t = datetime.fromisoformat(str(letzter).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (now - t).total_seconds() / 86400.0


def noetiges_n(werte) -> int | None:
    """Ab welchem n laege die 95%-Untergrenze ueber null, WENN Schnitt und Streuung so blieben?

    06.09.2026, Lucas: „ok, da hat es noch nichts rein geschafft? In 12 Plays nächste Chance …
    könnte für immer leer sein eigentlich."

    Beim Nachrechnen seiner Frage stand auf dem Board bei der einzigen lebendigen Schublade
    „15 von 30 Plays — noch 15". Das liest sich wie „gleich geschafft". Die echten Zahlen:

        Konjunktion · Top-5 + MLS   ROI  Schnitt +9,7 %  Streuung 0,958  ->  UG>0 ab n≈263
                                    CLV  Schnitt +1,91pp Streuung 2,980  ->  UG>0 ab n≈7 (erfuellt)

    `MIN_N` = 30 ist eine MINDESTZAHL, keine hinreichende. Bei Renditen ist die Streuung ungefaehr
    zehnmal so gross wie der Schnitt — das ist der Grund, warum CLV der schnelle Richter ist und
    ROI der langsame. „Noch 15" war also wieder ein Satz, der behauptet, was die Zahl daneben
    widerlegt (Bug-Klasse 1), diesmal in der Fortschrittsangabe selbst.

    ⚠️ Das hier ist eine HOCHRECHNUNG, keine Messung: sie schreibt den heutigen Schnitt und die
    heutige Streuung fort. Sie sagt nicht voraus, dass es klappt — sie sagt, wie weit es waere,
    falls alles so bleibt. Bei nicht-positivem Schnitt gibt es keine Zahl: mehr Plays bestaetigen
    dann das Negative, sie drehen es nicht. REIN/testbar."""
    xs = [float(x) for x in (werte or []) if isinstance(x, (int, float))]
    # 🔴 Eigener Fehler, noch am selben Abend gefunden: hier stand `len(xs) < 2`. Damit meldete
    # die Funktion fuer „Conviction 8" (n=4, ROI +77 %) und „WM · BEOBACHTEN" (n=2, ROI +81 %)
    # ein noetiges n von 2 bis 4 — also „fertig", aus zwei gluecklichen Plays. Aus zwei Werten
    # laesst sich keine Streuung schaetzen; die Formel bekam nur eine winzige sd geliefert und
    # antwortete brav. Genau die Krankheit, gegen die dieses Register gebaut ist, in dem Code,
    # der sie messen soll: ein Punktschaetzer entscheidet.
    if len(xs) < NOETIG_MIN_N:
        return None
    m = sum(xs) / len(xs)
    if m <= 0:
        return None
    var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    # Reine statistische Groesse, NICHT auf MIN_N geklemmt. Der erste Entwurf klemmte hier —
    # damit kamen fuer ROI und CLV beide „30" heraus, und der ganze Punkt (CLV braucht ~7,
    # ROI ~260) war weg. Wo die Mindestzahl gilt, gilt sie beim VERWENDEN, nicht beim Rechnen.
    return len(xs) if var <= 0 else int(math.ceil((Z * math.sqrt(var) / m) ** 2))


NOETIG_GRUND_TEXT = {
    "keine_einzelwerte": "aus einem Aggregat gerechnet \u2014 fuer eine Hochrechnung fehlen die "
                         "Einzelrenditen",
    "zu_wenige":         "fuer eine Hochrechnung sind es noch zu wenige Plays",
    "nicht_positiv":     "der ROI-Schnitt ist nicht positiv \u2014 mehr Plays bestaetigen das, "
                         "sie drehen es nicht",
}


def noetig_grund(werte, n=None):
    """WARUM es keine Hochrechnung gibt. None heisst: es gibt eine. REIN.

    🔴 25.09.2026 (Uebersicht-Check). `noetiges_n` gibt `None` aus DREI verschiedenen
    Gruenden zurueck, und der Aufrufer konnte sie nicht unterscheiden. Das Board hat aus der
    Leerstelle einen einzigen Satz gemacht — „Schnitt nicht positiv" — und ihn an 25 Zeilen
    geschrieben. Nachgezaehlt, auf welche er zutraf: **null**.

        Betfair-Aggregat, gar keine Einzelrenditen   12   (11 davon mit BELEGTER Untergrenze)
        unter NOETIG_MIN_N Werten                    12
        ruhende Zeile, Feld fehlt ganz                1
        Schnitt wirklich <= 0                         0

    Die schlimmste Zeile war `Kazakhstan Premier League · Match Odds`: ROI +43,8 %,
    Untergrenze +12,6 %, im Register als bestes Betfair-Fach mit einem Stern — und daneben
    stand, ihr Schnitt sei nicht positiv. Ursache ist `bewerte(name, "betfair", [], [])`: die
    Betfair-Faecher kommen aus einem Aggregat (n, hit, roi), `noetiges_n([])` faellt also am
    Mindest-n und nicht am Vorzeichen.

    Fehlerklasse: *fehlende Information rendert als Behauptung* — und hier nicht als harmloser
    Default, sondern als das Gegenteil der Wahrheit.

    Der Grund gehoert dorthin, wo die Zahl entsteht. Ein Frontend, das ihn aus einer Leerstelle
    errechnet, baut Produzenten-Logik nach — in einer Kopie, die weniger weiss als das Original.
    """
    xs = [float(x) for x in (werte or []) if isinstance(x, (int, float))]
    if not xs:
        # Kein einziger Einzelwert. Traegt die Zeile trotzdem Plays, stammt sie aus einem
        # Aggregat; ohne Plays ist sie einfach leer.
        return "keine_einzelwerte" if (n or 0) > 0 else "zu_wenige"
    if len(xs) < NOETIG_MIN_N:
        return "zu_wenige"
    if sum(xs) / len(xs) <= 0:
        return "nicht_positiv"
    return None


def entfernung(werte, ziehungen: int = 600, saat: int = 7) -> dict | None:
    """Wie weit ist ein Beleg — MIT der Unsicherheit dieser Schaetzung. REIN (feste Saat).

    🔴 06.09.2026, zwei Stunden nach `noetiges_n` gebaut, weil die Zahl selbst nicht hielt:

        20:1x Uhr   Konjunktion Top-5   n=15   ->  „rund 263 Plays noetig"
        20:4x Uhr   ein Play spaeter    n=16   ->  „rund 3053 Plays noetig"

    Ein einziger neuer Play hat die Hochrechnung um den Faktor 12 bewegt. Ich hatte Lucas daraus
    „rund vier Monate" gerechnet — das war meine zweite zu praezise Aussage an dem Tag.

    Nachgemessen per Bootstrap auf den echten Konjunktions-Zeilen (4.000 Ziehungen):

        bei n=15   Median  71   5–95 %      9 –  5833   ·  in 32 % der Ziehungen gar nicht positiv
        bei n=30   Median 166   5–95 %     31 – 11543   ·  in 19 %
        bei n=49   Median 131   5–95 %     20 –  8681   ·  in 25 %

    Die Entfernung ist bei diesen Stichproben schlicht **nicht schaetzbar**. Eine einzelne Zahl
    dorthin zu schreiben ist derselbe Fehler wie ein ROI ohne Untergrenze — nur eine Ebene
    hoeher: diesmal traegt der Punktschaetzer der PROGNOSE keine Schranke.

    Zurueck kommt deshalb nie eine nackte Zahl, sondern {median, lo, hi, nieAnteil, schaetzbar}.
    `schaetzbar` ist falsch, sobald die Spanne ueber eine Groessenordnung geht oder ein
    nennenswerter Teil der Ziehungen gar keinen positiven Schnitt hat.
    """
    xs = [float(x) for x in (werte or []) if isinstance(x, (int, float))]
    if len(xs) < NOETIG_MIN_N:
        return None
    import random as _r
    rng = _r.Random(saat)
    endlich, nie = [], 0
    for _ in range(ziehungen):
        probe = [xs[rng.randrange(len(xs))] for _ in xs]
        k = noetiges_n(probe)
        if k:
            endlich.append(k)
        else:
            nie += 1
    if not endlich:
        return {"median": None, "lo": None, "hi": None, "nieAnteil": 1.0, "schaetzbar": False}
    endlich.sort()
    lo = endlich[int(0.05 * (len(endlich) - 1))]
    hi = endlich[int(0.95 * (len(endlich) - 1))]
    med = endlich[len(endlich) // 2]
    nie_anteil = nie / float(ziehungen)
    # Schaetzbar heisst: die Spanne bleibt innerhalb einer Groessenordnung UND der Schnitt ist
    # in fast allen Ziehungen positiv. Beides ist heute bei keiner Schublade der Fall — genau
    # das soll die Oberflaeche sagen duerfen, statt eine Zahl zu erfinden.
    # Zwei Wege zu „schaetzbar": entweder liegt selbst das pessimistische Ende schon unter der
    # Mindestzahl — dann ist die Prognose gar nicht die bindende Groesse, sondern MIN_N —, oder
    # die Spanne bleibt innerhalb einer Groessenordnung. Ohne den ersten Zweig faellt eine sehr
    # enge Verteilung durch, nur weil 14 mehr als das Zehnfache von 1 ist.
    schaetzbar = (nie_anteil <= 0.10) and (hi <= MIN_N or hi <= 10 * max(1, lo))
    return {"median": med, "lo": lo, "hi": hi,
            "nieAnteil": round(nie_anteil, 3), "schaetzbar": bool(schaetzbar)}


def clv_urteil(clv, clv_lb, clv_og) -> str:
    """Der CLV-Zustand einer Schublade — aus DEN FELDERN, die am Ende auf der Zeile stehen.

    🔴 15.09.2026 (Lucas-Uebersicht-Check). Im Register standen 8 von 200 Zeilen mit einer
    CLV-Zahl NEBEN dem Urteil „· kein CLV" — „Public-Pushes −2,35 pp · kein CLV",
    „Public · Halbzeit −18,18 pp · kein CLV". Beides gleichzeitig kann nicht stimmen.

    Die Ursache ist keine Rechenfehler, sondern die Reihenfolge: `bewerte()` bekommt von den
    Aggregat-Quellen (Betfair-Track, Betfair-Public) eine LEERE CLV-Liste und schreibt darum
    korrekt „nicht erhoben" — und ERST DANACH setzt der Aufrufer `eintrag["clv"] = avgClvBf`.
    Das Urteil beschrieb einen Stand, den die Zeile nicht mehr hatte. Dieselbe Klasse wie die
    Cards-Kachel am selben Tag: ein Wort, das etwas anderes benennt als die Zahl daneben.

    Deshalb steht die Regel ab jetzt genau einmal hier, und `baue()` zieht sie ueber JEDE Zeile
    nach, nachdem alle Quellen ihre Felder gesetzt haben. Wer spaeter ein `clv` nachtraegt,
    bekommt das passende Urteil automatisch mit.
    """
    if clv_lb is not None and clv_lb > MIN_CLV_LB:
        return "belegt"
    if clv_og is not None and clv_og < MIN_CLV_LB:
        return "negativ belegt"
    if clv is not None:
        return "gemessen, nicht belegt"
    return "nicht erhoben"


def clv_urteil_nachziehen(zeilen):
    """Jede Zeile bekommt das Urteil, das zu IHREN Feldern passt (s. `clv_urteil`)."""
    for r in zeilen:
        if isinstance(r, dict):
            r["clvUrteil"] = clv_urteil(r.get("clv"), r.get("clvLb"), r.get("clvOg"))
    return zeilen


def bewerte(name: str, strom: str, renditen, clvs, meta=None, letzter=None, now=None) -> dict:
    """Eine Schublade -> ein Urteil. `renditen` sind Renditen JE PLAY (pnl/stake), nicht die
    Summe: nur so misst die Streuung das, was sie messen soll. `letzter` = Zeitstempel des
    juengsten abgerechneten Plays; ohne ihn kann die Lebendig-Bedingung nicht greifen."""
    n = len(renditen)
    roi, roi_lb = _mittel(renditen), untergrenze(renditen)
    clv, clv_lb = _mittel(clvs), untergrenze(clvs)
    clv_og = obergrenze(clvs)
    alter = _alter_tage(letzter, now or _now())

    if alter is not None and alter > MAX_ALTER_TAGE:
        # Zuerst pruefen: eine tote Schublade braucht keine ROI-Diskussion.
        return {"schublade": name, "strom": strom, "n": n, "status": "ruht",
                "grund": "letzter Play vor %d Tagen — die Schublade liefert nichts mehr" % round(alter),
                "roi": round(roi, 4) if roi is not None else None,
                "pl": round(sum(renditen), 2) if renditen else None,
                "roiLb": round(roi_lb, 4) if roi_lb is not None else None,
                "clv": round(clv, 3) if clv is not None else None,
                "clvLb": round(clv_lb, 3) if clv_lb is not None else None,
                # 15.09.2026: dieser Zweig baut die Zeile SELBST — und liess `clvOg`/`clvUrteil`
                # weg. Auf dem Board stand darum „WM · ABWAEGEN … CLV −2,2pp" ganz ohne Urteil,
                # waehrend jede andere Zeile eines trug. Eine ruhende Schublade ist ein anderer
                # Zustand als eine ohne CLV; als Leerstelle sahen beide gleich aus.
                "clvOg": round(clv_og, 3) if clv_og is not None else None,
                "clvUrteil": clv_urteil(clv, clv_lb, clv_og),
                # Eine ruhende Zeile hatte das Feld gar nicht — und eine fehlende Angabe
                # wurde im Board zu einer Aussage ueber ihren Schnitt.
                "noetigNRoi": None, "noetigNClv": None,
                "noetigGrund": noetig_grund(renditen, n),
                "fehltN": 0, "alterTage": round(alter, 1), **(meta or {})}

    # Die echte Entfernung zur Freigabe, nicht nur die zur Mindestzahl. `MIN_N` ist die
    # Schwelle, ab der ueberhaupt gerechnet wird — nicht die, ab der etwas belegt ist.
    _n_roi, _n_clv = noetiges_n(renditen), noetiges_n(clvs)
    _ent = entfernung(renditen)
    _weit = ""
    if n < MIN_N:
        if _n_roi is None and _ent is None:
            _weit = " · für eine Hochrechnung sind es noch zu wenige Plays"
        elif _n_roi is None:
            _weit = (" · der ROI-Schnitt ist nicht positiv — mehr Plays bestätigen das, "
                     "sie drehen es nicht")
        elif _ent and not _ent["schaetzbar"]:
            # Der ehrliche Fall, und heute der einzige: die Entfernung schwankt ueber
            # Groessenordnungen. „Noch 248" waere hier so falsch wie „noch 15".
            _weit = (" · wie weit es bis zu einem Beleg ist, lässt sich bei %d Plays NICHT "
                     "schätzen (Bootstrap: %d bis %d Plays, und in %d %% der Ziehungen ist der "
                     "Schnitt gar nicht positiv)"
                     % (n, _ent["lo"], _ent["hi"], round(100 * _ent["nieAnteil"])))
        else:
            _ziel_roi = max(_n_roi, MIN_N)   # unter der Mindestzahl wird nicht geurteilt
            _fehlt = max(0, _ziel_roi - n)
            _weit = (" · für einen ROI-Beleg wären rund %d Plays nötig%s"
                     % (_ziel_roi, (", also noch ~%d" % _fehlt) if _fehlt else ""))
        # 🔴 Dritte Ueberbehauptung an diesem Abend, und sie stand schon in einer Nachricht an
        # Lucas: „der CLV ist bereits belegt". Belegt heisst hier IMMER: Untergrenze ueber null.
        # `untergrenze` hat einen harten Boden bei UG_MIN_N=30 — unterhalb davon gibt es gar
        # keine, also kann dort auch nichts belegt sein. Was stimmt, ist etwas anderes und
        # nuetzlicheres: der CLV waere mit einem Bruchteil der Plays belegbar. Das ist eine
        # Aussage ueber die BINDENDE Huerde, keine ueber einen Beweis.
        if clv_lb is not None and clv_lb > MIN_CLV_LB:
            _weit += " — der CLV ist bereits belegt"
        elif _n_clv is not None and _n_roi is not None and _n_clv * 4 <= _n_roi:
            _weit += (" — die bindende Hürde ist der ROI: der CLV wäre schon nach ~%d Plays "
                      "belegbar" % max(_n_clv, MIN_N))

    if n < KANDIDAT_N:
        status, grund = "sammelt", f"{n} von {MIN_N} Plays{_weit}"
    elif n < MIN_N:
        status, grund = "kandidat", f"{n} von {MIN_N} Plays — noch {MIN_N - n}{_weit}"
    elif roi_lb is None or roi_lb <= MIN_ROI_LB:
        status, grund = "geprueft", "ROI nicht belegt über null"
    elif CLV_BLOCKT and clv_lb is None:
        status, grund = "geprueft", "kein CLV messbar — ohne den bleibt Glück und Kante ununterscheidbar"
    elif CLV_BLOCKT and clv_lb < MIN_CLV_LB:
        status, grund = "geprueft", "CLV negativ belegt — der ROI kam aus der Varianz, nicht aus einer Kante"
    elif alter is None:
        # 29.08.2026: kein Zeitstempel -> die Lebendig-Bedingung ist nicht pruefbar. Fail-closed,
        # wie bei fehlendem CLV: nicht wissen ist keine Erlaubnis. Aufgefallen an den Picks des
        # Spiels um Platz 3 (KO-3RD-FRA-SEN), zu denen im Datensatz gar kein Fixture existiert —
        # ohne diese Klausel koennte eine undatierbare Schublade freigegeben werden.
        status, grund = "geprueft", "kein Datum am Play — Lebendigkeit nicht prüfbar"
    else:
        status = "freigegeben"
        # Der Satz sagt, WORAUF die Freigabe beruht — und wenn der CLV dagegen spricht, sagt er
        # das im selben Atemzug. Eine Freigabe, die ihren eigenen Einwand verschweigt, ist genau
        # die Sorte Zahl, gegen die dieses Register gebaut ist.
        if clv_lb is not None and clv_lb > MIN_CLV_LB:
            grund = "ROI und CLV belegt"
        elif clv_og is not None and clv_og < MIN_CLV_LB:
            grund = ("ROI belegt (Untergrenze %+.1f %%) — ⚠️ der CLV ist dagegen negativ belegt "
                     "(Obergrenze %+.2f pp): in unseren Daten liefen Schubladen mit negativem CLV "
                     "im Schnitt −6,8 %%. Freigegeben auf die Rendite, nicht auf eine gemessene "
                     "Kante." % (100 * roi_lb, clv_og))
        elif clv is not None:
            grund = ("ROI belegt (Untergrenze %+.1f %%) — der CLV ist gemessen (%+.2f pp), aber "
                     "nicht belegt: unbekannt ist kein Nein." % (100 * roi_lb, clv))
        else:
            grund = ("ROI belegt (Untergrenze %+.1f %%) — für diese Schublade wird gar kein CLV "
                     "erhoben. Unbekannt ist kein Nein, aber auch kein Ja."
                     % (100 * roi_lb))

    return {
        "schublade": name, "strom": strom, "n": n, "status": status, "grund": grund,
        "roi": round(roi, 4) if roi is not None else None,
        # 08.09.2026 (Lucas: „ich seh dort die Schubladen die Sinn machen, mit ROI, P/L, CLV").
        # P/L in EINHEITEN, also die Summe der Renditen je Play. ROI sagt, wie gut eine Schublade
        # ist; P/L sagt, wie viel sie tatsaechlich gebracht hat — und die beiden koennen weit
        # auseinanderliegen: „Half Time" hat +5,0 % ROI aus 1.936 Plays (+96 Einheiten), waehrend
        # eine 30er-Schublade mit +36 % ROI nur +12 Einheiten getragen hat.
        "pl": round(sum(renditen), 2) if renditen else None,
        "roiLb": round(roi_lb, 4) if roi_lb is not None else None,
        "clv": round(clv, 3) if clv is not None else None,
        "clvLb": round(clv_lb, 3) if clv_lb is not None else None,
        # 08.09.2026: seit der CLV nicht mehr blockiert, muss er trotzdem SICHTBAR bleiben —
        # sonst verschwindet die einzige Warnung, die es zu einer Freigabe noch gibt.
        # Vier Zustaende, und „nicht erhoben" ist ausdruecklich nicht dasselbe wie „schlecht".
        "clvUrteil": clv_urteil(clv, clv_lb, clv_og),
        "clvOg": round(clv_og, 3) if clv_og is not None else None,
        "fehltN": max(0, MIN_N - n),
        # Damit die Oberflaeche die Entfernung nicht selbst ausrechnet (sie hat die Einzelwerte
        # gar nicht) und nicht wieder „noch 15" schreibt, wo ~248 gemeint sind.
        "noetigNRoi": _n_roi,
        "noetigNClv": _n_clv,
        # Warum keine Hochrechnung da ist — damit die Oberflaeche sie nicht raten muss.
        "noetigGrund": noetig_grund(renditen, n),
        # Die Prognose MIT ihrer eigenen Schranke — ohne die ist sie nur ein Punktschaetzer
        # eine Ebene hoeher (s. `entfernung`).
        "entfernung": _ent,
        "alterTage": round(alter, 1) if alter is not None else None,
        **(meta or {}),
    }


# ── Strom 1: Poly-Shortlist (Papier-Depot) ──────────────────────────────────────────────
_ENGINE_PROBE = 25       # so viele juengste Plays entscheiden, welche Engine gerade laeuft
_ENGINE_MIN_BELEG = 3    # so oft muss ein Stempel darin vorkommen, um zu zaehlen


def aktuelle_engine(track) -> str | None:
    """Welcher Engine-Stempel laeuft gerade? Aus den DATEN gelesen, nicht aus einer Env. REIN.

    ⚠️ 01.09.2026 — der Grund fuer diese Funktion: `poly_schubladen` konnte schon immer auf eine
    Engine-Version filtern, aber der Schalter hing an `FREIGABE_ENGINE`, und die Variable wurde
    NIRGENDS gesetzt (`freigabe.json` trug `engine: null`). Das Register mischte deshalb
    monatelang Engine-Versionen — die staerkste Schublade („Conviction 9", n=12, ROI +16%)
    bestand ausschliesslich aus Plays einer Engine, die es nicht mehr gibt. Sie konnte n=30 nie
    erreichen und schrumpfte stattdessen aus dem rollierenden Fenster heraus.
    Dieselbe Bauform wie beim x-Norm-Badge: eingebaut, feuert nie.

    Eine Env, die jemand setzen MUSS, ist keine Quelle — die Daten sind eine.

    Die Regel: der JUENGSTE Stempel, der im Fenster mindestens `_ENGINE_MIN_BELEG` mal vorkommt.
    Nicht die Mehrheit — die waere direkt nach einem Versionssprung noch beim ALTEN Stempel, und
    das Register wuerde einen halben Tag lang genau die Plays als aktuell zaehlen, die es gerade
    aussortieren soll. Nicht die letzte Zeile — eine einzelne Ausreisser-Zeile darf das Register
    nicht umschalten. Ein paar Belege sind der Kompromiss: umschalten, sobald der neue Stempel
    wirklich laeuft, und nicht wegen eines Zufalls.

    Findet sich gar kein Stempel, gibt es None: dann wird NICHT gefiltert — eine alte Datei ohne
    Stempel soll das Register nicht leeren.
    """
    st = (track or {}).get("settled") or []
    st = list(st.values()) if isinstance(st, dict) else list(st)
    kandidaten = [str(x.get("ev")) for x in st[-_ENGINE_PROBE:] if isinstance(x, dict) and x.get("ev")]
    if not kandidaten:
        op = (track or {}).get("open") or {}
        op = list(op.values()) if isinstance(op, dict) else list(op)
        kandidaten = [str(x.get("ev")) for x in op if isinstance(x, dict) and x.get("ev")]
    if not kandidaten:
        return None
    from collections import Counter
    zaehler = Counter(kandidaten)
    for ev in reversed(kandidaten):                     # von der juengsten Zeile rueckwaerts
        if zaehler[ev] >= _ENGINE_MIN_BELEG:
            return ev
    return zaehler.most_common(1)[0][0]                  # niemand hat genug Belege -> haeufigster


def poly_schubladen(track=None, engine=None) -> list:
    """Conviction-Buckets und Signal-Mixe.

    Plays aus einer aelteren Gewichtung sind fuer eine FREIGABE keine Zeugen — anders als beim
    Kalibrierer, der sie halb gewichtet weiterlaufen laesst. Hier geht es um eine Erlaubnis, nicht
    um eine Rangfolge; da ist Halbwissen keine Grundlage.

    ⚠️ Sie werden trotzdem NICHT verschwiegen. Wuerde man sie einfach wegfiltern, verschwaende die
    Schublade komplett aus dem Register und saehe aus wie „gab es nie" statt wie „die Datenbasis
    ist veraltet". Deshalb: das URTEIL rechnet nur auf der aktuellen Engine, die Alt-Plays laufen
    als Kontext mit (`nAlt`, `roiAlt`, `clvAlt`, `engineAlt`).

    `engine=None` -> aus den Daten bestimmen (s. `aktuelle_engine`). `engine=False` -> gar nicht
    filtern (fuer Tests und Rueckblicke)."""
    d = track if track is not None else _load("poly_shortlist_track.json")
    st = (d or {}).get("settled") or []
    st = list(st.values()) if isinstance(st, dict) else st
    # 08.09.2026 (Lucas: „aja, und was ist das in Polymarket — im Tracking vom Polymarket-Wallet
    # stehen da andere Sachen"). Zwei Gruende, warum die Kachel und die Track-Record-Seite
    # verschiedene Zahlen zeigten, und einer davon war ein echter Fehler:
    #
    #  1. Der ENGINE-FILTER (kein Fehler, aber ungesagt): die Freigabe rechnet nur auf der
    #     aktuellen Engine-Version — 240 von 644 abgerechneten Plays. Die Track-Seite zeigt
    #     alle. Das steht ab jetzt als `basis` auf der Kachel, statt dass man es wissen muss.
    #  2. Die GESPERRTEN KATEGORIEN (Fehler): die Track-Seite trennt „bespielbar" von „nicht
    #     bespielbar" (US-Sport, Kampfsport) und sagt dazu ausdruecklich „nur Beobachtung, kein
    #     Geld". Die Freigabe zaehlte sie mit. Ein Strom-ROI, der Wetten enthaelt, die gar nicht
    #     gespielt werden duerfen, beantwortet eine Frage, die niemand hat.
    #     Gemessen: 4 der 240 Plays, ROI der Kachel −6,6 % → −5,6 %. Klein heute, aber die
    #     gesperrten Kategorien laufen bei −43,2 % (n=53 ueber alle Engines) — sobald davon
    #     mehr in die aktuelle Engine faellt, verzerrt es die Kachel ernsthaft.
    #     Gefiltert wird ueber `poly_shortlist_track._row_cat` und NICHT ueber das rohe Feld:
    #     nicht jede Zeile traegt einen Stempel, und eine ungestempelte UFC-Zeile landete sonst
    #     im bespielbaren Topf. Genau dafuer gibt es den Helfer — und einen Waechter, der jeden
    #     rohen `.get("cat") in …`-Filter im Repo findet.
    _gesperrt = set((d or {}).get("blockedCats") or [])
    if _gesperrt:
        st = [x for x in st if _row_cat(x) not in _gesperrt]
    if engine is None:
        engine = aktuelle_engine(d)
    out = []

    def _teile(rows):
        """(aktuell, alt) — ohne Filter zaehlt alles als aktuell."""
        if not engine:
            return list(rows), []
        akt = [x for x in rows if x.get("ev") == engine]
        return akt, [x for x in rows if x.get("ev") != engine]

    def _kennzahlen(rows):
        r = [float(x.get("pnl") or 0) / float(x["stake"]) for x in rows if x.get("stake")]
        c = [float(x["clvPP"]) for x in rows if isinstance(x.get("clvPP"), (int, float))]
        return r, c

    def _rows(rows, name, meta=None):
        akt, alt = _teile(rows)
        r, c = _kennzahlen(akt)
        ra, ca = _kennzahlen(alt)
        if not r and not ra:
            return
        letzter = max((str(x.get("settledTs") or "") for x in akt), default="") or None
        z = bewerte(name, "poly", r, c, meta, letzter=letzter)
        if ra:
            z["nAlt"] = len(ra)
            z["roiAlt"] = round(sum(ra) / len(ra), 4)
            z["clvAlt"] = round(sum(ca) / len(ca), 3) if ca else None
            z["engineAlt"] = True
            if not r:
                # Die ganze Datenbasis ist alt. Das ist etwas anderes als „noch keine Daten" —
                # und der Unterschied gehoert in den Grund, nicht in eine Fussnote.
                z["grund"] = ("0 von %d Plays auf der aktuellen Engine — die %d vorhandenen "
                              "stammen aus einer frueheren Bewertung und zaehlen nicht"
                              % (MIN_N, len(ra)))
        out.append(z)

    for conv in sorted({x.get("conv") for x in st if x.get("conv") is not None}, reverse=True):
        _rows([x for x in st if x.get("conv") == conv], f"Conviction {conv}",
              {"art": "conviction", "wert": conv})
    CORE = {"money", "sharp", "steam", "pinn", "gvp", "bf"}
    mixe = {}
    for x in st:
        k = "+".join(sorted(t for t in (x.get("signals") or []) if t in CORE)) or "(ohne)"
        mixe.setdefault(k, []).append(x)
    for k, rows in mixe.items():
        _rows(rows, f"Mix {k}", {"art": "mix", "wert": k})
    _rows([x for x in st if x.get("public")], "Public-Kandidaten", {"art": "gate"})
    return out


# ── Strom 2: Cards ──────────────────────────────────────────────────────────────────────
CARD_DATEIEN = (("liga-data.json", "Liga"), ("mls-data.json", "MLS"), ("wm2026-data.json", "WM"))
STAKE_BET, STAKE_ABW = 10.0, 5.0


def _card_plays(dateien=CARD_DATEIEN) -> list:
    """Abgerechnete Card-Picks in dieselbe Form wie das Poly-Depot: Rendite je Play + CLV.
    NOBET, trackingExcluded und boldAlt fliegen raus — das sind keine Wetten (gleiche Regel
    wie im Tracking-Tab, sonst misst die Freigabe etwas anderes als die Anzeige)."""
    out = []
    for datei, label in dateien:
        d = _load(datei)
        # Pick-Keys tragen kein Datum — das steht am Fixture. Ohne Datum kann die
        # Lebendig-Bedingung nicht greifen, und genau daran ist die WM-Schublade aufgefallen.
        _fx_datum = {}
        for gk, g in ((d or {}).get("groups") or {}).items():
            for fx in (g.get("fixtures") or []):
                _fx_datum[f"{gk}-{fx.get('matchday')}-{fx.get('home')}-{fx.get('away')}"] = fx.get("date")
        # K.O.-Picks haengen unter „KO-{Runde}-{Heim}-{Gast}" und stehen NICHT in groups.fixtures.
        # Ohne sie blieben zwei WM-Picks ohne Datum und damit ohne Alter — dieselbe blinde Stelle
        # wie heute frueh im Tracking, wo die K.O.-Zeilen ihr result nicht mitfuehrten.
        for kf in ((d or {}).get("koFixtures") or []):
            _fx_datum[f"KO-{kf.get('round')}-{kf.get('home')}-{kf.get('away')}"] = (
                kf.get("date") or (str(kf.get("kickoff") or "")[:10] or None))
        for key, arr in ((d or {}).get("picks") or {}).items():
            for p in arr:
                res = str(p.get("result") or "").upper()
                if res not in ("WIN", "LOSS"):
                    continue
                if p.get("verdict") == "NOBET" or p.get("trackingExcluded") or p.get("boldAlt"):
                    continue
                odds = p.get("odds")
                if not isinstance(odds, (int, float)) or odds <= 1:
                    continue
                stake = float(p.get("stake") or (STAKE_BET if p.get("verdict") == "BET" else STAKE_ABW))
                pnl = (odds - 1) * stake if res == "WIN" else -stake
                out.append({"ds": label, "verdict": p.get("verdict") or "?",
                            "r": pnl / stake, "clv": p.get("clvPP"), "ts": _fx_datum.get(key)})
    return out


def card_schubladen(plays=None) -> list:
    plays = _card_plays() if plays is None else plays
    out, gruppen = [], {}
    for p in plays:
        gruppen.setdefault((p["ds"], p["verdict"]), []).append(p)
    for (ds, v), rows in gruppen.items():
        letzter = max((str(x.get("ts") or "") for x in rows), default="") or None
        out.append(bewerte(f"{ds} · {v}", "cards",
                           [x["r"] for x in rows],
                           [float(x["clv"]) for x in rows if isinstance(x.get("clv"), (int, float))],
                           {"art": "verdict", "wert": v, "datensatz": ds},
                           letzter=(letzter + "T12:00:00+00:00") if letzter else None))
    return out


# ── Strom 3: Betfair ────────────────────────────────────────────────────────────────────
def _betfair_streuung(n, hit, roi):
    """Betfair fuehrt kein Play-fuer-Play-Ledger, nur Aggregate (n/hitRate/roi je Liga x Markt).
    Ohne Einzelwerte gibt es keine echte Streuung — sie laesst sich aber aus der Binaerstruktur
    rekonstruieren: aus ROI und Trefferquote folgt die mittlere Quote o = (roi+1)/hit, und die
    Rendite je Play ist dann entweder o-1 (Treffer) oder -1. Die Standardabweichung davon ist
    o*sqrt(p(1-p)). Das ist eine NAEHERUNG und im Ergebnis als solche markiert — sie ueberschaetzt
    die Praezision nicht, weil sie die Quotenstreuung innerhalb der Schublade ignoriert und damit
    eher zu KLEINE Fehler liefert. Deshalb gilt Betfair nie allein als Freigabe-Beleg."""
    if not n or not hit or hit <= 0 or hit >= 1:
        return None
    o = (roi + 1.0) / hit
    sd = o * math.sqrt(hit * (1 - hit))
    return sd / math.sqrt(n)


def betfair_schubladen(rec=None, min_n=None) -> list:
    d = rec if rec is not None else _load("betfair_track_record.json")
    min_n = MIN_N if min_n is None else min_n
    out = []
    for quelle, art in (("byMarket", "markt"), ("byLeagueMarket", "liga_markt")):
        for name, v in ((d or {}).get(quelle) or {}).items():
            n, hit, roi = v.get("n") or 0, v.get("hitRate"), v.get("roi")
            if n < min_n or hit is None or roi is None:
                continue
            # 06.09.2026 (Lucas: „ok, da hat es noch nichts rein geschafft? … könnte für immer
            # leer sein eigentlich"). Beim Nachrechnen seiner Frage: von drei Schubladen, die
            # auf dem Board die ROI-Huerde nahmen, waren ZWEI ein Artefakt genau dieser
            # Naeherung — und beide sind Betfair:
            #
            #     Half Time                                    n=1840  Naeherung +0,43 %  ·  roiUg −0,26 %
            #     Sky Bet League 2 · First Half Goals 1.5       n=  33  Naeherung +4,63 %  ·  roiUg −0,05 %
            #
            # `betfair_track_record.json` rechnet fuer diese Eimer laengst eine eigene
            # Untergrenze (`roiUg`, ab n>=`ugAb`) — aus den Rohzeilen, nicht aus einer
            # Binaer-Rekonstruktion. Hier stand eine ZWEITE Untergrenze fuer dieselbe Menge,
            # und sie kam bei genau diesen beiden im VORZEICHEN anders heraus. Dieselbe Klasse
            # wie ueberall sonst heute: das Urteil gehoert dorthin, wo die Zahl entsteht.
            # Die Naeherung bleibt als Rueckfall fuer Eimer ohne `roiUg` — dann aber auch nur
            # dort, und weiter als `naeherung` markiert.
            _ug = v.get("roiUg")
            if isinstance(_ug, (int, float)):
                roi_lb, _naeh = float(_ug), False
            else:
                se = _betfair_streuung(n, hit, roi)
                roi_lb, _naeh = ((roi - Z * se) if se is not None else None), True
            # Betfair fuehrt kein CLV je Signal -> die CLV-Bedingung kann hier NICHT geprueft
            # werden. Fail-closed: ohne CLV keine Freigabe. Der Strom kann Kandidat werden,
            # freigegeben wird er erst, wenn das Ledger CLV je Signal mitschreibt.
            eintrag = bewerte(name.replace("|", " · "), "betfair", [], [],
                              {"art": art, "naeherung": _naeh})
            eintrag.update({"n": n, "roi": round(roi, 4),
                            # 08.09.2026: diese Schubladen kommen aus einem AGGREGAT, nicht aus
                            # Einzel-Renditen — `bewerte` bekommt hier eine leere Liste und kann
                            # das P/L nicht selbst summieren. ROI x n ist dieselbe Zahl (der ROI
                            # IST der Schnitt der Renditen), nur aus der anderen Richtung.
                            "pl": round(roi * n, 2),
                            "roiLb": round(roi_lb, 4) if roi_lb is not None else None,
                            # `bewerte` bekam oben [] — diese Faecher haben keine Einzelrenditen.
                            # Ohne diese Zeile stand im Artefakt eine Leerstelle, aus der das
                            # Board „Schnitt nicht positiv" machte (25.09.2026, s. noetig_grund).
                            "noetigGrund": "keine_einzelwerte",
                            "fehltN": max(0, MIN_N - n)})
            if roi_lb is None or roi_lb <= 0:
                eintrag["status"], eintrag["grund"] = "geprueft", "ROI nicht belegt über null"
            else:
                # 🔴 22.09.2026 (Lucas: „check ob wir CLV so implementiert haben, dass es
                # irgendwas blockt — auch im Betfair-Radar und im Terminal").
                #
                # Hier stand `status = "geprueft"` mit dem Grund „ohne Untergrenze keine
                # Freigabe". Das war ein CLV-Riegel, und er war der letzte im Haus: am
                # 08.09.2026 wurde `bewerte()` auf die Rendite umgestellt (`CLV_BLOCKT` aus),
                # dieser Zweig nicht. Dieselbe Fehlerklasse, die heute schon dreimal
                # aufgeschlagen ist: **eine Entscheidung, die an einer Stelle umgesetzt wird
                # und an den anderen stehen bleibt.**
                #
                # Gemessen am 22.09.2026 kostete er 12 Schubladen mit belegter
                # Rendite-Untergrenze, darunter Peruvian Primera Division · Half Time mit
                # +12,9 % Untergrenze ueber 32 Plays.
                #
                # ── Warum sie trotzdem nicht freigegeben werden ───────────────────────────
                # Weil das Register 407 Schubladen GLEICHZEITIG prueft. Bei einseitigem
                # 5-%-Band legt der Zufall allein 20,4 davon ueber die Huerde — tatsaechlich
                # liegen 15 drueber. Die Ausbeute ist KLEINER als der Zufall. Auf
                # Einzel-Ebene ueberlebt keine einzige Schublade eine
                # Mehrfachtest-Korrektur (Bonferroni: 0, Benjamini-Hochberg bei FDR 20 %: 0).
                #
                # Der CLV-Riegel hat also zufaellig etwas Richtiges verhindert, mit der
                # falschen Begruendung. Beides zu behalten waere bequem und unehrlich.
                # Deshalb (Lucas' Entscheidung, 22.09.2026): **Kandidat statt Freigabe.**
                # Das falsche Kriterium ist weg, und an seine Stelle tritt kein blindes Ja,
                # sondern die Bedingung, die zur Frage passt — eine Bestaetigung nach vorn.
                #
                # Die Mehrfachtest-Zahlen traegt `mehrfachtest_nachziehen()` nach, sobald in
                # `baue()` alle Zeilen vorliegen; hier sind sie noch nicht bekannt.
                eintrag["status"] = "kandidat"
                eintrag["grund"] = ("ROI belegt (Untergrenze %+.1f %%) — Freigabe erst nach "
                                    "Bestätigung nach vorn" % (100 * roi_lb))
                _clv = v.get("avgClvBf")
                if isinstance(_clv, (int, float)):
                    # Der CLV steht weiter auf der Zeile — angezeigt, nicht entscheidend.
                    eintrag["clv"] = round(float(_clv), 3)
            out.append(eintrag)
    # Das `clv` kommt hier NACH `bewerte()` dazu (s. `clv_urteil`) — also faellt das Urteil auch
    # hier neu, nicht erst in `baue()`. Wer diese Funktion einzeln aufruft (Tests, Skripte),
    # bekommt sonst Zeilen, die „kein CLV" sagen und eine CLV-Zahl tragen.
    return clv_urteil_nachziehen(out)


STAND_FILE = BASE / "freigabe_stand.json" if "BASE" in dir() else Path(__file__).resolve().parent / "freigabe_stand.json"


def darf_stand_schreiben(env=None) -> bool:
    """Nur der Pipeline-Lauf schreibt das Freigabe-Gedaechtnis fort.

    Vorfall 22.09.2026: ein Lauf von Hand hat zwei Schubladen einen Freigabe-Beginn
    gestempelt, die nie eine hatten. `FREIGABE_STAND=1` erzwingt das Schreiben von Hand
    (zum Reparieren), `=0` verbietet es auch in der Pipeline.
    """
    env = os.environ if env is None else env
    erzwungen = str(env.get("FREIGABE_STAND", "")).strip()
    if erzwungen in ("0", "off", "nein"):
        return False
    if erzwungen in ("1", "on", "ja"):
        return True
    return str(env.get("GITHUB_ACTIONS", "")).lower() == "true"


def stand_schreiben(stand, env=None) -> bool:
    """Schreibt den Stand — oder sagt laut, warum nicht. Gibt zurueck, ob geschrieben wurde."""
    if not darf_stand_schreiben(env):
        print("freigabe_stand.json NICHT geschrieben (kein Pipeline-Lauf; "
              "FREIGABE_STAND=1 erzwingt es)")
        return False
    try:
        STAND_FILE.write_text(json.dumps(stand, ensure_ascii=False, indent=1), encoding="utf-8")
        return True
    except Exception as _e:
        print("freigabe_stand.json nicht schreibbar:", _e)
        return False


# Die Bestaetigung nach vorn: so viele Plays muss eine Schublade NACH ihrer Kandidatur
# liefern, bevor eine Freigabe zur Debatte steht. Nicht mehr und nicht weniger als das
# Minimum, das im Haus fuer jede Schublade gilt — der Punkt ist nicht die Groesse, sondern
# dass diese Plays die Schublade nicht selbst ausgesucht hat.
NACHWEIS_N = MIN_N
# Der Grundtext, an dem eine Zeile als „wartet auf Bestaetigung nach vorn" erkennbar ist.
NACHWEIS_MARKE = "Bestätigung nach vorn"


def nachweis_ziel_nachziehen(zeilen, stand, now=None):
    """Gibt jeder wartenden Kandidaten-Zeile ihr eigenes Ziel-n. -> (zeilen, stand)

    🔴 23.09.2026, gefunden von `test_gegen_den_echten_bestand_behauptet_kein_balken_fertig`,
    einen Lauf nachdem der CLV-Riegel gefallen war.

    Die zehn Schubladen, die gestern von „freigegeben" auf „kandidat" gesetzt wurden, sind
    NICHT wegen zu weniger Plays Kandidat — „Peruvian Primera Division · Half Time" hat 32
    Plays bei +12,9 % Rendite-Untergrenze. Sie warten auf eine Bestaetigung nach vorn. Ohne
    eigenes `zielN` rechnete das Board ihren Balken aber gegen `minN=30` und zeigte „32/30",
    voll — fertig behauptet, waehrend das Verdikt daneben „Kandidat" sagt.

    Fehlerklasse: *ein Fortschrittsbalken, der gegen die falsche Huerde misst, weil die
    richtige nirgends steht.* Dieselbe wie am 06.09. und am 19.09., nur andersherum: zu
    optimistisch statt zu pessimistisch.

    Der Anker ist das n bei EINTRITT in die Kandidatur, nicht das heutige: ein Ziel, das mit
    dem Bestand mitwaechst, wird nie erreicht. Er steht im Freigabe-Gedaechtnis, damit er den
    Lauf ueberlebt. Fehlt er noch (erster Lauf nach dieser Aenderung), wird heute verankert —
    dann ist das Ziel ab jetzt gueltig statt rueckwirkend erfunden.
    """
    now = now or _now()
    stand = dict(stand or {})
    for z in zeilen or []:
        if not isinstance(z, dict) or z.get("status") != "kandidat":
            continue
        if NACHWEIS_MARKE not in str(z.get("grund") or ""):
            continue
        name = str(z.get("schublade") or "")
        if not name or name.startswith("_"):
            continue
        n = z.get("n")
        if not isinstance(n, (int, float)):
            continue
        eintrag = dict(stand.get(name) or {})
        k = dict(eintrag.get("kandidat") or {})
        if not isinstance(k.get("nBei"), (int, float)):
            k = {"ab": now.isoformat(), "nBei": int(n)}
        eintrag["kandidat"] = k
        stand[name] = eintrag
        ziel = int(k["nBei"]) + NACHWEIS_N
        z["zielN"] = ziel
        z["fehltN"] = max(0, ziel - int(n))
        z["seitKandidat"] = {"ab": k.get("ab"), "nBei": k.get("nBei"),
                             "nNeu": max(0, int(n) - int(k["nBei"]))}
    return zeilen, stand


def seit_freigabe(zeilen, stand, now=None) -> tuple:
    """Was eine Schublade geleistet hat, SEIT sie freigegeben wurde. REIN.

    🔴 22.09.2026 (Lucas: „ja" — auf die Frage, ob die drei freigegebenen Schubladen besser
    dastehen als die zwölf, die ich gerade auf Kandidat gesetzt habe).

    Die Antwort steckte nur in der git-Historie und musste von Hand rekonstruiert werden. Sie
    ist die einzige Zahl, die die Mehrfachtest-Frage wirklich entscheidet — und sie stand
    nirgends. Fehlerklasse: *die Messung, die das Urteil traegt, existiert nur als Rekonstruktion.*

    Rekonstruiert am 22.09.2026 aus 1.392 Laeufen der letzten 14 Tage:

        Schublade                        freigegeben     n / ROI dort    seither        Urteil
        Liga · ABWÄGEN                   08.09.  100 %    91 / +15,2 %    98 / +9,0 %   haelt
        Mix money+sharp+steam            16.09.   33 %    32 / +20,1 %    12 / +8,3 %   zu duenn
        🔒 E-Sport ohne Wallet-Nachweis   21.09.   10 %   120 / +14,8 %    15 / −14,1 %  frisch
        Public-Kandidaten (heute raus)   08.09.   20 %    35 / +20,8 %    64 / +0,3 %   zum Mittel

    Die letzte Zeile ist der Beleg, dass die Mehrfachtest-Warnung keine Theorie ist: ein ROI von
    +20,8 % auf 35 Plays fiel ueber 64 neue Plays auf +0,3 % — genau der Rueckfall zum Mittel,
    den die Rechnung vorhersagt. Und die erste ist der Beleg, dass sie nicht das letzte Wort ist:
    98 Plays out of sample bei +9,0 % sind eine Bestaetigung, auch ohne Voranmeldung.

    `stand` ist das Gedaechtnis: je Schublade der Zeitpunkt der ERSTEN Freigabe und der Stand
    (n, roi) zu dem Zeitpunkt. Es wird nie ueberschrieben, solange die Schublade freigegeben
    bleibt — sonst waere der Vergleich jeden Tag bei null, und die Zahl koennte nie wachsen.

    Faellt eine Schublade wieder heraus, bleibt ihr Eintrag stehen und `raus` zaehlt hoch. Ein
    Flackern ist selbst ein Befund: „Public-Kandidaten" ist in 14 Tagen elf Mal rein- und
    rausgefallen und stand am Ende in 20 % der Laeufe. Eine Freigabe, die taeglich wechselt,
    ist kein Beleg — sie ist eine Zahl, die um null schwankt.
    """
    now = now or _now()
    stand = dict(stand or {})
    for z in zeilen or []:
        if not isinstance(z, dict):
            continue
        name = str(z.get("schublade") or "")
        if not name or name.startswith("_"):
            continue        #  ist Dokumentation, keine Schublade
        alt = stand.get(name)
        if z.get("status") == "freigegeben":
            if not alt:
                stand[name] = {"ab": now.isoformat(), "nBei": z.get("n"),
                               "roiBei": z.get("roi"), "raus": 0, "drin": True}
                alt = stand[name]
            elif not alt.get("drin"):
                alt["drin"] = True
            n_bei, roi_bei = alt.get("nBei"), alt.get("roiBei")
            n_jetzt, roi_jetzt = z.get("n"), z.get("roi")
            eintrag = {"ab": alt.get("ab"), "nBei": n_bei, "roiBei": roi_bei,
                       "rausgefallen": alt.get("raus") or 0}
            if all(isinstance(x, (int, float)) for x in (n_bei, roi_bei, n_jetzt, roi_jetzt)):
                d = n_jetzt - n_bei
                eintrag["nNeu"] = d
                # Der ROI der NEUEN Plays — nicht der Gesamt-ROI, der die Anfangszahl mitschleppt.
                eintrag["roiNeu"] = round((roi_jetzt * n_jetzt - roi_bei * n_bei) / d, 4) if d > 0 else None
            z["seitFreigabe"] = eintrag
        elif alt and alt.get("drin"):
            alt["drin"] = False
            alt["raus"] = (alt.get("raus") or 0) + 1
    return zeilen, stand


def mehrfachtest_nachziehen(zeilen, ausbeute) -> list:
    """Traegt den Mehrfachtest-Befund in die Begruendung der Betfair-Kandidaten nach. REIN.

    🔴 22.09.2026. `ausbeute_ueber_huerde` gab es seit dem 20.09. — gerechnet wurde sie nie.
    Sie stand im Modul, wurde von keiner Stelle aufgerufen und landete in keinem Artefakt.
    Fehlerklasse: *ein Befund, der gemeldet, aber nicht gezogen wird* — dieselbe, die in diesem
    Repo am 13.09. das Beinahe-Buch und am 02.09. die Push-Abrechnung gebracht hat.

    Jetzt steht die Zahl dort, wo die Entscheidung erklaert wird: auf der Zeile, die sagt,
    warum eine Schublade mit belegter Rendite trotzdem nicht freigegeben ist.
    """
    a = ausbeute if isinstance(ausbeute, dict) else {}
    n_tests, erwartet = a.get("nTests"), a.get("erwartet")
    if not n_tests or erwartet is None:
        return zeilen                      # ohne Zahl kein Satz — lieber gar nichts behaupten
    for z in zeilen or []:
        if not isinstance(z, dict) or z.get("status") != "kandidat":
            continue
        if not isinstance(z.get("roiLb"), (int, float)) or z["roiLb"] <= 0:
            continue                       # die „noch zu wenige Plays"-Kandidaten meint das nicht
        z["mehrfachtest"] = {"nTests": n_tests, "erwartet": erwartet,
                             "nUeber": a.get("nUeber"), "ueberschuss": a.get("ueberschuss")}
        z["grund"] = (
            "ROI belegt (Untergrenze %+.1f %%) — aber von %d gleichzeitig geprüften Schubladen "
            "legt der Zufall allein %.1f über diese Hürde, und drüber liegen %s. Freigabe erst "
            "nach Bestätigung nach vorn."
            % (100 * z["roiLb"], int(n_tests), float(erwartet), a.get("nUeber")))
    return zeilen


def ausbeute_ueber_huerde(rows, alpha=0.05, sigma=3.0) -> dict:
    """Wie viele Schubladen ueber der ROI-Huerde liegen — und wie viele der Zufall dort
    hinlegt. 20.09.2026.

    Vorfall: der Wachhund in `tests/test_freigabe_entschieden.py` schlug binnen acht Tagen
    ELF Mal an, jedes Mal mit demselben Zuschnitt (n am Mindest-n, ROI +28..+75 %, kein CLV mit
    Streuung), und jedes Mal wurde darunter von Hand derselbe Absatz geschrieben: „bei ~193
    gleichzeitig geprueften Schubladen ist das die Signatur des Mehrfachtestens". Die Zahl, die
    das belegt, war nie gerechnet — sie war jedes Mal neu geschaetzt.

    Gerechnet: **332 Schubladen tragen eine Untergrenze, 8 liegen darueber, der Zufall legt bei
    einseitigem 5-%-Band 16,6 dorthin.** Die Ausbeute ist halb so gross wie der Zufall. Ein
    einzelner Uebertritt ist damit keine Nachricht, sondern der Normalfall.

    Fehlerklasse: ein Schwellen-Uebertritt ohne seinen Nenner.

    `ueberschuss` ist bewusst weit gefasst (Erwartung + 3 sigma). Die beiden Zerlegungen (Markt,
    Liga x Markt) schneiden dieselben Plays, sind also NICHT unabhaengig; unter positiver
    Abhaengigkeit streut die Anzahl staerker als Poisson, ein enges Band waere eine Schaerfe,
    die die Daten nicht hergeben. Der Wert faengt einen klaren Ueberschuss, keinen knappen —
    und sagt mit `schranke` selbst, wo seine Grenze liegt.
    """
    import math as _math
    tests = [r for r in (rows or []) if r.get("roiLb") is not None]
    ueber = [r for r in tests if r["roiLb"] > 0]
    erwartet = len(tests) * alpha
    schranke = erwartet + sigma * _math.sqrt(erwartet) if erwartet > 0 else 0.0
    return {"nTests": len(tests), "nUeber": len(ueber),
            "erwartet": round(erwartet, 1), "schranke": round(schranke, 1),
            "ueberschuss": len(ueber) > schranke,
            "namen": sorted(str(r.get("schublade") or r.get("liga") or "?") for r in ueber)}


def haelt_bei_doppeltem_n(rows, gesichtet: dict) -> list:
    """Welche schon gesichtete Schublade haelt die ROI-Huerde bei mindestens DOPPELTEM n.
    20.09.2026.

    Elf Sichtungen in `tests/test_freigabe_entschieden.py` nennen genau diese Bedingung als die,
    die aus einem Uebertritt einen Befund macht („erst wenn eine dieser Zeilen bei doppeltem n
    ueber der Huerde bleibt") — und keine einzige hat sie geprueft. Eine Schublade bei n=30 oben
    sagt nichts; dieselbe bei n=60 hat die Haelfte ihrer Zeilen NACH dem Auffallen gesammelt,
    also ausserhalb der Stichprobe, in der sie gefunden wurde.

    `gesichtet` ist {Schubladenname: n zum Zeitpunkt der Sichtung}. Ein Protokoll, das das n
    nicht mitfuehrt, ist eine Stummschaltung und keine Sichtung.
    """
    stand = {r.get("schublade"): r for r in (rows or [])}
    raus = []
    for name, n0 in (gesichtet or {}).items():
        r = stand.get(name)
        if not r or r.get("roiLb") is None or r["roiLb"] <= 0 or not n0:
            continue
        if (r.get("n") or 0) >= 2 * n0:
            raus.append({"schublade": name, "nSichtung": n0, "n": r["n"],
                         "roi": r.get("roi"), "roiLb": r["roiLb"]})
    return raus


def betfair_public_schubladen(rec=None) -> list:
    """Die Betfair-PUBLIC-Pushes als eigene Schublade — das, was wirklich aufs Handy geht.

    08.09.2026 (Lucas: „kann man bei Betfair anzeigen z.B. die Public-Push? die sind relativ
    solide"). Sie standen bis heute in KEINER Schublade: `betfair_schubladen` liest
    `betfair_track_record.json` (alle Signale), der Public-Kanal fuehrt seit jeher sein eigenes
    Register in `betfair_public_record.json`. Der Kanal, der als einziger von selbst sendet, war
    damit der einzige ohne Zeile im Register.

    Und beim Nachrechnen kippt der Eindruck: 58,1 % Treffer aus 191 Pushes klingt solide, mit der
    Durchschnittsquote 1,84 waeren das +6,9 % ROI — gemessen sind es −3,1 %. Der Unterschied ist
    keine Rundung, er ist die ganze Auskunft: die Treffer liegen bei KLEINEREN Quoten als die
    Fehlschuesse. „Eine Trefferquote ohne die Quoten ist keine Zahl" — hier steht der Fall dazu.

    `byScenario` (fresh/ht) ist eine ueberschneidungsfreie Zerlegung derselben Pushes und kommt
    als eigene Zeilen mit; die Dachzeile traegt `art="public"` und faellt damit NICHT in die
    Markt-Zerlegung des Stroms (sonst zaehlten dieselben Plays zweimal).
    """
    d = rec if rec is not None else _load("betfair_public_record.json")
    if not isinstance(d, dict):
        return []
    out = []

    def _zeile(name, v, art):
        n = int(v.get("n") or 0)
        roi, ug = v.get("roi"), v.get("roiUg")
        if not n or not isinstance(roi, (int, float)):
            return None
        e = bewerte(name, "betfair", [], [], {"art": art, "quelle": "betfair_public_eval.py",
                                              "kanal": "public"})
        e.update({"n": n, "roi": round(float(roi), 4), "pl": round(float(roi) * n, 2),
                  "roiLb": round(float(ug), 4) if isinstance(ug, (int, float)) else None,
                  "hit": v.get("hitRate"), "hitLb": v.get("hitUg"), "avgOdd": v.get("avgOdd"),
                  "fehltN": max(0, MIN_N - n), "status": "geprueft"})
        _clv = v.get("avgClvBf")
        if isinstance(_clv, (int, float)):
            e["clv"] = round(float(_clv), 3)
        if e["roiLb"] is not None and e["roiLb"] > 0:
            e["grund"] = "ROI belegt ueber null — aber ohne CLV-Streuung keine Freigabe"
        elif float(roi) < 0 and isinstance(v.get("hitRate"), (int, float)):
            e["grund"] = ("%d Pushes, %.1f %% Treffer — und trotzdem %+.1f %% ROI: die Treffer "
                          "liegen bei kleineren Quoten als die Fehlschuesse"
                          % (n, 100 * float(v["hitRate"]), 100 * float(roi)))
        else:
            e["grund"] = "ROI nicht belegt ueber null"
        return e

    dach = _zeile("Public-Pushes", d, "public")
    if dach:
        out.append(dach)
    for name, v in ((d.get("byScenario") or {}).items()):
        if not isinstance(v, dict):
            continue
        label = {"fresh": "Public · frisches Signal",
                 "ht": "Public · Halbzeit"}.get(name, "Public · " + str(name))
        z = _zeile(label, v, "public_szenario")
        if z:
            out.append(z)
    # dieselbe Reihenfolge-Falle wie oben: `clv` kommt aus `avgClvBf` erst nach `bewerte()`.
    return clv_urteil_nachziehen(out)


# ── Ligen: welche performen, quer ueber alle Betfair-Maerkte ────────────────────────────
LIGA_MIN_N = 30


def betfair_ligen(zeilen=None, min_n: int = None) -> list:
    """Eine Zeile je Liga aus den ROHZEILEN des Betfair-Ledgers. REIN (Zeilen injizierbar).

    08.09.2026 (Lucas: „geht aus dem Tracking eventuell auch anzeigen welche Ligen gut
    performen? die Daten haben wir"). Sie lagen wirklich da — nur nie zusammengefasst:
    `betfair_track_record.json` fuehrt `byMarket` und `byLeagueMarket`, aber kein `byLeague`.
    Eine Liga stand damit in bis zu sieben Zeilen mit je n≈30 und in keiner einzigen mit ihrem
    Gesamtbild.

    ⚠️ Das ist eine ZWEITE ueberschneidungsfreie Zerlegung derselben Plays (jeder Play hat genau
    eine Liga UND genau einen Markt). Sie darf deshalb nie zur Markt-Zerlegung addiert werden —
    sie steht als eigene Tabelle und geht nicht in `alle` ein.

    Untergrenze aus den Einzelrenditen, nicht aus der Binaer-Naeherung: hier liegen die Plays
    einzeln vor, anders als in den Aggregat-Schubladen weiter oben.
    """
    min_n = LIGA_MIN_N if min_n is None else min_n
    if zeilen is None:
        try:
            import betfair_track_store as _S
            zeilen = _S.load(str(BASE / "betfair_track_results.json"))
        except Exception:
            return []
    nach = {}
    for r in (zeilen or []):
        lg = r.get("league")
        o = r.get("odd") or r.get("entryOdd")
        if not lg or not isinstance(o, (int, float)) or o <= 1.0:
            continue
        e = nach.setdefault(lg, {"r": [], "c": [], "w": 0})
        e["r"].append((float(o) - 1.0) if r.get("win") else -1.0)
        if r.get("win"):
            e["w"] += 1
        if isinstance(r.get("clvBf"), (int, float)):
            e["c"].append(float(r["clvBf"]))
    out = []
    for lg, e in nach.items():
        n = len(e["r"])
        if n < min_n:
            continue
        roi, ug = _mittel(e["r"]), untergrenze(e["r"])
        clv = _mittel(e["c"])
        out.append({"liga": lg, "n": n, "wins": e["w"],
                    "hit": round(e["w"] / n, 4),
                    "hitLb": round(SG.wilson_lb(e["w"], n), 4),
                    "roi": round(roi, 4) if roi is not None else None,
                    "roiLb": round(ug, 4) if ug is not None else None,
                    "pl": round(sum(e["r"]), 2),
                    "clv": round(clv, 3) if clv is not None else None,
                    "belegt": bool(ug is not None and ug > 0)})
    out.sort(key=lambda r: -(r["roiLb"] if r["roiLb"] is not None else -9))
    return out


# ── Wallets: wem folgen die Poly-Pushes eigentlich ──────────────────────────────────────
WALLET_TOP_N = 10


def poly_wallets(scores=None, top: int = None) -> list:
    """Die Wallets hinter den Poly-Pushes, sortiert nach BELEGTER Trefferquote. REIN.

    08.09.2026 (Lucas: „waere eventuell auch gut wenn man anzeigt z.B. Top 10 Poly Wallets —
    dann weiss ich, die Pushs die ich krieg von denen sind gut"). Genau das beantwortet das Board
    bisher nirgends: `sharp_gate` entscheidet je Push still, ob eine Wallet zaehlt, aber WELCHE
    Wallets das sind und wie gut sie sind, stand nie irgendwo.

    Sortiert wird nach der Wilson-UNTERGRENZE, nicht nach der rohen Quote: 8/8 (100 %) und 40/50
    (80 %) sind nicht dieselbe Auskunft, und die rohe Quote stellt das kleine n nach vorn.
    `sharp_gate.sharp_grade` bleibt der Filter — dieselbe Definition, die auch entscheidet, ob
    eine Wallet ueberhaupt in einen Push einfliesst. Zwei Listen mit zwei Definitionen waeren
    genau der Fehler, den sharp_gate.py aufgeraeumt hat.

    P&L ist die LEBENSBILANZ der Wallet auf Polymarket (inkl. Wahlen und Krypto) und deshalb
    hier nur Beiwerk, nie der Rang — s. sharp_gate.py.
    """
    top = WALLET_TOP_N if top is None else top
    if scores is None:
        scores = ((_load("poly_wallet_track.json") or {}).get("scores")) or {}
    raus = []
    for w, v in (scores or {}).items():
        if not isinstance(v, dict):
            continue
        try:
            grad = SG.sharp_grade(v)
        except Exception:
            grad = 0
        if not grad:
            continue
        n = int(v.get("n") or 0)
        wins = int(v.get("wins") or 0)
        if n <= 0:
            continue
        raus.append({"wallet": w, "kurz": (w[:6] + "…" + w[-4:]) if len(w) > 12 else w,
                     "n": n, "wins": wins, "hit": round(wins / n, 4),
                     "hitLb": round(SG.wilson_lb(wins, n), 4),
                     "clv": round(float(v.get("clvSumPP") or 0) / n, 3),
                     "usd": int(v.get("usd") or 0),
                     "pnl": v.get("pnl") if isinstance(v.get("pnl"), (int, float)) else None,
                     "grad": round(float(grad), 3),
                     "sportarten": sorted((v.get("bySport") or {}).keys())})
    raus.sort(key=lambda r: (-r["hitLb"], -r["n"]))
    return raus[:top]


def killer_schublade(results=None, now=None) -> list:
    """Die Konjunktion aus killer.py als eigene Schublade.

    29.08.2026: die übrigen Betfair-Schubladen rechnen auf Aggregaten und können deshalb nie
    über „geprueft" hinauskommen — im Aggregat steht kein CLV je Signal. Die Konjunktion
    rechnet auf den Rohzeilen von betfair_track_results.json, dort liegt clvBf je Zeile.
    Sie kann also wirklich freigegeben werden. Genau das ist der Punkt: die Sektion, die Lucas
    blind spielen können soll, muss sich selbst freigeben statt behauptet zu werden."""
    try:
        from killer import schublade
    except Exception:
        return []
    out = []
    # 31.08.2026 (Lucas: „nur die Top 5 lassen oder erweitert?"): statt einer Schublade ueber
    # alle Ligen zwei getrennte. Gemessen war die Frage nicht entscheidbar — Top-5 hat den
    # besseren ROI-Punktschaetzer (n=10), der Rest den einzigen CLV mit Untergrenze ueber null
    # (n=70). Getrennt qualifiziert sich jede selbst, und keine Zeile geht verloren.
    # Beide Zuschnitte werden IMMER gemeldet, auch leer — „Top-5 sammelt noch" ist eine
    # Aussage, ein fehlender Eintrag waere keine.
    for scope, name in (("top5", "Konjunktion · Top-5 + MLS"),
                        ("rest", "Konjunktion · übrige Ligen")):
        s = schublade(results, scope=scope)
        if s["renditen"]:
            out.append(bewerte(name, "betfair", s["renditen"], s["clvs"],
                               {"art": "konjunktion", "quelle": "killer.py", "zuschnitt": scope},
                               s["letzter"], now))
    # 30.08.2026 (Lucas: „das wechselt auch ohne dass ich die Seite aktualisiere"): die Sektion
    # HÄLT ihre Treffer jetzt bis zum Anpfiff, weil `inflow` ein Intervall-Delta ist und sonst
    # im Minutentakt blinkt. Das ist eine andere Menge als die oben gemessene (dort zählt der
    # letzte Vor-Anpfiff-Schnappschuss), und sie wird zum HALTEPREIS abgerechnet — dem Preis,
    # den die Sektion wirklich gezeigt hat. Beide stehen nebeneinander, damit sichtbar wird,
    # ob das Halten die Kante kostet.
    try:
        from killer import schublade_gehalten
        g = schublade_gehalten()
        if g["renditen"]:
            out.append(bewerte("Konjunktion · gehalten (Haltepreis)", "betfair",
                               g["renditen"], g["clvs"],
                               {"art": "konjunktion_gehalten", "quelle": "killer_ledger.json"},
                               g["letzter"], now))
    except Exception:
        pass
    return out


def push_schubladen(ledger=None, now=None) -> list:
    """Das Schattenbuch des Card-Pushes: „ABWÄGEN · gepusht" gegen „ABWÄGEN · aussortiert".

    30.08.2026 (Lucas): der Gegensignal-Filter schneidet rund vier Fünftel der ABWÄGEN aus dem
    Public-Push. Damit dieser Schnitt sich nicht selbst bestätigt, laufen die Aussortierten im
    Schattenbuch weiter mit — und stehen hier nebeneinander. Wären sie in Wahrheit die besseren,
    stünde es in dieser Tabelle.

    Ohne CLV je Pick bleibt beides höchstens „geprueft" — die Cards führen keinen. Das ist
    gewollt: der Schnitt darf sichtbar besser dastehen, ohne allein deshalb freigegeben zu sein."""
    try:
        from pick_push_ledger import schubladen
    except Exception:
        return []
    out = []
    for name, d in sorted((schubladen(ledger) or {}).items()):
        if not d["renditen"]:
            continue
        out.append(bewerte(name, "cards", d["renditen"], [],
                           {"art": "push_filter", "quelle": "pick_push_ledger.py"},
                           d["letzter"], now))
    return out


# ── Welche Spiele fallen JETZT in eine freigegebene Schublade? ──────────────────────────
# 08.09.2026 (Lucas: „also es wird nur das geschickt, aber nicht welche Spiele — na dann brauch
# ich das eher nicht. Interessant waere ja, welche Spiele fuer die freigegebenen Schubladen in
# Frage kaemen").
#
# Er hat recht, und der Einwand trifft den Kern: das Register urteilte ueber Schubladen und hoerte
# genau dort auf, wo es nuetzlich wird. „Liga · ABWAEGEN ist freigegeben" ist eine Aussage ueber
# 91 abgerechnete Plays der Vergangenheit; was man damit TUT, steht erst in den 31 offenen Picks,
# die heute unter dieselbe Definition fallen.
#
# ⭐ DIE SCHNITTE WERDEN NICHT NACHGEBAUT. Jede Schublade traegt ihre Definition schon als `meta`
# mit (`art` + `wert` + `datensatz`), weil `bewerte` sie beim Zaehlen dorthin geschrieben hat.
# Hier wird dieselbe Definition auf die OFFENEN Plays angewandt. Baute diese Funktion die
# Bedingung nach, haetten wir zwei Wahrheiten ueber eine Schublade — und die Liste koennte
# Spiele zeigen, die in der Messung nie gezaehlt haetten.
#
# ⚠️ „NICHT AUFLOESBAR" IST NICHT „KEINE SPIELE". Fuer die Betfair-Aggregat-Schubladen gibt es
# gar keine Liste offener Plays (sie rechnen auf Eimern, nicht auf Zeilen). Eine leere Liste
# waere dort eine Luege in dieselbe Richtung wie ein fehlender CLV, der als „nein" gelesen wird.
# Deshalb sagt das Ergebnis ausdruecklich, ob die Frage ueberhaupt beantwortbar war.


def _fixture_index(d):
    """Fixture-Schluessel -> Fixture, in derselben Form wie `_card_plays` sie bildet."""
    idx = {}
    for gk, g in ((d or {}).get("groups") or {}).items():
        for fx in (g.get("fixtures") or []):
            idx["%s-%s-%s-%s" % (gk, fx.get("matchday"), fx.get("home"), fx.get("away"))] = fx
    for kf in ((d or {}).get("koFixtures") or []):
        idx["KO-%s-%s-%s" % (kf.get("round"), kf.get("home"), kf.get("away"))] = kf
    return idx


def _zeitpunkt(x):
    """ISO-Zeitstempel -> aware datetime, sonst None. Ein Datum ohne Zeitzone gilt als UTC —
    dieselbe Annahme wie in `_alter_tage`, damit nicht zwei Stellen verschieden rechnen."""
    if not x:
        return None
    try:
        t = datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t


def _kickoff(fx):
    for feld in ("kickoff", "date"):
        t = _zeitpunkt(fx.get(feld))
        if t is not None:
            return t
    return None


def _card_offen(datensatz, verdict, dateien=None, now=None) -> list:
    """Offene Card-Picks eines Datensatzes mit diesem Verdikt. REIN (Dateien injizierbar)."""
    now = now or _now()
    raus = []
    for datei, label in (dateien or CARD_DATEIEN):
        if label != datensatz:
            continue
        d = _load(datei)
        fixtures = _fixture_index(d)
        for k, picks in ((d or {}).get("picks") or {}).items():
            fx = fixtures.get(k)
            if not fx:
                continue
            ko = _kickoff(fx)
            if ko is None or ko <= now:
                continue                      # angepfiffen oder ohne Datum: kein offener Play
            for x in (picks or []):
                if not isinstance(x, dict) or x.get("verdict") != verdict:
                    continue
                # Dieselbe Ausschlussregel wie in `_card_plays` — sonst zeigt die Liste Picks,
                # die in der Messung nie mitgezaehlt haetten.
                if x.get("trackingExcluded") or x.get("boldAlt"):
                    continue
                raus.append({
                    "spiel": "%s v %s" % (fx.get("homeName") or fx.get("home"),
                                          fx.get("awayName") or fx.get("away")),
                    "auswahl": x.get("market"), "quote": x.get("odds"),
                    "anpfiff": ko.isoformat(), "datensatz": label,
                    "id": "%s|%s" % (k, x.get("market")),
                })
    raus.sort(key=lambda r: r["anpfiff"])
    return raus


def _poly_name(key, maerkte):
    """„A vs B" aus den beiden Seitennamen des Poly-Marktes — sonst None.

    Der Shortlist-Eintrag traegt nur den Marktschluessel („sea-fro-ven-2026-09-06-more-markets"),
    und der ist als Spielname unlesbar. Die Namen stehen im Markt selbst; sie werden hier
    NACHGESCHLAGEN und nicht aus dem Schluessel geraten — aus „sea-fro-ven" laesst sich kein
    Vereinsname rekonstruieren, nur einer erfinden.
    """
    m = (maerkte or {}).get(key)
    if not isinstance(m, dict):
        return None
    namen = [str(n) for n in ((m.get("prices") or {}).keys()) if str(n).strip()]
    return " vs ".join(namen) if len(namen) == 2 else None


def _poly_anpfiff(z):
    """Anpfiff aus `firstTs` + `htkAtEntry` (Stunden bis Anpfiff im Moment des Einstiegs).

    Keine Schaetzung: beide Werte stehen in der Zeile und ihre Summe IST der Anpfiff. Fehlt
    einer, gibt es keinen — und dann steht auch keiner da.
    """
    t = _zeitpunkt(z.get("firstTs"))
    h = z.get("htkAtEntry")
    if t is None or not isinstance(h, (int, float)):
        return None
    return (t + timedelta(hours=float(h))).isoformat()


def _poly_offen(pruef, track=None, now=None, maerkte=None) -> tuple:
    """Offene Poly-Shortlist-Zeilen, die `pruef(zeile)` erfuellen. REIN.

    Gibt (plays, laufend). Angepfiffene Zeilen fliegen raus: das Depot laesst eine offene
    Position stehen, bis sie abgerechnet ist — als Handlungsvorschlag ist ein laufendes Spiel
    aber nichts mehr wert. Ohne bestimmbaren Anpfiff bleibt die Zeile drin (unbekannt ist kein
    „vorbei"), traegt dann aber auch kein Datum.
    """
    now = now or _now()
    d = track if track is not None else _load("poly_shortlist_track.json")
    if maerkte is None:
        maerkte = _load("poly_money_upcoming.json", {})
    op = (d or {}).get("open") or []
    zeilen = list(op.values()) if isinstance(op, dict) else list(op)
    raus, _laufend = [], 0
    for z in zeilen:
        if not isinstance(z, dict) or not pruef(z):
            continue
        ko = _poly_anpfiff(z)
        if ko is not None and _zeitpunkt(ko) <= now:
            # ⭐ Gezaehlt, nicht nur verworfen. „Keine Kandidaten" und „alle vier laufen schon"
            # sehen als leere Liste gleich aus und sind zwei voellig verschiedene Auskuenfte —
            # die erste heisst warten, die zweite heisst zu spaet.
            _laufend += 1
            continue
        key = z.get("key")
        raus.append({"spiel": _poly_name(key, maerkte) or key, "schluessel": key,
                     "auswahl": z.get("side"),
                     "quote": (round(1.0 / float(z["entryPrice"]), 2)
                               if isinstance(z.get("entryPrice"), (int, float)) and z["entryPrice"] else None),
                     "anpfiff": ko, "datensatz": z.get("cat") or z.get("league"),
                     "conv": z.get("conv"), "warum": (z.get("reasons") or [])[:2],
                     "id": "%s|%s" % (key, z.get("side"))})
    raus.sort(key=lambda r: (r["anpfiff"] is None, r["anpfiff"] or ""))
    return raus, _laufend


def offene_plays(zeile, karten=None, track=None, now=None) -> dict:
    """Welche offenen Plays fallen unter DIESE Schublade? -> {aufloesbar, plays, grund}.

    Der Schnitt kommt aus der `meta` der Schublade, nicht aus einer zweiten Regel hier.
    """
    strom, art = zeile.get("strom"), zeile.get("art")
    if strom == "cards" and art == "verdict" and zeile.get("wert") and zeile.get("datensatz"):
        return {"aufloesbar": True,
                "plays": _card_offen(zeile["datensatz"], zeile["wert"], karten, now)}
    if strom == "poly" and art == "gate" and zeile.get("schublade") == "Public-Kandidaten":
        pl, lf = _poly_offen(lambda z: bool(z.get("public")), track, now)
        return {"aufloesbar": True, "plays": pl, "laufend": lf}
    if strom == "poly" and art == "conviction" and isinstance(zeile.get("wert"), int):
        _w = zeile["wert"]
        pl, lf = _poly_offen(lambda z: int(z.get("conv") or 0) == _w, track, now)
        return {"aufloesbar": True, "plays": pl, "laufend": lf}
    # Betfair rechnet auf AGGREGATEN (Liga x Markt), nicht auf einzelnen offenen Zeilen — es gibt
    # hier gar keine Liste, aus der sich „diese Spiele" bilden liesse. Das ist eine Auskunft ueber
    # unsere Datenlage, keine ueber den heutigen Spielplan, und wird auch so ausgegeben.
    return {"aufloesbar": False, "plays": [],
            "grund": ("für diesen Schnitt gibt es keine Liste offener Plays — die Schublade "
                      "rechnet auf Aggregaten, nicht auf einzelnen Zeilen")}


def spiele(zeilen, karten=None, track=None, now=None) -> list:
    """Je FREIGEGEBENER Schublade die offenen Plays, die unter sie fallen."""
    raus = []
    for z in (zeilen or []):
        if z.get("status") != "freigegeben":
            continue
        e = offene_plays(z, karten, track, now)
        raus.append({"schublade": z.get("schublade"), "strom": z.get("strom"),
                     "roiLb": z.get("roiLb"), "clvUrteil": z.get("clvUrteil"),
                     "aufloesbar": e["aufloesbar"], "grund": e.get("grund"),
                     # `laufend` trennt „keine Kandidaten" von „alle laufen schon" — als leere
                     # Liste sehen die beiden gleich aus und heissen das Gegenteil voneinander.
                     "laufend": e.get("laufend") or 0,
                     "n": len(e["plays"]), "plays": e["plays"]})
    return raus


# ── Zusammenbau ─────────────────────────────────────────────────────────────────────────
RANG = {"freigegeben": 0, "kandidat": 1, "geprueft": 2, "sammelt": 3, "ruht": 4}


# 08.09.2026 (Lucas: „dann weiss ich fuer mich auch was ich besser folgen kann — eher Poly, eher
# Betfair, eher Konsens, eher Cards").
#
# ⭐ Die Schubladen eines Stroms UEBERLAPPEN sich: „Conviction 5" (n=125) und „Mix money+sharp"
# (n=89) sind zwei Schnitte durch dieselben Poly-Plays. Wer sie aufsummiert, erfindet eine Zahl —
# 580 „Plays" aus vielleicht 200. Deshalb wird je Strom EINE ueberschneidungsfreie Zerlegung
# gerechnet und ihr Name mitgeschrieben, damit auf dem Board steht, worueber summiert wurde.
#
# Gewaehlt ist jeweils die Zerlegung, unter der jeder Play GENAU EINMAL vorkommt:
#   cards   → Verdikt   (ein Pick hat ein Verdikt)
#   poly    → Conviction (ein Play hat eine Conviction-Stufe)
#   betfair → Markt     (ein Play gehoert zu einem Markt)
# „Mix …", „Gate", „Konjunktion" und die Liga×Markt-Schubladen sind Schnitte durch dieselben
# Plays und bleiben deshalb aussen vor — sie stehen einzeln in der Liste.
STROM_ZERLEGUNG = {"cards": ("verdict", "nach Verdikt"),
                   "poly": ("conviction", "nach Conviction"),
                   "betfair": ("markt", "nach Markt")}


# Woher die Zahl einer Strom-Kachel kommt — in einem Satz, den man ohne Vorwissen liest.
# 08.09.2026: ohne ihn stand „Polymarket −6,6 %" neben einer Track-Seite mit „+0,1 %", und beide
# waren richtig: verschiedene Mengen, gleich beschriftet.
def strom_basis(strom, track=None) -> str | None:
    if strom != "poly":
        return None
    d = track if track is not None else _load("poly_shortlist_track.json")
    st = (d or {}).get("settled") or []
    st = list(st.values()) if isinstance(st, dict) else st
    if not st:
        return None
    eng = aktuelle_engine(d)
    gesperrt = set((d or {}).get("blockedCats") or [])
    zaehlt = [x for x in st if (not eng or x.get("ev") == eng) and _row_cat(x) not in gesperrt]
    teile = ["%d von %d abgerechneten Plays" % (len(zaehlt), len(st))]
    if eng:
        teile.append("nur Engine %s" % eng)
    if gesperrt:
        teile.append("ohne %s" % ", ".join(sorted(gesperrt)))
    return " · ".join(teile)


def stroeme(zeilen, track=None) -> list:
    """Je Strom eine Zeile aus seiner ueberschneidungsfreien Zerlegung. REIN.

    Gibt n, ROI, P/L und CLV — und `zerlegung`, damit niemand die Zahl fuer „alles von Poly"
    haelt. Ein Strom ohne die passende Zerlegung kommt gar nicht vor, statt mit einer Summe
    ueber Ueberlappungen dazustehen.
    """
    out = []
    for strom, (art, label) in STROM_ZERLEGUNG.items():
        # RUHENDE Schubladen bleiben draussen. Nicht aus Kosmetik: die WM-Schubladen tragen
        # 158 der 314 Card-Plays, und die WM ist vorbei. Eine Zeile „Cards" mit halber
        # WM-Historie beantwortet „wem soll ich JETZT folgen" mit einem Turnier von gestern.
        rs = [r for r in (zeilen or [])
              if r.get("strom") == strom and r.get("art") == art and r.get("status") != "ruht"
              and isinstance(r.get("roi"), (int, float)) and (r.get("n") or 0) > 0]
        if not rs:
            continue
        n = sum(r["n"] for r in rs)
        pl = sum(r["pl"] for r in rs if isinstance(r.get("pl"), (int, float)))
        _c = [(r["clv"], r["n"]) for r in rs if isinstance(r.get("clv"), (int, float))]
        clv = (sum(c * k for c, k in _c) / sum(k for _, k in _c)) if _c else None
        out.append({"strom": strom, "zerlegung": label, "art": art, "gruppen": len(rs),
                    "basis": strom_basis(strom, track),
                    "n": n, "pl": round(pl, 2), "roi": round(pl / n, 4) if n else None,
                    "clv": round(clv, 3) if clv is not None else None,
                    # Wie viele der Schubladen DIESES Stroms tragen ueberhaupt einen Beleg?
                    # Das ist die ehrliche Antwort auf „wem soll ich folgen" — nicht der Schnitt.
                    "belegte": len([r for r in (zeilen or [])
                                    if r.get("strom") == strom
                                    and isinstance(r.get("roiLb"), (int, float)) and r["roiLb"] > 0])})
    out.sort(key=lambda r: -(r.get("roi") or -9))
    return out


# ── Strom 6: vorangemeldete Kandidaten ──────────────────────────────────────────────────
def _fade_unter_plays() -> list:
    """Die Fade-Schublade als Plays, die `vorregistrierung._rendite` versteht.

    Die Rendite kommt aus `fade_unter.fade_rendite()` — eine Quelle, nicht zwei. Baute diese
    Funktion die Rechnung nach, haetten wir zwei Wahrheiten ueber dieselbe Zahl, und die
    Schublade koennte etwas anderes messen als die Flaeche im Radar zeigt.
    """
    try:
        import fade_unter as FU
    except Exception:
        return []
    try:
        zeilen = FU._store.load(str(BASE / "betfair_track_results.json"))
    except Exception:
        return []
    raus = []
    for z in (zeilen or []):
        if not FU.gilt(z):
            continue
        wert, _q = FU.fade_rendite(z)
        if wert is None:
            continue
        raus.append({"market": z.get("market"), "fav": z.get("fav"),
                     "pnl": wert, "stake": 1.0, "settledAt": z.get("settledAt")})
    return raus


def _liga_conv5_plays() -> list:
    """Die Liga-Card-Picks als Plays, die `vorregistrierung._rendite` versteht.

    11.09.2026 — der Zuschnitt zur Frage, wo die BET-Schwelle der Liga sitzen sollte. Das Lern-Ledger fuehrt `odds` und
    `result` ("WIN"/"LOSS"), `_rendite` erwartet `odd` und `win` (bool); ausserdem heisst der
    Zeitstempel dort `resolvedAt`, waehrend `teilen()` nach settledTs/settledAt/resolvedTs sucht.
    Ohne diese Uebersetzung wuerde die Schublade still LEER messen und ewig „0 von 80" melden —
    genau der Defekt, den `_rendite` im eigenen Docstring als „Geduld-Tarnung" beschreibt.

    Bewusst das LEDGER und nicht liga-data.json: dort steht je Pick der Stand, mit dem er ins
    Rennen ging (eingefroren bei Anpfiff), nicht der spaeter ueberschriebene.
    """
    d = _load("liga_signal_ledger.json")
    zeilen = d if isinstance(d, list) else ((d or {}).get("records") or [])
    raus = []
    for z in zeilen:
        if not isinstance(z, dict):
            continue
        o, r = z.get("odds") or z.get("entryOdd"), z.get("result")
        if not isinstance(o, (int, float)) or o <= 1.0 or r not in ("WIN", "LOSS"):
            continue
        raus.append({"convictionScore": z.get("convictionScore"),
                     "odd": float(o), "win": (r == "WIN"),
                     "clvPP": z.get("clvPP"),
                     # `teilen()` liest settledTs | settledAt | resolvedTs — der Ledger nennt es
                     # resolvedAt. Unuebersetzt waere JEDER Play „vor der Anmeldung" und das
                     # Urteil bliebe fuer immer leer.
                     "resolvedTs": z.get("resolvedAt")})
    return raus


def vorregistrierte_schubladen(track=None, reg=None, now=None, schreiben=True) -> list:
    """Zuschnitte, die VOR der Messung festgeschrieben wurden — s. vorregistrierung.py.

    Der Unterschied zu jeder anderen Schublade hier: das Urteil zaehlt ausschliesslich Plays, die
    NACH der Anmeldung abgerechnet wurden. Der Rueckblick, aus dem die Idee stammt, laeuft als
    `rueckblick`/`nDavor` mit — sichtbar, aber ausdruecklich nicht als Beleg. Ein Ausschnitt, den
    man nachtraeglich schneidet, weil er gut aussieht, sieht immer gut aus.

    Die Anmeldung passiert beim ERSTEN Lauf von selbst (mit dem Rueckblick als Anlass) und wird nie
    ueberschrieben — der Zeitstempel ist der ganze Wert der Datei."""
    try:
        import vorregistrierung as VR
    except Exception:
        return []
    now = now or _now()
    d = track if track is not None else _load("poly_shortlist_track.json")
    st = (d or {}).get("settled") or []
    st = list(st.values()) if isinstance(st, dict) else st
    # Je Zuschnitt die eigene Quelle. Der Poly-Zuschnitt rechnet auf dem Shortlist-Depot, der
    # Buecher-Score auf punkte_ledger.json — zwei verschiedene Buecher, ein Register.
    _quellen = {"poly_bf_bestaetigt": st,
                "esport_ohne_wallet": st,
                "buecher_score_hoch": [x for x in (_load("punkte_ledger.json") or [])
                                       if isinstance(x, dict)],
                # 06.09.2026: der Fade misst NICHT die Zeile, wie sie im Ledger steht — er misst
                # die GEGENSEITE. Deshalb kommt die Rendite hier fertig als pnl/stake herein,
                # inklusive Kommission. Wuerde man die Rohzeile durchreichen, rechnete
                # `_rendite` die Geldseite (odd/win) und die Schublade maesse das Gegenteil
                # von dem, was sie behauptet.
                "fade_unter": _fade_unter_plays(),
                # 11.09.2026: die Liga-Picks kommen aus dem Lern-Ledger, uebersetzt auf die
                # Buchform, die `_rendite` und `teilen` verstehen.
                "liga_conv_ab_5": _liga_conv5_plays()}
    reg = VR.laden() if reg is None else dict(reg)
    vorher = dict(reg)
    out = []
    for kennung, z in VR.ZUSCHNITTE.items():
        if kennung not in reg:
            # Anlass festhalten: die Zahl, die zur Anmeldung gefuehrt hat — als Kontext, nie als Beleg.
            _alt = [x for x in _quellen.get(kennung, st) if isinstance(x, dict) and z["pruef"](x)]
            _r, _c = VR.kennzahlen(_alt)
            reg = VR.anmelden(reg, kennung, now=now, rueckblick={
                # 02.09.2026: untergrenze() gibt unter drei Werten bewusst None zurueck (keine
                # Streuungsschaetzung = keine erfundene Zahl). round(None) sprengte dann die
                # allererste Anmeldung eines Zuschnitts — genau den Lauf, der das Register anlegt.
                "n": len(_r), "roi": _rnd(_mittel(_r), 4),
                "roiLb": _rnd(untergrenze(_r), 4),
                "clv": _rnd(_mittel(_c), 3),
                "hinweis": "Anlass der Anmeldung — NICHT Teil des Urteils (rueckwaerts geschnitten)"})
        e = reg[kennung]
        name = "🔒 " + z["name"]
        if VR.signatur_bruch(reg, kennung):
            out.append({"schublade": name, "strom": z["strom"], "n": 0, "status": "ruht",
                        "grund": "Zuschnitt seit der Anmeldung geändert — die Vorregistrierung ist "
                                 "ungültig. Neu anmelden (und damit bei null anfangen) oder den "
                                 "alten Zuschnitt wiederherstellen.",
                        "roi": None, "roiLb": None, "clv": None, "clvLb": None,
                        "fehltN": 0, "art": "vorangemeldet", "ungueltig": True})
            continue
        seit, davor = VR.teilen(_quellen.get(kennung, st), kennung, e)
        r, c = VR.kennzahlen(seit)
        rd, _ = VR.kennzahlen(davor)
        letzter = max((str(x.get("settledTs") or x.get("settledAt") or "") for x in seit),
                      default="") or None
        ziel = e.get("zielN") or MIN_N
        z_row = bewerte(name, z["strom"], r, c,
                        {"art": "vorangemeldet", "angemeldet": e.get("angemeldet"),
                         "zielN": ziel, "warum": z.get("warum"), "quelle": z.get("quelle")},
                        letzter=letzter, now=now)
        # Eigenes Ziel-n: der Zuschnitt hat seines bei der Anmeldung genannt, damit niemand aufhoert
        # zu messen, sobald die Zahl gefaellt.
        if len(r) < ziel and z_row["status"] not in ("ruht",):
            # 06.09.2026 (Lucas: „ich versteh nicht, was da wirklich angezeigt wird und wieso").
            # Hier stand die Ruecksetzung auf „kandidat" bedingungslos. Auf dem Board ergab das:
            #
            #   🔒 Poly-Auswahl · von Betfair bestaetigt — Kandidat, „59 von 60 Plays — noch 1"
            #   dieselbe Zeile:  n=59, ROI −9,4 %, ROI-Untergrenze **−30,6 %**
            #
            # „Noch ein Play" liest sich wie kurz vor der Freigabe. Die Zahl daneben sagt das
            # Gegenteil: bei 59 Plays und einer Untergrenze von −30,6 % kann ein einzelner Play
            # nichts mehr drehen. Das ist die Klasse *ein Satz behauptet, was die Zahl daneben
            # widerlegt* — und sie sass ausgerechnet in der obersten Kachel.
            #
            # Das Ziel-n aus der Anmeldung bleibt (niemand hoert auf zu messen, sobald die Zahl
            # gefaellt). Aber eine bereits ENTSCHIEDENE Messung wird nicht mehr in Hoffnung
            # umgeschrieben: liegt eine Untergrenze vor und ist sie klar negativ, bleibt das
            # Urteil stehen und der Zaehlstand wandert in den Grund.
            _entschieden = (z_row.get("roiLb") is not None and z_row["roiLb"] <= MIN_ROI_LB)
            if _entschieden:
                z_row["grund"] = ("ROI nicht belegt über null (Untergrenze %.1f %% aus %d Plays) — "
                                  "das Ziel von %d Plays läuft weiter, ändert aber nichts mehr"
                                  % (z_row["roiLb"] * 100, len(r), ziel))
            else:
                z_row["status"] = "sammelt" if len(r) < KANDIDAT_N else "kandidat"
                z_row["grund"] = ("%d von %d Plays SEIT der Anmeldung (%s) — vorher abgerechnete "
                                  "zählen nicht" % (len(r), ziel, str(e.get("angemeldet"))[:10]))
            z_row["fehltN"] = ziel - len(r)
        z_row["nDavor"] = len(rd)
        z_row["rueckblick"] = e.get("rueckblick")
        out.append(z_row)
    if schreiben and reg != vorher:
        try:
            VR.schreiben(reg)
        except Exception:
            pass          # Register ist Beiwerk; das Urteil dieses Laufs steht trotzdem
    return out


def baue(engine=None, track=None, cards=None, betfair=None, now=None) -> dict:
    now = now or _now()
    # 01.09.2026: die Datei muss sagen, WORAUF sie gefiltert hat. Vorher stand hier der rohe
    # Parameter — bei `engine=None` also `null`, obwohl gefiltert wurde. Eine Datei, die ueber
    # ihre eigene Filterung schweigt, ist genau der Grund, warum der tote Filter monatelang
    # niemandem auffiel.
    if engine is None:
        _t = track if track is not None else _load("poly_shortlist_track.json")
        engine_benutzt = aktuelle_engine(_t)
    else:
        engine_benutzt = engine or None          # False (= bewusst nicht filtern) -> null
    zeilen = (poly_schubladen(track, engine) + card_schubladen(cards)
              + betfair_schubladen(betfair) + betfair_public_schubladen()
              + killer_schublade(now=now)
              + push_schubladen(now=now) + vorregistrierte_schubladen(track, now=now))
    # Nach ALLEN Quellen: die Aggregat-Zweige tragen ihr `clv` erst nach `bewerte()` nach
    # (s. `clv_urteil` — 15.09.2026). Ohne diese Zeile stuende dort ein Urteil von vorher.
    clv_urteil_nachziehen(zeilen)
    # 22.09.2026: der Mehrfachtest-Befund wird jetzt GERECHNET und gezogen — vorher stand die
    # Funktion im Modul und wurde von niemandem aufgerufen.
    _ausbeute = ausbeute_ueber_huerde(zeilen)
    mehrfachtest_nachziehen(zeilen, _ausbeute)
    # 22.09.2026: die Vorwaerts-Bilanz seit der Freigabe — die einzige Zahl, die die
    # Mehrfachtest-Frage entscheidet, und bis heute nur als git-Rekonstruktion vorhanden.
    try:
        _stand_alt = json.loads(STAND_FILE.read_text(encoding="utf-8")) if STAND_FILE.exists() else {}
    except Exception:
        _stand_alt = {}
    # 22.09.2026, noch am selben Tag: `baue()` hat den Stand hier SELBST geschrieben. Damit hat
    # jeder Probelauf — meiner von Hand, jeder Test, der `baue()` aufruft — einen Freigabe-Beginn
    # in das Protokoll gestempelt. Zwei erfundene Startpunkte ("Conviction 7", "Mix money",
    # 22.09. 20:25) sind so entstanden und waeren nie wieder verschwunden: ein bestehender
    # Eintrag wird nie neu gestempelt, das ist der Sinn der Datei. freigabe.json waere davon
    # harmlos, weil sie jeder Lauf komplett neu schreibt — ein Gedaechtnis aber nicht.
    # Seither: `baue()` RECHNET den Stand und gibt ihn zurueck, geschrieben wird er nur von
    # `main()` und nur im Pipeline-Lauf (`stand_schreiben()`).
    zeilen, _stand_alt = nachweis_ziel_nachziehen(zeilen, _stand_alt, now)
    _zeilen2, _stand = seit_freigabe(zeilen, _stand_alt, now)
    zeilen.sort(key=lambda r: (RANG.get(r["status"], 9), -(r.get("roiLb") or -9), -r["n"]))
    frei = [r for r in zeilen if r["status"] == "freigegeben"]
    kand = [r for r in zeilen if r["status"] == "kandidat"]
    return {
        "generatedAt": now.isoformat(),
        "engine": engine_benutzt,
        "engineGefiltert": bool(engine_benutzt),
        "regeln": {"minN": MIN_N, "z": Z, "kandidatAbN": KANDIDAT_N,
                   "maxAlterTage": MAX_ALTER_TAGE,
                   "clvBlockt": CLV_BLOCKT,
                   "text": (("freigegeben = n>=%d UND ROI-Untergrenze>0 UND CLV-Untergrenze>=0 "
                             "UND juengster Play juenger als %d Tage, alles auf der aktuellen "
                             "Engine-Version" % (MIN_N, MAX_ALTER_TAGE))
                            if CLV_BLOCKT else
                            ("freigegeben = n>=%d UND ROI-Untergrenze>0 UND juengster Play "
                             "juenger als %d Tage, alles auf der aktuellen Engine-Version. "
                             "Der CLV blockiert seit 08.09.2026 nicht mehr — er steht als "
                             "Urteil auf jeder Zeile, mit Warnung bei negativ belegtem CLV."
                             % (MIN_N, MAX_ALTER_TAGE)))},
        "freigegeben": frei,
        "kandidaten": kand,
        # Wie viele Schubladen ueber der Huerde liegen — und wie viele der Zufall dort hinlegt.
        # Die Zahl, an der haengt, ob ein Uebertritt eine Nachricht oder der Normalfall ist.
        "ausbeute": _ausbeute,
        # Der fortgeschriebene Stand — NICHT geschrieben, nur gerechnet (s.o.).
        "stand": _stand,
        "stroeme": stroeme(zeilen),
        # Zwei eigene Tabellen — KEINE Schubladen, weil sie zweite
        # Zerlegungen derselben Plays sind (Ligen) bzw. gar keine Plays
        # zaehlen, sondern Wallets.
        # Welche Spiele fallen JETZT unter eine freigegebene Schublade — die Antwort, an der
        # das Register bisher aufhoerte.
        "spiele": spiele(zeilen),
        "ligen": (_ligen := betfair_ligen()),
        # 🔴 29.09.2026 (Lucas' Übersicht-Check: „🏆 Welche Ligen tragen — 263 mit mindestens 30
        # Plays · 8 belegt"). Bei 263 gleichzeitig geprueften Ligen legt der Zufall allein 13,2
        # ueber die Huerde — „8 belegt" ist WENIGER als der Zufall, stand aber gruen da wie ein
        # Fund. Dieselbe Rechnung wie `ausbeute` fuer die Schubladen, fuer die zweite Zerlegung.
        # Fehlerklasse: ein Schwellen-Uebertritt ohne seinen Nenner (s. ausbeute_ueber_huerde).
        "ligenAusbeute": ausbeute_ueber_huerde(_ligen),
        "wallets": poly_wallets(),
        "alle": zeilen,
        "zusammenfassung": {
            "schubladen": len(zeilen), "freigegeben": len(frei), "kandidaten": len(kand),
            "ruhend": len([r for r in zeilen if r["status"] == "ruht"]),
            # ⚠️ `naechsteFreigabe` ist die Entfernung zur MINDESTZAHL, nicht zur Freigabe.
            # 06.09.2026, Lucas las den Badge als „in 12 Plays nächste Chance" — genau so ist er
            # gemeint, und genau so ist er falsch: bei n=30 wird erst GERECHNET. Wie weit es
            # wirklich waere, steht daneben und heisst jetzt auch so.
            "naechsteFreigabe": min([r["fehltN"] for r in kand], default=None),
            # Die kleinste Zahl Plays, ab der bei heutigem Schnitt und heutiger Streuung
            # ueberhaupt eine Schublade ihre ROI-Untergrenze ueber null haette. None = keine
            # einzige hat einen positiven Schnitt; dann ist Warten keine Strategie.
            "naechsterBeleg": min([r["noetigNRoi"] - r["n"] for r in kand
                                   if r.get("noetigNRoi") and r["noetigNRoi"] > r["n"]],
                                  default=None),
        },
    }


def main() -> int:
    from safe_write import write_json_atomic
    # 01.09.2026: Default ist NICHT mehr „kein Filter". Die Engine kommt aus den Daten
    # (s. aktuelle_engine); `FREIGABE_ENGINE=<stempel>` erzwingt eine bestimmte, `=off` schaltet
    # das Filtern ab. Vorher war der Default „gar nicht filtern", und weil die Variable nirgends
    # gesetzt wurde, mischte das Register monatelang Engine-Versionen.
    _ev = os.environ.get("FREIGABE_ENGINE")
    ev = False if str(_ev).lower() == "off" else (_ev or None)
    d = baue(engine=ev)
    # Der Stand gehoert in SEINE Datei, nicht zusaetzlich in freigabe.json: zwei Kopien
    # derselben Buchfuehrung driften. Was jede Zeile braucht, steht als `seitFreigabe` an ihr.
    stand_schreiben(d.pop("stand", None) or {})
    write_json_atomic(BASE / OUT_FILE, d, indent=1)
    z = d["zusammenfassung"]
    print("=== freigabe.py ===")
    print(f"  {z['schubladen']} Schubladen · {z['freigegeben']} freigegeben · {z['kandidaten']} Kandidaten")
    for r in d["alle"][:14]:
        roi = f"{100*r['roi']:+6.1f}%" if r["roi"] is not None else "   —  "
        lb = f"{100*r['roiLb']:+6.1f}%" if r["roiLb"] is not None else "   —  "
        clv = f"{r['clv']:+5.2f}" if r["clv"] is not None else "  —  "
        print(f"  [{r['status']:<11}] {r['strom']:<8} {r['schublade']:<26} n={r['n']:>4} "
              f"ROI {roi} (UG {lb})  CLV {clv}  · {r['grund']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
