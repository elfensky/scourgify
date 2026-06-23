#!/usr/bin/env python3
# Rebuild #relationships from normalized participant names. Run: calibre-debug -e apply_relationships.py [-- --apply]
import sys, re, csv, os
APPLY = "--apply" in sys.argv
LIB = "/Users/andrei/Library/Mobile Documents/com~apple~CloudDocs/Calibre/fanfiction"
OUT = os.path.dirname(os.path.abspath(__file__))
from calibre.library import db as DB
lib = DB(LIB).new_api
def norm(s):
    s = str(s).strip().lower(); s = re.sub(r"[\[\]\(\)]", "", s); s = s.replace("&", "and")
    s = re.sub(r"[\s_\-/]+", " ", s); s = re.sub(r"[^\w ]", "", s); return s.strip()
def mp(fn):
    try: return list(csv.DictReader(open(f"{OUT}/{fn}")))
    except FileNotFoundError: return []

char_canon = {}
for r in mp("characters_map.csv"):
    if r["decision"].startswith("merge→") and r["confidence"] == "high": char_canon[r["value"]] = r["decision"].split("→",1)[1]
char_canon.update({"Harry P.":"Harry Potter","Hinata H.":"Hinata Hyuuga","Ron W.":"Ron Weasley",
    "Bellatrix L.":"Bellatrix Lestrange","Narcissa M.":"Narcissa Malfoy","Minerva M.":"Minerva McGonagall",
    "Andromeda T.":"Andromeda Tonks","Jarvis":"J.A.R.V.I.S.","Bellatrix Black Lestrange":"Bellatrix Lestrange",
    "Bellatrix":"Bellatrix Lestrange","Andromeda Black Tonks":"Andromeda Tonks"})
char_fa = {(r["abbrev"], r["fandom"]): r["fullname"] for r in mp("characters_fandom_aware.csv")
           if r.get("fullname") and r["confidence"] in ("high", "medium")}
clook = {norm(k): v for k, v in char_canon.items()}
def resolve(p, fandoms):
    p = p.split("|")[0].strip()
    if p in char_canon: return char_canon[p]
    for fd in fandoms:
        if (p, fd) in char_fa: return char_fa[(p, fd)]
    return clook.get(norm(p), p)

ids = lib.all_book_ids()
chg = {}; mixed = 0
for b in ids:
    R = lib.field_for('#relationships', b)
    if not R: continue
    F = list(lib.field_for('#fandoms', b) or ())
    newR = set()
    for ship in R:
        if "&" in ship and "/" in ship: newR.add(ship); mixed += 1; continue
        conn = "&" if "&" in ship else "/"
        parts = [resolve(p, F) for p in re.split(r"[/&]", ship) if p.strip()]
        seen = []; [seen.append(p) for p in parts if p not in seen]
        newR.add(conn.join(sorted(seen, key=lambda x: x.lower())) if len(seen) > 1 else (seen[0] if seen else ship))
    nt = tuple(sorted(newR))
    if nt != tuple(sorted(R)): chg[b] = nt

before = len(lib.all_field_names('#relationships'))
print("APPLY" if APPLY else "PRE-APPLY (no write)")
print(f"books with relationships changed: {len(chg)} | mixed-connector ships left as-is: {mixed}")
# sample
for b in list(chg)[:6]:
    print("   ", tuple(sorted(lib.field_for('#relationships', b))), "->", chg[b])
if APPLY:
    lib.set_field('#relationships', chg)
    after = len(lib.all_field_names('#relationships'))
    print(f"\nWROTE. distinct ships: {before} -> {after}")
else:
    print("\nRe-run with -- --apply to write.")
