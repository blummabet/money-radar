"""Ticker-Gedächtnis (Lucas 18.11. 15:02, „1–6, liebe es" — der Live-Ticker / „Alles" soll vom „was passiert" zum
„was taugt" werden). Bisher baute die Seite den Feed bei jedem Abruf neu aus den aktuellen Listen: jede Meldung
trug die Uhrzeit des Abrufs, ihre Quote war die von jetzt, nach dem Ende verschwand sie ohne Ergebnis.

Hier merkt sich der Server je Meldung (Art + Spiel + Markt + Auswahl, live zusätzlich der Stand):
* erstes Auftauchen (Zeit, Quote, Minute/Stand) — Grundlage für „seitdem" (1) und „neu seit dem letzten Besuch" (2),
* die Quote dieser Auswahl bei jedem Lauf bis Spielende → kleine Preisspur mit Toren (4) und „jetzt"-Quote (1),
* das Größte, was die Meldung erreichte (Serie n×, Geld, % unter der Uhr) — eine Zeile, die mitwächst (6),
* nach Abpfiff das Ergebnis (✓/✗) über bilanz.gewonnen (3).
Nur Betfair-Meldungen (Dauerzufluss, Zufluss vor Anpfiff, Live-Zufluss, gegen die Uhr). Klein gehalten: 3 Tage,
Spur höchstens 40 Punkte — ein paar hundert KB (Speicher-Regel).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bilanz

DATEI = Path(__file__).resolve().parent / ".push_state" / "ticker.json"
TAGE = 3
SPUR_MAX = 40


def _ts(s):
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def _quellen(d):
    """(art, eintrag, markt, auswahl, quote, extra) aus den aktuellen Listen. REIN."""
    for x in d.get("serien") or []:
        yield "serie", x, x.get("markt"), x.get("auswahl"), x.get("quote"), {"n": x.get("n"), "eur": x.get("eur"), "anteil": x.get("anteil")}
    for liste, art in ((d.get("liveZufluss"), "live"), (d.get("zufluss30"), "zufluss")):
        for x in liste or []:
            fl = (x.get("fluss") or [None])[0]
            if fl:
                yield art, x, fl.get("markt"), fl.get("auswahl"), fl.get("quote"), {"eur": fl.get("eur"), "anteil": fl.get("anteil"),
                                                                                    "quoteVorher": fl.get("quoteVorher")}
    for x in d.get("bfDrops") or []:            # 18.11. 15:22 (Lucas: die neuen Drops aus dem Pinnacle-Tab auch in den Ticker)
        yield "bfdrop", x, x.get("markt"), x.get("auf"), x.get("quote"), {"pp": x.get("pp"), "eur": x.get("eur"),
                                                                          "quoteVorher": x.get("vonQuote")}
    for x in d.get("bfUhr") or []:
        yield "uhr", x, x.get("markt"), x.get("auswahl"), x.get("quote"), {"unter": x.get("unter"), "eur": x.get("neu"),
                                                                          "quoteVorher": x.get("q0"), "erwartet": x.get("erwartet")}


def lauf(d, prices, endst, jetzt=None):
    jetzt = jetzt or datetime.now(timezone.utc)
    try:
        buch = json.loads(DATEI.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        buch = {}
    spiele = {str(m.get("matchId")): m for m in (prices or {}).get("matches") or []}
    radar = {str(s.get("id")): s for s in (d.get("radar") or {}).get("spiele") or []}
    aktiv = set()
    for art, x, mk, aw, q, extra in _quellen(d):
        sid = str(x.get("id"))
        if not mk or not aw or not isinstance(q, (int, float)):
            continue
        lv = (radar.get(sid) or {}).get("live") or {}
        live = bool(lv) and art != "zufluss"
        st = lv.get("stand") if live else None
        key = f"{art}|{sid}|{mk}|{aw}" + (f"|{st[0]}:{st[1]}" if live and st and None not in st else "")
        aktiv.add(key)
        e = buch.get(key)
        if e is None:
            e = buch[key] = {"art": art, "id": sid, "heim": x.get("heim"), "gast": x.get("gast"), "liga": x.get("liga"),
                             "land": x.get("land"), "anpfiff": x.get("anpfiff"), "markt": mk, "auswahl": aw,
                             "erst": jetzt.isoformat(timespec="seconds"), "qErst": q, "minErst": lv.get("min") if live else None,
                             "standErst": st, "live": live, "spur": []}
        e["letzt"] = jetzt.isoformat(timespec="seconds")
        e["qLetzt"] = q
        for k, v in extra.items():
            if isinstance(v, (int, float)):
                if k in ("n", "eur", "unter", "pp"):
                    e[k + "Max"] = max(v, e.get(k + "Max") or 0)
                e[k] = v
            elif v is not None:
                e[k] = v
    # Preisspur + jetzt-Quote, Ergebnis
    for key, e in buch.items():
        e["aktiv"] = key in aktiv
        m = spiele.get(e["id"])
        if m and not e.get("ft"):
            mk = (m.get("markets") or {}).get(e["markt"]) or {}
            q = next((r.get("odd") for r in mk.get("runners") or [] if r.get("name") == e["auswahl"]), None)
            li = m.get("liveInfo") or {}
            if isinstance(q, (int, float)) and q > 1:
                e["qJetzt"] = q
                sp = e["spur"]
                st = [li.get("goal_v1"), li.get("goal_v2")] if li.get("status") == "IN_PLAY" or li.get("time") else None
                punkt = [jetzt.strftime("%H:%M"), q] + ([st] if st and None not in st else [])
                if not sp or sp[-1][1] != q or (len(sp[-1]) > 2) != (len(punkt) > 2) or (len(punkt) > 2 and sp[-1][2] != punkt[2]):
                    sp.append(punkt)
                    del sp[:-SPUR_MAX]
        es = (endst or {}).get(e["id"])
        if es and es.get("ft") and e.get("status") is None:
            g = bilanz.gewonnen(e["markt"], e["auswahl"], e.get("heim"), e.get("gast"), es.get("ft"), es.get("ht"))
            if g in (True, False, "void"):
                e["status"] = "won" if g is True else "lost" if g is False else "void"
                e["ft"] = es.get("ft")
    grenze = jetzt - timedelta(days=TAGE)
    buch = {k: v for k, v in buch.items() if (_ts(v.get("erst")) or jetzt) >= grenze}
    DATEI.parent.mkdir(parents=True, exist_ok=True)
    DATEI.write_text(json.dumps(buch, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    # an die Seite: 24 h, neueste zuerst
    g24 = jetzt - timedelta(hours=24)
    liste = sorted((dict(v, key=k) for k, v in buch.items() if (_ts(v.get("erst")) or jetzt) >= g24),
                   key=lambda v: v["erst"], reverse=True)[:500]
    return liste
