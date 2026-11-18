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


if __name__ == "__main__":
    schritt, ordner, aus = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    if schritt == "inventur":
        inventur(ordner, aus)
    elif schritt == "pruefen":
        pruefen(ordner, aus, sys.argv[4:])
    else:
        sys.exit(f"Schritt {schritt} kommt nach der Inventur")
