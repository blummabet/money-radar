"""Die zwei Statistik-Helfer, die stake_analyse.py braucht.

25.10.2026 (Lucas: „neues Projekt sauber halten"): vorher lagen sie in freigabe.py (1.867 Zeilen) und sharp_gate.py
(284 Zeilen) aus dem alten Projekt, die sonst nur Dateien des alten Projekts lasen (Cards, Shortlist, Killer-Buch …)
und hier nie liefen. Die beiden Funktionen sind unverändert übernommen.
"""
from __future__ import annotations

import math

Z = 1.645          # einseitige 95%-Grenze
UG_MIN_N = 30      # unter so vielen Werten keine Untergrenze — drei ähnliche Werte sind kein Beleg


def untergrenze(werte, z: float = Z, min_n: int = None):
    """Einseitige 95%-Untergrenze des Mittelwerts (Normalapproximation), erst ab n ≥ 30."""
    n = len(werte)
    grenze = UG_MIN_N if min_n is None else min_n
    if n < max(3, grenze):
        return None
    m = sum(werte) / n
    var = sum((x - m) ** 2 for x in werte) / (n - 1)
    return m - z * math.sqrt(var) / math.sqrt(n)


def wilson_lb(wins, n, z: float = Z) -> float:
    """Untere Wilson-Grenze der Trefferquote — zieht sich bei kleinem n zusammen (5/9 ≠ 500/900)."""
    n = int(n or 0)
    if n <= 0:
        return 0.0
    p = (wins or 0) / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return centre - margin
