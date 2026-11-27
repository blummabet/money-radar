#!/usr/bin/env python3
"""
abgesagt.py — abgesagte / verlegte Spiele erkennen. 27.11.2026.

Lucas (VVV Venlo – RKC, Jong Utrecht – Almere, Jong AZ – Oss, alle Eerste Divisie, 3½ h vor Anpfiff): „die Spiele
sind abgesagt, und dann hast du immer Irrsinnssummen auf Polymarket — wissen wir irgendwoher, dass das abgesagt ist?"
Polymarket löst ein abgesagtes Spiel auf „Remis" bzw. 50/50 auf; sobald es feststeht, kaufen Profis die sichere Seite
zu 98–99 ¢ — daher die Riesen-Beträge. Betfair zeigt das nicht (Markt bleibt offen, wird später annulliert).

Zwei Quellen, jede reicht:
* API-Football: /fixtures?date=…&status=PST-CANC-ABD-SUSP — höchstens alle 30 Min, 2 Abrufe (heute, morgen UTC).
* Polymarket-Preis: vor Anpfiff (≥ 20 Min) steht eine Seite bei ≥ 95 ¢ — das gibt es bei einem offenen Spiel nicht.
Ergebnis: d["abgesagt"] = {id: {"grund", "quelle"}} und s["abgesagt"] an Radar-Spiel und Topf. Pushes und Public
lassen diese Spiele aus, die Übersicht stellt sie nach unten. REIN bis auf lauf().
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASIS = Path(__file__).resolve().parent
CACHE = BASIS / ".bf" / "abgesagt.json"
STATUS = {"PST": "verlegt", "CANC": "abgesagt", "ABD": "abgebrochen", "SUSP": "unterbrochen", "AWD": "am grünen Tisch", "WO": "kampflos"}
PREIS_MIN = 0.95
VORLAUF_MIN = 20


def _zeit(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None


def aus_poly(toepfe, jetzt):
    """{id: grund} — vor Anpfiff steht eine Polymarket-Seite bei ≥ 95 ¢."""
    aus = {}
    for t in toepfe or []:
        ko = _zeit(t.get("anpfiff"))
        if t.get("live") or not ko or ko - jetzt < timedelta(minutes=VORLAUF_MIN):
            continue
        pr = ((t.get("q") or {}).get("poly") or {}).get("preise") or {}
        k, p = max(((k, v) for k, v in pr.items() if isinstance(v, (int, float))), key=lambda kv: kv[1], default=(None, 0))
        if p >= PREIS_MIN:
            name = {"heim": t.get("heim"), "gast": t.get("gast"), "remis": "Remis"}.get(k, k)
            aus[str(t.get("id"))] = {"quelle": "Polymarket", "grund": f"Polymarket {round(p * 100)} ¢ auf {name} vor Anpfiff — wohl abgesagt"}
    return aus


def aus_apif(spiele, fixtures, zuordnen):
    """{id: grund} aus API-Football-Status (PST/CANC/…)."""
    aus = {}
    for s in spiele or []:
        if s.get("live"):
            continue
        fx = zuordnen(s, fixtures)
        st = (((fx or {}).get("fixture") or {}).get("status") or {}).get("short")
        if st in STATUS:
            aus[str(s.get("id"))] = {"quelle": "API-Football", "grund": f"laut API-Football {STATUS[st]}", "status": st}
    return aus


def lauf(d, get, zuordnen, jetzt=None):
    """Setzt d["abgesagt"] und markiert Radar-Spiele/Töpfe. Gibt die Zahl der API-Abrufe zurück."""
    jetzt = jetzt or datetime.now(timezone.utc)
    abrufe = 0
    try:
        c = json.loads(CACHE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        c = {}
    fx = c.get("fixtures") or []
    if get and jetzt - (_zeit(c.get("ts")) or jetzt - timedelta(days=1)) >= timedelta(minutes=30):
        fx = []
        for tag in (jetzt.date(), (jetzt + timedelta(days=1)).date()):
            r = (get(f"/fixtures?date={tag.isoformat()}&status=PST-CANC-ABD-SUSP-AWD-WO", timeout=20) or {}).get("response") or []
            abrufe += 1
            fx += r
        c = {"ts": jetzt.isoformat(), "fixtures": fx}
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")
    spiele = (d.get("radar") or {}).get("spiele") or []
    ab = {**aus_poly(d.get("toepfe"), jetzt), **aus_apif(spiele, fx, zuordnen)}
    d["abgesagt"] = ab
    for s in spiele:
        if str(s.get("id")) in ab:
            s["abgesagt"] = ab[str(s.get("id"))]["grund"]
    for t in d.get("toepfe") or []:
        if str(t.get("id")) in ab:
            t["abgesagt"] = ab[str(t.get("id"))]["grund"]
    return abrufe


def poly_keys(d):
    """Polymarket-Schlüssel (Slug-Anfang) der abgesagten Spiele — Wal-Belege tragen den Markt-Slug als id."""
    ab = d.get("abgesagt") or {}
    return {((t.get("q") or {}).get("poly") or {}).get("key") for t in d.get("toepfe") or [] if str(t.get("id")) in ab} - {None, ""}


def betroffen(d, beleg) -> bool:
    """Gehört dieser Push-Beleg zu einem abgesagten Spiel?"""
    ab = d.get("abgesagt") or {}
    if not ab or not beleg:
        return False
    i = str(beleg.get("id") or "")
    if i in ab:
        return True
    return any(k and i.startswith(k) for k in poly_keys(d))
