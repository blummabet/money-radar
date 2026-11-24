"""Selbstprüfung (24.11., Lucas: „die kleine Fassung, ja"). Prüft nur die Kernrechnungen, an denen Fehler teuer sind —
mit erfundenen Daten, ohne Netz, ohne Dateien aus dem Gedächtnis. Läuft nach jeder Code-Änderung (selbstpruefung.yml).
Regel: nur erweitern, wenn ein Fehler durchgerutscht ist, den ein Test hier gefunden hätte.

Aufruf: python3 tests/selbstpruefung.py   (Ausgang 1 = mindestens eine Prüfung fehlgeschlagen)
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WURZEL))
FEHLER = []


def pruefe(name):
    def deko(f):
        try:
            f()
            print(f"  ✓ {name}")
        except Exception:  # noqa: BLE001
            FEHLER.append(name)
            print(f"  ✗ {name}\n" + "".join("      " + z for z in traceback.format_exc().splitlines(True)[-4:]))
        return f
    return deko


def gleich(a, b, text=""):
    assert a == b, f"{text} erwartet {b!r}, bekommen {a!r}"


def nah(a, b, tol=0.15, text=""):
    assert a is not None and abs(a - b) <= tol, f"{text} erwartet ≈{b}, bekommen {a}"


# ── 1. Abrechnung (bilanz.py) ────────────────────────────────────────────────────────────────────────────────────
@pruefe("Abrechnung: Sieg, Remis, Über/Unter, beide treffen, Halbzeit, Handicap")
def _():
    import bilanz as b
    g = lambda m, a, ft, ht=(0, 0): b.gewonnen(m, a, "Arsenal", "Chelsea", list(ft), list(ht))  # noqa: E731
    gleich(g("Match Odds", "Arsenal", (2, 1)), True)
    gleich(g("Match Odds", "Chelsea", (2, 1)), False)
    gleich(g("Match Odds", "The Draw", (1, 1)), True)
    gleich(g("Over/Under 2.5 Goals", "Over 2.5 Goals", (2, 1)), True)
    gleich(g("Over/Under 2.5 Goals", "Under 2.5 Goals", (2, 1)), False)
    gleich(g("Both teams to Score?", "Yes", (1, 1)), True)
    gleich(g("Both teams to Score?", "Yes", (2, 0)), False)
    gleich(g("Half Time", "Arsenal", (1, 3), (1, 0)), True)
    gleich(g("First Half Goals 0.5", "Over 0.5 Goals", (2, 0), (0, 0)), False)
    gleich(g("Asian Handicap", "Arsenal -1", (2, 1)), "void")


@pruefe("Polymarket-Ausgang: geschlossen, klarer Preis ohne „closed“, 50:50, „gegen X“")
def _():
    import bilanz as b
    ev = lambda closed, preise, titel="", outs=("Yes", "No"): {"markets": [{"outcomes": json.dumps(list(outs)), "outcomePrices": json.dumps([str(p) for p in preise]),  # noqa: E731
                                                                          "groupItemTitle": titel, "closed": closed, "slug": "x"}]}
    gleich(b._poly_ausgang(ev(True, [1, 0], "Arsenal"), "Arsenal"), True)
    gleich(b._poly_ausgang(ev(True, [1, 0], "Arsenal"), "gegen Arsenal"), False)
    gleich(b._poly_ausgang(ev(True, [0.5, 0.5], "Arsenal"), "Arsenal"), "void")
    ko = datetime.now(timezone.utc) - timedelta(hours=5)
    gleich(b._poly_ausgang(ev(False, [0.99, 0.01], "Arsenal"), "Arsenal", ko=ko), True)                     # ≥ 0,985 nach 4 h
    gleich(b._poly_ausgang(ev(False, [0.95, 0.05], "Arsenal"), "Arsenal", ko=ko), None)                     # noch offen
    gleich(b._poly_ausgang(ev(True, [0, 1], outs=("FaZe", "GOSH")), "GOSH", key="x"), True)                  # E-Sport, Hauptmarkt


# ── 2. Sport-Profis (poly_profis.py) ────────────────────────────────────────────────────────────────────────────
@pruefe("Profis: Einsatz = Stück × Ø Preis + Gebühr, Teilverkauf, Market-Maker, Sportarten")
def _():
    import poly_profis as P
    fuss = {p: "Fußball" for p in P.FUSS_FEST}
    z = lambda cid, slug, size, ap, pnl, fee=0, cost=None: {"condition_id": cid, "outcome_index": 0, "event_slug": slug, "total_size": size,  # noqa: E731
                                                             "avg_price": ap, "entry_fees_usdc": fee, "total_cost_usdc": cost, "total_pnl": pnl, "last_event_at": 2e9}
    # Verlust inkl. Gebühr = voller Einsatz (Log 24.11.: 641 × 0,56 + $7,90)
    w = P.wertung([z("a", "val-ur1-kbg", 641.22, 0.5599, -366.98, fee=7.8998)], fuss, 0)
    gleich(w["unplausibel"], 0, "Gebühr")
    nah(w["einsatz"], 366.98, 0.05, "Einsatz")
    w = P.wertung([z("a2", "val-ur1-kbg", 641.22, 0.5599, 274.1, fee=7.8998)], fuss, 0)       # gewonnen: Gebühr im Einsatz
    nah(w["einsatz"], 366.92, 0.02, "Einsatz mit Gebühr (Gewinn)")
    # Teilverkauf: total_cost_usdc ist nur noch die Rest-Basis — Einsatz muss aus Stück × Preis kommen
    w = P.wertung([z("b", "epl-ars-che", 1000, 0.5, 200, cost=10)], fuss, 0)
    nah(w["einsatz"], 500, 0.01, "Teilverkauf")
    # Market-Maker: Verlust größer als Kauf → Einsatz = Verlust, als unplausibel gezählt
    w = P.wertung([z("c", "lol-hle-t1", 13000, 0.03, -555.36)], fuss, 0)
    gleich(w["unplausibel"], 1)
    nah(w["einsatz"], 555.36, 0.01)
    # Sportarten: große Ligen ohne heutigen Markt, Krypto und ATP raus
    gleich(P.sportart("epl-ars-che-2026-11-01", fuss), "Fußball")
    gleich(P.sportart("conl-x-y", fuss), "Fußball")
    gleich(P.sportart("cs2-faze-gosh", fuss), "E-Sport")
    gleich(P.sportart("itf-a-b", fuss), "Tennis")
    for raus in ("btc-up-down", "atp-x-y", "nba-a-b", "esoccer-x"):
        gleich(P.sportart(raus, fuss), None, raus)


@pruefe("Profis: Urteil (gewichtete Untergrenze, ≥ 50 Wetten, ≥ 2 % Rendite)")
def _():
    import poly_profis as P
    fuss = {"epl": "Fußball"}
    def reihe(n, gewinn_je, verlust_je, quote_gewinn):
        return [{"condition_id": f"k{i}", "outcome_index": 0, "event_slug": "epl-a-b", "total_size": 100, "avg_price": 0.5,
                 "total_pnl": gewinn_je if i % 10 < quote_gewinn else verlust_je, "last_event_at": 2e9} for i in range(n)]
    gut = P.urteil(P.wertung(reihe(200, 50, -50, 7), fuss, 0))      # 70 % Treffer bei Quote 2 → +40 %
    gleich(gut[0], True, "klar gut")
    wenig = P.urteil(P.wertung(reihe(40, 50, -50, 7), fuss, 0))
    gleich(wenig[0], False, "unter 50 Wetten")
    knapp = P.urteil(P.wertung(reihe(1000, 50.5, -50, 5), fuss, 0))  # +0,5 % — unter W6
    gleich(knapp[0], False, "unter 2 % Rendite")


# ── 3. Push-Schwellen (push.py: W7, W8, W9) ──────────────────────────────────────────────────────────────────────
def _wal_daten(setzen, wale_im_topf=()):
    return {"poly": {"wallets": [], "setzen": setzen}, "pinnAlle": [],
            "toepfe": [{"id": "t1", "heim": "FaZe", "gast": "GOSH", "liga": "CS2", "q": {"poly": {"key": "cs2-faze-gosh-2026-11-24", "preise": {"heim": 0.58, "gast": 0.42}, "betrag": 60000}},
                        "wale": list(wale_im_topf)}]}


def _setzen(usd, seite="FaZe", wallet="0xaaaa", schnitt=2000, **extra):
    return {"wallet": wallet, "markt": "cs2-faze-gosh-2026-11-24", "seite": seite, "usd": usd, "preis": 0.58, "jetzt": 0.58, "einstieg": 0.58,
            "spiel": "FaZe vs GOSH", "sport": "E-Sport", "anpfiff": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
            "schnittUsd": schnitt, "n": 80, "clv": 1.2, "clvUg": 0.4, "rang": 1, "rangVon": 5, **extra}


def _arten(d):
    import push
    return [((x[3] or {}).get("art"), x[1] is not None) for x in push.wale(d)]


@pruefe("Wal-Push W7: unter $5k still, ab $5k Push, halbe Schwelle bei ≥ 3× Schnitt")
def _():
    gleich(_arten(_wal_daten([_setzen(1500)])), [("walUnter", False)], "$1,5k")
    gleich(_arten(_wal_daten([_setzen(6000)])), [("wal", True)], "$6k")
    gleich(_arten(_wal_daten([_setzen(3000, schnitt=900)])), [("wal", True)], "$3k bei 3,3× Schnitt")


@pruefe("Wal-Push W8: belegtes Gegen-Geld ≥ halber Einsatz → Streit")
def _():
    gegen = [{"wallet": "0xbbbb", "seite": "GOSH", "usd": 21000, "k": "gast"}]
    import push
    out = list(push.wale(_wal_daten([_setzen(26000)], gegen)))
    w8 = (out[0][3] or {}).get("w8") or {}
    gleich(w8.get("streit"), True, "Streit")
    gleich(out[0][4], None, "kein Public bei Streit")
    klein = list(push.wale(_wal_daten([_setzen(26000)], [{**gegen[0], "usd": 5000}])))
    gleich(((klein[0][3] or {}).get("w8") or {}).get("streit"), False, "kleines Gegen-Geld")


@pruefe("Wal-Push W9: Sport-Profi nur ab Untergrenze ≥ 3 % und ≥ 100 Wetten")
def _():
    schwach = _setzen(9000, profi=True, renditeUg=1.0, n=773, rendite=8.5, gewinn=116000, name="JabJabich")
    stark = _setzen(9000, profi=True, renditeUg=13.4, n=3964, rendite=20.9, gewinn=142000, name="testyeagles", flach=9.0, flachUg=4.1)
    gleich(_arten(_wal_daten([schwach])), [("walProfiB", False)], "schwacher Profi")
    gleich(_arten(_wal_daten([stark])), [("wal", True)], "Top-Profi")
    gleich(_arten(_wal_daten([{**stark, "flachUg": 1.0}])), [("walProfiB", False)], "W10: je Wette unter 3 %")
    w11 = _setzen(2000, schnitt=500, profi=True, renditeUg=13.4, n=3964, rendite=20.9, gewinn=142000, flach=9.0, flachUg=4.1)
    gleich(_arten(_wal_daten([w11])), [("wal", True)], "W11: Top-Profi 4× Schnitt, $2k")
    gleich(_arten(_wal_daten([{**w11, "usd": 900, "schnittUsd": 200}])), [("walUnter", False)], "W11: unter $1.000")
    gleich(_arten(_wal_daten([{**w11, "usd": 2000, "schnittUsd": 1000}])), [("walUnter", False)], "W11: nur 2× Schnitt")
    import push
    def typ(o):
        x = list(push.wale(_wal_daten([o])))[0]
        return (x[3] or {}).get("typ"), x[1].split("·")[0].strip(), x[4] is not None
    gleich(typ(w11), ("profiEin", "🎯 <b>Profi-Einstieg", False), "W12: Profi-Einstieg, kein Public")
    gleich(typ(stark), ("profiWal", "🐋🎯 <b>Profi-Wal", True), "W12: Profi-Wal")
    gleich(typ(_setzen(9000))[:2], (None, "🐋 <b>Wal-Einstieg"), "W12: normaler Wal")


@pruefe("W10: Rendite je Wette — Riesen-Glückstreffer (Artemiion) fällt durch")
def _():
    import build
    # 50 Kleinwetten à $50 mit −20 %, 2 Riesenwetten à $10k mit +150 % → aufs Geld stark, je Wette negativ
    u = [-0.2] * 50 + [1.5] * 2
    f = build._flach({"n": len(u), "uSum": sum(u), "uSq": sum(x * x for x in u)})
    assert f["flach"] < 2 and f["flachUg"] < 0, f
    u = [0.9, -1.0] * 200   # 400 Wetten, im Schnitt −5 % je Wette
    f = build._flach({"n": len(u), "uSum": sum(u), "uSq": sum(x * x for x in u)})
    nah(f["flach"], -5.0, 0.01)


@pruefe("Public-Ergebnis: Nachwuchsspiel zählt nie fürs Profispiel (Feyenoord – Porto 24.11.)")
def _():
    import build, bilanz
    t0 = datetime(2026, 11, 24, 18, 58, tzinfo=timezone.utc)
    jung = (bilanz._team("Feyenoord Youth"), bilanz._team("Porto U19"), datetime(2026, 11, 24, 15, tzinfo=timezone.utc), [0, 1], "Feyenoord Youth", "Porto U19")
    gleich(build._public_gewonnen({"spiel": "Feyenoord – Porto", "auswahl": "Porto"}, t0, [jung]), None, "U19-Ergebnis fürs Profispiel")
    profi = (bilanz._team("Feyenoord"), bilanz._team("Porto"), datetime(2026, 11, 24, 20, tzinfo=timezone.utc), [1, 2], "Feyenoord", "Porto")
    gleich(build._public_gewonnen({"spiel": "Feyenoord – Porto", "auswahl": "Porto"}, t0, [jung, profi]), True)


@pruefe("Top 3 T10: Bestätigung nötig, nicht kurz vor Anpfiff, live kein Sieg bei Gleichstand")
def _():
    import top3
    j = datetime(2026, 11, 24, 12, tzinfo=timezone.utc)
    gleich(top3.t10_gruende({"neu": 3, "quote": 1}, False, j + timedelta(hours=5), j), ["nur Geld, keine Bestätigung"])
    gleich(top3.t10_gruende({"neu": 3, "serie": 3}, False, j + timedelta(minutes=30), j), ["letzte Stunde vor Anpfiff"])
    gleich(top3.t10_gruende({"neu": 3, "druck": 2}, True, None, j, "Match Odds", "gleich"), ["live Sieg bei Gleichstand"])
    gleich(top3.t10_gruende({"neu": 3, "druck": 2}, True, None, j, "Over/Under 2.5 Goals", "gleich"), [])
    gleich(top3.t10_gruende({"neu": 3, "stake": 3}, False, j + timedelta(hours=3), j), [])


@pruefe("Prüfungs-Register: zählt abgerechnete Fälle ab Start, Datum macht reif")
def _():
    import pruefungen
    j = datetime(2026, 12, 1, tzinfo=timezone.utc)
    z = [["2026-11-25T10:00", "top3", 0.5, 1, 1.8]] * 100 + [["2026-11-20T10:00", "top3", 0.5, 1, 1.8], ["2026-11-25T10:00", "top3", None, None, 1.8]]
    s = {x["id"]: x for x in pruefungen.stand(z, j)}
    gleich(s["T10"]["n"], 100)
    gleich(s["T10"]["reif"], True)
    gleich(s["W7"]["reif"], False)
    gleich(s["S7"]["reif"], True, "Datum 29.11. vorbei")
    gleich(len({x["id"] for x in pruefungen.REGISTER}), len(pruefungen.REGISTER), "doppelte Prüfungs-Ids")


@pruefe("Uhr-Modell: Führender fällt mit der Zeit, unplausibles Buch → keine Erwartung")
def _():
    import markout as m
    q = {"A": 1.47, "B": 12.0, "The Draw": 4.2}
    e = m.uhr_erwartung("Match Odds", "A", 1.47, 35, 45, [1, 0], q, "A", "B")
    assert e is not None and e < 1.47, f"Führender 1:0 sollte kürzer werden, bekommen {e}"
    gleich(m.uhr_erwartung("Match Odds", "A", 1.47, 35, 45, [1, 0], {"A": 1.47, "B": 6.0, "The Draw": 2.0}, "A", "B"), None, "Buch mit 38 % Marge")
    e2 = m.uhr_erwartung("Over/Under 2.5 Goals", "Over 2.5 Goals", 1.8, 50, 60, [1, 0])
    assert e2 is not None and e2 > 1.8, "Über steigt ohne Tor mit der Zeit"


@pruefe("LD7: nachhaltiger Drop erkannt, ein Ausreißer nach oben bricht die Reihe")
def _():
    import build
    J = build.JETZT
    ko = J - timedelta(minutes=40)
    def pt(vor, qa, qh, qd, mi):
        return {"ts": (J - timedelta(minutes=vor)).isoformat(), "sc": [0, 0], "min": mi,
                "sel": {"Match Odds": {"H": [30000, qh], "A": [15000, qa], "The Draw": [8000, qd]}}}
    pr = {"matches": [{"matchId": "1", "kickoff": ko.isoformat(), "home": "H", "away": "A", "league": "X", "liveInfo": {"time": 38}}]}
    gut = [pt(30, 5.4, 1.6, 3.9, 10), pt(22, 5.2, 1.6, 3.9, 18), pt(17, 4.6, 1.7, 3.8, 23), pt(12, 4.2, 1.8, 3.7, 28), pt(7, 3.8, 1.9, 3.6, 33), pt(2, 3.4, 2.05, 3.5, 38)]
    gleich(len(build.bf_nachhaltig(pr, {"1": gut})), 1, "Drop 4,6 → 3,4")
    zack = list(gut)
    zack[3] = pt(12, 5.0, 1.8, 3.7, 28)
    gleich(len(build.bf_nachhaltig(pr, {"1": zack})), 0, "Ausreißer nach oben")


# ── 4. Liga-Bilanz (build.py, Regel LW3) ─────────────────────────────────────────────────────────────────────────
@pruefe("Liga-Bilanz LW3: Quote vor Anpfiff (qs), Märkte je Spiel gebündelt")
def _():
    import build
    rr = [{"q": 3.0, "qs": 2.0, "w": True, "_mid": "1"}, {"q": 3.0, "qs": 2.0, "w": False, "_mid": "2"}]
    st = build._liga_stat(rr)
    nah(st["roi"], -1.0, 0.01, "ROI mit qs (nicht q)")
    # dieselbe Wette dreifach in einem Spiel darf die Unsicherheit nicht verkleinern
    einzeln = build._liga_stat([{"qs": 2.0, "w": i % 2 == 0, "_mid": str(i)} for i in range(40)])
    dreifach = build._liga_stat([{"qs": 2.0, "w": i % 2 == 0, "_mid": str(i)} for i in range(40) for _ in range(3)])
    assert (dreifach["og"] - dreifach["ug"]) >= 0.95 * (einzeln["og"] - einzeln["ug"]), "Cluster: Band schrumpft durch Dopplung"


# ── 5. Seite (site/index.html) ───────────────────────────────────────────────────────────────────────────────────
@pruefe("Seite: Skript fehlerfrei, Regel-Nummern eindeutig")
def _():
    s = (WURZEL / "site" / "index.html").read_text(encoding="utf-8")
    js = "\n".join(re.findall(r"<script>(.*?)</script>", s, re.S))
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(js)
    r = subprocess.run(["node", "--check", f.name], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-500:]
    ids = re.findall(r'\{id:"([^"]+)", t:"', s)
    doppelt = sorted({i for i in ids if ids.count(i) > 1})
    assert not doppelt, f"doppelte Regel-Nummern: {doppelt}"


@pruefe("Python: alle Dateien übersetzbar")
def _():
    import py_compile
    for p in list(WURZEL.glob("*.py")) + list((WURZEL / "stake").glob("*.py")) + list((WURZEL / "social").glob("*.py")):
        py_compile.compile(str(p), doraise=True)


if __name__ == "__main__":
    print(f"Selbstprüfung: {len(FEHLER)} Fehler" if FEHLER else "Selbstprüfung: alles in Ordnung")
    sys.exit(1 if FEHLER else 0)
