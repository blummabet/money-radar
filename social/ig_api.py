#!/usr/bin/env python3
"""ig_api.py — Instagram veröffentlichen über die Meta Graph API (Lucas 04.11.2026).

Token: System-User-Token aus der Meta-App „Cocobet – Test1“ (Unified Login, läuft nie ab) als Secret IG_TOKEN,
Konto @cocobet89 als IG_USER_ID. Der Token wird nie ausgegeben.

  python3 social/ig_api.py pruefen          → Konto lesen (nichts wird gepostet)
  python3 social/ig_api.py posten URL TEXT  → Bild (öffentliche JPEG-URL) mit Text posten
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HOSTS = ("https://graph.facebook.com/v21.0", "https://graph.instagram.com/v21.0")


def _req(methode, host, pfad, params):
    params = dict(params, access_token=os.environ["IG_TOKEN"])
    daten = urllib.parse.urlencode(params).encode()
    url = f"{host}/{pfad}" + (f"?{daten.decode()}" if methode == "GET" else "")
    req = urllib.request.Request(url, data=None if methode == "GET" else daten, method=methode)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:  # noqa: BLE001
            return e.code, {"error": {"message": "keine JSON-Antwort"}}


def host_finden(uid):
    for h in HOSTS:
        st, d = _req("GET", h, uid, {"fields": "id,username,followers_count,media_count"})
        print(f"{h.split('/')[2]}: HTTP {st} → {json.dumps(d)[:300]}")
        if st == 200 and d.get("username"):
            return h, d
    return None, None


def posten(host, uid, bild_url, text, story=False):
    params = {"image_url": bild_url, "media_type": "STORIES"} if story else {"image_url": bild_url, "caption": text}
    st, d = _req("POST", host, f"{uid}/media", params)
    if st != 200 or not d.get("id"):
        raise SystemExit(f"Container fehlgeschlagen: HTTP {st} {json.dumps(d)[:300]}")
    cid = d["id"]
    for _ in range(20):                       # Bild wird von Meta geholt und geprüft
        st, s = _req("GET", host, cid, {"fields": "status_code"})
        if s.get("status_code") == "FINISHED":
            break
        if s.get("status_code") in ("ERROR", "EXPIRED"):
            raise SystemExit(f"Container {s}")
        time.sleep(3)
    st, d = _req("POST", host, f"{uid}/media_publish", {"creation_id": cid})
    if st != 200:
        raise SystemExit(f"Veröffentlichen fehlgeschlagen: HTTP {st} {json.dumps(d)[:300]}")
    print("gepostet:", d.get("id"))
    return d.get("id")


if __name__ == "__main__":
    uid = os.environ.get("IG_USER_ID", "").strip()
    if not os.environ.get("IG_TOKEN") or not uid:
        raise SystemExit("IG_TOKEN / IG_USER_ID fehlen")
    host, konto = host_finden(uid)
    if not host:
        raise SystemExit("Konto nicht lesbar — Token oder ID passen nicht")
    print(f"OK: @{konto['username']} · {konto.get('followers_count')} Follower · {konto.get('media_count')} Beiträge · über {host.split('/')[2]}")
    if len(sys.argv) > 3 and sys.argv[1] == "posten":
        posten(host, uid, sys.argv[2], sys.argv[3])
