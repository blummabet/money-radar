#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stake_burst_push.py — 11.09.2026 (Lucas): Einsatz-Bursts bei Stake in den TRADES-Channel.

Der Anlass war eine Nachricht aus einer VIP-Gruppe, die Lucas geschickt hat:

    Cienciano - Montevideo City Torque · Volume: 17605.64$ · 7 bets / 4 bets in 48 sec
    Player to be carded (sure sub) - Cabello, Carlos
      $11580.35 x 3.35 · $1994 x 3.35 · $2990 x 3.35 · $1041.29 x 3.35

Lucas: „mir geht's bei Stake wirklich um die Geldeinsaetze, die dort reinfliessen."

Vier Wetten, EINE Auswahl, DIESELBE Quote, innerhalb einer Minute. Genau dieses Muster laesst
sich im Highroller-Feed erkennen — Wallets und Nutzer nicht (Stake gibt `user` fuer alle 20.000
Zeilen als null zurueck, obwohl das Feld im Schema steht; sie anonymisieren serverseitig).

── Warum gerade diese Schwellen ──────────────────────────────────────────────────────────────
Gemessen an 15.646 abgerechneten Einzelwetten (aus `abrechnung`, NICHT aus dem Feed-Status —
der zieht nicht nach und haette die Basis auf 1.524 gedrueckt):

    >=3 Wetten, 5 Min, ab $10k                     29,0 Bursts/Tag   ROI  +7,1 %   UG  +1,0 %
    >=4 Wetten, 5 Min, ab $10k                     16,5              ROI +10,2 %   UG  +3,4 %
    >=4 Wetten, 5 Min, ab $10k, GLEICHE Quote       5,8 (live)       ROI +29,0 %   UG +19,1 %
                                                    6,8 (vor)        ROI  +8,8 %   UG  +1,1 %

Der wirksame Hebel ist NICHT der Betrag — im Gegenteil: ab $50k faellt der ROI auf −12,3 %, die
Kante sitzt im Band $10-20k. Der Hebel ist die GLEICHE QUOTE: der Buchmacher hat auf das Geld
nicht reagiert. Genau das zeigt auch Lucas' Beispiel (viermal 3,35).

Lucas: „Schwellen muessen wir sicher nachstellen, weil hab Angst dass da zu viel kommt." Deshalb
zusaetzlich ein harter Deckel je Lauf. Der Deckel ist eine LAERMGRENZE, keine Rangfolge — er
nimmt die aeltesten zuerst, weil jede Sortierung nach Guete eine Behauptung waere, die wir nicht
belegen koennen. Wie viele er unterdrueckt hat, steht in der Nachricht und im Buch.

⚠️ Beide Phasen laufen mit und werden GETRENNT gestempelt. Live ist die staerkere Messung, aber
Lucas' eigenes Beispiel war vor Anpfiff — eine der beiden vorab wegzuwerfen hiesse, die Frage
schon beantwortet zu haben.

Env:
  STAKE_BURST_MIN_N      Wetten je Burst (Default 4)
  STAKE_BURST_FENSTER_S  Zeitfenster in Sekunden (Default 300)
  STAKE_BURST_MIN_USD    Mindestsumme des Bursts (Default 10000)
  STAKE_BURST_MAX        max Pushes je Lauf (Default 4)
  TELEGRAM_TOKEN + TELEGRAM_TRADES_CHAT_ID — ohne Token = Vorschau (stdout)
"""
from __future__ import annotations
import html
import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from telegram_trades import send_trades_message

BASE = Path(os.environ.get("STAKE_DATEN") or Path(__file__).resolve().parent)  # money-radar: Daten in .st/
LEDGER_FILE = BASE / "stake_burst_ledger.json"
WETTEN_FILE = BASE / "stake_bet_ledger.json"   # 13.09.2026: die Quelle der Abrechnung
VERWORFEN_FILE = BASE / "stake_burst_verworfen.json"   # 13.09.2026: das Beinahe-Buch
VERWORFEN_KEEP = 1500
SEEN_FILE = BASE / "stake_burst_seen.json"
QUELLE_FILE = BASE / "stake_highroller.json"

MIN_N = int(os.environ.get("STAKE_BURST_MIN_N") or 4)
FENSTER_S = float(os.environ.get("STAKE_BURST_FENSTER_S") or 300)
MIN_USD = float(os.environ.get("STAKE_BURST_MIN_USD") or 10000)
MAX_PUSH = int(os.environ.get("STAKE_BURST_MAX") or 4)
# 13.09.2026 (Lucas: „aber jetzt rennen die Bursts normal als Push weiter und ich krieg die
# weiter rein"). Der Schalter trennt MESSEN von SENDEN. Vorher waren beide dasselbe: eine
# Buchzeile entstand nur, wenn `send_trades_message` erfolgreich war — den Kanal stillzulegen
# haette also auch die Messung stillgelegt, und die zwei Wochen Beobachtung waeren weg gewesen.
#
# Dieselbe Trennung wie im Pick-Schattenbuch: das Buch schreibt JEDEN erkannten Burst mit, der
# Push ist eine davon unabhaengige Entscheidung. Was nicht rausging, traegt `push: false` und
# den Grund — die Kanal-Bilanz in den Stats zaehlt weiterhin nur, was den Kanal verlassen hat.
PUSH_AN = (os.environ.get("STAKE_BURST_PUSH") or "true").strip().lower() \
    not in ("false", "0", "nein", "off", "aus")

# 🎯 Mindestquote — 12.09.2026 (Lucas: „wieso kommt da so eine odd?", zu @1,01 und @1,15).
#
# Der Push hatte KEINEN Quotenboden. Beim Poly-Band habe ich einen gebaut (1,35, aus Lucas'
# eigener Ansage) und hier nie einen gesetzt — dieselbe Luecke, dieselbe Woche.
#
# Gemessen an den 72 Bursts der letzten sechs Tage: **19 liegen unter Quote 1,10** (davon sechs
# bei 1,01). Das ist kein Signal, das ist jemand, der auf ein entschiedenes Spiel 1 % abgreift.
# Der Boden macht das Band auf BEIDEN Achsen besser — er nimmt Laerm UND hebt die Kante:
#
#     ohne Boden   72 Bursts   n=365   86,0 % Treffer   ROI +29,1 %   UG +22,3 %
#     ab 1,20      43          n=247   79,4 %           ROI +40,9 %   UG +31,0 %
#     ab 1,35      36          n=212   77,8 %           ROI +45,9 %   UG +34,3 %
#
# 1,35 ist im Projekt ohnehin der Boden (pick-engine.js, stake-radar.js, Poly-Dominanz) — eine
# vierte Zahl waere hier nur eine weitere, die man im Kopf behalten muss.
MIN_QUOTE = float(os.environ.get("STAKE_BURST_MIN_QUOTE") or 1.35)

# 🔢 Wie viele Tickets macht einen Burst? — 16.09.2026 (Lucas: „das sollte nicht beschraenkt
# sein auf sieben oder vier Wetten … vielleicht reichen da drei schnelle Einsaetze").
#
# Gemessen am Ledger 10.–16.09. (16.104 abgerechnete Einzelwetten, 5,37 Tage), EIN Burst je
# Auswahl — also genau die Einheit, die auch gepusht wird — >=$10k, Quote>=1.35, Rendite aus
# der Abrechnung der Wetten selbst, Untergrenze als 5-%-Bootstrap ueber die Bursts:
#
#     Quotenregel      n>=5              n>=4              n>=3              n>=2
#     strikt gleich    +65,8 % (UG +26,3 %)  +28,4 % (−1,0 %)  +13,6 % (−8,5 %)  +5,9 % (−6,6 %)
#     groesste Gruppe  +36,2 % (UG  +5,1 %)  +19,4 % (−3,8 %)   +9,0 % (−9,0 %)  +5,1 % (−6,5 %)
#     Quote egal       +27,0 % (UG  +5,4 %)  +14,1 % (−2,6 %)   +8,6 % (−5,8 %)  +0,6 % (−9,3 %)
#     Bursts/Tag       3,4 / 6,0 / 10,4      6,1 / 9,3 / 15,5   11,0 / 15,3 / 24,8  27,6 / 33,0 / 51,2
#
# Die Treppe ist in ALLEN DREI Quotenregeln streng monoton: je mehr Tickets, desto staerker.
# Runter auf 3 kauft das Doppelte an Menge und verliert den Beleg (Untergrenze faellt von
# −1,0 % auf −8,5 %). Bewiesen ist derzeit nur n>=5. Zwoelf Zellen, ein Muster — die einzelne
# Zelle ist Rauschen, die Monotonie ist es nicht.
#
# ⚠️ Die Rendite ist die der WALE, nicht unsere: wir steigen Minuten spaeter ein, moeglicherweise
# zu schlechteren Quoten. Fuer Stake gibt es kein CLV, also ist das die beste verfuegbare
# Naeherung — und sie taugt fuer den VERGLEICH der Schwellen, nicht als Renditeversprechen.
#
# MIN_N bleibt deshalb bei 4 (und nicht bei 3), aber der Preis dieser Schwelle wird ab jetzt
# MITGESCHRIEBEN statt geschaetzt: Cluster, die NUR an der Anzahl scheitern, gehen mit dem
# Grund `unter_min_n` ins Beinahe-Buch. Nach zwei Wochen beantwortet unser eigenes Buch die
# Frage — nicht ein Skript, das ich einmal laufen lasse.
BEOBACHTEN_AB_N = int(os.environ.get("STAKE_BURST_BEOBACHTEN_AB_N") or 3)

# ⏱️ Frische — 12.09.2026 (Lucas: „Wertlos war gestern schon. Wieso kommt das jetzt?").
#
# Der gemeldete Burst lag auf Venezia-Fiorentina, 11.09. um 20:29 — gepusht am 12.09. Der Grund:
# `stake_highroller.json` haelt ein 48-Stunden-Fenster, und die Erkennung hatte KEINE
# Altersgrenze. Sie fand Bursts irgendwo im Fenster, auch zwoelf Stunden alte.
#
# Genau dieselbe Fehlerklasse wie beim Betfair-Halbzeit-Push einen Tag vorher („die Tore alle
# schon ewig her"): die Regel prueft den Zustand, aber nicht, WANN er galt.
#
# 🔴 17.09.2026 (Lucas: „der Betfair-Cron sollte alle 10 min, tut er aber nicht — in Wahrheit
# rennt er alle 15 min"). Hier stand „der Runner laeuft alle 10 Minuten, 30 Minuten sind also
# reichlich Puffer". Gemessen an `health/betfair.json`: 17 von 19 Abstaenden exakt 15,0 Minuten.
# Der Puffer ist also nicht das Dreifache eines Laufs, sondern das Doppelte — und weil der
# Stake-Feed nur 50 Eintraege (~21 Min) im Gedaechtnis hat, kann ein Burst beim ersten Sehen
# schon 15 Minuten alt sein. 30 bleibt richtig, aber knapp: der naechste Lauf ist die letzte
# Chance, nicht die zweitletzte. Wer hier schraubt, muss den echten Takt kennen, nicht den
# im Cron behaupteten — deshalb steht die Messung jetzt dabei.
MAX_ALTER_MIN = float(os.environ.get("STAKE_BURST_MAX_ALTER_MIN") or 30)
LEDGER_KEEP = 800
SEEN_KEEP_H = 48.0


def _load(p, default):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def _save(p, data) -> None:
    Path(p).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _ts(x):
    try:
        return datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _usd(x) -> str:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return "?"
    if x >= 1_000_000:
        return "$%.1fM" % (x / 1_000_000)
    if x >= 1000:
        return "$%.1fK" % (x / 1000)
    return "$%d" % round(x)


def _quote(w):
    q = w.get("beinQuote")
    if not isinstance(q, (int, float)):
        q = w.get("quote")
    return q if isinstance(q, (int, float)) and q > 1 else None


# Der Rueckfall kommt aus dem SAMMLER, nicht aus einer zweiten Liste hier. Sonst driftet er
# beim naechsten Umbau der Sperre auseinander — und genau das ist am 12.09. passiert, als
# „Cricket" dort dazukam und hier ein hartkodiertes ("US-Sport",) stehen blieb. Defensiv
# gekapselt: faellt der Import aus, bleibt der Push lauffaehig statt zu sterben.
try:
    from stake_highroller_fetch import GESPERRT as _SAMMLER_GESPERRT
    GESPERRT_FALLBACK = tuple(sorted(_SAMMLER_GESPERRT))
except Exception:                                            # pragma: no cover
    GESPERRT_FALLBACK = ("Cricket", "Kampfsport", "US-Sport")


def gesperrte_kats(quelle=None):
    """Die ausgeblendeten Sportarten — aus `stake_highroller.json`, nicht hier hartkodiert. REIN.

    12.09.2026 (Lucas: „ok das waeren dann nur bursts zu Top Ligen oder"). Beim Nachzaehlen fiel
    auf, dass der Burst-Push als EINZIGE Stake-Flaeche keine Sperrliste las. Der Sammler fuehrt
    sie (`stake_highroller_fetch.GESPERRT`, seit 03.09.: „Ganze US-Sport brauch ich aktuell mal
    nicht") und schreibt sie als `gesperrt` ins Artefakt; der Radar liest sie von dort. Also
    liest dieser Push sie auch von dort — eine zweite Liste waere genau die Drift, die im
    Poly-Band einen Tag vorher aufgeraeumt wurde.

    Gemessen betrifft es 1 von 72 Bursts. Der Aufwand lohnt trotzdem: die Konsistenz ist der
    Punkt, nicht die eine Karte.
    """
    got = (quelle or {}).get("gesperrt") if isinstance(quelle, dict) else None
    kats = [str(c) for c in got if c] if isinstance(got, list) else []
    return kats or list(GESPERRT_FALLBACK)


# 📉 Quote faellt mit dem Geld — 26.09.2026 (Lucas schickt einen Fremd-Radar-Post: „#Slovakia
# #2 #Liga · FC Vion Zlate Moravce – Vrable – FC Petrzalka · 9 bets / 8 bets in 3 min · Draw No
# Bet – FC Petrzalka" — „Wieso finden wir sowas nicht").
#
# Wir hatten jede einzelne Wette: 11 Tickets, $20.015, 190 Sekunden, alle auf dieselbe Auswahl.
# Das Beinahe-Buch fuehrte den Fall sogar — mit dem Grund `quoten_uneinheitlich`: 1,85 → 1,75 →
# 1,70. Die Regel verlangte die GLEICHE Quote („der Buchmacher hat auf das Geld nicht reagiert").
# Hier HAT er reagiert: die Quote wurde mit jedem Ticket kuerzer. Das ist dasselbe Muster, das
# auf Betfair Steam heisst — das Geld bewegt den Preis —, und die Regel warf es als Rauschen weg.
#
# Gemessen am Ledger (20.–26.09., 6,6 Tage, Bursts >=4 Tickets / 5 Min / ab $10k / Quote >=1,35,
# Rendite aus der Abrechnung der Wetten selbst, Band = 5–95 %-Bootstrap ueber die Bursts):
#
#     gleiche Quote (die bisherige Regel)   n= 9   ROI −48,9 %   [−83,6, −12,9]
#     Quote FAELLT mit jedem Ticket          n=21   ROI +30,2 %   [ −1,4, +59,6]
#     Quote steigt                           n=12   ROI −18,6 %
#     gemischt                               n=26   ROI −13,0 %
#
# ⚠️ Das ist KEIN Beleg: 21 Bursts, die Untergrenze knapp unter null, eine Woche Basis, und die
# Aufteilung ist nach dem Blick auf Lucas' Fall entstanden. Genau deshalb wird die Art gestempelt
# (`quotenArt`) und getrennt abgerechnet — und die „gleiche Quote" verliert nichts: sie laeuft
# weiter, ihre eigene Zahl steht ab heute auf der Karte statt einer getippten.
def quoten_art(qs) -> str:
    """„gleich" | „fallend" | „steigend" | „gemischt" — der Quotenverlauf eines Bursts. REIN.
    `qs` in zeitlicher Reihenfolge. „fallend" heisst: nie steigend und am Ende kuerzer als am
    Anfang — jedes Ticket bekam dieselbe oder eine schlechtere Quote als das davor."""
    qs = [round(float(q), 2) for q in (qs or [])]
    if not qs or len(set(qs)) == 1:
        return "gleich"
    if all(qs[k + 1] <= qs[k] for k in range(len(qs) - 1)):
        return "fallend"
    if all(qs[k + 1] >= qs[k] for k in range(len(qs) - 1)):
        return "steigend"
    return "gemischt"


def buch_bilanz(ledger, quoten_art_=None) -> dict:
    """Was die gesendeten Auswahl-Bursts dieser Quoten-Art bisher gebracht haben. REIN.
    Alte Zeilen ohne `quotenArt` sind per Konstruktion „gleich" (nur die gab es)."""
    rr = [z["rendite"] for z in (ledger or [])
          if isinstance(z, dict) and (z.get("art") or "auswahl") == "auswahl"
          and (z.get("quotenArt") or "gleich") == (quoten_art_ or "gleich")
          and isinstance(z.get("rendite"), (int, float))]
    if not rr:
        return {"n": 0}
    n = len(rr)
    m = sum(rr) / n
    ug = None
    if n >= 10:
        sd = (sum((x - m) ** 2 for x in rr) / (n - 1)) ** 0.5
        ug = max(-1.0, m - 1.645 * sd / n ** 0.5)
    return {"n": n, "roi": m, "ug": ug}


def _bilanz_satz(bil, name) -> str:
    """Ein Satz aus dem eigenen Buch — oder das ehrliche „noch nichts"."""
    if not bil or not bil.get("n"):
        return ("Beobachtungsband „%s“ — noch kein gesendeter Burst dieser Art abgerechnet, "
                "also kein Beleg." % name)
    t = "Eigenes Buch „%s“: %d abgerechnet · ROI %+.1f %%" % (name, bil["n"], 100 * bil["roi"])
    if bil.get("ug") is not None:
        t += " (Untergrenze %+.1f %%)" % (100 * bil["ug"])
        t += " — trägt" if bil["ug"] > 0 else " — kein Beleg"
    else:
        t += " — unter 10 Bursts kein Urteil"
    return t


def bursts(wetten, min_n=None, fenster_s=None, min_usd=None, gesperrt=None,
           min_quote=None, max_alter_min=None, now=None, verworfen=None,
           beobachten_ab_n=None) -> list:
    """Alle Einsatz-Bursts im Feed. REIN (alles injizierbar).

    Ein Burst ist: `min_n` Einzelwetten auf DIESELBE Auswahl, innerhalb von `fenster_s`, zusammen
    mindestens `min_usd`, ALLE zur SELBEN Quote.

    Kombiwetten bleiben draussen — ihr Einsatz haengt an mehreren Spielen und ist keinem davon
    zurechenbar (dieselbe Regel wie im Stake-Radar seit 03.09.).

    Je Auswahl wird HOECHSTENS EIN Burst gemeldet (der erste). Sonst wuerde eine lange Serie von
    Wetten dieselbe Auswahl mehrfach ausloesen, und der Kanal saehe ein Ereignis als fuenf.
    """
    min_n = MIN_N if min_n is None else min_n
    fenster_s = FENSTER_S if fenster_s is None else fenster_s
    min_usd = MIN_USD if min_usd is None else min_usd
    gesperrt = list(GESPERRT_FALLBACK) if gesperrt is None else list(gesperrt)
    min_quote = MIN_QUOTE if min_quote is None else min_quote
    max_alter_min = MAX_ALTER_MIN if max_alter_min is None else max_alter_min
    beobachten_ab_n = BEOBACHTEN_AB_N if beobachten_ab_n is None else beobachten_ab_n
    now = now or datetime.now(timezone.utc)
    je_auswahl = {}
    je_gesperrt = {}
    for w in wetten or []:
        if not isinstance(w, dict) or w.get("kombi"):
            continue
        if w.get("kat") in gesperrt:
            # 13.09.2026: auch die gesperrten Sportarten laufen ins Beinahe-Buch — getrennt
            # gesammelt, damit spaeter beantwortbar ist, wie viel die Sperre kostet. Eine
            # Sperre, deren Preis niemand kennt, ist eine Vermutung mit Bestandsschutz.
            _a, _t = w.get("auswahlId"), _ts(w.get("ts"))
            if verworfen is not None and _a and _t is not None:
                je_gesperrt.setdefault(_a, []).append((_t, w))
            continue
        a, t, u, q = w.get("auswahlId"), _ts(w.get("ts")), w.get("einsatzUsd"), _quote(w)
        if not a or t is None or q is None:
            continue
        if not isinstance(u, (int, float)) or isinstance(u, bool) or u <= 0:
            continue
        je_auswahl.setdefault(a, []).append((t, w))
    aus = []
    for a, v in je_auswahl.items():
        v.sort(key=lambda z: z[0])
        # 🔴 16.09.2026 (Lucas: „wundert mich, dass von Tag auf Tag nicht mehrere Einsaetze
        # hintereinander sind … ich glaube, dass da irgendwo hakt"). Er hatte recht, und der
        # Fehler stand genau hier: BEI JEDEM `break` unten wurde die ganze AUSWAHL verlassen,
        # nicht nur das eine Fenster. Fiel also das erste Fenster einer Auswahl durch eine Regel
        # (uneinheitliche Quoten, Quote zu tief, Summe zu klein) oder war es schlicht zu alt,
        # dann wurden alle SPAETEREN Fenster derselben Auswahl nie mehr angesehen — obwohl ein
        # spaeteres Fenster juenger ist und andere Quoten tragen kann.
        #
        # Gemessen am Ledger vom 10.–16.09. (20.000 Wetten, 5,37 Tage), Schwellen unveraendert:
        #     heutiger Code (break):     22 Bursts   ·   Replay alle 10 Min, Alter 30: 25
        #     ohne den Fehl-break:       33 Bursts   ·   Replay: 35
        # Also rund jeder DRITTE gueltige Burst ging verloren — nicht an den Schwellen, sondern
        # an der Schleife. „Hoechstens ein Burst je Auswahl" war gewollt; „hoechstens ein
        # VERSUCH je Auswahl" war es nie.
        #
        # Das Beinahe-Buch bekommt weiterhin genau EINE Zeile je Auswahl (die des ersten
        # gescheiterten Fensters) — und nur dann, wenn die Auswahl am Ende gar keinen Burst
        # ergeben hat. Sonst stuende dieselbe Auswahl als Treffer UND als Beinahe im Buch.
        beinahe = None      # erstes Fenster ab `min_n`, das an einer Regel scheitert
        klein = None        # erstes Fenster UNTER `min_n`, das sonst sauber waere
        for i in range(len(v)):
            j = i
            while j + 1 < len(v) and (v[j + 1][0] - v[i][0]).total_seconds() <= fenster_s:
                j += 1
            if j - i + 1 < min_n:
                # Cluster unter der Anzahl-Schwelle: KEIN Push, aber eine Zeile im Beinahe-Buch,
                # wenn es sonst an nichts scheitert. Das ist der einzige Filter, dessen Preis
                # bisher nirgends stand — die Schwellen-Treppe oben musste ich dafuer aus dem
                # rollierenden Ledger rechnen, das nur fuenf Tage weit zurueckreicht.
                _n = j - i + 1
                if (klein is None and verworfen is not None and _n >= beobachten_ab_n
                        and (now - v[j][0]).total_seconds() / 60.0 <= max_alter_min):
                    _g = [z[1] for z in v[i:j + 1]]
                    _su = sum(float(x["einsatzUsd"]) for x in _g)
                    if (len({round(float(_quote(x)), 2) for x in _g}) == 1
                            and float(_quote(_g[0])) >= min_quote and _su >= min_usd):
                        klein = _beinahe(a, _g, _su, ["unter_min_n"], v[i][0], v[j][0])
                continue
            g = [z[1] for z in v[i:j + 1]]
            summe = sum(float(x["einsatzUsd"]) for x in g)
            # ⭐ ALLE Gruende sammeln statt beim ersten auszusteigen. Der Unterschied ist der
            # ganze Zweck des Beinahe-Buchs: „scheitert an GENAU EINER Regel" ist die Frage,
            # die man spaeter stellt, und die kann man nicht beantworten, wenn nur der erste
            # Grund notiert ist.
            gruende = []
            qs = [round(float(_quote(x)), 2) for x in g]
            art = quoten_art(qs)
            if art not in ("gleich", "fallend"):
                gruende.append("quoten_uneinheitlich")
            # Bei fallender Quote zaehlt die LETZTE (niedrigste) gegen den Boden — sonst kaeme ein
            # Burst durch, dessen Quote beim Einstieg schon unter 1,35 liegt.
            if min(qs) < min_quote:
                gruende.append("quote_zu_tief")       # @1,01 ist kein Signal — s. MIN_QUOTE
            if summe < min_usd:
                gruende.append("summe_zu_klein")
            if gruende:
                # Zu alt ist KEIN Grund fuers Beinahe-Buch: das Cluster war fachlich in Ordnung,
                # wir haben es nur zu spaet gesehen. Es hier mitzuzaehlen wuerde die Frage
                # „welche Regel kostet uns kleine Ligen" mit Laufzeit-Rauschen zumuellen.
                if (beinahe is None and verworfen is not None
                        and (now - v[j][0]).total_seconds() / 60.0 <= max_alter_min):
                    beinahe = _beinahe(a, g, summe, gruende, v[i][0], v[j][0])
                continue
            if (now - v[j][0]).total_seconds() / 60.0 > max_alter_min:
                continue                  # zu alt zum Melden — s. MAX_ALTER_MIN
            aus.append({"auswahlId": a, "wetten": g, "summe": summe,
                        "sekunden": (v[j][0] - v[i][0]).total_seconds(),
                        "von": v[i][0], "bis": v[j][0], "quotenArt": art})
            beinahe = klein = None        # Treffer schlaegt Beinahe — nie beides je Auswahl
            break
        # Ein echter Beinahe-Treffer (genug Tickets, an einer Regel gescheitert) sagt mehr als
        # ein zu kleines Cluster — er bekommt den einen Platz je Auswahl.
        if verworfen is not None and (beinahe or klein) is not None:
            verworfen.append(beinahe if beinahe is not None else klein)
    if verworfen is not None:
        for a, v in je_gesperrt.items():
            v.sort(key=lambda z: z[0])
            for i in range(len(v)):
                j = i
                while j + 1 < len(v) and (v[j + 1][0] - v[i][0]).total_seconds() <= fenster_s:
                    j += 1
                if j - i + 1 < min_n:
                    continue
                g = [z[1] for z in v[i:j + 1]]
                if (now - v[j][0]).total_seconds() / 60.0 <= max_alter_min:
                    verworfen.append(_beinahe(a, g, sum(float(x["einsatzUsd"]) for x in g),
                                              ["sportart_gesperrt"], v[i][0], v[j][0]))
                break
    aus.sort(key=lambda b: b["von"])      # aelteste zuerst — der Deckel ist keine Rangfolge
    return aus


# ═══ Die zweite Gruppierung: je SPIEL statt je Auswahl ═══════════════════════════════════════
# 🔴 22.09.2026 (Lucas schickt einen Fremd-Radar-Post: „⚽️ #Uzbekistan #Pro_League, PFC Terdu –
# FK Gazalkent, Volume $15.734, Total 5 bets / 5 bets in 7 min" — „genau sowas ist halt das Geile,
# wenn man sowas findet, und wir HAETTEN es gefunden. Also macht das bitte so, dass man es findet").
#
# Wir hatten die Wetten. Sechs Fussballwetten auf genau dieses Spiel standen im Ledger, darunter
# die aus dem fremden Post ($2.000 x 1,75 auf FK Gazalkent -1,5). Gefunden hat sie niemand, weil
# `bursts()` je AUSWAHL gruppiert und dieselbe Quote verlangt — der Fremd-Radar gruppiert je SPIEL.
# Die groesste gleichgerichtete Gruppe war n=3 / $7.262 auf zwei Quoten (1,80 und 1,85). Kein
# Fehler in der bestehenden Regel: eine andere Frage, die niemand gestellt hat.
#
# ── Was den Fall interessant macht, und was nicht ────────────────────────────────────────────
# „Mehrere Wetten auf ein Spiel" allein ist kein Signal — gemessen am Ledger (16.553
# Einzelwetten, 5,80 Tage) waeren das 28 bis 55 Meldungen pro Tag, die Haelfte davon Premier
# League und Serie A. Drei Einschraenkungen, jede einzeln gemessen:
#
#   Zuschnitt (je Spiel, US-Sport/Kampfsport gesperrt)          Bursts   pro Tag
#   >=4 Wetten / 5 Min / ab $10k                                   162      27,9
#   dazu: KEINE GEGENSEITE (alle gerichteten Wetten ein Team)      144      24,8
#   dazu: alle Quoten >= 1,35                                       —         —
#   dazu: Summe >= 2x der Liga-Norm (Median x Anzahl)               —         —
#   alles zusammen, Fenster 30 Min                                  58      10,0
#
# · KEINE GEGENSEITE ist die tragende Regel. Sie unterscheidet „viel Geld auf dieses Spiel"
#   (zwei Parteien, die sich uneinig sind — das ist ein Markt, kein Signal) von „viel Geld auf
#   DIESE Mannschaft ueber mehrere Maerkte". Genau das war Lucas' Fall: drei Wetten auf
#   FK Gazalkent (1x2, 1. Halbzeit, Handicap), drei neutrale auf Over — und keine einzige auf
#   Terdu. Neutrale Wetten (Over/Under, Torzahl) zaehlen zur Summe, aber nicht als Gegenseite.
# · Das FENSTER ist 30 Minuten statt 5: die Auswahl-Regel misst einen Schwall auf EINE Auswahl,
#   hier verteilt sich dasselbe Geld ueber mehrere Maerkte und braucht laenger. Lucas' Fall
#   spannt 20 Minuten.
# · Der QUOTENBODEN (1,35) und der NORM-FAKTOR (2x) sind dieselben Zahlen, die im Haus ohnehin
#   gelten — keine zwei neuen, die man sich merken muss.
#
# ⚠️ Was dieser Zuschnitt NICHT kann: die grossen Ligen raushalten. Von den 58 sind 25 Ebene 1
# (Premier League, Serie A, Bundesliga), wo eine Burst-Summe nach Lucas' eigenem Massstab „0
# sagt". Der Norm-Faktor faengt das nicht, weil grosse Ligen auch eine grosse Norm haben, und
# die Spielklasse taugt hier nicht als Filter — Lucas' eigene Liga steht seit heute als
# „mehrdeutig" in `stake_liga_stufe` (der Slug `pro-league` traegt zwei Klassen). Deshalb steht
# die Spielklasse auf der KARTE und filtert nicht. Erst messen, dann behaupten.
#
# ⚠️ Und es gibt noch keine Rendite-Messung fuer diesen Zuschnitt. Der Auswahl-Burst hat eine
# (+45,9 %, UG +34,3 %, n=212); die gilt hier NICHT — es ist eine andere Einheit. Das Buch
# stempelt deshalb `art: "spiel"`, damit die zwei nie in einer Zahl zusammenfallen.
# ── Die Schwellen, und wie sie zustande kommen ──────────────────────────────────────────────
# Gemessen am Ledger (16.553 Einzelwetten, 5,80 Tage), je Zeile die Lautstaerke und ob Lucas'
# eigener Fall (PFC Terdu – FK Gazalkent, 5 Wetten, $15.734, 1,74x) noch durchkommt:
#
#     2,00x / ab $10k / >=4 Wetten / >=1 gerichtet     16,4 / Tag     Lucas faellt durch
#     1,50x / ab $10k / >=4 / >=1                      21,9           ✓
#     1,50x / ab $15k / >=4 / >=2                      16,2           ✓
#     1,50x / ab $12k / >=4 / >=2                      16,9           ✓  ← gewaehlt
#     1,50x / ab $12k / >=4 / >=3                      13,1           faellt durch
#     2,00x / ab $15k / >=4 / >=2                      12,4           faellt durch
#
# ⚠️ EHRLICH GESAGT: diese Schwellen sind NICHT belegt. Sie sind so gesetzt, dass der Fall, den
# Lucas gezeigt hat, mit etwas Puffer durchkommt („genau sowas ist halt das Geile, wenn man sowas
# findet … macht das bitte so, dass man es findet") und die Lautstaerke unter 20 am Tag bleibt.
# Das ist eine Auswahl nach EINEM Beispiel — genau die Klasse, vor der die Schwellen-Treppe des
# Auswahl-Bursts warnt. Deshalb: eigener Buch-Stempel (`art: "spiel"`), eigener Deckel, und in
# zwei Wochen dieselbe Schwellen-Treppe wie am 16.09. fuer die Auswahl-Bursts. Bis dahin ist das
# ein Beobachtungsband, kein Signal.
#
# MIN_GERICHTET ist die Regel, die am meisten traegt: mindestens zwei Wetten muessen eine
# MANNSCHAFT benennen. Vier neutrale Over/Under-Tickets sind ein Torlinien-Handel, keine
# Richtungswette — und ohne diese Zahl waere jeder Ueber/Unter-Schwall eine Meldung.
SPIEL_MIN_N = int(os.environ.get("STAKE_SPIEL_MIN_N") or 4)
SPIEL_MIN_GERICHTET = int(os.environ.get("STAKE_SPIEL_MIN_GERICHTET") or 2)
SPIEL_FENSTER_S = float(os.environ.get("STAKE_SPIEL_FENSTER_S") or 1800)
SPIEL_MIN_USD = float(os.environ.get("STAKE_SPIEL_MIN_USD") or 12000)
SPIEL_MIN_FAKTOR = float(os.environ.get("STAKE_SPIEL_MIN_FAKTOR") or 1.5)
SPIEL_MAX_PUSH = int(os.environ.get("STAKE_SPIEL_MAX") or 2)
SPIEL_AN = (os.environ.get("STAKE_SPIEL_PUSH") or "true").strip().lower() \
    not in ("false", "0", "nein", "off", "aus")
NORM_FILE = BASE / "stake_league_norm.json"


def _teams(event) -> list:
    """Die beiden Mannschaften aus dem Event-Namen. Leer, wenn das Format nicht passt —
    dann gibt es keine Seite und der Burst faellt durch, statt eine zu erfinden."""
    e = str(event or "")
    return [t.strip() for t in e.split(" - ", 1)] if " - " in e else []


# Wortteile, die keine Mannschaft bezeichnen, sondern ihre Rechtsform oder ihren Kader.
# Der Feed setzt sie im Spielnamen und in der Auswahl unterschiedlich — genau daran ist der
# Fall vom 24.09.2026 gescheitert.
# 🔴 Zweiter Versuch. Im ersten standen hier auch „atletico", „deportivo", „sporting" und
# „club" — Woerter, die zur Rechtsform GEHOEREN koennen, aber genauso oft der Name selbst
# sind. „Atletico Madrid" gegen „Real Madrid" verlor dadurch seine Seite: nach Abzug von
# „atletico" blieb „madrid", und das haben beide.
# Fehlerklasse: *eine Rauschliste, die auch das Signal enthaelt.* Drin bleibt nur, was NIE
# eine Mannschaft bezeichnet — Rechtsform-Kuerzel und Kader-Zusatz. Alles andere trennt
# `_unterscheidend` ohnehin heraus, sobald beide Namen es tragen.
_TEAM_RAUSCHEN = {
    "fc", "cf", "ca", "ac", "sc", "cd", "afc", "afbc", "cfc", "sv", "tsv", "fk", "sk",
    "vv", "vfb", "vfl", "bsc", "rc", "as", "ss", "us", "ud", "sd", "cs", "cp",
    "reserve", "reserves", "reserva", "reservas",
    "u19", "u20", "u21", "u23", "youth", "juniors", "junior",
    "women", "womens", "ladies", "femenino", "femenina", "frauen",
}


def _flach(s) -> str:
    """Kleinbuchstaben ohne Akzente. REIN.

    🔴 24.09.2026, beim Nachmessen des eigenen Fixes gefunden: der Feed schreibt „Atletico
    Madrid" im Spielnamen und „Atlético Madrid" in der Auswahl. 22 Zeilen scheiterten allein
    daran — neunmal Atletico gegen Real Madrid, dazu Vélez, Tucumán, Juárez, Goztepe.
    Dieselbe Klasse wie die Schreibweise selbst, nur eine Ebene tiefer: zwei Schreibungen
    desselben Namens, und ein Vergleich, der auf Zeichengleichheit besteht. Und derselbe
    Fehler wie gestern in `odds_anker_luecke`, wo „Primera División" am ó zu „divisi" zerfiel
    — zwei Dateien, dieselbe Woche."""
    x = unicodedata.normalize("NFKD", str(s or "").lower())
    return "".join(c for c in x if not unicodedata.combining(c))


def _team_kern(name: str) -> set:
    """Die Woerter einer Mannschaft ohne Rechtsform und Kader-Zusatz. REIN."""
    toks = [t for t in re.split(r"[^a-z0-9]+", _flach(name)) if t]
    return {t for t in toks if t not in _TEAM_RAUSCHEN} or set(toks)


def _unterscheidend(a: str, b: str) -> tuple:
    """Die Woerter, die A von B trennen — und umgekehrt. REIN.

    🔴 Erster Versuch war „Kern = Woerter ab vier Zeichen". Der hat Lucas' Fall geloest und
    dabei 167 E-Sport-Zeilen die Seite genommen: „LGD Gaming" gegen „Xtreme Gaming" — `lgd`
    ist drei Zeichen lang, fiel weg, uebrig blieb `gaming`, und das passt auf beide.
    Fehlerklasse: *ein Filter, der das Unterscheidende wegwirft, weil es kurz ist.*

    Was eine Mannschaft von der anderen trennt, kann man nicht an der Wortlaenge ablesen —
    nur am Vergleich mit dem Gegner. Genau der steht hier daneben.
    """
    ka, kb = _team_kern(a), _team_kern(b)
    return ka - kb, kb - ka


def _trifft(worte: set, tokens: set) -> bool:
    """Kommt eines der unterscheidenden Woerter in der Auswahl vor? REIN.

    Auch als Anfang eines laengeren Wortes, ab vier Zeichen: der Feed schreibt „Roma - Inter"
    im Spielnamen und „Internazionale" in der Auswahl. Kuerzere Anfaenge bleiben draussen,
    sonst passt „real" auf „realmadrid" genauso wie auf „realidad".
    """
    for wort in worte or ():
        if wort in tokens:
            return True
        if len(wort) >= 4 and any(t.startswith(wort) for t in tokens):
            return True
    return False


def seite(w) -> str | None:
    """Auf welche MANNSCHAFT zeigt diese Auswahl? None heisst neutral (Over/Under, Torzahl) —
    nicht „unbekannt" und erst recht nicht „Gegenseite".

    Bewusst nur bei EINDEUTIGEM Treffer: bei „Deportivo La Coruna - Deportivo Alaves" passt
    „Deportivo" auf beide, und eine geratene Seite waere schlimmer als keine.

    🔴 24.09.2026 (Lucas: „Das finden wir nicht im stake burst?" — Deportivo Riestra Reserve
    gegen Barracas Central Reserve, 14 Wetten, 39.347 $ auf ein Spiel in 57 Minuten). Wir
    hatten jede einzelne Zeile im Ledger. Der Burst fiel trotzdem durch, und zwar an genau
    einer Stelle:

        event   „Deportivo Riestra Afbc Reserve - CA Barracas Central Reserve"
        auswahl „Deportivo Riestra Reserves (-1.5)"

    Hier stand `t.lower() in a` — der volle Mannschaftsname musste woertlich in der Auswahl
    vorkommen. „Afbc Reserve" gegen „Reserves": dieselbe Mannschaft, zwei Schreibweisen, kein
    Treffer. Damit war `gerichtet` fuer ALLE 14 Zeilen null, und jedes Fenster fiel an
    `len(seiten) != 1` durch — obwohl eines davon 28.360 $ bei Faktor 1,82 getragen haette.

    Fehlerklasse: *eine Zuordnung, die auf Zeichengleichheit besteht, wo die Quelle zwei
    Schreibweisen fuehrt.* Dieselbe, an der am 01.09. der MLS-Anker haengen blieb („US MLS"
    gegen „Major League Soccer", 105 Zeilen ohne Anker).

    Verglichen wird deshalb ueber den KERN des Namens. Die Vorsicht bleibt: der Kern der
    anderen Mannschaft darf nicht ebenfalls passen, sonst gibt es weiter keine Seite.
    """
    a = _flach((w or {}).get("auswahl"))
    if not a:
        return None
    teams = _teams((w or {}).get("event"))
    if len(teams) != 2 or not all(teams):
        return None
    a_tok = {t for t in re.split(r"[^a-z0-9]+", a) if t}
    ka, kb = _team_kern(teams[0]), _team_kern(teams[1])
    da, db = ka - kb, kb - ka
    # 🔴 27.09.2026 (Wachhund `test_keine_zeile_des_ledgers_verliert_ihre_seite`, 29 Zeilen):
    # „Alexander Zverev - Alex de Minaur", Auswahl „Alexander Zverev". Das Wort „alex" von de
    # Minaur passte als ANFANG auf „alexander" — beide Seiten trafen, also keine Seite. Ein
    # Auswahl-Wort, das woertlich zur einen Mannschaft gehoert, ist kein Beleg fuer die andere.
    trifft_a = _trifft(da, a_tok - kb)
    trifft_b = _trifft(db, a_tok - ka)
    # Und der zweite Fall derselben Meldung: „Wydad AC - Wydad Temara", „Nigeria - Nigeria A".
    # Der eine Name steckt GANZ im anderen, die erste Mannschaft hat also kein eigenes Wort —
    # `da` ist leer und konnte nie treffen. Sie ist gemeint, wenn ihr ganzer Kern in der Auswahl
    # steht und nichts, was die andere unterscheidet („Wydad AC or Draw", „Nigeria").
    if not da and not trifft_b and ka and ka <= a_tok:
        trifft_a = True
    if not db and not trifft_a and kb and kb <= a_tok:
        trifft_b = True
    # 🔴 04.10.2026 (Wachhund, „Racing Club Montevideo - Penarol", Auswahl „CA Penarol Montevideo").
    # Beide trafen: „penarol" ist der GANZE Kern der einen Mannschaft, „montevideo" nur der
    # Stadtname im Kern der anderen. Ein vollstaendig genannter Kern schlaegt einen Teiltreffer —
    # aber nur dann; sind beide ganz oder beide halb genannt, wird weiter nicht geraten.
    if trifft_a and trifft_b:
        voll_a, voll_b = bool(da) and da <= a_tok, bool(db) and db <= a_tok
        if voll_a != voll_b:
            return teams[0] if voll_a else teams[1]
    if trifft_a == trifft_b:      # keines von beiden, oder beide — dann wird nicht geraten
        return None
    return teams[0] if trifft_a else teams[1]


def _norm_fuer(norm, w):
    """25.10.2026: Norm nach Turnier-ID („id:<ligaId>"), sonst nach Name — wie stake_league_norm.norm_fuer."""
    norm = norm or {}
    if w.get("ligaId") and ("id:%s" % w["ligaId"]) in norm:
        return norm["id:%s" % w["ligaId"]]
    return norm.get(str(w.get("liga") or ""))


def liga_norm(pfad=None) -> dict:
    """{Liganame: Median-Einsatz}. Fehlt die Datei oder die Liga, gibt es keinen Faktor —
    und ohne Faktor kein Push. Fehlende Information ist hier bewusst eine Sperre und kein
    harmloser Default: „wir kennen die Liga nicht" heisst nicht „der Einsatz ist normal"."""
    d = _load(pfad or NORM_FILE, {}) or {}
    aus = {}
    for name, v in (d.get("ligen") or {}).items():
        m = (v or {}).get("median")
        if isinstance(m, (int, float)) and m > 0:
            aus[str(name)] = float(m)
    return aus


def spiel_bursts(wetten, min_n=None, fenster_s=None, min_usd=None, min_faktor=None,
                 min_quote=None, max_alter_min=None, gesperrt=None, norm=None, now=None,
                 min_gerichtet=None) -> list:
    """Bursts je SPIEL, einseitig. REIN (alles injizierbar).

    Ein Spiel-Burst ist: `min_n` Einzelwetten auf DASSELBE Spiel innerhalb von `fenster_s`,
    zusammen mindestens `min_usd` UND mindestens `min_faktor` mal der Liga-Norm, alle Quoten
    ueber `min_quote`, und alle gerichteten Wetten auf DIESELBE Mannschaft.

    Je Spiel hoechstens einer (der erste) — dieselbe Regel wie bei den Auswahl-Bursts, aus
    demselben Grund: sonst sieht der Kanal ein Ereignis als fuenf.
    """
    min_n = SPIEL_MIN_N if min_n is None else min_n
    min_gerichtet = SPIEL_MIN_GERICHTET if min_gerichtet is None else min_gerichtet
    fenster_s = SPIEL_FENSTER_S if fenster_s is None else fenster_s
    min_usd = SPIEL_MIN_USD if min_usd is None else min_usd
    min_faktor = SPIEL_MIN_FAKTOR if min_faktor is None else min_faktor
    min_quote = MIN_QUOTE if min_quote is None else min_quote
    max_alter_min = MAX_ALTER_MIN if max_alter_min is None else max_alter_min
    gesperrt = list(GESPERRT_FALLBACK) if gesperrt is None else list(gesperrt)
    norm = liga_norm() if norm is None else norm
    now = now or datetime.now(timezone.utc)

    je_spiel = {}
    for w in wetten or []:
        if not isinstance(w, dict) or w.get("kombi"):
            continue
        if w.get("kat") in gesperrt:
            continue
        ev, t, u = w.get("eventId"), _ts(w.get("ts")), w.get("einsatzUsd")
        if not ev or t is None:
            continue
        if not isinstance(u, (int, float)) or isinstance(u, bool) or u <= 0:
            continue
        # 🔴 Der Quotenboden wirkt hier je ZEILE, nicht je Cluster — anders als beim
        # Auswahl-Burst, wo ohnehin alle Wetten dieselbe Quote tragen. Zuerst stand hier
        # `if min(quoten) < min_quote: verwirf das Cluster`, und genau daran fiel Lucas'
        # eigener Fall durch: fuenf Wetten zwischen 1,65 und 1,85 auf FK Gazalkent, plus eine
        # Abstauber-Wette @1,03 zwanzig Minuten spaeter. Eine einzelne 1,03 darf die fuenf
        # anderen nicht entwerten. Sie zaehlt weder zur Anzahl noch zur Summe — sie ist
        # einfach nicht Teil der Beobachtung.
        q = _quote(w)
        if q is None or q < min_quote:
            continue
        je_spiel.setdefault(ev, []).append((t, w))

    aus = []
    for ev, v in je_spiel.items():
        if len(v) < min_n:
            continue
        v.sort(key=lambda z: z[0])
        for i in range(len(v)):
            j = i
            while j + 1 < len(v) and (v[j + 1][0] - v[i][0]).total_seconds() <= fenster_s:
                j += 1
            if j - i + 1 < min_n:
                continue
            g = [z[1] for z in v[i:j + 1]]
            gerichtet = [x for x in g if seite(x)]
            seiten = {seite(x) for x in gerichtet}
            if len(seiten) != 1:          # keine gerichtete Wette, oder eine Gegenseite
                continue
            # Mindestens `min_gerichtet` Tickets muessen eine MANNSCHAFT benennen. Vier neutrale
            # Over/Under-Wetten plus eine auf die Heimelf sind ein Torlinien-Handel, keine
            # Richtungswette — und ohne diese Zahl waere jeder Ueber/Unter-Schwall eine Meldung.
            if len(gerichtet) < min_gerichtet:
                continue
            summe = sum(float(x["einsatzUsd"]) for x in g)
            if summe < min_usd:
                continue
            med = _norm_fuer(norm, g[0])   # 25.10.: nach Turnier-ID, nicht nach Name
            if not med:
                continue                  # keine Norm = kein Faktor = kein Urteil
            faktor = summe / (med * len(g))
            if faktor < min_faktor:
                continue
            if (now - v[j][0]).total_seconds() / 60.0 > max_alter_min:
                continue                  # zu alt zum Melden — dieselbe Grenze wie oben
            aus.append({"eventId": ev, "wetten": g, "summe": summe, "faktor": faktor,
                        "seite": list(seiten)[0], "nGerichtet": len(gerichtet),
                        "sekunden": (v[j][0] - v[i][0]).total_seconds(),
                        "von": v[i][0], "bis": v[j][0]})
            break
    aus.sort(key=lambda b: b["von"])      # aelteste zuerst — der Deckel ist keine Rangfolge
    return aus


def spiel_key(b) -> str:
    """Dedup ueber das SPIEL. Ein zweiter Schwall auf dasselbe Spiel ist dieselbe Beobachtung."""
    return "spiel:%s" % b.get("eventId")


def build_spiel_card(b, unterdrueckt=0) -> str:
    """Die Karte des Spiel-Bursts. Sie sagt in ihrer Kopfzeile, dass sie eine ANDERE Einheit
    misst als der Auswahl-Burst — sonst liest man die Rendite der einen auf der anderen."""
    g = b["wetten"]
    erste = g[0]
    e = lambda x: html.escape(str(x or ""), quote=False)
    maerkte = []
    for x in g:
        m = str(x.get("markt") or "")
        if m and m not in maerkte:
            maerkte.append(m)
    phase = ("live" if all(x.get("phase") == "live" for x in g)
             else "vor Anpfiff" if all(x.get("phase") == "vor" for x in g) else "gemischt")
    try:
        import stake_liga_stufe as _LS
        klasse = _LS.stufe(erste.get("ligaSlug"), erste.get("sport"), erste.get("ligaId"))
    except Exception:
        klasse = None
    KLARTEXT = {"1": "oberste Spielklasse", "2": "zweite Spielklasse",
                "3": "dritte Klasse und tiefer", "kontinental": "kontinental",
                "pokal": "Pokal", "frauen": "Frauen", "jugend": "Jugend",
                "reserve": "Reserve", "srl": "Simulated Reality",
                "mehrdeutig": "Spielklasse nicht eindeutig", "freundschaft": "Freundschaftsspiel"}

    lines = ["▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓",
             "🎯 <b>STAKE-SPIEL</b> · <b>%d Wetten</b> auf <b>eine Seite</b> in <b>%s</b>"
             % (len(g), _sek_text(int(round(b["sekunden"])))),
             "▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓", ""]
    lines.append("%s <i>%s</i>" % (_emoji(erste.get("kat")), e(erste.get("kat") or "Sport")))
    lines.append("<b>%s</b>" % e(erste.get("event")))
    lines.append("<i>%s%s</i>" % (e(erste.get("liga")),
                                 (" · " + KLARTEXT.get(klasse, klasse)) if klasse else ""))
    lines.append("")
    lines.append("➡️ alles auf <b>%s</b>" % e(b.get("seite")))
    lines.append("💰 <b>%s</b> über %d %s · <b>%.1fx</b> der Liga-Norm"
                 % (_usd(b["summe"]), len(maerkte), "Märkte" if len(maerkte) != 1 else "Markt",
                    b["faktor"]))
    lines.append("")
    for x in sorted(g, key=lambda y: -float(y.get("einsatzUsd") or 0))[:6]:
        t = _ts(x.get("ts"))
        lines.append("   %s  @%.2f  <i>%s</i>  <code>%s</code>"
                     % (_usd(x.get("einsatzUsd")), _quote(x), e(x.get("auswahl")),
                        t.strftime("%H:%M:%S") if t else "?"))
    lines.append("")
    lines.append("⏱️ <b>%s</b>" % phase)
    if unterdrueckt:
        lines.append("<i>+%d weitere Spiel-Bursts in diesem Lauf nicht gesendet (Deckel %d)</i>"
                     % (unterdrueckt, SPIEL_MAX_PUSH))
    lines.append("\n<i>🔬 NEU seit 22.09.2026 und noch OHNE eigene Rendite-Messung. Das ist eine "
                 "andere Einheit als der ⚡ Stake-Burst — dessen +45,9 %% gelten hier NICHT. "
                 "Zuschnitt: >=%d Wetten je Spiel (davon >=%d mit Mannschaft), %d Min, ab %s, "
                 "mindestens %.1fx der Liga-Norm, Quoten ab %.2f, keine Gegenseite. Gemessen am "
                 "Ledger sind das rund 17 Meldungen pro Tag; die Schwellen sind nach EINEM "
                 "Beispiel gesetzt und noch nicht belegt.</i>"
                 % (SPIEL_MIN_N, SPIEL_MIN_GERICHTET, int(SPIEL_FENSTER_S // 60),
                    _usd(SPIEL_MIN_USD), SPIEL_MIN_FAKTOR, MIN_QUOTE))
    return "\n".join(lines)


def spiel_buch_zeile(b, ts) -> dict:
    """Eigene Buchzeile mit `art: "spiel"`. Die zwei Arten duerfen NIE in einer Zahl
    zusammenfallen — sie messen verschiedene Einheiten, und genau daran ist am 07.09. die
    Spielklassen-Ansicht fast gescheitert."""
    g = b["wetten"]
    erste = g[0]
    return {
        "k": spiel_key(b), "art": "spiel",
        "eventId": b["eventId"], "event": erste.get("event"),
        "liga": erste.get("liga"), "ligaSlug": erste.get("ligaSlug"), "kat": erste.get("kat"),
        "seite": b.get("seite"), "nGerichtet": b.get("nGerichtet"),
        "maerkte": sorted({str(x.get("markt") or "") for x in g if x.get("markt")}),
        "summeUsd": round(float(b["summe"]), 2),
        "faktor": round(float(b["faktor"]), 2),
        "nWetten": len(g), "sekunden": round(float(b["sekunden"]), 1),
        "phase": ("live" if all(x.get("phase") == "live" for x in g)
                  else "vor" if all(x.get("phase") == "vor" for x in g) else "gemischt"),
        "betIds": [x.get("id") for x in g],
        "sentAt": ts, "status": "pending",
    }


# ── Torlinien-Burst je Spiel (07.10.2026, Lucas) ───────────────────────────────────────────
# 🔴 07.10.2026 (Lucas: „wieso war es eigentlich kein Burst? … ja will sowas für over under,
# dachte sowas haben wir eh auch"). Riestra Reserve – Rivadavia Reserve: 10 Wetten, ~28 K$, ALLE
# auf Over, verteilt auf 2.25 / 2.5 / 2.75 / 3, vor Anpfiff. Wir hatten jede Zeile im Ledger.
#   · Der Auswahl-Burst sah vier Linien und je Linie hoechstens 2 Wetten in 5 Min.
#   · Der Spiel-Burst haette im Fenster 17:34-17:51 UTC getragen (4 Wetten, 18,5 K$, 2,7x Norm)
#     — und fiel an `min_gerichtet`: mindestens 2 Wetten muessen eine MANNSCHAFT nennen. Die Regel
#     stand mit Absicht da („sonst waere jeder Ueber/Unter-Schwall eine Meldung").
# Diese Sorge war nie gemessen. Nachgezaehlt im Rohbuch (5 Tage, Fussball, dieselben Schwellen,
# nur Spieltor-Linien, eine Richtung): 4 Faelle — keine Flut, sondern genau das gesuchte Muster.
# Ergebnisse waren dabei nicht angesehen (fast alles noch offen).
# Fehlerklasse: eine Einheit, die die Beobachtung nach der falschen Achse zerschneidet — hier nach
# LINIE statt nach RICHTUNG. Over 2.25 und Over 3 sind dieselbe Meinung, nur anders bepreist.
# Eigene Einheit, eigener Dedup, eigenes Buch (`art: "tore"`), erst nur Trades-Kanal.
TOR_MAERKTE = {"asian total", "total goals", "total"}   # ganzes Spiel, beide Teams — nichts sonst
TOR_MIN_N = int(os.environ.get("STAKE_TOR_MIN_N") or 4)
TOR_FENSTER_S = float(os.environ.get("STAKE_TOR_FENSTER_S") or 1800)
TOR_MIN_USD = float(os.environ.get("STAKE_TOR_MIN_USD") or 12000)
TOR_MIN_FAKTOR = float(os.environ.get("STAKE_TOR_MIN_FAKTOR") or 1.5)
TOR_MAX_PUSH = int(os.environ.get("STAKE_TOR_MAX") or 2)
TOR_AN = (os.environ.get("STAKE_TOR_PUSH") or "true").strip().lower() \
    not in ("0", "false", "no", "off")
TOR_ZIEL_N = 100
_TOR_RX = re.compile(r"^(over|under)\s+\d+(?:\.\d+)?\s*$", re.I)


def richtung_tore(w):
    """„Over" / „Under" fuer eine Spieltor-Linie des GANZEN Spiels, sonst None. REIN.

    Bewusst eine Positivliste: „1st Half - Asian Total", „Total Corners", „Spain Total" (Teamtore),
    „Total & Both Teams to Score" sind andere Wetten und zaehlen nicht mit."""
    m = str((w or {}).get("markt") or "").strip().lower()
    if m not in TOR_MAERKTE:
        return None
    a = str((w or {}).get("auswahl") or "").strip()
    if not _TOR_RX.match(a):
        return None
    return "Over" if a.lower().startswith("over") else "Under"


def tor_bursts(wetten, min_n=None, fenster_s=None, min_usd=None, min_faktor=None,
               min_quote=None, max_alter_min=None, gesperrt=None, norm=None, now=None) -> list:
    """Torlinien-Bursts je Spiel, eine Richtung. REIN (alles injizierbar).

    `min_n` Einzelwetten auf Spieltor-Linien DESSELBEN Spiels in `fenster_s`, alle in DIESELBE
    Richtung (Linie egal), zusammen >= `min_usd` UND >= `min_faktor` x Liga-Norm je Wette. Eine
    einzige Torlinien-Wette in die Gegenrichtung im Fenster verwirft es. Wetten auf andere Maerkte
    spielen keine Rolle — dafuer gibt es den Spiel-Burst. Je Spiel hoechstens einer.
    """
    min_n = TOR_MIN_N if min_n is None else min_n
    fenster_s = TOR_FENSTER_S if fenster_s is None else fenster_s
    min_usd = TOR_MIN_USD if min_usd is None else min_usd
    min_faktor = TOR_MIN_FAKTOR if min_faktor is None else min_faktor
    min_quote = MIN_QUOTE if min_quote is None else min_quote
    max_alter_min = MAX_ALTER_MIN if max_alter_min is None else max_alter_min
    gesperrt = list(GESPERRT_FALLBACK) if gesperrt is None else list(gesperrt)
    norm = liga_norm() if norm is None else norm
    now = now or datetime.now(timezone.utc)

    je_spiel = {}
    for w in wetten or []:
        if not isinstance(w, dict) or w.get("kombi") or int(w.get("nBeine") or 1) != 1:
            continue
        if w.get("kat") != "Fußball" or w.get("kat") in gesperrt:
            continue
        r = richtung_tore(w)
        ev, t, u = w.get("eventId"), _ts(w.get("ts")), w.get("einsatzUsd")
        if not r or not ev or t is None:
            continue
        if not isinstance(u, (int, float)) or isinstance(u, bool) or u <= 0:
            continue
        q = _quote(w)
        if q is None or q < min_quote:
            continue
        je_spiel.setdefault(ev, []).append((t, r, w))

    aus = []
    for ev, v in je_spiel.items():
        if len(v) < min_n:
            continue
        v.sort(key=lambda z: z[0])
        for i in range(len(v)):
            j = i
            while j + 1 < len(v) and (v[j + 1][0] - v[i][0]).total_seconds() <= fenster_s:
                j += 1
            if j - i + 1 < min_n:
                continue
            fenster = v[i:j + 1]
            richtungen = {z[1] for z in fenster}
            if len(richtungen) != 1:
                continue                  # Gegenrichtung im Fenster: kein einseitiges Geld
            g = [z[2] for z in fenster]
            summe = sum(float(x["einsatzUsd"]) for x in g)
            if summe < min_usd:
                continue
            med = _norm_fuer(norm, g[0])   # 25.10.: nach Turnier-ID, nicht nach Name
            if not med:
                continue                  # keine Norm = kein Faktor = kein Urteil
            faktor = summe / (med * len(g))
            if faktor < min_faktor:
                continue
            if (now - v[j][0]).total_seconds() / 60.0 > max_alter_min:
                continue
            aus.append({"eventId": ev, "wetten": g, "summe": summe, "faktor": faktor,
                        "richtung": fenster[0][1],
                        "sekunden": (v[j][0] - v[i][0]).total_seconds(),
                        "von": v[i][0], "bis": v[j][0]})
            break
    aus.sort(key=lambda b: b["von"])
    return aus


def buch_kuerzen(led, keep=None) -> list:
    """Die letzten `keep` Zeilen JE BUCHART, Reihenfolge bleibt. REIN.

    🔴 07.10.2026, gefunden beim Bau der Torlinien-Bursts: hier stand `led[-LEDGER_KEEP:]` ueber
    ALLE Arten. 501 Zeilen in 25 Tagen, davon 328 Spiel-Bursts — in gut zwei Wochen waere der
    Deckel erreicht, und dann haetten die haeufigen Spiel-Bursts die seltenen Kleine-Liga- und
    Torlinien-Zeilen hinausgeschoben, BEVOR deren Messung (Ziel n=100) fertig ist. Der Zaehler in
    messungen.py liest genau dieses Buch.
    Fehlerklasse: ein gemeinsamer Deckel fuer Einheiten mit sehr verschiedener Rate — die seltene
    verliert still gegen die haeufige.
    """
    keep = LEDGER_KEEP if keep is None else keep
    zaehl, behalten = {}, []
    for z in reversed(led or []):
        art = (z.get("art") if isinstance(z, dict) else None) or "auswahl"
        zaehl[art] = zaehl.get(art, 0) + 1
        if zaehl[art] <= keep:
            behalten.append(z)
    return list(reversed(behalten))


def tor_key(b) -> str:
    return "tore:%s" % b.get("eventId")


def art_bilanz(ledger, art, phase=None) -> dict:
    """n, mittlere Rendite, einseitige Untergrenze (z=1,645 ab n=30) fuer EINE Buchart. REIN."""
    rr = [z["rendite"] for z in (ledger or []) if isinstance(z, dict) and z.get("art") == art
          and (phase is None or z.get("phase") == phase)
          and isinstance(z.get("rendite"), (int, float))]
    if not rr:
        return {"n": 0}
    n, m = len(rr), sum(rr) / len(rr)
    ug = None
    if n >= 30:
        sd = (sum((x - m) ** 2 for x in rr) / (n - 1)) ** 0.5
        ug = max(-1.0, m - 1.645 * sd / n ** 0.5)
    return {"n": n, "roi": m, "ug": ug}


def build_tor_card(b, bilanz=None, unterdrueckt=0) -> str:
    """Dieselbe Bauform wie die STAKE-SPIEL-Karte (07.10.2026, Lucas: „wieso sieht es optisch
    nicht wie eine normale Burst-Nachricht aus") — Kopfbalken, Sportzeile, Spiel, Liga, Richtung,
    Summe, die Einzelwetten mit Uhrzeit, Phase, Fussnote. Wer die eine lesen kann, liest die andere."""
    g = b["wetten"]
    erste = g[0]
    e = lambda x: html.escape(str(x or ""), quote=False)
    try:
        import stake_liga_stufe as _LS
        klasse = str(_LS.stufe(erste.get("ligaSlug"), erste.get("sport"), erste.get("ligaId")))
    except Exception:
        klasse = None
    KLARTEXT = {"1": "oberste Spielklasse", "2": "zweite Spielklasse",
                "3": "dritte Klasse und tiefer", "kontinental": "kontinental",
                "pokal": "Pokal", "frauen": "Frauen", "jugend": "Jugend",
                "reserve": "Reserve", "srl": "Simulated Reality",
                "mehrdeutig": "Spielklasse nicht eindeutig", "freundschaft": "Freundschaftsspiel"}
    klein = klasse in ("3", "reserve")
    linien = []
    for x in g:
        a = str(x.get("auswahl") or "")
        if a and a not in linien:
            linien.append(a)
    phase = _phase(g)

    lines = ["▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓",
             "📈 <b>STAKE-TORE</b> · <b>%d Wetten</b> auf <b>%s</b> in <b>%s</b>"
             % (len(g), b["richtung"].upper(), _sek_text(int(round(b["sekunden"])))),
             "▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓", ""]
    lines.append("%s <i>%s</i>%s" % (_emoji(erste.get("kat")), e(erste.get("kat") or "Sport"),
                                    " · 🔎 <b>kleine Liga</b>" if klein else ""))
    lines.append("<b>%s</b>" % e(erste.get("event")))
    lines.append("<i>%s%s</i>" % (e(erste.get("liga")),
                                 (" · " + KLARTEXT.get(klasse, klasse)) if klasse else ""))
    lines.append("")
    lines.append("➡️ alles auf <b>%s</b> · Torlinien" % b["richtung"].upper())
    lines.append("💰 <b>%s</b> über %d %s · <b>%.1fx</b> der Liga-Norm"
                 % (_usd(b["summe"]), len(linien), "Linien" if len(linien) != 1 else "Linie",
                    b["faktor"]))
    lines.append("")
    for x in sorted(g, key=lambda y: -float(y.get("einsatzUsd") or 0))[:6]:
        t = _ts(x.get("ts"))
        lines.append("   %s  @%.2f  <i>%s</i>  <code>%s</code>"
                     % (_usd(x.get("einsatzUsd")), _quote(x), e(x.get("auswahl")),
                        t.strftime("%H:%M:%S") if t else "?"))
    lines.append("")
    ko = _ts(erste.get("anpfiff"))
    if phase == "vor":
        lines.append("⏱️ <b>vor Anpfiff</b>%s" % (" · Anpfiff %s UTC" % ko.strftime("%d.%m. %H:%M")
                                                  if ko else ""))
    else:
        lines.append("⏱️ <b>%s</b>" % phase)
    if unterdrueckt:
        lines.append("<i>+%d weitere Torlinien-Bursts in diesem Lauf nicht gesendet (Deckel %d)</i>"
                     % (unterdrueckt, TOR_MAX_PUSH))
    bil = bilanz or {}
    if not bil.get("n"):
        mess = "noch keine Meldung abgerechnet"
    else:
        mess = "bisher %d abgerechnet · ROI %+.1f %%" % (bil["n"], bil["roi"] * 100)
        mess += (" · Untergrenze %+.1f %%" % (bil["ug"] * 100)) if bil.get("ug") is not None \
            else " · kein Urteil unter 30"
    lines.append("\n<i>🔬 Testlauf seit 07.10.2026, nur Trades · %s (%s). Zuschnitt: >=%d Wetten "
                 "auf Spieltor-Linien je Spiel, %d Min, eine Richtung (Linie egal), ab %s, "
                 "mindestens %.1fx der Liga-Norm, Quoten ab %.2f, keine Gegenrichtung. Urteil ab "
                 "n=%d, vor Anpfiff und live getrennt.</i>"
                 % (mess, "vor Anpfiff" if phase == "vor" else "live/gemischt", TOR_MIN_N,
                    int(TOR_FENSTER_S // 60), _usd(TOR_MIN_USD), TOR_MIN_FAKTOR, MIN_QUOTE,
                    TOR_ZIEL_N))
    return "\n".join(lines)


def tor_buch_zeile(b, ts) -> dict:
    g = b["wetten"]
    erste = g[0]
    return {
        "k": tor_key(b), "art": "tore",
        "eventId": b["eventId"], "event": erste.get("event"),
        "liga": erste.get("liga"), "ligaSlug": erste.get("ligaSlug"), "kat": erste.get("kat"),
        "ebene": _ebene(erste),
        "richtung": b["richtung"],
        "linien": sorted({str(x.get("auswahl") or "") for x in g}),
        "summeUsd": round(float(b["summe"]), 2),
        "faktor": round(float(b["faktor"]), 2),
        "nWetten": len(g), "sekunden": round(float(b["sekunden"]), 1),
        "phase": _phase(g), "anpfiff": erste.get("anpfiff"),
        "betIds": [x.get("id") for x in g],
        "sentAt": ts, "status": "pending",
    }


def _beinahe(auswahl_id, g, summe, gruende, von, bis) -> dict:
    """Eine Zeile fuers Beinahe-Buch: ein Cluster, das die Regeln NICHT passiert hat.

    🔴 13.09.2026 (Lucas: „es muss auch solche Bursts auf generelle Ligen geben — ich seh da
    immer Screens aus dieser einen Gruppe, irgendwelche Panama-Ligen"). Die Frage liess sich
    nicht beantworten: der Kanal wusste nur, was er GESENDET hat. Ich habe sie an diesem Tag
    von Hand aus dem Ledger rekonstruiert (Ergebnis: es liegt nicht an den Schwellen, sondern
    daran, dass Stakes Feed erst ab ~1.000 $ je Wette ueberhaupt etwas zeigt) — und genau das
    darf beim naechsten Mal nicht wieder eine Stunde Handarbeit sein.

    Dieselbe Regel wie ueberall hier: was wir wegfiltern, muss messbar bleiben. Ein Filter, dessen
    Preis niemand kennt, laesst sich weder rechtfertigen noch widerlegen.
    """
    erste = g[0]
    return {"auswahlId": auswahl_id, "gruende": sorted(gruende),
            "liga": erste.get("liga"), "ligaSlug": erste.get("ligaSlug"),
            "kat": erste.get("kat"), "sport": erste.get("sport"),
            "event": erste.get("event"), "markt": erste.get("markt"),
            "auswahl": erste.get("auswahl"),
            "quote": _quote(erste), "quoten": sorted({round(float(_quote(x)), 2) for x in g}),
            "nWetten": len(g), "summeUsd": round(float(summe), 2),
            "sekunden": round((bis - von).total_seconds(), 1),
            "gesehenAm": von.isoformat()}


def burst_key(b) -> str:
    """Dedup-Schluessel. Die Auswahl allein reicht: ein zweiter Burst auf dieselbe Auswahl ist
    dieselbe Beobachtung, nicht eine neue."""
    return str(b.get("auswahlId"))


def prune_seen(seen, now=None, keep_h=SEEN_KEEP_H) -> dict:
    """Dedup-Stand aufraeumen. REIN. Ohne das waechst die Datei ewig, und eine Auswahl, die in
    zwei Wochen wieder auftaucht, bliebe fuer immer gesperrt."""
    now = now or datetime.now(timezone.utc)
    aus = {}
    for k, v in (seen if isinstance(seen, dict) else {}).items():
        t = _ts((v or {}).get("ts") if isinstance(v, dict) else None)
        if t is not None and (now - t).total_seconds() / 3600.0 <= keep_h:
            aus[k] = v
    return aus


def build_burst_card(b, unterdrueckt=0, bilanz=None) -> str:
    """Die Telegram-Karte. Bewusst anders gebaut als die Poly-Dominanz-Karte: dort fuehrt der
    ANTEIL, hier die GESCHWINDIGKEIT — das ist die Eigenschaft, um die es geht."""
    g = b["wetten"]
    erste = g[0]
    q = _quote(erste)
    sek = int(round(b["sekunden"]))
    phase = ("live" if all(x.get("phase") == "live" for x in g)
             else "vor Anpfiff" if all(x.get("phase") == "vor" for x in g) else "gemischt")
    e = lambda x: html.escape(str(x or ""), quote=False)

    fallend = b.get("quotenArt") == "fallend"
    lines = ["▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓",
             "⚡%s <b>STAKE-BURST</b> · <b>%d Wetten</b> in <b>%s</b>"
             % ("📉" if fallend else "", len(g), _sek_text(sek)),
             "▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓", ""]
    lines.append("%s <i>%s</i>" % (_emoji(erste.get("kat")), e(erste.get("kat") or "Sport")))
    lines.append("<b>%s</b>" % e(erste.get("event")))
    lines.append("<i>%s</i>" % e(erste.get("liga")))
    lines.append("")
    lines.append("🎯 %s — <b>%s</b>" % (e(erste.get("markt")), e(erste.get("auswahl"))))
    if fallend:
        _qs = [_quote(x) for x in g]
        lines.append("💰 <b>%s</b> auf einer Auswahl" % _usd(b["summe"]))
        lines.append("📉 <b>Quote fällt mit dem Geld: @%.2f → @%.2f</b>" % (_qs[0], _qs[-1]))
    else:
        lines.append("💰 <b>%s</b> auf einer Auswahl · alle @<b>%.2f</b>" % (_usd(b["summe"]), q))
    lines.append("")
    # Bei fallender Quote zeitlich statt nach Groesse — der Verlauf IST die Aussage.
    _reihe = (g if fallend else sorted(g, key=lambda y: -float(y.get("einsatzUsd") or 0)))
    for x in _reihe[:8 if fallend else 6]:
        t = _ts(x.get("ts"))
        lines.append("   %s  @%.2f  <code>%s</code>" % (_usd(x.get("einsatzUsd")), _quote(x),
                                                        t.strftime("%H:%M:%S") if t else "?"))
    lines.append("")
    lines.append("⏱️ <b>%s</b>" % phase)
    if unterdrueckt:
        lines.append("<i>+%d weitere Bursts in diesem Lauf nicht gesendet (Deckel %d)</i>"
                     % (unterdrueckt, MAX_PUSH))
    # 🔴 26.09.2026: hier stand fest getippt „+45,9 % ROI (Untergrenze +34,3 %, n=212)" — die
    # Rueckrechnung vom 12.09. Das eigene Buch sagte am 26.09. etwas anderes: 72 gesendete Bursts,
    # ROI +4,5 %, Untergrenze −13,8 %. Ein Satz, dem die Zahl daneben widerspricht, stand auf JEDER
    # Karte. Jetzt steht dort, was die gesendeten Bursts DIESER Art tatsaechlich gebracht haben.
    lines.append("")
    lines.append("<i>🔬 " + _bilanz_satz(bilanz, "Quote fällt" if fallend else "gleiche Quote") + "</i>")
    return "\n".join(lines)


def _sek_text(s) -> str:
    s = int(s)
    if s < 60:
        return "%d Sek" % max(s, 1)
    return "%d:%02d Min" % (s // 60, s % 60)


_EMOJI = {"Fußball": "⚽", "Tennis": "🎾", "E-Sport": "🎮", "US-Sport": "🏈", "Cricket": "🏏",
          "Basketball": "🏀", "Tischtennis": "🏓", "Volleyball": "🏐", "Handball": "🤾",
          "Eishockey": "🏒", "Darts": "🎯", "Snooker": "🎱", "Kampfsport": "🥊"}


def _emoji(kat) -> str:
    return _EMOJI.get(str(kat or ""), "🏆")


def buch_zeile(b, ts) -> dict:
    """Eine gesendete Beobachtung als Buchzeile. Abgerechnet wird sie spaeter aus dem Ledger —
    `stake_settle.py` traegt die Ergebnisse auf den Wetten nach, die hier namentlich stehen."""
    g = b["wetten"]
    erste = g[0]
    return {
        "k": burst_key(b),
        "auswahlId": b["auswahlId"], "eventId": erste.get("eventId"),
        "event": erste.get("event"), "liga": erste.get("liga"), "kat": erste.get("kat"),
        "markt": erste.get("markt"), "auswahl": erste.get("auswahl"),
        "quote": _quote(erste),
        "summeUsd": round(float(b["summe"]), 2),
        "nWetten": len(g), "sekunden": round(float(b["sekunden"]), 1),
        # Phase getrennt stempeln: live und vor Anpfiff sind zwei verschiedene Messungen
        # (+29 % gegen +8,8 %), zusammengerechnet waere keine von beiden zu beantworten.
        "phase": ("live" if all(x.get("phase") == "live" for x in g)
                  else "vor" if all(x.get("phase") == "vor" for x in g) else "gemischt"),
        "betIds": [x.get("id") for x in g],
        # 26.09.2026: getrennt messen — „gleich" und „fallend" sind zwei Hypothesen.
        "quotenArt": b.get("quotenArt") or "gleich",
        "quotenVerlauf": [_quote(x) for x in g],
        "sentAt": ts, "status": "pending",
    }


# ── Abrechnung (13.09.2026, Lucas: „die Stake-Bursts haette ich auch gerne in den Stats") ──
#
# 🔴 Beim Bauen des Stats-Blocks stellte sich heraus: ALLE 15 Buchzeilen standen auf `pending`.
# Der Kopf von `buch_zeile` sagte „abgerechnet wird sie spaeter aus dem Ledger" — nur tat das
# niemand. Der Kanal pushte seit dem 11.09. und mass sich nie. Das ist die Fehlerklasse „wer
# pusht, misst den Push", diesmal in ihrer stillsten Form: nichts ist falsch, es steht nur
# nichts da.
#
# ⚠️ Und es war dringender, als es aussah. `stake_bet_ledger.json` ist ein ROLLIERENDES Fenster
# von 20.000 Wetten — gemessen am 13.09. sind das **5,3 Tage**. Eine Burst-Zeile, die in diesem
# Fenster nicht abgerechnet wird, ist danach nicht mehr abrechenbar: ihre Wetten sind aus der
# Quelle gefallen. Deshalb laeuft die Abrechnung bei JEDEM Lauf mit (alle 15 Minuten), und
# deshalb bekommt eine Zeile, deren Wetten verschwunden sind, den Status `nicht_abrechenbar`
# statt ewig `pending` — sonst waechst ein Haufen Zeilen, die aussehen, als kaeme da noch was.
STATUS_OFFEN = "pending"
STATUS_FERTIG = "abgerechnet"
STATUS_TOT = "nicht_abrechenbar"


def _rendite(zeile: dict, wetten_idx: dict):
    """(rendite, grund) fuer eine Burst-Zeile. Rendite je Einsatz-Dollar, `None` = noch nicht.

    ⭐ Nur wenn ALLE Wetten des Bursts einen Endstand tragen. Teilweise abzurechnen waere
    verzerrt: die frueh fertigen Beine sind nicht dieselbe Menge wie der ganze Burst, und die
    Zeile wuerde beim naechsten Lauf eine andere Zahl zeigen als beim letzten.

    ⭐ Gewichtet nach GELD (Summe pnl / Summe Einsatz), nicht je Wette gemittelt: ein Burst ist
    EINE Position, die auf mehrere Tickets verteilt wurde. Genau das ist ja das Muster, das ihn
    zum Burst macht.
    """
    ids = zeile.get("betIds") or []
    if not ids:
        return None, "keine Wett-IDs in der Zeile"
    da = [wetten_idx.get(i) for i in ids]
    if any(x is None for x in da):
        return None, "mindestens eine Wette ist aus dem rollierenden Stake-Ledger gefallen"
    abr = [(x.get("abrechnung") or {}) for x in da]
    if not all(y.get("endstand") for y in abr):
        return None, "laeuft noch"
    einsatz = sum(float(y.get("einsatzUsdGeprueft") or 0) for y in abr)
    if einsatz <= 0:
        return None, "kein geprueefter Einsatz"
    pnl = sum(float(y.get("pnlUsd") or 0) for y in abr)
    return pnl / einsatz, None


def abrechnen(zeilen: list, wetten: list, now=None) -> tuple[list, int, int]:
    """Offene Buchzeilen abrechnen. Gibt (Zeilen, neu abgerechnet, aufgegeben) zurueck. REIN."""
    now = now or datetime.now(timezone.utc)
    idx = {w.get("id"): w for w in (wetten or []) if isinstance(w, dict) and w.get("id")}
    # Ab wann ist eine Zeile verloren? Sobald ihre Wetten fehlen UND die Quelle nicht mehr so
    # weit zurueckreicht. Die zweite Bedingung ist wichtig: ein leeres oder halb geladenes
    # Stake-Ledger darf nicht reihenweise Zeilen fuer tot erklaeren.
    quelle_ab = min((str(w.get("ts") or "") for w in (wetten or []) if w.get("ts")), default=None)
    fertig = tot = 0
    for z in zeilen:
        if not isinstance(z, dict) or z.get("status") != STATUS_OFFEN:
            continue
        r, grund = _rendite(z, idx)
        if r is not None:
            z["status"] = STATUS_FERTIG
            z["rendite"] = round(r, 4)
            z["win"] = r > 0
            z["settledAt"] = now.isoformat()
            fertig += 1
        elif quelle_ab and str(z.get("sentAt") or "") < quelle_ab:
            z["status"] = STATUS_TOT
            z["grund"] = grund
            z["settledAt"] = now.isoformat()
            tot += 1
    return zeilen, fertig, tot


def verworfen_mischen(alt: dict, neu: list, now) -> dict:
    """Beinahe-Buch fortschreiben. Ein Cluster zaehlt EINMAL (Schluessel = Auswahl). REIN.

    Ohne den Dedup zaehlte dasselbe Cluster bei jedem Lauf neu mit, solange es im Fenster liegt
    — nach einer Stunde stuende „sechsmal an der Quote gescheitert" da, wo es einmal war. Genau
    diese Sorte Zaehlfehler macht eine Statistik unbrauchbar, ohne dass etwas kaputt aussieht.
    """
    zeilen = list((alt or {}).get("zeilen") or [])
    bekannt = {str(z.get("auswahlId")) for z in zeilen if isinstance(z, dict)}
    for z in neu or []:
        if str(z.get("auswahlId")) not in bekannt:
            zeilen.append(z)
            bekannt.add(str(z.get("auswahlId")))
    zeilen = zeilen[-VERWORFEN_KEEP:]
    zaehler, je_liga, allein = {}, {}, {}
    for z in zeilen:
        g = z.get("gruende") or []
        for x in g:
            zaehler[x] = zaehler.get(x, 0) + 1
        # ⭐ Die eigentliche Frage: an welcher EINEN Regel scheitert ein sonst gueltiges Cluster?
        # Eine Zeile mit drei Gruenden sagt nichts darueber, welche Regel man lockern muesste.
        if len(g) == 1:
            allein[g[0]] = allein.get(g[0], 0) + 1
        L = z.get("liga") or "?"
        je_liga[L] = je_liga.get(L, 0) + 1
    tage = sorted(str(z.get("gesehenAm") or "")[:10] for z in zeilen if z.get("gesehenAm"))
    return {"generatedAt": now.isoformat(), "n": len(zeilen),
            "von": tage[0] if tage else None, "bis": tage[-1] if tage else None,
            "zaehler": zaehler, "nurDieserGrund": allein,
            "jeLiga": dict(sorted(je_liga.items(), key=lambda kv: -kv[1])[:40]),
            "zeilen": zeilen}


def _verworfen_buchen(beinahe: list, now) -> None:
    buch = verworfen_mischen(_load(VERWORFEN_FILE, {}), beinahe, now)
    try:
        _save(VERWORFEN_FILE, buch)
    except Exception as e:
        print("Beinahe-Buch-Schreibfehler:", e)
        return
    a = buch.get("nurDieserGrund") or {}
    print("   🔍 Beinahe-Buch: +%d neu · %d gesamt seit %s · scheitert an GENAU EINER Regel: %s"
          % (len(beinahe), buch["n"], buch.get("von") or "—",
             ", ".join("%s %d" % (k, v) for k, v in sorted(a.items(), key=lambda kv: -kv[1]))
             or "—"))


# ── Kleine Ligen: die einzelne grosse Wette (30.09.2026, Lucas) ────────────────────────────
#
# Lucas: „hab ja oft genug erwähnt das ich die kleinen ligen halt gerne hätte — aber bis heute
# nicht ein burst dafür gekommen". Nachgemessen am 30.09.2026 auf dem Rohbuch (5,7 Tage,
# 20.000 Wetten): auf Ebene 3 im Fussball standen NIE mehr als 2 Wetten derselben Seite in
# 30 Minuten, auf Ebene 2 dreimal 4. Die Burst-Regel (>=4 Wetten) kann dort strukturell nie
# feuern — der Feed zeigt nur Wetten ab $1.000, und davon kommen in kleinen Ligen einzelne an,
# keine Schwaerme. Das Geld zeigt sich dort als EINE grosse Wette, nicht als Haeufung.
#
# Deshalb eine eigene Einheit mit eigenem Buch (`art: "klein"`), NICHT eine gelockerte
# Burst-Regel: die Burst-Bilanz misst etwas anderes und darf nicht mit dieser vermischt werden.
#
# Zuschnitt, VOR der Messung festgelegt (Register `stake-kleine-liga`):
#   Fussball · Ebene 2 oder 3 (stake_liga_stufe) · vor Anpfiff · Einzelwette · Quote ab 1,35 ·
#   Einsatz >= 2x Norm der Liga (fehlt sie: Median der Ebene) · max. 30 Min alt.
# Gemessen gaebe das ~1,4 Meldungen am Tag. Rueckblickend lagen Ebene 2+3 bei -1,8 % (n=274,
# Untergrenze -10,6 %), vor Anpfiff -12,3 % (n=31) — KEIN Beleg, deshalb nur Trades, Ziel n=100.
#
# 🔴 01.10.2026, Neuzuschnitt BEVOR eine einzige Zeile gebucht war (Lucas: „mit kleinen Ligen
# meine ich nicht La Liga 2 — dort rennt natürlich auch Geld rein. Ich meine Argentinien
# Reserve-Liga und so … Live hat sehr wohl eine Auswirkung, wenn es z. B. 0:0 steht"):
#   · Ebene 2 raus (La Liga 2, Serie B, J2: dort ist $2.000 normales Geld), Reserve-Ligen rein.
#   · live erlaubt — die Phase und die Spielminute stehen in jeder Buchzeile, damit live und
#     vor Anpfiff getrennt abgerechnet werden (das Tennis-Argument „Geld folgt dem Spielstand"
#     ist im Fussball eine offene Frage, keine Annahme).
#   · Schwelle 2x -> 1,5x Norm.
# Nachgezaehlt im Rohbuch (5,8 Tage): Ebene 3 + Reserve, Einzelwette, Quote ab 1,35, >= 1,5x Norm
# -> 19 Wetten, rund 3 am Tag, 18 davon live. Unter den alten Regeln: 0 seit dem Start.
# EHRLICH DAZU: Lucas' zwei Beispiele (Riestra Reserve, Terdu–Gazalkent) stehen GAR NICHT im
# Feed — die Quelle sieht solche Spiele nur selten. Der Zuschnitt holt heraus, was da ist.
KLEIN_EBENEN = tuple(e.strip() for e in (os.environ.get("STAKE_KLEIN_EBENEN") or "3,reserve").split(",")
                     if e.strip())
KLEIN_MIN_FAKTOR = float(os.environ.get("STAKE_KLEIN_MIN_FAKTOR") or 1.5)
# Live nur bis Minute 75: der Feed wird alle 15 Min geholt, eine Meldung zu einer Wette aus der
# 94. Minute (Boreham Wood, Over 4.5, $116.000) kaeme nach Abpfiff an. Spielbar ist, was danach
# noch Spielzeit hat. Unbekannte Minute bleibt drin — sie ist meist ein Feed-Loch, kein Spaetkick.
KLEIN_LIVE_MAX_MIN = float(os.environ.get("STAKE_KLEIN_LIVE_MAX_MIN") or 75)
KLEIN_MAX_PUSH = int(os.environ.get("STAKE_KLEIN_MAX") or 3)
KLEIN_AN = (os.environ.get("STAKE_KLEIN_PUSH") or "true").strip().lower() \
    not in ("0", "false", "no", "off")
KLEIN_ZIEL_N = 100
AUSWERTUNG_FILE = BASE / "stake_auswertung.json"


def ebenen_median(pfad=None) -> dict:
    """{Ebene: Median-Einsatz} aus stake_auswertung.json (randliga.ebeneMedian). REIN bis aufs Lesen."""
    d = _load(pfad or AUSWERTUNG_FILE, {}) or {}
    em = ((d.get("randliga") or {}).get("ebeneMedian")) or {}
    return {str(k): float(v) for k, v in em.items() if isinstance(v, (int, float)) and v > 0}


def _ebene(w):
    try:
        import stake_liga_stufe as _LS
        return str(_LS.stufe(w.get("ligaSlug"), w.get("sport"), w.get("ligaId")))
    except Exception:
        return None


def kleine_liga(wetten, norm=None, ebene_median=None, now=None, min_faktor=None,
                min_quote=None, max_alter_min=None) -> list:
    """Grosse Einzelwetten in kleinen Fussball-Ligen (vor Anpfiff UND live), je Auswahl gebuendelt. REIN.

    Ohne Bezugsgroesse (weder Liga-Norm noch Ebenen-Median) kein Treffer — „wir kennen die
    Liga nicht" heisst nicht „der Einsatz ist gross" (dieselbe Regel wie `liga_norm`)."""
    now = now or datetime.now(timezone.utc)
    norm = norm or {}
    ebene_median = ebene_median or {}
    min_faktor = KLEIN_MIN_FAKTOR if min_faktor is None else min_faktor
    min_quote = MIN_QUOTE if min_quote is None else min_quote
    max_alter_min = MAX_ALTER_MIN if max_alter_min is None else max_alter_min
    gruppen = {}
    for w in wetten or []:
        if not isinstance(w, dict) or w.get("kat") != "Fußball":
            continue
        if w.get("kombi") or int(w.get("nBeine") or 1) != 1 or w.get("phase") not in ("vor", "live"):
            continue
        q = _quote(w)
        if not isinstance(q, (int, float)) or q < min_quote:
            continue
        t = _ts(w.get("ts"))
        if t is None or (now - t).total_seconds() > max_alter_min * 60:
            continue
        if w.get("phase") == "live":
            try:
                if float(w.get("spielminute")) > KLEIN_LIVE_MAX_MIN:
                    continue
            except (TypeError, ValueError):
                pass
        ko = _ts(w.get("anpfiff"))
        if w.get("phase") == "vor" and ko is not None and ko <= now:
            continue                      # „vor"-Wette, Spiel laeuft schon: Preis ist weg
        eb = _ebene(w)
        if eb not in KLEIN_EBENEN or not w.get("auswahlId"):
            continue
        gruppen.setdefault(w["auswahlId"], []).append((w, eb))
    aus = []
    for aid, g in gruppen.items():
        erste, eb = g[0]
        ref, basis = _norm_fuer(norm, erste), "liga"
        if not ref:
            ref, basis = ebene_median.get(eb), "ebene"
        if not ref and eb == "reserve":
            # stake_auswertung fuehrt keinen Reserve-Median (zu wenige Wetten). Die naechste
            # Bezugsgroesse ist Ebene 3 — „unbekannt" bleibt sonst „kein Treffer".
            ref, basis = ebene_median.get("3"), "ebene"
        if not ref:
            continue
        summe = sum(float(x.get("einsatzUsd") or 0) for x, _ in g)
        faktor = summe / ref
        if faktor < min_faktor:
            continue
        aus.append({"auswahlId": aid, "wetten": [x for x, _ in g], "summe": summe,
                    "faktor": faktor, "ebene": eb, "ref": ref, "refBasis": basis})
    aus.sort(key=lambda b: -b["faktor"])
    return aus


def klein_key(b) -> str:
    return "klein:%s" % b.get("auswahlId")


def klein_bilanz(ledger) -> dict:
    """Was die gesendeten Kleine-Liga-Alarme bisher gebracht haben — NUR diese Buchart. REIN."""
    rr = [z["rendite"] for z in (ledger or []) if isinstance(z, dict) and z.get("art") == "klein"
          and isinstance(z.get("rendite"), (int, float))]
    if not rr:
        return {"n": 0}
    n, m = len(rr), sum(rr) / len(rr)
    ug = None
    if n >= 30:
        sd = (sum((x - m) ** 2 for x in rr) / (n - 1)) ** 0.5
        ug = max(-1.0, m - 1.645 * sd / n ** 0.5)
    return {"n": n, "roi": m, "ug": ug}


def build_klein_card(b, bilanz=None) -> str:
    g = b["wetten"]
    erste = g[0]
    e = lambda x: html.escape(str(x or ""), quote=False)
    ko = _ts(erste.get("anpfiff"))
    bil = bilanz or {}
    if not bil.get("n"):
        mess = "noch keine Meldung abgerechnet"
    elif bil.get("ug") is None:
        mess = "bisher %d abgerechnet · ROI %+.1f %% · kein Urteil unter 30" % (bil["n"], 100 * bil["roi"])
    else:
        mess = "bisher %d abgerechnet · ROI %+.1f %% · Untergrenze %+.1f %%" % (
            bil["n"], 100 * bil["roi"], 100 * bil["ug"])
    ph = _phase(g)
    minute = next((x.get("spielminute") for x in g if x.get("spielminute") not in (None, "")), None)
    klasse = {"3": "dritte Klasse und tiefer", "reserve": "Reserve-Liga"}.get(b["ebene"], "kleine Liga")
    if ph == "vor":
        zeitzeile = "⏱️ Anpfiff %s UTC" % (ko.strftime("%d.%m. %H:%M") if ko else "?")
    else:
        zeitzeile = "🔴 live%s" % ((" · Minute %s" % e(minute)) if minute is not None else "")
    lines = ["🔎 <b>KLEINE LIGA</b> · grosse Einzelwette %s" % ("vor Anpfiff" if ph == "vor" else "live"),
             "━━━━━━━━━━━━━━",
             "⚽ <b>%s</b>" % e(erste.get("event")),
             "<i>%s · %s</i>" % (e(erste.get("liga")), klasse),
             "",
             "➡️ <b>%s</b> · %s @<b>%.2f</b>" % (e(erste.get("auswahl")), e(erste.get("markt")), _quote(erste)),
             "💰 <b>%s</b>%s · <b>%.1fx</b> der Norm (%s %s)" % (
                 _usd(b["summe"]), (" in %d Wetten" % len(g)) if len(g) > 1 else "",
                 b["faktor"], "Liga" if b["refBasis"] == "liga" else "Ebene", _usd(b["ref"])),
             zeitzeile,
             "",
             "🔬 <i>Testlauf, nur Trades · %s. Jede Meldung wird abgerechnet, Urteil ab n=%d. "
             "Live und vor Anpfiff werden getrennt ausgewertet.</i>" % (mess, KLEIN_ZIEL_N)]
    return "\n".join(lines)


def klein_buch_zeile(b, ts) -> dict:
    g = b["wetten"]
    erste = g[0]
    return {"k": klein_key(b), "art": "klein", "auswahlId": b["auswahlId"],
            "eventId": erste.get("eventId"), "event": erste.get("event"),
            "liga": erste.get("liga"), "ligaSlug": erste.get("ligaSlug"), "kat": erste.get("kat"),
            "ebene": b["ebene"], "markt": erste.get("markt"), "auswahl": erste.get("auswahl"),
            "quote": _quote(erste), "summeUsd": round(float(b["summe"]), 2),
            "faktor": round(float(b["faktor"]), 2), "refBasis": b["refBasis"],
            "nWetten": len(g), "phase": _phase(g), "anpfiff": erste.get("anpfiff"),
            "spielminute": next((x.get("spielminute") for x in g
                                 if x.get("spielminute") not in (None, "")), None),
            "betIds": [x.get("id") for x in g], "sentAt": ts, "status": "pending"}


# 🔴 01.10.2026 (Lucas: „diese ganzen Stake-Bursts zu ATP und WTA — die können ja nie positiv
# sein, oder? Da müssen wir im Filter anpassen"). Nachgerechnet auf 95 abgerechneten Tennis-Bursts:
#     Spiel-Burst live      n=72  ROI −9,3 %  [−26,1 .. +7,5]
#     Auswahl-Burst live    n=17  ROI +0,5 %  [−36,1 .. +37,1]
#     vor Anpfiff           n=2   (beide gewonnen)
#     ATP/WTA −7,6 % (n=69) · Challenger −12,4 % (n=12) — die Turnierstufe trennt NICHT.
# Bewiesen negativ ist das nicht (Obergrenze ueber null). Der Grund zum Stummschalten ist der
# Mechanismus, derselbe wie beim Kursrutsch-Alarm am 19.09.: live im Tennis folgt das Geld dem
# Spielstand (Break, Satzgewinn) — der Preis hat das schon, bevor die Tickets ankommen. Das ist
# Reaktion, keine Information. Vor Anpfiff gibt es diesen Mechanismus nicht; dort bleibt alles.
#
# Stumm heisst NICHT weg: jeder Burst kommt weiter ins Buch (push=false, pushGrund) und wird
# abgerechnet. Traegt Tennis live dort irgendwann, steht es im Block „nicht gesendet".
STUMM_LIVE_KATS = tuple(k.strip() for k in (os.environ.get("STAKE_BURST_STUMM_LIVE") or "Tennis").split(",")
                        if k.strip())


# 08.10.2026: das Muster lebt beim Sammler (eine Quelle fuer Push und Uebersicht).
try:
    from stake_highroller_fetch import GESPERRT_LIGEN_MUSTER as _TOUR_MUSTER
except Exception:                                            # pragma: no cover
    _TOUR_MUSTER = r"\b(atp|wta)\b"
_TOUR_RX = __import__("re").compile(_TOUR_MUSTER, __import__("re").I)


def _phase(g) -> str:
    return ("live" if all(x.get("phase") == "live" for x in g)
            else "vor" if all(x.get("phase") == "vor" for x in g) else "gemischt")


def stumm_grund(b, kats=None) -> str | None:
    """Warum dieser Burst NICHT gesendet wird (oder None). REIN.

    Live und gemischt zaehlen beide: ein Burst, der vor Anpfiff beginnt und live weiterlaeuft,
    traegt die Spielstand-Reaktion schon in sich."""
    kats = STUMM_LIVE_KATS if kats is None else kats
    g = (b or {}).get("wetten") or []
    if not g:
        return None
    kat = g[0].get("kat")
    if kat in kats and _phase(g) != "vor":
        return f"{kat} live stumm (Geld folgt dem Spielstand)"
    # 04.10.2026 (Lucas, zur Karte „STAKE-SPIEL Zverev - Djokovic, ATP Beijing, vor Anpfiff":
    # „Haben wir nicht gesagt, ATP zeigen wir in Bursts nicht?"). Am 01.10. wurde nur Tennis LIVE
    # stumm geschaltet — vor Anpfiff gab es zwei Faelle (beide gewonnen), also keinen Grund aus den
    # Daten. Lucas will ATP/WTA ganz aus dem Kanal; das ist eine Entscheidung, keine Messung.
    # Gebucht wird weiter (push=false), ITF bleibt laut.
    if kat == "Tennis" and _TOUR_RX.search(str(g[0].get("liga") or "")):
        return "ATP/WTA stumm (Lucas 04.10.)"
    # 12.10.2026 (Lucas, zur Karte „🎯 1x2 — Draw · Coventry - Newcastle · $17.8K · alle @3.90":
    # „aus dem Burst nimm bitte Draw wetten raus … sowas wertlos"). Im Buch standen 10 Remis-
    # Bursts, 5 abgerechnet, alle verloren — n zu klein fuer ein Urteil, das hier ist eine
    # Entscheidung, keine Messung. Nur das reine Remis: „Draw No Bet" und „X or Draw" (Doppelte
    # Chance) bleiben laut. Gebucht wird weiter (push=false, pushGrund).
    if all(ist_remis(x) for x in g):
        return "Remis stumm (Lucas 12.10.)"
    return None


_REMIS = {"draw", "x", "remis", "unentschieden", "tie"}


def ist_remis(w) -> bool:
    """Reine Remis-Wette (Auswahl „Draw"/„X"/…). REIN."""
    return str((w or {}).get("auswahl") or "").strip().lower() in _REMIS


def zu_senden(neu, push_an=None, max_push=None) -> list:
    """Welche der neuen Bursts gehen raus? REIN — damit „Push aus" pruefbar ist.

    Stand vorher als Ausdruck mitten in `main` und war damit nur am Quelltext zu pruefen. Eine
    Mutation, die den Schalter ignoriert, waere gruen durchgelaufen — genau deshalb hier.
    """
    push_an = PUSH_AN if push_an is None else push_an
    max_push = MAX_PUSH if max_push is None else max_push
    return list(neu or [])[:max_push] if push_an else []


def main() -> int:
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    quelle = _load(QUELLE_FILE, {})
    wetten = (quelle or {}).get("wetten") or []
    if not wetten:
        print("Stake-Burst: keine Wetten in stake_highroller.json — nichts zu tun.")
        return 0

    seen = prune_seen(_load(SEEN_FILE, {}), now)
    _gesperrt = gesperrte_kats(quelle)
    beinahe = []
    alle = bursts(wetten, gesperrt=_gesperrt, now=now, verworfen=beinahe)
    neu = [b for b in alle if burst_key(b) not in seen]
    print("⚡ Stake-Burst: %d frische(r) Burst(s), %d davon neu (>=%d Wetten, %ds, ab %s, "
          "Quote ab %.2f, max %.0f Min alt, gleiche oder fallende Quote; ausgeblendet: %s)"
          % (len(alle), len(neu), MIN_N, int(FENSTER_S), _usd(MIN_USD), MIN_QUOTE,
             MAX_ALTER_MIN, ", ".join(_gesperrt) or "—"))

    neu_laut = [b for b in neu if not stumm_grund(b)]
    senden = zu_senden(neu_laut)
    unterdrueckt = max(len(neu_laut) - len(senden), 0)
    led = _load(LEDGER_FILE, [])
    if not isinstance(led, list):
        led = []
    schon = {e.get("k") for e in led if isinstance(e, dict)}
    gesendet = 0
    for i, b in enumerate(senden):
        text = build_burst_card(b, unterdrueckt if i == len(senden) - 1 else 0,
                                bilanz=buch_bilanz(led, b.get("quotenArt") or "gleich"))
        if not send_trades_message(text):
            continue
        gesendet += 1
        seen[burst_key(b)] = {"ts": now_iso, "summe": round(float(b["summe"]), 2)}
    # ⭐ JEDER erkannte Burst kommt ins Buch — auch der, der am Deckel haengengeblieben ist, und
    # auch jeder, wenn der Push ganz aus ist. Vorher stand hier `led.append` INNERHALB der
    # Sende-Schleife: alles ueber dem Deckel wurde gezaehlt, aber nie gemessen, und ein
    # stillgelegter Kanal haette gar nichts mehr gesammelt.
    neu_gebucht = 0
    for b in neu:
        k = burst_key(b)
        if k in schon:
            continue
        z = buch_zeile(b, now_iso)
        z["push"] = k in seen          # nur ein erfolgreicher Send steht in `seen`
        if not z["push"]:
            z["pushGrund"] = stumm_grund(b) or (
                "Push abgeschaltet (STAKE_BURST_PUSH)" if not PUSH_AN
                else "Deckel des Laufs erreicht" if k not in {burst_key(x) for x in senden}
                else "Senden fehlgeschlagen")
        led.append(z)
        schon.add(k)
        neu_gebucht += 1

    # ── Die zweite Gruppierung: je SPIEL (22.09.2026, Lucas) ────────────────────────────────
    # Eigener Deckel, eigener Dedup-Schluessel, eigene Buchart. Was hier gesendet wird, ist eine
    # ANDERE Beobachtung als oben — die beiden teilen nur die Sperrliste und den Kanal.
    sp_alle = spiel_bursts(wetten, gesperrt=_gesperrt, now=now)
    sp_neu = [b for b in sp_alle if spiel_key(b) not in seen]
    sp_laut = [b for b in sp_neu if not stumm_grund(b)]
    sp_senden = sp_laut[:SPIEL_MAX_PUSH] if (SPIEL_AN and PUSH_AN) else []
    sp_unterdrueckt = max(len(sp_laut) - len(sp_senden), 0)
    print("🎯 Stake-Spiel: %d frische(r) Spiel-Burst(s), %d davon neu (>=%d Wetten je Spiel, "
          "davon >=%d mit Mannschaft, %d Min, ab %s, >=%.1fx Liga-Norm, Quote ab %.2f, "
          "keine Gegenseite)"
          % (len(sp_alle), len(sp_neu), SPIEL_MIN_N, SPIEL_MIN_GERICHTET,
             int(SPIEL_FENSTER_S // 60), _usd(SPIEL_MIN_USD), SPIEL_MIN_FAKTOR, MIN_QUOTE))
    sp_gesendet = 0
    for i, b in enumerate(sp_senden):
        text = build_spiel_card(b, sp_unterdrueckt if i == len(sp_senden) - 1 else 0)
        if not send_trades_message(text):
            continue
        sp_gesendet += 1
        seen[spiel_key(b)] = {"ts": now_iso, "summe": round(float(b["summe"]), 2)}
    # Auch hier: JEDER erkannte kommt ins Buch, auch der ungesendete. Sonst kennt das Protokoll
    # nur die Ueberlebenden — die Fehlerklasse, die hier am 13.09. schon einmal repariert wurde.
    for b in sp_neu:
        k = spiel_key(b)
        if k in schon:
            continue
        z = spiel_buch_zeile(b, now_iso)
        z["push"] = k in seen
        if not z["push"]:
            z["pushGrund"] = stumm_grund(b) or (
                "Push abgeschaltet" if not (PUSH_AN and SPIEL_AN)
                else "Deckel des Laufs erreicht" if k not in {spiel_key(x) for x in sp_senden}
                else "Senden fehlgeschlagen")
        led.append(z)
        schon.add(k)
        neu_gebucht += 1

    # ── Torlinien je Spiel, eine Richtung (07.10.2026, Lucas) ───────────────────────────
    to_alle = tor_bursts(wetten, gesperrt=_gesperrt, now=now)
    to_neu = [b for b in to_alle if tor_key(b) not in seen and tor_key(b) not in schon]
    to_laut = [b for b in to_neu if not stumm_grund(b)]
    to_senden = to_laut[:TOR_MAX_PUSH] if (TOR_AN and PUSH_AN) else []
    to_unterdrueckt = max(len(to_laut) - len(to_senden), 0)
    print("📈 Stake-Tore: %d Torlinien-Burst(s), %d neu (>=%d Wetten je Spiel, eine Richtung, "
          "%d Min, ab %s, >=%.1fx Norm)" % (len(to_alle), len(to_neu), TOR_MIN_N,
                                             int(TOR_FENSTER_S // 60), _usd(TOR_MIN_USD),
                                             TOR_MIN_FAKTOR))
    for i, b in enumerate(to_senden):
        text = build_tor_card(b, bilanz=art_bilanz(led, "tore", _phase(b["wetten"])),
                              unterdrueckt=to_unterdrueckt if i == len(to_senden) - 1 else 0)
        if not send_trades_message(text):
            continue
        seen[tor_key(b)] = {"ts": now_iso, "summe": round(float(b["summe"]), 2)}
    for b in to_neu:                      # jeder erkannte ins Buch, auch der ungesendete
        k = tor_key(b)
        z = tor_buch_zeile(b, now_iso)
        z["push"] = k in seen
        if not z["push"]:
            z["pushGrund"] = stumm_grund(b) or (
                "Push abgeschaltet" if not (PUSH_AN and TOR_AN)
                else "Deckel des Laufs erreicht" if k not in {tor_key(x) for x in to_senden}
                else "Senden fehlgeschlagen")
        led.append(z)
        schon.add(k)
        neu_gebucht += 1

    # ── Die dritte Einheit: kleine Ligen (30.09.2026, Lucas) ─────────────────────────────
    kl_alle = kleine_liga(wetten, norm=liga_norm(), ebene_median=ebenen_median(), now=now)
    kl_neu = [b for b in kl_alle if klein_key(b) not in seen and klein_key(b) not in schon]
    kl_senden = [b for b in kl_neu if not stumm_grund(b)][:KLEIN_MAX_PUSH] if (KLEIN_AN and PUSH_AN) else []
    print("🔎 Kleine Liga: %d Treffer, %d neu (Fussball Ebene %s, vor Anpfiff + live, >=%.1fx Norm, "
          "Quote ab %.2f)" % (len(kl_alle), len(kl_neu), "/".join(KLEIN_EBENEN),
                              KLEIN_MIN_FAKTOR, MIN_QUOTE))
    kl_gesendet = 0
    for b in kl_senden:
        if not send_trades_message(build_klein_card(b, bilanz=klein_bilanz(led))):
            continue
        kl_gesendet += 1
        seen[klein_key(b)] = {"ts": now_iso, "summe": round(float(b["summe"]), 2)}
    for b in kl_neu:                      # jeder erkannte ins Buch, auch der ungesendete
        k = klein_key(b)
        z = klein_buch_zeile(b, now_iso)
        z["push"] = k in seen
        if not z["push"]:
            z["pushGrund"] = stumm_grund(b) or ("Push abgeschaltet" if not (PUSH_AN and KLEIN_AN)
                              else "Deckel des Laufs erreicht" if k not in {klein_key(x) for x in kl_senden}
                              else "Senden fehlgeschlagen")
        led.append(z)
        schon.add(k)
        neu_gebucht += 1
    if kl_gesendet:
        print("   🔎 %d Kleine-Liga-Meldung(en) gesendet" % kl_gesendet)

    _save(SEEN_FILE, seen)
    _verworfen_buchen(beinahe, now)
    # Bei JEDEM Lauf abrechnen — das Fenster der Quelle ist nur ~5 Tage breit (s. oben).
    led, _fertig, _tot = abrechnen(led, _load(WETTEN_FILE, {}).get("wetten") or [], now)
    _offen = sum(1 for e in led if isinstance(e, dict) and e.get("status") == STATUS_OFFEN)
    print("   📒 Buch: +%d abgerechnet · %d offen%s"
          % (_fertig, _offen, (" · %d aufgegeben (Wetten aus dem Ledger gefallen)" % _tot) if _tot else ""))
    try:
        _save(LEDGER_FILE, buch_kuerzen(led))
    except Exception as e:
        print("Stake-Burst-Ledger-Schreibfehler:", e)
    print("   %d Auswahl-Burst(s) + %d Spiel-Burst(s) gesendet, %d/%d unterdrueckt "
          "(Deckel %d/%d)%s · %d neu im Buch."
          % (gesendet, sp_gesendet, unterdrueckt, sp_unterdrueckt, MAX_PUSH, SPIEL_MAX_PUSH,
             "" if PUSH_AN else " — PUSH IST AUS, es wird nur gemessen", neu_gebucht))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
