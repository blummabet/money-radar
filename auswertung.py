#!/usr/bin/env python3
"""auswertung.py — Lucas 22.10.: „was wäre sinnvoller nachzuspielen?" Wertet das eigene Push-Buch
(.push_state/buch.json, Actions-Cache) nach Art und Untergruppen aus und schreibt Markdown nach stdout.
Läuft nur per Hand (auswertung.yml), schreibt nichts zurück."""
import json, math, sys
from collections import defaultdict
from pathlib import Path

buch = json.loads((Path(__file__).resolve().parent / ".push_state" / "buch.json").read_text(encoding="utf-8"))
print(f"# Push-Buch: {len(buch)} Einträge\n")
print("Beispiel-Felder:", sorted({k for b in buch[-50:] for k in b}), "\n")
fertig = [b for b in buch if b.get("status") == "abgerechnet" and isinstance(b.get("r"), (int, float))]
for b in buch:      # CLV aus der Schlussquote (nur vor Anpfiff gemessen)
    if b.get("quote") and isinstance(b.get("schluss"), (int, float)) and b["schluss"] > 1:
        b["clv"] = b["quote"] / b["schluss"] - 1
print(f"abgerechnet mit Ergebnis: {len(fertig)}\n")

def stat(z):
    n = len(z)
    if not n: return None
    r = [b["r"] for b in z]; roi = sum(r) / n
    sd = math.sqrt(sum((x - roi) ** 2 for x in r) / max(1, n - 1)); se = sd / math.sqrt(n)
    tr = sum(1 for b in z if b.get("win")) / n
    erw = sum(1 / b["quote"] for b in z if b.get("quote")) / max(1, sum(1 for b in z if b.get("quote")))
    c = [b["clv"] for b in z if isinstance(b.get("clv"), (int, float))]
    qm = sorted(b["quote"] for b in z if b.get("quote"))
    return n, tr, erw, roi, se, (sum(c) / len(c) if c else None), len(c), (qm[len(qm)//2] if qm else None)

def zeile(name, z):
    s = stat(z)
    if not s: return
    n, tr, erw, roi, se, clv, nc, qm = s
    flag = " ⭐" if roi - 1.0 * se > 0 and n >= 25 else ""
    print(f"| {name} | {n} | {tr*100:.0f} % | {erw*100:.0f} % | {roi*100:+.1f} % ± {se*100:.1f}{flag} | {'' if clv is None else f'{clv*100:+.1f} % ({nc})'} | {qm or '–'} |")

def kopf(t):
    print(f"\n## {t}\n\n| Gruppe | n | Treffer | Quote sagte | Rendite ± Fehler | CLV | Median-Quote |\n|---|---|---|---|---|---|---|")

def band(q):
    if not q: return "?"
    return "<1,40" if q < 1.4 else "1,40–1,69" if q < 1.7 else "1,70–1,99" if q < 2 else "2,00–2,99" if q < 3 else "≥3,00"

def marktgr(m):
    m = str(m or "")
    return "Sieg (1X2)" if m == "Match Odds" else "Über/Unter" if "Over/Under" in m else "1. HZ" if ("Half" in m or "First Half" in m) else "Poly-Markt" if not m else "anderer"

kopf("Je Art")
arten = defaultdict(list)
for b in fertig: arten[b.get("art")].append(b)
for a, z in sorted(arten.items(), key=lambda kv: -len(kv[1])): zeile(a, z)

for a, z in sorted(arten.items(), key=lambda kv: -len(kv[1])):
    if len(z) < 20: continue
    for titel, fn in (("Quote", lambda b: band(b.get("quote"))), ("Markt", lambda b: marktgr(b.get("markt"))),
                      ("Top-5", lambda b: "Top-5" if b.get("top5") else "Rest"),
                      ("Auswahl", lambda b: "Über" if str(b.get("auswahl","")).lower().startswith("over") else "Unter" if str(b.get("auswahl","")).lower().startswith("under") else "Remis" if "draw" in str(b.get("auswahl","")).lower() else "Team/sonst"),
                      ("Anteil am Geld", lambda b: "?" if b.get("anteil") is None else "<70 %" if b["anteil"] < 70 else "70–89 %" if b["anteil"] < 90 else "≥90 %"),
                      ("Betrag", lambda b: "?" if not b.get("eur") and not b.get("usd") else ("<10k" if (b.get("eur") or b.get("usd") or 0) < 1e4 else "10–30k" if (b.get("eur") or b.get("usd")) < 3e4 else "≥30k")),
                      ("Serie n", lambda b: "?" if b.get("n") is None else str(min(b["n"], 6)) + ("+" if b["n"] >= 6 else "")),
                      ("Sport", lambda b: b.get("sport") or "Fußball"),
                      ("Phase", lambda b: "live" if b.get("live") else "vor Anpfiff")):
        g = defaultdict(list)
        for b in z: g[fn(b)].append(b)
        if len(g) < 2: continue
        kopf(f"{a} nach {titel}")
        for k, v in sorted(g.items(), key=lambda kv: str(kv[0])): zeile(k, v)

# Kombinationen, die als Regel taugen könnten
kopf("Kandidaten-Regeln (alle Arten)")
regeln = {
  "Dauerzufluss, Quote 1,40–1,99": lambda b: b.get("art")=="serie" and b.get("quote") and 1.4<=b["quote"]<2,
  "Dauerzufluss, Sieg-Markt": lambda b: b.get("art")=="serie" and b.get("markt")=="Match Odds",
  "Dauerzufluss, n≥5": lambda b: b.get("art")=="serie" and (b.get("n") or 0)>=5,
  "Live-Zufluss, Quote 1,40–1,99": lambda b: b.get("art")=="live" and b.get("quote") and 1.4<=b["quote"]<2,
  "Zufluss vor Anpfiff, Sieg-Markt": lambda b: b.get("art")=="zufluss" and b.get("markt")=="Match Odds",
  "Fix-Verdacht": lambda b: b.get("art")=="fix",
  "Poly-Dauerzufluss": lambda b: b.get("art")=="pserie",
  "alle Betfair vor Anpfiff, Sieg-Markt, Quote <2": lambda b: b.get("art") in ("serie","zufluss") and not b.get("live") and b.get("markt")=="Match Odds" and b.get("quote") and b["quote"]<2,
  "alles mit Quote ≥2,00": lambda b: (b.get("quote") or 0)>=2,
  "Dauerzufluss vor Anpfiff, Quote 1,40–1,99": lambda b: b.get("art")=="serie" and not b.get("live") and b.get("quote") and 1.4<=b["quote"]<2,
  "Dauerzufluss live, Quote 1,40–1,99": lambda b: b.get("art")=="serie" and b.get("live") and b.get("quote") and 1.4<=b["quote"]<2,
  "Dauerzufluss ≥€30k, Quote 1,40–1,99": lambda b: b.get("art")=="serie" and (b.get("eur") or 0)>=3e4 and b.get("quote") and 1.4<=b["quote"]<2,
  "Serie+Zufluss+Live, ≥€30k, Quote 1,40–2,99": lambda b: b.get("art") in ("serie","zufluss","live") and (b.get("eur") or 0)>=3e4 and b.get("quote") and 1.4<=b["quote"]<3,
  "Live-Zufluss, Quote 1,70–2,99": lambda b: b.get("art")=="live" and b.get("quote") and 1.7<=b["quote"]<3,
}
for k, f in regeln.items(): zeile(k, [b for b in fertig if f(b)])

# Zeitlicher Verlauf je Art (Woche)
print("\n## Verlauf je Tag (Rendite in Einheiten, kumuliert)\n")
for a, z in arten.items():
    if len(z) < 20: continue
    tag = defaultdict(float)
    for b in z: tag[str(b.get("ts",""))[:10]] += b["r"]
    k = 0; s = []
    for t in sorted(tag): k += tag[t]; s.append(f"{t[5:]}:{k:+.1f}")
    print(f"- **{a}**: " + "  ".join(s))

# Gibt es das Spiel auf Polymarket? (nur Pushes der letzten 30 h, so weit reicht der Markt-Scan)
print("\n## Polymarket-Abdeckung der Betfair-Pushes (letzte 30 h)\n")
try:
    from datetime import datetime, timedelta, timezone
    import unicodedata, re as _re
    pm = json.loads((Path(__file__).resolve().parent / ".pm" / "poly_maerkte.json").read_text(encoding="utf-8")).get("maerkte") or {}
    STOP = {"al","el","fc","sc","cf","ac","as","ud","cd","sk","fk","club","de","la","le","the","real","ca","ss","us","sv","vfb","vfl","tsg","u19","u21","u23","w","team"}
    def tok(n):
        n = unicodedata.normalize("NFD", str(n or "")).encode("ascii","ignore").decode().lower()
        return [w for w in _re.split(r"[^a-z0-9]+", _re.sub(r"\(.*?\)", " ", n)) if len(w) >= 3 and w not in STOP]
    idx = []
    for k, m in pm.items():
        if not _re.search(r"\d{4}-\d\d-\d\d$", k): continue
        idx.append((k, set(tok(m.get("titel") or "")) | {w for o in (m.get("prices") or {}) for w in tok(o)}, m))
    jetzt = datetime.now(timezone.utc); n = treffer = 0; je = defaultdict(lambda: [0, 0])
    for b in buch:
        if b.get("art") in ("pserie", "wal") or not b.get("heim"): continue
        t = datetime.fromisoformat(str(b.get("ts")).replace("Z","+00:00"))
        if jetzt - t > timedelta(hours=30): continue
        h, g = tok(b.get("heim"))[:1], tok(b.get("gast"))[:1]
        hit = next((k for k, w, m in idx if h and g and h[0] in w and g[0] in w), None)
        n += 1; treffer += bool(hit); je[b.get("art")][0] += 1; je[b.get("art")][1] += bool(hit)
    print(f"{treffer} von {n} Pushes haben einen Polymarket-Markt ({100*treffer/max(1,n):.0f} %)")
    for a, (x, y) in je.items(): print(f"- {a}: {y}/{x}")
except Exception as exc:
    print("Abdeckung nicht messbar:", exc)
