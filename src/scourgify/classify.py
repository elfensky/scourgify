#!/usr/bin/env python3
"""Content-based tagging from a controlled vocabulary, with TWO outputs per book:
  1) added_tags    — tags chosen from defaults/classify_vocab.txt (the consolidated set); these get APPLIED.
  2) proposed_new  — short reusable tags the model thinks apply but are NOT in the vocab yet; aggregated into
                     classify_newtags_ranked.csv for review, so the vocabulary grows cleanly (promote -> vocab).

  scourgify classify [--engine apple|claude|openai|gemini] [--incremental] [--workers N] [--batch N] [--fresh]
  scourgify classify --apply                    # apply 'added_tags' + stamp #wrangled (Calibre CLOSED; writes shell to calibre-debug)

Engines (--engine):  apple = on-device Apple Foundation Models via ./afm (free; macOS 26+).
          claude = Anthropic (ANTHROPIC_API_KEY) | openai = OpenAI (OPENAI_API_KEY) | gemini = Google (GEMINI_API_KEY) | mistral = Mistral (MISTRAL_API_KEY).
          --model overrides the per-engine default. Only books with < --min-tags tags AND a description are processed.
          Runs are resumable (skip books already in the proposal; --fresh restarts). Dry-run until --apply.
--incremental = cheap maintenance after new downloads: (re)process ONLY new/changed books — never classified,
          #updated newer than their own #wrangled marker, or re-fetched (added-date newer). --last N / --since DATE
          instead select by added/updated date ("update the last 30 books"). Scoped runs select exactly their books;
          the sparse-book default (< --min-tags) applies only when no scope flag is given. --apply auto-creates the
          #wrangled datetime column and stamps EVERY processed book, so the state lives IN the library — no external
          file. Selection semantics live in select.py (shared with the wizard header)."""
import argparse, os, re, csv, json, subprocess, collections, time, difflib
from concurrent.futures import ThreadPoolExecutor, as_completed
from scourgify import select
from scourgify.common import (HERE, DATA, user_dir, ro_connect, custom_column_id, run_writer, library,
                              current_tags, op_create_column, op_set_field, op_stamp_now,
                              interactive as _interactive, confirm as _confirm)
from scourgify.artifacts import (PROP, RANK, FAIL, PROP_COLS,           # artifact formats live in artifacts.py;
                                 read_proposal, write_proposal, write_ranked, archive)
# the engine seam lives in engines.py; re-exported here so `classify.ENGINES` / `classify.ask_retry`
# stay valid for promote, the wizard, and existing tests
from scourgify.engines import ENGINES, ENGINE_ENV, PRICING, ERR_TRUNC, usable_engines, ask_retry
try:                                              # rich is optional: live dashboard/tables in system python3
    from rich.console import Console, Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.progress import Progress, BarColumn, TextColumn, MofNCompleteColumn, TimeRemainingColumn
    from rich.table import Table
    from rich.text import Text
    _con = Console(stderr=True); RICH = True
except ImportError:
    RICH = False

AO3_VOCAB = f"{DATA}/ao3_vocab.csv"     # per-library canonical AO3 freeforms (name,uses); absent on fresh installs
SPEND_GATE = 200        # cloud runs above this many books require an explicit yes
DEDUP_CUTOFF = 0.86     # difflib ratio at/above which a proposed tag counts as a variant of an existing one

_VOCAB = None
def _read_vocab_file(path: str) -> list:
    return [l.strip() for l in open(path) if l.strip() and not l.startswith("#")] if os.path.exists(path) else []

def load_vocab() -> list:
    """Curated core ∪ AO3 high-frequency seed, then optional CWD overrides/classify_vocab.txt (a line appends
    a term; '-term' removes one — and can trim a seeded term too). Lazy so a packaging problem gives a real
    error at use, not at import, and installed users can override. See build_classify_seed.py for the seed."""
    global _VOCAB
    if _VOCAB is None:
        terms, have = [], set()                                                   # curated core first, then AO3 seed;
        for t in (_read_vocab_file(f"{HERE}/defaults/classify_vocab.txt")          # first spelling of a norm wins,
                  + _read_vocab_file(f"{HERE}/defaults/classify_vocab_ao3.txt")):  # so a hand-edit dup can't sneak in
            if t.lower() not in have: terms.append(t); have.add(t.lower())
        ov = os.path.join(user_dir(), "overrides", "classify_vocab.txt")
        if os.path.exists(ov):
            for l in open(ov):
                l = l.strip()
                if not l or l.startswith("#"): continue
                if l.startswith("-"): terms = [t for t in terms if t.lower() != l[1:].strip().lower()]
                elif l.lower() not in {t.lower() for t in terms}: terms.append(l)
        _VOCAB = terms
    return _VOCAB

_ALIASES = None
def load_aliases() -> dict:
    """candidate -> target snaps from overrides/promote_aliases.csv (written by `scourgify promote --apply`),
    so tags we've decided are synonyms stop getting re-proposed as 'new'. {} if absent."""
    global _ALIASES
    if _ALIASES is None:
        p = os.path.join(user_dir(), "overrides", "promote_aliases.csv")
        _ALIASES = {}
        if os.path.exists(p):
            for r in csv.DictReader(open(p)):
                if r.get("candidate") and r.get("target"):
                    _ALIASES[r["candidate"].strip().lower()] = r["target"].strip()
    return _ALIASES

_AO3 = None
def load_ao3_vocab() -> list:
    """The per-library AO3 canonical freeforms (data/ao3_vocab.csv 'name' column, built by ao3_import.py).
    Absent on a fresh install — degrade to [] silently; it's an extra reference layer, not a requirement."""
    global _AO3
    if _AO3 is None:
        try:
            _AO3 = [r["name"] for r in csv.DictReader(open(AO3_VOCAB)) if r.get("name", "").strip()]
        except OSError:
            _AO3 = []
    return _AO3

def existing_terms() -> list:
    """The reference a proposed-new tag is checked against: curated vocab ∪ ao3_vocab.csv, deduped
    case-insensitively with the curated spelling winning on collision (~1,450 terms — trivial for difflib)."""
    seen, out = set(), []
    for t in load_vocab() + load_ao3_vocab():
        if t.lower() not in seen:
            seen.add(t.lower()); out.append(t)
    return out

def est_cost(n_books: int, engine: str) -> float:
    """Rough list-price $ estimate for a run: input ≈ prompt chars/4 tokens, output ≈ 80 tokens/book."""
    i, o = PRICING.get(engine, (0.0, 0.0))
    tokens_in = (len(", ".join(load_vocab())) + 1900) / 4      # vocab + 1500-char description + instructions
    return n_books * (tokens_in * i + 80 * o) / 1e6


def prompt_for(desc: str, maxtags: int) -> str:
    return ("You are tagging a fanfiction story. Return ONLY a JSON object with two arrays:\n"
            f'  "tags": tags from the CONTROLLED LIST below that clearly apply (exact spelling, at most {maxtags}; '
            "be conservative; [] if vague; do NOT echo the whole list).\n"
            '  "new": up to 3 SHORT reusable trope/genre/theme tags (Title Case) that clearly apply but are NOT in the '
            "list and would be worth adding to the vocabulary. No plot specifics, character names, or fandoms; [] if none.\n"
            f"CONTROLLED LIST: {', '.join(load_vocab())}\n\nDESCRIPTION:\n{desc[:1500]}\n\nJSON:")

def parse_resp(text: str, maxtags: int = 6, cutoff: float = DEDUP_CUTOFF) -> tuple[list[str], list[str]]:
    m = re.search(r"\{.*\}", text, re.S)
    if not m: return [], []
    try: obj = json.loads(m.group(0))
    except Exception: return [], []
    vlow = {v.lower(): v for v in load_vocab()}
    vkeys = list(vlow)                          # lowercased vocab, for the fuzzy near-miss snap below
    vt = [vlow[str(t).strip().lower()] for t in (obj.get("tags") or []) if str(t).strip().lower() in vlow]
    if len(vt) > maxtags * 2: vt = []          # model echoed the list, not selecting
    def keep(canon):
        if canon not in vt: vt.append(canon)   # snapped/exact hit -> apply the canonical vocab spelling
    nt, seen = [], set()
    for t in (obj.get("new") or []):        # `or []`: a model may emit "tags"/"new": null — None isn't iterable
        t = str(t).strip(); tl = t.lower()
        # first char must be alphanumeric: also blocks =/+/-/@ spreadsheet-formula injection in the review CSVs
        if not (t and t[0].isalnum() and 1 < len(t) <= 40): continue
        if tl in vlow: keep(vlow[tl]); continue                     # already a vocab term the model mislabeled "new"
        al = load_aliases().get(tl)
        if al is not None:                          # a decided synonym: snap to vocab, else drop
            if al.lower() in vlow: keep(vlow[al.lower()])
            continue
        if tl in seen: continue
        near = difflib.get_close_matches(tl, vkeys, n=1, cutoff=cutoff)
        if near: keep(vlow[near[0]])           # ponytail: fuzzy snap can mis-map look-alikes; --dedup-cutoff tunes it
        else: seen.add(tl); nt.append(t)
    return vt[:maxtags], nt[:3]


def annotate_new(ranked, cutoff: float = DEDUP_CUTOFF, existing: list | None = None) -> list:
    """Smart review rows for the proposed-new tags: for each, its nearest existing tag
    (curated vocab ∪ ao3_vocab.csv) + similarity + verdict. Genuinely-new first (by count), near-dupes last.
    Pure (pass `existing` in tests) — this is the once-per-run matching against the full reference."""
    existing = existing_terms() if existing is None else existing
    elow = {e.lower(): e for e in existing}
    keys = list(elow)
    rows = []
    for tag, cnt in ranked.most_common():
        near = difflib.get_close_matches(tag.lower(), keys, n=1, cutoff=0.0)
        if near:
            nearest = elow[near[0]]
            sim = round(difflib.SequenceMatcher(None, tag.lower(), near[0]).ratio(), 2)
        else:
            nearest, sim = "", 0.0
        verdict = "near-duplicate" if sim >= cutoff else "new"
        rows.append([tag, cnt, nearest, sim, verdict])
    rows.sort(key=lambda r: (r[4] != "new", -r[1]))    # new first, then by descending count
    return rows


# ---- live run display ----
def sparkline(vals: list, width: int = 28) -> str:
    """Unicode sparkline of a numeric series (last `width` points), scaled to its max."""
    vals = [v for v in vals][-width:]
    if not vals: return ""
    blocks = "▁▂▃▄▅▆▇█"
    hi = max(vals)
    if hi <= 0: return blocks[0] * len(vals)
    return "".join(blocks[min(7, int(v * 8 / hi))] for v in vals)

class _Dashboard:
    """Live display for a classify run: progress bar, running numbers (tagged / failed /
    no-match / rate), a throughput sparkline, and the rising new-tag candidates.
    rich renders it live; without rich it degrades to a checkpoint line every 25 books."""
    BUCKET = 5.0                                   # seconds per throughput bucket

    def __init__(self, todo_n, done_before, targets_n):
        self.total, self.done_before, self.targets = todo_n, done_before, targets_n
        self.n = self.tagged = self.fails = 0
        self.newtags = collections.Counter()
        self.t0 = time.monotonic(); self.hist = [0]
        self.live = self.prog = self.task = None

    def __enter__(self):
        if RICH and self.total:
            self.prog = Progress(TextColumn("[cyan]classifying"), BarColumn(bar_width=None),
                                 MofNCompleteColumn(), TimeRemainingColumn(), console=_con)
            self.task = self.prog.add_task("", total=self.total)
            self.live = Live(self._render(), console=_con, refresh_per_second=4)
            self.live.__enter__()
        return self

    def __exit__(self, *exc):
        if self.live: self.live.__exit__(*exc)
        return False

    def update(self, vt, nt, err):
        self.n += 1
        if err: self.fails += 1
        elif vt: self.tagged += 1
        self.newtags.update(nt)
        b = int((time.monotonic() - self.t0) // self.BUCKET)
        while len(self.hist) <= b: self.hist.append(0)
        self.hist[b] += 1
        if self.live:
            self.prog.update(self.task, advance=1)
            self.live.update(self._render())
        elif self.n % 25 == 0:
            el = time.monotonic() - self.t0
            print(f"  +{self.n}/{self.total} … {self.tagged} tagged, {self.fails} failed, {self.n / el:.1f}/s")

    def _render(self):
        el = time.monotonic() - self.t0
        rate = self.n / el if el > 1 else 0.0
        g = Table.grid(padding=(0, 2))
        g.add_row("[bold]this run[/]", f"{self.n}/{self.total}",
                  "[green]tagged[/]", str(self.tagged),
                  "[red]failed[/]", str(self.fails),
                  "[dim]no match[/]", str(max(0, self.n - self.tagged - self.fails)),
                  "[bold]rate[/]", f"{rate:.1f}/s")
        parts = [self.prog, g]
        spark = sparkline(self.hist)
        if spark: parts.append(Text.assemble(("throughput  ", "bold"), (spark, "cyan")))
        if self.newtags:
            top = " · ".join(f"{t} ×{c}" for t, c in self.newtags.most_common(5))
            parts.append(Text.assemble(("rising candidates  ", "bold"), (top, "magenta")))
        return Panel(Group(*parts), border_style="cyan", padding=(0, 1),
                     title=f"classify — {self.done_before + self.n}/{self.targets} total")


# ---- apply: 'added_tags' + stamp #wrangled — standalone, no LLM calls ----
def apply_proposal() -> None:
    if not os.path.exists(PROP):
        raise SystemExit(f"no proposal to apply ({os.path.basename(PROP)} not found — run a classify pass first).")
    con = ro_connect()
    cur = current_tags(con)
    have_wrangled = custom_column_id(con, "wrangled") is not None
    chg, processed = {}, []
    for r in read_proposal():
        b = r["book_id"]; processed.append(b)
        if r["added_tags"]: chg[b] = sorted(cur.get(b, set()) | set(r["added_tags"]))   # union with current tags
    ops = []
    if not have_wrangled:                                             # first run: create + backfill whole library as wrangled-now
        ops.append(op_create_column("wrangled", "Wrangled", "datetime"))
        ops.append(op_stamp_now("#wrangled"))
    ops.append(op_set_field("tags", chg))
    # stamp EVERY processed book, tagged or not — an unstamped no-tag book would be re-sent to the LLM forever
    ops.append(op_stamp_now("#wrangled", processed))
    run_writer(ops)
    # archive so a later --apply can't re-add tags you've since hand-removed (stale rows never re-apply)
    arch = archive(PROP, "applied")
    print(f"applied tags to {len(chg)} books + stamped #wrangled on {len(processed)} processed; proposal archived -> {os.path.basename(arch)}")


def apply_proposal_step() -> None:
    """1-by-1 review of the proposal: each book's proposed tags as a checklist. Accepted tags are
    applied + the book stamped; rejected tags are dropped and logged (class=ai, a hallucination filter,
    NOT a rule bug). Skip/quit leave a book's row pending in the proposal for a later run."""
    if not os.path.exists(PROP):
        raise SystemExit(f"no proposal to apply ({os.path.basename(PROP)} not found — run a classify pass first).")
    from scourgify import ui
    if not ui.interactive():
        raise SystemExit("--step needs an interactive terminal (omit it to apply the whole proposal).")
    from scourgify.common import log_rejects
    con = ro_connect()
    desc = {b: strip_html(t) for b, t in con.execute("SELECT book, text FROM comments")}
    titles = {b: t for b, t in con.execute("SELECT id, title FROM books")}
    decided, pending, rejects, quit_ = [], [], [], False
    for r in read_proposal():
        tags = r["added_tags"]
        if quit_: pending.append(r); continue
        if not tags: decided.append(r); continue               # no-tag book: stamp only (else re-sent forever)
        b = r["book_id"]; title = str(r.get("title") or titles.get(b, ""))
        acc, rej, action = ui.checklist(f"[bold]#{b}[/]  {title[:64]}", tags, subtitle=(desc.get(b, "")[:280] or "(no description)"))
        if action == "quit": quit_ = True; pending.append(r); continue
        if action == "skip": pending.append(r); continue
        for i in rej:
            rejects.append({"stage": "classify", "book": b, "title": title, "kind": "add",
                            "column": "tags", "before": "", "after": tags[i], "class": "ai"})
        decided.append({**r, "added_tags": [tags[i] for i in acc]})
    log_rejects(rejects)
    if not decided:
        print("(nothing decided — proposal left untouched.)"); return
    write_proposal(decided); apply_proposal()                  # applies + stamps the decided rows, archives PROP
    if pending:
        write_proposal(pending)
        print(f"{len(pending)} book(s) left pending for a later run -> {os.path.basename(PROP)}")


# ---- gather books (read-only) ----
def strip_html(s: str | None) -> str: return re.sub(r"<[^>]+>", " ", s or "").strip()

def book_text(path: str | None, limit: int = 6000) -> str:
    if not path or not os.path.exists(path): return ""
    if path.lower().endswith(".epub"):                  # fast path: epub is a zip of XHTML
        import zipfile
        try:
            z = zipfile.ZipFile(path); out = []
            for n in z.namelist():
                if not n.lower().endswith((".xhtml", ".html", ".htm")): continue
                if z.getinfo(n).file_size > 2_000_000: continue        # untrusted download: skip zip-bomb members
                t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", z.read(n).decode("utf-8", "ignore"))).strip()
                if len(t) > 200: out.append(t)           # skip nav/title pages
                if sum(len(x) for x in out) > limit: break
            return " ".join(out)[:limit]
        except Exception: return ""
    import tempfile                                      # other formats (MOBI/PDF/DOCX/…): let calibre extract
    try:
        with tempfile.TemporaryDirectory() as td:
            o = os.path.join(td, "o.txt")
            subprocess.run(["ebook-convert", path, o], capture_output=True, timeout=180)
            return re.sub(r"\s+", " ", open(o, errors="ignore").read()).strip()[:limit] if os.path.exists(o) else ""
    except Exception: return ""

def gather(a: argparse.Namespace) -> tuple:
    """-> (targets [(book, text)], titles, needs). Scope comes from the flags, first match wins:
    --incremental / --last N / --since DATE select ONLY matching books (newest-added-first);
    bare classify keeps the sparse mode (fewer than --min-tags tags). `needs(b)` is True for
    explicitly scoped books — the resume logic uses it to re-process them even if already proposed."""
    con = ro_connect(); c = con.cursor()
    if a.all:         ids, scope = select.pick(con, "all"), "whole library"
    elif a.incremental: ids, scope = select.pick(con, "incremental"), "new/changed since last classify"
    elif a.last:      ids, scope = select.pick(con, "last", n=a.last), f"last {a.last} added"
    elif a.since:     ids, scope = select.pick(con, "since", since=a.since), f"added/updated since {a.since}"
    else:             ids, scope = select.pick(con, "sparse", min_tags=a.min_tags), f"fewer than {a.min_tags} tags"
    explicit = set(ids) if (a.all or a.incremental or a.last or a.since) else set()
    def needs(b): return b in explicit
    desc = {b: t for b, t in c.execute("SELECT book, text FROM comments")}
    bookfile = {}
    if a.text_fallback:                           # when the description is thin, sample the book's own text
        bp = {b: p for b, p in c.execute("SELECT id, path FROM books")}
        byb = {}
        for b, fmt, name in c.execute("SELECT book, format, name FROM data"):
            byb.setdefault(b, {})[fmt.upper()] = os.path.join(library(), bp[b], name + "." + fmt.lower())
        for b, fm in byb.items():
            bookfile[b] = fm.get("EPUB") or next(iter(fm.values()))   # prefer EPUB, else any available format
    def text_for(b):
        d = strip_html(desc.get(b, ""))
        if len(d) >= 80 or not a.text_fallback: return d
        et = book_text(bookfile.get(b, ""))
        return (d + " " + et).strip() if et else d
    targets = [(b, text_for(b)) for b in ids]
    kept = [(b, t) for b, t in targets if t and len(t) >= 40]
    print(f"  scope: {scope} -> {len(ids)} books")
    if len(kept) < len(targets):                  # no silent drops: thin descriptions are reported, not vanished
        print(f"  note: {len(targets) - len(kept)} dropped (description under 40 chars"
              + (")" if a.text_fallback else "; --text-fallback samples the book text instead)"))
    titles = {b: t for b, t in c.execute("SELECT id, title FROM books")}
    if a.limit: kept = kept[:a.limit]
    return kept, titles, needs


def bakeoff(a: argparse.Namespace, targets: list, engines: list, n: int = 5) -> dict:
    """The same n sample books through each engine, sequentially — for comparing output quality
    before committing to a full run. -> {book: {engine: (vocab_tags, new_tags, err)}}.
    Display-only: never touches the proposal CSV."""
    out = {}
    for e in engines:
        eng = ENGINES[e]("", a.timeout)                       # per-engine default model
        for b, d in targets[:n]:
            resp, err = ask_retry(eng, prompt_for(d, a.max_tags), tries=1)   # one shot per sample, shared error format
            vt, nt = parse_resp(resp, a.max_tags, a.dedup_cutoff)
            out.setdefault(b, {})[e] = (vt, nt, err)
    return out


def spend_gate(n_books: int, engine: str, yes: bool) -> None:
    """THE cloud-spend confirmation — the single owner of the gate. `yes` answers it up front
    (the CLI --yes flag, or the wizard's own cost-estimate confirm)."""
    if engine == "apple" or n_books <= SPEND_GATE or yes: return
    msg = f"about to send {n_books} books to the {engine} API (costs money; --incremental/--batch shrink it)."
    if not _interactive():
        raise SystemExit(f"  {msg}\n  non-interactive: re-run with --yes to confirm.")
    if not _confirm(f"  {msg} proceed?"):
        raise SystemExit("aborted (nothing sent).")


def classify_run(a: argparse.Namespace) -> None:
    a = normalize(a)                               # owns its invariants (apple → workers=1) regardless of caller
    targets, titles, needs = gather(a)
    print(f"engine={a.engine}  candidate books: {len(targets)}")

    proposal, done = {}, set()                     # book -> (vocab_tags, proposed_new_tags)
    if not a.fresh:                                # resume: skip books already in proposal
        for r in read_proposal():
            bid, at = r["book_id"], r["added_tags"]
            proposal[bid] = (at, r["proposed_new"])
            if (at or not a.text_fallback) and not needs(bid): done.add(bid)   # re-process books changed since last wrangle
        if done: print(f"  resuming: {len(done)} already in proposal (pass --fresh to restart)")
    def dump():
        write_proposal([{"book_id": b, "title": titles.get(b, ""), "added_tags": vt, "proposed_new": nt}
                        for b, (vt, nt) in proposal.items()])

    todo = [(b, d) for b, d in targets if b not in done]
    if a.batch: todo = todo[:a.batch]
    spend_gate(len(todo), a.engine, a.yes)         # cloud runs cost real money

    eng = ENGINES[a.engine](a.model, a.timeout)
    def work(b, d):
        out, err = ask_retry(eng, prompt_for(d, a.max_tags)); vt, nt = parse_resp(out, a.max_tags, a.dedup_cutoff); return b, err, vt, nt

    failures = []
    print(f"  {len(todo)} to do this run, {a.workers} concurrent")
    ex = ThreadPoolExecutor(max_workers=a.workers)
    interrupted = False
    try:
        with _Dashboard(len(todo), len(done), len(targets)) as dash:
            futs = [ex.submit(work, b, d) for b, d in todo]
            for fut in as_completed(futs):
                b, err, vt, nt = fut.result()
                if err: failures.append((b, err))
                else: proposal[b] = (vt, nt)          # record EVERY non-errored book, even a no-match (vt=nt=[]):
                                                      # --apply stamps every proposal row, so it isn't re-sent forever.
                                                      # errors are excluded on purpose — they retry (e.g. --engine apple).
                dash.update(vt, nt, err)
                if dash.n % 50 == 0: dump()           # checkpoint regardless of UI
    except KeyboardInterrupt:
        # Ctrl+C: never start queued work, don't wait for in-flight requests (they're
        # abandoned; runs are resumable so nothing is lost beyond the requests in the air)
        interrupted = True
        ex.shutdown(wait=False, cancel_futures=True)
    else:
        ex.shutdown()
    dump()
    if interrupted:
        print(f"\n  interrupted — {len(proposal)} results saved to the proposal; re-run to resume where you left off.")
    if failures:
        with open(FAIL, "w", newline="") as f:
            w = csv.writer(f); w.writerow(["book_id", "title", "reason"])
            for b, e in failures: w.writerow([b, titles.get(b, ""), e])
        bytype = collections.Counter(e.split(":")[0].split(" ")[0] for _, e in failures)
        print(f"failures: {len(failures)} -> {os.path.basename(FAIL)}  by type: {dict(bytype)}")
        print("  (recover blocked books with a no-policy engine: scourgify classify --engine apple)")

    ranked = collections.Counter()
    for vt, nt in proposal.values():
        for t in nt: ranked[t] += 1
    rows = annotate_new(ranked, a.dedup_cutoff)               # nearest existing tag + verdict for each candidate
    fresh = [r for r in rows if r[4] == "new"]                # genuinely novel — the ones worth promoting
    write_ranked(rows)
    print(f"\nOutput 1 (apply): {sum(1 for v in proposal.values() if v[0])} books with vocab tags -> {os.path.basename(PROP)} (col 'added_tags')")
    print(f"Output 2 (grow):  {len(fresh)} new + {len(rows) - len(fresh)} near-dupes of existing tags -> {os.path.basename(RANK)} (promote 'verdict=new' rows into defaults/classify_vocab.txt)")
    if RICH and rows:
        tbl = Table(title="top new-tag candidates (verdict=new → promote; near-duplicate ≈ an existing tag)")
        tbl.add_column("count", justify="right", style="cyan"); tbl.add_column("proposed tag")
        tbl.add_column("nearest existing", style="dim"); tbl.add_column("verdict")
        for tag, cnt, nearest, sim, verdict in rows[:25]:
            tbl.add_row(str(cnt), tag, f"{nearest} ({sim})" if nearest else "",
                        f"[green]new[/]" if verdict == "new" else f"[yellow]≈ dupe[/]")
        _con.print(tbl)
    elif rows:
        print("top new-tag candidates (verdict | count | tag | nearest existing):")
        for tag, cnt, nearest, sim, verdict in rows[:25]:
            print(f"  {verdict:14} {cnt:4}  {tag}" + (f"  ≈ {nearest} ({sim})" if nearest else ""))
    print("\nApply vocab tags with: scourgify classify --apply   (Calibre closed)")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Content-based tagging from a controlled vocabulary (LLM engines; dry-run until --apply).")
    p.add_argument("--engine", default="apple", choices=sorted(ENGINES), help="apple = on-device, free (default)")
    p.add_argument("--apply", action="store_true", help="apply 'added_tags' from the proposal + stamp #wrangled (Calibre closed)")
    p.add_argument("--step", action="store_true", help="with --apply: review each book's tags 1-by-1 (interactive; untick to reject)")
    p.add_argument("--incremental", action="store_true", help="only new/changed books (never classified, #updated newer than their #wrangled marker, or re-fetched)")
    p.add_argument("--all", action="store_true", help="the WHOLE library — every book, regardless of tag count (a full cloud pass costs real money)")
    p.add_argument("--last", type=int, default=0, metavar="N", help="(re)process the N most recently added books")
    p.add_argument("--since", default="", metavar="DATE", help="(re)process books added or site-updated on/after this ISO date")
    p.add_argument("--fresh", action="store_true", help="ignore the existing proposal and restart (a full cloud pass costs real money)")
    p.add_argument("--batch", type=int, default=0, metavar="N", help="process only N new books this run (re-run resumes)")
    p.add_argument("--limit", type=int, default=0, metavar="N", help="hard cap on candidate books")
    p.add_argument("--workers", type=int, default=8, metavar="N", help="concurrent API requests (cloud engines are I/O-bound)")
    p.add_argument("--min-tags", type=int, default=2, metavar="N", help="process books with fewer than N tags")
    p.add_argument("--max-tags", type=int, default=6, metavar="N", help="max vocab tags per book")
    p.add_argument("--dedup-cutoff", type=float, default=DEDUP_CUTOFF, metavar="R",
                   help=f"difflib ratio (0-1) to treat a proposed tag as a variant of an existing one (default {DEDUP_CUTOFF})")
    p.add_argument("--model", default="", help="override the per-engine default model")
    p.add_argument("--timeout", type=int, default=60, metavar="S", help="per-request HTTP timeout")
    p.add_argument("--text-fallback", action="store_true", help="sample the book's own prose when the description is thin")
    p.add_argument("--bakeoff", action="store_true", help="compare a few sample books across every usable engine, then exit (no proposal written)")
    p.add_argument("--yes", "-y", action="store_true", help="skip the large-cloud-run confirmation")
    return p

def normalize(a: argparse.Namespace) -> argparse.Namespace:
    """Post-parse invariants (idempotent; classify_run applies them itself, so callers never
    have to worry about ordering them around engine choice)."""
    if a.engine == "apple": a.workers = 1        # apple = one subprocess pipe, not thread-safe
    library()                                    # fail fast with a clear message
    os.makedirs(DATA, exist_ok=True)
    return a


def default_opts(**overrides) -> argparse.Namespace:
    """The non-CLI entry to a run's options: parser defaults + keyword overrides. The argparse
    parser stays the single schema; the wizard is the second adapter that fills it."""
    a = build_parser().parse_args([])
    for k, v in overrides.items(): setattr(a, k, v)
    return a

def bakeoff_cli(a: argparse.Namespace) -> None:
    """`scourgify classify --bakeoff`: the same sample-books-across-engines comparison the wizard offers,
    display-only (never writes the proposal). Plain text — works with or without rich."""
    a.text_fallback = True                         # thin descriptions sample the book text, like the wizard's compare
    targets, titles, _ = gather(a)
    if not targets:
        print("no candidate books with usable text — nothing to compare."); return
    engs = usable_engines()
    if not engs:
        print("no usable engines — set an API key (ANTHROPIC/OPENAI/GEMINI/MISTRAL) or install the afm/swift toolchain."); return
    n = min(5, len(targets))
    print(f"bake-off: {n} sample book(s) × {', '.join(engs)} (sequential — a minute or two)…\n")
    res = bakeoff(a, targets, engs, n=n)
    for b, per in res.items():
        print(f"#{b}  {str(titles.get(b, ''))[:60]}")
        for e in engs:
            vt, nt, err = per.get(e, ([], [], "—"))
            body = err if err else (", ".join(vt) or "(none)") + (f"   +new: {', '.join(nt)}" if nt else "")
            print(f"    {e:8} {body}")
        print()

def main() -> None:
    a = normalize(build_parser().parse_args())
    if a.bakeoff: bakeoff_cli(a)
    elif a.apply: apply_proposal_step() if a.step else apply_proposal()
    else: classify_run(a)


if __name__ == "__main__":
    main()
