"""Speicherformat für den Betfair-Ledger (betfair_track_results.json).

01.09.2026 (Lucas: „kann es sein dass da schon ewig 8000 steht"). Es stand ewig 8000 da, weil
RESULTS_KEEP=8000 den Ledger deckelte — und weil ~1.300 Signale pro Tag abgerechnet werden, hielt
der Ledger damit exakt SECHS TAGE. Jeder Liga×Markt-Bucket kam deshalb nie über n≈24 hinaus, und
das Lern-Board (ab n=15 dreht es Card-Signale um) entschied dauerhaft auf einer Wochenstichprobe.

Der Deckel muss also hoch. Nur: die Datei wird bei jedem Betfair-Lauf committet (Cron seit dem
17.09.2026 `*/15`, gemessen 15,0 Min — vorher stand hier „alle 10 Minuten"), und .git steht bereits
bei ~1 GB. Im alten Format (Liste von Dicts, 392 B/Zeile) wären 40.000 Zeilen 15,7 MB pro Commit —
das erschlägt das Repo schneller, als die Historie nützt.

Darum dieses Format: Spaltenform mit internierten Wörterbüchern. Liga, Markt, Team, Land, via, fav
und dir wiederholen sich tausendfach und stehen künftig einmal im Kopf; die Zeile trägt nur den
Index. settledAt wird als Sekunden-Offset zu einer Basis abgelegt statt als 32-Zeichen-ISO.

    gemessen an den echten 8.000 Zeilen:  392 B/Zeile  →  108 B/Zeile
    40.000 Zeilen:  15,7 MB (alt)  →  4,3 MB (neu)

VERLUST: genau zwei, beide bewusst und beide gemessen (tests/test_betfair_track_store.py prüft
den Round-Trip gegen den echten 8.000-Zeilen-Ledger):
  1. die Mikrosekunden in settledAt — Sekundenauflösung bleibt;
  2. ein Feld, das ausdrücklich auf None steht, kommt als FEHLENDER Schlüssel zurück (im echten
     Ledger betrifft das pinnClose/pinnFair/clvPinn in 7.886 von 8.000 Zeilen). .get() liefert
     beide Male None, und am 01.09.2026 wurde geprüft: kein Leser des Ledgers fragt je nach
     Schlüsselpräsenz („x in zeile"), alle gehen über .get(). Wer das ändert, muss hier nachsehen.
Alles Übrige ist round-trip-identisch — Wert wie Typ.

Warum nicht einfach Felder weglassen (der erste Plan)? Weil es nichts bringt: ft/ht/via/resChk/
country zusammen sind 17% der Datei, nicht die Hälfte — und home/away trägt byTeamMarket, ft/ht
trägt sowohl das Korrekturfenster in betfair_track_record.py als auch _track_index() in
betfair_public_eval.py. Wegwerfen hätte also wenig gespart und zwei Auswertungen beschädigt.

UNBEKANNTE FELDER: was hier nicht als Spalte steht, geht NICHT verloren — es landet in der
rest-Spalte am Zeilenende. Wer morgen ein Feld an die Zeile hängt, verliert es nicht still; es wird
nur weniger kompakt gespeichert, bis es hier als Spalte nachgetragen wird. Dasselbe gilt für einen
Wert, der nicht in seine Spalte passt (etwa conc=1 statt conc=True): er wandert in rest, statt beim
Lesen als etwas anderes zurückzukommen. Fehlende Information ist keine Erlaubnis — auch nicht beim
Komprimieren.

⚠️ RÜCKROLLEN IST NICHT SYMMETRISCH. Ein Revert auf den Stand vor dem 01.09. liest die neue
Datei als Dict, faellt in `if not isinstance(results, list): results = []` — und WISCHT den Ledger
beim naechsten Schreiben. Wer zurueckrollen will, sichert vorher betfair_track_results.json oder
entpackt sie einmal von Hand (json.dump(entpacken(json.load(...)))). Vorwaerts ist nahtlos,
rueckwaerts nicht.

ALTFORMAT: load()/entpacken() nimmt weiterhin die blanke Liste entgegen. Der erste Lauf nach dem
Deployment liest also die alten 8.000 Zeilen und schreibt sie im neuen Format zurück. Es gibt keine
Migration und keinen Stichtag.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

FORMAT = 1
BASIS = 1750000000          # Sekunden-Epoch, von dem settledAt abgezogen wird (≈ 15.06.2025)

# (Feldname, Art, Wörterbuch-Schlüssel). Reihenfolge = Spaltenreihenfolge, darf nur ANGEHÄNGT
# werden — nie umsortiert, nie gelöscht, sonst liest ein alter Ledger falsch.
SPALTEN = (
    ("league",    "dict", "l"),
    ("market",    "dict", "m"),
    ("home",      "dict", "t"),
    ("away",      "dict", "t"),   # Heim und Gast teilen sich ein Wörterbuch
    ("country",   "dict", "c"),
    ("via",       "dict", "v"),
    ("fav",       "dict", "f"),
    ("dir",       "dict", "d"),
    ("odd",       "roh",  None),
    ("entryOdd",  "roh",  None),
    ("pinnClose", "roh",  None),
    ("pinnFair",  "roh",  None),
    ("clvBf",     "roh",  None),
    ("clvPinn",   "roh",  None),
    ("conc",      "bool", None),
    ("inflow",    "bool", None),
    ("win",       "bool", None),
    ("resChk",    "bool", None),
    ("settledAt", "zeit", None),
    ("matchId",   "roh",  None),
    ("ft",        "roh",  None),
    ("ht",        "roh",  None),
    # 06.09.2026 (Lucas: „das ins Positive umkehren und faden"). Der ECHTE Gegenpreis der
    # Zwei-Weg-Maerkte samt gematchtem Volumen. Ohne ihn liess sich der Fade nur aus einem
    # angenommenen Overround rekonstruieren — s. betfair_track_record.gegenseite().
    # ANGEHAENGT, nie dazwischen: alte Zeilen sind kuerzer und lesen weiterhin korrekt.
    ("gegenOdd",  "roh",  None),
    ("gegenVol",  "roh",  None),
    ("entryGegenOdd", "roh", None),
    # 19.09.2026 (Lucas: „wir haben noch nicht die optimale Einstellung … das muessten wir
    # rueckrechnen"). Die ZAHLEN hinter conc/inflow. Bis heute stand im Buch nur das Urteil
    # (share >= 0,65 ja/nein, Zufluss >= 2.000 EUR ja/nein) — eine Schwelle, deren Zahl man
    # nicht kennt, laesst sich nicht verschieben. Siehe betfair_track_record.capture.
    # share als GANZE PROZENT, nicht als Bruch: 0,837 kostet 5 Zeichen, 84 kostet 2, und die
    # Frage lautet „0,80 oder 0,85", nicht „0,837 oder 0,841".
    # ANGEHAENGT, nie dazwischen: alte Zeilen sind kuerzer und lesen weiterhin korrekt.
    ("sharePct",       "roh", None),
    ("maxSharePct",    "roh", None),
    ("maxShareMinVor", "roh", None),
    ("mktVol",         "roh", None),
    ("entryMktVol",    "roh", None),
    ("zuflussMax",     "roh", None),
    ("laeufe",         "roh", None),
)
_NAMEN = frozenset(f for f, _, _ in SPALTEN)


def _zeit_zu_int(s):
    """ISO-Zeitstempel → Sekunden-Offset zur Basis. None, wenn nicht verlustarm darstellbar."""
    if not isinstance(s, str) or not s:
        return None
    try:
        t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return int(t.timestamp()) - BASIS


def _int_zu_zeit(n):
    if not isinstance(n, (int, float)):
        return None
    return datetime.fromtimestamp(int(n) + BASIS, tz=timezone.utc).isoformat()


def packen(zeilen):
    """Liste von Zeilen-Dicts → kompaktes Spaltenformat. REIN."""
    woerter, index = {}, {}

    def idx(schluessel, wert):
        if wert is None:
            return None
        liste = woerter.setdefault(schluessel, [])
        karte = index.setdefault(schluessel, {})
        if wert not in karte:
            karte[wert] = len(liste)
            liste.append(wert)
        return karte[wert]

    raus = []
    for z in (zeilen or []):
        if not isinstance(z, dict):
            continue
        rest = {k: v for k, v in z.items() if k not in _NAMEN}
        spalten = []
        for feld, art, schluessel in SPALTEN:
            if feld not in z:
                spalten.append(None)
                continue
            wert = z[feld]
            if wert is None:
                spalten.append(None)
            elif art == "dict":
                if isinstance(wert, str):
                    spalten.append(idx(schluessel, wert))
                else:
                    spalten.append(None); rest[feld] = wert       # passt nicht → verlustfrei in rest
            elif art == "bool":
                if isinstance(wert, bool):
                    spalten.append(1 if wert else 0)
                else:
                    spalten.append(None); rest[feld] = wert
            elif art == "zeit":
                n = _zeit_zu_int(wert)
                if n is None:
                    spalten.append(None); rest[feld] = wert
                else:
                    spalten.append(n)
            else:
                spalten.append(wert)
        spalten.append(rest or None)
        raus.append(spalten)
    return {"fmt": FORMAT, "basis": BASIS, "woerter": woerter, "zeilen": raus}


def entpacken(daten):
    """Spaltenformat ODER Altformat (blanke Liste) → Liste von Zeilen-Dicts. REIN.

    Unlesbares gibt [] zurück statt zu werfen — der Aufrufer soll nicht abstürzen, aber der
    Wächter check_betfair_ledger meldet die leere Datei, damit sie nicht still durchgeht."""
    if isinstance(daten, list):                       # Altformat
        return [z for z in daten if isinstance(z, dict)]
    if not isinstance(daten, dict) or not isinstance(daten.get("zeilen"), list):
        return []
    woerter = daten.get("woerter") if isinstance(daten.get("woerter"), dict) else {}
    basis = daten.get("basis")
    basis = basis if isinstance(basis, (int, float)) else BASIS

    def wort(schluessel, i):
        liste = woerter.get(schluessel)
        if not isinstance(liste, list) or not isinstance(i, int) or not (0 <= i < len(liste)):
            return None
        return liste[i]

    raus = []
    for sp in daten["zeilen"]:
        if not isinstance(sp, list):
            continue
        z = {}
        for pos, (feld, art, schluessel) in enumerate(SPALTEN):
            wert = sp[pos] if pos < len(sp) else None
            if wert is None:
                continue
            if art == "dict":
                w = wort(schluessel, wert)
                if w is not None:
                    z[feld] = w
            elif art == "bool":
                z[feld] = bool(wert)
            elif art == "zeit":
                t = _int_zu_zeit(int(wert) + basis - BASIS) if isinstance(wert, (int, float)) else None
                if t is not None:
                    z[feld] = t
            else:
                z[feld] = wert
        rest = sp[len(SPALTEN)] if len(sp) > len(SPALTEN) else None
        if isinstance(rest, dict):
            z.update(rest)
        raus.append(z)
    return raus


def load(pfad):
    """Ledger von Platte lesen — beide Formate. Fehlt die Datei oder ist sie kaputt: []."""
    try:
        return entpacken(json.loads(Path(pfad).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return []


def dump(pfad, zeilen, schreiber=None):
    """Ledger schreiben. schreiber(pfad, daten) injizierbar (Default: safe_write, atomar)."""
    daten = packen(zeilen)
    if schreiber is None:
        from safe_write import write_json_atomic
        write_json_atomic(Path(pfad), daten, indent=None)
    else:
        schreiber(Path(pfad), daten)
    return daten


def fenster(zeilen):
    """{n, von, bis, tage} über settledAt — für Wächter und UI. Keine Wertung. REIN."""
    ts = sorted(z["settledAt"] for z in (zeilen or [])
                if isinstance(z, dict) and isinstance(z.get("settledAt"), str))
    if not ts:
        return {"n": len(zeilen or []), "von": None, "bis": None, "tage": None}
    a, b = _zeit_zu_int(ts[0]), _zeit_zu_int(ts[-1])
    tage = round((b - a) / 86400.0, 1) if (a is not None and b is not None) else None
    return {"n": len(zeilen), "von": ts[0], "bis": ts[-1], "tage": tage}
