#!/usr/bin/env python3
"""scourgify — normalize a FanFicFare-imported Calibre library from generic defaults + config.

Set CALIBRE_LIBRARY to your library folder first, then (everything runs under plain python3;
writes shell out to calibre-debug automatically):
  scourgify setup            # interactive health check + first-run wizard
  scourgify audit            # read-only dry-run report of every pass
  scourgify apply --apply    # write changes  (Calibre must be CLOSED)
"""
import os, sys, re, csv, collections
from scourgify import report
from scourgify.artifacts import read_rows as read_csv     # the ONE "DictReader or []" reader
from scourgify.common import (DEFAULTS as DEFAULTS_DIR, user_dir, norm, ascii_fold, load_config, library,
                              read_lines, ro_connect, read_custom_column, run_writer,
                              titles as book_titles, op_set_field)

# ---------------- defaults + overrides ----------------
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

def load_maps(cfg: dict, defaults_dir: str | None = None, overrides_dir: str | None = None) -> dict:
    """Build the in-memory fold maps from the data layers (ao3 ← defaults ← overrides, later wins).
    The dirs are parameters with production defaults so tests pass temp layers instead of
    reassigning module globals."""
    DEF = defaults_dir or DEFAULTS_DIR
    odir = overrides_dir or os.path.join(user_dir(), cfg["overrides"].get("dir", "overrides"))
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

def genre_allowed(na: str, m: dict) -> bool:
    """Is this normalized value an allowlisted genre — or a subtype of one, like
    'AU - Canon Divergence'? The ONE predicate; transform and every report share it."""
    return na in m["gallow"] or any(na.startswith(x + " ") for x in m["gallow"] if len(x) >= 4)

# ---------------- the transform (per book) ----------------
def transform(d: dict, m: dict, beh: dict, known_chars: frozenset | set = frozenset(),
              tagcanon: dict | None = None, log: list | None = None) -> tuple[dict, bool, bool]:
    """d: dict col_key -> list[str] for configured columns. Returns (newd, lost_fandom, lost_char).
    known_chars: normalized set of character names in the library (to rescue chars misfiled in #genres).
    tagcanon: norm -> canonical spelling map for generic normalize-merge of tag variants.
    log: optional list — transform APPENDS its per-value decisions (kind, where, before, after)
    so reports can explain what changed from the engine's OWN choices instead of re-deriving
    (and silently desyncing from) the rules. Kinds: canon/fold/split/move/drop/decompose."""
    note = log.append if log is not None else (lambda e: None)
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
                if p:
                    seedF.update(p["fandoms"]); seedC.update(p["characters"]); seedG.update(p["genres"]); seedT.update(p["tags"])
                    note(("decompose", "", v, ", ".join(c + "=" + "/".join(p[c]) for c in ("fandoms", "characters", "tags", "genres") if p[c])))
                else: keep.append(v)
            return keep
        F = set(_dec(F)); G0 = _dec(G0); T = _dec(T)
    # fandoms: alias -> canonical (drop if mapped to empty)
    nF = set(); relocatedF = False
    for f in F:
        tgt = m["fan"].get(f, f)
        if not tgt: note(("drop", "fandoms", f, "")); continue      # alias -> empty: drop
        if norm(tgt) in m["fan_block"]:                       # a curated non-fandom (kink/rating/status/meta) -> tag pipeline routes it
            T.append(tgt); relocatedF = True                  # value preserved in tags -> not a fandom loss
            note(("move", "fandoms → tags", tgt, "")); continue
        nF.add(tgt)
        if tgt != f: note(("canon", "fandoms", f, tgt))
    nF |= {m["fan"].get(f, f) for f in seedF if m["fan"].get(f, f)}   # decomposed fandoms (skip alias->empty)
    # characters: fold abbrev/case -> full (global, then fandom-scoped)
    nC = set()
    for ch in C:
        if beh["fold_characters"]:
            folded = _lookup(m["char"], ch) or next(
                (m["char_fd"][k] for fd in nF for k in ((ch, fd), (norm(ch), norm(fd))) if k in m["char_fd"]), ch)
            if folded != ch: note(("fold", "characters", ch, folded))
            ch = folded
        nC.add(ch)
    nC |= seedC                                                # decomposed characters
    # genres: split -> canon -> allowlist(keep) else move to tags
    nG = set(); routed = set()
    for g in G0:
        atoms = m["gsplit"].get(g, [g])
        if g in m["gsplit"]: note(("split", "genres", g, "|".join(atoms)))
        for atom in atoms:
            a = m["gcanon"].get(atom, atom); na = norm(a)
            if a != atom: note(("canon", "genres", atom, a))
            if genre_allowed(na, m):
                nG.add(a)                                       # allowlisted genre or a subtype of one (AU - Canon Divergence)
            elif na in m["fanvals"]: nF.add(a); note(("move", "genres → fandoms", a, ""))    # misfiled fandom
            elif na in known_chars: nC.add(a); note(("move", "genres → characters", a, ""))  # misfiled character
            else: routed.add(a); note(("move", "genres → tags", a, ""))   # freeform -> through the tag pipeline below
    nG |= seedG                                                 # decomposed genres
    # tags: junk drop / trope route / surface-fold / ascii / redundancy-strip
    # (routed ex-genres go through the same pipeline, so they trope-fold/dedupe like any tag)
    nT = set(seedT)                                             # decomposed tags
    homes = {norm(x) for x in nF | nC | nG | R | (set(st) if isinstance(st, list) else {st} if st else set())}
    for t in sorted(set(T) | routed):
        if is_junk(t, m): note(("drop", "tags", t, "")); continue
        tv = _lookup(m["trope"], t)
        if tv:
            canon, route = tv; route = trope_route(canon, route, beh)
            if canon != t: note(("fold", "tags", t, canon))
            if route == "genre": (nG if genre_allowed(norm(canon), m) else nT).add(canon)   # genre only if allowlisted, else tag (keeps #genres idempotent)
            elif route == "fandom": nF.add(m["fan"].get(canon, canon))
            elif route == "character": nC.add(canon)
            elif beh.get("tropes_as") == "genre" and norm(canon) not in m["rating"]: (nG if genre_allowed(norm(canon), m) else nT).add(canon)
            else: nT.add(canon)                       # tag fold
            continue
        if norm(t) in known_chars: nC.add(t); note(("move", "tags → characters", t, "")); continue  # a known character -> #characters
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
    """Read-only dry-run report of every pass. Plans once, then reports from the plan —
    the examples come from transform's OWN decision log, never a re-derivation of the rules."""
    plan(cfg, m).audit_report()

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

class Plan:
    """ONE full-library transform pass, computed once — the deep module behind wrangle's whole
    write path. The audit report, the apply preview, the SAFETY guards, the 1-by-1 step review,
    and the write itself all read this object; nothing recomputes. Drive it as:
        p = plan(cfg, maps); p.preview(); p.guard(); [p.step()]; p.write()
    """

    def __init__(self, cfg: dict, m: dict):
        self.cfg, self.m, self.beh = cfg, m, cfg["behavior"]
        self.cols, self.perbook, self.present, self.nb, allb = read_library(cfg)
        self.known_chars = {norm(v) for bb in self.perbook for v in self.perbook[bb].get("characters", [])}
        self.tagcanon = build_tagcanon((t for bb in self.perbook for t in self.perbook[bb].get("tags", [])), self.m)
        self.changes = collections.defaultdict(dict)   # {label: {book: new sorted values}} — what write() sends
        self.diffs = collections.defaultdict(dict)     # {book: {label: (gone, added)}} — what previews show
        self.before = {k: set() for k in self.cols}; self.after = {k: set() for k in self.cols}
        self.decisions = []                            # transform's own (kind, where, before, after) log
        self.lostF = self.lostC = self.tagsB = self.tagsA = 0
        for b in allb:
            d = {k: self.perbook[b].get(k, []) for k in self.cols}
            for k in self.cols: self.before[k].update(d.get(k, []))
            nd, lf, lc = transform(d, self.m, self.beh, self.known_chars, self.tagcanon, log=self.decisions)
            self.lostF += lf; self.lostC += lc
            self.tagsB += len(d.get("tags", [])); self.tagsA += len(nd.get("tags", []))
            booknorms = None
            for k, lab in self.cols.items():
                if k in nd: self.after[k].update(nd[k])
                if k in nd and tuple(sorted(nd[k])) != tuple(sorted(d.get(k, []))):
                    self.changes[lab][b] = sorted(nd[k])
                    old, new = set(d.get(k, [])), set(nd[k])
                    if booknorms is None:   # after-state of every column, for "where did it go" annotations
                        booknorms = {l2: {norm(x) for x in nd.get(k2, d.get(k2, []))} for k2, l2 in self.cols.items()}
                    gone = [(v, next((l2 for l2, ns in booknorms.items() if l2 != lab and norm(v) in ns), None))
                            for v in sorted(old - new)]
                    self.diffs[b][lab] = (gone, sorted(new - old))

    @property
    def n_books(self) -> int:
        """Distinct books that would change (the wizard auto-skips a clean library on 0)."""
        return len({b for ch in self.changes.values() for b in ch})

    def preview(self, detail: bool = True, write: bool = False) -> None:
        """Per-column changed counts (+ the mass/unique detail with detail=True) + the SAFETY line."""
        print("APPLY" if write else "PRE-APPLY (no write)")
        for lab, ch in self.changes.items(): print(f"  {lab:14} books changed: {len(ch)}")
        if detail and self.diffs:
            _preview_report(self.m, self.diffs)
        print(f"  SAFETY losing last fandom: {self.lostF} | character: {self.lostC} | "
              f"tag assignments: {self.tagsB} -> {self.tagsA}")

    def guard(self, force: bool = False) -> None:
        """The semantic SAFETY guards (SystemExit on a data-loss shaped change-set)."""
        data_loss_guard(self.lostF, self.lostC, force)
        tag_loss_guard(self.tagsB, self.tagsA, force)

    def step(self) -> None:
        """1-by-1 review of the per-book UNIQUE edits (ui.checklist); rejected edits are removed
        from this plan's changes and logged for `scourgify overrides`."""
        from scourgify import ui
        if not ui.interactive():
            raise SystemExit("--step needs an interactive terminal (omit it for a bulk apply).")
        _, unique = _classify_edits(self.m, self.diffs)
        if not unique: return
        from scourgify.overrides import _step_walk   # lazy: breaks the wrangle<->overrides import cycle
        rejects = _step_walk(self.m, self.beh, self.cols, self.perbook, self.changes, unique,
                             self.known_chars, self.tagcanon)
        if rejects:
            from scourgify.common import log_rejects, REJECTS
            log_rejects(rejects)
            nauto = sum(1 for r in rejects if r["class"] == "auto")
            print(f"  logged {len(rejects)} reject(s) -> {os.path.basename(REJECTS)}"
                  + (f"  ({nauto} → run `scourgify overrides` to stop them recurring)" if nauto else ""))

    def write(self, force: bool = False) -> None:
        # pass force through: the plan's own data_loss/tag_loss guards already ran, so a deliberately
        # --forced deletion here must not be second-guessed by run_writer's coarse last-line wipe guard.
        run_writer([op_set_field(lab, ch) for lab, ch in self.changes.items()], force=force)

    def audit_report(self) -> None:
        """The full `scourgify audit` output: distinct-value deltas, SAFETY, and per-rule examples
        read straight from transform's decision log."""
        print("=" * 60); print("scourgify AUDIT (read-only, no changes)"); print("=" * 60)
        print(f"books: {self.nb}   columns active: "
              f"{', '.join(f'{k}->{v}' for k, v in self.cols.items() if self.present.get(k))}")
        miss = [k for k in self.cols if not self.present.get(k)]
        if miss: print(f"MISSING columns (run `setup`): {miss}")
        rows = []
        for k in self.cols:
            if not self.present.get(k): continue
            b, a = len(self.before[k]), len(self.after[k]); delta = a - b
            rows.append([k, str(b), str(a),
                         (str(delta), "red") if delta < 0 else (f"+{delta}", "green") if delta > 0 else "0"])
        report.table("proposed changes (distinct values per column)",
                     ["column", "before", "after", "delta"], rows, right=(1, 2, 3))
        safe_ok = self.lostF == self.lostC == 0
        report.say(f"\nSAFETY  losing last fandom: {self.lostF}   losing last character: {self.lostC}   "
                   f"tag assignments: {self.tagsB} -> {self.tagsA}   "
                   + ("✓ no data loss" if safe_ok else "⚠ review the losses above before apply"),
                   "green" if safe_ok else "red")
        # concrete examples — which rules actually fired on THIS library's values, from the
        # transform's own decision log (one mechanism; a rule change can't desync this report)
        def ex(items, n=10):
            return "  " + (", ".join(items[:n]) + (f"  …(+{len(items) - n} more)" if len(items) > n else "")) if items else ""
        def show(label, items):
            items = sorted(items)
            if items: print(f"{label} ({len(items)}):{ex(items)}")
        d = set(self.decisions)
        print("\n--- examples of what would change (from the engine's own decisions) ---")
        show("characters fold", [f"{b}→{a}" for k, w, b, a in d if (k, w) == ("fold", "characters")])
        show("fandoms canon", [f"{b}→{a}" for k, w, b, a in d if (k, w) == ("canon", "fandoms")]
             + [f"{b}→DROP" for k, w, b, a in d if (k, w) == ("drop", "fandoms")])
        show("fandoms → tags (blocklisted non-fandoms)", [b for k, w, b, a in d if (k, w) == ("move", "fandoms → tags")])
        show("genres split/canon", [f"{b}→{a}" for k, w, b, a in d if k in ("split", "canon") and w == "genres"])
        show("genres → fandoms", [b for k, w, b, a in d if (k, w) == ("move", "genres → fandoms")])
        show("genres → characters", [b for k, w, b, a in d if (k, w) == ("move", "genres → characters")])
        show("genres → tags (not in allowlist)", [b for k, w, b, a in d if (k, w) == ("move", "genres → tags")])
        show("decompose", [f"{b} → {a}" for k, w, b, a in d if k == "decompose"])
        show("tags drop", [b for k, w, b, a in d if (k, w) == ("drop", "tags")])
        show("tags fold/route", [f"{b}→{a}" for k, w, b, a in d if (k, w) == ("fold", "tags")])
        show("tags → characters", [b for k, w, b, a in d if (k, w) == ("move", "tags → characters")])


def plan(cfg: dict, m: dict) -> Plan:
    """Compute the full-library Plan once; report/step/write all read it (see Plan)."""
    return Plan(cfg, m)

MASS_MIN = 3             # a change on this many books is "mass" — aggregated, not listed per book

def _char_fd(m: dict, v: str, cands: dict) -> str | None:
    """Fandom-scoped character fold. The diff doesn't carry the book's fandoms, so match on the variant
    alone and prefer a target the book actually gained (ambiguous only if two fandoms fold v differently)."""
    hits = [c for (vk, _), c in m["char_fd"].items() if vk == v or vk == norm(v)]
    return next((c for c in hits if norm(c) in cands), hits[0] if hits else None)


def _colmap(m: dict, lab: str, v: str, cands: dict | None = None) -> str | None:
    """Where would this column's engine fold v? (for pairing a removal with its rename target)
    Mirrors transform's order — junk is dropped BEFORE the trope lookup, characters fall back to
    the fandom-scoped map — so the checklist labels an edit the same way the engine performed it."""
    if lab == "tags":
        if is_junk(v, m): return None
        return (_lookup(m["trope"], v) or (None,))[0]
    if "fandom" in lab: return m["fan"].get(v)
    if "character" in lab: return _lookup(m["char"], v) or _char_fd(m, v, cands or {})
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
                w = _colmap(m, lab, v, bynorm) or bynorm.get(norm(v))   # engine fold, else a same-norm respelling
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
    Rendering (rich-or-plain) is report.py's problem, not this module's."""
    mass, unique = _classify_edits(m, diffs)
    def fmt(kind, where, before, after):
        return {"rename": (where, f"{before} → {after}"), "move": (where, before),
                "drop": (where, f"− {before} (dropped)"), "add": (where, f"+ {after}")}[kind]
    top_mass = sorted(mass.items(), key=lambda kv: -kv[1])[:top]
    rest = len(mass) - len(top_mass)
    ids = sorted(unique)[-books:]                               # highest ids = newest books
    titles = book_titles(ro_connect(), ids) if ids else {}
    def grouped(edits):
        """[(kind, where, joined-values)] — one line per relation, values joined."""
        g = {}
        for kind, where, before, after in sorted(edits):
            g.setdefault((kind, where), []).append(
                {"rename": f"{before} → {after}", "move": before, "drop": before, "add": after}[kind])
        label = {"rename": "", "move": "", "drop": "dropped: ", "add": "added: "}
        return [(where if kind in ("rename", "move") else f"{label[kind]}{where}", " · ".join(vals))
                for (kind, where), vals in g.items()]
    rows = []
    for (kind, where, before, after), n in top_mass:
        w, c = fmt(kind, where, before, after)
        rows.append([(f"{n:,}", "cyan"), (w, "dim"), c])
    if rest > 0: rows.append(["…", "", f"+{rest} more mass folds (scourgify audit shows every value)"])
    report.table(f"mass folds — same change on {MASS_MIN}+ books", ["books", "where", "change"], rows, right=(0,))
    if unique:
        nodes = [(f"#{b}  {str(titles.get(b, ''))[:64]}",
                  [(f"{w:22} {vals}") for w, vals in grouped(unique[b])])
                 for b in reversed(ids)]
        report.tree(f"unique changes — newest {len(ids)} of {len(unique):,} books", nodes)


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
        from scourgify.common import interactive
        if interactive():
            from scourgify import wizard   # lazy: keeps rich fully optional for the plain subcommands
            wizard.run()
        else:
            p.print_help()
        sys.exit(0)
    library()                      # fail fast with a clear message before doing any work
    if a.command == "setup":
        from scourgify import setup as setup_mod   # setup lives in its own module (no normalization concepts)
        setup_mod.setup(load_config(), yes=a.yes)
        return
    cfg = load_config(); maps = load_maps(cfg)
    if a.command == "audit":
        audit(cfg, maps)
    elif a.command == "apply":
        do_write = a.apply or a.step
        p = plan(cfg, maps)                        # ONE compute: preview, guards, step, and write all read it
        p.preview(write=do_write)
        p.guard(a.force)
        if a.step: p.step()
        if do_write: p.write(a.force)
        else: print("Re-run: scourgify apply --apply   (Calibre closed; writes shell out to calibre-debug)")


if __name__ == "__main__":
    main()
