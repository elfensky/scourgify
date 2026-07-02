#!/usr/bin/env python3
"""calibre-wrangler — normalize a FanFicFare-imported Calibre library from generic defaults + config.

Set CALIBRE_LIBRARY to your library folder first, then (everything runs under plain python3;
writes shell out to calibre-debug automatically):
  python3 wrangle.py setup            # interactive health check + first-run wizard
  python3 wrangle.py audit            # read-only dry-run report of every pass
  python3 wrangle.py apply --apply    # write changes  (Calibre must be CLOSED)
"""
import os, sys, re, csv, collections
from common import HERE, DEFAULTS as DEF, norm, ascii_fold, load_config, library, ro_connect, read_custom_column, run_writer
try:                                   # rich is optional (present in system python3 for `audit`; absent under calibre-debug)
    from rich.console import Console
    from rich.table import Table
    _con = Console(); RICH = True
except ImportError:
    RICH = False

# ---------------- defaults + overrides ----------------
def read_csv(path):
    return list(csv.DictReader(open(path))) if os.path.exists(path) else []
def read_lines(path):
    return [l.rstrip("\n") for l in open(path)] if os.path.exists(path) else []

def read_tropes(path):
    """tropes.csv: delimiter-sniffed (','|';'), positional variant,canonical,route; unknown route (freeform note) -> 'tag'."""
    if not os.path.exists(path): return []
    with open(path) as f: first = f.readline()
    delim = ";" if (";" in first and first.count(";") >= first.count(",")) else ","
    out = []
    with open(path) as f:
        for c in csv.reader(f, delimiter=delim):
            if not c or not c[0].strip() or c[0].strip().lower() == "variant": continue
            var = c[0].strip()
            canon = c[1].strip() if len(c) > 1 and c[1].strip() else var
            route = c[2].strip().lower() if len(c) > 2 and c[2].strip() else "tag"
            if route not in ("tag", "genre", "character", "fandom", "drop"): route = "tag"
            out.append((var, canon, route))
    return out

def resolve_trope_chains(raw):
    """Follow variant->canonical to a terminal; break cycles by min spelling. Fixes chains (A->B->C) and cycles (A<->B)."""
    res = {}
    for start in raw:
        seen, cur = set(), start
        while True:
            nxt = raw.get(cur, (cur,))[0]
            if nxt == cur or nxt not in raw: term = nxt; break
            if nxt in seen: term = min(seen | {cur, nxt}); break
            seen.add(cur); cur = nxt
        res[start] = (term, raw[start][1])
    return res

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
    m["fanvals"] = {norm(v) for v in m["fan"].values()}
    m["trope"] = resolve_trope_chains({v: (cn, rt) for v, cn, rt in
        (read_tropes(os.path.join(DEF, "tropes.csv")) + read_tropes(os.path.join(odir, "tropes.csv")))})
    m["fan_block"] = {norm(x) for x in read_lines(os.path.join(DEF, "fandom_blocklist.txt")) + read_lines(os.path.join(odir, "fandom_blocklist.txt")) if x and not x.startswith("#")}  # values that are never fandoms
    m["decompose"] = {}                          # one contextual value -> parts in several columns (e.g. "Fate SI" -> Type-Moon + SI/OC)
    for r in both("decompose.csv"):
        m["decompose"][norm(r["value"])] = {k: [x.strip() for x in (r.get(k) or "").split(";") if x.strip()]
                                             for k in ("fandoms", "characters", "tags", "genres")}
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

def is_junk(t, m):
    if t.strip().lower() in m["junk_exact"]: return True
    return any(rx.search(t) for rx in m["junk_rx"])

def build_tagcanon(spellings, m):
    """norm -> canonical spelling: most-common spelling per normalized form; bundled tropes canonical wins."""
    spell = collections.Counter(spellings)
    bynorm = collections.defaultdict(list)
    for t, ct in spell.items(): bynorm[norm(t)].append((ct, t))
    tc = {nm: max(lst)[1] for nm, lst in bynorm.items()}     # max by (count, spelling)
    for v, (cn, rt) in m["trope"].items():
        if rt == "tag": tc[norm(cn)] = cn
    return tc

# route for a trope, honoring config (au/crossover/etc. genre-vs-tag toggle)
def trope_route(canon, route, beh):
    key = {"alternate universe": "au_as", "crossover": "crossover_as",
           "reincarnation": "reincarnation_as", "time travel": "time_travel_as"}.get(norm(canon))
    if key: return beh.get(key, route)
    return route

# ---------------- the transform (per book) ----------------
def transform(d, m, beh, known_chars=frozenset(), tagcanon=None):
    """d: dict col_key -> list[str] for configured columns. Returns (newd, lost_fandom, lost_char).
    known_chars: normalized set of character names in the library (to rescue chars misfiled in #genres).
    tagcanon: norm -> canonical spelling map for generic normalize-merge of tag variants."""
    F = set(d.get("fandoms", [])); C = set(d.get("characters", [])); G0 = list(d.get("genres", []))
    R = set(d.get("relationships", [])); T = list(d.get("tags", [])); st = d.get("status", [])
    had_F, had_C = bool(F), bool(C)
    # decompose contextual compounds (e.g. "Fate SI" -> fandom Type-Moon + tag SI/OC) before normal routing
    seedF, seedC, seedG, seedT = set(), set(), set(), set()
    if m.get("decompose"):
        def _dec(vals):
            keep = []
            for v in vals:
                p = m["decompose"].get(norm(v))
                if p: seedF.update(p["fandoms"]); seedC.update(p["characters"]); seedG.update(p["genres"]); seedT.update(p["tags"])
                else: keep.append(v)
            return keep
        F = set(_dec(F)); G0 = _dec(G0); T = _dec(T)
    # fandoms: alias -> canonical (drop if mapped to empty)
    nF = set()
    for f in F:
        tgt = m["fan"].get(f, f)
        if not tgt: continue                                  # alias -> empty: drop
        if norm(tgt) in m["fan_block"]:                       # a curated non-fandom (kink/rating/status/meta) -> tag pipeline routes it
            T.append(tgt); continue
        nF.add(tgt)
    nF |= {m["fan"].get(f, f) for f in seedF if m["fan"].get(f, f)}   # decomposed fandoms (skip alias->empty)
    # characters: fold abbrev/case -> full (global, then fandom-scoped)
    nC = set()
    for ch in C:
        if beh["fold_characters"]:
            ch = m["char"].get(ch) or next((m["char_fd"][(ch, fd)] for fd in nF if (ch, fd) in m["char_fd"]), ch)
        nC.add(ch)
    nC |= seedC                                                # decomposed characters
    # genres: split -> canon -> allowlist(keep) else move to tags
    nG = set(); extra_tags = set()
    ga = lambda na: na in m["gallow"] or any(na.startswith(x + " ") for x in m["gallow"] if len(x) >= 4)  # allowed genre?
    for g in G0:
        for atom in (m["gsplit"].get(g, [g])):
            a = m["gcanon"].get(atom, atom); na = norm(a)
            if ga(na):
                nG.add(a)                                       # allowlisted genre or a subtype of one (AU - Canon Divergence)
            elif na in m["fanvals"]: nF.add(a)                  # misfiled fandom
            elif na in known_chars: nC.add(a)                   # misfiled character (e.g. Akeno Himejima in #genres)
            else: extra_tags.add(a)                             # freeform -> tag
    nG |= seedG                                                 # decomposed genres
    # tags: junk drop / trope route / surface-fold / ascii / redundancy-strip
    nT = set(extra_tags) | seedT                                # decomposed tags
    homes = {norm(x) for x in nF | nC | nG | R | (set(st) if isinstance(st, list) else {st} if st else set())}
    for t in T:
        if is_junk(t, m): continue
        if t in m["trope"]:
            canon, route = m["trope"][t]; route = trope_route(canon, route, beh)
            if route == "genre": (nG if ga(norm(canon)) else nT).add(canon)   # genre only if allowlisted, else tag (keeps #genres idempotent)
            elif route == "fandom": nF.add(m["fan"].get(canon, canon))
            elif route == "character": nC.add(canon)
            elif beh.get("tropes_as") == "genre" and norm(canon) not in m["rating"]: (nG if ga(norm(canon)) else nT).add(canon)
            else: nT.add(canon)                       # tag fold
            continue
        if norm(t) in known_chars: nC.add(t); continue   # tag is actually a known character -> #characters
        if not beh.get("keep_categories", True) and norm(t) in {"multi", "gen", "f m", "m m", "f f", "other"}: continue
        tt = ascii_fold(t) if beh["ascii_only_tags"] else t
        if norm(tt) in homes: continue                # redundant: already in a structured column -> strip
        nT.add(tt)
    if tagcanon: nT = {tagcanon.get(norm(t), t) for t in nT}      # generic normalize-merge to canonical spelling
    newd = {"fandoms": sorted(nF), "characters": sorted(nC), "genres": sorted(nG),
            "relationships": sorted(R), "tags": sorted(nT)}
    if st: newd["status"] = st
    # losing a fandom only counts if the book had a REAL one (a blocklisted non-fandom going to 0 is intentional)
    had_real_F = any(m["fan"].get(f, f) and norm(m["fan"].get(f, f)) not in m["fan_block"] for f in F)
    return newd, (had_real_F and not nF), (had_C and not nC)

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

def read_library(cfg):
    """Read all configured columns per book via read-only sqlite. -> (cols, perbook, present, nb, allb)."""
    cols = col_key_label(cfg)
    con = ro_connect(); c = con.cursor()
    perbook = collections.defaultdict(lambda: collections.defaultdict(list)); present = {}
    for key, label in cols.items():
        if label == "tags":
            present[key] = True
            for b, v in c.execute("SELECT l.book,t.name FROM books_tags_link l JOIN tags t ON t.id=l.tag"): perbook[b][key].append(v)
            continue
        vals = read_custom_column(con, label, multi=True)
        present[key] = vals is not None
        for b, vs in (vals or {}).items(): perbook[b][key].extend(vs)
    nb = c.execute("SELECT count(*) FROM books").fetchone()[0]
    allb = set(perbook) | {r[0] for r in c.execute("SELECT id FROM books")}
    return cols, perbook, present, nb, allb

def audit(cfg, m):
    beh = cfg["behavior"]
    cols, perbook, present, nb, allb = read_library(cfg)
    before = {k: set() for k in cols}; after = {k: set() for k in cols}
    lostF = lostC = tagsB = tagsA = 0
    known_chars = {norm(v) for bb in perbook for v in perbook[bb].get("characters", [])}
    tagcanon = build_tagcanon((t for bb in perbook for t in perbook[bb].get("tags", [])), m)
    for b in allb:
        d = {k: perbook[b].get(k, []) for k in cols}
        for k in cols: before[k].update(d.get(k, []))
        nd, lf, lc = transform(d, m, beh, known_chars, tagcanon); lostF += lf; lostC += lc
        tagsB += len(d.get("tags", [])); tagsA += len(nd.get("tags", []))
        for k in cols:
            if k in nd: after[k].update(nd[k])
    print("=" * 60); print("calibre-wrangler AUDIT (read-only, no changes)"); print("=" * 60)
    print(f"books: {nb}   columns active: {', '.join(f'{k}->{v}' for k, v in cols.items() if present.get(k))}")
    miss = [k for k in cols if not present.get(k)]
    if miss: print(f"MISSING columns (run `setup`): {miss}")
    rows = [(k, len(before[k]), len(after[k]), len(after[k]) - len(before[k])) for k in cols if present.get(k)]
    if RICH:
        t = Table(title="proposed changes (distinct values per column)")
        t.add_column("column"); t.add_column("before", justify="right"); t.add_column("after", justify="right"); t.add_column("delta", justify="right")
        for k, b, a, d in rows:
            t.add_row(k, str(b), str(a), f"[red]{d}[/red]" if d < 0 else f"[green]+{d}[/green]" if d > 0 else "0")
        _con.print(t)
    else:
        print(f"\n{'column':14}{'before':>9}{'after':>9}{'delta':>8}")
        for k, b, a, d in rows: print(f"{k:14}{b:>9}{a:>9}{d:>8}")
    safe_ok = lostF == lostC == 0
    tagline = f"tag assignments: {tagsB} -> {tagsA}"
    if RICH:
        _con.print(f"\n[bold]SAFETY[/bold]  losing last fandom: {lostF}   losing last character: {lostC}   {tagline}   " + ("[green]✓ no data loss[/green]" if safe_ok else "[red]⚠ review losses[/red]"))
    else:
        print(f"\nSAFETY  books losing last fandom: {lostF}   losing last character: {lostC}   {tagline}")
        print("OK — no data loss." if safe_ok else "WARNING: review the losses above before apply.")
    # concrete examples — which rules actually fire on THIS library's values
    def ex(items, n=10): return "  " + (", ".join(items[:n]) + (f"  …(+{len(items)-n} more)" if len(items) > n else "")) if items else ""
    print("\n--- examples of what would change (sampled from your values) ---")
    if "characters" in before:
        fc = [f"{v}→{m['char'][v]}" for v in sorted(before["characters"]) if v in m["char"] and m["char"][v] != v]
        if fc: print(f"characters fold ({len(fc)}):{ex(fc)}")
    if "fandoms" in before:
        ff = [f"{v}→{m['fan'][v] or 'DROP'}" for v in sorted(before["fandoms"]) if v in m["fan"] and m["fan"][v] != v]
        if ff: print(f"fandoms canon ({len(ff)}):{ex(ff)}")
    if "genres" in before:
        dkey = lambda v: norm(v) in m["decompose"]
        gm = [f"{v}→{'|'.join(m['gsplit'][v]) if v in m['gsplit'] else m['gcanon'].get(v, v)}" for v in sorted(before["genres"]) if (v in m["gsplit"] or v in m["gcanon"]) and not dkey(v)]
        def _kept(v):
            na = norm(m["gcanon"].get(v, v))
            return na in m["gallow"] or any(na.startswith(x + " ") for x in m["gallow"] if len(x) >= 4)
        gch = [v for v in sorted(before["genres"]) if v not in m["gsplit"] and not _kept(v) and norm(v) not in m["fanvals"] and norm(v) in known_chars and not dkey(v)]
        gmv = [v for v in sorted(before["genres"]) if v not in m["gsplit"] and not _kept(v) and norm(v) not in m["fanvals"] and norm(v) not in known_chars and not dkey(v)]
        if gm: print(f"genres split/canon ({len(gm)}):{ex(gm)}")
        if gch: print(f"genres → characters ({len(gch)}):{ex(gch)}")
        if gmv: print(f"genres → tags (not in allowlist) ({len(gmv)}):{ex(gmv)}")
    if m.get("decompose"):
        de = []
        for k in ("fandoms", "genres", "tags"):
            for v in sorted(before.get(k, set())):
                p = m["decompose"].get(norm(v))
                if not p: continue
                bits = [c + "=" + "/".join(p[c]) for c in ("fandoms", "characters", "tags", "genres") if p[c]]
                de.append(v + " → " + ", ".join(bits))
        if de: print(f"decompose ({len(de)}):{ex(de)}")
    if "tags" in before:
        drops = [v for v in sorted(before["tags"]) if is_junk(v, m)]
        folds = [f"{v}→{m['trope'][v][0]}" for v in sorted(before["tags"]) if v in m["trope"] and m["trope"][v][0] != v]
        if drops: print(f"tags drop ({len(drops)}):{ex(drops)}")
        if folds: print(f"tags fold/route ({len(folds)}):{ex(folds)}")

# ---------------- APPLY (standalone: compute via sqlite, write via calibre-debug helper) ----------------
def tag_loss_guard(tags_before, tags_after, force):
    """Abort on a suspicious mass-deletion of tags (e.g. an over-broad junk.txt regex).
    ponytail: heuristic ceiling — >25% shrink AND >200 assignments lost; --force overrides."""
    lost = tags_before - tags_after
    if tags_before and lost > max(200, tags_before // 4) and not force:
        raise SystemExit(f"ABORT: tags would shrink {tags_before} -> {tags_after} assignments (-{lost}). "
                         "Check junk.txt / overrides for an over-broad rule, or re-run with --force.")

def apply_changes(cfg, m, do_write, force=False):
    beh = cfg["behavior"]
    cols, perbook, present, nb, allb = read_library(cfg)
    known_chars = {norm(v) for bb in perbook for v in perbook[bb].get("characters", [])}
    tagcanon = build_tagcanon((t for bb in perbook for t in perbook[bb].get("tags", [])), m)
    changes = collections.defaultdict(dict); lostF = lostC = tagsB = tagsA = 0
    for b in allb:
        d = {k: perbook[b].get(k, []) for k in cols}
        nd, lf, lc = transform(d, m, beh, known_chars, tagcanon); lostF += lf; lostC += lc
        tagsB += len(d.get("tags", [])); tagsA += len(nd.get("tags", []))
        for k, lab in cols.items():
            if k in nd and tuple(sorted(nd[k])) != tuple(sorted(d.get(k, []))):
                changes[lab][b] = sorted(nd[k])
    print("APPLY" if do_write else "PRE-APPLY (no write)")
    for lab, ch in changes.items(): print(f"  {lab:14} books changed: {len(ch)}")
    print(f"  SAFETY losing last fandom: {lostF} | character: {lostC} | tag assignments: {tagsB} -> {tagsA}")
    if lostF or lostC: raise SystemExit("ABORT: data loss detected")
    tag_loss_guard(tagsB, tagsA, force)
    if do_write:
        run_writer([{"op": "set_field", "field": lab, "values": {str(b): v for b, v in ch.items()}} for lab, ch in changes.items()])
    else:
        print("Re-run: python3 wrangle.py apply --apply   (Calibre closed; writes shell out to calibre-debug)")

def write_config(colmap, beh=None):
    b = beh or {}                                     # preserve existing toggles on re-run; defaults on first run
    bo = lambda k, d: "true" if b.get(k, d) else "false"
    sv = lambda k, d: b.get(k, d)
    L = ["# calibre-wrangler configuration (generated by `setup`; edit anytime).", "", "[columns]",
         '# FanFicFare field -> Calibre column LABEL. "" disables that field\'s passes.']
    L += [f'{k:<13} = "{colmap.get(k, "")}"' for k in ("fandoms", "characters", "relationships", "genres", "status", "tags")]
    L += ["", "# behavior toggles — opinionated defaults; flip to taste", "[behavior]",
          f"fold_characters  = {bo('fold_characters', True)}     # abbreviation -> full-name defaults (Harry P. -> Harry Potter)",
          f"ascii_only_tags  = {bo('ascii_only_tags', True)}     # transliterate non-ASCII tags to plain ASCII",
          f'au_as            = "{sv("au_as", "genre")}"  # where Alternate Universe lands: "genre" or "tag"',
          f'crossover_as     = "{sv("crossover_as", "genre")}"',
          f'reincarnation_as = "{sv("reincarnation_as", "genre")}"',
          f'time_travel_as   = "{sv("time_travel_as", "genre")}"',
          f"fold_ratings     = {bo('fold_ratings', False)}    # Erotica->Smut, Adult->Mature",
          f"keep_categories  = {bo('keep_categories', True)}     # keep Multi/Gen/F-M tags (false drops them)",
          f'tropes_as        = "{sv("tropes_as", "tag")}"    # fold recognized tropes (SI/OC, Fix-It…) into #genres? "genre" or "tag"',
          "", "[overrides]",
          "# folder of user files (same formats as defaults/) that extend & win over the defaults",
          'dir = "overrides"', ""]
    open(os.path.join(HERE, "config.toml"), "w").write("\n".join(L))

OK, WARN, BAD = "✓", "⚠", "✗"     # status glyphs (plain; no color dependency)
def _interactive():
    # interactive iff stdin AND stderr are TTYs and nothing forces otherwise (pattern from lintle's term.py):
    # prevents an invisible-prompt hang when output is piped/redirected or under CI / --yes.
    if os.environ.get("CI") or os.environ.get("NONINTERACTIVE") or "--yes" in sys.argv or "-y" in sys.argv:
        return False
    try: return sys.stdin.isatty() and sys.stderr.isatty()
    except Exception: return False
def _ask(prompt, default=True):
    """y/n prompt; off a TTY (pipe / CI / --yes) take the default instead of blocking. 3 retries; EOF -> default."""
    if not _interactive(): return default
    for _ in range(3):
        try: a = input(f"{prompt} [{'Y/n' if default else 'y/N'}] ").strip().lower()
        except EOFError: return default
        if a == "": return default
        if a in ("y", "yes"): return True
        if a in ("n", "no"): return False
        print("  please answer y or n.")
    return default

def setup(cfg):
    import subprocess, shutil, json as _json
    print("=" * 64); print("  calibre-wrangler — setup & health check"); print("=" * 64)
    if not _interactive(): print("(non-interactive — taking recommended defaults; run in a terminal to choose per item)")
    ops = []                                          # column/pref writes queued here, applied via calibre-debug at the end
    con = ro_connect()

    # [1] library
    print("\n[1] Library");  print(f"  {OK} metadata.db  ({library()})")

    # [2] FanFicFare: installed? configured? known gotchas?
    print("\n[2] FanFicFare")
    try:
        out = subprocess.run(["calibre-customize", "-l"], capture_output=True, text=True, timeout=30).stdout if shutil.which("calibre-customize") else ""
        installed = ("fanficfare" in out.lower()) if out else None
    except Exception: installed = None
    print(f"  {OK} plugin installed" if installed else
          f"  {BAD} plugin NOT installed (Calibre → Preferences → Plugins → Get new plugins → FanFicFare)" if installed is False else
          f"  {WARN} couldn't query plugins (continuing)")
    row = con.execute("SELECT val FROM preferences WHERE key='namespaced:FanFicFarePlugin:settings'").fetchone()
    settings = _json.loads(row[0]) if row else {}
    fff = settings.get("custom_cols") or {}
    if not settings:
        print(f"  {WARN} no FanFicFare config for this library yet — configure FFF + import a story, then re-run setup")
    else:
        print(f"  {OK} configured — Calibre column ← FFF field:")
        for col, fld in sorted(fff.items()): print(f"        {col:16} ← {fld}")
        ini = settings.get("personal.ini", ""); no = settings.get("custom_cols_newonly", {}) or {}
        issues = []
        if fff.get("#fandoms") == "series": issues.append("#fandoms ← series  (fandom-vs-series gotcha: fandoms land in the numbered Series field)")
        if any(l.strip().lower() == "include_in_series:category" for l in ini.splitlines()): issues.append("personal.ini: include_in_series:category  (stuffs the fandom into Series)")
        if no.get("#genres") is not True: issues.append("#genres not newonly-protected  (a metadata re-fetch would re-pollute your cleaned genres)")
        for i in issues: print(f"  {WARN} {i}")
        if not issues: print(f"  {OK} config looks correct (no known gotchas)")
        elif _ask("  → Fix these now (map #fandoms←category, drop include_in_series, protect #genres)?"):
            import copy; s = copy.deepcopy(settings)
            s["personal.ini"] = "\n".join(l for l in s.get("personal.ini", "").splitlines() if l.strip().lower() != "include_in_series:category")
            if fff.get("#fandoms") == "series": s.setdefault("custom_cols", {})["#fandoms"] = "category"
            s.setdefault("custom_cols_newonly", {})["#genres"] = True
            ops.append({"op": "set_pref", "key": "namespaced:FanFicFarePlugin:settings", "value": s}); print(f"  {OK} queued FanFicFare config fix")

    # [3] columns: the engine's 5 + the datetime markers staleness/classify need
    print("\n[3] Columns")
    have = {"#" + l for (l,) in con.execute("SELECT label FROM custom_columns")} | {"tags"}
    REC = [("#fandoms", "Fandoms", "text", True), ("#characters", "Characters", "text", True),
           ("#relationships", "Relationships", "text", True), ("#genres", "Genres", "text", True),
           ("#status", "Status", "text", False), ("#updated", "Updated", "datetime", False),
           ("#wrangled", "Wrangled", "datetime", False)]
    for label, name, dt, mult in REC:
        if label in have: print(f"  {OK} {label}"); continue
        why = "  (staleness + classify --incremental need this)" if label in ("#updated", "#wrangled") else ""
        if _ask(f"  {BAD} {label} missing — create '{name}' ({dt}{', multiple' if mult else ''}){why}?"):
            ops.append({"op": "create_column", "label": label.lstrip("#"), "name": name, "datatype": dt, "is_multiple": mult}); have.add(label); print(f"      queued {label}")
        else: print(f"      skipped {label}")

    # [4] config.toml column map (FFF field -> our key, else adopt existing labels)
    print("\n[4] config.toml")
    FFF2KEY = {"category": "fandoms", "characters": "characters", "ships": "relationships", "genre": "genres", "status": "status"}
    colmap = {"tags": "tags"}
    for col, fld in fff.items():
        k = FFF2KEY.get(fld)
        if k and col in have: colmap[k] = col
    for label, key in (("#fandoms", "fandoms"), ("#characters", "characters"), ("#relationships", "relationships"), ("#genres", "genres"), ("#status", "status")):
        if not colmap.get(key) and label in have: colmap[key] = label
    write_config(colmap, cfg["behavior"])
    print(f"  {OK} wrote config.toml (behavior toggles preserved):")
    for k in ("fandoms", "characters", "relationships", "genres", "status", "tags"):
        lab = colmap.get(k, ""); print(f"        {k:13} → {lab or '(unset — pass not run for this column)'}")

    # [5] overrides
    odir = os.path.join(HERE, cfg["overrides"].get("dir", "overrides"))
    print("\n[5] Overrides");  print(f"  {OK} {odir}" if os.path.isdir(odir) else f"  {WARN} no overrides/ dir (optional — add your own maps here; they win over defaults/)")

    if ops:
        print(f"\n[6] Applying {len(ops)} change(s) to Calibre (via calibre-debug)")
        run_writer(ops)
    print("\n" + "-" * 64)
    print("Setup complete. Next:")
    print("  python3 wrangle.py audit          # read-only dry-run of all passes")
    print("  python3 wrangle.py apply --apply  # write changes (Calibre closed; backs up first)")
    print("  python3 classify.py --incremental # content-tag new/updated books (cheap)")

# ---------------- main ----------------
if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Normalize a FanFicFare-imported Calibre library (writes auto-shell to calibre-debug). "
                                            "With no command: launch the interactive wizard.")
    p.add_argument("command", nargs="?", default=None, choices=["setup", "audit", "apply"],
                   help="setup: interactive health check + configure | audit: read-only dry-run | apply: write changes | (none): wizard")
    p.add_argument("--apply", action="store_true", help="with `apply`: actually write (Calibre closed)")
    p.add_argument("--force", action="store_true", help="override the tag mass-deletion guardrail")
    p.add_argument("--yes", "-y", action="store_true", help="non-interactive: take the recommended default for every prompt")
    a = p.parse_args()
    if a.command is None:
        if sys.stdin.isatty() and sys.stdout.isatty():
            import wizard                  # lazy: keeps rich fully optional for the plain subcommands
            wizard.run()
        else:
            p.print_help()
        sys.exit(0)
    library()                      # fail fast with a clear message before doing any work
    cfg = load_config(); maps = load_maps(cfg)
    if a.command == "audit": audit(cfg, maps)
    elif a.command == "apply": apply_changes(cfg, maps, a.apply, a.force)
    elif a.command == "setup": setup(cfg)
