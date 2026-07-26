# Explicit book scope (`--books`) + full-library test run — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a first-class `--books` scope to classify/wrangle/staleness, then use it to exercise every scourgify surface against a real 7,949-book Calibre library with gemini and openai.

**Architecture:** One grammar and one parser live in `select.py` (already the sole owner of "which books does this run operate on"). Three thin wirings consume it: classify gets a new `pick` mode, wrangle gets `Plan.restrict()` applied *after* the full-library compute (the read must stay library-wide because `transform()` needs global context), staleness filters its result rows. The subtlety is guardrail correctness — `Plan`'s loss counters move from scalars to per-book dicts so the SAFETY guards judge the scoped write set rather than library-wide totals.

**Tech Stack:** Python 3.10+ stdlib, `rich` (via `report.py`/`ui.py`/`wizard.py` only), sqlite3, `uv`, plain-assert tests.

## Global Constraints

- Package management is **`uv` only** — never `pip`, never `pipx`. Run everything as `uv run scourgify …` from the repo checkout.
- Tests are **plain asserts, no framework**: `uv run tests/test_<name>.py`, also pytest-collectable. Every `tests/test_*.py` is in CI by glob.
- Core tools (`wrangle`/`classify`/`staleness`/`select`/`common`) must **never hard-import `rich`** — all rendering goes through `report.py`. `_writer.py` must never import `rich`, `ui`, `wizard`, or `report`.
- User-facing failures raise **`SystemExit`** with a plain message (the house convention). `ValueError` is reserved for internal invariant violations.
- Work lands on **`develop`**; history stays linear. Commit after every task.
- `CALIBRE_LIBRARY="/Users/andrei/Library/Mobile Documents/com~apple~CloudDocs/Calibre/fanfiction"` — 7,949 books, all 11 custom columns present, 7,948 already `#wrangled`-stamped.
- **Calibre must be CLOSED for every write step.** `run_writer()` refuses otherwise and fails closed.
- Available engines: **`gemini` and `openai` only.** `ANTHROPIC_API_KEY` and `MISTRAL_API_KEY` are unset, so `--engine claude`/`mistral` will fail. `apple` (on-device, free) is available on this Mac.
- The library is on **iCloud Drive** — check nothing is mid-sync before each write.
- Scratchpad for all temporary files: `/private/tmp/claude-501/-Users-andrei-Developer-scourgify/ab16e4cb-93ce-4f43-aebb-5474f192e4bd/scratchpad`

---

# Part A — the `--books` feature

### Task 1: `select.parse_books` + the `"ids"` pick mode

**Files:**
- Modify: `src/scourgify/select.py` (add `import os`; new `parse_books`; new branch in `pick`)
- Test: `tests/test_selection.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `select.parse_books(spec: str) -> list[int]` — raises `SystemExit` on a malformed token or unreadable `@file`.
  - `select.pick(con, mode="ids", ids=[...]) -> list[int]` — newest-added-first, ids absent from the library silently dropped (the caller reports the count).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_selection.py`, before the `if __name__` block:

```python
def test_parse_books_forms():
    assert select.parse_books("3,1,2") == [3, 1, 2]            # order preserved, not sorted
    assert select.parse_books("10-13") == [10, 11, 12, 13]     # inclusive range
    assert select.parse_books("7") == [7]
    assert select.parse_books("5, 1-3 ,5,2") == [5, 1, 2, 3]   # whitespace tolerated, de-duplicated


def test_parse_books_at_file():
    path = os.path.join(tempfile.mkdtemp(), "ids.txt")
    open(path, "w").write("# a comment\n7\n8,9\n\n10-11   # trailing comment\n")
    assert select.parse_books(f"@{path},1") == [7, 8, 9, 10, 11, 1]


def test_parse_books_rejects_garbage():
    nested = os.path.join(tempfile.mkdtemp(), "loop.txt")
    open(nested, "w").write("@%s\n" % nested)
    for bad in ("x", "1,2x", "9-3", "1-", "@/nonexistent/ids.txt", f"@{nested}"):
        try:
            select.parse_books(bad)
            assert False, f"expected SystemExit for {bad!r}"
        except SystemExit:
            pass


def test_pick_ids():
    assert select.pick(_con(), "ids", ids=[4, 2, 99]) == [2, 4]   # newest-added-first; 99 absent -> dropped
    assert select.pick(_con(), "ids", ids=[]) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run tests/test_selection.py`
Expected: FAIL — `AttributeError: module 'scourgify.select' has no attribute 'parse_books'`

- [ ] **Step 3: Implement `parse_books`**

In `src/scourgify/select.py`, change the import line to `import collections, os, sqlite3` and add after `_key()`:

```python
def _tokens(spec: str, depth: int = 0):
    """Comma-separated tokens, with '@file' expanded to its contents (one level deep).
    ponytail: one level is the ceiling — a file that references another file is a loop
    waiting to happen, and nobody needs an include graph to name fifty books."""
    for raw in spec.split(","):
        t = raw.strip()
        if not t: continue
        if not t.startswith("@"):
            yield t; continue
        if depth:
            raise SystemExit(f"--books: '@file' inside a file is not supported ({t})")
        path = os.path.expanduser(t[1:])
        try: text = open(path).read()
        except OSError as e:
            raise SystemExit(f"--books: cannot read {path}: {e}")
        yield from _tokens(",".join(ln.split("#")[0] for ln in text.splitlines()), depth + 1)


def parse_books(spec: str) -> list[int]:
    """'1,2,3' | '10-20' | '@ids.txt' | any comma-combination -> de-duplicated [book_id ...],
    order preserved. A file holds ids one per line (or comma-separated); '#' starts a comment.
    SystemExit on anything unparseable — this is user input, not an internal invariant."""
    out = []
    for t in _tokens(spec):
        if "-" in t:
            lo, _, hi = t.partition("-")
            try: lo, hi = int(lo), int(hi)
            except ValueError: raise SystemExit(f"--books: bad range {t!r} (expected 'LOW-HIGH')")
            if hi < lo: raise SystemExit(f"--books: empty range {t!r} (high is below low)")
            out.extend(range(lo, hi + 1))
        else:
            try: out.append(int(t))
            except ValueError: raise SystemExit(f"--books: {t!r} is not a book id")
    return list(dict.fromkeys(out))          # de-dup, first-seen order
```

- [ ] **Step 4: Implement the `"ids"` mode**

In `src/scourgify/select.py`, change `pick`'s signature and add the branch. The new signature:

```python
def pick(con: sqlite3.Connection, mode: str = "incremental", n: int = 0,
         since: str = "", min_tags: int = 2, ids: list[int] | None = None) -> list[int]:
    """[book_id ...] newest-added-first for one scope:
      incremental — changed() books only            last   — the n most recently added
      since       — added OR site-updated >= date   sparse — fewer than min_tags tags
      all         — everything                      ids    — exactly these (absent ones dropped)"""
```

Add this branch immediately before the `if mode == "all":` line:

```python
    if mode == "ids":
        want = set(ids or ())
        return [b for b in newest if b in want]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run tests/test_selection.py`
Expected: PASS — all tests, including the pre-existing ones.

- [ ] **Step 6: Commit**

```bash
git add src/scourgify/select.py tests/test_selection.py
git commit -m "feat(select): parse_books + an 'ids' pick mode

One grammar for naming books ('1,2,3', '10-20', '@ids.txt', combinations),
parsed in the module that already owns 'which books does this run operate
on'. @file expands one level deep — an include graph is not needed to name
fifty books, and one level can't loop.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: `classify --books`

**Files:**
- Modify: `src/scourgify/classify.py` (`gather()` around line 232-262; `build_parser()` around line 398)
- Test: `tests/test_classify_run.py`

**Interfaces:**
- Consumes: `select.parse_books(spec) -> list[int]`, `select.pick(con, "ids", ids=[...]) -> list[int]` (Task 1).
- Produces: `--books SPEC` on `scourgify classify`; `classify.default_opts(books="1,3")` works because `default_opts` is built from the parser.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_classify_run.py`, before the `if __name__` block:

```python
def test_books_scope_selects_exactly_those_books():
    """--books names books directly — no stamp state, no tag-count heuristic. Ids not in the
    library are dropped (and counted), never fatal."""
    books = [{"id": i, "added": f"2026-01-0{i} 10:00:00", "desc": DESC, "tags": ["t", "u"]}
             for i in (1, 2, 3)]
    with harness(books):
        p = classify.plan(classify.default_opts(books="3,1,99"))
        assert {b for b, _ in p.targets} == {1, 3}            # book 2 untouched, 99 dropped
        assert [b for b, _ in p.targets] == [3, 1]            # newest-added-first


def test_books_scope_counts_as_explicit_so_resume_reprocesses():
    """A --books book already sitting in the proposal is re-processed, like every other explicit
    scope — the user asked for it by id."""
    books = [{"id": 1, "added": "2026-01-01 10:00:00", "desc": DESC}]
    with harness(books):
        artifacts.write_proposal([{"book_id": 1, "title": "b1", "added_tags": ["X"], "proposed_new": []}])
        p = classify.plan(classify.default_opts(books="1"))
        assert [b for b, _ in p.todo] == [1] and not p.done
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run tests/test_classify_run.py`
Expected: FAIL — `AttributeError: 'Namespace' object has no attribute 'books'`

- [ ] **Step 3: Add the flag**

In `src/scourgify/classify.py`, in `build_parser()`, add immediately **above** the `--incremental` line:

```python
    p.add_argument("--books", default="", metavar="SPEC",
                   help="only these books: '1,2,3', '10-20', '@ids.txt' (one id per line), or a combination")
```

- [ ] **Step 4: Wire it into `gather()`**

In `src/scourgify/classify.py`, replace the scope-resolution block (currently lines 239-245) with:

```python
    missing = 0
    if a.books:                                   # explicit ids win over every other scope flag
        want = select.parse_books(a.books)
        ids = select.pick(con, "ids", ids=want)
        missing, scope = len(want) - len(ids), f"{len(want)} book(s) by id"
    elif a.all:       ids, scope = select.pick(con, "all"), "whole library"
    elif a.incremental: ids, scope = select.pick(con, "incremental"), "new/changed since last classify"
    elif a.last:      ids, scope = select.pick(con, "last", n=a.last), f"last {a.last} added"
    elif a.since:     ids, scope = select.pick(con, "since", since=a.since), f"added/updated since {a.since}"
    else:             ids, scope = select.pick(con, "sparse", min_tags=a.min_tags), f"fewer than {a.min_tags} tags"
    explicit = set(ids) if (a.books or a.all or a.incremental or a.last or a.since) else set()
    def needs(b): return b in explicit
```

Then, in the same function, immediately **after** the existing `print(f"  scope: {scope} -> {len(ids)} books")` line, add:

```python
    if missing: print(f"  note: {missing} requested id(s) not in the library")
```

Also update `gather`'s docstring first line to mention the new flag — replace `--incremental / --last N / --since DATE select ONLY matching books` with:

```
    --books SPEC / --incremental / --last N / --since DATE select ONLY matching books
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run tests/test_classify_run.py`
Expected: PASS — all tests.

- [ ] **Step 6: Commit**

```bash
git add src/scourgify/classify.py tests/test_classify_run.py
git commit -m "feat(classify): --books names the target books directly

Explicit ids win over every other scope flag and count as an explicit scope,
so resume re-processes them. Ids absent from the library are dropped and
reported rather than being fatal.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: `Plan`'s loss counters go per-book

**Files:**
- Modify: `src/scourgify/wrangle.py` (`Plan.__init__` lines 281-307; add four properties after `n_books`)
- Test: `tests/test_plan.py` (existing `test_plan_single_compute_feeds_changes_guards_and_decisions` must stay green — it asserts `(p.tagsB, p.tagsA) == (3, 2)`)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `Plan.lost: dict[int, tuple[bool, bool]]` — `{book: (lost_fandom, lost_char)}`, only for books with a loss.
  - `Plan.tagn: dict[int, tuple[int, int]]` — `{book: (tags_before, tags_after)}`, every book.
  - `Plan.lostF` / `Plan.lostC` / `Plan.tagsB` / `Plan.tagsA` — now read-only properties summing the dicts. Same names, same meaning, same call sites.

This is a pure refactor: no behavior changes, no new flag. It exists as its own task because Task 4's guard correctness depends entirely on it, and a reviewer should be able to approve this and reject that.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_plan.py`, before the `if __name__` block:

```python
def test_plan_keeps_loss_accounting_per_book():
    """The SAFETY counters are per-book so a scoped write can be judged on its own books
    (see restrict()). The aggregate properties keep the old names and the old meaning."""
    lib = tempfile.mkdtemp()
    build(os.path.join(lib, "metadata.db"),
          [dict(id=1, added="2026-01-01", tags=["Complete"]),
           dict(id=2, added="2026-01-02", tags=["Complete", "Keeper"])],
          custom=[("fandoms", {1: "Ghost", 2: "Real Fandom"})]).close()
    old = os.environ.get("CALIBRE_LIBRARY"); os.environ["CALIBRE_LIBRARY"] = lib
    try:
        cfg = load_config(path="/nonexistent/config.toml")
        p = wrangle.plan(cfg, maps(fan={"Ghost": ""}, junk_exact={"complete"}))
        assert p.lost == {1: (True, False)}          # only book 1 loses its last fandom
        assert p.tagn == {1: (1, 0), 2: (2, 1)}      # every book carries its tag counts
        assert (p.lostF, p.lostC) == (1, 0)          # aggregates unchanged in name and meaning
        assert (p.tagsB, p.tagsA) == (3, 1)
    finally:
        os.environ.pop("CALIBRE_LIBRARY", None) if old is None else os.environ.__setitem__("CALIBRE_LIBRARY", old)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run tests/test_plan.py`
Expected: FAIL — `AttributeError: 'Plan' object has no attribute 'lost'`

- [ ] **Step 3: Store the counters per-book**

In `src/scourgify/wrangle.py`, replace line 290:

```python
        self.lostF = self.lostC = self.tagsB = self.tagsA = 0
```

with:

```python
        self.lost = {}       # book -> (lost_fandom, lost_char), only for books with a loss
        self.tagn = {}       # book -> (tags_before, tags_after) — per-book so restrict() can re-derive
```

Then replace lines 295-296:

```python
            self.lostF += lf; self.lostC += lc
            self.tagsB += len(d.get("tags", [])); self.tagsA += len(nd.get("tags", []))
```

with:

```python
            if lf or lc: self.lost[b] = (lf, lc)
            self.tagn[b] = (len(d.get("tags", [])), len(nd.get("tags", [])))
```

- [ ] **Step 4: Add the aggregate properties**

In `src/scourgify/wrangle.py`, immediately after the existing `n_books` property (which ends with the `return len({b for ch in self.changes.values() for b in ch})` line), add:

```python
    # The SAFETY aggregates the guards and reports read. Derived, not accumulated, so narrowing
    # the plan (restrict) narrows these too instead of judging a scoped write on library totals.
    @property
    def lostF(self) -> int: return sum(1 for lf, _ in self.lost.values() if lf)

    @property
    def lostC(self) -> int: return sum(1 for _, lc in self.lost.values() if lc)

    @property
    def tagsB(self) -> int: return sum(b for b, _ in self.tagn.values())

    @property
    def tagsA(self) -> int: return sum(a for _, a in self.tagn.values())
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run tests/test_plan.py && uv run tests/test_core.py`
Expected: PASS — including the pre-existing `(p.tagsB, p.tagsA) == (3, 2)` assertion.

- [ ] **Step 6: Commit**

```bash
git add src/scourgify/wrangle.py tests/test_plan.py
git commit -m "refactor(wrangle): Plan's SAFETY counters are per-book, aggregates derived

lostF/lostC/tagsB/tagsA keep their names and meaning but become properties
over per-book dicts. No behavior change on its own; it is what lets a scoped
plan be judged on its own books instead of on library-wide totals.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: `Plan.restrict()` + `wrangle apply --books`

**Files:**
- Modify: `src/scourgify/wrangle.py` (new `Plan.restrict` after `n_books`/the properties; `main()`'s parser and `apply` branch)
- Test: `tests/test_plan.py`

**Interfaces:**
- Consumes: `Plan.lost` / `Plan.tagn` (Task 3); `select.parse_books` (Task 1).
- Produces: `Plan.restrict(ids) -> Plan` (returns self, mutates in place); `--books SPEC` on `scourgify apply`.

**Scope note:** `--books` goes on `apply` only, **not** on `audit`. `audit_report()` reads `self.decisions`, `self.before` and `self.after`, and transform's decision log is a flat `(kind, where, before, after)` tuple with no book id — there is nothing to filter on. `audit` stays the full-library per-value view by definition; the scoped diff is what `apply --books` previews. This is a deliberate narrowing of the spec, which said "audit/apply".

- [ ] **Step 1: Write the failing test**

Append to `tests/test_plan.py`, before the `if __name__` block:

```python
def test_restrict_narrows_the_write_set_and_the_guards():
    """restrict() narrows what gets WRITTEN, not what gets READ — transform still sees the whole
    library (tagcanon, known_chars). The guards then judge the scoped books: book 1's data loss
    must not abort a write that only touches book 2."""
    lib = tempfile.mkdtemp()
    build(os.path.join(lib, "metadata.db"),
          [dict(id=1, added="2026-01-01", tags=["Complete"]),
           dict(id=2, added="2026-01-02", tags=["Complete", "Keeper"])],
          custom=[("fandoms", {1: "Ghost", 2: "Real Fandom"})]).close()
    old = os.environ.get("CALIBRE_LIBRARY"); os.environ["CALIBRE_LIBRARY"] = lib
    try:
        cfg = load_config(path="/nonexistent/config.toml")
        m = maps(fan={"Ghost": ""}, junk_exact={"complete"})

        full = wrangle.plan(cfg, m)
        assert full.n_books == 2 and full.lostF == 1
        try:
            full.guard(); assert False, "expected the data-loss guard to abort the full plan"
        except SystemExit:
            pass

        scoped = wrangle.plan(cfg, m).restrict([2])
        assert scoped.n_books == 1                       # only book 2 is in the write set
        assert set(scoped.diffs) == {2}
        assert 1 not in scoped.changes["tags"]
        assert (scoped.lostF, scoped.lostC) == (0, 0)    # book 1's loss is out of scope
        assert (scoped.tagsB, scoped.tagsA) == (2, 1)
        scoped.guard()                                   # no abort — this is the regression
        assert scoped.tagcanon == full.tagcanon          # global context survived the narrowing
    finally:
        os.environ.pop("CALIBRE_LIBRARY", None) if old is None else os.environ.__setitem__("CALIBRE_LIBRARY", old)


def test_restrict_to_nothing_is_a_clean_no_op():
    lib = tempfile.mkdtemp()
    build(os.path.join(lib, "metadata.db"),
          [dict(id=1, added="2026-01-01", tags=["Complete"])]).close()
    old = os.environ.get("CALIBRE_LIBRARY"); os.environ["CALIBRE_LIBRARY"] = lib
    try:
        cfg = load_config(path="/nonexistent/config.toml")
        p = wrangle.plan(cfg, maps(junk_exact={"complete"})).restrict([999])
        assert p.n_books == 0 and not p.diffs and (p.tagsB, p.tagsA) == (0, 0)
        p.guard()
    finally:
        os.environ.pop("CALIBRE_LIBRARY", None) if old is None else os.environ.__setitem__("CALIBRE_LIBRARY", old)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run tests/test_plan.py`
Expected: FAIL — `AttributeError: 'Plan' object has no attribute 'restrict'`

- [ ] **Step 3: Implement `restrict`**

In `src/scourgify/wrangle.py`, immediately after the four properties added in Task 3, add:

```python
    def restrict(self, ids) -> "Plan":
        """Narrow this plan to `ids` and return self. The full-library compute STAYS — transform
        needs global context (tagcanon majority spelling, known_chars), so scoping the read would
        silently change the answer for the selected books. Only the write set narrows: changes,
        diffs, and the per-book SAFETY counters, so preview/guard/step/write all see the scope and
        the guards judge these books rather than the library. before/after/decisions are left whole
        — they feed the library-wide audit report, which is not scopeable (see main())."""
        keep = set(ids)
        for lab in list(self.changes):
            kept = {b: v for b, v in self.changes[lab].items() if b in keep}
            if kept: self.changes[lab] = kept
            else: del self.changes[lab]
        self.diffs = collections.defaultdict(dict, {b: d for b, d in self.diffs.items() if b in keep})
        self.lost = {b: v for b, v in self.lost.items() if b in keep}
        self.tagn = {b: v for b, v in self.tagn.items() if b in keep}
        return self
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run tests/test_plan.py`
Expected: PASS — all tests.

- [ ] **Step 5: Add `--books` to the `apply` CLI**

In `src/scourgify/wrangle.py`, in `main()`, add this argument immediately after the `--step` line:

```python
    p.add_argument("--books", default="", metavar="SPEC",
                   help="with `apply`: only these books — '1,2,3', '10-20', '@ids.txt' (audit is always library-wide)")
```

Then replace the `elif a.command == "apply":` block body with:

```python
    elif a.command == "apply":
        do_write = a.apply or a.step
        p = plan(cfg, maps)                        # ONE compute: preview, guards, step, and write all read it
        if a.books:
            from scourgify import select
            want = select.parse_books(a.books)
            p.restrict(want)                       # narrows the WRITE set; the read stays library-wide
            print(f"  scope: {len(want)} book(s) by id -> {p.n_books} with changes")
        p.preview(write=do_write)
        p.guard(a.force)
        if a.step: p.step()
        if do_write: p.write(a.force)
        else: print("Re-run: scourgify apply --apply   (Calibre closed; writes shell out to calibre-debug)")
```

- [ ] **Step 6: Verify the CLI end-to-end against the fixture library**

Run:
```bash
uv run tests/test_plan.py && uv run tests/test_core.py && uv run tests/test_cli.py
```
Expected: PASS.

Then a live smoke against the real library (read-only — no `--apply`):
```bash
uv run scourgify apply --books 1,2,3
```
Expected: a `scope: 3 book(s) by id -> N with changes` line, a preview covering at most those three books, and the closing `Re-run:` hint. No write.

- [ ] **Step 7: Commit**

```bash
git add src/scourgify/wrangle.py tests/test_plan.py
git commit -m "feat(wrangle): apply --books scopes the write set

restrict() narrows changes/diffs/SAFETY-counters after the full-library
compute — the read has to stay whole because transform needs global context
(tagcanon, known_chars), so scoping it would change the answer for the very
books being selected. The guards now judge the scoped books: one book's data
loss elsewhere in the library no longer aborts a fifty-book write.

audit stays library-wide: its report reads transform's decision log, whose
tuples carry no book id, so there is nothing to filter on.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: `staleness --books`

**Files:**
- Modify: `src/scourgify/staleness.py` (`compute()` signature and return; `main()`)
- Test: `tests/test_staleness_scope.py` (create)

**Interfaces:**
- Consumes: `select.parse_books` (Task 1).
- Produces: `staleness.compute(stale_years=2.0, dead_years=5.0, books=None) -> tuple[str, list]` — `books` is an iterable of ids or `None` for the whole library. `--books SPEC` on `scourgify staleness`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_staleness_scope.py`:

```python
#!/usr/bin/env python3
"""Pins staleness's book scope: --books narrows which books get re-derived, and narrows
nothing else. The pure `derive` rule is covered in test_core.py.
No framework:  uv run tests/test_staleness_scope.py   (also pytest-collectable)."""
import contextlib, datetime, os, sys, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from fixture_db import build
from scourgify import staleness


@contextlib.contextmanager
def library():
    """Three In-Progress books whose #updated ages put every one of them over the dead line."""
    with tempfile.TemporaryDirectory() as td:
        lib = os.path.join(td, "library"); os.makedirs(lib)
        long_ago = (datetime.date.today() - datetime.timedelta(days=8 * 365)).isoformat()
        build(os.path.join(lib, "metadata.db"),
              [dict(id=i, added="2026-01-01") for i in (1, 2, 3)],
              custom=[("status", {i: "In-Progress" for i in (1, 2, 3)}),
                      ("updated", {i: long_ago for i in (1, 2, 3)})]).close()
        saved = os.environ.get("CALIBRE_LIBRARY")
        os.environ["CALIBRE_LIBRARY"] = lib
        try:
            yield
        finally:
            os.environ.pop("CALIBRE_LIBRARY", None) if saved is None else os.environ.__setitem__("CALIBRE_LIBRARY", saved)


def test_compute_whole_library_by_default():
    with library():
        label, rows = staleness.compute()
        assert label == "#status"
        assert sorted(b for b, _, _, _ in rows) == [1, 2, 3]
        assert {n for _, _, n, _ in rows} == {"Abandoned"}


def test_compute_books_narrows_the_rows():
    with library():
        _, rows = staleness.compute(books=[3, 1])
        assert sorted(b for b, _, _, _ in rows) == [1, 3]


def test_compute_books_empty_selects_nothing():
    with library():
        _, rows = staleness.compute(books=[])
        assert rows == []


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run tests/test_staleness_scope.py`
Expected: FAIL — `TypeError: compute() got an unexpected keyword argument 'books'`

- [ ] **Step 3: Add the `books` parameter to `compute`**

In `src/scourgify/staleness.py`, change `compute`'s signature and its book loop. Replace:

```python
def compute(stale_years: float = 2.0, dead_years: float = 5.0) -> tuple[str, list]:
    """-> (status_label, [(book, old, new, age_years), ...]) for books whose status would change."""
```

with:

```python
def compute(stale_years: float = 2.0, dead_years: float = 5.0,
            books=None) -> tuple[str, list]:
    """-> (status_label, [(book, old, new, age_years), ...]) for books whose status would change.
    books: an iterable of ids to restrict to, or None for the whole library. Each book's status
    depends only on its own #updated age, so a plain filter is the whole of the scoping."""
```

Then replace the loop:

```python
    rows = []
    for b, s in status.items():
```

with:

```python
    want = None if books is None else set(books)
    rows = []
    for b, s in status.items():
        if want is not None and b not in want: continue
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run tests/test_staleness_scope.py`
Expected: PASS — 3 tests.

- [ ] **Step 5: Add the CLI flag**

In `src/scourgify/staleness.py`, change the import line to:

```python
from scourgify.common import load_config, ro_connect, read_custom_column, run_writer, op_set_field
from scourgify import select
```

In `main()`, add after the `--dead-years` argument:

```python
    p.add_argument("--books", default="", metavar="SPEC",
                   help="only these books: '1,2,3', '10-20', '@ids.txt' (one id per line), or a combination")
```

and change the compute call plus the header print:

```python
Replace these two existing lines in `main()`:

```python
    label, rows = compute(a.stale_years, a.dead_years)
    print(f"staleness audit  (today={datetime.date.today()}, stale>={a.stale_years}y, dead>={a.dead_years}y)")
```

with these three:

```python
    books = select.parse_books(a.books) if a.books else None
    label, rows = compute(a.stale_years, a.dead_years, books)
    print(f"staleness audit  (today={datetime.date.today()}, stale>={a.stale_years}y, dead>={a.dead_years}y"
          + (f", scoped to {len(books)} book(s)" if books is not None else "") + ")")
```

- [ ] **Step 6: Verify against the real library (read-only)**

Run:
```bash
uv run tests/test_staleness_scope.py
uv run scourgify staleness --books 1-50
```
Expected: the header carries `scoped to 50 book(s)`, the transition table covers at most those books, and the closing hint says the run was a dry run. No write.

- [ ] **Step 7: Commit**

```bash
git add src/scourgify/staleness.py tests/test_staleness_scope.py
git commit -m "feat(staleness): --books narrows which books get re-derived

Each book's status depends only on its own #updated age, so scoping is a
plain row filter — no global context to preserve, unlike wrangle.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: document `--books`

**Files:**
- Modify: `CLAUDE.md`, `README.md`, `docs/USERGUIDE.md`
- Modify: `docs/superpowers/specs/2026-07-26-book-scope-and-full-library-test-design.md` (record the audit narrowing)

The flag spans three tools, so its documentation is one cross-cutting change rather than a rider on any single tool's task.

- [ ] **Step 1: Update `CLAUDE.md`**

In the `select.py` paragraph, after the sentence ending `so they can never disagree.`, add:

```
`parse_books()` owns the `--books` spec grammar (`1,2,3`, `10-20`, `@ids.txt`, or any
comma-combination; `@file` expands one level deep) and the `ids` pick mode selects exactly those
books — the one way `classify`, `wrangle apply` and `staleness` are pointed at a named set.
```

In the `wrangle.py` paragraph, after the sentence ending `(the CLI and the wizard drive the same object).`, add:

```
`Plan.restrict(ids)` narrows the WRITE set (`changes`/`diffs` and the per-book SAFETY counters)
*after* the full compute — `read_library` stays library-wide because `transform()` needs global
context (tagcanon majority spelling, `known_chars`), so scoping the read would change the answer
for the selected books. `apply --books` uses it; `audit` is deliberately library-wide (its report
reads transform's decision log, whose tuples carry no book id).
```

- [ ] **Step 2: Update `README.md`**

In `README.md`, the scope-flag list is at lines 255-260. Insert `--books` at the head of that list — it wins over the others, so it reads first. Change:

```
  `--incremental` = only new/changed books; `--last N` = the N most recently
```

to:

```
  `--books SPEC` = exactly these books by id (`1,2,3`, `10-20`, `@ids.txt` one per line, or a
  comma-combination — it wins over every other scope flag); `--incremental` = only new/changed
  books; `--last N` = the N most recently
```

Then, after line 228's targeted-redo sentence (`scourgify classify --last 30` … `--since 2026-06-01`), append: *`--books 1,2,3` or `--books @ids.txt` when you know exactly which books you mean; `apply --books` and `staleness --books` take the same spec.*

- [ ] **Step 3: Update `docs/USERGUIDE.md`**

In the cheat sheet block (around line 124), add a line after `scourgify classify --incremental`:

```bash
scourgify classify --books 1,2,3 # AI-tag exactly these books (also: apply --books, staleness --books)
```

- [ ] **Step 4: Correct the spec**

In `docs/superpowers/specs/2026-07-26-book-scope-and-full-library-test-design.md`, in the "Three wirings" table, change the `wrangle audit / apply` row's tool cell to `wrangle apply` and append to the "Why `wrangle` restricts instead of reading less" section:

```
`audit` is deliberately left library-wide. Its report reads `transform`'s decision log, whose
`(kind, where, before, after)` tuples carry no book id, so there is nothing to filter on; adding
one would change the log's shape for every consumer. The scoped diff is what `apply --books`
previews.
```

- [ ] **Step 5: Verify the whole suite still passes**

Run: `for t in tests/test_*.py; do echo "== $t"; uv run "$t" || break; done`
Expected: every file reports `N tests passed`.

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md README.md docs/USERGUIDE.md docs/superpowers/specs/2026-07-26-book-scope-and-full-library-test-design.md
git commit -m "docs: --books scope across classify/apply/staleness

Also records the one narrowing from the spec: audit stays library-wide
because its report reads a decision log with no book id in it.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

# Part B — the full-library test run

Everything below runs against the **real** library. The user has taken a backup. Findings are collected in one file as they occur; bugs get fixed on `develop` with a regression test each (Task 13).

**Findings file:** create `docs/superpowers/reports/2026-07-26-full-library-test-findings.md` at the start of Task 7 and append to it throughout. Each finding records: surface, command, expected, actual, severity (blocker / bug / papercut / cosmetic), and fix status.

### Task 7: Phase 0 — baseline and the id set

**Files:**
- Create: `docs/superpowers/reports/2026-07-26-full-library-test-findings.md`
- Create (scratchpad): `ids50.txt`, `baseline.json`

- [ ] **Step 1: Confirm the safety net**

```bash
uv run scourgify rollback --list
pgrep -l -i calibre || echo "calibre closed"
brctl status "$CALIBRE_LIBRARY" 2>/dev/null | head -5 || echo "no icloud status available"
```
Expected: a list of `ff_<ts>.db` backups; `calibre closed`; no pending iCloud uploads. If Calibre is running, stop and tell the user — every write step needs it closed.

- [ ] **Step 2: Snapshot metadata.db to the scratchpad**

`SP` must be **exported** — later steps read it from inside Python heredocs via `os.environ["SP"]`. Every subsequent Bash step in Part B re-exports it, since shell state does not persist between tool calls.

```bash
export SP=/private/tmp/claude-501/-Users-andrei-Developer-scourgify/ab16e4cb-93ce-4f43-aebb-5474f192e4bd/scratchpad
mkdir -p "$SP"
cp "$CALIBRE_LIBRARY/metadata.db" "$SP/metadata.pre-test.db"
ls -la "$SP/metadata.pre-test.db"
```
Expected: a ~24M file. This is independent of scourgify's own backups — it is the reference for the before/after diff, not a restore path.

- [ ] **Step 3: Record the pre-state**

```bash
python3 - "$SP/baseline.json" <<'PY'
import json, sqlite3, sys, os
db = os.environ["CALIBRE_LIBRARY"] + "/metadata.db"
c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
one = lambda q: c.execute(q).fetchone()[0]
base = {
    "books": one("SELECT count(*) FROM books"),
    "tag_assignments": one("SELECT count(*) FROM books_tags_link"),
    "distinct_tags": one("SELECT count(*) FROM tags"),
    "comments": one("SELECT count(*) FROM comments"),
    "wrangled_stamped": one("SELECT count(*) FROM custom_column_22"),
}
for cid, label in c.execute("SELECT id, label FROM custom_columns"):
    for tbl in (f"books_custom_column_{cid}_link", f"custom_column_{cid}"):
        try:
            base[f"col_{label}"] = one(f"SELECT count(*) FROM {tbl}")
            break
        except sqlite3.OperationalError:
            continue
json.dump(base, open(sys.argv[1], "w"), indent=2)
print(json.dumps(base, indent=2))
PY
```
Expected: `books` is 7949, `wrangled_stamped` is 7948. Record the whole object in the findings file under a "Baseline" heading.

- [ ] **Step 4: Pick the 50 book ids**

Deterministic so the run is reproducible and re-runnable: seed 20260726, sample from the real id set.

```bash
python3 - "$SP/ids50.txt" <<'PY'
import os, random, sqlite3, sys
c = sqlite3.connect(f"file:{os.environ['CALIBRE_LIBRARY']}/metadata.db?mode=ro", uri=True)
# only books with a description worth classifying — a 50-book sample of empty comments
# would test nothing but the thin-description drop path.
ids = [r[0] for r in c.execute(
    "SELECT b.id FROM books b JOIN comments cm ON cm.book=b.id WHERE length(cm.text) > 200 ORDER BY b.id")]
random.seed(20260726)
pick = sorted(random.sample(ids, 50))
open(sys.argv[1], "w").write("# scourgify full-library test — 50 books, seed 20260726\n"
                             + "\n".join(map(str, pick)) + "\n")
print(f"{len(ids)} candidates -> 50 picked: {pick[:5]} … {pick[-3:]}")
PY
cat "$SP/ids50.txt" | head -8
```
Expected: 50 ids written. Record the file's contents in the findings file so the run can be reproduced.

- [ ] **Step 5: Verify the id file drives the parser**

```bash
uv run python -c "
import sys; sys.path.insert(0, 'src')
from scourgify import select
ids = select.parse_books('@$SP/ids50.txt')
print(len(ids), ids[:5])
assert len(ids) == 50
"
```
Expected: `50 [...]` — the `@file` path works against a real file with a comment header.

- [ ] **Step 6: Commit the findings file skeleton**

```bash
git add docs/superpowers/reports/2026-07-26-full-library-test-findings.md
git commit -m "docs: findings file for the full-library test run

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Phase 2 — read-only at full scale, then the go/no-go checkpoint

Read-only throughout. Nothing here writes to the library.

- [ ] **Step 1: `setup` health check**

```bash
uv run scourgify setup --yes 2>&1 | tee "$SP/out.setup.txt"
```
Expected: reports FanFicFare presence, all 11 columns present, config found at `~/.config/scourgify/config.toml`. It must not offer to create columns. Record anything it flags.

- [ ] **Step 2: Full `audit`**

```bash
uv run scourgify audit 2>&1 | tee "$SP/out.audit.txt"
tail -40 "$SP/out.audit.txt"
```
Expected: the distinct-value delta table per column, a `SAFETY` line, and the per-rule examples. Record: the delta per column, `losing last fandom` / `losing last character` counts, and the `tag assignments: B -> A` numbers. A non-zero loss count is a **blocker** for Phase 3 — flag it to the user rather than proceeding.

- [ ] **Step 3: `apply` dry run (the write preview and the guards)**

```bash
uv run scourgify apply 2>&1 | tee "$SP/out.apply-dry.txt"
```
Expected: `PRE-APPLY (no write)`, per-column changed-book counts, the mass/unique detail, the `SAFETY` line, and the `Re-run:` hint. No `ABORT`. If it aborts, that is the guardrail working — record which guard and stop for the user.

- [ ] **Step 4: `apply --books` dry run (the new flag at real scale)**

```bash
uv run scourgify apply --books "@$SP/ids50.txt" 2>&1 | tee "$SP/out.apply-dry-50.txt"
```
Expected: a `scope: 50 book(s) by id -> N with changes` line and a preview strictly narrower than Step 3's. Cross-check: every book id named in the detail lines appears in `ids50.txt`.

- [ ] **Step 5: `staleness` dry run, full and scoped**

```bash
uv run scourgify staleness 2>&1 | tee "$SP/out.staleness-dry.txt"
uv run scourgify staleness --books "@$SP/ids50.txt" 2>&1 | tee "$SP/out.staleness-dry-50.txt"
```
Expected: a transition table (`In-Progress → Hiatus` etc.) with counts, and the scoped run's counts ≤ the full run's. Record both.

- [ ] **Step 6: `classify` scope resolution over the 50 (one book billed)**

There is no dry-run flag on classify, and interrupting a run mid-flight leaves a partial proposal. `--batch 1` is the cheap way to see the scope line, the spend gate and the dashboard for real: it resolves all 50 targets but processes exactly one book.

```bash
uv run scourgify classify --books "@$SP/ids50.txt" --engine gemini --text-fallback --batch 1 --yes 2>&1 | tee "$SP/out.classify-scope.txt"
```
Expected: `scope: 50 book(s) by id -> 50 books`, `1 to do this run`, and a one-row proposal. Cost: one request. Record whether the spend gate fired — it is documented to gate cloud runs above 200 books, so 50 passing straight through is expected; note the observed behavior either way.

Then reset so Phase 4 starts clean:

```bash
rm -f ~/.config/scourgify/data/classify_proposal.csv
```

- [ ] **Step 7: Rollback listing and the `--books` error paths**

```bash
uv run scourgify rollback --list
uv run scourgify apply --books "notanid"      ; echo "rc=$?"
uv run scourgify apply --books "9-3"          ; echo "rc=$?"
uv run scourgify apply --books "@/nope.txt"   ; echo "rc=$?"
uv run scourgify apply --books "99999999"     ; echo "rc=$?"
```
Expected: the first three exit non-zero with a plain one-line `--books:` message and no traceback; the last succeeds with `scope: 1 book(s) by id -> 0 with changes` (an absent id is not an error).

- [ ] **Step 8: Plain (rich-absent) rendering**

```bash
uv run --isolated --no-project --with . scourgify audit 2>&1 | tail -20
```
Expected: readable plain-text output with no traceback — `report.py`'s plain path. If `rich` turns out to be a hard dependency of the package, note that this check could not isolate it and instead verify the non-TTY path with `uv run scourgify audit | cat`.

- [ ] **Step 9: STOP — go/no-go checkpoint**

Write a summary to the findings file and present it to the user:
- books affected per column by a full `apply`
- the SAFETY numbers
- staleness transition counts
- any finding from Steps 1-8

Then ask: **proceed to the library-wide writes in Phase 3, or skip to the 50-book classify work in Phase 4?** Do not run Task 9 without an explicit go.

---

### Task 9: Phase 3 — library-wide writes and the idempotency claim

**Gated on the Task 8 go/no-go.** Calibre must be closed.

- [ ] **Step 1: Confirm Calibre is closed and note the backup count**

```bash
pgrep -l -i calibre || echo "calibre closed"
uv run scourgify rollback --list | tail -3
```
Expected: `calibre closed`. Record the newest backup's name — Step 2 must add a new one.

- [ ] **Step 2: `apply --apply`**

```bash
uv run scourgify apply --apply 2>&1 | tee "$SP/out.apply-write.txt"
```
Expected: `APPLY`, then the write. Verify a fresh `ff_<ts>.db` appeared via `uv run scourgify rollback --list`. If the auto-backup did not happen, that is a **blocker** — record it and stop.

- [ ] **Step 3: Prove idempotency**

```bash
uv run scourgify apply 2>&1 | tee "$SP/out.apply-dry-2.txt"
```
Expected: every per-column `books changed:` count is **0**, and `n_books` is 0. Any non-zero count means a pass is not idempotent — record the column, the example values, and treat it as a **bug** for Task 13.

- [ ] **Step 4: `staleness --apply`**

```bash
uv run scourgify staleness --apply 2>&1 | tee "$SP/out.staleness-write.txt"
uv run scourgify staleness 2>&1 | tail -5
```
Expected: the write reports N books re-derived; the immediate re-run reports **0** re-derivations. A non-zero second run is a **bug**.

- [ ] **Step 5: Confirm nothing catastrophic happened**

Re-run the Task 7 Step 3 baseline script into `$SP/after-phase3.json` and diff it against `baseline.json`. Expected: `books` unchanged at 7949, `comments` unchanged, `distinct_tags` and `tag_assignments` moved by the amounts the audit predicted (compare against `out.audit.txt`'s `tag assignments: B -> A` line). A book-count change is a **blocker**.

- [ ] **Step 6: Record**

Append the Phase 3 results to the findings file: what changed, whether both passes were idempotent, whether the predicted numbers matched the actual ones.

---

### Task 10: Phase 4 — classify over the 50, gemini then openai

Cost: roughly €0.10–0.40 total. Calibre closed for the `--apply` steps.

- [ ] **Step 1: Engine bake-off**

```bash
uv run scourgify classify --bakeoff --books "@$SP/ids50.txt" 2>&1 | tee "$SP/out.bakeoff.txt"
```
Expected: the same sample books through gemini, openai and apple, side by side, and **no proposal written** (`ls -la ~/.config/scourgify/data/classify_proposal.csv` must be unchanged). Record how the three engines differ and whether any errored. `claude`/`mistral` should be reported as unusable (no key), not crash.

- [ ] **Step 2: Archive the current proposal, then run gemini over the 50**

```bash
cp ~/.config/scourgify/data/classify_proposal.csv "$SP/proposal.before.csv"
uv run scourgify classify --books "@$SP/ids50.txt" --engine gemini --text-fallback --fresh --yes 2>&1 | tee "$SP/out.classify-gemini.txt"
cp ~/.config/scourgify/data/classify_proposal.csv "$SP/proposal.gemini.csv"
```
Expected: `scope: 50 book(s) by id -> 50 books`, the live dashboard (progress, tagged/failed/rate, throughput sparkline, rising candidates), and a proposal with ~50 rows. Record: wall-clock, tagged count, failed count, and whether any book hit Gemini's `PROHIBITED_CONTENT` block.

- [ ] **Step 3: Check the failure and candidate artifacts**

```bash
cat ~/.config/scourgify/data/classify_failures.csv
head -20 ~/.config/scourgify/data/classify_newtags_ranked.csv
python3 -c "
import csv; rows=list(csv.DictReader(open('$SP/proposal.gemini.csv')))
print(len(rows),'rows;',sum(1 for r in rows if r['added_tags']),'with tags;',sum(1 for r in rows if r['proposed_new']),'with candidates')"
```
Expected: failures (if any) named with a reason; ranked candidates aggregated. A `PROHIBITED_CONTENT` row here is documented behavior, not a bug — record it as an observation.

- [ ] **Step 4: Apply the gemini proposal**

```bash
pgrep -l -i calibre || echo "calibre closed"
uv run scourgify classify --apply 2>&1 | tee "$SP/out.classify-apply.txt"
ls -la ~/.config/scourgify/data/ | grep classify_proposal_applied | tail -2
```
Expected: tags written, every processed book stamped `#wrangled`, and the proposal archived to `classify_proposal_applied_<ts>.csv`.

- [ ] **Step 5: Verify the tags actually landed**

```bash
python3 - <<'PY'
import csv, os, sqlite3
sp = os.environ["SP"]
c = sqlite3.connect(f"file:{os.environ['CALIBRE_LIBRARY']}/metadata.db?mode=ro", uri=True)
have = {}
for b, t in c.execute("SELECT l.book, t.name FROM books_tags_link l JOIN tags t ON t.id=l.tag"):
    have.setdefault(b, set()).add(t)
missing = []
for r in csv.DictReader(open(f"{sp}/proposal.gemini.csv")):
    for t in filter(None, r["added_tags"].split("; ")):
        if t not in have.get(int(r["book_id"]), set()):
            missing.append((r["book_id"], t))
print("proposed tags missing from the library:", len(missing), missing[:5])
try:                                  # #wrangled is column 22; either storage shape may be in use
    stamped = {r[0] for r in c.execute("SELECT book FROM books_custom_column_22_link")}
except sqlite3.OperationalError:
    stamped = {r[0] for r in c.execute("SELECT book FROM custom_column_22")}
want = {int(l) for l in open(f"{sp}/ids50.txt") if l.strip() and not l.startswith("#")}
print("of the 50, unstamped:", sorted(want - stamped))
PY
```
Expected: zero missing tags, zero unstamped books. Either being non-zero is a **blocker**.

- [ ] **Step 6: The same 50 books through openai**

```bash
uv run scourgify classify --books "@$SP/ids50.txt" --engine openai --text-fallback --fresh --yes 2>&1 | tee "$SP/out.classify-openai.txt"
cp ~/.config/scourgify/data/classify_proposal.csv "$SP/proposal.openai.csv"
```
Expected: same scope line, same 50 books. Record wall-clock, tagged/failed counts, and how they compare to gemini's.

- [ ] **Step 7: Head-to-head engine diff**

```bash
python3 - <<'PY'
import csv, os
sp = os.environ["SP"]
load = lambda f: {int(r["book_id"]): (set(filter(None, r["added_tags"].split("; "))),
                                      r["title"]) for r in csv.DictReader(open(f))}
g, o = load(f"{sp}/proposal.gemini.csv"), load(f"{sp}/proposal.openai.csv")
both = sorted(set(g) & set(o))
agree = sum(len(g[b][0] & o[b][0]) for b in both)
gonly = sum(len(g[b][0] - o[b][0]) for b in both)
oonly = sum(len(o[b][0] - g[b][0]) for b in both)
print(f"{len(both)} books compared: {agree} tags agreed, {gonly} gemini-only, {oonly} openai-only")
print(f"mean tags/book — gemini {sum(len(g[b][0]) for b in both)/len(both):.2f}"
      f"  openai {sum(len(o[b][0]) for b in both)/len(both):.2f}")
for b in both[:8]:
    print(f"  #{b} {g[b][1][:40]:40} G={sorted(g[b][0])} O={sorted(o[b][0])}")
PY
```
Record the whole output in the findings file — this is a deliverable, not just a check.

- [ ] **Step 8: The 1-by-1 step review and the rejects log**

```bash
wc -l ~/.config/scourgify/data/rejects.csv 2>/dev/null || echo "no rejects yet"
uv run scourgify classify --apply --step
```
This one is interactive — walk a handful of books, untick some tags, and confirm. Expected: the checklist renders, unticked tags do not get written, and `~/.config/scourgify/data/rejects.csv` gains rows classed as classify rejects (log-only, not auto-suppressible). Verify:

```bash
tail -5 ~/.config/scourgify/data/rejects.csv
```

- [ ] **Step 9: Record**

Append Phase 4 to the findings file: per-engine timings, tagged/failed counts, the head-to-head table, the artifact checks, and anything that surprised you.

---

### Task 11: Phase 5 — promote, backfill, overrides

- [ ] **Step 1: Note that `promote`'s default engine is unusable here**

```bash
uv run scourgify promote --limit 3 2>&1 | head -10
```
`promote --engine` defaults to `claude` and `ANTHROPIC_API_KEY` is unset. Expected: a clear message naming the missing key, not a traceback. Record the actual behavior — an unhelpful failure here is a **papercut** worth fixing in Task 13.

- [ ] **Step 2: Adjudicate the new-tag candidates, cross-model**

```bash
uv run scourgify promote --engine gemini --verify-with openai --limit 20 --yes 2>&1 | tee "$SP/out.promote.txt"
head -20 ~/.config/scourgify/data/promote_review.csv
```
Expected: advocate (gemini) then skeptic (openai) verdicts, written to `promote_review.csv`. Record the verdict distribution and any disagreement between the two models.

- [ ] **Step 3: Fold the verdicts into `overrides/`**

```bash
cp -r ~/.config/scourgify/overrides "$SP/overrides.before"
uv run scourgify promote --apply 2>&1 | tee "$SP/out.promote-apply.txt"
diff -r "$SP/overrides.before" ~/.config/scourgify/overrides | head -30
```
Expected: `overrides/classify_vocab.txt` and/or `overrides/promote_aliases.csv` gain lines; `.bak-<ts>` backups appear alongside. The review CSV is archived to `promote_review_applied_<ts>.csv`.

- [ ] **Step 4: Backfill — the deterministic close of the loop**

```bash
uv run scourgify promote --backfill 2>&1 | tee "$SP/out.backfill-dry.txt"
pgrep -l -i calibre || echo "calibre closed"
uv run scourgify promote --backfill --apply 2>&1 | tee "$SP/out.backfill-write.txt"
```
Expected: the dry run previews which promoted/aliased tags land on which books (union of the books that proposed them, from the archived proposals plus `promote_ledger.csv`); the apply writes them with no LLM call. Verify a couple of the named books actually gained the tag via sqlite.

- [ ] **Step 5: Rejects → overrides**

```bash
uv run scourgify overrides 2>&1 | tee "$SP/out.overrides-dry.txt"
```
Expected: the deterministic (wrangle) rejects are offered as identity-override lines; the classify rejects from Task 10 Step 8 are reported as log-only, not converted. If there are no wrangle rejects, run `uv run scourgify apply --step --books "@$SP/ids50.txt"`, reject a change, then re-run this step. Then:

```bash
uv run scourgify overrides --apply 2>&1 | tee "$SP/out.overrides-apply.txt"
uv run scourgify apply --books "@$SP/ids50.txt" 2>&1 | tail -10
```
Expected: the rejected change no longer appears in the preview — the override suppressed it. That round trip is the point of the feature; record whether it held.

- [ ] **Step 6: Record, and snapshot the post-Phase-5 state**

Re-run the Task 7 Step 3 baseline script into `$SP/after-phase5.json` — Task 12 Step 7 compares against it to prove the rollback round trip returned the library to exactly this state.

Then append Phase 5 to the findings file, including the cross-model disagreement rate and whether the reject→override→gone round trip worked.

---

### Task 12: Phase 6 — the wizard under a PTY, and Phase 7 — rollback

**Files:**
- Create: `tests/drive_wizard_live.py`

- [ ] **Step 1: Read the existing driver**

Read `tests/drive_wizard.py` in full. It drives the real TTY surface in a pseudo-terminal against a *fixture* library: `MENU_KEYS` walks the landing menu, `RULES` maps prompt regexes to canned answers, `CHECKS` asserts on the cleaned transcript. The live variant reuses that machinery with a different target and different answers.

- [ ] **Step 2: Write the live driver**

Create `tests/drive_wizard_live.py` — a copy of `drive_wizard.py`'s PTY loop with three changes:

1. **No fixture.** Drop the `build(...)`/`tempfile` setup; use the real `CALIBRE_LIBRARY` and the real `~/.config/scourgify` home from the ambient environment (do not set `SCOURGIFY_HOME`).
2. **Answers that write.** Replace `RULES` with:

```python
RULES = [
    (r"choose \[n/a/s\]", "n\n"),      # classify scope -> new/changed (the cheap default)
    (r"choose \[a/r/s\]", "s\n"),      # wrangle apply menu -> SKIP (phase 3 already covered it)
    (r"choose \[a/r/k/d\]", "a\n"),    # review menu -> APPLY the proposal
    (r"⏎ apply ticked", "\n"),         # checklist -> accept the ticked set
    (r"natural next step.*\(y\)", "y\n"),
    (r"re-derive .*\(True\)", "y\n"),  # staleness confirm -> write
    (r"remaining steps\?", "y\n"),
]
```

3. **Live checks.** Replace `CHECKS` with assertions that fit a real library:

```python
CHECKS = [
    (r"7,?9\d\d books|\d{4} books", "header shows the real library size"),
    (r"what would you like to do\?", "landing menu appeared"),
    (r"scope", "classify scope menu appeared"),
    (r"€|estimate|cost", "per-engine cost estimate rendered"),
    (r"consistent ✓|re-derive", "staleness stage ran"),
    (r"candidates to adjudicate|no new-tag candidates|previously-adjudicated", "promote stage ran"),
    (r"backfill|done ✓", "backfill stage ran"),
]
```

Keep the 120s deadline from the original but raise it to 600 — a real classify stage calls an engine.

- [ ] **Step 3: Dry-run the driver's flow against the fixture first**

```bash
uv run tests/drive_wizard.py
```
Expected: the existing driver still passes end to end. This confirms the PTY machinery works on this machine before pointing it at the real library.

- [ ] **Step 4: Run the live driver**

```bash
pgrep -l -i calibre || echo "calibre closed"
uv run tests/drive_wizard_live.py 2>&1 | tee "$SP/out.wizard-live.txt"
```
Expected: every CHECK passes and the wizard exits 0. Any `STALLED` output means a prompt the rules do not cover — record the transcript tail, add the rule, re-run. Record every rendering oddity in the findings file (misaligned tables, a truncated dashboard, a prompt whose default is wrong).

- [ ] **Step 5: The Calibre-open refusal**

```bash
open -a calibre; sleep 15; pgrep -l -i calibre
uv run scourgify staleness --apply ; echo "rc=$?"
osascript -e 'quit app "calibre"'; sleep 5; pgrep -l -i calibre || echo "calibre closed"
```
Expected: the write refuses with a clear message naming Calibre, and exits non-zero. A write that goes through anyway is a **blocker**.

- [ ] **Step 6: The `--force` override**

Construct a guardrail trip deliberately: an over-broad junk regex is exactly the failure `tag_loss_guard` exists to catch (it aborts on a >25% tag shrink losing >200 assignments).

```bash
cp ~/.config/scourgify/overrides/junk.txt "$SP/junk.bak" 2>/dev/null || touch "$SP/junk.bak"
echo '/^.*$/' >> ~/.config/scourgify/overrides/junk.txt     # matches every tag — must abort
uv run scourgify apply ; echo "rc=$?"
```
Expected: `ABORT: tags would shrink … Check junk.txt / overrides for an over-broad rule`, non-zero exit, no write. If it does **not** abort, that is a **blocker**.

Then confirm the escape hatch reaches the write path without taking it — `--force` with no `--apply` still stops at the `Re-run:` hint:

```bash
uv run scourgify apply --force ; echo "rc=$?"
```
Expected: exit 0, the preview renders, no `ABORT`, and the closing `Re-run:` hint. Do **not** add `--apply` here — the point is that `--force` clears the guard, not that a library-wide tag wipe can be committed.

Restore immediately:

```bash
cp "$SP/junk.bak" ~/.config/scourgify/overrides/junk.txt
uv run scourgify apply 2>&1 | tail -3      # confirm the guard is quiet again
```

- [ ] **Step 7: Phase 7 — roll back, then roll the rollback back**

```bash
uv run scourgify rollback --list
uv run scourgify rollback --yes 2>&1 | tee "$SP/out.rollback.txt"
uv run scourgify rollback --list      # the pre-restore snapshot of the CURRENT db must now be newest
```
Expected: the newest backup is restored, and a snapshot of the just-replaced db appears in the list — that is what makes the rollback reversible. Verify the library moved backwards (re-run the baseline script and compare), then:

```bash
uv run scourgify rollback --yes       # restore the snapshot taken during the rollback
```
Expected: the library returns to its post-Phase-5 state. Re-run the baseline script and confirm the numbers match `after-phase5`. If they do not, the reversibility claim in CLAUDE.md is wrong — a **blocker** finding.

- [ ] **Step 8: Commit the live driver**

```bash
git add tests/drive_wizard_live.py
git commit -m "test(wizard): PTY driver for the live library, writes included

Not in CI (not named test_*) and not runnable on a fixture — it needs a real
library and real API keys. The pre-release companion to drive_wizard.py:
same PTY machinery, answers that say yes at the write gates.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 13: Fix what the run found

For each finding classed **blocker** or **bug**, in severity order:

- [ ] **Step 1: Write a failing regression test**

Add it to the existing `tests/test_*.py` that owns the surface (`test_plan.py` for wrangle's Plan, `test_classify_run.py` for the classify pipeline, `test_selection.py` for scope, `test_promote.py` for promote, `test_overrides.py` for the overrides round trip). Create a new `tests/test_<surface>.py` only if none fits — a new file is in CI by existing.

- [ ] **Step 2: Run it to verify it fails, and that it fails for the right reason**

Run: `uv run tests/test_<file>.py`
Expected: FAIL, with a message that describes the bug rather than a setup error.

- [ ] **Step 3: Fix the bug**

Smallest change that makes the test pass. If the fix touches a rendering path, keep it inside `report.py` — no call site gets a second plain renderer.

- [ ] **Step 4: Run the whole suite**

Run: `for t in tests/test_*.py; do echo "== $t"; uv run "$t" || break; done`
Expected: every file reports `N tests passed`.

- [ ] **Step 5: Commit, one bug per commit**

```bash
git add <files>
git commit -m "fix(<module>): <what was wrong>

<why it happened, and what the regression test pins>

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

- [ ] **Step 6: Finalize and commit the findings report**

Complete `docs/superpowers/reports/2026-07-26-full-library-test-findings.md`: every finding with its severity and fix status, the engine head-to-head table, the cost actually spent, and a short "what is not covered" section naming the surfaces this run did not reach.

```bash
git add docs/superpowers/reports/2026-07-26-full-library-test-findings.md
git commit -m "docs: full-library test run findings

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

- [ ] **Step 7: Report to the user**

Summarize: what was tested, what broke, what was fixed, what is still open, and what the run cost.
