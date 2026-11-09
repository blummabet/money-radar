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


MIN_QUOTE = 1.50          # Lucas 09.11.: „nichts mit einer 1,2" — jeder kennt die Favoriten
MIN_ANTEIL = 60
SPORTARTEN = {"Fußball": "⚽", "E-Sport": "🎮", "Tennis": "🎾"}


def _kandidat(g, serien, pserien):
    """Lucas 09.11. 18:20: nur Spiele mit Geschichte — die Märkte sind sich einig, Quote ab 1,50, und dazu ein großes
    oder bewährtes Wallet und/oder Geld, das über den Tag schon geflossen ist. Gegen-Wal (bewährt, ≥ $5k) = raus."""
    q = g.get("q") or {}
    bf, po, st = q.get("betfair") or {}, q.get("poly") or {}, q.get("stake") or {}
    seiten = {k: v.get("auf") for k, v in (("betfair", bf), ("poly", po), ("stake", st))
              if v.get("auf") and (v.get("anteil") or 0) >= MIN_ANTEIL and float(v.get("betrag") or 0) >= (10000 if k != "stake" else 3000)}
    if not seiten:
        return None
    k = max(set(seiten.values()), key=list(seiten.values()).count)
    if any(v != k for v in seiten.values()):
        return None                                    # Märkte uneinig → Coinflip, kein Thema
    quote = (bf.get("quoten") or {}).get(k) if bf.get("auf") == k else None
    if not isinstance(quote, (int, float)):
        pr = (po.get("ask") or {}).get(k) or (po.get("preise") or {}).get(k)
        quote = round(1 / pr, 2) if isinstance(pr, (int, float)) and 0 < pr < 1 else None
    if not isinstance(quote, (int, float)) or quote < MIN_QUOTE:
        return None
    wale = g.get("wale") or []
    if any(w.get("k") != k and (w.get("usd") or 0) >= 5000 and (w.get("clvUg") or 0) > 0 for w in wale):
        return None
    wal = max((w for w in wale if w.get("k") == k), key=lambda w: w.get("usd") or 0, default=None)
    fluss = serien.get(str(g.get("id"))) or pserien.get(str(po.get("key")))
    story, punkte = [], 0
    if len(seiten) >= 2:
        nm = [{"betfair": "Betfair", "poly": "Polymarket", "stake": "Stake high rollers"}[m] for m in seiten]
        story.append((", ".join(nm[:-1]) + " and " + nm[-1]) + " on the same side")
        punkte += 2 * (len(seiten) - 1)
    if wal and (wal.get("usd") or 0) >= 10000:
        story.append(f"a single wallet with {_geld(wal['usd'], '$')} on it" + (" – one with a winning record" if (wal.get("clvUg") or 0) > 0 else ""))
        punkte += 2 + (1 if (wal.get("clvUg") or 0) > 0 else 0)
    if fluss:
        story.append(f"money has been coming in for {fluss.get('dauerMin') or '?'} min" if fluss.get("dauerMin") else "a steady flow all day")
        punkte += 1
    if punkte < 2:
        return None                                    # keine Geschichte → nicht posten
    geld = float(bf.get("betrag") or 0) + 0.9 * float(po.get("betrag") or 0)
    return {"g": g, "k": k, "quote": quote, "bf": bf, "po": po, "st": st, "wal": wal, "story": story,
            "punkte": punkte, "geld": geld, "fluss": fluss}


def text(d: dict, jetzt: datetime) -> str | None:
    w = jetzt.astimezone(WIEN)
    bis = (w + timedelta(days=1)).replace(hour=1, minute=0, second=0, microsecond=0)
    serien = {str(x.get("id")): x for x in d.get("serien") or []}
    pserien = {str(x.get("id")): x for x in d.get("polySerien") or []}
    ks = []
    for g in d.get("toepfe") or []:
        if g.get("sport") not in SPORTARTEN or g.get("live") or g.get("entschieden"):
            continue
        ko = _ts(g.get("anpfiff"))
        if not ko or not (jetzt <= ko <= bis):
            continue
        k = _kandidat(g, serien, pserien)
        if k:
            k["ko"] = ko
            ks.append(k)
    if not ks:
        return None
    ks.sort(key=lambda k: (-k["punkte"], -k["geld"]))
    ks = ks[:3]
    a = ks[0]
    z = [f"**Where the money agrees tonight** ({w:%a %d %b}, times CET)", "",
         f"Biggest one: **{_seite(a['g'], a['k'])}** @ {a['quote']:.2f} – {a['story'][0]}" + (f", plus {a['story'][1]}" if len(a["story"]) > 1 else "") + ".", ""]
    for k in ks:
        g = k["g"]
        z.append(f"{SPORTARTEN[g['sport']]} **{g['heim']} vs {g['gast']}** – {k['ko'].astimezone(WIEN):%H:%M}, {g.get('liga') or ''}")
        z.append(f"- Money on **{_seite(g, k['k'])}** @ {k['quote']:.2f}")
        if k["bf"].get("auf") == k["k"]:
            z.append(f"- Betfair: {_geld(k['bf'].get('betrag'))} matched, {k['bf'].get('anteil')}% on that side")
        if k["po"].get("auf") == k["k"]:
            z.append(f"- Polymarket: {_geld(k['po'].get('betrag'), '$')} traded, {k['po'].get('anteil')}% on that side"
                     + (f", biggest wallet {_geld(k['wal']['usd'], '$')}" if k["wal"] and (k["wal"].get("usd") or 0) >= 3000 else ""))
        if k["st"].get("auf") == k["k"]:
            z.append(f"- Stake: {_geld(k['st'].get('betrag'), '$')} from high rollers")
        z.append("")
    z.append("*Only games where the markets point the same way and the odds are 1.50+. Just data, not a tip.*")
    return "\n".join(z)


def nachricht(d: dict, jetzt: datetime) -> str | None:
    t = text(d, jetzt)
    if not t:
        return None
    return "📋 <b>Reddit Daily Thread</b> – antippen zum Kopieren\n\n<pre>" + html.escape(t) + "</pre>"
