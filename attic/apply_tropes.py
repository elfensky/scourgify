#!/usr/bin/env python3
import os
# Apply freeform->trope map. Run: calibre-debug -e apply_tropes.py [-- --apply]
import sys, csv, os, collections
APPLY = "--apply" in sys.argv
LIB = os.path.expanduser(os.environ.get("CALIBRE_LIBRARY", ""))
if not LIB:
    raise SystemExit("Set CALIBRE_LIBRARY to your Calibre library folder (the one containing metadata.db).")
OUT = os.path.dirname(os.path.abspath(__file__))
from calibre.library import db as DB
lib = DB(LIB).new_api

JP_FIX = {"Uchiha Obito": "Obito Uchiha", "Inoue Orihime": "Orihime Inoue",
          "Shihouin Yoruichi": "Yoruichi Shihouin", "Aono Tsukune": "Tsukune Aono"}
mapd = {}
for r in csv.DictReader(open(f"{OUT}/freeform_trope_map.csv")):
    mapd[r["tag"]] = {"action": r["action"], "target": JP_FIX.get(r["target"], r["target"])}
# user overrides
mapd["Multi"] = {"action": "keep", "target": ""}
if "Finished" in mapd: mapd["Finished"] = {"action": "to_status", "target": "Completed"}

def fv(field, b):
    x = lib.field_for(field, b)
    return list(x) if isinstance(x, (tuple, list, set, frozenset)) else ([x] if x else [])

ids = lib.all_book_ids()
ct, cg, cc, cf, cs = {}, {}, {}, {}, {}
n = collections.Counter()
for b in ids:
    T = fv("tags", b)
    if not any(t in mapd for t in T): continue
    G = set(fv("#genres", b)); C = set(fv("#characters", b)); F = set(fv("#fandoms", b))
    st = lib.field_for("#status", b); new_status = st
    newT = set(); g0, c0, f0 = set(G), set(C), set(F)
    for t in T:
        r = mapd.get(t)
        if not r: newT.add(t); continue
        a, tg = r["action"], r["target"]
        if a == "keep": newT.add(t)
        elif a == "fold": newT.add(tg or t); n["fold"] += 1
        elif a == "drop": n["drop"] += 1
        elif a == "to_genres": G.add(tg); n["to_genres"] += 1
        elif a == "to_characters": C.add(tg); n["to_characters"] += 1
        elif a == "to_fandoms": F.add(tg); n["to_fandoms"] += 1
        elif a == "to_status":
            if tg and not new_status: new_status = tg
            n["to_status"] += 1
        else: newT.add(t)
    ct[b] = tuple(sorted(newT))
    if G != g0: cg[b] = tuple(sorted(G))
    if C != c0: cc[b] = tuple(sorted(C))
    if F != f0: cf[b] = tuple(sorted(F))
    if new_status != st: cs[b] = new_status

before = len(lib.all_field_names('tags'))
print("APPLY" if APPLY else "PRE-APPLY (no write)")
print(f"  tag actions: {dict(n)}")
print(f"  books: tags {len(ct)} | +genres {len(cg)} | +chars {len(cc)} | +fandoms {len(cf)} | status {len(cs)}")
if APPLY:
    lib.set_field('#genres', cg); lib.set_field('#characters', cc); lib.set_field('#fandoms', cf)
    lib.set_field('#status', cs); lib.set_field('tags', ct)
    print(f"\nWROTE. distinct tags {before} -> {len(lib.all_field_names('tags'))}")
else:
    print("\nRe-run with -- --apply to write.")
