#!/usr/bin/env python3
import os
# READ-ONLY. Generates editable review CSVs for the Calibre fanfic cleanup. No DB writes.
# Each row carries a `decision` column pre-filled with the suggestion — edit only what you disagree with.
import sqlite3, re, csv, collections, os

LIB = os.path.expanduser(os.environ.get("CALIBRE_LIBRARY", ""))
if not LIB:
    raise SystemExit("Set CALIBRE_LIBRARY to your Calibre library folder (the one containing metadata.db).")
DB = os.path.join(LIB, "metadata.db")
OUT = os.path.dirname(os.path.abspath(__file__))
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True); c = con.cursor()

def cid(label):
    r = c.execute("SELECT id,is_multiple FROM custom_columns WHERE label=?", (label,)).fetchone()
    return r

def col_counts(label):
    """{value: assignment_count} for a custom column (multi or single)."""
    r = cid(label)
    if not r: return {}
    i, multi = r
    if multi:
        q = (f"SELECT v.value,count(*) FROM custom_column_{i} v "
             f"JOIN books_custom_column_{i}_link l ON l.value=v.id GROUP BY v.id")
    else:
        q = f"SELECT value,count(*) FROM custom_column_{i} GROUP BY value"
    return dict(c.execute(q).fetchall())

def norm(s):
    s = str(s).strip().lower()
    s = re.sub(r"[\[\]\(\)]", "", s); s = s.replace("&", "and")
    s = re.sub(r"[\s_\-/]+", " ", s); s = re.sub(r"[^\w ]", "", s, flags=re.UNICODE)
    return s.strip()
def ntight(s): return norm(s).replace(" ", "")

# ---- load all columns ----
tags = dict(c.execute("SELECT t.name,count(*) FROM tags t JOIN books_tags_link l ON l.tag=t.id GROUP BY t.id").fetchall())
fandoms = col_counts("fandoms"); characters = col_counts("characters")
genres = col_counts("genres"); relationships = col_counts("relationships"); status = col_counts("status")

def normset(d): return {norm(k) for k in d}, {ntight(k) for k in d}
F_n, F_t = normset(fandoms); C_n, C_t = normset(characters)
R_n, R_t = normset(relationships); S_n, _ = normset(status); G_n, _ = normset(genres)

# ---- vocabularies ----
REAL_GENRES = {"adventure","romance","humor","humour","comedy","drama","angst","hurt comfort",
    "friendship","family","fantasy","sci fi","science fiction","supernatural","mystery","horror",
    "tragedy","parody","suspense","western","poetry","crime","spiritual","general","sport","sports",
    "action","thriller","slice of life","action adventure"}
RATING = {"nsfw","sfw","explicit","explicit sexual content","explicit content","explicit language",
    "explicit sex","mature","mature content","adult","adult content","adult themes","erotica","smut",
    "lemon","lemons","lime","limes","pwp","fluff","gore","graphic","graphic violence","violence",
    "r rated","m rated","x rated","lemon free","no smut","dark content","mature audiences"}
TROPE_TAGS = {"self insert","si","si oc","oc","oc insert","original character","reader insert","harem",
    "bashing","powerful","powerful harry","godlike","godlike powers","manipulative","independent",
    "slow burn","fix it","wbwl","boy who lived","genderbend","fem","smart","dark","evil","grey",
    "soul bond","bond","master of death","gamer","system","litrpg","op mc"}
TROPE_GENRES = {"alternate universe","au","canon divergence","alternate universe canon divergence",
    "crossover","reincarnation","time travel","time loop","alternate history","dimensional travel",
    "isekai","fusion","alternate universe time travel"}
JUNK_DROP = {"fanfiction","fan fiction","anime and manga","anime manga","tv and movies","tv movies",
    "x overs","x over","books","movies","games","cartoons and comics","comics","misc","miscellaneous",
    "general audiences","none","na","unknown"}
CATEGORY = {"f m","m m","f f","gen","multi","other","het","slash","femslash","poly","no romance"}

def home_column(n, nt):
    """Which structured column genuinely owns this normalized value (strongest signal first)."""
    if n in F_n or nt in F_t: return "fandoms"
    if n in C_n or nt in C_t: return "characters"
    if n in R_n or nt in R_t: return "relationships"
    if n in S_n: return "status"
    return None

def classify(value, source):
    """Return (type_guess, suggested_target, note). source = 'tags' or 'genres'."""
    n = norm(value); nt = ntight(value)
    if n in JUNK_DROP: return ("junk", "drop", "noise / non-informative")
    if n in RATING:    return ("rating", "→tags", "content rating -> tags (per your call)")
    if n in CATEGORY:  return ("category", "→tags", "relationship-category marker -> tags")
    if n in REAL_GENRES: return ("genre", "keep-genres" if source=="genres" else "→genres", "real genre")
    if n in TROPE_GENRES: return ("trope", "→genres", "setting/plot trope (suggest genres; flip to tags if you prefer)")
    if n in TROPE_TAGS:   return ("trope", "→tags", "meta trope (suggest tags; flip to genres if you prefer)")
    h = home_column(n, nt)
    # priority-rule caveat: don't promote rating/category words even if they stray into a high-prio col
    if h == "fandoms" and (n in {"multi","fantasy","gen","f m","m m","f f","other","none"}):
        return ("category", "→tags", "category/marker — stray fandom mis-entry, do NOT promote")
    if h:
        if source == "tags":   return (h, "strip", f"already in {h} -> remove from tags (backfill first if missing on a book)")
        else:                  return (h, f"→{h}", f"misfiled in genres -> move to {h}")
    # no structured home, not a known meta word
    if nt in F_t: return ("fandom?", "→fandoms", "looks like a fandom variant — verify")
    if nt in C_t: return ("character?", "→characters", "looks like a character variant — verify")
    if source == "tags":
        return ("freeform", "keep-tags", "freeform tag with no structured home — stays in tags")
    # genres source: freeform descriptor that isn't a real genre -> default to tags (your model)
    return ("freeform", "→tags", "freeform descriptor — suggest move to tags; set keep-genres if it's a real (sub)genre")

def write_csv(name, header, rows):
    p = os.path.join(OUT, name)
    with open(p, "w", newline="") as f:
        w = csv.writer(f); w.writerow(header); w.writerows(rows)
    return len(rows)

summary = {}

# ---- genres_map ----
rows = []
for v, n in sorted(genres.items(), key=lambda x: -x[1]):
    tg, tgt, note = classify(v, "genres")
    rows.append([v, n, tg, tgt, tgt, note])  # decision pre-filled = tgt
summary["genres_map.csv"] = write_csv("genres_map.csv",
    ["value","count","type_guess","suggested_target","decision","notes"], rows)

# ---- tags_map ----
rows = []
for v, n in sorted(tags.items(), key=lambda x: -x[1]):
    tg, tgt, note = classify(v, "tags")
    rows.append([v, n, tg, tgt, tgt, note])
summary["tags_map.csv"] = write_csv("tags_map.csv",
    ["value","count","type_guess","suggested_target","decision","notes"], rows)

# ---- fandoms_map ----
rows = []
fan_groups = collections.defaultdict(list)
for v, n in fandoms.items(): fan_groups[ntight(v)].append((v, n))
seen = set()
for v, n in sorted(fandoms.items(), key=lambda x: -x[1]):
    grp = fan_groups[ntight(v)]
    canon = max(grp, key=lambda x: x[1])[0]
    nn = norm(v)
    if nn in RATING: tgt, note = "→tags", "not a fandom; rating -> tags"
    elif nn in TROPE_TAGS or nn in {"si","oc","au","harem"}: tgt, note = "→tags", "not a fandom; trope -> tags"
    elif nn in {"complete","completed","oneshot"}: tgt, note = "→status", "status leaked into fandoms"
    elif len(grp) > 1 and v != canon: tgt, note = f"merge→{canon}", "case/spelling/JP variant"
    else: tgt, note = "keep", ""
    rows.append([v, n, tgt, tgt, note])
summary["fandoms_map.csv"] = write_csv("fandoms_map.csv",
    ["value","count","suggested_target","decision","notes"], rows)

# ---- characters_map (only rows needing action) ----
rows = []
# case/punct duplicate groups
ch_groups = collections.defaultdict(list)
for v, n in characters.items(): ch_groups[norm(v)].append((v, n))
for k, grp in ch_groups.items():
    if len(grp) > 1:
        canon = max(grp, key=lambda x: x[1])[0]
        for v, n in sorted(grp, key=lambda x: -x[1]):
            if v != canon:
                rows.append([v, n, f"merge→{canon}", "case-dupe", "high", f"merge→{canon}", "identical except case/punct"])
# FFN abbreviation -> full name candidates
fulls = collections.defaultdict(list)  # (first, last_initial) -> [(name,count)]
abbr_re = re.compile(r"^(.+?)\s+([A-Za-z])\.?$")
abbrevs = []
for v, n in characters.items():
    m = abbr_re.match(v.strip())
    parts = v.strip().split()
    if m and len(parts) == 2 and len(parts[1].rstrip(".")) == 1:
        abbrevs.append((v, n, norm(parts[0]), parts[1].rstrip(".").lower()))
    elif len(parts) >= 2 and len(parts[-1]) > 1:  # full name candidate
        fulls[(norm(parts[0]), parts[-1][0].lower())].append((v, n))
for v, n, first, init in sorted(abbrevs, key=lambda x: -x[1]):
    cands = fulls.get((first, init), [])
    if len(cands) == 1:
        canon = cands[0][0]; conf = "high"; note = "single full-name match"
    elif len(cands) > 1:
        canon = max(cands, key=lambda x: x[1])[0]; conf = "low"; note = f"{len(cands)} candidates (homonym risk): " + ", ".join(x[0] for x in cands[:4])
    else:
        canon = ""; conf = "none"; note = "no full-name candidate found"
    rows.append([v, n, f"merge→{canon}" if canon else "review", "abbrev", conf, f"merge→{canon}" if conf=="high" else "review", note])
summary["characters_map.csv"] = write_csv("characters_map.csv",
    ["value","count","suggested_target","kind","confidence","decision","notes"], rows)

# ---- relationships_map (variant groups) ----
def ship_key(v):
    parts = re.split(r"\s*[\/&×x]\s*| x ", v.strip())
    parts = [norm(p) for p in parts if norm(p)]
    return tuple(sorted(parts))
rel_groups = collections.defaultdict(list)
for v, n in relationships.items(): rel_groups[ship_key(v)].append((v, n))
rows = []
for k, grp in sorted(rel_groups.items(), key=lambda kv: -sum(n for _, n in kv[1])):
    if len(grp) > 1:
        grp.sort(key=lambda x: -x[1]); canon = grp[0][0]
        variants = "; ".join(f"{v} ({n})" for v, n in grp)
        rows.append([variants, sum(n for _, n in grp), canon, "exact-name", "high", canon, "same spelled-out names, differing order/connector/case"])
summary["relationships_map.csv"] = write_csv("relationships_map.csv",
    ["variants","total_count","suggested_canonical","kind","confidence","decision","notes"], rows)
# NOTE: cross-spelling ships (Harry/Hermione = Harry P./Hermione G.) need char-name resolution; flagged separately.

# ---- authors_map (collision groups only) ----
auth = dict(c.execute("SELECT a.name,count(*) FROM authors a JOIN books_authors_link l ON l.author=a.id GROUP BY a.id").fetchall())
def an(s): return re.sub(r"[\s_]+", " ", s.strip().lower())
ag = collections.defaultdict(list)
for name, n in auth.items(): ag[an(name)].append((name, n))
rows = []
for k, grp in sorted(ag.items(), key=lambda kv: -sum(n for _, n in kv[1])):
    if len(grp) > 1:
        grp.sort(key=lambda x: -x[1]); canon = grp[0][0]
        rows.append(["; ".join(f"{v} ({n})" for v, n in grp), sum(n for _, n in grp), canon, canon])
summary["authors_map.csv"] = write_csv("authors_map.csv",
    ["variants","total_books","suggested_canonical","decision"], rows)

# ---- README ----
with open(os.path.join(OUT, "00_README.md"), "w") as f:
    f.write("# Calibre fanfic cleanup — review maps\n\n")
    f.write("Edit the `decision` column in each CSV. Pre-filled with my suggestion — change only what you disagree with.\n\n")
    f.write("Targets: `keep`/`keep-genres` (leave), `strip` (remove from tags; lives in a column), ")
    f.write("`→tags`/`→genres`/`→fandoms`/`→characters`/`→status` (move), `merge→X` (rename to X), `drop` (delete), `review` (you decide).\n\n")
    f.write("Nothing is applied until you approve. Row counts:\n\n")
    for k in sorted(summary): f.write(f"- **{k}** — {summary[k]} rows\n")
    f.write("\nNote: cross-spelling ship variants (e.g. `Harry/Hermione` = `Harry P./Hermione G.`) ")
    f.write("need character-name resolution and are NOT auto-grouped here — handle after the characters map.\n")

print("WROTE to", OUT)
for k in sorted(summary): print(f"  {k}: {summary[k]} rows")
