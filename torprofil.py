"""Torprofil (Lucas 22.11. 18:01: „ich gehe dann händisch in Flashscore und schaue, wie spielen die zu Hause die letzten
Spiele, wie das Auswärtsteam auswärts, wie viele Tore, wie viele 0:0 … Teams, die drücken wie irre, aber immer sehr
torarme Spiele — dann ist es wertlos").

Je Live-Spiel mit API-Football-Zuordnung: die letzten 6 Heimspiele des Heimteams und die letzten 6 Auswärtsspiele des
Gasts (sonst die letzten 6 überhaupt) — Ø Tore, Über 2,5, 0:0, knappe Spiele (≤ 1 Tor), Halbzeit-Tore. Daraus die
Tor-Erwartung aus der Form ((Heim-Tore + Gast-Gegentore)/2 + (Gast-Tore + Heim-Gegentore)/2) neben die des Markts.
Je Team ein Abruf /fixtures?team=…&last=15, 20 h zwischengespeichert (.bf/torprofil.json, ein paar hundert KB).

Vorab festgelegt (TP1, still gemessen): „torreich – erst x Tore" = live, höchstens 1 Tor, Druck-Index ≥ 55 eines
Teams, Form-Erwartung ≥ 3,0 Tore ODER ≥ 8 von 12 Spielen über 2,5. Gebucht wird Über (Tore + 0,5) zur Betfair-Quote,
je Spiel und Stand einmal. Urteil nach 100 Fällen: trägt, wenn öfter getroffen wird, als die Quote erwartet.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATEI = Path(__file__).resolve().parent / ".bf" / "torprofil.json"
FRISCH_H = 20
JE_LAUF = 14
N_SPIELE = 6
JETZT = datetime.now(timezone.utc)


def _ts(s):
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def _spiele(resp, tid):
    """→ [[datum, 'H'|'A', gegner, tore_für, tore_gegen, hz_für, hz_gegen], …] neueste zuerst, nur beendete Spiele."""
    out = []
    for fx in resp or []:
        st = ((fx.get("fixture") or {}).get("status") or {}).get("short")
        if st not in ("FT", "AET", "PEN") or "friendl" in str((fx.get("league") or {}).get("name") or "").lower():
            continue                         # Testspiele zählen nicht (OFI hatte Rennes, Hapoel Be'er Sheva drin)
        t, g, sc = fx.get("teams") or {}, fx.get("goals") or {}, (fx.get("score") or {}).get("halftime") or {}
        heim = (t.get("home") or {}).get("id") == tid
        gh, ga = g.get("home"), g.get("away")
        if not isinstance(gh, int) or not isinstance(ga, int):
            continue
        hh, ha = sc.get("home"), sc.get("away")
        out.append([str((fx.get("fixture") or {}).get("date"))[:10], "H" if heim else "A",
                    ((t.get("away") if heim else t.get("home")) or {}).get("name"),
                    gh if heim else ga, ga if heim else gh,
                    (hh if heim else ha) if isinstance(hh, int) else None, (ha if heim else hh) if isinstance(ha, int) else None])
    out.sort(key=lambda x: x[0], reverse=True)
    return out


def seite(spiele, ort):
    """Profil aus den letzten 6 Spielen am Ort (H/A), sonst den letzten 6 überhaupt. REIN."""
    am_ort = [s for s in spiele if s[1] == ort][:N_SPIELE]
    wahl, nur_ort = (am_ort, True) if len(am_ort) >= 4 else (spiele[:N_SPIELE], False)
    n = len(wahl)
    if n < 3:
        return None
    tore = [s[3] + s[4] for s in wahl]
    hz = [s[5] + s[6] for s in wahl if isinstance(s[5], int) and isinstance(s[6], int)]
    return {"n": n, "amOrt": nur_ort, "avg": round(sum(tore) / n, 2), "fuer": round(sum(s[3] for s in wahl) / n, 2),
            "gegen": round(sum(s[4] for s in wahl) / n, 2), "ue25": sum(t > 2.5 for t in tore), "ue15": sum(t > 1.5 for t in tore),
            "null": sum(t == 0 for t in tore), "knapp": sum(t <= 1 for t in tore),
            "htAvg": round(sum(hz) / len(hz), 2) if hz else None, "ht05": sum(t >= 1 for t in hz) if hz else None, "htN": len(hz),
            "spiele": [[s[0][8:10] + "." + s[0][5:7] + ".", s[1], s[2], f"{s[3]}:{s[4]}", f"{s[5]}:{s[6]}" if isinstance(s[5], int) else None] for s in wahl]}


def profil(h, g):
    if not h or not g:
        return None
    erw = round((h["fuer"] + g["gegen"]) / 2 + (g["fuer"] + h["gegen"]) / 2, 2)
    n = h["n"] + g["n"]
    ue25 = h["ue25"] + g["ue25"]
    torreich = erw >= 3.0 or ue25 >= round(8 / 12 * n)
    torarm = erw <= 2.0 or (h["knapp"] + g["knapp"]) >= round(6 / 12 * n)
    return {"heim": h, "gast": g, "erwartung": erw, "ue25": ue25, "n": n, "torreich": torreich, "torarm": torarm and not torreich}


def lauf(d, live_fx, get, n, zuordnen):
    """Füllt d["torprofil"] {radar-id: profil + hinweis}. Zuordnung: Live-Spiele über die Live-Liste (schon abgerufen),
    Spiele der nächsten 8 h über /fixtures?date=heute (1 Abruf je 3 h). Höchstens min(JE_LAUF, n) Abrufe. → Abrufe."""
    try:
        tp = json.loads(DATEI.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        tp = {}
    alt = lambda x, h: JETZT - (_ts(x) or JETZT - timedelta(days=9)) >= timedelta(hours=h)
    teams = {k: v for k, v in (tp.get("teams") or {}).items() if not alt(v.get("ts"), 96) and v.get("v") == 2}
    zuord = {k: v for k, v in (tp.get("zuord") or {}).items() if not alt(v[2], 30)}
    spiele = [s for s in (d.get("radar") or {}).get("spiele") or [] if s.get("id")]
    radar = {str(s["id"]): s for s in spiele}
    grenze = min(JE_LAUF, max(0, n))
    abrufe = 0

    def zu(fxs, nur):
        for sp in nur:
            fx = zuordnen(sp, fxs)
            t = (fx or {}).get("teams") or {}
            hid, aid = (t.get("home") or {}).get("id"), (t.get("away") or {}).get("id")
            if hid and aid:
                zuord[str(sp["id"])] = [hid, aid, JETZT.isoformat()]

    offen = lambda live: [s for s in spiele if bool(s.get("live")) == live and str(s["id"]) not in zuord]
    if live_fx:
        zu(live_fx, offen(True))
    bald = [s for s in offen(False) if (lambda k: k and timedelta(0) <= k - JETZT <= timedelta(hours=8))(_ts(s.get("anpfiff")))]
    if bald and alt(tp.get("tagTs"), 3) and abrufe < grenze:
        r = get("/fixtures?date=%s" % JETZT.strftime("%Y-%m-%d"))
        abrufe += 1
        tp["tagTs"] = JETZT.isoformat()
        zu((r or {}).get("response") or [], bald)
    # Reihenfolge der Team-Abrufe: Live zuerst, dann nach Anpfiff
    reihe = sorted((rid for rid in zuord if rid in radar),
                   key=lambda rid: (not radar[rid].get("live"), str(radar[rid].get("anpfiff") or "")))
    druck = {str(x.get("id")): x for x in ((d.get("druck") or {}).get("spiele") or [])} if isinstance(d.get("druck"), dict) else {}
    out = {}
    for rid in reihe:
        hid, aid = zuord[rid][:2]
        for tid in (hid, aid):
            k = str(tid)
            if (k in teams and not alt(teams[k].get("ts"), FRISCH_H)) or abrufe >= grenze:
                continue
            r = get("/fixtures?team=%s&last=15" % tid)
            abrufe += 1
            if r is not None:
                teams[k] = {"v": 2, "ts": JETZT.isoformat(), "spiele": _spiele((r or {}).get("response"), tid)}
        p = profil(seite((teams.get(str(hid)) or {}).get("spiele") or [], "H"),
                   seite((teams.get(str(aid)) or {}).get("spiele") or [], "A"))
        if not p:
            continue
        sp = radar[rid]
        fl = sp.get("torFluss") or {}
        p["markt"] = fl.get("rate") if isinstance(fl.get("rate"), (int, float)) else None
        st = (sp.get("live") or {}).get("stand") or [None, None]
        tore = (st[0] or 0) + (st[1] or 0) if sp.get("live") and None not in st else None
        idx = (druck.get(rid) or {}).get("index") or 0
        p["hinweis"] = bool(tore is not None and tore <= 1 and idx >= 55 and p["torreich"])
        p["tore"], p["druck"] = tore, idx
        out[rid] = p
    tp.update({"ts": JETZT.isoformat(), "teams": teams, "zuord": zuord})
    DATEI.parent.mkdir(exist_ok=True)
    DATEI.write_text(json.dumps(tp, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    d["torprofil"] = out
    return abrufe
