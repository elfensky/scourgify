#!/usr/bin/env python3
import os
# READ-ONLY dry-run. Simulates the full cleanup in memory and reports deltas + safety. No DB writes.
import sqlite3, re, csv, collections, os
LIB = os.path.expanduser(os.environ.get("CALIBRE_LIBRARY", ""))
if not LIB:
    raise SystemExit("Set CALIBRE_LIBRARY to your Calibre library folder (the one containing metadata.db).")
DB = os.path.join(LIB, "metadata.db")
OUT = os.path.dirname(os.path.abspath(__file__))
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True); c = con.cursor()
def norm(s):
    s = str(s).strip().lower(); s = re.sub(r"[\[\]\(\)]", "", s); s = s.replace("&", "and")
    s = re.sub(r"[\s_\-/]+", " ", s); s = re.sub(r"[^\w ]", "", s); return s.strip()
def cid(l): return c.execute("SELECT id,is_multiple FROM custom_columns WHERE label=?", (l,)).fetchone()
def per_book(label):
    i, m = cid(l := label)
    d = collections.defaultdict(list)
    if m:
        for b, v in c.execute(f"SELECT x.book,v.value FROM books_custom_column_{i}_link x JOIN custom_column_{i} v ON v.id=x.value"): d[b].append(v)
    else:
        for b, v in c.execute(f"SELECT book,value FROM custom_column_{i}"): d[b].append(v)
    return d
NB = c.execute("SELECT count(*) FROM books").fetchone()[0]
bT = collections.defaultdict(list)
for b, v in c.execute("SELECT l.book,t.name FROM books_tags_link l JOIN tags t ON t.id=l.tag"): bT[b].append(v)
bF, bC, bG = per_book("fandoms"), per_book("characters"), per_book("genres")
def mp(fn):
    try: return list(csv.DictReader(open(f"{OUT}/{fn}")))
    except FileNotFoundError: return []

# ---------- resolvers ----------
# tags
tag_target = {}; tag_home = {}
for r in mp("tags_map.csv"):
    tag_target[r["value"]] = r["suggested_target"]
    m = re.search(r"already in (\w+)", r["notes"])
    if m: tag_home[r["value"]] = m.group(1)
tag_canon = {}                                   # surface-dupe merges within tags
for r in mp("tags_surface_dupes.csv"):
    canon = r["decision"]
    for part in r["variants"].split(";"):
        v = part.rsplit("(", 1)[0].strip()
        if v: tag_canon[v] = canon
# OVERRIDE: Erotica stays a tag
for v in list(tag_target):
    if norm(v) == "erotica": tag_target[v] = "keep-tags"

# genres
gen_target = {}
for r in mp("genres_map.csv"): gen_target[r["value"]] = r["suggested_target"]
subgenre_keep = set()
for r in mp("subgenres_proposal.csv"):
    if norm(r["value"]) != "erotica": subgenre_keep.add(r["value"])   # Erotica -> tag, not genre
# genre canonical consolidation
GEN_CANON = {"humour":"Humor","humor?":"Humor","sci fi":"Sci-Fi","science fiction":"Sci-Fi",
    "hurt-comfort":"Hurt/Comfort","hurt comfort":"Hurt/Comfort","comedy?":"Comedy",
    "slice of life?":"Slice of Life","slice of life":"Slice of Life","action-adventure":"Action/Adventure",
    "#action":"Action","sports!":"Sports","au":"Alternate Universe","alternate universe":"Alternate Universe"}
def genre_canon(v): return GEN_CANON.get(norm(v), v)

# fandoms
fan_dec = {}
for r in mp("fandoms_map.csv"): fan_dec[r["value"]] = r["decision"]
# OVERRIDES from review
for v in list(fan_dec):
    n = norm(v)
    if n == "litrpg": fan_dec[v] = "→genres"
    if "multicross" in n.replace(" ", "") or "multi cross" in n or "multicrossover" in n.replace(" ", ""): fan_dec[v] = "→genres"

# characters: safe merges + fandom-aware (value+fandom)
char_canon = {}
for r in mp("characters_map.csv"):
    if r["decision"].startswith("merge→") and r["confidence"] == "high": char_canon[r["value"]] = r["decision"].split("→",1)[1]
char_canon.update({"Harry P.":"Harry Potter","Hinata H.":"Hinata Hyuuga","Ron W.":"Ron Weasley",
    "Bellatrix L.":"Bellatrix Lestrange","Narcissa M.":"Narcissa Malfoy","Minerva M.":"Minerva McGonagall",
    "Andromeda T.":"Andromeda Tonks","Jarvis":"J.A.R.V.I.S."})
char_fa = {}                                       # (abbrev, fandom) -> fullname
for r in mp("characters_fandom_aware.csv"):
    if r.get("fullname") and r["confidence"] in ("high","medium"): char_fa[(r["abbrev"], r["fandom"])] = r["fullname"]
def resolve_char(v, fandoms):
    if v in char_canon: return char_canon[v]
    for fd in fandoms:
        if (v, fd) in char_fa: return char_fa[(v, fd)]
    return v

# fandom canonical alias (for values moved INTO fandoms from tags/genres) — top known + identity
FAN_ALIAS = {"asoiaf":"A Song of Ice and Fire","dxd":"Highschool DxD","highschool dxd":"Highschool DxD",
    "high school dxd":"Highschool DxD","rwby":"RWBY","hp":"Harry Potter","mha":"My Hero Academia"}
def fandom_canon(v): return FAN_ALIAS.get(norm(v), v)
def fan_final(v):
    d = fan_dec.get(v, "keep")
    return d.split("→", 1)[1] if d.startswith("merge→") else fandom_canon(v)

# ---------- simulate per book ----------
def asctight(s):
    return norm(str(s).encode("ascii", "ignore").decode()).replace(" ", "")
# ascii-tight -> canonical fandom (catches JP/alt-name-suffixed fandom values)
fan_values = {v for vs in bF.values() for v in vs}
fan_tight = {}
for v in fan_values:
    if fan_dec.get(v, "keep") in ("→tags", "→status", "→genres"): continue
    at = asctight(v)
    if len(at) >= 5: fan_tight.setdefault(at, fan_final(v))
def foldfan(s):                                  # match s (or its pre-/|( part) to a canonical fandom
    for cand in (s, s.split("/")[0], s.split("|")[0], s.split("(")[0]):
        at = asctight(cand)
        if len(at) >= 5 and at in fan_tight: return fan_tight[at]
    return None
loss_fandom = loss_char = junk_only_fandom = folded2fan = 0
final_tags, final_gen, final_fan, final_chr = collections.Counter(), collections.Counter(), collections.Counter(), collections.Counter()
moved_tag2col = collections.Counter(); dropped_tags = 0; merged_tags = 0; kept_tags = 0
allbooks = set(bT) | set(bF) | set(bC) | set(bG)
for b in allbooks:
    F = set(bF.get(b, [])); C = set(bC.get(b, [])); G = set(bG.get(b, [])); T = bT.get(b, [])
    real_F = [v for v in F if fan_dec.get(v, "keep") not in ("→tags", "→status", "→genres")]
    had_fandom = bool(real_F) or any(tag_home.get(t) == "fandoms" for t in T) or any(gen_target.get(g) == "→fandoms" for g in G)
    had_char = bool(C) or any(tag_home.get(t) == "characters" for t in T) or any(gen_target.get(g) == "→characters" for g in G)
    if F and not real_F: junk_only_fandom += 1     # only fandom value(s) were junk like NSFW
    F = {fan_final(v) for v in real_F}
    # process tags
    newT = set()
    for t in T:
        tgt = tag_target.get(t, "keep-tags"); home = tag_home.get(t)
        if tgt == "strip" and home == "fandoms": F.add(fandom_canon(t))
        elif tgt == "strip" and home == "characters": C.add(resolve_char(t, F))
        elif tgt == "strip" and home == "status": pass
        elif tgt == "strip" and home == "relationships": pass
        elif tgt == "→genres": G.add(genre_canon(t))
        elif tgt == "drop": pass
        else:
            ff = foldfan(t) if tgt == "keep-tags" else None
            if ff: F.add(ff); folded2fan += 1                  # JP/alt-name fandom tag -> fandoms
            else: newT.add(tag_canon.get(t, t))
    # process genres moves
    newG = set()
    for g in list(G):
        tgt = gen_target.get(g)
        if g in subgenre_keep or tgt == "keep-genres": newG.add(genre_canon(g))
        elif tgt == "→fandoms": F.add(fandom_canon(g))
        elif tgt == "→characters": C.add(resolve_char(g, F))
        elif tgt == "→status": pass
        elif tgt == "→relationships": pass
        elif tgt == "drop": pass
        elif tgt == "→tags":
            ff = foldfan(g)
            if ff: F.add(ff); folded2fan += 1
            else: newT.add(tag_canon.get(g, g))
        else: newG.add(genre_canon(g))          # moved-in tag-genres / unknown -> keep
    # characters: canonicalize
    newC = {resolve_char(v, F) for v in C}
    if had_fandom and not F: loss_fandom += 1
    if had_char and not newC: loss_char += 1
    for v in newT: final_tags[v] += 1
    for v in newG: final_gen[v] += 1
    for v in F: final_fan[v] += 1
    for v in newC: final_chr[v] += 1

# before counts
bt = len({v for vs in bT.values() for v in vs}); bg = len({v for vs in bG.values() for v in vs})
bf = len({v for vs in bF.values() for v in vs}); bc = len({v for vs in bC.values() for v in vs})
rebuild = mp("relationships_rebuild.csv"); authors = mp("authors_map.csv")

print("="*64); print("DRY RUN — simulated result (NO changes written)"); print("="*64)
print(f"books: {NB}\n")
print("%-14s %10s %10s %10s" % ("column", "before", "after", "delta"))
for name, bcount, after in [("tags", bt, len(final_tags)), ("genres", bg, len(final_gen)),
                            ("fandoms", bf, len(final_fan)), ("characters", bc, len(final_chr))]:
    print("%-14s %10d %10d %10d" % (name, bcount, after, after - bcount))
print(f"\nrelationships: {len(rebuild)} ship groups rebuilt/merged")
print(f"authors: {len(authors)} merge groups")
print(f"surface-dupe tag merges loaded: {len(tag_canon)} | JP/alt fandom values folded to fandoms: {folded2fan}")
print("\n--- SAFETY INVARIANT (real loss must be 0) ---")
print(f"books losing their last REAL fandom: {loss_fandom}")
print(f"books losing their last character: {loss_char}")
print(f"(info) books whose only fandom value was junk like NSFW -> correctly end empty: {junk_only_fandom}")
assert loss_fandom == 0 and loss_char == 0, "SAFETY FAIL: data loss detected"
print("OK: no book loses real fandom/character data.")
print("\n--- top final genres (should be clean genre set) ---")
for v, n in final_gen.most_common(20): print("   %5d  %s" % (n, v))
print(f"\n--- final tags: {len(final_tags)} distinct (was {bt}); top 15 ---")
for v, n in final_tags.most_common(15): print("   %5d  %s" % (n, v))
