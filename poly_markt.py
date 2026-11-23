#!/usr/bin/env python3
"""
poly_markt.py — eigener Polymarket-Scan des Geldradars (Lucas 20.10.2026: „das muss autonom im neuen Projekt laufen,
egal ob das alte noch da ist oder nicht").

Ersetzt, was bisher aus dem alten Projekt kam (poly_money_upcoming / _broad_live / _broad_offen / _broad_close und
poly_wallet_track). Bewusst schlank: nur das, was der Geldradar zeigt.

Je Lauf (Mac-Runner „radar", Polymarket blockt Rechenzentren):
  1. Spiele finden: Gamma-Ereignisse der Tags soccer / esports / tennis plus die volumenstärksten tag-los,
     Anpfiff zwischen −4 h (läuft) und +30 h.
  2. Je Spiel die Sieger-Ausgänge mit Preis und Markt-Volumen (dieselben Etiketten wie im alten Projekt:
     Teamname bzw. „Draw (A vs. B)", Tore-Bündel „Over"/„Under") — damit passen Wallet-Positionen und Frontend.
  3. Für die geldstärksten Spiele die Halter je Ausgang (/holders): Geld je Seite und die größten Wallets,
     dazu jede Wallet, die wir schon verfolgen. Für neue Wal-Positionen der echte Ø-Einstieg (/positions).
  4. Wallet-Buch fortschreiben: Einstieg beim ersten Sehen, Schlusspreis = letzter Preis vor Anpfiff,
     nach der Auflösung CLV (Schluss − Einstieg) und Treffer je Wallet und Sportart.
  5. Lebensbilanz (P&L) der verfolgten Wallets nachziehen, gedeckelt.

Ausgabe in .pm/ (Actions-Cache „polymarkt-…", kein Commit):
  poly_maerkte.json        {key: Markt}  — alles, was der Seitenbau braucht
  poly_wallet_track.json   {open, scores, updatedAt}
"""
from __future__ import annotations

import gzip
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASIS = Path(__file__).resolve().parent / ".pm"
MAERKTE = BASIS / "poly_maerkte.json"
TRACK = BASIS / "poly_wallet_track.json"
# 20.11. (Lucas: „mehrere Wale auf einer Seite — das muss doch möglich sein"): aufgelöste Einzelpositionen ab $1k
# 60 Tage aufheben. Bisher gingen sie nach der Auflösung nur in die Wallet-Bilanz ein — welche Wale GEMEINSAM auf
# einem Spiel saßen, war danach weg, jede Kombinations-Idee musste wochenlang neu gemessen werden. ~250 Positionen
# am Tag, kompakt je Spiel ≈ 1–2 MB für 60 Tage (Speicher-Regel).
ERLEDIGT = BASIS / "poly_erledigt.json"
ERLEDIGT_TAGE, ERLEDIGT_MIN_USD = 60, 1000
_ERLEDIGT_NEU = []

GAMMA = "https://gamma-api.polymarket.com/events"
import poly_v2      # 03.11.2026: Data API v1 (/holders, /positions) abgeschaltet → v2 (condition=, cursor, snake_case)
# 23.11.: user-pnl-api.polymarket.com ist abgeschaltet (HTTP 410, „retired") — lieferte seit dem Umzug nie einen Wert.
# Nachfolger laut docs.polymarket.com/api-reference/wallet/get-a-users-pnl-series: Data API v2, Antwort
# {"data": {"points": [{"timestamp" (s), "realized_pnl", "position_pnl" (= realisiert + offen, kann null sein), …}]}}
PNL_API = "https://data-api.polymarket.com/v2/user-pnl?user={user}&interval=all&fidelity=1d"

TAGS = ("soccer", "esports", "tennis")
# 14.11.2026 (Lucas: Romania–Poland ohne Poly): Nations League lief nie ein — kein einziger „unl-"-Slug in den Daten,
# obwohl Polymarket das Spiel führt. Länderspiele hängen offenbar nicht unter „soccer". Zusätzliche Tags (unbekannte
# Tags liefern einfach eine leere Seite) und eine Slug-Präfix-Regel als Netz, falls Polymarket den Tag ändert.
# Nachtrag: Romania–Poland trägt soccer + uefa-nations-league — der eigentliche Grund war der gzip-Ausfall (s. _get).
TAGS_INTL = ("uefa-nations-league", "fifa-world-cup", "world-cup-qualifiers", "euro-qualifiers")
_FOOT_SLUG_RE = re.compile(r"^(unl|wcq|ecq|euq|uwcq|cwcq|afcq|conq)-\w", re.I)
FENSTER_VOR_H, FENSTER_NACH_H = 30.0, 4.0
MIN_VOL_HALTER = float(os.environ.get("PM_MIN_VOL_HALTER") or 5000)   # ab so viel Markt-Volumen lohnt der Halter-Abruf
HALTER_SPIELE = int(os.environ.get("PM_HALTER_SPIELE") or 80)        # je Lauf so viele Spiele mit Haltern (Lucas 20.10.: verdoppelt)
HALTER_SEITEN = 2                                                    # bis 400 Halter je Ausgang
POS_BUDGET = int(os.environ.get("PM_POS_BUDGET") or 80)              # /positions-Abrufe je Lauf (Ø-Einstieg)
PNL_BUDGET = int(os.environ.get("PM_PNL_BUDGET") or 25)              # Lebensbilanz-Abrufe je Lauf
AUFLOES_BUDGET = int(os.environ.get("PM_AUFLOES_BUDGET") or 40)      # Auflösungs-Nachschläge je Lauf
WALE_JE_MARKT, WALE_HART, BEKANNT_AB_N = 12, 40, 4
TRACK_HALTEN_H = 96                                                  # offene Position ohne Markt-Sichtung so lange halten
LAUFZEIT_MAX_S = float(os.environ.get("PM_LAUFZEIT_S") or 240)     # Lauf alle 5 Min → unter 4 Min bleiben
SPORTARTEN = {"Fußball", "E-Sport", "Tennis"}

_SPORT_BY_TAG = {"esports": "E-Sport", "cs2": "E-Sport", "csgo": "E-Sport", "lol": "E-Sport", "dota": "E-Sport",
                 "dota-2": "E-Sport", "valorant": "E-Sport", "league-of-legends": "E-Sport",
                 "tennis": "Tennis", "atp": "Tennis", "wta": "Tennis",
                 "nba": "US-Sport", "nfl": "US-Sport", "mlb": "US-Sport", "nhl": "US-Sport"}
_FOOT_TAGS = ("soccer", "football", "epl", "ucl", "uel", "mls", "la-liga", "laliga", "bundesliga",
              "serie-a", "ligue-1", "primeira-liga", "eredivisie", "super-lig", "brazil-serie-a", "brasileirao")
_MAP_PROP_RE = re.compile(
    r"\b(map|game|karte|spiel)\s*\d|handicap|spread|over\s*/?\s*under|correct\s*score"
    r"|first\s*blood|\bduration\b|\bkills?\b|\brounds?\b|to\s*win\s*(map|game)\b", re.I)
_EXHIBITION_RE = re.compile(r"\blegends\b|all[- ]?stars?\b|\bexhibition\b|\btestimonial\b|charity\s*match"
                            r"|\bveterans?\b|\bold\s*boys\b|\bmasters\b|\blegends?\s+xi\b", re.I)
_SLUG_LEAGUE_ALIAS = {"lal": "LALIGA"}

JETZT = datetime.now(timezone.utc)
_START = time.time()
_STAT = {"ok": 0, "fehler": 0}


# ── Netz ─────────────────────────────────────────────────────────────────────────────────────────────────
def _get(url):
    for versuch in range(2):
        try:
            # 14.11.2026: Gamma liefert Listen mit limit > 50 nur noch gzip — ohne den Header HTTP 406 „gzip_required"
            req = urllib.request.Request(url, headers={"User-Agent": "Geldradar/1.0", "Accept": "application/json",
                                                       "Accept-Encoding": "gzip"})
            with urllib.request.urlopen(req, timeout=12) as r:
                raw = r.read()
                if (r.headers.get("Content-Encoding") or "").lower() == "gzip" or raw[:2] == b"\x1f\x8b":
                    raw = gzip.decompress(raw)
                _STAT["ok"] += 1
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            if "erster" not in _STAT:
                try:
                    body = e.read()[:200].decode("utf-8", "replace")
                except Exception:  # noqa: BLE001
                    body = ""
                _STAT["erster"] = f"HTTP {e.code} {url[:90]} | {body} | {dict(e.headers or {}).get('Content-Type')}"
            if e.code == 404:
                return None
            if (e.code == 429 or e.code >= 500) and versuch == 0:
                time.sleep(2)
                continue
        except Exception as e:  # noqa: BLE001
            _STAT.setdefault("erster", f"{type(e).__name__}: {str(e)[:120]} {url[:70]}")
            if versuch == 0:
                time.sleep(1)
                continue
    _STAT["fehler"] += 1
    _h = urllib.parse.urlparse(url)
    _STAT.setdefault("wo", {})[_h.netloc + _h.path.split("?")[0][:30]] = _STAT.get("wo", {}).get(_h.netloc + _h.path.split("?")[0][:30], 0) + 1
    return None


CLOB_BUECHER = "https://clob.polymarket.com/books"
BUCH_FENSTER_H = (-2.5, 12)                  # Spiele, die gerade laufen oder in den nächsten 12 h beginnen
BUCH_SPANNE = 0.02                           # Liquidität: alles bis 2 Cent über dem besten Ask


def _post(url, daten):
    for versuch in range(2):
        try:
            req = urllib.request.Request(url, data=json.dumps(daten).encode(), method="POST",
                                         headers={"User-Agent": "Geldradar/1.0", "Accept": "application/json",
                                                  "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as r:
                _STAT["ok"] += 1
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if (e.code == 429 or e.code >= 500) and versuch == 0:
                time.sleep(2)
                continue
            break
        except Exception:  # noqa: BLE001
            if versuch == 0:
                time.sleep(1)
                continue
    _STAT["fehler"] += 1
    return None


def buecher(maerkte, oc_je):
    """Lucas 08.11. (Polymarket-Schattenbuch): bester Ask und Liquidität bis +2 Cent je Ausgang — damit der Build
    zu jedem Public-Signal festhalten kann, zu welchem Preis und wie viel man auf Polymarket hätte setzen können."""
    ziele = []
    for k, m in maerkte.items():
        if not re.search(r"\d{4}-\d{2}-\d{2}$", k):
            continue
        if not (BUCH_FENSTER_H[0] <= m.get("hoursToKickoff", 99) <= BUCH_FENSTER_H[1]):
            continue
        ziele += [(k, o["label"], str(o["token"])) for o in oc_je.get(k) or [] if o.get("token")]
    n_ok = 0
    for i in range(0, len(ziele), 100):
        if not _zeit_ok():
            break
        teil = ziele[i:i + 100]
        antwort = _post(CLOB_BUECHER, [{"token_id": t} for _, _, t in teil])
        if not isinstance(antwort, list):
            continue
        je_token = {str(b.get("asset_id")): b for b in antwort if isinstance(b, dict)}
        for k, label, t in teil:
            b = je_token.get(t)
            if not b:
                continue
            try:
                asks = sorted((float(x["price"]), float(x["size"])) for x in b.get("asks") or [])
            except (KeyError, TypeError, ValueError):
                continue
            if not asks:
                continue
            best = asks[0][0]
            tiefe = sum(p * sz for p, sz in asks if p <= best + BUCH_SPANNE + 1e-9)
            maerkte[k].setdefault("ask", {})[label] = round(best, 4)
            maerkte[k].setdefault("tiefe", {})[label] = round(tiefe)
            n_ok += 1
    for k in {k for k, _, _ in ziele}:
        if maerkte[k].get("ask"):
            maerkte[k]["buchTs"] = JETZT.isoformat()
    print(f"  poly_markt: Orderbuch für {n_ok} von {len(ziele)} Ausgängen")


def _zeit_ok():
    return time.time() - _START < LAUFZEIT_MAX_S


# ── reine Helfer (aus dem alten Projekt übernommen, dieselben Etiketten) ─────────────────────────────────────
def _ts(s):
    if not s:
        return None
    txt = str(s).strip().replace("Z", "+00:00").replace(" ", "T", 1)
    if txt.endswith("+00"):
        txt += ":00"
    try:
        t = datetime.fromisoformat(txt)
    except Exception:  # noqa: BLE001
        return None
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t


def ko_zeit(ev):
    """Anpfiff. 22.10. (Lucas: Wallet 0xf7f6 „Top-Liste, aber überall im Minus"): „startDate" ist bei Gamma der Tag,
    an dem der Markt ANGELEGT wurde — stand es vor dem gameStartTime der Märkte, galt jedes Tennis-/E-Sport-Spiel
    sofort als „läuft", es gab nie einen Schlusspreis und der Schlusskurs-Vorsprung wurde aus dem Preis WÄHREND
    des Spiels gerechnet (+12 bis +19 pp statt ~0). startDate deshalb nur noch als letzter Notnagel."""
    for f in ("startTime", "gameStartTime"):
        t = _ts(ev.get(f))
        if t:
            return t
    for m in ev.get("markets") or []:
        t = _ts(m.get("gameStartTime"))
        if t:
            return t
    return _ts(ev.get("startDate"))


def liga_aus_slug(key):
    head = str(key or "").split("-", 1)[0].strip().lower()
    if not head or head.isdigit():
        return "OTHER"
    return _SLUG_LEAGUE_ALIAS.get(head, head.upper())


def sport_von(ev, tag=None):
    t = str(tag or "").lower()
    if t in _SPORT_BY_TAG:
        return _SPORT_BY_TAG[t]
    if t in _FOOT_TAGS or t in TAGS_INTL:
        return "Fußball"
    for x in ev.get("tags") or []:
        slug = str((x.get("slug") if isinstance(x, dict) else x) or "").lower()
        if slug in _SPORT_BY_TAG:
            return _SPORT_BY_TAG[slug]
        if slug in _FOOT_TAGS or slug in TAGS_INTL:
            return "Fußball"
    if _FOOT_SLUG_RE.match(str(ev.get("slug") or "")):
        return "Fußball"
    return None


def _markt_zeilen(m):
    try:
        names = json.loads(m.get("outcomes", "[]") or "[]")
        prices = json.loads(m.get("outcomePrices", "[]") or "[]")
        tokens = json.loads(m.get("clobTokenIds", "[]") or "[]")
    except Exception:  # noqa: BLE001
        return []
    if len(names) < 2 or len(prices) != len(names) or {str(n).strip().lower() for n in names} == {"yes", "no"}:
        return []
    rows = []
    for i, nm in enumerate(names):
        try:
            p = float(prices[i])
        except (TypeError, ValueError, IndexError):
            p = None
        rows.append({"label": str(nm), "price": p, "cond": m.get("conditionId"), "token": tokens[i] if i < len(tokens) else None})
    return rows


def _mkt_vol(m):
    v = m.get("volumeNum") if m.get("volumeNum") is not None else m.get("volume")
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def ausgaenge(ev):
    """Sieger-Ausgänge eines Ereignisses → [{label, price, cond, token}] (wie _outcomes im alten Projekt)."""
    markets = ev.get("markets") or []
    cand = [(m, _markt_zeilen(m)) for m in markets if not m.get("closed")]
    cand = [(m, r) for m, r in cand if r]
    if cand:
        serie = [(m, r) for m, r in cand if not _MAP_PROP_RE.search(" ".join(str(m.get(k) or "") for k in ("question", "groupItemTitle", "slug")))]
        pool = serie or cand
        pool.sort(key=lambda mr: -_mkt_vol(mr[0]))
        return pool[0][1]
    rows = []
    for m in markets:
        title = m.get("groupItemTitle")
        if not title:
            continue
        try:
            names = json.loads(m.get("outcomes", "[]") or "[]")
            prices = json.loads(m.get("outcomePrices", "[]") or "[]")
            tokens = json.loads(m.get("clobTokenIds", "[]") or "[]")
        except Exception:  # noqa: BLE001
            continue
        low = [str(n).strip().lower() for n in names]
        if "yes" not in low:
            continue
        yi = low.index("yes")
        try:
            p = float(prices[yi])
        except (TypeError, ValueError, IndexError):
            p = None
        rows.append({"label": str(title), "price": p, "cond": m.get("conditionId"), "token": tokens[yi] if yi < len(tokens) else None})
    return rows if len(rows) >= 2 else []


def markt_volumen(ev, oc):
    conds = {o.get("cond") for o in oc if o.get("cond")}
    tot = sum(_mkt_vol(m) for m in ev.get("markets") or [] if m.get("conditionId") in conds)
    if tot > 0:
        return tot
    try:
        return float(ev.get("volume") or 0)
    except (TypeError, ValueError):
        return 0.0


def sieger(preise, tol=0.02):
    best, bp = None, 0.0
    for k, v in (preise or {}).items():
        try:
            p = float(v)
        except (TypeError, ValueError):
            continue
        if p > bp:
            best, bp = k, p
    return best if bp >= 1.0 - tol else None


def _halter_parse(data, token):
    groups = data if isinstance(data, list) else (data.get("holders") if isinstance(data, dict) else None)
    rows = None
    for g in groups or []:
        if isinstance(g, dict) and "holders" in g:
            if str(g.get("token") or g.get("asset") or g.get("tokenId")) == str(token):
                rows = g.get("holders")
                break
        else:
            # v2 kann flache Zeilen liefern (beide Ausgänge gemischt) → nach Token filtern, wenn die Zeile einen trägt
            rows = [r for r in groups if not isinstance(r, dict) or not (r.get("asset") or r.get("tokenId"))
                    or str(r.get("asset") or r.get("tokenId")) == str(token)]
            break
    out = []
    for h in rows or []:
        if not isinstance(h, dict):
            continue
        w = h.get("proxyWallet") or h.get("proxy_wallet") or h.get("wallet")
        try:
            a = float(h.get("amount") or h.get("size") or h.get("balance"))
        except (TypeError, ValueError):
            continue
        if w and a > 0:
            out.append((str(w).lower(), a))
    return out


HALTER_AVG = {}


def alle_halter(cond, token):
    alle, gesehen = [], set()
    # 23.11.: include_pnl liefert je Halter avg_price/entry_cost — Einstieg für ALLE Wale ohne /positions-Abruf
    # include_pnl=true geht nur mit kleinem limit (limit=200 → HTTP 400, 23.11.) → Haupt-Abruf wie bisher, Probe daneben
    data = poly_v2.seiten(_get, "holders", HALTER_SEITEN, condition=cond, limit=200)
    if data is None:
        return [], True
    if _STAT.get("probe", 0) < 1:
        _STAT["probe"] = 1
        for lim in (100, 50, 20):
            pr = _get(poly_v2.url("holders", condition=cond, limit=lim, include_pnl="true"))
            print(f"  poly_markt: Probe holders include_pnl limit={lim}: {'ok' if pr is not None else 'Fehler'}")
    for g in data if isinstance(data, list) else []:
        for h in (g.get("holders") if isinstance(g, dict) and "holders" in g else [g]) or []:
            if isinstance(h, dict) and str(h.get("token_id") or h.get("asset") or token) == str(token):
                w = str(h.get("proxy_wallet") or h.get("proxyWallet") or "").lower()
                if w and isinstance(h.get("avg_price"), (int, float)) and 0 < h["avg_price"] < 1:
                    HALTER_AVG[(w, str(token))] = round(float(h["avg_price"]), 4)
    for w, a in _halter_parse(data, token):
        if w not in gesehen:
            gesehen.add(w)
            alle.append((w, a))
    return alle, len(data) >= 200 * HALTER_SEITEN


def wal_auswahl(wale, bekannt):
    sortiert = sorted(wale, key=lambda x: -(x.get("usd") or 0))
    raus = list(sortiert[:WALE_JE_MARKT])
    drin = {(x["wallet"], x["side"]) for x in raus}
    for x in sortiert[WALE_JE_MARKT:]:
        if len(raus) >= WALE_HART:
            break
        if x["wallet"] in bekannt and (x["wallet"], x["side"]) not in drin:
            raus.append(x)
            drin.add((x["wallet"], x["side"]))
    return raus


def split_guete(shares, trunc):
    sh = {k: v for k, v in (shares or {}).items() if isinstance(v, (int, float))}
    if len(sh) < 2 or sum(sh.values()) <= 0:
        return {"art": "leer", "trunc": trunc}
    if len(sh) == 2:
        return {"art": "preis_echo", "trunc": trunc}
    return {"art": "abgeschnitten" if trunc else "belastbar", "trunc": bool(trunc)}


def portfolio_wert(data):
    """Summe des aktuellen Werts aller offenen Positionen (Bankroll-Näherung; Bargeld sehen wir nicht)."""
    v = 0.0
    for p in data or []:
        if isinstance(p, dict):
            try:
                v += float(p.get("currentValue", p.get("current_value")) or 0)
            except (TypeError, ValueError):
                pass
    return v if v > 0 else None


def avg_aus_positionen(data, token):
    for p in data or []:
        if isinstance(p, dict) and str(p.get("asset") or p.get("token") or p.get("tokenId") or "") == str(token):
            try:
                ap = float(p.get("avgPrice", p.get("avg_price")))
            except (TypeError, ValueError):
                return None
            return round(ap, 4) if 0 < ap < 1 else None
    return None


# ── 1. Spiele finden ─────────────────────────────────────────────────────────────────────────────────────────
def _seiten(query, max_seiten, decke=None):
    """14.11.2026: Gamma lehnt Offset-Listen auf /events mit HTTP 406 ab (still seit spätestens 12.11. — 0 Märkte,
    die Seite lebte vom Rest-Gedächtnis). Jetzt /events/keyset mit after_cursor; Offset nur noch als Rückfall.
    decke: Schluss, sobald die Seite hinter dem Fenster endet (Sortierung nach endDate)."""
    cursor = None
    for seite in range(max_seiten):
        r = _get(f"{GAMMA}/keyset?{query}&limit=100" + (f"&after_cursor={urllib.parse.quote(cursor)}" if cursor else ""))
        if r is None and seite == 0:
            r = _get(f"{GAMMA}?{query}&limit=100&offset=0")
        page = (r.get("events") if isinstance(r, dict) else r) or []
        if not isinstance(page, list) or not page:
            return
        yield page
        cursor = r.get("next_cursor") if isinstance(r, dict) else None
        if not cursor:
            return
        letzt = _ts((page[-1] or {}).get("endDate")) if decke and isinstance(page[-1], dict) else None
        if letzt and letzt > decke:
            return


def ereignisse():
    boden = (JETZT - timedelta(hours=FENSTER_NACH_H)).strftime("%Y-%m-%dT%H:%M:%SZ")
    gesehen = {}
    # 07.11. (Lucas: kein Wal-Post am Samstag): nach endDate sortiert fressen an einem Fußball-Samstag die laufenden
    # Spiele samt Nebenmärkten (Halbzeit, exaktes Ergebnis, more-markets) die 400 Plätze — Abend und Sonntag fehlten.
    # Deshalb Fußball bis 15 Seiten und Schluss erst, wenn die Seite hinter dem Fenster endet.
    decke = JETZT + timedelta(hours=FENSTER_VOR_H + 6)
    basis = f"closed=false&order=endDate&ascending=true&end_date_min={boden}"
    for tag in TAGS:
        for page in _seiten(f"tag_slug={tag}&{basis}", 15 if tag == "soccer" else 4, decke):
            for ev in page:
                if isinstance(ev, dict) and ev.get("slug"):
                    gesehen.setdefault(ev["slug"], (ev, tag))
    for tag in TAGS_INTL:                        # 14.11.: Länderspiele (Nations League, Qualis) — max. 2 Seiten je Tag
        for page in _seiten(f"tag_slug={tag}&{basis}", 2, decke):
            for ev in page:
                if isinstance(ev, dict) and ev.get("slug"):
                    gesehen.setdefault(ev["slug"], (ev, tag))
    for page in _seiten("closed=false&order=volume&ascending=false", 3):   # tag-los: die volumenstärksten
        for ev in page:
            if isinstance(ev, dict) and ev.get("slug"):
                gesehen.setdefault(ev["slug"], (ev, None))
    intl = [(k, t, [str((x.get("slug") if isinstance(x, dict) else x) or "") for x in (e.get("tags") or [])][:6])
            for k, (e, t) in gesehen.items() if _FOOT_SLUG_RE.match(k)]
    print(f"poly_markt: {len(intl)} Länderspiel-Events (unl/wcq …)" + "".join(f"\n  {k} via {t} tags={tg}" for k, t, tg in intl[:8]))
    return list(gesehen.values())


def markt_zeile(ev, tag):
    key = ev.get("slug")
    ko = ko_zeit(ev)
    if not key or not ko:
        return None, None
    htk = (ko - JETZT).total_seconds() / 3600
    if not (-FENSTER_NACH_H <= htk <= FENSTER_VOR_H):
        return None, None
    sport = sport_von(ev, tag)
    if sport not in SPORTARTEN:
        return None, None
    oc = [o for o in ausgaenge(ev) if isinstance(o.get("price"), (int, float))]
    if len(oc) < 2 or any(_EXHIBITION_RE.search(o["label"]) for o in oc):
        return None, None
    return {"key": key, "titel": ev.get("title"), "league": liga_aus_slug(key), "sport": sport,
            "koTs": ko.isoformat(), "hoursToKickoff": round(htk, 2), "live": htk < 0,
            "totalUsd": round(markt_volumen(ev, oc)), "prices": {o["label"]: round(o["price"], 4) for o in oc},
            "shares": {}, "whales": []}, oc


# ── 4. Wallet-Buch ─────────────────────────────────────────────────────────────────────────────────────────
TAGE_HALTEN = 60


def geld_aus_position(usd, last_price, entry, win, anteile=None):
    """(Gewinn, Einsatz) in $ einer aufgelösten Position — oder (None, None). Wie im alten Projekt:
    Anteile = usd / letzter Preis, Einsatz = Anteile × Einstieg, der Gewinner zahlt 1 $ je Anteil.
    Unterstellt Halten bis zur Auflösung (wir sehen Positionen, keine Verkäufe)."""
    # 16.11. (Report: $2 Mrd. Einsatz in einer Woche, einzelne Wallets $90 Mio. auf 5 Wetten): seit 10.11. wird der
    # letzte Preis JEDER offenen Position fortgeschrieben, der Betrag aber nur, wenn die Halter abgerufen werden. Kurz
    # vor der Auflösung steht der Verlierer bei 0,001 → Anteile = Betrag ÷ 0,001 explodierten. Jetzt zählen die
    # Anteile, die zum selben Zeitpunkt wie der Betrag gemessen wurden; ohne sie nur bei einem Preis ab 5 ¢.
    try:
        en = float(entry)
        if isinstance(anteile, (int, float)) and anteile > 0:
            a = float(anteile)
        else:
            u, lp = float(usd or 0), float(last_price)
            if not (u > 0 and 0.05 <= lp < 1):
                return None, None
            a = u / lp
    except (TypeError, ValueError):
        return None, None
    if not (0 < en < 1):
        return None, None
    anteile = a
    einsatz = anteile * en
    return (round(anteile - einsatz, 2) if win else round(-einsatz, 2)), round(einsatz, 2)


def tage_fortschreiben(s, clv, win, gewinn=None, einsatz=None):
    """Lucas 22.10. (Rangliste auch nach Gewinn 7/30 Tage): Tages-Gedächtnis je Wallet im Format des alten
    Projekts {Tag: [n, clvSum, wins, clvSqSum, gewinn, einsatz, nGeld]} — das übernommene Gedächtnis läuft so nahtlos weiter."""
    tag = JETZT.date().isoformat()
    tage = {k: list(v) for k, v in (s.get("tage") or {}).items() if isinstance(v, (list, tuple)) and len(v) >= 4}
    e = tage.get(tag) or [0, 0.0, 0, 0.0]
    c = float(clv)
    neu = [e[0] + 1, round(e[1] + c, 2), e[2] + (1 if win else 0), round(e[3] + c * c, 2)]
    g_ok = isinstance(gewinn, (int, float)) and isinstance(einsatz, (int, float))
    if len(e) >= 6 or g_ok:
        neu += [round((float(e[4]) if len(e) >= 6 else 0.0) + (gewinn or 0.0), 2),
                round((float(e[5]) if len(e) >= 6 else 0.0) + (einsatz or 0.0), 2),
                (int(e[6]) if len(e) >= 7 else 0) + (1 if g_ok else 0)]
    tage[tag] = neu
    s["tage"] = {k: tage[k] for k in sorted(tage)[-TAGE_HALTEN:]}


def geld_fix_1611(track):
    """Einmalig (16.11.): die Geld-Felder (Gewinn, Einsatz, nGeld) der Tage ab 10.11. sind durch den Anteile-Fehler
    unbrauchbar — n, CLV und Treffer bleiben, nur das Geld dieser Tage wird verworfen. Ab dem Fix zählt es wieder."""
    if track.get("geldFix1611"):
        return
    for s in (track.get("scores") or {}).values():
        for tag, v in list((s.get("tage") or {}).items()):
            if "2026-11-10" <= tag <= "2026-11-16" and isinstance(v, list) and len(v) > 4:
                s["tage"][tag] = v[:4]
    track["geldFix1611"] = True


# Regel WL1 (Lucas 21.11. 20:11: „Live ist mir wichtiger … dann musst du halt einen Yield ausrechnen … wie viel vom
# Wallet wird gestaked, was kommt raus — eine valide Zahl, die uns beim Bewerten hilft"). Vorab festgelegt:
# * Bisher wurden nur Positionen gewertet, die wir VOR Anpfiff sahen (Schlusskurs). 39 % der Wal-Positionen sehen wir
#   zuerst live — Live-Spieler konnten nie bewährt werden.
# * Je Position, die wir zuerst LIVE sehen (Preis 3–97 ¢): Folge-Rendite je Einheit = 1/Preis − 1 bei Gewinn, sonst −1
#   (zum Preis unserer ersten Sichtung — das, was man nachspielen kann; jede Wette zählt gleich, damit große Einsätze
#   den Schnitt nicht tragen), dazu der Live-Markout: Preis 15–30 Min später − Preis bei Sichtung (pp).
# * Für ALLE gewerteten Positionen (auch vor Anpfiff) dieselbe Folge-Rendite — der Ertrag neben dem Schlusskurs.
# * Einsatz relativ zum Wallet: Betrag ÷ ihr üblicher Einsatz (Faktor) und, wo wir ihre Positionen abrufen, ÷ Wert
#   ihres offenen Portfolios (Bankroll-Anteil).
# * Live-bewährt (WL1): ≥ 30 Live-Wertungen, Untergrenze (z = 1,645) des Live-Markouts > 0 UND Folge-Rendite > 0.
#   Erst still in der Rangliste; Pushes/Public erst nach Lucas' OK.
LIVE_MIN_P, LIVE_MAX_P = 0.03, 0.97


def _rendite(preis, win):
    return (1 / preis - 1) if win else -1.0


def live_wertung(scores, e, win):
    """WL1: Folge-Rendite (alle Positionen) und Live-Zweig (zuerst live gesehen) in die Bilanz der Wallet."""
    p0 = e.get("firstPrice")
    if not isinstance(p0, (int, float)) or not LIVE_MIN_P <= p0 <= LIVE_MAX_P:
        return
    s = scores.setdefault(e["wallet"], {"n": 0, "clvSumPP": 0.0, "wins": 0, "usd": 0})
    r = _rendite(p0, win)
    vorab = (e.get("htkFirst") or 0) > 0 and not e.get("liveFirst")
    for k, ja in (("y", True), ("live", not vorab)):
        if not ja:
            continue
        s[k + "N"] = int(s.get(k + "N") or 0) + 1
        s[k + "RSum"] = round(float(s.get(k + "RSum") or 0) + r, 4)
        s[k + "RSq"] = round(float(s.get(k + "RSq") or 0) + r * r, 4)
        s[k + "Wins"] = int(s.get(k + "Wins") or 0) + (1 if win else 0)
        s[k + "Usd"] = round(float(s.get(k + "Usd") or 0) + float(e.get("usdFirst") or e.get("usd") or 0))
    if not vorab:
        mo = (e["p15"] - p0) * 100 if isinstance(e.get("p15"), (int, float)) else None
        if mo is not None:
            s["moN"] = int(s.get("moN") or 0) + 1
            s["moSum"] = round(float(s.get("moSum") or 0) + mo, 2)
            s["moSq"] = round(float(s.get("moSq") or 0) + mo * mo, 2)
        tag = JETZT.date().isoformat()
        lt = s.setdefault("liveTage", {})
        v = lt.get(tag) or [0, 0.0, 0, 0.0]
        lt[tag] = [v[0] + 1, round(v[1] + r, 4), v[2] + (mo is not None), round(v[3] + (mo or 0), 2)]
        s["liveTage"] = {k: lt[k] for k in sorted(lt)[-TAGE_HALTEN:]}
        if e.get("sport"):
            b = s.setdefault("liveSport", {}).setdefault(e["sport"], [0, 0.0])
            b[0] += 1
            b[1] = round(b[1] + r, 4)


CLV_MAX_PP = 30      # 21.11.: vor Anpfiff bewegt sich kein Preis um mehr als 30 Cent — darüber ist es ein Datenfehler


def bereinigung_2111(track):
    """Einmalig (21.11., Lucas: „beim Tracken aufpassen, dass wir keinen Fehler haben"): der Einstieg kam bei einem Teil
    der Positionen aus avgPrice (/positions), auch wenn dieser Abruf erst NACH Anpfiff lief — dann steckten Live-Käufe
    im Einstieg (Bayern – Köln: Einstieg 0,56 bei 0,91 vor Anpfiff, CLV +35 pp). Tage, an denen eine Wallet im Schnitt
    mehr als 20 pp CLV je Wette hatte, werden aus ihrer Bilanz genommen (n, CLV, Quadrate, Treffer exakt aus dem
    Tages-Gedächtnis). Betraf 151 von ~70.000 Wetten, darunter die Nr. 1 und Nr. 5 der Bewährten-Rangliste."""
    if track.get("bereinigt2111"):
        return
    weg = 0
    for sc in (track.get("scores") or {}).values():
        for tag, v in list((sc.get("tage") or {}).items()):
            if not (isinstance(v, list) and len(v) >= 4 and v[0] and abs(v[1] / v[0]) > 20):
                continue
            n, c, w, q = int(v[0]), float(v[1]), int(v[2]), float(v[3])
            for k in ("n", "clvFenN"):
                sc[k] = max(0, int(sc.get(k) or 0) - n)
            for k in ("clvSumPP", "clvFenSum"):
                sc[k] = round(float(sc.get(k) or 0) - c, 2)
            sc["clvSqSum"] = round(max(0.0, float(sc.get("clvSqSum") or 0) - q), 2)
            sc["wins"] = max(0, int(sc.get("wins") or 0) - w)
            sc["bereinigt2111"] = int(sc.get("bereinigt2111") or 0) + n
            del sc["tage"][tag]
            weg += n
    track["bereinigt2111"] = weg or True
    print(f"  poly_markt: Bereinigung 21.11. — {weg} Wetten mit unmöglichem CLV aus den Bilanzen genommen")


def track_fortschreiben(track, maerkte, aufgeloest):
    openp = {k: dict(v) for k, v in (track.get("open") or {}).items()}
    scores = {w: dict(s) for w, s in (track.get("scores") or {}).items()}
    for m in maerkte.values():
        prices = m.get("prices") or {}
        for wh in m.get("whales") or []:
            w, side = wh.get("wallet"), wh.get("side")
            p = prices.get(side)
            if not w or side is None or not isinstance(p, (int, float)):
                continue
            ok = f"{w}|{m['key']}|{side}"
            e = openp.get(ok)
            if e is None:
                e = openp[ok] = {"wallet": w, "key": m["key"], "side": side, "league": m.get("league"), "sport": m.get("sport"),
                                 "firstPrice": round(p, 4), "firstTs": JETZT.isoformat(),
                                 "htkFirst": m.get("hoursToKickoff"), "usdFirst": round(float(wh.get("usd") or 0)),
                                 "liveFirst": bool(m.get("live"))}
            e["lastPrice"] = round(p, 4)
            e["usd"] = round(float(wh.get("usd") or 0))
            if p > 0:
                e["anteile"] = round(e["usd"] / p, 2)          # 16.11.: Anteile zum Zeitpunkt des Betrags
            e["marktUsd"], e["koTs"] = m.get("totalUsd"), m.get("koTs")
            if not m.get("live") and (_ts(m.get("koTs")) or JETZT + timedelta(1)) > JETZT:
                e["closePrice"] = round(p, 4)          # letzter Preis vor Anpfiff = Schlusspreis (21.11.: auch nach der Uhr)
            if isinstance(wh.get("avgPrice"), (int, float)):
                e["entryPrice"] = wh["avgPrice"]
    # 10.11. (Polymarket-Check): Preise ALLER offenen Positionen fortschreiben, nicht nur in Märkten, deren Halter
    # in diesem Lauf abgerufen wurden. Vorher hatten 70 % der Positionen nach Anpfiff keinen Schlusspreis und fielen
    # bei der Auflösung ungewertet raus — die Rangliste sah nur ein Drittel der Wetten.
    for e in openp.values():
        m = maerkte.get(e.get("key"))
        p = ((m or {}).get("prices") or {}).get(e.get("side"))
        if not isinstance(p, (int, float)):
            continue
        e["lastPrice"] = round(p, 4)
        if not m.get("live") and (_ts(m.get("koTs")) or JETZT + timedelta(1)) > JETZT:
            e["closePrice"] = round(p, 4)
        # Regel WL1 (21.11.): Live-Markout — Preis der Seite 15–30 Min nach unserer ersten Live-Sichtung
        if e.get("liveFirst") and e.get("p15") is None and m.get("live"):
            t0 = _ts(e.get("firstTs"))
            if t0 and timedelta(minutes=15) <= JETZT - t0 <= timedelta(minutes=30):
                e["p15"] = round(p, 4)
    for ok in list(openp):
        e = openp[ok]
        preise = aufgeloest.get(e.get("key"))
        if preise is None:
            continue
        gew = sieger(preise)
        del openp[ok]
        if (e.get("usd") or 0) >= ERLEDIGT_MIN_USD:
            _ERLEDIGT_NEU.append((e, gew))
        if gew is None or e.get("side") not in preise:
            continue                                   # 50:50 oder Seite nicht zuordenbar → nicht werten
        # 22.10.: nur echte Werte vor Anpfiff — kein Rückfall auf den letzten Preis (der liegt im Spiel und kennt
        # den Ausgang schon) und kein erstes Sehen nach Anpfiff als Einstieg
        win = e["side"] == gew
        live_wertung(scores, e, win)
        # 21.11.: gewertet wird ab dem Preis, zu dem WIR die Position zuerst sahen (vor Anpfiff) — das ist, was man
        # nachspielen kann, und es ist sauber. avgPrice (/positions) bleibt nur zur Anzeige: lief der Abruf nach
        # Anpfiff, steckten Live-Käufe darin (Bayern – Köln 0,56 statt 0,91). Unmögliche Sprünge (> 30 pp) zählen nicht.
        einstieg = e.get("firstPrice") if (e.get("htkFirst") or 0) > 0 else None
        schluss = e.get("closePrice")
        if not isinstance(einstieg, (int, float)) or not isinstance(schluss, (int, float)):
            continue
        clv = (schluss - einstieg) * 100
        if abs(clv) > CLV_MAX_PP:
            _STAT["clvUnmoeglich"] = _STAT.get("clvUnmoeglich", 0) + 1
            continue
        s = scores.setdefault(e["wallet"], {"n": 0, "clvSumPP": 0.0, "wins": 0, "usd": 0})
        s["n"] = (s.get("n") or 0) + 1
        s["clvSumPP"] = round((s.get("clvSumPP") or 0) + clv, 2)
        s["clvSqSum"] = round((s.get("clvSqSum") or 0) + clv * clv, 2)
        s["clvFenN"] = (s.get("clvFenN") or 0) + 1
        s["clvFenSum"] = round((s.get("clvFenSum") or 0) + clv, 2)
        s["wins"] = (s.get("wins") or 0) + (1 if win else 0)
        s["usd"] = (s.get("usd") or 0) + (e.get("usd") or 0)
        s.setdefault("firstTs", JETZT.date().isoformat())
        s["lastTs"] = JETZT.date().isoformat()
        tage_fortschreiben(s, clv, win, *geld_aus_position(e.get("usd"), e.get("lastPrice"), einstieg, win, e.get("anteile")))
        if e.get("sport"):
            b = s.setdefault("bySport", {}).setdefault(e["sport"], {"n": 0, "clvSumPP": 0.0, "wins": 0})
            b["n"] += 1
            b["clvSumPP"] = round(b["clvSumPP"] + clv, 2)
            b["clvSqSum"] = round((b.get("clvSqSum") or 0) + clv * clv, 2)
            b["wins"] += 1 if win else 0
    grenze = JETZT - timedelta(hours=TRACK_HALTEN_H)
    for ok in list(openp):
        e = openp[ok]
        ko = _ts(e.get("koTs")) or _ts(e.get("firstTs"))
        if ko and ko < grenze:
            del openp[ok]                              # nie aufgelöst gesehen → raus
            _STAT["verfallen"] = _STAT.get("verfallen", 0) + 1
            _STAT.setdefault("verfallenKeys", set()).add(e.get("key"))
    return {"open": openp, "scores": scores, "updatedAt": JETZT.isoformat(), "geprueft": track.get("geprueft") or {}}


def erledigt_schreiben():
    """Aufgelöste Positionen (ab $1k) je Spiel ablegen: {key: {ko, sport, liga, gew, pos: [[wallet, seite, usd,
    einstieg, schluss, erstesSehen]]}}. gew None = 50:50/nicht zuordenbar."""
    if not _ERLEDIGT_NEU:
        return
    try:
        alt = json.loads(ERLEDIGT.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        alt = {}
    for e, gew in _ERLEDIGT_NEU:
        sp = alt.setdefault(e["key"], {"ko": e.get("koTs"), "sport": e.get("sport"), "liga": e.get("league"), "gew": gew, "pos": []})
        einstieg = e.get("firstPrice") if (e.get("htkFirst") or 0) > 0 else None       # 21.11. wie die Wertung
        zeile = [e.get("wallet"), e.get("side"), e.get("usd"), einstieg, e.get("closePrice"), str(e.get("firstTs") or "")[:16],
                 e.get("entryPrice")]
        sp["pos"] = [p for p in sp["pos"] if not (p[0] == zeile[0] and p[1] == zeile[1])] + [zeile]
    grenze = (JETZT - timedelta(days=ERLEDIGT_TAGE)).isoformat()
    alt = {k: v for k, v in alt.items() if str(v.get("ko") or "9") >= grenze}
    tmp = ERLEDIGT.with_suffix(".tmp")
    tmp.write_text(json.dumps(alt, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(ERLEDIGT)
    print(f"  poly_markt: {len(_ERLEDIGT_NEU)} aufgelöste Positionen archiviert · {len(alt)} Spiele im Archiv")


AUFL_FEST_H = 4          # 23.11.: so lange nach Anpfiff gilt ein klarer Preis auch ohne „closed" als Ergebnis
AUFL_FEST_P = 0.985


def aufloesung_ev(ev, ko=None):
    """Aufgelöste Preise der Sieger-Ausgänge eines Ereignisses {Etikett: Preis} — None, solange nicht entschieden.
    Dieselbe Auswahl wie beim Erfassen (ausgaenge), nur dass geschlossene Märkte zählen."""
    oc = []
    markets = ev.get("markets") or []
    # dieselbe Auswahl wie beim Erfassen, aber auch geschlossene Märkte zulassen
    cand = [(m, _markt_zeilen(m)) for m in markets]
    cand = [(m, r) for m, r in cand if r]
    if cand:
        serie = [(m, r) for m, r in cand if not _MAP_PROP_RE.search(" ".join(str(m.get(k) or "") for k in ("question", "groupItemTitle", "slug")))]
        pool = serie or cand
        pool.sort(key=lambda mr: -_mkt_vol(mr[0]))
        oc, fertig = pool[0][1], bool(pool[0][0].get("closed"))
    else:
        fertig = all(m.get("closed") for m in markets if m.get("groupItemTitle")) and any(m.get("groupItemTitle") for m in markets)
        for m in markets:
            title = m.get("groupItemTitle")
            if not title:
                continue
            try:
                names = [str(n).lower() for n in json.loads(m.get("outcomes", "[]") or "[]")]
                prices = json.loads(m.get("outcomePrices", "[]") or "[]")
                oc.append({"label": str(title), "price": float(prices[names.index("yes")])})
            except Exception:  # noqa: BLE001
                continue
    preise = [o.get("price") for o in oc if isinstance(o.get("price"), (int, float))]
    if fertig and preise and (max(preise) >= 0.98 or max(preise) - min(preise) < 0.02):
        return {o["label"]: o.get("price") for o in oc}
    # 23.11. (Prüfung): Polymarket schließt Märkte oft Tage später (UMA „proposed" oder gar nichts) — Kryvbas – Kudrivka
    # vom 21.11. stand am 23.11. noch offen bei Remis 0,981. Solche Positionen fielen nach 96 h ungewertet raus.
    # Klarer Preis (≥ 0,985) mindestens 4 h nach Anpfiff = Ergebnis.
    # Spätestens 12 h nach Anpfiff wird niemand mehr gehandelt — dann reicht ≥ 0,93 (dünne Remis-Märkte stehen bei 0,98).
    if not fertig and preise and ko and ((JETZT - ko >= timedelta(hours=AUFL_FEST_H) and max(preise) >= AUFL_FEST_P)
                                         or (JETZT - ko >= timedelta(hours=12) and max(preise) >= 0.93)):
        _STAT["auflOhneZu"] = _STAT.get("auflOhneZu", 0) + 1
        return {o["label"]: (1.0 if o.get("price") == max(preise) else 0.0) for o in oc}
    return None                                         # geschlossen, aber noch nicht ausgezahlt → nächster Lauf


def aufloesungen(track, gesehen_keys):
    """Ereignisse offener Positionen nachschlagen, deren Spiel vorbei sein sollte → {key: {Etikett: Preis}}."""
    faellig = {}
    for e in (track.get("open") or {}).values():
        ko = _ts(e.get("koTs"))
        if e.get("key") and ko and JETZT - ko > timedelta(hours=2.5):
            faellig.setdefault(e["key"], ko)
    out = {}
    # 10.11.: reihum statt immer die ältesten — Ereignisse, die (noch) nicht aufgelöst sind, belegten sonst jeden Lauf
    # das ganze Budget, und frisch beendete Spiele warteten. Zuerst nie bzw. am längsten nicht geprüfte.
    geprueft = {k: v for k, v in (track.get("geprueft") or {}).items() if k in faellig}
    reihe = sorted(faellig, key=lambda k: (geprueft.get(k) or "", faellig[k]))[:AUFLOES_BUDGET]
    for key in reihe:
        if not _zeit_ok():
            break
        geprueft[key] = JETZT.isoformat()
        ev = _get(f"{GAMMA}?slug={urllib.parse.quote(key)}")
        if not isinstance(ev, list) or not ev:
            continue
        preise = aufloesung_ev(ev[0], faellig.get(key))
        if preise:
            out[key] = preise
        else:                                           # 23.11. Prüfung: warum nicht entschieden? (erste 6 je Lauf ins Log)
            _STAT.setdefault("offenWarum", [])
            if len(_STAT["offenWarum"]) < 6:
                ms = ev[0].get("markets") or []
                _STAT["offenWarum"].append(f"{key}: {sum(1 for m in ms if m.get('closed'))}/{len(ms)} zu, "
                                           f"umaResolution={[m.get('umaResolutionStatus') for m in ms][:3]}, "
                                           f"Preise={[m.get('outcomePrices') for m in ms][:3]}, Titel={[m.get('groupItemTitle') or m.get('question') for m in ms][:3]}")
    track["geprueft"] = geprueft
    print(f"  poly_markt: Auflösung — {len(faellig)} Märkte fällig, {len(reihe)} nachgeschlagen, {len(out)} entschieden"
          f" (davon {_STAT.get('auflOhneZu', 0)} über klaren Preis ohne Schließung)")
    for z in _STAT.get("offenWarum") or []:
        print(f"    offen: {z[:420]}")
    return out


# ── Ereignisse für das Push-Buch ──────────────────────────────────────────────────────────────────────────────
# Polymarket blockt die GitHub-Server, auf denen bilanz.py rechnet. Darum schlägt dieser Mac-Lauf die Ereignisse der
# offenen Polymarket-Pushes nach (das Push-Buch kommt über den Actions-Cache „radar-…") und legt sie kompakt ab.
BUCH = Path(__file__).resolve().parent / ".push_state" / "buch.json"
EREIGNISSE = BASIS / "poly_ereignisse.json"


def buch_ereignisse():
    try:
        buch = json.loads(BUCH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return
    try:
        alt = json.loads(EREIGNISSE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        alt = {}
    keys = {}
    for b in buch:
        ko = _ts(b.get("anpfiff"))
        if b.get("art") in ("pserie", "wal", "top3p", "publicErg", "pserieGegen", "walKlein", "pk1", "pk1Streit", "cm1") and b.get("status") == "offen" and b.get("id") and ko \
                and JETZT - ko > timedelta(hours=2):
            keys[b["id"]] = ko
    neu = {}
    for key in sorted(keys, key=lambda k: keys[k])[:AUFLOES_BUDGET]:
        if not _zeit_ok():
            break
        ev = _get(f"{GAMMA}?slug={urllib.parse.quote(str(key))}")
        if isinstance(ev, list) and ev:
            neu[key] = {"t": JETZT.isoformat(), "markets": [
                {f: m.get(f) for f in ("outcomes", "outcomePrices", "closed", "groupItemTitle", "question", "slug",
                                       "volumeNum", "volume", "conditionId")} for m in ev[0].get("markets") or []]}
    grenze = (JETZT - timedelta(days=5)).isoformat()
    aus = {k: v for k, v in {**alt, **neu}.items() if str(v.get("t") or "") >= grenze}
    tmp = EREIGNISSE.with_suffix(".tmp")
    tmp.write_text(json.dumps(aus, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(EREIGNISSE)
    print(f"  poly_markt: {len(neu)} Ereignisse für das Push-Buch nachgeschlagen")


# ── Hauptlauf ────────────────────────────────────────────────────────────────────────────────────────────────
def pnl_punkte(antwort):
    """v2 user-pnl → [(epoch s, pnl $)] alt → neu. pnl = position_pnl (realisiert + offen), sonst realized_pnl. REIN."""
    d = (antwort or {}).get("data") if isinstance(antwort, dict) else None
    out = []
    for p in (d or {}).get("points") or []:
        if not isinstance(p, dict) or not isinstance(p.get("timestamp"), (int, float)):
            continue
        v = p.get("position_pnl") if isinstance(p.get("position_pnl"), (int, float)) else p.get("realized_pnl")
        if isinstance(v, (int, float)):
            t = p["timestamp"] / 1000 if p["timestamp"] > 1e12 else p["timestamp"]
            out.append((t, round(float(v), 2)))
    return sorted(out)


def main() -> int:
    BASIS.mkdir(exist_ok=True)
    try:
        track = json.loads(TRACK.read_text(encoding="utf-8"))
        geld_fix_1611(track)
        bereinigung_2111(track)
    except Exception:  # noqa: BLE001
        track = {}
    bekannt = {w for w, s in (track.get("scores") or {}).items()     # 21.11.: Live-Wertungen zählen mit (WL1)
               if isinstance(s, dict) and (s.get("n") or 0) + (s.get("liveN") or 0) >= BEKANNT_AB_N}

    maerkte, oc_je = {}, {}
    for ev, tag in ereignisse():
        zeile, oc = markt_zeile(ev, tag)
        if zeile:
            alt = maerkte.get(zeile["key"])
            if not alt or zeile["totalUsd"] > alt["totalUsd"]:
                maerkte[zeile["key"]], oc_je[zeile["key"]] = zeile, oc
    print(f"  poly_markt: {len(maerkte)} Märkte im Fenster ({_STAT['ok']} Abrufe)"
          + (f" — erster Fehler: {_STAT['erster']}" if _STAT.get("erster") else ""))
    try:
        buecher(maerkte, oc_je)
    except Exception as exc:  # noqa: BLE001 — das Schattenbuch darf den Scan nie aufhalten
        print(f"  poly_markt: Orderbuch fehlgeschlagen: {exc}")

    # Halter für die geldstärksten Spiele (live und vor Anpfiff), nicht für Kleinstmärkte
    # 07.11.: Nebenmärkte (Halbzeit, exaktes Ergebnis …) zeigt der Build ohnehin nicht — keine Halter-Abrufe dafür.
    # Und laufende Spiele bekommen höchstens die Hälfte der Plätze: an einem Fußball-Samstag nahmen sie alle 80,
    # die Spiele VOR Anpfiff (dort sitzen die Wale für die Story) blieben ohne Halter.
    haupt = [m for m in maerkte.values() if m["totalUsd"] >= MIN_VOL_HALTER and re.search(r"\d{4}-\d{2}-\d{2}$", m["key"])]
    live_k = sorted((m for m in haupt if m["live"]), key=lambda m: -m["totalUsd"])[:HALTER_SPIELE // 2]
    vor_k = sorted((m for m in haupt if not m["live"]), key=lambda m: -m["totalUsd"])
    kandidaten = live_k + vor_k[:HALTER_SPIELE - len(live_k)]
    pos_cache, pos_budget, portfolio = {}, [POS_BUDGET], {}
    alte_open = track.get("open") or {}
    for m in kandidaten:
        if not _zeit_ok():
            print("  poly_markt: Zeitdeckel erreicht, Rest im nächsten Lauf")
            break
        usd, wale, trunc = {}, [], False
        for o in oc_je[m["key"]]:
            if not (o.get("cond") and o.get("token")):
                continue
            halter, ab = alle_halter(o["cond"], o["token"])
            trunc = trunc or ab
            usd[o["label"]] = round(sum(a for _, a in halter) * o["price"])
            wale += [{"wallet": w, "side": o["label"], "usd": round(a * o["price"])} for w, a in halter]
        if sum(usd.values()) <= 0:
            continue
        m["shares"], m["splitGuete"] = usd, split_guete(usd, trunc)
        m["whales"] = wal_auswahl(wale, bekannt)
        tok = {o["label"]: o.get("token") for o in oc_je[m["key"]]}
        for wh in m["whales"]:
            if (wh.get("usd") or 0) < 1000:
                continue
            ok = f"{wh['wallet']}|{m['key']}|{wh['side']}"
            ha = HALTER_AVG.get((wh["wallet"], str(tok.get(wh["side"]))))
            if ha is not None and not m.get("live"):          # 23.11.: aus /v2/holders, nur vor Anpfiff (wie 21.11.)
                wh["avgPrice"] = ha
                _STAT["avgHalter"] = _STAT.get("avgHalter", 0) + 1
                if wh["wallet"] in pos_cache or pos_budget[0] <= 0:
                    continue
            elif (alte_open.get(ok) or {}).get("entryPrice"):
                wh["avgPrice"] = alte_open[ok]["entryPrice"]
                continue
            if wh["wallet"] not in pos_cache:
                if pos_budget[0] <= 0:
                    continue
                pos_budget[0] -= 1
                pos_cache[wh["wallet"]] = poly_v2.seiten(_get, "positions", 1, user=wh["wallet"], limit=500) or []
            pf = portfolio_wert(pos_cache[wh["wallet"]])
            if pf:
                portfolio[wh["wallet"]] = pf
            ap = avg_aus_positionen(pos_cache[wh["wallet"]], tok.get(wh["side"]))
            if ap is not None and not m.get("live"):          # 21.11.: Einstieg nur vor Anpfiff (Live-Käufe verfälschen ihn)
                wh["avgPrice"] = ap
    print(f"  poly_markt: Halter für {sum(1 for m in maerkte.values() if m['whales'])} Märkte · "
          f"{POS_BUDGET - pos_budget[0]} Einstiegs-Abrufe · Einstieg aus Halter-Liste {_STAT.get('avgHalter', 0)}×")

    aufg = aufloesungen(track, set(maerkte))
    buch_ereignisse()
    track = track_fortschreiben(track, maerkte, aufg)
    for w, pf in portfolio.items():                       # WL1: Bankroll-Näherung = Wert der offenen Positionen
        sc = track["scores"].setdefault(w, {"n": 0, "clvSumPP": 0.0, "wins": 0, "usd": 0})
        sc["portfolio"], sc["portfolioTs"] = round(pf), JETZT.isoformat()
    try:
        erledigt_schreiben()
    except Exception as exc:  # noqa: BLE001 — das Archiv darf den Scan nie aufhalten
        print(f"  poly_markt: Archiv fehlgeschlagen: {exc}")

    # Lebensbilanz: zuerst Wallets ohne Wert, dann die mit der meisten Historie
    kand = sorted((w for w, s in track["scores"].items() if isinstance(s, dict) and (s.get("n") or 0) >= 5),
                  key=lambda w: (isinstance(track["scores"][w].get("pnl"), (int, float)), str(track["scores"][w].get("pnlTs") or ""),
                                 -(track["scores"][w].get("n") or 0)))
    for w in kand[:PNL_BUDGET]:
        if not _zeit_ok():
            break
        pts = pnl_punkte(_get(PNL_API.format(user=w)))
        if pts:
            sc = track["scores"][w]
            sc["pnl"] = pts[-1][1]
            # 22.10.: dieselbe Tagesreihe liefert auch 7 und 30 Tage (Polymarket gesamt, alle Kategorien)
            for tg in (7, 30):
                vorher = [p for p in pts if p[0] <= pts[-1][0] - tg * 86400]
                sc[f"pnl{tg}"] = round(sc["pnl"] - vorher[-1][1], 2) if vorher else None
            sc["pnlTs"] = JETZT.isoformat()
            _STAT["pnlOk"] = _STAT.get("pnlOk", 0) + 1

    for p, d in ((MAERKTE, {"generatedAt": JETZT.isoformat(), "maerkte": maerkte}), (TRACK, track)):
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(p)
    if _STAT.get("wo") or _STAT.get("erster"):        # 21.11.: woher die Fehler kommen (seit Tagen ~26 je Lauf)
        print(f"  poly_markt: Fehler nach Ziel {_STAT.get('wo')} · erster: {_STAT.get('erster')}")
    if _STAT.get("clvUnmoeglich"):
        print(f"  poly_markt: {_STAT['clvUnmoeglich']} Positionen mit unmöglichem CLV (> {CLV_MAX_PP} pp) nicht gewertet")
    print(f"  poly_markt: Lebensbilanz (v2 user-pnl) für {_STAT.get('pnlOk', 0)} Wallets geholt")
    if _STAT.get("verfallen"):                          # 23.11.: Positionen, die nie aufgelöst wurden, gehen der Wertung verloren
        print(f"  poly_markt: ⚠️ {_STAT['verfallen']} Positionen nach {TRACK_HALTEN_H} h ohne Auflösung verworfen: "
              + ", ".join(sorted(_STAT["verfallenKeys"])[:10]))
    print(f"  poly_markt: {len(aufg)} Auflösungen gewertet · {len(track['open'])} offene Wal-Positionen · "
          f"{len(track['scores'])} Wallets · Abrufe ok {_STAT['ok']} / Fehler {_STAT['fehler']} · {time.time() - _START:.0f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
