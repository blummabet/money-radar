#!/usr/bin/env python3
"""
stake_seltenheit.py — wie ueberraschend ist dieser Einsatz WIRKLICH?
====================================================================
04.09.2026 (Lucas: „ich seh da ein US Open Woman Single, da steht 129 mal so viel. Danach
US Open Main Single 83 mal so viel. Das wird sich, glaub ich, eher glaetten, umso laenger
die Bewerbe laufen.")

Es glaettet sich nicht — es waechst. Gemessen ueber die 31 Ligen mit gelernter Norm am
04.09.: Korrelation zwischen log(n) und log(max/median) = **r = +0,68**.

    Stichproben je Liga   Ligen   Median von max/median
    15-29                 14      7x
    30-59                  9      5x
    60-149                 5     18x
    150-399                2     91x
    400+                   1     83x

Der Grund ist mechanisch und hat mit Wetten nichts zu tun: der **Median steht nach einem Tag
still, das Maximum kann nur steigen**. Wer viele Ziehungen hat, hat irgendwann eine grosse
dabei. `max/median` sortiert damit nach *„wo haben wir am meisten gesammelt"* — nicht nach
*„wo ist etwas Auffaelliges passiert"*. US Open stand oben, weil US Open laeuft.

Dieselbe Krankheit wie bei den Serien vor `zufallPct`: eine 6er-Serie ist in einer Liga, in
der jeder Zweite eine hat, weniger wert als eine 3er dort, wo das selten ist. Und dieselbe wie
im Betfair-Badge am 24.08.: *„Das Badge war nicht ungenau, es war invertiert."*

## Der zweite Befund: der Median normalisiert fast nichts
Die 31 Liga-Mediane liegen zwischen 1.300 und 4.500 — Spanne 3,5x, dicht geklumpt um 2.000.
Das ist eine Anzeigeschwelle von Stake, kein Liga-Merkmal. `x Norm` war damit praktisch
*„Einsatz durch 2.000"*, die Liga steckte kaum drin. Der p90 dagegen spannt 2.753 bis 40.000
(14,5x) — der Schwanz traegt die Liga-Information, die Mitte nicht.

## Was hier stattdessen gerechnet wird
Nicht *„wie viel groesser als normal"*, sondern *„wie viele Wetten dieser Groesse haetten wir
bei dem, was wir von dieser Liga gesehen haben, ueberhaupt erwartet?"*

  1. Schwanz-Index alpha nach **Hill** aus den groessten k Stichproben der Liga selbst
     (Einsatzverteilungen sind schwer-schwaenzig; ein Normal-Modell waere hier falsch).
  2. Schwanzwahrscheinlichkeit  P(X >= x) = (k/n) * (x / x_k)^(-alpha)   fuer x >= x_k.
  3. **erwartetN = n * P(X >= x)** — die erwartete Anzahl solcher Wetten in dem, was wir sahen.
  4. **ueberErwartung = 1 / erwartetN.**

`erwartetN ~ 1` heisst: genau das, was eine Stichprobe dieser Groesse hergibt — das groesste
Element JEDER Stichprobe ist per Konstruktion einmal erwartbar. Erst `erwartetN` deutlich
unter 1 ist ein Befund. Und weil n in Schritt 3 drinsteht, waechst die Zahl NICHT mehr
mit der Sammeldauer.

Probe an den echten Daten vom 04.09.: US Open Women Singles faellt von 129x (Platz 1) auf
*erwartbar* — 263 Ziehungen aus dieser Verteilung liefern so eine Wette. Die Rangliste kippt
vollstaendig.

## Warum EIN Schwanzausschnitt nicht reicht
Der erste Entwurf rechnete mit einem festen k (10 % der Stichprobe) — und war nicht stabil.
Probe an den echten Daten:

    Caribbean Premier League   k=5: 11,5x   k=7: 22,1x   k=11: 1,9x   k=15: 1,2x
    US Open Men Singles        k=28: 0,9x   k=44: 0,8x   k=56: 0,7x   k=112: 0,9x

Die CPL stand mit k=7 auf Platz 1 der ganzen Liste. Ihre groessten Werte sind aber
8.588 / 8.732 / 9.160 / 9.250 / 9.300 / 9.300 / 9.300 / 9.855 / 9.900 — ein **Plateau**, und
darueber eine einzelne 15.580. Ein Hill-Schaetzer, der ganz im Plateau sitzt, sieht dort
keinerlei Streuung, schaetzt alpha riesig und erklaert die naechstgroessere Wette fuer
nahezu unmoeglich. Das ist kein Befund, das ist eine geklemmte Varianz — dieselbe Krankheit
wie „UG +74 % aus drei Plays" am 03.09. und wie die geklemmte Whale-Varianz am 02.09.

Deshalb wird hier **ueber mehrere k gerechnet und der KONSERVATIVSTE Wert genommen** — die
kleinste Ueberraschung, nicht die groesste. Nur was auch beim ungnaedigsten Schwanzausschnitt
noch ueber Erwartung liegt, gilt als auffaellig. Die CPL faellt damit von 22,1x auf 1,2x
(zu Recht), US Open Women von 7,2x auf 3,6x und die Premier League auf 4,0x — beide bleiben.

`kSpanne` gibt die Bandbreite mit aus: liegen die k-Werte weit auseinander, ist die Zahl
wackelig, und das soll man sehen koennen statt es zu raten.

## Und warum auch `ueberErwartung` allein noch kein Urteil ist
Ein Test hat es aufgedeckt: schon in einer **sauberen Stichprobe, in der per Konstruktion
nichts auffaellig ist**, liegt das Maximum in 23-31 % der Faelle bei `ueberErwartung >= 2`.
Der Median liegt bei 1,1 — die Eichung stimmt also —, aber der Schwanz der Nullverteilung ist
breit. Eine feste Schwelle von 2 haette rund jede vierte Liga faelschlich gemeldet.

Schlimmer: die Nullverteilung haengt SELBST von n ab. Ihr 90 %-Punkt laeuft von 3,2 (n=100)
auf 5,0 (n=600). Eine feste Schwelle wuerde also wieder die grossen Ligen bevorzugen — genau
die Krankheit, gegen die dieses Modul gebaut wurde, nur eine Etage hoeher.

Deshalb wird `ueberErwartung` zum Schluss gegen eine **simulierte Nullverteilung** gehalten
(`NULL`, 6.000 Durchlaeufe je Stuetzstelle, in log n interpoliert; die Groesse ist skalen- und
formunabhaengig, eine Tabelle reicht also). Heraus kommt `zufallPct`: *wie oft eine voellig
unauffaellige Liga dieser Groesse ein mindestens so ueberraschendes Maximum hervorbringt.*

Das ist dieselbe Groesse wie `zufallPct` bei den Serien, und sie ist n-frei — 1 % heisst in
einer Liga mit 40 Wetten dasselbe wie in einer mit 600.

## Zwei Untergrenzen, und beide geben `None`, nicht eine harmlose Zahl
- **n < TAIL_MIN_N (40).** Ein Hill-Schaetzer auf 15 Werten schaetzt nichts, er raet. Darunter
  gibt es kein Urteil — und „kein Urteil" muss anders aussehen als ein gemessenes
  „unauffaellig". *Fehlende Information ist keine Erlaubnis.*
- **entarteter Schwanz** (x_k <= 0, alpha nicht endlich). Auch dann `None`.

Wer unter der Grenze liegt, behaelt den Median-Faktor als das, was er ist: eine Beschreibung,
kein Urteil.

REIN/testbar, kein I/O.
"""
from __future__ import annotations

import math

# Ab wie vielen Stichproben ein Schwanz ueberhaupt geschaetzt wird. Darunter: kein Urteil.
TAIL_MIN_N = 40
# Ueber diese Schwanzausschnitte wird gerechnet; gewertet wird der KONSERVATIVSTE.
TAIL_ANTEILE = (0.05, 0.08, 0.10, 0.15, 0.20)
TAIL_MIN_K = 5

# Nullverteilung von `ueberErwartung` am Maximum einer Stichprobe, in der NICHTS auffaellig
# ist: {n: (p90, p95, p99)}, je 6.000 Durchlaeufe. Skalen- und formunabhaengig — deshalb
# haengt sie nur an n. Erzeugt am 04.09.2026; wer TAIL_ANTEILE oder TAIL_MIN_K aendert,
# muss sie neu erzeugen, sonst zeigt `zufallPct` auf eine Eichung, die es nicht mehr gibt
# (ein Test haelt das fest).
NULL = {
    40: (3.65, 5.02, 8.54),
    60: (3.37, 4.63, 8.14),
    100: (3.16, 4.26, 7.81),
    160: (3.55, 5.21, 11.60),
    250: (3.84, 6.19, 15.73),
    400: (4.40, 7.74, 24.61),
    600: (4.96, 8.76, 31.42),
}
NULL_STUFEN = (0.10, 0.05, 0.01)


def _fit(werte, k):
    """Ein Hill-Fit auf den groessten k Werten einer SORTIERTEN Liste. None, wenn entartet."""
    n = len(werte)
    if k < TAIL_MIN_K or k >= n:
        return None
    x_k = werte[-(k + 1)]
    if x_k <= 0:
        return None
    s = sum(math.log(x / x_k) for x in werte[-k:] if x > 0)
    if s <= 0:
        return None
    alpha = k / s
    if not math.isfinite(alpha) or alpha <= 0:
        return None
    return {"n": n, "k": k, "xK": round(x_k, 2), "alpha": round(alpha, 4)}


def schwanz(betraege) -> list | None:
    """Alle Schwanz-Fits einer Liga aus ihren eigenen Stichproben.

    -> [{"n","k","xK","alpha"}, ...]  oder None, wenn die Liga zu duenn ist.
    Mehrere Fits, weil ein einzelner nicht traegt (siehe Kopf).
    """
    werte = sorted(float(b) for b in (betraege or [])
                   if isinstance(b, (int, float)) and not isinstance(b, bool) and b > 0)
    n = len(werte)
    if n < TAIL_MIN_N:
        return None
    ks, fits = set(), []
    for a in TAIL_ANTEILE:
        k = max(TAIL_MIN_K, int(n * a))
        if k in ks:
            continue
        ks.add(k)
        f = _fit(werte, k)
        if f:
            fits.append(f)
    return fits or None


def p_schwanz(betrag, fit) -> float | None:
    """P(X >= betrag) aus EINEM Fit. None, wenn nicht bestimmbar.

    Unterhalb der Schwelle x_k wird NICHT extrapoliert — dort ist der Schwanz nicht
    zustaendig. Der Wert bekommt die Schwanzmasse k/n als Obergrenze, damit die Zahl
    monoton bleibt und nie ueber 1 laeuft.
    """
    if not fit or not isinstance(betrag, (int, float)) or isinstance(betrag, bool) or betrag <= 0:
        return None
    basis = fit["k"] / fit["n"]
    if betrag <= fit["xK"]:
        return basis
    p = basis * (betrag / fit["xK"]) ** (-fit["alpha"])
    if not math.isfinite(p) or p <= 0:
        return None
    return min(1.0, p)


def _null_schwellen(n):
    """Die drei Nullschwellen fuer eine Liga mit n Stichproben, in log n interpoliert."""
    stellen = sorted(NULL)
    if n <= stellen[0]:
        return NULL[stellen[0]]
    if n >= stellen[-1]:
        return NULL[stellen[-1]]
    for a, b in zip(stellen, stellen[1:]):
        if a <= n <= b:
            t = (math.log(n) - math.log(a)) / (math.log(b) - math.log(a))
            return tuple(NULL[a][i] + t * (NULL[b][i] - NULL[a][i]) for i in range(3))
    return NULL[stellen[-1]]


def zufall_pct(ueber_erwartung, n) -> float | None:
    """Wie oft bringt eine voellig unauffaellige Liga dieser Groesse ein mindestens so
    ueberraschendes Maximum hervor? -> 0.01 / 0.05 / 0.10 / 1.0 (Obergrenzen), None ohne Eingabe.

    Bewusst grob: die Simulation traegt drei Stuetzpunkte, keine zweite Nachkommastelle.
    Eine feinere Zahl waere erfunden."""
    if not isinstance(ueber_erwartung, (int, float)) or not isinstance(n, int) or n <= 0:
        return None
    p90, p95, p99 = _null_schwellen(n)
    if ueber_erwartung >= p99:
        return 0.01
    if ueber_erwartung >= p95:
        return 0.05
    if ueber_erwartung >= p90:
        return 0.10
    return 1.0


def seltenheit(betrag, fits) -> dict | None:
    """Wie viele Wetten dieser Groesse waren in dem, was wir sahen, zu erwarten?

    Gewertet wird der KONSERVATIVSTE Schwanzausschnitt — die kleinste Ueberraschung.

    -> {"erwartetN", "ueberErwartung", "zufallPct", "n", "kSpanne"}  oder None.
    `zufallPct` ist das Urteil, `ueberErwartung` die Groesse dahinter.

    `erwartetN` ~ 1  = genau das, was eine Stichprobe dieser Groesse hergibt.
    `erwartetN` 0,05 = so eine Wette erwarten wir erst, wenn wir 20x so viel gesehen haben.
    """
    if isinstance(fits, dict):          # Einzel-Fit bleibt zulaessig
        fits = [fits]
    werte = []
    for f in (fits or []):
        p = p_schwanz(betrag, f)
        if p is None:
            continue
        erwartet = f["n"] * p
        if erwartet > 0:
            werte.append(erwartet)
    if not werte:
        return None
    erwartet = max(werte)               # groesste Erwartung = kleinste Ueberraschung
    spanne = sorted(round(1.0 / e, 2) for e in werte)
    ue = round(1.0 / erwartet, 2)
    n = fits[0]["n"]
    return {"erwartetN": round(erwartet, 4),
            "ueberErwartung": ue,
            "zufallPct": zufall_pct(ue, n),
            "n": n,
            "kSpanne": [spanne[0], spanne[-1]]}
