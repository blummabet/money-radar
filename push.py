#!/usr/bin/env python3
"""
push.py — Pushes in den Test-Channel (Lucas, 17.10.2026).

Liest die gerade gebaute site/data.json und schickt nur, was NEU ist, an TELEGRAM_TEST_CHAT_ID.
Vier Arten, von Lucas gewählt: Frischer Zufluss (Betfair), HZ 0:0, belegte Wale (Polymarket),
Stake-Burst. Was schon gesendet wurde, steht in .push_state/gesehen.json — die Datei lebt im
GitHub-Actions-Cache, nicht im Repo (keine Daten-Commits).

Erster Lauf ohne Gedächtnis: alles Aktuelle wird nur als gesehen markiert, nichts gesendet —
sonst kommt eine Flut alter Meldungen.

Aufruf:  python3 push.py            (sendet, wenn TELEGRAM_TOKEN + TELEGRAM_TEST_CHAT_ID gesetzt)
         python3 push.py --trocken  (zeigt nur, was gesendet würde)
"""
from __future__ import annotations

import html
import json
import os
import sys
import re
import urllib.parse
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import public as pubmod

BASIS = Path(__file__).resolve().parent
DATEN = BASIS / "site" / "data.json"
STAND = BASIS / ".push_state" / "gesehen.json"
SEITE = "https://blummabet.github.io/money-radar/"
JETZT = datetime.now(timezone.utc)

# Lucas 17.10.: Schwellen getrennt nach Top-5 und Rest, sonst kommt zu viel. Geeicht an 3,5 Tagen
# Betfair-Historie (größter Sieg-Markt-Zuwachs je Spiel): in den Top-5 kommen fast in JEDEM Spiel
# 50–100k in 30 Min dazu, im Rest liegt die Mitte bei unter 3k vor Anpfiff und ~2,4k live.
# Gezählt wird hier nur das Geld auf EINE Auswahl mit mind. 65 % des neuen Geldes im Markt.
SCHWELLE = {                  # (vor Anpfiff in 30 Min, live in 15 Min)
    True:  (50000, 100000),   # Top-5
    False: (10000, 15000),    # alle anderen Ligen
}
ZUFLUSS_MIN_EUR = SCHWELLE[False][0]
ZUFLUSS_PAUSE_H = 6          # dasselbe Spiel + dieselbe Seite höchstens alle 6 h
FRISCH_MIN = 45              # HZ/Stake-Meldungen, die älter sind, gelten als verpasst und werden nicht nachgeschickt
WAL_MIN_USD = 1000
STAKE_TOP5_MIN_USD = 50000   # Regel S5 (12.11.)
STAKE_TOP5_EINDEUTIG = {"UEFA Champions League", "UEFA Nations League", "LaLiga", "Ligue 1"}
WAL_MIN_USD_TOP5 = 3000      # in den Top-5 sind $1000 Alltag
WAL_MIN_FAKTOR = 0.25        # Wal-Regel 2 (06.11.): mindestens ¼ des üblichen Einsatzes
MAX_JE_LAUF = 12             # Notbremse gegen Spam
SERIE_ABSTAND_MIN = 30       # Lucas 18.10. (Igdir 7× und 8× im 5-Min-Abstand): dieselbe Serie frühestens nach 30 Min wieder


def ts(s):
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def e(t) -> str:
    return html.escape(str(t if t is not None else ""), quote=False)


def flagge(cc) -> str:
    cc = str(cc or "").upper()
    if cc == "UK":
        cc = "GB"
    if len(cc) == 2 and cc.isalpha():
        return "".join(chr(0x1F1E6 + ord(c) - 65) for c in cc) + " "
    return ""


def q2(v) -> str:
    return "–" if v is None else f"{float(v):.2f}".replace(".", ",")


def geld(v, z="€") -> str:
    if v is None:
        return "–"
    a = abs(float(v))
    if a >= 1e6:
        return f"{z}{a / 1e6:.1f}".replace(".", ",") + " Mio"
    if a >= 1e4:
        return f"{z}{round(a / 1e3)}k"
    if a >= 1e3:
        return f"{z}{a / 1e3:.1f}k".replace(".", ",")
    return f"{z}{round(a)}"


def anpfiff(iso) -> str:
    k = ts(iso)
    if not k:
        return ""
    m = round((k - JETZT).total_seconds() / 60)
    if m <= 0:
        return "läuft"
    if m < 60:
        return f"Anpfiff in {m} Min"
    wien = k.astimezone(ZoneInfo("Europe/Vienna"))
    return f"Anpfiff {wien:%H:%M}" if m < 18 * 60 else f"Anpfiff {wien:%d.%m. %H:%M}"


def remis(n) -> str:
    return "Remis" if str(n or "").lower().startswith(("the draw", "draw")) else str(n or "")


# ── die vier Arten ───────────────────────────────────────────────────────────────
def _auswahl_de(n):
    import re as _re
    m = _re.match(r"^(Over|Under) (\d+)\.5( Goals)?$", str(n or ""))
    if m:
        return f"{'Über' if m.group(1) == 'Over' else 'Unter'} {m.group(2)},5"
    return {"The Draw": "Remis", "Yes": "Ja", "No": "Nein"}.get(n, n)


def _markt_de(n):
    import re as _re
    n = str(n or "")
    m = _re.match(r"^Over/Under (\d)\.5 Goals$", n) or _re.match(r"^First Half Goals (\d)\.5$", n)
    if m:
        return ("HZ-Tore " if n.startswith("First") else "Über/Unter ") + f"{m.group(1)},5"
    return {"Match Odds": "Sieg-Markt", "Both teams to Score?": "Beide treffen", "Half Time": "HZ-Sieger",
            "Correct Score": "Ergebnis", "Half Time Score": "HZ-Ergebnis"}.get(n, n)


def pinnacle_zeile(d, z, fl):
    """Lucas 17.10.: „Pinnacle in die Push, sofern die Ligen Pini auch haben". Nur für den Sieg-Markt
    (Pinnacle-Quoten haben wir nur als 1X2). Wahrscheinlichkeit rauf = Quote fällt = zieht mit dem Geld."""
    if fl.get("markt") != "Match Odds":
        return ""
    p = next((x for x in d.get("pinnAlle") or [] if str(x.get("id")) == str(z.get("id"))), None)
    if not p:
        return ""
    a = str(fl.get("auswahl") or "")
    name = "Remis" if a.lower() in ("the draw", "draw") else a
    o = next((x for x in p.get("ausgaenge") or [] if x.get("name") == name), None)
    if not o or not o.get("quote"):
        return ""
    k = o.get("kurve") or []
    pp30 = round(k[-1] - k[-7], 1) if len(k) >= 7 else (round(k[-1] - k[0], 1) if len(k) >= 2 else None)
    if pp30 is None or abs(pp30) < 0.5:
        wie = "ruhig"
    elif pp30 > 0:
        wie = f"zieht mit ✅ ({pp30:+.1f} pp in 30 Min)".replace(".", ",")
    else:
        wie = f"geht dagegen ⚠️ ({pp30:+.1f} pp in 30 Min)".replace(".", ",")
    vorher = f", seit Beginn {q2(o['vorher'])} → {q2(o['quote'])}" if o.get("vorher") and abs((o.get("pp") or 0)) >= 0.5 else ""
    return f"📌 Pinnacle {q2(o['quote'])}: {wie}{vorher}\n"


def _beleg(art, z, fl, live=False):
    return {"art": art, "id": str(z.get("id")), "heim": z.get("heim"), "gast": z.get("gast"), "liga": z.get("liga"),
            "anpfiff": z.get("anpfiff"), "top5": bool(z.get("top5")), "live": live,
            "markt": fl.get("markt"), "auswahl": fl.get("auswahl"), "quote": fl.get("quote"), "eur": fl.get("eur")}


def _pbalken(pct):
    n = max(0, min(10, round((pct or 0) / 10)))
    return "▰" * n + "▱" * (10 - n)


def _dauer(mn):
    mn = int(mn or 0)
    return f"{mn} Min" if mn < 90 else f"{round(mn / 60)} Std."


def _live_von(d, sid):
    sp = next((x for x in (d.get("radar") or {}).get("spiele") or [] if str(x.get("id")) == str(sid)), None) or {}
    lv = sp.get("live") or {}
    return lv.get("min"), lv.get("stand")


def _mit(pub, eur=None, n=None):
    """02.11. (Public-Regel 4): Zahlen, nach denen der Topf die Kandidaten bewertet — Geld (€ bzw. $) und Kettenlänge."""
    if pub:
        pub["eur"] = float(eur) if isinstance(eur, (int, float)) else None
        pub["n"] = n
    return pub


def _pub(art, spiel, liga, quote, auswahl, *, roh=None, markt=None, anpfiff=None, live=False, minute=None, stand=None,
         sport=None, betrag=None, gruende=()):
    """Lucas 31.10.: Daten für die Public-Fassung (public.py) — nur das, was dort steht."""
    return {"art": art, "spiel": spiel, "liga": liga, "quote": quote, "auswahl": auswahl, "auswahlRoh": roh,
            "markt": None if markt in (None, "", "Match Odds", "Sieg-Markt") or str(markt).startswith("Über/Unter")
            else "1. Halbzeit" if str(markt).startswith("HZ-Tore") else markt, "anpfiff": anpfiff,
            "live": bool(live), "minute": minute, "stand": stand, "sport": sport, "betrag": betrag, "gruende": list(gruende)}


POLY_SCHATTEN_AB = "2026-11-09"
_TEAM_WEG = re.compile(r"\b(fc|cf|sc|ac|afc|cd|ca|fk|sk|bk|if|club|de|the|u\d\d)\b")


def _tn(x):
    x = _TEAM_WEG.sub(" ", str(x or "").lower())
    return re.sub(r"[^a-z0-9]", "", x)


def _team_gleich(a, b):
    a, b = _tn(a), _tn(b)
    return bool(a and b) and (a in b or b in a or a[:5] == b[:5])


def poly_schatten(m) -> dict:
    """Lucas 08.11. („so als würden wir auf Polymarket im Hintergrund mitspielen"): zu jedem gesendeten Public-Signal
    der Polymarket-Preis (bester Ask) und die Liquidität bis +2 Cent im Moment des Posts — oder warum es ihn nicht gab."""
    teile = re.split(r"\s+[–-]\s+", str(m.get("spiel") or ""), maxsplit=1)
    if len(teile) != 2:
        return {"fehlt": "Spiel nicht lesbar"}
    ausw = str(m.get("auswahlRoh") or m.get("auswahl") or "")
    if m.get("markt") or re.match(r"^(über|unter|over|under|beide|btts|ja|nein|yes|no)\b", ausw, re.I) or re.search(r"\d[.,]5", ausw):
        return {"fehlt": "Markt nicht auf Polymarket"}
    try:
        buch = json.loads((BASIS / "poly_buch.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"fehlt": "Polymarket-Daten fehlten"}
    h, g = teile
    for e in buch:
        if (_team_gleich(h, e["heim"]) and _team_gleich(g, e["gast"])) or (_team_gleich(h, e["gast"]) and _team_gleich(g, e["heim"])):
            if re.match(r"^(remis|unentschieden|draw|x)\b", ausw, re.I):
                seite = "remis"
            elif _team_gleich(ausw, e["heim"]) and not _team_gleich(ausw, e["gast"]):
                seite = "heim"
            elif _team_gleich(ausw, e["gast"]) and not _team_gleich(ausw, e["heim"]):
                seite = "gast"
            else:
                return {"fehlt": "Auswahl nicht zuzuordnen", "key": e["key"]}
            ask = (e.get("ask") or {}).get(seite)
            if not isinstance(ask, (int, float)) or not 0 < ask < 1:
                return {"fehlt": "kein Orderbuch", "key": e["key"]}
            alter = None
            t = ts(e.get("buchTs"))
            if t:
                alter = round((JETZT - t).total_seconds() / 60)
            return {"key": e["key"], "seite": seite, "ask": ask, "quote": round(1 / ask, 3),
                    "tiefe": (e.get("tiefe") or {}).get(seite), "alterMin": alter,
                    "label": (e.get("labels") or {}).get(seite), "anpfiff": e.get("anpfiff")}
    return {"fehlt": "Spiel nicht auf Polymarket"}


def liga_zeile(d, liga):
    """Lucas 19.10.: Liga-Bilanz in den Pushes — trägt hohes Geld in DIESER Liga bisher?"""
    lb = ((d.get("ligaBilanz") or {}).get("ligen") or {}).get(liga)
    if not lb:
        return ""
    h = lb["hoch"]
    marke = {"trägt": "✅ trägt", "trägt nicht": "⛔ trägt nicht"}.get(lb["urteil"], "noch offen")
    roi = ("+" if h["roi"] > 0 else "") + str(h["roi"]).replace(".", ",")
    return (f"📚 <i>Liga-Bilanz hohes Geld: {h['treffer']} % getroffen, Quote sagte {h['erwartet']} % · ROI {roi} % "
            f"· {h['n']} Märkte · <b>{marke}</b></i>\n")


def _quote_ok(q, q0, live=False):
    """Lucas 20.10. (Porto U19, Über 3,5: das Geld drückte die Quote von 1,63 auf 1,35 — kein Push, weil 1,35 < 1,40):
    vor Anpfiff zählt die Quote VOR dem Geldfluss. Drückt das Geld sie unter die Grenze, ist das ein Zeichen, kein
    Ausschluss. Live bleibt es bei der aktuellen Quote — dort fallen Quoten auch ohne Geld, allein durch die Spielzeit
    (Unter-Linien, Führende), und das wären keine Signale."""
    if not isinstance(q, (int, float)):
        return True
    if q >= SERIE_PUSH_MIN_QUOTE:
        return True
    return (not live) and isinstance(q0, (int, float)) and q0 >= SERIE_PUSH_MIN_QUOTE and q >= 1.15


def _drop_zeile(q, q0):
    """Markant, wenn das Geld die Quote gedrückt hat (mind. 3 %)."""
    if isinstance(q, (int, float)) and isinstance(q0, (int, float)) and q0 > 0 and q < q0 * 0.97:
        return f"🔻 <b>Quote gedrückt: {q2(q0)} → {q2(q)}</b> (−{round(100 * (1 - q / q0))} %)\n"
    return ""


def _in_serie(d, sid, markt, auswahl):
    """Lucas 18.10. (Avaí: Dauerzufluss UND Frischer Zufluss gleichzeitig, dieselben €11k): läuft auf dieselbe
    Auswahl schon eine Serie, meldet nur die Serie — sie zeigt mehr (Verlauf, wie oft in Folge)."""
    return any(str(x.get("id")) == str(sid) and x.get("markt") == markt and x.get("auswahl") == auswahl
               and _quote_ok(x.get("quote"), x.get("quoteStart"), bool(x.get("live")))
               for x in d.get("serien") or [])


def stat_zeile(d, sid):
    """Lucas 19.10. (APISPORTS_KEY im neuen Repo): Live-Statistik aus live_stats.py, wenn vorhanden."""
    x = (d.get("liveStats") or {}).get(str(sid)) or {}
    st = _hz_stat(x.get("statistik") or {}) if x.get("statistik") else None
    if not st:
        return ""
    minute = f" bis {x['min']}'" if x.get("min") else ""
    return f"📊 <b>Live-Stats</b>{minute}: {st}\n" + rot_zeile(d, sid)


def rot_zeile(d, sid):
    """Lucas 03.11. (Port FC – Beijing 72' 3:4, Beijing zu zehnt): Rote Karten sichtbar machen (API-Football: Anzahl je Team)."""
    x = (d.get("liveStats") or {}).get(str(sid)) or {}
    st = x.get("statistik") or {}
    r = [(st.get("heim") or {}).get("rot"), (st.get("gast") or {}).get("rot")]
    if not all(isinstance(v, (int, float)) for v in r) or not any(r):
        return ""
    sp = next((s for s in (d.get("radar") or {}).get("spiele") or [] if str(s.get("id")) == str(sid)), {})
    namen = [sp.get("heim") or "Heim", sp.get("gast") or "Gast"]
    teile = [f"{e(namen[i])} zu {'zehnt' if r[i] == 1 else 'neunt' if r[i] == 2 else str(11 - int(r[i]))}" for i in (0, 1) if r[i]]
    return f"🟥 <b>Rote Karte</b>: {' · '.join(teile)}\n"


def zufluss(d):
    """Lucas 17.10.: nicht das Geld aufs ganze Spiel, sondern auf WELCHE Auswahl es einseitig floss."""
    for z in d.get("zufluss30") or []:
        fl = (z.get("fluss") or [None])[0]
        if not fl or fl.get("eur", 0) < SCHWELLE[bool(z.get("top5"))][0]:
            continue
        if not _quote_ok(fl.get("quote"), fl.get("quoteVorher")):
            continue                    # Lucas 18.10.: Pushes erst ab Quote 1,40 — gemessen VOR dem Geldfluss (20.10.)
        if _in_serie(d, z.get("id"), fl.get("markt"), fl.get("auswahl")):
            continue
        key = f"zf|{z.get('id')}|{fl.get('markt')}|{fl.get('auswahl')}"
        q, qv = fl.get("quote"), fl.get("quoteVorher")
        drop = _drop_zeile(q, qv)
        richtung = "" if drop else (f" ↓ (war {q2(qv)})" if q < qv else f" ↑ (war {q2(qv)})") if q and qv and abs(q - qv) >= 0.02 else ""
        text = (f"🟡 <b>Frischer Zufluss</b> · Betfair\n"
                f"{flagge(z.get('land'))}<b>{e(z.get('heim'))} – {e(z.get('gast'))}</b>\n"
                f"{e(z.get('liga'))} · {anpfiff(z.get('anpfiff'))}\n\n"
                f"💶 <b>+{geld(fl.get('eur'))}</b> in 30 Min auf <b>{e(remis(_auswahl_de(fl.get('auswahl'))))}</b> @ <b>{q2(q)}</b>{richtung}\n"
                f"<i>= {fl.get('anteil')} % von allem, was in diesem Markt neu kam</i>\n"
                + drop + "\n"
                + _geld_block(fl, 30)
                + pinnacle_zeile(d, z, fl)
                + liga_zeile(d, z.get("liga")))
        gedr = isinstance(q, (int, float)) and isinstance(qv, (int, float)) and q < qv * 0.97
        pub = _pub("zufluss", f"{z.get('heim')} – {z.get('gast')}", z.get("liga"), q, remis(_auswahl_de(fl.get("auswahl"))),
                   roh=fl.get("auswahl"), markt=_markt_de(fl.get("markt")), anpfiff=z.get("anpfiff"),
                   gruende=[f"{_pbalken(fl.get('anteil'))} {fl.get('anteil')} % des neuen Geldes",
                            f"⚡ +{geld(fl.get('eur'))} in den letzten 30 Min" + (f" · Quote {q2(qv)} → {q2(q)}" if gedr else "")])
        _mit(pub, fl.get("eur"))
        pub["x"] = {"anteil": fl.get("anteil"), "min": 30, "qv": qv}
        yield key, text, ZUFLUSS_PAUSE_H, _beleg("zufluss", z, fl), pub


def live(d):
    """Lucas 17.10.: „Ja, inplay will ich auch" — einseitiges neues Geld in laufenden Spielen (15 Min).
    Je Spiel + Markt + Auswahl nur einmal (12 h Pause), entschiedene Spiele kommen gar nicht in die Liste."""
    tot = {str(s.get("id")) for s in (d.get("radar") or {}).get("spiele") or [] if s.get("entschieden") or s.get("klarVorn")}
    for z in d.get("liveZufluss") or []:
        if str(z.get("id")) in tot:
            continue                  # 24.10.: die Seite zeigt solche Spiele jetzt (Tor-Märkte), gepusht wird wie bisher nicht
        fl = (z.get("fluss") or [None])[0]
        if not fl or fl.get("eur", 0) < SCHWELLE[bool(z.get("top5"))][1]:
            continue
        if isinstance(fl.get("quote"), (int, float)) and fl["quote"] < SERIE_PUSH_MIN_QUOTE:
            continue
        if _in_serie(d, z.get("id"), fl.get("markt"), fl.get("auswahl")):
            continue
        lv = z.get("live") or {}
        st = lv.get("stand") or [0, 0]
        key = f"lz|{z.get('id')}|{fl.get('markt')}|{fl.get('auswahl')}"
        # Lucas 30.10. (RKC – Almere 70' 0:1, „+€17k auf Almere — und wieso kommt dann die Push?"): derselbe Filter wie beim
        # Dauerzufluss — Geld auf das Team, das schon führt, wird nicht gepusht, nur still gemessen (art „liveFuehrt").
        k_l = 0 if fl.get("auswahl") == z.get("heim") else 1 if fl.get("auswahl") == z.get("gast") else None
        if k_l is not None and st[0] is not None and st[1] is not None and st[k_l] > st[1 - k_l]:
            yield (f"lf|{z.get('id')}|{fl.get('markt')}|{fl.get('auswahl')}", None, None,
                   {**_beleg("liveFuehrt", z, fl, live=True), "stand": st, "minute": lv.get("min"), "lage": "vorn"})
            continue
        q, qv = fl.get("quote"), fl.get("quoteVorher")
        drop = _drop_zeile(q, qv)
        richtung = "" if drop else (f" ↓ (war {q2(qv)})" if q < qv else f" ↑ (war {q2(qv)})") if q and qv and abs(q - qv) >= 0.02 else ""
        minute = f"{lv.get('min')}'" if lv.get("min") is not None else "live"
        text = (f"🔴 <b>Live-Zufluss</b> · Betfair\n"
                f"{flagge(z.get('land'))}<b>{e(z.get('heim'))} – {e(z.get('gast'))}</b>\n"
                f"{e(z.get('liga'))} · {minute} · <b>{st[0]}:{st[1]}</b>\n\n"
                f"💶 <b>+{geld(fl.get('eur'))}</b> in 15 Min auf <b>{e(remis(_auswahl_de(fl.get('auswahl'))))}</b> @ <b>{q2(q)}</b>{richtung}\n"
                f"<i>= {fl.get('anteil')} % von allem, was in diesem Markt neu kam</i>\n"
                + drop
                + stat_zeile(d, z.get("id")) + "\n"
                + _geld_block(fl, 15)
                + liga_zeile(d, z.get("liga")))
        yield key, text, 12, _beleg("live", z, fl, live=True)


def _hz_q(q):
    """„@1,26 (79 %)" — Quote mit der Wahrscheinlichkeit, die sie sagt."""
    return f"@{q2(q)} <i>({round(100 / q)} %)</i>" if q else "–"


def _hz_k(v):
    return f"{v / 1000:.1f}K".replace(".", ",") if v >= 1000 else f"{round(v)}"


def _hz_zufluss(z):
    if not z:
        return None
    teile = []
    for kurz, lab in (("o15", "O/U 1,5"), ("o05", "O/U 0,5")):
        x = z.get(kurz)
        if not x:
            continue
        summe = (x.get("over") or 0) + (x.get("under") or 0)
        teile.append(f"{lab}: kein neues Geld" if summe < 1 else f"{lab}: €{_hz_k(summe)}, davon {round(100 * (x.get('overAnteil') or 0))} % auf Over")
    return f"💶 <b>Geld letzte {z.get('minuten')} Min:</b> " + " · ".join(teile) if teile else None


def _hz_stat(st):
    h, g = st.get("heim") or {}, st.get("gast") or {}
    teile = [f"{lab} {h[f]}–{g[f]}" for f, lab in (("aufsTor", "aufs Tor"), ("schuesse", "Schüsse"), ("ecken", "Ecken"),
                                                   ("gefAngriffe", "gef. Angriffe")) if f in h and f in g]
    if "ballbesitz" in h and "ballbesitz" in g:
        teile.append(f"Ballbesitz {round(h['ballbesitz'])}–{round(g['ballbesitz'])} %")
    if "xg" in h and "xg" in g:
        teile.append(f"xG {h['xg']:.2f}–{g['xg']:.2f}".replace(".", ","))
    return " · ".join(teile) or None


def _hz_serie(s):
    if not s:
        return "noch keine Spiele"
    if s.get("toreFuer") is not None and s.get("toreGegen") is not None:
        tore = f"Ø {s['toreFuer']:.1f}:{s['toreGegen']:.1f} Tore".replace(".", ",")
    else:
        tore = f"Ø {s.get('toreSchnitt') or 0:.1f} Tore".replace(".", ",")
    txt = f"{' '.join(s.get('form') or '')} · {tore} · O2,5 {s.get('over25')}/{s.get('n')}"
    if s.get("nHz"):
        txt += f" · Tor in 2. HZ {s.get('torIn2hz')}/{s['nHz']}"
    return txt


def halbzeit(d):
    """Lucas 17.10.: die HZ-Pushes „waren im alten sehr wichtig für mich" — darum im selben Aufbau wie
    dort (Vorher-Erwartung, Pausenquoten mit Wahrscheinlichkeit, Geld rund um die Pause, Statistik
    der 1. HZ, Form beider Teams). Je Spiel eine Nachricht."""
    for h in (d.get("hz") or {}).get("heute") or []:
        t = ts(h.get("zeit"))
        if not t or (JETZT - t).total_seconds() > FRISCH_MIN * 60:
            continue
        key = f"hz|{h.get('spiel')}|{t:%Y-%m-%d}"
        q, v, gr = h.get("quoten") or {}, h.get("vor") or {}, h.get("gruppen") or []
        wann = "zur Pause" if (h.get("phase") or "HZ") == "HZ" else f"{h.get('minute')}' · 2. HZ läuft schon"
        z = [f"⏸️ <b>HALBZEIT 0:0</b>\n",
             f"{flagge(h.get('land'))}<b>{e(h.get('spiel'))}</b>\n<i>{e(str(h.get('liga') or '')[:38])} · {wann}</i>\n\n"]
        erw = ([f"Over 2,5 {_hz_q(v.get('over25'))}"] if "tore" in gr and v.get("over25") else []) + \
              ([f"Heimsieg {_hz_q(v.get('heim'))}"] if "heim" in gr and v.get("heim") else []) + \
              ([f"Auswärtssieg {_hz_q(v.get('gast'))}"] if "gast" in gr and v.get("gast") else [])
        if erw:
            z.append(f"🎯 <b>Vorher:</b> {' · '.join(erw)}\n")
        jetzt = []
        if q.get("over05"):
            jetzt.append(f"Tor in 2. HZ <b>{_hz_q(q['over05'])}</b>")
        if q.get("over15"):
            jetzt.append(f"2+ Tore {_hz_q(q['over15'])}")
        # Lucas 20.10.: Sieg nur für den Favoriten vor Anpfiff — ein Underdog-Heimsieg ist kein Tipp
        if "heim" in gr and q.get("heim"):
            jetzt.append(f"<b>Heimsieg {_hz_q(q['heim'])}</b>")
        if "gast" in gr and q.get("gast"):
            jetzt.append(f"<b>Auswärtssieg {_hz_q(q['gast'])}</b> <i>(neu, wird gemessen)</i>")
        z.append("💰 <b>Jetzt:</b>\n   " + ("\n   ".join(jetzt) if jetzt else "–") + "\n")
        zt = _hz_zufluss(h.get("zufluss"))
        if zt:
            z.append(zt + "\n")
        st = _hz_stat(h.get("statistik") or {}) if h.get("statistik") else None
        if st:
            z.append(f"📊 <b>1. HZ:</b> {st}\n")
        # Lucas 18.10.: Chancen da, Tore nicht → eigenes Urteil, mit der bisherigen Bilanz genau dieser Gruppe
        ch = h.get("chancen")
        if ch:
            cb = ((d.get("hz") or {}).get("chancenBuch") or {}).get(ch) or {}
            sx = h.get("statistik") or {}
            mit_xg = (sx.get("heim") or {}).get("xg") is not None
            if ch == "hoch":
                z.append("🔥 <b>Chancen da, Tore nicht</b> — " + ("xG ≥ 1,0 in der 1. HZ" if mit_xg else "≥ 5 Schüsse aufs Tor") + "\n")
            else:
                z.append("🧊 <b>Wenig Chancen</b> — " + ("xG unter 1,0 in der 1. HZ" if mit_xg else "unter 5 Schüssen aufs Tor") + "\n")
            if cb.get("n"):
                roi = lambda v: ("+" if v > 0 else "") + str(v).replace(".", ",") + " %"
                z.append(f"   <i>bisher in dieser Gruppe: {cb['n']} Spiele, {cb['torPct']} % noch ein Tor"
                         + (f" · 2+ Tore ROI {roi(cb['o15Roi'])}" if cb.get("o15Roi") is not None else "")
                         + (" — noch wenige Spiele, nur ein Hinweis" if cb["n"] < 50 else "") + "</i>\n")
        s = h.get("serie") or {}
        if s.get("heim") or s.get("gast"):
            z.append(f"📈 <b>Form</b> (neueste zuerst)\n   {e(h.get('heim') or 'Heim')}: {_hz_serie(s.get('heim'))}\n"
                     f"   {e(h.get('gast') or 'Gast')}: {_hz_serie(s.get('gast'))}\n")
        b = next((x for x in (d.get("hz") or {}).get("bericht") or [] if "Tor in 2" in str(x.get("wette"))), None)
        if b and b.get("n"):
            z.append(f"\n🔬 <i>Buch „Tor in 2. HZ“: {b['n']} abgerechnet · getroffen {round(b.get('treffer') or 0)} %, "
                     f"Quote sagte {round(b.get('erwartet') or 0)} % · ROI {str(round(b.get('roi') or 0, 1)).replace('.', ',')} %</i>\n")
        yield key, "".join(z), None


def _hz_liga_zeile(a):
    """Lucas 25.10.: der HZ-Markt gegen das, was in dieser Liga bis Minute 30 üblich darauf liegt."""
    if not a.get("xLiga"):
        return ""
    return (f"📏 {str(a['xLiga']).replace('.', ',')}× so viel wie in dieser Liga üblich"
            f" <i>(sonst {geld(a.get('ligaNorm'))} bis Min. 30)</i>\n")


def bf_halbzeit(d):
    """Lucas 17.10.: die Betfair-HZ-Pushes aus dem alten Projekt („die sollen auch rein") —
    🔵 Halbzeit-Geld (einseitig) und ⚫ Fix-Verdacht. Erneut gemeldet erst, wenn das Geld um +50 % wuchs."""
    import math
    spiele_ = {str(s.get("id")): s for s in (d.get("radar") or {}).get("spiele") or []}
    for a in d.get("bfHtPushes") or []:
        # Lucas 09.11. 20:37 („bei Unter 1,5 in der 1. Halbzeit spiele ich dagegen das Over — ich gehe davon aus, das
        # ist gelayt"): still mitmessen, Über 0,5 und Über 1,5 1. HZ zur Betfair-Quote beim Push (einmal je Spiel)
        if str(a.get("markt")) == "First Half Goals 1.5" and str(a.get("auf") or "").lower().startswith("under"):
            sp_ = spiele_.get(str(a.get("id"))) or {}
            lv_ = sp_.get("live") or {}
            for mk_, aus_, art_ in (("First Half Goals 0.5", "Over 0.5 Goals", "htGegen05"), ("First Half Goals 1.5", "Over 1.5 Goals", "htGegen15")):
                m_ = next((m for m in sp_.get("maerkteDetail") or [] if m.get("name") == mk_), None)
                q_ = next((r.get("quote") for r in (m_ or {}).get("auswahl") or [] if r.get("name") == aus_), None)
                if isinstance(q_, (int, float)) and q_ > 1:
                    yield (f"hzg|{a.get('id')}|{art_}", None, None,
                           {"art": art_, "id": str(a.get("id")), "heim": a.get("heim"), "gast": a.get("gast"), "liga": a.get("liga"),
                            "anpfiff": a.get("anpfiff"), "top5": a.get("top5"), "live": bool(lv_), "markt": mk_, "auswahl": aus_,
                            "quote": q_, "stand": lv_.get("stand"), "minute": lv_.get("min"), "pushArt": a.get("art")})
        stufe = int(math.log(max(a.get("eur") or 1, 1)) / math.log(1.5))
        key = f"bfht|{a.get('art')}|{a.get('id')}|{a.get('markt')}|{a.get('auf')}|{stufe}"
        lv = a.get("live")
        st = (f"🔴 live {lv.get('min')}' · {lv['stand'][0] or 0}:{lv['stand'][1] or 0}" if lv
              else f"⏰ {anpfiff(a.get('anpfiff'))}")
        kopf = (f"{flagge(a.get('land'))}<b>{e(a.get('heim'))} – {e(a.get('gast'))}</b>\n"
                f"<i>{e(str(a.get('liga') or '')[:48])}</i>\n{st}\n\n")
        auf = remis(_auswahl_de(a.get("auf")))
        quote = f" @ <b>{q2(a.get('quote'))}</b>" if a.get("quote") else ""
        if a.get("art") == "fix":
            text = (f"⚫ <b>Betfair · Fix-Verdacht</b>\nmehr Geld auf die Halbzeit als aufs Spiel\n" + kopf
                    + f"💷 {e(_markt_de(a.get('markt')))}: <b>{geld(a.get('eur'))}</b>  vs  {e(_markt_de(a.get('ftMarkt')))}: {geld(a.get('ftEur'))}"
                      f" · <b>{str(a.get('faktor')).replace('.', ',')}×</b>\n"
                    + _hz_liga_zeile(a)
                    + _anteil_balken(auf, a.get("anteil"), quote.replace("<b>", "").replace("</b>", "") + (" · sehr einseitig" if (a.get("anteil") or 0) >= 90 else ""))
                    # Lucas 23.10.: bei niedriger Quote auf Über 0,5 spielt er die asiatische Linie 0,75 oder 1,0
                    + ("💡 Quote niedrig → asiatisch Über 0,75 oder 1,0 (1. HZ)\n"
                       if "0.5" in str(a.get("markt")) and str(a.get("auf") or "").lower().startswith("over")
                       and isinstance(a.get("quote"), (int, float)) and a["quote"] < 1.35 else ""))
        else:
            seiten = " · ".join(f"{e(remis(_auswahl_de(x['name'])))} {x['anteil']} %" for x in a.get("seiten") or [])
            text = (f"🔵 <b>Betfair · Halbzeit-Geld (einseitig)</b>\n" + kopf
                    + f"💷 {e(_markt_de(a.get('markt')))}: <b>{geld(a.get('eur'))}</b> gematcht\n"
                    + _hz_liga_zeile(a)
                    + _anteil_balken(auf, a.get("anteil"), quote.replace("<b>", "").replace("</b>", ""))
                    + (f"<i>{seiten}</i>\n" if seiten else ""))
        pub = _pub(a.get("art"), f"{a.get('heim')} – {a.get('gast')}", a.get("liga"), a.get("quote"), auf, roh=a.get("auf"),
                   markt=_markt_de(a.get("markt")), anpfiff=a.get("anpfiff"), live=bool(lv),
                   minute=(lv or {}).get("min"), stand=(lv or {}).get("stand"),
                   gruende=[f"📏 {str(a.get('faktor')).replace('.', ',')}× mehr Geld auf die 1. Halbzeit als aufs ganze Spiel"
                            if a.get("faktor") else "",
                            f"{_pbalken(a.get('anteil'))} {a.get('anteil')} % des Geldes in diesem Markt" if a.get("anteil") else ""])
        _mit(pub, a.get("eur"))
        yield key, text, None, {"art": a.get("art"), "id": str(a.get("id")), "heim": a.get("heim"), "gast": a.get("gast"),
                                "liga": a.get("liga"), "anpfiff": a.get("anpfiff"), "top5": bool(a.get("top")), "live": bool(lv),
                                "markt": a.get("markt"), "auswahl": a.get("auf"), "quote": a.get("quote"), "eur": a.get("eur")}, pub


SPORT = {"Fußball": "⚽", "E-Sport": "🎮", "Tennis": "🎾"}


X_EXTRA = {}          # 10.11.: Zusatz-Bausteine für die X-Fassung je Push-Schlüssel (nur im Lauf)
PINN_PUSH_MIN_QUOTE = 1.40
PINN_PAUSE_H = 3


def pinnacle(d):
    """Lucas 18.10.: Pinnacle-Drops aus dem eigenen Scan über alle Spiele — live (Team, das nicht führt,
    gewinnt ohne Tor) und frische Drops vor Anpfiff. Dasselbe Spiel + dieselbe Seite höchstens alle 3 h."""
    pd = d.get("pinnDrops") or {}
    import top3
    spiele = {str(s.get("id")): s for s in (d.get("radar") or {}).get("spiele") or []}
    for x, live in [(x, True) for x in pd.get("live") or []] + [(x, False) for x in pd.get("vor") or [] if x.get("frisch")]:
        if not x.get("quote") or x["quote"] < PINN_PUSH_MIN_QUOTE:
            continue
        # Lucas 25.10. („ein Pinnacle-Drop allein — keinen Plan, was ich damit mach. Sinn macht nur: Pinnacle dropt UND
        # Betfair oder Poly zieht mit — dann ist bei dem Spiel was"): gepusht wird nur noch mit Bestätigung durch Geld.
        sp = spiele.get(str(x.get("bfId") or ""))
        if not sp:
            continue
        mo = next((m for m in sp.get("maerkteDetail") or [] if m.get("name") == "Match Odds"), None)
        a = next((r for r in (mo or {}).get("auswahl") or []
                  if top3.seite_von(sp.get("heim"), sp.get("gast"), r.get("name")) == x.get("ausgang")), None)
        if not a:
            continue
        belege = []
        neu = a.get("neu") or 0
        top_neu = max([r.get("neu") or 0 for r in (mo or {}).get("auswahl") or []] or [0])
        if neu >= max(1000, 0.03 * (sp.get("eur") or 0)) and neu >= top_neu:
            belege.append(f"💶 Betfair: <b>+{geld(neu)}</b> neu auf {e(x['auf'])} in {sp.get('fensterMin') or 30} Min")
        if a.get("q60") and a.get("quote") and a["quote"] < a["q60"] * 0.97:
            belege.append(f"📉 Betfair-Quote zieht mit: {q2(a['q60'])} → <b>{q2(a['quote'])}</b>")
        pm = ((d.get("auchWoanders") or {}).get(str(sp.get("id"))) or {}).get("poly") or {}
        if (pm.get("usd") or 0) >= 1000 and top3.seite_von(sp.get("heim"), sp.get("gast"), pm.get("auf")) == x.get("ausgang"):
            belege.append(f"🟣 Polymarket: <b>+{geld(pm['usd'], '$')}</b> auf {e(x['auf'])} in 30 Min")
        if not belege:
            continue
        key = f"pd|{x['id']}|{x['ausgang']}|{'l' if live else 'v'}"
        kopf = (f"{flagge(x.get('land'))}<b>{e(x['heim'])} – {e(x['gast'])}</b>\n<i>{e(x.get('liga'))}</i> · "
                + (f"🔴 {x.get('min')}' · {x['stand'][0]}:{x['stand'][1]}" if live else f"⏰ {anpfiff(x.get('anpfiff'))}") + "\n\n")
        v = x.get("verlauf") or []
        lo, hi = (min(v), max(v)) if v else (0, 0)
        kurve = "".join("▁▂▃▄▅▆▇█"[round((w - lo) / (hi - lo) * 7)] for w in v) if hi > lo else ""
        if live:
            text = (f"📉 <b>Pinnacle · Live-Drop</b>\n" + kopf
                    + f"🎯 <b>{e(x['auf'])}</b>: {q2(x['vonQuote'])} → <b>{q2(x['quote'])}</b>\n"
                    + f"<i>Chance {str(x['vonPct']).replace('.', ',')} → {str(x['nachPct']).replace('.', ',')} % in {x['fensterMin']} Min, ohne Tor — "
                      f"{'bei Gleichstand' if x['stand'][0] == x['stand'][1] else 'obwohl hinten'}. Die Zeit allein würde die Chance senken.</i>\n"
                    + (f"<code>{kurve}</code> <i>Pinnacle-Chance{' je 5 Min' if live else ''}</i>\n" if len(kurve) >= 3 else ""))
        else:
            text = (f"📉 <b>Pinnacle · Drop vor Anpfiff</b>\n" + kopf
                    + f"🎯 <b>{e(x['auf'])}</b>: {q2(x['vonQuote'])} → <b>{q2(x['quote'])}</b>\n"
                    + f"<i>Chance {str(x['vonPct']).replace('.', ',')} → {str(x['nachPct']).replace('.', ',')} %, davon +{str(x['pp60']).replace('.', ',')} pp in der letzten Stunde</i>\n"
                    + (f"<code>{kurve}</code> <i>Pinnacle-Chance{' je 5 Min' if live else ''}</i>\n" if len(kurve) >= 3 else ""))
        text += "\n<b>Das Geld zieht mit:</b>\n" + "\n".join(belege) + "\n"
        q_bf = a.get("quote")
        # 10.11. (Lucas: „€44 on CD Pamplona … die Beträge sind irgendwas"): beim Pinnacle-Push ist der Kurs das Signal,
        # nicht der Betrag (oft zieht nur die Betfair-Quote mit). Für X die Bausteine des Telegram-Pushes mitgeben.
        X_EXTRA[key] = {"von": x.get("vonQuote"), "nach": x.get("quote"), "vonPct": x.get("vonPct"), "nachPct": x.get("nachPct"),
                        "pp60": x.get("pp60"), "min": x.get("fensterMin"), "verlauf": v, "bfNeu": neu,
                        "bfVon": a.get("q60") if a.get("q60") and a.get("quote") and a["quote"] < a["q60"] * 0.97 else None,
                        "bfNach": a.get("quote"), "poly": pm.get("usd") if (pm.get("usd") or 0) >= 1000 and
                        top3.seite_von(sp.get("heim"), sp.get("gast"), pm.get("auf")) == x.get("ausgang") else None,
                        "auf": x.get("auf"), "stand": x.get("stand") if live else None, "minute": x.get("min") if live else None}
        beleg = ({"art": "pinn", "id": str(sp.get("id")), "heim": sp.get("heim"), "gast": sp.get("gast"), "liga": sp.get("liga"),
                  "anpfiff": sp.get("anpfiff"), "top5": bool(sp.get("top5")), "live": live, "markt": "Match Odds",
                  "auswahl": a.get("name"), "quote": q_bf, "eur": neu}
                 if isinstance(q_bf, (int, float)) and q_bf >= PINN_PUSH_MIN_QUOTE else None)
        yield key, text, PINN_PAUSE_H, beleg


def _poly_topf(d, markt):
    """Das Spiel aus „Wo am meisten liegt", in dem dieser Polymarket-Markt steckt (Betfair/Pinnacle/Stake daneben)."""
    basis = re.sub(r"^(.*?\d{4}-\d{2}-\d{2})-.+$", r"\1", str(markt or ""))   # jeder Nebenmarkt → das Spiel (29.10.)
    return next((g for g in d.get("toepfe") or [] if ((g.get("q") or {}).get("poly") or {}).get("key") == basis), None)


def _seite_von(g, o):
    for w in g.get("wale") or []:
        if w.get("wallet") == o.get("wallet") and w.get("seite") == o.get("seite"):
            return w.get("k")
    n = str(o.get("seite") or "").lower()
    if n.startswith("draw"):
        return "remis"
    for k in ("heim", "gast"):
        t = str(g.get(k) or "").lower()
        if t and (t in n or n in t):
            return k
    return None


def _preis_balken(p, breite=12):
    n = max(0, min(breite, round(p * breite)))
    return "█" * n + "░" * (breite - n)


def wale(d):
    """Lucas 18.10. („Geht poly auch cooler?"): nicht nur Betrag + CLV-Zahl, sondern die drei Fragen, die zählen —
    WER setzt (Bilanz in Worten, wie überzeugt: Einsatz vs. sein Schnitt), WAS sagt der Markt (alle Preise als
    Balken, Betfair/Pinnacle-Quote daneben) und SIND ANDERE dabei (Geld der anderen Plattformen, weitere Wale)."""
    wallets = {w.get("wallet"): w for w in (d.get("poly") or {}).get("wallets") or []}
    for o in (d.get("poly") or {}).get("setzen") or []:
        top5 = str(o.get("markt") or "").split("-")[0] in {"epl", "bun", "lal", "sea", "fl1", "lig1", "ucl", "unl"}
        if (o.get("usd") or 0) < (WAL_MIN_USD_TOP5 if top5 else WAL_MIN_USD):
            continue
        key = f"wal|{o.get('wallet')}|{o.get('markt')}|{o.get('seite')}"
        w = wallets.get(o.get("wallet")) or {}
        g = _poly_topf(d, o.get("markt"))
        k = _seite_von(g, o) if g else None
        neben_m = re.match(r"^.*?\d{4}-\d{2}-\d{2}-(.+)$", str(o.get("markt") or ""))
        nebenmarkt = bool(neben_m)
        seite = remis(_pm_label(o.get("seite")) if nebenmarkt else o.get("seite"))
        if nebenmarkt and seite in ("Over", "Under"):
            seite = "Über" if seite == "Over" else "Unter"      # Linie liefert der Track nicht mit
            if "corners" in neben_m.group(1):
                seite += " (Ecken)"
        preis = o.get("jetzt") if o.get("jetzt") is not None else o.get("preis")
        ein = o.get("einstieg") or o.get("preis")
        usd = float(o.get("usd") or 0)
        # 12.11. (Lucas: „$4,3k auf Remis" bei 99¢ = Quote 1,01 — „wertlos wegen Quote", das Spiel ist offenbar abgesagt
        # und der Markt praktisch entschieden): ab 90¢ (Quote ≤ 1,11) kein Wal-Push, weder Einstieg noch aktueller Preis.
        if max(float(preis or 0), float(ein or 0)) >= 0.90:
            continue

        # Kopf
        titel = e(o.get("spiel"))
        if g and not nebenmarkt:
            titel = f"{e(g.get('heim'))} – {e(g.get('gast'))}"
        liga = str((g or {}).get("liga") or "")
        liga = "" if liga.isupper() else e(liga)          # „ESPORTS"/„SOCCER" aus Polymarket sagt nichts
        zeilen = [f"🐋 <b>Wal-Einstieg · {geld(usd, '$')} auf {e(seite)}</b>",
                  f"{SPORT.get(o.get('sport'), '')} <b>{titel}</b>" + (f"\n<i>{flagge((g or {}).get('land'))}{liga}</i>" if liga else ""),
                  f"🕒 {anpfiff(o.get('anpfiff'))}", ""]

        # Was der Markt sagt: alle Poly-Preise als Balken, die Wal-Seite markiert, Quote daneben
        pp = ((g or {}).get("q") or {}).get("poly", {}).get("preise") or {} if not nebenmarkt else {}
        namen = {"heim": (g or {}).get("heim"), "remis": "Remis", "gast": (g or {}).get("gast")}
        reihe = [(sk, pp[sk]) for sk in ("heim", "remis", "gast") if isinstance(pp.get(sk), (int, float))]
        if k and reihe and any(sk == k for sk, _ in reihe):
            zeilen.append("<b>Polymarket-Preise</b>")
            for sk, p in reihe:
                nm = str(namen[sk] or "")[:12].ljust(12)
                z = f"{e(nm)} {_preis_balken(p)} {round(p * 100):>2}¢"
                zeilen.append(f"<b><code>{z} ◀</code></b>" if sk == k else f"<code>{z}</code>")
            zeilen.append("")
        if preis:
            bew = ""
            if ein and abs(preis - ein) >= 0.01:
                bew = f" · seit Einstieg {round(ein * 100)}→{round(preis * 100)}¢ {'↑' if preis > ein else '↓'}"
            zeilen.append(f"💵 <b>{round(preis * 100)}¢</b> = Quote <b>{q2(1 / preis)}</b>{bew}")
        # Gegencheck an den anderen Börsen (nur Sieg-Markt)
        if g and k and not nebenmarkt:
            vgl = []
            bq = (((g.get("q") or {}).get("betfair") or {}).get("quoten") or {}).get(k)
            if bq:
                vgl.append(f"Betfair {q2(bq)}")
            pin = next((x for x in d.get("pinnAlle") or [] if str(x.get("id")) == str(g.get("id"))), None)
            po = next((a for a in (pin or {}).get("ausgaenge") or [] if a.get("name") == namen[k]), None)
            if po and po.get("quote"):
                vgl.append(f"Pinnacle {q2(po['quote'])}")
            if vgl:
                besser = bq and preis and 1 / preis > bq * 1.01
                zeilen.append("⚖️ " + " · ".join(vgl) + (" → Poly zahlt mehr" if besser else ""))
        mgr = (((g or {}).get("q") or {}).get("poly") or {}).get("betrag") if not nebenmarkt else None
        if mgr and usd and float(mgr) > 0:              # heutige Marktgröße, nicht die beim Einstieg
            ant = round(100 * min(usd, float(mgr)) / float(mgr))
            if ant >= 10:
                zeilen.append(f"🏋️ das sind <b>{ant} %</b> des ganzen Marktes")
        zeilen.append("")

        # Wer setzt
        n, pnl, schnitt = o.get("n") or w.get("fensterN"), o.get("pnl") or w.get("pnl"), o.get("schnittUsd") or w.get("schnittUsd")
        wer = f"🧠 <b>Wallet {e(str(o.get('wallet'))[2:6])}</b>"
        if o.get("rang") and o.get("rangVon"):
            wer += f" · Nr. {o['rang']} von {o['rangVon']} belegten"
        zeilen.append(wer)
        bil = []
        if n:
            bil.append(f"{n} Wetten")
        if pnl is not None:
            bil.append(("+" if pnl >= 0 else "−") + geld(abs(pnl), "$"))
        bil.append(f"schlägt den Schlusskurs um Ø {q2(o.get('clv'))} pp (sicher ≥ {q2(o.get('clvUg'))})")
        zeilen.append("   " + " · ".join(bil))
        if schnitt and usd:
            f = usd / float(schnitt)
            if f >= 1.5:
                zeilen.append(f"   🔥 setzt sonst Ø {geld(schnitt, '$')} — diesmal <b>{q2(f)}×</b> so viel")
            elif f <= 0.5:
                zeilen.append(f"   setzt sonst Ø {geld(schnitt, '$')} — diesmal eher klein")

        # Sind andere dabei
        if g and k:
            mit, gegen = [], []
            for x in g.get("wale") or []:
                if x.get("wallet") == o.get("wallet") or not x.get("k"):
                    continue
                (mit if x["k"] == k else gegen).append(x)
            andere = []
            for qn, label, z in (("betfair", "Betfair", "€"), ("stake", "Stake", "$")):
                q = (g.get("q") or {}).get(qn) or {}
                sv = q.get("seiten") or {}
                summe = sum(sv.values())
                if summe >= 3000:
                    pc = round(100 * (sv.get(k) or 0) / summe)
                    andere.append(f"{label} {pc} % " + ("✅" if pc >= 55 else "⚠️ dagegen" if pc <= 35 else "≈ geteilt"))
            if andere or mit or gegen:
                zeilen.append("")
                zeilen.append(f"🤝 <b>Geld auf {e(namen.get(k) or seite)} anderswo</b>")
                if andere:
                    zeilen.append("   " + " · ".join(andere))
                if mit or gegen:
                    t = []
                    if mit:
                        t.append(f"+{len(mit)} belegte{'r' if len(mit) == 1 else ''} Wal{'e' if len(mit) > 1 else ''} mit ({geld(sum(x['usd'] for x in mit), '$')})")
                    if gegen:
                        t.append(f"⚠️ {len(gegen)} dagegen ({geld(sum(x['usd'] for x in gegen), '$')})")
                    zeilen.append("   " + " · ".join(t))

        mk = urllib.parse.quote(re.sub(r"^(.*?\d{4}-\d{2}-\d{2})-.+$", r"\1", str(o.get("markt") or "")))
        zeilen.append("")
        zeilen.append(f'<a href="https://polymarket.com/event/{mk}">Markt öffnen</a> · '
                      f'<a href="https://polymarket.com/profile/{e(o.get("wallet"))}">Wallet ansehen</a>')
        # Lucas 20.10.: Wal-Pushes ins eigene Buch (vorher rechnete nur das alte Projekt sie ab)
        beleg = ({"art": "wal", "id": o.get("markt"), "titel": o.get("spiel"), "heim": None, "gast": None,
                  "liga": (g or {}).get("liga"), "sport": o.get("sport") or "Fußball", "anpfiff": o.get("anpfiff"),
                  "markt": "Polymarket", "auswahl": o.get("seite"), "anzeige": seite, "quote": round(1 / preis, 3),
                  "preis": preis, "usd": round(usd), "wallet": o.get("wallet"), "live": False,
                  "faktor": round(usd / float(schnitt), 2) if schnitt and usd else None,   # 02.11.: Einsatz ÷ üblicher Einsatz
                  "top5": top5} if preis and 0 < preis < 1 else None)
        gr = []
        if pnl and pnl > 0:
            gr.append(f"🏆 Wallet mit +{geld(pnl, '$')} Gewinn")
        if schnitt and usd and usd / float(schnitt) >= 1.5:
            gr.append(f"🔥 setzt sonst Ø {geld(schnitt, '$')}, diesmal {q2(usd / float(schnitt))}× so viel")
        pub = (_pub("wal", (f"{g.get('heim')} – {g.get('gast')}" if g and not nebenmarkt else str(o.get("spiel") or "")),
                    (g or {}).get("liga"), round(1 / preis, 2), seite, roh=o.get("seite"), anpfiff=o.get("anpfiff"),
                    sport=o.get("sport"), betrag=pubmod.geld(usd, "$"), gruende=gr) if preis and 0 < preis < 1 else None)
        if pub:
            pub["faktor"] = round(usd / float(schnitt), 2) if schnitt and usd else None     # Public-Regel 3
            _mit(pub, usd)
            # 10.11. (Lucas: X-Post „lieblos"): dieselben Bausteine wie der Telegram-Push, für die englische X-Fassung
            pub["x"] = {"bars": [[str(namen[sk] or ""), round(p, 3), sk == k] for sk, p in reihe] if (k and reihe) else [],
                        "pnl": pnl, "n": n, "clv": o.get("clv"), "preis": preis, "ein": ein,
                        "faktor": round(usd / float(schnitt), 2) if schnitt and usd else None, "slug": o.get("markt")}
        # Wal-Regel 2 (Lucas 06.11. 13:50, Dynamo Dresden: $1,1k bei einem Wallet, das sonst Ø $17k setzt): unter ¼ des
        # üblichen Einsatzes kein Push — still als „walKlein" mitbuchen (bis 06.11.: unter ¼ n=13, −39 %)
        if schnitt and usd and usd / float(schnitt) < WAL_MIN_FAKTOR:
            if beleg:
                yield f"wk|{o.get('wallet')}|{o.get('markt')}|{o.get('seite')}", None, None, {**beleg, "art": "walKlein"}
            continue
        yield key, "\n".join(zeilen) + "\n", None, beleg, pub


def _stake_fuehrt(g, auswahl, markt, radar):
    """Liegt die Stake-Auswahl (Sieg-Markt) live gerade vorn? → Beleg fürs Schattenbuch, sonst None. REIN."""
    if not auswahl or str(markt or "").lower() not in ("match result", "1x2", "winner", "match winner", "match odds", "fulltime result"):
        return None
    teile = [x.strip().lower() for x in str(g.get("spiel") or "").split(" - ")]
    if len(teile) != 2:
        return None
    for sp in radar:
        h, gg = str(sp.get("heim") or "").lower(), str(sp.get("gast") or "").lower()
        if not (h[:5] and gg[:5] and h[:5] in teile[0] and gg[:5] in teile[1]):
            continue
        st = (sp.get("live") or {}).get("stand")
        if not st or st[0] is None or st[1] is None:
            return None
        a = str(auswahl).lower()
        k = 0 if h[:5] in a else 1 if gg[:5] in a else None
        if k is None or st[k] <= st[1 - k]:
            return None
        return {"art": "stakeFuehrt", "id": str(sp.get("id")), "heim": sp.get("heim"), "gast": sp.get("gast"), "liga": sp.get("liga") or g.get("liga"),
                "anpfiff": sp.get("anpfiff") or g.get("anpfiff"), "top5": bool(sp.get("top5")), "live": True, "markt": "Match Odds",
                "auswahl": sp.get("heim") if k == 0 else sp.get("gast"), "stand": st, "lage": "vorn"}
    return None


def _stake_team(gruppe, radar, t0):
    """Lucas 03.11. (Platense, Deportivo: Team gewann, Spiel-Burst-Paket verlor): still mitmessen, ob „dem Team folgen"
    trägt — Sieg-Markt bei Betfair zur Quote beim Erkennen, art „stakeTeam". Nur Fußball (Betfair-Radar). REIN."""
    z = next((x for x in sorted(gruppe, key=lambda x: -(x.get("usd") or 0)) if "Spiel-Burst" in str(x.get("art") or "")), None)
    if not z or not z.get("auswahl"):
        return None
    teile = [x.strip().lower() for x in str(z.get("spiel") or "").split(" - ")]
    if len(teile) != 2:
        return None
    a = str(z["auswahl"]).lower()
    for sp in radar:
        h, gg = str(sp.get("heim") or "").lower(), str(sp.get("gast") or "").lower()
        if not (h[:5] and gg[:5] and h[:5] in teile[0] and gg[:5] in teile[1]):
            continue
        k = "heim" if (h[:5] in a or teile[0][:5] in a) else "gast" if (gg[:5] in a or teile[1][:5] in a) else None
        q = (sp.get("quoten") or {}).get(k) if k else None
        if not isinstance(q, (int, float)) or q <= 1:
            return None
        lv = sp.get("live") or {}
        return (f"tm|{z.get('spiel')}|{t0:%Y-%m-%dT%H:%M}", None, None,
                {"art": "stakeTeam", "id": str(sp.get("id")), "heim": sp.get("heim"), "gast": sp.get("gast"),
                 "liga": sp.get("liga") or z.get("liga"), "anpfiff": sp.get("anpfiff") or z.get("anpfiff"),
                 "top5": bool(sp.get("top5")), "live": z.get("phase") == "live", "markt": "Match Odds",
                 "auswahl": sp.get(k), "quote": q, "usd": z.get("usd"), "maerkte": z.get("markt"),
                 "stand": lv.get("stand"), "minute": lv.get("min")})
    return None


def _bf_sieg_quote(spiel, auswahl, markt, radar):
    """Betfair-Quote (Sieg-Markt) für eine Stake-Auswahl ohne eigene Quote — nur Team-Sieg oder Remis. REIN."""
    # 07.11. 17:30 (Lucas: „Stake trifft so gut, kommt aber nie in den Kanal"): auch Pakete aus mehreren Märkten
    # („Asian Handicap, Match Result, …") — gemessen als stakeTeam: 33 Tipps, 67 % bei 50 % erwartet, +37,7 %.
    # Nur Märkte, die eindeutig KEIN Team-Tipp sind (Tore, Ecken …), bleiben ohne Quote.
    if markt and not re.search(r"match result|1x2|winner|match odds|asian handicap|draw no bet|double chance|handicap",
                               str(markt), re.I):
        return None
    teile = [x.strip().lower() for x in str(spiel or "").split(" - ")]
    if len(teile) != 2:
        return None
    a = str(auswahl or "").lower()
    for sp in radar:
        h, gg = str(sp.get("heim") or "").lower(), str(sp.get("gast") or "").lower()
        if not (h[:5] and gg[:5] and h[:5] in teile[0] and gg[:5] in teile[1]):
            continue
        k = ("remis" if a in ("draw", "remis", "unentschieden", "x") else
             "heim" if (h[:5] in a or teile[0][:5] in a) else "gast" if (gg[:5] in a or teile[1][:5] in a) else None)
        q = (sp.get("quoten") or {}).get(k) if k else None
        return q if isinstance(q, (int, float)) and q > 1 else None
    return None


def _stake_beleg(g, z, auswahl, quote, radar):
    """10.11. (Stake-Check, Lucas „ja"): den GESENDETEN Stake-Push selbst ins Buch — genau diese Auswahl zu genau dieser
    Quote. Fußball mit Betfair-Spiel und Sieg-/Tor-Markt: über unsere Ergebnisse; sonst (E-Sport, Sondermärkte): über die
    abgerechnete Stake-Wette derselben Auswahl. Vorher maß die Statistik das Gesamtergebnis des Wals. REIN."""
    if not auswahl:
        return None
    markt = str(z.get("markt") or "").strip()
    nur_sieg = "Spiel-Burst" in str(z.get("art") or "") or "," in markt
    basis = {"art": "stake", "liga": g.get("liga"), "anpfiff": g.get("anpfiff"), "live": g.get("phase") == "live",
             "quote": quote if isinstance(quote, (int, float)) else None, "usd": g.get("usd"), "faktor": g.get("faktor"),
             "sport": g.get("kat"), "stakeArt": z.get("art"), "stakeMarkt": markt or None, "minute": g.get("minute")}
    teile = [x.strip().lower() for x in str(g.get("spiel") or "").split(" - ")]
    a = str(auswahl).strip()
    if (g.get("kat") or "Fußball") == "Fußball" and len(teile) == 2:
        for sp in radar:
            h, gg = str(sp.get("heim") or "").lower(), str(sp.get("gast") or "").lower()
            if not (h[:5] and gg[:5] and h[:5] in teile[0] and gg[:5] in teile[1]):
                continue
            bf = None
            if nur_sieg or re.search(r"^(1x2|match result|match winner|winner|fulltime result|match odds)$", markt, re.I):
                al = a.lower()
                k = ("The Draw" if al in ("draw", "remis", "x", "unentschieden") else
                     sp.get("heim") if (h[:5] in al or teile[0][:5] in al) else sp.get("gast") if (gg[:5] in al or teile[1][:5] in al) else None)
                if k:
                    bf = ("Match Odds", k)
            else:
                m_ = re.match(r"^(over|under)\s+(\d+\.5)$", a, re.I)
                if m_ and re.search(r"^(total|total goals|asian total|over/under|goals over/under)$", markt, re.I):
                    bf = (f"Over/Under {m_.group(2)} Goals", f"{m_.group(1).capitalize()} {m_.group(2)} Goals")
            if bf:
                return {**basis, "id": str(sp.get("id")), "heim": sp.get("heim"), "gast": sp.get("gast"),
                        "anpfiff": sp.get("anpfiff") or g.get("anpfiff"), "top5": bool(sp.get("top5")),
                        "markt": bf[0], "auswahl": bf[1], "liga": sp.get("liga") or g.get("liga")}
            break
    if not z.get("betIds"):
        return None
    hg = [x.strip() for x in str(g.get("spiel") or "").split(" - ")]
    return {**basis, "id": f"stake:{z.get('eventId') or g.get('spiel')}", "heim": hg[0] if len(hg) == 2 else g.get("spiel"),
            "gast": hg[1] if len(hg) == 2 else "", "markt": markt or None, "auswahl": a,
            "stakeBets": list(z.get("betIds") or []), "nurSieg": nur_sieg}


def stake(d):
    """Lucas 18.10. (Brighton 3:0: Push „$127k @ 1,48, live" — die $127k und 1,48 waren der Burst von VOR dem Spiel,
    ausgelöst hat ein neuer $17k-Spiel-Burst bei 3:0): je Push nur die Signale, die zusammen kamen (≤ 5 Min), mit
    IHREN Zahlen. Führt ein Team bei Betfair unter 1,40, oder liegt die Quote des Bursts unter 1,40 → kein Push."""
    sig = []
    for z in (d.get("stake") or {}).get("heute") or []:
        t = ts(z.get("zeit"))
        if t and (JETZT - t).total_seconds() <= FRISCH_MIN * 60:
            sig.append((t, z))
    sig.sort(key=lambda x: x[0], reverse=True)
    radar = (d.get("radar") or {}).get("spiele") or []

    def klar_vorn(spiel):
        teile = [x.strip().lower() for x in str(spiel or "").split(" - ")]
        if len(teile) != 2:
            return False
        for sp in radar:
            h, g = str(sp.get("heim") or "").lower(), str(sp.get("gast") or "").lower()
            if h[:5] and g[:5] and h[:5] in teile[0] and g[:5] in teile[1]:
                return bool(sp.get("klarVorn") or sp.get("entschieden"))
        return False

    def jetzt_live(spiel):
        """Lucas 03.11. (Ryukyu – Gifu: Push sagte 16', im Spiel stand es schon 30' und 1:2): die Stake-Daten kommen
        über den Mac-Runner bis ~15 Min verspätet an. Darum steht der aktuelle Stand vom Betfair-Radar dabei."""
        teile = [x.strip().lower() for x in str(spiel or "").split(" - ")]
        if len(teile) != 2:
            return None
        for sp in radar:
            h, g = str(sp.get("heim") or "").lower(), str(sp.get("gast") or "").lower()
            if h[:5] and g[:5] and h[:5] in teile[0] and g[:5] in teile[1]:
                lv = sp.get("live") or {}
                st = lv.get("stand")
                if isinstance(lv.get("min"), (int, float)) and isinstance(st, list) and len(st) == 2:
                    return int(lv["min"]), st
        return None

    def stake_top5(z):
        teile = [x.strip().lower() for x in str(z.get("spiel") or "").split(" - ")]
        if len(teile) == 2:
            for sp in radar:
                h, g_ = str(sp.get("heim") or "").lower(), str(sp.get("gast") or "").lower()
                if h[:5] and g_[:5] and h[:5] in teile[0] and g_[:5] in teile[1]:
                    return bool(sp.get("top5"))
        return str(z.get("liga") or "") in STAKE_TOP5_EINDEUTIG

    benutzt = set()
    for t0, z0 in sig:
        if id(z0) in benutzt:
            continue
        gruppe = [z for t, z in sig if z.get("spiel") == z0.get("spiel") and abs((t - t0).total_seconds()) <= 300]
        for z in gruppe:
            benutzt.add(id(z))
        g = max(gruppe, key=lambda z: z.get("usd") or 0)
        tm = _stake_team(gruppe, radar, t0)
        if tm:
            yield tm
        mit_auswahl = next((z for z in sorted(gruppe, key=lambda z: -(z.get("usd") or 0)) if z.get("auswahl")), None)
        auswahl = (mit_auswahl or {}).get("auswahl")
        quote = (mit_auswahl or {}).get("quote") or g.get("quote")
        quelle_q = "stake"
        if not quote and auswahl:
            # Public-Regel 10 (Lucas 07.11.): 83 von 123 Stake-Signalen kamen ohne Quote → nie im Kanal, nie abgerechnet.
            # Sieg-Markt: die Betfair-Quote derselben Auswahl nehmen (wie bei stakeTeam).
            quote = _bf_sieg_quote(g.get("spiel"), auswahl, (mit_auswahl or g).get("markt"), radar)
            quelle_q = "betfair" if quote else "stake"
        if (isinstance(quote, (int, float)) and quote < 1.40) or (g.get("phase") == "live" and klar_vorn(g.get("spiel"))):
            continue
        # REGEL S5 (Lucas 12.11., Italy – France $47k in 5 Wetten: „die Stake-Bursts in den Top-Ligen sind sonst zu einfach,
        # da spielen viele hohe Beträge"): in den Top-5 inkl. Champions League und Nations League erst ab $50k — dieselbe
        # Grenze wie beim Stake-Dauerzufluss dort. Liganamen bei Stake sind mehrdeutig („Premier League", „Bundesliga",
        # „Serie A" gibt es mehrfach), darum zuerst das Betfair-Spiel fragen, sonst nur die eindeutigen Namen.
        if (g.get("usd") or 0) < STAKE_TOP5_MIN_USD and stake_top5(g):
            continue
        # Lucas 31.10. (Accrington – Northampton 40' 1:0, $18k auf Accrington @ 1,40: „Macht keinen Sinn, oder?"): REGEL S2 —
        # wie beim Betfair-Zufluss seit 30.10.: Live-Geld auf das Team, das gerade führt (egal wie knapp), wird nicht gepusht,
        # nur still gemessen (art „stakeFuehrt", Sieg-Markt beim Betfair-Spiel). Auswertung mit den anderen am 13.11.
        fs = _stake_fuehrt(g, auswahl, (mit_auswahl or g).get("markt"), radar) if g.get("phase") == "live" else None
        if fs:
            yield f"sf|{g.get('spiel')}|{t0:%Y-%m-%dT%H:%M}", None, None, {**fs, "quote": quote, "usd": g.get("usd"), "minute": g.get("minute")}
            continue
        # Lucas 18.10. (Málaga – Sociedad: Torlinien-Burst von 20:01 kam um 20:30, Spiel schon aus): live ist ein
        # Burst nach 10 Min Geschichte — die Stake-Datei des alten Projekts kommt manchmal 20–30 Min verspätet an.
        # 19.10.: 15 statt 10 Min — der Seitenbau sieht einen Burst erst mit dem nächsten Stake-Lauf (bis ~10 Min nach
        # Erkennung); mit 10 Min fiel z. B. Al Fayha – Al Ahli (Über, $42k, 16:49) knapp durch und kam nie.
        if g.get("phase") == "live" and (JETZT - t0).total_seconds() > 15 * 60:
            continue
        # Lucas 20.10. (Fenerbahçe – Slavia: Spiel-Burst kam ~10 Min nach Abpfiff, „live"): Fußball ist ~110 Min nach
        # Anpfiff aus. Kam der Burst so spät oder ab der 88. Minute, ist es Schluss-/Abrechnungsgeld, kein Signal.
        ko = ts(g.get("anpfiff"))
        minute = g.get("minute")
        # 10.11. (Stake-Check): 2 von 3 Live-Zeilen kamen ohne Anpfiff/Minute → Schlussgeld rutschte durch (bei denen mit
        # Anpfiff lagen 19 von 67 über 100 Min nach Anpfiff). Fehlt beides, die Minute beim Geld aus dem Betfair-Stand schätzen.
        if not ko and not isinstance(minute, (int, float)) and g.get("phase") == "live":
            jl0 = jetzt_live(g.get("spiel"))
            if jl0:
                minute = jl0[0] - (JETZT - t0).total_seconds() / 60
        if (g.get("kat") or "Fußball") == "Fußball" and g.get("phase") == "live" and (
                (ko and ((JETZT - ko).total_seconds() > 110 * 60 or (t0 - ko).total_seconds() > 100 * 60))
                or (isinstance(minute, (int, float)) and minute >= 88)):
            continue
        arten = []
        for z in gruppe:
            if z.get("art") and z["art"] not in arten:
                arten.append(z["art"])
        key = f"st|{g.get('spiel')}|{t0:%Y-%m-%dT%H:%M}"
        phase = ("live" + (f" {g['minute']}'" if g.get("minute") else "")) if g.get("phase") == "live" else "vor Anpfiff"
        if g.get("phase") == "live":
            alt_min = (JETZT - t0).total_seconds() / 60
            jl = jetzt_live(g.get("spiel"))
            if alt_min >= 4:
                phase += f" (Geld um {t0.astimezone(ZoneInfo('Europe/Vienna')):%H:%M}, vor {alt_min:.0f} Min)"
            if jl:
                phase += f"\n⏱ jetzt {jl[0]}' · Stand {jl[1][0]}:{jl[1][1]}"
        text = (f"🟢 <b>Stake · {e(' + '.join(arten))}</b>\n"
                f"<b>{e(g.get('spiel'))}</b>\n{e(g.get('liga'))} · {phase}\n\n"
                f"💵 <b>{geld(g.get('usd'), '$')}</b> in {g.get('wetten')} Wetten" + (f" auf <b>{e(auswahl)}</b>" if auswahl else "")
                + (f" @ {q2(quote)}" + (" (Betfair-Quote)" if quelle_q == "betfair" else "") if quote else "") + "\n"
                + (f"📏 {q2(g.get('faktor'))}-mal so viel wie in dieser Liga üblich\n" if g.get("faktor") else "")
                + (f"🗂 {e((mit_auswahl or g).get('markt'))}\n" if (mit_auswahl or g).get("markt") else ""))
        mk = (mit_auswahl or g).get("markt")
        pub = _pub("stake", str(g.get("spiel") or "").replace(" - ", " – "), g.get("liga"), quote, auswahl,
                   markt=None if quelle_q == "betfair" or str(mk or "").lower() in ("match result", "1x2", "winner", "match winner")
                   else re.sub(r"\s*-\s*Twoway$", "", str(mk or "")) or None,
                   anpfiff=g.get("anpfiff"), live=g.get("phase") == "live", minute=g.get("minute"), sport=g.get("kat"),
                   betrag=pubmod.geld(g.get("usd"), "$"),
                   gruende=[f"🎯 {g.get('wetten')} Großwetten innerhalb weniger Minuten",
                            f"📏 {q2(g.get('faktor'))}× so viel wie in dieser Liga üblich" if g.get("faktor") else ""]) if auswahl else None
        _mit(pub, g.get("usd"))
        if pub:
            pub["x"] = {"wetten": g.get("wetten"), "faktor": g.get("faktor")}
        try:
            beleg = _stake_beleg(g, mit_auswahl or g, auswahl, quote, radar)
        except Exception as exc:  # noqa: BLE001
            print(f"  stake-beleg: {exc}")
            beleg = None
        yield key, text, None, beleg, pub


def _geld_block(fl, minuten):
    """Lucas 18.10. („immer noch verwirrend"): statt drei Prozentzahlen EIN Bild — der ganze Markt je Auswahl
    als Balken mit Betrag, das neue Geld dahinter. Die Auswahl mit dem Zufluss ist fett."""
    vt = fl.get("marktVerteilung") or []
    if not vt:
        return _anteil_balken(remis(_auswahl_de(fl.get("auswahl"))), fl.get("anteil"), " des neuen Geldes")
    mg = sum(x[1] for x in vt) or 1
    breite = max(len(remis(_auswahl_de(x[0]))[:14]) for x in vt)
    zeilen = []
    for name, vol, quote, neu in vt:
        n = max(0, min(10, round(10 * vol / mg)))
        lab = remis(_auswahl_de(name))[:14].ljust(breite)
        # Lucas 22.10. („liegen 16k auf Über 2,5 oder 13k+12k?"): der Betrag ist der STAND, das neue Geld steckt darin
        z = f"{lab} {'▓' * n}{'░' * (10 - n)} {geld(vol):>6}" + (f" (davon {geld(neu)} neu)" if neu >= 500 else "")
        zeilen.append(z)
    kopf = f"🏦 So liegt der <b>{e(_markt_de(fl.get('markt')))}</b> jetzt ({geld(mg)} im ganzen Markt):\n"
    return kopf + "<code>" + e("\n".join(zeilen)) + "</code>\n"


def _poly_geld_block(d, x, lab):
    """Lucas 09.11. (Phantom – ILLYRIANS: „$9,3k — ist das jetzt viel oder wenig? Keinen Plan, wie viel gesamt auf dem
    Markt liegt"): wie beim Betfair-Push der ganze Markt je Seite als Balken, das neue Geld dahinter.
    Quelle: offene Positionen je Seite (Halter) aus poly_buch.json; sonst nur das gehandelte Volumen."""
    key = str(x.get("id"))
    neu = float(x.get("usd") or 0)
    pf = next((m for m in (d.get("polyFluss") or {}).get("maerkte") or [] if str(m.get("key")) == key), {})
    gesamt = float(pf.get("usd") or 0)
    try:
        e_ = next((b for b in json.loads((BASIS / "poly_buch.json").read_text(encoding="utf-8")) if str(b.get("key")) == key), None)
    except Exception:  # noqa: BLE001
        e_ = None
    gesamt = gesamt or float((e_ or {}).get("betrag") or 0)
    seiten = {(e_.get("labels") or {}).get(k, k): float(v or 0) for k, v in ((e_ or {}).get("seiten") or {}).items() if float(v or 0) >= 1} if e_ else {}
    # Lucas 09.11. 20:41 (Tecnico – Manta, Über 2,5 auf „More Markets"): Nebenmärkte haben keine Halter-Positionen —
    # dann die Käufe je Seite aus dem Polymarket-Fluss (so weit die Abfrage zurückreicht), nur dieselbe Linie
    if not (seiten and sum(seiten.values()) >= neu) and pf.get("alle"):
        alle = {str(k): float(v or 0) for k, v in pf["alle"].items() if float(v or 0) >= 1}
        m_ = re.search(r"\(([^)]*)\)\s*$", str(x.get("auswahl") or ""))
        if m_:
            alle = {k: v for k, v in alle.items() if k.endswith(f"({m_.group(1)})")}
        if alle and sum(alle.values()) >= neu:
            summe = sum(alle.values())
            reihen = sorted(alle.items(), key=lambda kv: -kv[1])[:4]
            name = lambda n: _pm_label(n)[:16]
            breite = max(len(name(n)) for n, _ in reihen)
            zeilen = []
            for n, v in reihen:
                k = max(0, min(10, round(10 * v / summe)))
                z = f"{name(n).ljust(breite)} {'▓' * k}{'░' * (10 - k)} {geld(v, '$'):>6}"
                if n == str(x.get("auswahl")) and neu >= 500:
                    z += f" (davon {geld(neu, '$')} neu)"
                zeilen.append(z)
            rw = pf.get("alleReichweiteMin")
            zeit = (f"letzte {round(rw / 60)} h" if rw and rw >= 90 else f"letzte {round(rw)} Min" if rw else "zuletzt")
            return (f"🏦 So liegt der Markt ({zeit}: {geld(summe, '$')} gekauft"
                    + (f", {geld(gesamt, '$')} gehandelt" if gesamt > summe * 1.05 else "") + "):\n"
                    + "<code>" + e("\n".join(zeilen)) + "</code>\n")
    if seiten and sum(seiten.values()) >= neu:
        summe = sum(seiten.values())
        reihen = sorted(seiten.items(), key=lambda kv: -kv[1])
        kurz = lambda n: "Remis" if str(n).lower().startswith("draw") else re.sub(r"\s+(A?FC|CF|CE|SC)\b", "", str(n))[:16]
        breite = max(len(kurz(n)) for n, _ in reihen)
        zeilen = []
        for n, v in reihen:
            k = max(0, min(10, round(10 * v / summe)))
            z = f"{kurz(n).ljust(breite)} {'▓' * k}{'░' * (10 - k)} {geld(v, '$'):>6}"
            if str(n) in (str(lab), str(x.get("auswahl"))) and neu >= 500:
                z += f" (davon {geld(neu, '$')} neu)"
            zeilen.append(z)
        kopf = (f"🏦 So liegt der Markt jetzt ({geld(summe, '$')} in offenen Positionen"
                + (f", {geld(gesamt, '$')} gehandelt" if gesamt > summe else "") + "):\n")
        return kopf + "<code>" + e("\n".join(zeilen)) + "</code>\n"
    if gesamt:
        return (f"🏦 Im ganzen Markt bisher <b>{geld(gesamt, '$')}</b> gehandelt — die {geld(neu, '$')} der letzten "
                f"{x.get('dauerMin')} Min sind <b>{max(1, round(100 * neu / gesamt))} %</b> davon\n")
    return ""


def _wie_einseitig(pct):
    p = pct or 0
    return (f"praktisch das ganze ({p} %)" if p >= 95 else f"fast das ganze ({p} %)" if p >= 85
            else f"der Großteil ({p} %)")


def _markt_gesamt(fl):
    """„🏦 Sieg-Markt gesamt €48k · davon auf RKC Waalwijk €31k (65 %)"."""
    mg, ag = fl.get("marktGesamt"), fl.get("auswahlGesamt")
    if not mg:
        return ""
    return (f"🏦 {e(_markt_de(fl.get('markt')))} gesamt <b>{geld(mg)}</b>"
            + (f" · davon {e(remis(_auswahl_de(fl.get('auswahl'))))} {geld(ag)} ({round(100 * ag / mg)} %)" if ag else "") + "\n")


def _anteil_balken(name, pct, zusatz=""):
    """Lucas 18.10.: „📊 Over 3.5 Goals ▓▓▓▓▓▓▓░░░ 71 %" — der Balken aus dem alten Push, in allen Pushes."""
    if pct is None:
        return ""
    n = max(0, min(10, round(pct / 10)))
    return f"📊 {e(name)}  <code>{'▓' * n}{'░' * (10 - n)}</code> <b>{round(pct)} %</b>{zusatz}\n"


def _pm_label(t):
    """Polymarket-Etikett lesbar machen — dieselben Regeln wie pmLabel auf der Seite."""
    import re as _re
    t = str(t or "")
    m = _re.match(r"^(Over|Under) \(O/U (\d+)\.5\)$", t)
    if m:
        return f"{'Über' if m.group(1) == 'Over' else 'Unter'} {m.group(2)},5"
    m = _re.match(r"^(.+?) \((.+?) \((-?\d+(?:\.\d+)?)\)\)$", t)
    if m:
        h = float(m.group(3)) * (1 if m.group(1) == m.group(2) else -1)
        return f"{m.group(1)} {'+' if h > 0 else '−'}{str(abs(h)).replace('.', ',')}"
    if t.lower().startswith("gegen both teams to score"):
        return "Beide treffen: Nein"
    if t.lower().startswith("both teams to score"):
        return "Beide treffen: Ja"
    return _re.sub(r"^gegen Draw.*$", "gegen Remis", _re.sub(r"^Draw.*$", "Remis", t))


def _balken(werte):
    """▁▂▃▅▇ — die Säulen als Text für Telegram."""
    zeichen, mx = "·▁▂▃▄▅▆▇█", max(werte or [0])
    return "".join(zeichen[0 if not v else max(1, round(v / mx * 8))] for v in werte) if mx > 0 else ""


SERIE_PUSH_MIN_QUOTE = 1.40   # Lucas 17.10. (Bayern 1,30): viel Geld auf den klaren Favoriten ist der Normalfall — anzeigen ja, pushen nein


def _stufe(n):
    return 3 if n < 5 else 5 if n < 8 else 8 if n < 12 else 12


def _tor_zeile(x):
    """Zeile „Tor während der Serie" (18.10.; 03.11. REGEL S3: live zählt oben nur das Geld seit dem letzten Tor)."""
    t = x.get("tor")
    if not t:
        return ""
    kopf = f"⚽ <b>Tor während der Serie</b> ({t['von'][0]}:{t['von'][1]} → {t['nach'][0]}:{t['nach'][1]}): "
    if x.get("eur_vorTor") is not None:
        return (kopf + f"oben nur das Geld seit dem {t['nach'][0]}:{t['nach'][1]} — davor kamen weitere "
                f"{geld(x['eur_vorTor'])} im Schnitt @ {q2(t['quoteVorher'])} (zählt nicht)\n")
    if t.get("seitStand") is not None:
        return (kopf + f"<b>{geld(t['seitStand'])}</b> seit dem {t['nach'][0]}:{t['nach'][1]}, "
                f"{geld(x['eur'] - t['seitStand'])} kamen davor im Schnitt @ {q2(t['quoteVorher'])}\n")
    return kopf + f"{geld(t['vorTor'])} kamen vorher, {geld(t['nachTor'])} danach\n"


def _zeitlich(x) -> str:
    """Lucas 07.11. (Boca – Defensa 45', „€57k in 75 Min"): die Summe las sich wie gerade eben gekommen, kam aber großteils
    rund um den Anpfiff. Deshalb: wann das Geld kam — Beginn, die letzten 15 Min und der größte Abruf."""
    if x.get("letzte15") is None:
        return ""
    wien = lambda i: ts(i).astimezone(ZoneInfo("Europe/Vienna")).strftime("%H:%M") if ts(i) else "?"
    teile = [f"seit {wien(x.get('seit'))}", f"letzte 15 Min <b>{geld(x['letzte15'])}</b>"]
    sp = x.get("spitze")
    if sp and sp[0]:
        teile.append(f"größter Schub {geld(sp[0])} um {wien(sp[1])}")
    return "⏱ " + " · ".join(teile) + "\n"


def serie(d):
    """Lucas 17.10.: wiederholter Zufluss auf dieselbe Auswahl („bei jedem Scan wieder 10.000") ist ein
    eigener KPI. Push ab 3 Abrufen in Folge, wieder bei 5, 8 und 12 — nicht bei jedem neuen Treffer."""
    tot_s = {str(s.get("id")) for s in (d.get("radar") or {}).get("spiele") or [] if s.get("entschieden") or s.get("klarVorn")}
    # REGEL S4 (07.11.): abgeebbte Serien still ins Buch — einmal je Auswahl, zur Quote von jetzt
    for x in d.get("serienRuhig") or []:
        if str(x.get("id")) in tot_s or not x.get("quote"):
            continue
        yield (f"sr|{x['id']}|{x['markt']}|{x['auswahl']}", None, None,
               {"art": "serieRuhig", "id": x["id"], "heim": x["heim"], "gast": x["gast"], "liga": x.get("liga"),
                "anpfiff": x.get("anpfiff"), "top5": x.get("top5"), "live": bool(x.get("live")), "markt": x["markt"],
                "auswahl": x["auswahl"], "quote": x["quote"], "eur": x["eur"], "n": x["n"],
                "letzte15": x.get("letzte15"), "schnitt15": x.get("schnitt15")})
    for x in d.get("serien") or []:
        if str(x.get("id")) in tot_s:
            continue                  # 24.10.: auf der Seite sichtbar, gepusht wird wie bisher nicht
        if not _quote_ok(x.get("quote"), max([v for v in [x.get("quoteStart")] + [f.get("quoteVorher") for z_ in d.get("zufluss30") or []
                         if str(z_.get("id")) == str(x["id"]) for f in z_.get("fluss") or [] if f.get("auswahl") == x["auswahl"]]
                         if isinstance(v, (int, float))], default=None), bool(x.get("live"))):
            continue
        # Lucas 03.11. (Kobe – Ratchaburi: €241k „@ 1,96", das Geld kam großteils bei 1:0 zu ~1,20, dann 1:1 — „Punkt 1
        # einbauen"): REGEL S3 — live zählt nur das Geld SEIT DEM LETZTEN TOR. Ein Tor macht ein anderes Spiel; Geld davor
        # steht nur als Hinweis dabei. Seit dem Tor braucht es wieder 3 Abrufe in Folge und die Mindestsumme.
        if x.get("eurGesamt") is not None:          # REGEL S3 (build.serien): Zahlen schon seit dem letzten Tor
            x = {**x, "eur_vorTor": x["eurGesamt"] - x["eur"], "quoteStart": None}
        key = f"se|{x['id']}|{x['markt']}|{x['auswahl']}|{_stufe(x['n'])}" + (f"|{x['tor']['nach'][0]}:{x['tor']['nach'][1]}" if x.get("eur_vorTor") is not None else "")
        q, q0 = x.get("quote"), x.get("quoteStart")
        # Lucas 30.10. (Elversberg 32' 2:0 @ 1,40: „da kommt viel rein, das so Schema hat — filtern wir raus, messen im
        # Hintergrund"): live Dauerzufluss auf ein Team, das schon FÜHRT, wird nicht gepusht, sondern nur still ins Buch
        # geschrieben (art „serieFuehrt"). Nach ~2 Wochen vergleichen mit den gepushten Serien.
        sp_l = next((sp for sp in (d.get("radar") or {}).get("spiele") or [] if sp.get("id") == x["id"]), None) or {}
        st_l = (sp_l.get("live") or {}).get("stand")
        k_l = 0 if x["auswahl"] == x["heim"] else 1 if x["auswahl"] == x["gast"] else None
        if x.get("live") and st_l and st_l[0] is not None and k_l is not None and st_l[k_l] > st_l[1 - k_l]:
            yield (f"sf|{x['id']}|{x['markt']}|{x['auswahl']}", None, None,
                   {"art": "serieFuehrt", "id": x["id"], "heim": x["heim"], "gast": x["gast"], "liga": x.get("liga"),
                    "anpfiff": x.get("anpfiff"), "top5": x.get("top5"), "live": True, "markt": x["markt"], "auswahl": x["auswahl"],
                    "quote": q, "eur": x["eur"], "stand": st_l, "minute": (sp_l.get("live") or {}).get("min"), "lage": "vorn",
                    "n": x["n"]})
            continue
        # Lucas 02.11. (Mallorca – Cádiz 52' 0:1, Push „@ 1,60", Betfair stand längst bei 3,40): die Quote der Serie ist die
        # des letzten Ketten-Blocks — fällt danach ein Tor, ist sie veraltet. Live gilt der JETZIGE Preis der Auswahl; hat er
        # sich um mehr als ein Viertel bewegt, war das Geld vor dem Ereignis → kein Push (das Spiel ist ein anderes).
        if x.get("live"):
            q_jetzt = next((v[2] for v in x.get("marktVerteilung") or [] if isinstance(v, (list, tuple)) and len(v) > 2
                            and v[0] == x["auswahl"] and isinstance(v[2], (int, float)) and v[2] > 1), None)
            if q_jetzt and q and (q_jetzt > q * 1.25 or q_jetzt < q / 1.25):
                continue
            if q_jetzt:
                q = q_jetzt
        # die Quote vor dem Geldfluss kann älter sein als der Serienbeginn (Porto: 1,63 vor 30 Min, Serie ab 1,43)
        vorher = [f.get("quoteVorher") for z_ in d.get("zufluss30") or [] if str(z_.get("id")) == str(x["id"])
                  for f in z_.get("fluss") or [] if f.get("markt") == x["markt"] and f.get("auswahl") == x["auswahl"]]
        q_vor = max([v for v in [q0, *vorher] if isinstance(v, (int, float))], default=q0)
        drop = _drop_zeile(q, q_vor) if not x.get("live") else ""
        richtung = "" if drop else f" (Start {q2(q0)})" if q and q0 and abs(q - q0) >= 0.02 else ""
        # Live steigt die Quote des Führenden/Favoriten mit der Zeit — aber nicht um ein Viertel in einer Dreiviertelstunde.
        # Steigt sie stärker, wurde das gematchte Geld eher von Lay-Seite in den Markt gedrückt (Volumen hat keine Richtung).
        lay_hinweis = ""
        # 05.11. (Athletico-PR – Vasco 10' 0:0, 2,48 → 2,94, Endstand 1:3): nur Sieg-Märkte — bei Tor-Linien steigt die
        # Über-Quote ohne Tor ganz normal mit der Uhr, das ist kein Lay-Zeichen.
        sieg_markt = not re.search(r"Over/Under|Goals|Correct Score", str(x.get("markt") or ""))
        lay_verdacht = bool(x.get("live") and sieg_markt and q and q0 and q > q0 * 1.15 and not x.get("tor"))
        if lay_verdacht:
            lay_hinweis = (f"⚠️ <i>Quote stieg um {round(100 * (q / q0 - 1))} % ohne Tor — mehr als die Spielzeit erklärt. "
                           f"Gematchtes Geld kann auch Lay sein: der Markt glaubt eher weniger an diese Seite.</i>\n")
        lv = next((sp.get("live") for sp in (d.get("radar") or {}).get("spiele") or [] if sp.get("id") == x["id"]), None)
        st = (f"🔴 {lv.get('min')}' · {(lv.get('stand') or [0, 0])[0]}:{(lv.get('stand') or [0, 0])[1]}" if lv else f"⏰ {anpfiff(x.get('anpfiff'))}")
        text = (f"🔁 <b>Dauerzufluss</b> · Betfair · <b>{x['n']}× in Folge</b>\n"
                f"{flagge(x.get('land'))}<b>{e(x['heim'])} – {e(x['gast'])}</b>\n{e(x.get('liga'))} · {st}\n\n"
                f"💶 <b>{geld(x['eur'])}</b> in {x['dauerMin']} Min auf <b>{e(remis(_auswahl_de(x['auswahl'])))}</b> @ <b>{q2(q)}</b>{richtung}\n"
                f"<i>= im Schnitt {x.get('anteil')} % von allem, was je Abruf in diesem Markt neu kam</i>\n"
                + _zeitlich(x)
                + drop
                + lay_hinweis
                + (stat_zeile(d, x["id"]) if x.get("live") else "")
                + _tor_zeile(x)
                + "\n"
                + (_geld_block({"markt": x["markt"], "auswahl": x["auswahl"], "marktVerteilung": x["marktVerteilung"]}, 0)
                   if x.get("marktVerteilung") else "")
                + (f"<code>{_balken(x['saeulen'][-24:])}</code> <i>letzte 2 h, je Säule 5 Min</i>\n" if any(x['saeulen'][-24:]) else "")
                + liga_zeile(d, x.get("liga")))
        mi_, st_ = _live_von(d, x["id"])
        pub = _pub("serie", f"{x['heim']} – {x['gast']}", x.get("liga"), q, remis(_auswahl_de(x["auswahl"])), roh=x["auswahl"],
                   markt=_markt_de(x["markt"]), anpfiff=x.get("anpfiff"), live=bool(x.get("live")), minute=mi_, stand=st_,
                   gruende=[f"{_pbalken(x.get('anteil'))} {x.get('anteil')} % des Geldes",
                            f"⏱ {x['n']}× in Folge · +{geld(x['eur'])} in {_dauer(x.get('dauerMin'))}"])
        pub["layVerdacht"] = lay_verdacht           # Public-Regel 8
        _mit(pub, x.get("eur"), x.get("n"))
        pub["x"] = {"n": x["n"], "min": x.get("dauerMin"), "anteil": x.get("anteil"), "q0": q0, "saeulen": (x.get("saeulen") or [])[-24:]}
        yield key, text, None, {"art": "serie", "id": x["id"], "heim": x["heim"], "gast": x["gast"], "liga": x.get("liga"),
                                "anpfiff": x.get("anpfiff"), "top5": x.get("top5"), "live": bool(x.get("live")),
                                "markt": x["markt"], "auswahl": x["auswahl"], "quote": q, "eur": x["eur"],
                                "quoteStart": q0, "anteil": x.get("anteil"), "lay": lay_verdacht}, pub
        if lay_verdacht:
            # 06.11. (Lucas „Ja bitte" — Anregung „Reverse Line Movement nicht nur filtern, sondern invertieren"): still die
            # Gegenseite buchen = Lay der Auswahl zur Rück-Quote, auf 1 Einheit Risiko umgerechnet (gewinnt 1/(q−1), wenn die
            # Auswahl NICHT gewinnt). Je Spiel + Auswahl einmal.
            yield (f"lg|{x['id']}|{x['markt']}|{x['auswahl']}", None, None,
                   {"art": "layGegen", "id": x["id"], "heim": x["heim"], "gast": x["gast"], "liga": x.get("liga"),
                    "anpfiff": x.get("anpfiff"), "top5": x.get("top5"), "live": True, "markt": x["markt"],
                    "auswahl": x["auswahl"], "gegen": True, "quoteRueck": q, "quote": round(q / (q - 1), 3),
                    "quoteStart": q0, "eur": x["eur"], "stand": (x.get("live") or {}).get("stand") if isinstance(x.get("live"), dict) else None})
    for x in d.get("polySerien") or []:
        # wie bei Betfair/Stake: Favoriten-Geld (Quote < 1,40 ≙ Preis > 71 ¢) wird nicht gepusht; ohne Preis auch nicht
        if not x.get("quote") or x["quote"] < SERIE_PUSH_MIN_QUOTE:
            continue
        key = f"ps|{x['id']}|{x['auswahl']}|{_stufe(x['n'])}"
        lab = _pm_label(x["auswahl"])
        # Lucas 18.10.: „gegen Cruzeiro" ausschreiben
        import re as _re
        mg = _re.match(r"^gegen (.+)$", str(x.get("auswahl") or ""))
        if mg and not _re.match(r"^(Draw|Remis|Both)", mg.group(1), _re.I):
            teams = [t.strip() for t in _re.split(r"\s+vs\.?\s+", str(x.get("titel") or ""), flags=_re.I)]
            ander = next((t for t in teams if t and t != mg.group(1) and mg.group(1) not in t and t not in mg.group(1)), None)
            lab = (ander if (x.get("sport") or "Fußball") != "Fußball" and ander
                   else f"{mg.group(1)} gewinnt nicht" + (f" ({ander} oder Remis)" if ander else ""))
        # Lucas 26.10. (Nemiga – Aurora: erst $37k auf Nemiga vor Anpfiff, 40 Min später live $117k auf Aurora — „passt
        # das?"): kam auf denselben Markt in den letzten 3 h schon ein Dauerzufluss auf die ANDERE Seite, steht das dabei.
        gegen = _poly_gegenrichtung(x)
        text = (f"🔁 <b>Dauerzufluss</b> · Polymarket · <b>{x['n']}× in Folge</b>\n"
                f"{SPORT.get(x.get('sport'), '')} <b>{e(x.get('titel'))}</b>\n{'🔴 live' if x.get('live') else '⏰ ' + anpfiff(x.get('anpfiff'))}\n"
                + (f"↔️ <b>Gegenrichtung</b>: vor {gegen['vorMin']} Min kam der Dauerzufluss auf <b>{e(gegen['anzeige'])}</b>"
                   f" @ {gegen['preis']} ¢ — jetzt kippt das Geld\n" if gegen else "") + "\n"
                f"💵 <b>{geld(x['usd'], '$')}</b> in {x['dauerMin']} Min auf <b>{e(lab)}</b>"
                f" @ {round(x['preis'] * 100)} ¢ = Quote <b>{q2(x['quote'])}</b>\n"
                # Lucas 26.10. („erst 92 %, dann 97 % auf die andere Seite — wie geht das?"): der Anteil gilt nur für DIESES
                # Zeitfenster, nicht für den ganzen Markt — steht jetzt dabei, samt Summe aller Käufe darin
                + f"<i>= {round(x.get('anteil') or 0)} % aller Käufe in diesen {x['dauerMin']} Min "
                  f"({geld(round(x['usd'] * 100 / max(1, x.get('anteil') or 100)), '$')}, nicht nur Wale)</i>\n\n"
                + _poly_geld_block(d, x, lab)
                + f"<code>{_balken(x['saeulen'])}</code> <i>letzte 4 h, je Säule 10 Min</i>\n"
                f'<a href="https://polymarket.com/event/{urllib.parse.quote(str(x["id"]))}">Markt öffnen</a>\n')
        # Public-Regel 10 (Lucas 07.11.): Polymarket-Dauerzufluss darf in den Kanal, Regeln wie Betfair-Dauerzufluss
        pub = _pub("pserie", _re.sub(r"\s+(A?FC|CF)\b", "", _re.sub(r"\s+vs\.?\s+", " – ", str(x.get("titel") or ""), flags=_re.I)), x.get("liga"), x["quote"], _re.sub(r"\s+(A?FC|CF)\b", "", str(lab)),
                   roh=x["auswahl"], anpfiff=x.get("anpfiff"), live=bool(x.get("live")), sport=x.get("sport") or "Fußball",
                   betrag=pubmod.geld(x.get("usd"), "$"),
                   gruende=[f"🔁 {x['n']}× in Folge neues Geld auf diese Seite", f"{round(x.get('anteil') or 0)} % der Käufe"])
        _mit(pub, x.get("usd"), x.get("n"))
        if pub:
            pub["x"] = {"n": x["n"], "min": x.get("dauerMin"), "anteil": x.get("anteil"), "preis": x.get("preis"), "saeulen": x.get("saeulen") or [], "slug": x.get("id")}
        # Public-Regel 18 (Lucas 09.11. FOKUS – Alliance: „solang sich die Seiten nicht einig sind, ist es ein Coinflip"):
        # hält ein bewährtes Wallet (Rangliste, CLV-Untergrenze > 0) ≥ $5k auf der Gegenseite, oder kam in den letzten 6 h
        # ein Wal-Push auf die Gegenseite, geht die Serie nicht in den Public-Kanal — still gemessen als „pserieGegen".
        gw = _poly_gegenwal(d, x)
        if gw and pub:
            pub = None
            yield (f"pg|{x['id']}|{x['auswahl']}", None, None,
                   {"art": "pserieGegen", "id": x["id"], "titel": x.get("titel"), "heim": None, "gast": None,
                    "liga": x.get("liga"), "sport": x.get("sport") or "Fußball", "anpfiff": x.get("anpfiff"),
                    "markt": "Polymarket", "auswahl": x["auswahl"], "anzeige": lab, "quote": x["quote"],
                    "preis": x.get("preis"), "live": bool(x.get("live")), "top5": bool(x.get("top5")), "n": x["n"],
                    "gegenWal": gw})
        # Lucas 20.10.: Polymarket-Dauerzufluss ins Buch — Preis zum Push, abgerechnet über das Gamma-Ereignis
        yield key, text, None, {"art": "pserie", "id": x["id"], "titel": x.get("titel"), "heim": None, "gast": None,
                                "liga": x.get("liga"), "sport": x.get("sport") or "Fußball", "anpfiff": x.get("anpfiff"),
                                "markt": "Polymarket", "auswahl": x["auswahl"], "anzeige": lab, "quote": x["quote"],
                                "preis": x.get("preis"), "live": bool(x.get("live")), "top5": bool(x.get("top5")), "n": x["n"],
                                "gegenrichtung": bool(gegen)}, pub
    for x in d.get("stakeSerien") or []:
        if isinstance(x.get("quote"), (int, float)) and x["quote"] < SERIE_PUSH_MIN_QUOTE:
            continue
        key = f"ss|{x['id']}|{x['markt']}|{x['auswahl']}|{_stufe(x['n'])}"
        text = (f"🔁 <b>Dauerzufluss</b> · Stake · <b>{x['n']}× in Folge</b>\n"
                f"{SPORT.get(x.get('sport'), '')} <b>{e(x.get('titel'))}</b>\n{e(x.get('liga'))} · {'🔴 live' if x.get('live') else '⏰ ' + anpfiff(x.get('anpfiff'))}\n\n"
                f"💵 <b>{geld(x['usd'], '$')}</b> in {x['wetten']} Wetten über {x['dauerMin']} Min auf <b>{e(x['auswahl'])}</b>"
                + (f" · letzte @ {q2(x.get('quote'))}" if x.get("quote") else "") + "\n"
                + f"🗂 {e(x.get('markt'))}" + (f" · letzte Wette vor {x['letzteVorMin']} Min" if x.get("letzteVorMin") is not None else "") + "\n"
                + _anteil_balken(x['auswahl'], x.get("anteil"), " der großen Wetten")
                + f"<code>{_balken(x['saeulen'])}</code> <i>letzte 4 h, je Säule 10 Min</i>\n")
        yield key, text, None


GEGENWAL_MIN_USD = 5000


def _poly_gegenwal(d, x):
    """Public-Regel 18: bewährtes Geld auf der Gegenseite desselben Polymarket-Markts — Beschreibung oder None."""
    key, ausw = str(x.get("id")), str(x.get("auswahl"))
    gegen = [w for w in (d.get("polyBelegt") or {}).get(key) or []
             if str(w.get("seite")) != ausw and (w.get("usd") or 0) >= GEGENWAL_MIN_USD and (w.get("clvUg") or 0) > 0]
    if gegen:
        w = max(gegen, key=lambda w: w.get("usd") or 0)
        return {"quelle": "belegt", "seite": w.get("seite"), "usd": round(w.get("usd") or 0), "wallet": str(w.get("wallet"))[-4:]}
    for w in ((d.get("pushBilanz") or {}).get("walVerlauf") or [])[-300:]:
        t = ts(str(w.get("zeit") or "") + ("" if "+" in str(w.get("zeit") or "") else "+00:00"))
        if (str(w.get("key")) == key and str(w.get("seite")) != ausw and t and JETZT - t <= timedelta(hours=6)):
            return {"quelle": "walPush", "seite": w.get("seite"), "usd": round(w.get("usd") or 0), "wallet": str(w.get("wallet"))[-4:]}
    return None


def _poly_gegenrichtung(x):
    """Letzter Polymarket-Dauerzufluss auf denselben Markt, aber eine ANDERE Auswahl, in den letzten 3 h — oder None."""
    try:
        import bilanz
        buch = bilanz.laden()
    except Exception:  # noqa: BLE001
        return None
    for b in reversed(buch):
        t = ts(b.get("ts"))
        if not t or JETZT - t > timedelta(hours=3):
            continue
        if b.get("art") == "pserie" and b.get("id") == x.get("id") and b.get("auswahl") != x.get("auswahl"):
            return {"vorMin": round((JETZT - t).total_seconds() / 60), "anzeige": b.get("anzeige") or b.get("auswahl"),
                    "preis": round((b.get("preis") or 0) * 100)}
    return None


def _gleiche_richtung(m1, a1, m2, a2):
    """Zeigen zwei Tipps in dieselbe Richtung? Sieg: dieselbe Auswahl; Tore: beide Über bzw. beide Unter. REIN."""
    m1, m2, a1, a2 = str(m1 or ""), str(m2 or ""), str(a1 or ""), str(a2 or "")
    if m1 == "Match Odds" and m2 == "Match Odds":
        return a1 == a2
    if m1.startswith("Over/Under") and m2.startswith("Over/Under"):
        return a1.lower()[:4] == a2.lower()[:4] and a1.lower()[:4] in ("over", "unde")
    return False


def ki_top3(d):
    """Lucas 10.11. 09:42 (TEST): „wenn ein Spiel sich überschneidet (AI-Pick und Top 3) — highlighten und in den
    CocoBet-Money-Push". Offener AI-Pick (≤ 60 Min alt) + dasselbe Spiel jetzt in den Top 3 in dieselbe Richtung →
    einmal je Spiel melden und still als „kiTop3" buchen.

    Regel K2 ab 11.11. 20:00 (Malmö (W) – Ajax (W): AI-Pick 23. Min @ 2,42, Top-3-Treffer erst zur Halbzeit, der Push
    zeigte trotzdem Minute und Quote von damals): Überschneidung nur, wenn der AI-Pick ≤ 20 Min alt ist UND der Stand
    seitdem gleich blieb. Gebucht wird zur Quote, die die Top 3 JETZT zeigen — die bekommt man beim Push. Vorher (K1):
    60 Min, Quote des AI-Picks."""
    K = d.get("kiPicks") or {}
    picks = [x for x in K.get("heute") or [] if x.get("status") == "offen" and ts(x.get("ts"))
             and (JETZT - ts(x["ts"])).total_seconds() <= 20 * 60]
    if not picks:
        return
    try:
        import top3 as _t3
        oben = _t3.rangliste(d, JETZT)[:3]
    except Exception as exc:  # noqa: BLE001
        print(f"  ki_top3: {exc}")
        return
    for x in picks:
        for rang, t in enumerate(oben, 1):
            if str(t.get("id")) != str(x.get("id")) or not _gleiche_richtung(x.get("markt"), x.get("auswahl"), t.get("markt"), t.get("auswahl")):
                continue
            st_ki, st_jetzt = list(x.get("stand") or []), list(t.get("stand") or [])
            if not st_jetzt or st_ki != st_jetzt:
                break                                  # K2: Tor seit dem AI-Pick (oder Stand unbekannt) → keine Überschneidung
            q_jetzt = t.get("quote")
            if not q_jetzt or q_jetzt < 1.2:
                break
            nm = {"serie": "Dauerzufluss", "neu": "neues Geld", "norm": "Liga-Norm", "quote": "Quote gedrückt", "poly": "Polymarket",
                  "stake": "Stake", "maerkte": "mehrere Märkte", "druck": "Druck", "push": "HZ-Push", "liga": "Liga trägt"}
            sig = ", ".join(nm.get(k, k) for k in t.get("signale") or [])
            text = (f"🤖⭐ <b>AI-Pick + Top 3</b> <i>(Test)</i>\n<b>{e(x.get('heim'))} – {e(x.get('gast'))}</b>\n"
                    f"{e(x.get('liga'))} · Stand {':'.join(str(v) for v in st_jetzt)} · AI-Pick vor {round((JETZT - ts(x['ts'])).total_seconds() / 60)} Min (in der {x.get('minute')}.)\n\n"
                    f"🎯 <b>{e(x.get('tipp') or x.get('auswahl'))}</b> @ {q2(q_jetzt)} jetzt · beim AI-Pick {q2(x.get('quote'))} · spielbar ab {q2(x.get('mindest'))}\n"
                    f"🤖 Modell: Wert +{100 * (x.get('wert') or 0):.0f} % · Geld drauf {geld(x.get('geldEur'))} ({x.get('geldAnteil')} %)\n"
                    f"🏆 Top 3: Platz {rang} mit {str(t.get('punkte')).replace('.', ',')} Punkten ({e(sig)})\n"
                    f"<i>Zwei unabhängige Wege zeigen auf dieselbe Wette — nur Test, nicht public.</i>")
            beleg = {k: x.get(k) for k in ("id", "heim", "gast", "liga", "markt", "auswahl", "tipp", "quote", "fair", "wert", "minute", "stand", "geldEur")}
            beleg.update({"quote": round(q_jetzt, 3), "kiQuote": x.get("quote"), "regel": "K2",
                          "art": "kiTop3", "live": True, "anpfiff": t.get("anpfiff"), "top3Rang": rang, "top3Punkte": t.get("punkte")})
            yield f"kt|{x.get('id')}", text, None, beleg, None
            break


ARTEN = (serie, zufluss, live, bf_halbzeit, halbzeit, pinnacle, wale, stake, ki_top3)


# ── Wächter: meldet, wenn eine Datenquelle hängt (einmal), und wenn sie wieder da ist ─────────
# Lucas 17.10.: „Sind da Wächter notwendig?" — ja: Mac aus, Schlüssel abgelaufen, Betwatch down.
# Stake fehlt bewusst: dessen Zeitstempel ist die letzte Meldung, nicht der letzte Abruf (nachts ruhig).
# 25.10.: HZ-Finder und Polymarket laufen längst im eigenen 5-Min-Takt (nicht mehr altes Projekt) — Namen und Grenzen angepasst
WAECHTER = {"betfair": ("Betfair (Betwatch)", 30), "pinnacle": ("Pinnacle", 30), "hz": ("HZ-Finder", 30),
            "polymarket": ("Polymarket-Wale (Mac)", 40), "stakeAbruf": ("Stake-Abruf (Mac)", 30), "polyFluss": ("Polymarket-Fluss (Mac)", 40)}


def waechter(d, gesehen):
    q = dict(d.get("quellen") or {})
    q["polyFluss"] = (d.get("polyFluss") or {}).get("stand")
    for k, (name, grenze) in WAECHTER.items():
        t = ts(q.get(k))
        alt = (JETZT - t).total_seconds() / 60 if t else None
        key = f"wa|{k}"
        if alt is None or alt > grenze:
            if key not in gesehen:
                wie = f"seit {round(alt)} Min kein neuer Stand" if alt is not None else "gar kein Stand"
                yield key, f"⚠️ <b>Wächter</b>: {e(name)} — {wie} (Grenze {grenze} Min).\nPushes dieser Quelle fallen aus, bis sie wieder läuft.", "setzen"
        elif key in gesehen:
            yield key, f"✅ <b>Wächter</b>: {e(name)} läuft wieder (Stand vor {round(alt)} Min).", "loeschen"


# ── Senden ───────────────────────────────────────────────────────────────────────
STATUS = {"fehler": []}


def _x_post():
    sys.path.insert(0, str(BASIS / "social"))
    import x_post
    return x_post


def senden(token, chat, text, link=True) -> bool:
    body = urllib.parse.urlencode({"chat_id": chat, "text": text + (f'\n<a href="{SEITE}">Geldradar</a>' if link else ""),
                                   "parse_mode": "HTML", "disable_web_page_preview": "true"}).encode()
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", body, timeout=20) as r:
            return json.loads(r.read().decode()).get("ok", False)
    except urllib.error.HTTPError as exc:
        msg = f"HTTP {exc.code}: {exc.read().decode(errors='replace')[:200]}"
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)[:200]
    msg = msg.replace(token, "***")
    print(f"  ⚠️ Telegram: {msg}")
    STATUS["fehler"].append(msg)
    return False


def main() -> int:
    trocken = "--trocken" in sys.argv
    token, chat = os.environ.get("TELEGRAM_TOKEN", "").strip(), os.environ.get("TELEGRAM_TEST_CHAT_ID", "").strip()
    if not trocken and not (token and chat):
        print("push: TELEGRAM_TOKEN/TELEGRAM_TEST_CHAT_ID fehlen — nichts gesendet")
        status_schreiben({"fehlt": [n for n, v in (("TELEGRAM_TOKEN", token), ("TELEGRAM_TEST_CHAT_ID", chat)) if not v]})
        return 0
    if os.environ.get("PUSH_TEST") == "1":
        ok = senden(token, chat, "✅ <b>Testnachricht</b> vom Geldradar — der Test-Channel ist verbunden.")
        status_schreiben({"test": ok, "fehler": STATUS["fehler"]})
        return 0
    d = json.loads(DATEN.read_text(encoding="utf-8"))
    erster_lauf = not STAND.exists()
    gesehen = {} if erster_lauf else json.loads(STAND.read_text(encoding="utf-8"))
    grenze = (JETZT - timedelta(days=3)).isoformat()
    gesehen = {k: v for k, v in gesehen.items() if v >= grenze or k.startswith("wa|")}      # alte Einträge vergessen

    gesendet = 0
    if not erster_lauf:
        for key, text, was in waechter(d, gesehen):
            ok = True if trocken else senden(token, chat, text)
            if trocken:
                print("──", key, "\n" + text)
            if ok:
                if was == "setzen":
                    gesehen[key] = JETZT.isoformat()
                else:
                    gesehen.pop(key, None)
    import bilanz
    public = pubmod
    buch = bilanz.laden()
    pub_stand = public.laden(JETZT)
    pub_chat = os.environ.get("PUBLIC_CHAT_ID", "").strip() or chat     # Lucas 31.10.: erst Test-Channel, dann Public

    def erkannt():
        heute = JETZT.astimezone(ZoneInfo("Europe/Vienna")).date()
        return sum(1 for k, v in gesehen.items() if not k.startswith(("wa|", "sf|", "lf|", "tm|", "lg|", "wk|")) and ts(v)
                   and ts(v).astimezone(ZoneInfo("Europe/Vienna")).date() == heute)

    for art in ARTEN:
        for item in art(d):
            key, text, pause_h = item[:3]
            beleg = item[3] if len(item) > 3 else None
            pub = item[4] if len(item) > 4 else None
            alt = gesehen.get(key)
            if alt and (pause_h is None or (JETZT - ts(alt)).total_seconds() < pause_h * 3600):
                continue
            if key.startswith(("st|", "tm|")):
                basis = key.rsplit("|", 1)[0] + "|"
                # tm| (stakeTeam): je Spiel nur einmal messen — ein Paket kommt oft in mehreren Schüben
                if any(k.startswith(basis) and (key.startswith("tm|") or (JETZT - ts(v)).total_seconds() < 30 * 60) for k, v in gesehen.items()):
                    continue
            if key.startswith(("se|", "ps|", "ss|")):
                basis = key.rsplit("|", 1)[0] + "|"
                if any(k.startswith(basis) and (JETZT - ts(v)).total_seconds() < SERIE_ABSTAND_MIN * 60
                       for k, v in gesehen.items()):
                    continue        # nicht als gesehen markieren: die Stufe kommt nach der Pause mit frischen Zahlen
            if erster_lauf:
                gesehen[key] = JETZT.isoformat()
                continue
            if text is None:                 # 30.10.: nur messen, nicht senden (Schattenbuch)
                if beleg and not trocken:
                    bilanz.eintragen(buch, beleg, JETZT)
                gesehen[key] = JETZT.isoformat()
                continue
            if gesendet >= MAX_JE_LAUF:
                print(f"  Notbremse: mehr als {MAX_JE_LAUF} Meldungen, Rest im nächsten Lauf")
                break
            if trocken:
                print("──", key, "\n" + text)
                ok = True
            else:
                ok = senden(token, chat, text)
            if ok:
                gesehen[key] = JETZT.isoformat()
                gesendet += 1
                try:                 # Public-Regel 4 (02.11.): nicht sofort senden — in den Topf, bewertet wird unten
                    if pub:
                        public.sammeln(pub_stand, pub, JETZT)
                except Exception as exc:  # noqa: BLE001
                    print(f"  public: {exc}")
                try:                 # 10.11. (Lucas): jeder gesendete Push wird X-Kandidat (Cocobet auf X, Entwurf/echt)
                    if not trocken:
                        _xd = pub or beleg
                        if _xd and key in X_EXTRA:
                            _xd = {**_xd, "x": X_EXTRA[key]}
                        _x_post().merken((_xd or {}).get("art"), _xd, key, JETZT)
                except Exception as exc:  # noqa: BLE001
                    print(f"  x merken: {exc}")
                if beleg and not trocken:
                    # Lucas 25.10. („kleine Ligen mit 30k sagen mehr als 1 Mio auf Chelsea — nicht pauschal auswerten"):
                    # die Liga-Norm (übliches Geld je Spiel in dieser Liga) beim Push mitschreiben → Auswertung je Ligagröße
                    sp_ = next((x for x in (d.get("radar") or {}).get("spiele") or [] if str(x.get("id")) == str(beleg.get("id"))), None)
                    if sp_ and "normEur" not in beleg:
                        beleg = {**beleg, "normEur": sp_.get("normEur"), "ligaEur": sp_.get("ligaEur"), "liga": beleg.get("liga") or sp_.get("liga")}
                    # 30.10. (Elversberg 32' 2:0, Dauerzufluss auf Elversberg @ 1,40): Stand + Lage der Auswahl-Seite beim Push
                    # mitschreiben — trägt live Geld auf den, der schon führt?
                    lv_ = (sp_ or {}).get("live") or {}
                    if beleg.get("live") and lv_.get("stand") and lv_["stand"][0] is not None:
                        a_ = str(beleg.get("auswahl") or "")
                        k_ = 0 if a_ == sp_.get("heim") else 1 if a_ == sp_.get("gast") else None
                        st_ = lv_["stand"]
                        beleg = {**beleg, "stand": st_, "minute": lv_.get("min"),
                                 "lage": None if k_ is None else "vorn" if st_[k_] > st_[1 - k_] else "hinten" if st_[k_] < st_[1 - k_] else "gleich"}
                        # 03.11. (Beijing zu zehnt): Rote Karten still mitschreiben — Auswahl in Über-/Unterzahl? (Auswertung 15.11.)
                        ls_ = (((d.get("liveStats") or {}).get(str(beleg.get("id"))) or {}).get("statistik") or {})
                        r_ = [(ls_.get("heim") or {}).get("rot"), (ls_.get("gast") or {}).get("rot")]
                        if k_ is not None and all(isinstance(v, (int, float)) for v in r_):
                            beleg["rot"] = r_
                            beleg["zahl"] = ("keine" if r_[0] == r_[1] == 0 else "unter" if r_[k_] > r_[1 - k_]
                                             else "ueber" if r_[k_] < r_[1 - k_] else "gleich")
                    # 07.11. (Bucaramanga: derselbe Fix-Verdacht 4× im Buch, weil er bei +50 % Geld neu gemeldet wird):
                    # Halbzeit-Geld und Fix je Spiel + Markt + Auswahl nur EINMAL buchen — sonst verzerren Wiederholungen die Bilanz
                    if beleg.get("art") in ("ht", "fix") and any(
                            b.get("art") == beleg["art"] and str(b.get("id")) == str(beleg.get("id"))
                            and b.get("markt") == beleg.get("markt") and b.get("auswahl") == beleg.get("auswahl") for b in buch):
                        continue
                    bilanz.eintragen(buch, beleg, JETZT)
    if not trocken:
        try:                         # Lucas 23.10.: Top 3 jetzt mitschreiben und abrechnen (Messung, kein Push)
            import top3
            n3 = top3.eintragen(d, buch, JETZT)
            if n3:
                print(f"  top3: {n3} neu im Buch")
        except Exception as exc:  # noqa: BLE001
            print(f"  top3: {exc}")
        try:                         # Lucas 04.11.: „Geld über der Quote" still mitmessen + Instagram-Top-3 (ab 17 Uhr)
            import ueber_quote
            nu = ueber_quote.eintragen(d, buch, JETZT, BASIS / ".push_state")
            if nu:
                print(f"  ueberQuote: {nu} neu im Buch")
        except Exception as exc:  # noqa: BLE001
            print(f"  ueberQuote: {exc}")
        try:                         # die Top-3-Datei liegt im Gedächtnis; jede Seite bekommt sie mit (Instagram liest sie)
            import shutil
            if (BASIS / ".push_state" / "ig_top3.json").exists():
                shutil.copy(BASIS / ".push_state" / "ig_top3.json", BASIS / "site" / "ig_top3.json")
        except Exception as exc:  # noqa: BLE001
            print(f"  ig_top3: {exc}")
        try:                         # Lucas 25.10.: Druck-Radar still mitmessen (Index ≥ 70 → „noch ein Tor")
            import druck
            nd = druck.eintragen(d, buch, JETZT)
            if nd:
                print(f"  druck: {nd} neu im Buch")
            nt = druck.naechstes_tor(d, buch)       # 30.10.: wer macht das nächste Tor?
            if nt:
                print(f"  druck: {nt}× nächstes Tor festgestellt")
        except Exception as exc:  # noqa: BLE001
            print(f"  druck: {exc}")
        try:                         # Lucas 25.10.: „über der Liga-Norm" still mitmessen — folgt das Ergebnis dem Geld?
            import norm_mess
            nn = norm_mess.eintragen(d, buch, JETZT)
            if nn:
                print(f"  norm: {nn} neu im Buch")
        except Exception as exc:  # noqa: BLE001
            print(f"  norm: {exc}")
        try:
            bilanz.schreiben(bilanz.aktualisieren(buch, JETZT), JETZT)
        except Exception as exc:  # noqa: BLE001  — Auswertung darf das Pushen nie aufhalten
            print(f"  bilanz: {exc}")
        STAND.parent.mkdir(parents=True, exist_ok=True)
        STAND.write_text(json.dumps(gesehen, ensure_ascii=False), encoding="utf-8")
        try:                         # Public-Regel 13: Liga-Wertung aus dem eigenen Buch
            public.LIGA_WERT.clear(); public.LIGA_WERT.update(public.liga_werte(bilanz.laden(), ((d.get("ligaBilanz") or {}).get("ligen"))))
        except Exception as exc:  # noqa: BLE001
            print(f"  liga-wertung: {exc}")
        try:                         # Public-Regel 4: zu den Terminen den besten Kandidaten aus dem Topf
            _jetzt_live = {public._norm(f"{x.get('heim')} – {x.get('gast')}"): (x.get("live") or {}).get("stand")
                           for x in (d.get("radar") or {}).get("spiele") or [] if x.get("live")}
            for m_, pl_, pkt_, grund_ in public.auswahl(pub_stand, JETZT, aktuell=lambda m: _jetzt_live.get(public._norm(m.get("spiel")))):
                pt = public.text(m_, pl_, erkannt(), JETZT)
                if senden(token, pub_chat, pt, link=False):
                    public.buchen(pub_stand, m_, pl_, JETZT, punkte=pkt_, grund=grund_)
                    if JETZT.strftime("%Y-%m-%d") >= POLY_SCHATTEN_AB:
                        try:
                            pp_ = pub_stand["liste"][-1]["poly"] = poly_schatten(m_)
                            # 09.11.: über Polymarket selbst abrechnen lassen (E-Sport/Tennis haben kein eigenes Ergebnisbuch)
                            if pp_.get("key") and pp_.get("label") and not trocken:
                                b_ = bilanz.laden()
                                bilanz.eintragen(b_, {"art": "publicErg", "id": pp_["key"], "auswahl": pp_["label"],
                                                      "quote": m_.get("quote"), "anpfiff": pp_.get("anpfiff") or m_.get("anpfiff"),
                                                      "spiel": m_.get("spiel"), "pubZeit": JETZT.strftime("%Y-%m-%dT%H:%M"),
                                                      "liga": m_.get("liga")}, JETZT)
                                bilanz.schreiben(b_, JETZT)
                        except Exception as exc:  # noqa: BLE001
                            print(f"  poly-schatten: {exc}")
                    print(f"  public: {m_.get('spiel')} · {pkt_} Punkte · {grund_}")
        except Exception as exc:  # noqa: BLE001
            print(f"  public: {exc}")
        public.speichern(pub_stand)
        try:                         # 10.11.: Cocobet auf X — höchstens 3 je Stunde, nichts, was im Public steht
            nx = _x_post().lauf(pub_stand, JETZT, lambda t: senden(token, chat, t, link=False), public._norm)
            if nx:
                print(f"  x: {nx} {'gepostet' if os.environ.get('X_LIVE') == '1' else 'als Entwurf'}")
        except Exception as exc:  # noqa: BLE001
            print(f"  x: {exc}")
        try:                         # 11.11. (Lucas zum „How the money moved"-Bild: „haben wir dazu ein Signal gepostet?
            # Sonst ist das komisch"): welche Spiele hatten in den letzten 2 Tagen ein öffentliches Signal — X oder Public-Kanal.
            # Das Band-Bild (social/posten.py x_bilder) nimmt nur noch diese Spiele.
            # Je Spiel das FRÜHESTE Signal mit Zeit (UTC), Auswahl, Markt, Quote und wo — fürs Band als Markierung + Ergebnis.
            _xs = json.loads(_x_post().STAND.read_text(encoding="utf-8")) if _x_post().STAND.exists() else {}
            _xq = {k_.get("key"): k_ for k_ in (json.loads(_x_post().QUEUE.read_text(encoding="utf-8")) if _x_post().QUEUE.exists() else [])}
            _sig = {}

            def _merk(nk, zeit, wo, auswahl, markt, quote):
                alt_ = _sig.get(nk)
                if not alt_ or str(zeit) < str(alt_.get("zeit")):
                    _sig[nk] = {"zeit": str(zeit)[:16], "wo": wo, "auswahl": auswahl, "markt": markt, "quote": quote}
            for t_ in _xs.get("gepostet") or []:
                if len(t_) > 2 and t_[2]:
                    k_ = _xq.get(t_[0]) or {}
                    _merk(t_[2], t_[1], "X", k_.get("auswahl"), k_.get("markt"), k_.get("quote"))
            _gr = (JETZT - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
            for l_ in pub_stand.get("liste") or []:
                if l_.get("gesendet") and str(l_.get("zeit") or "") >= _gr and l_.get("spiel"):
                    _merk(public._norm(l_["spiel"]), l_.get("zeit"), "Telegram", l_.get("auswahl"), None, l_.get("quote"))
            (BASIS / "site" / "x_signale.json").write_text(json.dumps({"stand": JETZT.isoformat(), "spiele": _sig}, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            print(f"  x_signale: {exc}")
        try:                         # 02.11.: den Topf auf der Seite zeigen (Statistik → Public-Kanal)
            (BASIS / "site" / "topf.json").write_text(json.dumps(public.export(pub_stand, JETZT), ensure_ascii=False), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            print(f"  topf: {exc}")
    print(f"push: {'erster Lauf, ' + str(len(gesehen)) + ' als gesehen markiert' if erster_lauf else str(gesendet) + ' gesendet'}")
    if not trocken:
        heute = JETZT.astimezone(ZoneInfo("Europe/Vienna")).strftime("%Y-%m-%d")
        je_art = {}
        for k, v in gesehen.items():
            t = ts(v)
            if t and t.astimezone(ZoneInfo("Europe/Vienna")).strftime("%Y-%m-%d") == heute:
                je_art[k.split("|")[0]] = je_art.get(k.split("|")[0], 0) + 1
        status_schreiben({"ersterLauf": erster_lauf, "gesendet": gesendet, "gesehen": len(gesehen),
                          "heuteJeArt": je_art, "fehler": STATUS["fehler"][:5]})
    return 0


def status_schreiben(info):
    """Kurzer Lauf-Bericht neben der Seite (site/push_status.json) — ohne Token/Chat-ID, nur zum Nachsehen."""
    try:
        (BASIS / "site" / "push_status.json").write_text(json.dumps({"zeit": JETZT.isoformat(), **info}, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


if __name__ == "__main__":
    sys.exit(main())
