#!/usr/bin/env python3
"""
stake_highroller_fetch.py — Stake Highroller-Sammler
================================================================================
03.09.2026 (Lucas: „ich würde gerne nur im Dashboard einen Bereich mit den Spielen sehen,
mit Schwellen die wir definieren, dann rein und wir sammeln das").

## Was das hier ist — und was es NICHT ist
Stake zeigt große Wetten öffentlich an (stake.com/sports/highrollers: Event, User, Zeit,
Quote, Einsatz). Das ist die einzige Quelle im Projekt, die EINZELNE Einsätze mit Betrag
nennt. Betfair gibt Matched-Volumen, Polymarket gibt Preis-als-Geldanteil, Pinnacle gibt
den Anker — keine davon nennt eine einzelne Wette.

Dieser Sammler holt diese Liste und legt sie ab. Mehr nicht. Er bewertet nichts, er pusht
nichts, er erzeugt kein Signal. Es gibt für Stake-Einsatzfluss im Projekt KEINE gemessene
Trefferquote und KEINEN gemessenen CLV — solange das so ist, ist jede Bewertung („starkes
Signal bei 4-5 Wetten in 1 Minute") eine Behauptung ohne Beleg, und die kommt hier nicht
rein. Erst sammeln, dann gegen den Pinnacle-Schlusskurs messen, dann urteilen.

## Bekannte Schwäche der Quelle (steht mit im Ausgabe-JSON, damit sie sichtbar bleibt)
Stake hat eine „Wetten verbergen"-Einstellung. Wer sie nutzt, taucht hier nicht auf. Die
Liste ist also nicht „die großen Einsätze", sondern „die großen Einsätze der Konten, die
sich zeigen". Das ist eine Auswahl, keine Grundgesamtheit.

## Woher die Abfrage kommt — geraten wird nicht
Endpunkt: https://stake.com/_api/graphql, Feld `highrollerSportBets`, ohne Anmeldung.
Die Abfrage ist am 03.09.2026 aus der Netzwerkanfrage MITGELESEN, die Stakes eigene
Highroller-Seite selbst stellt. Introspection ist dort abgeschaltet (HTTP 400, Apollo),
und die "Did you mean"-Vorschlaege sind es auch — beides waere aber kein Grund zu raten:
ein geratener Feldname, der zufaellig existiert und das Falsche liefert, ist genau die
Sorte stiller Fehler, die uns hier schon zweimal Geld gekostet hat.

Drei Wege, in dieser Reihenfolge:
  1. die zuletzt funktionierende Abfrage aus stake_query.json
  2. die verifizierte Abfrage (QUERY_BEKANNT, s.u.)
  3. aus den Validierungsfehlern des Servers lernen — das Netz fuer den Tag, an dem Stake
     umbaut. graphql-js validiert das ganze Dokument, bevor es etwas ausfuehrt, und schreibt
     in die Fehler, was es stattdessen kennt; weil alle Verstoesse gemeinsam zurueckkommen,
     kostet eine ganze Ebene genau eine Anfrage.
Findet keiner etwas, steht status="schema_unbekannt" da — und NICHT eine leere Liste, die
wie "heute keine grossen Wetten" aussaehe.

## Was der Feed NICHT hergibt
`user` ist immer null: Stake anonymisiert die Highroller-Liste vollstaendig. Ein
Track-Record je Konto, wie ihn die Poly-Wallets tragen, ist hier unmoeglich. Es gibt
aggregierten Fluss, nie "dieser Spieler hat schon wieder recht behalten".

## Ausgabe
  stake_highroller.json  — aktuelle Sicht fürs Dashboard (Wetten im Fenster, roh)
  stake_bet_ledger.json  — die Sammlung (dedupliziert über Wett-ID, gedeckelt)
  stake_schema_probe.json— was die Sonde über das Schema gelernt hat (nur --sonde)
  stake_query.json       — die gelernte Abfrage, damit nicht jeder Lauf neu lernt
  stake_kurse.json       — USD-Kurse je Waehrung (aus derselben Quelle), mit Stand

## Env
  STAKE_MIN_USD        Mindesteinsatz in USD (Default 1000)
  STAKE_FENSTER_H      Wie weit die Dashboard-Sicht zurückreicht (Default 48)
  STAKE_LEDGER_KEEP    Max Wetten im Ledger (Default 20000)
  STAKE_LIMIT          Wie viele Einträge je Abruf (Default 50)
  STAKE_ENDPUNKT       Endpunkt überschreiben

## Aufruf
  python3 stake_highroller_fetch.py --sonde     → nur prüfen, nichts schreiben ausser Probe
  python3 stake_highroller_fetch.py             → holen, normalisieren, ablegen
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

BASE = Path(os.environ.get("STAKE_DATEN") or Path(__file__).resolve().parent)  # money-radar: Daten in .st/

VIEW_FILE   = BASE / "stake_highroller.json"
LEDGER_FILE = BASE / "stake_bet_ledger.json"
PROBE_FILE  = BASE / "stake_schema_probe.json"
QUERY_FILE  = BASE / "stake_query.json"
KURS_FILE   = BASE / "stake_kurse.json"

ENDPUNKTE = [
    "https://stake.com/_api/graphql",
    "https://api.stake.com/graphql",
    "https://stake.bet/_api/graphql",
]

MIN_USD      = float(os.environ.get("STAKE_MIN_USD")     or 1000)
FENSTER_H    = int(os.environ.get("STAKE_FENSTER_H")     or 48)
LEDGER_KEEP  = int(os.environ.get("STAKE_LEDGER_KEEP")   or 20000)
ABRUF_LIMIT  = int(os.environ.get("STAKE_LIMIT")         or 50)
VIEW_KEEP    = 1500          # so viele rohe Wetten reicht die Sicht ans Frontend

KOPF = {
    "content-type": "application/json",
    "accept": "*/*",
    "user-agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "origin": "https://stake.com",
    "referer": "https://stake.com/sports/highrollers",
}

# Stablecoins rechnen 1:1. Alles andere braucht einen Kurs; ohne Kurs bleibt der Wert None —
# eine unbekannte Größe darf NICHT als 0 durchgehen, sonst sieht „weiß ich nicht" aus wie
# „war klein".
STABLE = {"usdt", "usdc", "busd", "dai", "usd", "tusd", "usdp"}

# ── Sportarten ───────────────────────────────────────────────────────────────
# 03.09.2026 (Lucas: „Ganze US-Sport brauch ich aktuell mal nicht. Ähnlich Poly. Das würd ich
# entfernen: NBA MLB NHL NFL"). Dieselben Kategorienamen wie im Poly-Tab (_pwSportCategory),
# damit eine Sperre in beiden Flaechen dasselbe heisst.
#
# GESPERRT heisst AUSGEBLENDET, nicht ungesammelt — und das ist die schon getroffene
# Entscheidung aus dem Poly-Fall vom 24.08. ("sollen wir die dann ganz rausnehmen? was wenn sie
# besser werden?"): das Mitschreiben ist gratis und die EINZIGE Art, je zu merken, dass eine
# Sportart dreht. Der Sammler sammelt also weiter alles; die Auswertung fuehrt gesperrte
# Sportarten in einer eigenen Schublade, und der Tab zeigt sie nicht, sagt aber wie viele
# er weglaesst. Ein stiller Filter waere genau die Sorte Fehler, die wir hier ausraeumen.
# 12.09.2026 (Lucas: „Cricket bitte raus") — nach einem Burst-Push auf
# „1st Innings - Narine, Sunil Total · Under 12.5" in der Caribbean Premier League.
# Gilt wie US-Sport: ausgeblendet, nicht ungesammelt. Die Auswertung fuehrt Cricket weiter in
# der eigenen Schublade (470 pending Wetten im Bestand) — faellt es dort je auf, sieht man es.
# 27.09.2026 (Lucas: „Kampfsport und Cricket … nimm sowohl als auch raus … mehr genug mit
# E-Sport, Tennis und so"). Anlass: Stake sperrte Cricket, liess aber Kampfsport durch
# (Inoue – Nasukawa stand in „Stake · größtes Geld"), Poly genau umgekehrt. Ab jetzt EINE Liste
# fuer beide Plattformen; tests/test_sperrliste_eine_fuer_alle.py haelt alle Kopien gleich.
GESPERRT = {"US-Sport", "Kampfsport", "Cricket"}
# Gesperrte LIGEN innerhalb einer erlaubten Sportart — als Muster ueber den Liganamen.
# 04.10.2026 (Lucas: „ATP auch, was keiner braucht") fuer die Pushes; 08.10.2026 („ATP und WTA
# können wir da draus nehmen") auch fuer die Stake-Kacheln der Uebersicht. ITF bleibt.
# EINE Quelle: stake_burst_push liest sie, das Frontend liest `gesperrtLigenMuster` im Artefakt.
GESPERRT_LIGEN_MUSTER = r"\b(atp|wta)\b"
_LIGEN_RX = re.compile(GESPERRT_LIGEN_MUSTER, re.I)


def ist_langzeit(w: dict) -> bool:
    """Langzeitwette (Outright) statt Spiel — erkennbar an der fehlenden Paarung „A - B".

    10.10.2026 (Übersicht-Check, Lucas: „ja"): „Ballon dor 2026 ⏱ 378 h · Ballon d`Or - Winner:
    Harry Kane $51.5K" stand in „Stake · größtes Geld" und „noch spielbar". Diese Kacheln
    zeigen Spiele; eine Wette, die in 16 Tagen entschieden wird, gehoert nicht dazu. Im
    Bestand am 10.10.: 6 von 1.500 Zeilen (Ballon d'Or, Nippon Series, Pro Wrestling).
    Gesammelt wird weiter — das Feld sagt nur, was es ist.
    """
    return " - " not in str(w.get("event") or "")


def sperr_grund(w: dict):
    """Warum eine Wette nicht angezeigt/gewertet wird — None, wenn sie erlaubt ist.

    10.10.2026 (Übersicht-Check): „Buse I / Vallejo D - Andreozzi G / Guinard M · ATP
    Shanghai, China Men Doubles · $70.6K … ×28.2" stand oben in „Stake · über der Norm",
    zwei Tage nachdem ATP/WTA aus der Übersicht genommen war. Die Liga-Sperre lebte nur im
    Frontend-Filter `_mdStakeWetten`; die Norm-Kachel liest `auffaellige` aus stake_analyse,
    und das kannte nur die Kategorien. Seitdem EINE Prüfung für Kategorie UND Liga hier.
    """
    kat = w.get("kat") or sport_kategorie(w.get("sport"), w.get("liga"))
    if kat in GESPERRT:
        return kat
    if _LIGEN_RX.search(str(w.get("liga") or "")):
        return "Liga gesperrt (ATP/WTA)"
    return None

_KAT_SLUG = {
    "soccer": "Fußball", "football": "Fußball",
    "tennis": "Tennis",
    "baseball": "US-Sport", "basketball": "US-Sport", "ice-hockey": "US-Sport",
    "icehockey": "US-Sport", "american-football": "US-Sport", "americanfootball": "US-Sport",
    "counter-strike": "E-Sport", "league-of-legends": "E-Sport", "dota-2": "E-Sport",
    "dota2": "E-Sport", "valorant": "E-Sport", "rainbow-six": "E-Sport",
    "starcraft": "E-Sport", "fifa": "E-Sport", "efootball-bots": "E-Sport",
    "call-of-duty": "E-Sport", "rocket-league": "E-Sport", "king-of-glory": "E-Sport",
    "mma": "Kampfsport", "boxing": "Kampfsport",
    "golf": "Golf", "motorsport": "Motorsport", "formula-1": "Motorsport",
    "cricket": "Cricket", "table-tennis": "Tischtennis", "volleyball": "Volleyball",
    "handball": "Handball", "darts": "Darts", "snooker": "Snooker", "rugby": "Rugby",
    "aussie-rules": "Rugby", "badminton": "Badminton",
}

def sport_kategorie(sport, liga=None) -> str:
    """Slug zuerst, Liganame als Rueckfall — nie geraten, wo der Slug es weiss.

    MLS ist bewusst NICHT US-Sport: es ist Fussball und wird im Projekt getradet.
    """
    sl = (sport or "").strip().lower()
    if sl in _KAT_SLUG:
        return _KAT_SLUG[sl]
    x = " %s %s " % (sl, (liga or "").strip().lower())
    # 03.09.2026 — Wortgrenzen, keine Leerzeichen: die Liga hiess "NCAA, Regular", und
    # " ncaa " passt darauf nicht (Komma statt Leerzeichen). Der Slug hat den Fall hier zwar
    # schon gefangen, aber der Rueckfall darf sich nicht auf den Slug verlassen — genau dafuer
    # ist er da. Im Frontend fehlte zusaetzlich american-football ganz, und dort ist es
    # aufgefallen: "Illinois Fighting Illini - UAB Blazers" stand in einer Kachel.
    if re.search(r"\b(nba|mlb|nfl|nhl|wnba|ncaa)\b|basketball|baseball|ice-?hockey"
                 r"|american[- ]?football", x):
        return "US-Sport"
    if any(t in x for t in ("esport", "cs2", "csgo", " lol ", "dota", "valorant")):
        return "E-Sport"
    if any(t in x for t in ("tennis", " wta ", " atp ")):
        return "Tennis"
    if any(t in x for t in ("mma", "ufc", "boxing")):
        return "Kampfsport"
    if "cricket" in x:
        return "Cricket"
    if "golf" in x:
        return "Golf"
    # MLS steht bewusst hier und NICHT beim US-Sport: sie ist Fussball und wird im Projekt
    # getradet. Ohne Slug fiel sie vorher auf "Sonstige" — der Test hat es gefunden.
    if any(t in x for t in ("soccer", "fussball", "liga", "ligue", "serie", "premier",
                            "bundesliga", "eredivisie", "championship", " mls ", " epl ",
                            " ucl ", " uel ", "allsven", "primeira", "super lig", "superlig")):
        return "Fußball"
    return "Sonstige"

# 03.09.2026 — im ersten echten Ledger hatten 25 von 93 Wetten (27%) keinen USD-Wert: eth, sol,
# btc, cad, try, ltc, xrp, aed. Ein Viertel des Flusses unsichtbar, und darunter waren keine
# Kleinbeträge. Die Kurse liegen bei derselben Quelle:
#
#   currencyConfiguration(isAcp: false) { currencies { name rates { currency rate } } }
#
# 174 Währungen, je Währung ein Kurs gegen jede andere; wir nehmen die Zeile currency=="usd".
# Gemessen am 03.09.: btc 81433,23 · eth 2511,93 · sol 105,41 · cad 0,7252.
#
# Der Kurs wird EINMAL je Lauf geholt und in stake_kurse.json abgelegt. Schlägt der Abruf fehl,
# gilt der letzte bekannte Kurs weiter — mit seinem Alter, das im Datensatz mitfährt. Ein alter
# Kurs ist für die Größenordnung einer Wette brauchbar, ein stillschweigend alter nicht.
KURS_QUERY = ("query K($a: Boolean!) { currencyConfiguration(isAcp: $a) "
              "{ currencies { name rates { currency rate } } } }")
KURS_MAX_ALTER_H = 24


def kurse_holen(url: str, alt: dict = None) -> dict:
    """-> {"usd": {waehrung: kurs}, "geholt": iso, "quelle": "live"|"cache"|"leer"}"""
    st, d, err = _post(url, {"query": KURS_QUERY, "variables": {"a": False}})
    rows = _pfad(d, "data", "currencyConfiguration", "currencies")
    tab = {}
    for r in rows or []:
        name = (r.get("name") or "").lower()
        for x in (r.get("rates") or []):
            if (x.get("currency") or "").lower() == "usd" and isinstance(x.get("rate"), (int, float)):
                tab[name] = float(x["rate"])
                break
    if tab:
        return {"usd": tab, "geholt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "quelle": "live", "n": len(tab)}
    behalten = (alt or {}).get("usd") or {}
    return {"usd": behalten, "geholt": (alt or {}).get("geholt"),
            "quelle": "cache" if behalten else "leer", "n": len(behalten),
            "fehler": err or "keine Kurse in der Antwort"}


def _kurs_alter_h(kurse: dict):
    g = (kurse or {}).get("geholt")
    if not g:
        return None
    try:
        d = datetime.fromisoformat(str(g).replace("Z", "+00:00"))
    except Exception:
        return None
    return (datetime.now(timezone.utc) - d).total_seconds() / 3600.0


# ── HTTP ─────────────────────────────────────────────────────────────────────
def _post(url: str, body: dict, timeout: int = 25):
    """-> (status:int|None, daten:dict|None, fehler:str|None)"""
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), headers=KOPF, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            roh = r.read()
            try:
                return r.status, json.loads(roh.decode("utf-8", "replace")), None
            except Exception:
                return r.status, None, "kein JSON: " + roh[:200].decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        # 03.09.2026 — mein Fehler, und er hat den ganzen Lernweg lahmgelegt: GraphQL-Server
        # antworten auf VALIDIERUNGSFEHLER mit HTTP 400 und trotzdem mit einem gueltigen
        # JSON-Body voller "errors". Genau dieser Body IST die Auskunft, aus der wir lernen.
        # Hier wurde er weggeworfen und durch "HTTP 400 <Textschnipsel>" ersetzt — die Sonde
        # meldete daraufhin "Endpunkt antwortet nicht", obwohl der Server praezise geantwortet
        # hatte ("Cannot query field \"highroller\" on type \"Query\"."). Ein Statuscode ist
        # kein Grund, den Inhalt nicht zu lesen.
        try:
            roh = e.read()
        except Exception:
            roh = b""
        txt = roh[:300].decode("utf-8", "replace")
        try:
            d = json.loads(roh.decode("utf-8", "replace"))
            if isinstance(d, dict) and ("errors" in d or "data" in d):
                return e.code, d, None
        except Exception:
            pass
        return e.code, None, "HTTP %s %s" % (e.code, txt)
    except Exception as e:
        return None, None, str(e)


# ── Schema-Findung ───────────────────────────────────────────────────────────
INTRO_QUERY = """
{ __schema { queryType { name fields { name
  args { name type { kind name ofType { kind name } } }
  type { kind name ofType { kind name ofType { kind name ofType { kind name } } } } } } } }
"""

INTRO_TYP = """
query T($n: String!) { __type(name: $n) { name kind
  fields { name
    args { name }
    type { kind name ofType { kind name ofType { kind name ofType { kind name } } } } }
  possibleTypes { name } } }
"""


def _typ_name(t: dict) -> str:
    """Wickelt NON_NULL/LIST ab und gibt den nackten Typnamen."""
    # 03.09.2026: `t = t.get("ofType") or {}` allein dreht sich bei {} ewig im Kreis —
    # der Test test_typ_name_bei_muell_leer_statt_absturz hat genau das aufgedeckt.
    # Abbruch gehoert an das FEHLEN von ofType, nicht an den Typ von t.
    while isinstance(t, dict):
        if t.get("name"):
            return t["name"]
        naechst = t.get("ofType")
        if not isinstance(naechst, dict):
            return ""
        t = naechst
    return ""


def _query_felder(url: str):
    st, d, err = _post(url, {"query": INTRO_QUERY})
    if not d or d.get("errors") or not d.get("data"):
        grund = err
        if not grund and d and d.get("errors"):
            grund = str((d["errors"][0] or {}).get("message") or "")[:160]
        return None, grund or "keine Antwort"
    qt = ((d.get("data") or {}).get("__schema") or {}).get("queryType") or {}
    return qt.get("fields") or [], None


def _waehle_feld(felder: list):
    """Das Query-Feld, das die Highroller-Liste liefert."""
    kand = []
    for f in felder:
        n = (f.get("name") or "").lower()
        if "highroller" in n or "highrollers" in n:
            kand.append(f)
    if not kand:
        return None
    # Sport schlägt Casino, sonst der kürzeste Name.
    sport = [f for f in kand if "sport" in (f.get("name") or "").lower()]
    return (sport or sorted(kand, key=lambda f: len(f["name"])))[0]


SKALAR = {"SCALAR", "ENUM"}

# Was die Sonde bei der Feldsuche gehoert hat — landet in stake_schema_probe.json.
_PROTOKOLL: list = []


def _selektion(url: str, typname: str, tiefe: int, cache: dict, pfad: tuple = ()):
    """Baut rekursiv eine Selektion: alle argumentlosen Skalare, plus Objektfelder
    bis `tiefe`. Kein Raten von Feldnamen — nur was das Schema wirklich hat."""
    if tiefe <= 0 or not typname or typname in pfad:
        return ""
    if typname not in cache:
        st, d, err = _post(url, {"query": INTRO_TYP, "variables": {"n": typname}})
        cache[typname] = ((d or {}).get("data") or {}).get("__type") or {}
    typ = cache[typname]
    felder = typ.get("fields") or []
    if not felder:
        # Union/Interface ohne Felder: über possibleTypes inline-fragmentieren
        moegl = [p.get("name") for p in (typ.get("possibleTypes") or []) if p.get("name")]
        teile = []
        for pt in moegl[:6]:
            inner = _selektion(url, pt, tiefe - 1, cache, pfad + (typname,))
            if inner:
                teile.append("... on %s %s" % (pt, inner))
        return "{ __typename " + " ".join(teile) + " }" if teile else ""
    teile = ["__typename"]
    for f in felder:
        if f.get("args"):
            continue                        # Felder mit Argumenten überspringen
        t = f.get("type") or {}
        tn = _typ_name(t)
        kind = t.get("kind")
        while kind in ("NON_NULL", "LIST") and t.get("ofType"):
            t = t["ofType"]
            kind = t.get("kind")
        if kind in SKALAR:
            teile.append(f["name"])
        elif kind in ("OBJECT", "UNION", "INTERFACE"):
            inner = _selektion(url, tn, tiefe - 1, cache, pfad + (typname,))
            if inner:
                teile.append("%s %s" % (f["name"], inner))
    return "{ " + " ".join(teile) + " }"



# ── Schema lernen, wenn Introspection aus ist ────────────────────────────────
# 03.09.2026 — die Sonde auf dem Mac-Runner hat geliefert:
#
#   stake.com/_api/graphql   HTTP 400  "GraphQL introspection is not allowed by Apollo Server"
#   api.stake.com/graphql    HTTP 404  Cannot POST /graphql
#   stake.bet/_api/graphql   HTTP 403  Cloudflare-Challenge
#
# Der erste Treffer ist die gute Nachricht: der Endpunkt lebt, ist vom Mac aus erreichbar und
# antwortet als Apollo-Server mit sauberem JSON. Nur nachschlagen dürfen wir nicht.
#
# Raten wäre jetzt die naheliegende und die falsche Antwort — ein geratener Feldname, der zufällig
# existiert, aber das Falsche liefert, wäre genau die Sorte stiller Fehler, die uns hier schon
# zweimal Geld gekostet hat. Stattdessen wird gefragt, nur anders: graphql-js VALIDIERT das ganze
# Dokument, bevor es irgendetwas ausführt, und schreibt in die Fehler, was es stattdessen kennt.
#
#   Cannot query field "amont" on type "SportBet". Did you mean "amount"?
#   Field "highrollerSportBets" of type "[SportBet!]!" must have a selection of subfields.
#   Field "user" argument "id" of type "String!" is required, but it was not provided.
#
# Damit ist der Server sein eigenes Schema-Verzeichnis. Und weil die Validierung ALLE Fehler eines
# Dokuments auf einmal zurückgibt, kostet eine ganze Ebene genau EINE Anfrage: ein Kandidaten-
# Vokabular hinschicken, aus den Fehlern streichen, was es nicht gibt, aufklappen, was Unterfelder
# braucht, weitermachen. Nach drei bis vier Runden steht die Abfrage.
#
# Gelernt wird einmal; das Ergebnis liegt in stake_query.json und wird wiederverwendet, bis es
# bricht. Erst dann wird neu gelernt — nicht bei jedem Lauf.

RX_UNBEKANNT = re.compile(r'Cannot query field "([^"]+)" on type "([^"]+)"')
RX_BRAUCHT_SUB = re.compile(r'Field "([^"]+)" of type "([^"]+)" must have a selection of subfields')
RX_KEIN_SUB = re.compile(r'Field "([^"]+)" must not have a selection')
RX_MEINTEN = re.compile(r'Did you mean ([^?]+)\?')
RX_ARG_PFLICHT = re.compile(r'Field "([^"]+)" argument "([^"]+)"')

# Saatwörter für das Query-Feld. Sie müssen nicht stimmen — sie müssen nur NAH genug sein,
# damit graphql-js seinen „Did you mean"-Vorschlag mitschickt.
# 03.09.2026: „highroller" allein brachte nichts — graphql-js schlaegt nur vor, was LEXIKALISCH
# nah dran ist, und von „highroller" zu „highrollerSportBets" ist es zu weit. Deshalb mehrere
# Schreibweisen dicht am erwarteten Namen. Das ist kein Raten auf gut Glueck: ein Treffer wird
# daran erkannt, dass der Server „must have a selection of subfields" sagt — also bestaetigt,
# dass es das Feld gibt und dass es eine Liste von Objekten liefert. Was es dann wirklich
# liefert, steht vor dem ersten Sammeln in stake_schema_probe.json und wird angeschaut.
QUERY_SAAT = [
    "highrollerSportBets", "highrollerBets", "highRollerSportBets", "highRollerBets",
    "highrollerSports", "highrollers", "highRollers", "highroller",
    "sportBets", "sportsBets", "bets", "latestBets", "recentBets", "bigBets",
]

# Kandidaten je Ebene. Ein Name, den es nicht gibt, kostet nichts außer einer Zeile Fehlertext.
FELD_SAAT = [
    "id", "iid", "amount", "amountUsd", "currency", "value", "payout", "payoutMultiplier",
    "potentialMultiplier", "odds", "createdAt", "updatedAt", "placedAt", "status", "active",
    "user", "bet", "game", "outcomes", "outcome", "market", "fixture", "tournament",
    "category", "sport", "name", "slug", "startingAt", "type", "count",
]

LERN_RUNDEN = 8
LERN_TIEFE = 4

# Bekannte Grenze: Union- und Interface-Typen (etwa ein `bet`, das SportBet ODER CasinoBet sein
# kann) brauchen Inline-Fragmente, die dieser Lernweg nicht baut — solche Zweige fallen weg
# statt halb zu entstehen. Was oben liegt (Betrag, Waehrung, Quote, Zeit, Nutzer) reicht fuer
# die Sammlung; was fehlt, steht in stake_schema_probe.json und ist damit sichtbar, nicht still.


def _fehlertexte(url: str, query: str):
    st, d, err = _post(url, {"query": query})
    if d and d.get("errors"):
        return [str(e.get("message") or "") for e in d["errors"]], d, None
    return [], d, err


def _meinten(msg: str):
    """Die Namen aus 'Did you mean "a", "b", or "c"?'."""
    m = RX_MEINTEN.search(msg or "")
    return re.findall(r'"([^"]+)"', m.group(1)) if m else []


def _nackt(typ: str) -> str:
    """[SportBet!]! -> SportBet"""
    return (typ or "").strip("[]!")


def _feld_raten(url: str, protokoll: list = None):
    """-> (feldName, typName, notiz). Findet das Query-Feld über die Auskunft des Servers.

    Zwei Wege, beide aus derselben Quelle: „must have a selection of subfields" beweist, dass
    es das Feld GIBT und dass es Objekte liefert; „Did you mean ..." nennt Namen, auf die wir
    von allein nicht kommen. Apollo blendet die Vorschläge in Produktion oft aus — dann trägt
    der erste Weg allein, und deshalb steht dort eine Liste von Schreibweisen statt einer.
    """
    vorschlaege: dict = {}
    tot = 0
    for saat in QUERY_SAAT:
        msgs, _d, err = _fehlertexte(url, "{ %s }" % saat)
        if protokoll is not None:
            protokoll.append({"saat": saat, "fehler": msgs[:3] or None,
                              "transport": err[:160] if err else None})
        if err and not msgs:
            # Transportfehler (403, 404, Timeout) — nicht die Antwort des Schemas.
            tot += 1
            if tot >= 3:
                return None, None, "Endpunkt antwortet nicht: %s" % err
            continue
        for m in msgs:
            t = RX_BRAUCHT_SUB.search(m)
            if t and t.group(1) == saat:
                return saat, t.group(2), "direkt getroffen"
            for v in _meinten(m):
                vorschlaege[v] = vorschlaege.get(v, 0) + 1
    if not vorschlaege:
        return None, None, ("keine der %d Schreibweisen existiert, und der Server schlaegt "
                            "nichts vor (Apollo blendet 'Did you mean' in Produktion aus)"
                            % len(QUERY_SAAT))
    # Highroller schlägt alles, Sport schlägt Casino, danach der häufigste Vorschlag.
    def rang(n):
        k = n.lower()
        return (0 if "highroller" in k else 1, 0 if "sport" in k else 1, -vorschlaege[n], len(n))
    for name in sorted(vorschlaege, key=rang):
        msgs, _d, _e = _fehlertexte(url, "{ %s }" % name)
        for m in msgs:
            t = RX_BRAUCHT_SUB.search(m)
            if t and t.group(1) == name:
                return name, t.group(2), "ueber Vorschlag gefunden"
    return None, None, "Vorschlaege %s, keiner liefert eine Liste" % sorted(vorschlaege)


def _knoten(typ: str) -> dict:
    """Ein Kandidaten-Knoten: welcher Typ, und welche Feldnamen wir dort probieren."""
    return {"typ": typ, "felder": {x: None for x in FELD_SAAT}}


def _baue(feld: str, knoten: dict, arg: str = "") -> str:
    def sel(k):
        teile = []
        for n in sorted(k["felder"]):
            kind = k["felder"][n]
            teile.append(n + (" " + sel(kind) if kind else ""))
        return "{ " + " ".join(teile) + " }"
    return "query HR($limit: Int) { %s%s %s }" % (
        feld, "(%s: $limit)" % arg if arg else "", sel(knoten))


def _anwenden(knoten: dict, weg: dict, auf: dict, zu_flach: set, tiefe: int, ahnen: tuple = ()):
    """Eine Runde Erkenntnis in den Kandidatenbaum einarbeiten.

    03.09.2026 — hier steckte ein Denkfehler, den der nachgebaute Apollo-Server aufgedeckt hat:
    zuerst wurden die Fehler GLOBAL nach Feldnamen angewandt. `amount` ist auf SportBet gültig
    und auf User nicht, also strich „Cannot query field amount on type User" auch das gültige
    amount am Wurzelknoten — Runde für Runde, bis der Baum leer war und die Abfrage gar keine
    Selektion mehr hatte. Die Fehlermeldung nennt den TYP; also wird nach Typ gestrichen, und
    jeder Knoten weiß, welcher er ist.

    weg:       {typ: {feldname}}      nicht vorhanden auf diesem Typ
    auf:       {feldname: typname}    braucht Unterfelder, und zwar von diesem Typ
    zu_flach:  {feldname}             hat gar keine Unterfelder — Block wieder einklappen
    """
    t = knoten["typ"]
    for n in list(knoten["felder"]):
        if n in weg.get(t, ()):
            del knoten["felder"][n]
            continue
        kind = knoten["felder"][n]
        if kind is None:
            if n in auf:
                zt = _nackt(auf[n])
                # Zyklen (User -> bet -> user -> ...) und zu tiefe Zweige werden weggelassen,
                # nicht halb gebaut: eine ungültige Abfrage lernt nichts mehr dazu.
                if len(ahnen) + 1 < tiefe and zt not in ahnen and zt != t:
                    knoten["felder"][n] = _knoten(zt)
                else:
                    del knoten["felder"][n]
        else:
            if n in zu_flach:
                knoten["felder"][n] = None
                continue
            _anwenden(kind, weg, auf, zu_flach, tiefe, ahnen + (t,))
            if not kind["felder"]:
                del knoten["felder"][n]


def _zaehle(knoten: dict) -> int:
    return sum(1 + (_zaehle(v) if v else 0) for v in knoten["felder"].values())


def schema_lernen(url: str, feld: str, typ: str, arg: str = ""):
    """Baut die Selektion über die Validierungsfehler des Servers auf.

    Rückgabe: (queryText, notiz). Eine Ebene kostet EINE Anfrage, egal wie breit sie ist —
    graphql-js meldet alle Verstöße eines Dokuments gemeinsam.
    """
    wurzel = _knoten(_nackt(typ))
    fremd = []
    for runde in range(LERN_RUNDEN):
        if not wurzel["felder"]:
            return None, "Kandidatenbaum leer nach %d Runden — Saatliste passt nicht zum Schema" % runde
        q = _baue(feld, wurzel, arg)
        msgs, d, err = _fehlertexte(url, q)
        if not msgs:
            if err:
                return None, "Endpunkt bricht ab: %s" % err
            return q, "gelernt in %d Runde(n), %d Felder" % (runde + 1, _zaehle(wurzel))
        weg: dict = {}
        auf: dict = {}
        flach: set = set()
        for m in msgs:
            u = RX_UNBEKANNT.search(m)
            if u:
                weg.setdefault(u.group(2), set()).add(u.group(1))
                continue
            s = RX_BRAUCHT_SUB.search(m)
            if s and s.group(1) != feld:
                auf[s.group(1)] = s.group(2)
                continue
            f = RX_KEIN_SUB.search(m)
            if f:
                flach.add(f.group(1))
                continue
            a = RX_ARG_PFLICHT.search(m)
            if a:
                weg.setdefault(wurzel["typ"], set()).add(a.group(1))
                continue
            fremd.append(m[:140])
        if not (weg or auf or flach):
            # Fehler, die nichts mit der Struktur zu tun haben (Auth, Rate-Limit): abbrechen,
            # statt weiterzuprobieren und die Ursache zu verschleiern.
            return None, "unklarer Fehler: " + " | ".join(fremd[:3])
        _anwenden(wurzel, weg, auf, flach, LERN_TIEFE)
    return None, "nach %d Runden nicht konvergiert: %s" % (LERN_RUNDEN, " | ".join(fremd[:3]))


def _limit_arg(url: str, feld: str) -> str:
    """Nimmt der Server ein limit/first? Ohne Argument holt er sonst seinen Default."""
    for arg in ("limit", "first"):
        msgs, _d, _e = _fehlertexte(url, "query HR($limit: Int) { %s(%s: $limit) { __typename } }"
                                    % (feld, arg))
        if not any('Unknown argument "%s"' % arg in m for m in msgs):
            return arg
    return ""

def schema_finden(url: str, tiefe: int = 4):
    """-> (feldName, queryText, notiz) oder (None, None, grund)

    Zwei Wege, in dieser Reihenfolge:
      1. Introspection — der saubere. Stake hat sie zu (Apollo, 03.09.2026); ein anderer
         Endpunkt oder ein spaeteres Stake koennen sie wieder offen haben.
      2. Ueber die Validierungsfehler des Servers lernen (s. Block darueber).
    Geraten wird auf keinem der beiden Wege.
    """
    felder, err = _query_felder(url)
    if felder is not None:
        f = _waehle_feld(felder)
        if not f:
            return None, None, "kein Highroller-Feld unter %d Query-Feldern" % len(felder)
        tn = _typ_name(f.get("type") or {})
        sel = _selektion(url, tn, tiefe, {})
        if not sel:
            return None, None, "Rueckgabetyp %s ohne lesbare Felder" % tn
        args = f.get("args") or []
        argname = next((a["name"] for a in args
                        if (a.get("name") or "").lower() in ("limit", "first")), None)
        kopf = "query HR($limit: Int) { %s%s %s }" % (
            f["name"], "(%s: $limit)" % argname if argname else "", sel)
        return f["name"], kopf, "Introspection, Typ %s" % tn

    feld, typ, notiz = _feld_raten(url, _PROTOKOLL)
    if not feld:
        return None, None, "Introspection aus (%s); %s" % ((err or "?")[:120], notiz)
    arg = _limit_arg(url, feld)
    query, notiz2 = schema_lernen(url, feld, typ, arg)
    if not query:
        return None, None, "Feld %s vom Typ %s gefunden, Selektion nicht: %s" % (feld, typ, notiz2)
    return feld, query, "aus Fehlermeldungen gelernt (%s -> %s), %s" % (feld, typ, notiz2)

# ── Die verifizierte Abfrage ─────────────────────────────────────────────────
# 03.09.2026 — nicht geraten und nicht erschlossen, sondern MITGELESEN: Stakes eigene
# Highroller-Seite schickt genau diese Anfrage an genau diesen Endpunkt. Damit ist der
# Lernweg unten nur noch das Netz für den Tag, an dem Stake umbaut.
#
# Was der Feed hergibt (an einem echten Datensatz geprüft):
#   iid              "sport:648201156"   ← die Wett-Nummer, die auch auf dem Wettschein steht
#   amount/currency  2360 / "usdc"       ← in der Währung der Wette, nicht in USD
#   potentialMultiplier                  ← Gesamtquote der Wette
#   outcomes[]       je Bein: odds, outcome.name ("Taylor Fritz"), market.name ("Winner"),
#                    fixtureName, fixture.startTime, tournament.name, sport.slug
#
# Und drei Dinge, die man wissen muss, bevor man daraus etwas ableitet:
#
#  1. `user` ist IMMER null. Stake anonymisiert die Highroller-Liste vollständig. Ein
#     Track-Record je Konto — das, was die Poly-Wallets tragen — ist hier unmöglich.
#     Es gibt nur aggregierten Fluss, nie „dieser Spieler hat wieder recht behalten".
#  2. limit ist bei 50 gedeckelt, und 51 liefert STILL eine leere Liste. Kein Fehler,
#     keine Warnung — genau die Sorte Antwort, die im Dashboard wie ein ruhiger Tag
#     aussieht. Deshalb wird hier hart gedeckelt und eine leere Antwort als „leer"
#     ausgewiesen, nicht als „ok".
#  3. Zeitstempel kommen als RFC-1123 ("Thu, 03 Sep 2026 19:11:23 GMT"), nicht als ISO.
#     datetime.fromisoformat scheitert daran — jede Wette wäre stumm aus dem Fenster
#     gefallen und das Dashboard hätte „keine großen Wetten" gezeigt.
FELD_BEKANNT = "highrollerSportBets"
MAX_LIMIT = 50          # 51 gibt kommentarlos 0 Einträge zurück
QUERY_BEKANNT = """query HR($limit: Int) {
  highrollerSportBets(limit: $limit) {
    __typename id iid
    bet {
      __typename
      ... on SportBet {
        id customBet createdAt updatedAt potentialMultiplier amount currency
        user { id name }
        outcomes {
          id odds status fixtureName fixtureAbreviation
          outcome { id name }
          market { id name }
          fixture { id startTime tournament { id name slug category { sport { slug name } } } }
        }
      }
    }
  }
}"""


# ── Normalisierung ───────────────────────────────────────────────────────────
def _sammle(obj, schluessel: set, tiefe: int = 8):
    """Sucht rekursiv nach Schlüsseln (klein geschrieben) und gibt alle Treffer."""
    fund = []
    if tiefe <= 0:
        return fund
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() in schluessel and not isinstance(v, (dict, list)):
                fund.append((k, v))
            fund.extend(_sammle(v, schluessel, tiefe - 1))
    elif isinstance(obj, list):
        for v in obj[:30]:
            fund.extend(_sammle(v, schluessel, tiefe - 1))
    return fund


def _erst(obj, schluessel: set, typ=None):
    for _k, v in _sammle(obj, schluessel):
        if v is None:
            continue
        if typ is float:
            try:
                return float(v)
            except Exception:
                continue
        if typ is str and not isinstance(v, str):
            continue
        return v
    return None


def _pfad(obj, *namen):
    """Ein Wert entlang eines bekannten Pfades. Fehlt ein Glied, ist es None —
    kein Default, kein Ersatzwert."""
    k = obj
    for n in namen:
        if not isinstance(k, dict):
            return None
        k = k.get(n)
    return k


def _zeit(s):
    """Stakes Zeitstempel sind RFC-1123, nicht ISO. Rückgabe: ISO-UTC oder None.

    03.09.2026 — hier hätte der stillste Fehler des ganzen Features gesessen: der Sammler
    hätte 'Thu, 03 Sep 2026 19:11:23 GMT' gespeichert, das Fenster hätte per fromisoformat
    keine einzige Wette einordnen können, und das Dashboard hätte eine vollständig gefüllte
    Sammlung als 'keine großen Wetten im Fenster' gezeigt."""
    if not s or not isinstance(s, str):
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        try:
            d = parsedate_to_datetime(s)
        except Exception:
            return None
    if d is None:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _usd(betrag, waehrung, roh, kurse: dict = None) -> tuple:
    """-> (usd:float|None, grund:str). None heißt unbekannt, NICHT null."""
    direkt = _erst(roh, {"amountusd", "usdamount", "amountindollars", "usdvalue"}, float)
    if direkt is not None:
        return float(direkt), "feld"
    if betrag is None:
        return None, "kein_betrag"
    w = (waehrung or "").lower()
    if w in STABLE:
        return float(betrag), "stablecoin"
    k = ((kurse or {}).get("usd") or {}).get(w)
    if isinstance(k, (int, float)) and k > 0:
        alter = _kurs_alter_h(kurse)
        if alter is not None and alter > KURS_MAX_ALTER_H:
            # Ein Kurs von gestern taugt fuer eine Groessenordnung, aber er soll sich nicht
            # als frisch ausgeben — der Grund traegt das Alter mit.
            return float(betrag) * float(k), "kurs_alt_%dh" % round(alter)
        return float(betrag) * float(k), "kurs"
    return None, "kurs_fehlt:" + (w or "?")


def normalisiere(rec: dict, kurse: dict = None) -> dict:
    """Aus einem rohen Highroller-Eintrag die Felder ziehen, die wir brauchen.

    Erst über den bekannten Pfad (verifiziert am echten Feed), dann als Netz über
    Feldnamen irgendwo in der Tiefe — falls Stake umbaut, soll wenigstens der Betrag
    noch ankommen, statt dass die Zeile still leer wird.
    """
    b = rec.get("bet") if isinstance(rec.get("bet"), dict) else {}
    beine = b.get("outcomes") if isinstance(b.get("outcomes"), list) else []
    erst = beine[0] if beine and isinstance(beine[0], dict) else {}

    wid = rec.get("iid") or rec.get("id") or _erst(rec, {"id", "iid", "betid"}, str)
    ts = _zeit(b.get("createdAt")) or _zeit(_erst(rec, {"createdat", "placedat", "timestamp"}, str))
    betrag = b.get("amount")
    if betrag is None:
        betrag = _erst(rec, {"amount", "wager", "stake", "betamount"}, float)
    try:
        betrag = float(betrag) if betrag is not None else None
    except Exception:
        betrag = None
    waehrung = b.get("currency") or _erst(rec, {"currency", "currencyname"}, str)
    quote = b.get("potentialMultiplier")
    if quote is None:
        quote = _erst(rec, {"odds", "totalodds", "potentialmultiplier"}, float)
    try:
        quote = float(quote) if quote is not None else None
    except Exception:
        quote = None

    usd, usd_grund = _usd(betrag, waehrung, rec, kurse)
    kombi = len(beine) > 1

    # 03.09.2026 (Lucas: „@1,03 und 1,2 ist schon relativ low") — der Einsatz allein ist ein
    # schiefes Mass. Gemessen an 445 Wetten: die 32%, die unter Quote 1,35 liegen, tragen 35%
    # des EINSATZES, aber nur 3% des MOEGLICHEN GEWINNS. Eine Liste nach Einsatz sortiert
    # deshalb systematisch Favoritenschieber nach oben — $264k auf Sakkari @1,20 riskiert eine
    # Viertelmillion, um $53k zu gewinnen.
    # Umgekehrt taugt der moegliche Gewinn allein auch nicht: an der Spitze steht dann $3.260
    # @298,98 auf ein Tennis-Aussenseiterspiel, also ein Lottoschein. Deshalb faehrt beides mit
    # und keins ersetzt das andere.
    gewinn = None
    if usd is not None and quote is not None and quote > 1:
        gewinn = round(usd * (quote - 1), 2)

    # 03.09.2026 — im ersten echten Ledger waren 77 von 93 Wetten LIVE gesetzt, nur 16 vor
    # Anpfiff. Das sind zwei verschiedene Dinge und muessen getrennt gemessen werden:
    #   · vor Anpfiff → CLV gegen den Pinnacle-Schlusskurs ist moeglich
    #   · live        → es gibt keinen Schlusskurs; hier zaehlt nur die Abrechnung, UND die
    #                   Spielminute, denn ein Einsatz in der 85. Minute auf den Fuehrenden ist
    #                   kein Signal, sondern jemand, der 1,05 einsammelt (Hapoel-Fall).
    # Ohne Anpfiff-Angabe bleibt die Phase "unbekannt" — nicht "vor", nicht "live".
    ts_iso, ko_iso = ts, _zeit(_pfad(erst, "fixture", "startTime"))
    phase, minute = "unbekannt", None
    if ts_iso and ko_iso:
        try:
            t_bet = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
            t_ko = datetime.fromisoformat(ko_iso.replace("Z", "+00:00"))
            delta = (t_bet - t_ko).total_seconds() / 60.0
            if delta > 0:
                phase, minute = "live", int(round(delta))
            else:
                phase, minute = "vor", None
        except Exception:
            pass

    return {
        "id": str(wid) if wid is not None else None,
        "ts": ts,
        # Stake anonymisiert die Liste vollstaendig — das Feld bleibt, damit sichtbar ist,
        # DASS es leer ist, statt dass jemand spaeter einen Track-Record darauf plant.
        "user": _pfad(b, "user", "name"),
        "betrag": betrag,
        "waehrung": (waehrung or "").lower() or None,
        "einsatzUsd": usd,
        "usdGrund": usd_grund,
        "quote": quote,
        "gewinnUsd": gewinn,
        "kombi": kombi,
        "nBeine": len(beine) or None,
        "eigenbau": bool(b.get("customBet")),
        "sport": _pfad(erst, "fixture", "tournament", "category", "sport", "slug"),
        "kat": sport_kategorie(_pfad(erst, "fixture", "tournament", "category", "sport", "slug"),
                               _pfad(erst, "fixture", "tournament", "name")),
        "liga": _pfad(erst, "fixture", "tournament", "name"),
        "ligaSlug": _pfad(erst, "fixture", "tournament", "slug"),
        # 🔴 22.09.2026 — die Abfrage holt `tournament { id name slug }` seit jeher, und die
        # `id` wurde hier weggeworfen. Der Slug ist der schlechtere Schluessel: er ist weder
        # sportarten- noch laenderrein. Unter `pro-league` liegen die uzbekische Pro League
        # (Ebene 2) und Volleyball, unter `1st-division` daenische und norwegische ZWEITE
        # Ligen neben der zyprischen ERSTEN. `stake_liga_stufe.py` musste deshalb heute zwei
        # Eintraege auf „mehrdeutig" zuruecknehmen — mit der Turnier-ID ist die Frage
        # eindeutig beantwortbar, ohne sie bleibt sie es nicht.
        #
        # Nur mitschreiben, nichts entscheiden: die ID sammelt sich erst. Ein Wert, der noch
        # nicht da ist, darf keine Regel tragen.
        "ligaId": _pfad(erst, "fixture", "tournament", "id"),
        "event": erst.get("fixtureName") or _erst(rec, {"fixturename", "eventname"}, str),
        "eventId": _pfad(erst, "fixture", "id"),
        "markt": _pfad(erst, "market", "name"),
        "auswahl": _pfad(erst, "outcome", "name"),
        "auswahlId": _pfad(erst, "outcome", "id") or erst.get("id"),
        "beinQuote": erst.get("odds"),
        "status": erst.get("status"),
        "anpfiff": ko_iso,
        "phase": phase,
        # Minuten seit Anpfiff. Nicht die offizielle Spielminute (die kennt der Feed nicht) —
        # verstrichene Zeit, inklusive Halbzeit. Fuer „frueh oder spaet" reicht das.
        "spielminute": minute,
    }


# ── Ledger ───────────────────────────────────────────────────────────────────
def _lade(pfad: Path, standard):
    try:
        with open(pfad, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return standard


def _schreibe(pfad: Path, daten):
    tmp = pfad.with_suffix(pfad.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(daten, f, ensure_ascii=False, separators=(",", ":"))
    tmp.replace(pfad)


def luecke_messen(alt: dict, neu: list) -> dict:
    """Haben wir zwischen zwei Abrufen Wetten verpasst?

    03.09.2026 — erster echter Lauf: 50 Wetten deckten 12 Minuten ab, der Job lief alle 15.
    Der Feed gibt hoechstens 50 Eintraege her (MAX_LIMIT); ist der aelteste davon JUENGER als
    der juengste des letzten Laufs, liegt dazwischen ein Loch, aus dem wir nichts gesehen haben.
    Das passiert genau dann, wenn viel los ist — also wenn es am meisten zaehlt.

    Gemessen wird es, weil eine Luecke, die niemand sieht, spaeter als "an dem Abend war halt
    wenig los" durchgeht.
    """
    vorher = [w.get("ts") for w in (alt.get("wetten") or []) if w.get("ts")]
    frisch = [w.get("ts") for w in neu if w.get("ts")]
    if not vorher or not frisch:
        return {"luecke": None, "grund": "erster Lauf"}
    juengster_alt = max(vorher)
    aeltester_neu = min(frisch)
    if aeltester_neu <= juengster_alt:
        return {"luecke": False, "abdeckungMin": _minuten(min(frisch), max(frisch))}
    return {"luecke": True, "lueckeMin": _minuten(juengster_alt, aeltester_neu),
            "abdeckungMin": _minuten(min(frisch), max(frisch))}


def _minuten(a, b):
    try:
        da = datetime.fromisoformat(str(a).replace("Z", "+00:00"))
        db = datetime.fromisoformat(str(b).replace("Z", "+00:00"))
        return round((db - da).total_seconds() / 60.0, 1)
    except Exception:
        return None


def luecken_bilanz(alt: dict, luecke: dict, jetzt: str) -> dict:
    """Wie oft und wie lange war der Feed unbeobachtet? Fortlaufend. REIN.

    🔴 13.09.2026 (Lucas: „die Erhoehung der Abrufe hat eh keine negativen Auswirkungen?").
    Doch, sie hat: die repo-weite Schedule-Last liegt bei 534 Laeufen/Tag, der Deckel bei 540,
    und er steht dort, weil GitHub ab ~585 anfing, Schedules zu verzoegern und zu verschlucken
    (test_cron_schedule_hygiene). `betfair.yml` von 10 auf 5 Minuten waere +144/Tag — also 678,
    deutlich ueber der Schwelle, ab der ALLE Laeufe unzuverlaessig werden. Dazu doppelt so viele
    Burst-Pushes und doppelte Last auf dem Mac.

    `luecke_messen` rechnet die Luecke seit dem 03.09. je Lauf aus — und warf sie jedes Mal weg
    bis auf den letzten Wert. Damit war „wie oft passiert das?" nicht zu beantworten, und genau
    das ist die Zahl, die entscheidet, ob sich der Takt lohnt. Jetzt laeuft sie mit.

    Kein Urteil hier: erst messen, dann Schedule-Budget ausgeben.
    """
    a = dict(alt or {})
    a["laeufe"] = int(a.get("laeufe") or 0) + 1
    a["seit"] = a.get("seit") or jetzt
    a["zuletzt"] = jetzt
    if not isinstance(luecke, dict) or luecke.get("luecke") is None:
        return a                       # erster Lauf / kein Vergleich moeglich
    if luecke.get("luecke"):
        a["mitLuecke"] = int(a.get("mitLuecke") or 0) + 1
        m = luecke.get("lueckeMin")
        if isinstance(m, (int, float)):
            a["minutenBlind"] = round(float(a.get("minutenBlind") or 0) + float(m), 1)
            a["laengsteMin"] = max(float(a.get("laengsteMin") or 0), float(m))
    ab = luecke.get("abdeckungMin")
    if isinstance(ab, (int, float)):
        n = int(a.get("mitAbdeckung") or 0) + 1
        a["mitAbdeckung"] = n
        a["abdeckungMinSchnitt"] = round(
            ((a.get("abdeckungMinSchnitt") or 0) * (n - 1) + float(ab)) / n, 1)
    v = int(a.get("laeufe") or 0)
    a["anteilMitLueckePct"] = round(100.0 * int(a.get("mitLuecke") or 0) / v, 1) if v else None
    return a


def ledger_mischen(alt: dict, neu: list, jetzt: str) -> dict:
    """Dedupliziert über die Wett-ID. Einträge ohne ID werden verworfen —
    ohne ID kann man nicht deduplizieren, und doppelt gezähltes Geld ist
    schlimmer als eine fehlende Wette."""
    wetten = list(alt.get("wetten") or [])
    bekannt = {w.get("id") for w in wetten if w.get("id")}
    zugang = 0
    ohne_id = 0
    for w in neu:
        if not w.get("id"):
            ohne_id += 1
            continue
        if w["id"] in bekannt:
            continue
        bekannt.add(w["id"])
        wetten.append(w)
        zugang += 1
    # 04.09.2026 (Lucas: „geh die verbleibenden Duplikate durch"). Alt-Zeilen ohne `kat`
    # zwangen das Frontend zu einem NACHBAU von sport_kategorie() — und der hat schon einmal
    # geschadet: „Chicago Cubs – Milwaukee Brewers" stand trotz US-Sport-Sperre in einer Kachel,
    # weil der Filter `w.kat || ''` las und die Alt-Zeile leer war. Der Nachbau ist seither eine
    # zweite Wahrheit, die niemand mitpflegt.
    #
    # Statt das Raten im Frontend zu pflegen: hier nachtragen. Danach traegt JEDE Zeile ihre
    # Kategorie, und das Frontend braucht keine eigene Regel mehr.
    nachgetragen = 0
    for w in wetten:
        if not w.get("kat"):
            w["kat"] = sport_kategorie(w.get("sport"), w.get("liga"))
            nachgetragen += 1
        # Immer neu gesetzt (billig, und eine Regelaenderung wirkt sofort auf den Bestand).
        w["langzeit"] = ist_langzeit(w)
    wetten.sort(key=lambda w: (w.get("ts") or ""), reverse=True)
    if len(wetten) > LEDGER_KEEP:
        wetten = wetten[:LEDGER_KEEP]
    return {
        "seit": alt.get("seit") or jetzt,
        "aktualisiert": jetzt,
        "n": len(wetten),
        "zugangLetzterLauf": zugang,
        "katNachgetragen": nachgetragen,   # 04.09.2026: Alt-Zeilen ohne Kategorie
        "ohneIdVerworfen": ohne_id,
        "wetten": wetten,
    }


def _im_fenster(w: dict, ab: datetime) -> bool:
    ts = w.get("ts")
    if not ts:
        return False
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return False
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d >= ab


def sicht_bauen(ledger: dict, jetzt: datetime, status: str, endpunkt: str,
                feld: str, notiz: str, kurse: dict = None) -> dict:
    ab = jetzt - timedelta(hours=FENSTER_H)
    im_fenster = [w for w in (ledger.get("wetten") or []) if _im_fenster(w, ab)]
    ueber = [w for w in im_fenster
             if w.get("einsatzUsd") is not None and w["einsatzUsd"] >= MIN_USD]
    unklar = [w for w in im_fenster if w.get("einsatzUsd") is None]
    return {
        "asof": jetzt.isoformat().replace("+00:00", "Z"),
        "status": status,
        "endpunkt": endpunkt,
        "feld": feld,
        "notiz": notiz,
        "schwelleUsd": MIN_USD,
        "fensterH": FENSTER_H,
        "sammlungSeit": ledger.get("seit"),
        "nLedger": ledger.get("n") or 0,
        "nFenster": len(im_fenster),
        "nUeberSchwelle": len(ueber),
        "nEinsatzUnbekannt": len(unklar),
        "kurse": {k: v for k, v in (kurse or {}).items() if k != "usd"},
        # Eine Quelle fuer Tab und Auswertung — der Filter wird nicht zweimal definiert.
        "gesperrt": sorted(GESPERRT),
        "gesperrtLigenMuster": GESPERRT_LIGEN_MUSTER,
        "luecke": ledger.get("luecke") or {},
        # 🔴 17.09.2026 (Lucas schickt einen Fremd-Radar-Post: „Kasachstan Pervaya Liga, 6 Wetten
        # in 3 Minuten — sowas findest du nicht? Gab's nicht in unserem Feed?").
        #
        # Gab es nicht: null Zeilen aus Kasachstan in 20.000 Wetten. Der Grund steht seit jeher
        # in der Datei, nur nicht auf der Flaeche: `luecke` zeigt den LETZTEN Lauf („Abruf deckt
        # 37 min") und liest sich gesund, waehrend die BILANZ sagt, dass in 138 von 369 Laeufen
        # eine Luecke entstand — 855,8 Minuten blind seit dem 13.09., laengste Einzelluecke
        # 24,3 Minuten. Der Feed gibt hoechstens 50 Eintraege her (MAX_LIMIT; 51 liefert
        # kommentarlos null), das sind bei 2,4 Wetten/Minute rund 21 Minuten Gedaechtnis — und
        # der Sammler haengt am 10-Minuten-Takt von betfair.yml, der nicht immer puenktlich ist.
        #
        # Eine Momentaufnahme dort, wo eine Bilanz hingehoert: dieselbe Fehlerklasse wie ueberall
        # hier. Also wandert die Bilanz mit in die Sicht.
        "luecken": ledger.get("luecken") or {},
        # Die Sicht trägt die ROHEN Wetten im Fenster — gruppiert und gefiltert wird
        # im Frontend, damit Lucas die Schwellen live drehen kann.
        "wetten": im_fenster[:VIEW_KEEP],
        "belegt": False,
        "hinweis": ("Reine Sammlung. Für Stake-Einsatzfluss gibt es im Projekt noch keine "
                    "gemessene Trefferquote und keinen gemessenen CLV. Die Liste zeigt nur "
                    "Konten, die ihre Wetten sichtbar lassen — das ist eine Auswahl, keine "
                    "Grundgesamtheit."),
    }


# ── Hauptlauf ────────────────────────────────────────────────────────────────
def holen(sonde: bool = False):
    jetzt = datetime.now(timezone.utc)
    jetzt_s = jetzt.isoformat().replace("+00:00", "Z")
    urls = [os.environ["STAKE_ENDPUNKT"]] if os.environ.get("STAKE_ENDPUNKT") else ENDPUNKTE

    versuche = []
    feld = query = None
    url_ok = ""

    # Gelernt wird EINMAL, nicht alle 15 Minuten: Lernen kostet ein gutes Dutzend Anfragen,
    # das Ergebnis haelt, bis Stake das Schema umbaut. Der Cache wird deshalb zuerst probiert
    # und erst verworfen, wenn er wirklich nicht mehr traegt.
    cache = _lade(QUERY_FILE, {})
    if not sonde and cache.get("query") and cache.get("url") in urls:
        st, d, err = _post(cache["url"], {"query": cache["query"],
                                          "variables": {"limit": min(ABRUF_LIMIT, MAX_LIMIT)}})
        if d and not d.get("errors") and (d.get("data") or {}).get(cache.get("feld")) is not None:
            feld, query, url_ok = cache["feld"], cache["query"], cache["url"]
            versuche.append({"url": url_ok, "feld": feld, "notiz": "aus stake_query.json"})

    # Zweiter Weg: die verifizierte Abfrage. Sie stammt aus der Netzwerkanfrage, die Stakes
    # eigene Seite stellt — kein Raten, und billiger als jedes Lernen.
    if not feld:
        for url in urls:
            st, d, err = _post(url, {"query": QUERY_BEKANNT,
                                     "variables": {"limit": min(ABRUF_LIMIT, MAX_LIMIT)}})
            if d and not d.get("errors") and isinstance((d.get("data") or {}).get(FELD_BEKANNT), list):
                feld, query, url_ok = FELD_BEKANNT, QUERY_BEKANNT, url
                versuche.append({"url": url, "feld": feld, "notiz": "verifizierte Abfrage"})
                if not sonde:
                    _schreibe(QUERY_FILE, {"url": url, "feld": feld, "query": query,
                                           "gelernt": jetzt_s, "notiz": "verifizierte Abfrage"})
                break
            versuche.append({"url": url, "feld": None, "notiz": "verifizierte Abfrage greift nicht: %s"
                             % (err or json.dumps((d or {}).get("errors"))[:160])})

    # Dritter Weg: aus den Fehlermeldungen lernen — das Netz fuer den Tag, an dem Stake umbaut.
    if not feld:
        for url in urls:
            f, q, notiz = schema_finden(url)
            versuche.append({"url": url, "feld": f, "notiz": notiz})
            if f:
                feld, query, url_ok = f, q, url
                _schreibe(QUERY_FILE, {"url": url, "feld": f, "query": q,
                                       "gelernt": jetzt_s, "notiz": notiz})
                break

    if not feld:
        sicht = sicht_bauen(_lade(LEDGER_FILE, {}), jetzt, "schema_unbekannt", "", "",
                            "; ".join("%s: %s" % (v["url"], v["notiz"]) for v in versuche))
        if sonde:
            _schreibe(PROBE_FILE, {"asof": jetzt_s, "status": "schema_unbekannt",
                                   "versuche": versuche, "feldsuche": _PROTOKOLL[:40]})
            print("\n──── was der Server auf jede Schreibweise sagt ────")
            for e in _PROTOKOLL[:40]:
                print(" %-22s %s" % (e["saat"], (e["fehler"] or [e["transport"] or "—"])[0][:150]))
            print(json.dumps(versuche, ensure_ascii=False, indent=2))
            return 1
        _schreibe(VIEW_FILE, sicht)
        print("kein Schema gefunden:", json.dumps(versuche, ensure_ascii=False))
        return 1

    st, d, err = _post(url_ok, {"query": query,
                                "variables": {"limit": min(ABRUF_LIMIT, MAX_LIMIT)}})
    roh = ((d or {}).get("data") or {}).get(feld)
    if isinstance(roh, dict):
        for k in ("data", "results", "items", "edges", "nodes"):
            if isinstance(roh.get(k), list):
                roh = roh[k]
                break
    if not isinstance(roh, list):
        roh = []

    if sonde:
        _schreibe(PROBE_FILE, {
            "asof": jetzt_s, "status": "ok" if roh else "leer", "endpunkt": url_ok,
            "feld": feld, "query": query, "httpStatus": st, "fehler": err,
            "nRoh": len(roh), "beispielRoh": roh[:2],
            "beispielNormalisiert": [normalisiere(r, kurse_holen(url_ok, _lade(KURS_FILE, {})))
                                     for r in roh[:5]],
            "graphqlFehler": (d or {}).get("errors"),
            "feldsuche": _PROTOKOLL[:40],
        })
        print("Endpunkt : %s\nFeld     : %s\nHTTP     : %s\nEinträge : %d"
              % (url_ok, feld, st, len(roh)))
        if roh:
            print("\nErster Eintrag normalisiert:")
            print(json.dumps(normalisiere(roh[0]), ensure_ascii=False, indent=2))
        else:
            print("Fehler:", err, (d or {}).get("errors"))
        return 0 if roh else 1

    # Kurse einmal je Lauf; scheitert der Abruf, gilt der letzte bekannte weiter (mit Alter).
    kurse = kurse_holen(url_ok, _lade(KURS_FILE, {}))
    if kurse.get("usd"):
        _schreibe(KURS_FILE, kurse)
    neu = [normalisiere(r, kurse) for r in roh]
    vorher = _lade(LEDGER_FILE, {})
    luecke = luecke_messen(vorher, neu)
    ledger = ledger_mischen(vorher, neu, jetzt_s)
    ledger["luecke"] = luecke
    ledger["luecken"] = luecken_bilanz(vorher.get("luecken"), luecke, jetzt_s)
    # 13.10.2026: was stake_settle.py oben ins Ledger schreibt (Bilanz, Zeitpunkt, Laufdiagnose),
    # ging hier bei jedem Abruf verloren — ledger_mischen baut das Dict neu. Mitnehmen.
    for k in ("bilanz", "abgerechnet", "abrechnungLauf"):
        if k in (vorher or {}) and k not in ledger:
            ledger[k] = vorher[k]
    _schreibe(LEDGER_FILE, ledger)
    status = "ok" if roh else ("leer" if not err else "fehler")
    _schreibe(VIEW_FILE, sicht_bauen(ledger, jetzt, status, url_ok, feld, err or "", kurse))
    print("Stake: %d geholt, %d neu, Ledger %d, Fenster %dh, Schwelle $%.0f"
          % (len(roh), ledger["zugangLetzterLauf"], ledger["n"], FENSTER_H, MIN_USD))
    if luecke.get("luecke"):
        print("  ⚠️  Luecke: %s Min ungesehen (dieser Abruf deckt %s Min ab, %d Eintraege = Deckel)"
              % (luecke.get("lueckeMin"), luecke.get("abdeckungMin"), len(roh)))
    elif luecke.get("abdeckungMin") is not None:
        print("  Abdeckung dieses Abrufs: %s Min" % luecke["abdeckungMin"])
    return 0 if roh else 1


if __name__ == "__main__":
    sys.exit(holen(sonde="--sonde" in sys.argv))
