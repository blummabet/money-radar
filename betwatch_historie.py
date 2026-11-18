"""Betwatch-Historie (gekauft, 18.11.2026) — Inventur und später Nachspielen in unser 5-Minuten-Format.

Läuft NUR auf dem Mac (Workflow betwatch.yml): Rohdaten liegen unter ~/betwatch/<archiv>/JJJJ/MM/TT/<matchId>/
  <matchId>.json          Meta: Teams, Liga, Land, Anstoß, Märkte (Name, league_average, Runner mit Eröffnungs-/Anstoßquote)
  <marketId>.parquet      jeder Trade: date, cote (Quote), volume (gematcht), runner_identifier, score
  <matchId>_score.parquet Spielstand-Verlauf
Ausgabe nur klein (Ordner `ergebnis`): inventur.json, spiele.csv.gz.

Aufruf: python -I betwatch_historie.py inventur <ordner> <ausgabe>
"""
from __future__ import annotations

import csv
import gzip
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

JUNG = re.compile(r"\bU-?(1[5-9]|2[0-3])\b|Youth|Junior|Reserve|\bII\b|\bB\b|Women|Femen|Frauen|\(W\)| W$|Primavera|Development|Academy|Amateur", re.I)


def _ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def teams(m):
    """teams ist meist {home:{name}, away:{name}}, bei manchen Spielen eine Liste."""
    t = m.get("teams") or {}
    if isinstance(t, list):
        if not getattr(teams, "gezeigt", False):
            teams.gezeigt = True
            print("teams als Liste, Beispiel:", json.dumps(t)[:300], m.get("league"), flush=True)
        nm = [(x.get("name") if isinstance(x, dict) else str(x)) for x in t] + [None, None]
        return nm[0], nm[1]
    g = lambda k: (t.get(k) or {}).get("name") if isinstance(t.get(k), dict) else t.get(k)
    return g("home"), g("away")


def spiele(ordner: Path):
    for meta in sorted(ordner.glob("*/*/*/*/*.json")):
        if meta.name.startswith("._"):
            continue
        yield meta


def inventur(ordner: Path, aus: Path):
    import pyarrow.parquet as pq

    n = 0
    tage, ligen, laender, maerkte = Counter(), Counter(), Counter(), Counter()
    liga_vol = defaultdict(float)
    ticks_je_spiel, vol_je_spiel, vor_std, maerkte_je_spiel = [], [], [], []
    ohne_ticks = ohne_score = jung = 0
    tick_summe = leer = 0
    vol_summe_ticks = vol_summe_meta = 0.0
    fehler = []
    zeilen = []
    for meta in spiele(ordner):
        try:
            m = json.loads(meta.read_text())
        except Exception as e:  # noqa: BLE001
            fehler.append(f"{meta.name}: {e}")
            continue
        n += 1
        d = meta.parent
        ko = _ts(m.get("kickoff"))
        liga = f"{m.get('league')} [{m.get('country')}]"
        ligen[liga] += 1
        laender[m.get("country") or "?"] += 1
        if ko:
            tage[ko.date().isoformat()] += 1
        h, a = teams(m)
        ist_jung = bool(JUNG.search(f"{m.get('league')} {h} {a}"))
        jung += ist_jung
        ticks = 0
        frueh = None
        vol_meta = 0.0
        for mk in m.get("markets") or []:
            maerkte[re.sub(r"\d+\.\d", "X.5", mk.get("name") or "?")] += 1
            vol_meta += sum(r.get("volume") or 0 for r in mk.get("runners") or [])
            p = d / f"{mk.get('market_id')}.parquet"
            if not p.exists():
                continue
            try:
                f = pq.ParquetFile(p)
                if not {"date", "volume"} <= set(f.schema_arrow.names):
                    leer += 1
                    continue
                t = f.read(columns=["date", "volume"])
            except Exception as e:  # noqa: BLE001
                fehler.append(f"{p.name}: {e}")
                continue
            ticks += t.num_rows
            vol_summe_ticks += sum(v for v in t.column("volume").to_pylist() if v)
            if t.num_rows:
                erst = min(t.column("date").to_pylist())
                erst = erst if isinstance(erst, datetime) else _ts(erst)
                if erst and erst.tzinfo is None and ko:
                    erst = erst.replace(tzinfo=ko.tzinfo)
                if erst and (frueh is None or erst < frueh):
                    frueh = erst
        vol_summe_meta += vol_meta
        liga_vol[liga] += vol_meta
        tick_summe += ticks
        ticks_je_spiel.append(ticks)
        vol_je_spiel.append(vol_meta)
        maerkte_je_spiel.append(len(m.get("markets") or []))
        ohne_ticks += ticks == 0
        ohne_score += not (d / f"{m.get('match_id')}_score.parquet").exists()
        if frueh and ko:
            vor_std.append((ko - frueh).total_seconds() / 3600)
        zeilen.append([m.get("match_id"), m.get("kickoff"), h, a, m.get("league"), m.get("country"),
                       m.get("final_score"), round(vol_meta), ticks, len(m.get("markets") or []), int(ist_jung)])
        if n % 500 == 0:
            print(f"… {n} Spiele", flush=True)

    def q(xs, p):
        xs = sorted(xs)
        return round(xs[min(len(xs) - 1, int(p * len(xs)))], 1) if xs else None

    inv = {
        "spiele": n, "tage": dict(sorted(tage.items())), "ligen": len(ligen), "laender": len(laender),
        "topLigen": ligen.most_common(40), "ligenNachVolumen": sorted(((k, round(v)) for k, v in liga_vol.items()), key=lambda x: -x[1])[:40],
        "nachwuchsReserveFrauen": jung, "ohneTicks": ohne_ticks, "ohneSpielstand": ohne_score, "leereMarktDateien": leer,
        "ticksGesamt": tick_summe, "ticksJeSpiel": {"median": q(ticks_je_spiel, .5), "p10": q(ticks_je_spiel, .1), "p90": q(ticks_je_spiel, .9)},
        "volumenJeSpiel": {"median": q(vol_je_spiel, .5), "p10": q(vol_je_spiel, .1), "p90": q(vol_je_spiel, .9)},
        "volumenTicksZuMeta": round(vol_summe_ticks / vol_summe_meta, 4) if vol_summe_meta else None,
        "erstHandelStundenVorAnstoss": {"median": q(vor_std, .5), "p10": q(vor_std, .1), "p90": q(vor_std, .9)},
        "maerkteJeSpiel": {"median": statistics.median(maerkte_je_spiel) if maerkte_je_spiel else None},
        "marktArten": maerkte.most_common(30), "fehler": fehler[:20], "fehlerAnzahl": len(fehler),
    }
    aus.mkdir(parents=True, exist_ok=True)
    (aus / "inventur.json").write_text(json.dumps(inv, ensure_ascii=False, indent=1))
    with gzip.open(aus / "spiele.csv.gz", "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(["matchId", "kickoff", "home", "away", "league", "country", "final", "vol", "ticks", "maerkte", "jung"])
        w.writerows(zeilen)
    print(json.dumps({k: v for k, v in inv.items() if k not in ("tage", "topLigen", "ligenNachVolumen", "marktArten")}, ensure_ascii=False, indent=1))
    print("Tage:", len(tage), min(tage) if tage else "-", "bis", max(tage) if tage else "-")
    print("Top-Ligen:", ligen.most_common(15))


BF_ARTEN = None  # alle Buch-Einträge mit Betwatch-Spiel-ID (Zahl) werden geprüft


def pruefen(ordner: Path, aus: Path, buch_dateien):
    """Nachspiel-Prüfung: Sehen die gekauften Trades zum Push-Zeitpunkt dasselbe wie unser Live-System?
    Je Buch-Eintrag (Betfair-Arten, Oktober): Quote = letzter Trade / Ø der letzten 5 Min vor dem Push,
    Geld auf die Auswahl in den 15/30/60 Min davor (zum Vergleich mit `eur`)."""
    import pyarrow.parquet as pq
    from datetime import timedelta

    buch = []
    for f in buch_dateien:
        try:
            buch += [x for x in json.loads(Path(f).read_text()) if isinstance(x, dict)]
        except Exception as e:  # noqa: BLE001
            print("Buch nicht lesbar:", f, e)
    ordn = {m.parent.name: m for m in spiele(ordner)}
    tage = sorted({m.parts[-5] + "-" + m.parts[-4] + "-" + m.parts[-3] for m in ordn.values()})
    von, bis = tage[0], tage[-1] + "T23:59"
    kand, gesehen = [], set()
    for b in buch:
        t = str(b.get("ts") or "")
        if not (von <= t[:10] and t[:16] <= bis) or not str(b.get("id") or "").isdigit():
            continue
        k = (b.get("art"), b.get("id"), b.get("markt"), b.get("auswahl"), t[:16])
        if k in gesehen:
            continue
        gesehen.add(k)
        kand.append(b)
    print(f"Buch: {len(buch)} Einträge, im Datenzeitraum mit Betfair-ID: {len(kand)}", flush=True)
    cache = {}
    erg = []
    stat = Counter()
    for b in kand:
        art = b.get("art")
        meta = ordn.get(str(b["id"]))
        if not meta:
            stat["spielFehlt"] += 1
            erg.append({"art": art, "id": b["id"], "ts": b["ts"], "status": "spielFehlt", "liga": b.get("liga")})
            continue
        m = cache.get(meta)
        if m is None:
            m = cache[meta] = json.loads(meta.read_text())
        mk = next((x for x in m.get("markets") or [] if x.get("name") == b.get("markt")), None)
        rn = next((r for r in (mk or {}).get("runners") or [] if r.get("name") == b.get("auswahl")), None)
        if not rn:
            stat["marktFehlt"] += 1
            erg.append({"art": art, "id": b["id"], "ts": b["ts"], "status": "marktFehlt", "markt": b.get("markt"), "auswahl": b.get("auswahl"),
                        "maerkte": [x.get("name") for x in m.get("markets") or []][:25]})
            continue
        p = meta.parent / f"{mk['market_id']}.parquet"
        try:
            t = pq.read_table(p, columns=["date", "cote", "volume", "runner_identifier"]).to_pydict()
        except Exception:  # noqa: BLE001
            stat["keineTicks"] += 1
            continue
        push = _ts(b["ts"])
        ri = rn.get("runner_identifier")
        z = [(d, c, v) for d, c, v, r in zip(t["date"], t["cote"], t["volume"], t["runner_identifier"]) if r == ri and d <= push]
        if not z:
            stat["keinHandelVorher"] += 1
            erg.append({"art": art, "id": b["id"], "ts": b["ts"], "status": "keinHandelVorher"})
            continue
        letzte = z[-1][1]
        f5 = [(c, v) for d, c, v in z if d >= push - timedelta(minutes=5)]
        vw5 = sum(c * v for c, v in f5) / sum(v for c, v in f5) if f5 and sum(v for c, v in f5) else None
        geld = {w: sum(v for d, c, v in z if d >= push - timedelta(minutes=w)) for w in (15, 30, 60)}
        q = b.get("quote")
        abw = round(100 * (letzte / q - 1), 1) if isinstance(q, (int, float)) and q > 1 else None
        stat["geprueft"] += 1
        erg.append({"art": art, "id": b["id"], "ts": b["ts"], "status": "ok", "live": bool(b.get("live")), "liga": b.get("liga"),
                    "quote": q, "letzte": letzte, "vw5": round(vw5, 3) if vw5 else None, "abwPct": abw,
                    "alterLetzterTradeMin": round((push - z[-1][0]).total_seconds() / 60, 1),
                    "eur": b.get("eur"), "geld15": geld[15], "geld30": geld[30], "geld60": geld[60], "summeBis": sum(v for _, _, v in z)})
    ok = [e for e in erg if e["status"] == "ok" and e["abwPct"] is not None]

    def zus(xs):
        a = sorted(abs(e["abwPct"]) for e in xs)
        if not a:
            return None
        return {"n": len(a), "medianAbwPct": a[len(a) // 2], "bis2Pct": round(sum(x <= 2 for x in a) / len(a), 3),
                "bis5Pct": round(sum(x <= 5 for x in a) / len(a), 3)}

    je_art = {}
    for e in ok:
        je_art.setdefault(e["art"], []).append(e)
    verh = {}
    for w in ("geld15", "geld30", "geld60", "summeBis"):
        r = sorted(e[w] / e["eur"] for e in ok if isinstance(e.get("eur"), (int, float)) and e["eur"] > 0)
        verh[w] = {"n": len(r), "median": round(r[len(r) // 2], 3) if r else None}
    bericht = {"zeitraum": [von, bis], "kandidaten": len(kand), "status": dict(stat), "quoteGesamt": zus(ok),
               "quoteVorAnpfiff": zus([e for e in ok if not e["live"]]), "quoteLive": zus([e for e in ok if e["live"]]),
               "quoteJeArt": {k: zus(v) for k, v in sorted(je_art.items(), key=lambda x: -len(x[1]))},
               "geldZuEur": verh,
               "groessteAbweichungen": sorted(ok, key=lambda e: -abs(e["abwPct"]))[:15],
               "fehlendeMaerkte": [e for e in erg if e["status"] == "marktFehlt"][:10],
               "fehlendeSpieleLigen": Counter(e.get("liga") for e in erg if e["status"] == "spielFehlt").most_common(20)}
    aus.mkdir(parents=True, exist_ok=True)
    (aus / "pruefung.json").write_text(json.dumps(bericht, ensure_ascii=False, indent=1, default=str))
    with gzip.open(aus / "pruefung_zeilen.json.gz", "wt") as f:
        json.dump(erg, f, default=str)
    print(json.dumps(bericht, ensure_ascii=False, indent=1, default=str)[:12000])


# ───────────────────────── Nachspielen + Backtest (Lucas 18.11. „ok mach") ─────────────────────────
# Vorab festgelegt (18.11.2026, 09:00 Wien), damit nichts nachträglich passend gemacht wird:
# * Zeitraster 5 Min wie live; Quote je Auswahl = Ø-Preis (volumengewichtet) der letzten 5 Min, sonst letzter Trade.
# * Regeln wie live (build.py/push.py, Stand 18.11.): Zufluss 30 Min (≥ €10k / Top-5 €50k, ≥ 65 % des neuen Geldes),
#   Live-Zufluss 15 Min seit dem letzten Tor (≥ €15k / €100k), Dauerzufluss (Serie: ≥ 3 Abrufe ≥ 60 %, Boden €1k / €10k,
#   Lücken ≤ 16 Min, Summe ≥ max(€10k / €75k, ¼ Liga-Norm), live nur seit dem letzten Tor), Betfair-Drop BD2
#   (≥ 6 pp gegenüber 24 h vor Anpfiff, −12 %, 3 Abrufe, €500 Auswahl / €2k Markt).
# * Gezählt wird je Spiel + Markt + Auswahl (+ Stand bei Live-Zufluss) nur das ERSTE Auslösen (wie die Statistik).
# * Ergebnis: Betfair-Abrechnung (WINNER/LOSER aus den Daten), Rendite zur Signal-Quote OHNE Provision
#   (Betfair nimmt ~5 % vom Gewinn — steht extra dabei). CLV vor Anpfiff gegen den Ø-Preis der letzten 5 Min vor Anpfiff.
# * Liga-Norm = Median des Gesamtvolumens je Spiel dieser Liga im selben Monat (leichter Blick nach vorn, wird genannt).
# * Abweichungen zu live, die bleiben: kein Pinnacle, kein Stake/Poly, keine Pausen-/Spam-Regeln, kein „in Serie"-Abgleich.
import bisect
from datetime import timedelta, timezone

TOP5_RX = re.compile(r"^(english premier league|german bundesliga|spanish la liga|italian serie a|french ligue 1|uefa champions league|uefa nations league( a)?)$", re.I)
BD_MAERKTE = re.compile(r"^(Match Odds|Half Time|Over/Under \d\.5 Goals|First Half Goals \d\.5|Both teams to Score\?)$")
SIEG_MAERKTE = ("Match Odds", "Half Time", "Draw no Bet")
SCHRITT = 300
VOR_H = 24


def _ep(d):
    return d.timestamp()


class Runner:
    __slots__ = ("t", "cv", "cpv", "p")

    def __init__(self, zeilen):
        zeilen.sort()
        self.t, self.cv, self.cpv, self.p = [], [], [], []
        v = pv = 0.0
        for t, c, vol in zeilen:
            v += vol
            pv += vol * c
            self.t.append(t); self.cv.append(v); self.cpv.append(pv); self.p.append(c)

    def vol(self, t):
        i = bisect.bisect_right(self.t, t)
        return self.cv[i - 1] if i else 0.0

    def quote(self, t, fenster=300):
        i = bisect.bisect_right(self.t, t)
        if not i:
            return None
        j = bisect.bisect_right(self.t, t - fenster)
        if i > j:
            dv = self.cv[i - 1] - (self.cv[j - 1] if j else 0)
            if dv > 0:
                return (self.cpv[i - 1] - (self.cpv[j - 1] if j else 0)) / dv
        return self.p[i - 1]


def _linie_durch(markt, stand):
    m = re.match(r"^(?:Over/Under|First Half Goals) (\d+)\.5", str(markt or ""))
    return bool(m and stand and sum(stand) > int(m.group(1)))


def _spiel_laden(meta_pfad):
    import pyarrow.parquet as pq
    m = json.loads(meta_pfad.read_text())
    d = meta_pfad.parent
    maerkte = {}
    for mk in m.get("markets") or []:
        p = d / f"{mk.get('market_id')}.parquet"
        if not p.exists():
            continue
        try:
            f = pq.ParquetFile(p)
            if not {"date", "cote", "volume", "runner_identifier"} <= set(f.schema_arrow.names):
                continue
            t = f.read(columns=["date", "cote", "volume", "runner_identifier"]).to_pydict()
        except Exception:  # noqa: BLE001
            continue
        je = defaultdict(list)
        for dt, c, v, r in zip(t["date"], t["cote"], t["volume"], t["runner_identifier"]):
            if c and c > 1 and v:
                je[r].append((_ep(dt), float(c), float(v)))
        rn = {}
        for r in mk.get("runners") or []:
            z = je.get(r.get("runner_identifier"))
            if z:
                rn[r.get("name")] = (Runner(z), r.get("status"))
        if rn:
            maerkte[mk.get("name")] = rn
    stand = []
    sp = d / f"{m.get('match_id')}_score.parquet"
    if sp.exists():
        try:
            t = pq.read_table(sp, columns=["date", "time", "goal_v1", "goal_v2", "is_ht", "finished"]).to_pydict()
            stand = sorted(zip([_ep(x) for x in t["date"]], t["goal_v1"], t["goal_v2"], t["time"], t["is_ht"], t["finished"]))
        except Exception:  # noqa: BLE001
            stand = []
    return m, maerkte, stand


def _stand_bei(stand, t):
    i = bisect.bisect_right([x[0] for x in stand], t) if stand else 0
    return stand[i - 1] if i else None


def nachspielen_spiel(meta_pfad, norm_je_liga):
    m, maerkte, stand = _spiel_laden(meta_pfad)
    ko = _ts(m.get("kickoff"))
    if not ko or not maerkte:
        return [], None
    ko_e = ko.timestamp()
    liga = m.get("league") or ""
    top5 = bool(TOP5_RX.search(liga))
    h, a = teams(m)
    jung = bool(JUNG.search(f"{liga} {h} {a}"))
    norm = norm_je_liga.get(liga) or 0
    letzte = max(r.t[-1] for rn in maerkte.values() for r, _ in rn.values())
    start = (ko_e - VOR_H * 3600) // SCHRITT * SCHRITT
    fertig = next((x[0] for x in stand if x[5]), None)
    ende = min(letzte, fertig or letzte, ko_e + 3 * 3600)
    if ende <= start:
        return [], None
    signale = []
    treffer_je = defaultdict(list)  # key → [(t, dv, anteil, q, sc)] — Abrufe, in denen die Auswahl vorn lag
    ko_q = {}
    for mk, rn in maerkte.items():
        for n, (r, st) in rn.items():
            ko_q[(mk, n)] = r.quote(ko_e - 1)
    basis = {(mk, n): r.quote(ko_e - VOR_H * 3600, 3600) or (r.p[0] if r.t else None) for mk, rn in maerkte.items() for n, (r, _) in rn.items()}
    bd_streak = defaultdict(int)
    letztes_tor_t = None
    vor_sc = None
    t = start + SCHRITT
    while t <= ende:
        live = t >= ko_e
        s = _stand_bei(stand, t) if live else None
        sc = (s[1], s[2]) if s else ((0, 0) if live else None)
        minute = s[3] if s else (0 if live else None)
        if live and vor_sc is not None and sc != vor_sc:
            letztes_tor_t = t
        vor_sc = sc if live else vor_sc
        mo = maerkte.get("Match Odds") or {}
        mo_q = {n: r.quote(t) for n, (r, _) in mo.items()}
        entschieden = bool(live and minute is not None and (minute >= 85 or (minute >= 30 and min([q for q in mo_q.values() if q] or [9]) <= 1.10)))
        klar = False
        if live and sc and sc[0] != sc[1]:
            fq = mo_q.get(h if sc[0] > sc[1] else a)
            klar = bool(fq and fq < 1.40)
        # ── Takt (5 Min) ──
        hts = {}
        for mk, rn in maerkte.items():
            d_ = {n: r.vol(t) - r.vol(t - SCHRITT) for n, (r, _) in rn.items()}
            ges = sum(x for x in d_.values() if x > 0)
            if ges < 100:
                continue
            n, dv = max(d_.items(), key=lambda kv: kv[1])
            hts[(mk, n)] = (dv, 100 * dv / ges, rn[n][0].quote(t))
        boden_s = 10000 if top5 else 1000
        for key, x in hts.items():
            if x[0] >= boden_s and x[1] >= 60:
                treffer_je[key].append((t, *x, sc))
        gemein = {"id": m.get("match_id"), "liga": liga, "top5": top5, "jung": jung, "live": live, "min": minute,
                  "stand": list(sc) if sc else None, "t": t, "ko": ko_e}
        # ── Zufluss / Live-Zufluss ──
        fen = 15 if live else 30
        t0 = t - fen * 60
        if live and letztes_tor_t and letztes_tor_t > t0:
            t0 = letztes_tor_t
        if t0 < t and (not live or t - t0 >= 300):
            beste = None
            for mk, rn in maerkte.items():
                if live and (_linie_durch(mk, sc) or ((entschieden or klar) and mk in SIEG_MAERKTE)):
                    continue
                d_ = {n: r.vol(t) - r.vol(t0) for n, (r, _) in rn.items()}
                ges = sum(x for x in d_.values() if x > 0)
                if ges <= 0:
                    continue
                n, dv = max(d_.items(), key=lambda kv: kv[1])
                q, qv = rn[n][0].quote(t), rn[n][0].quote(t0)
                if dv < (1500 if live else 3000) or dv / ges < 0.65 or (q and q < 1.20):
                    continue
                if not live and q and qv and q > qv * 1.01:
                    continue
                if live and q and qv and not (1 / 1.4 <= q / qv <= 1.4):
                    continue
                if beste is None or dv > beste[2]:
                    beste = (mk, n, dv, round(100 * dv / ges), q, qv)
            if beste:
                mk, n, dv, ant, q, qv = beste
                schw = (100000 if top5 else 15000) if live else (50000 if top5 else 10000)
                q_ok = (q and q >= 1.40) or (not live and qv and qv >= 1.40 and q and q >= 1.15)
                if dv >= schw and q_ok:
                    art = "live" if live else "zufluss"
                    if live and sc and n in (h, a):
                        k_ = 0 if n == h else 1
                        if sc[k_] > sc[1 - k_]:
                            art = "liveFuehrt"
                    signale.append({**gemein, "art": art, "markt": mk, "auswahl": n, "eur": round(dv), "anteil": ant,
                                    "quote": q, "quoteVorher": qv,
                                    "key": (art, mk, n, tuple(sc) if live and sc else None)})
        # ── Dauerzufluss (Serie) ──
        if True:
            for key in hts:
                tr = treffer_je.get(key) or []
                if len(tr) < 3 or tr[-1][0] != t:
                    continue
                kette = [tr[-1]]
                for x in reversed(tr[:-1]):
                    if kette[-1][0] - x[0] <= 16 * 60:
                        kette.append(x)
                    else:
                        break
                kette.reverse()
                if live:
                    kette = [k for k in kette if (k[4] or (0, 0)) == sc]
                if len(kette) < 3:
                    continue
                summe = sum(k[1] for k in kette)
                if summe < max(75000 if top5 else 10000, 0.25 * norm):
                    continue
                mk, n = key
                if live and (((entschieden or klar) and mk in SIEG_MAERKTE) or _linie_durch(mk, sc)):
                    continue
                if live and re.match(r"^(Half Time|First Half)", mk) and minute is not None and minute > 45:
                    continue
                q0, q1 = kette[0][3], kette[-1][3]
                if q1 is not None and q1 < 1.20:
                    continue
                if not live and q0 and q1 and q1 > q0 * 1.01:
                    continue
                art = "serie"
                if live and sc and n in (h, a):
                    k_ = 0 if n == h else 1
                    if sc[k_] > sc[1 - k_]:
                        art = "serieFuehrt"
                signale.append({**gemein, "art": art, "markt": mk, "auswahl": n, "eur": round(summe), "n": len(kette),
                                "anteil": round(sum(k[2] for k in kette) / len(kette)), "quote": q1, "quoteVorher": q0,
                                "letzte15": round(sum(k[1] for k in kette if t - k[0] <= 15 * 60)),
                                "key": (art, mk, n, None)})
        # ── Betfair-Drop BD2 (vor Anpfiff, letzte 24 h) ──
        if not live:
            for mk, rn in maerkte.items():
                if not BD_MAERKTE.match(mk):
                    continue
                mk_eur = sum(r.vol(t) for r, _ in rn.values())
                if mk_eur < 2000:
                    continue
                for n, (r, _) in rn.items():
                    q1, q0 = r.quote(t), basis.get((mk, n))
                    ok = bool(q1 and q0 and 1.15 <= q1 <= 10 and r.vol(t) >= 500 and (1 / q1 - 1 / q0) * 100 >= 6 and q1 <= q0 * 0.88)
                    bd_streak[(mk, n)] = bd_streak[(mk, n)] + 1 if ok else 0
                    if bd_streak[(mk, n)] >= 3:
                        signale.append({**gemein, "art": "bfDrop", "markt": mk, "auswahl": n, "eur": round(r.vol(t)),
                                        "quote": q1, "quoteVorher": q0, "pp": round((1 / q1 - 1 / q0) * 100, 1),
                                        "key": ("bfDrop", mk, n, None)})
        t += SCHRITT
    # erstes Auslösen je Schlüssel, abrechnen
    erst, aus = set(), []
    for x in signale:
        k = x.pop("key")
        if k in erst:
            continue
        erst.add(k)
        r, status = maerkte[x["markt"]][x["auswahl"]]
        if status not in ("WINNER", "LOSER") or not x.get("quote"):
            continue
        x["win"] = status == "WINNER"
        x["r"] = round(x["quote"] - 1, 3) if x["win"] else -1.0
        kq = ko_q.get((x["markt"], x["auswahl"]))
        if not x["live"] and kq:
            x["clv"] = round(100 * (x["quote"] / kq - 1), 2)
            for w in (15, 30):
                qn = r.quote(min(x["t"] + w * 60, ko_e - 1))
                x[f"q{w}"] = round(qn, 3) if qn else None
            x["qKo"] = round(kq, 3)
        if x["live"]:
            qn = r.quote(x["t"] + 600)
            s2 = _stand_bei(stand, x["t"] + 600)
            if qn and s2 and x.get("stand") and [s2[1], s2[2]] == x["stand"]:
                x["mo10"] = round(100 * (x["quote"] / qn - 1), 2)
        x["quote"] = round(x["quote"], 3)
        x["quoteVorher"] = round(x["quoteVorher"], 3) if x.get("quoteVorher") else None
        aus.append(x)
    # Kontrolle: jede Match-Odds-Auswahl zum Preis 60 Min vor Anpfiff
    kontrolle = []
    for n, (r, status) in (maerkte.get("Match Odds") or {}).items():
        q = r.quote(ko_e - 3600, 1800)
        if q and status in ("WINNER", "LOSER") and 1.15 <= q <= 15:
            kontrolle.append((q, status == "WINNER", liga, top5, jung))
    return aus, kontrolle


def _bewerten(zeilen):
    n = len(zeilen)
    if not n:
        return {"n": 0}
    r = sum(z["r"] for z in zeilen)
    gew = sum(1 for z in zeilen if z["win"])
    r_prov = sum((z["r"] * 0.95 if z["r"] > 0 else z["r"]) for z in zeilen)
    clv = [z["clv"] for z in zeilen if z.get("clv") is not None]
    mo = [z["mo10"] for z in zeilen if z.get("mo10") is not None]
    q = sorted(z["quote"] for z in zeilen)
    # Unsicherheit: Standardfehler der Rendite je Einsatz
    m = r / n
    sd = (sum((z["r"] - m) ** 2 for z in zeilen) / max(1, n - 1)) ** 0.5
    return {"n": n, "treffer": round(gew / n, 3), "roi": round(100 * m, 1), "roiNachProvision": round(100 * r_prov / n, 1),
            "fehlerRoi": round(100 * sd / n ** 0.5, 1), "quoteMedian": q[n // 2],
            "clv": round(sum(clv) / len(clv), 2) if clv else None, "clvN": len(clv),
            "markout10": round(sum(mo) / len(mo), 2) if mo else None, "markoutN": len(mo)}


def _qeimer(q):
    return "<1,60" if q < 1.6 else "1,60–2,19" if q < 2.2 else "2,20–3,99" if q < 4 else "≥4,00"


PROZESSE = 4          # Mac hat 8 Kerne; der 5-Min-Bau läuft nebenher weiter (gleicher Runner wartet aber, darum kurz halten)
_NORM = {}


def _init_norm(n):
    _NORM.update(n)


def _ein_spiel(pfad):
    try:
        return nachspielen_spiel(Path(pfad), _NORM)
    except Exception as e:  # noqa: BLE001
        print("Fehler", Path(pfad).parent.name, repr(e)[:200], flush=True)
        return [], []


def backtest(ordner: Path, aus: Path):
    metas = list(spiele(ordner))
    # Liga-Norm: Median Gesamtvolumen je Spiel und Liga
    vol_liga = defaultdict(list)
    for mp in metas:
        try:
            m = json.loads(mp.read_text())
        except Exception:  # noqa: BLE001
            continue
        vol_liga[m.get("league") or ""].append(sum(r.get("volume") or 0 for mk in m.get("markets") or [] for r in mk.get("runners") or []))
    norm = {k: sorted(v)[len(v) // 2] for k, v in vol_liga.items() if len(v) >= 3}
    alle, kontrolle = [], []
    import time
    t0 = time.time()
    import multiprocessing as mp_
    with mp_.get_context("spawn").Pool(PROZESSE, initializer=_init_norm, initargs=(norm,)) as pool:
        for i, (s, k) in enumerate(pool.imap_unordered(_ein_spiel, [str(x) for x in metas], chunksize=8)):
            alle += s
            kontrolle += k or []
        if i % 250 == 0:
            print(f"… {i}/{len(metas)} Spiele, {len(alle)} Signale, {time.time() - t0:.0f}s", flush=True)
    bericht = {"spiele": len(metas), "signale": len(alle), "regeln": "siehe betwatch_historie.py, vorab festgelegt 18.11.2026"}
    # Kontrolle (Markt ohne Signal)
    kz = [{"r": (q - 1) if w else -1.0, "win": w, "quote": q} for q, w, *_ in kontrolle]
    bericht["kontrolleAlleMatchOdds60vorAnpfiff"] = _bewerten(kz)
    je = defaultdict(list)
    for z in alle:
        je[z["art"]].append(z)
    bericht["jeArt"] = {k: _bewerten(v) for k, v in sorted(je.items(), key=lambda x: -len(x[1]))}
    gruppen = {}
    for art, zz in je.items():
        g = {}
        for name, f in (("Top-5", lambda z: z["top5"]), ("Rest", lambda z: not z["top5"]),
                        ("Nachwuchs/Reserve/Frauen", lambda z: z["jung"]), ("ohne Nachwuchs", lambda z: not z["jung"]),
                        ("vor Anpfiff", lambda z: not z["live"]), ("live", lambda z: z["live"]),
                        ("Match Odds", lambda z: z["markt"] == "Match Odds"), ("Tor-Linien", lambda z: "Goals" in z["markt"]),
                        ("Push-Quote ≥ 1,40", lambda z: z["quote"] >= 1.40)):
            g[name] = _bewerten([z for z in zz if f(z)])
        for qb in ("<1,60", "1,60–2,19", "2,20–3,99", "≥4,00"):
            g["Quote " + qb] = _bewerten([z for z in zz if _qeimer(z["quote"]) == qb])
        if art in ("serie", "zufluss", "live"):
            for lo, hi in ((0, 20000), (20000, 50000), (50000, 150000), (150000, 1e12)):
                g[f"Geld €{lo // 1000}k–{'' if hi > 1e11 else str(int(hi // 1000)) + 'k'}"] = _bewerten([z for z in zz if lo <= z["eur"] < hi])
        if art == "serie":
            for lo, hi in ((3, 4), (4, 6), (6, 10), (10, 999)):
                g[f"Serie {lo}–{hi - 1}×"] = _bewerten([z for z in zz if lo <= z.get("n", 0) < hi])
            g["live · Quote ≥ 2,20"] = _bewerten([z for z in zz if z["live"] and z["quote"] >= 2.2])
            g["live · Quote < 2,20"] = _bewerten([z for z in zz if z["live"] and z["quote"] < 2.2])
            g["live · Nachwuchs"] = _bewerten([z for z in zz if z["live"] and z["jung"]])
            g["letzte 15 Min ≥ ½ der Summe"] = _bewerten([z for z in zz if z.get("letzte15", 0) >= z["eur"] / 2])
            g["letzte 15 Min < ½ der Summe"] = _bewerten([z for z in zz if z.get("letzte15", 0) < z["eur"] / 2])
        gruppen[art] = g
    bericht["gruppen"] = gruppen
    # PV1: sofort vs. 15/30 Min warten vs. Anpfiff (nur vor Anpfiff)
    pv = {}
    for art, zz in je.items():
        vor = [z for z in zz if not z["live"] and z.get("qKo")]
        if len(vor) < 20:
            continue
        def roi_bei(feld):
            xs = [((z[feld] - 1) if z["win"] else -1.0) for z in vor if z.get(feld)]
            return {"n": len(xs), "roi": round(100 * sum(xs) / len(xs), 1)} if xs else None
        pv[art] = {"sofort": roi_bei("quote"), "nach15": roi_bei("q15"), "nach30": roi_bei("q30"), "anpfiff": roi_bei("qKo")}
    bericht["wartenOderSenden"] = pv
    # Wochen (Stabilität)
    wo = defaultdict(lambda: defaultdict(list))
    for z in alle:
        w = datetime.fromtimestamp(z["t"], timezone.utc).strftime("%G-W%V")
        wo[z["art"]][w].append(z)
    bericht["jeWoche"] = {a: {w: _bewerten(v)["roi"] for w, v in sorted(ws.items())} for a, ws in wo.items()}
    aus.mkdir(parents=True, exist_ok=True)
    (aus / "backtest.json").write_text(json.dumps(bericht, ensure_ascii=False, indent=1))
    with gzip.open(aus / "signale.json.gz", "wt") as f:
        json.dump(alle, f, separators=(",", ":"))
    print("BERICHT_START")
    print(json.dumps(bericht, ensure_ascii=False))
    print("BERICHT_ENDE")


if __name__ == "__main__":
    schritt, ordner, aus = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    if schritt == "inventur":
        inventur(ordner, aus)
    elif schritt == "backtest":
        backtest(ordner, aus)
    elif schritt == "pruefen":
        pruefen(ordner, aus, sys.argv[4:])
    else:
        sys.exit(f"Schritt {schritt} kommt nach der Inventur")
