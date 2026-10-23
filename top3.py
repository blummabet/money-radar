"""Top 3 jetzt — dieselbe Wertung wie auf der Startseite, für die Messung (Lucas 23.10.: „ja bitte").

Vorab festgelegt (23.10., 19:45 Wien):
  • Über ALLE Ligen gewertet (der Liga-Schalter ist nur Anzeige).
  • Ins Buch kommt jedes Spiel + Markt + Auswahl beim ERSTEN Einzug in die Top 3, zur Quote in diesem Moment,
    mit Platz und Punkten. Später erneut oben = kein zweiter Eintrag (nur „bestRang" wird nachgezogen).
  • Betfair-Spiele art „top3" (abgerechnet wie die Betfair-Pushes, 2 % Kommission, Schlussquote vor Anpfiff),
    Polymarket-Märkte ohne Betfair art „top3p" (abgerechnet wie der Polymarket-Dauerzufluss).
  • Ziel 60 abgerechnete. Bestanden = Rendite auch abzüglich Fehler über null UND Schlussquote/Kurs danach geschlagen.

Die Regeln müssen zu topDreiDaten() in site/index.html passen — ändert sich das eine, das andere mitziehen.
"""
from __future__ import annotations

import math
import re
import unicodedata
from datetime import datetime, timedelta, timezone

STOP = {"al", "el", "fc", "sc", "cf", "ac", "as", "ud", "cd", "sk", "fk", "club", "de", "la", "le", "the", "real", "ca", "ss",
        "us", "sv", "vfb", "vfl", "tsg", "u19", "u21", "u23", "team", "esports", "gaming", "academy"}


def _ts(s):
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def team(n):
    t = unicodedata.normalize("NFD", str(n or "")).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"\(.*?\)", " ", t)
    w = [x for x in re.split(r"[^a-z0-9]+", t) if len(x) >= 3 and x not in STOP]
    return w[0] if w else str(n or "").lower().strip()


def schluessel(h, g):
    return "|".join(sorted([team(h), team(g)])) if h and g else None


def seite_von(heim, gast, name):
    n = str(name or "")
    if re.match(r"^(the )?draw$", n, re.I):
        return "remis"
    if n == heim:
        return "heim"
    if n == gast:
        return "gast"
    t, th, tg, nn = team(n), team(heim), team(gast), n.lower()
    if t and t == th:
        return "heim"
    if t and t == tg:
        return "gast"
    if th and th in nn:
        return "heim"
    if tg and tg in nn:
        return "gast"
    return None


def _ou(n):
    m = re.match(r"^(over|under)", str(n or ""), re.I)
    return m.group(1).lower() if m else None


def _burst(g, jetzt):
    arten = " ".join(g.get("arten") or [g.get("art") or ""])
    t = _ts(g.get("zeit"))
    return bool(re.search("burst", arten, re.I) and t and jetzt - t < timedelta(hours=3))


def _teams_aus_titel(titel):
    t = re.sub(r"^[A-Za-z0-9 .-]{2,24}: ", "", str(titel or ""))
    t = re.sub(r" - More Markets$| - Halftime Result$", "", t)
    t = re.sub(r" \(BO\d\).*$", "", t)
    teile = [x.strip() for x in re.split(r"\s+vs\.?\s+|\s+-\s+|\s+–\s+", t, flags=re.I) if x.strip()]
    return teile[:2] if len(teile) >= 2 else (None, None)


def rangliste(d, jetzt):
    LB = (d.get("ligaBilanz") or {}).get("ligen") or {}
    serien = d.get("serien") or []
    pushes = (d.get("bfPushes") or {}).get("liste") or []
    toepfe = {str(t.get("id")): t for t in d.get("toepfe") or []}
    auch = d.get("auchWoanders") or {}
    gruppen = (d.get("stake") or {}).get("gruppen") or []
    aus = []
    for s in (d.get("radar") or {}).get("spiele") or []:
        lv = s.get("live") or None
        if s.get("entschieden") or s.get("klarVorn") or (lv and isinstance(lv.get("min"), (int, float)) and lv["min"] >= 85):
            continue
        md, p = s.get("maerkteDetail") or [], {}
        se = next((x for x in serien if x.get("id") == s.get("id")), None)
        top = mk = None
        neu = 0
        for m in md:
            for a in m.get("auswahl") or []:
                q = a.get("quote")
                if a.get("name") == "Rest" or (q and q < 1.20) or not (a.get("neu") or 0) > 0:
                    continue
                neu += a["neu"]
                if not top or a["neu"] > top["neu"]:
                    top, mk = a, m
        if se and (se.get("quote") or 0) >= 1.20:
            p["serie"] = min(4, 2.5 + (se["n"] - 3) * 0.5)
        pz = [x for x in pushes if x.get("heim") == s.get("heim") and x.get("gast") == s.get("gast") and _ts(x.get("zeit"))
              and jetzt - _ts(x["zeit"]) < timedelta(hours=6) and re.search("Halbzeit|Fix", str(x.get("art") or ""), re.I)]
        if pz:
            p["push"] = min(2, 1 + 0.5 * (len(pz) - 1))
        if neu >= max(1000, 0.03 * (s.get("eur") or 0)):
            p["neu"] = min(3, math.log2(neu / 1000) + 1)
        if (s.get("xnorm") or 0) >= 1.5:
            p["norm"] = min(2, (s["xnorm"] - 1) * 0.8)
        fA, fM = top, mk
        if se:
            m = next((x for x in md if x.get("name") == se.get("markt")), None)
            a = m and next((x for x in m.get("auswahl") or [] if x.get("name") == se.get("auswahl")), None)
            if a:
                fA, fM = a, m
        if not fA:
            continue
        stand = (lv or {}).get("stand") or [0, 0]
        tore = (stand[0] or 0) + (stand[1] or 0) if lv else 0
        fk = seite_von(s.get("heim"), s.get("gast"), fA.get("name"))
        zeit_drop = fk == "remis" or str(fA.get("name") or "").lower().startswith("under")
        q, q60 = fA.get("quote"), fA.get("q60")
        if q60 and q and q < q60 * 0.97 and not (lv and (tore > 0 or zeit_drop)):
            p["quote"] = min(2, (q60 / q - 1) * 100 / 5)
        aw = auch.get(str(s.get("id"))) or {}
        wale = [w for w in (toepfe.get(str(s.get("id"))) or {}).get("wale") or [] if fk and w.get("bewiesen") and w.get("k") == fk]
        if wale:
            p["poly"] = 2
        elif ((aw.get("poly") or {}).get("usd") or 0) >= 1000:
            p["poly"] = 1
        sk = schluessel(s.get("heim"), s.get("gast"))

        def gleich(g):
            if fk:
                return seite_von(s.get("heim"), s.get("gast"), g.get("auswahl")) == fk
            return bool(_ou(fA.get("name")) and _ou(fA.get("name")) == _ou(g.get("auswahl")))
        burst = next((g for g in gruppen if _burst(g, jetzt) and schluessel(*(str(g.get("spiel") or "").split(" - ", 1) + [""])[:2]) == sk
                      and gleich(g)), None)
        if burst:
            p["stake"] = 2
        elif ((aw.get("stake") or {}).get("usd") or 0) >= 1000:
            p["stake"] = 1
        lb = LB.get(s.get("liga")) or {}
        if lb.get("urteil") == "trägt":
            p["liga"] = 1
        summe = sum(p.values()) - (1 if lb.get("urteil") == "trägt nicht" else 0)
        aus.append({"art": "top3", "id": str(s.get("id")), "heim": s.get("heim"), "gast": s.get("gast"), "liga": s.get("liga"),
                    "anpfiff": s.get("anpfiff"), "live": bool(lv), "top5": bool(s.get("top5")), "markt": (fM or {}).get("name"),
                    "auswahl": fA.get("name"), "quote": q, "eur": s.get("eur"), "punkte": round(summe, 2),
                    "signale": sorted(p), "n": len(p)})

    # Polymarket-Märkte ohne Betfair-Spiel (E-Sport, Tennis, Fußball, den Betfair nicht führt)
    bf_teams = [((team(s.get("heim")), team(s.get("gast"))), _ts(s.get("anpfiff"))) for s in (d.get("radar") or {}).get("spiele") or []]
    pm_maerkte = {m.get("key"): m for m in (d.get("poly") or {}).get("maerkte") or []}
    for m in (d.get("polyFluss") or {}).get("maerkte") or []:
        if re.search(r"-more-markets$", str(m.get("key") or "")):
            continue
        heim, gast = _teams_aus_titel(m.get("titel"))
        if not heim:
            continue
        ko = _ts(m.get("anpfiff"))
        a_, b_ = team(heim), team(gast)
        if any((a_ in t or b_ in t) and (not ko or not k or abs((k - ko).total_seconds()) < 3 * 3600) for t, k in bf_teams):
            continue
        se = next((x for x in d.get("polySerien") or [] if x.get("id") == m.get("key")), None)
        w = (m.get("fenster") or {}).get("30") or {}
        fokus = se.get("auswahl") if se else (w.get("auf") if (w.get("anteil") or 0) >= 60 else None)
        if not fokus:
            continue
        preis = (m.get("preise") or {}).get(fokus) or (se or {}).get("preis")
        quote = 1 / preis if preis else (se or {}).get("quote")
        if not quote or quote < 1.20:
            continue
        p = {}
        if se and (se.get("quote") or 0) >= 1.20:
            p["serie"] = min(4, 2.5 + (se["n"] - 3) * 0.5)
        sei = w.get("seiten") or {}
        neu = (sei.get(fokus) or 0) - (sei.get("gegen " + fokus) or 0)
        if neu >= max(2000, 0.03 * (m.get("usd") or 0)):
            p["neu"] = min(3, math.log2(neu / 1000) + 1)
        if [x for x in (pm_maerkte.get(m.get("key")) or {}).get("wale") or [] if x.get("bewiesen") and x.get("seite") == fokus]:
            p["poly"] = 2
        sk = schluessel(heim, gast)
        if any(_burst(g, jetzt) and schluessel(*(str(g.get("spiel") or "").split(" - ", 1) + [""])[:2]) == sk
               and team(g.get("auswahl")) == team(fokus) for g in gruppen):
            p["stake"] = 2
        aus.append({"art": "top3p", "id": m.get("key"), "heim": heim, "gast": gast, "titel": m.get("titel"), "liga": m.get("liga"),
                    "sport": m.get("sport") or "Fußball", "anpfiff": m.get("anpfiff"), "live": bool(m.get("live")),
                    "top5": bool(m.get("top5")), "markt": "Sieger", "auswahl": fokus, "quote": round(quote, 4),
                    "preis": round(1 / quote, 4), "usd": m.get("usd"), "punkte": round(sum(p.values()), 2), "signale": sorted(p), "n": len(p)})
    aus = [x for x in aus if x["n"] >= 2 and x["punkte"] >= 3]
    aus.sort(key=lambda x: -x["punkte"])
    return aus


def eintragen(d, buch, jetzt):
    """Neue Einzüge in die Top 3 ins Buch; bestehende bekommen nur ihren besten Platz nachgezogen."""
    neu = 0
    offen = {(b.get("id"), b.get("markt"), b.get("auswahl")): b for b in buch if b.get("art") in ("top3", "top3p")}
    for rang, x in enumerate(rangliste(d, jetzt)[:3], 1):
        k = (x["id"], x["markt"], x["auswahl"])
        if k in offen:
            b = offen[k]
            b["bestRang"] = min(b.get("bestRang") or rang, rang)
            b["bestPunkte"] = max(b.get("bestPunkte") or 0, x["punkte"])
            continue
        b = {**x, "rang": rang, "bestRang": rang, "bestPunkte": x["punkte"], "ts": jetzt.isoformat(), "status": "offen"}
        buch.append(b)
        offen[k] = b
        neu += 1
    return neu
