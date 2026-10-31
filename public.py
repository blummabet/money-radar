"""
public.py — Public-Regel 1 (Lucas 31.10.2026): welche Pushes in den öffentlichen Kanal gehen und wie sie aussehen.

Regel, festgelegt am 31.10. vor der Messung (Auswertung seit 16.10.: 3 je Fenster ≈ 10 Signale/Tag):
  • große Ligen (GROSS) + Dauerzufluss / Zufluss vor Anpfiff / Fix-Verdacht / Stake-Burst / Wal, Quote 1,60–3,00
  • ODER Stake-Burst / Fix-Verdacht in jeder Liga, Quote 1,80–3,00
  • ODER Wal-Einstieg (Polymarket) in jeder Liga und im E-Sport, Quote 1,60–3,00 (Lucas 31.10. 11:23)
  • je Spiel höchstens 1× am Tag
  • 4 Zeitfenster (Wiener Zeit: bis 12, 12–16, 16–20, ab 20 Uhr), je Fenster höchstens 3 — wer zuerst kommt
Abgerechnet wird über die Statistik (build.py hängt die Public-Signale als Art „public" an), Ziel 60.

Ziel-Chat: PUBLIC_CHAT_ID. Ist sie nicht gesetzt, geht alles in den Test-Channel — so kann nichts aus Versehen
öffentlich werden, solange Lucas den Stil prüft.
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BASIS = Path(__file__).resolve().parent
STAND = BASIS / ".push_state" / "public.json"
WIEN = ZoneInfo("Europe/Vienna")
REGEL = "R1"
AB = "2026-10-31T10:30:00+00:00"
ZIEL = 60
JE_FENSTER = 3
FENSTER = ((0, 12, "12:00"), (12, 16, "16:00"), (16, 20, "20:00"), (20, 24, None))   # von, bis, „nächste ab"
ARTEN = {"serie", "zufluss", "fix", "stake", "wal"}
NUR_ALLE_LIGEN = {"stake", "fix"}

GROSS = re.compile(
    r"(English Premier League|^Premier League$|La Liga|Bundesliga|Italian Serie [AB]|^Serie [AB]$|Ligue [12]|Championship$|"
    r"Eredivisie|Primeira|Belgian|Jupiler|Scottish Premiership|Super Lig|Turkish Super|UEFA Champions League|"
    r"UEFA Europa League|UEFA Conference League|Austrian|Swiss Super|Danish Superliga|Segunda|FA Cup|DFB|Copa del Rey|"
    r"Coppa Italia|Brazilian Serie A|AFC Champions League$)", re.I)

KOPF = {"serie": ("🔁", "DAUERZUFLUSS"), "zufluss": ("💶", "GELD VOR ANPFIFF"), "fix": ("🚨", "AUFFÄLLIGES GELD"),
        "stake": ("🟢", "STAKE-BURST"), "wal": ("🐋", "WAL STEIGT EIN")}
LIGA_KURZ = {"English Premier League": "Premier League", "Spanish La Liga": "La Liga", "German Bundesliga": "Bundesliga",
             "Italian Serie A": "Serie A", "French Ligue 1": "Ligue 1", "English Sky Bet Championship": "Championship",
             "German Bundesliga 2": "2. Bundesliga", "Italian Serie B": "Serie B", "French Ligue 2": "Ligue 2",
             "Spanish Segunda Division": "Segunda División", "Dutch Eredivisie": "Eredivisie",
             "Portuguese Primeira Liga": "Primeira Liga", "Turkish Super League": "Süper Lig",
             "UEFA Champions League": "Champions League", "UEFA Europa League": "Europa League",
             "UEFA Conference League": "Conference League", "Brazilian Serie A": "Brasileirão"}
SPORT = {"Fußball": "⚽", "E-Sport": "🎮", "Tennis": "🎾"}


def e(t) -> str:
    return html.escape(str(t if t is not None else ""), quote=False)


def q2(v) -> str:
    return f"{float(v):.2f}".replace(".", ",")


def geld(v, z="€") -> str:
    a = abs(float(v or 0))
    if a >= 1e6:
        return f"{z}{a / 1e6:.1f}".replace(".", ",") + " Mio"
    if a >= 1e5:
        return f"{z}{round(a / 1e3)}k"
    return f"{z}{round(a, -2 if a >= 1e4 else 0):,.0f}".replace(",", ".")


def _ts(s):
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())[:14]


def _fenster(jetzt):
    h = jetzt.astimezone(WIEN).hour
    return next(i for i, (v, b, _) in enumerate(FENSTER) if v <= h < b)


def passt(m) -> bool:
    """m: {art, liga, quote, …} — gilt die Public-Regel 1 für diese Meldung (ohne Kontingent)?"""
    art, q, liga = m.get("art"), m.get("quote"), str(m.get("liga") or "")
    if art not in ARTEN or not isinstance(q, (int, float)):
        return False
    if GROSS.search(liga) and 1.6 <= q < 3.0:
        return True
    if art == "wal":                      # Lucas 31.10. 11:23: Wale in jeder Liga + E-Sport (seit 16.10.: 77 · +10,9 %)
        return 1.6 <= q < 3.0
    return art in NUR_ALLE_LIGEN and 1.8 <= q < 3.0


def laden(jetzt):
    try:
        s = json.loads(STAND.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        s = {}
    tag = jetzt.astimezone(WIEN).strftime("%Y-%m-%d")
    if s.get("tag") != tag:
        s.update({"tag": tag, "fenster": {}, "spiele": []})
    grenze = (jetzt - timedelta(days=70)).isoformat()
    s["liste"] = [x for x in s.get("liste") or [] if x.get("zeit", "") >= grenze]
    return s


def speichern(s):
    STAND.parent.mkdir(parents=True, exist_ok=True)
    STAND.write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")


def platz(s, m, jetzt):
    """None = kein Platz / schon gesendet; sonst (Nr. im Fenster, Fenster-Index)."""
    if jetzt < _ts(AB):
        return None
    sk = _norm(m.get("spiel"))
    if not sk or sk in s["spiele"]:
        return None
    f = _fenster(jetzt)
    n = (s["fenster"].get(str(f)) or 0) + 1
    return (n, f) if n <= JE_FENSTER else None


def buchen(s, m, nr_f, jetzt):
    n, f = nr_f
    s["fenster"][str(f)] = n
    s["spiele"].append(_norm(m.get("spiel")))
    s["liste"].append({"zeit": jetzt.strftime("%Y-%m-%dT%H:%M"), "art": m.get("art"), "spiel": m.get("spiel"),
                       "auswahl": m.get("auswahlRoh") or m.get("auswahl"), "quote": m.get("quote"), "liga": m.get("liga"),
                       "live": bool(m.get("live")), "regel": REGEL})


def _wann(iso, jetzt):
    k = _ts(iso)
    if not k:
        return ""
    mn = round((k - jetzt).total_seconds() / 60)
    if mn <= 0:
        return ""
    if mn < 60:
        return f"in {mn} Min"
    w, h = k.astimezone(WIEN), jetzt.astimezone(WIEN)
    tage = (w.date() - h.date()).days
    return (f"heute {w:%H:%M}" if tage == 0 else f"morgen {w:%H:%M}" if tage == 1
            else f"{['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So'][w.weekday()]} {w:%d.%m.} {w:%H:%M}")


def _balken(pct):
    n = max(0, min(10, round((pct or 0) / 10)))
    return "▰" * n + "▱" * (10 - n)


def text(m, nr_f, erkannt, jetzt) -> str:
    n, f = nr_f
    emo, name = KOPF[m["art"]]
    kopf = f"{emo} <b>{name}</b>"
    if m.get("live"):
        lv = "🔴 <b>LIVE" + (f" {m['minute']}'" if m.get("minute") else "") + "</b>"
        st = m.get("stand")
        kopf += f" · {lv}" + (f" · {st[0]}:{st[1]}" if st and st[0] is not None else "")
    liga = LIGA_KURZ.get(m.get("liga"), m.get("liga") or "")
    wann = "" if m.get("live") else _wann(m.get("anpfiff"), jetzt)
    zeile2 = " · ".join(x for x in (f"{SPORT.get(m.get('sport') or 'Fußball', '⚽')} {e(liga)}".strip(), wann) if x)
    z = [kopf, zeile2, f"<b>{e(m['spiel'])}</b>", ""]
    auf = f"<b>{e(m['auswahl'])}</b>" + (f" <i>({e(m['markt'])})</i>" if m.get("markt") else "")
    if m.get("betrag"):
        z.append(f"💰 <b>{m['betrag']}</b> auf {auf} @ <b>{q2(m['quote'])}</b>")
    else:
        z.append(f"💰 Geld auf {auf} @ <b>{q2(m['quote'])}</b>")
    z += [x for x in m.get("gruende") or [] if x][:2]
    z.append("")
    bis = FENSTER[f][1]
    z.append(f"📡 Signal {n} von {JE_FENSTER} " + (f"bis {bis} Uhr" if bis < 24 else "heute Nacht")
             + f" · heute {erkannt} Geldbewegungen erkannt")
    if n == JE_FENSTER and FENSTER[f][2]:
        z.append(f"⏳ Nächste Signale ab {FENSTER[f][2]}")
    return "\n".join(z)
