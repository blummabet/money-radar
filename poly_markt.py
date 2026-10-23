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

GAMMA = "https://gamma-api.polymarket.com/events"
HOLDERS = "https://data-api.polymarket.com/holders?market={cond}&limit=200&offset={off}"
POSITIONS = "https://data-api.polymarket.com/positions?user={user}&sizeThreshold=1&limit=500"
PNL_API = "https://user-pnl-api.polymarket.com/user-pnl?user_address={user}&interval=all&fidelity=1d"

TAGS = ("soccer", "esports", "tennis")
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
            req = urllib.request.Request(url, headers={"User-Agent": "Geldradar/1.0", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=12) as r:
                _STAT["ok"] += 1
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if (e.code == 429 or e.code >= 500) and versuch == 0:
                time.sleep(2)
                continue
        except Exception:  # noqa: BLE001
            if versuch == 0:
                time.sleep(1)
                continue
    _STAT["fehler"] += 1
    return None


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
    if t in _FOOT_TAGS:
        return "Fußball"
    for x in ev.get("tags") or []:
        slug = str((x.get("slug") if isinstance(x, dict) else x) or "").lower()
        if slug in _SPORT_BY_TAG:
            return _SPORT_BY_TAG[slug]
        if slug in _FOOT_TAGS:
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
            if str(g.get("token")) == str(token):
                rows = g.get("holders")
                break
        else:
            rows = groups
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


def alle_halter(cond, token):
    alle, gesehen, ab = [], set(), False
    for seite in range(HALTER_SEITEN):
        data = _get(HOLDERS.format(cond=cond, off=seite * 200))
        if not data:
            ab = True
            break
        rows = _halter_parse(data, token)
        neu = [(w, a) for w, a in rows if w not in gesehen]
        gesehen.update(w for w, _ in neu)
        alle.extend(neu)
        if len(rows) < 200:
            break
        if not neu or seite == HALTER_SEITEN - 1:
            ab = True
            break
    return alle, ab


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
def ereignisse():
    boden = (JETZT - timedelta(hours=FENSTER_NACH_H)).strftime("%Y-%m-%dT%H:%M:%SZ")
    gesehen = {}
    for tag in TAGS:
        for seite in range(4):
            page = _get(f"{GAMMA}?tag_slug={tag}&limit=100&offset={seite * 100}&active=true&closed=false"
                        f"&order=endDate&ascending=true&end_date_min={boden}")
            if not isinstance(page, list) or not page:
                break
            for ev in page:
                if isinstance(ev, dict) and ev.get("slug"):
                    gesehen.setdefault(ev["slug"], (ev, tag))
            if len(page) < 100:
                break
    for seite in range(3):                       # tag-los: die volumenstärksten (fängt Ligen ohne passenden Tag)
        page = _get(f"{GAMMA}?limit=100&offset={seite * 100}&active=true&closed=false&order=volume&ascending=false")
        if not isinstance(page, list) or not page:
            break
        for ev in page:
            if isinstance(ev, dict) and ev.get("slug"):
                gesehen.setdefault(ev["slug"], (ev, None))
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


def geld_aus_position(usd, last_price, entry, win):
    """(Gewinn, Einsatz) in $ einer aufgelösten Position — oder (None, None). Wie im alten Projekt:
    Anteile = usd / letzter Preis, Einsatz = Anteile × Einstieg, der Gewinner zahlt 1 $ je Anteil.
    Unterstellt Halten bis zur Auflösung (wir sehen Positionen, keine Verkäufe)."""
    try:
        u, lp, en = float(usd or 0), float(last_price), float(entry)
    except (TypeError, ValueError):
        return None, None
    if not (u > 0 and 0 < lp < 1 and 0 < en < 1):
        return None, None
    anteile = u / lp
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
                                 "htkFirst": m.get("hoursToKickoff")}
            e["lastPrice"] = round(p, 4)
            e["usd"] = round(float(wh.get("usd") or 0))
            e["marktUsd"], e["koTs"] = m.get("totalUsd"), m.get("koTs")
            if not m.get("live"):
                e["closePrice"] = round(p, 4)          # letzter Preis vor Anpfiff = Schlusspreis
            if isinstance(wh.get("avgPrice"), (int, float)):
                e["entryPrice"] = wh["avgPrice"]
    for ok in list(openp):
        e = openp[ok]
        preise = aufgeloest.get(e.get("key"))
        if preise is None:
            continue
        gew = sieger(preise)
        del openp[ok]
        if gew is None or e.get("side") not in preise:
            continue                                   # 50:50 oder Seite nicht zuordenbar → nicht werten
        # 22.10.: nur echte Werte vor Anpfiff — kein Rückfall auf den letzten Preis (der liegt im Spiel und kennt
        # den Ausgang schon) und kein erstes Sehen nach Anpfiff als Einstieg
        einstieg = e.get("entryPrice") or (e.get("firstPrice") if (e.get("htkFirst") or 0) > 0 else None)
        schluss = e.get("closePrice")
        if not isinstance(einstieg, (int, float)) or not isinstance(schluss, (int, float)):
            continue
        clv = (schluss - einstieg) * 100
        win = e["side"] == gew
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
        tage_fortschreiben(s, clv, win, *geld_aus_position(e.get("usd"), e.get("lastPrice"), einstieg, win))
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
    return {"open": openp, "scores": scores, "updatedAt": JETZT.isoformat(), "reparatur": track.get("reparatur")}


def aufloesung_ev(ev):
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
    return None                                         # geschlossen, aber noch nicht ausgezahlt → nächster Lauf


def aufloesungen(track, gesehen_keys):
    """Ereignisse offener Positionen nachschlagen, deren Spiel vorbei sein sollte → {key: {Etikett: Preis}}."""
    faellig = {}
    for e in (track.get("open") or {}).values():
        ko = _ts(e.get("koTs"))
        if e.get("key") and ko and JETZT - ko > timedelta(hours=2.5):
            faellig.setdefault(e["key"], ko)
    out = {}
    for key in sorted(faellig, key=lambda k: faellig[k])[:AUFLOES_BUDGET]:
        if not _zeit_ok():
            break
        ev = _get(f"{GAMMA}?slug={urllib.parse.quote(key)}")
        if not isinstance(ev, list) or not ev:
            continue
        preise = aufloesung_ev(ev[0])
        if preise:
            out[key] = preise
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
        if b.get("art") in ("pserie", "wal", "top3p") and b.get("status") == "offen" and b.get("id") and ko \
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
REPARATUR = "2026-10-22"


def reparatur_2210(track):
    """Einmalig: die Wallet-Bilanzen seit der Übernahme (20.10.) sind durch den Anpfiff-Fehler verfälscht. Die
    Bilanzen werden aus dem alten Projekt neu übernommen (es misst dieselben Wallets bis heute richtig); offene
    Positionen bleiben. Ist das alte Projekt nicht erreichbar, werden die Schlusskurs-Felder geleert statt falsch
    weitergeführt."""
    if track.get("reparatur") == REPARATUR:
        return track
    try:   # 11 MB — eigener, längerer Abruf statt _get (12 s)
        with urllib.request.urlopen("https://raw.githubusercontent.com/blummabet/betting-dashboard/main/poly_wallet_track.json", timeout=90) as r:
            alt = json.loads(r.read())
    except Exception:  # noqa: BLE001
        alt = None
    if isinstance(alt, dict) and isinstance(alt.get("scores"), dict) and len(alt["scores"]) > 1000:
        track["scores"] = alt["scores"]
        print(f"  poly_markt: Reparatur — {len(alt['scores'])} Wallet-Bilanzen aus dem alten Projekt neu übernommen")
    else:
        for sc in (track.get("scores") or {}).values():
            for k in ("clvFenN", "clvFenSum", "clvSqSum"):
                sc.pop(k, None)
        print("  poly_markt: Reparatur — altes Projekt nicht erreichbar, Schlusskurs-Fenster geleert")
    track["reparatur"] = REPARATUR
    return track


def main() -> int:
    BASIS.mkdir(exist_ok=True)
    try:
        track = json.loads(TRACK.read_text(encoding="utf-8"))
        track = reparatur_2210(track)
    except Exception:  # noqa: BLE001
        track = {}
    bekannt = {w for w, s in (track.get("scores") or {}).items() if isinstance(s, dict) and (s.get("n") or 0) >= BEKANNT_AB_N}

    maerkte, oc_je = {}, {}
    for ev, tag in ereignisse():
        zeile, oc = markt_zeile(ev, tag)
        if zeile:
            alt = maerkte.get(zeile["key"])
            if not alt or zeile["totalUsd"] > alt["totalUsd"]:
                maerkte[zeile["key"]], oc_je[zeile["key"]] = zeile, oc
    print(f"  poly_markt: {len(maerkte)} Märkte im Fenster ({_STAT['ok']} Abrufe)")

    # Halter für die geldstärksten Spiele (live und vor Anpfiff), nicht für Kleinstmärkte
    kandidaten = sorted((m for m in maerkte.values() if m["totalUsd"] >= MIN_VOL_HALTER),
                        key=lambda m: (not m["live"], -m["totalUsd"]))[:HALTER_SPIELE]   # laufende Spiele zuerst, jedes Mal
    pos_cache, pos_budget = {}, [POS_BUDGET]
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
            if (alte_open.get(ok) or {}).get("entryPrice"):
                wh["avgPrice"] = alte_open[ok]["entryPrice"]
                continue
            if wh["wallet"] not in pos_cache:
                if pos_budget[0] <= 0:
                    continue
                pos_budget[0] -= 1
                pos_cache[wh["wallet"]] = _get(POSITIONS.format(user=wh["wallet"])) or []
            ap = avg_aus_positionen(pos_cache[wh["wallet"]], tok.get(wh["side"]))
            if ap is not None:
                wh["avgPrice"] = ap
    print(f"  poly_markt: Halter für {sum(1 for m in maerkte.values() if m['whales'])} Märkte · "
          f"{POS_BUDGET - pos_budget[0]} Einstiegs-Abrufe")

    aufg = aufloesungen(track, set(maerkte))
    buch_ereignisse()
    track = track_fortschreiben(track, maerkte, aufg)

    # Lebensbilanz: zuerst Wallets ohne Wert, dann die mit der meisten Historie
    kand = sorted((w for w, s in track["scores"].items() if isinstance(s, dict) and (s.get("n") or 0) >= 5),
                  key=lambda w: (isinstance(track["scores"][w].get("pnl"), (int, float)), str(track["scores"][w].get("pnlTs") or ""),
                                 -(track["scores"][w].get("n") or 0)))
    for w in kand[:PNL_BUDGET]:
        if not _zeit_ok():
            break
        data = _get(PNL_API.format(user=w))
        if isinstance(data, list) and data and isinstance(data[-1], dict):
            try:
                sc = track["scores"][w]
                sc["pnl"] = round(float(data[-1].get("p")), 2)
                # 22.10.: dieselbe Tagesreihe liefert auch 7 und 30 Tage (Polymarket gesamt, alle Kategorien)
                sek = lambda x: (lambda t: t / 1000 if t > 1e12 else t)(float(x.get("t") or 0))   # Sekunden oder ms
                jetzt_t = sek(data[-1]) or JETZT.timestamp()
                for tg in (7, 30):
                    vorher = [x for x in data if isinstance(x, dict) and sek(x) <= jetzt_t - tg * 86400]
                    sc[f"pnl{tg}"] = round(sc["pnl"] - float(vorher[-1].get("p")), 2) if vorher else None
                sc["pnlTs"] = JETZT.isoformat()
            except (TypeError, ValueError):
                pass

    for p, d in ((MAERKTE, {"generatedAt": JETZT.isoformat(), "maerkte": maerkte}), (TRACK, track)):
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(p)
    print(f"  poly_markt: {len(aufg)} Auflösungen gewertet · {len(track['open'])} offene Wal-Positionen · "
          f"{len(track['scores'])} Wallets · Abrufe ok {_STAT['ok']} / Fehler {_STAT['fehler']} · {time.time() - _START:.0f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
