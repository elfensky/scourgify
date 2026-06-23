#!/usr/bin/env python3
# Apply: genres split, non-English tags, fandom consolidation, symbol fixes.
# Run: calibre-debug -e apply_more.py [-- --apply]   (Calibre must be CLOSED)
import sys, csv, os, collections
APPLY = "--apply" in sys.argv
LIB = "/Users/andrei/Library/Mobile Documents/com~apple~CloudDocs/Calibre/fanfiction"
OUT = os.path.dirname(os.path.abspath(__file__))
from calibre.library import db as DB
lib = DB(LIB).new_api
def mp(fn): return list(csv.DictReader(open(f"{OUT}/{fn}")))
def pf(s): return s.replace("Pokémon", "Pokemon")     # force-ASCII Pokemon

# genres split/normalize
SPLIT = {"Adventure/Action": ["Adventure","Action"], "Action/Adventure": ["Action","Adventure"],
    "Action-Adventure": ["Action","Adventure"], "Drama & Romance": ["Drama","Romance"],
    "Action & Romance": ["Action","Romance"], "Adventure & Romance": ["Adventure","Romance"]}
VAR = {"Comedy?":"Comedy","Humor?":"Humor","isekai?":"Isekai","Crossover?":"Crossover",
    "Time-Travel":"Time Travel","#Action":"Action","Sports!":"Sports","Science Fiction":"Sci-Fi"}

ne_map = {r["tag"]: r for r in mp("nonenglish_tags_map.csv")}
fan_map = {r["fandom"]: {"action": r["action"], "target": pf(r.get("target","")), "also": r.get("also_tag","")} for r in mp("fandoms_consolidate_map.csv")}
fan_map["Pokémon"] = {"action": "merge", "target": "Pokemon", "also": ""}
sym_map = {(r["column"], r["value"]): r for r in mp("symbols_map.csv")}

def fv(field, b):
    x = lib.field_for(field, b)
    return list(x) if isinstance(x, (tuple, list, set, frozenset)) else ([x] if x else [])

ids = lib.all_book_ids()
ct, cg, cf, cc = {}, {}, {}, {}
n = collections.Counter(); lost = 0
for b in ids:
    T0, F0, C0, G0 = fv('tags', b), fv('#fandoms', b), fv('#characters', b), fv('#genres', b)
    newT, newF, newC, newG = set(), set(), set(), set()
    # genres split
    for g in G0:
        if g in SPLIT: newG.update(SPLIT[g]); n["genre_split"] += 1
        else: newG.add(VAR.get(g, g))
    # fandoms
    real_before = any(fan_map.get(f, {}).get("action") not in ("to_real", "drop") for f in F0) or any(fan_map.get(f, {}).get("target") for f in F0)
    for f in F0:
        r = fan_map.get(f, {"action": "keep", "target": "", "also": ""})
        a, t, al = r["action"], r["target"], r.get("also", "")
        if a == "merge": newF.add(t or f); n["fan_merge"] += 1
        elif a == "to_real":
            if t: newF.add(t)
            if al == "Self-Insert": newT.add("Self-Insert")
            elif al == "Crossover": newG.add("Crossover")
            elif al: newT.add(al)
            n["fan_to_real"] += 1
        elif a == "drop": n["fan_drop"] += 1
        else: newF.add(pf(f))
    # characters: symbol fix
    for ch in C0:
        sm = sym_map.get(("characters", ch))
        if sm and sm["action"] == "drop": n["sym_drop"] += 1; continue
        if sm and sm["action"] == "strip": newC.add(sm["target"] or ch); n["sym_strip"] += 1; continue
        newC.add(ch)
    # tags: non-english then symbol
    for tg in T0:
        r = ne_map.get(tg)
        if r:
            a, t = r["action"], pf(r["target"])
            if a == "fold": newT.add(t or tg)
            elif a == "to_characters": newC.add(t)
            elif a == "to_fandoms": newF.add(t)
            elif a == "drop": pass
            else: newT.add(tg)
            n["ne_" + a] += 1; continue
        sm = sym_map.get(("tags", tg))
        if sm and sm["action"] == "drop": n["sym_drop"] += 1; continue
        if sm and sm["action"] == "strip": newT.add(sm["target"] or tg); n["sym_strip"] += 1; continue
        newT.add(tg)
    if real_before and not newF: lost += 1
    if tuple(sorted(newT)) != tuple(sorted(T0)): ct[b] = tuple(sorted(newT))
    if tuple(sorted(newG)) != tuple(sorted(G0)): cg[b] = tuple(sorted(newG))
    if tuple(sorted(newF)) != tuple(sorted(F0)): cf[b] = tuple(sorted(newF))
    if tuple(sorted(newC)) != tuple(sorted(C0)): cc[b] = tuple(sorted(newC))

print("APPLY" if APPLY else "PRE-APPLY (no write)")
print(f"  actions: {dict(n)}")
print(f"  books changed: tags {len(ct)} | genres {len(cg)} | fandoms {len(cf)} | chars {len(cc)}")
print(f"  SAFETY books losing last real fandom: {lost}")
assert lost == 0, "ABORT: fandom data loss"
if APPLY:
    lib.set_field('#genres', cg); lib.set_field('#fandoms', cf); lib.set_field('#characters', cc); lib.set_field('tags', ct)
    print("\nWROTE all four.")
else:
    print("\nRe-run with -- --apply to write (Calibre must be closed).")
