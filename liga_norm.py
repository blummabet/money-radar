#!/usr/bin/env python3
"""
betfair_league_norm.py — gelernte Liga-Basis fuers ×-Norm-Badge (24.08.2026, Lucas).

## Das Problem
Das Badge „×N Norm" im Radar soll sagen: liegt auf DIESEM Spiel ungewoehnlich viel Geld? Dafuer
braucht es eine Antwort auf „was ist ueblich?". Bis heute kam die aus dem AKTUELLEN Schnappschuss:
Median aller gerade laufenden Spiele derselben Liga+Phase, mit Fallback auf den globalen Pool.

Am 22.08. wurde die Liga-Stufe eingebaut — sie greift aber praktisch nie. Ein Schnappschuss hat je
Liga fast nie 4 Spiele (24.08.: 2 von 34 Ligen), also faellt fast alles auf den GLOBALEN Pool durch,
und der ist voll mit Slovenian U19 (Median ~11K). Ergebnis: Fulham–Chelsea „×80.6 Norm",
Roma–Fiorentina „×20.9". Gemessen an echten EPL-Spielen liegt Fulham–Chelsea bei ×0.6 — das Badge
war nicht ungenau, es war invertiert.

## Die Loesung
Die Basis muss aus der ZEIT kommen, nicht aus dem Moment: was ist ueblich fuer ein EPL-Spiel in
dieser Phase, ueber die letzten Wochen? Genau das sammelt dieses Modul.

Quelle ist betfair_history.json (Volumen-Kurve je Event). Die Liga steht dort nicht drin — sie kommt
per Join ueber betfair_prices.json (heutige Spiele) und betfair_track_results.json (abgerechnete
Signale, traegt matchId + league). Deckung am 24.08.: 1030 von 1050 Events.

Wichtig fuers Messen: eine Phase wird erst dann als Stichprobe eingefroren, wenn das Spiel sie
VERLASSEN hat. Sonst traegt ein Spiel, das gerade erst angepfiffen wurde, seinen halben l1-Stand bei
und drueckt den Median. Volumen waechst nur — je Phase zaehlt der hoechste gesehene Stand.

## Dateien
  liest  betfair_history.json, betfair_prices.json, betfair_track_results.json
  fuehrt betfair_league_norm_state.json — rollende Stichproben je Liga|Phase (Arbeitsstand)
  schreibt betfair_league_norm.json — nur die fertigen Mediane, klein genug fuers Dashboard
"""
import json
from datetime import datetime, timezone
from pathlib import Path

# money-radar 20.10.2026 (Lucas: „muss autonom im neuen Projekt laufen"): Kopie aus dem alten Projekt, liest und
# schreibt nur noch .bf/ (Actions-Cache). Die Liga älterer Spiele kommt aus dem eigenen Ergebnisbuch (ergebnisse.py).
BASE = Path(__file__).resolve().parent / ".bf"
HISTORY_FILE = BASE / "betfair_history.json"
PRICES_FILE = BASE / "betfair_prices.json"
RESULTS_FILE = BASE / "ergebnisse.json"
STATE_FILE = BASE / "betfair_league_norm_state.json"   # Stichproben (gross, nur fuer dieses Modul)
NORM_FILE = BASE / "betfair_league_norm.json"           # nur die Mediane — DAS laedt das Dashboard

WINDOW_DAYS = 60      # aelter als das faellt raus (Ligen aendern ihr Geldniveau ueber eine Saison)
SAMPLE_CAP = 150      # je Liga|Phase hoechstens so viele Stichproben (neueste gewinnen)
MIN_EUR = 3000        # Kleckerspiele verzerren den Median nach unten -> nicht als Basis zaehlen
# Lucas 25.10.: vor Anpfiff liegen in kleinen Ligen oft erst €500–2.000 — mit €3.000 fielen genau diese Spiele raus,
# die Norm kam zu hoch, ein kleines Spiel mit auffällig viel Geld sah unauffällig aus. Vor Anpfiff daher €500.
MIN_EUR_VOR = 500
LIVE_MAX_H = 3.5      # so lange nach Anpfiff gilt ein Spiel als laufend, danach vorbei
STAGES = ("p0", "p1", "l1", "l2")


# ── reiner Kern ──────────────────────────────────────────────────────────────
def _ms(s):
    """ISO-Zeitstempel → Millisekunden. None bei allem Unlesbaren. REIN."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp() * 1000
    except Exception:
        return None


def stage_of(ts_ms, kick_ms):
    """Spielphase zu einem Zeitpunkt — identisch zu _stageOf in betfair-radar.js. REIN.

    p0 = mehr als 3h vor Anpfiff · p1 = letzte 3h davor · l1 = 1. Halbzeit · l2 = ab 45' ·
    None = vorbei (oder Anpfiff unbekannt).
    """
    if ts_ms is None or kick_ms is None:
        return None
    d = ts_ms - kick_ms
    if d >= 0:
        if d > LIVE_MAX_H * 3.6e6:
            return None
        return "l2" if d > 45 * 60000 else "l1"
    return "p1" if -d <= 3 * 3.6e6 else "p0"


def _stage_idx(st):
    return STAGES.index(st) if st in STAGES else len(STAGES)


TOT_QUOTE = 1.15     # Lucas 22.10. (Persik Kediri): Geld zu Quoten darunter ist Absicherung/Einsammeln, kein Interesse


def totes_geld(s):
    """Volumen eines Schnappschusses auf Auswahlen unter TOT_QUOTE (aus "sel": {Markt: {Auswahl: [vol, quote]}}). REIN."""
    tot = 0.0
    for mk in (s.get("sel") or {}).values():
        for v in (mk or {}).values():
            if isinstance(v, list) and len(v) > 1 and isinstance(v[1], (int, float)) and v[1] < TOT_QUOTE:
                tot += float(v[0] or 0)
    return tot


def samples_from_history(history, leagues, now_ms):
    """Je Event+Phase eine Stichprobe → [{"league","stage","vol","eid","ts"}]. REIN.

    Nur ABGESCHLOSSENE Phasen: ein Spiel, das gerade in l1 steht, liefert p0 und p1, aber kein l1 —
    dessen Endstand kennen wir noch nicht. Je Phase zaehlt der hoechste gesehene Stand (Volumen
    waechst monoton; der letzte Snapshot einer Phase ist ihr Endstand).
    """
    out = []
    for eid, snaps in (history or {}).items():
        league = (leagues or {}).get(str(eid))
        if not league or not isinstance(snaps, list) or not snaps:
            continue
        kick = None
        for s in snaps:
            if isinstance(s, dict) and s.get("kickoff"):
                kick = _ms(s["kickoff"])
        if kick is None:
            continue
        cur = _stage_idx(stage_of(now_ms, kick))     # aktuelle Phase; alles davor ist fertig
        best, last_ts = {}, {}
        for s in snaps:
            if not isinstance(s, dict):
                continue
            ts, vol = _ms(s.get("ts")), s.get("totalVol")
            if ts is None or not isinstance(vol, (int, float)):
                continue
            vol = float(vol) - totes_geld(s)
            st = stage_of(ts, kick)
            if st is None or _stage_idx(st) >= cur:   # laufende (und unbekannte) Phase: noch offen
                continue
            if float(vol) > best.get(st, 0):
                best[st] = float(vol)
            last_ts[st] = max(last_ts.get(st, ts), ts)
        for st, vol in best.items():
            if vol >= (MIN_EUR_VOR if st in ("p0", "p1") else MIN_EUR):
                out.append({"league": league, "stage": st, "vol": round(vol),
                            "eid": str(eid), "ts": last_ts.get(st), "rein": True})
    return out


def merge_samples(store, new, now_ms):
    """Neue Stichproben in den Bestand mischen: dedup je (Bucket, Event), Fenster + Kappung. REIN.

    Ein Event taucht bei jedem Lauf erneut in der History auf — ohne Dedup waere der Median die
    Anzahl der Laeufe, nicht die Anzahl der Spiele.
    """
    buckets = {k: list(v) for k, v in (store or {}).items() if isinstance(v, list)}
    for s in new:
        key = "%s|%s" % (s["league"], s["stage"])
        rows = buckets.setdefault(key, [])
        hit = next((r for r in rows if len(r) > 2 and r[2] == s["eid"]), None)
        rein = 1 if s.get("rein") else 0
        if hit:
            alt_rein = len(hit) > 3 and hit[3] == 1
            # 22.10.: eine bereinigte Messung (ohne Geld unter 1,15) ersetzt die alte ungefilterte, auch wenn sie kleiner ist
            if (rein and not alt_rein) or s["vol"] > hit[1]:
                hit[:] = [s["ts"], s["vol"], s["eid"], rein]
        else:
            rows.append([s["ts"], s["vol"], s["eid"], rein])
    cutoff = now_ms - WINDOW_DAYS * 86400000
    out = {}
    for key, rows in buckets.items():
        rows = [r for r in rows if isinstance(r, list) and len(r) > 2
                and isinstance(r[0], (int, float)) and r[0] >= cutoff]
        rows.sort(key=lambda r: r[0])
        if len(rows) > SAMPLE_CAP:
            rows = rows[-SAMPLE_CAP:]
        if rows:
            out[key] = rows
    return out


def aggregate(buckets):
    """Bucket → {"med","n"}. Median, nicht Mittel: ein einzelnes Grossspiel soll die Basis nicht
    verbiegen — genau davor schuetzt das Badge ja. REIN."""
    out = {}
    for key, rows in (buckets or {}).items():
        vals = sorted(float(r[1]) for r in rows if len(r) > 1 and isinstance(r[1], (int, float)))
        if vals:
            out[key] = {"med": round(vals[len(vals) // 2]), "n": len(vals),
                        "rein": sum(1 for r in rows if len(r) > 3 and r[3] == 1)}   # davon schon ohne Absicherungsgeld gemessen
    return out


def league_map(prices, results):
    """matchId → Liga. Heutige Spiele aus betfair_prices, aeltere aus dem abgerechneten Ledger. REIN."""
    out = {}
    if isinstance(results, dict):                     # eigenes Ergebnisbuch: {matchId: {l: Liga, …}}
        results = [{"matchId": k, "league": v.get("l")} for k, v in results.items() if isinstance(v, dict)]
    for row in (results or []):
        if isinstance(row, dict) and row.get("matchId") and row.get("league"):
            out[str(row["matchId"])] = row["league"]
    for m in ((prices or {}).get("matches") or []):
        if isinstance(m, dict) and m.get("matchId") and m.get("league"):
            out[str(m["matchId"])] = m["league"]      # frischer Stand gewinnt
    return out


# ── I/O ──────────────────────────────────────────────────────────────────────
def _load(p, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def main():
    print("=== betfair_league_norm.py ===")
    now = datetime.now(timezone.utc)
    now_ms = now.timestamp() * 1000
    history = _load(HISTORY_FILE, {})
    if not history:
        print("  ℹ️  Keine betfair_history.json — nichts zu lernen."); return
    leagues = league_map(_load(PRICES_FILE, {}), _load(RESULTS_FILE, {}))
    prev = _load(STATE_FILE, {})
    new = samples_from_history(history, leagues, now_ms)
    buckets = merge_samples(prev.get("samples") or {}, new, now_ms)
    agg = aggregate(buckets)
    total = sum(v["n"] for v in agg.values())
    usable = sum(1 for v in agg.values() if v["n"] >= 4)
    # Stichproben und Ergebnis getrennt: das Dashboard laedt die Basis bei JEDEM Refresh — es soll
    # die Mediane bekommen (ein paar KB), nicht den ganzen Arbeitsstand (>100 KB).
    STATE_FILE.write_text(json.dumps({"generatedAt": now.isoformat(), "samples": buckets},
                                     ensure_ascii=False), encoding="utf-8")
    NORM_FILE.write_text(json.dumps({
        "generatedAt": now.isoformat(),
        "windowDays": WINDOW_DAYS, "minEur": MIN_EUR,
        "n": total, "buckets": len(agg), "usable": usable,
        "byLeagueStage": agg,
    }, ensure_ascii=False), encoding="utf-8")
    print("  📚 %d Stichproben in %d Liga|Phase-Buckets (%d davon ab n≥4 nutzbar)"
          % (total, len(agg), usable))
    for key in ("English Premier League|p1", "Italian Serie A|p1", "Spanish La Liga|p1"):
        if key in agg:
            print("     %-30s Median €%s (n=%d)" % (key, format(agg[key]["med"], ","), agg[key]["n"]))


if __name__ == "__main__":
    main()
