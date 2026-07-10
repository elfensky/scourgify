#!/usr/bin/env python3
"""scourgify — normalize a FanFicFare-imported Calibre library from generic defaults + config.

Set CALIBRE_LIBRARY to your library folder first, then (everything runs under plain python3;
writes shell out to calibre-debug automatically):
  scourgify setup            # interactive health check + first-run wizard
  scourgify audit            # read-only dry-run report of every pass
  scourgify apply --apply    # write changes  (Calibre must be CLOSED)
"""
import os, sys, re, csv, time, collections
from scourgify.common import DEFAULTS as DEF, norm, ascii_fold, load_config, library, ro_connect, read_custom_column, run_writer
try:                                   # rich is optional (present in system python3 for `audit`; absent under calibre-debug)
    from rich.console import Console
    from rich.table import Table
    _con = Console(); RICH = True
except ImportError:
    RICH = False

# ---------------- defaults + overrides ----------------
def read_csv(path: str) -> list:
    return list(csv.DictReader(open(path))) if os.path.exists(path) else []
def read_lines(path: str) -> list:
    return [l.rstrip("\n") for l in open(path)] if os.path.exists(path) else []

def read_tropes(path: str) -> list:
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

def resolve_trope_chains(raw: dict) -> dict:
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

def load_maps(cfg: dict) -> dict:
    odir = os.path.join(os.getcwd(), cfg["overrides"].get("dir", "overrides"))
    ao3 = os.path.join(DEF, "ao3")               # generated AO3 layer (build_ao3_layer.py) — loaded FIRST, everything overrides it
    def ao3_pairs(fn):                           # master,name,rel pair rows -> {name: master}; {} if the layer is absent
        return {r["name"]: r["master"] for r in read_csv(os.path.join(ao3, fn))}
    def both(fn):  # defaults first, overrides last (override wins)
        return read_csv(os.path.join(DEF, fn)) + read_csv(os.path.join(odir, fn))
    m = {}
    m["gallow"] = {norm(x) for x in read_lines(os.path.join(DEF, "genres_allow.txt")) + read_lines(os.path.join(odir, "genres_allow.txt")) if x and not x.startswith("#")}
    m["char"] = ao3_pairs("characters.csv"); m["char_fd"] = {}   # global variant->canon ; (variant,fandom)->canon
    for r in both("characters.csv"):
        if r.get("fandom"): m["char_fd"][(r["variant"], r["fandom"])] = r["canonical"]
        else: m["char"][r["variant"]] = r["canonical"]
    m["fan"] = ao3_pairs("universes.csv")
    curated_fan = {r["alias"]: r["canonical"] for r in both("fandoms.csv")}
    m["fan"].update(curated_fan)
    for a in m["fan"]:                           # flatten chains so a curated re-point of an ao3 master cascades
        t, seen = m["fan"][a], {a}
        while t in m["fan"] and t not in seen: seen.add(t); t = m["fan"][t]
        m["fan"][a] = t
    # fanvals feeds the misfiled-genre rescue — CURATED names only: the 22k AO3 universe names
    # include generic words (Empire, Hero, Kingdom) that would hijack ordinary genre values
    m["fanvals"] = {norm(v) for v in curated_fan.values()}
    # AO3 freeform folds route dynamically: allowlisted canonical -> genre, else tag; curated rows win
    tropes = {a: (c, "genre" if norm(c) in m["gallow"] else "tag") for a, c in ao3_pairs("tags.csv").items()}
    tropes.update({v: (cn, rt) for v, cn, rt in
        (read_tropes(os.path.join(DEF, "tropes.csv")) + read_tropes(os.path.join(odir, "tropes.csv")))})
    m["trope"] = resolve_trope_chains(tropes)
    m["fan_block"] = {norm(x) for x in read_lines(os.path.join(DEF, "fandom_blocklist.txt")) + read_lines(os.path.join(odir, "fandom_blocklist.txt")) if x and not x.startswith("#")}  # values that are never fandoms
    m["decompose"] = {}                          # one contextual value -> parts in several columns (e.g. "Fate SI" -> Type-Moon + SI/OC)
    for r in both("decompose.csv"):
        m["decompose"][norm(r["value"])] = {k: [x.strip() for x in (r.get(k) or "").split(";") if x.strip()]
                                             for k in ("fandoms", "characters", "tags", "genres")}
    m["gsplit"] = {r["combined"]: r["atoms"].split("|") for r in both("genres_split.csv")}
    m["gcanon"] = ao3_pairs("genres.csv")        # AO3 genre synonyms first; curated rows win
    m["gcanon"].update({r["variant"]: r["canonical"] for r in both("genres_canon.csv")})
    m["rating"] = {norm(x) for x in read_lines(os.path.join(DEF, "ratings.txt")) + read_lines(os.path.join(odir, "ratings.txt")) if x and not x.startswith("#")}
    m["junk_exact"], m["junk_rx"] = set(), []
    for ln in read_lines(os.path.join(DEF, "junk.txt")) + read_lines(os.path.join(odir, "junk.txt")):
        if not ln or ln.startswith("#"): continue
        if ln.startswith("re:"): m["junk_rx"].append(re.compile(ln[3:], re.I))
        else: m["junk_exact"].add(ln.strip().lower())
    # case/spacing-insensitive fallback for the fold maps: a book value that differs only in casing or
    # punctuation still matches. Raw keys stay and win; these add norm-keyed aliases (see _lookup). ponytail
    for mp in (m["char"], m["trope"]):
        for k, v in list(mp.items()): mp.setdefault(norm(k), v)
    for (vk, fd), c in list(m["char_fd"].items()): m["char_fd"].setdefault((norm(vk), norm(fd)), c)
    return m

def _lookup(mp: dict, key: str):
    """Fold-map lookup that tolerates casing/punctuation: exact key first, then its norm().
    Returns the map value (str for char/fandom, (canon, route) tuple for trope) or None."""
    return mp[key] if key in mp else mp.get(norm(key))

def is_junk(t: str, m: dict) -> bool:
    if t.strip().lower() in m["junk_exact"]: return True
    return any(rx.search(t) for rx in m["junk_rx"])

def build_tagcanon(spellings, m: dict) -> dict:
    """norm -> canonical spelling: most-common spelling per normalized form; bundled tropes canonical wins."""
    spell = collections.Counter(spellings)
    bynorm = collections.defaultdict(list)
    for t, ct in spell.items(): bynorm[norm(t)].append((ct, t))
    tc = {nm: max(lst)[1] for nm, lst in bynorm.items()}     # max by (count, spelling)
    for v, (cn, rt) in m["trope"].items():
        if rt == "tag": tc[norm(cn)] = cn
    return tc

# route for a trope, honoring config (au/crossover/etc. genre-vs-tag toggle)
def trope_route(canon: str, route: str, beh: dict) -> str:
    key = {"alternate universe": "au_as", "crossover": "crossover_as",
           "reincarnation": "reincarnation_as", "time travel": "time_travel_as"}.get(norm(canon))
    if key: return beh.get(key, route)
    return route

# ---------------- the transform (per book) ----------------
def transform(d: dict, m: dict, beh: dict, known_chars: frozenset | set = frozenset(),
              tagcanon: dict | None = None) -> tuple[dict, bool, bool]:
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
    nF = set(); relocatedF = False
    for f in F:
        tgt = m["fan"].get(f, f)
        if not tgt: continue                                  # alias -> empty: drop
        if norm(tgt) in m["fan_block"]:                       # a curated non-fandom (kink/rating/status/meta) -> tag pipeline routes it
            T.append(tgt); relocatedF = True; continue        # value preserved in tags -> not a fandom loss
        nF.add(tgt)
    nF |= {m["fan"].get(f, f) for f in seedF if m["fan"].get(f, f)}   # decomposed fandoms (skip alias->empty)
    # characters: fold abbrev/case -> full (global, then fandom-scoped)
    nC = set()
    for ch in C:
        if beh["fold_characters"]:
            ch = _lookup(m["char"], ch) or next(
                (m["char_fd"][k] for fd in nF for k in ((ch, fd), (norm(ch), norm(fd))) if k in m["char_fd"]), ch)
        nC.add(ch)
    nC |= seedC                                                # decomposed characters
    # genres: split -> canon -> allowlist(keep) else move to tags
    nG = set(); routed = set()
    ga = lambda na: na in m["gallow"] or any(na.startswith(x + " ") for x in m["gallow"] if len(x) >= 4)  # allowed genre?
    for g in G0:
        for atom in (m["gsplit"].get(g, [g])):
            a = m["gcanon"].get(atom, atom); na = norm(a)
            if ga(na):
                nG.add(a)                                       # allowlisted genre or a subtype of one (AU - Canon Divergence)
            elif na in m["fanvals"]: nF.add(a)                  # misfiled fandom
            elif na in known_chars: nC.add(a)                   # misfiled character (e.g. Akeno Himejima in #genres)
            else: routed.add(a)                                 # freeform -> through the tag pipeline below
    nG |= seedG                                                 # decomposed genres
    # tags: junk drop / trope route / surface-fold / ascii / redundancy-strip
    # (routed ex-genres go through the same pipeline, so they trope-fold/dedupe like any tag)
    nT = set(seedT)                                             # decomposed tags
    homes = {norm(x) for x in nF | nC | nG | R | (set(st) if isinstance(st, list) else {st} if st else set())}
    for t in sorted(set(T) | routed):
        if is_junk(t, m): continue
        tv = _lookup(m["trope"], t)
        if tv:
            canon, route = tv; route = trope_route(canon, route, beh)
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
    # SAFETY: a fanfic always has a fandom, so a book that had one and ends with an empty
    # #fandoms has lost it (a bad fandoms.csv alias->"" or an empty decompose payload) — UNLESS
    # its only "fandom" was a blocklisted non-fandom relocated to tags (value preserved, above).
    # Characters may legitimately be absent, so only flag a loss for books that had some.
    return newd, (had_F and not nF and not relocatedF), (had_C and not nC)

# ---------------- AUDIT (read-only sqlite) ----------------
def col_key_label(cfg: dict) -> dict:
    return {k: v for k, v in cfg["columns"].items() if v}     # col_key -> calibre label

def read_library(cfg: dict) -> tuple:
    """Read all configured columns per book via read-only sqlite. -> (cols, perbook, present, nb, allb).
    Always the whole library, deliberately: transform() needs global context (tagcanon majority
    spelling, known_chars), runs in seconds, and apply only writes books that actually changed —
    scoped selection (select.py) is for the expensive LLM pass, not this one."""
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

def audit(cfg: dict, m: dict) -> None:
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
    print("=" * 60); print("scourgify AUDIT (read-only, no changes)"); print("=" * 60)
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
        fc = [f"{v}→{c}" for v in sorted(before["characters"]) if (c := _lookup(m['char'], v)) and c != v]
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
        folds = [f"{v}→{tv[0]}" for v in sorted(before["tags"]) if (tv := _lookup(m['trope'], v)) and tv[0] != v]
        if drops: print(f"tags drop ({len(drops)}):{ex(drops)}")
        if folds: print(f"tags fold/route ({len(folds)}):{ex(folds)}")

# ---------------- APPLY (standalone: compute via sqlite, write via calibre-debug helper) ----------------
DETAIL_BOOKS = 10           # per-book diff lines shown in the apply preview before deferring to `audit`
TAG_SHRINK_FRACTION = 0.25  # mass-deletion guardrail: abort if tags shrink more than this fraction ...
TAG_SHRINK_FLOOR = 200      # ... AND lose more than this many assignments (named like SPEND_GATE/BACKUP_KEEP)

def tag_loss_guard(tags_before: int, tags_after: int, force: bool) -> None:
    """Abort on a suspicious mass-deletion of tags (e.g. an over-broad junk.txt regex).
    ponytail: heuristic ceiling — >25% shrink AND >200 assignments lost; --force overrides."""
    lost = tags_before - tags_after
    if tags_before and lost > max(TAG_SHRINK_FLOOR, int(tags_before * TAG_SHRINK_FRACTION)) and not force:
        raise SystemExit(f"ABORT: tags would shrink {tags_before} -> {tags_after} assignments (-{lost}). "
                         "Check junk.txt / overrides for an over-broad rule, or re-run with --force.")

def data_loss_guard(lost_fandom: int, lost_char: int, force: bool) -> None:
    """SAFETY: abort if any book would lose its LAST fandom or character (CLAUDE.md invariant).
    transform() reports these only for a real value dropping to zero — a blocklist-route to tags
    is preserved and not counted. --force overrides (for a deliberate bulk deletion), like tag_loss_guard."""
    if (lost_fandom or lost_char) and not force:
        raise SystemExit(f"ABORT: {lost_fandom} book(s) would lose their last fandom, {lost_char} their last "
                         "character. Check your fandoms.csv aliases / decompose overrides for a rule that "
                         "empties a book, or re-run with --force if the deletion is intentional.")

def apply_changes(cfg: dict, m: dict, do_write: bool, force: bool = False,
                  cli_hint: bool = True, detail: bool = True, step: bool = False) -> int:
    """-> number of distinct books that would change (the wizard uses it to auto-skip a clean library).
    detail=True prints per-book -removed/+added values for the first DETAIL_BOOKS changed books.
    step=True walks the per-book UNIQUE edits through ui.checklist() (rich+interactive only) and
    writes only the accepted subset; rejected edits are logged for `scourgify overrides`."""
    beh = cfg["behavior"]
    cols, perbook, present, nb, allb = read_library(cfg)
    known_chars = {norm(v) for bb in perbook for v in perbook[bb].get("characters", [])}
    tagcanon = build_tagcanon((t for bb in perbook for t in perbook[bb].get("tags", [])), m)
    changes = collections.defaultdict(dict); diffs = collections.defaultdict(dict)
    lostF = lostC = tagsB = tagsA = 0
    for b in allb:
        d = {k: perbook[b].get(k, []) for k in cols}
        nd, lf, lc = transform(d, m, beh, known_chars, tagcanon); lostF += lf; lostC += lc
        tagsB += len(d.get("tags", [])); tagsA += len(nd.get("tags", []))
        booknorms = None
        for k, lab in cols.items():
            if k in nd and tuple(sorted(nd[k])) != tuple(sorted(d.get(k, []))):
                changes[lab][b] = sorted(nd[k])
                old, new = set(d.get(k, [])), set(nd[k])
                if booknorms is None:   # after-state of every column, for "where did it go" annotations
                    booknorms = {l2: {norm(x) for x in nd.get(k2, d.get(k2, []))} for k2, l2 in cols.items()}
                gone = [(v, next((l2 for l2, ns in booknorms.items() if l2 != lab and norm(v) in ns), None))
                        for v in sorted(old - new)]
                diffs[b][lab] = (gone, sorted(new - old))
    print("APPLY" if do_write else "PRE-APPLY (no write)")
    for lab, ch in changes.items(): print(f"  {lab:14} books changed: {len(ch)}")
    if detail and diffs:
        _preview_report(m, diffs)
    print(f"  SAFETY losing last fandom: {lostF} | character: {lostC} | tag assignments: {tagsB} -> {tagsA}")
    data_loss_guard(lostF, lostC, force)
    tag_loss_guard(tagsB, tagsA, force)
    if step and diffs:
        from scourgify import ui
        if not ui.interactive():
            raise SystemExit("--step needs an interactive terminal (omit it for a bulk apply).")
        _, unique = _classify_edits(m, diffs)
        if unique:
            from scourgify.overrides import _step_walk   # lazy: breaks the wrangle<->overrides import cycle
            rejects = _step_walk(m, beh, cols, perbook, changes, unique, known_chars, tagcanon)
            if rejects:
                from scourgify.common import log_rejects, REJECTS
                log_rejects(rejects)
                nauto = sum(1 for r in rejects if r["class"] == "auto")
                print(f"  logged {len(rejects)} reject(s) -> {os.path.basename(REJECTS)}"
                      + (f"  ({nauto} → run `scourgify overrides` to stop them recurring)" if nauto else ""))
    if do_write:
        # pass force through: wrangle's own data_loss/tag_loss guards already ran, so a deliberately
        # --forced deletion here must not be second-guessed by run_writer's coarse last-line wipe guard.
        run_writer([{"op": "set_field", "field": lab, "values": {str(b): v for b, v in ch.items()}} for lab, ch in changes.items()], force=force)
    elif cli_hint:
        print("Re-run: scourgify apply --apply   (Calibre closed; writes shell out to calibre-debug)")
    return len({b for ch in changes.values() for b in ch})

MASS_MIN = 3             # a change on this many books is "mass" — aggregated, not listed per book

def _colmap(m: dict, lab: str, v: str) -> str | None:
    """Where would this column's engine fold v? (for pairing a removal with its rename target)"""
    if lab == "tags": return (_lookup(m["trope"], v) or (None,))[0]
    if "fandom" in lab: return m["fan"].get(v)
    if "character" in lab: return _lookup(m["char"], v)
    if "genre" in lab: return m["gcanon"].get(v)
    return None


def _classify_edits(m: dict, diffs: dict) -> tuple:
    """diffs -> (mass Counter{(kind, where, before, after): n_books}, unique {book: [(kind, where, before, after)]}).
    kind: 'rename' (fold within a column — incl. merging into an already-present canonical),
    'move' (crossed columns), 'drop' (gone), 'add' (appeared)."""
    per_book = {}
    for b, bylab in diffs.items():
        edits = []
        for lab, (rm, ad) in bylab.items():
            added = set(ad)
            bynorm = {norm(w): w for w in ad}
            for v, dest in rm:
                if dest:
                    edits.append(("move", f"{lab} → {dest}", v, "")); continue
                w = _colmap(m, lab, v) or bynorm.get(norm(v))   # engine fold, else a same-norm respelling
                if w:
                    edits.append(("rename", lab, v, w)); added.discard(w)
                else:
                    edits.append(("drop", lab, v, ""))
            moved_in = {norm(v) for l2, (rm2, _) in bylab.items() for v, dest in rm2 if dest == lab}
            for w in sorted(added):
                if norm(w) not in moved_in:                     # a move-in is already shown from its source side
                    edits.append(("add", lab, "", w))
        per_book[b] = edits
    mass = collections.Counter(e for edits in per_book.values() for e in set(edits))
    mass = {e: n for e, n in mass.items() if n >= MASS_MIN}
    unique = {b: [e for e in edits if e not in mass] for b, edits in per_book.items()}
    return mass, {b: es for b, es in unique.items() if es}


def _preview_report(m: dict, diffs: dict, top: int = 15, books: int = DETAIL_BOOKS) -> None:
    """The human-readable change report: aggregated mass folds + per-book unique changes.
    rich tables/tree when available; aligned plain text otherwise."""
    mass, unique = _classify_edits(m, diffs)
    def fmt(kind, where, before, after):
        return {"rename": (where, f"{before} → {after}"), "move": (where, before),
                "drop": (where, f"− {before} (dropped)"), "add": (where, f"+ {after}")}[kind]
    top_mass = sorted(mass.items(), key=lambda kv: -kv[1])[:top]
    rest = len(mass) - len(top_mass)
    ids = sorted(unique)[-books:]                               # highest ids = newest books
    con = ro_connect()
    titles = dict(con.execute(f"SELECT id, title FROM books WHERE id IN ({','.join('?' * len(ids))})", ids)) if ids else {}
    def grouped(edits):
        """[(kind, where, joined-values)] — one line per relation, values joined."""
        g = {}
        for kind, where, before, after in sorted(edits):
            g.setdefault((kind, where), []).append(
                {"rename": f"{before} → {after}", "move": before, "drop": before, "add": after}[kind])
        label = {"rename": "", "move": "", "drop": "dropped: ", "add": "added: "}
        return [(where if kind in ("rename", "move") else f"{label[kind]}{where}", " · ".join(vals))
                for (kind, where), vals in g.items()]
    if RICH:
        from rich.tree import Tree
        t = Table(title=f"mass folds — same change on {MASS_MIN}+ books", title_justify="left")
        t.add_column("books", justify="right", style="cyan"); t.add_column("where", style="dim"); t.add_column("change")
        for (kind, where, before, after), n in top_mass:
            w, c = fmt(kind, where, before, after); t.add_row(f"{n:,}", w, c)
        if rest > 0: t.add_row("…", "", f"+{rest} more mass folds (scourgify audit shows every value)")
        _con.print(t)
        if unique:
            tree = Tree(f"[bold]unique changes[/] — newest {len(ids)} of {len(unique):,} books")
            for b in reversed(ids):
                node = tree.add(f"[bold]#{b}[/]  {str(titles.get(b, ''))[:64]}")
                for w, vals in grouped(unique[b]):
                    node.add(f"[dim]{w:22}[/] {vals}")
            _con.print(tree)
    else:
        print(f"  mass folds (same change on {MASS_MIN}+ books):")
        for (kind, where, before, after), n in top_mass:
            w, c = fmt(kind, where, before, after); print(f"  {n:6,}x  {w:22} {c}")
        if rest > 0: print(f"          … +{rest} more mass folds")
        if unique:
            print(f"  unique changes (newest {len(ids)} of {len(unique):,} books):")
            for b in reversed(ids):
                print(f"  #{b}  {str(titles.get(b, ''))[:64]}")
                for w, vals in grouped(unique[b]):
                    print(f"      {w:22} {vals}")


def write_config(colmap: dict, beh: dict | None = None) -> None:
    b = beh or {}                                     # preserve existing toggles on re-run; defaults on first run
    bo = lambda k, d: "true" if b.get(k, d) else "false"
    sv = lambda k, d: b.get(k, d)
    L = ["# scourgify configuration (generated by `setup`; edit anytime).", "", "[columns]",
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
    open(os.path.join(os.getcwd(), "config.toml"), "w").write("\n".join(L))

OK, WARN, BAD = "✓", "⚠", "✗"     # status glyphs (plain; no color dependency)
def _interactive() -> bool:
    # interactive iff stdin AND stderr are TTYs and nothing forces otherwise (pattern from lintle's term.py):
    # prevents an invisible-prompt hang when output is piped/redirected or under CI / --yes.
    if os.environ.get("CI") or os.environ.get("NONINTERACTIVE") or "--yes" in sys.argv or "-y" in sys.argv:
        return False
    try: return sys.stdin.isatty() and sys.stderr.isatty()
    except Exception: return False
def _ask(prompt: str, default: bool = True) -> bool:
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

def setup(cfg: dict) -> None:
    import subprocess, shutil, json as _json
    print("=" * 64); print("  scourgify — setup & health check"); print("=" * 64)
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
    odir = os.path.join(os.getcwd(), cfg["overrides"].get("dir", "overrides"))
    print("\n[5] Overrides");  print(f"  {OK} {odir}" if os.path.isdir(odir) else f"  {WARN} no overrides/ dir (optional — add your own maps here; they win over defaults/)")

    if ops:
        print(f"\n[6] Applying {len(ops)} change(s) to Calibre (via calibre-debug)")
        run_writer(ops)
    print("\n" + "-" * 64)
    print("Setup complete. Next:")
    print("  scourgify audit          # read-only dry-run of all passes")
    print("  scourgify apply --apply  # write changes (Calibre closed; backs up first)")
    print("  scourgify classify --incremental # content-tag new/updated books (cheap)")

# ---------------- main ----------------
def main() -> None:
    import argparse
    p = argparse.ArgumentParser(prog="scourgify",
                                description="Normalize a FanFicFare-imported Calibre library (writes auto-shell to calibre-debug). "
                                            "With no command: launch the interactive wizard.")
    p.add_argument("command", nargs="?", default=None, choices=["setup", "audit", "apply"],
                   help="setup: interactive health check + configure | audit: read-only dry-run | apply: write changes | (none): wizard")
    p.add_argument("--apply", action="store_true", help="with `apply`: actually write (Calibre closed)")
    p.add_argument("--step", action="store_true", help="with `apply`: review each book's unique changes 1-by-1 (interactive)")
    p.add_argument("--force", action="store_true", help="override the tag mass-deletion guardrail")
    p.add_argument("--yes", "-y", action="store_true", help="non-interactive: take the recommended default for every prompt")
    a = p.parse_args()
    if a.command is None:
        if sys.stdin.isatty() and sys.stdout.isatty():
            from scourgify import wizard   # lazy: keeps rich fully optional for the plain subcommands
            wizard.run()
        else:
            p.print_help()
        sys.exit(0)
    library()                      # fail fast with a clear message before doing any work
    cfg = load_config(); maps = load_maps(cfg)
    if a.command == "audit": audit(cfg, maps)
    elif a.command == "apply": apply_changes(cfg, maps, a.apply or a.step, a.force, step=a.step)
    elif a.command == "setup": setup(cfg)


if __name__ == "__main__":
    main()
