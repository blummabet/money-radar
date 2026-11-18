"""„Früh bewegt" — Pinnacle zieht 1–7 Tage vor Anpfiff (Regel FB1, Lucas 18.11.2026: „kannst du bauen, wenn es uns
eventuell was bringt"; Anlass 11.11.: ein Betfair-Tippster holt seinen CLV Tage vor Anpfiff).

Vorab festgelegt (18.11.2026, 10:20 Wien) — so und nicht anders:
* Früh bewegt = die Pinnacle-Wahrscheinlichkeit (ohne Marge) einer Auswahl im Sieg-Markt liegt um ≥ 3 pp ÜBER dem
  ersten Stand, gemessen mindestens 24 h vor Anpfiff, und das an zwei 3-h-Ständen in Folge. Je Spiel zählt die erste
  Erkennung (die Auswahl mit dem größten Anstieg in dem Moment).
* Gemessen wird still (kein Push): CLV = Pinnacle-Quote beim Erkennen ÷ Pinnacle-Schlussquote (letzter Stand vor
  Anpfiff, aus .pin/frueh_archiv.json) − 1. Dazu: wie viele pp die Auswahl bis zum Schluss weiter zog oder zurückging,
  und ob Betfair beim Erkennen schon mitgezogen hatte.
* Urteil nach 150 abgerechneten Fällen: trägt, wenn der mittlere CLV über +1 % liegt UND mehr als die Hälfte positiv ist.
Bis dahin steht der Bereich auf der Pinnacle-Seite als „Beobachtung".
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

MIN_PP = 3.0
VOR_H = 24
BUCH_TAGE = 90
URTEIL_N = 150
NAMEN = ("heim", "remis", "gast")


def _ts(s):
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def _lesen(p, leer):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return leer


def erkennen(e, jetzt):
    """→ (Auswahl-Index, Erkennungs-Punkt) oder None. REIN. e = Eintrag aus pinnacle_alle.json."""
    ko, erst = _ts(e.get("anpfiff")), e.get("erst")
    if not ko or not erst or len(erst) < 7:
        return None
    pts = sorted([p for p in e.get("frueh") or [] if isinstance(p, list) and len(p) >= 7 and _ts(p[0])
                  and ko - _ts(p[0]) >= timedelta(hours=VOR_H) and p[0] > erst[0]], key=lambda p: p[0])
    for a, b in zip(pts, pts[1:]):
        best = None
        for i in range(3):
            da, db = 100 * (a[1 + i] - erst[1 + i]), 100 * (b[1 + i] - erst[1 + i])
            if da >= MIN_PP and db >= MIN_PP and (best is None or db > best[1]):
                best = (i, db)
        if best:
            return best[0], b
    return None


def _bf_pp(e, i, bis=None):
    """Betfair-Wahrscheinlichkeit der Auswahl i: Veränderung erster Stand → Stand bis `bis` (pp). REIN."""
    bf = sorted([b for b in e.get("bf") or [] if isinstance(b, list) and len(b) >= 4], key=lambda b: b[0])
    if bis:
        bf = [b for b in bf if b[0] <= bis]
    if len(bf) < 2:
        return None

    def p(b):
        q = [1 / x for x in b[1:4] if isinstance(x, (int, float)) and x > 1]
        return q[i] / sum(q) if len(q) == 3 else None
    a, z = p(bf[0]), p(bf[-1])
    return round(100 * (z - a), 1) if a is not None and z is not None else None


def lauf(pin_ordner: Path, jetzt=None):
    jetzt = jetzt or datetime.now(timezone.utc)
    alle = (_lesen(pin_ordner / "pinnacle_alle.json", {}) or {}).get("spiele") or {}
    archiv = _lesen(pin_ordner / "frueh_archiv.json", {})
    buch_p = pin_ordner / "fb1_buch.json"
    buch = _lesen(buch_p, {})
    liste = []
    for sid, e in alle.items():
        ko = _ts(e.get("anpfiff"))
        if not ko or ko <= jetzt:
            continue
        tr = erkennen(e, jetzt)
        if not tr:
            continue
        i, punkt = tr
        erst, akt = e["erst"], e.get("jetzt") or punkt
        if sid not in buch:
            buch[sid] = {"ts": punkt[0], "i": i, "seite": [e.get("heim"), "Remis", e.get("gast")][i], "q": punkt[4 + i],
                         "p": punkt[1 + i], "pErst": erst[1 + i], "qErst": erst[4 + i], "liga": e.get("liga"),
                         "heim": e.get("heim"), "gast": e.get("gast"), "anpfiff": e.get("anpfiff"),
                         "bfMit": _bf_pp(e, i, punkt[0])}
        b = buch[sid]
        i = b["i"]
        verlauf = sorted({p[0]: p for p in (e.get("frueh") or []) + (e.get("grob") or [])}.values(), key=lambda p: p[0])
        liste.append({"sid": sid, "liga": e.get("liga"), "heim": e.get("heim"), "gast": e.get("gast"), "anpfiff": e.get("anpfiff"),
                      "seite": b["seite"], "i": i, "qErst": b["qErst"], "qErkannt": b["q"], "qJetzt": akt[4 + i],
                      "ppErkannt": round(100 * (b["p"] - b["pErst"]), 1), "ppJetzt": round(100 * (akt[1 + i] - b["pErst"]), 1),
                      "weiter": round(100 * (akt[1 + i] - b["p"]), 1), "erkannt": b["ts"], "seit": erst[0],
                      "bfMit": b.get("bfMit"), "bfJetzt": _bf_pp(e, i),
                      "verlauf": [[p[0][:16], round(100 * p[1 + i], 1)] for p in verlauf][-48:]})
    # abrechnen: Schlussquote aus dem Früh-Archiv
    for sid, b in buch.items():
        a = archiv.get(sid)
        if b.get("clv") is not None or not a or not a.get("schluss"):
            continue
        s = a["schluss"]
        if isinstance(s[4 + b["i"]], (int, float)) and s[4 + b["i"]] > 1:
            b["schlussQ"] = s[4 + b["i"]]
            b["clv"] = round(100 * (b["q"] / s[4 + b["i"]] - 1), 2)
            b["weiterPp"] = round(100 * (s[1 + b["i"]] - b["p"]), 1)
    grenze = (jetzt - timedelta(days=BUCH_TAGE)).isoformat()
    buch = {k: v for k, v in buch.items() if str(v.get("anpfiff")) >= grenze}
    buch_p.write_text(json.dumps(buch, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    ab = [b for b in buch.values() if b.get("clv") is not None]
    n = len(ab)
    mess = {"regel": "FB1", "erkannt": len(buch), "abgerechnet": n, "ziel": URTEIL_N}
    if n:
        clv = [b["clv"] for b in ab]
        mess.update(clvMittel=round(sum(clv) / n, 2), anteilPositiv=round(sum(c > 0 for c in clv) / n, 3),
                    weiterAnteil=round(sum((b.get("weiterPp") or 0) > 0 for b in ab) / n, 3),
                    zurueckAnteil=round(sum((b.get("weiterPp") or 0) < 0 for b in ab) / n, 3),
                    bfMitClv=_mittel([b["clv"] for b in ab if (b.get("bfMit") or 0) >= 1]),
                    bfOhneClv=_mittel([b["clv"] for b in ab if (b.get("bfMit") or 0) < 1]))
        if n >= URTEIL_N:
            mess["urteil"] = "trägt" if mess["clvMittel"] > 1 and mess["anteilPositiv"] > 0.5 else "trägt nicht"
    liste.sort(key=lambda x: (-x["ppJetzt"]))
    return {"liste": liste[:40], "n": len(liste), "mess": mess}


def _mittel(xs):
    return {"n": len(xs), "clv": round(sum(xs) / len(xs), 2)} if xs else None
