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
import urllib.parse
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

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
WAL_MIN_USD_TOP5 = 3000      # in den Top-5 sind $1000 Alltag
MAX_JE_LAUF = 12             # Notbremse gegen Spam


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


def zufluss(d):
    """Lucas 17.10.: nicht das Geld aufs ganze Spiel, sondern auf WELCHE Auswahl es einseitig floss."""
    for z in d.get("zufluss30") or []:
        fl = (z.get("fluss") or [None])[0]
        if not fl or fl.get("eur", 0) < SCHWELLE[bool(z.get("top5"))][0]:
            continue
        key = f"zf|{z.get('id')}|{fl.get('markt')}|{fl.get('auswahl')}"
        q, qv = fl.get("quote"), fl.get("quoteVorher")
        richtung = (f", Quote fällt ↓ (von {q2(qv)})" if q < qv else f", Quote steigt ↑ (von {q2(qv)})") if q and qv and abs(q - qv) >= 0.02 else ""
        pin = z.get("pinnacle") if fl.get("markt") == "Match Odds" else None
        text = (f"🟡 <b>Frischer Zufluss</b> · Betfair\n"
                f"{flagge(z.get('land'))}<b>{e(z.get('heim'))} – {e(z.get('gast'))}</b>\n"
                f"{e(z.get('liga'))} · {anpfiff(z.get('anpfiff'))}\n\n"
                f"💶 <b>+{geld(fl.get('eur'))}</b> in 30 Min auf <b>{e(_auswahl_de(fl.get('auswahl')))}</b> @ <b>{q2(q)}</b>{richtung}\n"
                f"📊 {fl.get('anteil')} % des neuen Geldes in {e(_markt_de(fl.get('markt')))}\n"
                + (f"📌 Pinnacle {'einig ✅' if pin.get('einig') else 'anderer Meinung ⚠️'}\n" if pin else ""))
        yield key, text, ZUFLUSS_PAUSE_H


def live(d):
    """Lucas 17.10.: „Ja, inplay will ich auch" — einseitiges neues Geld in laufenden Spielen (15 Min).
    Je Spiel + Markt + Auswahl nur einmal (12 h Pause), entschiedene Spiele kommen gar nicht in die Liste."""
    for z in d.get("liveZufluss") or []:
        fl = (z.get("fluss") or [None])[0]
        if not fl or fl.get("eur", 0) < SCHWELLE[bool(z.get("top5"))][1]:
            continue
        lv = z.get("live") or {}
        st = lv.get("stand") or [0, 0]
        key = f"lz|{z.get('id')}|{fl.get('markt')}|{fl.get('auswahl')}"
        q, qv = fl.get("quote"), fl.get("quoteVorher")
        richtung = (f", Quote fällt ↓ (von {q2(qv)})" if q < qv else f", Quote steigt ↑ (von {q2(qv)})") if q and qv and abs(q - qv) >= 0.02 else ""
        minute = f"{lv.get('min')}'" if lv.get("min") is not None else "live"
        text = (f"🔴 <b>Live-Zufluss</b> · Betfair\n"
                f"{flagge(z.get('land'))}<b>{e(z.get('heim'))} – {e(z.get('gast'))}</b>\n"
                f"{e(z.get('liga'))} · {minute} · <b>{st[0]}:{st[1]}</b>\n\n"
                f"💶 <b>+{geld(fl.get('eur'))}</b> in 15 Min auf <b>{e(_auswahl_de(fl.get('auswahl')))}</b> @ <b>{q2(q)}</b>{richtung}\n"
                f"📊 {fl.get('anteil')} % des neuen Geldes in {e(_markt_de(fl.get('markt')))}\n")
        yield key, text, 12


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
              ([f"Heimsieg {_hz_q(v.get('heim'))}"] if "heim" in gr and v.get("heim") else [])
        if erw:
            z.append(f"🎯 <b>Vorher:</b> {' · '.join(erw)}\n")
        jetzt = []
        if q.get("over05"):
            jetzt.append(f"Tor in 2. HZ <b>{_hz_q(q['over05'])}</b>")
        if q.get("over15"):
            jetzt.append(f"2+ Tore {_hz_q(q['over15'])}")
        if q.get("heim"):
            jetzt.append((f"<b>Heimsieg {_hz_q(q['heim'])}</b>" if "heim" in gr else f"Heimsieg {_hz_q(q['heim'])}"))
        z.append("💰 <b>Jetzt:</b>\n   " + ("\n   ".join(jetzt) if jetzt else "–") + "\n")
        zt = _hz_zufluss(h.get("zufluss"))
        if zt:
            z.append(zt + "\n")
        st = _hz_stat(h.get("statistik") or {}) if h.get("statistik") else None
        if st:
            z.append(f"📊 <b>1. HZ:</b> {st}\n")
        s = h.get("serie") or {}
        if s.get("heim") or s.get("gast"):
            z.append(f"📈 <b>Form</b> (neueste zuerst)\n   {e(h.get('heim') or 'Heim')}: {_hz_serie(s.get('heim'))}\n"
                     f"   {e(h.get('gast') or 'Gast')}: {_hz_serie(s.get('gast'))}\n")
        b = next((x for x in (d.get("hz") or {}).get("bericht") or [] if "Tor in 2" in str(x.get("wette"))), None)
        if b and b.get("n"):
            z.append(f"\n🔬 <i>Buch „Tor in 2. HZ“: {b['n']} abgerechnet · getroffen {round(b.get('treffer') or 0)} %, "
                     f"Quote sagte {round(b.get('erwartet') or 0)} % · ROI {str(round(b.get('roi') or 0, 1)).replace('.', ',')} %</i>\n")
        yield key, "".join(z), None


def bf_halbzeit(d):
    """Lucas 17.10.: die Betfair-HZ-Pushes aus dem alten Projekt („die sollen auch rein") —
    🔵 Halbzeit-Geld (einseitig) und ⚫ Fix-Verdacht. Erneut gemeldet erst, wenn das Geld um +50 % wuchs."""
    import math
    for a in d.get("bfHtPushes") or []:
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
                    + f"📊 <b>{a.get('anteil')} %</b> auf <b>{e(auf)}</b>{quote}" + (" · sehr einseitig" if (a.get("anteil") or 0) >= 90 else "") + "\n")
        else:
            seiten = " · ".join(f"{e(remis(_auswahl_de(x['name'])))} {x['anteil']} %" for x in a.get("seiten") or [])
            text = (f"🔵 <b>Betfair · Halbzeit-Geld (einseitig)</b>\n" + kopf
                    + f"💷 {e(_markt_de(a.get('markt')))}: <b>{geld(a.get('eur'))}</b> gematcht\n"
                    + f"📊 <b>{a.get('anteil')} %</b> auf <b>{e(auf)}</b>{quote}\n"
                    + (f"<i>{seiten}</i>\n" if seiten else ""))
        yield key, text, None


SPORT = {"Fußball": "⚽", "E-Sport": "🎮", "Tennis": "🎾"}


def wale(d):
    for o in (d.get("poly") or {}).get("setzen") or []:
        top5 = str(o.get("markt") or "").split("-")[0] in {"epl", "bun", "lal", "sea", "fl1", "lig1"}
        if (o.get("usd") or 0) < (WAL_MIN_USD_TOP5 if top5 else WAL_MIN_USD):
            continue
        key = f"wal|{o.get('wallet')}|{o.get('markt')}|{o.get('seite')}"
        preis, jetzt = o.get("preis"), o.get("jetzt")
        text = (f"🟣 <b>Belegter Wal steigt ein</b> · Polymarket\n"
                f"{SPORT.get(o.get('sport'), '')} <b>{e(o.get('spiel'))}</b>\n{anpfiff(o.get('anpfiff'))}\n\n"
                f"💵 <b>{geld(o.get('usd'), '$')}</b> auf <b>{e(remis(o.get('seite')))}</b>"
                + (f" @ {round(preis * 100)} ¢ (Quote {q2(1 / preis)})" if preis else "") + "\n"
                + (f"📈 jetzt {round(jetzt * 100)} ¢\n" if jetzt is not None else "")
                + f"🧠 Wallet {e(str(o.get('wallet'))[2:6])} schlägt den Schlusskurs im Schnitt um "
                  f"{q2(o.get('clv'))} pp (vorsichtig mind. {q2(o.get('clvUg'))})\n"
                f'<a href="https://polymarket.com/event/{urllib.parse.quote(str(o.get("markt") or ""))}">Markt öffnen</a>\n')
        yield key, text, None


def stake(d):
    for g in (d.get("stake") or {}).get("gruppen") or []:
        t = ts(g.get("zeit"))
        if not t or (JETZT - t).total_seconds() > FRISCH_MIN * 60:
            continue
        key = f"st|{g.get('spiel')}|{g.get('auswahl')}|{t:%Y-%m-%d}"
        text = (f"🟢 <b>Stake · {e(' + '.join(g.get('arten') or []))}</b>\n"
                f"<b>{e(g.get('spiel'))}</b>\n{e(g.get('liga'))} · {'live' if g.get('phase') == 'live' else 'vor Anpfiff'}\n\n"
                f"💵 <b>{geld(g.get('usd'), '$')}</b> in {g.get('wetten')} Wetten auf <b>{e(g.get('auswahl'))}</b>"
                + (f" @ {q2(g.get('quote'))}" if g.get("quote") else "") + "\n"
                + (f"📏 {q2(g.get('faktor'))}-mal so viel wie in dieser Liga üblich\n" if g.get("faktor") else "")
                + (f"🗂 {e(g.get('markt'))}\n" if g.get("markt") else ""))
        yield key, text, None


ARTEN = (zufluss, live, bf_halbzeit, halbzeit, wale, stake)


# ── Wächter: meldet, wenn eine Datenquelle hängt (einmal), und wenn sie wieder da ist ─────────
# Lucas 17.10.: „Sind da Wächter notwendig?" — ja: Mac aus, Schlüssel abgelaufen, Betwatch down.
# Stake fehlt bewusst: dessen Zeitstempel ist die letzte Meldung, nicht der letzte Abruf (nachts ruhig).
WAECHTER = {"betfair": ("Betfair (Betwatch)", 30), "pinnacle": ("Pinnacle", 30), "hz": ("HZ-Finder (altes Projekt, Mac)", 45),
            "polymarket": ("Polymarket-Wale (altes Projekt, Mac)", 120), "polyFluss": ("Polymarket-Fluss (Mac-Runner radar)", 40)}


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


def senden(token, chat, text) -> bool:
    body = urllib.parse.urlencode({"chat_id": chat, "text": text + f'\n<a href="{SEITE}">Geldradar</a>',
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
    for art in ARTEN:
        for key, text, pause_h in art(d):
            alt = gesehen.get(key)
            if alt and (pause_h is None or (JETZT - ts(alt)).total_seconds() < pause_h * 3600):
                continue
            if erster_lauf:
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
    if not trocken:
        STAND.parent.mkdir(parents=True, exist_ok=True)
        STAND.write_text(json.dumps(gesehen, ensure_ascii=False), encoding="utf-8")
    print(f"push: {'erster Lauf, ' + str(len(gesehen)) + ' als gesehen markiert' if erster_lauf else str(gesendet) + ' gesendet'}")
    if not trocken:
        status_schreiben({"ersterLauf": erster_lauf, "gesendet": gesendet, "gesehen": len(gesehen), "fehler": STATUS["fehler"][:5]})
    return 0


def status_schreiben(info):
    """Kurzer Lauf-Bericht neben der Seite (site/push_status.json) — ohne Token/Chat-ID, nur zum Nachsehen."""
    try:
        (BASIS / "site" / "push_status.json").write_text(json.dumps({"zeit": JETZT.isoformat(), **info}, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


if __name__ == "__main__":
    sys.exit(main())
