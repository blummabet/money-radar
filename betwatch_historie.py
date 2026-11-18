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


if __name__ == "__main__":
    schritt, ordner, aus = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    if schritt == "inventur":
        inventur(ordner, aus)
    else:
        sys.exit(f"Schritt {schritt} kommt nach der Inventur")
