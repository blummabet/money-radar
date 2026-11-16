"""report_grafik.py — Wochen-Report „Where the money went" (Lucas 16.11.2026: „aus dem Report-Menüpunkt auf X und Insta").

Montags, aus site/report.json, Zeitraum „7t" (Mo–So der Vorwoche):
  x_bild(z)   X, englisch, 1200×675
  story(z)    Instagram-Story, deutsch, 1080×1920
  x_text(z)   Text zum X-Post

Polymarket nur, wenn der Zeitraum die verworfenen Beträge 10.–16.11. nicht berührt (Anteile-Fehler, behoben 16.11.).
"""
from __future__ import annotations

import html
from datetime import date

import grafik
import x_grafik

e = html.escape
POLY_LUECKE = ("2026-11-10", "2026-11-16")
KAT = [("E-Sport", "🎮", "Esports", "E-Sport"), ("Tennis", "🎾", "Tennis", "Tennis"), ("Fußball", "⚽", "Football", "Fußball")]
MON_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def mit_poly(z) -> bool:
    P = z.get("poly") or {}
    return bool(P.get("einsatz")) and not (z["von"] <= POLY_LUECKE[1] and z["bis"] >= POLY_LUECKE[0])


def brauchbar(z) -> bool:
    return bool((z.get("stake") or {}).get("eEinsatz")) and bool((z.get("betfair") or {}).get("moV"))


def en(v, w="$", vz=False):
    a = abs(v)
    s = f"{a / 1e6:.1f}M" if a >= 1e6 else f"{round(a / 1e3)}k" if a >= 1e3 else f"{round(a)}"
    return ("+" if vz and v > 0 else "−" if v < 0 else "") + w + s


def de(v, w="$", vz=False):
    a = abs(v)
    s = f"{a / 1e6:.1f} Mio".replace(".", ",") if a >= 1e6 else f"{round(a / 1e3)}k" if a >= 1e3 else f"{round(a)}"
    return ("+" if vz and v > 0 else "−" if v < 0 else "") + w + s


def zeitraum_en(z):
    a, b = date.fromisoformat(z["von"]), date.fromisoformat(z["bis"])
    return f"{a.day}–{b.day} {MON_EN[b.month - 1]}" if a.month == b.month else f"{a.day} {MON_EN[a.month - 1]} – {b.day} {MON_EN[b.month - 1]}"


def zeitraum_de(z):
    a, b = date.fromisoformat(z["von"]), date.fromisoformat(z["bis"])
    return f"{a.day}.–{b.day}.{b.month}." if a.month == b.month else f"{a.day}.{a.month}.–{b.day}.{b.month}."


def _wahl(x):
    a, m = str(x.get("auswahl") or ""), str(x.get("markt") or "")
    return f"{m}: {a}" if m and a.lower().split(" ")[0] in ("yes", "no", "over", "under", "draw", "x") else a


def _vz(v, n=1):
    return f"{v:+.{n}f}".replace("-", "−")


def _ligen(B, n=4):
    L = list((B.get("ligen") or {}).items())[:n]
    mx = max([v for _, v in L] + [1])
    return "".join(f"<div class='lz'><span>{e(str(l).replace('UEFA ', '').replace('English ', ''))}</span>"
                   f"<div class='sb'><i style='width:{100 * v / mx:.1f}%'></i></div><b>{en(v, '€')}</b></div>" for l, v in L)


def _sport_zeilen(S, lang):
    ks = [(k, S["kat"][k]) for k, *_ in KAT if k in (S.get("kat") or {})]
    mx = max([v["einsatz"] for _, v in ks] + [1])
    out = ""
    for k, v in sorted(ks, key=lambda kv: kv[1]["pnl"]):
        ic, n_en, n_de = next((i, a, b) for kk, i, a, b in KAT if kk == k)
        f = en if lang == "en" else de
        out += (f"<div class='sz'><div class='sn'>{ic} {e(n_en if lang == 'en' else n_de)}</div>"
                f"<div class='sb'><i style='width:{100 * v['einsatz'] / mx:.1f}%'></i><span>{f(v['einsatz'])}</span></div>"
                f"<div class='sp {'g' if v['pnl'] > 0 else 'v'}'>{f(v['pnl'], vz=True)}</div></div>")
    return out


def x_bild(z) -> str:
    S, B = z["stake"], z["betfair"]
    v = (S.get("verlierer") or [None])[0]
    gw = (S.get("gewinner") or [None])[0]
    t = (B.get("top") or [None])[0]
    fav = round(100 * B["favQuote"]) if B.get("favQuote") is not None else None
    css = """
.lt{font-size:14px;font-weight:700;color:var(--grau);margin-top:22px}
.lz{display:grid;grid-template-columns:190px 1fr 74px;gap:10px;align-items:center;margin-top:8px;font-size:15px}
.lz span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.lz b{text-align:right;font-size:15px}
.lz .sb{height:14px}
.zw{display:grid;grid-template-columns:1.12fr 1fr;gap:18px;margin-top:22px;flex:1;margin-bottom:18px}
.c{background:var(--karte);border-radius:18px;padding:20px 24px;display:flex;flex-direction:column}
.q{font-size:15px;font-weight:700;color:var(--grau)}
.big{font-family:'Barlow Condensed';font-weight:800;font-size:66px;line-height:.95;margin-top:6px}
.big small{font-size:20px;font-family:'Inter';font-weight:600;color:var(--grau);margin-left:10px}
.ue{font-size:15px;color:var(--grau);margin-top:4px}
.g{color:#0F7A47}.v{color:#C0392B}
.sz{display:grid;grid-template-columns:118px 1fr 92px;gap:12px;align-items:center;margin-top:10px}
.sn{font-size:16px;font-weight:600}
.sb{position:relative;height:24px;background:#EDEFF2;border-radius:6px;overflow:hidden}
.sb i{position:absolute;left:0;top:0;bottom:0;background:var(--hell)}
.sb span{position:relative;font-size:13px;font-weight:600;padding-left:8px;line-height:24px;color:var(--grau)}
.sp{font-family:'Barlow Condensed';font-weight:800;font-size:28px;text-align:right}
.fu{margin-top:auto;font-size:15px;line-height:1.35;border-top:1px solid #E3E6EA;padding-top:10px}
.fb{height:14px;border-radius:7px;background:#EDEFF2;margin-top:12px;overflow:hidden}.fb i{display:block;height:100%;background:var(--gelb)}
"""
    links = (f"<div class='c'><div class='q'>Stake high rollers · single bets</div>"
             f"<div class='big {'g' if S['pnl'] > 0 else 'v'}'>{en(S['pnl'], vz=True)}<small>{_vz(100 * S['roi'])}% ROI</small></div>"
             f"<div class='ue'>{S['n']:,} bets on football, tennis &amp; esports</div>{_sport_zeilen(S, 'en')}"
             + "<div class='fu'>"
             + (f"Biggest win: <b>{en(gw['pnl'], vz=True)}</b> from {en(gw['einsatz'])} on {e(_wahl(gw))} @ {gw['quote']} ({e(gw['event'])}).<br>" if gw and gw["pnl"] > 0 else "")
             + (f"Biggest loss: <b>{en(v['einsatz'])}</b> on {e(_wahl(v))} @ {v['quote']} ({e(v['event'])}) – gone." if v else "") + "</div></div>")
    rechts = (f"<div class='c'><div class='q'>Betfair · match odds before kick-off</div>"
              f"<div class='big'>{en(B['moV'], '€')}<small>{B['moN']:,} games</small></div>"
              + (f"<div class='ue' style='margin-top:14px'>Side with 60%+ of the money won <b style='color:var(--tinte)'>{B['favW']} of {B['favN']}</b> ({fav}%)</div>"
                 f"<div class='fb'><i style='width:{fav}%'></i></div>" if fav is not None else "")
              + (f"<div class='lt'>Where it was matched</div>{_ligen(B)}" if B.get("ligen") else "")
              + (f"<div class='fu'>Biggest game: <b>{e(t['spiel'])}</b>, {en(t['v'], '€')} matched. The money was "
                 f"{'right ✅' if t.get('geldRecht') else 'wrong ❌'}{' (' + '–'.join(map(str, t['ft'])) + ')' if t.get('ft') else ''}.</div>" if t else "")
              + "</div>")
    body = (x_grafik._kopf("Where the money went", f"Week {zeitraum_en(z)} · what the big money did, and how it ended",
                           en(S["einsatz"]), "staked by Stake high rollers")
            + f"<div class='zw'>{links}{rechts}</div>" + x_grafik.FUSS)
    return x_grafik._seite(body, css)


def x_text(z) -> str:
    S, B = z["stake"], z["betfair"]
    ks = sorted(((k, v) for k, v in (S.get("kat") or {}).items()), key=lambda kv: kv[1]["pnl"])
    schlimm = ""
    if ks and ks[0][1]["pnl"] < 0:
        n = {"E-Sport": "Esports", "Fußball": "Football"}.get(ks[0][0], ks[0][0])
        schlimm = f" {n} alone: {en(ks[0][1]['pnl'], vz=True)}."
    t = (f"📊 Where the money went · {zeitraum_en(z)}\n\n"
         f"Stake high rollers bet {en(S['einsatz'])} on football, tennis & esports. Single bets: {en(S['pnl'], vz=True)} "
         f"({_vz(100 * S['roi'])}%).{schlimm}\n\n"
         f"Betfair: {en(B['moV'], '€')} matched before kick-off on {B['moN']:,} games.")
    if B.get("favQuote") is not None:
        t += f" The side with 60%+ of the money won {round(100 * B['favQuote'])}%."
    if mit_poly(z):
        P = z["poly"]
        t += f"\n\nTracked Polymarket whales: {en(P['gewinn'], vz=True)} on {en(P['einsatz'])}."
    return t


STORY_CSS = """
html,body{width:1080px;height:1920px}
.st{width:1080px;height:1920px;padding:200px 64px 0;display:flex;flex-direction:column}
.kopf .t{font-size:56px}
.zr{font-size:32px;color:var(--grau);margin-top:34px;font-weight:500;line-height:1.3}
.c{background:var(--karte);border-radius:28px;padding:34px 40px;margin-top:30px}
.q{font-size:28px;font-weight:700;color:var(--grau)}
.big{font-family:'Barlow Condensed';font-weight:800;font-size:150px;line-height:.9;margin-top:10px;letter-spacing:-1px}
.big small{font-size:34px;font-family:'Inter';font-weight:600;color:var(--grau);margin-left:16px;letter-spacing:0}
.ue{font-size:28px;color:var(--grau);margin-top:8px}
.g{color:var(--gewinn)}.v{color:var(--verlust)}
.sz{display:grid;grid-template-columns:190px 1fr 220px;gap:18px;align-items:center;margin-top:18px}
.sn{font-size:30px;font-weight:600}
.sb{position:relative;height:44px;background:#EDEFF2;border-radius:10px;overflow:hidden}
.sb i{position:absolute;left:0;top:0;bottom:0;background:var(--hell)}
.sb span{position:relative;font-size:24px;font-weight:600;padding-left:14px;line-height:44px;color:var(--grau)}
.sp{font-family:'Barlow Condensed';font-weight:800;font-size:46px;text-align:right;white-space:nowrap}
.fu{margin-top:22px;font-size:28px;line-height:1.4;border-top:2px solid #E3E6EA;padding-top:18px}
.fb{height:26px;border-radius:13px;background:#EDEFF2;margin-top:16px;overflow:hidden}.fb i{display:block;height:100%;background:var(--gelb)}
.ende{margin-top:auto;height:150px;margin:auto -64px 200px;padding:0 64px;background:var(--tinte);color:#fff;display:flex;align-items:center;justify-content:space-between}
.ende .l{font-family:'Barlow Condensed';font-weight:700;font-size:50px}.ende .l b{color:var(--gelb)}
.ende .r{font-size:24px;color:#B9BEC5;text-align:right;line-height:1.45}
"""


def story(z) -> str:
    S, B = z["stake"], z["betfair"]
    v = (S.get("verlierer") or [None])[0]
    t = (B.get("top") or [None])[0]
    fav = round(100 * B["favQuote"]) if B.get("favQuote") is not None else None
    tausend = lambda n: f"{n:,}".replace(",", ".")  # noqa: E731
    roi = f"{_vz(100 * S['roi'])} %".replace(".", ",")
    stake = (f"<div class='c'><div class='q'>Stake-Highroller · Einzelwetten</div>"
             f"<div class='big {'g' if S['pnl'] > 0 else 'v'}'>{de(S['pnl'], vz=True)}<small>{roi}</small></div>"
             f"<div class='ue'>{tausend(S['n'])} Wetten, zusammen {de(S['einsatz'])} Einsatz</div>{_sport_zeilen(S, 'de')}"
             + (f"<div class='fu'>Größter Verlust: <b>{de(v['einsatz'])}</b> auf {e(_wahl(v))} zu {grafik.q2(v['quote'])} "
                f"({e(v['event'])}) – weg.</div>" if v else "") + "</div>")
    bf = (f"<div class='c'><div class='q'>Betfair · Sieg-Markt vor Anpfiff</div>"
          f"<div class='big'>{de(B['moV'], '€')}<small>{tausend(B['moN'])} Spiele</small></div>"
          + (f"<div class='ue' style='margin-top:16px'>Seite mit 60 %+ des Geldes gewann <b style='color:var(--tinte)'>{B['favW']} von {B['favN']}</b> ({fav} %)</div>"
             f"<div class='fb'><i style='width:{fav}%'></i></div>" if fav is not None else "")
          + (f"<div class='fu'>Teuerstes Spiel: <b>{e(t['spiel'])}</b>, {de(t['v'], '€')} gehandelt. Das Geld lag "
             f"{'richtig ✅' if t.get('geldRecht') else 'falsch ❌'}{' (' + ':'.join(map(str, t['ft'])) + ')' if t.get('ft') else ''}.</div>" if t else "")
          + "</div>")
    body = (f"<div class='st'>{grafik._kopf('Wohin das Geld floss', f'Woche {zeitraum_de(z)}')}"
            f"<div class='zr'>Was das große Geld letzte Woche gesetzt hat – und wie es ausging.</div>{stake}{bf}"
            "<div class='ende'><div class='l'>t.me/<b>c0c0bet</b></div><div class='r'>Echtes Geld, echte Wetten<br>18+ · keine Wettempfehlung</div></div></div>")
    return grafik._html(body, STORY_CSS)
