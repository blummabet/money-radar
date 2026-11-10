"""x_grafik.py — Bilder für Cocobet auf X (Lucas 10.11.2026), englisch, 1200×675 (16:9, wird im X-Feed nicht beschnitten).

  band(meta, phasen)   „How the money moved": Phasenband eines beendeten Spiels (Sieg-Spur + Tore-Spur, Tore als Marker)
  wal(spiel)           „Smart money split": belegte Polymarket-Wallets je Seite, eindeutig/umstritten

Gleiche Marke wie die Instagram-Grafiken (grafik.py): Grau/Weiß/Schwarz + CocoBet-Gelb, Barlow Condensed + Inter.
"""
from __future__ import annotations

import html
import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import grafik

WIEN = ZoneInfo("Europe/Vienna")
e = html.escape
B, H = 1200, 675

CSS = f"""
:root{{--bg:#E9ECEF;--karte:#FFFFFF;--tinte:#0B0B0B;--grau:#5E6672;--hell:#D5D9DE;--gelb:#F6C000;--h:#E08600;--a:#2F6FE0;--x:#9AA1AD;--o:#0F7A47;--u:#C0392B}}
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{width:{B}px;height:{H}px;background:var(--bg);color:var(--tinte);font-family:'Inter',sans-serif;-webkit-font-smoothing:antialiased}}
.s{{width:{B}px;height:{H}px;padding:40px 48px 0;display:flex;flex-direction:column}}
.k{{display:flex;align-items:center;gap:16px}}
.k img{{width:58px;height:58px;border-radius:50%}}
.k .t{{font-family:'Barlow Condensed';font-weight:700;font-size:34px;line-height:1}}
.k .u{{font-size:17px;color:var(--grau);margin-top:4px;font-weight:500}}
.k .r{{margin-left:auto;text-align:right}}
.k .r b{{font-family:'Barlow Condensed';font-size:40px;font-weight:800;line-height:1}}
.k .r small{{display:block;font-size:14px;color:var(--grau)}}
.f{{margin-top:auto;height:62px;margin-left:-48px;margin-right:-48px;padding:0 48px;background:var(--tinte);color:#fff;display:flex;align-items:center;justify-content:space-between}}
.f .l{{font-family:'Barlow Condensed';font-weight:700;font-size:28px}} .f .l b{{color:var(--gelb)}}
.f .r{{font-size:14px;color:#B9BEC5}}
"""


def _seite(body, css=""):
    return f"<!doctype html><html lang='en'><head><meta charset='utf-8'><style>{grafik._fonts()}{CSS}{css}</style></head><body><div class='s'>{body}</div></body></html>"


def _kopf(titel, unter, rechts="", rechts_klein=""):
    return (f"<div class='k'><img src='data:image/png;base64,{grafik._b64('logo.png')}' alt=''><div><div class='t'>{e(titel)}</div>"
            f"<div class='u'>{e(unter)}</div></div><div class='r'><b>{e(rechts)}</b><small>{e(rechts_klein)}</small></div></div>")


FUSS = "<div class='f'><div class='l'>t.me/<b>c0c0bet</b></div><div class='r'>Where the big money goes · 18+ · not betting advice</div></div>"


def _ts(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00")) if s else None


def band(meta: dict, Z: list) -> str:
    T = lambda z: datetime.fromisoformat(z[0] + ":00+00:00")  # noqa: E731
    ko = _ts(meta.get("anpfiff"))
    t0, letzt = T(Z[0]), T(Z[-1]) + timedelta(minutes=5)
    t1 = max(letzt, ko + timedelta(minutes=110)) if ko else letzt
    spanne = (t1 - t0).total_seconds()
    x = lambda t: max(0.0, min(100.0, (t - t0).total_seconds() / spanne * 100))  # noqa: E731
    maxE = max([z[2] or 0 for z in Z] + [1]); maxT = max([abs(z[4] or 0) for z in Z] + [1])
    op = lambda v, m: 0.2 + 0.8 * math.log1p(v) / math.log1p(m)  # noqa: E731
    FS = {"h": "var(--h)", "a": "var(--a)", "x": "var(--x)"}

    def stueck(i, farbe, wert):
        a = x(T(Z[i])); b = x(T(Z[i + 1])) if i + 1 < len(Z) else x(T(Z[i]) + timedelta(minutes=5))
        return f"<i style='left:{a:.2f}%;width:{max(0.5, b - a - 0.35):.2f}%;background:{farbe};opacity:{wert:.2f}'></i>"
    sieg = "".join(stueck(i, FS[z[1]], op(z[2] or 0, maxE)) for i, z in enumerate(Z) if z[1] in FS)
    tore = "".join(stueck(i, "var(--o)" if z[4] > 0 else "var(--u)", op(abs(z[4]), maxT)) for i, z in enumerate(Z) if z[4])
    mk = []
    if ko and t0 <= ko <= t1:
        mk += [(ko, "Kick-off"), (ko + timedelta(minutes=47), "HT")]
    vor = None
    for z in Z:
        if z[5] and vor and z[5] != vor:
            mk.append((T(z) - timedelta(minutes=2), f"⚽ {z[5][0]}:{z[5][1]}"))
        if z[5]:
            vor = z[5]
    mk.sort()
    marker, lx, reihe = "", -99.0, 0
    for t, txt in mk:
        p = x(t); reihe = (reihe + 1) % 2 if p - lx < 9 else 0; lx = p
        rand = "transform:translateX(-100%)" if p > 90 else "transform:none" if p < 6 else ""
        marker += f"<span class='mk' style='left:{p:.2f}%'><em style='top:{-52 if reihe else -28}px;{rand}'>{e(txt)}</em></span>"
    achse = "".join(f"<span style='left:{f * 100:.0f}%'>{(t0 + timedelta(seconds=spanne * f)).astimezone(WIEN):%H:%M}</span>" for f in (0, .25, .5, .75, 1))
    ft = meta.get("ft")
    css = """
.z{display:grid;grid-template-columns:70px 1fr;align-items:center;gap:14px;margin-top:10px}
.z.z1{margin-top:96px}
.lb{font-size:16px;font-weight:700;color:var(--grau)}
.b{position:relative;height:170px;border-radius:12px;background:var(--hell)}
.b.t{height:64px}
.b i{position:absolute;top:0;bottom:0;border-radius:3px}
.mk{position:absolute;top:-6px;bottom:-6px;width:0;border-left:2px dashed var(--tinte)}
.mk em{position:absolute;left:0;transform:translateX(-50%);font-style:normal;font-size:17px;font-weight:700;white-space:nowrap}
.ax{position:relative;height:20px}
.ax span{position:absolute;transform:translateX(-50%);font-size:14px;color:var(--grau)} .ax span:first-child{transform:none} .ax span:last-child{transform:translateX(-100%)}
.lg{display:flex;gap:22px;font-size:16px;color:var(--grau);margin-top:16px} .lg i{display:inline-block;width:14px;height:14px;border-radius:4px;margin-right:6px;vertical-align:-1px} .lg .r{margin-left:auto}
"""
    body = (_kopf("How the money moved", f"{meta['heim']} vs {meta['gast']} · {meta.get('liga') or ''}",
                  f"{ft[0]}:{ft[1]}" if ft else "", "final" if ft else "")
            + f"<div class='z z1'><div class='lb'>Winner</div><div class='b'>{sieg}{marker}</div></div>"
            + f"<div class='z'><div class='lb'>Goals</div><div class='b t'>{tore}</div></div>"
            + f"<div class='z' style='margin-top:6px'><div></div><div class='ax'>{achse}</div></div>"
            + f"<div class='lg'><span><i style='background:var(--h)'></i>{e(meta['heim'])}</span><span><i style='background:var(--x)'></i>Draw</span>"
              f"<span><i style='background:var(--a)'></i>{e(meta['gast'])}</span><span class='r'><i style='background:var(--o)'></i>Over "
              f"<i style='background:var(--u);margin-left:12px'></i>Under</span></div>"
            + "<div style='font-size:14px;color:var(--grau);margin-top:8px'>Each block = 5 minutes of new money on Betfair · stronger colour = more money · times CET</div>"
            + FUSS)
    return _seite(body, css)


def band_text(meta: dict, Z: list) -> str | None:
    """Die Geschichte in zwei Sätzen — None, wenn das Spiel keine hat."""
    ko = _ts(meta.get("anpfiff"))
    T = lambda z: datetime.fromisoformat(z[0] + ":00+00:00")  # noqa: E731
    nm = {"h": meta["heim"], "a": meta["gast"], "x": "the draw"}
    vor = [z for z in Z if z[1] and (not ko or T(z) < ko)]
    nach = [z for z in Z if z[1] and ko and T(z) >= ko]
    def haupt(zs):
        s = {}
        for z in zs:
            s[z[1]] = s.get(z[1], 0) + (z[2] or 0)
        if not s:
            return None, 0, 0
        k = max(s, key=s.get)
        return k, s[k], s[k] / (sum(s.values()) or 1)
    k1, e1, a1 = haupt(vor)
    # Kippen: größter zusammenhängender Block der anderen Seite nach Anpfiff
    k2, e2, a2 = haupt(nach[len(nach) // 2:]) if nach else (None, 0, 0)
    ft = meta.get("ft")
    end = f" Final score {ft[0]}:{ft[1]}." if ft else ""
    geld = lambda v: f"€{v / 1000:.0f}k" if v >= 9500 else f"€{v / 1000:.1f}k"  # noqa: E731
    if k1 and k2 and k1 != k2 and e2 >= 5000:
        return (f"How the money moved: {meta['heim']} vs {meta['gast']}.\n\nBefore kick-off the flow was on {nm[k1]} ({a1 * 100:.0f}%). "
                f"Late in the game it flipped – {geld(e2)} onto {nm[k2]}.{end}")
    k, ges, a = haupt([z for z in Z if z[1]])
    if k and a >= 0.7 and ges >= 15000:
        return (f"How the money moved: {meta['heim']} vs {meta['gast']}.\n\n{geld(ges)} of new money, {a * 100:.0f}% of it on {nm[k]} – "
                f"wave after wave, all day.{end}")
    return None


def band_score(meta, Z):
    ges = sum(z[2] or 0 for z in Z)
    seiten = {z[1] for z in Z if z[1] and (z[2] or 0) >= 1000}
    return ges * (1.6 if len(seiten) > 1 else 1.0)


def wal(g: dict) -> str:
    """g = {spiel, anpfiff, sport, seiten:[{name, usd, wallets:[(id, usd, clv)]}], klar, anteil}"""
    S = g["seiten"]; ges = sum(s["usd"] for s in S) or 1
    zeilen = ""
    for i, s in enumerate(S):
        pct = s["usd"] / ges * 100
        w = " · ".join(f"{e(a)} ${u / 1000:.0f}k (+{c:.1f}pp)" for a, u, c in s["wallets"][:4])
        zeilen += (f"<div class='w'><div class='n'><b>{e(s['name'])}</b><span>{pct:.0f}%</span><em>${s['usd'] / 1000:.1f}k</em></div>"
                   f"<div class='bar'><i style='width:{pct:.1f}%;background:{'#7A5AF8' if i == 0 else '#B8A8F5'}'></i></div><div class='wl'>{w}</div></div>")
    ko = _ts(g.get("anpfiff"))
    css = """
.w{margin-top:26px} .w:first-of-type{margin-top:48px}
.n{display:flex;align-items:baseline;gap:16px} .n b{font-family:'Barlow Condensed';font-size:40px;font-weight:700} .n span{font-family:'Barlow Condensed';font-size:52px;font-weight:800;margin-left:auto} .n em{font-style:normal;font-size:20px;color:var(--grau);width:110px;text-align:right}
.bar{height:26px;border-radius:8px;background:var(--hell);overflow:hidden;margin-top:6px} .bar i{display:block;height:100%}
.wl{font-size:15px;color:var(--grau);margin-top:8px}
.chip{display:inline-block;margin-top:26px;font-size:18px;font-weight:700;padding:6px 16px;border-radius:999px}
"""
    chip = (f"<span class='chip' style='background:#D7F0E2;color:#0F7A47'>✓ One-sided · {g['anteil'] * 100:.0f}% of proven money</span>" if g["klar"]
            else "<span class='chip' style='background:#FCE9C8;color:#A35F00'>⚔ Contested – proven wallets on both sides</span>")
    body = (_kopf("Smart money split", f"{g['spiel']} · Polymarket" + (f" · {ko.astimezone(WIEN):%a %H:%M} CET" if ko else ""),
                  f"${ges / 1000:.0f}k", "proven wallets")
            + zeilen + f"<div>{chip}</div>"
            + "<div style='font-size:14px;color:var(--grau);margin-top:14px'>Proven = wallets that beat Polymarket's closing price over 15+ settled bets</div>"
            + FUSS)
    return _seite(body, css)
