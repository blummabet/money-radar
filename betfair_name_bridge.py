#!/usr/bin/env python3
"""
betfair_name_bridge.py — Betfair-Schreibweise ↔ unsere Fixture-Schreibweise. REIN, kein I/O.

25.08.2026 zuerst im Match-Page-Generator gebaut (die Event-Pages fanden „Real Betis" nicht,
weil Betwatch „Betis" schreibt). 26.08.2026 hier herausgezogen, weil Terminal-Kartenlink und
Kohärenz-Beobachter dieselbe Brücke brauchen — und eine zweite Kopie genau die Drift erzeugt,
die uns bei der Travel-Logik schon einmal eingefangen hat (ein Ort gefixt, einer nicht).

Die Brücke ist bewusst ENG. Ein falscher Treffer ist schlimmer als kein Treffer: er hängt einem
Spiel fremdes Geld an. Deshalb gilt überall „im Zweifel nichts".

Geldradar 19.10.2026: Kopie aus dem alten Projekt (betting-dashboard) für live_stats.py.
"""
from __future__ import annotations

from datetime import date as _date, timedelta as _td

# Wörter, die zu viele Vereine teilen — als Brücke wertlos. Vor allem STADT-Namen sind gefährlich:
# „Manchester United" und „Manchester City" würden sonst als dasselbe Team gelten, und der
# Paar-Test rettet das nicht, wenn beide am selben Tag gegen dieselbe Mannschaft spielen.
STOPWORDS = {"united", "sporting", "national", "internacional", "juniors", "wanderers",
             "rangers", "rovers", "albion", "county", "manchester", "london", "madrid",
             "milano", "milan", "roma", "torino", "sevilla", "bristol", "sheffield",
             "nottingham", "newcastle", "birmingham", "istanbul", "moskva", "beograd"}


def event_key(a, b) -> str:
    """Reihenfolge-unabhängiger Schlüssel für ein Paar. Fällt ohne poly_cross_sport zurück."""
    try:
        from poly_cross_sport import event_key as _ek
        return _ek(a, b)
    except Exception:
        return "-".join(sorted([(a or "").strip().lower(), (b or "").strip().lower()]))


def _norm(x) -> str:
    """klein, ohne Akzente, nur a-z0-9 (Geldradar-Kopie: ohne poly_cross_sport, Akzente zusätzlich weg — „Skövde"/„Skovde")."""
    import re
    import unicodedata
    t = unicodedata.normalize("NFKD", str(x or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", t.lower())


def compatible(a, b) -> bool:
    """Zwei Team-Schreibweisen, dasselbe Team?

    Enthaltensein reicht — aber erst ab 4 Zeichen, sonst würde „FC" auf alles passen.
    „Athletic Club" und „Athletic Bilbao" enthalten einander NICHT, teilen aber ein markantes
    Wort; dafür der Wort-Schnitt mit Mindestlänge 5. Die hält „Real"/„Real" (Madrid vs Sociedad)
    bewusst draußen — genau das wären die gefährlichen Fehltreffer.
    """
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na == nb or (len(na) >= 4 and len(nb) >= 4 and (na in nb or nb in na)):
        return True
    wa = {w for w in (_norm(x) for x in str(a).split()) if len(w) >= 5 and w not in STOPWORDS}
    wb = {w for w in (_norm(x) for x in str(b).split()) if len(w) >= 5 and w not in STOPWORDS}
    return bool(wa & wb)


def pair_matches(home, away, cand_home, cand_away) -> bool:
    """Beide Seiten kompatibel — in einer der beiden Richtungen (Heimrecht kann kippen)."""
    return ((compatible(home, cand_home) and compatible(away, cand_away))
            or (compatible(home, cand_away) and compatible(away, cand_home)))


def days_around(date) -> list:
    """Der Spieltag plus ±1 Tag — Anpfiff und unsere Datumsangabe können in der Zeitzone kippen.

    31.08.2026 oeffentlich gemacht: wer einen Index nach TAG baut (statt nur nach Team-Paar),
    braucht exakt dieselbe Tages-Logik wie die Bruecke hier. Zwei Kopien driften auseinander.
    """
    try:
        d0 = _date.fromisoformat(str(date)[:10])
        return [str(d0 + _td(days=k)) for k in (0, -1, 1)]
    except Exception:
        return [str(date)[:10]]


_days_around = days_around          # Rueckwaerts-Kompatibilitaet, gleiche Funktion


def find(snaps, fuzzy, home, away, date=None):
    """Betfair-Eintrag zum Spiel. Erst exakt über den event_key, dann die Namens-Brücke am
    selben Spieltag. Der Treffer muss EINDEUTIG sein — zwei Kandidaten heißt lieber keiner.

    snaps: {event_key: eintrag} · fuzzy: {"YYYY-MM-DD": [eintrag, ...]}
    """
    m = (snaps or {}).get(event_key(home, away))
    if m:
        return m
    if not fuzzy or not date:
        return None
    hits = []
    for day in _days_around(date):
        for cand in (fuzzy.get(day) or []):
            if pair_matches(home, away, cand.get("home"), cand.get("away")):
                hits.append(cand)
    return hits[0] if len(hits) == 1 else None


def index(entries, date_of=lambda e: str(e.get("kickoff") or "")[:10]):
    """Liste von Betfair-Einträgen → (snaps, fuzzy) für find(). REIN."""
    snaps, fuzzy = {}, {}
    for e in (entries or []):
        if not isinstance(e, dict):
            continue
        snaps[event_key(e.get("home"), e.get("away"))] = e
        fuzzy.setdefault(date_of(e), []).append(e)
    return snaps, fuzzy
