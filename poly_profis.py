"""Regel W4 „Sport-Profis" (24.11., Lucas: „du kannst mir nicht erzählen, dass wir auf einem der größten Prediction-
Märkte der Welt nur 5 Wallets finden, die nachweislich im Plus sind").

Die W3-Liste kennt nur Wallets, die wir selbst als große Halter in unseren Märkten gesehen haben, und nur deren Wetten
seit Ende Oktober. Hier der Weg von der anderen Seite:
  1. Kandidaten: Polymarket-Bestenliste /v2/leaderboard (Kategorie sports und esports, Zeitraum Monat und gesamt,
     nach Gewinn) — alle 12 h, je bis 500 Plätze.
  2. Je Kandidat die abgeschlossenen Positionen der letzten 90 Tage (/v2/positions, CLOSED + REDEEMABLE +
     REDEEMABLE_LOST — sonst fehlen nicht eingelöste Verluste und die Rendite sähe zu gut aus), nur unsere Sportarten
     (Fußball, E-Sport, Tennis ohne ATP/WTA). Je Lauf höchstens PROFI_JE_LAUF Wallets (eigener Zeitdeckel), jede alle 3 Tage neu.
  3. Belegt (W4): mindestens 50 Positionen in unseren Sportarten, Rendite (Gewinn ÷ Einsatz) über null UND die
     Untergrenze der Rendite je Wette (z = 1,645) über null.
Die Sportart einer Position kommt aus dem Slug-Präfix: E-Sport/Tennis fest, Fußball-Präfixe lernt die Datei aus den
Märkten, die poly_markt sieht (z. B. „den-", „tur-", „ukr1-"). Unbekannte Präfixe zählen nicht.

Regel W5 (24.11., Lucas: „ja, können wir gewichten … keine Fehler mehr … auch wenn ein neues Wallet auftaucht, muss das
erfasst werden"):
  · Untergrenze nach EINSATZ gewichtet (Verhältnis-Schätzer Gewinn ÷ Einsatz, z = 1,645) statt je Wette gleich —
    so, wie Rendite gerechnet wird. Große Einzeleinsätze machen das Band breit, das ist gewollt.
  · Kandidaten auch aus UNSEREN Märkten: jede Wallet, die poly_markt als Wal (ab WAL_USD) sieht, kommt sofort in die
    Prüfung, VOR der Bestenliste. Bleibt 30 Tage nach der letzten Sichtung Kandidat.
  · Sportart je Präfix lernt die Datei aus allen Märkten (sport-Feld), nicht nur Fußball. Unbekannte Präfixe werden
    gezählt (unbekannt) — so sieht man, was der Wertung fehlt.
  · Plausibilität je Wallet: Verlust über Einsatz, fehlende Felder, abgeschnittene Seiten werden gezählt. Über 10 %
    unplausible Zeilen → kein Urteil („pruefen"), der Daten-Wächter meldet es.
"""
from __future__ import annotations

import json
import math
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import poly_v2

DATEI = Path(__file__).resolve().parent / ".pm" / "poly_profis.json"
JETZT = datetime.now(timezone.utc)
BOARD_H = 12
NEU_TAGE = 3
PROFI_JE_LAUF = 40       # 24.11.: Lauf braucht ~45 s von 240 s — 40 Wallets ≈ 120 Abrufe, eigener Zeitdeckel PROFI_S
PROFI_S = 90      # 24.11.: Poly-Lauf blieb sonst knapp unter dem 5-Min-Takt (bis 264 s)
FENSTER_TAGE = 90
MIN_N = 50
MIN_RENDITE = 0.02   # Regel W6 (24.11.): Auflösungs-Käufer (99 % Treffer, +0,1 %) sind kein Signal
VERSION = 5        # 2: Einsatz aus Stück × Ø Preis statt total_cost_usdc · 3: W5 (gewichtet, Plausibilität) · 4: + Gebühr · 5: Fußball-Präfixe fest + Archiv, Einsatz ≥ Verlust
WAL_USD = 500
SICHT_TAGE = 30
SEITEN, LIMIT = 3, 1000
Z = 1.645
ESPORT = ("cs2", "csgo", "lol", "dota2", "dota", "val", "valorant", "ow", "r6", "rl", "cod", "mlbb", "hok")
TENNIS = ("itf", "chall", "wtt")
# 24.11. (Log „unbekannt": epl 2886, lal 2764, sea 2137, unl 1963 …): nur aus den GERADE offenen Märkten gelernt, fehlten
# die großen Ligen, wenn an dem Tag keins ihrer Spiele lief. Feste Fußball-Präfixe + Lernen aus dem 60-Tage-Archiv.
FUSS_FEST = ("epl", "lal", "sea", "bun", "fl1", "ucl", "uel", "uecl", "unl", "mls", "ere", "por", "tur", "bra", "arg", "mex",
             "jpn", "kor", "chi", "aus", "sco", "bel", "den", "nor", "swe", "sui", "aut", "gre", "pol", "cze", "rou", "ukr",
             "sau", "efl", "elc", "fac", "cdr", "dfb", "itc", "lib", "sud", "wcq", "fif", "euro", "cop", "col", "per",
             "uru", "par", "ecu", "chl", "isr", "cyp", "hrv", "srb", "bul", "hun", "svk", "svn", "ire", "wal", "ind",
             "tha", "vie", "idn", "mys", "qat", "uae", "egy", "mar", "rsa", "can", "usl", "bun2", "lal2", "sea2", "fl2",
             "efl1", "efl2", "argpn", "ukr1", "dfb", "coppa", "afc", "caf", "concacaf", "conmebol", "kor1", "jpn1",
             "uslc", "afcq", "conl", "uwcl", "wcl", "uefq", "cafq", "conq", "nwsl", "wsl", "ligamx", "uecq")
# 24.11.: Frage-Märkte (Langzeit/Spezial: „will-arsenal-win-the-premier-league", „what-…", „how-many-…") sind keine
# Spiele — raus, sonst meldet der Wächter sie als unbekannte Liga
RAUS = ("will", "highest", "lowest", "temperature", "weather", "what", "how", "who", "which", "when", "where", "does", "is", "can", "top", "most", "us", "2026", "2027",
        "esoccer", "fifa25", "efootball", "btc", "eth", "sol", "xrp", "doge", "bnb", "crypto", "hype", "elon", "ballon", "crint", "cric", "ipl", "atp", "wta", "nba", "nfl", "mlb", "nhl", "ncaa", "cbb", "cfb", "wnba", "ufc", "mma", "f1", "nascar", "pga", "golf",
        # 25.11. (Wächter: bitcoin 825, ethereum 335): ausgeschriebene Krypto-Namen und andere Nicht-Sport-Themen gleich mit
        "bitcoin", "ethereum", "solana", "ripple", "dogecoin", "cardano", "litecoin", "chainlink", "avalanche", "hyperliquid",
        "pepe", "shiba", "tron", "sui", "spx", "sp500", "nasdaq", "dow", "gold", "silver", "oil", "fed", "fomc", "cpi",
        "trump", "election", "presidential", "mayor", "nobel", "oscars", "grammys", "eurovision")
_PRAEFIX = re.compile(r"^([a-z0-9]+)-")


def praefix(slug):
    m = _PRAEFIX.match(str(slug or "").lower())
    return m.group(1) if m else None


UNSERE = ("Fußball", "E-Sport", "Tennis")


def sportart(slug, fuss):
    """fuss = gelernte Präfixe: dict {präfix: Sport} oder (alt) Menge von Fußball-Präfixen."""
    p = praefix(slug)
    if not p or p in RAUS:
        return None
    if p in ESPORT:
        return "E-Sport"
    if p in TENNIS:
        return "Tennis"
    if isinstance(fuss, dict):
        return fuss.get(p) if fuss.get(p) in UNSERE else None
    return "Fußball" if p in fuss else None


def wertung(zeilen, fuss, ab_ts):
    """Positionen → {n, einsatz, gewinn, wins, uSum, uSq, bySport}. Je Bedingung (condition_id+outcome) einmal. REIN."""
    gesehen = set()
    out = {"n": 0, "einsatz": 0.0, "gewinn": 0.0, "wins": 0, "uSum": 0.0, "uSq": 0.0, "gSq": 0.0, "gc": 0.0, "cSq": 0.0,
           "bySport": {}, "unplausibel": 0, "ohneFeld": 0, "unbekannt": {}}
    wetten = []                                   # 24.11. Wallet-Seite: (Zeit, Einsatz, Gewinn, Sport, Titel, Seite, Ø Preis)
    for z in zeilen or []:
        if not isinstance(z, dict):
            continue
        k = (z.get("condition_id") or z.get("conditionId"), z.get("outcome_index", z.get("outcomeIndex")))
        if k in gesehen:
            continue
        gesehen.add(k)
        t = z.get("last_event_at") or z.get("first_entry_at")
        if isinstance(t, (int, float)) and t < ab_ts:
            continue
        slug = z.get("event_slug") or z.get("eventSlug") or z.get("slug")
        sp = sportart(slug, fuss)
        if not sp:
            pf = praefix(slug)
            if pf and pf not in RAUS and not (isinstance(fuss, dict) and pf in fuss):
                out["unbekannt"][pf] = out["unbekannt"].get(pf, 0) + 1
            continue
        # Einsatz = gekaufte Stück (gesamt) × Ø Einstieg. total_cost_usdc schrumpft bei Teilverkäufen (Rest-Basis) —
        # damit kam −537 % Rendite heraus, mehr Verlust als Einsatz geht nicht.
        ts_, ap = z.get("total_size", z.get("totalSize")), z.get("avg_price", z.get("avgPrice"))
        # 24.11. (Log): + Gebühr. 641 Stück × 0,56 = $359, Verlust $367 — die $8 sind entry_fees_usdc.
        gebuehr = z.get("entry_fees_usdc", z.get("entryFeesUsdc"))
        gebuehr = gebuehr if isinstance(gebuehr, (int, float)) and gebuehr > 0 else 0.0
        kosten = ts_ * ap + gebuehr if isinstance(ts_, (int, float)) and isinstance(ap, (int, float)) and ts_ * ap > 0 \
            else z.get("total_cost_usdc", z.get("entry_cost_usdc"))
        g = z.get("total_pnl", z.get("realized_pnl"))
        if not isinstance(kosten, (int, float)) or not isinstance(g, (int, float)):
            out["ohneFeld"] += 1
            continue
        if kosten < 5:
            continue
        if g < -1.05 * kosten:
            # 24.11.: geschlossene Positionen mit mehr Verlust als Stück × Ø Preis (Split/Merge, Umtausch) — der Verlust
            # laut Polymarket ist echt, also war der Einsatz mindestens so hoch: Einsatz = Verlust (vorsichtig).                    # mehr Verlust als Einsatz ist unmöglich → Datenfehler zählen
            out["unplausibel"] += 1
            if len(out.setdefault("beispiele", [])) < 2:   # Rohfelder für die Diagnose (Log + Datei)
                out["beispiele"].append({k: z.get(k) for k in ("status", "event_slug", "outcome", "total_size", "current_size",
                                         "avg_price", "current_price", "entry_cost_usdc", "total_cost_usdc", "realized_pnl",
                                         "unrealized_pnl", "total_pnl", "current_value", "redeemable", "totalBought", "initialValue")})
        kosten = max(kosten, -g)
        u = max(-1.0, min(20.0, g / kosten))
        out["n"] += 1
        out["einsatz"] += kosten
        out["gewinn"] += g
        out["wins"] += g > 0
        out["uSum"] += u
        out["uSq"] += u * u
        out["gSq"] += g * g
        out["gc"] += g * kosten
        out["cSq"] += kosten * kosten
        b = out["bySport"].setdefault(sp, [0, 0.0, 0.0])
        b[0] += 1
        b[1] += kosten
        b[2] += g
        wetten.append((t if isinstance(t, (int, float)) else ab_ts, kosten, g, sp,
                       z.get("title") or z.get("event_title") or slug, z.get("outcome"),
                       ap if isinstance(ap, (int, float)) else None))
    out.update(steckbrief(wetten, ab_ts))
    for k in ("einsatz", "gewinn", "uSum", "uSq", "gSq", "gc", "cSq"):
        out[k] = round(out[k], 3)
    out["unbekannt"] = dict(sorted(out["unbekannt"].items(), key=lambda kv: -kv[1])[:15])
    return out


KURVE_PUNKTE = 30


def steckbrief(wetten, ab_ts):
    """24.11. (Lucas: „wen könnte ich da kopieren oder was macht der"): Gewinnkurve, letzte Wetten, Stil. REIN.
    kurve = kumulierter Gewinn am Ende jedes 3-Tage-Abschnitts (30 Punkte über 90 Tage).
    preisBand = Einsatz-Anteil % mit Einstieg < 35 ¢ (Außenseiter) / 35–65 ¢ / > 65 ¢ (Favorit).
    top5 = Gewinn der 5 besten Wetten (zeigt, ob der Gewinn an wenigen Wetten hängt). neff = (ΣC)²/ΣC²."""
    if len(wetten) < 20:                          # Seite zeigt erst ab 20 Wetten (Speicher sparen)
        return {"kurve": []}
    wetten = sorted(wetten, key=lambda x: x[0])
    schritt = FENSTER_TAGE * 86400 / KURVE_PUNKTE
    kurve, summe, i = [], 0.0, 0
    for k in range(KURVE_PUNKTE):
        ende = ab_ts + (k + 1) * schritt
        while i < len(wetten) and wetten[i][0] < ende:
            summe += wetten[i][2]
            i += 1
        kurve.append(round(summe))
    while i < len(wetten):                         # jünger als das Fenster-Ende (Uhr) → letzter Punkt
        summe += wetten[i][2]
        i += 1
    kurve[-1] = round(summe)
    band, mit_preis, gew_preis = [0.0, 0.0, 0.0], 0.0, 0.0
    for _, c, _, _, _, _, p in wetten:
        if p is None:
            continue
        band[0 if p < 0.35 else 1 if p <= 0.65 else 2] += c
        mit_preis += c
        gew_preis += c * p
    C = sum(w[1] for w in wetten)
    cq = sum(w[1] * w[1] for w in wetten)
    return {"kurve": kurve,
            "letzte": [{"t": int(w[0]), "ti": str(w[4] or "")[:70], "o": str(w[5] or "")[:30], "c": round(w[1]),
                        "g": round(w[2]), "p": round(w[6], 3) if w[6] is not None else None, "sp": w[3]}
                       for w in reversed(wetten[-8:])],
            "preis": round(gew_preis / mit_preis, 3) if mit_preis else None,
            "preisBand": [round(100 * b / mit_preis) for b in band] if mit_preis else None,
            "top5": round(sum(sorted((w[2] for w in wetten), reverse=True)[:5])),
            "neff": round(C * C / cq, 1) if cq else None,
            "groesste": round(max(w[1] for w in wetten))}


def urteil(w):
    """W5 → (belegt, rendite %, untergrenze %) für eine Wertung. Untergrenze nach Einsatz gewichtet:
    R = ΣG/ΣC, SE = √(n/(n−1)·Σ(G−R·C)²)/ΣC, Σ(G−R·C)² = ΣG² − 2R·ΣGC + R²·ΣC². REIN."""
    n = w.get("n") or 0
    C = w.get("einsatz") or 0
    if n < 2 or not C:
        return False, None, None
    R = w["gewinn"] / C
    rest = max(0.0, (w.get("gSq") or 0) - 2 * R * (w.get("gc") or 0) + R * R * (w.get("cSq") or 0))
    se = math.sqrt(n / (n - 1) * rest) / C
    ug = 100 * (R - Z * se)
    return bool(n >= MIN_N and R >= MIN_RENDITE and ug > 0), round(100 * R, 1), round(ug, 1)


def plausibel(w, abgeschnitten=False):
    """→ (ok, grund). Über 10 % unplausible oder feldlose Zeilen → kein Urteil. REIN."""
    n = (w.get("n") or 0) + (w.get("ohneFeld") or 0)
    schlecht = (w.get("unplausibel") or 0) + (w.get("ohneFeld") or 0)
    if n and schlecht > max(2, 0.1 * n):
        return False, f"{schlecht} von {n} Zeilen unplausibel/ohne Feld"
    return True, ("Seiten abgeschnitten — nur die jüngsten Positionen" if abgeschnitten else None)


def lauf(get, maerkte, stat, zeit_ok=lambda: True, lern=None):
    """Ein Schritt je poly_markt-Lauf. get = poly_markt._get. → Zahl der Abrufe."""
    try:
        d = json.loads(DATEI.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        d = {}
    fuss = {p: "Fußball" for p in FUSS_FEST}
    fuss.update(d.get("praefixe") or {})
    for p in d.get("fussPraefixe") or []:         # alte Datei (nur Fußball)
        fuss.setdefault(p, "Fußball")
    jetzt_iso = JETZT.isoformat()
    sicht = dict(d.get("gesichtet") or {})        # W5: Wale aus unseren Märkten {wallet: letzte Sichtung}
    for k, sp in (lern or {}).items():            # Archiv/offene Wal-Positionen: nur Präfix → Sport
        if sp and praefix(k):
            fuss[praefix(k)] = sp
    for k, m in (maerkte or {}).items():          # Präfix → Sport lernen, Wale merken
        m = m or {}
        if m.get("sport") and praefix(k):
            fuss[praefix(k)] = m["sport"]
        for wh in m.get("whales") or []:
            w = str(wh.get("wallet") or "").lower()
            if w.startswith("0x") and float(wh.get("usd") or 0) >= WAL_USD:
                sicht[w] = jetzt_iso
    sicht = {w: t for w, t in sicht.items() if JETZT - datetime.fromisoformat(t) <= timedelta(days=SICHT_TAGE)}
    abrufe, t0 = 0, time.time()
    board = d.get("board") or {}
    alt = lambda t, h: not t or JETZT - datetime.fromisoformat(t) > timedelta(hours=h)   # noqa: E731
    if alt(board.get("ts"), BOARD_H):
        kand = {}
        for cat in ("sports", "esports"):
            for per in ("month", "all"):
                rows = poly_v2.seiten(get, "leaderboard", 1, category=cat, time_period=per, sort_by="PNL", limit=500)
                abrufe += 1
                for r in rows or []:
                    w = str(r.get("user_id") or r.get("proxyWallet") or "").lower()
                    if not w.startswith("0x") or not isinstance(r.get("pnl"), (int, float)) or r["pnl"] <= 0:
                        continue
                    e = kand.setdefault(w, {"name": r.get("user_name") or r.get("userName")})
                    e[f"{cat[:2]}_{per}"] = round(r["pnl"])
        if kand:
            board = {"ts": JETZT.isoformat(), "kandidaten": kand}
        stat["profiBoard"] = len(kand)
    kand = dict(board.get("kandidaten") or {})
    for w in sicht:
        kand.setdefault(w, {})
    wallets = d.get("wallets") or {}
    # Reihenfolge: gesichtete Wale ohne aktuelle Wertung → Bestenliste ohne aktuelle Wertung → älteste Wertung zuerst
    frisch_fehler = lambda w: not alt((wallets.get(w) or {}).get("fehlerTs"), 6)   # noqa: E731  (400er nicht jeden Lauf)
    # 24.11.: Wertungen ohne Steckbrief (Kurve, letzte Wetten) gelten als veraltet — belegte zuerst nachholen
    # 24.11. 18:00: nur Wallets mit ≥ MIN_N Wetten brauchen den Steckbrief — der volle Nachhol-Stau (2.000 Wallets)
    # lief in die Polymarket-Drosselung (HTTP 429)
    aktuell = lambda w: (wallets.get(w) or {}).get("v") == VERSION and (   # noqa: E731
        "kurve" in (wallets.get(w) or {}) or ((wallets.get(w) or {}).get("n") or 0) < MIN_N)
    reihe = sorted(kand, key=lambda w: (frisch_fehler(w), aktuell(w), not (wallets.get(w) or {}).get("belegt"),
                                        w not in sicht, (wallets.get(w) or {}).get("ts") or ""))
    ab_ts = (JETZT - timedelta(days=FENSTER_TAGE)).timestamp()
    gemacht = 0
    for w in reihe:
        if gemacht >= PROFI_JE_LAUF or not zeit_ok() or time.time() - t0 > PROFI_S:
            break
        if aktuell(w) and not alt((wallets.get(w) or {}).get("ts"), NEU_TAGE * 24):
            continue
        if frisch_fehler(w):
            continue
        zeilen, fehler, abgeschnitten = [], False, False
        for st in ("CLOSED", "REDEEMABLE", "REDEEMABLE_LOST"):
            r = poly_v2.seiten(get, "positions", SEITEN, user=w, status=st, sort_by="TIMESTAMP", limit=LIMIT,
                               start=int(ab_ts))
            abrufe += 1
            if r is None:
                fehler = True
                break
            abgeschnitten = abgeschnitten or len(r) >= SEITEN * LIMIT
            zeilen += r
        if fehler:
            stat["profiFehler"] = stat.get("profiFehler", 0) + 1
            alt_e = wallets.get(w) or {}
            alt_e["fehlerTs"] = jetzt_iso
            wallets[w] = alt_e
            continue
        if not stat.get("profiProbe") and zeilen:
            stat["profiProbe"] = sorted(zeilen[0].keys())[:40]
        wt = wertung(zeilen, fuss, ab_ts)
        bel, ren, ug = urteil(wt)
        ok, grund = plausibel(wt, abgeschnitten)
        if not ok:
            bel = False
            stat["profiUnplausibel"] = stat.get("profiUnplausibel", 0) + 1
        if wt.get("beispiele") and len(stat.setdefault("profiBeispiele", [])) < 4:
            stat["profiBeispiele"].append(wt["beispiele"][0])
        for pf, c in wt["unbekannt"].items():
            stat.setdefault("profiUnbekannt", {})[pf] = stat.get("profiUnbekannt", {}).get(pf, 0) + c
        wallets[w] = {**wt, "v": VERSION, "ts": JETZT.isoformat(), "rendite": ren, "renditeUg": ug, "belegt": bel,
                      "pruefen": None if ok else grund, "hinweis": grund if ok else None, "gesichtet": sicht.get(w),
                      "name": (kand.get(w) or {}).get("name") or next((z.get("name") for z in zeilen if isinstance(z, dict) and z.get("name")), None), "board": {k: v for k, v in (kand.get(w) or {}).items() if k != "name"},
                      "roh": len(zeilen), "abgeschnitten": abgeschnitten}
        gemacht += 1
    # Wallets, die aus der Bestenliste fallen, nach 30 Tagen vergessen
    wallets = {w: v for w, v in wallets.items() if w in kand or not alt(v.get("ts"), 30 * 24)}
    for w, v in wallets.items():                 # Sichtung nachtragen (auch ohne neue Wertung)
        if w in sicht:
            v["gesichtet"] = sicht[w]
    offen_sicht = sum(1 for w in sicht if (wallets.get(w) or {}).get("v") != VERSION)
    lauf_info = {"ts": jetzt_iso, "geprueft": gemacht, "fehler": stat.get("profiFehler", 0),
                 "unplausibel": stat.get("profiUnplausibel", 0), "offenGesichtet": offen_sicht,
                 "kandidaten": len(kand), "aktuell": sum(1 for v in wallets.values() if v.get("v") == VERSION)}
    d = {"board": board, "wallets": wallets, "praefixe": dict(sorted(fuss.items())[:3000]), "gesichtet": sicht,
         "unbekannt": stat.get("profiUnbekannt") or d.get("unbekannt") or {}, "lauf": lauf_info,
         "laeufe": ((d.get("laeufe") or []) + [lauf_info])[-60:], "ts": jetzt_iso}
    DATEI.parent.mkdir(exist_ok=True)
    DATEI.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    stat["profiGeprueft"] = gemacht
    stat["profiBelegt"] = sum(1 for v in wallets.values() if v.get("belegt") and v.get("v") == VERSION)
    stat["profiBekannt"] = len(wallets)
    stat["profiOffenGesichtet"] = offen_sicht
    return abrufe
