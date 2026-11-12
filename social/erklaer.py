#!/usr/bin/env python3
"""erklaer.py — drei Erklär-Folien „Was macht Cocobet?" (Lucas 12.11.2026, nach einer Insta-Frage
„habt ihr irgendwo eine Erklärseite?"). Ganz oberflächlich, je Folie eine Idee:

  1  Wir tippen nicht, wir folgen dem Geld — die drei Quellen
  2  Wie ein Signal entsteht — Geld-Anteil gegen das, was die Quote erwartet (Beispiel Serbien U19, Public 11.11.)
  3  Was unsere Posts bedeuten — die Post-Arten, und dass jedes Signal gezählt wird

1080×1350 im selben Stil wie die übrigen Insta-Grafiken (grafik.py). Sprache „de" (Instagram) oder „en" (X).
  python3 social/erklaer.py AUSGABEORDNER [de|en]
"""
from __future__ import annotations

import sys
from pathlib import Path

from grafik import FUSS, _html, _kopf, e, rendern

T = {
    "de": {
        "kopf": "So funktioniert Cocobet", "kopf_u": "Erklärt in drei Bildern", "fuss_r": "Wo das große Geld hinfließt<br>18+ · keine Wettempfehlung",
        "s1_u": "1 / 3 · Die Idee",
        "s1_h": "Wir tippen nicht.<br>Wir folgen dem Geld.",
        "s1_t": "Rund um die Uhr schauen wir auf drei Märkte, auf denen das große Geld sichtbar wird:",
        "quellen": [
            ("Betfair", "Wettbörse", "Wetter gegen Wetter, ohne Limits. Profis setzen hier große Beträge, und wir sehen jeden Euro, der neu dazukommt."),
            ("Polymarket", "Vorhersagemarkt", "Jede Wette ist öffentlich. Wir kennen die großen Wallets und wissen, wer bisher richtig lag."),
            ("Stake", "Krypto-Buchmacher", "Große Einsätze werden live angezeigt. Wir sehen, wo die Highroller gerade setzen."),
        ],
        "s1_f": "Landet auffällig viel Geld auf einer Seite, melden wir es — sofort.",
        "s2_u": "2 / 3 · Wie ein Signal entsteht",
        "s2_h": "Die Quote sagt das eine.<br>Das Geld sagt etwas anderes.",
        "s2_bsp": "Beispiel · Serbien U19 gegen Färöer U19 · gelber Strich = unser Signal",
        "s2_geld": "des neuen Geldes auf Serbien",
        "s2_quote": "erwartet die Quote 2,14",
        "s2_end": "Unser Signal kam beim Stand 0:1. Endstand 4:1 — Serbien hat das Spiel gedreht.",
        "s2_lg": "Gelb = Anteil am Geld · schwarzer Strich = was die Quote erwartet",
        "schritte": [
            ("Geld fließt", "Wir messen alle paar Minuten, wie viel neu auf jede Seite geht."),
            ("Vergleich mit der Quote", "Die Quote verrät, wie wahrscheinlich der Markt etwas hält."),
            ("Lücke groß genug?", "Erst wenn das Geld der Quote deutlich vorausläuft, kommt ein Signal."),
        ],
        "s3_u": "3 / 3 · Unsere Posts",
        "s3_h": "Was die Meldungen<br>bedeuten",
        "arten": [
            ("⚡", "Frisches Geld", "Viel neues Geld in kurzer Zeit auf eine Seite."),
            ("🔁", "Stetiger Zufluss", "Welle um Welle, immer auf dieselbe Seite."),
            ("🐋", "Wal", "Ein Polymarket-Wallet mit nachweisbarer Bilanz setzt groß."),
            ("🟢", "Highroller", "Mehrere große Stake-Wetten auf denselben Ausgang."),
            ("📊", "Rückblick", "Wie das Geld im Spiel floss — mit unserem Signal markiert."),
        ],
        "s3_f": "Jedes Signal wird gezählt, Treffer wie Nieten.<br>Die Bilanz steht offen auf unserer Seite.",
    },
    "en": {
        "kopf": "How Cocobet works", "kopf_u": "Explained in three pictures", "fuss_r": "Where the big money goes<br>18+ · not betting advice",
        "s1_u": "1 / 3 · The idea",
        "s1_h": "We don't tip.<br>We follow the money.",
        "s1_t": "Around the clock we watch three markets where big money becomes visible:",
        "quellen": [
            ("Betfair", "Betting exchange", "Bettor against bettor, no limits. Pros place big stakes here, and we see every new euro."),
            ("Polymarket", "Prediction market", "Every bet is public. We know the big wallets and who has been right so far."),
            ("Stake", "Crypto sportsbook", "Big bets are shown live. We see where the high rollers are going."),
        ],
        "s1_f": "When unusual money lands on one side, we flag it — right away.",
        "s2_u": "2 / 3 · How a signal is born",
        "s2_h": "The odds say one thing.<br>The money says another.",
        "s2_bsp": "Example · Serbia U19 vs Faroe Islands U19 · yellow line = our signal",
        "s2_geld": "of the new money on Serbia",
        "s2_quote": "is what odds of 2.14 expect",
        "s2_end": "Our signal came at 0:1. Final score 4:1 — Serbia turned it around.",
        "s2_lg": "Yellow = share of the money · black line = what the odds expect",
        "schritte": [
            ("Money flows", "Every few minutes we measure how much new money goes to each side."),
            ("Compare with the odds", "The odds tell you how likely the market thinks something is."),
            ("Gap big enough?", "Only when the money runs clearly ahead of the odds do we post a signal."),
        ],
        "s3_u": "3 / 3 · Our posts",
        "s3_h": "What our alerts<br>mean",
        "arten": [
            ("⚡", "Fresh money", "A lot of new money on one side in a short time."),
            ("🔁", "Steady flow", "Wave after wave, always on the same side."),
            ("🐋", "Whale alert", "A Polymarket wallet with a proven record bets big."),
            ("🟢", "High rollers", "Several big Stake bets on the same outcome."),
            ("📊", "Receipt", "How the money moved in the match — with our signal marked."),
        ],
        "s3_f": "Every signal is counted, wins and losses.<br>The record is public on our site.",
    },
}

# Beispiel für Folie 2: Serbien U19 – Färöer U19, 11.11. — Public-Signal 18:10 (50. Min, 0:1) auf Serbien @ 2,14, Endstand 4:1.
# Phasen aus dem Live-Stand von 23:31 (das 5-Min-Protokoll hält nur 6 h, darum erst ab der 34. Minute).
BSP_META = {"heim": "Serbia U19", "gast": "Faroe Islands U19", "anpfiff": "2026-11-11T16:00:00Z",
            "liga": "UEFA U19 Euro Qualifiers", "ft": [4, 1]}
BSP_Z = [["2026-11-11T16:34", "h", 413, 90, -11, [0, 0]], ["2026-11-11T16:39", "h", 5619, 95, -1316, [0, 0]],
         ["2026-11-11T16:44", "h", 7927, 99, 1340, [0, 0]], ["2026-11-11T16:49", "h", 2687, 83, -2724, [0, 1]],
         ["2026-11-11T16:54", "h", 3328, 87, 1094, [0, 1]], ["2026-11-11T16:59", "h", 1179, 87, 212, [0, 1]],
         ["2026-11-11T17:04", "h", 1722, 86, -229, [0, 1]], ["2026-11-11T17:09", "h", 9041, 90, -343, [0, 1]],
         ["2026-11-11T17:14", "h", 12213, 97, 1197, [1, 1]], ["2026-11-11T17:19", "h", 7322, 96, 847, [2, 1]],
         ["2026-11-11T17:24", "h", 16639, 99, -6655, [2, 1]], ["2026-11-11T17:29", "h", 3641, 99, -3995, [2, 1]],
         ["2026-11-11T17:34", "h", 152, 77, -3701, [4, 1]], ["2026-11-11T17:39", None, None, None, 1611, [4, 1]],
         ["2026-11-11T17:44", None, None, None, -11605, [4, 1]], ["2026-11-11T17:49", None, None, None, -41613, [4, 1]]]
BSP_SIG = {"zeit": "2026-11-11T17:10", "wo": "Telegram", "auswahl": "Serbia U19", "quote": 2.14}
BAND_DE = [(">Winner<", ">Sieger<"), (">Goals<", ">Tore<"), ("📣 Our signal", "📣 Unser Signal"), (" on Telegram", " auf Telegram"),
           ("✅ won", "✅ gewonnen"), ("❌ lost", "❌ verloren"), ("Serbia U19", "Serbien U19"), ("Faroe Islands U19", "Färöer U19"),
           ("@ 2.14", "@ 2,14"), ("></i>Draw<", "></i>Remis<"), ("></i>Over <", "></i>Über <"), ("></i>Under", "></i>Unter"),
           ("Each block = 5 minutes of new money on Betfair · stronger colour = more money · times CET",
            "Jeder Block = 5 Minuten neues Geld auf Betfair · kräftigere Farbe = mehr Geld · Uhrzeit MEZ")]


def band_bild(sprache):
    """Das „How the money moved"-Band des Beispiels, nur der Diagrammteil (ohne eigenen Kopf/Fuß), als data-URL."""
    import base64
    import io
    import tempfile

    import x_grafik
    from PIL import Image
    h = x_grafik.band(BSP_META, BSP_Z, BSP_SIG)
    if sprache == "de":
        for a, b in BAND_DE:
            h = h.replace(a, b)
    with tempfile.TemporaryDirectory() as tmp:
        roh = Path(tmp) / "band.jpg"
        rendern(h, roh, 675, 1200, 2)
        img = Image.open(roh).crop((60, 250, 2340, 1130))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=92)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


CSS = """
.zahl{font-family:'Barlow Condensed';font-weight:800}
.u2{font-size:24px;color:var(--grau);font-weight:600;margin-top:44px}
.h{font-family:'Barlow Condensed';font-weight:800;font-size:100px;line-height:.92;letter-spacing:-1.5px;margin-top:14px}
.txt{font-size:26px;color:var(--grau);line-height:1.4;margin-top:24px;max-width:900px}
.qs{display:flex;flex-direction:column;gap:14px;margin-top:24px}
.q{display:grid;grid-template-columns:250px 1fr;gap:26px;align-items:center;background:var(--karte);border-radius:22px;padding:20px 30px}
.q .n{font-family:'Barlow Condensed';font-weight:800;font-size:52px;line-height:1}
.q .art{font-size:20px;color:var(--grau);margin-top:6px;font-weight:600}
.q p{font-size:23px;line-height:1.38}
.f1{margin:28px 0 36px;font-size:30px;font-weight:600;line-height:1.35;border-left:8px solid var(--gelb);padding-left:22px}
.bsp{background:var(--karte);border-radius:24px;padding:30px 36px 34px;margin-top:40px}
.bsp .k{font-size:22px;color:var(--grau);font-weight:600}
.zz{display:flex;align-items:flex-end;justify-content:space-between;margin-top:18px}
.zz .z{font-family:'Barlow Condensed';font-weight:800;font-size:150px;line-height:.82;letter-spacing:-2px}
.zz .z small{font-size:.42em;letter-spacing:0;margin-left:2px}
.zz .z.leise{color:var(--grau);font-size:96px}
.zz .l{font-size:22px;color:var(--grau);margin-top:10px}
.zz .r{text-align:right}
.bal{position:relative;height:30px;border-radius:15px;background:#EDEFF2;margin-top:28px}
.bal .g{position:absolute;left:0;top:0;bottom:0;border-radius:15px;background:var(--gelb)}
.bal .mk{position:absolute;top:-12px;bottom:-12px;width:6px;margin-left:-3px;background:var(--tinte);border-radius:3px}
.lg{font-size:19px;color:var(--grau);margin-top:20px}
.bb{padding:20px 24px 22px;margin-top:26px}.bb img{display:block;width:100%;margin-top:12px;border-radius:10px}
.kz{font-size:21px;color:var(--grau);margin-top:10px}.kz b{color:var(--tinte);font-family:'Barlow Condensed';font-size:32px}
.end{font-size:22px;font-weight:600;margin-top:12px;padding-top:12px;border-top:2px solid #EDEFF2}
.st{display:flex;flex-direction:column;gap:16px;margin:30px 0}
.s{display:grid;grid-template-columns:62px 1fr;gap:22px;align-items:start}
.s .nr{font-family:'Barlow Condensed';font-weight:800;font-size:38px;background:var(--gelb);border-radius:12px;text-align:center;line-height:56px}
.s b{font-size:28px;display:block}
.s span{font-size:23px;color:var(--grau);line-height:1.35}
.st2{gap:10px;margin:22px 0 20px}.st2 .s{grid-template-columns:48px 1fr;gap:16px}.st2 .nr{font-size:30px;line-height:44px}.st2 b{font-size:23px}.st2 span{font-size:19px}
.ar{display:flex;flex-direction:column;gap:12px;margin-top:30px}
.a{display:grid;grid-template-columns:76px 1fr;gap:22px;align-items:center;background:var(--karte);border-radius:20px;padding:16px 26px}
.a .i{font-size:46px;text-align:center}
.a b{font-family:'Barlow Condensed';font-weight:700;font-size:40px;line-height:1;display:block}
.a span{font-size:22px;color:var(--grau);line-height:1.35;display:block;margin-top:5px}
"""


def _seite(t, inhalt):
    fuss = FUSS.split("<div class='r'>")[0] + f"<div class='r'>{t['fuss_r']}</div></div>"
    return _html(f"<div class='seite'>{_kopf(t['kopf'], t['kopf_u'])}{inhalt}{fuss}</div>", CSS)


def folie1(t):
    qs = "".join(f"<div class='q'><div><div class='n'>{e(n)}</div><div class='art'>{e(a)}</div></div><p>{e(p)}</p></div>"
                 for n, a, p in t["quellen"])
    return _seite(t, f"<div class='u2'>{e(t['s1_u'])}</div><div class='h'>{t['s1_h']}</div>"
                     f"<div class='txt'>{e(t['s1_t'])}</div><div class='qs'>{qs}</div><div class='f1'>{e(t['s1_f'])}</div>")


def folie2(t, sprache="de"):
    st = "".join(f"<div class='s'><div class='nr'>{i}</div><div><b>{e(b)}</b><span>{e(s)}</span></div></div>"
                 for i, (b, s) in enumerate(t["schritte"], 1))
    return _seite(t, f"<div class='u2'>{e(t['s2_u'])}</div><div class='h' style='font-size:76px'>{t['s2_h']}</div>"
                     f"<div class='bsp bb'><div class='k'>{e(t['s2_bsp'])}</div><img src='{band_bild(sprache)}' alt=''>"
                     f"<div class='kz'><b>87 %</b> {e(t['s2_geld'])} · <b>47 %</b> {e(t['s2_quote'])}</div>"
                     f"<div class='end'>✅ {e(t['s2_end'])}</div></div><div class='st st2'>{st}</div>")


def folie3(t):
    ar = "".join(f"<div class='a'><div class='i'>{i}</div><div><b>{e(n)}</b><span>{e(s)}</span></div></div>" for i, n, s in t["arten"])
    return _seite(t, f"<div class='u2'>{e(t['s3_u'])}</div><div class='h'>{t['s3_h']}</div>"
                     f"<div class='ar'>{ar}</div><div class='f1'>{t['s3_f']}</div>")


if __name__ == "__main__":
    ziel = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    sprachen = [sys.argv[2]] if len(sys.argv) > 2 else ["de", "en"]
    ziel.mkdir(parents=True, exist_ok=True)
    for sp in sprachen:
        for i, f in enumerate((folie1, folie2, folie3), 1):
            rendern(f(T[sp], sp) if f is folie2 else f(T[sp]), ziel / f"erklaer-{sp}-{i}.jpg", schaerfe=1)
            print(ziel / f"erklaer-{sp}-{i}.jpg")
