"""Top 3 — dynamische Gewichte (Top-3-Regel 7, Lucas 17.11.2026: „das Top 3 soll dynamisch aus den besten Signalen
sein … wenn ein Signal über einen gewissen Zeitraum schlechter ist, fällt es ab"; „mach alles, damit das besser wird").

Vorab festgelegt (17.11.2026, 13:45 Wien) — so und nicht anders:

1. **Kandidaten-Buch.** Bei jedem Lauf landen die besten 15 Betfair-Kandidaten der Top-3-Rangliste still in
   .push_state/t3_kandidaten.json — je Spiel nur der erste Auftritt, zur Quote in dem Moment, mit den Punkten JE SIGNAL.
   Abgerechnet wie die Betfair-Pushes (2 % Kommission). Nur so lässt sich prüfen, ob eine andere Gewichtung besser
   sortiert hätte — das Top-3-Buch selbst kennt nur, was schon oben stand.
2. **Gewichte zweimal pro Woche (Montag + Donnerstag)**, aus den letzten 28 Tagen des Kandidaten-Buchs:
   Ridge-Regression Rendite ~ Signale (+ live als Kontrolle), Vorab-Streuung je Signal 5 %-Punkte → ein Signal bekommt
   nur so viel Effekt, wie die Daten sicher hergeben. Faktor = 1 + Effekt / 8 pp, zwischen 0 und 2, je Schritt höchstens
   ±0,25 Änderung. Neue Signale starten bei Faktor 1 (= ihre festen Punkte) und bewegen sich nur über Wochen.
3. **Erst prüfen, dann schalten.** Walk-Forward: für jeden Stichtag ab 7 Tagen Daten die Gewichte NUR aus den Daten
   davor, dann die Tage bis zum nächsten Stichtag — trägt das obere Drittel nach dynamischen Punkten mehr als das obere
   Drittel nach festen? Aktiv wird es erst, wenn das an mindestens 4 Startpunkten geprüft ist, an der Mehrheit trägt UND
   zusammen trägt.
   Fällt die Prüfung später durch, gehen die Faktoren automatisch zurück auf 1 (feste Punkte).
4. Bis dahin rechnet alles mit und zeigt auf der Seite, was es ändern würde — die Wertung bleibt die feste (Regel 6).

Warum so vorsichtig: im alten Projekt hat genau diese Idee (Kalibrierer, Sept. 2026) die Plays out of sample in 6 von
6 Startpunkten SCHLECHTER sortiert — ein Signal mit +52 % auf kleiner Strecke bekam zwei Stufen, vorwärts brachte es +9 %.
Merksatz von damals: nach Verlässlichkeit schrumpfen, nicht nach Stichprobengröße; ein Split ist kein Beleg.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASIS = Path(__file__).resolve().parent
KAND = BASIS / ".push_state" / "t3_kandidaten.json"
GEW = BASIS / ".push_state" / "t3_gewichte.json"
TOP_N = 15
FENSTER_TAGE = 28
TAU = 0.05            # Vorab-Streuung eines Signal-Effekts (Rendite je Einheit)
PP_JE_FAKTOR = 0.08   # 8 pp Effekt = Faktor +1
SCHRITT = 0.25        # höchstens ±0,25 je Woche
MIN_STARTS = 4
SIGNALE = ("serie", "push", "neu", "quote", "poly", "stake", "maerkte", "druck", "liga", "norm", "jung", "torlinie")
# gemessen, aber ohne Punkte — ein Faktor ändert hier nichts. norm: Regel 6. jung/torlinie: Regel T8 (18.11., Lucas „ja"):
# im Betwatch-Oktober-Backtest waren Nachwuchs/Reserve/Frauen und Tor-Linien in allen vier Betfair-Signalen schwächer —
# keine feste Regel daraus, sondern still mitmessen; die Wochenrechnung zeigt vorwärts, ob es hier auch so ist.
STILL = ("norm", "jung", "torlinie")


def merkmale(z):
    """Stille Merkmale aus Liga/Teams/Markt — gilt auch rückwirkend für ältere Kandidaten. REIN."""
    import public
    return {"jung": bool(public._JUNG.search(f"{z.get('liga') or ''} {z.get('heim') or ''} {z.get('gast') or ''}")),
            "torlinie": public.ist_torlinie(z.get("markt"), z.get("auswahl"))}


def _hat(z, s):
    if s in ("jung", "torlinie") and s not in (z.get("still") or {}):
        return merkmale(z)[s]
    return bool((z.get("p") or {}).get(s) or (z.get("still") or {}).get(s))


def _ts(s):
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def _lesen(p, leer):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return leer


def _schreiben(p, x):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(x, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def aktive_faktoren():
    g = _lesen(GEW, {})
    return dict(g.get("faktoren") or {}) if g.get("aktiv") else {}


# ── 1. Kandidaten-Buch ───────────────────────────────────────────────────────────────────────────────────────────
def kandidaten_eintragen(d, jetzt, rangliste):
    """rangliste = top3.rangliste(d, jetzt) (bereits sortiert). Gibt die Zahl neuer Einträge zurück."""
    kand = _lesen(KAND, [])
    da = {str(k.get("id")) for k in kand}
    neu = 0
    for rang, x in enumerate([x for x in rangliste if x.get("art") == "top3"][:TOP_N], 1):
        if str(x.get("id")) in da or not isinstance(x.get("quote"), (int, float)):
            continue
        kand.append({k: x.get(k) for k in ("id", "heim", "gast", "liga", "anpfiff", "live", "top5", "markt", "auswahl",
                                             "quote", "punkte", "p", "stand", "lage")}
                    | {"still": {"norm": bool(x.get("normStill")), **merkmale(x)}, "art": "top3k", "rang": rang, "ts": jetzt.isoformat(), "status": "offen"})
        da.add(str(x.get("id")))
        neu += 1
    grenze = (jetzt - timedelta(days=60)).isoformat()
    _schreiben(KAND, [k for k in kand if str(k.get("ts")) >= grenze])
    return neu


def kandidaten_abrechnen(jetzt):
    import bilanz
    kand = _lesen(KAND, [])
    if kand:
        _schreiben(KAND, bilanz.aktualisieren(kand, jetzt))


# ── 2. Gewichte ──────────────────────────────────────────────────────────────────────────────────────────────────
def _loesen(A, b):
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(M[r][c]))
        if abs(M[piv][c]) < 1e-12:
            return None
        M[c], M[piv] = M[piv], M[c]
        for r in range(n):
            if r != c:
                f = M[r][c] / M[c][c]
                for k in range(c, n + 1):
                    M[r][k] -= f * M[c][k]
    return [M[i][n] / M[i][i] for i in range(n)]


def effekte(zeilen, signale=None):
    """Ridge: r ~ a + live + Σ Signal. Signal-Koeffizienten mit Vorab 0 ± TAU, Achsenabschnitt/live praktisch frei.
    → {signal: {"effekt": pp, "n": mit Signal, "roiMit", "roiOhne"}}. REIN."""
    Z = [z for z in zeilen if isinstance(z.get("r"), (int, float))]
    if len(Z) < 40:
        return {}
    sig = [s for s in (signale or SIGNALE) if sum(1 for z in Z if _hat(z, s)) >= 10]
    sd = math.sqrt(sum((z["r"] - sum(y["r"] for y in Z) / len(Z)) ** 2 for z in Z) / (len(Z) - 1)) or 1.0
    lam = (sd / TAU) ** 2
    X = [[1.0, 1.0 if z.get("live") else 0.0] + [1.0 if _hat(z, s) else 0.0 for s in sig] for z in Z]
    y = [z["r"] for z in Z]
    k = len(X[0])
    A = [[sum(X[i][a] * X[i][b] for i in range(len(X))) + (lam if a == b and a >= 2 else (1e-6 if a == b else 0.0))
          for b in range(k)] for a in range(k)]
    bb = [sum(X[i][a] * y[i] for i in range(len(X))) for a in range(k)]
    beta = _loesen(A, bb)
    if not beta:
        return {}
    aus = {}
    for j, s in enumerate(sig):
        mit = [z["r"] for z in Z if _hat(z, s)]
        ohne = [z["r"] for z in Z if not _hat(z, s)]
        aus[s] = {"effekt": round(100 * beta[j + 2], 1), "n": len(mit),
                  "roiMit": round(100 * sum(mit) / len(mit), 1) if mit else None,
                  "roiOhne": round(100 * sum(ohne) / len(ohne), 1) if ohne else None}
    return aus


def faktoren_neu(alt, eff, signale=None, still=None):
    """Ein Wochenschritt: Ziel 1 + Effekt/8 pp in [0, 2], höchstens ±0,25 vom alten Faktor. REIN."""
    neu = {}
    for s in (signale or SIGNALE):
        if s in (STILL if still is None else still):
            continue
        a = float((alt or {}).get(s, 1.0))
        e = (eff.get(s) or {}).get("effekt")
        if e is None:
            neu[s] = round(a, 2)
            continue
        ziel = max(0.0, min(2.0, 1 + (e / 100) / PP_JE_FAKTOR))
        neu[s] = round(max(a - SCHRITT, min(a + SCHRITT, ziel)), 2)
    return neu


def _punkte(z, f):
    return sum(v * float((f or {}).get(k, 1.0)) for k, v in (z.get("p") or {}).items())


# ── 3. Walk-Forward ──────────────────────────────────────────────────────────────────────────────────────────────
def _stichtage(von, bis):
    """Montag und Donnerstag, je 06:00 UTC, zwischen von und bis. REIN."""
    t = von.replace(hour=6, minute=0, second=0, microsecond=0)
    aus = []
    while t <= bis:
        if t.weekday() in (0, 3) and t > von:
            aus.append(t)
        t += timedelta(days=1)
    return aus


def walk_forward(kand, jetzt, signale=None, still=None):
    """Je Stichtag (Mo/Do, ab 7 Tagen Daten): Faktoren Schritt für Schritt NUR aus den Daten davor fortgeschrieben, dann
    die Tage bis zum nächsten Stichtag: oberes Drittel nach dynamischen gegen oberes Drittel nach festen Punkten."""
    Z = sorted([z for z in kand if isinstance(z.get("r"), (int, float)) and _ts(z.get("ts"))], key=lambda z: z["ts"])
    if not Z:
        return {"starts": [], "urteil": None}
    t0 = _ts(Z[0]["ts"])
    tage = _stichtage(t0, jetzt)
    sg, stl = signale or SIGNALE, STILL if still is None else still
    f = {s: 1.0 for s in sg if s not in stl}
    starts = []
    for i, st in enumerate(tage[:-1]):
        nxt = tage[i + 1]
        vor = [z for z in Z if st - timedelta(days=FENSTER_TAGE) <= _ts(z["ts"]) < st]
        f = faktoren_neu(f, effekte(vor, sg), sg, stl)
        if (st - t0).days < 7:
            continue
        test = [z for z in Z if st <= _ts(z["ts"]) < nxt]
        if len(test) < 30:
            continue
        drittel = max(10, len(test) // 3)
        dyn = sorted(test, key=lambda z: -_punkte(z, f))[:drittel]
        fest = sorted(test, key=lambda z: -_punkte(z, {}))[:drittel]
        r_d, r_f = sum(z["r"] for z in dyn) / len(dyn), sum(z["r"] for z in fest) / len(fest)
        starts.append({"ab": st.strftime("%Y-%m-%d"), "n": len(test), "dyn": round(100 * r_d, 1), "fest": round(100 * r_f, 1),
                       "traegt": r_d > r_f, "_d": r_d * len(dyn), "_f": r_f * len(fest)})
    urteil = None
    if len(starts) >= MIN_STARTS:
        mehrheit = sum(1 for x in starts if x["traegt"]) > len(starts) / 2
        zusammen = sum(x["_d"] for x in starts) > sum(x["_f"] for x in starts)
        urteil = "trägt" if mehrheit and zusammen else "trägt nicht"
    return {"starts": [{k: v for k, v in x.items() if not k.startswith("_")} for x in starts], "urteil": urteil}


def woche(jetzt, buch=None):
    """Zweimal je Woche (Mo + Do ab 06 Uhr UTC) — oder wenn noch nie gerechnet: Effekte, Faktoren, Prüfung, Schalter."""
    g = _lesen(GEW, {})
    letzte = _ts(g.get("stand"))
    stich = [t for t in _stichtage(jetzt - timedelta(days=8), jetzt) if t <= jetzt]
    kand = _lesen(KAND, [])
    if letzte and stich and letzte >= stich[-1]:
        # zwischen den Stichtagen nur die Zähler nachziehen (die Seite zeigt, wie sich das Buch füllt)
        g.update(kandidaten=len(kand), abgerechnet=sum(1 for z in kand if isinstance(z.get("r"), (int, float))),
                 seit=min((z.get("ts") for z in kand), default=None))
        _schreiben(GEW, g)
        return False
    fenster = [z for z in kand if _ts(z.get("ts")) and _ts(z["ts"]) >= jetzt - timedelta(days=FENSTER_TAGE)]
    eff = effekte(fenster)
    quelle = "kandidaten"
    if not eff and buch:
        # Anlauf: solange das Kandidaten-Buch dünn ist, die Effekte aus den Top-3-Einzügen (nur zur Anzeige)
        erst, z = set(), []
        for b in sorted((b for b in buch if b.get("art") == "top3"), key=lambda b: b.get("ts") or ""):
            if str(b.get("id")) in erst:
                continue
            erst.add(str(b.get("id")))
            if _ts(b.get("ts")) and _ts(b["ts"]) >= jetzt - timedelta(days=FENSTER_TAGE) and b.get("status") == "abgerechnet":
                z.append({"r": b.get("r"), "live": b.get("live"), "p": {s: 1 for s in b.get("signale") or []},
                          "still": {"norm": bool(b.get("normStill")), **merkmale(b)}})
        eff, quelle = effekte(z), "top3-einzuege"
    vorschlag = faktoren_neu(g.get("vorschlag") or g.get("faktoren") or {}, eff) if quelle == "kandidaten" else \
        dict(g.get("vorschlag") or {s: 1.0 for s in SIGNALE if s not in STILL})
    wf = walk_forward(kand, jetzt)
    aktiv = wf.get("urteil") == "trägt"
    g = {"regel": 7, "stand": jetzt.isoformat(), "aktiv": aktiv, "faktoren": vorschlag if aktiv else {s: 1.0 for s in SIGNALE if s not in STILL},
         "vorschlag": vorschlag, "effekte": eff, "quelle": quelle, "fensterTage": FENSTER_TAGE,
         "kandidaten": len(kand), "abgerechnet": sum(1 for z in kand if isinstance(z.get("r"), (int, float))),
         "seit": min((z.get("ts") for z in kand), default=None), "walkForward": wf}
    _schreiben(GEW, g)
    return True


def lauf(d, jetzt, buch, rangliste):
    """Aus push.py je Lauf: eintragen, abrechnen, ggf. Wochenschritt. Gibt eine Logzeile zurück."""
    n = kandidaten_eintragen(d, jetzt, rangliste)
    kandidaten_abrechnen(jetzt)
    w = woche(jetzt, buch)
    return f"t3-dynamik: {n} neue Kandidaten" + (" · Wochenschritt gerechnet" if w else "")
