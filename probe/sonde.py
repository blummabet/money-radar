#!/usr/bin/env python3
"""Einmalige Sonde (17.10.2026): antworten Betwatch und Stake von einem GitHub-Server aus?

Gibt nur Statuscodes, Laufzeiten, Mengen und Limit-Header aus — nie den Schlüssel, nie Rohdaten.
"""
import json, os, time, urllib.request, urllib.error

KEY = os.environ.get("BETWATCH_KEY", "").strip()
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"


def limit_kopf(h):
    return {k: v for k, v in h.items() if any(s in k.lower() for s in ("limit", "retry", "quota", "remaining", "throttle"))}


MODUS = {"m": None}


def _req(pfad, m):
    url, h = f"https://betwatch.fr/api/v1{pfad}", {"User-Agent": UA, "Accept": "application/json"}
    if m == "query":
        url += f"{'&' if '?' in pfad else '?'}key={KEY}"
    elif m == "token":
        h["Authorization"] = f"Token {KEY}"
    elif m == "apikey":
        h["X-API-Key"] = KEY
    elif m == "bearer":
        h["Authorization"] = f"Bearer {KEY}"
    return urllib.request.Request(url, headers=h)


def betwatch(pfad):
    if not MODUS["m"]:
        for m in ("query", "token", "apikey", "bearer"):
            try:
                with urllib.request.urlopen(_req(pfad, m), timeout=30) as r:
                    MODUS["m"] = m
                    print("Betwatch akzeptiert Anmeldung:", m)
                    break
            except urllib.error.HTTPError as e:
                print(f"  Anmeldung {m}: HTTP {e.code}")
        if not MODUS["m"]:
            return None
    t = time.time()
    try:
        with urllib.request.urlopen(_req(pfad, MODUS["m"]), timeout=30) as r:
            d = json.loads(r.read().decode())
            n = len(d) if isinstance(d, list) else len(d.get("results") or d.get("markets") or []) if isinstance(d, dict) else "?"
            print(f"Betwatch {pfad.split('?')[0]:28s} HTTP {r.status}  {time.time()-t:5.1f}s  n={n}  Limits={limit_kopf(r.headers)}")
            return d
    except urllib.error.HTTPError as e:
        print(f"Betwatch {pfad.split('?')[0]:28s} HTTP {e.code}  {time.time()-t:5.1f}s  Limits={limit_kopf(e.headers)}  {e.read()[:120]!r}")
    except Exception as e:
        print(f"Betwatch {pfad.split('?')[0]:28s} FEHLER {str(e)[:120]}")


def stake():
    q = json.loads(urllib.request.urlopen("https://raw.githubusercontent.com/blummabet/betting-dashboard/main/stake_query.json", timeout=30).read())
    body = json.dumps({"query": q["query"], "variables": {"limit": 50}}).encode()
    for url in ("https://stake.com/_api/graphql", "https://stake.bet/_api/graphql"):
        t = time.time()
        req = urllib.request.Request(url, body, headers={"content-type": "application/json", "accept": "*/*", "user-agent": UA,
                                                       "origin": "https://stake.com", "referer": "https://stake.com/sports/highrollers"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.loads(r.read().decode())
                rows = ((d.get("data") or {}).get(q["feld"]) or [])
                fehler = [x.get("message") for x in d.get("errors") or []][:2]
                print(f"Stake {url:34s} HTTP {r.status}  {time.time()-t:4.1f}s  Wetten={len(rows)}  Fehler={fehler}")
        except urllib.error.HTTPError as e:
            print(f"Stake {url:34s} HTTP {e.code}  {time.time()-t:4.1f}s  server={e.headers.get('server')}  {e.read()[:160]!r}")
        except Exception as e:
            print(f"Stake {url:34s} FEHLER {str(e)[:160]}")


if __name__ == "__main__":
    print("Schlüssel vorhanden:", bool(KEY), "Länge:", len(KEY))
    ev = betwatch("/football/events")
    betwatch("/football/live")
    if isinstance(ev, list) and ev:
        mid = ev[0].get("match_id")
        t = time.time()
        for i, e in enumerate(ev[:10]):
            betwatch(f"/football/event/{e.get('match_id')}")
        print(f"10 Detail-Abrufe in {time.time()-t:.1f}s")
