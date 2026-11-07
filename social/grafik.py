#!/usr/bin/env python3
"""grafik.py — Instagram-Grafiken aus den Geldradar-Daten (Lucas 04.11.2026).

Zwei Vorlagen, 1080×1350 (4:5, das Hochformat, das Instagram im Feed am größten zeigt), JPEG:
  bilanz(tag)  — „Gestern im Kanal": alle Public-Signale eines Tages mit ✓/✗, Bilanz und Einheiten
  geld(spiel)  — „Wo das Geld liegt": ein Spiel, Geld-Anteil gegen die Quote, je Quelle ein Balken

Gestaltung: helles Grau, Weiß, Schwarz und das CocoBet-Gelb aus dem Logo. Zahlen in Barlow Condensed
(Anzeigetafel), Text in Inter. Keine Gewinnversprechen; unten immer „18+ · keine Wettempfehlung".

  python3 social/grafik.py beispiel AUSGABEORDNER   → beide Vorlagen mit Beispieldaten
"""
from __future__ import annotations

import base64
import html
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ASSETS = Path(__file__).resolve().parent / "assets"
WIEN = ZoneInfo("Europe/Vienna")
TAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
MONATE = ["Jänner", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]
e = html.escape


def _b64(name):
    return base64.b64encode((ASSETS / name).read_bytes()).decode()


def _fonts():
    out = []
    for fam, datei, w in (("Barlow Condensed", "barlow-condensed-latin-{}-normal.woff2", (600, 700, 800)),
                          ("Inter", "inter-latin-{}-normal.woff2", (400, 500, 600, 700))):
        for x in w:
            out.append(f"@font-face{{font-family:'{fam}';font-weight:{x};src:url(data:font/woff2;base64,{_b64(datei.format(x))}) format('woff2')}}")
    return "\n".join(out)


def q2(v):
    return f"{v:.2f}".replace(".", ",")


def geld(v, w="€"):
    v = float(v or 0)
    return f"{w}{v / 1e6:.1f} Mio".replace(".", ",") if v >= 1e6 else f"{w}{round(v / 1000)}k" if v >= 1000 else f"{w}{round(v)}"


def auswahl_de(a, spiel=""):
    a = str(a or "")
    import re
    m = re.match(r"^(Over|Under) ([\d.]+)", a, re.I)
    if m:
        return ("Über " if m.group(1).lower() == "over" else "Unter ") + m.group(2).replace(".", ",") + " Tore"
    if a.lower() in ("the draw", "draw"):
        return "Remis"
    if not a and spiel:
        return spiel.split(" - ")[0].split(" – ")[0]
    return a


BASIS_CSS = """
:root{--bg:#E9ECEF;--karte:#FFFFFF;--tinte:#0B0B0B;--grau:#5E6672;--hell:#C9CED4;--gelb:#F6C000;--gewinn:#0F7A47;--verlust:#C0392B}
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:1080px;height:1350px;background:var(--bg);color:var(--tinte);font-family:'Inter',sans-serif;-webkit-font-smoothing:antialiased}
.seite{position:relative;width:1080px;height:1350px;padding:64px 64px 0;display:flex;flex-direction:column}
.kopf{display:flex;align-items:center;gap:22px}
.kopf img{width:92px;height:92px;border-radius:50%}
.kopf .t{font-family:'Barlow Condensed';font-weight:700;font-size:44px;line-height:1;letter-spacing:.2px}
.kopf .u{font-size:26px;color:var(--grau);margin-top:6px;font-weight:500}
.fuss{margin-top:auto;height:132px;margin-left:-64px;margin-right:-64px;padding:0 64px;background:var(--tinte);color:#fff;display:flex;align-items:center;justify-content:space-between}
.fuss .l{font-family:'Barlow Condensed';font-weight:700;font-size:44px;letter-spacing:.3px}
.fuss .l b{color:var(--gelb);font-weight:700}
.fuss .r{font-size:22px;color:#B9BEC5;text-align:right;line-height:1.45}
"""


def _html(body, css):
    return f"<!doctype html><html lang='de'><head><meta charset='utf-8'><style>{_fonts()}{BASIS_CSS}{css}</style></head><body>{body}</body></html>"


def _kopf(titel, unter):
    return (f"<div class='kopf'><img src='data:image/png;base64,{_b64('logo.png')}' alt=''>"
            f"<div><div class='t'>{e(titel)}</div><div class='u'>{e(unter)}</div></div></div>")


FUSS = ("<div class='fuss'><div class='l'>t.me/<b>c0c0bet</b></div>"
        "<div class='r'>Wo das große Geld hinfließt<br>18+ · keine Wettempfehlung</div></div>")


def bilanz(tag: datetime, signale: list[dict]) -> str:
    """signale: [{zeit (UTC ISO), spiel, auswahl, quote, r (Rendite je Einheit; 0 = void), w (1/0)}]"""
    ab = [s for s in signale if s.get("r") is not None and not (s["r"] == 0 and s.get("w") != 1)]
    gew = sum(1 for s in ab if s.get("w") == 1)
    ein = sum(s["r"] for s in ab)
    zeilen = []
    for s in signale[:12]:
        ok = s.get("w") == 1
        void = s.get("r") == 0 and not ok
        offen = s.get("r") is None
        zeichen = ("<svg viewBox='0 0 24 24'><path d='M5 12.5l4.5 4.5L19 7.5' fill='none' stroke='#fff' stroke-width='3.4' stroke-linecap='round' stroke-linejoin='round'/></svg>" if ok
                   else "<svg viewBox='0 0 24 24'><path d='M7 7l10 10M17 7L7 17' fill='none' stroke='#fff' stroke-width='3.4' stroke-linecap='round'/></svg>" if not (void or offen)
                   else "")
        klasse = "ok" if ok else "void" if (void or offen) else "nein"
        uhr = datetime.fromisoformat(str(s["zeit"]).replace("Z", "+00:00") if "+" in str(s["zeit"]) or "Z" in str(s["zeit"]) else str(s["zeit"]) + "+00:00").astimezone(WIEN)
        zeilen.append(f"<div class='z {klasse}'><span class='m'>{zeichen}</span>"
                      f"<span class='a'><b>{e(auswahl_de(s.get('auswahl'), s.get('spiel')))}</b><small>{e(str(s.get('spiel') or '').replace(' - ', ' – '))}</small></span>"
                      f"<span class='q'>{q2(s['quote']) if s.get('quote') else ''}</span><span class='u'>{uhr:%H:%M}</span></div>")
    titel = "Gestern im Kanal"
    unter = f"{TAGE[tag.weekday()]}, {tag.day}. {MONATE[tag.month - 1]}"
    vz = "+" if ein > 0 else "−" if ein < 0 else "±"
    body = (f"<div class='seite'>{_kopf(titel, unter)}"
            f"<div class='gross'><div class='bil'>{gew}<i>–</i>{len(ab) - gew}</div>"
            f"<div class='neben'><div class='ein {'plus' if ein >= 0 else 'minus'}'>{vz}{q2(abs(ein))}</div>"
            f"<div class='el'>Einheiten bei {len(ab)} Signalen<br>zur Quote im Moment des Posts</div></div></div>"
            f"<div class='liste'>{''.join(zeilen)}</div>{FUSS}</div>")
    css = """
.gross{display:flex;align-items:flex-end;gap:40px;margin:44px 0 30px}
.bil{font-family:'Barlow Condensed';font-weight:800;font-size:236px;line-height:.78;letter-spacing:-4px}
.bil i{font-style:normal;color:var(--hell);margin:0 6px}
.neben{padding-bottom:6px}
.ein{font-family:'Barlow Condensed';font-weight:700;font-size:88px;line-height:.9}
.ein.plus{color:var(--gewinn)}.ein.minus{color:var(--verlust)}
.el{font-size:24px;color:var(--grau);margin-top:10px;line-height:1.35}
.liste{background:var(--karte);border-radius:28px;padding:10px 34px}
.z{display:grid;grid-template-columns:44px 1fr auto 86px;align-items:center;gap:20px;height:60px;border-bottom:1px solid #EDEFF2}
.z:last-child{border-bottom:0}
.m{width:38px;height:38px;border-radius:50%;display:grid;place-items:center}
.m svg{width:24px;height:24px}
.ok .m{background:var(--gewinn)}.nein .m{background:var(--verlust)}.void .m{background:var(--hell)}
.a{display:flex;align-items:baseline;gap:14px;min-width:0;white-space:nowrap;overflow:hidden}
.a b{font-size:26px;font-weight:600}
.a small{font-size:20px;color:var(--grau);overflow:hidden;text-overflow:ellipsis}
.q{font-family:'Barlow Condensed';font-weight:700;font-size:32px}
.u{font-size:20px;color:var(--grau);text-align:right}
"""
    return _html(body, css)


def geld_spiel(s: dict) -> str:
    """s: {heim, gast, liga, anpfiff, seite ('heim'/'gast'/'remis'), anteil (0–100), impl (0–100 laut Quote), quote,
    gesamt (EUR), quellen: [{name, betrag, anteil}]}"""
    an = datetime.fromisoformat(str(s["anpfiff"]).replace("Z", "+00:00")).astimezone(WIEN)
    name = s["heim"] if s["seite"] == "heim" else s["gast"] if s["seite"] == "gast" else "Remis"
    diff = s["anteil"] - s["impl"]
    quellen = "".join(
        f"<div class='qz'><span class='qn'>{e(x['name'])}</span><span class='qb'><i style='width:{x['anteil']:.0f}%'></i></span>"
        f"<span class='qp'>{x['anteil']:.0f} %</span><span class='qg'>{geld(x['betrag'], x.get('w', '€'))}</span></div>" for x in s["quellen"])
    unter = f"{s['liga']} · heute {an:%H:%M}"
    body = (f"<div class='seite'>{_kopf('Wo das Geld liegt', unter)}"
            f"<div class='spiel'>{e(s['heim'])}<span>–</span>{e(s['gast'])}</div>"
            f"<div class='karte'><div class='k1'>vom Geld auf <b>{e(name)}</b></div>"
            f"<div class='paar'><div class='p'><div class='z'>{s['anteil']:.0f}<small>%</small></div><div class='zl'>des Geldes</div></div>"
            f"<div class='vs'>statt</div>"
            f"<div class='p leise'><div class='z'>{s['impl']:.0f}<small>%</small></div><div class='zl'>laut Quote {q2(s['quote'])}</div></div></div>"
            f"<div class='balken'><i class='g' style='width:{s['anteil']:.1f}%'></i><i class='mk' style='left:{s['impl']:.1f}%'></i></div>"
            f"<div class='bl'><span>Geld</span><span>Quote erwartet {s['impl']:.0f} %</span></div></div>"
            f"<div class='quellen'><div class='qt'>{geld(s['gesamt'])} gematcht · je Quelle</div>{quellen}</div>"
            f"<div class='hin'>{'+' if diff >= 0 else '−'}{abs(diff):.0f} Prozentpunkte mehr Geld, als die Quote erwarten lässt.</div>"
            f"{FUSS}</div>")
    css = """
.spiel{font-family:'Barlow Condensed';font-weight:800;font-size:104px;line-height:.95;margin:48px 0 34px;letter-spacing:-1px}
.spiel span{color:var(--hell);margin:0 18px}
.karte{background:var(--karte);border-radius:28px;padding:34px 40px 30px}
.k1{font-size:28px;color:var(--grau)}.k1 b{color:var(--tinte)}
.paar{display:flex;align-items:flex-end;gap:34px;margin-top:6px}
.z{font-family:'Barlow Condensed';font-weight:800;font-size:200px;line-height:.85;letter-spacing:-3px}
.z small{font-size:.42em;margin-left:4px;letter-spacing:0}
.zl{font-size:24px;color:var(--grau);margin-top:6px}
.leise .z{color:var(--hell);font-size:120px}
.vs{font-size:26px;color:var(--grau);padding-bottom:58px}
.balken{position:relative;height:30px;border-radius:15px;background:#EDEFF2;margin-top:30px;overflow:visible}
.balken .g{position:absolute;left:0;top:0;bottom:0;border-radius:15px;background:var(--gelb)}
.balken .mk{position:absolute;top:-10px;bottom:-10px;width:5px;margin-left:-2px;background:var(--tinte);border-radius:3px}
.bl{display:flex;justify-content:space-between;font-size:20px;color:var(--grau);margin-top:16px}
.quellen{margin-top:28px;padding:0 6px}
.qt{font-size:22px;color:var(--grau);margin-bottom:12px}
.qz{display:grid;grid-template-columns:200px 1fr 90px 120px;align-items:center;gap:18px;height:54px}
.qn{font-size:26px;font-weight:600}
.qb{height:16px;border-radius:8px;background:#D5D9DE;overflow:hidden}.qb i{display:block;height:100%;background:var(--tinte)}
.qp{font-family:'Barlow Condensed';font-weight:700;font-size:32px;text-align:right}
.qg{font-size:22px;color:var(--grau);text-align:right}
.hin{font-size:24px;color:var(--grau);margin-top:20px;padding:0 6px}
"""
    return _html(body, css)


def geld_drei(spiele: list[dict], titel_unter: str = "heute Abend") -> str:
    """Bis zu 3 Spiele, bei denen das Geld am weitesten über der Quote liegt (gleiche Felder wie geld_spiel)."""
    karten = []
    for i, s in enumerate(spiele[:3], 1):
        an = datetime.fromisoformat(str(s["anpfiff"]).replace("Z", "+00:00")).astimezone(WIEN)
        name = s["heim"] if s["seite"] == "heim" else s["gast"] if s["seite"] == "gast" else "Remis"
        karten.append(
            f"<div class='k'><div class='kk'><span class='nr'>{i}</span><span class='sp'>{e(s['heim'])} – {e(s['gast'])}</span>"
            f"<span class='lz'>{e(s['liga'])} · {an:%H:%M}</span></div>"
            f"<div class='km'><div class='wer'>Geld auf<br><b>{e(name)}</b></div>"
            f"<div class='pz'>{s['anteil']:.0f}<small>%</small></div>"
            f"<div class='qz'><span>Quote {q2(s['quote'])}</span><b>{s['impl']:.0f} %</b><span>erwartet</span></div></div>"
            f"<div class='bal'><i class='g' style='width:{s['anteil']:.1f}%'></i><i class='mk' style='left:{s['impl']:.1f}%'></i></div>"
            f"<div class='kf'>{geld(s['gesamt'])} gematcht · {' · '.join(x['name'] for x in s.get('quellen') or [])}</div></div>")
    body = (f"<div class='seite'>{_kopf('Wo das Geld liegt', titel_unter)}"
            f"<div class='sub'>Die drei Spiele, bei denen das Geld am weitesten über der Quote liegt</div>"
            f"<div class='ks'>{''.join(karten)}</div>{FUSS}</div>")
    css = """
.sub{font-size:26px;color:var(--grau);margin:30px 0 26px;line-height:1.35}
.ks{display:flex;flex-direction:column;gap:22px}
.k{background:var(--karte);border-radius:26px;padding:26px 34px 22px}
.kk{display:flex;align-items:baseline;gap:16px;white-space:nowrap}
.nr{font-family:'Barlow Condensed';font-weight:800;font-size:34px;background:var(--gelb);border-radius:10px;padding:0 12px;line-height:44px}
.sp{font-family:'Barlow Condensed';font-weight:700;font-size:44px;overflow:hidden;text-overflow:ellipsis}
.lz{margin-left:auto;font-size:21px;color:var(--grau)}
.km{display:grid;grid-template-columns:1fr auto 170px;align-items:end;gap:20px;margin-top:8px}
.wer{font-size:23px;color:var(--grau);padding-bottom:16px;line-height:1.3}.wer b{color:var(--tinte);font-size:30px}
.pz{font-family:'Barlow Condensed';font-weight:800;font-size:132px;line-height:.85;letter-spacing:-2px}
.pz small{font-size:.42em;margin-left:3px;letter-spacing:0}
.qz{display:flex;flex-direction:column;align-items:flex-end;font-size:20px;color:var(--grau);padding-bottom:10px}
.qz b{font-family:'Barlow Condensed';font-weight:700;font-size:52px;color:var(--hell);line-height:1}
.bal{position:relative;height:18px;border-radius:9px;background:#EDEFF2;margin-top:16px}
.bal .g{position:absolute;left:0;top:0;bottom:0;border-radius:9px;background:var(--gelb)}
.bal .mk{position:absolute;top:-7px;bottom:-7px;width:4px;margin-left:-2px;background:var(--tinte);border-radius:2px}
.kf{font-size:19px;color:var(--grau);margin-top:12px}
"""
    return _html(body, css)


STORY_CSS = """
html,body{width:1080px;height:1920px;background:var(--bg)}
.st{position:relative;width:1080px;height:1920px;padding:230px 72px 0;display:flex;flex-direction:column}
.kopf .t{font-size:52px}
.marke{margin-top:70px;font-family:'Barlow Condensed';font-weight:700;font-size:44px;letter-spacing:.5px}
.marke span{background:var(--gelb);padding:4px 16px;border-radius:10px}
.betrag{font-family:'Barlow Condensed';font-weight:800;font-size:230px;line-height:.9;margin-top:22px;letter-spacing:-2px}
.auf{font-size:62px;font-weight:700;margin-top:14px;line-height:1.1}
.spiel{font-size:38px;color:var(--grau);margin-top:22px;line-height:1.35;font-weight:500}
.zwei{display:flex;gap:24px;margin-top:50px;flex-shrink:0}
.zwei>div{flex:1;background:var(--karte);border-radius:28px;padding:30px 34px}
.zwei .k{font-size:32px;color:var(--grau);font-weight:600}
.zwei .w{font-family:'Barlow Condensed';font-weight:800;font-size:84px;line-height:1;margin-top:10px}
.g{color:var(--gewinn)}.v{color:var(--verlust)}
.balken{flex-shrink:0;display:flex;height:84px;border-radius:18px;overflow:hidden;margin-top:24px;font-family:'Barlow Condensed';font-weight:700;font-size:33px}
.balken div{display:flex;align-items:center;padding:0 16px;white-space:nowrap;overflow:hidden}
.bew{margin-top:30px;background:var(--tinte);color:#fff;border-radius:20px;padding:22px 30px;font-size:34px;font-weight:600;line-height:1.3}
.bk{font-size:28px;color:var(--grau);margin-top:16px;font-weight:500}
.cliff{margin-top:auto;margin-bottom:46px;text-align:center}
.cliff .f{font-family:'Barlow Condensed';font-weight:800;font-size:96px;line-height:1}
.cliff .h{font-size:36px;color:var(--grau);margin-top:14px;font-weight:500}
.ende{height:150px;margin:0 -72px 200px;padding:0 72px;background:var(--tinte);color:#fff;display:flex;align-items:center;justify-content:space-between}
.ende .l{font-family:'Barlow Condensed';font-weight:700;font-size:50px}.ende .l b{color:var(--gelb)}
.ende .r{font-size:24px;color:#B9BEC5;text-align:right;line-height:1.45}
"""


def _usd(v):
    return "$" + f"{round(float(v)):,}".replace(",", ".")


def _bew_zeile(w):
    if not w.get("bewiesen"):
        return ""
    platz = f" (Platz {w['rang']})" if w.get("rang") else ""
    return f"<div class='bew'>⭐ Gehört zu den besten Fußball-Wallets auf Polymarket{platz}</div>"


def wal_story(w: dict, ergebnis: str | None = None) -> str:
    """Story 1080×1920, hell. w: {usd, seite, spiel, liga, anpfiff (ISO), preis (0..1), preise {name: p}, wallet, endstand}
    ergebnis None = Abend (offen), "gewonnen"/"verloren" = Auflösung am Morgen. Wenig Text, große Zahlen (Lucas 05.11.)."""
    ko = datetime.fromisoformat(w["anpfiff"].replace("Z", "+00:00")).astimezone(WIEN)
    plus = w["usd"] / w["preis"] - w["usd"]
    pr = sorted(w["preise"].items(), key=lambda x: (x[0] != w["seite"], -x[1]))
    su = sum(p for _, p in pr) or 1
    grau = ["#5E6672", "#8A929C"]
    balken = "".join(f"<div style='width:{100 * p / su:.1f}%;background:{'var(--gelb)' if n == w['seite'] else grau[(i - 1) % 2]};"
                     f"color:{'var(--tinte)' if n == w['seite'] else '#fff'}'>{e(n.split(' (')[0]) if p / su > .2 else ''} {round(100 * p)} %</div>"
                     for i, (n, p) in enumerate(pr))
    if ergebnis is None:
        oben = (f"<div class='marke'><span>{'⭐ Profi des Abends' if w.get('profi') else '🐋 Wal des Tages'}</span></div><div class='betrag'>{_usd(w['usd'])}</div>"
                f"<div class='auf'>auf {e(w['seite'])}</div>")
        mitte = (f"<div class='zwei'><div><div class='k'>Geht's auf</div><div class='w g'>+{_usd(plus)}</div></div>"
                 f"<div><div class='k'>Geht's schief</div><div class='w v'>−{_usd(w['usd'])}</div></div></div>")
        cliff = "<div class='cliff'><div class='f'>Geht das auf?</div><div class='h'>Auflösung morgen früh</div></div>"
        bk = "So sieht Polymarket das Spiel"
    else:
        gew = ergebnis == "gewonnen"
        oben = (f"<div class='marke'><span>🐋 Auflösung</span></div>"
                f"<div class='betrag {'g' if gew else 'v'}'>{'+' if gew else '−'}{_usd(plus if gew else w['usd'])}</div>"
                f"<div class='auf'>{'Gewonnen' if gew else 'Verloren'}: {_usd(w['usd'])} auf {e(w['seite'])}</div>")
        mitte = (f"<div class='zwei'><div><div class='k'>Endstand</div><div class='w'>{e(w.get('endstand') or '–')}</div></div></div>")
        cliff = "<div class='cliff'><div class='f' style='font-size:84px'>Heute Abend: der nächste Wal</div></div>"
        bk = "So sah Polymarket das Spiel vor Anpfiff"
    body = (f"<div class='st'>{_kopf('CocoBet', f'{TAGE[ko.weekday()]}, {ko.day}. {MONATE[ko.month - 1]}')}{oben}"
            f"<div class='spiel'>{e(w['spiel'])} · {ko:%H:%M}<br>{e(w.get('liga') or '')}</div>{mitte}"
            f"<div class='balken'>{balken}</div><div class='bk'>{bk}</div>"
            + _bew_zeile(w) +
            f"{cliff}<div class='ende'><div class='l'>t.me/<b>c0c0bet</b></div><div class='r'>Echtes Geld, echte Wetten<br>18+ · keine Wettempfehlung</div></div></div>")
    return _html(body, STORY_CSS)


FEED_WAL_CSS = """
html,body{height:1350px}
.st{height:1350px;padding:64px 64px 0}
.marke{margin-top:34px;font-size:38px}
.betrag{font-size:170px;margin-top:14px}
.auf{font-size:52px}
.spiel{font-size:32px;margin-top:14px}
.zwei{margin-top:30px}.zwei .w{font-size:68px}.zwei>div{padding:22px 28px}
.balken{height:70px;margin-top:20px}
.bew{margin-top:20px;font-size:28px;padding:16px 24px}
.cliff{margin-bottom:22px}.cliff .f{font-size:72px}.cliff .h{font-size:30px}
.ende{margin:0 -64px 0;padding:0 64px;height:120px}
"""


def wal_feed(w: dict) -> str:
    """Lucas 07.11. (Instagram stuft die Morgen-Rückblicke als „anzüglich" ein, Konto aus den Empfehlungen): der Wal
    wandert in den Feed (4:5), der Rückblick in die Story."""
    return wal_story(w).replace("</style>", FEED_WAL_CSS + "</style>", 1)


def story_huelle(bild: Path) -> str:
    """Ein fertiges 4:5-Feed-Bild als Story (9:16): mittig auf Markenfläche, unten der Kanal."""
    import base64 as _b
    b64 = _b.b64encode(Path(bild).read_bytes()).decode()
    css = """html,body{width:1080px;height:1920px;background:var(--bg)}
.h{position:relative;width:1080px;height:1920px;display:flex;flex-direction:column;align-items:center;justify-content:center}
.h img{width:1080px}"""
    return _html(f"<div class='h'><img src='data:image/jpeg;base64,{b64}'></div>", css)


def rendern(html_text: str, ziel: Path, hoehe: int = 1350):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1080, "height": hoehe}, device_scale_factor=1)
        pg.set_content(html_text, wait_until="load")
        pg.wait_for_timeout(300)
        pg.screenshot(path=str(ziel), type="jpeg", quality=92, full_page=False)
        b.close()


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "beispiel":
        aus = Path(sys.argv[2]); aus.mkdir(parents=True, exist_ok=True)
        d = json.loads((Path(__file__).resolve().parent / "beispiel.json").read_text(encoding="utf-8"))
        rendern(bilanz(datetime(2026, 11, 3), d["gestern"]), aus / "bilanz.jpg")
        rendern(geld_spiel(d["geld"]), aus / "geld.jpg")
        print("fertig:", aus)
