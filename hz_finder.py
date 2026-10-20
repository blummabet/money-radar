#!/usr/bin/env python3
"""
hz_finder.py — Halbzeit 0:0 in Spielen, die vor dem Anpfiff Tore oder einen Heimsieg erwarten liessen. 04.10.2026.

Lucas: „wenn zb inplay 0:0 steht zur Pause, es aber ein Over-2.5-Spiel war bzw. Home win … dann
2. Halbzeit ok zu wetten. Mach ich selbst oft, nur such ich die Spiele dann immer manuell."

WAS GEMESSEN IST (03.10.2026, Betfair-Ledger, Spiele mit HZ 0:0):
    alle                                   n=1.737   Tor in 2. HZ 78 %   Heimsieg 36 %
    Over 2.5 erwartet (Geld-Seite OVER, Quote <= 1,75)   n=504   Tor in 2. HZ 85 %  >=2 Tore 56 %
    Heimfavorit (Heimquote vor Anpfiff <= 1,60)          n=216   Heimsieg 63 %
Fuer den Heimsieg liess sich die HZ-Quote nachmessen (Live-Match-Odds in der Historie): der Markt
preist es ein — Quote sagt 55-65 %, getroffen 56-63 %, ROI je nach Schwelle -2 % bis +9 %, ab
19.09. +1 %. Fuer die Tore gibt es KEINE gespeicherte HZ-Quote. Ob 85 % mehr sind, als die
Over-0.5-Quote zur Pause sagt, ist also offen — genau das misst dieses Buch.

Jeder Fund wird zur Quote IM MOMENT DER PAUSE gebucht (Over 0.5 / Over 1.5 / Heimsieg) und
gegen den Endstand abgerechnet. Am Endstand gesucht, am Signalzeitpunkt gefeuert — dieser Fehler
hat am 03.10. zwei Kandidaten gekostet; hier gibt es keine Schlussquote, nur die Quote zur Pause.

Die Erwartung vor dem Spiel kommt aus denselben Quellen wie die Messung oben:
    Tore    betfair_track_state.json (eingefrorene letzte Vor-Anpfiff-Seite + Quote, O/U 2.5)
    Heim    betfair_history.json (letzte Vor-Anpfiff-Heimquote der Match Odds)
Serien kommen aus team_archiv.json (Betfair-Ergebnisse, auch kleine Ligen).

Ausgabe hz_finder.json (Money Map -> Reiter „⏸️ HZ 0:0"), Push je Lauf gebuendelt in den
Trades-Kanal (HZ_PUSH=0 schaltet ab). Urteil am Erzeuger.
"""
from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime, timedelta, timezone

# money-radar 20.10.2026 (Lucas: „muss autonom im neuen Projekt laufen"): Kopie aus dem alten Projekt. Liest und
# schreibt .bf/ (Actions-Cache); die Vor-Anpfiff-Seite auf O/U 2.5 kommt aus .bf/liga_offen.json (ergebnisse.py),
# Endstände aus .bf/ergebnisse.json. Gesendet wird hier nichts — den HZ-Push macht push.py aus data.json.
BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".bf")
AUSGABE_FILE = "hz_finder.json"
SEEN_FILE = "hz_finder_seen.json"
TORE_MAX_QUOTE = 1.75
HEIM_MAX_QUOTE = 1.60
# 12.10.2026 (Lucas: „probieren wir es mal und zaehl mit, koennen wir immer wieder umstellen").
# Rueckblick 05.09.-11.10. (Betfair-Stand zur Pause, 2 % Kommission):
#   Vorab-Heimquote bis 1.30          n= 60  ROI  -7,0 %  (beide Zeithaelften negativ)
#   Nationalteams bis 1.60            n= 33  ROI -16,5 %
#   1.30-1.60, nur Vereine            n=160  ROI +20,3 %
# Die Grenze 1.30 ist NACHTRAEGLICH aus den Daten gezogen — deshalb im Messregister
# (hz-heim-130) vorwaerts gemessen. Die Tore-Gruppe ist davon unberuehrt.
HEIM_MIN_QUOTE = 1.30
HEIM_REGEL = "1.30-1.60 ohne Nationalteams"
SPAET_MIN, SPAET_MAX = 46, 55          # 2. HZ laeuft schon, steht aber noch 0:0 (Lauf hat die Pause verpasst)
KOMMISSION = 0.02
VERFALL_TAGE = 3
KEEP = 3000
MINDEST_N = 100
Z = 1.645
UG_MIN_N = 30
PUSH_DECKEL = 8
WETTEN = {"tore": ("over05", "over15"), "heim": ("heim",)}
WETT_TEXT = {"over05": "Over 0.5 (Tor in 2. HZ)", "over15": "Over 1.5 (2+ Tore)", "heim": "Heimsieg"}


def _laden(pfad, leer=None):
    try:
        with open(pfad, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return leer


def _runner(m, markt, test):
    for r in (((m.get("markets") or {}).get(markt) or {}).get("runners") or []):
        if test(str(r.get("name") or "")) and isinstance(r.get("odd"), (int, float)) and r["odd"] > 1:
            return float(r["odd"])
    return None


def vor_heimquote(hist_punkte):
    """Letzte Vor-Anpfiff-Heimquote aus betfair_history (Punkte ohne Minute). REIN."""
    best = None
    for p in hist_punkte or []:
        if not isinstance(p, dict) or p.get("min") is not None:
            continue
        hw = (p.get("mo") or {}).get("hw")
        if isinstance(hw, (int, float)) and hw > 1 and (best is None or str(p.get("ts")) >= str(best[0])):
            best = (p.get("ts"), float(hw))
    return best[1] if best else None


def phase(li):
    """'HZ' | '2.HZ' | None — nur bei 0:0. REIN."""
    if not li or li.get("finished") or li.get("goal_v1") != 0 or li.get("goal_v2") != 0:
        return None
    if li.get("is_ht"):
        return "HZ"
    t = li.get("time")
    if isinstance(t, (int, float)) and SPAET_MIN <= t <= SPAET_MAX:
        return "2.HZ"
    return None


def kandidat(m, state_pend, hist_punkte, archiv=None):
    """Ein Spiel -> Fund oder None. REIN."""
    li = m.get("liveInfo") or {}
    ph = phase(li)
    if not ph:
        return None
    sig = ((state_pend or {}).get("signals") or {}).get("Over/Under 2.5 Goals") or {}
    gruppen, vor = [], {}
    if sig.get("fav") == "OVER" and isinstance(sig.get("odd"), (int, float)) and sig["odd"] <= TORE_MAX_QUOTE:
        gruppen.append("tore")
        vor["over25"] = sig["odd"]
    hw = vor_heimquote(hist_punkte)
    if (hw is not None and HEIM_MIN_QUOTE < hw <= HEIM_MAX_QUOTE
            and not ist_nationalteam(m.get("league"))):
        gruppen.append("heim")
        vor["heim"] = hw
    if not gruppen:
        return None
    home = m.get("home")
    quoten = {"over05": _runner(m, "Over/Under 0.5 Goals", lambda s: s.startswith("Over")),
              "over15": _runner(m, "Over/Under 1.5 Goals", lambda s: s.startswith("Over")),
              "heim": _runner(m, "Match Odds", lambda s: s == str(home))}
    serie = None
    if archiv is not None:
        import team_archiv
        serie = {"heim": team_archiv.serie(archiv, home), "gast": team_archiv.serie(archiv, m.get("away"))}
    return {"k": "hz:%s" % m.get("matchId"), "matchId": str(m.get("matchId")), "home": home,
            "away": m.get("away"), "league": m.get("league"), "country": m.get("country"),
            "kickoff": m.get("kickoff"), "phase": ph, "minute": li.get("time"),
            "gruppen": gruppen, "vor": vor, "quoten": quoten, "serie": serie,
            **({"heimRegel": HEIM_REGEL} if "heim" in gruppen else {})}


# ── Frisches Geld rund um die Pause (04.10.2026) ──────────────────────────────────────────
# Lucas: „kein K.-o.-Kriterium, aber wenn auf Betfair zur Pause oder rund um die Pause noch was
# auf Over 1.5 gesetzt wird, waere das ein cooles Signal." Die Betfair-Historie fuehrt O/U 0.5/1.5
# nicht — der Preisstand aber schon, mit dem gematchten Geld je Seite (kumuliert). Also merkt sich
# der Finder in JEDEM Lauf den Geldstand dieser Maerkte fuer laufende 0:0-Spiele; zur Pause ist
# der Zufluss = jetzt minus letzter Stand. Kein Filter: gebucht und angezeigt, damit das Buch am
# Ende sagen kann, ob „Geld auf Over" die Treffer verbessert.
ZUFLUSS_MAERKTE = (("Over/Under 1.5 Goals", "o15"), ("Over/Under 0.5 Goals", "o05"))
ZUFLUSS_MAX_ALTER_MIN = 40


def geldstand(m) -> dict:
    """{'o15': {'over': vol, 'under': vol}, …} aus dem aktuellen Preisstand. REIN."""
    aus = {}
    for markt, kurz in ZUFLUSS_MAERKTE:
        o = u = None
        for r in (((m.get("markets") or {}).get(markt) or {}).get("runners") or []):
            n = str(r.get("name") or "")
            if n.startswith("Over"):
                o = float(r.get("vol") or 0)
            elif n.startswith("Under"):
                u = float(r.get("vol") or 0)
        if o is not None and u is not None:
            aus[kurz] = {"over": o, "under": u}
    return aus


def zufluss(vorher, jetzt_stand, vorher_ts, jetzt) -> dict | None:
    """Frisches Geld je Markt seit dem letzten Lauf. None ohne brauchbaren Vergleich. REIN."""
    t0 = None
    try:
        t0 = datetime.fromisoformat(str(vorher_ts).replace("Z", "+00:00"))
    except ValueError:
        pass
    if not vorher or not t0:
        return None
    minuten = (jetzt - t0).total_seconds() / 60
    if minuten <= 0 or minuten > ZUFLUSS_MAX_ALTER_MIN:
        return None
    aus = {"minuten": round(minuten)}
    for kurz in ("o15", "o05"):
        a, b = (vorher or {}).get(kurz), (jetzt_stand or {}).get(kurz)
        if not a or not b:
            continue
        do, du = max(0.0, b["over"] - a["over"]), max(0.0, b["under"] - a["under"])
        aus[kurz] = {"over": round(do), "under": round(du),
                     "overAnteil": round(do / (do + du), 3) if do + du > 0 else None}
    return aus if len(aus) > 1 else None


ZUFLUSS_MIN_EUR = 200
ZUFLUSS_MIN_ANTEIL = 0.60


def over_zufluss(z) -> bool:
    """Kam rund um die Pause nennenswert frisches Geld auf Over 1.5? REIN."""
    x = (z or {}).get("o15") or {}
    return (x.get("over", 0) + x.get("under", 0)) >= ZUFLUSS_MIN_EUR \
        and (x.get("overAnteil") or 0) >= ZUFLUSS_MIN_ANTEIL


def _zufluss_txt(z) -> str | None:
    if not z:
        return None
    teile = []
    for kurz, lab in (("o15", "O/U 1.5"), ("o05", "O/U 0.5")):
        x = z.get(kurz)
        if not x:
            continue
        summe = x["over"] + x["under"]
        if summe < 1:
            teile.append("%s: kein neues Geld" % lab)
        else:
            teile.append("%s: €%s, davon %d %% auf Over" % (lab, _k(summe), round(100 * x["overAnteil"])))
    return ("💶 <b>Geld letzte %d Min:</b> " % z["minuten"] + " · ".join(teile)) if teile else None


def _k(v):
    return ("%.1fK" % (v / 1000)) if v >= 1000 else "%d" % round(v)


# ── Abrechnen ───────────────────────────────────────────────────────────────────────────────
def ergebnis(ft):
    h, a = ft
    return {"over05": h + a >= 1, "over15": h + a >= 2, "heim": h > a}


def abrechnen(eintraege, endstaende, jetzt) -> list:
    """endstaende: matchId -> (ft, ht). REIN."""
    aus = []
    for e in eintraege:
        if e.get("status") != "pending":
            aus.append(e)
            continue
        e = dict(e)
        es = endstaende.get(e["matchId"])
        if es and es[0]:
            ft, ht = es
            if e.get("phase") == "HZ" and ht is not None and list(ht) != [0, 0]:
                e["status"] = "ungueltig"          # Feed sagte 0:0 zur Pause, das Ergebnis nicht
                e["grund"] = "HZ laut Ergebnis %s" % ht
            else:
                erg = ergebnis(ft)
                e["status"], e["ft"], e["wetten"] = "abgerechnet", ft, {}
                for g in e.get("gruppen") or []:
                    for w in WETTEN[g]:
                        q = (e.get("quoten") or {}).get(w)
                        e["wetten"][w] = {"win": erg[w], "quote": q,
                                          "r": (round((q - 1) * (1 - KOMMISSION), 4) if erg[w] else -1.0) if q else None}
        else:
            try:
                alt = datetime.fromisoformat(str(e.get("gebuchtAt")).replace("Z", "+00:00"))
            except ValueError:
                alt = jetzt
            if jetzt - alt > timedelta(days=VERFALL_TAGE):
                e["status"] = "unaufgeloest"
        aus.append(e)
    return aus


def kennzahlen(paare) -> dict:
    """paare: [(win, quote, r)]. REIN."""
    n = len(paare)
    if not n:
        return {"n": 0, "trefferPct": None, "erwartetPct": None, "roi": None, "ug": None, "og": None}
    rs = [p[2] for p in paare]
    m = sum(rs) / n
    k = {"n": n, "trefferPct": round(100 * sum(1 for p in paare if p[0]) / n, 1),
         "erwartetPct": round(100 * sum(1 / p[1] for p in paare) / n, 1),
         "roi": round(100 * m, 1), "ug": None, "og": None}
    if n >= UG_MIN_N:
        se = math.sqrt(sum((r - m) ** 2 for r in rs) / (n - 1) / n)
        k["ug"], k["og"] = round(100 * (m - Z * se), 1), round(100 * (m + Z * se), 1)
    return k


def urteil(k) -> str:
    if k["n"] < MINDEST_N or k["ug"] is None:
        return "sammelt"
    if k["ug"] > 0:
        return "traegt"
    if k["og"] < 0:
        return "traegt nicht"
    return "offen"


# 09.10.2026 (Lucas: „hab die Spiele der Nationalteams ausgelassen, weil die nicht gut als Streak
# messbar sind mmn — und hab da echt viele Winner mitgenommen"). Die Tabelle mass bisher das
# ganze Feature, nicht das, was er spielt. Ab jetzt je Wette auch Vereine / Nationalteams
# getrennt — ob sein Filter messbar etwas bringt, sagt dann die Tabelle, nicht das Gefuehl.
# Am Wettbewerbsnamen erkannt (Betfair), nicht am Team: eine Positivliste, im Zweifel „Verein".
_NATIONAL_RX = re.compile(
    r"international|nations league|euro qualif|world cup|wc qualif|copa america|"
    r"africa cup|nations cup|afcon|asian cup|gold cup|olympic", re.I)


def ist_nationalteam(league) -> bool:
    """Laenderspiel (A-Team oder Auswahl U15–U23, Frauen wie Maenner)? REIN."""
    return bool(_NATIONAL_RX.search(str(league or "")))


def bericht(eintraege) -> dict:
    aus = {}
    for g, wetten in WETTEN.items():
        for w in wetten:
            paare = [(x["win"], x["quote"], x["r"]) for e in eintraege
                     if e.get("status") == "abgerechnet" and g in (e.get("gruppen") or ())
                     for ww, x in (e.get("wetten") or {}).items() if ww == w and x.get("r") is not None]
            k = kennzahlen(paare)
            # 04.10.2026: dieselbe Wette, aufgeteilt nach frischem Geld auf Over 1.5 rund um die
            # Pause (s. zufluss). Kein eigenes Urteil — nur der Vergleich, ob das Signal etwas traegt.
            mit, ohne, verein, national = [], [], [], []
            for e in eintraege:
                x = (e.get("wetten") or {}).get(w)
                if e.get("status") != "abgerechnet" or g not in (e.get("gruppen") or ()) \
                        or not x or x.get("r") is None:
                    continue
                (mit if over_zufluss(e.get("zufluss")) else ohne).append((x["win"], x["quote"], x["r"]))
                (national if ist_nationalteam(e.get("league")) else verein).append(
                    (x["win"], x["quote"], x["r"]))
            aus["%s/%s" % (g, w)] = {"gruppe": g, "wette": w, "text": WETT_TEXT[w], **k, "urteil": urteil(k),
                                     "mitOverZufluss": kennzahlen(mit), "ohneOverZufluss": kennzahlen(ohne),
                                     "vereine": kennzahlen(verein), "nationalteams": kennzahlen(national)}
    return aus


# ── Push ────────────────────────────────────────────────────────────────────────────────────
def _esc(s):
    return str(s if s is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _serie_txt(s):
    if not s:
        return "noch keine Spiele"
    # 10.10.2026 (Lucas: „sind die 4.1 und 4.6 Tore gesamt, wenn jeweils ein Team spielt?"): ja —
    # und genau das sagte „Ø 4.1 Tore" nicht. Geschossen:kassiert ist eindeutig und zeigt, ob die
    # Tore vorne oder hinten fallen. Alte Serien ohne die Felder behalten die Summe.
    if s.get("toreFuer") is not None and s.get("toreGegen") is not None:
        tore = "Ø %.1f:%.1f Tore" % (s["toreFuer"], s["toreGegen"])
    else:
        tore = "Ø %.1f Tore" % s["toreSchnitt"]
    txt = "%s · %s · O2.5 %d/%d" % (" ".join(s["form"]), tore, s["over25"], s["n"])
    if s.get("nHz"):
        txt += " · Tor in 2. HZ %d/%d" % (s["torIn2hz"], s["nHz"])
    return txt


def _stat_txt(st):
    """„aufs Tor 5–1 · Schuesse 12–4 · Ecken 6–2 · Ballbesitz 64–36 % · xG 1.10–0.20". REIN."""
    h, g = st["heim"], st["gast"]
    teile = []
    for feld, lab in (("aufsTor", "aufs Tor"), ("schuesse", "Schüsse"), ("ecken", "Ecken"),
                      ("gefAngriffe", "gef. Angriffe")):
        if feld in h and feld in g:
            teile.append("%s %d–%d" % (lab, h[feld], g[feld]))
    if "ballbesitz" in h and "ballbesitz" in g:
        teile.append("Ballbesitz %d–%d %%" % (h["ballbesitz"], g["ballbesitz"]))
    if "xg" in h and "xg" in g:
        teile.append("xG %.2f–%.2f" % (h["xg"], g["xg"]))
    return " · ".join(teile) or "–"


def _flagge(cc):
    """'NL' -> 🇳🇱; alles andere (International, leer) -> ⚽. REIN."""
    cc = str(cc or "").strip().upper()
    if len(cc) == 2 and cc.isalpha():
        return "".join(chr(0x1F1E6 + ord(c) - 65) for c in cc)
    return "⚽"


def _q(q):
    """'@1.26 (79 %)' — Quote mit der Wahrscheinlichkeit, die sie sagt. REIN."""
    return "@%.2f <i>(%d %%)</i>" % (q, round(100 / q)) if q else "–"


def staerke(f) -> float:
    """Wie deutlich war die Erwartung vor dem Spiel? Hoehere Zahl zuerst. REIN.
    Nur zum Sortieren der Karte — gefiltert wird danach nicht (das Buch entscheidet)."""
    v = f.get("vor") or {}
    return max(1 / v["over25"] if v.get("over25") else 0, 1 / v["heim"] if v.get("heim") else 0)


def nachricht(funde, bericht_=None) -> str:
    """🎨 04.10.2026 (Lucas: „optisch ein bisschen schoener … schoener lesbar in Telegram").
    Je Spiel ein Block: wer/wo, was vorher erwartet war, was die Pause jetzt bietet (mit der
    Wahrscheinlichkeit, die die Quote sagt), Form beider Teams. Staerkste Erwartung zuerst."""
    funde = sorted(funde, key=staerke, reverse=True)
    n = len(funde)
    t = ["⏸️ <b>HALBZEIT 0:0</b> · %s mehr erwartet war\n━━━━━━━━━━━━━━━━\n"
         % ("1 Spiel, in dem" if n == 1 else "%d Spiele, in denen" % n)]
    for i, f in enumerate(funde[:PUSH_DECKEL]):
        q, v = f.get("quoten") or {}, f.get("vor") or {}
        if i:
            t.append("┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n")
        wann = "zur Pause" if f["phase"] == "HZ" else "%s' · 2. HZ läuft schon" % f.get("minute")
        t.append("%s <b>%s – %s</b>\n<i>%s · %s</i>\n\n"
                 % (_flagge(f.get("country")), _esc(f["home"]), _esc(f["away"]),
                    _esc(str(f.get("league") or "")[:38]), wann))
        erw = []
        if "tore" in f["gruppen"]:
            erw.append("Over 2.5 %s" % _q(v["over25"]))
        if "heim" in f["gruppen"]:
            erw.append("Heimsieg %s" % _q(v["heim"]))
        t.append("🎯 <b>Vorher:</b> %s\n" % " · ".join(erw))
        jetzt = []
        if q.get("over05"):
            jetzt.append("Tor in 2. HZ <b>%s</b>" % _q(q["over05"]))
        if q.get("over15"):
            jetzt.append("2+ Tore %s" % _q(q["over15"]))
        if q.get("heim"):
            jetzt.append(("<b>Heimsieg %s</b>" if "heim" in f["gruppen"] else "Heimsieg %s") % _q(q["heim"]))
        t.append("💰 <b>Jetzt:</b>\n   %s\n" % ("\n   ".join(jetzt) if jetzt else "–"))
        zt = _zufluss_txt(f.get("zufluss"))
        if zt:
            t.append(zt + "\n")
        st = ((f.get("apif") or {}).get("statistik")) or {}
        if st.get("heim") and st.get("gast"):
            t.append("📊 <b>1. HZ:</b> %s\n" % _stat_txt(st))
        s = dict(f.get("serie") or {})
        for seite, ser in (((f.get("apif") or {}).get("serie")) or {}).items():
            s[seite] = ser                    # die laengere, rueckwirkende Serie gewinnt
        if s.get("heim") or s.get("gast"):
            t.append("📈 <b>Form</b> (neueste zuerst)\n   %s: %s\n   %s: %s\n"
                     % (_esc(f["home"]), _serie_txt(s.get("heim")), _esc(f["away"]), _serie_txt(s.get("gast"))))
        t.append("\n")
    if n > PUSH_DECKEL:
        t.append("<i>+%d weitere → Money Map · ⏸️ HZ 0:0</i>\n" % (n - PUSH_DECKEL))
    b = (bericht_ or {}).get("tore/over05") or {}
    if b.get("n"):
        t.append("🔬 <i>Buch „Tor in 2. HZ“: %d abgerechnet · getroffen %s %%, Quote sagte %s %% · ROI %+.1f %%</i>"
                 % (b["n"], b["trefferPct"], b["erwartetPct"], b["roi"]))
    else:
        t.append("🔬 <i>Jeder Fund wird zur Pausenquote gebucht · Urteil ab n=%d · Rückblick: Tor in 2. HZ in 85 %% solcher Spiele</i>"
                 % MINDEST_N)
    return "".join(t)


# ── Lauf ────────────────────────────────────────────────────────────────────────────────────
def main(base_dir=BASE, jetzt=None, senden=None) -> int:
    jetzt = jetzt or datetime.now(timezone.utc)
    pj = lambda n: os.path.join(base_dir, n)
    import team_archiv
    archiv = team_archiv.aktualisieren(base_dir, jetzt)
    prices = _laden(pj("betfair_prices.json"), {}) or {}
    # Vor-Anpfiff-Seite O/U 2.5 aus dem eigenen Liga-Buch (Stand beim letzten Abruf vor Anpfiff)
    pend = {}
    for k_, z_ in (_laden(pj("liga_offen.json"), {}) or {}).items():
        if z_.get("m") == "Over/Under 2.5 Goals":
            pend[k_.split("|")[0]] = {"signals": {"Over/Under 2.5 Goals": {
                "fav": "OVER" if str(z_.get("sel") or "").startswith("Over") else "UNDER", "odd": z_.get("qs") or z_.get("q")}}}
    hist = _laden(pj("betfair_history.json"), {}) or {}
    stand = _laden(pj(AUSGABE_FILE), {}) or {}
    eintraege = list(stand.get("eintraege") or [])
    haben = {e.get("k") for e in eintraege}

    seen = _laden(pj(SEEN_FILE), {}) or {}
    beob_alt = stand.get("beobachtung") or {}
    beob = {}
    neu = []
    for m in prices.get("matches") or []:
        mid = str(m.get("matchId"))
        li = m.get("liveInfo") or {}
        # 04.10.2026: Geldstand O/U 0.5/1.5 fuer jedes laufende 0:0 merken (Zufluss zur Pause).
        if li.get("time") is not None and not li.get("finished") and li.get("goal_v1") == 0 \
                and li.get("goal_v2") == 0:
            gs = geldstand(m)
            if gs:
                beob[mid] = {"ts": jetzt.isoformat(), "stand": gs}
        f = kandidat(m, pend.get(mid), hist.get(mid), archiv)
        if not f or f["k"] in haben or f["k"] in seen:
            continue
        alt = beob_alt.get(mid) or {}
        f["zufluss"] = zufluss(alt.get("stand"), geldstand(m), alt.get("ts"), jetzt)
        f["gebuchtAt"], f["status"] = jetzt.isoformat(), "pending"
        eintraege.append(f)
        haben.add(f["k"])
        neu.append(f)

    # 04.10.2026: Live-Statistik zur Pause + rueckwirkende Serie aus API-Football (apif_live).
    # Reine Anreicherung — ohne Schluessel oder bei Ausfall laeuft alles wie bisher.
    apif_z = None
    if neu and os.environ.get("APISPORTS_KEY"):
        try:
            import apif_live
            apif_z = apif_live.anreichern(neu)
        except Exception as e:  # noqa: BLE001
            print("[hz_finder] API-Football-Anreicherung uebersprungen:", e)
    apif_sum = dict(stand.get("apif") or {})
    for k_, v_ in (apif_z or {}).items():
        apif_sum[k_] = apif_sum.get(k_, 0) + v_

    endst = {}
    for mid_, e_ in (_laden(pj("ergebnisse.json"), {}) or {}).items():
        if isinstance(e_, dict) and e_.get("ft"):
            endst[str(mid_)] = (e_.get("ft"), e_.get("ht"))
    for mid, s in archiv.items():
        endst.setdefault(mid, (s.get("ft"), s.get("ht")))
    eintraege = abrechnen(eintraege, endst, jetzt)[-KEEP:]
    ber = bericht(eintraege)

    grenze = (jetzt - timedelta(hours=36)).isoformat()
    aus = {"updatedAt": jetzt.isoformat(),
           "regel": {"toreMaxQuote": TORE_MAX_QUOTE, "heimMaxQuote": HEIM_MAX_QUOTE,
                     "heimMinQuote": HEIM_MIN_QUOTE, "heimRegel": HEIM_REGEL,
                     "kommission": KOMMISSION, "mindestN": MINDEST_N},
           "bericht": ber,
           "apif": apif_sum,
           "beobachtung": beob,   # Geldstand O/U 0.5/1.5 laufender 0:0-Spiele (fuer den naechsten Lauf)   # Abdeckung API-Football seit Start: Funde / gematcht / mit Statistik / mit Serie
           "zuletzt": [e for e in reversed(eintraege) if str(e.get("gebuchtAt") or "") >= grenze][:60],
           "eintraege": eintraege}
    def write_json_atomic(pfad, d, indent=None):
        with open(str(pfad) + ".tmp", "w", encoding="utf-8") as f_:
            json.dump(d, f_, ensure_ascii=False, indent=indent)
        os.replace(str(pfad) + ".tmp", str(pfad))
    write_json_atomic(pj(AUSGABE_FILE), aus)

    for f in neu:
        seen[f["k"]] = jetzt.isoformat()
    alt = (jetzt - timedelta(days=VERFALL_TAGE)).isoformat()
    seen = {k: v for k, v in seen.items() if str(v) >= alt}
    write_json_atomic(pj(SEEN_FILE), seen)
    print("[hz_finder] %d neu, %d im Buch, Tor 2.HZ: %s" % (len(neu), len(eintraege), ber["tore/over05"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
