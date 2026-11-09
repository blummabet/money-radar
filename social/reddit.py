#!/usr/bin/env python3
"""reddit.py — Abend-Text für die Reddit-Daily-Threads (Lucas 09.11.2026).

„So gegen 17, 18 Uhr irgendwelche Nachrichten, die ich dort reinposten kann für die Abendspiele … mit Zufluss hin und
her." Reine Info, kein Tipp: wo heute Abend das Geld liegt — Betfair (gematcht + Verteilung), Polymarket (Volumen +
größter Wal), ob gerade noch Geld nachkommt. Englisch, Reddit-Markdown, als <pre> in den Testkanal: antippen = kopiert.
"""
from __future__ import annotations

import html
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

WIEN = ZoneInfo("Europe/Vienna")
MIN_EUR = 3000            # unter €3k gematcht ist ein Abendspiel kein Thema
HOECHSTENS = 6


def _ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _geld(v, w="€"):
    v = float(v or 0)
    return f"{w}{v / 1000:.0f}k" if v >= 9500 else f"{w}{v / 1000:.1f}k" if v >= 1000 else f"{w}{v:.0f}"


def _seite(g, k):
    return {"heim": g["heim"], "gast": g["gast"], "remis": "the draw"}.get(k or "", "")


def text(d: dict, jetzt: datetime) -> str | None:
    w = jetzt.astimezone(WIEN)
    bis = (w + timedelta(days=1)).replace(hour=1, minute=0, second=0, microsecond=0)
    serien = {str(x.get("id")): x for x in d.get("serien") or []}
    pm = {m.get("key"): m for m in (d.get("poly") or {}).get("maerkte") or []}
    spiele = []
    for g in d.get("toepfe") or []:
        if g.get("sport") != "Fußball" or g.get("live") or g.get("entschieden"):
            continue
        ko = _ts(g.get("anpfiff"))
        if not ko or not (jetzt <= ko <= bis):
            continue
        bf, po = (g.get("q") or {}).get("betfair") or {}, (g.get("q") or {}).get("poly") or {}
        if float(bf.get("betrag") or 0) < MIN_EUR:
            continue
        spiele.append((float(bf.get("betrag") or 0) + 0.9 * float(po.get("betrag") or 0), g, ko, bf, po))
    if not spiele:
        return None
    spiele.sort(key=lambda x: -x[0])
    zeilen = [f"**Where the money is tonight** ({w:%a %d %b}, kick-offs CET)", ""]
    for _, g, ko, bf, po in spiele[:HOECHSTENS]:
        zeilen.append(f"**{g['heim']} vs {g['gast']}** – {ko.astimezone(WIEN):%H:%M}, {g.get('liga') or ''}")
        auf, q = bf.get("auf"), (bf.get("quoten") or {}).get(bf.get("auf") or "")
        teil = f"Betfair: {_geld(bf.get('betrag'))} matched"
        if auf and bf.get("anteil"):
            teil += f", {bf['anteil']}% on {_seite(g, auf)}" + (f" ({q:.2f})" if isinstance(q, (int, float)) else "")
        zeilen.append(f"- {teil}")
        x = serien.get(str(g.get("id")))
        if x and x.get("letzte15"):
            zeilen.append(f"- Still coming in: {_geld(x['letzte15'])} in the last 15 min on {x.get('auswahl')}")
        if float(po.get("betrag") or 0) >= 2000:
            p = f"Polymarket: {_geld(po['betrag'], '$')} traded"
            if po.get("auf") and po.get("anteil"):
                p += f", {po['anteil']}% on {_seite(g, po['auf'])}"
            wal = max((wh for wh in (pm.get(po.get("key")) or {}).get("wale") or [] if (wh.get("usd") or 0) >= 3000),
                      key=lambda wh: wh["usd"], default=None)
            if wal:
                p += f" · biggest wallet {_geld(wal['usd'], '$')} on {wal['seite']}"
            zeilen.append(f"- {p}")
        zeilen.append("")
    zeilen.append("*Just market data, not a tip. Matched money has a backer and a layer – odds direction tells you who's pushing.*")
    return "\n".join(zeilen)


def nachricht(d: dict, jetzt: datetime) -> str | None:
    t = text(d, jetzt)
    if not t:
        return None
    return "📋 <b>Reddit Daily Thread</b> – antippen zum Kopieren\n\n<pre>" + html.escape(t) + "</pre>"
