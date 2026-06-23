#!/usr/bin/env python3
# Build the bundled generic `defaults/` from a source library's review maps (the *_map.csv files).
# Maintainer-only: needs the personal review maps present (they're gitignored). The OUTPUT defaults/
# is committed and ships with the tool. Generic fanfic facts only — personal preference folds excluded.
import csv, os
HERE = os.path.dirname(os.path.abspath(__file__))
DEF = os.path.join(HERE, "defaults"); os.makedirs(DEF, exist_ok=True)

def rows(fn):
    p = os.path.join(HERE, fn)
    return list(csv.DictReader(open(p))) if os.path.exists(p) else []
def write_csv(name, header, data):
    seen, out = set(), []
    for r in data:
        k = tuple(r)
        if k not in seen and all(x.strip() for x in r[:2]): seen.add(k); out.append(r)
    with open(os.path.join(DEF, name), "w", newline="") as f:
        w = csv.writer(f); w.writerow(header); w.writerows(sorted(out, key=lambda r: r[0].lower()))
    return len(out)
def write_txt(name, header, lines):
    with open(os.path.join(DEF, name), "w") as f:
        f.write(header.rstrip() + "\n"); f.write("\n".join(lines) + "\n")
    return len(lines)

# preference folds to EXCLUDE from generic defaults (user's taste, belong in config, not facts)
PREF_FOLDS = {("erotica", "Smut"), ("adult", "Mature"), ("sex", "Smut"), ("adult", "Smut")}

# ---- characters.csv : variant,canonical,fandom  (fandom blank = global; set = homonym-scoped) ----
chars = []
for r in rows("characters_map.csv"):
    d = r.get("decision", "")
    if d.startswith("merge→") and r.get("confidence") == "high":
        chars.append([r["value"], d.split("→", 1)[1], ""])
for r in rows("characters_fandom_aware.csv"):
    if r.get("fullname") and r.get("confidence") in ("high", "medium") \
       and r.get("fandom") not in ("", "(no fandom)") and r["fullname"] != r["abbrev"]:
        chars.append([r["abbrev"], r["fullname"], r["fandom"]])
n_chars = write_csv("characters.csv", ["variant", "canonical", "fandom"], chars)

# ---- fandoms.csv : alias,canonical (casing / JP->EN / "X SI"->X / franchise dup) ----
fan = [[r["fandom"], r["target"]] for r in rows("fandoms_consolidate_map.csv")
       if r.get("action") in ("merge", "to_real") and r.get("target")]
n_fan = write_csv("fandoms.csv", ["alias", "canonical"], fan)

# ---- tropes.csv : variant,canonical,route  (route = tag|genre|character|fandom) ----
trop = []
for r in rows("freeform_trope_map.csv"):
    a, t = r.get("action"), r.get("target", "")
    if not t: continue
    if a == "fold" and (r["tag"].strip().lower(), t) not in PREF_FOLDS: trop.append([r["tag"], t, "tag"])
    elif a == "to_genres": trop.append([r["tag"], t, "genre"])
    elif a == "to_characters": trop.append([r["tag"], t, "character"])
    elif a == "to_fandoms": trop.append([r["tag"], t, "fandom"])
for r in rows("tags_surface_dupes.csv"):
    canon = r.get("decision") or r.get("proposed_canonical", "")
    for part in r.get("variants", "").split(";"):
        v = part.rsplit("(", 1)[0].strip()
        if v and canon and v != canon: trop.append([v, canon, "tag"])
n_trop = write_csv("tropes.csv", ["variant", "canonical", "route"], trop)

# ---- junk.txt : drop list (plain = exact ci; 're:' prefix = regex ci) ----
exact = set()
for r in rows("freeform_trope_map.csv"):
    if r.get("action") == "drop": exact.add(r["tag"])
for r in rows("tags_map.csv"):
    if r.get("suggested_target") == "drop": exact.add(r["value"])
PATTERNS = [r"no beta", r"cross-?posted on", r"\bto be added\b", r"do ?n.?t copy", r"do not copy",
            r"author regrets (nothing|everything)", r"more tags", r"will add more tags", r"like men$"]
junk_lines = ["re:" + p for p in PATTERNS] + sorted({v for v in exact if v.strip()}, key=str.lower)
n_junk = write_txt("junk.txt", "# Tags to DROP. plain line = case-insensitive exact match; 're:<regex>' = case-insensitive regex search.", junk_lines)

# ---- genres: split / canon / allowlist  +  ratings vocab ----
SPLIT = {"Adventure/Action": "Adventure|Action", "Action/Adventure": "Action|Adventure",
         "Action-Adventure": "Action|Adventure", "Drama & Romance": "Drama|Romance",
         "Action & Romance": "Action|Romance", "Adventure & Romance": "Adventure|Romance"}
n_split = write_csv("genres_split.csv", ["combined", "atoms"], [[k, v] for k, v in SPLIT.items()])
GCANON = {"Comedy?": "Comedy", "Humor?": "Humor", "isekai?": "Isekai", "Crossover?": "Crossover",
          "Time-Travel": "Time Travel", "#Action": "Action", "Sports!": "Sports",
          "Science Fiction": "Sci-Fi", "Humour": "Humor", "Sci Fi": "Sci-Fi", "Hurt-Comfort": "Hurt/Comfort"}
n_gcanon = write_csv("genres_canon.csv", ["variant", "canonical"], [[k, v] for k, v in GCANON.items()])
GENRES = ["Adventure", "Romance", "Drama", "Humor", "Angst", "Hurt/Comfort", "Friendship", "Family",
          "Fantasy", "Sci-Fi", "Supernatural", "Mystery", "Horror", "Tragedy", "Parody", "Suspense",
          "Crime", "Spiritual", "General", "Action", "Crossover", "Alternate Universe", "Reincarnation",
          "Time Travel", "Isekai", "Slice of Life", "Comedy", "War", "LitRPG", "Historical", "Mecha",
          "Space Opera", "Steampunk", "Cyberpunk", "Dark Fantasy", "Urban Fantasy", "Western"]
n_allow = write_txt("genres_allow.txt", "# Canonical genre vocabulary (one per line). Values here stay in #genres; freeform values not here move to tags.", GENRES)
RATINGS = ["NSFW", "SFW", "Explicit", "Mature", "Teen", "General Audiences", "Smut", "Lemon", "Lime",
           "PWP", "Fluff", "Gore", "Graphic Violence", "Erotica", "Adult", "Underage", "Non-Con", "Dub-Con"]
n_rate = write_txt("ratings.txt", "# Content-rating / warning vocabulary (kept as tags).", RATINGS)

print("defaults/ built:")
for nm, c in [("characters.csv", n_chars), ("fandoms.csv", n_fan), ("tropes.csv", n_trop),
              ("junk.txt", n_junk), ("genres_split.csv", n_split), ("genres_canon.csv", n_gcanon),
              ("genres_allow.txt", n_allow), ("ratings.txt", n_rate)]:
    print(f"  {nm:20} {c}")
print("TOTAL generic default entries:", n_chars + n_fan + n_trop + n_junk + n_split + n_gcanon + n_allow + n_rate)
