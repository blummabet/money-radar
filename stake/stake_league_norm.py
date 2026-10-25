#!/usr/bin/env python3
"""
stake_league_norm.py — was in DIESER Liga ein normaler Einsatz ist, über die Zeit gelernt
================================================================================
03.09.2026 (Lucas: „das heisst wir lernen jetzt auch schon mit, was normale Einsätze für eine
Liga sind und was dann höher ist, je mehr Daten wir sammeln?").

Ja — aber nicht so, wie es zuerst gebaut war, und der Grund steht schon einmal im Repo.

## Warum die Norm nicht aus dem Ledger kommen darf
`stake_analyse.py` hat die Norm anfangs bei jedem Lauf frisch aus `stake_bet_ledger.json`
gerechnet. Das Ledger ist auf 20.000 Wetten gedeckelt, und gemessen am 03.09. laufen rund
4,3 Wetten pro Minute ein — der Deckel reicht also **etwa 3,2 Tage** zurück.

Damit sieht die Norm immer nur ein rollendes Drei-Tage-Fenster. Eine Liga, die einmal pro Woche
spielt, sammelt darin NIE die 15 Wetten, ab denen es eine Norm gibt. Genau die Ligen also, um
die es Lucas geht — die kleinen, die selten spielen — hätten dauerhaft keine Basis gehabt, und
alles wäre auf das schwächere Ersatzkriterium durchgefallen.

Das ist derselbe Fehler wie im Betfair-Badge am 24.08.: die Basis kam aus dem MOMENT statt aus
der ZEIT, worauf Fulham–Chelsea mit „×80,6 Norm" dastand, während es gegen echte EPL-Spiele
gemessen bei ×0,6 lag. Dort steht die Lehre im Kopf der Datei: *„Das Badge war nicht ungenau,
es war invertiert."*

## Wie es stattdessen läuft
Dieses Modul führt einen eigenen, wachsenden Stichprobenstand je Liga — dieselbe Bauart wie
`betfair_league_norm_state.json`:

  · Jeder Lauf trägt die NEUEN Wetten aus dem Ledger nach (dedupliziert über die Wett-ID,
    damit ein zweiter Lauf nichts doppelt zählt).
  · Je Liga bleiben die letzten JE_LIGA_MAX Stichproben und höchstens ALTER_MAX_TAGE — die
    Norm soll mitwandern, wenn eine Liga größer wird, aber nicht bei jedem Neustart vergessen.
  · Gesperrte Sportarten kommen gar nicht erst in den Stand (sie sollen die Norm nicht
    verziehen), Kombis auch nicht (ihr Einsatz gehört keinem Spiel allein).

## Was am Ende dasteht
`stake_league_norm.json`: je Liga n, Median, 90 %-Punkt, Maximum, und ab wann gemessen wurde —
dazu die Schwanz-Fits (`schwanz`) fuer die n-korrigierte Auffaelligkeit.

04.09.2026 — warum der Schwanz dazukam: `max/median` waechst mit der Stichprobengroesse
(r = +0,68 ueber 31 Ligen), und die Liga-Mediane liegen ohnehin alle um 2.000. Die Rangliste
sortierte damit nach Sammeldauer statt nach Auffaelligkeit. Die Begruendung steht vollstaendig
im Kopf von `stake_seltenheit.py`.
Unter MIN_N gibt es weiterhin **keine Norm** — nicht Median 0, nicht der globale Wert. Über
eine Liga mit vier Wetten ist nichts bekannt, und das muss anders aussehen als ein gemessenes
„unauffällig".

## Env
  STAKE_NORM_MIN_N        ab wie vielen Stichproben eine Liga eine Norm bekommt (Default 15)
  STAKE_NORM_JE_LIGA_MAX  wie viele Stichproben je Liga behalten werden (Default 600)
  STAKE_NORM_ALTER_TAGE   wie alt eine Stichprobe werden darf (Default 120)
"""
from __future__ import annotations

import os
import statistics
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE = Path(os.environ.get("STAKE_DATEN") or Path(__file__).resolve().parent)  # money-radar: Daten in .st/
sys.path.insert(0, str(BASE))

import stake_highroller_fetch as SH
from stake_seltenheit import schwanz

LEDGER_FILE = BASE / "stake_bet_ledger.json"
STATE_FILE = BASE / "stake_league_norm_state.json"
OUT_FILE = BASE / "stake_league_norm.json"

MIN_N = int(os.environ.get("STAKE_NORM_MIN_N") or 15)
JE_LIGA_MAX = int(os.environ.get("STAKE_NORM_JE_LIGA_MAX") or 600)
ALTER_MAX_TAGE = int(os.environ.get("STAKE_NORM_ALTER_TAGE") or 120)


def _ms(ts):
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp() * 1000.0


def _kat(w):
    return w.get("kat") or SH.sport_kategorie(w.get("sport"), w.get("liga"))


def taugt(w: dict) -> bool:
    """Welche Wette darf in die Norm?

    Nicht jede: eine Kombi hängt an mehreren Spielen (ihr Einsatz gehört keinem allein), eine
    Wette ohne USD-Wert ist unbekannt und nicht klein, und gesperrte Sportarten sollen die
    Norm der übrigen nicht verziehen.
    """
    if not w.get("liga") or not w.get("id"):
        return False
    if w.get("kombi"):
        return False
    if not w.get("einsatzUsd"):
        return False
    if _kat(w) in SH.GESPERRT:
        return False
    return _ms(w.get("ts")) is not None


# 25.10.2026 (Geldradar, Altlast): die Norm war nach dem ANZEIGENAMEN der Liga verschlüsselt — „Premier League",
# „Pro League", „Super League" gibt es in mehreren Ländern, die teilten sich eine Norm. Jetzt nach der Turnier-ID von
# Stake („id:<ligaId>"), der Name nur noch als Rückfall für Wetten ohne ID.
def schluessel(w: dict) -> str:
    return "id:%s" % w["ligaId"] if w.get("ligaId") else str(w.get("liga") or "")


def norm_fuer(norm: dict, w: dict):
    """Der Norm-Eintrag einer Wette: erst nach Turnier-ID, dann nach Name (alte Stände, Wetten ohne ID)."""
    norm = norm or {}
    if w.get("ligaId") and ("id:%s" % w["ligaId"]) in norm:
        return norm["id:%s" % w["ligaId"]]
    return norm.get(str(w.get("liga") or ""))


def _umschluesseln(proben: dict, wetten: list) -> dict:
    """Einmalig: Namens-Reihen auf die ID umhängen, wo der Name eindeutig EINE Liga ist; mehrdeutige Namen
    (eine Reihe aus mehreren Ligen gemischt) verwerfen — die lernen unter ihren IDs neu."""
    ids = {}
    for w in wetten:
        if w.get("liga") and w.get("ligaId"):
            ids.setdefault(str(w["liga"]), set()).add(str(w["ligaId"]))
    aus = {k: v for k, v in proben.items() if k.startswith("id:")}
    for name, reihe in proben.items():
        if name.startswith("id:"):
            continue
        s = ids.get(name)
        if not s:
            aus[name] = reihe                      # nicht im Ledger gesehen — bleibt als Rückfall
        elif len(s) == 1:
            k = "id:" + next(iter(s))
            bekannt = {p[2] for p in aus.get(k) or []}
            aus[k] = (aus.get(k) or []) + [p for p in reihe if p[2] not in bekannt]
        # mehrdeutig: weg
    return aus


def nachtragen(state: dict, wetten: list, jetzt: datetime = None) -> dict:
    """Neue Wetten in den Stichprobenstand einarbeiten. Dedupliziert über die Wett-ID —
    derselbe Lauf zweimal ändert nichts."""
    jetzt = jetzt or datetime.now(timezone.utc)
    proben = dict(state.get("samples") or {})
    if state.get("schluessel") != "id":
        proben = _umschluesseln(proben, wetten)
    zugang = 0
    for w in wetten:
        if not taugt(w):
            continue
        liga = schluessel(w)
        reihe = list(proben.get(liga) or [])
        if any(p[2] == w["id"] for p in reihe):
            continue
        reihe.append([_ms(w["ts"]), round(float(w["einsatzUsd"]), 2), w["id"]])
        proben[liga] = reihe
        zugang += 1

    # Aufräumen: zu alt raus, dann je Liga auf die jüngsten JE_LIGA_MAX kürzen. Gekürzt wird
    # am ALTEN Ende — die Norm soll mitwandern, wenn eine Liga wächst.
    grenze = (jetzt - timedelta(days=ALTER_MAX_TAGE)).timestamp() * 1000.0
    for liga in list(proben):
        reihe = [p for p in proben[liga] if p[0] and p[0] >= grenze]
        reihe.sort(key=lambda p: p[0])
        if len(reihe) > JE_LIGA_MAX:
            reihe = reihe[-JE_LIGA_MAX:]
        if reihe:
            proben[liga] = reihe
        else:
            del proben[liga]

    return {"generatedAt": jetzt.isoformat().replace("+00:00", "Z"),
            "zugangLetzterLauf": zugang, "schluessel": "id", "samples": proben}


def norm_bauen(state: dict) -> dict:
    """Aus dem Stand die Kennzahlen je Liga. Unter MIN_N: keine Zahl, nur die Zählung."""
    out = {}
    for liga, reihe in (state.get("samples") or {}).items():
        betraege = sorted(p[1] for p in reihe)
        n = len(betraege)
        eintrag = {"n": n}
        if n < MIN_N:
            eintrag.update({"basis": "zu duenn", "median": None, "p90": None, "max": None})
        else:
            eintrag.update({
                "basis": "gelernt",
                "median": round(statistics.median(betraege), 2),
                "p90": round(betraege[min(n - 1, int(round(0.9 * (n - 1))))], 2),
                "max": round(betraege[-1], 2),
            })
            # Schwanz-Fits fuer die n-korrigierte Auffaelligkeit (stake_seltenheit).
            # Getrennte Untergrenze: die Norm gibt es ab MIN_N, ein SCHAETZBARER Schwanz
            # erst ab TAIL_MIN_N. Eine Liga kann also einen Median haben und trotzdem
            # kein Seltenheitsurteil — das ist gewollt und muss unterscheidbar bleiben.
            fits = schwanz(betraege)
            eintrag["schwanz"] = fits          # None, wenn nicht schaetzbar. Kein Ersatzwert.
        zeiten = [p[0] for p in reihe if p[0]]
        if zeiten:
            eintrag["seit"] = (datetime.fromtimestamp(min(zeiten) / 1000.0, timezone.utc)
                               .isoformat().replace("+00:00", "Z"))
            eintrag["bis"] = (datetime.fromtimestamp(max(zeiten) / 1000.0, timezone.utc)
                              .isoformat().replace("+00:00", "Z"))
            eintrag["tage"] = round((max(zeiten) - min(zeiten)) / 86400000.0, 1)
        out[liga] = eintrag
    return out


def main() -> int:
    print("=== stake_league_norm.py ===")
    led = SH._lade(LEDGER_FILE, {})
    wetten = led.get("wetten") or []
    if not wetten:
        print("  ℹ️  leeres Ledger — nichts nachzutragen.")
        return 0
    state = nachtragen(SH._lade(STATE_FILE, {}), wetten)
    SH._schreibe(STATE_FILE, state)
    norm = norm_bauen(state)
    SH._schreibe(OUT_FILE, {"generatedAt": state["generatedAt"], "minN": MIN_N,
                            "jeLigaMax": JE_LIGA_MAX, "alterMaxTage": ALTER_MAX_TAGE,
                            "ligen": norm})
    gelernt = [k for k, v in norm.items() if v["basis"] == "gelernt"]
    print("  %d neue Stichproben · %d Ligen im Stand · %d davon mit Norm (ab n=%d)"
          % (state["zugangLetzterLauf"], len(norm), len(gelernt), MIN_N))
    for k in sorted(gelernt, key=lambda k: -norm[k]["n"])[:8]:
        v = norm[k]
        print("   %-32s n=%-4d Median $%-8.0f p90 $%-8.0f ueber %.1f Tage"
              % (k[:32], v["n"], v["median"], v["p90"], v.get("tage") or 0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
