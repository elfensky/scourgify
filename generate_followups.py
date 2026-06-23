#!/usr/bin/env python3
# READ-ONLY. Builds: relationships_rebuild.csv, tags_surface_dupes.csv (mechanical),
# and two INPUT files for the knowledge workflow. No DB writes.
import sqlite3, re, csv, collections, os
DB = "/Users/andrei/Library/Mobile Documents/com~apple~CloudDocs/Calibre/fanfiction/metadata.db"
OUT = os.path.dirname(os.path.abspath(__file__))
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True); c = con.cursor()

def norm(s):
    s = str(s).strip().lower(); s = re.sub(r"[\[\]\(\)]", "", s); s = s.replace("&", "and")
    s = re.sub(r"[\s_\-/]+", " ", s); s = re.sub(r"[^\w ]", "", s); return s.strip()
def cid(l): return c.execute("SELECT id,is_multiple FROM custom_columns WHERE label=?", (l,)).fetchone()
def colcounts(l):
    i, m = cid(l)
    if m: q = f"SELECT v.value,count(*) FROM custom_column_{i} v JOIN books_custom_column_{i}_link x ON x.value=v.id GROUP BY v.id"
    else: q = f"SELECT value,count(*) FROM custom_column_{i} GROUP BY value"
    return dict(c.execute(q).fetchall())

# ---------- char normalization map (from approved characters_map safe decisions) ----------
charmap = {}
for r in csv.DictReader(open(f"{OUT}/characters_map.csv")):
    d = r["decision"]
    if d.startswith("merge→") and r["confidence"] == "high":
        charmap[r["value"]] = d.split("→", 1)[1]
charmap.update({"Harry P.": "Harry Potter", "Hinata H.": "Hinata Hyuuga", "Ron W.": "Ron Weasley",
    "Bellatrix L.": "Bellatrix Lestrange", "Narcissa M.": "Narcissa Malfoy",
    "Minerva M.": "Minerva McGonagall", "Andromeda T.": "Andromeda Tonks", "Jarvis": "J.A.R.V.I.S."})
charlook = {norm(k): v for k, v in charmap.items()}
def resolve_part(p):
    p = p.split("|")[0].strip()           # drop AO3 "| alt-name" aliases
    return charmap.get(p) or charlook.get(norm(p)) or p

# ---------- relationships_rebuild.csv ----------
rel = colcounts("relationships")
def rebuild(v):
    has_amp, has_sl = "&" in v, "/" in v
    if has_amp and has_sl: return None              # mixed connector -> leave for manual
    conn = "&" if has_amp else "/"
    parts = [resolve_part(p) for p in re.split(r"[\/&]", v) if p.strip()]
    seen = []; [seen.append(p) for p in parts if p not in seen]
    return conn.join(sorted(seen, key=lambda x: x.lower()))
groups = collections.defaultdict(list)
mixed = []
for v, n in rel.items():
    rb = rebuild(v)
    (mixed if rb is None else groups[rb]).append((v, n))
rows = []
for canon, grp in groups.items():
    if len(grp) > 1 or grp[0][0] != canon:          # only rows that actually change/merge
        grp.sort(key=lambda x: -x[1])
        rows.append(["; ".join(f"{v}({n})" for v, n in grp), sum(n for _, n in grp), canon, canon])
rows.sort(key=lambda r: -r[1])
with open(f"{OUT}/relationships_rebuild.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["variants", "total_count", "rebuilt_canonical", "decision"]); w.writerows(rows)
rels_changed = len(rows)

# ---------- tags_surface_dupes.csv ----------
tags = dict(c.execute("SELECT t.name,count(*) FROM tags t JOIN books_tags_link l ON l.tag=t.id GROUP BY t.id").fetchall())
# only the tags that STAY (keep-tags / →tags), per tags_map
keepset = set()
for r in csv.DictReader(open(f"{OUT}/tags_map.csv")):
    if r["suggested_target"] in ("keep-tags", "→tags"): keepset.add(r["value"])
ACRONYMS = {"nsfw","sfw","si","oc","pwp","au","ooc","bamf","wbwl","asoiaf","mcu","dc","hp","ust","poc",
    "lgbt","bdsm","dom","sub","fwb","mc","op","gl","bl"}
STD = {"self insert":"Self-Insert","fix it":"Fix-It","hurt comfort":"Hurt/Comfort","si oc":"SI/OC",
    "oc insert":"SI/OC","slow burn":"Slow Burn","time travel":"Time Travel","fem slash":"Femslash",
    "pwp":"PWP","alternate universe":"Alternate Universe"}
def smart(group):
    group = sorted(group, key=lambda x: -x[1]); n0 = norm(group[0][0])
    if n0 in STD: return STD[n0]
    if n0 in ACRONYMS: return n0.upper()
    return group[0][0]                               # most-used as-is
g = collections.defaultdict(list)
for t, n in tags.items():
    if t in keepset: g[norm(t)].append((t, n))
rows = []
for k, grp in g.items():
    if len(grp) > 1:
        canon = smart(grp); grp.sort(key=lambda x: -x[1])
        rows.append(["; ".join(f"{v}({n})" for v, n in grp), sum(n for _, n in grp), canon, canon])
rows.sort(key=lambda r: -r[1])
with open(f"{OUT}/tags_surface_dupes.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["variants", "total_count", "proposed_canonical", "decision"]); w.writerows(rows)
tags_groups = len(rows)

# ---------- INPUT: chars_abbrev_by_fandom.csv (for knowledge agent A) ----------
# the unresolved abbrevs/homonyms = characters_map rows with confidence low/none
unresolved = set()
for r in csv.DictReader(open(f"{OUT}/characters_map.csv")):
    if r["confidence"] in ("low", "none"): unresolved.add(r["value"])
ci = cid("characters")[0]; fi = cid("fandoms")[0]
# book -> fandoms
bf = collections.defaultdict(list)
for book, val in c.execute(f"SELECT l.book,v.value FROM books_custom_column_{fi}_link l JOIN custom_column_{fi} v ON v.id=l.value"):
    bf[book].append(val)
# (char value, fandom) -> book count
pair = collections.Counter()
for book, val in c.execute(f"SELECT l.book,v.value FROM books_custom_column_{ci}_link l JOIN custom_column_{ci} v ON v.id=l.value"):
    if val in unresolved:
        fds = bf.get(book) or ["(no fandom)"]
        for fd in fds: pair[(val, fd)] += 1
rows = [[v, fd, n] for (v, fd), n in sorted(pair.items(), key=lambda x: (-x[1], x[0][0]))]
with open(f"{OUT}/_input_chars_abbrev_by_fandom.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["abbrev", "fandom", "books"]); w.writerows(rows)

# ---------- INPUT: _input_genres_tail.csv (for knowledge agent B) ----------
tail = []
for r in csv.DictReader(open(f"{OUT}/genres_map.csv")):
    if r["suggested_target"] == "→tags": tail.append((r["value"], int(r["count"])))
tail.sort(key=lambda x: -x[1])
with open(f"{OUT}/_input_genres_tail.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["value", "count"]); w.writerows(tail[:400])

print(f"relationships_rebuild.csv: {rels_changed} groups changed/merged  ({len(mixed)} mixed-connector left manual)")
print(f"tags_surface_dupes.csv: {tags_groups} merge groups")
print(f"_input_chars_abbrev_by_fandom.csv: {len(rows)} (abbrev,fandom) pairs from {len(unresolved)} abbrevs")
print(f"_input_genres_tail.csv: top 400 of {len(tail)} freeform genre values")
print("\nsample tag surface-dupes:")
for r in [r for r in open(f'{OUT}/tags_surface_dupes.csv').read().splitlines()[1:6]]: print("  ", r[:90])
