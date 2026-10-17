#!/usr/bin/env python3
"""
poly_fluss.py — wohin fließt auf Polymarket gerade das NEUE Geld? (Lucas, 17.10.2026)

„Bei Polymarket interessiert mich dasselbe": nicht das Volumen eines Spiels, sondern auf welche
SEITE die Käufe der letzten 30 Minuten gingen. Quelle sind die einzelnen Trades je Markt
(data-api.polymarket.com/trades), zugeordnet über die Gamma-Ereignisse (Slug → Märkte → Seiten).

Läuft auf dem Mac-Runner „radar" (Polymarket blockt Rechenzentren), alle ~10 Min. Liest die
Marktliste aus dem alten Projekt (öffentliches Repo), schreibt .pf/poly_fluss.json. Nichts wird
committet — die Datei reist über den Actions-Cache zum Seitenbau.

Zuordnung:
  * 1X2 (drei Ja/Nein-Märkte „Gewinnt X?"): Kauf von „Ja" = Geld AUF X, Kauf von „Nein" = Geld GEGEN X.
  * Zwei-Wege (E-Sport, Tennis, Über/Unter): Kauf einer Seite = Geld auf diese Seite.
  * Verkäufe zählen nicht als neues Geld (das ist Geld, das geht).
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAW = "https://raw.githubusercontent.com/blummabet/betting-dashboard/main/"
GAMMA = "https://gamma-api.polymarket.com/events?slug={slug}"
TRADES = "https://data-api.polymarket.com/trades?market={cond}&limit=500&takerOnly=true"
OUT = Path(__file__).resolve().parent / ".pf" / "poly_fluss.json"
JETZT = datetime.now(timezone.utc)
FENSTER_MIN = (30, 10)
TAKT_MIN, TAKT_N = 10, 24          # Säulen für die Serien: 24 × 10 Min = 4 h (Lucas 17.10.)
MAX_EVENTS = int(os.environ.get("POLY_FLUSS_MAX") or 60)
SPORTARTEN = {"Fußball", "E-Sport", "Tennis"}
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126 Safari/537.36",
      "Accept": "application/json"}

try:
    import certifi  # noqa: F401
    CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:  # noqa: BLE001
    CTX = ssl.create_default_context()


def get(url, tries=2):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20, context=CTX) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            if i == tries - 1:
                print(f"  ⚠️ {url[:90]}: {str(exc)[:120]}")
            time.sleep(0.6)
    return None


def ts(s):
    try:
        if isinstance(s, (int, float)):
            return datetime.fromtimestamp(float(s), tz=timezone.utc)
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def kandidaten():
    """Märkte aus dem alten Projekt: kommende 30 h + laufende, erlaubte Sportarten, kein ATP/WTA."""
    up = get(RAW + "poly_money_upcoming.json") or {}
    live = get(RAW + "poly_money_broad_live.json") or {}
    pool = {}
    for quelle, ist_live in ((up, False), (live, True)):
        for key, m in (quelle or {}).items():
            if not isinstance(m, dict) or key.endswith("-exact-score") or key.startswith(("atp-", "wta-")):
                continue
            if (m.get("sport") or "Fußball") not in SPORTARTEN:
                continue
            k = ts(m.get("koTs"))
            if not k or not (-2.5 <= (k - JETZT).total_seconds() / 3600 <= 30):
                continue
            pool[key] = {"key": key, "sport": m.get("sport") or "Fußball", "liga": m.get("league"),
                         "anpfiff": m.get("koTs"), "usd": float(m.get("totalUsd") or 0),
                         "live": ist_live or (k <= JETZT)}
    # nach Volumen, Live-Spiele bevorzugt (dort ändert sich am meisten)
    return sorted(pool.values(), key=lambda m: (not m["live"], -m["usd"]))[:MAX_EVENTS]


def seiten(event):
    """Gamma-Event → [(conditionId, {outcome: Etikett-für-Kauf})]."""
    out = []
    for mk in event.get("markets") or []:
        cond = mk.get("conditionId")
        if not cond or mk.get("closed"):
            continue
        try:
            outs = json.loads(mk.get("outcomes") or "[]")
        except Exception:  # noqa: BLE001
            outs = []
        titel = (mk.get("groupItemTitle") or "").strip()
        if [o.lower() for o in outs] == ["yes", "no"] and titel:
            lab = {"Yes": titel, "No": f"gegen {titel}"}
        elif titel and len(event.get("markets") or []) > 1:
            lab = {o: f"{o} ({titel})" for o in outs}       # z. B. „Under (O/U 2.5)"
        else:
            lab = {o: o for o in outs}
        out.append((cond, lab, mk.get("question") or titel))
    return out


def fluss_fuer(m):
    ev = get(GAMMA.format(slug=urllib.parse.quote(m["key"])))
    if not isinstance(ev, list) or not ev:
        return None
    event = ev[0]
    summe = {f: {} for f in FENSTER_MIN}
    je_markt = {f: {} for f in FENSTER_MIN}     # cond -> {Etikett: usd}
    gross = []
    takt, takt_summe, aeltester = {}, [0.0] * TAKT_N, 0.0
    for cond, lab, frage in seiten(event):
        tr = get(TRADES.format(cond=cond))
        if not isinstance(tr, list):
            continue
        for t in tr:
            if str(t.get("side") or "").upper() != "BUY":
                continue
            t0 = ts(t.get("timestamp"))
            if not t0:
                continue
            alter = (JETZT - t0).total_seconds() / 60
            try:
                usd = float(t.get("size") or 0) * float(t.get("price") or 0)
            except (TypeError, ValueError):
                continue
            etikett = lab.get(t.get("outcome")) or t.get("outcome") or "?"
            aeltester = max(aeltester, alter)
            b = int(alter // TAKT_MIN)
            if 0 <= b < TAKT_N:
                i = TAKT_N - 1 - b                       # links alt, rechts neu
                takt.setdefault(etikett, [0.0] * TAKT_N)[i] += usd
                takt_summe[i] += usd
            for f in FENSTER_MIN:
                if alter <= f:
                    summe[f][etikett] = summe[f].get(etikett, 0) + usd
                    jm = je_markt[f].setdefault(cond, {})
                    jm[etikett] = jm.get(etikett, 0) + usd
            if alter <= 30 and usd >= 1000:
                gross.append({"auf": etikett, "usd": round(usd), "preis": round(float(t.get("price") or 0), 3),
                              "vor": round(alter), "wallet": t.get("proxyWallet")})
        time.sleep(0.05)
    titel = event.get("title") or m["key"]
    fenster = {}
    mehrere = m["key"].endswith("-more-markets")
    for f, s in summe.items():
        ges = sum(s.values())
        if ges <= 0:
            continue
        if mehrere:
            # Über/Unter-Linien, BTTS … sind eigene Märkte: Anteil INNERHALB des Marktes, sonst verwässert er
            best = None
            for cond, sm in je_markt[f].items():
                mg = sum(sm.values())
                lab, v = max(sm.items(), key=lambda kv: kv[1])
                if not best or v > best[1]:
                    best = (lab, v, mg)
            top_lab, top_usd, basis = best
        else:
            top_lab, top_usd = max(s.items(), key=lambda kv: kv[1])
            basis = ges
        fenster[str(f)] = {"neu": round(ges), "auf": top_lab, "usd": round(top_usd), "anteil": round(100 * top_usd / basis),
                           "seiten": {k: round(v) for k, v in sorted(s.items(), key=lambda kv: -kv[1])[:5]}}
    if not fenster:
        return None
    gross.sort(key=lambda g: -g["usd"])
    top = sorted(takt.items(), key=lambda kv: -sum(kv[1]))[:4]
    return {**m, "titel": titel, "fenster": fenster, "gross": gross[:5],
            "takt": {k: [round(x) for x in v] for k, v in top}, "taktSumme": [round(x) for x in takt_summe],
            # die Trade-Abfrage holt höchstens 500 je Markt — so weit zurück reichen die Säulen wirklich
            "taktReichweiteMin": round(aeltester)}


def main() -> int:
    """17.10.: abends (viele Live-Spiele, viele Trades) brauchte der Lauf nacheinander > 8 Min und wurde
    vom Zeitlimit abgebrochen — dann gab es GAR KEINE Datei. Jetzt parallel (8 gleichzeitig) und mit
    Zeitbudget: was bis dahin fertig ist, wird geschrieben, der Rest kommt im nächsten Lauf."""
    from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
    OUT.parent.mkdir(exist_ok=True)
    k = kandidaten()
    print(f"poly_fluss: {len(k)} Märkte")
    rows, start, budget = [], time.time(), float(os.environ.get("POLY_FLUSS_BUDGET_S") or 240)
    with ThreadPoolExecutor(max_workers=8) as pool:
        offen = {pool.submit(fluss_fuer, m) for m in k}
        while offen:
            rest = budget - (time.time() - start)
            if rest <= 0:
                print(f"  Zeitbudget {budget:.0f}s erreicht — {len(offen)} Märkte bleiben für den nächsten Lauf")
                for f in offen:
                    f.cancel()
                break
            fertig, offen = wait(offen, timeout=rest, return_when=FIRST_COMPLETED)
            for f in fertig:
                try:
                    r = f.result()
                except Exception as exc:  # noqa: BLE001
                    print(f"  ⚠️ {str(exc)[:120]}")
                    continue
                if r:
                    rows.append(r)
        pool.shutdown(wait=False, cancel_futures=True)
    rows.sort(key=lambda r: -(r["fenster"].get("30") or {}).get("usd", 0))
    OUT.write_text(json.dumps({"generatedAt": JETZT.isoformat(), "n": len(rows), "maerkte": rows},
                              ensure_ascii=False), encoding="utf-8")
    print(f"poly_fluss: {len(rows)} Märkte mit Käufen in 30 Min → {OUT} ({time.time() - start:.0f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
