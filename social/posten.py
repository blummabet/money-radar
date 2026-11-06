#!/usr/bin/env python3
"""posten.py — Instagram-Posts für @cocobet89 (Lucas 04.11.2026).

  morgen  ~9:25  „Gestern im Kanal": alle Public-Signale des Vortags (Wiener Kalendertag) mit ✓/✗
  abend   ~17:10 „Wo das Geld liegt": die Top 3 „Geld über der Quote" (ueber_quote.py schreibt site/ig_top3.json)

Bild → Zweig „bilder" (öffentlich über raw.githubusercontent.com, Meta holt es dort ab) → Graph API.
Je Art höchstens ein Post am Tag (gemerkt in bilder/gepostet.json). TROCKEN=1: nur Bild bauen, nichts posten.
"""
from __future__ import annotations

import json
import os
import random
import re
import string
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import grafik  # noqa: E402

SEITE = "https://blummabet.github.io/money-radar/"
RAW = "https://raw.githubusercontent.com/{repo}/bilder/{datei}"
WIEN = ZoneInfo("Europe/Vienna")
JETZT = datetime.now(timezone.utc)
W = JETZT.astimezone(WIEN)
REPO = os.environ.get("GITHUB_REPOSITORY", "blummabet/money-radar")
ARBEIT = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "bilder"
TAGS = "#fussball #sportwetten #betfair #polymarket #wetten #fußball #championsleague #esports"


def hol(url, text=False):
    req = urllib.request.Request(url, headers={"User-Agent": "geldradar-instagram", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=30) as r:
        b = r.read()
    return b.decode("utf-8") if text else json.loads(b)


def statistik():
    """Über den Zeiger (404.html mit data-stand), damit der CDN-Cache nicht stört."""
    zufall = "".join(random.choices(string.ascii_lowercase, k=10))
    try:
        z = hol(f"{SEITE}z/{zufall}", text=True)
    except urllib.error.HTTPError as e:
        z = e.read().decode("utf-8", "ignore")
    m = re.search(r'data-stand="(d/\d+)\.json"', z)
    if m:
        try:
            return hol(f"{SEITE}{m.group(1)}-s.json")
        except Exception:  # noqa: BLE001
            pass
    return hol(f"{SEITE}statistik.json?t={int(time.time())}")


def daten():
    """Die ganze Seiten-Datei (d/<stand>.json) über den Zeiger."""
    zufall = "".join(random.choices(string.ascii_lowercase, k=10))
    try:
        z = hol(f"{SEITE}z/{zufall}", text=True)
    except urllib.error.HTTPError as e:
        z = e.read().decode("utf-8", "ignore")
    m = re.search(r'data-stand="(d/\d+)\.json"', z)
    d = hol(f"{SEITE}{m.group(1)}.json") if m else hol(f"{SEITE}data.json?t={int(time.time())}")
    try:                                        # 06.11.: hängt GitHub Pages, ist der Zweig „live" neuer
        s = hol(f"https://raw.githubusercontent.com/{REPO}/live/stand.json?t={int(time.time())}")
        if s.get("generatedAt", "") > d.get("generatedAt", ""):
            return hol(f"https://raw.githubusercontent.com/{REPO}/live/{s['datei']}?t={int(time.time())}")
    except Exception:  # noqa: BLE001
        pass
    return d


def bilder_zweig():
    ARBEIT.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["git", "fetch", "-q", "--depth=1", "origin", "bilder"], capture_output=True)
    if r.returncode == 0:
        subprocess.run(["git", f"--work-tree={ARBEIT}", "checkout", "origin/bilder", "--", "."], capture_output=True)
        subprocess.run(["git", "reset", "-q"], capture_output=True)
    p = ARBEIT / "gepostet.json"
    return json.loads(p.read_text()) if p.exists() else {}


def bilder_sichern(gepostet):
    (ARBEIT / "gepostet.json").write_text(json.dumps(gepostet, indent=1))
    jpgs = sorted(ARBEIT.glob("*.jpg"))
    for alt in jpgs[:-20]:
        alt.unlink()
    import shutil
    shutil.rmtree(ARBEIT / ".git", ignore_errors=True)   # 04.11.: zweiter Aufruf im selben Lauf scheiterte an „branch bilder already exists"
    tok = os.environ["GH_TOKEN"]
    cmds = [["git", "init", "-q"], ["git", "checkout", "-q", "--orphan", "bilder"], ["git", "add", "-A"],
            ["git", "-c", "user.name=geldradar-bot", "-c", "user.email=geldradar-bot@users.noreply.github.com", "commit", "-qm", f"Bilder {W:%F %H:%M}"],
            ["git", "push", "-qf", f"https://x-access-token:{tok}@github.com/{REPO}.git", "HEAD:bilder"]]
    for c in cmds:
        subprocess.run(c, cwd=ARBEIT, check=True)


def morgen():
    gestern = (W - timedelta(days=1)).date()
    d = statistik()
    F = d["felder"]
    rows = [dict(zip(F, z)) for z in d["zeilen"] if z[1] == "public"]
    tag = [r for r in rows if datetime.fromisoformat(r["zeit"] + "+00:00").astimezone(WIEN).date() == gestern]
    if not tag:
        return None
    sig = [{"zeit": r["zeit"], "spiel": r["titel"], "auswahl": r["auswahl"], "quote": r["quote"], "r": r["r"], "w": r["w"]} for r in tag]
    ab = [s for s in sig if s["r"] is not None and not (s["r"] == 0 and s["w"] != 1)]
    gew = sum(1 for s in ab if s["w"] == 1)
    ein = sum(s["r"] for s in ab)
    html = grafik.bilanz(datetime.combine(gestern, datetime.min.time()), sig)
    text = (f"Gestern im Kanal: {gew}–{len(ab) - gew}, {'+' if ein >= 0 else '−'}{grafik.q2(abs(ein))} Einheiten bei {len(ab)} Signalen.\n\n"
            "Jedes Signal kommt aus echtem Geld: Betfair, Polymarket und Stake. Höchstens 3 pro Zeitfenster, "
            "jedes wird offen abgerechnet – auch die verlorenen.\n\n"
            "Die Signale kommen live im Telegram-Kanal: t.me/c0c0bet (Link in Bio)\n\n"
            "18+ · Keine Wettempfehlung · Glücksspiel kann süchtig machen.\n\n" + TAGS)
    return html, text


def abend():
    heute = W.strftime("%Y-%m-%d")
    for _ in range(12):                        # die Datei entsteht im ersten Seitenbau ab 17:00 — notfalls kurz warten
        try:
            t = hol(f"{SEITE}ig_top3.json?t={int(time.time())}")
            if t.get("tag") == heute:
                break
        except Exception:  # noqa: BLE001
            t = {}
        time.sleep(60)
    else:
        return None
    sp = t.get("spiele") or []
    if not sp:
        return None
    for s in sp:
        s["liga"] = re.sub(r"^UEFA |^English Sky Bet ", "", str(s.get("liga") or ""))
    html = grafik.geld_drei(sp, f"{grafik.TAGE[W.weekday()]}, {W.day}. {grafik.MONATE[W.month - 1]} · heute Abend")
    zeilen = "\n".join(f"{i}. {s['heim']} – {s['gast']}: {s['anteil']:.0f} % des Geldes auf "
                       f"{s['heim'] if s['seite'] == 'heim' else s['gast'] if s['seite'] == 'gast' else 'Remis'}, "
                       f"die Quote {grafik.q2(s['quote'])} erwartet {s['impl']:.0f} %" for i, s in enumerate(sp, 1))
    text = ("Wo das Geld heute Abend liegt – die drei Spiele, bei denen das Geld am weitesten über der Quote liegt:\n\n"
            f"{zeilen}\n\nZusammen aus Betfair, Polymarket und Stake. Ob das trägt, messen wir mit – Ergebnis gibt's hier.\n\n"
            "Live-Signale im Telegram-Kanal: t.me/c0c0bet (Link in Bio)\n\n"
            "18+ · Keine Wettempfehlung · Glücksspiel kann süchtig machen.\n\n" + TAGS)
    return html, text


def _veroeffentlichen(gepostet, schluessel, html, text, story=False):
    datei = f"{schluessel.split('|')[0]}-{W:%Y%m%d-%H%M}.jpg"
    grafik.rendern(html, ARBEIT / datei, 1920 if story else 1350)
    print(text)
    if os.environ.get("TROCKEN"):
        gepostet[f"trocken|{datei}"] = W.isoformat()
        bilder_sichern(gepostet)
        print("TROCKEN — Bild:", RAW.format(repo=REPO, datei=datei))
        return
    bilder_sichern(gepostet)
    url = RAW.format(repo=REPO, datei=datei)
    for _ in range(10):                        # bis raw.githubusercontent.com das Bild ausliefert
        try:
            urllib.request.urlopen(url, timeout=20).read(10)
            break
        except Exception:  # noqa: BLE001
            time.sleep(6)
    import ig_api
    host, _konto = ig_api.host_finden(os.environ["IG_USER_ID"])
    mid = ig_api.posten(host, os.environ["IG_USER_ID"], url, text, story=story)
    gepostet[schluessel] = {"zeit": W.isoformat(), "id": mid, "bild": datei}
    bilder_sichern(gepostet)


def wal_abend(gepostet):
    import wal
    w = wal.auswahl(daten(), JETZT)
    if not w:
        print("kein Wal heute (ab $10k, Fußball, Anpfiff in 5 h, Betfair-Spiel)")
        gepostet[f"wal|{W:%Y-%m-%d}"] = {"zeit": W.isoformat(), "keiner": True}
        if not os.environ.get("TROCKEN"):
            bilder_sichern(gepostet)
        return
    print("Wal:", json.dumps(w, ensure_ascii=False))
    _veroeffentlichen(gepostet, f"wal|{W:%Y-%m-%d}", grafik.wal_story(w), f"Wal des Tages: ${w['usd']:,} auf {w['seite']} ({w['spiel']}) · Wallet {w['wallet']}", story=True)
    if gepostet.get(f"wal|{W:%Y-%m-%d}"):
        gepostet[f"wal|{W:%Y-%m-%d}"]["wal"] = w
        bilder_sichern(gepostet)
    pr = wal.profi(daten(), JETZT, w)
    if pr and not gepostet.get(f"profi|{W:%Y-%m-%d}"):
        pr["profi"] = True
        print("Profi:", json.dumps(pr, ensure_ascii=False))
        _veroeffentlichen(gepostet, f"profi|{W:%Y-%m-%d}", grafik.wal_story(pr), f"Profi des Abends: ${pr['usd']:,} auf {pr['seite']}", story=True)
        if gepostet.get(f"profi|{W:%Y-%m-%d}"):
            gepostet[f"profi|{W:%Y-%m-%d}"]["wal"] = pr
            bilder_sichern(gepostet)


def wal_aufloesung(gepostet):
    import wal
    gestern = (W - timedelta(days=1)).strftime("%Y-%m-%d")
    for art in ("wal", "profi"):
        alt = (gepostet.get(f"{art}|{gestern}") or {}).get("wal")
        if not alt or gepostet.get(f"{art}Aufl|{gestern}"):
            continue
        er = wal.ergebnis(daten(), alt)
        if not er:
            print(f"{art} von gestern: noch kein Endstand")
            continue
        ergebnis, endstand = er
        _veroeffentlichen(gepostet, f"{art}Aufl|{gestern}", grafik.wal_story({**alt, "endstand": endstand}, ergebnis),
                          f"Auflösung {art}: {ergebnis} ({endstand})", story=True)


def main(modus):
    gepostet = bilder_zweig()
    if modus == "wal":
        if gepostet.get(f"wal|{W:%Y-%m-%d}") and not os.environ.get("ERZWINGEN"):
            print("Wal heute schon erledigt")
            return 0
        wal_abend(gepostet)
        return 0
    schluessel = f"{modus}|{W:%Y-%m-%d}"
    if not (gepostet.get(schluessel) and not os.environ.get("ERZWINGEN")):
        r = morgen() if modus == "morgen" else abend()
        if r:
            _veroeffentlichen(gepostet, schluessel, *r)
        else:
            print("nichts zu posten")
    else:
        print("heute schon gepostet:", gepostet[schluessel])
    if modus == "morgen":
        try:
            wal_aufloesung(gepostet)
        except Exception as ex:  # noqa: BLE001 — die Story darf den Morgenpost nicht reißen
            print("Wal-Auflösung fehlgeschlagen:", ex)
            raise
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "morgen"))
