#!/usr/bin/env python3
"""
fetch_betfair_betwatch.py — Betfair-Exchange-Odds + gematchte VOLUMINA via Betwatch (28.07.2026, Lucas).

Betfair direkt geht aus Österreich nicht (Konto-Geoblock) → Umweg über Betwatch (betwatch.fr), ein
EU-Aggregator, der Betfair-Odds UND Volumina liefert. Nur Football (100€-Tarif = alle Fußball-Ligen).

Zwei Produkte hängen dran:
  A) Top-5 + MLS: volumen-gewichtetes Sharp-Signal (Steam, Betfair-vs-Pinnacle, besserer CLV).
  B) Alle anderen Ligen (HT-Fokus): wo liegt das Geld / auffällige einseitige Bewegungen +
     Liga-Profitabilitäts-Backtest.

## API (Django REST) — an echten Antworten verifiziert 29.07.2026
  Auth:    ?key={BETWATCH_KEY}  (Query-Param! Authorization-Header → HTTP 403)
  Base:    https://betwatch.fr/api/v1
  GET /football/events            → [{match_id, teams{v1,v2}, league, country, kickoff, live_info}]
  GET /football/event/{match_id}  → {..., league_id, markets:[{market_id, name, average_volume,
                                     last_checked, runners:[{runner_id, name, odd, volume}]}]}
  GET /football/live              → wie events + live_info{time,is_ht,goal_v1,goal_v2,red_*,finished} + markets
  Märkte: "Match Odds" · "Both teams to Score?" · "Over/Under X.5 Goals" (0.5–8.5) · "Correct Score" ·
          "Draw no Bet" · "Half Time" · "First Half Goals 0.5/1.5/2.5" · "Half Time/Full Time".
  average_volume = gematchtes £-Volumen je Markt (der Money-Indikator). runner.odd = Betfair-Preis.

## Ausgabe
  betfair_prices.json   — {matches:[snapshot...], _meta:{generatedAt, n, live}}.
  betfair_history.json  — {matchId: [{ts, totalVol, mo:{hw,dr,aw,vol}}...], _meta}. Für Steam + Volumen-Delta.

## Env
  BETWATCH_KEY          — API-Token (GitHub-Secret). Ohne Key: No-Op (kein Wipe).
  BETWATCH_MAX_DETAIL   — Deckel für /event/{id}-Detailcalls je Lauf (Default 150).
  BETWATCH_WINDOW_H     — Prematch-Fenster in Std. für Detailcalls (Default 26).

Reiner Kern (parse_events, devig_1x2, build_snapshot, select_ids, append_history) ist netz-frei/testbar;
nur main() ruft die API. Defensiv: bei Fetch-Fehler NIE gute Daten überschreiben (Wipe-Guard).
"""
import json
import os
import re
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

# money-radar (17.10.2026): Kopie aus blummabet/betting-dashboard, läuft hier auf GitHub-Servern
# alle 5 Min. Dateien liegen in .bf/ (Actions-Cache), nicht im Repo.
BASE = Path(__file__).resolve().parent / ".bf"
BASE.mkdir(exist_ok=True)
PRICES_FILE = BASE / "betfair_prices.json"
HISTORY_FILE = BASE / "betfair_history.json"
API_BASE = "https://betwatch.fr/api/v1"
KEY = (os.environ.get("BETWATCH_KEY") or "").strip()
HTTP_TIMEOUT = 20
MAX_DETAIL = int(os.environ.get("BETWATCH_MAX_DETAIL") or 220)   # ~0,3 s je Abruf, passt in den 5-Min-Takt
WINDOW_H = float(os.environ.get("BETWATCH_WINDOW_H") or 26)
# Top-5 + MLS mit LÄNGEREM Vorlauf erfassen (72h statt 26h), sonst tauchen sie erst ~1 Tag vor
# Anpfiff auf — für Signale/Triple zu spät (29.07.2026, Lucas). Betwatch-Namen sind land-präfixiert
# ("German Bundesliga" …). Modest gehalten: 3 Tage, keine 2 Wochen.
PRIORITY_WINDOW_H = float(os.environ.get("BETWATCH_PRIO_WINDOW_H") or 72)
PRIORITY_RX = re.compile(r"(german bundesliga|english premier league|spanish la ?liga|italian serie a|"
                         r"french ligue 1|major league soccer|\bmls\b)", re.I)
# 08.09.2026 (Lucas: „vorher war Real Madrid drin, heute Champions League mit dem meisten Geld …
# nun sind beide Spiele verschwunden").
#
# Genau das war es, und es lag am Deckel: `select_ids` holt Detail-Calls in der Reihenfolge
# live → Prioritaets-Ligen (72h) → alles andere im 26h-Fenster NACH ANPFIFF sortiert, gedeckelt
# bei MAX_DETAIL=150. Die Champions League stand in keiner Prioritaets-Liste, also lag sie im
# dritten Topf — und der war heute frueh voll, bevor 21:00 Uhr an der Reihe war.
#
# Gemessen an den beiden Laeufen dieses Morgens:
#   04:57 UTC → 125 Spiele (unter dem Deckel) → Real Madrid v Inter mit **102.861 €** drin
#   07:57 UTC → 150 Spiele (= Deckel)         → ALLE VIER CL-Spiele weg (Real Madrid, Dortmund,
#                                               Porto v Man City, Lille v Betis), dazu Boca,
#                                               Fluminense, Bolton v West Ham
#   dafuer neu: 19 Scottish Challenge Cup, 13 English National League Cup, 6 Portuguese U23 …
#
# Das Spiel mit dem meisten Geld des Tages fiel also raus, weil vierzig Pokalspiele frueher
# anpfeifen. Der Push war da schon draussen — die Flaeche, auf die er zeigte, war leer.
#
# Zwei Ursachen, beide behoben:
#  1. Die Prioritaets-Definition wich von der ab, die der VERBRAUCHER benutzt: betfair-radar.js
#     stuft mit `tierOf()` in top5 / UEFA+International / Rest und gibt den ersten beiden
#     dieselbe Schwelle. Die hier war eine zweite, kuerzere Liste. Jetzt dieselbe Definition.
#  2. Der Rest-Topf sortierte nach ANPFIFF. Das ist die einzige Achse, die nichts darueber sagt,
#     ob uns ein Spiel interessiert. Jetzt zuerst nach dem Geld, das wir vom letzten Lauf schon
#     KENNEN (betfair_prices.json), dann nach Anpfiff. Ein Spiel mit 100k faellt damit nicht
#     mehr aus dem Fenster, nur weil es spaeter beginnt — und das gilt auch fuer Wettbewerbe,
#     die auf keiner Liste stehen (Libertadores, Sudamericana …).
# 12.10.2026 (Lucas: „Orsomarso – Boca Juniors de Cali … Half Time 11.904 € auf Boca, 96 % …
# Wieso finden wir dieses Spiel nicht? Kein Push in Trades, nichts in der HT-Kachel, nichts im
# Radar. Das Geld schon min. 30 min oben").
#
# Es lag wieder am Deckel, diesmal im PRIORITAETS-Topf. Lauf 19:05 UTC: 150 Detail-Calls =
# 40 live + 110 vor Anpfiff — und von den 110 pfiff KEINES in den naechsten 6 Stunden an. Das
# fruehste war 02:00 UTC morgen. Laenderspielpause: `ist_prioritaet` zaehlt Land „INT" und alles
# mit „Champions League" — also Frauen-Freundschaftsspiele, Olympia-Quali der Frauen, UEFA U19,
# AFC Champions League Two, bis zu drei Tage im Voraus, 21 davon ohne einen Euro Volumen. Dieser
# 72h-Topf kam VOR dem 26h-Topf, also bekam der heutige Abend (Kolumbien, Argentinien, Brasilien
# …) keinen einzigen Platz. Orsomarso (Anpfiff 20:30 UTC, Primera B) war nie im Feed — und ein
# Spiel, das nie im Feed war, hat auch kein „bekanntes Geld", mit dem es sich nach vorn sortieren
# koennte. Der Guard vom 08.09. sah es nicht: er vermisst nur Spiele, die schon einmal drin waren.
#
# Fix: was BALD anpfeift, kommt vor die Vorschau. Reihenfolge jetzt live → bekannt grosses Geld
# (>= 20.000 €, der Fall vom 08.09.) → alles mit Anpfiff in NAH_H Stunden (jede Liga) →
# Prioritaet im 72h-Fenster → Rest im 26h-Fenster; jeder Topf nach bekanntem Geld, dann Anpfiff. Und der Lauf schreibt `nahGesamt`/`nahDrin` ins _meta, damit ein
# Guard die Luecke sieht, auch wenn das Spiel nie im Feed war.
NAH_H = float(os.environ.get("BETWATCH_NAH_H") or 6)
GROSS_BEKANNT_EUR = 20000.0      # = GROSSGELD_EUR im Guard (Radar-Schwelle Top/Intl)
UEFA_RX = re.compile(r"(champions league|europa league|europa conference|conference league|uefa)", re.I)
INTL_COUNTRY_RX = re.compile(r"^(int|international|eu|europe)$", re.I)


def ist_prioritaet(e) -> bool:
    """Dieselbe Einstufung wie `tierOf()` im Radar: Top-5/MLS oder UEFA/International. REIN."""
    liga = str((e or {}).get("league") or "")
    if PRIORITY_RX.search(liga) or UEFA_RX.search(liga):
        return True
    return bool(INTL_COUNTRY_RX.match(str((e or {}).get("country") or "")))


def bekanntes_volumen(prices: dict) -> dict:
    """{matchId: totalVol} aus dem letzten Lauf. Kostet keinen API-Call — wir wissen es schon."""
    out = {}
    for m in ((prices or {}).get("matches") or []):
        if isinstance(m, dict) and m.get("matchId") is not None:
            try:
                out[m["matchId"]] = float(m.get("totalVol") or 0)
            except (TypeError, ValueError):
                continue
    return out
HIST_KEEP_H = 72
HIST_MAX_POINTS = 240   # 5-Min-Takt: 240 Punkte = 20 h
# Für den „frisches Geld"-Zufluss im Dashboard: je Snapshot das Markt-Volumen der Dashboard-Märkte
# mitschreiben (mkv). Nur diese 7 (kompakt) — und nur auf den letzten 2 History-Punkten behalten
# (Delta = letzter minus vorletzter), damit die History nicht aufbläht.
TRACKED_MARKETS = ["Match Odds", "Over/Under 2.5 Goals", "Over/Under 3.5 Goals",
                   "Both teams to Score?", "Half Time", "First Half Goals 0.5", "First Half Goals 1.5"]
MKV_KEEP_POINTS = 2
SEL_KEEP_POINTS = 9


def _now():
    return datetime.now(timezone.utc)


# ── pure parsing / helpers (netz-frei, testbar) ───────────────────────────────
def parse_events(data):
    """events/live-Liste → [{matchId, home, away, league, country, kickoff, live}]."""
    out = []
    for e in (data or []):
        if not isinstance(e, dict):
            continue
        mid = e.get("match_id")
        if mid is None:
            continue
        t = e.get("teams") or {}
        out.append({
            "matchId": mid, "home": t.get("v1"), "away": t.get("v2"),
            "league": e.get("league"), "country": e.get("country"),
            "kickoff": e.get("kickoff"), "live": bool(e.get("live_info") or {}),
        })
    return out


def devig_1x2(hw, dr, aw):
    """Betfair-1X2-Odds → faire Wahrscheinlichkeit (Overround raus). None wenn unvollständig/unplausibel.
    Exchange-Overround ist winzig (~1.00–1.05); grobe Plausibilitätsgrenze fängt Platzhalter ab."""
    try:
        ih, idr, ia = 1.0 / float(hw), 1.0 / float(dr), 1.0 / float(aw)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    s = ih + idr + ia
    if not (0.90 < s < 1.60):
        return None
    return {"home": round(ih / s, 4), "draw": round(idr / s, 4), "away": round(ia / s, 4)}


def build_snapshot(ev, now=None):
    """event/{id}-Antwort → kompakter Snapshot mit ALLEN Märkten (roh) + abgeleitetem 1X2-Fair +
    Gesamt-Volumen. REIN/testbar. Behält HT/First-Half/O-U für die Signal-/Dashboard-Schicht."""
    now = now or _now()
    teams = ev.get("teams") or {}
    home, away = teams.get("v1"), teams.get("v2")
    markets, total_vol = {}, 0.0
    for m in (ev.get("markets") or []):
        if not isinstance(m, dict) or not m.get("name"):
            continue
        # Runner als geordnete Liste MIT Einzel-Volumen (das ECHTE gematchte Geld je Ausgang —
        # wie in Betwatchs Money-Ansicht).
        runners = [
            {"name": r.get("name"), "odd": r.get("odd"), "vol": r.get("volume")}
            for r in (m.get("runners") or []) if isinstance(r, dict) and r.get("name")
        ]
        # Markt-Geld = SUMME der Runner-Volumina. NICHT average_volume — das ist eine andere,
        # viel größere Kennzahl (33–1261× je Markt) und passt nie zur Ausgangs-Verteilung (29.07.2026,
        # Lucas: „24K oben, aber 10€ je Ausgang?"). Verifiziert an echten Betwatch-Antworten.
        rsum = sum((r["vol"] or 0) for r in runners if isinstance(r.get("vol"), (int, float)))
        total_vol += rsum
        markets[m["name"]] = {"vol": round(rsum, 2), "runners": runners}
    mo = markets.get("Match Odds")
    hw = dr = aw = vol1x2 = fair = None
    if mo:
        rr = {x["name"]: x["odd"] for x in mo["runners"]}   # name→odd für 1X2-Ableitung
        vol1x2 = mo["vol"]
        hw, dr, aw = rr.get(home), rr.get("The Draw"), rr.get(away)
        fair = devig_1x2(hw, dr, aw)
    return {
        "matchId": ev.get("match_id"), "home": home, "away": away,
        "league": ev.get("league"), "leagueId": ev.get("league_id"), "country": ev.get("country"),
        "kickoff": ev.get("kickoff"), "liveInfo": ev.get("live_info") or {},
        "capturedAt": now.isoformat(), "totalVol": round(total_vol),
        "mo": {"hw": hw, "dr": dr, "aw": aw, "vol": vol1x2, "fair": fair},
        "markets": markets,
    }


def dedup_matchups(snaps):
    """Betwatch listet ein Spiel gelegentlich unter ZWEI matchIds (Doppel-Event) → dieselbe Karte
    erscheint doppelt im Radar (01.08.2026, Lucas). select_ids dedupt nur per matchId, nicht per
    Paarung. Hier: pro (Heim, Auswaerts, Anpfiff-Tag) nur den Eintrag mit dem MEISTEN Volumen behalten
    (das Doppel-Listing hat i.d.R. eine leere Zweit-Karte). Reihenfolge bleibt (erstes Auftreten)."""
    best, order = {}, []
    for s in snaps:
        key = ((s.get("home") or "").strip().lower(),
               (s.get("away") or "").strip().lower(),
               (s.get("kickoff") or "")[:10])
        cur = best.get(key)
        if cur is None:
            best[key] = s
            order.append(key)
        elif (s.get("totalVol") or 0) > (cur.get("totalVol") or 0):
            best[key] = s
    return [best[k] for k in order]


def select_ids(parsed, now=None, window_h=WINDOW_H, cap=MAX_DETAIL, prio_window_h=PRIORITY_WINDOW_H,
               vol_bekannt=None, nah_h=NAH_H):
    """Welche Matches bekommen einen (teuren) Detail-Call: alle LIVE zuerst, dann die
    Prioritaets-Wettbewerbe im WEITEN Fenster (prio_window_h, ~3 Tage), dann alle anderen im
    Standard-Fenster (window_h, 26h) — die aber nach BEKANNTEM GELD sortiert, nicht nach Anpfiff.
    Gedeckelt. REIN/testbar.

    `vol_bekannt`: {matchId: totalVol} aus dem letzten Lauf (siehe bekanntes_volumen). Fehlt es,
    bleibt es bei der Anpfiff-Reihenfolge — dann ist die Auswahl so gut wie vorher, nie schlechter.
    """
    now = now or _now()
    horizon = now + timedelta(hours=window_h)
    prio_horizon = now + timedelta(hours=prio_window_h)

    def _ko(e):
        try:
            return datetime.fromisoformat(str(e.get("kickoff")).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None

    live = [e for e in parsed if e.get("live")]
    nah_horizon = now + timedelta(hours=nah_h)
    nah, prio, pre = [], [], []
    for e in parsed:
        if e.get("live"):
            continue
        k = _ko(e)
        if k is None or k < now:
            continue
        if k <= nah_horizon:                       # 12.10.: der heutige Abend vor der Vorschau
            nah.append((k, e))
        elif ist_prioritaet(e) and k <= prio_horizon:
            prio.append((k, e))
        elif k <= horizon:
            pre.append((k, e))
    # Jeder Topf: erst das Geld, das wir schon kennen, dann der Anpfiff. Ohne bekanntes
    # Volumen ist der Schluessel 0 — die Reihenfolge faellt dann auf Anpfiff zurueck.
    vb = vol_bekannt or {}

    def _geld(x):
        return float(vb.get(x[1].get("matchId")) or 0)
    # Spiele mit schon bekanntem grossen Geld (08.09.: Real Madrid 102.861 €) behalten ihren Platz
    # vor dem Nah-Topf — sonst wuerden 40 fruehe Pokalspiele ohne Geld sie wieder verdraengen.
    gross = [x for x in prio + pre if _geld(x) >= GROSS_BEKANNT_EUR]
    prio = [x for x in prio if _geld(x) < GROSS_BEKANNT_EUR]
    pre = [x for x in pre if _geld(x) < GROSS_BEKANNT_EUR]
    for topf in (gross, nah, prio, pre):
        topf.sort(key=lambda x: (-_geld(x), x[0]))
    ordered = ([e["matchId"] for e in live] + [e["matchId"] for _, e in gross]
               + [e["matchId"] for _, e in nah]
               + [e["matchId"] for _, e in prio] + [e["matchId"] for _, e in pre])
    # dedup, Reihenfolge erhalten
    seen, out = set(), []
    for mid in ordered:
        if mid not in seen:
            seen.add(mid)
            out.append(mid)
    return out[:cap]


def nah_abdeckung(parsed, ids, now=None, nah_h=NAH_H) -> dict:
    """Wie viele der Spiele, die in nah_h Stunden anpfeifen, bekamen einen Detail-Call? REIN.

    Die Gegenprobe zum Deckel, gemessen an der LISTE (alle Events), nicht am Feed — ein Spiel,
    das nie im Feed war, sieht man nur so (Orsomarso, 12.10.)."""
    now = now or _now()
    drin = set(ids or [])
    nah = []
    for e in parsed or []:
        if e.get("live"):
            continue
        try:
            k = datetime.fromisoformat(str(e.get("kickoff")).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        if now <= k <= now + timedelta(hours=nah_h):
            nah.append(e)
    fehlt = [e for e in nah if e.get("matchId") not in drin]
    return {"nahH": nah_h, "nahGesamt": len(nah), "nahDrin": len(nah) - len(fehlt),
            "nahFehlt": ["%s v %s (%s)" % (e.get("home"), e.get("away"), e.get("league"))
                         for e in fehlt[:10]]}


def append_history(hist, snap, now=None, keep_h=HIST_KEEP_H, max_points=HIST_MAX_POINTS):
    """Zeitreihe je Match fortschreiben (totalVol + 1X2 + Anpfiff) → Steam/Volumen-Delta später.
    REIN/testbar. Prunt Matches, die seit keep_h nicht mehr gesehen wurden."""
    now = now or _now()
    hist = dict(hist or {})
    mid = str(snap.get("matchId"))
    if mid in ("None", ""):
        return hist
    # Markt-Volumina der Dashboard-Märkte (für „frisches Geld"-Zufluss).
    mkv = {}
    for name in TRACKED_MARKETS:
        mk = (snap.get("markets") or {}).get(name)
        if isinstance(mk, dict) and isinstance(mk.get("vol"), (int, float)):
            mkv[name] = mk["vol"]
    li = snap.get("liveInfo") or {}
    # 10.08.2026 (Lucas): echten Spielstand + rote Karten mitschreiben. Damit erkennt der Push ein
    # Spielereignis im Zufluss-Fenster EXAKT (Score/Karten aenderten sich zwischen zwei Scans) statt es
    # aus einem 40%-Quotensprung zu RATEN (Braga-Fall). goal_v1/goal_v2/red_v1/red_v2 liefert Betwatch live.
    sc = [li.get("goal_v1"), li.get("goal_v2")] if li.get("goal_v1") is not None else None
    rc = ([li.get("red_v1") or 0, li.get("red_v2") or 0]
          if (li.get("red_v1") is not None or li.get("red_v2") is not None) else None)
    pt = {"ts": now.isoformat(), "totalVol": snap.get("totalVol"),
          "mo": {k: (snap.get("mo") or {}).get(k) for k in ("hw", "dr", "aw", "vol")},
          "kickoff": snap.get("kickoff"), "mkv": mkv,
          # money-radar 17.10. (Lucas: „wichtig ist, auf welchen Markt das Geld geht, nicht auf welches
          # Spiel"): Volumen + Quote JE AUSWAHL in jedem Markt, damit der Zufluss pro Auswahl rechenbar ist.
          "sel": {name: {r["name"]: [round(r.get("vol") or 0, 1), r.get("odd")]
                         for r in (mk.get("runners") or []) if isinstance(r, dict) and r.get("name") and (r.get("vol") or 0) > 0}
                  for name, mk in (snap.get("markets") or {}).items() if isinstance(mk, dict) and (mk.get("vol") or 0) > 0},
          "min": li.get("time"), "sc": sc, "rc": rc}
    arr = list(hist.get(mid) or [])
    arr.append(pt)
    arr = arr[-max_points:]
    # mkv nur auf den letzten MKV_KEEP_POINTS Punkten behalten (Platz sparen — Delta braucht nur 2).
    for p in arr[:-MKV_KEEP_POINTS]:
        if isinstance(p, dict):
            p.pop("mkv", None)
    for p in arr[:-SEL_KEEP_POINTS]:   # Auswahl-Volumina nur für die letzten ~40 Min
        if isinstance(p, dict):
            p.pop("sel", None)
    hist[mid] = arr
    return hist


def prune_history(hist, now=None, keep_h=HIST_KEEP_H):
    now = now or _now()
    cutoff = now - timedelta(hours=keep_h)
    out = {}
    for mid, arr in (hist or {}).items():
        if mid == "_meta" or not isinstance(arr, list) or not arr:
            continue
        try:
            last = datetime.fromisoformat(str(arr[-1].get("ts")).replace("Z", "+00:00"))
        except (ValueError, TypeError, AttributeError):
            last = None
        if last is None or last >= cutoff:
            out[mid] = arr
    return out


# ── network (nur main) ────────────────────────────────────────────────────────
# Cloudflare weist Requests ohne echten User-Agent teils mit 403 ab → Browser-Header mitschicken.
_BASE_HEADERS = {
    "Accept": "application/json, text/html;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-AT,de;q=0.9,en;q=0.8",
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
}
# Auth-Varianten in Reihenfolge probieren; die erste, die 200 liefert, wird gemerkt + geloggt.
RESULTS_MAX = 50       # Endstände je Abfrage (POST /football/results)
_AUTH_ORDER = ["token", "query", "apikey", "bearer"]   # 17.10.: Betwatch nimmt nur noch den Token-Header
_AUTH = {"mode": None}


def _build_req(path, mode):
    headers = dict(_BASE_HEADERS)
    url = API_BASE + path
    if mode == "query":
        sep = "&" if "?" in path else "?"
        url = f"{url}{sep}key={KEY}"
    elif mode == "token":
        headers["Authorization"] = f"Token {KEY}"
    elif mode == "apikey":
        headers["X-API-Key"] = KEY
    elif mode == "bearer":
        headers["Authorization"] = f"Bearer {KEY}"
    return urllib.request.Request(url, headers=headers)


def _get(path):
    modes = [_AUTH["mode"]] if _AUTH["mode"] else _AUTH_ORDER
    last_code = None
    for mode in modes:
        try:
            with urllib.request.urlopen(_build_req(path, mode), timeout=HTTP_TIMEOUT) as r:
                data = json.loads(r.read().decode("utf-8"))
                if _AUTH["mode"] != mode:
                    _AUTH["mode"] = mode
                    print(f"  🔑 Auth-Methode akzeptiert: {mode}")
                return data
        except urllib.error.HTTPError as e:
            last_code = e.code
            continue   # nächste Auth-Variante probieren
        except Exception as e:
            print(f"  ⚠️  Betwatch Fehler bei {path[:48]}: {e}")
            return None
    print(f"  ⚠️  Betwatch HTTP {last_code} bei {path[:48]} — alle Auth-Methoden abgelehnt "
          f"(Key falsch/abgelaufen?)")
    return None


RESULTS_MAX = int(os.environ.get("BETWATCH_RESULTS_MAX") or 200)   # Batch-Deckel je Ergebnis-Abfrage


def _build_post_req(path, mode, body: bytes):
    headers = dict(_BASE_HEADERS)
    headers["Content-Type"] = "application/json"
    url = API_BASE + path
    if mode == "query":
        sep = "&" if "?" in path else "?"
        url = f"{url}{sep}key={KEY}"
    elif mode == "token":
        headers["Authorization"] = f"Token {KEY}"
    elif mode == "apikey":
        headers["X-API-Key"] = KEY
    elif mode == "bearer":
        headers["Authorization"] = f"Bearer {KEY}"
    return urllib.request.Request(url, headers=headers, data=body, method="POST")


def fetch_results(match_ids):
    """10.08.2026 (Lucas): POST /football/results — AUTORITATIVE Endstaende (bis 30 Tage nach Spielende)
    fuer eine Liste Event-IDs. Fuellt das Loch der finished/vanish-Abrechnung: Spiele, die aus dem Feed
    fielen bevor wir „finished"/spaeten Live-Stand sahen. Gibt {str(matchId): {goal_v1, goal_v2, finished,
    red_v1, red_v2, ...}}. Netz NUR hier; ohne KEY / bei Fehler leeres Dict → Aufrufer nutzt die alte Logik."""
    ids = []
    for x in (match_ids or []):
        try:
            ids.append(int(x))
        except (TypeError, ValueError):
            continue
    ids = ids[:RESULTS_MAX]
    if not ids or not KEY:
        return {}
    body = json.dumps({"eventIds": ids}).encode("utf-8")
    modes = [_AUTH["mode"]] if _AUTH["mode"] else _AUTH_ORDER
    last_code = None
    for mode in modes:
        try:
            with urllib.request.urlopen(_build_post_req("/football/results", mode, body), timeout=HTTP_TIMEOUT) as r:
                data = json.loads(r.read().decode("utf-8"))
                if _AUTH["mode"] != mode:
                    _AUTH["mode"] = mode
                return {str(k): v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}
        except urllib.error.HTTPError as e:
            last_code = e.code
            continue
        except Exception as e:
            print(f"  ⚠️  Betwatch results Fehler: {e}")
            return {}
    print(f"  ⚠️  Betwatch results HTTP {last_code} — alle Auth-Methoden abgelehnt")
    return {}


def _load(p):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main():
    print("=== fetch_betfair_betwatch.py ===")
    if not KEY:
        print("  ℹ️  BETWATCH_KEY nicht gesetzt — übersprungen (kein Wipe).")
        return 0
    now = _now()
    events = _get("/football/events")
    live = _get("/football/live")
    if events is None and live is None:
        print("  ⚠️  Betwatch nicht erreichbar — bestehende Dateien bleiben (kein Wipe).")
        return 1
    by_id = {}
    for e in parse_events(events or []):
        by_id[e["matchId"]] = e
    for e in parse_events(live or []):
        by_id[e["matchId"]] = e   # live überschreibt prematch
    _vorher = _load(PRICES_FILE) if callable(globals().get("_load")) else {}
    _vol = bekanntes_volumen(_vorher)
    ids = select_ids(list(by_id.values()), now=now, vol_bekannt=_vol)
    print(f"  {len(by_id)} Events · {sum(1 for e in by_id.values() if e['live'])} live · "
          f"{len(ids)} Detail-Calls (Fenster {WINDOW_H:.0f}h, Cap {MAX_DETAIL})")
    # Der Deckel darf nicht still schneiden. Wenn er greift UND dabei ein Spiel faellt, von dem
    # wir aus dem letzten Lauf Geld kennen, ist das genau der Fall vom 08.09. — dann steht es
    # hier, mit Namen und Betrag.
    if len(ids) >= MAX_DETAIL:
        _drin = set(ids)
        _fehlt = sorted(((v, by_id[k].get("league"), by_id[k].get("home"), by_id[k].get("away"))
                         for k, v in _vol.items() if k not in _drin and v >= 20000 and k in by_id),
                        reverse=True)[:5]
        print(f"  ⚠️  Cap {MAX_DETAIL} erreicht — {len(by_id) - len(ids)} Events ohne Detail-Call.")
        for v, lg, h, a in _fehlt:
            print(f"      ❗ {h} v {a} ({lg}) hatte zuletzt {v:,.0f} € und faellt raus")

    snaps, hist = [], prune_history(_load(HISTORY_FILE), now=now)
    for mid in ids:
        d = _get(f"/football/event/{mid}")
        if not isinstance(d, dict) or not d.get("markets"):
            continue
        snap = build_snapshot(d, now=now)
        snaps.append(snap)
        hist = append_history(hist, snap, now=now)

    snaps = dedup_matchups(snaps)   # Doppel-Listings (zwei matchIds, ein Spiel) zusammenführen
    if not snaps:
        print("  ⚠️  Keine Snapshots gebaut — bestehende Preise bleiben (kein Wipe).")
        return 1

    PRICES_FILE.write_text(json.dumps(
        {"_meta": {"generatedAt": now.isoformat(), "n": len(snaps), **nah_abdeckung(
                       list(by_id.values()), ids, now=now),
                   "live": sum(1 for s in snaps if s.get("liveInfo"))},
         "matches": snaps}, ensure_ascii=False, indent=1), encoding="utf-8")
    hist.setdefault("_meta", {})["updatedAt"] = now.isoformat()
    HISTORY_FILE.write_text(json.dumps(hist, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"  ✅  {len(snaps)} Match-Snapshots → betfair_prices.json · History fortgeschrieben")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
