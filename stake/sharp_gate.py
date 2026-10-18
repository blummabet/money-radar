#!/usr/bin/env python3
"""
sharp_gate.py — 29.08.2026 (Lucas: „prinzipiell checken, welche Wallets wir tracken").

DIE Definition von „scharf". Vorher gab es vier:

  _pwIsSharpScore  (poly-wallets.js)   n>=4 · rohe Quote >=55% · CLV>=0 · P&L>0 ZWINGEND
  _is_smart        (poly_whale_watch)  n>=8 · Wilson >50%      · CLV>=0 · P&L unbekannt OK
  is_sharp         (poly_live_watch)   handkopierte Klammer der JS-Konstanten
  SHARP_MIN_CLV    (poly_money_broad)  n>=4 · Quote >=50%      · CLV>=1.5pp   (seit 05.08. tot)

Gemessen am Stand vom 29.08. lieferten die zwei lebenden Gates 42 bzw. 16 Wallets bei einer
Schnittmenge von 15 — 27 Wallets trugen also auf der Seite Konviktion, die im Push nie als
bewiesen gegolten haetten. Schlimmer: die beiden behandelten fehlende Daten GENAU UMGEKEHRT.
Das Gate, das wirklich sendet, liess unbekannten P&L durch; das Gate, das nur anzeigt, warf ihn
raus — obwohl nur 13% aller Wallets ueberhaupt einen P&L-Wert haben. Damit entschied nicht die
Qualitaet einer Wallet, sondern ob das 60er-Fetch-Budget sie erwischt hatte.

Was hier gilt, und warum:

  n >= SHARP_MIN_N (8)
      Der alte JS-Boden von 4 war ohnehin Dekoration: `enrich_wallet_pnl` holt P&L erst ab n>=5,
      und das JS-Gate verlangte P&L>0 — von 131 Wallets mit exakt n=4 hatte KEINE einen Wert.
      n>=4 und n>=8 lieferten deshalb dasselbe Ergebnis. Jetzt steht die 8 ehrlich da.

  Wilson-Untergrenze der Trefferquote > 50% (einseitig, z=1.645)
      Eine rohe Quote von 55% bei n=9 ist kein Beweis: 5/9 hat eine Wilson-Untergrenze von 30%.
      Von den 42 Wallets, die das alte JS-Gate „scharf" nannte, bestanden 27 diesen Test nicht —
      darunter eine mit 5/9, CLV +0,03pp und $729 Lebensbilanz. Die Stichprobe entscheidet mit,
      nicht nur der Anteil.

  🔴 Ø CLV >= 0 — ENTFERNT am 22.09.2026
      Lucas, zum wiederholten Mal und diesmal ausdruecklich: „Den CLV von mir aus messe ihn,
      aber ich will, dass der nicht irgendwo irgendwie limitiert. Nur CLV ist leider nicht das
      Wichtige, und das sage ich jetzt schon seit Wochen. Wichtiger ist der Profit. Und siehst
      du, dann fliegen gute Wallets raus." Dieselbe Entscheidung wurde am 08.09. schon fuer das
      Freigabe-Register getroffen (`freigabe.py`: „Der CLV blockiert nicht mehr, er BESCHREIBT")
      — auf der Polymarket-Seite ist sie nie nachgezogen worden.

      Was die Zeile gekostet hat, gemessen am Track vom 22.09.2026 (4.394 Wallets):
          bestehen das Gate heute            27
          ohne diese eine Zeile              48
          also NUR an CLV gescheitert        21   davon 11 mit gemessenem 30-Tage-Profit,
                                                  zusammen +$176.456
      Darunter eine Wallet mit 87 % Trefferquote aus 76 Aufloesungen und +$19.629 in 30 Tagen,
      gekickt wegen Ø CLV −0,13pp. Und eine mit 81 % aus 16 und +$95.712 bei −0,34pp.

      Und die Zeile ist nicht nur teuer, sie ist gegenlaeufig. Vorwaertsprobe (Auswahl an den
      Aufloesungen 17.–19.09., gemessen an denen vom 20.–21.09.; einsatzgewichtet):
          alle Wallets (Basisrate)      n=913   Folge-ROI  −3,8 %
          Ø CLV >= 0  (dieses Gate)     n=108   Folge-ROI −13,6 %
          Ø CLV <  0  (was es kickt)    n=119   Folge-ROI  −5,3 %
          Profit > 0 in der Auswahl     n=205   Folge-ROI  +4,4 %
          Profit <= 0                   n=226   Folge-ROI −34,6 %
          CLV +, aber Profit −          n= 53   Folge-ROI −23,9 %
          Profit +, aber CLV − (gekickt) n=53   Folge-ROI  −2,8 %
      Das Gate waehlte die SCHLECHTERE Haelfte. Der Profit trennt um 39 Prozentpunkte, der CLV
      um −8 (also in die falsche Richtung).
      ⚠️ Duenn: drei Tage Auswahl, zwei Tage Messung — mehr geben die CLV-Tagesbuckets nicht
      her (sie reichen erst bis zum 17.09. zurueck). Das „Halten bis zur Aufloesung"-Modell
      bucht ausserdem Verluste, die niemand genommen hat; verlaesslich ist die ORDNUNG, nicht
      die Hoehe. Aber die Ordnung sagt in beiden Richtungen dasselbe.

      CLV wird weiter berechnet, mitgeschrieben und ueberall angezeigt. Er entscheidet nichts.

  KEIN gemessener 30-Tage-VERLUST
      An die Stelle des CLV tritt das, was Lucas als Massstab nennt. Dieselbe Form wie beim
      P&L: ein AUSSCHLUSS, kein Beweis, und UNBEKANNT ist kein Ausschluss — sonst haengt das
      Gate an der Mess-Abdeckung statt an der Wallet. Die ist heute 386 von 4.394 (9 %) und
      waechst erst mit dem naechsten Pipeline-Lauf auf ~2.500.
      Wirkung: 48 ohne CLV-Zeile → 40 mit dieser. Acht Wallets mit gemessenem 30-Tage-Verlust,
      die vorher „bewiesen scharf" hiessen.

  KEIN bestaetigter Verlierer (P&L bekannt UND < 0)
      P&L ist ein AUSSCHLUSS, kein Beweis. Zwei Gruende: er ist bei 87% der Wallets unbekannt,
      und er misst etwas anderes als die Trefferquote — `hit` kommt aus den Positionen, die wir
      sehen (je Markt die 4 groessten Holder), `pnl` aus `/user-pnl?interval=all`, also der
      gesamten Polymarket-Lebensbilanz inklusive Wahlen und Krypto. Wer +$3,7 Mio aus Wahlmaerkten
      hat, kann im Fussball trotzdem nichts koennen. Beides zu einem „bewiesen" zu verrechnen war
      eine Vermischung zweier Welten.

Die JS-Seite (poly-wallets.js `_pwIsSharpScore`) spiegelt das. Geteilter Code geht ueber die
Sprachgrenze nicht, ein geteilter VERTRAG schon: tests/fixtures/sharp_gate_cases.json haelt die
Faelle, und sowohl pytest als auch node pruefen beide Implementierungen dagegen. Weicht eine ab,
faellt es sofort auf — nicht erst, wenn die Seite etwas anderes behauptet als der Push.

Rein/netzfrei/testbar.
"""
from __future__ import annotations

import math
import os

SHARP_MIN_N = int(os.environ.get("SHARP_MIN_N") or 8)
# 1.645 = 95% einseitig („signifikant ueber 50%"); 1.2816 = 90% (mehr Treffer), 1.96 = strenger.
SHARP_Z = float(os.environ.get("SHARP_Z") or 1.645)


def wilson_lb(wins, n, z: float = SHARP_Z) -> float:
    """Untere Wilson-Grenze der Trefferquote. Robuster als die rohe Quote bei kleinem n:
    sie zieht sich mit der Stichprobe zusammen, statt 5/9 wie 500/900 zu behandeln."""
    n = int(n or 0)
    if n <= 0:
        return 0.0
    p = (wins or 0) / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return centre - margin


def beats_coinflip(wins, n, z: float = SHARP_Z) -> bool:
    """Ist die Trefferquote SIGNIFIKANT ueber 50% — oder koennte es Zufall sein?"""
    return bool(n) and wilson_lb(wins, n, z) > 0.5


def sport_profit(score, fenster: str = "fenster30"):
    """Der gemessene Sport-Profit dieser Wallet im Fenster — oder None, wenn NICHT GEMESSEN.

    22.09.2026. Nicht zu verwechseln mit `pnl`: das ist Polymarkets plattformweite
    Lebensbilanz inklusive Wahlen und Krypto. Dies hier ist Fussball, Tennis, E-Sport —
    gerechnet von `poly_money_broad` aus Anteilen x Einstieg gegen Auszahlung.

    None heisst „nicht gemessen" und darf nie zu 0.0 werden: die Abdeckung liegt heute bei
    9 % der Wallets, ein Gate auf 0.0 wuerde also 91 % der Wallets fuer die Mess-Abdeckung
    bestrafen statt fuer ihre Leistung.
    """
    if not isinstance(score, dict):
        return None
    f = score.get(fenster)
    if not isinstance(f, dict):
        return None
    g = f.get("gewinn")
    return float(g) if isinstance(g, (int, float)) else None


def is_confirmed_money_loser(score, fenster: str = "fenster30") -> bool:
    """Sport-Profit im Fenster GEMESSEN und negativ. Unbekannt ist kein Verlust-Nachweis."""
    g = sport_profit(score, fenster)
    return g is not None and g < 0


def _felder(score):
    """Nimmt beide Formen: die rohe aus poly_wallet_track.json ({n, wins, clvSumPP, pnl}) und die
    abgeleitete des Frontends ({n, hit, avgClv, pnl}). Gibt (n, wins, avg_clv, pnl) zurueck;
    pnl ist None, wenn UNBEKANNT — der Unterschied zu 0.0 ist der ganze Punkt.

    `avg_clv` wird weiterhin geliefert — er wird angezeigt, nur nicht mehr gefragt, wenn es
    ums Ausschliessen geht (s. Modul-Doku, 22.09.2026)."""
    if not isinstance(score, dict):
        return 0, 0, 0.0, None
    n = int(score.get("n") or 0)
    if n <= 0:
        return 0, 0, 0.0, None
    wins = score.get("wins")
    if wins is None:
        wins = round(float(score.get("hit") or 0) * n)
    if "avgClv" in score and score.get("avgClv") is not None:
        avg_clv = float(score.get("avgClv") or 0)
    else:
        avg_clv = float(score.get("clvSumPP") or 0) / n
    pnl = score.get("pnl")
    pnl = float(pnl) if isinstance(pnl, (int, float)) else None
    return n, int(wins), avg_clv, pnl


def is_sharp(score, min_n: int = SHARP_MIN_N, z: float = SHARP_Z) -> bool:
    """Die eine Definition. Siehe Modul-Doku fuer das Warum je Bedingung."""
    n, wins, avg_clv, pnl = _felder(score)
    if n < min_n:
        return False
    if not beats_coinflip(wins, n, z):
        return False
    # 🔴 22.09.2026: hier stand `if avg_clv < 0: return False`. Entfernt — s. Modul-Doku.
    # Der CLV wird weiter gerechnet und angezeigt; er schliesst niemanden mehr aus.
    # 22.09.2026 abends: EIN Aufruf statt zwei. `is_confirmed_loser` entscheidet jetzt selbst,
    # ob die Sport-Zahl oder die Lebensbilanz gilt — vorher standen hier zwei Bedingungen, von
    # denen die erste (`pnl`) die zweite (Sport) ueberstimmen konnte.
    if is_confirmed_loser(score):
        return False
    return True


# ── Der Regler (01.09.2026) ──────────────────────────────────────────────────
# Gemessen, warum das binaere Gate allein zu teuer ist: Wallets auf dem Stand 25.08.
# klassifiziert und danach ausgewertet, was sie WIRKLICH getan haben (Delta der Aggregate
# in poly_wallet_track.json — reine Vorwaerts-Leistung, kein Rueckblick):
#
#   z=1.645 (dieses Gate)  16 Wallets  ->  n=180  54,4% Treffer (UG 48,3%)  Ø CLV +0,26pp
#   z=1.282                24 Wallets  ->  n=251  55,8%         (UG 50,6%)  Ø CLV +0,55pp
#   z=1.036                33 Wallets  ->  n=290  55,2%         (UG 50,3%)  Ø CLV +0,61pp
#
#   die ausgeschlossene Bande (rohe Quote >=55%, Wilson-UG <=50%, CLV>=0):
#                          35 Wallets  ->  n=136  52,2%                     Ø CLV +0,94pp
#
# Die strengste Einstellung liefert die SCHLECHTESTE Vorwaerts-Leistung auf jeder Achse.
# Die Strenge kauft keine Treffsicherheit, sie kauft eine kleinere, verrauschtere Auswahl.
#
# ⚠️ Der Fehler lag nicht in der Schwelle, sondern in der FORM: `is_sharp` ist ein Schalter.
# Eine Wallet mit 60% aus 65 Plays (Wilson-UG 49,8% — zwei Zehntel zu wenig) trug dieselbe
# Null bei wie eine mit 30% aus 8. Genau drei solcher Wallets lieferten danach +1,28 / +1,89 /
# +1,91pp CLV.
#
# Deshalb: fuer Zwecke, die ABWAEGEN (die Conviction), ein Regler statt eines Schalters.
# Fuer Zwecke, die VEROEFFENTLICHEN (der Public-Push), bleibt der Schalter — dort kostet ein
# Fehlalarm Glaubwuerdigkeit, und Strenge ist der richtige Preis dafuer.
#
# ⚠️ NICHT auf den Sieger getunt: vier z-Werte auf EINEM Wochenfenster, da ist der Beste
# teilweise Zufall. Belegt ist nur, dass 1,645 nicht besser ist als lockerer. Die Rampe
# umgeht die Frage, statt sie zu beantworten — sie braucht keinen zweiten Schwellenwert.
GRADE_FLOOR_LB = float(os.environ.get("SHARP_GRADE_FLOOR") or 0.40)


def sharp_grade(score, min_n: int = SHARP_MIN_N, z: float = SHARP_Z,
                floor: float = GRADE_FLOOR_LB) -> float:
    """Wie gut ist diese Wallet BELEGT? 0.0 (gar nicht) bis 1.0 (bewiesen). REIN.

    Die harten Ausschluesse sind dieselben wie in `is_sharp` — zu wenig Plays, bestaetigter
    Verlierer (Lebensbilanz oder gemessener 30-Tage-Sportverlust) geben 0.0. Der CLV ist am
    22.09.2026 aus beiden entfernt worden; er beschreibt, er blockiert nicht. Dazwischen laeuft die Wilson-Untergrenze linear:
    bei >50% voll, bei <=`floor` null. Kein Sprung an der 50%-Klippe.

    Damit gilt per Konstruktion `is_sharp(s) == (sharp_grade(s) >= 1.0)` — eine Definition,
    zwei Lesarten. Der Test haelt das fest.
    """
    n, wins, avg_clv, pnl = _felder(score)
    if n < min_n:
        return 0.0
    # 🔴 22.09.2026: hier stand `if avg_clv < 0: return 0.0`. Entfernt — s. Modul-Doku.
    if is_confirmed_loser(score):
        return 0.0
    lb = wilson_lb(wins, n, z)
    if lb > 0.5:
        return 1.0
    if lb <= floor or floor >= 0.5:
        return 0.0
    return (lb - floor) / (0.5 - floor)


def is_confirmed_loser(score) -> bool:
    """Ist diese Wallet ein nachgewiesener Verlierer? Unbekannt ist KEIN Nachweis.

    🔴 22.09.2026, abends (Lucas: „und sind die Whale-Pushes fuer Public richtig? Wo ein
    Problem?"). Das Problem stand eine Stufe ueber dem CLV-Tor, das heute Mittag gefallen ist,
    und es ist derselbe Fehler: **ein Kriterium, das etwas anderes misst als das, wonach
    entschieden wird.**

    Hier stand nur `pnl < 0`. `pnl` ist Polymarkets Lebensbilanz ueber ALLES — Wahlen, Krypto,
    Sport in einer Zahl. Der Modulkopf sagt das seit dem 29.08. selbst: *„Wer +$3,7 Mio aus
    Wahlmaerkten hat, kann im Fussball trotzdem nichts koennen."* Die Umkehrung stand nie da
    und gilt genauso.

    Gemessen am Track vom 22.09.2026 (1.562 offene Positionen des Public-Trichters):
        als „bestaetigter Verlierer" abgelehnt          527  (ein Drittel aller Positionen)
        davon mit gemessenem 30-Tage-SPORT-Profit       504
        davon im PLUS                                   225
    Die groessten Faelle:
        +$863.781 Sport in 30 Tagen (n=92),  abgelehnt wegen −$2.420.880 Lebensbilanz
        +$814.563 Sport in 30 Tagen (n=63),  abgelehnt wegen   −$189.546 Lebensbilanz
        +$418.011 Sport in 30 Tagen (n=986), abgelehnt wegen   −$450.041 Lebensbilanz
    Die erste davon ist dieselbe Wallet, die auf der Profit-Rangliste des Dashboards auf Rang 2
    steht. Sie war im oeffentlichen Kanal gesperrt.

    Und die Strafe kam doppelt: `_is_smart` (poly_whale_watch) ruft `is_sharp`, das denselben
    Ausschluss enthaelt — die Wallet galt damit auch als „unbewiesen" und musste statt $25.000
    ganze $100.000 auf einer Position haben.

    ── Die Regel jetzt ────────────────────────────────────────────────────────────────────
    Gemessen schlaegt ungemessen. Liegt ein 30-Tage-SPORT-Profit vor, entscheidet der; sonst
    bleibt die Lebensbilanz als Notbehelf. Das ist keine Lockerung, sondern ein Tausch: der
    Zuschnitt wird in BEIDE Richtungen schaerfer. Am Track gemessen steigt die Zahl der
    abgelehnten Positionen von 527 auf 639 — Wallets mit gemessenem Sport-VERLUST, die vorher
    durchkamen, weil ihre Lebensbilanz unbekannt war.

    ⚠️ Die Abdeckung des Sport-Profits liegt bei 386 von 4.394 Wallets (9 %) und waechst mit dem
    naechsten Pipeline-Lauf. Fuer die uebrigen aendert sich nichts.
    """
    if not isinstance(score, dict):
        return False
    geld = sport_profit(score)
    if geld is not None:
        return geld < 0
    pnl = score.get("pnl")
    return isinstance(pnl, (int, float)) and pnl < 0
