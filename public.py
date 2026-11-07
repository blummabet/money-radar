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
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BASIS = Path(__file__).resolve().parent
STAND = BASIS / ".push_state" / "public.json"
WIEN = ZoneInfo("Europe/Vienna")
REGEL = "R13"   # R13 07.11. ~17:00 Liga-Wertung im Topf (±4 Punkte aus eigener Liga-Bilanz) · R12 07.11. ~16:05 Polymarket-Dauerzufluss wieder raus (Lucas „0815-Dreck"; über alles n=384, 56 % bei 54 % erwartet, +4 %) · R11 07.11. ~13:30 Premier League in keiner Art im Kanal · R10 07.11. ~8:45 Polymarket-Dauerzufluss in den Kanal; Stake ohne eigene Quote nimmt die Betfair-Quote · R9 05.11. ~20:15 Live-Kandidat nur ohne Tor seit dem Signal und höchstens 10 Min alt · R8 05.11. ~6:15 Live-Dauerzufluss mit Quote +15 % ohne Tor (Lay-Verdacht) nicht in den Kanal · R7 04.11. ~12:40 Betfair-Geld auf Unter nicht in den Kanal · R6 03.11. ~13:10 live Dauerzufluss: nur Geld seit dem letzten Tor zählt (Kobe – Ratchaburi) · R5 02.11. 21:30 Dauerzufluss aus allen Ligen, 21:35 Quote ab 1,50 überall (Geld vor Anpfiff weiter nur große Ligen) · (R4 ab 02.11. 16 Uhr: Live-Kandidaten dürfen den nächsten Termin vorziehen, ab 18 Uhr erst ab 10 Punkten) · R1 31.10. 10:30 · R2 31.10. 13:10 ohne PL-Betfair-Signale · R3 02.11. ~14:15 Wal nur ≥ 2× üblicher Einsatz, max. 1 je Fenster
#                · R4 02.11. ~15 Uhr: sammeln statt sofort — Topf, Punkte, drei Termine je Fenster
WAL_MIN_FAKTOR = 2.0
WAL_JE_FENSTER = 1
AB = "2026-10-31T10:30:00+00:00"
ZIEL = 60
JE_FENSTER = 3
FENSTER = ((0, 12, "12:00"), (12, 16, "16:00"), (16, 20, "20:00"), (20, 24, None))   # von, bis, „nächste ab"
ARTEN = {"serie", "zufluss", "fix", "stake", "wal"}   # pserie war R10 (07.11. 8:45) – R12 (07.11. 16:05) wieder raus
NUR_ALLE_LIGEN = {"stake", "fix"}

GROSS = re.compile(
    r"(English Premier League|^Premier League$|La Liga|Bundesliga|Italian Serie [AB]|^Serie [AB]$|Ligue [12]|Championship$|"
    r"Eredivisie|Primeira|Belgian|Jupiler|Scottish Premiership|Super Lig|Turkish Super|UEFA Champions League|"
    r"UEFA Europa League|UEFA Conference League|Austrian|Swiss Super|Danish Superliga|Segunda|FA Cup|DFB|Copa del Rey|"
    r"Coppa Italia|Brazilian Serie A|AFC Champions League$)", re.I)

# Lucas 02.11. („so simpel wie möglich, dass es der dümmste User versteht"): Namen nach der Quelle, je Nachricht nur
# noch die Geld-Zeile + höchstens der Anteil-Balken, Fußzeile ohne „heute N Geldbewegungen erkannt".
KOPF = {"serie": ("🔁", "BETFAIR INFO"), "zufluss": ("💶", "BETFAIR VOR ANPFIFF"), "fix": ("🚨", "AUFFÄLLIGES GELD"),
        "stake": ("🟢", "STAKE.com GELD"), "wal": ("🐋", "POLYMARKET WAL"), "pserie": ("🔁", "POLYMARKET INFO")}
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
    # Regel 2 (Lucas 31.10. 13:03, wie im alten Projekt): Premier League ohne Betfair-Geld-Signale — dort liegt
    # immer das meiste Geld, Dauerzufluss/Zufluss sagen nichts (seit 16.10.: 17 Tipps, −48 % / −18 %).
    # Stake-Burst und Wal bleiben (10 Tipps +85 %, 2 Tipps +94 %).
    if art in ("serie", "zufluss") and re.search(r"^(English )?Premier League$", liga, re.I):
        return False
    # Regel 11 (Lucas 07.11. 13:23, Leeds – Tottenham „$34.700 auf Tottenham" über Polymarket: „Bitte Premier League immer
    # raus — Drecks-Liga zum Wetten"): Premier League in KEINER Art mehr im Kanal (auch Stake, Wal, Polymarket).
    # Polymarket nennt sie „EPL". Trades-Channel und Buch bleiben.
    if re.search(r"^(English )?Premier League$|^EPL$", liga.strip(), re.I):
        return False
    # Regel 3 (Lucas 02.11., Penafiel – Feirense „$2.770 auf Penafiel — wieso kam der?"): ein Wal nur, wenn die Wallet
    # mindestens doppelt so viel setzt wie sonst (Überzeugung statt Größe). Vor GROSS geprüft — sonst ließ „Segunda"
    # (gemeint war die spanische) auch die portugiesische 2. Liga ohne diese Prüfung durch.
    if art == "wal" and not (isinstance(m.get("faktor"), (int, float)) and m["faktor"] >= WAL_MIN_FAKTOR):
        return False
    # Regel 7 (Lucas 04.11., Pohang – G-Osaka 12' 0:0 „Unter 2,5 @ 1,51", Endstand 2:3: „im alten Projekt haben wir Under
    # rausgenommen … bei hohen Einsätzen auf Unter ist viel gelayed"): Betfair-Geld auf Unter geht nicht in den Kanal.
    # Push in den Trades-Channel und Buch bleiben (gemessen wird weiter). Stake/Wal sind nicht betroffen (dort gibt es kein Lay).
    if art in ("serie", "zufluss", "fix", "live") and re.match(r"^\s*(under|unter)\b", str(m.get("auswahlRoh") or m.get("auswahl") or ""), re.I):
        return False
    # Regel 8 (Lucas 05.11. 06:08, Athletico-PR – Vasco 10' 0:0: €72k auf Athletico, Quote trotzdem 2,48 → 2,94, Endstand
    # 1:3): Live-Dauerzufluss im Sieg-Markt, dessen Quote seit Serienbeginn ohne Tor um mehr als 15 % stieg, geht nicht in
    # den Kanal — gematchtes Geld war dann eher Lay. Trades-Push (mit ⚠️) und Buch bleiben, gemessen wird weiter.
    if art == "serie" and m.get("layVerdacht"):
        return False
    # Regel 5 (02.11. 21:35, Lucas „allgemein auf 1,5?"): Quote ab 1,50 für alle Arten außer Geld vor Anpfiff (Band 1,50–1,60:
    # 199 Tipps +8,0 %, besser als 1,60–1,80 mit +2,0 %; Geld vor Anpfiff dort −11,3 %), in allen Ligen gleich.
    if art == "zufluss":
        return bool(GROSS.search(liga)) and 1.5 <= q < 3.0   # 02.11. 21:32 Lucas: „lass die 1,5" — einheitlich
    if art == "wal":                      # Lucas 31.10. 11:23: Wale in jeder Liga + E-Sport (seit 16.10.: 77 · +10,9 %)
        return 1.5 <= q < 3.0
    # Regel 5 (Lucas 02.11. 21:29: „diese Sperre hab ich nie definiert — ich bin größter Fan von kleinen Ligen und viel
    # Geld"): Dauerzufluss aus ALLEN Ligen. Gemessen: kleine Ligen 315 Tipps +10,9 % (große 240 · +7,6 %).
    # Regel 10 (Lucas 07.11. 08:30, „mach beides, dann schauen wir, wie die nächsten Tage laufen"): Polymarket-Dauerzufluss
    # wie der Betfair-Dauerzufluss (seit 02.11.: 123 Tipps, 59 % getroffen bei 53 % erwartet, +9,3 %).
    if art in ("serie", "pserie"):
        return 1.5 <= q < 3.0
    return art in NUR_ALLE_LIGEN and 1.5 <= q < 3.0     # Stake + Auffälliges Geld: alle Ligen ab 1,50 (vorher kleine Ligen erst ab 1,80)


# ── Public-Regel 4 (Lucas 02.11.: „ein bisschen sammeln, sehen wo Bewegung ist … sonst wird der erstbeste Blödsinn
# gepostet"; „was am Vormittag kommt, wird weiter beobachtet und wird stärker, je näher das Spiel kommt") ─────────────
# Jedes passende Signal kommt in den Topf, je Spiel + Tipp EIN Kandidat; spätere Signale zum selben Tipp machen ihn stärker.
# Der Topf überlebt die Fenster (Vormittags-Geld zählt im Fenster 12–16 mit). Zu drei Terminen je Fenster geht der beste
# Kandidat raus; ein sehr starker sofort; einer kurz vor Anpfiff darf einen Termin vorziehen.
TERMINE = {0: (540, 620, 700), 1: (60, 140, 220), 2: (60, 140, 220), 3: (60, 140, 220)}   # Minuten ab Fensterbeginn (0–12: 9:00 10:20 11:40)
MIN_PUNKTE, EXPRESS, LETZTE_CHANCE, NAH_H = 5.0, 14.0, 9.0, 4
LIVE_VORZIEHEN = 10.0  # 02.11. 16 Uhr: ein Live-Kandidat lebt nur 20 Min, die Termine liegen 80 Min auseinander → er darf vorziehen
QUELLE = {"serie": "betfair", "zufluss": "betfair", "fix": "betfair", "stake": "stake", "wal": "poly", "pserie": "poly"}


def _key(m):
    return f"{_norm(m.get('spiel'))}|{_norm(m.get('auswahlRoh') or m.get('auswahl'))}"


def sammeln(s, m, jetzt):
    """Signal in den Topf (nur wenn es die Regel erfüllt und das Spiel heute noch nicht gepostet wurde)."""
    if not passt(m) or not _norm(m.get("spiel")) or _norm(m.get("spiel")) in s.get("spiele", []):
        return
    t = s.setdefault("topf", {})
    k = _key(m)
    c = t.get(k) or {"erst": jetzt.isoformat(), "ereignisse": []}
    c["m"] = {x: v for x, v in m.items()}
    c["letzt"] = jetzt.isoformat()
    c["ereignisse"] = (c["ereignisse"] + [{"zeit": jetzt.isoformat(), "art": m.get("art"), "eur": m.get("eur"), "n": m.get("n"),
                                            "faktor": m.get("faktor")}])[-30:]
    t[k] = c


# Regel 13 (Lucas 07.11. 16:44, „ein Ranking nach Liga — welche Liga trifft am besten, die kriegt Bonuspunkte"):
# je Liga Bonus/Malus aus unseren eigenen abgerechneten Signalen (Betfair, Stake, Wal): Treffer minus Erwartung der
# Quote, gedämpft mit n/(n+30), erst ab 20 Tipps, höchstens ±4 Punkte. push.py füllt LIGA_WERT vor der Auswahl.
LIGA_WERT: dict = {}
LIGA_MIN_N, LIGA_DAEMPFUNG, LIGA_FAKTOR, LIGA_MAX = 20, 30, 0.4, 4.0


def liga_schluessel(liga):
    l = LIGA_KURZ.get(str(liga or "").strip(), str(liga or "").strip())
    return {"epl": "premier league", "english premier league": "premier league"}.get(l.lower(), l.lower())


def liga_werte(buch):
    """buch: Bilanz-Einträge → {liga: (punkte, n, treffer %, erwartet %)}. REIN."""
    g = {}
    for b in buch:
        if b.get("status") != "abgerechnet" or b.get("art") not in ("serie", "zufluss", "live", "stake", "wal", "fix"):
            continue
        q = b.get("quote")
        if not isinstance(q, (int, float)) or q <= 1 or b.get("win") is None or (b.get("r") == 0 and not b.get("win")):
            continue
        k = liga_schluessel(b.get("liga"))
        x = g.setdefault(k, [0, 0.0, 0.0])
        x[0] += 1; x[1] += 1 if b.get("win") else 0; x[2] += 1 / q
    out = {}
    for k, (n, t, e_) in g.items():
        if n < LIGA_MIN_N:
            continue
        diff_pp = 100 * (t - e_) / n
        pkt = max(-LIGA_MAX, min(LIGA_MAX, diff_pp * n / (n + LIGA_DAEMPFUNG) * LIGA_FAKTOR))
        out[k] = (round(pkt, 1), n, round(100 * t / n), round(100 * e_ / n))
    return out


def punkte(c, jetzt):
    p = _punkte_roh(c, jetzt)
    if p is None:
        return None
    w = LIGA_WERT.get(liga_schluessel((c.get("m") or {}).get("liga")))
    return round(p + (w[0] if w else 0), 1)


def _punkte_roh(c, jetzt):
    """Stärke eines Kandidaten — None = nicht mehr spielbar (verfällt). REIN.
    Grundwert 2 · +1 je weiterem Signal (max. 5) · +4 je weiterer Quelle (Betfair/Stake/Polymarket) · Geld: +2 je
    Verdopplung über €5k (max. 8, je Art der höchste Stand, dann summiert) · Dauerzufluss-Kette: +½ je Glied (max. 4)
    · Wal: + sein Vielfaches des üblichen Einsatzes (max. 4) · −3, wenn seit 90 Min nichts mehr kam."""
    m, ev = c.get("m") or {}, c.get("ereignisse") or []
    if not ev:
        return None
    letzt = _ts(c.get("letzt"))
    alter = (jetzt - letzt).total_seconds() / 60 if letzt else 999
    if m.get("live"):
        if alter > 20 or (isinstance(m.get("minute"), (int, float)) and m["minute"] > 75):
            return None
    else:
        ko = _ts(m.get("anpfiff"))
        if alter > 240 or not ko or (ko - jetzt).total_seconds() < 20 * 60:
            return None
    quellen = {QUELLE.get(e.get("art")) for e in ev}
    je_art = {}
    for e in ev:
        if isinstance(e.get("eur"), (int, float)):
            je_art[e["art"]] = max(je_art.get(e["art"], 0), e["eur"])
    geld = sum(je_art.values())
    kette = max([e.get("n") or 0 for e in ev if e.get("art") in ("serie", "pserie")] or [0])
    p = 2 + min(5, len(ev) - 1) + 4 * (len(quellen) - 1) + (min(8, max(0, 2 * math.log2(geld / 5000))) if geld > 0 else 0) \
        + min(4, kette / 2) + min(4, max([e.get("faktor") or 0 for e in ev if e.get("art") == "wal"] or [0])) \
        - (3 if alter > 90 else 0)
    return round(p, 1)


def _termine_vorbei(f, jetzt):
    w = jetzt.astimezone(WIEN)
    ab = (w.hour - FENSTER[f][0]) * 60 + w.minute
    return sum(1 for t in TERMINE[f] if ab >= t)


LIVE_FRISCH_MIN = 10   # Regel 9


def _ueberholt(c, jetzt, aktuell):
    """Public-Regel 9 (Lucas 05.11. 20:05, Sparta Prag – Bournemouth: Signal zur Pause bei 1:0, im Kanal erst ~20 Min
    später mit „45' · 1:0" — da stand es längst 1:1, Lucas hat es als void gewertet): ein Live-Kandidat geht nur raus,
    solange seit dem Spielstand im Signal KEIN Tor fiel und das letzte Signal höchstens 10 Min alt ist."""
    m = c.get("m") or {}
    if not m.get("live"):
        return False
    letzt = _ts(c.get("letzt"))
    if not letzt or (jetzt - letzt).total_seconds() > LIVE_FRISCH_MIN * 60:
        return True
    jetzt_stand = aktuell(m) if aktuell else None
    alt = m.get("stand")
    return bool(jetzt_stand and alt and list(jetzt_stand) != list(alt))


def auswahl(s, jetzt, aktuell=None):
    """Was jetzt aus dem Topf in den Kanal geht: [(m, (nr, fenster), punkte, grund)]. Räumt Verfallenes ab
    (mit seinen besten Punkten als „nicht gesendet" in der Liste — so lässt sich später messen, ob die Auswahl besser trifft).
    aktuell(m) → aktueller Spielstand [h, a] oder None (Regel 9)."""
    if jetzt < _ts(AB):
        return []
    t = s.setdefault("topf", {})
    bewertet = []
    for k, c in list(t.items()):
        p = None if _ueberholt(c, jetzt, aktuell) else punkte(c, jetzt)
        if p is None or _norm((c.get("m") or {}).get("spiel")) in s.get("spiele", []):
            m = c.get("m") or {}
            if c.get("best") is not None and _norm(m.get("spiel")) not in s.get("spiele", []):
                s["liste"].append({"zeit": (c.get("bestZeit") or c.get("letzt") or "")[:16], "art": m.get("art"), "spiel": m.get("spiel"),
                                   "auswahl": m.get("auswahlRoh") or m.get("auswahl"), "quote": m.get("quote"), "liga": m.get("liga"),
                                   "live": bool(m.get("live")), "regel": REGEL, "gesendet": False, "punkte": c.get("best"),
                                   "signale": len(c.get("ereignisse") or [])})
            t.pop(k, None)
            continue
        if c.get("best") is None or p > c["best"]:
            c["best"], c["bestZeit"] = p, jetzt.isoformat()
        bewertet.append((p, k, c))
    bewertet.sort(key=lambda x: -x[0])
    f = _fenster(jetzt)
    gesendet = s["fenster"].get(str(f)) or 0
    vorbei = _termine_vorbei(f, jetzt)
    aus = []
    for p, k, c in bewertet:
        if gesendet >= JE_FENSTER:
            break
        m = c["m"]
        if m.get("art") == "wal" and ((s.get("arten") or {}).get(str(f)) or []).count("wal") + sum(1 for x in aus if x[0].get("art") == "wal") >= WAL_JE_FENSTER:
            continue
        ko = _ts(m.get("anpfiff"))
        bald = bool(not m.get("live") and ko and (ko - jetzt).total_seconds() < 40 * 60)
        # Lucas: Geld vom Vormittag für ein Abendspiel wird weiter beobachtet und stärker — zu einem Termin nur, was live
        # ist oder in den nächsten 4 h anpfeift (früher nur, wenn es „sehr stark" ist)
        nah = bool(m.get("live") or (ko and (ko - jetzt).total_seconds() <= NAH_H * 3600))
        if vorbei - gesendet > 0 and p >= MIN_PUNKTE and nah:
            grund = "Termin"
        elif p >= EXPRESS:
            grund = "sehr stark"
        elif bald and p >= LETZTE_CHANCE and vorbei + 1 - gesendet > 0:
            grund = "kurz vor Anpfiff"
        elif m.get("live") and p >= LIVE_VORZIEHEN and vorbei + 1 - gesendet > 0:
            grund = "live"
        else:
            continue
        gesendet += 1
        aus.append((m, (gesendet, f), p, grund))
        t.pop(k, None)
    return aus


def export(s, jetzt):
    """Für die Seite (Statistik → Public-Kanal, Lucas 02.11.: „den Topf irgendwo darstellen"): was gerade drinliegt,
    mit Punkten, und wie die Termine im laufenden Fenster stehen."""
    f = _fenster(jetzt)
    v, b, _ = FENSTER[f]
    gesendet = s["fenster"].get(str(f)) or 0
    ab = jetzt.astimezone(WIEN).replace(hour=v, minute=0, second=0, microsecond=0)
    termine = []
    for i, t in enumerate(TERMINE[f]):
        zt = ab + timedelta(minutes=t)
        termine.append({"uhr": zt.strftime("%H:%M"), "vorbei": zt <= jetzt.astimezone(WIEN), "gefuellt": i < gesendet})
    kand = []
    for k, c in (s.get("topf") or {}).items():
        p = punkte(c, jetzt)
        if p is None:
            continue
        m, ev = c.get("m") or {}, c.get("ereignisse") or []
        ko = _ts(m.get("anpfiff"))
        je_art = {}
        for e in ev:
            if isinstance(e.get("eur"), (int, float)):
                je_art[e["art"]] = max(je_art.get(e["art"], 0), e["eur"])
        kand.append({"spiel": m.get("spiel"), "auswahl": m.get("auswahl"), "markt": m.get("markt"), "liga": LIGA_KURZ.get(m.get("liga"), m.get("liga")),
                     "sport": m.get("sport"), "quote": m.get("quote"), "live": bool(m.get("live")), "minute": m.get("minute"),
                     "anpfiff": m.get("anpfiff"), "punkte": p, "signale": len(ev), "quellen": sorted({QUELLE.get(e.get("art")) for e in ev} - {None}),
                     "arten": [e.get("art") for e in ev][-8:], "geld": round(sum(je_art.values())), "seit": c.get("erst"), "zuletzt": c.get("letzt"),
                     "nah": bool(m.get("live") or (ko and (ko - jetzt).total_seconds() <= NAH_H * 3600))})
    kand.sort(key=lambda x: -x["punkte"])
    heute = [x for x in s.get("liste") or [] if x.get("zeit", "")[:10] == jetzt.strftime("%Y-%m-%d") and str(x.get("regel") or "") >= "R4"]
    return {"stand": jetzt.isoformat(), "regel": REGEL, "fenster": f"{v}–{b} Uhr", "gesendet": gesendet, "max": JE_FENSTER, "termine": termine,
            "schwellen": {"termin": MIN_PUNKTE, "sofort": EXPRESS, "live": LIVE_VORZIEHEN, "kurzVorAnpfiff": LETZTE_CHANCE, "nahStunden": NAH_H},
            "kandidaten": kand[:15], "heute": [{"zeit": x.get("zeit"), "spiel": x.get("spiel"), "auswahl": x.get("auswahl"), "quote": x.get("quote"),
                                                 "punkte": x.get("punkte"), "grund": x.get("grund"), "gesendet": x.get("gesendet", True)} for x in heute][-20:]}


def laden(jetzt):
    try:
        s = json.loads(STAND.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        s = {}
    tag = jetzt.astimezone(WIEN).strftime("%Y-%m-%d")
    if s.get("tag") != tag:
        s.update({"tag": tag, "fenster": {}, "spiele": [], "arten": {}})
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
    # Regel 3: höchstens 1 Wal je Fenster — die übrigen Plätze bleiben für Betfair/Stake
    if m.get("art") == "wal" and ((s.get("arten") or {}).get(str(f)) or []).count("wal") >= WAL_JE_FENSTER:
        return None
    n = (s["fenster"].get(str(f)) or 0) + 1
    return (n, f) if n <= JE_FENSTER else None


def buchen(s, m, nr_f, jetzt, punkte=None, grund=None):
    n, f = nr_f
    s["fenster"][str(f)] = n
    s.setdefault("arten", {}).setdefault(str(f), []).append(m.get("art"))
    s["spiele"].append(_norm(m.get("spiel")))
    s["liste"].append({"zeit": jetzt.strftime("%Y-%m-%dT%H:%M"), "art": m.get("art"), "spiel": m.get("spiel"),
                       "auswahl": m.get("auswahlRoh") or m.get("auswahl"), "quote": m.get("quote"), "liga": m.get("liga"),
                       "live": bool(m.get("live")), "regel": REGEL, "faktor": m.get("faktor"),
                       "gesendet": True, "punkte": punkte, "grund": grund})


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
        lv = "⚡ <b>LIVE" + (f" {m['minute']}'" if m.get("minute") else "") + "</b>"
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
    z += [x for x in m.get("gruende") or [] if x and ("▰" in x or "▱" in x)][:1]
    z.append("")
    bis = FENSTER[f][1]
    z.append(f"📡 Signal {n} von {JE_FENSTER} " + (f"bis {bis} Uhr" if bis < 24 else "heute Nacht"))
    if n == JE_FENSTER and FENSTER[f][2]:
        z.append(f"⏳ Nächste Signale ab {FENSTER[f][2]}")
    return "\n".join(z)
