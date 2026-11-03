#!/usr/bin/env python3
"""Einmal-Probe (03.11.2026): Polymarket hat die Data API v1 am 02.11. abgeschaltet (410 Gone).
Fragt die v2-Routen für einen aktiven Markt ab und druckt die Rohantwort (gekürzt) — für den Umbau."""
import json, ssl, urllib.request, urllib.parse
UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
def get(u):
    try:
        with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=20, context=ssl.create_default_context()) as r:
            return r.status, r.read().decode()[:2500]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:600]
    except Exception as e:
        return None, str(e)[:300]
st, body = get("https://gamma-api.polymarket.com/markets?active=true&closed=false&order=volume24hr&ascending=false&limit=1")
m = json.loads(body)[0]
cond = m["conditionId"]
print("Markt:", m.get("question"), cond)
B = "https://data-api.polymarket.com/v2/"
for pfad in (f"trades?condition={cond}&limit=3", f"trades?condition={cond}&limit=3&takerOnly=true", f"trades?condition={cond}&limit=3&taker_only=true",
             f"trades?condition={cond}&limit=3&side=BUY", f"holders?condition={cond}&limit=3", "status"):
    st, body = get(B + pfad)
    print(f"\n=== {pfad} → {st}\n{body}")
