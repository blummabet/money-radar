#!/usr/bin/env python3
"""
money-radar (17.10.2026): unveränderte Kopie aus blummabet/betting-dashboard (betfair_consensus.py).
Läuft hier in .pin/ alle 5 Min auf GitHub-Servern; Eingaben kommen aus .bf/ und vom alten Repo.

betfair_consensus.py — 09.08.2026 (Lucas): rein LESENDE Zweitmeinung zu jedem Betfair-Signal-Spiel.

Komplett getrennt vom bestehenden betfair_alerts.py / Push — fasst nichts an, sendet nichts. Nimmt die
Betfair-Spiele aus betfair_prices.json, holt zu den gecoverten Ligen die Pinnacle- + Soft-Quoten von
the-odds-api, matcht per Liga + Teamname + Anpfiff, de-viggt zu Wahrscheinlichkeiten, misst die Bewegung
seit dem letzten Lauf und schreibt einen Konsens (Betfair-Geld-Seite vs. Pinnacle vs. Soft) nach
betfair_consensus.json. Nur der neue Radar-Tab liest das.

Der Sinn: „ist das Betfair-Geld schlau?" — wenn Pinnacle die Quote in dieselbe Richtung zieht und die
Soft-Books nachhinken, ist es bestaetigt; ruehrt sich Pinnacle nicht, ist es eher Positionierung/Rauschen.
Obskure Ligen ohne Odds-Anker bleiben ehrlich als „kein Anker" markiert.

Laeuft im betfair.yml nach dem Fetch. Env: ODDS_API_KEY (wie fetch_liga_odds). Netzwerk nur hier — die
reine Match-/De-vig-/Konsens-Logik ist ohne Netz testbar (tests/test_betfair_consensus.py).
"""
from __future__ import annotations
import json
import os
import re
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timezone

PRICES_FILE    = "betfair_prices.json"
DIRECTION_FILE = "betfair_direction.json"
HIST_FILE      = "betfair_consensus_history.json"   # Pinnacle-Prob-Snapshots je Spiel (fuer die Bewegung)
OUT_FILE       = "betfair_consensus.json"
MONEYMAP_FILE  = "money_map.json"          # 11.08.2026 (Lucas): Money-Map-Feed (Bubble-Cards)
MMLEDGER_FILE  = "money_map_ledger.json"   # Konsens-Signale mitschreiben -> Tracking (ab Tag 1)
MMRECORD_FILE  = "money_map_record.json"   # Trefferquote je Verdikt-Typ (Tracking-Tab)
MM_RESULTS_MIN_H = 3.0                      # Anpfiff so lange her -> Spiel sicher vorbei -> abrechenbar
MM_SINGLE_MIN    = float(os.environ.get("MM_SINGLE_MIN_USD") or 150000)   # 12.08.2026 (Lucas): nur EINE Geldquelle (Betfair ODER Poly) -> braucht so viel Geld, sonst raus. Betfair+Poly = immer rein. Pinnacle zaehlt NICHT (nur Odds-Anker).
MM_STRONG_PCT    = float(os.environ.get("MM_STRONG_PCT") or 55)   # 13.08.2026 (Lucas-Audit): "starke" Konsens/Divergenz erst, wenn BEIDE Geld-Seiten >= so viel % Mehrheit haben (54/46 = Muenzwurf).
try:
    from fetch_betfair_betwatch import fetch_results as _fetch_results  # autoritative Endstaende (Runner)
except Exception:
    _fetch_results = None
# 🔴 21.09.2026 (Lucas: „meine api keys sind abgelaufen"). Hier stand der Schluessel als
# Rueckfall im Klartext. Lief das Secret leer oder ab, griff still der fest verdrahtete — und
# der war seit dem 20.09. abends TOT. Sichtbar war davon nichts: 40 Abrufe je Lauf wie immer,
# nur `ankerQuote` fiel von 0,50 auf 0,0, und das fiel erst einen Tag spaeter auf.
# Fehlerklasse: fehlende Information rendert als harmloser Default — hier war der Default
# zusaetzlich ein totes Zugangsdatum.
# Kein Schluessel heisst ab jetzt: kein Abruf, und es steht im Artefakt.
ODDS_KEY       = (os.environ.get("ODDS_API_KEY") or "").strip()
ODDS_BASE      = "https://api.the-odds-api.com/v4"
REGIONS        = "eu,uk,us"     # Pinnacle liegt in eu; Soft-Books ueber uk/us breiter erfasst
HIST_KEEP      = 24             # letzte n Snapshots je Spiel behalten
# 29.08.2026: 8 Snapshots × ~15 Min = ein Zwei-Stunden-Fenster, und bei Doppelläufen lagen
# zwei davon vier Minuten auseinander. Pinnacle schärft sich über Stunden, nicht über Minuten.
# Gleiche Lösung wie in poly_price_path.py: Mindestabstand statt jeden Lauf mitschreiben.
# 24 × ≥20 Min ≈ 8 Stunden Fenster bei kleinerer Datei als vorher pro Stunde.
SNAP_MIN_ABSTAND_MIN = 20      # näher beieinander liegende Snapshots nicht mitschreiben
MATCH_MIN      = 0.60          # Namens-Match-Schwelle (beide Teams muessen teilen)
# 09.08.2026 (Lucas): EXAKT dieselben Spiele wie die Betfair-Radar-Liste (betfair-radar.js qualifies()):
# tier-basiert — groesster FT-Markt >= 20K (Top/Int) / 15K (Rest) ODER groesster HT-Markt >= 10K / 5K.
# Betwatch liefert schon EUR (EURFX=1), distTotal = Summe der Runner-Vols.
_MK_FT = ("Match Odds", "Over/Under 2.5 Goals", "Over/Under 3.5 Goals", "Both teams to Score?")
_MK_HT = ("Half Time", "First Half Goals 0.5", "First Half Goals 1.5")
# 28.09.2026: „German Bundesliga 2", „English Premier League 2 - Div 1" (U21) und „US MLS Next Pro
# League" liefen als Top 5 durch — die Suchbegriffe trafen die zweite Liga mit. 537 Signale im
# Buch, 4 gesendete Pushes. Negativer Lookahead statt Liste; tests/test_top5_eine_regel.py haelt
# alle Kopien (Python + Radar) gleich.
_TOP5_RX  = re.compile(r"(german bundesliga(?!\s*2)|english premier league(?!\s*2)|spanish la ?liga(?!\s*2)|italian serie a|french ligue 1|\bmls\b(?!\s*next)|major league soccer)", re.I)
_TOP5_NEG = re.compile(r"(summer series|friendl|reserve|women|u1[0-9]\b|youth|amateur)", re.I)
_UEFA_RX  = re.compile(r"(champions league|europa league|europa conference|conference league|uefa)", re.I)

# ── Betfair-Liga-Name -> the-odds-api sport_key ──────────────────────────────
# Aus dem 09.08.2026-Abgleich (Betfair-Feed × the-odds-api /sports). Nur wo eine aktive Odds-Liga
# existiert; alles andere -> kein Anker. Weitere Ligen greifen automatisch, sobald ihr exakter
# Betfair-Ligastring hier steht (Rest ist nur Nachschlagen — falscher/fehlender Key = schadet nicht).
LEAGUE_ODDS_KEY = {
    # aus dem echten Feed bestaetigt:
    "Brazilian Serie A":            "soccer_brazil_campeonato",
    "Brazilian Serie B":            "soccer_brazil_serie_b",
    "Portuguese Primeira Liga":     "soccer_portugal_primeira_liga",
    "Argentinian Primera Division": "soccer_argentina_primera_division",
    "Austrian Bundesliga":          "soccer_austria_bundesliga",
    "Chilean Primera Division":     "soccer_chile_campeonato",
    "Belgian Pro League":           "soccer_belgium_first_div",
    "Finnish Veikkausliiga":        "soccer_finland_veikkausliiga",
    "Italian Coppa Italia":         "soccer_italy_coppa_italia",
    "Norwegian Eliteserien":        "soccer_norway_eliteserien",
    "Polish Ekstraklasa":           "soccer_poland_ekstraklasa",
    "German 3 Liga":                "soccer_germany_liga3",
    "Danish Superliga":             "soccer_denmark_superliga",
    "English Football League Cup":  "soccer_england_efl_cup",
    "Irish Premier Division":       "soccer_league_of_ireland",
    "Scottish Premiership":         "soccer_spl",
    # Top-5 + MLS (Betfair-Strings aus den Alerts bestaetigt):
    "English Premier League":       "soccer_epl",
    "Spanish La Liga":              "soccer_spain_la_liga",
    "German Bundesliga":            "soccer_germany_bundesliga",
    "Italian Serie A":              "soccer_italy_serie_a",
    "French Ligue 1":               "soccer_france_ligue_one",
    # 🔴 01.09.2026 (Lucas: „Pinnacle haben wir zu tausend Prozent der Spiele"): hatten wir NICHT —
    # fuer MLS ueberhaupt nicht. Der Eintrag stand seit jeher da, aber der Betfair-Feed schreibt die
    # Liga „US MLS", nicht „Major League Soccer". 105 MLS-Zeilen im Ledger, davon 0 mit Pinnacle-
    # Anker. Wieder „eingebaut, feuert aber nie" — diesmal an einem Schreibweisen-Unterschied.
    # Beide Schreibweisen bleiben stehen: ein Key, der nie trifft, schadet nicht, ein fehlender schon.
    "US MLS":                       "soccer_usa_mls",
    "Major League Soccer":          "soccer_usa_mls",
    # 13.08.2026 (Lucas): internationale Klub-Wettbewerbe (im Feed aktiv). Falscher/fehlender Key schadet
    # nicht (dann bleibt der Anker leer). UEFA-Gruppenphase kann hier genauso ergaenzt werden, sobald die
    # Betfair-Ligastrings feststehen (soccer_uefa_champs_league / _europa_league / _europa_conference_league).
    "CONMEBOL Copa Libertadores":   "soccer_conmebol_copa_libertadores",
    "CONMEBOL Copa Sudamericana":   "soccer_conmebol_copa_sudamericana",
    # 14.08.2026 (Lucas): grosse Ligen, die Betfair global zeigt, aber uns fehlten. TheOddsAPI-Keys
    # gegen die-odds-api-Doku verifiziert; Betfair-Strings aus dem echten Feed. In Saison ab ~August.
    "Turkish Super League":         "soccer_turkey_super_league",
    "Dutch Eredivisie":             "soccer_netherlands_eredivisie",
    "English Sky Bet Championship": "soccer_efl_champ",
    "German Bundesliga 2":          "soccer_germany_bundesliga2",
    "Swedish Allsvenskan":          "soccer_sweden_allsvenskan",
    "Saudi Professional League":    "soccer_saudi_arabia_pro_league",
    # 🔴 25.09.2026, zweiter Lauf des Anker-Abgleichs (`odds_anker_luecke.py`).
    #
    # WARUM das ueberhaupt noch etwas bringt, obwohl seit 01.09. alle aktiven Wettbewerbe geholt
    # werden: der globale Pool ist ein zweiter Anlauf fuer die MATCH ODDS, nicht fuer die Totals.
    # Gemessen am 25.09.: von 38 Ligen mit Anker kamen 37 aus dem Pool und nur 1 aus dieser
    # Liste — aber `tev = match_event(m, totals_by_key.get(k, [])) if k else None`, und
    # `totals_by_key` wird ausdruecklich nur fuer die kuratierten Keys gefuellt (Laufzeit, nicht
    # Quota). Ohne Eintrag hier gibt es also einen Pinnacle-Anker fuer 1X2, aber NIE einen fuer
    # die Torlinien. Diese Liste ist damit nicht mehr das Tor zum Anker, sondern das Tor zu den
    # Totals — und Over/Under 2.5 und 3.5 sind zusammen die groessten Schubladen im Ledger.
    #
    # Geprueft mit den echten 253 Ligastrings des Ledgers; jede Zeile zusaetzlich von Hand gegen
    # die Spielklasse des Landes gehalten. Preis: 13 x 8.640 = 112.320 Credits/Monat, zusammen
    # 532.090 von 5.000.000 (10,6 %) — und 13 weitere Totals-Calls je Lauf gegen ODDS_GESAMT_S.
    "Japanese J League":            "soccer_japan_j_league",            # n=350
    "Spanish Segunda Division":     "soccer_spain_segunda_division",    # n=269
    "Swedish Superettan":           "soccer_sweden_superettan",         # n=242
    "Mexican Liga MX":              "soccer_mexico_ligamx",             # n=235
    "Italian Serie B":              "soccer_italy_serie_b",             # n=228
    "French Ligue 2":               "soccer_france_ligue_two",          # n=218
    "South Korean K1 League":       "soccer_korea_kleague1",            # n=210
    "Greek Super League":           "soccer_greece_super_league",       # n=176
    "Swiss Super League":           "soccer_switzerland_superleague",   # n=165
    "Chinese Super League":         "soccer_china_superleague",         # n=133, bei /sports inaktiv
    "German Frauen-Bundesliga":     "soccer_germany_bundesliga_women",  # n=88,  bei /sports inaktiv
    # Diese zwei hat das Skript ABGELEHNT, und zwar richtig: unser Feed schreibt den Sponsor mit
    # („Sky Bet"), die Gegenseite nicht, und dass ein Sponsorname nichts bedeutet und „Nacional"
    # alles, weiss kein Textvergleich. Hier entscheidet Fussballwissen: Sky Bet League One/Two
    # sind League One/Two.
    "English Sky Bet League 1":     "soccer_england_league1",           # n=285
    "English Sky Bet League 2":     "soccer_england_league2",           # n=277
}


# ── Automatische Ligen-Entdeckung (01.09.2026) ───────────────────────────────
# Lucas: „ich akzeptier keine andere Meinung, da wir extra die OddsAPI mit fuenf Millionen API-Calls
# zur Verfuegung haben. Da koennen wir so viel Pinnacle abfragen, bis die Tuer nicht mehr zugeht."
#
# Er hat recht, und die Messung gab ihm recht: MORGEN haben 1 von 57 Betfair-Spielen einen
# Pinnacle-Anker (2%). Ueber den ganzen Ledger kommt Pinnacle in 23 von 212 Ligen an — 9% der
# Zeilen. Der Grund war NIE die Quota, sondern die Handliste `LEAGUE_ODDS_KEY` oben: 30 Ligen
# gegen 229 Ligastrings im Feed. Was nicht drinstand, bekam gar keinen Anker.
#
# Ab jetzt: /sports einmal je Lauf abfragen (dieser Endpunkt kostet KEIN Kontingent) und ALLE
# aktiven Fussball-Wettbewerbe holen. Die Handliste bleibt — sie ist die schnelle, sichere Zuordnung
# Liga->Key. Neu ist der zweite Anlauf: findet die Handliste nichts, wird im GLOBALEN Pool gesucht.
#
# ⚠️ Der globale Pool braucht eine Anpfiff-Schranke. `match_event` vergleicht nur Teamnamen; ueber
# 70 Wettbewerbe hinweg trifft dasselbe Klubpaar sonst auch in Pokal, Liga und Reserve gleichzeitig.
# Mit +-ANPFIFF_FENSTER_H ist die Verwechslung praktisch ausgeschlossen — zwei Klubs spielen nicht
# zweimal in derselben Stunde. Ohne lesbare Anpfiffzeit auf BEIDEN Seiten gilt der Treffer NICHT:
# fehlende Information ist keine Erlaubnis, auch beim Zuordnen nicht.
ANKER_FILE       = "betfair_anker.json"   # Zweitmeinungen OHNE die Radar-Volumenschwelle
SPORTS_FILE      = "odds_sports.json"     # Abzug von /sports, damit der Lauf nicht bei Netzfehler blind wird
ANPFIFF_FENSTER_H = 2.0                   # Schranke fuer den globalen Pool
MAX_ODDS_KEYS    = int(os.environ.get("BF_MAX_ODDS_KEYS") or 90)   # Laufzeit-Deckel, nicht Quota-Deckel
# ⚠️ 02.09.2026 — der Deckel allein reicht NICHT. Der Job hat `timeout-minutes: 8` und laeuft alle
# 10 Minuten; 90 Fetches nacheinander mit je 20s Socket-Timeout sind im schlechtesten Fall 30
# Minuten. Dann killt GitHub den Job mitten im Konsens, und ALLES danach (Track-Record, Konjunktion,
# Freigabe, Commit) laeuft nie — derselbe stille Totalausfall wie beim `git add`, nur mit anderer
# Ursache. Deshalb zusaetzlich eine WANDUHR-Schranke: die kuratierten Ligen stehen in `hol` vorne
# und werden immer geholt, die entdeckten Extras nur, solange das Budget reicht.
ODDS_BUDGET_S    = float(os.environ.get("BF_ODDS_BUDGET_S") or 100)  # Wanduhr fuer die Entdeckungs-Extras
ODDS_TIMEOUT_S   = float(os.environ.get("BF_ODDS_TIMEOUT_S") or 8)   # je Call; 20s war fuer 90 Calls zu lang
ODDS_GESAMT_S    = float(os.environ.get("BF_ODDS_GESAMT_S") or 240)  # harte Schranke fuer die GANZE Netzphase


def aktive_fussball_keys(sports, max_keys=MAX_ODDS_KEYS):
    """/sports-Antwort -> Liste aktiver Fussball-sport_keys. REIN/testbar.

    Gefiltert auf `group == "Soccer"` und `active`. Ausserdem raus: Outright-/Winner-Maerkte
    (`has_outrights`), die keine Einzelspiele fuehren und nur Calls kosten."""
    out = []
    for x in (sports or []):
        if not isinstance(x, dict):
            continue
        if str(x.get("group") or "").lower() != "soccer":
            continue
        if not x.get("active") or x.get("has_outrights"):
            continue
        k = x.get("key")
        if isinstance(k, str) and k:
            out.append(k)
    return sorted(set(out))[:max_keys]


def _stunden(a, b):
    """|a-b| in Stunden, None wenn eine Seite fehlt/unlesbar. REIN."""
    if not a or not b:
        return None
    try:
        ta = datetime.fromisoformat(str(a).replace("Z", "+00:00"))
        tb = datetime.fromisoformat(str(b).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if ta.tzinfo is None:
        ta = ta.replace(tzinfo=timezone.utc)
    if tb.tzinfo is None:
        tb = tb.replace(tzinfo=timezone.utc)
    return abs((ta - tb).total_seconds()) / 3600.0


# ── I/O ──────────────────────────────────────────────────────────────────────
def _load(path, default):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return default


def _dump(path, obj):
    try:
        json.dump(obj, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    except Exception as e:
        print("Schreibfehler %s: %s" % (path, e))


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


# ── Namens-Normalisierung + Match (die riskante, darum voll getestete Stelle) ──
# NUR klare Rechtsform-/Verbindungs-Token — KEINE unterscheidenden Woerter (sonst kollabieren
# „Manchester United" und „Manchester City" beide auf „manchester" -> Fehl-Match). Der Liga- +
# Anpfiff-Filter faengt den Rest ab.
_NOISE = {
    "fc", "cf", "sc", "afc", "cd", "ac", "if", "fk", "fs", "bk", "ff", "sk", "ud",
    "cfc", "sad", "bsc", "club", "de", "the", "calcio", "aj", "ogc", "rcd", "ssc",
}


# 13.08.2026 (Lucas-Audit): Abkuerzungs-Aliasse — Betfair kuerzt Klubs anders ab als Poly ("Man Utd"
# <-> "Manchester United", "Wolves" <-> "Wolverhampton"). Ohne diese Expansion teilen sie 0 Tokens und
# der ganze Poly-Eintrag fiel still raus. NUR eindeutige, KEINE unterscheidenden Tokens (united/city bleiben
# getrennt, weil sie separat gemappt/erhalten werden).
#
# 06.09.2026 (Lucas: „Angers v Rennes — wieso da kein Poly?"): Poly fuehrt Vereine mit ihrem vollen
# historischen Namen. „Rennes" gegen „Stade Rennais FC 1901" teilt NULL Tokens — der Rueckfall
# verlangt mindestens eines und verweigerte zu Recht, denn ohne diese Bedingung paart er
# irgendwann falsch. Ein niedrigerer Schwellwert waere die falsche Antwort; die richtige ist,
# dass „rennais" und „rennes" dasselbe Wort sind. Deshalb Alias statt Schwelle.
_ALIAS = {
    "utd": "united", "man": "manchester", "wolves": "wolverhampton",
    "spurs": "tottenham", "sheff": "sheffield", "nottm": "nottingham",
    "wba": "westbrom", "qpr": "queensparkrangers",
    # Vereinsnamen, die Poly voll ausschreibt und Betfair kurz fuehrt (06.09.2026):
    "rennais": "rennes", "monchengladbach": "gladbach", "moenchengladbach": "gladbach",
    "internazionale": "inter", "napoli": "napoli", "sassuolo": "sassuolo",
    "hoffenheim": "hoffenheim", "leverkusen": "leverkusen",
    "atletico": "atletico", "athletic": "athletic",
    # 26.09.2026 (Lucas: „da stimmt fix was mit dem Namensabgleich nicht"). Laendernamen, die
    # Betfair anders schreibt als die-odds-api. „Czechia" gegen „Czech Republic" teilt NULL Tokens
    # — der Abgleich verlangt aber je Team mindestens eines, also fiel Czechia v Croatia still
    # durch. Nur Schreibweisen desselben Landes, keine Kuerzel, die zwei Laender treffen koennten.
    "czechia": "czech", "turkiye": "turkey", "holland": "netherlands",
    "herzegovina": "bosnia",
}


def _norm(s) -> list:
    """Team-Name -> Liste signifikanter Tokens (klein, akzentfrei, ohne Rausch-Tokens/Zahlen)."""
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    return [_ALIAS.get(t, t) for t in s.split() if t and t not in _NOISE and not t.isdigit()]


def _name_score(a, b) -> float:
    """0..1: geteilte signifikante Tokens / groessere Tokenmenge. 0 = kein gemeinsames Kern-Token."""
    A, B = set(_norm(a)), set(_norm(b))
    if not A or not B:
        return 0.0
    inter = A & B
    if not inter:
        return 0.0
    return len(inter) / max(len(A), len(B))


def _median(xs):
    """Ausreisser-fester Konsens: Median einer Zahlenliste (leere/ungueltige Liste -> None)."""
    ys = sorted(v for v in xs if isinstance(v, (int, float)))
    n = len(ys)
    if n == 0:
        return None
    mid = n // 2
    return ys[mid] if n % 2 else (ys[mid - 1] + ys[mid]) / 2.0


def _devig3(h, d, a):
    """Drei-Weg-Quoten (dezimal) -> de-viggte Wahrscheinlichkeiten [heim, remis, auswaerts]."""
    try:
        inv = [1.0 / float(h), 1.0 / float(d), 1.0 / float(a)]
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    s = sum(inv)
    if s <= 0:
        return None
    return [x / s for x in inv]


def _devig2(over, under):
    """Zwei-Weg-Quoten (Over/Under, dezimal) -> de-viggte [pOver, pUnder]."""
    try:
        io, iu = 1.0 / float(over), 1.0 / float(under)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    tot = io + iu
    if tot <= 0:
        return None
    return [io / tot, iu / tot]


def _fmt_line(pt):
    """Totals-Punkt (2.5) -> Label-Schluessel passend zu Betfair ('2.5'). 3.0 -> '3'."""
    try:
        return ("%g" % float(pt))
    except (TypeError, ValueError):
        return None


def parse_event(ev) -> dict:
    """the-odds-api-Event -> Probs (de-viggt) UND rohe Dezimal-Quoten je Quelle, [heim, remis, auswaerts]."""
    home, away = ev.get("home_team"), ev.get("away_team")
    pinn, pinn_odds, soft_p, soft_o = None, None, [], []
    # 27.09.2026 (Lucas: „Nations League muss funktionieren, da gibt es zu 100 % Quoten"): welche
    # Buecher im Event stehen, war bisher unsichtbar — „kein_pinnacle" konnte heissen „Pinnacle
    # fehlt" ODER „Pinnacle da, aber verworfen". Jetzt steht es im Artefakt.
    buecher = sorted({str(bk.get("key")) for bk in (ev.get("bookmakers") or []) if bk.get("key")})
    pinn_roh = None
    for bk in ev.get("bookmakers") or []:
        h = d = a = None
        rest = []
        for mkt in bk.get("markets") or []:
            if mkt.get("key") != "h2h":
                continue
            for oc in mkt.get("outcomes") or []:
                nm, pr = oc.get("name"), oc.get("price")
                if nm == home:
                    h = pr
                elif nm == away:
                    a = pr
                elif nm and str(nm).lower() == "draw":
                    d = pr
                else:
                    rest.append((nm, pr))
        # 27.09.2026: schreibt ein Buch die Teams anders als das Event („Ireland" statt „Republic
        # of Ireland"), fiel es bisher STILL heraus — bei Pinnacle hiess das: kein Anker. Genau
        # eine fehlende Seite + genau ein unbekannter Name + Remis da: ueber den Namensabgleich
        # zuordnen, aber nur wenn der Name wirklich zu DIESEM Team passt und nicht zum anderen.
        if d and len(rest) == 1 and (h is None) != (a is None):
            nm, pr = rest[0]
            fehlt, anderes = (home, away) if h is None else (away, home)
            if _name_score(nm, fehlt) > 0 and _name_score(nm, fehlt) > _name_score(nm, anderes):
                if h is None:
                    h = pr
                else:
                    a = pr
        if bk.get("key") == "pinnacle" and not (h and d and a):
            pinn_roh = [str(x[0]) for x in rest] or None
        probs = _devig3(h, d, a) if (h and d and a) else None
        if not probs:
            continue
        odds = [float(h), float(d), float(a)]
        if bk.get("key") == "pinnacle":
            pinn, pinn_odds = probs, odds
        else:
            soft_p.append(probs)
            soft_o.append(odds)
    soft = [sum(x[i] for x in soft_p) / len(soft_p) for i in range(3)] if soft_p else None
    # 11.08.2026 (Lucas): Median statt Mittelwert der rohen Dezimalquoten -- bei Aussenseitern zieht ein
    # einzelnes Buch mit irrer (oft live-traeger) Quote den arithmetischen Schnitt hoch und laesst die
    # Anzeige von Scan zu Scan springen (11 -> 34). Der Median ist ausreisser-fest.
    soft_odds = [_median([x[i] for x in soft_o]) for i in range(3)] if soft_o else None
    out = {"home": home, "away": away, "commence": ev.get("commence_time"),
           "pinn": pinn, "soft": soft, "pinnOdds": pinn_odds, "softOdds": soft_odds, "nSoft": len(soft_p),
           "buecher": buecher}
    if pinn_roh:
        out["pinnRoh"] = pinn_roh     # Pinnacle stand im Event, liess sich aber nicht zuordnen
    return out


def parse_totals(ev) -> dict:
    """the-odds-api-Event (regions=eu, markets=totals+alternate_totals) -> Pinnacle-Over/Under je Linie,
    de-viggt zu fairen Wahrscheinlichkeiten. Nur Pinnacle (der Anker). Rueckgabe wie parse_event
    (home/away/commence) plus 'totals': {lineStr: {overFair,underFair,overOdd,underOdd}}."""
    home, away = ev.get("home_team"), ev.get("away_team")
    raw = {}
    for bk in ev.get("bookmakers") or []:
        if bk.get("key") != "pinnacle":
            continue
        for mkt in bk.get("markets") or []:
            if mkt.get("key") not in ("totals", "alternate_totals"):
                continue
            for oc in mkt.get("outcomes") or []:
                pt, nm, pr = oc.get("point"), oc.get("name"), oc.get("price")
                key = _fmt_line(pt)
                if key is None or nm is None or not pr:
                    continue
                slot = raw.setdefault(key, {})
                side = str(nm).lower()
                if side.startswith("over"):
                    slot["overOdd"] = float(pr)
                elif side.startswith("under"):
                    slot["underOdd"] = float(pr)
        break
    totals = {}
    for key, v in raw.items():
        o, u = v.get("overOdd"), v.get("underOdd")
        fair = _devig2(o, u)
        if not fair:
            continue
        totals[key] = {"overFair": _r(fair[0]), "underFair": _r(fair[1]),
                       "overOdd": round(o, 2), "underOdd": round(u, 2)}
    return {"home": home, "away": away, "commence": ev.get("commence_time"), "totals": totals}


def _flip(ev) -> dict:
    """Event mit vertauschten Teams (falls Heim/Auswaerts zwischen den Quellen gedreht sind)."""
    def rev(p):
        return [p[2], p[1], p[0]] if p else None
    return {"home": ev["away"], "away": ev["home"], "commence": ev.get("commence"),
            "pinn": rev(ev.get("pinn")), "soft": rev(ev.get("soft")),
            "pinnOdds": rev(ev.get("pinnOdds")), "softOdds": rev(ev.get("softOdds")), "nSoft": ev.get("nSoft"),
            "totals": ev.get("totals"), "key": ev.get("key"), "buecher": ev.get("buecher"),
            "pinnRoh": ev.get("pinnRoh")}


# ── Polymarket (globaler Broad-Scan: poly_money_broad_close.json) ─────────────
_POLY_NON_TEAM = {"yes", "no", "draw", "the draw", "tie", "over", "under"}


def _ist_nicht_team(k) -> bool:
    """Ist dieser Ausgangs-Key KEIN Teamname?

    05.09.2026 (Uebersicht-Check, Brighton v Leeds): die Menge oben wurde exakt verglichen —
    Polymarket schreibt den dritten Ausgang eines 1X2-Markts aber als
    „Draw (Brighton & Hove Albion FC vs. Leeds United FC)". Gemessen im Bestand:
    **543 von 565 1X2-Maerkten (96 %)** hatten damit einen Draw-Key, der als TEAMNAME galt.

    Folge: `team_keys` hatte drei statt zwei Eintraege, und der Abkuerzungs-Rueckfall vom
    12.08. („Paris St-G" vs „Paris Saint-Germain") konnte fuer praktisch JEDES Fussballspiel
    nie greifen — ein Rueckfall, der seit dem Tag seiner Einfuehrung tot war. Brighton fiel
    genau darueber aus der Money Map, obwohl $439.712 Poly-Geld im Markt lagen und dieselbe
    Uebersicht sie zwei Kacheln weiter anzeigte.
    """
    t = str(k or "").strip().lower()
    if t in _POLY_NON_TEAM:
        return True
    # „Draw (A vs. B)" / „The Draw (…)" — Klammerzusatz gehoert nicht zum Ausgang.
    return t.startswith("draw (") or t.startswith("the draw (")


def _poly_key_for(name, keys):
    """Bester Preis-Schluessel (Team-Name) fuer ein Team; None wenn nichts ordentlich matcht."""
    best, bsc = None, 0.49
    for k in keys:
        if _ist_nicht_team(k):
            continue
        sc = _name_score(name, k)
        if sc > bsc:
            best, bsc = k, sc
    return best


# Annahmeschwelle fuer den Abkuerzungs-Rueckfall (Summe beider Namens-Scores). Gemessen:
# "Brighton"+"Leeds" gegen die offiziellen Namen ergibt 0,83; ein zufaelliges Fremdpaar, bei dem
# eine Seite stark matcht und die andere nur ein Allerwelts-Token teilt, liegt darunter.
RUECKFALL_MIN_SUMME = 0.60


# ── 07.09.2026 (Uebersicht-Check, Al-Ahed v Al Ahli Akhaa Aley) ──────────────
# Die Money Map zeigte fuer ein LIBANESISCHES POKALSPIEL „Poly $267.964, Konsens 3/3" — das
# Geld stammte aus `spl-hil-ahl-2026-09-01`, Al Hilal gegen Al Ahli (Saudi Pro League), einem
# Markt, der **sechs Tage vorher abgerechnet** war. Zwei Luecken auf einmal:
#
#   1. Der Kandidaten-Pool enthaelt jeden abgerechneten Snapshot. Der Close-Feed haelt sie
#      absichtlich 30 Tage lang (fuer die Treffer-Auswertung, siehe poly_money_broad.py) —
#      gemessen sind das 2.494 von 2.557 Eintraegen. Jeder Join sucht also gegen ~2.500 fertige
#      Spiele, und nur der Namens-Score steht dazwischen.
#   2. Der Abkuerzungs-Rueckfall verlangt „mindestens EIN gemeinsames Token". Hier war es „al" —
#      und „al" ist kein Name, sondern ein Artikel.
#
# Zur zweiten Luecke gehoert eine Fehldiagnose von mir, die hier stehen bleibt, damit sie
# niemand wiederholt: ich hatte „al" mit 14,6 % der Pool-Eintraege gemessen und daraus eine
# Haeufigkeitsregel gebaut. Falsch gezaehlt — die 14,6 % kamen daher, dass ich dasselbe Token
# in den DREI Ausgaengen desselben Marktes dreimal zaehlte. Pro EINTRAG steht „al" in 34 von
# 2.557 (1,3 %), und eine Schwelle, die 1,3 % faengt, faengt auch echte Vereinsnamen.
#
# Was bleibt: die Haeufigkeitsregel raeumt die Struktur-Woerter ab (draw, vs, over, under,
# score — gemessen 7 Tokens ueber 5 %), und die Laengen-Regel faengt Artikel wie „al". Der
# eigentliche Fix gegen den Fall von heute ist Nummer 1, nicht Nummer 2.
_ALLERWELTS_ANTEIL = 0.05          # Token in >=5 % der Pool-EINTRAEGE = nicht unterscheidend
_KERN_MIN_LEN = 3                  # „al", „fc", „sc" sind keine Namen
_ALLERWELTS_CACHE = {}


def allerwelts_tokens(poly_entries, anteil: float = _ALLERWELTS_ANTEIL) -> set:
    """Tokens, die im Kandidaten-Pool so haeufig sind, dass sie nichts unterscheiden. REIN."""
    entries = list(poly_entries or [])
    if not entries:
        return set()
    zaehler = {}
    for pe in entries:
        gesehen = set()
        for name in (pe.get("prices") or {}):
            gesehen.update(_norm(name))
        for t in gesehen:
            zaehler[t] = zaehler.get(t, 0) + 1
    grenze = max(2, int(len(entries) * anteil))
    return {t for t, n in zaehler.items() if n >= grenze}


# 🔴 29.09.2026 (Stoerungsmeldung: „Poly-Markt enl-har-ht-2026-09-29 haengt an 3 Betfair-Spielen").
# Hartlepool v Harrogate Town lag auch an „FC Halifax Town v Boston Utd" und „Slough Town v
# Ebbsfleet Utd". Geteilt wurde je Seite genau EIN Wort — „town" und „united" — das gibt 0,5 + 0,5
# = 1,0 und liegt ueber der Direkt-Schwelle 0,99. Die Allerwelts-Liste faengt das nicht: sie zaehlt
# Haeufigkeit im Poly-POOL, und dort sind englische Fuenftligisten selten. Gattungswoerter sind
# unabhaengig vom Pool keine Namen. Fehlerklasse: *ein Treffer, der nur auf Gattungswoertern steht.*
_GATTUNG = frozenset({
    # NUR Woerter, die nie allein einen Verein bezeichnen. Bewusst NICHT hier: inter, sporting,
    # athletic, racing, nacional, union — „Inter Milan", „Sporting CP", „Athletic Club" und
    # „Racing Club" teilen mit der Betfair-Schreibweise oft NUR dieses Wort.
    "town", "united", "city", "county", "rovers", "wanderers", "albion", "borough",
    "real", "atletico", "deportivo", "club", "sport", "sports",
    "fk", "fc", "sc", "afc", "women", "ladies", "reserves", "youth",
})


def _unterscheidet(a, b, allerwelts) -> bool:
    """Teilen zwei Namen ein Wort, das sie als DIESEN Verein ausweist? REIN.

    Kein Allerwelts-Token des Pools, kein Gattungswort. Bewusst OHNE Mindestlaenge: „AZ" gegen
    „AZ Alkmaar" teilt nur „az" — Fuellwoerter („de", „fc") nimmt `_norm` schon heraus.
    """
    geteilt = (set(_norm(a)) & set(_norm(b))) - set(allerwelts or ()) - _GATTUNG
    return bool(geteilt)


def _teilt_kern_token(a, b, allerwelts) -> bool:
    """Teilen sich zwei Namen ein Token, das ein NAME sein kann — lang genug und im Pool
    nicht Allerwelt? REIN."""
    geteilt = (set(_norm(a)) & set(_norm(b))) - set(allerwelts or ())
    return any(len(t) >= _KERN_MIN_LEN for t in geteilt)


# ── 08.09.2026 (Uebersicht-Check, Real Madrid U19) ───────────────────────────
# `betfair_anker.json` fuehrte fuer „Real Madrid U19 v Inter U19" (UEFA Youth League) den
# Poly-Markt `ucl-rma-int-2026-09-08` mit $294.571 — das ist das Geld des SENIOREN-Spiels
# Real Madrid v Inter am selben Abend. Dasselbe fuenfmal: Porto U19 bekam Man Citys Markt
# (der damit an ZWEI Betfair-Spielen gleichzeitig hing), Club Brugge U19 den von Aston Villa,
# Goztepe U19 $339.991 und Alanyaspor U19 $120.566 aus den Erwachsenen-Ligen.
#
# Ursache ist der Namens-Score, nicht der Pool: „Real Madrid U19" gegen „Real Madrid CF"
# ergibt 2/3 = 0,67 (das „CF" faellt als Rausch-Token weg, das „U19" bleibt in der groesseren
# Menge stehen), „Inter U19" gegen „Inter Milan" 1/2 = 0,50 — Summe 1,17 ueber der Schwelle
# von 0,99. Der Score kann das gar nicht anders sehen: er zaehlt geteilte Tokens, und die
# Nachwuchsmannschaft teilt fast alle.
#
# ⭐ Ein Altersmarker ist kein Rauschen, er ist die Identitaet: „Real Madrid U19" ist ein
# anderes Team als „Real Madrid", nicht dasselbe Team mit einem Zusatz. Also wird nicht am
# Schwellwert gedreht — die Marker muessen auf BEIDEN Seiten uebereinstimmen, und eine
# fehlende Angabe zaehlt als „Erste Mannschaft", weil genau so beide Feeds sie fuehren.
_ELF_MARKER_RX = re.compile(
    r"^(u\d{2}|ii|iii|b|w|women|womens|ladies|frauen|feminin[eo]|femenino|femminile|"
    r"reserve|reserves|youth|jugend|junior|juniors|academy|jong)$")


def elf_marker(name) -> frozenset:
    """Alters-/Mannschaftsmarker eines Teamnamens („U19", „II", „W") als Menge. REIN.

    Leere Menge = erste Herrenmannschaft. Bewusst auf dem ROHEN Namen, nicht auf `_norm`:
    dort faellt genau die Kurzform weg, um die es geht.
    """
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    return frozenset(t for t in s.split() if _ELF_MARKER_RX.match(t))


def gleiche_elf(a, b) -> bool:
    """Beschreiben zwei Namen dieselbe Mannschaftsebene? REIN.

    „Real Madrid U19" vs „Real Madrid CF" -> False. „Real Madrid" vs „Real Madrid CF" -> True.
    """
    return elf_marker(a) == elf_marker(b)


def abgerechnet_vor_anpfiff(pe, kickoff) -> bool:
    """Wurde dieser Poly-Markt abgerechnet, BEVOR dieses Spiel angepfiffen wurde? REIN.

    Dann kann er dieses Spiel nicht meinen — unabhaengig davon, wie gut die Namen passen.
    Ohne beide Zeitstempel wird NICHTS behauptet (und nichts verworfen): ein fehlender
    Stempel ist kein Beleg, und ein stiller Ausschluss waere hier so schaedlich wie der
    falsche Join.
    """
    if not pe.get("resolved"):
        return False
    ra, ko = _ts(pe.get("resolvedAt")), _ts(kickoff)
    if ra is None or ko is None:
        return False
    return ra < ko


def _best_poly_entry(m, poly_entries):
    """Bester Poly-Eintrag zum Betfair-Spiel -> (pe, hk, ak) oder None. Beide Teams muessen matchen."""
    home, away = m.get("home"), m.get("away")
    kickoff = m.get("kickoff")
    _cache_key = id(poly_entries)
    _allerwelts = _ALLERWELTS_CACHE.get(_cache_key)
    if _allerwelts is None:
        _allerwelts = _ALLERWELTS_CACHE[_cache_key] = allerwelts_tokens(poly_entries)
    # 05.09.2026: `bsc` war Bestenliste UND Annahmeschwelle in einer Variable (Start 0.99).
    # Damit galt die strenge Direkt-Schwelle auch fuer den Abkuerzungs-Rueckfall, der genau
    # dafuer gebaut wurde, sie zu unterlaufen — er konnte nie zum Zug kommen. Jetzt getrennt:
    # `bsc` rangiert nur noch, angenommen wird gegen `schwelle`.
    best, bsc = None, 0.0
    for pe in poly_entries:
        if abgerechnet_vor_anpfiff(pe, kickoff):
            continue          # fertiges Spiel von vorgestern kann dieses hier nicht sein
        keys = list((pe.get("prices") or {}).keys())
        hk, ak = _poly_key_for(home, keys), _poly_key_for(away, keys)
        # 12.08.2026 (Lucas): eine Seite matcht stark, die andere ist bei Poly anders abgekuerzt
        # (Betfair "Paris St-G" vs Poly "Paris Saint-Germain" -> nur 0.33, unter der 0.49-Schwelle).
        # Bei genau 2 Team-Ausgaengen die fehlende Seite als den Gegner ableiten -- aber nur, wenn sie
        # mindestens EIN Token teilt (schuetzt vor Falsch-Paarung, wenn nur ein Team zufaellig gleich heisst).
        team_keys = [k for k in keys if not _ist_nicht_team(k)]
        rueckfall = False
        if len(team_keys) == 2:
            # 07.09.2026: „mindestens ein gemeinsames Token" reicht nicht — es muss ein
            # UNTERSCHEIDENDES sein. „Al-Ahed" und „Al Hilal Saudi Club" teilen „al", und
            # „al" steht in 14,6 % des Pools.
            if hk and not ak:
                cand = next((k for k in team_keys if k != hk), None)
                if cand and gleiche_elf(away, cand) and _teilt_kern_token(away, cand, _allerwelts):
                    ak, rueckfall = cand, True
            elif ak and not hk:
                cand = next((k for k in team_keys if k != ak), None)
                if cand and gleiche_elf(home, cand) and _teilt_kern_token(home, cand, _allerwelts):
                    hk, rueckfall = cand, True
        if hk and ak and hk != ak:
            # 08.09.2026: erst die Ebene, dann der Score. Ein Nachwuchs- oder Frauenteam teilt
            # fast alle Tokens mit der ersten Mannschaft — der Score kann die beiden nie trennen.
            if not (gleiche_elf(home, hk) and gleiche_elf(away, ak)):
                continue
            # 29.09.2026: beide Seiten brauchen ein unterscheidendes Wort (s. _GATTUNG).
            if not (_unterscheidet(home, hk, _allerwelts) and _unterscheidet(away, ak, _allerwelts)):
                continue
            sc = _name_score(home, hk) + _name_score(away, ak)
            # 05.09.2026 (Uebersicht-Check): der Rueckfall oben setzte die Schluessel korrekt,
            # und DIESE Zeile warf sie danach wieder weg. `_name_score` misst geteilte Tokens
            # geteilt durch die groessere Menge — „Brighton" gegen „Brighton & Hove Albion FC"
            # ergibt 0,33, „Leeds" gegen „Leeds United FC" 0,50. Summe 0,83, Schwelle 0,99:
            # abgelehnt. Der Rueckfall war damit doppelt tot (die Draw-Erkennung davor auch),
            # und Brighton v Leeds fehlte in der Money Map, obwohl $439.712 Poly-Geld im Markt
            # lagen und dieselbe Uebersicht sie zwei Kacheln weiter zeigte.
            #
            # Wo der Rueckfall greift, ist das Paar bereits durch den MARKT festgenagelt: genau
            # zwei Team-Ausgaenge, eine Seite ueber der Einzelschwelle, die andere teilt ein
            # Token. Dann traegt eine niedrigere Annahmeschwelle. Direkte Treffer bleiben streng.
            schwelle = RUECKFALL_MIN_SUMME if rueckfall else 0.99
            if sc > schwelle and sc > bsc:
                best, bsc = (pe, hk, ak), sc
    return best


def match_poly(m, ms, poly_entries):
    """Poly-Markt zum Betfair-Spiel: {vol, odd (fuer die Geld-Seite, None wenn Poly die Seite nicht hat),
    sharePct}. None wenn Poly den Markt nicht hat. Beide Teams muessen matchen."""
    best = _best_poly_entry(m, poly_entries)
    if not best:
        return None
    pe, hk, ak = best
    prices, shares = pe.get("prices") or {}, pe.get("shares") or {}
    side = ms.get("side") if ms else None
    if side == "home":
        key = hk
    elif side == "away":
        key = ak
    else:
        key = next((k for k in prices if str(k).lower() in ("draw", "the draw", "tie")), None)
    price = prices.get(key) if key else None
    tot = sum(v for v in shares.values() if isinstance(v, (int, float))) if shares else 0
    # 06.09.2026 (Lucas: „wieso da kein Poly?") — hier stand nur der Geld-Anteil aus `shares`, und
    # Shares traegt AUSSCHLIESSLICH der close-Pool (~3h-Freeze). Alles von upcoming/liga/scan bekam
    # `sharePct = None`, `killer.py` las daraus `polyStatus = "unbekannt"` und das Board zeigte ❔ —
    # obwohl wir Markt UND Preis hatten. `money_map_row()` faellt genau hier seit dem 12.08. auf den
    # PREIS zurueck („auf Poly = geldgewichtete Wahrscheinlichkeit"); `match_poly` tat es nie.
    # Zwei Leser desselben Pools, einer mit Rueckfall, einer ohne — daher „Money Map: Konsens 3/3"
    # neben „Punktestand: POLY ❔" fuer dasselbe Spiel.
    #
    # Preis-Anteil ist NICHT Geld-Anteil: `shareSrc` sagt, welcher von beiden es ist, damit niemand
    # den einen als den anderen ausgibt.
    share_pct, share_src = None, None
    if key and tot and isinstance(shares.get(key), (int, float)):
        share_pct, share_src = round(shares.get(key) / tot * 100), "geld"
    elif key and isinstance(price, (int, float)) and 0.0 < price < 1.0:
        share_pct, share_src = round(price * 100), "preis"
    # 01.09.2026 (Lucas: „Poly Top Wallet drauf"): die Wale des Poly-Marktes bis in die Konsens-Zeile
    # durchreichen — GEFILTERT auf unsere Seite, damit der Buecher-Score sie bewerten kann, ohne den
    # Markt ein zweites Mal zu joinen. Wer auf der GEGENSEITE steht, ist kein Verstaerker und faellt
    # hier raus; die Zeile traegt nur, was fuer die eigene Seite spricht.
    _w = [w for w in (pe.get("whales") or []) if isinstance(w, dict)]
    _unsere = [w for w in _w if key and str(w.get("side")) == str(key)] if key else []
    return {"vol": round(pe.get("totalUsd") or 0),
            "odd": round(1.0 / price, 2) if (isinstance(price, (int, float)) and price > 0) else None,
            "sharePct": share_pct,
            "shareSrc": share_src,
            "key": pe.get("key"),
            # 06.09.2026: der Poly-OUTCOME-Name unserer Seite („CR Flamengo"), so wie ihn der
            # Markt schreibt. Er steht hier, damit die Oberflaeche „dasselbe Spiel" von
            # „dieselbe Seite" unterscheiden kann, OHNE Namen zu raten: die Poly-Shortlist
            # fuehrt exakt diese Zeichenkette als `side`. Ohne ihn muesste das Frontend
            # „Flamengo" gegen „CR Flamengo" halten — Produzenten-Logik im Renderer.
            "sideKey": key,
            # None (= nicht erhoben) und [] (= erhoben, keine Wale auf unserer Seite) sind
            # verschiedene Dinge. Ohne diese Trennung wuerde der Score ein fehlendes Holder-Fenster
            # als „kein Wal dafuer" lesen — derselbe Fehler wie das `or 0` bei sharePct.
            "whales": ([{"wallet": w.get("wallet"), "usd": w.get("usd")} for w in _unsere[:6]]
                       if _w else None),
            "whaleUsd": (round(sum(w.get("usd") or 0 for w in _unsere)) if _w else None)}



def pick_poly(m, ms, is_live, poly_close, poly_live, poly_upcoming, poly_liga=()):
    """Poly-Quelle je Phase (18.08.2026, Lucas): LAUFENDES Spiel -> frische Live-Poly ZUERST (sonst
    zeigt der Terminal auf einem Live-Spiel die eingefrorene Pre-Match-Quote aus dem Close-Pool).
    Nicht-live: Close (<=3h Freeze, mit Holder-Shares) zuerst, dann Upcoming (weit vor Anpfiff, Preis+Vol).
    Rein additiv/reine Auswahl-Logik, ohne Netz testbar."""
    poly = match_poly(m, ms, poly_live) if is_live else None
    if poly is None:
        poly = match_poly(m, ms, poly_close)
    if poly is None:
        poly = match_poly(m, ms, poly_upcoming)
    if poly is None:
        # 06.09.2026: letzte Preis-Quelle aus dem Liga-Fetcher. Reiner Preis, kein Geld —
        # deshalb ganz hinten und als `src="liga"` erkennbar.
        poly = match_poly(m, ms, poly_liga)
    return poly

def anker_grund(m, ev, alle_events, max_h=ANPFIFF_FENSTER_H):
    """Warum hat dieses Spiel keinen Pinnacle-Anker? None, wenn es einen hat. REIN.

    🔴 26.09.2026 (Lucas: „geh na klar hat the odds api diese Spiele — da stimmt fix was mit dem
    Namensabgleich nicht"). North Macedonia v Switzerland, Iceland v Estonia, Albania v Belarus,
    Czechia v Croatia: „Pinnacle nicht erhoben", obwohl der Wettbewerb abgerufen wurde und andere
    Spiele daraus einen Anker hatten. Ob das Event fehlte, der Name nicht passte, die Anpfiffzeit
    abwich oder Pinnacle im Event keine Quote hatte, sagte das Artefakt nicht — alle vier Faelle
    sahen gleich aus. Fehlerklasse: *ein Fehlschlag ohne Grund laesst sich nur raten.*

    Gruende:
      kein_pinnacle  Event gefunden, aber ohne Pinnacle-Quote darin
      zeit           Namen passen, Anpfiff weicht mehr als `max_h` Stunden ab
      name           ein Kandidat teilt Tokens, aber nicht genug (oder nur ein Team)
      kein_kandidat  kein Event mit auch nur einem gemeinsamen Namens-Token
    """
    spiel = "%s v %s" % (m.get("home"), m.get("away"))
    if ev is not None:
        if ev.get("pinn"):
            return None
        out = {"spiel": spiel, "grund": "kein_pinnacle", "kandidat": "%s v %s" % (ev.get("home"), ev.get("away")),
               "key": ev.get("key"), "nSoft": ev.get("nSoft")}
        # 27.09.2026: der Beweis, welcher der Faelle es ist — Pinnacle gar nicht im Event, oder da
        # und nicht zuordenbar (dann mit den Namen, die Pinnacle schreibt).
        if ev.get("buecher") is not None:
            out["pinnacleImEvent"] = "pinnacle" in (ev.get("buecher") or [])
            out["nBuecher"] = len(ev.get("buecher") or [])
        if ev.get("pinnRoh"):
            out["pinnRoh"] = ev.get("pinnRoh")
        return out
    home, away = m.get("home"), m.get("away")
    best, best_sc, best_both, best_seite = None, 0.0, False, 0.0
    for e in (alle_events or []):
        hh, aa = _name_score(home, e.get("home")), _name_score(away, e.get("away"))
        ha, ah = _name_score(home, e.get("away")), _name_score(away, e.get("home"))
        sc, both = max((hh + aa, hh > 0 and aa > 0), (ha + ah, ha > 0 and ah > 0))
        if sc > best_sc:
            best, best_sc, best_both, best_seite = e, sc, both, max(hh, aa, ha, ah)
    if best is None:
        return {"spiel": spiel, "grund": "kein_kandidat"}
    # 🔴 30.09.2026 (Stoerungsmeldung): „Roots FC v Sporting Club Bengaluru (Bangalore Super
    # Division League): name — bester Kandidat „Braga v Sporting Lisbon"". Gemeinsam war EIN Wort
    # auf EINER Seite (Score 0,5). Das ist kein Kandidat, das ist ein Allerweltswort — und der
    # Waechter meldete es als Namensfehler, den wir beheben koennten. Der Fall, fuer den `name`
    # gebaut wurde (Czechia v Croatia gegen Czech Republic v Croatia), hatte eine Seite VOLL
    # getroffen (1,0); „Bosnia" gegen „Bosnia and Herzegovina" ist einseitig 0,5 und trotzdem
    # echt — der Unterschied ist der ANPFIFF: eine Namensluecke ist nur plausibel, wenn die Zeit
    # passt. Braga v Sporting lag Stunden daneben. Einseitig, unter der Match-Schwelle UND zu
    # anderer Zeit heisst: kein Kandidat.
    # 01.10.2026 (Stoerungsmeldung): „Wolfsburg v TSV Havelse (Friendly Matches): name — bester
    # Kandidat „Jahn Regensburg v TSV Havelse"". Hier traf eine Seite VOLL (1,0) — der Kandidat
    # war aber Havelses Drittliga-Spiel, nicht das Testspiel. Ein Verein spielt nie zwei Spiele
    # gleichzeitig: trifft nur EINE Seite, entscheidet der Anpfiff, egal wie gut der Name passt.
    if not best_both:
        _d = _stunden(m.get("kickoff"), best.get("commence"))
        if _d is None or _d > max_h:
            return {"spiel": spiel, "grund": "kein_kandidat",
                    "naechster": "%s v %s" % (best.get("home"), best.get("away"))}
    info = {"spiel": spiel, "kandidat": "%s v %s" % (best.get("home"), best.get("away")), "key": best.get("key"),
            "score": round(best_sc, 2)}
    if best_both and best_sc >= MATCH_MIN:
        d = _stunden(m.get("kickoff"), best.get("commence"))
        if d is None or d > max_h:
            return dict(info, grund="zeit", abstandH=(round(d, 1) if d is not None else None))
        # Sollte nicht vorkommen: dann haette match_event getroffen. Ehrlich melden statt raten.
        return dict(info, grund="unerklaert")
    return dict(info, grund="name")


def match_event(m, evs, max_h=None):
    """Bestes Odds-Event zum Betfair-Spiel, orientiert auf dessen Heim/Auswaerts. None wenn kein Match.

    `max_h` setzt eine Anpfiff-Schranke (fuer den globalen Pool, s. Kopf): liegen die Anpfiffzeiten
    weiter auseinander, zaehlt der Namenstreffer nicht. Fehlt eine der beiden Zeiten, zaehlt er
    ebenfalls nicht — ein Treffer, den wir nicht pruefen koennen, ist keiner."""
    home, away = m.get("home"), m.get("away")
    best, best_sc = None, MATCH_MIN
    for ev in evs:
        d = _name_score(home, ev["home"]) + _name_score(away, ev["away"])
        s = _name_score(home, ev["away"]) + _name_score(away, ev["home"])
        if d >= s and _name_score(home, ev["home"]) > 0 and _name_score(away, ev["away"]) > 0:
            sc, cand = d, ev
        elif s > d and _name_score(home, ev["away"]) > 0 and _name_score(away, ev["home"]) > 0:
            sc, cand = s, _flip(ev)
        else:
            continue
        if max_h is not None:
            d = _stunden(m.get("kickoff"), ev.get("commence"))
            if d is None or d > max_h:
                continue
        if sc > best_sc:
            best, best_sc = cand, sc
    return best


def pinn_nachtragen(m, ev, evs, max_h=None):
    """Hat das gefundene Event keine Pinnacle-Quote, aber ein ZWEITES Event zum selben Spiel schon,
    wird Pinnacle von dort uebernommen. REIN.

    🔴 27.09.2026 (Lucas: „Nations League muss funktionieren, egal an welcher Stelle — da gibt es
    zu 100 % Quoten"). Austria v Kosovo, Gibraltar v Andorra, Israel v Republic of Ireland,
    Northern Ireland v Hungary, Scotland v Switzerland: 24 von 24 Laeufen ohne Pinnacle, waehrend
    Norway und Denmark aus demselben Wettbewerb 24 von 24 hatten. `match_event` nimmt EIN Event —
    bei Gleichstand das erste. Fuehrt die API dasselbe Spiel zweimal (ein Buch schreibt Namen
    oder Anstoss anders), stand Pinnacle im zweiten, und das wurde nie angesehen.

    Dieselben Regeln wie `match_event` (beide Teams, Schwelle, Anpfiff-Fenster) — nur dass hier
    unter den Kandidaten MIT Pinnacle gesucht wird. Die Soft-Seite bleibt vom ersten Event.
    """
    if ev is None or ev.get("pinn"):
        return ev
    mit = [e for e in (evs or []) if e.get("pinn")]
    if not mit:
        return ev
    fenster = max_h if max_h is not None else ANPFIFF_FENSTER_H
    zweit = match_event(m, mit, max_h=fenster)
    if zweit is None or not zweit.get("pinn"):
        return ev
    out = dict(ev)
    out["pinn"], out["pinnOdds"] = zweit.get("pinn"), zweit.get("pinnOdds")
    out["pinnQuelle"] = "%s v %s" % (zweit.get("home"), zweit.get("away"))
    return out


# ── Betfair-Geld-Seite + Richtung ────────────────────────────────────────────
def _is_top_tier(m) -> bool:
    lg = str(m.get("league") or "")
    if _TOP5_RX.search(lg) and not _TOP5_NEG.search(lg):
        return True
    cc = str(m.get("country") or "")
    return bool(_UEFA_RX.search(lg)) or bool(re.match(r"^(int|international|eu|europe)$", cc, re.I))


def qualifies_radar(m) -> bool:
    """EXAKT die Radar-Listen-Schwelle (betfair-radar.js qualifies): groesster FT-Markt >= tier-Schwelle
    ODER groesster HT-Markt >= tier-Schwelle. So zeigt der Konsens genau dieselben Spiele wie die Liste."""
    mk = m.get("markets") or {}

    def vol(name):
        return sum((r.get("vol") or 0.0) for r in ((mk.get(name) or {}).get("runners") or []))

    top = _is_top_tier(m)
    ft_thr, ht_thr = (20000.0, 10000.0) if top else (15000.0, 5000.0)
    ft_max = max([vol(n) for n in _MK_FT] or [0.0])
    ht_max = max([vol(n) for n in _MK_HT] or [0.0])
    return ft_max >= ft_thr or ht_max >= ht_thr


def money_side(m):
    """Seite mit dem meisten gematchten 1X2-Geld: {side home/draw/away, name, share, odd, totVol}."""
    mk = (m.get("markets") or {}).get("Match Odds")
    rs = (mk or {}).get("runners") or []
    if not rs:
        return None
    tot = sum((r.get("vol") or 0.0) for r in rs) or 1.0
    lead = max(rs, key=lambda r: (r.get("vol") or 0.0))
    name = lead.get("name")
    # 22.08.2026 (Lucas: „Kohle sieht aus wie Home, liegt aber 90% auf Alkmaar"): Betfair liefert den
    # Ausgangsnamen oft anders formatiert als den Teamnamen ("Az Alkmaar" vs "AZ Alkmaar") -> exaktes
    # == schlug fehl und fiel in den else-Zweig -> falsche Seite "home". Jetzt Fuzzy-Match (_name_score)
    # gegen BEIDE Teams, stärkere Seite gewinnt (Draw separat).
    nm = str(name or "")
    if nm.strip().lower() in ("the draw", "draw", "tie"):
        side = "draw"
    else:
        sh = _name_score(nm, m.get("home") or "")
        sa = _name_score(nm, m.get("away") or "")
        side = "away" if sa > sh else "home"
    return {"side": side, "name": name, "share": (lead.get("vol") or 0.0) / tot,
            "odd": lead.get("odd"), "totVol": tot}


def _money_dir(direction, m, ms):
    """Back/Lay-Richtung der Geld-Seite aus betfair_direction.json (in=Back, out=driftet)."""
    if not (direction and ms):
        return None
    try:
        e = ((direction.get(str(m.get("matchId"))) or {}).get("Match Odds") or {}).get(ms.get("name"))
        return (e or {}).get("dir")
    except Exception:
        return None


def _fav(probs):
    i = max(range(3), key=lambda k: probs[k])
    return ("home", "draw", "away")[i]


def _r(x):
    return round(float(x), 4) if isinstance(x, (int, float)) else None


def poly_fav(m, poly_entries):
    """11.08.2026 (Lucas Money-Map): Polys EIGENE Geld-Favoriten-Seite (nicht die Betfair-Seite -> fuer
    Divergenz-Erkennung): {side, name, sharePct, usd} oder None. REIN/testbar."""
    best = _best_poly_entry(m, poly_entries)
    if not best:
        return None
    pe, hk, ak = best
    prices, shares = pe.get("prices") or {}, pe.get("shares") or {}
    dk = next((k for k in prices if str(k).lower() in ("draw", "the draw", "tie")), None)
    cand = [("home", hk), ("away", ak)] + ([("draw", dk)] if dk else [])
    tot = sum(v for v in shares.values() if isinstance(v, (int, float))) or 0
    if tot > 0:
        bside, bsh, bname = None, -1, None
        for side, key in cand:
            sh = shares.get(key) if key else None
            if isinstance(sh, (int, float)) and sh > bsh:
                bside, bsh, bname = side, sh, key
        if bside is None:
            return None
        return {"side": bside, "name": bname, "sharePct": round(bsh / tot * 100),
                "usd": round(pe.get("totalUsd") or 0), "src": pe.get("src") or "close"}
    # 12.08.2026 (Lucas Money-Map): keine Shares (upcoming-Erfassung, weit vor Anpfiff) -> Favorit aus dem
    # PREIS (auf Poly = geldgewichtete Wahrscheinlichkeit). sharePct = Preis*100, usd = Markt-Volumen.
    bside, bp, bname = None, -1.0, None
    for side, key in cand:
        p = prices.get(key) if key else None
        if isinstance(p, (int, float)) and p > bp:
            bside, bp, bname = side, p, key
    if bside is None or bp <= 0:
        return None
    return {"side": bside, "name": bname, "sharePct": round(bp * 100),
            "usd": round(pe.get("totalUsd") or 0), "src": pe.get("src") or "upcoming"}


def _poly_has_any_overlap(m, pools) -> bool:
    """13.08.2026 (Lucas-Audit): Liegt im Poly-Pool ueberhaupt ein Kandidat, der mit Heim ODER Auswaerts
    mind. ein Token teilt? Fuer den Namens-Match-Miss-Zaehler: Betfair-Geld da, poly=None, aber ein
    plausibler Poly-Markt existiert -> wahrscheinlich eine (Rest-)Abkuerzungs-Luecke. REIN."""
    home, away = m.get("home"), m.get("away")
    for pe in (pools or []):
        for k in ((pe.get("prices") or {}).keys()):
            if _ist_nicht_team(k):
                continue
            if _name_score(home, k) > 0 or _name_score(away, k) > 0:
                return True
    return False


def _poly_ist_geld(pl) -> bool:
    """Zaehlt diese Poly-Seite als GELDQUELLE — oder ist sie nur ein Preis?

    30.08.2026 (Lucas-Checkup der Uebersicht): _mm_money_ok schliesst src=="scan" seit dem
    23.08. bewusst aus (ein Scan-Poly liefert nur den fairen Preis, kein echtes Geld) und das
    Frontend beschriftet ihn als „Poly · Preis (duenn)". nSources, mmStrong und der
    no_anchor-Rueckfall haben diese Entscheidung nie mitbekommen und zaehlten ihn voll mit.
    Folge auf der Uebersicht: Chelsea–Brighton stand als „✅ knapp einig · 3 / 3" da — die
    dritte Quelle waren $1.410 neben €328.000 Betfair-Geld. Napoli–Como genauso mit $1.787.
    Eine Bestaetigung, die aus einem einzelnen Ticket besteht, ist keine.

    EINE Definition, hier. _mm_money_ok ruft sie ebenfalls auf."""
    if not pl:
        return False
    if pl.get("src") == "scan":
        return False
    try:
        return float(pl.get("usd") or 0) > 0
    except (TypeError, ValueError):
        return False


# 03.09.2026 (Lucas: „Was fehlt dann noch von Poly bei dem Betis - Real Madrid Beispiel?").
# Antwort: das Geld, komplett. Die Zeile kam aus dem pinnacle_poly_scan (src="scan"), der nur ein
# Preis-Tripel und ein `vol` liefert — `shares` ist dort per Konstruktion leer. Fuer
# Betis–Real Madrid stand da `vol: 74.0`. Vierundsiebzig Dollar. Der Geld-Scan hatte den Markt
# nicht, weil er zwei Bedingungen verfehlt: Anpfiff in ~36h (Fenster: 3h) und $74 Volumen
# (Schwelle: $7.500). Beides voellig normal so frueh — der Markt fuellt sich noch.
#
# Was dabei auffiel und der eigentliche Grund fuer diese Funktion ist: der Poly-PREIS lag bei
# 47,5¢ fuer Real Madrid, waehrend Pinnacle 69% und Betfair 92% Geldanteil bei Quote 1,41 (~71%)
# sagten. Gut zwanzig Punkte daneben. Das ist keine abweichende Meinung, das ist ein Markt, den
# niemand angefasst hat — der Eroeffnungskurs steht noch da. Und die „Zustimmung" bestand darin,
# dass 47,5¢ (Real) knapp ueber 45¢ (Betis) lag: zweieinhalb Cent Abstand bei $74 Umsatz.
# Rauschen, das zufaellig in dieselbe Richtung zeigt.
#
# Ein solcher Preis darf die Poly-Seite nicht so fuellen, als lehnte sich dort jemand an. Er
# bleibt sichtbar (die Karte zeigt ihn weiter als „Preis (duenn)"), traegt aber ab jetzt seine
# Abweichung mit, damit die Oberflaeche ihn kennzeichnen kann statt ihn als Zustimmung zu lesen.
POLY_PREIS_MAX_ABW_PP = 15.0   # Scan-Preis weiter als das vom Anker weg = keine Anlehnung


def poly_preis_abweichung(pl, pinn):
    """Wie weit liegt ein reiner Scan-PREIS vom Pinnacle-Anker? → Abweichung in Prozentpunkten
    oder None. REIN/testbar.

    None heisst „nicht pruefbar", nicht „in Ordnung": ohne Anker oder ohne Preis gibt es keine
    Abweichung zu messen, und der Aufrufer entscheidet, was er daraus macht. Fuer eine Poly-Seite
    aus echtem Geld (src != "scan") ist die Frage gegenstandslos — dort misst sharePct Geld, nicht
    einen Preis, und die beiden sind nicht vergleichbar.
    """
    if not pl or not pinn or pl.get("src") != "scan":
        return None
    pct = pl.get("sharePct")
    anker = pinn.get(pl.get("side"))
    if not isinstance(pct, (int, float)) or not isinstance(anker, (int, float)):
        return None
    return round(abs(float(pct) - float(anker) * 100.0), 1)


def money_map_row(g, pf):
    """11.08.2026 (Lucas Money-Map): build_game-Output g + poly_fav pf -> bubble-fertige Zeile.
    Betfair-Geld (EUR) + Poly-Geld (USD, eigene Seite) + Pinnacle-Probs + Verdikt + nSources. REIN/testbar."""
    bf_side = g.get("moneySide")
    pinn = g.get("pinn")
    row = {"matchId": g.get("matchId"), "home": g.get("home"), "away": g.get("away"),
           "league": g.get("league"), "live": g.get("live"), "kickoff": g.get("kickoff"),
           "verdict": g.get("verdict"),
           # 01.09.2026 (Lucas: „was macht das besser als die Money Map?"). Beim Vergleich fiel auf:
           # die Map schrieb `moneyWin` mit, aber NIE eine Quote. Ihre 81% Trefferquote bei „stark"
           # sagen deshalb nichts ueber Geld — das Geld liegt auf Favoriten, eine hohe Trefferquote
           # ist dort der Normalfall. Ohne Preis ist die Map nicht widerlegbar, und was nicht
           # widerlegbar ist, belegt auch nichts. `moneyOdd` gab es in build_game() bereits, sie kam
           # nur nie hier an.
           "betfair": ({"side": bf_side, "name": g.get("moneyName"),
                        "sharePct": g.get("moneySharePct"), "eur": g.get("totVol"),
                        "odd": g.get("moneyOdd")} if bf_side else None),
           "poly": ({"side": pf["side"], "name": pf["name"], "sharePct": pf["sharePct"], "usd": pf["usd"],
                     "src": pf.get("src")}
                    if pf else None),
           "pinn": ({"fav": pinn.get("fav"), "home": pinn.get("home"), "draw": pinn.get("draw"),
                     "away": pinn.get("away")} if pinn else None)}
    # Money-Map-Verdikt auch OHNE Pinnacle/Soft-Anker (11.08.2026, Lucas): die Map lebt vom Geld,
    # Pinnacle ist nur Anker. Fehlt der Anker (z.B. UEFA Super Cup / Pokal - nicht in den 22 Odds-Ligen),
    # aber Betfair UND Poly liegen vor, dann Konsens/Divergenz aus Betfair-Seite vs Poly-Seite ableiten
    # (2/3, "ehrlich"). Nur Betfair allein bleibt no_anchor (nichts zum Vergleichen).
    _pl_geld = _poly_ist_geld(row["poly"])
    if row["verdict"] == "no_anchor" and row["betfair"] and _pl_geld:
        row["verdict"] = "konsens" if row["betfair"]["side"] == row["poly"]["side"] else "uneinig"
    # 13.08.2026 (Lucas-Audit): Magnitude. "stark" nur, wenn BEIDE Geld-Seiten eine klare Mehrheit
    # (>= MM_STRONG_PCT) zeigen -> das Frontend daempft schwache 54/46-Faelle (kein Signal).
    _bfm = (row.get("betfair") or {}).get("sharePct") or 0
    _plm = (row.get("poly") or {}).get("sharePct") or 0
    row["mmStrong"] = bool(row.get("betfair") and _pl_geld and _bfm >= MM_STRONG_PCT and _plm >= MM_STRONG_PCT)
    # 30.08.2026: ein reiner Scan-Preis fuellt die Poly-SEITE (er bleibt sichtbar), zaehlt aber
    # nicht als Quelle. `polyGeld` sagt dem Frontend, warum aus 3 eine 2 wurde.
    row["polyGeld"] = _pl_geld
    row["nSources"] = sum(1 for x in (row["betfair"], (row["poly"] if _pl_geld else None), row["pinn"]) if x)
    # 03.09.2026: Abweichung eines reinen Scan-Preises vom Anker mitschreiben (s. oben).
    _abw = poly_preis_abweichung(row["poly"], row["pinn"])
    if _abw is not None:
        row["polyPreisAbwPP"] = _abw
        row["polyPreisWeit"] = _abw > POLY_PREIS_MAX_ABW_PP
    return row


def _mm_money_ok(row, single_min=MM_SINGLE_MIN):
    """12.08.2026 (Lucas): die Money-Map ist ein VERGLEICH -> sie braucht echtes Geld auf zwei
    Seiten. Geldquellen sind nur Betfair (EUR) + Poly (USD); Pinnacle ist bloss der Odds-Anker,
    zaehlt hier NICHT. Regel: Betfair UND Poly -> immer rein. Nur eine Quelle -> nur wenn >= single_min
    (Marquee-Spiel wie ein UEFA-Super-Cup). Gar kein Geld -> raus. Killt duenne Einzelquellen-Spiele
    (U19/Minnows), ohne gute Divergenz-Faelle mit kleinem Betfair aber echtem Poly zu opfern. REIN."""
    bf = float((row.get("betfair") or {}).get("eur") or 0)
    _pl = row.get("poly") or {}
    pl = float(_pl.get("usd") or 0)
    # 23.08.2026 (Lucas): ein Scan-Poly (nur fairer Preis, ~kein echtes Geld) füllt die Poly-SEITE,
    # zählt aber NICHT als Geldquelle — sonst würde ein dünner Preis einen schwachen Betfair-Row retten.
    pl_money = pl if _poly_ist_geld(_pl) else 0.0
    n_money = (1 if bf > 0 else 0) + (1 if pl_money > 0 else 0)
    if n_money >= 2:
        return True
    if n_money == 1:
        return max(bf, pl) >= single_min
    return False


def _mm_ts(v):
    try:
        t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def mm_vor_anpfiff(e) -> bool:
    """Wurde die Zeile zuletzt VOR Anpfiff geschrieben? Nur dann ist ihr Urteil ein Vorab-Urteil. REIN."""
    ko, up = _mm_ts((e or {}).get("kickoff")), _mm_ts((e or {}).get("updatedAt"))
    return ko is not None and up is not None and up <= ko


def update_mm_ledger(prev, rows, now=None, keep=2000):
    """11.08.2026 (Lucas Money-Map Tracking): Konsens-Signale mitschreiben (upsert je matchId, solange
    pending) -> Basis fuers spaetere Settlement (gewann die Konsens-Seite?). REIN/testbar.

    🔴 03.10.2026 (Poly-Gesamtschau): die Money-Map-Bilanz zeigte „Konsens" mit 89 % Treffern bei
    einer mittleren Quote von 1,62 (63 % Preis) VOR Anpfiff, ROI +49 %. Das gibt es im Fussball
    nicht. Ursache: die Zeile wurde bis zum Abpfiff weiter geschrieben — Urteil, Geld-Seite und
    `moneyOddLast` kamen aus dem LAUFENDEN Spiel (391 von 402 Zeilen zuletzt nach Anpfiff
    geschrieben), und live fliesst das Geld zu dem, der fuehrt. Die Bilanz kannte den Ausgang.
    Fehlerklasse: eine Vorab-Behauptung, die nach dem Ereignis weiter fortgeschrieben wird.
    Ab Anpfiff ist die Zeile eingefroren; eine Zeile, die erst nach Anpfiff auftaucht, entsteht
    gar nicht."""
    now = now or _now_iso()
    _jetzt = _mm_ts(now)
    led = {}
    for e in (prev or []):
        if isinstance(e, dict) and e.get("matchId"):
            led[e["matchId"]] = dict(e)
    for r in rows or []:
        mid = r.get("matchId")
        if not mid or r.get("verdict") in (None, "no_anchor"):
            continue
        e = led.get(mid)
        if e and e.get("status") not in (None, "pending"):
            continue                              # schon abgerechnet -> nicht ueberschreiben
        _ko = _mm_ts(r.get("kickoff") or (e or {}).get("kickoff"))
        if _ko is not None and _jetzt is not None and _jetzt >= _ko:
            continue                              # ab Anpfiff eingefroren (s. Docstring)
        bf, pinn, poly = r.get("betfair") or {}, r.get("pinn") or {}, r.get("poly") or {}
        # ZWEI Preise, und zwar bewusst:
        #   moneyOddFirst — die Quote, als die Zeile ZUERST auftauchte. Nur die haette man nehmen
        #                   koennen; sie ist die ehrliche Basis fuers ROI (wie killer.haltePreis).
        #   moneyOddLast  — die zuletzt gesehene Quote vor Anpfiff. Erst sie macht CLV moeglich,
        #                   und CLV ist bei kleinem n belastbarer als ROI.
        # Die erste wird NIE ueberschrieben, die zweite bei jedem Lauf.
        _odd = bf.get("odd")
        _first = (e or {}).get("moneyOddFirst")
        _alte_seite = (e or {}).get("moneySide")
        if _alte_seite is not None and _alte_seite != bf.get("side"):
            _first = None                          # Seite gewechselt -> das ist eine ANDERE Wette,
                                                   # der alte Einstiegspreis gilt dafuer nicht
        led[mid] = {"matchId": mid, "home": r.get("home"), "away": r.get("away"), "league": r.get("league"),
                    "kickoff": r.get("kickoff"), "verdict": r.get("verdict"), "nSources": r.get("nSources"),
                    "moneySide": bf.get("side"), "moneyName": bf.get("name"),
                    "moneyOddFirst": _first if _first is not None else _odd,
                    "moneyOddLast": _odd if _odd is not None else (e or {}).get("moneyOddLast"),
                    "pinnFav": pinn.get("fav"), "polySide": poly.get("side"), "mmStrong": r.get("mmStrong"),
                    "firstSeen": (e or {}).get("firstSeen") or now, "updatedAt": now, "status": "pending"}
    return list(led.values())[-keep:]


def _winner_1x2(g1, g2):
    """1X2-Gewinner aus dem Endstand [heim, ausw] -> home/draw/away oder None."""
    if not isinstance(g1, int) or not isinstance(g2, int):
        return None
    return "home" if g1 > g2 else "away" if g2 > g1 else "draw"


def _feed_finished_map(prices):
    """13.08.2026 (Lucas): matchId -> [goal_v1, goal_v2] fuer im Live-Feed als 'finished' markierte Spiele.
    Autoritativer Endstand aus betfair_prices.json — DIESELBE Quelle, aus der der Track-Record abrechnet.
    Kein Netz. Greift, solange das Spiel noch im Betwatch-Fenster (~26h) steht."""
    out = {}
    for m in ((prices or {}).get("matches") or []):
        if not isinstance(m, dict):
            continue
        li = m.get("liveInfo") or {}
        if li.get("finished") and li.get("goal_v1") is not None and li.get("goal_v2") is not None:
            out[str(m.get("matchId"))] = [li.get("goal_v1"), li.get("goal_v2")]
    return out


def settle_mm_ledger(ledger, results_fetch=None, now=None, min_h=None, prices=None):
    """11.08.2026 (Lucas Money-Map Tracking): pending-Konsens-Eintraege gegen den echten Endstand
    abrechnen. Gewann die Betfair-Geld-Seite (moneySide) -> status won/lost; zusaetzlich pinnWin.
    13.08.2026 (Lucas "tracking funktioniert nicht"): ZWEI Quellen wie der Track-Record —
      1) Live-Feed 'finished' (betfair_prices.json, KEIN Netz) — greift, solange das Spiel im Fenster ist.
      2) fetch_results (autoritativer Endpoint) fuer aus dem Feed gefallene Spiele (braucht BETWATCH_KEY).
    Vorher lief NUR (2); ohne Key im Konsens-Step blieb jeder Eintrag fuer immer pending. REIN/testbar."""
    from datetime import timedelta
    now = now or datetime.now(timezone.utc)
    min_h = MM_RESULTS_MIN_H if min_h is None else min_h
    ledger = [dict(e) for e in (ledger or []) if isinstance(e, dict)]

    def _apply(e, g1, g2):
        w = _winner_1x2(g1, g2)
        if w is None:
            return
        e["ftScore"] = [g1, g2]
        e["winner"] = w
        e["moneyWin"] = (e.get("moneySide") == w)
        if e.get("polySide"):
            e["polyWin"] = (e.get("polySide") == w)
        if e.get("pinnFav"):
            e["pinnWin"] = (e.get("pinnFav") == w)
        e["status"] = "won" if e["moneyWin"] else "lost"
        e["settledAt"] = now.isoformat()

    # 1) Endstand aus dem Live-Feed (kein Netz) — bewaehrter Track-Record-Pfad.
    feed = _feed_finished_map(prices)
    for e in ledger:
        if e.get("status") != "pending":
            continue
        sc = feed.get(str(e.get("matchId")))
        if sc and sc[0] is not None and sc[1] is not None:
            _apply(e, sc[0], sc[1])

    # 2) Autoritativer Endpoint fuer den Rest (aus dem Feed gefallen). Braucht BETWATCH_KEY im Job.
    if results_fetch is not None:
        cutoff = now - timedelta(hours=min_h)
        stale = []
        for e in ledger:
            if e.get("status") != "pending":
                continue
            try:
                kt = datetime.fromisoformat(str(e.get("kickoff")).replace("Z", "+00:00"))
            except (ValueError, TypeError):
                kt = None
            if kt is not None and kt < cutoff:
                stale.append(str(e.get("matchId")))
        if stale:
            res = results_fetch(stale) or {}
            for e in ledger:
                if e.get("status") != "pending":
                    continue
                r = res.get(str(e.get("matchId"))) if isinstance(res, dict) else None
                if not isinstance(r, dict) or not r.get("finished") or r.get("goal_v1") is None:
                    continue
                _apply(e, r.get("goal_v1"), r.get("goal_v2"))
    return ledger


def mm_summary(ledger, now=None, recent_keep=40):
    """13.08.2026 (Lucas: mehr Tracking): Trefferquote je Verdikt (folgt man der Betfair-Geld-Seite) PLUS
    Poly-Quote (folgt man der Poly-Seite) und Pinnacle-Favorit; je Liga; Divergenz-Duell (Betfair vs Poly
    wenn uneinig, wer trifft oefter?); und die letzten abgerechneten Spiele. Poly/Duell werden direkt aus
    polySide vs Endstand berechnet -> funktioniert auch fuer schon abgerechnete Alt-Zeilen. REIN/testbar."""
    buckets, leagues, strength = {}, {}, {}
    gn = gw = 0
    # 01.09.2026: Trefferquote UND Rendite — und beide streng getrennt gezaehlt. Zeilen aus der Zeit
    # vor dem 01.09. tragen keine Quote; sie zaehlen in die Trefferquote, aber NICHT in den ROI.
    # Deshalb ein eigenes `nRoi` je Eimer: eine Rendite aus 12 von 900 Zeilen darf nicht aussehen
    # wie eine aus 900.
    grend, gclv = [], []

    def _leer():
        return {"n": 0, "wins": 0, "polyN": 0, "polyWins": 0, "pinnN": 0, "pinnWins": 0,
                "rend": [], "clv": []}

    def _geld(b, e):
        """Rendite zum EINSTIEGSPREIS (nur der war nehmbar) und CLV Einstieg→letzte Quote."""
        o1, o2 = e.get("moneyOddFirst"), e.get("moneyOddLast")
        if isinstance(o1, (int, float)) and o1 > 1:
            b["rend"].append((o1 - 1.0) if e.get("moneyWin") else -1.0)
            if isinstance(o2, (int, float)) and o2 > 1:
                b["clv"].append(round((1.0 / o2 - 1.0 / o1) * 100.0, 2))

    # 03.10.2026: nur Zeilen, deren letzter Stand VOR Anpfiff liegt. Alles davor wurde live
    # fortgeschrieben und kennt den Ausgang (s. update_mm_ledger) — es zaehlt nirgends mehr mit.
    settled = [e for e in (ledger or []) if isinstance(e, dict) and e.get("status") in ("won", "lost")
               and mm_vor_anpfiff(e)]
    for e in settled:
        w = e.get("winner")
        v = e.get("verdict") or "?"
        b = buckets.setdefault(v, _leer())
        b["n"] += 1
        b["wins"] += 1 if e.get("moneyWin") else 0
        _geld(b, e)
        ps = e.get("polySide")
        if ps and w:
            b["polyN"] += 1
            b["polyWins"] += 1 if ps == w else 0
        if e.get("pinnWin") is not None:
            b["pinnN"] += 1
            b["pinnWins"] += 1 if e.get("pinnWin") else 0
        L = leagues.setdefault(e.get("league") or "?", {"n": 0, "wins": 0})
        L["n"] += 1
        L["wins"] += 1 if e.get("moneyWin") else 0
        st = e.get("mmStrong")
        if st is not None:
            SB = strength.setdefault(bool(st), {"n": 0, "wins": 0, "rend": [], "clv": []})
            SB["n"] += 1
            SB["wins"] += 1 if e.get("moneyWin") else 0
            _geld(SB, e)
        gn += 1
        gw += 1 if e.get("moneyWin") else 0
        o1, o2 = e.get("moneyOddFirst"), e.get("moneyOddLast")
        if isinstance(o1, (int, float)) and o1 > 1:
            grend.append((o1 - 1.0) if e.get("moneyWin") else -1.0)
            if isinstance(o2, (int, float)) and o2 > 1:
                gclv.append(round((1.0 / o2 - 1.0 / o1) * 100.0, 2))

    def _rate(x, n):
        return round(x / n, 4) if n else None

    def _ug(v, z=1.645):
        """Einseitige 95%-Untergrenze. Derselbe Richter wie im Freigabe-Register und im Killer —
        ein Punktschaetzer ist kein Beleg ([[feedback_punktschaetzer_kein_beleg]])."""
        n = len(v)
        if n < 2:
            return None
        m = sum(v) / n
        var = sum((x - m) ** 2 for x in v) / (n - 1)
        return round(m - z * (var ** 0.5) / (n ** 0.5), 4)

    def _geldfin(b):
        r, c = b.get("rend") or [], b.get("clv") or []
        return {"nRoi": len(r),
                "roi": round(sum(r) / len(r), 4) if r else None,
                "roiLb": _ug(r),
                "nClv": len(c),
                "clv": round(sum(c) / len(c), 2) if c else None,
                "clvLb": _ug(c)}

    def _fin(b):
        d = {"n": b["n"], "wins": b["wins"], "hitRate": _rate(b["wins"], b["n"]),
             "polyN": b["polyN"], "polyWins": b["polyWins"], "polyHitRate": _rate(b["polyWins"], b["polyN"]),
             "pinnN": b["pinnN"], "pinnHitRate": _rate(b["pinnWins"], b["pinnN"])}
        d.update(_geldfin(b))
        return d

    def _sfin(s):
        if not s:
            return None
        d = {"n": s["n"], "wins": s["wins"], "hitRate": _rate(s["wins"], s["n"])}
        d.update(_geldfin(s))
        return d

    dv = buckets.get("uneinig") or {"n": 0, "wins": 0, "polyN": 0, "polyWins": 0}
    divergence = {"n": dv["n"], "betfairWins": dv["wins"], "betfairRate": _rate(dv["wins"], dv["n"]),
                  "polyN": dv["polyN"], "polyWins": dv["polyWins"], "polyRate": _rate(dv["polyWins"], dv["polyN"])}

    by_league = dict(sorted(
        ((lg, {"n": L["n"], "wins": L["wins"], "hitRate": _rate(L["wins"], L["n"])}) for lg, L in leagues.items()),
        key=lambda kv: (-kv[1]["n"], kv[0])))

    def _slim(e):
        ps, w = e.get("polySide"), e.get("winner")
        return {"matchId": e.get("matchId"), "home": e.get("home"), "away": e.get("away"),
                "league": e.get("league"), "verdict": e.get("verdict"),
                "moneySide": e.get("moneySide"), "moneyName": e.get("moneyName"),
                "polySide": ps, "winner": w, "moneyWin": e.get("moneyWin"),
                "polyWin": ((ps == w) if (ps and w) else None),
                "ftScore": e.get("ftScore"), "kickoff": e.get("kickoff"), "settledAt": e.get("settledAt")}
    recent = [_slim(e) for e in sorted(settled, key=lambda e: (e.get("settledAt") or e.get("kickoff") or ""), reverse=True)[:recent_keep]]

    return {"generatedAt": now or _now_iso(),
            "byVerdict": {k: _fin(v) for k, v in buckets.items()},
            "byLeague": by_league,
            "byStrength": {"strong": _sfin(strength.get(True)), "weak": _sfin(strength.get(False))},
            "divergence": divergence,
            "recent": recent,
            "global": dict({"n": gn, "wins": gw, "hitRate": _rate(gw, gn)},
                           **_geldfin({"rend": grend, "clv": gclv})),
            "pending": sum(1 for e in (ledger or []) if isinstance(e, dict) and e.get("status") == "pending"),
            # 03.10.2026: wie viele abgerechnete Zeilen NICHT zaehlen, weil sie live fortgeschrieben
            # wurden — damit der Sprung von ~2.000 auf eine Handvoll erklaert ist, nicht raetselhaft.
            "ausgeschlossenLive": sum(1 for e in (ledger or []) if isinstance(e, dict)
                                      and e.get("status") in ("won", "lost") and not mm_vor_anpfiff(e))}


# ── Pinnacle-Bewegung ────────────────────────────────────────────────────────
# 29.08.2026 (Lucas: „Pini move da" als Bedingung fürs Killer-Element):
# Die Bewegung wurde gegen den UNMITTELBAR vorigen Snapshot gerechnet. Der Scan läuft alle
# ~15 Minuten, gelegentlich zweimal in vier Minuten — zwischen zwei Läufen bewegt Pinnacle
# sich praktisch nie. Ergebnis: über 40 Spiele gab es genau ZWEI verschiedene Werte (−0.0
# und 1.2). Die Bewegung war faktisch tot und als Bedingung wertlos.
# Gemessen wird jetzt über das FENSTER (ältester bis aktueller Snapshot) — genau so, wie es
# _pwMoveFor in poly-wallets.js für die Poly-Preise längst macht. Aus der echten Historie:
# 0.658 → 0.544 = −11.4pp über zwei Stunden, vorher unsichtbar.
#
# Drei Regeln müssen mit:
#  · Nur VOR Anpfiff. Ein Live-Repricing nach einem Tor (in der Historie: 0.50 → 0.69
#    innerhalb eines Laufs) ist ein Spielstand, keine Sharp-Bewegung.
#  · Snapshots ohne `pinn` werden ÜBERSPRUNGEN, nicht als 0 gelesen — fehlende Information
#    ist keine Bewegung.
#  · `stepPP` ist der letzte ECHTE Schritt (identische Nachbar-Snapshots aus einem Doppel-
#    lauf werden übersprungen). Zieht er in dieselbe Richtung wie das Fenster, läuft die
#    Bewegung noch — dieselbe Definition wie beim Poly-Steam.
PINN_MOVE_MIN_PP = 1.0     # darunter Rauschen, kein Move


def _ts(x):
    try:
        return datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return None


def pinn_move(prevlist, pinn, side, kickoff=None, live=False):
    """Bewegung der Pinnacle-Wahrscheinlichkeit der Geld-Seite über das Snapshot-Fenster. REIN."""
    i = {"home": 0, "draw": 1, "away": 2}.get(side)
    if i is None or not pinn or len(pinn) <= i or live:
        return None
    if isinstance(prevlist, dict):      # Rückwärts-kompatibel: ein einzelner Snapshot
        prevlist = [prevlist]
    ko = _ts(kickoff)
    hist = []
    for snap in (prevlist or []):
        if not isinstance(snap, dict):
            continue
        p = snap.get("pinn")
        if not isinstance(p, (list, tuple)) or len(p) <= i or not isinstance(p[i], (int, float)):
            continue
        ts = _ts(snap.get("ts"))
        if ko and ts and ts >= ko:
            continue                    # nach Anpfiff: Spielstand, nicht Sharp-Geld
        hist.append(float(p[i]))
    if not hist:
        return None
    jetzt = float(pinn[i])
    move = (jetzt - hist[0]) * 100.0
    step = (jetzt - hist[-1]) * 100.0
    for v in reversed(hist):            # letzter Schritt, der überhaupt einer war
        if abs(jetzt - v) > 1e-9:
            step = (jetzt - v) * 100.0
            break
    return {"movePP": round(move, 1), "stepPP": round(step, 1), "n": len(hist) + 1,
            "move": abs(move) >= PINN_MOVE_MIN_PP,
            "laeuft": (step > 0) == (move > 0) and abs(step) >= 0.2}


def build_game(m, ev, prev, direction, poly=None, totals_ev=None) -> dict:
    """Ein Spiel: Betfair-Geld-Seite + (falls Anker) Pinnacle/Soft-Probs+Quoten, Poly-Odd+Volumen,
    Bewegung, Verdikt. REIN (Netz passiert vorher)."""
    ms = money_side(m)
    li = m.get("liveInfo") or {}
    idx = {"home": 0, "draw": 1, "away": 2}
    i = idx.get(ms.get("side")) if ms else None
    out = {
        "matchId": str(m.get("matchId")), "home": m.get("home"), "away": m.get("away"),
        "league": m.get("league"), "country": m.get("country"), "kickoff": m.get("kickoff"),
        "live": bool(li.get("time")) and not li.get("finished"),
        "moneySide": ms.get("side") if ms else None,
        "moneyName": ms.get("name") if ms else None,
        "moneySharePct": round((ms.get("share") or 0.0) * 100) if ms else None,
        "moneyOdd": ms.get("odd") if ms else None,
        "moneyDir": _money_dir(direction, m, ms),
        "totVol": round(ms.get("totVol")) if ms else 0,
        # rohe Quoten je Quelle FUER DIE GELD-SEITE (das, was Lucas direkt vergleichen will):
        "pinnOdd": (round(ev["pinnOdds"][i], 2) if (ev and ev.get("pinnOdds") and i is not None) else None),
        "softOdd": (round(ev["softOdds"][i], 2) if (ev and ev.get("softOdds") and i is not None) else None),
        "softN": ev.get("nSoft") if ev else None,
        "poly": poly,
        "pinn": None, "soft": None, "pinnMovePP": None, "pinnMove": None,
        "verdict": "no_anchor", "agree": None,
       "pinnTotals": None,
    }
    pinn = ev.get("pinn") if ev else None
    soft = ev.get("soft") if ev else None
    if pinn:
        out["pinn"] = {"home": _r(pinn[0]), "draw": _r(pinn[1]), "away": _r(pinn[2]), "fav": _fav(pinn)}
    if soft:
        out["soft"] = {"home": _r(soft[0]), "draw": _r(soft[1]), "away": _r(soft[2]),
                       "fav": _fav(soft), "n": ev.get("nSoft")}
    # Bewegung: Pinnacle-Prob der Geld-Seite über das Snapshot-Fenster (s. pinn_move oben)
    if pinn and ms:
        pm = pinn_move(prev, pinn, ms.get("side"), m.get("kickoff"), live=out["live"])
        if pm:
            out["pinnMovePP"] = pm["movePP"]
            out["pinnMove"] = pm
    # Verdikt
    ref = pinn or soft
    if ms and ref:
        pinn_ok = (pinn is None) or (_fav(pinn) == ms["side"])
        soft_ok = (soft is None) or (_fav(soft) == ms["side"])
        anchor_fav = _fav(ref) == ms["side"]
        if anchor_fav and pinn_ok and soft_ok:
            out["verdict"] = "konsens"       # alle Quellen sehen dieselbe Seite vorn
        elif anchor_fav:
            out["verdict"] = "teil"          # Anker stimmt, eine Quelle schert aus
        else:
            out["verdict"] = "uneinig"       # Buchmacher sehen die andere Seite vorn
        out["agree"] = out["verdict"] in ("konsens", "teil")
    # 18.08.2026 (Lucas): Pinnacle-Totals (volle Over/Under-Leiter) andocken -> O/U-Edge im Terminal.
    if totals_ev and totals_ev.get("totals"):
        out["pinnTotals"] = totals_ev["totals"]
    return out


# ── Netzwerk (nur hier; laeuft am Mac-Runner) ────────────────────────────────
# Was die API ueber unser Kontingent sagt — sie schickt es bei JEDER Antwort mit, gelesen hat
# es bisher niemand. 🔴 21.09.2026: genau deshalb war ein abgelaufener Schluessel nicht von
# einem stillen Tag zu unterscheiden. `used`/`remaining` beantworten ausserdem die Frage, die
# sonst nur zu schaetzen war: wie oft rufen wir Pinnacle eigentlich auf.
# `letzterFehler` NUR fuer Antworten des Anbieters (HTTP-Status). Netzfehler (Timeout, Reset)
# zaehlen getrennt: 02.10.2026 meldete die Stoerungsmeldung „🔴 Schluessel ungueltig/abgelaufen"
# wegen EINES Timeouts — der Schluessel war gesund (45/45 Keys mit Daten, 4,98 Mio. Kontingent).
KONTINGENT = {"used": None, "remaining": None, "letzterFehler": None,
              "netzFehler": 0, "letzterNetzfehler": None}


def zaehle_keys(events_by_key: dict) -> dict:
    """{versucht, mitDaten, events}. REIN.

    🔴 21.09.2026: die Zahl im Artefakt war `len(events_by_key)` — also die VERSUCHTEN
    Keys. Ein toter Schluessel liefert fuer jeden Key eine leere Liste, und der Zaehler blieb
    bei 40. Genau daran habe ich am Morgen die richtige Vermutung „der Key ist tot" widerlegt.
    Fehlerklasse: ein Zaehler, der die Absicht zaehlt statt den Erfolg.
    Die Unterscheidung gehoert dorthin, wo die Zahl entsteht — deshalb hier und nicht im Leser.
    """
    d = events_by_key if isinstance(events_by_key, dict) else {}
    return {"versucht": len(d),
            "mitDaten": sum(1 for v in d.values() if v),
            "events": sum(len(v) for v in d.values() if hasattr(v, "__len__"))}


def _kontingent_merken(resp):
    for kopf, feld in (("x-requests-used", "used"), ("x-requests-remaining", "remaining")):
        v = resp.headers.get(kopf)
        if v is None:
            continue
        try:
            KONTINGENT[feld] = int(float(v))
        except (TypeError, ValueError):
            pass


def fetch_odds(sport_key):
    if not ODDS_KEY:
        return []
    url = ("%s/sports/%s/odds?apiKey=%s&regions=%s&markets=h2h&oddsFormat=decimal&dateFormat=iso"
           % (ODDS_BASE, sport_key, ODDS_KEY, REGIONS))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "cocobet-consensus"})
        with urllib.request.urlopen(req, timeout=ODDS_TIMEOUT_S) as r:
            _kontingent_merken(r)
            data = json.load(r)
        return data if isinstance(data, list) else []
    except urllib.error.HTTPError as e:
        # 401/403 = Schluessel tot oder falsch, 429 = Kontingent leer. Das sind KEINE leeren
        # Spieltage, und sie duerfen sich nicht als solche tarnen.
        KONTINGENT["letzterFehler"] = "HTTP %s" % e.code
        print("odds-fetch %s: HTTP %s" % (sport_key, e.code))
        return []
    except Exception as e:
        KONTINGENT["netzFehler"] = int(KONTINGENT.get("netzFehler") or 0) + 1
        KONTINGENT["letzterNetzfehler"] = str(e)[:120]
        print("odds-fetch %s: %s" % (sport_key, e))
        return []


def fetch_sports():
    """/sports — die Liste aller Wettbewerbe. Kostet KEIN Kontingent (the-odds-api zaehlt nur
    /odds-Calls). Faellt der Call aus, greift der letzte Abzug von Platte: der Lauf soll durch einen
    Netzfehler nicht seine halbe Abdeckung verlieren."""
    if not ODDS_KEY:
        return []
    url = "%s/sports?apiKey=%s" % (ODDS_BASE, ODDS_KEY)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "cocobet-consensus"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.load(r)
        return data if isinstance(data, list) else []
    except Exception as e:
        print("sports-fetch: %s" % e)
        return []


def fetch_totals(sport_key):
    """Pinnacle Over/Under, VOLLE Leiter (18.08.2026, Lucas): separater Call, nur eu-Region (dort liegt
    Pinnacle) + markets=totals,alternate_totals. So kostet es nur die Totals-Maerkte in EINER Region
    statt aller Maerkte ueber alle Regionen. h2h-Call bleibt unveraendert."""
    if not ODDS_KEY:
        return []
    url = ("%s/sports/%s/odds?apiKey=%s&regions=eu&markets=totals,alternate_totals&oddsFormat=decimal&dateFormat=iso"
           % (ODDS_BASE, sport_key, ODDS_KEY))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "cocobet-consensus"})
        with urllib.request.urlopen(req, timeout=ODDS_TIMEOUT_S) as r:
            data = json.load(r)
        return data if isinstance(data, list) else []
    except Exception as e:
        print("totals-fetch %s: %s" % (sport_key, e))
        return []


def totals_keys(need_keys, live_pool, global_pool, match_fn, max_h=None):
    """Fuer welche Wettbewerbe die Pinnacle-O/U-Leiter geholt wird. REIN (match_fn wird gereicht).

    🔴 27.09.2026 (Terminal-Check, Denmark v Wales): unter „Andere Maerkte" stand „Noch keine
    Pinnacle-Totals im Datensatz — erscheinen nach dem naechsten Betfair-Lauf". Sie waeren nie
    erschienen: die Leiter wurde NUR fuer die Handliste geholt (`need`, gewollt=1 an dem Tag), und
    die Nations League bekommt ihren Pinnacle-Anker ueber die Entdeckung (globaler Pool). Ein
    Spiel mit Anker, aber ohne Totals — weil der Totals-Call an einer anderen Liste hing als der
    Anker. Jetzt: Handliste zuerst, dann jeder entdeckte Key, der einem Radar-Spiel einen
    PINNACLE-Anker geliefert hat. Ohne Pinnacle im h2h gibt es auch keine Pinnacle-Totals —
    solche Keys kosten nur einen Call.
    """
    out = list(dict.fromkeys(need_keys or []))
    for m in live_pool or []:
        ev = match_fn(m, global_pool or [], max_h=max_h)
        k = (ev or {}).get("key")
        if k and (ev or {}).get("pinn") and k not in out:
            out.append(k)
    return out


def main():
    prices = _load(PRICES_FILE, {})
    matches = prices.get("matches") or []
    direction = _load(DIRECTION_FILE, {})
    if not isinstance(direction, dict):
        direction = {}
    hist = _load(HIST_FILE, {})
    if not isinstance(hist, dict):
        hist = {}

    # EXAKT die Spiele der Betfair-Radar-Liste (qualifies_radar), fertige raus.
    live_pool = [m for m in matches
                 if not (m.get("liveInfo") or {}).get("finished") and qualifies_radar(m)]

    # welche Odds-Ligen brauchen wir (nur die, die auch im Feed liegen)?
    need = {}
    for m in live_pool:
        k = LEAGUE_ODDS_KEY.get(m.get("league"))
        if k:
            need.setdefault(k, []).append(m)

    # 01.09.2026: die Handliste ist nur noch die SCHNELLE Zuordnung, nicht mehr das Tor. Zusaetzlich
    # werden ALLE aktiven Fussball-Wettbewerbe geholt (Quota ist da, Laufzeit ist der Deckel).
    _sports = fetch_sports()
    if _sports:
        _dump(SPORTS_FILE, {"generatedAt": _now_iso(), "sports": _sports})
    else:
        _sports = (_load(SPORTS_FILE, {}) or {}).get("sports") or []   # letzter Abzug statt blind
    _alle_keys = aktive_fussball_keys(_sports)
    hol = list(dict.fromkeys(list(need.keys()) + _alle_keys))[:MAX_ODDS_KEYS]
    print("odds: %d Ligen aus der Handliste + %d aktive Fussball-Wettbewerbe -> %d Calls"
          % (len(need), len(_alle_keys), len(hol)))

    # Kuratierte Keys IMMER, Entdeckungs-Extras nur solange die Wanduhr reicht. Ein abgebrochener
    # Lauf waere teurer als ein paar fehlende Anker: ohne Konsens laeuft die halbe Kette nicht.
    import time as _time
    _t0, events_by_key, _abbruch = _time.monotonic(), {}, 0
    for k in hol:
        if k not in need and (_time.monotonic() - _t0) > ODDS_BUDGET_S:
            _abbruch += 1
            continue
        # 26.09.2026: jedes Event traegt seinen Key — sonst kann die Anker-Diagnose nicht sagen,
        # aus welchem Wettbewerb ein Kandidat stammt.
        events_by_key[k] = [dict(parse_event(e), key=k) for e in (fetch_odds(k) or [])]
    if _abbruch:
        print("odds: Zeitbudget %.0fs erschoepft -> %d entdeckte Wettbewerbe diesmal ausgelassen "
              "(kuratierte sind vollstaendig)" % (ODDS_BUDGET_S, _abbruch))
    # money-radar 18.10.2026: eigener Pinnacle-Scan über ALLE abgerufenen Spiele (nicht nur die Radar-Liste),
    # vor Anpfiff und live — gleich hier, damit ein späterer Timeout ihn nicht mitreißt.
    try:
        import pinnacle_scan
        pinnacle_scan.fortschreiben(events_by_key, _sports)
    except Exception as _e:  # noqa: BLE001
        print("pinnacle_scan: nicht gelaufen (%s)" % str(_e)[:100])
    # Totals nur fuer die kuratierten Keys: die O/U-Leiter wird ausschliesslich im Poly-Terminal
    # verbraucht, und jeder weitere Call kostet Laufzeit auf einem Runner mit 12-Minuten-Deckel.
    # Totals ebenfalls an die Wanduhr: 30 kuratierte Keys mit je 8s Timeout waeren allein schon
    # 4 Minuten. Die O/U-Leiter ist Komfort (Poly-Terminal) — der Konsens ist es nicht.
    # Globaler Pool fuer den zweiten Anlauf — nur die Keys, die NICHT schon in der Handliste stehen,
    # damit ein kuratierter Treffer immer Vorrang behaelt.
    _global_pool = [e for k, evs in events_by_key.items() if k not in need for e in evs]
    _alle_events = [e for evs in events_by_key.values() for e in evs]

    # Totals nach dem Pool: die Leiter haengt jetzt an denselben Keys wie der Anker (totals_keys).
    # Weiter an der Wanduhr — die O/U-Leiter ist Komfort (Poly-Terminal), der Konsens ist es nicht.
    _tot_keys = totals_keys(
        list(need.keys()),
        [m for m in live_pool if not LEAGUE_ODDS_KEY.get(m.get("league"))],
        _global_pool, match_event, max_h=ANPFIFF_FENSTER_H)
    totals_by_key = {}
    for k in _tot_keys:
        if (_time.monotonic() - _t0) > ODDS_GESAMT_S:
            print("odds: Gesamtbudget %.0fs erreicht -> Totals fuer %d Ligen ausgelassen"
                  % (ODDS_GESAMT_S, len(_tot_keys) - len(totals_by_key)))
            break
        totals_by_key[k] = [parse_totals(e) for e in (fetch_totals(k) or [])]

    # Poly (globaler Broad-Scan, committet vom Poly-Workflow): nur Basis-Moneylines (kein
    # more-markets/exact-score/total), damit wir die Team-Preise + Volumen matchen koennen.
    # 06.09.2026 (Lucas: „wenn in beiden Elementen dasselbe Team, dann ja eindeutiger"). Der
    # Marktschluessel steht in diesen Pools NUR als Dict-Schluessel — `dict(v, src=...)` warf ihn
    # weg, also lieferte `match_poly` fuer close/live/upcoming immer `key: None`. Nur der
    # Liga-Fallback setzte ihn (`"key": _v.get("slug")`), und genau deshalb konnte niemand eine
    # Konsens-Zeile mit einer Poly-Zeile verbinden: „Remo v Flamengo" und „bra-cre-fla-2026-09-06"
    # sind dasselbe Spiel, aber der einzige gemeinsame Schluessel wurde hier verworfen.
    # Dieselbe Klasse wie `match_eintrag` heute frueh: die Daten waren da, sie wurden nur nie
    # weitergereicht. `v.get("key") or k` — ein eigenes Feld hat Vorrang, sonst der Dict-Schluessel.
    poly_raw = _load("poly_money_broad_close.json", {})
    poly_entries = [dict(v, src="close", key=v.get("key") or k) for k, v in poly_raw.items()
                    if isinstance(v, dict) and v.get("prices")
                    and not any(x in str(k) for x in ("-more-markets", "-exact-score", "-total", "-spread"))
                    and len(v.get("prices")) <= 4] if isinstance(poly_raw, dict) else []
    # Stufe 4 (Live): fuer laufende Spiele die LIVE-Poly-Blase (poly_money_broad_live.json).
    # Additiv — nur die Money-Map nutzt sie; der Betfair-Radar (games) bleibt auf dem Close-Freeze.
    poly_live_raw = _load("poly_money_broad_live.json", {})
    poly_live_entries = [dict(v, src="live", key=v.get("key") or k) for k, v in poly_live_raw.items()
                    if isinstance(v, dict) and v.get("prices")
                    and not any(x in str(k) for x in ("-more-markets", "-exact-score", "-total", "-spread"))
                    and len(v.get("prices")) <= 4] if isinstance(poly_live_raw, dict) else []
    # Money-Map (12.08.2026, Lucas): breitere "upcoming"-Erfassung (nur Preis+Vol, kein Holder-Call) ->
    # Poly-Blase auch fuer Spiele weit vor Anpfiff (Super Cup/Pokal), wenn close/live den Markt noch nicht hat.
    poly_upcoming_raw = _load("poly_money_upcoming.json", {})
    poly_upcoming_entries = [dict(v, src="upcoming", key=v.get("key") or k) for k, v in poly_upcoming_raw.items()
                    if isinstance(v, dict) and v.get("prices")
                    and not any(x in str(k) for x in ("-more-markets", "-exact-score", "-total", "-spread"))
                    and len(v.get("prices")) <= 4] if isinstance(poly_upcoming_raw, dict) else []
    # 06.09.2026 (Lucas mit Polymarket-Screenshot: „Serie A sind alle Spiele da"). Er hatte recht und
    # ich lag falsch — ich hatte aus „nicht in unseren Artefakten" auf „gibt es nicht" geschlossen.
    # `poly_money_broad.py` hatte am 06.09. von vier Serie-A-Spielen nur zwei (fro-ven, par-mon);
    # `sea-juv-mil` ($94,6K Event-Volumen) und `sea-bol-sas` ($50,0K) fehlten dort.
    #
    # Sie liegen aber laengst in `liga_poly_prices.json` — mit Slug, Preisen und Volumen, vom
    # Liga-Fetcher erfasst. Wir HOLEN die Daten also, der Konsens fragte sie nur nie.
    # Reine Preis-Quelle (keine Geld-Verteilung), deshalb `src="liga"` und hinter close/upcoming.
    _lp = _load("liga_poly_prices.json", {})
    poly_liga_entries = []
    for _k, _v in ((_lp.get("prices") or {}) if isinstance(_lp, dict) else {}).items():
        if not isinstance(_v, dict):
            continue
        _hn, _an = _v.get("homeName"), _v.get("awayName")
        _hw, _dr, _aw = _v.get("hw"), _v.get("dr"), _v.get("aw")
        if not (_hn and _an) or not all(isinstance(x, (int, float)) for x in (_hw, _dr, _aw)):
            continue
        # Ein Markt, in dem alle drei Ausgaenge gleich teuer sind, ist ein Platzhalter ohne Handel
        # (im Bestand: „Bologna v Torino 0.5/0.5/0.5, vol 0"). Kein Preis ist besser als ein erfundener.
        if _hw == _dr == _aw:
            continue
        poly_liga_entries.append({
            "src": "liga", "key": _v.get("slug"), "totalUsd": round(_v.get("vol") or 0),
            "prices": {_hn: _hw, "Draw (%s vs. %s)" % (_hn, _an): _dr, _an: _aw}})

    # 23.08.2026 (Lucas: „Serie A ist alles da, aber Money-Map zeigt kein Poly"): letzter Fallback ist
    # der FAIRE Poly-Preis aus dem Pinnacle×Poly-Scan (pinnacle_poly_scan.json). Er deckt auch dünne
    # Märkte ($597-Bücher), die durch die Volumen-Schwelle des Broad-Money-Scans fallen. Er füllt NUR
    # die Poly-SEITE (für den Konsens) — als „scan" markiert, damit er im Render als dünn/Preis erscheint
    # und im Money-Gate NICHT als echte Geldquelle zählt (siehe _mm_money_ok).
    scan_raw = _load("pinnacle_poly_scan.json", {})
    poly_scan_entries = []
    for _k, _v in ((scan_raw.get("games") or {}).items() if isinstance(scan_raw, dict) else []):
        if not isinstance(_v, dict):
            continue
        _snaps = _v.get("snaps") or []
        _last = _snaps[-1] if _snaps else None
        _poly = (_last or {}).get("poly") if isinstance(_last, dict) else None
        _home, _away = _v.get("home"), _v.get("away")
        if not (isinstance(_poly, list) and len(_poly) >= 3 and _home and _away):
            continue
        poly_scan_entries.append({
            "prices": {_home: _poly[0], "Draw": _poly[1], _away: _poly[2]},
            "shares": {}, "totalUsd": (_last or {}).get("vol") or 0, "src": "scan"})

    # ── Anker-Pool (02.09.2026) ──────────────────────────────────────────────────────────
    # Lucas: „Wieso gibt es kein Pini? Das Spiel ist zu 100% bei Pinnacle." Er hatte recht, und die
    # Ursache war unsere eigene: `games` entsteht nur aus `live_pool`, und der ist mit
    # `qualifies_radar()` auf >=15.000 EUR im groessten FT-Markt gefiltert — dieselbe Schwelle wie
    # die Radar-LISTE. Sassuolo–Frosinone (Coppa Italia, in der Ankerkarte!) lag bei 10.289 EUR,
    # fiel raus, und damit fragte NIEMAND Pinnacle fuer dieses Spiel. Der Buecher-Punktestand sah
    # kein zweites Buch und meldete korrekt ❔ — die Luecke war zwei Schichten tiefer.
    #
    # ⭐ Eine Schwelle, die entscheidet WAS ANGEZEIGT wird, darf nicht entscheiden, OB WIR FRAGEN.
    # Der Anker-Pool trennt beides: `games` bleibt exakt die Radar-Liste (unveraendert), und
    # `betfair_anker.json` traegt Zweitmeinungen fuer JEDES noch nicht beendete Spiel. Kostet keinen
    # einzigen zusaetzlichen API-Call — die Events sind laengst geholt, es ist nur Zuordnen.
    def _anker_zeile(m):
        _li = m.get("liveInfo") or {}
        _isl = bool(_li.get("time")) and not _li.get("finished")
        _ms = money_side(m)
        _k = LEAGUE_ODDS_KEY.get(m.get("league"))
        _ev = match_event(m, events_by_key.get(_k, [])) if _k else None
        if _ev is None:
            _ev = match_event(m, _global_pool, max_h=ANPFIFF_FENSTER_H)
        _ev = pinn_nachtragen(m, _ev, _alle_events, ANPFIFF_FENSTER_H)
        _poly = pick_poly(m, _ms, _isl, poly_entries, poly_live_entries, poly_upcoming_entries)
        _g = build_game(m, _ev, hist.get(str(m.get("matchId"))) or [], direction, _poly)
        # 08.09.2026: dieselbe Poly-Pool-Kaskade wie die Money Map, damit die Zentrale-Zeile
        # unten aus derselben Quelle stammt wie die Blase — sonst zeigen zwei Flaechen
        # verschiedene Poly-Zahlen zum selben Spiel, und niemand sieht, welche gilt.
        _pool = poly_scan_entries
        for _p in ((poly_live_entries if _isl else []), poly_entries, poly_upcoming_entries,
                   poly_liga_entries):
            if _p and _best_poly_entry(m, _p) is not None:
                _pool = _p
                break
        # Nur die Felder, die der Buecher-Score liest — die Datei wird alle 15 Minuten committet.
        # 08.09.2026 (Lucas: „koennte man das optisch nicht ins Kaestchen schreiben, wieviel Kohle
        # oben liegt?"): `totVol` kam dazu. Ohne es kann der Score fuer Anker-Spiele zwar den
        # ANTEIL zeigen („88 %"), aber nie den Betrag — und das sind 48 der 54 Zeilen, also
        # ausgerechnet fast alle. Ein Feld, das laengst gerechnet ist und nur nicht mitkam.
        _anker = {kk: _g.get(kk) for kk in ("moneySide", "moneyName", "poly", "pinn", "pinnMove",
                                            "league", "kickoff", "verdict", "totVol")}
        if not _anker.get("pinn"):
            _anker["ankerGrund"] = anker_grund(m, _ev, _alle_events)
        return _anker, money_map_row(_g, poly_fav(m, _pool))

    now = _now_iso()
    games, new_hist, mm_rows, mm_name_misses = [], {}, [], []
    for m in live_pool:
        mid = str(m.get("matchId"))
        k = LEAGUE_ODDS_KEY.get(m.get("league"))
        ev = match_event(m, events_by_key.get(k, [])) if k else None
        if ev is None:
            # Zweiter Anlauf im globalen Pool — MIT Anpfiff-Schranke. So bekommen auch Pokal- und
            # Kleinligaspiele einen Anker, ohne dass die Handliste sie kennen muss.
            ev = match_event(m, _global_pool, max_h=ANPFIFF_FENSTER_H)
        ev = pinn_nachtragen(m, ev, _alle_events, ANPFIFF_FENSTER_H)
        # 27.09.2026: Totals ueber den Key des ANKERS, nicht nur ueber die Handliste.
        _tk = k or (ev or {}).get("key")
        tev = match_event(m, totals_by_key.get(_tk, [])) if _tk else None
        # Poly-Quelle je nach Phase (18.08.2026, Lucas): LAUFENDES Spiel -> FRISCHE Live-Poly hat Vorrang,
        # sonst zeigte der Terminal auf einem Live-Spiel die eingefrorene Pre-Match-Quote aus dem Close-Pool.
        # Nicht-live: Close (<=3h Freeze, mit Holder-Shares) zuerst, dann die breitere Upcoming-Erfassung
        # (Preis+Vol, kein Freeze) fuer Spiele weit vor Anpfiff. sharePct kann bei live/upcoming None sein.
        _li = m.get("liveInfo") or {}
        _isl = bool(_li.get("time")) and not _li.get("finished")
        poly = pick_poly(m, money_side(m), _isl, poly_entries, poly_live_entries,
                         poly_upcoming_entries, poly_liga_entries)
        prevlist = hist.get(mid) or []
        # 29.08.2026: build_game bekommt die GANZE Fenster-Historie, nicht mehr nur den
        # letzten Snapshot — sonst misst pinn_move zwei Läufe im Abstand von Minuten.
        prev = prevlist
        g = build_game(m, ev, prev, direction, poly, totals_ev=tev)
        if not g.get("pinn"):
            g["ankerGrund"] = anker_grund(m, ev, _alle_events)
        games.append(g)
        # Money-Map Poly-Pool (12.08.2026, Lucas): live (laufend) > close (<=3h, mit Shares) >
        # upcoming (weit draussen, nur Preis+Vol). So erscheint die Poly-Blase auch lange vor Anpfiff.
        li = m.get("liveInfo") or {}
        is_live = bool(li.get("time")) and not li.get("finished")
        if is_live and _best_poly_entry(m, poly_live_entries) is not None:
            mm_pool, mm_g = poly_live_entries, build_game(m, ev, prev, direction, match_poly(m, money_side(m), poly_live_entries))
        elif _best_poly_entry(m, poly_entries) is not None:
            mm_pool, mm_g = poly_entries, g
        elif _best_poly_entry(m, poly_upcoming_entries) is not None:
            mm_pool, mm_g = poly_upcoming_entries, g   # Verdikt bleibt no_anchor; money_map_row leitet Konsens aus Betfair vs Poly ab
        elif _best_poly_entry(m, poly_liga_entries) is not None:
            mm_pool, mm_g = poly_liga_entries, g       # 06.09.2026: Preis aus dem Liga-Fetcher
        else:
            mm_pool, mm_g = poly_scan_entries, g   # 23.08.2026 (Lucas): dünner Markt -> fairer Poly-Preis aus dem Scan
        _mmr = money_map_row(mm_g, poly_fav(m, mm_pool))
        mm_rows.append(_mmr)
        # 13.08.2026 (Lucas-Audit): Namens-Match-Miss zaehlen - Betfair-Geld da, Poly=None, aber im
        # Pool liegt ein Kandidat mit Token-Overlap (wahrscheinlich Abkuerzungs-Luecke).
        # 05.09.2026 (Uebersicht-Check): geprueft wird gegen ALLE durchsuchten Pools, nicht nur
        # gegen den gewaehlten. Vorher lief die Pruefung gegen `mm_pool` — und das ist im
        # Fehlerfall gerade der Rueckfall-Pool (Scan), der ausgewaehlt wurde, WEIL nichts
        # gematcht hat. Der Zaehler war damit genau fuer die Misses blind, um die es geht:
        # Brighton v Leeds fehlte mit $439.712 Poly-Geld in der Map und tauchte in
        # `nameMatchMissList` nicht auf.
        _durchsucht = (poly_live_entries if is_live else []) + list(poly_entries) \
            + list(poly_upcoming_entries) + list(mm_pool)
        if _mmr.get("betfair") and not _mmr.get("poly") and _poly_has_any_overlap(m, _durchsucht):
            mm_name_misses.append({"matchId": mid, "home": m.get("home"), "away": m.get("away"), "league": m.get("league")})
        snap = {"ts": now}
        if ev and ev.get("pinn"):
            snap["pinn"] = [round(x, 4) for x in ev["pinn"]]
        if snap.get("pinn") or prevlist:
            _letzte = _ts((prevlist[-1] or {}).get("ts")) if prevlist else None
            _jetzt = _ts(now)
            _zu_dicht = bool(_letzte and _jetzt
                             and (_jetzt - _letzte).total_seconds() < SNAP_MIN_ABSTAND_MIN * 60)
            # Zu dicht am letzten Eintrag: NICHT anhängen (sonst schiebt ein Doppellauf das
            # Fenster weg und der letzte Schritt misst vier Minuten statt zwanzig).
            new_hist[mid] = (prevlist if _zu_dicht else (prevlist + [snap]))[-HIST_KEEP:]

    games.sort(key=lambda g: (g.get("verdict") != "no_anchor", g.get("totVol") or 0), reverse=True)
    covered = sum(1 for g in games if g.get("verdict") != "no_anchor")
    out = {"generatedAt": now, "count": len(games), "covered": covered,
           # 🔴 25.09.2026: hier stand `sorted(set(LEAGUE_ODDS_KEY.values()))` unter dem Namen
           # „leaguesCovered". Das ist keine Messung, sondern die Handliste — das Feld behauptete
           # Abdeckung aus einer Absichtserklaerung. Seit dem 01.09. holt der Lauf ALLE aktiven
           # Wettbewerbe, also untertreibt es zusaetzlich: gemessen am 25.09. hatten 38 Ligen einen
           # Anker, 37 davon ueber den globalen Pool, und keine einzige davon stand in dem Feld.
           # Wer diese Zahl liest, sucht eine Luecke, die anders aussieht als sie ist.
           # Fehlerklasse: *eine Zahl, die einer besseren Messung daneben widerspricht und
           # trotzdem als Tatsache dasteht.*
           "leaguesKuratiert": sorted(set(LEAGUE_ODDS_KEY.values())),
           "leaguesMitAnker": sorted({str(g.get("league")) for g in games
                                      if isinstance(g, dict) and g.get("pinn")
                                      and g.get("league")}),
           "leaguesMitTotals": sorted({str(g.get("league")) for g in games
                                       if isinstance(g, dict) and g.get("pinnTotals")
                                       and g.get("league")}),
           # Was WIRKLICH geholt wurde — die Handliste allein sagt das seit dem 01.09. nicht mehr.
           # 🔴 21.09.2026: hier stand `len(events_by_key)` — die Zahl der VERSUCHTEN Keys.
           # Ein toter Schluessel liefert fuer jeden Key eine leere Liste, der Zaehler blieb
           # trotzdem bei 40, und genau daran habe ich am Morgen des 21.09. die richtige
           # Vermutung „der Key ist tot" widerlegt. Er war tot.
           # Fehlerklasse: ein Zaehler, der die Absicht zaehlt statt den Erfolg.
           "oddsKeysFetched": zaehle_keys(events_by_key)["versucht"],
           "oddsKeysMitDaten": zaehle_keys(events_by_key)["mitDaten"],
           "oddsEvents": zaehle_keys(events_by_key)["events"],
           # 🔴 25.09.2026: 13 neue Handlisten-Ligen = 13 weitere Totals-Calls je Lauf gegen
           # ODDS_GESAMT_S. Reisst das Budget, werden die Totals der HINTEREN Keys ausgelassen —
           # und das stand bisher nur im Log des Runners. Ohne diese Zahl sieht „keine Torlinie"
           # in einer Liga genau so aus wie „Pinnacle bietet keine an".
           # Fehlerklasse: *ein Abbruch, der nur im Log steht, ist fuer jeden Leser ein Nullwert.*
           "oddsTotals": dict(zaehle_keys(totals_by_key),
                              gewollt=len(_tot_keys),
                              ausgelassen=max(0, len(_tot_keys) - len(totals_by_key))),
           "oddsKontingent": dict(KONTINGENT),
           "ankerQuote": (round(sum(1 for g in games if g.get("pinn")) / len(games), 3)
                          if games else None),
           "games": games}
    # Anker fuer ALLE noch offenen Spiele — auch die unter der Radar-Schwelle. Die Spiele aus
    # `games` sind bereits vollstaendig, deshalb hier nur die uebrigen (kein Duplikat).
    _im_radar = {str(g.get("matchId")) for g in games}
    anker = {}
    for m in matches:
        _mid = str(m.get("matchId"))
        if _mid in _im_radar or (m.get("liveInfo") or {}).get("finished"):
            continue
        if not (m.get("markets") or {}).get("Match Odds"):
            continue
        try:
            anker[_mid], _ = _anker_zeile(m)
        except Exception:
            continue          # ein einzelnes Spiel darf den Pool nicht kosten
    _mit_pinn = sum(1 for v in anker.values() if v.get("pinn")) + sum(1 for g in games if g.get("pinn"))
    _n_ges = len(anker) + len(games)
    # 🔴 20.09.2026: `ankerQuote` stand auf 0,0 % und der Waechter meldete „the-odds-api-Key tot
    # oder Namens-Match gebrochen". Nachgezaehlt: von 113 offenen Spielen liegen 101 in Ligen, die
    # in LEAGUE_ODDS_KEY gar nicht gemappt sind — sie KOENNEN nie einen Anker bekommen (Salvadoran
    # Primera, Jamaican Premier, Serbian First League, Italian Serie C …). Der richtige Nenner sind
    # die 12 ankerbaren Spiele, nicht alle 113.
    #
    # Am 02.09. wurde dieser Nenner schon einmal repariert — damals von `games` (bereits auf
    # 15.000 EUR gefiltert) auf ALLE offenen Spiele. Er wurde dabei zu weit aufgemacht.
    #
    # Fehlerklasse: ein Prozentsatz ohne seinen Nenner — heute zum zweiten Mal.
    _ankerbar = [v for v in list(anker.values()) + list(games)
                 if LEAGUE_ODDS_KEY.get(v.get("league"))]
    _n_ankerbar = len(_ankerbar)
    _mit_pinn_ankerbar = sum(1 for v in _ankerbar if v.get("pinn"))
    _dump(ANKER_FILE, {"generatedAt": now, "n": len(anker),
                       "hinweis": "Zweitmeinungen OHNE die Radar-Volumenschwelle — Quelle fuer den "
                                  "Buecher-Punktestand. `games` in betfair_consensus.json bleibt die "
                                  "Radar-Liste.",
                       "anker": anker})
    print("anker: %d Spiele ausserhalb der Radar-Schwelle · Pinnacle auf %d von %d ANKERBAREN "
          "Spielen (%.0f%%) · %d offene Spiele insgesamt, %d davon in nicht gemappten Ligen"
          % (len(anker), _mit_pinn_ankerbar, _n_ankerbar,
             (_mit_pinn_ankerbar / _n_ankerbar * 100) if _n_ankerbar else 0,
             _n_ges, _n_ges - _n_ankerbar))
    # ⚠️ 02.09.2026: `ankerQuote` mass vorher gegen `games` — also gegen den bereits auf >=15.000 EUR
    # gefilterten Pool. „100%" hiess dann „100% von drei Spielen", waehrend keines der Spiele im
    # Punktestand einen Anker hatte. Der richtige Nenner sind ALLE offenen Spiele.
    # Die Quote rechnet ab jetzt ueber die ankerbaren Spiele. `ankerNOffen` bleibt daneben
    # stehen, damit sichtbar ist, wie klein der ankerbare Ausschnitt ist.
    out["ankerQuote"] = round(_mit_pinn_ankerbar / _n_ankerbar, 3) if _n_ankerbar else None
    out["ankerN"] = _n_ankerbar
    out["ankerNOffen"] = _n_ges
    out["ankerMit"] = _mit_pinn_ankerbar
    # 26.09.2026: warum die Spiele ohne Anker keinen haben — gezaehlt und mit Beispielen.
    _ohne = [(mid_, v) for mid_, v in anker.items() if isinstance(v, dict) and v.get("ankerGrund")] \
        + [(str(g_.get("matchId")), g_) for g_ in games if isinstance(g_, dict) and g_.get("ankerGrund")]
    from collections import Counter as _Ctr
    out["ankerDiagnose"] = {
        "nach": dict(_Ctr(v["ankerGrund"].get("grund") for _, v in _ohne)),
        "beispiele": [dict(v["ankerGrund"], matchId=mid_, liga=v.get("league"))
                      for mid_, v in _ohne if v["ankerGrund"].get("grund") != "kein_kandidat"][:40],
    }
    _dump(OUT_FILE, out)
    _dump(HIST_FILE, new_hist)
    # Money-Map (11.08.2026, Lucas): bubble-fertiger Feed + Konsens-Ledger fuers Tracking. Additiv.
    mm_rows = [r for r in mm_rows if _mm_money_ok(r)]   # 12.08.2026 (Lucas): min. 2 Geldquellen (Betfair+Poly) ODER eine Quelle >= MM_SINGLE_MIN
    mm_rows.sort(key=lambda r: (r.get("verdict") != "no_anchor", (r.get("betfair") or {}).get("eur") or 0), reverse=True)
    _dump(MONEYMAP_FILE, {"generatedAt": now, "count": len(mm_rows), "rows": mm_rows,
                          "nameMatchMisses": len(mm_name_misses), "nameMatchMissList": mm_name_misses[:20]})
    if mm_name_misses:
        print("WARN Money-Map Namens-Match-Miss: %d Spiele mit Betfair-Geld aber poly=None trotz Poly-Kandidat (%s ...)"
              % (len(mm_name_misses), ", ".join("%s-%s" % (x["home"], x["away"]) for x in mm_name_misses[:3])))
    mm_led = update_mm_ledger(_load(MMLEDGER_FILE, []), mm_rows, now=now)
    # 13.08.2026 (Lucas): Feed (betfair_prices.json) MIT reingeben -> abrechnen ohne Netz; _fetch_results als Backfill.
    mm_led = settle_mm_ledger(mm_led, results_fetch=_fetch_results, prices=prices)   # abrechnen gegen Endstand
    _dump(MMLEDGER_FILE, mm_led)
    _dump(MMRECORD_FILE, mm_summary(mm_led))
    _mm_done = sum(1 for e in mm_led if isinstance(e, dict) and e.get("status") in ("won", "lost"))
    _mm_open = sum(1 for e in mm_led if isinstance(e, dict) and e.get("status") == "pending")
    print("Money-Map: %d Zeilen -> %d im Ledger (%d abgerechnet, %d offen)" % (len(mm_rows), len(mm_led), _mm_done, _mm_open))
    print("Betfair-Konsens: %d Spiele (Radar-Schwelle), %d mit Odds-Anker" % (len(games), covered))


if __name__ == "__main__":
    main()
