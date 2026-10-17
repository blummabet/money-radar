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

ZUFLUSS_MIN_EUR = 10000      # darunter kein Push (die Überblicks-Liste hat eigene, niedrigere Schwellen)
ZUFLUSS_PAUSE_H = 6          # dasselbe Spiel + dieselbe Seite höchstens alle 6 h
FRISCH_MIN = 45              # HZ/Stake-Meldungen, die älter sind, gelten als verpasst und werden nicht nachgeschickt
WAL_MIN_USD = 1000
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
        if not fl or fl.get("eur", 0) < ZUFLUSS_MIN_EUR:
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


def halbzeit(d):
    for h in (d.get("hz") or {}).get("heute") or []:
        t = ts(h.get("zeit"))
        if not t or (JETZT - t).total_seconds() > FRISCH_MIN * 60:
            continue
        key = f"hz|{h.get('spiel')}|{t:%Y-%m-%d}"
        q = h.get("quoten") or {}
        text = (f"⏸ <b>Halbzeit 0:0</b>\n"
                f"{flagge(h.get('land'))}<b>{e(h.get('spiel'))}</b>\n{e(h.get('liga'))}\n\n"
                f"⚽ Tor in der 2. HZ @ <b>{q2(q.get('over05'))}</b>\n"
                f"⚽⚽ 2+ Tore @ <b>{q2(q.get('over15'))}</b>\n"
                f"🏠 Heimsieg @ <b>{q2(q.get('heim'))}</b>\n")
        yield key, text, None


SPORT = {"Fußball": "⚽", "E-Sport": "🎮", "Tennis": "🎾"}


def wale(d):
    for o in (d.get("poly") or {}).get("setzen") or []:
        if (o.get("usd") or 0) < WAL_MIN_USD:
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


ARTEN = (zufluss, halbzeit, wale, stake)


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
    gesehen = {k: v for k, v in gesehen.items() if v >= grenze}      # alte Einträge vergessen

    gesendet = 0
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
