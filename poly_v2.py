"""poly_v2.py — Polymarket Data API v2 (03.11.2026).

Polymarket hat die Data API v1 (/trades, /holders, /positions …) am 02.11.2026 abgeschaltet: jede Anfrage → 410 Gone.
Seitdem kamen keine Käufe, Halter und Positionen mehr (keine Seiten-Aufteilung, kein Poly-Dauerzufluss, keine Wal-
Einstiege). v2 laut https://docs.polymarket.com/migrate/data-api-v1-to-v2 :
  · Pfade unter /v2/…, `market=` → `condition=`, `offset` → `cursor` (offset/market = HTTP 400)
  · Antwort {"data": …, "pagination": {"next_cursor", "has_more"}} statt nackter Liste
  · Felder snake_case (proxy_wallet, condition_id, token_id …)
Dieses Modul holt v2-Seiten und übersetzt die Zeilen in die v1-Namen (camelCase, token_id → asset), damit der
übrige Code unverändert bleibt. REIN bis auf `seiten()` (Netz über den übergebenen Getter)."""
from __future__ import annotations

import re
import urllib.parse

BASIS = "https://data-api.polymarket.com/v2/"
# v2-Namen, die nicht einfach snake → camel sind (v1-Name dahinter)
ALIAS = {"token_id": "asset", "current_size": "size", "total_cost_usdc": "initialValue", "entry_cost_usdc": "initialValue",
         "unrealized_pnl": "cashPnl", "current_price": "curPrice", "total_size": "totalBought", "last_event_at": "timestamp",
         "token": "token"}


def _camel(k: str) -> str:
    return re.sub(r"_([a-z0-9])", lambda m: m.group(1).upper(), k)


def v1(x):
    """v2-Zeile(n) → v1-Namen. Beide Schreibweisen bleiben erhalten (alte Felder zuerst lesen geht weiter)."""
    if isinstance(x, list):
        return [v1(y) for y in x]
    if not isinstance(x, dict):
        return x
    out = {}
    for k, v in x.items():
        v = v1(v)
        out[k] = v
        if "_" in k:
            out.setdefault(_camel(k), v)
        if k in ALIAS:
            out.setdefault(ALIAS[k], v)
    return out


def url(pfad: str, **params) -> str:
    p = {k: v for k, v in params.items() if v is not None}
    return BASIS + pfad + ("?" + urllib.parse.urlencode(p) if p else "")


def nutzlast(antwort):
    """(zeilen, next_cursor) aus einer v2-Antwort; nackte Liste (v1-artig) geht auch."""
    if isinstance(antwort, list):
        return v1(antwort), None
    if not isinstance(antwort, dict):
        return None, None
    d = antwort.get("data", antwort)
    pg = antwort.get("pagination") or {}
    cur = pg.get("next_cursor") or pg.get("nextCursor")
    if pg.get("has_more") is False:
        cur = None
    return v1(d), cur


def seiten(getter, pfad: str, max_seiten: int = 1, **params):
    """Alle Zeilen über bis zu max_seiten Cursor-Seiten. Gleiche Filter auf jeder Seite (verlangt v2). None = Fehler."""
    alle, cur = [], None
    for i in range(max_seiten):
        q = dict(params)
        if cur:
            q["cursor"] = cur
            q.pop("limit", None)          # limit gilt nur auf der ersten Seite
        roh = getter(url(pfad, **q))
        if roh is None:
            return alle if i else None
        z, cur = nutzlast(roh)
        if isinstance(z, list):
            alle.extend(z)
        elif z is not None:
            alle.append(z)
        if not cur:
            break
    return alle
