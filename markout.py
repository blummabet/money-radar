"""Live-CLV (17.11.2026, Lucas: „Punkt 3 machen" — CLV für Betfair live, HZ und Stake-Wal).

Vor Anpfiff gibt es eine Schlussquote, live nicht. Live messen wir darum, wohin der Markt nach dem Signal geht:

* **Markout** = Quote beim Signal ÷ Quote derselben Auswahl ~10 Min später − 1, nur wenn in der Zeit kein Tor fiel.
* Live laufen Quoten auch ohne Tor von selbst (Zeit vergeht: Unter wird kürzer, Über und Außenseiter länger).
  Darum zieht die Messung die **Grunddrift** ab: derselbe Markout über ALLE Live-Auswahlen gleicher Art (Markt, Seite,
  Quotenbereich, Spielphase), ebenfalls ohne Tor. Übrig bleibt, was das Signal mehr wusste als die Uhr.
* **HZ 0:0**: Das Signal kommt in der Pause. Die Schlussquote ist die letzte Quote vor Wiederanpfiff — in der Pause
  tickt keine Uhr, das ist eine echte Schlussquote.
* **Stake-Wal**: die Seite des Wals bei Betfair (Sieg-Markt oder Torlinie) beim Push, dann Schlussquote vor Anpfiff bzw.
  Markout live wie oben.

Alles still in .push_state (live_drift.json, hz_markout.json, stake_markout.json). Ausgewertet in build.py → Statistik.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

TAGE = 14                 # Grunddrift über die letzten 14 Tage
FENSTER = (8, 13)         # „~10 Min später" = 8–13 Min (Lauf alle 5 Min)


def _ts(s):
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def _lesen(p, leer):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return leer


def _schreiben(p, x):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(x, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def eimer(markt, auswahl, q, minute):
    """Art der Auswahl für die Grunddrift. REIN."""
    m, a = str(markt or ""), str(auswahl or "").lower()
    if m == "Match Odds":
        fam = "remis" if a in ("the draw", "draw") else "team"
    elif m.startswith("Over/Under"):
        fam = ("over" if a.startswith("over") else "under") + (re.search(r"\d+\.5", m) or [""])[0]
    elif m.startswith("First Half Goals"):
        fam = "hz" + ("over" if a.startswith("over") else "under")
    elif m in ("Both teams to Score?",):
        fam = "btts" + a
    else:
        return None
    qb = "a" if q < 1.5 else "b" if q < 2.2 else "c" if q < 3.5 else "d" if q < 7 else "e"
    mn = minute if isinstance(minute, (int, float)) else 45
    mb = "1" if mn < 30 else "2" if mn < 46 else "3" if mn < 70 else "4"
    return f"{fam}|{qb}|{mb}"


def _live_auswahlen(d):
    for s in (d.get("radar") or {}).get("spiele") or []:
        lv = s.get("live") or {}
        if not s.get("live") or not isinstance(lv, dict) or not lv.get("stand"):
            continue
        for m in s.get("maerkteDetail") or []:
            for a in m.get("auswahl") or []:
                q = a.get("quote")
                if a.get("name") == "Rest" or not isinstance(q, (int, float)) or not 1.01 < q < 50:
                    continue
                yield str(s.get("id")), m.get("name"), a.get("name"), q, list(lv["stand"]), lv.get("min"), bool(lv.get("ht"))


def drift(d, pfad, jetzt):
    """Schnappschuss aller Live-Quoten + Grunddrift aus dem Vergleich mit dem Stand vor ~10 Min."""
    st = _lesen(pfad, {"snaps": [], "tage": {}})
    jetzt_s = jetzt.isoformat()
    neu = {f"{i}|{m}|{a}": [q, s, mn] for i, m, a, q, s, mn, ht in _live_auswahlen(d)}
    tag = jetzt.strftime("%Y-%m-%d")
    T = st["tage"].setdefault(tag, {})
    alt = next((x for x in st["snaps"] if FENSTER[0] <= (jetzt - _ts(x[0])).total_seconds() / 60 <= FENSTER[1]), None)
    if alt:
        for k, (q0, s0, mn0) in alt[1].items():
            x = neu.get(k)
            if not x or x[1] != s0:
                continue
            i, m, a = k.split("|", 2)
            e = eimer(m, a, q0, mn0)
            if not e:
                continue
            v = T.setdefault(e, [0.0, 0])
            v[0] = round(v[0] + (q0 / x[0] - 1), 5)
            v[1] += 1
    st["snaps"] = [x for x in st["snaps"] if (jetzt - _ts(x[0])).total_seconds() < 20 * 60] + [[jetzt_s, neu]]
    grenze = (jetzt - timedelta(days=TAGE)).strftime("%Y-%m-%d")
    st["tage"] = {k: v for k, v in st["tage"].items() if k >= grenze}
    _schreiben(pfad, st)


def grunddrift(st):
    """{eimer: Ø Markout} über alle gespeicherten Tage, nur Eimer mit ≥ 30 Werten. REIN."""
    sum_ = {}
    for v in (st or {}).get("tage", {}).values():
        for e, (s, n) in v.items():
            x = sum_.setdefault(e, [0.0, 0])
            x[0] += s
            x[1] += n
    return {e: s / n for e, (s, n) in sum_.items() if n >= 30}


def bereinigt(q0, nach, stand0, min0, markt, auswahl, gd):
    """Markout minus Grunddrift in %, None wenn nicht messbar (Tor dazwischen, keine Grunddrift). REIN."""
    if not (isinstance(q0, (int, float)) and q0 > 1 and isinstance(nach, list) and len(nach) >= 2):
        return None
    q1, s1 = nach[0], nach[1]
    if not isinstance(q1, (int, float)) or q1 <= 1 or not stand0 or list(s1 or []) != list(stand0):
        return None
    e = eimer(markt, auswahl, q0, min0)
    if not e or e not in gd:
        return None
    return round(100 * ((q0 / q1 - 1) - gd[e]), 1)


def _spiel(radar, heim, gast):
    h, g = str(heim or "").lower()[:5], str(gast or "").lower()[:5]
    for s in radar:
        sh, sg = str(s.get("heim") or "").lower(), str(s.get("gast") or "").lower()
        if h and g and sh[:5] == h and sg[:5] == g:
            return s
    for s in radar:
        sh, sg = str(s.get("heim") or "").lower(), str(s.get("gast") or "").lower()
        if h and g and h in sh and g in sg:
            return s
    return None


def _quote(s, markt, auswahl):
    m = next((m for m in s.get("maerkteDetail") or [] if m.get("name") == markt), None)
    q = next((a.get("quote") for a in (m or {}).get("auswahl") or [] if a.get("name") == auswahl), None)
    if q is None and markt == "Match Odds":
        k = "heim" if auswahl == s.get("heim") else "gast" if auswahl == s.get("gast") else "remis" if auswahl == "The Draw" else None
        q = (s.get("quoten") or {}).get(k) if k else None
    return q if isinstance(q, (int, float)) and q > 1 else None


def hz(d, finder, pfad, jetzt):
    """HZ-0:0-Einträge: Quote beim Buchen und die letzte Quote vor Wiederanpfiff (Über 0,5 in der 2. HZ, Heimsieg)."""
    st = _lesen(pfad, {})
    radar = {str(s.get("id")): s for s in (d.get("radar") or {}).get("spiele") or []}
    for e in (finder or {}).get("eintraege") or []:
        t0 = _ts(e.get("gebuchtAt"))
        if not t0 or (jetzt - t0) > timedelta(minutes=40):
            continue
        k = e.get("k") or f"hz:{e.get('matchId')}"
        x = st.setdefault(k, {"t": e.get("gebuchtAt"), "q0": {"over05": (e.get("quoten") or {}).get("over05"),
                                                           "heim": (e.get("quoten") or {}).get("heim")}})
        s = radar.get(str(e.get("matchId")))
        lv = (s or {}).get("live") or {}
        if not s or lv.get("stand") != [0, 0]:
            continue
        mn = lv.get("min")
        if not (lv.get("ht") or (isinstance(mn, (int, float)) and mn <= 46)):
            continue                                 # zweite Halbzeit läuft schon → letzte Pausenquote bleibt stehen
        qo = _quote(s, "Over/Under 0.5 Goals", "Over 0.5 Goals")
        qh = _quote(s, "Match Odds", s.get("heim"))
        if qo:
            x["over05"] = qo
        if qh:
            x["heim"] = qh
        x["ts"] = jetzt.isoformat()
    grenze = (jetzt - timedelta(days=45)).isoformat()
    _schreiben(pfad, {k: v for k, v in st.items() if str(v.get("t") or "") >= grenze})


def _stake_seite(s, auswahl):
    """Stake-Auswahl → (Betfair-Markt, Betfair-Auswahl). Team, Remis oder Torlinie."""
    a = str(auswahl or "").strip()
    al = a.lower()
    if al in ("draw", "remis", "x", "unentschieden"):
        return "Match Odds", "The Draw"
    m = re.match(r"^(over|under)\b\s*([\d.,/ ]*)", al)
    if m:
        linien = [float(x.replace(",", ".")) for x in re.findall(r"\d+(?:[.,]\d+)?", m.group(2))]
        halb = sorted(x for x in linien if abs(x % 1 - 0.5) < 1e-9)
        if not halb:
            return None
        l = halb[0] if m.group(1) == "over" else halb[-1]
        return f"Over/Under {l:.1f} Goals", f"{m.group(1).capitalize()} {l:.1f} Goals"
    h, g = str(s.get("heim") or ""), str(s.get("gast") or "")
    if h[:5].lower() and h[:5].lower() in al:
        return "Match Odds", h
    if g[:5].lower() and g[:5].lower() in al:
        return "Match Odds", g
    return None


def stake(d, pfad, jetzt):
    """Stake-Wal-Pushes: Betfair-Quote der Wal-Seite beim Push, dann Schlussquote vor Anpfiff bzw. ~10 Min später live."""
    st = _lesen(pfad, {})
    radar = (d.get("radar") or {}).get("spiele") or []
    by_id = {str(s.get("id")): s for s in radar}
    for z in (d.get("stake") or {}).get("heute") or []:
        t0 = _ts(z.get("zeit"))
        if not t0 or z.get("kat") not in (None, "Fußball"):
            continue
        k = f"{str(z.get('zeit'))[:16]}|{z.get('spiel')}"
        if k in st or (jetzt - t0) > timedelta(minutes=15):
            continue
        teile = re.split(r"\s+-\s+", str(z.get("spiel") or ""), maxsplit=1)
        if len(teile) != 2:
            continue
        s = _spiel(radar, teile[0], teile[1])
        ms = _stake_seite(s, z.get("auswahl")) if s else None
        q = _quote(s, *ms) if ms else None
        if not q:
            continue
        lv = s.get("live") or {}
        st[k] = {"t": z.get("zeit"), "id": str(s.get("id")), "markt": ms[0], "auswahl": ms[1], "q0": q,
                 "live": bool(s.get("live")), "stand": lv.get("stand"), "min": lv.get("min"), "anpfiff": s.get("anpfiff")}
    for k, x in st.items():
        s = by_id.get(x.get("id"))
        t0 = _ts(x.get("t"))
        if not s or not t0:
            continue
        if not x.get("live"):
            ko = _ts(x.get("anpfiff"))
            if ko and jetzt < ko:
                q = _quote(s, x["markt"], x["auswahl"])
                if q:
                    x["schluss"], x["schlussTs"] = q, jetzt.isoformat()
        elif "nach10" not in x and FENSTER[0] <= (jetzt - t0).total_seconds() / 60 <= FENSTER[1] + 7:
            q = _quote(s, x["markt"], x["auswahl"])
            lv = s.get("live") or {}
            if q:
                x["nach10"] = [q, lv.get("stand"), lv.get("min")]
    grenze = (jetzt - timedelta(days=45)).isoformat()
    _schreiben(pfad, {k: v for k, v in st.items() if str(v.get("t") or "") >= grenze})


def clv_vor(q0, schluss, schluss_ts, anpfiff):
    """CLV vor Anpfiff in %, nur wenn die Schlussquote höchstens 20 Min vor Anpfiff stand. REIN."""
    t, ko = _ts(schluss_ts), _ts(anpfiff)
    if not (isinstance(q0, (int, float)) and isinstance(schluss, (int, float)) and schluss > 1 and t and ko):
        return None
    if t < ko - timedelta(minutes=20):
        return None
    return round(100 * (q0 / schluss - 1), 1)
