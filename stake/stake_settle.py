#!/usr/bin/env python3
"""
stake_settle.py — Stake-Wetten abrechnen
================================================================================
03.09.2026 (Lucas: 'bitte alles umsetzen'). Der Schritt, nach dem wir zum ersten Mal eine
gemessene Zahl haben statt einer Vermutung.

## Warum das der wichtigste Baustein ist
Stakes Schnittstelle lässt jede Wette nachschlagen:

    bet(iid: "sport:648199979") { ... on SportBet {
        status payout payoutMultiplier outcomes { status odds outcome { name } } } }

und antwortet mit `status: "settled"`, `payout: 0` und je Bein `"won"` / `"lost"`. Damit
brauchen wir für den Stake-Fluss **keine Ergebnis-Pipeline und kein Namens-Matching** — die
Wahrheit kommt aus derselben Quelle wie die Wette. Bei Betfair und Polymarket müssen wir
Ergebnisse selbst beschaffen und Namen brücken; hier fragen wir einfach nach.

## Was die Messeinheit ist — und was nicht
Gezählt wird das **BEIN** (ein Bein = eine Meinung zu einem Spiel), nicht die Wette. Bei
einer Einzelwette ist beides dasselbe. Bei einer Kombi ist jedes Bein eine Meinung, aber der
Einsatz hängt an allen zugleich — deshalb:

  · Trefferquote  → über alle Beine (Kombi-Beine zählen mit)
  · Geld / Rendite → NUR über Einzelwetten (bei einer Kombi ist kein Betrag zurechenbar)

Das ist dieselbe Trennung wie im Dashboard, wo Kombis sichtbar bleiben, aber nicht ins
Spielgeld zählen.

## Abrechnung heisst nicht „gewonnen"
`status` kennt neben won/lost auch void/cancelled/cashout. Ein annullierte Wette ist KEIN
Verlust und kein Treffer — sie fällt aus der Quote heraus und wird getrennt gezählt. Eine
Wette, die nach der Frist immer noch offen ist, bleibt als `unaufloesbar` sichtbar, statt
stillschweigend zu verschwinden (dieselbe Regel wie in poly_public_eval.py).

## Env
  STAKE_SETTLE_MAX        Wieviele Wetten je Lauf nachfragen (Default 300)
  STAKE_SETTLE_BATCH      Wieviele je GraphQL-Anfrage (Default 25, per Alias gebündelt)
  STAKE_SETTLE_REIF_H     Ab wieviel Stunden nach Anpfiff nachgefragt wird (Default 3)
  STAKE_SETTLE_AUFGEBEN_D Ab wann eine offene Wette als unaufloesbar gilt (Default 5)
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE = Path(os.environ.get("STAKE_DATEN") or Path(__file__).resolve().parent)  # money-radar: Daten in .st/
sys.path.insert(0, str(BASE))

import stake_highroller_fetch as SH   # Endpunkt, _post, Kurse, Ledger-IO — eine Quelle

LEDGER_FILE = BASE / "stake_bet_ledger.json"
KURS_FILE = BASE / "stake_kurse.json"
QUERY_FILE = BASE / "stake_query.json"

# 12.10.2026: 300 -> 1000. Seit dem 08.10. kommen ~4.500 Wetten am Tag statt ~1.200, und nach
# dem Stillstand (s. reihenfolge) stehen 17.000 offen. Mit 300 je Lauf haette der Rueckstand
# laenger gebraucht, als das Ledger (20.000 Zeilen, jetzt ~4,5 Tage) die Wetten haelt.
MAX_JE_LAUF = int(os.environ.get("STAKE_SETTLE_MAX") or 1000)
BATCH = int(os.environ.get("STAKE_SETTLE_BATCH") or 25)
REIF_H = float(os.environ.get("STAKE_SETTLE_REIF_H") or 3)
AUFGEBEN_D = float(os.environ.get("STAKE_SETTLE_AUFGEBEN_D") or 5)
# 13.10.2026: Tempo und Drosselung, s. GEDROSSELT unten.
PAUSE_S = float(os.environ.get("STAKE_SETTLE_PAUSE_S") or 2)        # zwischen zwei Stapeln
RUHE_S = float(os.environ.get("STAKE_SETTLE_RUHE_S") or 20)         # nach einem leeren Stapel
BUDGET_S = float(os.environ.get("STAKE_SETTLE_BUDGET_S") or 180)    # je Lauf hoechstens
BURST_FILE = BASE / "stake_burst_ledger.json"

# Was NICHT abgerechnet ist. Am echten Endpunkt gepruefte Zustaende (03.09.2026):
#   "settled"   → fertig, payout steht, Beine tragen won/lost
#   "confirmed" → angenommen, laeuft noch (Beine "pending") ← waere fast durchgerutscht
# Die Liste ist bewusst eine Liste des OFFENEN, nicht des Fertigen: taucht morgen ein neuer
# Zwischenzustand auf, gilt er als offen und die Wette wird weiter nachgefragt. Andersherum
# waere ein unbekannter Zustand stillschweigend „abgerechnet" — und die Quote falsch.
OFFEN = {"active", "pending", "open", "confirmed", "placed", "accepted", "cashout_pending",
         None, ""}
NEUTRAL = {"void", "cancelled", "canceled", "refunded", "push", "tie"}

BEIN_FELDER = "status odds outcome { name } fixtureName"
WETT_FELDER = ("__typename ... on SportBet { amount currency status active payout "
               "payoutMultiplier potentialMultiplier createdAt updatedAt outcomes { %s } }"
               % BEIN_FELDER)


def _zeit(s):
    return SH._zeit(s)


def _parse(s):
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def faellig(w: dict, jetzt: datetime) -> bool:
    """Ist diese Wette reif zum Nachfragen?

    Reif heisst: das Spiel ist lange genug her. Ohne Anpfiff-Angabe zählt der Zeitpunkt der
    Wette plus eine grosszuegige Frist — lieber einmal zu frueh gefragt (kostet eine Anfrage)
    als eine Wette nie abgerechnet (kostet die Messung).
    """
    a = w.get("abrechnung") or {}
    if a.get("endstand"):
        return False
    # 12.10.2026: aufgegebene Wetten fragen wir nicht mehr — s. reihenfolge().
    if a.get("unaufloesbar"):
        return False
    anker = _parse(w.get("anpfiff")) or _parse(w.get("ts"))
    if not anker:
        return False
    return jetzt >= anker + timedelta(hours=REIF_H)


def reihenfolge(w: dict, vorrang: set | None = None):
    """Sortierschluessel fuer die Nachfrage. REIN.

    🔴 12.10.2026 (Lucas fragte nach den Stake-Bursts in den Stats; dabei aufgefallen): seit
    dem 08.10. ~01:43 UTC wurde KEINE Stake-Wette mehr abgerechnet — 17.015 reife Wetten offen,
    W41 der Burst-Bilanz 8 von 41 abgerechnet. Hier stand „aelteste zuerst", gedeckelt auf 300
    je Lauf. Fuer 298 Wetten vom 08.10. kam von Stake keine Antwort (bet(iid) leer). Sie blieben
    damit reif, standen als aelteste immer vorn und fuellten jeden Lauf allein — alles danach
    wurde nie gefragt. Fehlerklasse: eine Warteschlange, deren Kopf nie fertig wird.

    Jetzt: nie gefragte Wetten zuerst (aelteste zuerst), danach die schon gefragten nach dem
    Zeitpunkt der letzten Frage. Eine Wette ohne Antwort rutscht so nach jedem Versuch nach
    hinten, statt den Kopf zu blockieren.
    """
    a = w.get("abrechnung") or {}
    g = a.get("geprueft")
    # 13.10.2026: Wetten hinter einer gesendeten Meldung (Burst-/Kleine-Liga-/Torlinien-Push)
    # kommen VOR allem anderen dran. Sie sind die Messung; der Rest ist Statistik. Und sie
    # haben eine Frist: nach ~5 Tagen fallen sie aus dem rollierenden Ledger und die Meldung
    # wird als „nicht abrechenbar" abgeschrieben (stake_burst_push.abrechnen).
    # Unter den Meldungs-Wetten zuerst die AELTESTEN — sie fallen als erste aus dem Fenster.
    # (Hinter den offenen Meldungen standen am 13.10. 1.267 Wetten, nicht elf: mehr als ein Lauf.)
    if vorrang and w.get("id") in vorrang:
        return (0, 0, str(w.get("ts") or ""))
    return (1, 1, str(g)) if g else (1, 0, str(w.get("ts") or ""))


def vorrang_ids(burst_ledger) -> set:
    """Wett-IDs hinter noch offenen gesendeten Meldungen (alle Arten). REIN."""
    zeilen = burst_ledger if isinstance(burst_ledger, list) else \
        ((burst_ledger or {}).get("zeilen") or [])
    out = set()
    for z in zeilen:
        if isinstance(z, dict) and z.get("status") == "pending":
            out.update(i for i in (z.get("betIds") or []) if i)
    return out


def aufgegeben(w: dict, jetzt: datetime) -> bool:
    anker = _parse(w.get("anpfiff")) or _parse(w.get("ts"))
    return bool(anker and jetzt >= anker + timedelta(days=AUFGEBEN_D))


def _alias(i: int) -> str:
    return "b%d" % i


def frage_batch(url: str, iids: list):
    """Mehrere Wetten in EINER Anfrage — GraphQL-Aliase machen das möglich.
    300 Wetten einzeln wären 300 Anfragen; so sind es zwölf."""
    if not iids:
        return {}, None
    teile, vars_, werte = [], [], {}
    for i, iid in enumerate(iids):
        teile.append('%s: bet(iid: $i%d) { id iid bet { %s } }' % (_alias(i), i, WETT_FELDER))
        vars_.append("$i%d: String!" % i)
        werte["i%d" % i] = iid
    q = "query S(%s) { %s }" % (", ".join(vars_), " ".join(teile))
    st, d, err = SH._post(url, {"query": q, "variables": werte}, timeout=40)
    if not d or not d.get("data"):
        return {}, err or json.dumps((d or {}).get("errors"))[:200]
    # 13.10.2026: Stake drosselt auch EINZELNE Aliase in einem sonst beantworteten Stapel —
    # mit einem Fehler je Pfad: {"path": ["b0"], "message": "Request limit reached.",
    # "errorType": "rateLimitHandler"}. Solche Wetten kommen als None in `out`: gefragt, aber
    # nicht beantwortet, und KEIN Befund ueber die Wette (main zaehlt sie nicht als ohneAntwort).
    gedrosselt = set()
    for e in (d.get("errors") or []):
        if isinstance(e, dict) and _ist_drossel(e):
            for p in (e.get("path") or [])[:1]:
                gedrosselt.add(p)
    out = {}
    for i, iid in enumerate(iids):
        knoten = (d["data"] or {}).get(_alias(i))
        if knoten:
            out[iid] = knoten
        elif _alias(i) in gedrosselt:
            out[iid] = None
    if not any(out.values()):
        # 🔴 13.10.2026 — GEDROSSELT. Seit dem 08./09.10. beantwortete Stake je Lauf nur den
        # ersten (selten zweiten) Stapel; jeder weitere kam mit `data` zurueck, aber ALLE
        # Aliase leer. Hier wurde das als „diese 25 Wetten haben keine Antwort" verbucht,
        # weitergefragt und die Wetten nach hinten geschoben: ~970 von ~1.000 Fragen je Lauf
        # ins Leere, 16.344 von 20.000 Wetten mit `ohneAntwort`, und die Kleine-Liga-Meldungen
        # seit dem 09.10. blieben offen. Belege aus dem Ledger: frische Wetten (<12 h) scheiterten
        # genauso (165), alte (>48 h) wurden beantwortet (145) — nicht das Alter, die Position
        # im Lauf entschied. Ein Stapel, in dem KEINE Wette eine Antwort hat, ist deshalb ein
        # Fehler des Stapels, nicht 25 Befunde ueber Wetten.
        return {}, "leerer Stapel (gedrosselt?) " + json.dumps((d or {}).get("errors"))[:200]
    return out, None


def _ist_drossel(e: dict) -> bool:
    txt = (str(e.get("errorType") or "") + " " + str(e.get("message") or "")).lower()
    return "ratelimit" in txt or "rate limit" in txt or "request limit" in txt


def lies_abrechnung(knoten: dict, kurse: dict = None) -> dict:
    """Aus der Antwort den Endstand ziehen. Kein Raten: was Stake nicht sagt, bleibt None."""
    b = (knoten or {}).get("bet") or {}
    status = (b.get("status") or "").lower() or None
    beine = []
    for o in (b.get("outcomes") or []):
        s = (o.get("status") or "").lower() or None
        beine.append({
            "status": s,
            "quote": o.get("odds"),
            "auswahl": ((o.get("outcome") or {}).get("name")),
            "event": o.get("fixtureName"),
            "treffer": None if (s in OFFEN or s in NEUTRAL) else (s == "won"),
            "neutral": s in NEUTRAL,
        })
    offen = status in OFFEN or any(x["status"] in OFFEN for x in beine)
    betrag = b.get("amount")
    waehrung = (b.get("currency") or "").lower() or None
    auszahlung = b.get("payout")
    einsatz_usd, _g1 = SH._usd(betrag, waehrung, {}, kurse)
    aus_usd, _g2 = SH._usd(auszahlung, waehrung, {}, kurse)
    return {
        "endstand": (not offen) and bool(status),
        "status": status,
        "beine": beine,
        "auszahlung": auszahlung,
        "auszahlungUsd": aus_usd,
        "auszahlungFaktor": b.get("payoutMultiplier"),
        "einsatzUsdGeprueft": einsatz_usd,
        # Gewinn/Verlust NUR bei Einzelwetten — bei einer Kombi haengt der Einsatz an
        # mehreren Spielen und ist keinem davon zurechenbar.
        "pnlUsd": (round(aus_usd - einsatz_usd, 2)
                   if (len(beine) == 1 and aus_usd is not None and einsatz_usd is not None)
                   else None),
        "geprueft": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def bilanz(wetten: list) -> dict:
    """Was steht im Ledger — ohne jede Bewertung, nur gezählt."""
    beine = treffer = daneben = neutral = 0
    offen = unaufloesbar = 0
    einzel_pnl, einzel_einsatz = 0.0, 0.0
    n_einzel = 0
    for w in wetten:
        a = w.get("abrechnung") or {}
        if not a.get("endstand"):
            if a.get("unaufloesbar"):
                unaufloesbar += 1
            else:
                offen += 1
            continue
        for x in a.get("beine") or []:
            beine += 1
            if x.get("neutral"):
                neutral += 1
            elif x.get("treffer") is True:
                treffer += 1
            elif x.get("treffer") is False:
                daneben += 1
        if a.get("pnlUsd") is not None and w.get("einsatzUsd"):
            n_einzel += 1
            einzel_pnl += a["pnlUsd"]
            einzel_einsatz += w["einsatzUsd"]
    gewertet = treffer + daneben
    return {
        "beine": beine, "treffer": treffer, "daneben": daneben, "neutral": neutral,
        "gewertet": gewertet,
        "quote": round(treffer / gewertet, 4) if gewertet else None,
        "offen": offen, "unaufloesbar": unaufloesbar,
        "einzelN": n_einzel,
        "einzelEinsatzUsd": round(einzel_einsatz, 2),
        "einzelPnlUsd": round(einzel_pnl, 2),
        "einzelRoi": round(einzel_pnl / einzel_einsatz, 4) if einzel_einsatz else None,
    }


def main() -> int:
    print("=== stake_settle.py ===")
    jetzt = datetime.now(timezone.utc)
    led = SH._lade(LEDGER_FILE, {})
    wetten = led.get("wetten") or []
    if not wetten:
        print("  ℹ️  leeres Ledger — nichts abzurechnen.")
        return 0

    url = (SH._lade(QUERY_FILE, {}) or {}).get("url") or SH.ENDPUNKTE[0]
    kurse = SH._lade(KURS_FILE, {})

    vorrang = vorrang_ids(SH._lade(BURST_FILE, []))
    dran = [w for w in wetten if faellig(w, jetzt) and w.get("id")]
    dran.sort(key=lambda w: reihenfolge(w, vorrang))   # Meldungen, dann nie gefragt, s. reihenfolge()
    dran = dran[:MAX_JE_LAUF]
    n_vor = sum(1 for w in dran if w["id"] in vorrang)
    print("  %d von %d Wetten sind reif (>= %.0fh nach Anpfiff), davon %d hinter einer Meldung."
          % (len(dran), len(wetten), REIF_H, n_vor))

    nach_id = {w["id"]: w for w in wetten if w.get("id")}
    geholt = fehler = ohne_antwort = 0
    stapel_ok = stapel_leer = 0
    beantwortet = set()
    gedrosselt_n = 0
    abbruch, letzter_fehler = None, None
    start = time.monotonic()
    for i in range(0, len(dran), BATCH):
        if time.monotonic() - start > BUDGET_S:
            abbruch = "Zeitbudget %.0fs" % BUDGET_S
            break
        if i:
            time.sleep(PAUSE_S)
        stapel = [w["id"] for w in dran[i:i + BATCH]]
        antw, err = frage_batch(url, stapel)
        if err and err.startswith("leerer Stapel"):
            # Einmal durchatmen und denselben Stapel nochmal. Bleibt er leer, ist fuer diesen
            # Lauf Schluss — die uebrigen Wetten werden NICHT als „ohne Antwort" verbucht.
            stapel_leer += 1
            letzter_fehler = err
            time.sleep(RUHE_S)
            antw, err = frage_batch(url, stapel)
            if err:
                abbruch = "gedrosselt nach %d beantworteten Stapeln" % stapel_ok
                print("  ⏸️  Stapel %d leer, auch nach %.0fs Pause — Rest beim naechsten Lauf. %s"
                      % (i // BATCH + 1, RUHE_S, err[:120]))
                break
        if err:
            fehler += 1
            letzter_fehler = err
            print("  ⚠️  Stapel %d: %s" % (i // BATCH + 1, err[:120]))
            continue
        stapel_ok += 1
        gedrosselt_n += sum(1 for v in antw.values() if v is None)
        for iid, knoten in antw.items():
            w = nach_id.get(iid)
            if not w or knoten is None:      # None = gedrosselt, s. frage_batch
                continue
            w["abrechnung"] = lies_abrechnung(knoten, kurse)
            beantwortet.add(iid)
            geholt += 1
        # Gefragt, aber keine Antwort: festhalten, damit die Wette nach hinten rutscht.
        _jetzt_s = jetzt.isoformat().replace("+00:00", "Z")
        for iid in stapel:
            if iid in antw or iid not in nach_id:
                continue
            a = dict(nach_id[iid].get("abrechnung") or {})
            a["geprueft"] = _jetzt_s
            a["ohneAntwort"] = int(a.get("ohneAntwort") or 0) + 1
            nach_id[iid]["abrechnung"] = a
            ohne_antwort += 1

    # Wer nach der Frist immer noch offen ist, wird als unaufloesbar MARKIERT — nicht
    # geloescht. Eine Wette, die verschwindet, faelscht jede Quote nach oben.
    aufgeg = 0
    for w in wetten:
        a = w.get("abrechnung") or {}
        if not a.get("endstand") and aufgegeben(w, jetzt) and not a.get("unaufloesbar"):
            a["unaufloesbar"] = True
            a.setdefault("geprueft", jetzt.isoformat().replace("+00:00", "Z"))
            w["abrechnung"] = a
            aufgeg += 1

    led["wetten"] = wetten
    led["bilanz"] = bilanz(wetten)
    # Was dieser Lauf geschafft hat — damit eine Drosselung im Ledger steht und nicht nur
    # in einem Log, das niemand liest (13.10.2026: so blieb der Fehler fuenf Tage unentdeckt).
    led["abrechnungLauf"] = {
        "am": jetzt.isoformat().replace("+00:00", "Z"),
        "reif": len(dran), "vorrang": n_vor,
        "vorrangBeantwortet": len(beantwortet & vorrang),
        "beantwortet": geholt, "ohneAntwort": ohne_antwort, "einzelnGedrosselt": gedrosselt_n,
        "stapelOk": stapel_ok, "stapelLeer": stapel_leer, "stapelFehler": fehler,
        "abbruch": abbruch, "letzterFehler": (letzter_fehler or None) and letzter_fehler[:200],
    }
    led["abgerechnet"] = jetzt.isoformat().replace("+00:00", "Z")
    SH._schreibe(LEDGER_FILE, led)

    b = led["bilanz"]
    print("  %d nachgefragt, %d ohne Antwort, %d als unaufloesbar markiert, %d Stapel mit Fehler, "
          "%d Stapel beantwortet%s."
          % (geholt, ohne_antwort, aufgeg, fehler, stapel_ok,
             (" · Abbruch: " + abbruch) if abbruch else ""))
    print("  Beine: %d gewertet (%d Treffer, %d daneben, %d neutral) · offen %d · unaufloesbar %d"
          % (b["gewertet"], b["treffer"], b["daneben"], b["neutral"], b["offen"], b["unaufloesbar"]))
    if b["quote"] is not None:
        print("  Rohe Trefferquote: %.1f%% auf n=%d — ein Punktschaetzer, kein Beleg."
              % (b["quote"] * 100, b["gewertet"]))
    if b["einzelRoi"] is not None:
        print("  Einzelwetten: ROI %.1f%% auf $%.0f Einsatz (n=%d)"
              % (b["einzelRoi"] * 100, b["einzelEinsatzUsd"], b["einzelN"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
