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
import argparse, os, csv, json, re, collections, difflib
from concurrent.futures import ThreadPoolExecutor, as_completed
from scourgify import booktext, engines as engines_mod, report, select
from scourgify.booktext import strip_html                               # text extraction lives in booktext.py
from scourgify.common import (HERE, data_dir, user_dir, ro_connect, custom_column_id, run_writer, library,
                              current_tags, titles as book_titles, op_create_column, op_set_field, op_stamp_now,
                              interactive as _interactive, confirm as _confirm)
from scourgify.overrides import ov_path, merge_vocab, read_aliases      # overrides/ paths + format readers live there
from scourgify.artifacts import (prop, rank, fail,                      # artifact paths + formats live in artifacts.py
                                 read_proposal, write_proposal, write_ranked, archive, archive_rows,
                                 classified_ids)
# the engine seam lives in engines.py; re-exported here so `classify.ENGINES` / `classify.ask_retry`
# stay valid for promote, the wizard, and existing tests
from scourgify.engines import ENGINES, ENGINE_ENV, PRICING, usable_engines, ask_retry, is_free, max_workers as engine_workers
from scourgify.report import Dashboard as _Dashboard                    # live display lives in report.py

def _ao3_vocab_path() -> str:           # per-library canonical AO3 freeforms (name,uses); absent on fresh installs
    return os.path.join(data_dir(), "ao3_vocab.csv")
SPEND_GATE = 200        # cloud runs above this many books require an explicit yes
DEDUP_CUTOFF = 0.86     # difflib ratio at/above which a proposed tag counts as a variant of an existing one

_VOCAB = None
def clear_caches() -> None:
    """Forget the memoized vocab/alias/AO3 loads (they key off user_dir(), which tests repoint
    via $SCOURGIFY_HOME) — the supported way to reload, instead of poking module globals."""
    global _VOCAB, _ALIASES, _AO3
    _VOCAB = _ALIASES = _AO3 = None

def _read_vocab_file(path: str) -> list:
    """The plain terms in a vocab file. '-term' lines are removal DIRECTIVES (merge_vocab applies
    them) — reading one as a literal term would put '-Foo' in the prompt's controlled vocabulary."""
    if not os.path.exists(path): return []
    return [l.strip() for l in open(path)
            if l.strip() and not l.startswith("#") and not l.strip().startswith("-")]

def load_vocab() -> list:
    """Curated core ∪ AO3 high-frequency seed, then the user's overrides classify_vocab.txt (a line appends
    a term; '-term' removes one — and can trim a seeded term too; semantics in overrides.merge_vocab). Lazy so
    a packaging problem gives a real error at use, not at import, and installed users can override."""
    global _VOCAB
    if _VOCAB is None:
        curated = f"{HERE}/defaults/classify_vocab.txt"
        terms, have = [], set()                                                   # curated core first, then AO3 seed;
        for t in (_read_vocab_file(curated)                                        # first spelling of a norm wins,
                  + _read_vocab_file(f"{HERE}/defaults/classify_vocab_ao3.txt")):  # so a hand-edit dup can't sneak in
            if t.lower() not in have: terms.append(t); have.add(t.lower())
        # the curated layer runs through merge_vocab too, so its '-term' lines can trim a term from the
        # GENERATED ao3 seed (which must never be hand-edited — regeneration overwrites it). Its plain
        # lines are already in `terms`, so that pass is a no-op for everything but the removals.
        _VOCAB = merge_vocab(merge_vocab(terms, curated))   # '-term' semantics live with the writer (overrides.py)
    return _VOCAB

_ALIASES = None
def load_aliases() -> dict:
    """candidate -> target snaps from overrides/promote_aliases.csv (written by `scourgify promote --apply`),
    so tags we've decided are synonyms stop getting re-proposed as 'new'. {} if absent."""
    global _ALIASES
    if _ALIASES is None:
        _ALIASES = read_aliases()                  # delimiter-sniffing reader lives with the writer (overrides.py)
    return _ALIASES

_AO3 = None
def load_ao3_vocab() -> list:
    """The per-library AO3 canonical freeforms (data/ao3_vocab.csv 'name' column, built by ao3_import.py).
    Absent on a fresh install — degrade to [] silently; it's an extra reference layer, not a requirement."""
    global _AO3
    if _AO3 is None:
        try:
            _AO3 = [r["name"] for r in csv.DictReader(open(_ao3_vocab_path())) if r.get("name", "").strip()]
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
    """Rough list-price $ estimate for a run: input ≈ prompt chars/4 tokens, output per engine.

    Output is per-engine (engines.TRAITS 'out_tokens') because a reasoning model bills its hidden
    thinking as output — gemini-2.5-flash spends ~14x the visible answer on thoughts. A flat 80
    under-quoted it fivefold, which is the worst direction for a number shown before spending."""
    i, o = PRICING.get(engine, (0.0, 0.0))
    tokens_in = (len(", ".join(load_vocab())) + 1900) / 4      # vocab + 1500-char description + instructions
    return n_books * (tokens_in * i + engines_mod.trait(engine, "out_tokens") * o) / 1e6


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


# ---- apply: 'added_tags' + stamp #wrangled — standalone, no LLM calls ----
def apply_proposal(rows: list | None = None) -> None:
    """rows=None (the --apply path): read the live proposal, apply + stamp it, archive it on
    success. Explicit rows (the --step path): apply + stamp exactly those; the proposal file is
    the CALLER's to archive/rewrite — it is never touched here, so a writer refusal loses nothing."""
    from_file = rows is None
    if from_file:
        if not os.path.exists(prop()):
            raise SystemExit(f"no proposal to apply ({os.path.basename(prop())} not found — run a classify pass first).")
        rows = read_proposal()
    con = ro_connect()
    cur = current_tags(con)
    known = {b for (b,) in con.execute("SELECT id FROM books")}
    have_wrangled = custom_column_id(con, "wrangled") is not None
    chg, processed, stale = {}, [], []
    for r in rows:
        b = r["book_id"]
        if b not in known:                         # a row can outlive its book (deleted / re-imported with
            stale.append(b); continue              # a new id) — a dead id would FK-abort the whole write
        processed.append(b)
        if r["added_tags"]: chg[b] = sorted(cur.get(b, set()) | set(r["added_tags"]))   # union with current tags
    if stale:
        print(f"  note: {len(stale)} stale proposal row(s) for books no longer in the library — skipped: {stale[:10]}")
    ops = []
    if not have_wrangled:                                             # first run: create + backfill whole library as wrangled-now
        ops.append(op_create_column("wrangled", "Wrangled", "datetime"))
        ops.append(op_stamp_now("#wrangled"))
    ops.append(op_set_field("tags", chg))
    # stamp EVERY processed book, tagged or not — an unstamped no-tag book would be re-sent to the LLM forever
    ops.append(op_stamp_now("#wrangled", processed))
    run_writer(ops)
    tail = ""
    if from_file:
        # archive so a later --apply can't re-add tags you've since hand-removed (stale rows never re-apply)
        tail = f"; proposal archived -> {os.path.basename(archive(prop(), 'applied'))}"
    print(f"applied tags to {len(chg)} books + stamped #wrangled on {len(processed)} processed{tail}")


def apply_proposal_step() -> None:
    """1-by-1 review of the proposal: each book's proposed tags as a checklist. Accepted tags are
    applied + the book stamped; rejected tags are dropped and logged (class=ai, a hallucination filter,
    NOT a rule bug). Skip/quit leave a book's row pending in the proposal for a later run."""
    if not os.path.exists(prop()):
        raise SystemExit(f"no proposal to apply ({os.path.basename(prop())} not found — run a classify pass first).")
    from scourgify import ui
    if not ui.interactive():
        raise SystemExit("--step needs an interactive terminal (omit it to apply the whole proposal).")
    from scourgify.common import log_rejects
    con = ro_connect()
    desc = {b: strip_html(t) for b, t in con.execute("SELECT book, text FROM comments")}
    titles = book_titles(con)
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
    apply_proposal(rows=decided)                   # PROP untouched until success — a writer refusal loses nothing
    # Archive ONLY the rows that were written. An *_applied_* archive is the durable record of
    # "this book has been classified", so naming a skipped book in one retires it from future
    # scopes despite nothing reaching the library — quit on book 2 of 200 used to file all 200.
    # The live proposal still holds the full record until it is replaced below, so a crash in
    # between loses nothing and simply re-applies (the tag write is a union, i.e. idempotent).
    arch = archive_rows(decided, "applied")
    if pending:
        write_proposal(pending)                    # backfill's _proposal_files() reads these from prop()
        print(f"{len(pending)} book(s) left pending for a later run -> {os.path.basename(prop())}")
    else:
        os.remove(prop())
    print(f"applied rows archived -> {os.path.basename(arch)}")


# ---- gather books (read-only) ----
def gather(a: argparse.Namespace) -> tuple:
    """-> (targets [(book, text)], titles, needs). Scope comes from the flags, first match wins:
    --books SPEC / --incremental / --last N / --since DATE select ONLY matching books (newest-added-first);
    bare classify keeps the sparse mode (fewer than --min-tags tags). `needs(b)` is True for
    explicitly scoped books — the resume logic uses it to re-process them even if already proposed.
    Text extraction (EPUB zip / ebook-convert) lives in booktext.py."""
    con = ro_connect(); c = con.cursor()
    missing = 0
    if a.books is not None:                       # explicit ids win over every other scope flag
        want = select.parse_books(a.books)
        ids = select.pick(con, "ids", ids=want)
        missing, scope = len(want) - len(ids), f"{len(want)} book(s) by id"
    elif a.all:       ids, scope = select.pick(con, "all"), "whole library"
    elif a.incremental: ids, scope = select.pick(con, "incremental"), "new/changed since last classify"
    elif a.unclassified:                          # the advancing scope: never attempted, and sendable
        ids = select.pick(con, "unclassified", seen=classified_ids(), text_fallback=a.text_fallback)
        scope = f"never classified ({len(ids)} outstanding)"
    elif a.last:      ids, scope = select.pick(con, "last", n=a.last), f"last {a.last} added"
    elif a.since:     ids, scope = select.pick(con, "since", since=a.since), f"added/updated since {a.since}"
    else:             ids, scope = select.pick(con, "sparse", min_tags=a.min_tags), f"fewer than {a.min_tags} tags"
    explicit = set(ids) if (a.books is not None or a.all or a.incremental or a.unclassified
                            or a.last or a.since) else set()
    def needs(b): return b in explicit
    desc = {b: t for b, t in c.execute("SELECT book, text FROM comments")}
    # when the description is thin, sample the book's own text instead of dropping the book
    bookfile = booktext.paths(con) if a.text_fallback else {}
    def text_for(b):
        d = strip_html(desc.get(b, ""))
        if len(d) >= 80 or not a.text_fallback: return d
        et = booktext.extract(bookfile.get(b, ""))
        return (d + " " + et).strip() if et else d
    targets = [(b, text_for(b)) for b in ids]
    kept = [(b, t) for b, t in targets if t and len(t) >= 40]
    # flush: the live dashboard writes straight through, so an unflushed plain print lands
    # AFTER it when stdout is a pipe (scripting/CI) rather than a terminal
    print(f"  scope: {scope} -> {len(ids)} books", flush=True)
    if missing: print(f"  note: {missing} requested id(s) not in the library")
    if len(kept) < len(targets):                  # no silent drops: thin descriptions are reported, not vanished
        print(f"  note: {len(targets) - len(kept)} dropped (description under 40 chars"
              + (")" if a.text_fallback else "; --text-fallback samples the book text instead)"))
    titles = book_titles(con)
    if a.limit: kept = kept[:a.limit]
    return kept, titles, needs


class Plan:
    """ONE resolved classify run — scope → targets (gather), resume → todo — priced, confirmed,
    and executed as the same object, so the cost the user confirms is over the exact todo set the
    run bills and the expensive text extraction never runs twice (mirrors wrangle.Plan; the CLI
    and the wizard drive the same object). The plan owns a COPY of the caller's options: steering
    a resolved plan (engine choice, the spend-gate answer) goes through `p.opts`, never through
    mutating the original namespace — that aliasing was load-bearing once, by accident."""

    def __init__(self, a: argparse.Namespace):
        import copy
        a = normalize(copy.copy(a))
        self.opts = a
        self.targets, self.titles, self.needs = gather(a)
        self.proposal, self.done = {}, set()           # book -> (vocab_tags, proposed_new_tags)
        if not a.fresh:                                # resume: skip books already in proposal
            for r in read_proposal():
                bid, at = r["book_id"], r["added_tags"]
                self.proposal[bid] = (at, r["proposed_new"])
                if (at or not a.text_fallback) and not self.needs(bid):   # re-process books changed since last wrangle
                    self.done.add(bid)
        self.todo = [(b, d) for b, d in self.targets if b not in self.done]
        if a.batch: self.todo = self.todo[:a.batch]

    def run(self, ask=None) -> None:
        """Execute the plan. `ask`: prompt -> (text, err) — tests inject a callable (the same
        seam promote.run has); default builds the configured engine and goes through ask_retry."""
        a, titles, proposal = self.opts, self.titles, self.proposal
        print(f"engine={a.engine}  candidate books: {len(self.targets)}", flush=True)
        if self.done: print(f"  resuming: {len(self.done)} already in proposal (pass --fresh to restart)", flush=True)
        def dump():
            write_proposal([{"book_id": b, "title": titles.get(b, ""), "added_tags": vt, "proposed_new": nt}
                            for b, (vt, nt) in proposal.items()])

        spend_gate(len(self.todo), a.engine, a.yes)    # cloud runs cost real money

        if ask is None:
            eng = ENGINES[a.engine](a.model, a.timeout)
            ask = lambda prompt: ask_retry(eng, prompt)
        def work(b, d):
            out, err = ask(prompt_for(d, a.max_tags)); vt, nt = parse_resp(out, a.max_tags, a.dedup_cutoff); return b, err, vt, nt

        failures = []
        workers = engine_workers(a.engine, a.workers)   # non-parallel engines (apple) cap at 1
        print(f"  {len(self.todo)} to do this run, {workers} concurrent", flush=True)
        ex = ThreadPoolExecutor(max_workers=workers)
        interrupted = False
        try:
            with _Dashboard(len(self.todo), len(self.done), len(self.targets)) as dash:
                futs = [ex.submit(work, b, d) for b, d in self.todo]
                for fut in as_completed(futs):
                    b, err, vt, nt = fut.result()
                    if err: failures.append((b, err))
                    else: proposal[b] = (vt, nt)      # record EVERY non-errored book, even a no-match (vt=nt=[]):
                                                      # --apply stamps every proposal row, so it isn't re-sent forever.
                                                      # errors are excluded on purpose — they retry (e.g. --engine apple).
                    dash.update(vt, nt, err)
                    if dash.n % 50 == 0: dump()       # checkpoint regardless of UI
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
        # Rewrite the log every run, not just when this one failed: a book recovered on another
        # engine has to LEAVE the list, or it reads as still-broken forever. Books outside this
        # run's scope are carried through — the log is library-wide, the run is not.
        from scourgify.artifacts import merge_failures, read_rows, write_failures
        # only books this run actually got an answer for — on Ctrl+C the queued ones were never
        # attempted, and clearing their old failure row would hide a real, still-unfixed failure
        processed = {b for b, _ in self.todo} & (set(proposal) | {b for b, _ in failures})
        prev = read_rows(fail()) if os.path.exists(fail()) else []
        write_failures(merge_failures(prev, processed, [[b, titles.get(b, ""), e] for b, e in failures]))
        if failures:
            bytype = collections.Counter(e.split(":")[0].split(" ")[0] for _, e in failures)
            print(f"failures: {len(failures)} -> {os.path.basename(fail())}  by type: {dict(bytype)}")
            print("  (recover blocked books with a no-policy engine: scourgify classify --engine apple)")

        ranked = collections.Counter()
        for vt, nt in proposal.values():
            for t in nt: ranked[t] += 1
        rows = annotate_new(ranked, a.dedup_cutoff)           # nearest existing tag + verdict for each candidate
        fresh = [r for r in rows if r[4] == "new"]            # genuinely novel — the ones worth promoting
        write_ranked(rows)
        print(f"\nOutput 1 (apply): {sum(1 for v in proposal.values() if v[0])} books with vocab tags -> {os.path.basename(prop())} (col 'added_tags')")
        print(f"Output 2 (grow):  {len(fresh)} new + {len(rows) - len(fresh)} near-dupes of existing tags -> {os.path.basename(rank())} (promote 'verdict=new' rows into defaults/classify_vocab.txt)")
        if rows:
            report.table("top new-tag candidates (verdict=new → promote; near-duplicate ≈ an existing tag)",
                         ["count", "proposed tag", "nearest existing", "verdict"],
                         [[(str(cnt), "cyan"), tag, (f"{nearest} ({sim})" if nearest else "", "dim"),
                           ("new", "green") if verdict == "new" else ("≈ dupe", "yellow")]
                          for tag, cnt, nearest, sim, verdict in rows[:25]], right=(0,))
        print("\nApply vocab tags with: scourgify classify --apply   (Calibre closed)")


def plan(a: argparse.Namespace) -> Plan:
    """Resolve a classify run ONCE. The wizard prices/confirms over this plan and run() executes
    the SAME plan — see Plan."""
    return Plan(a)


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
    if is_free(engine) or n_books <= SPEND_GATE or yes: return
    msg = f"about to send {n_books} books to the {engine} API (costs money; --incremental/--batch shrink it)."
    if not _interactive():
        raise SystemExit(f"  {msg}\n  non-interactive: re-run with --yes to confirm.")
    if not _confirm(f"  {msg} proceed?"):
        raise SystemExit("aborted (nothing sent).")


def classify_run(run) -> None:
    """Execute a classify run: a Plan (the wizard's path — planned, priced, and confirmed once)
    or a bare argparse Namespace (the CLI path — planned here)."""
    p = plan(run) if isinstance(run, argparse.Namespace) else run
    if getattr(p.opts, "scope_only", False):
        # STOP before any engine call. "dry run" in this tool means "does not write to the
        # library" — it does NOT mean "does not call the engine": a bare `classify` still sends
        # every selected book. This is the flag for "what would this select?".
        print(f"  scope-only: {len(p.targets)} candidate book(s), {len(p.todo)} would be sent"
              f" — nothing sent, nothing written.")
        for eng in sorted(ENGINES):
            c = est_cost(len(p.todo), eng)
            print(f"    {eng:8} {'free' if not c else f'~${c:.2f}'}")
        return
    p.run()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Content-based tagging from a controlled vocabulary (LLM engines; dry-run until --apply).")
    p.add_argument("--engine", default="apple", choices=sorted(ENGINES), help="apple = on-device, free (default)")
    p.add_argument("--apply", action="store_true", help="apply 'added_tags' from the proposal + stamp #wrangled (Calibre closed)")
    p.add_argument("--scope-only", action="store_true",
                   help="resolve the scope, print what WOULD be sent and what each engine would "
                        "cost, then stop — no engine calls. (Without --apply a run still SENDS: "
                        "'dry run' here means it does not write to the library.)")
    p.add_argument("--step", action="store_true", help="with --apply: review each book's tags 1-by-1 (interactive; untick to reject)")
    p.add_argument("--books", default=None, metavar="SPEC",
                   help="only these books: '1,2,3', '10-20', '@ids.txt' (one id per line), or a combination")
    p.add_argument("--incremental", action="store_true", help="only new/changed books (never classified, #updated newer than their #wrangled marker, or re-fetched)")
    p.add_argument("--unclassified", action="store_true",
                   help="only books classify has never attempted (see artifacts.classified_ids) and can "
                        "actually send — the scope that ADVANCES, so --batch N chews through a library")
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
    """Post-parse invariants (idempotent). Engine-dependent settings (apple → 1 worker) are
    resolved at run time, so the wizard can pick an engine after planning (via p.opts)."""
    library()                                    # fail fast with a clear message
    os.makedirs(data_dir(), exist_ok=True)
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
    if a.books is not None and a.apply:
        raise SystemExit("--books scopes which books are CLASSIFIED, not which proposal rows are applied. "
                         "Run `classify --books ...` first, then `classify --apply` to write the reviewed proposal.")
    if a.bakeoff: bakeoff_cli(a)
    elif a.apply: apply_proposal_step() if a.step else apply_proposal()
    else: classify_run(a)


if __name__ == "__main__":
    main()
