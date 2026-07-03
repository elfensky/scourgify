#!/usr/bin/env python3
import os
# Targeted re-clean of raw values introduced by new downloads/updates. Calibre CLOSED.
import sys, re, collections
APPLY = "--apply" in sys.argv
LIB = os.path.expanduser(os.environ.get("CALIBRE_LIBRARY", ""))
if not LIB:
    raise SystemExit("Set CALIBRE_LIBRARY to your Calibre library folder (the one containing metadata.db).")
from calibre.library import db as DB
api = DB(LIB).new_api

GENRE_FIX = {
    "Skyrim (Video Game)": ("fandom", "Skyrim"),
    "A Song Of Ice And Fire": ("fandom", "A Song of Ice and Fire"),
    "Game Of Thrones": ("fandom", "Game of Thrones"),
    "Self Insert": ("tag", "Self-Insert"),
    "Reincarnation Self Insert": ("split", "Reincarnation"),
    "Villain": ("tag", "Villain"), "Female Protagonist": ("tag", "Female Protagonist"),
    "Magic": ("tag", "Magic"), "Starks": ("drop", ""), "Targaryens": ("drop", ""),
}
TAG_DROP_EXACT = {"fanfiction", "fan fiction", "other additional tags to be added"}   # lowercase
TAG_TO_STATUS = {"in-progress": "In-Progress", "in progress": "In-Progress", "completed": "Completed"}  # lowercase keys
DROP_RX = re.compile(r"\bno beta\b|cross-?posted on|\bto be added\b|do ?n.?t copy|do not copy|author regrets (nothing|everything)", re.I)

ids = api.all_book_ids()
cg, cf, ct, cs = {}, {}, {}, {}
n = collections.Counter()
def fv(field, b):
    x = api.field_for(field, b)
    return list(x) if isinstance(x, (tuple, list, set, frozenset)) else ([x] if x else [])
for b in ids:
    G, F, T = fv('#genres', b), fv('#fandoms', b), fv('tags', b)
    nG, nF, nT = set(G), set(F), set(T)
    st = api.field_for('#status', b); new_status = st
    touched = False
    for g in list(G):
        if g in GENRE_FIX:
            act, tgt = GENRE_FIX[g]; nG.discard(g); touched = True; n['genre_' + act] += 1
            if act == "fandom": nF.add(tgt)
            elif act == "tag": nT.add(tgt)
            elif act == "split": nG.add(tgt); nT.add("Self-Insert")
    for t in list(T):
        tl = t.lower()
        if tl in TAG_DROP_EXACT: nT.discard(t); touched = True; n['tag_drop'] += 1
        elif tl in TAG_TO_STATUS:
            nT.discard(t); touched = True; n['tag_status'] += 1
            if not new_status: new_status = TAG_TO_STATUS[tl]
        elif tl == "self insert": nT.discard(t); nT.add("Self-Insert"); touched = True; n['tag_fold'] += 1
        elif DROP_RX.search(t): nT.discard(t); touched = True; n['tag_pattern_drop'] += 1
    if not touched: continue
    if nG != set(G): cg[b] = tuple(sorted(nG))
    if nF != set(F): cf[b] = tuple(sorted(nF))
    if nT != set(T): ct[b] = tuple(sorted(nT))
    if new_status != st: cs[b] = new_status

print("APPLY" if APPLY else "PRE-APPLY (no write)")
print("  actions:", dict(n))
print("  books changed: genres %d | fandoms %d | tags %d | status %d" % (len(cg), len(cf), len(ct), len(cs)))
if APPLY:
    api.set_field('#genres', cg); api.set_field('#fandoms', cf); api.set_field('tags', ct); api.set_field('#status', cs)
    print("\nWROTE.")
else:
    print("\nRe-run with -- --apply to write.")
