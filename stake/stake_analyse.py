#!/usr/bin/env python3
"""stake_analyse.py — der eine Bezugswert, den die Burst-Erkennung aus der Stake-Auswertung braucht.

25.10.2026 (Lucas: „neues Projekt sauber halten"): die Kopie aus dem alten Projekt (773 Zeilen) rechnete
Schubladen, Vorregistrierungen, Auffällige, Abdeckung und Bilanzen — gelesen wurde davon im Geldradar nur
`randliga.ebeneMedian` (stake_burst_push.py, „Kleine Liga"-Bursts: üblicher Einsatz je Spielklasse, wenn die
eigene Liga keine Norm hat). Genau das bleibt, im selben Format, damit stake_burst_push.py unverändert liest.

  liest     stake_bet_ledger.json
  schreibt  stake_auswertung.json — {"stand", "randliga": {"ebeneMedian": {Ebene: Median-Einsatz $}}}
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(os.environ.get("STAKE_DATEN") or Path(__file__).resolve().parent)  # Daten in .st/
sys.path.insert(0, str(BASE))

import stake_highroller_fetch as SH
import stake_liga_stufe as LS   # Spielklasse je Liga — eine Tabelle, ein Ort

LEDGER_FILE = BASE / "stake_bet_ledger.json"
OUT_FILE = BASE / "stake_auswertung.json"


def main() -> int:
    print("=== stake_analyse.py ===")
    wetten = [w for w in (SH._lade(LEDGER_FILE, {}).get("wetten") or []) if SH.sperr_grund(w) is None]
    if not wetten:
        print("  ℹ️  leeres Ledger — nichts auszuwerten.")
        return 0
    em = LS.ebene_median(wetten)
    SH._schreibe(OUT_FILE, {"stand": datetime.now(timezone.utc).isoformat(), "nWetten": len(wetten),
                            "randliga": {"ebeneMedian": em}})
    print("  Median-Einsatz je Spielklasse: %s (%d Wetten)" % (em, len(wetten)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
