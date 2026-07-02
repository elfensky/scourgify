#!/usr/bin/env python3
import os
# Run via: calibre-debug -e apply.py            (computes + safety-checks, NO write)
#          calibre-debug -e apply.py -- --apply  (writes via Calibre API)
# Mirrors dryrun.py. Applies: tags, #genres, #fandoms, #characters, #status, authors.
# HOLDS: #relationships, series, other columns. Backfill-before-strip; aborts on any data loss.
import sys, re, csv, os, collections
APPLY = "--apply" in sys.argv
LIB = os.path.expanduser(os.environ.get("CALIBRE_LIBRARY", ""))
if not LIB:
    raise SystemExit("Set CALIBRE_LIBRARY to your Calibre library folder (the one containing metadata.db).")
OUT = os.path.dirname(os.path.abspath(__file__))
from calibre.library import db as DB
lib = DB(LIB).new_api

def norm(s):
    s = str(s).strip().lower(); s = re.sub(r"[\[\]\(\)]", "", s); s = s.replace("&", "and")
    s = re.sub(r"[\s_\-/]+", " ", s); s = re.sub(r"[^\w ]", "", s); return s.strip()
def asctight(s): return norm(str(s).encode("ascii", "ignore").decode()).replace(" ", "")
def mp(fn):
    try: return list(csv.DictReader(open(f"{OUT}/{fn}")))
    except FileNotFoundError: return []

# ---------- resolvers (identical policy to dryrun.py) ----------
tag_target, tag_home = {}, {}
for r in mp("tags_map.csv"):
    tag_target[r["value"]] = r["suggested_target"]
    m = re.search(r"already in (\w+)", r["notes"])
    if m: tag_home[r["value"]] = m.group(1)
tag_canon = {}
for r in mp("tags_surface_dupes.csv"):
    for part in r["variants"].split(";"):
        v = part.rsplit("(", 1)[0].strip()
        if v: tag_canon[v] = r["decision"]
for v in list(tag_target):
    if norm(v) == "erotica": tag_target[v] = "keep-tags"

gen_target = {r["value"]: r["suggested_target"] for r in mp("genres_map.csv")}
subgenre_keep = {r["value"] for r in mp("subgenres_proposal.csv") if norm(r["value"]) != "erotica"}
GEN_CANON = {"humour":"Humor","humor?":"Humor","sci fi":"Sci-Fi","science fiction":"Sci-Fi",
    "hurt-comfort":"Hurt/Comfort","hurt comfort":"Hurt/Comfort","comedy?":"Comedy","slice of life?":"Slice of Life",
    "slice of life":"Slice of Life","action-adventure":"Action/Adventure","#action":"Action","sports!":"Sports",
    "au":"Alternate Universe","alternate universe":"Alternate Universe"}
def genre_canon(v): return GEN_CANON.get(norm(v), v)

fan_dec = {r["value"]: r["decision"] for r in mp("fandoms_map.csv")}
for v in list(fan_dec):
    n = norm(v)
    if n == "litrpg": fan_dec[v] = "→genres"
    if "multicross" in n.replace(" ", "") or "multicrossover" in n.replace(" ", ""): fan_dec[v] = "→genres"
FAN_ALIAS = {"asoiaf":"A Song of Ice and Fire","dxd":"Highschool DxD","highschool dxd":"Highschool DxD",
    "high school dxd":"Highschool DxD","rwby":"RWBY","hp":"Harry Potter","mha":"My Hero Academia"}
def fandom_canon(v): return FAN_ALIAS.get(norm(v), v)
def fan_final(v):
    d = fan_dec.get(v, "keep")
    return d.split("→", 1)[1] if d.startswith("merge→") else fandom_canon(v)

char_canon = {}
for r in mp("characters_map.csv"):
    if r["decision"].startswith("merge→") and r["confidence"] == "high": char_canon[r["value"]] = r["decision"].split("→",1)[1]
char_canon.update({"Harry P.":"Harry Potter","Hinata H.":"Hinata Hyuuga","Ron W.":"Ron Weasley",
    "Bellatrix L.":"Bellatrix Lestrange","Narcissa M.":"Narcissa Malfoy","Minerva M.":"Minerva McGonagall",
    "Andromeda T.":"Andromeda Tonks","Jarvis":"J.A.R.V.I.S."})
char_fa = {(r["abbrev"], r["fandom"]): r["fullname"] for r in mp("characters_fandom_aware.csv")
           if r.get("fullname") and r["confidence"] in ("high", "medium")}
def resolve_char(v, fandoms):
    if v in char_canon: return char_canon[v]
    for fd in fandoms:
        if (v, fd) in char_fa: return char_fa[(v, fd)]
    return v

STATUS_CANON = {"in-progress":"In-Progress","in progress":"In-Progress","completed":"Completed","complete":"Completed",
    "finished":"Completed","hiatus":"Hiatus","on hiatus":"Hiatus","abandoned":"Abandoned","dropped":"Dropped","rewritten":"Rewritten"}

fan_tight = {}
for v in lib.all_field_names('#fandoms'):
    if fan_dec.get(v, "keep") in ("→tags", "→status", "→genres"): continue
    at = asctight(v)
    if len(at) >= 5: fan_tight.setdefault(at, fan_final(v))
def foldfan(s):
    for cand in (s, s.split("/")[0], s.split("|")[0], s.split("(")[0]):
        at = asctight(cand)
        if len(at) >= 5 and at in fan_tight: return fan_tight[at]
    return None

# ---------- compute new per-book values ----------
ids = lib.all_book_ids()
def fv(field, b):
    x = lib.field_for(field, b)
    return list(x) if isinstance(x, (tuple, list, set, frozenset)) else ([x] if x else [])
chg = {k: {} for k in ('tags', '#genres', '#fandoms', '#characters', '#status')}
loss_fandom = loss_char = 0
for b in ids:
    T = fv('tags', b); F0 = fv('#fandoms', b); C = fv('#characters', b); G = fv('#genres', b)
    st = lib.field_for('#status', b)
    real_F = [v for v in F0 if fan_dec.get(v, "keep") not in ("→tags", "→status", "→genres")]
    had_fandom = bool(real_F) or any(tag_home.get(t) == "fandoms" for t in T) or any(gen_target.get(g) == "→fandoms" for g in G)
    had_char = bool(C) or any(tag_home.get(t) == "characters" for t in T) or any(gen_target.get(g) == "→characters" for g in G)
    F = {fan_final(v) for v in real_F}
    newT, newG = set(), set()
    new_status = st
    def set_status(val):
        global new_status
        cs = STATUS_CANON.get(norm(val))
        if cs and not new_status: new_status = cs
    for t in T:
        tgt = tag_target.get(t, "keep-tags"); home = tag_home.get(t)
        if tgt == "strip" and home == "fandoms": F.add(fandom_canon(t))
        elif tgt == "strip" and home == "characters": C = C + [resolve_char(t, F)]
        elif tgt == "strip" and home == "status": set_status(t)
        elif tgt == "strip" and home == "relationships": pass
        elif tgt == "→genres": newG.add(genre_canon(t))
        elif tgt == "drop": pass
        else:
            ff = foldfan(t) if tgt == "keep-tags" else None
            if ff: F.add(ff)
            else: newT.add(tag_canon.get(t, t))
    for g in G:
        tgt = gen_target.get(g)
        if g in subgenre_keep or tgt == "keep-genres": newG.add(genre_canon(g))
        elif tgt == "→fandoms": F.add(fandom_canon(g))
        elif tgt == "→characters": C = C + [resolve_char(g, F)]
        elif tgt == "→status": set_status(g)
        elif tgt == "→relationships": pass
        elif tgt == "drop": pass
        elif tgt == "→tags":
            ff = foldfan(g)
            if ff: F.add(ff)
            else: newT.add(tag_canon.get(g, g))
        else: newG.add(genre_canon(g))
    newC = sorted({resolve_char(v, F) for v in C})
    if had_fandom and not F: loss_fandom += 1
    if had_char and not newC: loss_char += 1
    chg['tags'][b] = tuple(sorted(newT))
    chg['#genres'][b] = tuple(sorted(newG))
    chg['#fandoms'][b] = tuple(sorted(F))
    chg['#characters'][b] = tuple(newC)
    if new_status != st: chg['#status'][b] = new_status

# ---------- authors ----------
auth_canon = {}
for r in mp("authors_map.csv"):
    for part in r["variants"].split(";"):
        v = part.rsplit("(", 1)[0].strip()
        if v: auth_canon[v] = r["decision"]
author_chg = {}
for b in ids:
    a = fv('authors', b)
    na = tuple(dict.fromkeys(auth_canon.get(x, x) for x in a))   # map + dedupe, keep order
    if tuple(a) != na: author_chg[b] = na

# ---------- safety + report ----------
print("=" * 60)
print("APPLY" if APPLY else "PRE-APPLY CHECK (no write; pass --apply to write)")
print("=" * 60)
print(f"books: {len(ids)}")
print(f"SAFETY: books losing last real fandom: {loss_fandom} | losing last character: {loss_char}")
assert loss_fandom == 0 and loss_char == 0, "ABORT: data loss detected, nothing written"
for f in ('tags', '#genres', '#fandoms', '#characters'):
    changed = sum(1 for b in chg[f] if tuple(sorted(fv(f, b))) != tuple(sorted(chg[f][b])))
    distinct_after = len({v for vs in chg[f].values() for v in vs})
    print(f"  {f:14} books changed: {changed:5}   distinct after: {distinct_after}")
print(f"  {'#status':14} books changed: {len(chg['#status'])}")
print(f"  {'authors':14} books changed: {len(author_chg)}")

if not APPLY:
    print("\nNo changes written. Re-run with:  calibre-debug -e apply.py -- --apply")
else:
    print("\nWRITING via Calibre API ...")
    for f in ('#fandoms', '#characters', '#status', '#genres', 'tags'):   # backfill cols first, tags last
        lib.set_field(f, chg[f] if f != '#status' else chg['#status'])
        print(f"  wrote {f}")
    if author_chg:
        lib.set_field('authors', author_chg); print("  wrote authors")
    print("DONE. Reopen Calibre and spot-check.")
