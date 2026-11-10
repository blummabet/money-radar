"""x_post.py — Cocobet auf X (Lucas 10.11.2026 12:31/12:42).

„Die guten Pushes aus Cocobet Money sind nirgends — nicht in den Public (der ist von der Menge optimal), aber auf X."
Regeln (Lucas): Englisch · höchstens 3 Posts je Stunde · Nachtruhe 3–7 Uhr Wien · nichts, was schon im Public-Kanal
steht · wechselnde Formulierungen, damit es nicht automatisch wirkt · keine Links im Post (X: Post mit Link ~13× teurer)
· alle 3 Tage ein Hinweis auf die Telegram-Gruppe. Keine Auswertung nötig („ich will nur auf Geldflüsse aufmerksam machen").

Erst ENTWURF: die Tweets landen als Vorschau im Testkanal. Echt gepostet wird erst, wenn X_LIVE=1 gesetzt ist und die
Schlüssel (X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET) als Secrets da sind.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

WIEN = ZoneInfo("Europe/Vienna")
BASIS = Path(__file__).resolve().parent.parent / ".push_state"
QUEUE = BASIS / "x_queue.json"
STAND = BASIS / "x_stand.json"
JE_STUNDE = 3
RUHE_VON, RUHE_BIS = 3, 7          # Uhr Wien
FRISCH_MIN = 20                    # ältere Kandidaten verfallen (live zählt jede Minute)
WERBUNG_ALLE_H = 72
TELEGRAM = "t.me/c0c0bet"
ARTEN_AUS = {"fix", "ht", "kiTop3", "hz", "pinn"}   # Fix-Verdacht nie öffentlich; HZ/Test nicht; Pinnacle nicht (10.11. Lucas: Kurs, kein Geld)

LIGA_EN = {}


def _laden(p, d):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return d


def _speichern(p, v):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(v, ensure_ascii=False), encoding="utf-8")


def _ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None


def _betrag(s):
    """'€28k' / '$1,2k' / 28000 → Zahl."""
    if isinstance(s, (int, float)):
        return float(s)
    m = re.match(r"^[€$]?\s*([\d.,]+)\s*([km]?)", str(s or "").strip(), re.I)
    if not m:
        return 0.0
    z = m.group(1)
    if re.fullmatch(r"\d{1,3}(\.\d{3})+(,\d+)?", z):            # 69.000 / 1.250.000,5 (deutsch)
        v = float(z.replace(".", "").replace(",", "."))
    elif z.count(",") == 1 and not re.fullmatch(r"\d{1,3}(,\d{3})+", z):
        v = float(z.replace(".", "").replace(",", "."))
    else:
        v = float(z.replace(",", ""))
    return v * (1000 if m.group(2).lower() == "k" else 1e6 if m.group(2).lower() == "m" else 1)


def _geld(v, w):
    v = float(v or 0)
    return f"{w}{v / 1e6:.1f}M" if v >= 1e6 else f"{w}{v / 1000:.0f}k" if v >= 9500 else f"{w}{v / 1000:.1f}k" if v >= 1000 else f"{w}{v:.0f}"


def _en(a):
    """Auswahl ins Englische (Über 2,5 → Over 2.5, Remis → Draw)."""
    a = str(a or "")
    a = re.sub(r"^Über\b", "Over", a)
    a = re.sub(r"^Unter\b", "Under", a)
    a = re.sub(r"\bTore\b", "goals", a)
    a = re.sub(r"(\d),(\d)", r"\1.\2", a)
    a = {"Remis": "the draw", "The Draw": "the draw", "Unentschieden": "the draw"}.get(a, a)
    a = re.sub(r"\s+Goals$", " goals", a)
    if re.match(r"^(Over|Under) \d+\.5$", a):
        a += " goals"
    return a.strip()


def merken(art, d, key, jetzt):
    """Ein in Cocobet Money gesendeter Push wird X-Kandidat. d = pub (Public-Daten) oder Beleg."""
    if not d or art in ARTEN_AUS:
        return
    spiel = d.get("spiel") or (f"{d.get('heim')} – {d.get('gast')}" if d.get("heim") and d.get("gast") else None)
    auswahl = d.get("auswahlRoh") or d.get("auswahl")
    if not spiel or not auswahl:
        return
    q = _laden(QUEUE, [])
    if any(x.get("key") == key for x in q):
        return
    waehrung = "$" if art in ("stake", "pserie", "wal") else "€"
    # 10.11. (erster echter Post „$69" statt $69k): public.geld schreibt „$69.000" — der Punkt ist ein Tausender-Trenner.
    # Deshalb zuerst die Zahl (eur/usd), den Text nur als Notnagel.
    zahl = next((float(d[f]) for f in ("eur", "usd") if isinstance(d.get(f), (int, float)) and d[f] > 0), None)
    betrag = zahl or _betrag(d.get("betrag"))
    q.append({"key": key, "art": art, "ts": jetzt.isoformat(), "spiel": spiel, "liga": d.get("liga"), "sport": d.get("sport"),
              "auswahl": auswahl, "markt": d.get("markt"), "quote": d.get("quote"), "live": bool(d.get("live")),
              "minute": d.get("minute"), "stand": d.get("stand"), "betrag": betrag, "waehrung": waehrung,
              "anpfiff": d.get("anpfiff"), "x": d.get("x") or {}})
    grenze = jetzt - timedelta(hours=24)
    _speichern(QUEUE, [x for x in q if (_ts(x.get("ts")) or jetzt) >= grenze][-300:])


# ── Texte: je Art mehrere Fassungen, gewählt über den Schlüssel (gleicher Push = gleiche Fassung) ──────────────
TEXTE = {
    "serie": [
        "Money keeps pouring into {sel}{at}.\n{spiel} · {liga}\n{betrag} in steady waves – not a one-off.",
        "{spiel}: wave after wave on {sel}{at}. {betrag} so far. {liga}.",
        "This one won't stop. {sel}{at} keeps getting backed – {betrag} and counting.\n{spiel} ({liga})",
        "Steady stream on Betfair: {sel}{at}\n{spiel} · {betrag} so far",
    ],
    "zufluss": [
        "Pre-match flow: {betrag} just hit {sel}{at}.\n{spiel} · {liga}{ko}",
        "Someone likes {sel}{at}. {betrag} in the last 30 min before {spiel}{ko}.",
        "{spiel}{ko}: {betrag} of fresh money on {sel}{at}. {liga}.",
    ],
    "live": [
        "{min}{spiel}: fresh money on {sel}{at} – {betrag} in minutes.",
        "In-play spike: {betrag} on {sel}{at}.\n{spiel}{min2}",
    ],
    "stake": [
        "High rollers on Stake: {betrag} on {sel}{at}.\n{spiel} · {liga}",
        "Big tickets landing on {sel}{at} – {betrag} from Stake high rollers.\n{spiel}",
        "Stake high-roller burst: {betrag} on {sel}{at}, {spiel}.",
    ],
    "pserie": [
        "Polymarket keeps buying {sel}{at}.\n{spiel} · {betrag} in a steady flow",
        "On-chain money stacking up on {sel}{at}: {betrag}. {spiel}.",
        "{spiel}: Polymarket buyers won't stop – {sel}{at}, {betrag} so far.",
    ],
    "wal": [
        "Whale alert 🐋 {betrag} on {sel}{at}.\n{spiel} · Polymarket",
        "A wallet with a track record just put {betrag} on {sel}{at}. {spiel}.",
        "🐋 {betrag} on {sel}{at} – {spiel}. Wallets like this beat the closing price more often than not.",
    ],
    "": [
        "Money on the move: {betrag} on {sel}{at}.\n{spiel} · {liga}",
        "{spiel}: {betrag} on {sel}{at}.",
    ],
}
EINSTIEG = ["", "", "", "👀 ", "📈 ", "💰 "]
WERBUNG = [
    "We track where the big money goes – Betfair, Polymarket and Stake high rollers.\nThe strongest signals go to our free Telegram: {tg} (link in bio)",
    "Most of what we post here is the raw flow. The filtered picks land in our free Telegram channel → {tg}",
    "Follow the money, not the tipsters. Free Telegram with our best signals: {tg}",
]


def _wahl(liste, key):
    h = int(hashlib.sha1(str(key).encode()).hexdigest(), 16)
    return liste[h % len(liste)], h


def text(k, jetzt) -> str:
    if k.get("art") == "pinn" and not (k.get("x") or {}).get("von"):
        return ""                                    # Pinnacle ohne Kurs: der Betrag allein sagt nichts (10.11.)
    try:
        neu = text_neu(k, jetzt)
        if neu:
            return neu
    except Exception as exc:  # noqa: BLE001
        print(f"  x: neue Fassung fehlgeschlagen ({str(exc)[:80]}), alte Vorlage")
    vorlagen = TEXTE.get(k["art"]) or TEXTE[""]
    v, h = _wahl(vorlagen, k["key"])
    q = k.get("quote")
    ko = ""
    if k.get("anpfiff") and not k.get("live"):
        t = _ts(k["anpfiff"])
        if t and t > jetzt:
            ko = f" · KO {t.astimezone(WIEN):%H:%M} CET"
    sel = _en(k["auswahl"])
    if k.get("markt") and not re.search(r"over|under|goals", sel, re.I):
        sel += f" ({_en(k['markt'])})"
    s = v.format(sel=sel, at=f" @ {q:.2f}" if isinstance(q, (int, float)) else "", spiel=k["spiel"].replace(" – ", " vs "),
                 liga=k.get("liga") or k.get("sport") or "", betrag=_geld(k.get("betrag"), k.get("waehrung") or "€"), ko=ko,
                 min=f"{k['minute']}' " if k.get("minute") else "LIVE ", min2=f" · {k['minute']}'" if k.get("minute") else " · live")
    s = re.sub(r"\s·\s*\n", "\n", s).replace(" · \n", "\n").replace("()", "").strip()
    vor = EINSTIEG[(h >> 8) % len(EINSTIEG)] if s[:1].isalnum() else ""
    return (vor + s)[:270]


# ── 10.11. (Lucas: „wirkt sehr lieblos … sieht sehr wenig"): X-Fassung mit denselben Bausteinen wie der Telegram-Push ──
KOPF = {
    "wal": ("🐋", "𝗪𝗛𝗔𝗟𝗘 𝗔𝗟𝗘𝗥𝗧", "Polymarket", "🟪"),
    "serie": ("🔁", "𝗦𝗧𝗘𝗔𝗗𝗬 𝗙𝗟𝗢𝗪", "Betfair", "🟧"),
    "zufluss": ("⚡", "𝗙𝗥𝗘𝗦𝗛 𝗠𝗢𝗡𝗘𝗬", "Betfair", "🟧"),
    "live": ("🔴", "𝗟𝗜𝗩𝗘 𝗠𝗢𝗡𝗘𝗬", "Betfair", "🟧"),
    "stake": ("🟢", "𝗛𝗜𝗚𝗛 𝗥𝗢𝗟𝗟𝗘𝗥𝗦", "Stake", "🟩"),
    "pserie": ("🔁", "𝗦𝗧𝗘𝗔𝗗𝗬 𝗙𝗟𝗢𝗪", "Polymarket", "🟪"),
    "pinn": ("📉", "𝗦𝗛𝗔𝗥𝗣 𝗠𝗢𝗩𝗘", "Pinnacle", "🟦"),
}
SPORT_EMOJI = {"Fußball": "⚽", "Tennis": "🎾", "E-Sport": "🎮"}
# 10.11. (Lucas „Tags … für Suche gut?" → „ja"): höchstens 2 — das Liga-/Spiel-Tag, dem Leute live folgen, und
# #Polymarket nur bei Polymarket. Kein #Betfair/#Stake (sucht kaum wer, #Stake ist Glücksspiel-Werbung). Unbekannte Liga = kein Tag.
LIGA_TAGS = [
    (r"women.?s champions league", "#UWCL"), (r"champions league", "#UCL"), (r"europa league", "#UEL"), (r"conference league", "#UECL"),
    (r"premier league", "#PremierLeague"), (r"la liga|laliga|primera division", "#LaLiga"), (r"serie a\b", "#SerieA"),
    (r"bundesliga", "#Bundesliga"), (r"ligue 1", "#Ligue1"), (r"eredivisie", "#Eredivisie"),
    (r"primeira liga|liga portugal", "#LigaPortugal"), (r"championship", "#EFLChampionship"), (r"fa cup", "#FACup"),
    (r"copa del rey", "#CopaDelRey"), (r"dfb.?pokal", "#DFBPokal"), (r"coppa italia", "#CoppaItalia"), (r"mls\b", "#MLS"),
    (r"saudi", "#SPL"), (r"libertadores", "#Libertadores"), (r"world cup", "#WorldCup"), (r"nations league", "#NationsLeague"),
    (r"atp finals", "#ATPFinals"), (r"wta finals", "#WTAFinals"), (r"davis cup", "#DavisCup"), (r"billie jean king", "#BJKCup"),
    (r"australian open", "#AusOpen"), (r"roland garros|french open", "#RolandGarros"), (r"wimbledon", "#Wimbledon"),
    (r"us open", "#USOpen"),
    (r"counter.?strike|intel extreme masters|\bcs2\b|\biem\b|\bblast\b|\bpgl\b|esl pro", "#CS2"), (r"dota", "#Dota2"),
    (r"league of legends|\blol\b|\blck\b|\blec\b|\blpl\b", "#LeagueOfLegends"), (r"valorant|\bvct\b", "#VALORANT"),
]
SLUG_TAGS = {"cs2": "#CS2", "csgo": "#CS2", "dota2": "#Dota2", "lol": "#LeagueOfLegends", "val": "#VALORANT",
             "ucl": "#UCL", "uel": "#UEL", "epl": "#PremierLeague", "lal": "#LaLiga", "sea": "#SerieA", "bun": "#Bundesliga",
             "fl1": "#Ligue1", "lig1": "#Ligue1", "mls": "#MLS"}


def _tags(k, quelle):
    x = k.get("x") or {}
    tag = None
    slug = str(x.get("slug") or "").split("-")[0].lower()
    if slug in SLUG_TAGS:
        tag = SLUG_TAGS[slug]
    if not tag:
        wo = f"{k.get('liga') or ''} {k.get('spiel') or ''}".lower()
        tag = next((t for muster, t in LIGA_TAGS if re.search(muster, wo)), None)
    tags = [t for t in (tag, "#Polymarket" if quelle == "Polymarket" else None) if t]
    return " ".join(tags)


def _gewicht(t):
    """X zählt CJK/Emoji doppelt — grob nachgebaut (Latein/Kyrillisch/Satzzeichen = 1)."""
    return sum(1 if (ord(c) <= 0x10FF or 0x2000 <= ord(c) <= 0x206F) else 2 for c in t)


def _kaesten(anteil, farbe, n=10):
    k = max(0, min(n, round((anteil or 0) / 100 * n)))
    return farbe * k + "⬜" * (n - k)


def _spark(werte):
    w = [float(x or 0) for x in werte or []]
    if len(w) < 4 or max(w) <= 0:
        return ""
    z, lo, hi = "▁▂▃▄▅▆▇█", (min(w) if min(w) > 0 else 0), max(w)
    return "".join(z[min(7, round((x - lo) / ((hi - lo) or 1) * 7))] for x in w)


def text_neu(k, jetzt) -> str | None:
    art = k.get("art")
    if art not in KOPF:
        return None
    emo, titel, quelle, farbe = KOPF[art]
    x = k.get("x") or {}
    q = k.get("quote")
    sel = _en(k["auswahl"])
    if k.get("markt") and k["markt"] not in ("Match Odds", "Sieg-Markt", "Polymarket") and not re.search(r"over|under|goals", sel, re.I):
        sel += f" ({_en(k['markt'])})"
    spiel = k["spiel"].replace(" – ", " vs ")
    geld = _geld(k.get("betrag"), k.get("waehrung") or "€")
    kopf = f"{emo} {titel} · {quelle}"
    if art in ("serie", "pserie") and x.get("n"):
        kopf += f" · {x['n']}× in a row"
    zeit = ""
    if k.get("live"):
        st = k.get("stand")
        zeit = "🔴 LIVE" + (f" {k['minute']}'" if k.get("minute") else "") + (f" · {st[0]}-{st[1]}" if isinstance(st, (list, tuple)) and len(st) == 2 else "")
    elif k.get("anpfiff"):
        t = _ts(k["anpfiff"])
        if t and t > jetzt:
            zeit = f"⏰ {t.astimezone(WIEN):%H:%M} CET" + ("" if t.astimezone(WIEN).date() == jetzt.astimezone(WIEN).date() else f" ({t.astimezone(WIEN):%d %b})")
    wo = " · ".join(v for v in (spiel, k.get("liga") if k.get("liga") and not str(k.get("liga")).isupper() else None) if v)
    at = f" @ {q:.2f}" if isinstance(q, (int, float)) else ""
    fenster = f" in {x['min']} min" if x.get("min") and art in ("zufluss", "serie", "pserie") else ""
    zeilen = [kopf, "", f"💰 {geld} on {sel}{at}{fenster}", f"{SPORT_EMOJI.get(k.get('sport') or 'Fußball', '⚽')} {wo}"]
    if zeit:
        zeilen.append(zeit)
    block, extra = [], []
    if art == "wal":
        for name, p, mein in (x.get("bars") or [])[:3]:
            name = "Draw" if name == "Remis" else name
            block.append(f"{round(p * 100):>2}% {_kaesten(p * 100, farbe if mein else '⬛', 8)} {name}{' ◀' if mein else ''}")
        if x.get("pnl") and x["pnl"] > 0:
            extra.append(f"🧠 Wallet up {_geld(x['pnl'], '$')}" + (f", beats the closing price by {x['clv']:.1f}pp on avg" if isinstance(x.get("clv"), (int, float)) and x["clv"] > 0 else ""))
        if x.get("faktor") and x["faktor"] >= 1.5:
            extra.append(f"🔥 {x['faktor']:.1f}× its usual stake")
        if x.get("ein") and x.get("preis") and abs(x["preis"] - x["ein"]) >= 0.01:
            extra.append(f"📈 Entry {round(x['ein'] * 100)}¢ → now {round(x['preis'] * 100)}¢")
    elif art in ("serie", "pserie", "zufluss"):
        if x.get("anteil"):
            block.append(f"{_kaesten(x['anteil'], farbe)} {round(x['anteil'])}% of all new money in this market")
        sp = _spark(x.get("saeulen"))
        if sp:
            block.append(f"{sp} last {'2h' if art == 'serie' else '4h'}")
        q0 = x.get("q0") or x.get("qv")
        if isinstance(q0, (int, float)) and isinstance(q, (int, float)) and abs(q - q0) >= 0.03:
            extra.append(f"{'📉' if q < q0 else '📈'} Odds {q0:.2f} → {q:.2f}")
    elif art == "pinn" and x.get("von") and x.get("nach"):
        # Pinnacle: der Kurs ist das Signal — Betrag nur, wenn er etwas sagt
        zeilen[2] = f"🎯 {sel}: {x['von']:.2f} → {x['nach']:.2f} at Pinnacle"
        if isinstance(x.get("vonPct"), (int, float)) and isinstance(x.get("nachPct"), (int, float)):
            block.append(f"Implied chance {x['vonPct']:.1f}% → {x['nachPct']:.1f}%" + (f" (+{x['pp60']:.1f}pp in the last hour)" if x.get("pp60") else ""))
        sp = _spark(x.get("verlauf"))
        if sp:
            block.append(f"{sp} Pinnacle chance")
        if x.get("bfVon") and x.get("bfNach"):
            extra.append(f"📉 Betfair follows: {x['bfVon']:.2f} → {x['bfNach']:.2f}")
        if (x.get("bfNeu") or 0) >= 1000:
            extra.append(f"💶 +{_geld(x['bfNeu'], '€')} new on Betfair")
        if x.get("poly"):
            extra.append(f"🟣 +{_geld(x['poly'], '$')} on Polymarket")
    elif art == "stake":
        if x.get("wetten"):
            extra.append(f"🎯 {x['wetten']} big tickets within minutes")
        if x.get("faktor"):
            extra.append(f"📏 {x['faktor']:.1f}× the usual money for this league")
    if art == "pinn" and not x.get("von"):
        return None                                   # alte Kandidaten ohne Kurs: lieber nicht posten (Betrag sagt nichts)
    teile = zeilen + ([""] + block if block else []) + ([""] + extra if extra else []) + ["", _tags(k, quelle)]
    t = "\n".join(teile).strip()
    while _gewicht(t) > 280 and (extra or block):     # zu lang: erst Zusatz, dann Balken kürzen
        (extra if extra else block).pop()
        teile = zeilen + ([""] + block if block else []) + ([""] + extra if extra else []) + ["", _tags(k, quelle)]
        t = "\n".join(teile).strip()
    return t if _gewicht(t) <= 280 else None


def _ruhe(jetzt):
    h = jetzt.astimezone(WIEN).hour
    return RUHE_VON <= h < RUHE_BIS


def lauf(pub_stand, jetzt, entwurf_senden, public_norm=lambda s: s) -> int:
    """Bis zu (3 je Stunde) frische Kandidaten posten bzw. als Entwurf senden. Gibt die Zahl zurück."""
    st = _laden(STAND, {"gepostet": [], "werbung": None})
    q = _laden(QUEUE, [])
    if _ruhe(jetzt):
        return 0
    stunde = [t for t in st["gepostet"] if _ts(t[1]) and jetzt - _ts(t[1]) < timedelta(hours=1)]
    frei = max(0, JE_STUNDE - len(stunde))
    schon = {t[0] for t in st["gepostet"]}
    spiele_schon = {t[2] for t in st["gepostet"] if len(t) > 2 and _ts(t[1]) and jetzt - _ts(t[1]) < timedelta(hours=3)}
    public_heute = set((pub_stand or {}).get("spiele") or [])
    kand = [k for k in q if k.get("art") not in ARTEN_AUS and k["key"] not in schon and _ts(k["ts"]) and jetzt - _ts(k["ts"]) <= timedelta(minutes=FRISCH_MIN)
            and public_norm(k["spiel"]) not in public_heute and public_norm(k["spiel"]) not in spiele_schon]
    kand.sort(key=lambda k: -(k.get("betrag") or 0))
    n = 0
    for k in kand:
        if n >= frei:
            break
        if public_norm(k["spiel"]) in spiele_schon:
            continue
        t = text(k, jetzt)
        if not t:
            continue
        if posten(t, entwurf_senden):
            st["gepostet"].append([k["key"], jetzt.isoformat(), public_norm(k["spiel"])])
            spiele_schon.add(public_norm(k["spiel"]))
            n += 1
    # alle 3 Tage ein Hinweis auf die Telegram-Gruppe (10–22 Uhr), zählt nicht gegen die 3 je Stunde
    w = _ts(st.get("werbung"))
    if 10 <= jetzt.astimezone(WIEN).hour < 22 and (not w or jetzt - w >= timedelta(hours=WERBUNG_ALLE_H)):
        t, _ = _wahl(WERBUNG, jetzt.strftime("%Y-%m-%d"))
        if posten(t.format(tg=TELEGRAM), entwurf_senden, mit_link=True):
            st["werbung"] = jetzt.isoformat()
    st["gepostet"] = [t for t in st["gepostet"] if _ts(t[1]) and jetzt - _ts(t[1]) < timedelta(days=3)]
    _speichern(STAND, st)
    return n


def _live():
    return os.environ.get("X_LIVE") == "1" and all(os.environ.get(k) for k in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET"))


def posten(t, entwurf_senden, mit_link=False) -> bool:
    if not _live():
        return bool(entwurf_senden(f"𝕏 <b>Entwurf</b>{' (mit Link)' if mit_link else ''} · {len(t)} Zeichen\n\n<pre>{html.escape(t)}</pre>"))
    return _tweet(t)


def _tweet(t, medien=None) -> bool:
    """POST https://api.x.com/2/tweets mit OAuth 1.0a (Nutzer-Kontext)."""
    url = "https://api.x.com/2/tweets"
    ck, cs = os.environ["X_API_KEY"], os.environ["X_API_SECRET"]
    tk, ts_ = os.environ["X_ACCESS_TOKEN"], os.environ["X_ACCESS_SECRET"]
    o = {"oauth_consumer_key": ck, "oauth_nonce": secrets.token_hex(16), "oauth_signature_method": "HMAC-SHA1",
         "oauth_timestamp": str(int(time.time())), "oauth_token": tk, "oauth_version": "1.0"}
    enc = lambda x: urllib.parse.quote(str(x), safe="~")  # noqa: E731
    basis = "&".join(["POST", enc(url), enc("&".join(f"{enc(k)}={enc(v)}" for k, v in sorted(o.items())))])
    sig = base64.b64encode(hmac.new(f"{enc(cs)}&{enc(ts_)}".encode(), basis.encode(), hashlib.sha1).digest()).decode()
    o["oauth_signature"] = sig
    kopf = "OAuth " + ", ".join(f'{enc(k)}="{enc(v)}"' for k, v in sorted(o.items()))
    req = urllib.request.Request(url, data=json.dumps({"text": t, **({"media": {"media_ids": medien}} if medien else {})}).encode(), method="POST",
                                 headers={"Authorization": kopf, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return 200 <= r.status < 300
    except Exception as exc:  # noqa: BLE001
        print(f"  x: Fehler {str(exc)[:120]}")
        return False


# ── Bilder (Lucas 10.11. 12:48: „schöne Grafiken dazwischen") ───────────────────────────────────────────────
def bild_posten(bild, t, foto_entwurf) -> bool:
    """Bild + Text: Entwurf → Foto in den Testkanal; live → Upload bei X, dann Post mit dem Bild."""
    if not _live():
        return bool(foto_entwurf(bild, f"𝕏 Entwurf (Bild) · {len(t)} Zeichen\n\n{html.escape(t)}"))
    mid = _medien_upload(bild)
    return bool(mid) and _tweet(t, [mid])


def _oauth_kopf(methode, url, extra=None):
    ck, cs = os.environ["X_API_KEY"], os.environ["X_API_SECRET"]
    tk, ts_ = os.environ["X_ACCESS_TOKEN"], os.environ["X_ACCESS_SECRET"]
    o = {"oauth_consumer_key": ck, "oauth_nonce": secrets.token_hex(16), "oauth_signature_method": "HMAC-SHA1",
         "oauth_timestamp": str(int(time.time())), "oauth_token": tk, "oauth_version": "1.0"}
    enc = lambda x: urllib.parse.quote(str(x), safe="~")  # noqa: E731
    alle = {**o, **(extra or {})}
    basis = "&".join([methode, enc(url), enc("&".join(f"{enc(k)}={enc(v)}" for k, v in sorted(alle.items())))])
    o["oauth_signature"] = base64.b64encode(hmac.new(f"{enc(cs)}&{enc(ts_)}".encode(), basis.encode(), hashlib.sha1).digest()).decode()
    return "OAuth " + ", ".join(f'{enc(k)}="{enc(v)}"' for k, v in sorted(o.items()))


def pruefen() -> bool:
    """Nur lesend: GET /2/users/me — stimmen die vier Schlüssel, welcher Account gehört dazu? Postet nichts."""
    fehlt = [k for k in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET") if not os.environ.get(k)]
    if fehlt:
        print(f"  x-test: fehlende Secrets {fehlt}")
        return False
    print("  x-test: Längen", {k: len(os.environ[k]) for k in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")})
    url = "https://api.x.com/2/users/me"
    req = urllib.request.Request(url, headers={"Authorization": _oauth_kopf("GET", url)})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = (json.loads(r.read().decode()).get("data") or {})
            print(f"  x-test: OK → @{d.get('username')} ({d.get('name')}) · X_LIVE={os.environ.get('X_LIVE') or 'aus'}")
            return True
    except urllib.error.HTTPError as exc:
        print(f"  x-test: HTTP {exc.code} {exc.read().decode()[:300]}")
    except Exception as exc:  # noqa: BLE001
        print(f"  x-test: Fehler {str(exc)[:200]}")
    return False


def _medien_upload(bild):
    """POST /2/media/upload (multipart, OAuth 1.0a). Gibt die media id zurück oder None."""
    url = "https://api.x.com/2/media/upload"
    grenze = "----cocobet" + secrets.token_hex(8)
    daten = Path(bild).read_bytes()
    body = (f"--{grenze}\r\nContent-Disposition: form-data; name=\"media_category\"\r\n\r\ntweet_image\r\n"
            f"--{grenze}\r\nContent-Disposition: form-data; name=\"media\"; filename=\"{Path(bild).name}\"\r\n"
            f"Content-Type: image/jpeg\r\n\r\n").encode() + daten + f"\r\n--{grenze}--\r\n".encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={"Authorization": _oauth_kopf("POST", url),
                                                                          "Content-Type": f"multipart/form-data; boundary={grenze}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            j = json.loads(r.read().decode())
            return (j.get("data") or {}).get("id") or j.get("media_id_string")
    except Exception as exc:  # noqa: BLE001
        print(f"  x: Bild-Upload fehlgeschlagen {str(exc)[:160]}")
        return None
