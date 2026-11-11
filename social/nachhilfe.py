#!/usr/bin/env python3
"""nachhilfe.py — stößt den Instagram-Post an, wenn GitHub den Zeitplan verschluckt hat (Lucas 05.11. 11:19:
„Wieso ist heute noch kein Instagram-Post rausgegangen?" — der Morgen-Termin 9:23 feuerte nie, der Abend-Termin
vom 04.11. kam 5½ h zu spät). Läuft am Ende jedes Seitenbaus; posten.py selbst verhindert Doppelposts."""
import base64, json, os, subprocess
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

REPO = os.environ.get("GITHUB_REPOSITORY", "blummabet/money-radar")
FENSTER = {"morgen": ((9, 40), (13, 0)), "abend": ((17, 25), (18, 15)), "wal": ((18, 20), (20, 30)), "abend2": ((20, 30), (22, 0))}
jetzt = datetime.now(timezone.utc)
w = jetzt.astimezone(ZoneInfo("Europe/Vienna"))


def gh(*a):
    r = subprocess.run(["gh", "api", *a], capture_output=True, text=True)
    return r.returncode, r.stdout


rc, out = gh(f"repos/{REPO}/contents/gepostet.json?ref=bilder")
gepostet = json.loads(base64.b64decode(json.loads(out)["content"])) if rc == 0 else {}


def lauf_unterwegs(min_abstand=20):
    rc2, o = gh(f"repos/{REPO}/actions/workflows/instagram.yml/runs?per_page=3")
    for r in (json.loads(o).get("workflow_runs") or []) if rc2 == 0 else []:
        t = datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
        if r["status"] != "completed" or jetzt - t < timedelta(minutes=min_abstand):
            return r["id"]
    return None


# 11.11. (Lucas: „häng das dran, die Grafiken auf X sind mir wichtig"): X-Grafiken alle 2 h zwischen 12 und 1 Uhr —
# liegt der letzte Lauf mehr als 2½ h zurück, selbst anstoßen
if (w.hour >= 12 or w.hour < 1):
    xl = gepostet.get("_xLauf")
    alt_x = jetzt - datetime.fromisoformat(xl) if xl else timedelta(days=1)
    if alt_x > timedelta(minutes=150) and not lauf_unterwegs(3):
        rc, _ = gh("-X", "POST", f"repos/{REPO}/actions/workflows/instagram.yml/dispatches", "-f", "ref=main",
                   "-f", "inputs[modus]=x_bilder", "-f", "inputs[trocken]=false")
        print("X-Grafiken nachgeholt angestoßen" if rc == 0 else "X-Anstoß fehlgeschlagen")
        raise SystemExit(0)

modus = next((m for m, (von, bis) in FENSTER.items() if von <= (w.hour, w.minute) < bis), None)
if not modus:
    raise SystemExit(0)
if modus == "abend2":
    modus = "abend"
offen = not gepostet.get(f"{modus}|{w:%Y-%m-%d}")
g = gepostet.get(f"wal|{w:%Y-%m-%d}") or {}
if modus == "wal" and g.get("keiner") and int(g.get("versuche") or 1) < 2 and (w.hour, w.minute) >= (19, 0):
    offen = True                                # 07.11.: zweiter Wal-Versuch ab 19:00
if modus == "wal" and not offen and not gepostet.get(f"abend|{w:%Y-%m-%d}"):
    modus, offen = "abend", True
if modus == "morgen" and not offen:            # Morgenpost da — fehlt noch die Wal-Auflösung von gestern?
    gestern = (w - timedelta(days=1)).strftime("%Y-%m-%d")
    offen = any((gepostet.get(f"{a}|{gestern}") or {}).get("wal") and not gepostet.get(f"{a}Aufl|{gestern}") for a in ("wal", "profi"))
if not offen:
    raise SystemExit(0)
rc, out = gh(f"repos/{REPO}/actions/workflows/instagram.yml/runs?per_page=3")
for r in (json.loads(out).get("workflow_runs") or []) if rc == 0 else []:
    t = datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
    if r["status"] != "completed" or jetzt - t < timedelta(minutes=20):
        print("Instagram-Lauf schon unterwegs/eben gelaufen:", r["id"], r["status"])
        raise SystemExit(0)
rc, _ = gh("-X", "POST", f"repos/{REPO}/actions/workflows/instagram.yml/dispatches", "-f", "ref=main",
           "-f", f"inputs[modus]={modus}", "-f", "inputs[trocken]=false")
print(f"Instagram {modus} nachgeholt angestoßen" if rc == 0 else "Anstoß fehlgeschlagen")
