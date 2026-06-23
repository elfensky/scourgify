#!/usr/bin/env python3
"""calibre-wrangler — normalize a FanFicFare-imported Calibre library from generic defaults + config.

Set CALIBRE_LIBRARY to your library folder first, then:
  python3 wrangle.py audit                    # read-only dry-run report (no Calibre needed)
  calibre-debug -e wrangle.py -- setup        # first-run wizard: detect/create columns, write config
  calibre-debug -e wrangle.py -- apply        # write changes  (Calibre must be CLOSED)
"""
import os, sys, re, csv, sqlite3, collections

HERE = os.path.dirname(os.path.abspath(__file__))
DEF = os.path.join(HERE, "defaults")
LIB = os.path.expanduser(os.environ.get("CALIBRE_LIBRARY", ""))
if not LIB:
    raise SystemExit("Set CALIBRE_LIBRARY to your Calibre library folder (the one containing metadata.db).")
DB = os.path.join(LIB, "metadata.db")
CMD = next((a for a in sys.argv[1:] if not a.startswith("-")), "audit")

def norm(s):
    s = str(s).strip().lower(); s = re.sub(r"[\[\]\(\)]", "", s); s = s.replace("&", "and")
    s = re.sub(r"[\s_\-/]+", " ", s); s = re.sub(r"[^\w ]", "", s, flags=re.UNICODE); return s.strip()

# ---------------- config (minimal TOML reader; no tomllib dependency) ----------------
def load_config():
    cfg = {"columns": {"fandoms": "#fandoms", "characters": "#characters", "relationships": "#relationships",
                       "genres": "#genres", "status": "#status", "tags": "tags"},
           "behavior": {"fold_characters": True, "ascii_only_tags": True, "au_as": "genre", "crossover_as": "genre",
                        "reincarnation_as": "genre", "time_travel_as": "genre", "fold_ratings": False, "keep_categories": True},
           "overrides": {"dir": "overrides"}}
    p = os.path.join(HERE, "config.toml")
    if os.path.exists(p):
        sec = None
        for raw in open(p):
            ln = raw.strip()
            if not ln or ln.startswith("#"): continue
            if ln.startswith("["): sec = ln[1:ln.index("]")].strip(); cfg.setdefault(sec, {}); continue
            if "=" in ln and sec:
                k, v = ln.split("=", 1); k = k.strip(); v = v.strip()
                if v[:1] in ("\"", "'"):                 # quoted string -> value between the quotes (# allowed inside)
                    v = v[1:].split(v[0], 1)[0]
                else:                                    # bool/number -> strip any trailing inline comment
                    v = v.split("#", 1)[0].strip()
                    if v.lower() in ("true", "false"): v = v.lower() == "true"
                cfg[sec][k] = v
    return cfg

# ---------------- defaults + overrides ----------------
def read_csv(path):
    return list(csv.DictReader(open(path))) if os.path.exists(path) else []
def read_lines(path):
    return [l.rstrip("\n") for l in open(path)] if os.path.exists(path) else []

def load_maps(cfg):
    odir = os.path.join(HERE, cfg["overrides"].get("dir", "overrides"))
    def both(fn):  # defaults first, overrides last (override wins)
        return read_csv(os.path.join(DEF, fn)) + read_csv(os.path.join(odir, fn))
    m = {}
    m["char"] = {}; m["char_fd"] = {}            # global variant->canon ; (variant,fandom)->canon
    for r in both("characters.csv"):
        if r.get("fandom"): m["char_fd"][(r["variant"], r["fandom"])] = r["canonical"]
        else: m["char"][r["variant"]] = r["canonical"]
    m["fan"] = {r["alias"]: r["canonical"] for r in both("fandoms.csv")}
    m["trope"] = {r["variant"]: (r["canonical"], r.get("route", "tag")) for r in both("tropes.csv")}
    m["gsplit"] = {r["combined"]: r["atoms"].split("|") for r in both("genres_split.csv")}
    m["gcanon"] = {r["variant"]: r["canonical"] for r in both("genres_canon.csv")}
    m["gallow"] = {norm(x) for x in read_lines(os.path.join(DEF, "genres_allow.txt")) + read_lines(os.path.join(odir, "genres_allow.txt")) if x and not x.startswith("#")}
    m["rating"] = {norm(x) for x in read_lines(os.path.join(DEF, "ratings.txt")) + read_lines(os.path.join(odir, "ratings.txt")) if x and not x.startswith("#")}
    m["junk_exact"], m["junk_rx"] = set(), []
    for ln in read_lines(os.path.join(DEF, "junk.txt")) + read_lines(os.path.join(odir, "junk.txt")):
        if not ln or ln.startswith("#"): continue
        if ln.startswith("re:"): m["junk_rx"].append(re.compile(ln[3:], re.I))
        else: m["junk_exact"].add(ln.strip().lower())
    return m

def ascii_fold(s):
    import unicodedata
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').replace("…", "...").replace("–", "-").replace("—", "-")
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()

def is_junk(t, m):
    if t.strip().lower() in m["junk_exact"]: return True
    return any(rx.search(t) for rx in m["junk_rx"])

# route for a trope, honoring config (au/crossover/etc. genre-vs-tag toggle)
def trope_route(canon, route, beh):
    key = {"alternate universe": "au_as", "crossover": "crossover_as",
           "reincarnation": "reincarnation_as", "time travel": "time_travel_as"}.get(norm(canon))
    if key: return beh.get(key, route)
    return route

# ---------------- the transform (per book) ----------------
def transform(d, m, beh, cols):
    """d: dict col_key -> list[str] for configured columns. Returns (newd, lost_fandom, lost_char)."""
    F = set(d.get("fandoms", [])); C = set(d.get("characters", [])); G0 = list(d.get("genres", []))
    R = set(d.get("relationships", [])); T = list(d.get("tags", [])); st = d.get("status", [])
    had_F, had_C = bool(F), bool(C)
    # fandoms: alias -> canonical (drop if mapped to empty)
    nF = set()
    for f in F:
        tgt = m["fan"].get(f, f)
        if tgt: nF.add(tgt)
    # characters: fold abbrev/case -> full (global, then fandom-scoped)
    nC = set()
    for ch in C:
        if beh["fold_characters"]:
            ch = m["char"].get(ch) or next((m["char_fd"][(ch, fd)] for fd in nF if (ch, fd) in m["char_fd"]), ch)
        nC.add(ch)
    # genres: split -> canon -> allowlist(keep) else move to tags
    nG = set(); extra_tags = set()
    for g in G0:
        for atom in (m["gsplit"].get(g, [g])):
            a = m["gcanon"].get(atom, atom)
            if norm(a) in m["gallow"]: nG.add(a)
            elif "genres" in cols and norm(a) in {norm(x) for x in m["fan"].values()}: nF.add(a)  # misfiled fandom
            else: extra_tags.add(a)                                                                # freeform -> tag
    # tags: junk drop / trope route / surface-fold / ascii / redundancy-strip
    nT = set(extra_tags)
    homes = {norm(x) for x in nF | nC | nG | R | (set(st) if isinstance(st, list) else {st} if st else set())}
    for t in T:
        if is_junk(t, m): continue
        if t in m["trope"]:
            canon, route = m["trope"][t]; route = trope_route(canon, route, beh)
            if route == "genre": nG.add(canon)
            elif route == "fandom": nF.add(m["fan"].get(canon, canon))
            elif route == "character": nC.add(canon)
            else: nT.add(canon)                       # tag fold
            continue
        if not beh.get("keep_categories", True) and norm(t) in {"multi", "gen", "f m", "m m", "f f", "other"}: continue
        tt = ascii_fold(t) if beh["ascii_only_tags"] else t
        if norm(tt) in homes: continue                # redundant: already in a structured column -> strip
        nT.add(tt)
    newd = {"fandoms": sorted(nF), "characters": sorted(nC), "genres": sorted(nG),
            "relationships": sorted(R), "tags": sorted(nT)}
    if st: newd["status"] = st
    return newd, (had_F and not nF), (had_C and not nC)

# ---------------- column resolution ----------------
def fff_columns_from_prefs(get_pref):
    """Read FanFicFare's custom_cols mapping from the library prefs, if present."""
    try:
        s = get_pref("namespaced:FanFicFarePlugin:settings") or {}
        return s.get("custom_cols", {}) or {}
    except Exception:
        return {}

# ---------------- AUDIT (read-only sqlite) ----------------
def col_key_label(cfg):
    return {k: v for k, v in cfg["columns"].items() if v}     # col_key -> calibre label

def audit(cfg, m):
    beh = cfg["behavior"]; cols = col_key_label(cfg)
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True); c = con.cursor()
    def cid(label):
        r = c.execute("SELECT id,is_multiple FROM custom_columns WHERE label=?", (label.lstrip("#"),)).fetchone()
        return r
    # per-book values per configured column
    perbook = collections.defaultdict(lambda: collections.defaultdict(list))
    present = {}
    for key, label in cols.items():
        if label == "tags":
            present[key] = True
            for b, v in c.execute("SELECT l.book,t.name FROM books_tags_link l JOIN tags t ON t.id=l.tag"): perbook[b][key].append(v)
            continue
        r = cid(label)
        if not r: present[key] = False; continue
        present[key] = True; i, mult = r
        has_link = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (f"books_custom_column_{i}_link",)).fetchone()
        if has_link:
            for b, v in c.execute(f"SELECT x.book,v.value FROM books_custom_column_{i}_link x JOIN custom_column_{i} v ON v.id=x.value"): perbook[b][key].append(v)
        else:
            for b, v in c.execute(f"SELECT book,value FROM custom_column_{i}"): perbook[b][key].append(v)
    nb = c.execute("SELECT count(*) FROM books").fetchone()[0]
    before = {k: set() for k in cols}; after = {k: set() for k in cols}
    lostF = lostC = 0; allb = set(perbook) | {r[0] for r in c.execute("SELECT id FROM books")}
    for b in allb:
        d = {k: perbook[b].get(k, []) for k in cols}
        for k in cols: before[k].update(d.get(k, []))
        nd, lf, lc = transform(d, m, beh, cols); lostF += lf; lostC += lc
        for k in cols:
            if k in nd: after[k].update(nd[k])
    print("=" * 60); print("calibre-wrangler AUDIT (read-only, no changes)"); print("=" * 60)
    print(f"books: {nb}   columns active: {', '.join(f'{k}->{v}' for k, v in cols.items() if present.get(k))}")
    miss = [k for k in cols if not present.get(k)]
    if miss: print(f"MISSING columns (run `setup`): {miss}")
    print(f"\n{'column':14}{'before':>9}{'after':>9}{'delta':>8}")
    for k in cols:
        if present.get(k): print(f"{k:14}{len(before[k]):>9}{len(after[k]):>9}{len(after[k])-len(before[k]):>8}")
    print(f"\nSAFETY  books losing last fandom: {lostF}   losing last character: {lostC}")
    print("OK — no data loss." if lostF == lostC == 0 else "WARNING: review the losses above before apply.")

# ---------------- APPLY / SETUP (Calibre API) ----------------
def with_api():
    from calibre.library import db as DB_
    return DB_(LIB).new_api

def apply_changes(cfg, m, do_write):
    beh = cfg["behavior"]; cols = col_key_label(cfg); api = with_api()
    def fv(label, b):
        x = api.field_for("tags" if label == "tags" else label, b)
        return list(x) if isinstance(x, (tuple, list, set, frozenset)) else ([x] if x else [])
    changes = collections.defaultdict(dict); lostF = lostC = 0
    for b in api.all_book_ids():
        d = {k: fv(lab, b) for k, lab in cols.items()}
        nd, lf, lc = transform(d, m, beh, cols); lostF += lf; lostC += lc
        for k, lab in cols.items():
            if k in nd and tuple(sorted(nd[k])) != tuple(sorted(d.get(k, []))):
                changes[lab][b] = tuple(nd[k])
    print("APPLY" if do_write else "PRE-APPLY (no write)")
    for lab, ch in changes.items(): print(f"  {lab:14} books changed: {len(ch)}")
    print(f"  SAFETY losing last fandom: {lostF} | character: {lostC}")
    assert lostF == 0 and lostC == 0, "ABORT: data loss detected"
    if do_write:
        for lab, ch in changes.items(): api.set_field(lab, ch)
        print("WROTE.")
    else:
        print("Re-run with -- --apply to write (Calibre must be closed).")

def write_config(colmap):
    L = ["# calibre-wrangler configuration (generated by `setup`; edit anytime).", "", "[columns]",
         '# FanFicFare field -> Calibre column LABEL. "" disables that field\'s passes.']
    L += [f'{k:<13} = "{colmap.get(k, "")}"' for k in ("fandoms", "characters", "relationships", "genres", "status", "tags")]
    L += ["", "# behavior toggles — opinionated defaults; flip to taste", "[behavior]",
          "fold_characters  = true     # abbreviation -> full-name defaults (Harry P. -> Harry Potter)",
          "ascii_only_tags  = true     # transliterate non-ASCII tags to plain ASCII",
          'au_as            = "genre"  # where Alternate Universe lands: "genre" or "tag"',
          'crossover_as     = "genre"',
          'reincarnation_as = "genre"',
          'time_travel_as   = "genre"',
          "fold_ratings     = false    # Erotica->Smut, Adult->Mature",
          "keep_categories  = true     # keep Multi/Gen/F-M tags (false drops them)",
          "", "[overrides]",
          "# folder of user files (same formats as defaults/) that extend & win over the defaults",
          'dir = "overrides"', ""]
    open(os.path.join(HERE, "config.toml"), "w").write("\n".join(L))

def setup(cfg):
    from calibre.library import db as _DB
    legacy = _DB(LIB); api = legacy.new_api          # legacy object can create_custom_column
    yes = ("--yes" in sys.argv) or ("-y" in sys.argv)
    fff = fff_columns_from_prefs(api.pref)
    have = set(api.field_metadata.all_field_keys())
    FFF2KEY = {"category": "fandoms", "characters": "characters", "ships": "relationships", "genre": "genres", "status": "status"}
    print("=" * 60); print("calibre-wrangler SETUP"); print("=" * 60)
    if fff:
        print("Detected FanFicFare mapping (Calibre column <- FFF field):")
        for col, fld in sorted(fff.items()): print(f"   {col:16} <- {fld}")
        if fff.get("#fandoms") == "series":
            print("   ⚠️  #fandoms <- series: likely the fandom-vs-series gotcha; consider apply_fff_config.py + map #fandoms <- category.")
    else:
        print("No FanFicFare custom_cols mapping found (is FFF configured for this library?).")
    colmap = {"tags": "tags"}
    for col, fld in fff.items():
        k = FFF2KEY.get(fld)
        if k: colmap[k] = col
    REC = [("#fandoms", "Fandoms", True), ("#characters", "Characters", True), ("#genres", "Genres", True),
           ("#relationships", "Relationships", True), ("#status", "Status", False)]
    KEYOF = {"#fandoms": "fandoms", "#characters": "characters", "#genres": "genres",
             "#relationships": "relationships", "#status": "status"}
    created = []
    for label, name, mult in REC:
        key = KEYOF[label]
        if colmap.get(key) in have: continue          # mapped + exists
        if label in have: colmap[key] = label; continue  # exists but unmapped -> adopt
        ans = "y" if yes else input(f"Create missing column {label} ('{name}', {'multiple' if mult else 'single'} text)? [Y/n] ").strip().lower()
        if ans in ("", "y", "yes"):
            legacy.create_custom_column(label.lstrip("#"), name, "text", mult); colmap[key] = label; created.append(label)
        else:
            colmap.setdefault(key, "")
    write_config(colmap)
    print(f"\ncreated columns: {created or 'none'}")
    print("wrote config.toml column map:")
    for k in ("fandoms", "characters", "relationships", "genres", "status", "tags"):
        print(f"   {k:13} -> {colmap.get(k, '') or '(unset)'}")
    print("\nNext: `calibre-debug -e wrangle.py -- apply` (pre-apply), review, then add --apply.")

# ---------------- main ----------------
cfg = load_config(); maps = load_maps(cfg)
if CMD == "audit": audit(cfg, maps)
elif CMD == "apply": apply_changes(cfg, maps, "--apply" in sys.argv)
elif CMD == "setup": setup(cfg)
else: print("commands: audit | apply | setup")
