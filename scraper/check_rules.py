#!/usr/bin/env python3
"""Vérifie la cohérence de data/rules.json : combien d'équipes arrivent dans chaque division
à la phase suivante (montées + descentes + maintiens) comparé à la composition annoncée.
Ne couvre que les règles à portée « division » avec une composition chiffrée ; les règles à
portée « poule » (ex. U10-U11) ou en pourcentage (from_pct/to_pct) ne sont pas vérifiées ici.

    python scraper/check_rules.py
"""
import json
from collections import defaultdict
from pathlib import Path

rules = json.loads((Path(__file__).resolve().parent.parent / "data" / "rules.json").read_text(encoding="utf-8"))
cats = [k for k, v in rules.items() if isinstance(v, dict) and not k.startswith("_") and k not in ("points",) and any(p.isdigit() for p in v)]
arrivals, expected = defaultdict(int), {}
total_src = defaultdict(int)

jobs = sorted(((int(ph), cat) for cat in cats for ph in rules[cat] if ph.isdigit()))
for ph, cat in jobs:
    r = rules[cat][str(ph)]
    comp = r.get("composition", {})
    n = comp.get("teams_63") or comp.get("teams")
    expected[(cat, ph)] = n
    if n is None:
        n = arrivals.get((cat, ph))  # composition inconnue : on prend ce qui arrive
        if n is None:
            continue
    moved = 0
    for t in r.get("tiers", []):
        k = t["last"] if "last" in t else t["to"] - t["from"] + 1
        if t.get("dest"):
            arrivals[(t["dest"], ph + 1)] += k
        moved += k
    for f in r.get("flows", []):
        arrivals[(f["dest"], ph + 1)] += f["n"]
        moved += f["n"]
    if any(t["kind"] in ("t1", "t2", "t3", "t4") for t in r.get("tiers", [])):
        continue  # brassage : chacun va quelque part, pas de « maintien »
    if r.get("tiers") or r.get("flows"):
        arrivals[(cat, ph + 1)] += n - moved  # les autres se maintiennent

print(f"{'division':<16}{'phase':<7}{'attendues':>10}{'arrivées':>10}  écart")
bad = 0
for (cat, ph), n in sorted(expected.items(), key=lambda x: (x[0][1], x[0][0])):
    if ph == 1:
        continue
    got = arrivals.get((cat, ph))
    if got is None:
        continue
    diff = "" if n is None else ("ok" if got == n else f"{got - n:+d}")
    if diff not in ("", "ok"):
        bad += 1
    print(f"{cat:<16}P{ph:<6}{'?' if n is None else n:>10}{got:>10}  {diff}")
print("\nIncohérences :", bad or "aucune")
