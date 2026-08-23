# Synopsis pass (#69) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every book ends up with a *settled* synopsis — a spoiler-safe back-cover description
either generated on-device from the book's own prose or inspected-and-kept — marked by a
`#synopsized` datetime stamp, and `classify --text-fallback` is deleted.

**Architecture:** A new tool module `src/scourgify/synopsis.py` in the house shape (pure
prompt/parse helpers + a `Plan` resolved once + `run()` that funnels writes through
`common.run_writer`). Queue membership lives in `select.pick("unsynopsized")` — the one owner of
"which books does this run operate on". Failures land in a `synopsis_failures.csv` artifact so the
queue is finite on the failure side. FanFicFare's Comments→"New Only" switch is read from the
library db and enforced as a pre-flight `GuardrailError`.

**Tech Stack:** Python stdlib + `rich` (via `report.py`/`ui.py` only) + Calibre's `calibre-debug`
writer + the Apple Foundation Models on-device engine (`engines.Apple`).

**Spec:**
- `docs/superpowers/plans/2026-08-20-synopsis-pass.md` (build briefing)
- GitHub issue #69 (**decision authority** where the two disagree)
- `CLAUDE.md` / `AGENTS.md` (house architecture — hard constraints)

---

## Verified facts (measured 2026-08-23, before any code)

**(a) FanFicFare's `std_cols_newonly` IS readable read-only from the library db.**

```
sqlite3 "file:metadata.db?mode=ro" \
  "select val from preferences where key='namespaced:FanFicFarePlugin:settings';"
```
returns a JSON blob containing
`"std_cols_newonly": {"authors": false, "comments": false, ..., "title": true}`.
`setup.py` already parses this exact blob (`setup.setup`, `[2] FanFicFare`).

⇒ **The guard is automatic** (decision 7's primary branch). No interactive-confirm degradation.
On the author's own library `comments` is currently `false`, so the guard will genuinely fire.

**(b) The apple engine's per-request context is 4096 tokens, hard.**

Probing `swiftc -O src/scourgify/afm.swift` with real extracted prose from a library EPUB:

| prompt prose | result |
|---|---|
| 15,000 chars | OK |
| 17,000 chars | OK |
| 18,000 chars | `exceededContextWindowSize … Content contains 4165 tokens, which exceeds the maximum allowed context size of 4096.` |
| 24,000 chars | FAIL (4308 tokens) |

Real English prose measures ≈ **4.3 chars/token**. Prompt **and** generated response count
against the same 4096. ⇒ chunk size **10,000 chars** (≈2,300 tokens) leaves ample room for the
prompt template and the answer. Latency measured 0.6–3.8 s per request.

**(c) `#updated` may be absent.** `select.resynopsize` therefore treats a missing `#updated` as
"never auto-refresh" — the stamp alone governs.

## Global Constraints

- **uv is the only supported installer.** Never `pip`/`pipx` in code, docs, or advice.
- **Plugin safety (hard).** `synopsis.py` must import clean under Calibre's bundled Python with
  **rich blocked** — render through `report.py`, never `import rich`, never import `ui`/`wizard`.
  It must be added to `CORE` in `tests/test_plugin_safety.py`.
- **No `SystemExit` in job-reachable code.** Every guard raises `common.GuardrailError`.
  `tests/test_plugin_safety.py` AST-checks this repo-wide.
- **All writes funnel through `common.run_writer(ops, tool=…, scope=…)`** built from the `op_*`
  constructors in `common.py`. Never hand-roll an ops dict, never call `calibre-debug` directly.
- **The wizard ASKS; the tool DOES.** `ui.checklist` may never appear in `wizard.py`
  (`tests/test_cli.py` reads its source and fails on `ui.checklist`, `run_writer(`, `op_set_field(`).
- **Scope ownership stays in `select.py`.** No other module may define who is in a queue.
- **Artifact CSV formats live in `artifacts.py`.** Never hand-read/write one elsewhere.
- **Menu keys are digits on fixed slots**; `ui.menu` returns a row's symbolic id, never its key.
- **Tests are plain asserts** with a `if __name__ == "__main__":` runner, pytest-collectable,
  no library/network/Calibre needed. Every `tests/test_*.py` is in CI by existing.
- Verification command for the whole suite:
  `for f in tests/test_*.py; do uv run "$f" >/dev/null || echo "FAIL $f"; done`

## File Structure

| File | Responsibility |
|---|---|
| **Create** `src/scourgify/synopsis.py` | The pass: prompts, chunking, parse helpers, `settle()`, `Plan`, `step()`, `main()`. |
| **Create** `tests/test_synopsis.py` | Pure helpers + `settle()` with a fake `ask` + the FFF guard. |
| **Create** `tests/test_synopsis_queue.py` | `select.pick("unsynopsized")` semantics against `fixture_db`. |
| Modify `src/scourgify/select.py` | `sendable()` loses its param; add `has_file()`, `SYN_STAMP`, `resynopsize()`, the `"unsynopsized"` mode. |
| Modify `src/scourgify/artifacts.py` | `syn_fail()` + `synopsis_failed_ids()`. |
| Modify `src/scourgify/setup.py` | `fff_settings()` / `comments_protected()` readers; the Comments→New Only health line + queued fix; `#synopsized` in `REC`. |
| Modify `src/scourgify/classify.py` | Delete `--text-fallback` and all its plumbing. |
| Modify `src/scourgify/booktext.py` | Docstring points at the synopsis pass (extraction otherwise unchanged). |
| Modify `src/scourgify/cli.py` | Dispatch `synopsis`. |
| Modify `src/scourgify/wizard.py` | `stage_synopsis` before classify; header + snapshot + task hint; drop `text_fallback=True`. |
| Modify `tests/test_selection.py` | Retirement: the two text-fallback tests become queue tests. |
| Modify `tests/test_wizard_flow.py` | The full-menu lap gains a slot; snapshot gains `synopsis`. |
| Modify `tests/test_plugin_safety.py` | `synopsis` joins `CORE`. |
| Modify `CLAUDE.md`/`AGENTS.md`, `README.md`, `USERGUIDE.md`, `CONTEXT.md`, `CHANGELOG.md`, `pyproject.toml` | Docs + version bump. |

---

### Task 1: The synopsis queue in `select.py`

Ownership first: every later task reads the queue from here.

**Files:**
- Modify: `src/scourgify/select.py` (`sendable` at :96, `pick` at :115)
- Modify: `src/scourgify/artifacts.py` (append after `fail()`/`classified_ids()`)
- Create: `tests/test_synopsis_queue.py`

**Interfaces:**
- Consumes: `fixture_db.build`, `common.read_custom_column`, `select._key`, `select._clocks`.
- Produces:
  - `select.SYN_STAMP: str = "#synopsized"`
  - `select.sendable(con) -> set` — **no second parameter any more**
  - `select.has_file(con) -> set`
  - `select.resynopsize(stamp, updated) -> bool`
  - `select.pick(con, "unsynopsized", seen=None) -> list[int]`
  - `artifacts.syn_fail() -> str`, `artifacts.synopsis_failed_ids() -> set`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_synopsis_queue.py`:

```python
#!/usr/bin/env python3
"""The synopsis queue — who the pass operates on, and why it is FINITE.

Three exits from the queue and each is load-bearing: the #synopsized stamp (settled), the
failure log (attempted and blocked), and having no text source at all (not work). Without
all three the sweep re-selects the same books forever, exactly as the classify backlog did.
No framework:  uv run tests/test_synopsis_queue.py   (also pytest-collectable)."""
import os, sys, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scourgify import artifacts, common, select
from fixture_db import build

LONG = "A description comfortably past the forty-character floor. " * 2
#  1 good blurb, unstamped                 -> queued (to be inspected and kept)
#  2 good blurb, stamped, no #updated      -> settled, stays out
#  3 good blurb, stamped, #updated NEWER   -> re-queued (the refresh clock)
#  4 thin blurb, has an EPUB               -> queued (generate from the file)
#  5 thin blurb, no file                   -> never queued (bucket C: not work)
#  6 good blurb, stamped, #updated OLDER   -> settled, stays out
BOOKS = [dict(id=1, added="2026-01-01 10:00:00", desc=LONG),
         dict(id=2, added="2026-01-02 10:00:00", desc=LONG),
         dict(id=3, added="2026-01-03 10:00:00", desc=LONG),
         dict(id=4, added="2026-01-04 10:00:00", desc="too short"),
         dict(id=5, added="2026-01-05 10:00:00", desc="too short"),
         dict(id=6, added="2026-01-06 10:00:00", desc=LONG)]
STAMP = "2026-05-01 12:00:00+00:00"
SYN = {2: STAMP, 3: STAMP, 6: STAMP}
UPD = {3: "2026-06-01", 6: "2026-04-01"}


def _con():
    d = tempfile.mkdtemp()
    con = build(os.path.join(d, "metadata.db"), BOOKS,
                custom=[("updated", UPD), ("wrangled", {}), ("synopsized", SYN)])
    con.execute("INSERT INTO data VALUES(?,?,?)", (4, "EPUB", "book4"))
    con.commit()
    return con


def test_resynopsize_is_the_pure_refresh_clock():
    assert select.resynopsize("", "2026-01-01") is True            # never settled
    assert select.resynopsize(None, None) is True
    assert select.resynopsize(STAMP, None) is False                # no #updated -> never auto-refresh
    assert select.resynopsize(STAMP, "2026-04-01") is False        # site update predates the settling
    assert select.resynopsize(STAMP, "2026-06-01") is True         # grew new chapters since


def test_queue_is_unstamped_or_stale_and_has_a_text_source():
    con = _con()
    assert select.pick(con, "unsynopsized", seen=set()) == [4, 3, 1]   # newest-added-first
    assert 2 not in select.pick(con, "unsynopsized", seen=set())       # settled
    assert 6 not in select.pick(con, "unsynopsized", seen=set())       # settled, older #updated
    assert 5 not in select.pick(con, "unsynopsized", seen=set())       # thin AND no file


def test_a_failed_book_leaves_the_queue_until_it_succeeds():
    """The finiteness rule the classify backlog learned the hard way: a book that errored gets no
    stamp on purpose (it must be retryable), so without the failure log it re-occupies the head of
    every batch forever."""
    con = _con()
    assert select.pick(con, "unsynopsized", seen={4}) == [3, 1]


def test_queue_defaults_read_the_failure_log():
    """Bare pick("unsynopsized") is the CORRECT call — the invariant lives in select, so the
    wizard header, the CLI and any future dashboard cannot compose it differently."""
    old = os.environ.get("SCOURGIFY_HOME")
    os.environ["SCOURGIFY_HOME"] = tempfile.mkdtemp()
    try:
        os.makedirs(common.data_dir(), exist_ok=True)
        con = _con()
        assert select.pick(con, "unsynopsized") == [4, 3, 1]
        artifacts.write_failures([[4, "book 4", "no readable text"]], artifacts.syn_fail())
        assert artifacts.synopsis_failed_ids() == {4}
        assert select.pick(con, "unsynopsized") == [3, 1]
        # ...and the synopsis log is NOT the classify log
        assert artifacts.syn_fail() != artifacts.fail()
    finally:
        os.environ.pop("SCOURGIFY_HOME", None)
        if old is not None: os.environ["SCOURGIFY_HOME"] = old


def test_has_file_and_sendable_are_separate_predicates():
    """sendable() answers 'could classify send this' (description only). has_file() answers
    'is there prose to read'. The synopsis queue is their union; the classify backlog is
    sendable() alone — the split that replaces the old text_fallback boolean."""
    con = _con()
    assert select.sendable(con) == {1, 2, 3, 6}
    assert select.has_file(con) == {4}


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run tests/test_synopsis_queue.py`
Expected: FAIL — `AttributeError: module 'scourgify.select' has no attribute 'resynopsize'`

- [ ] **Step 3: Add the artifact accessors**

In `src/scourgify/artifacts.py`, immediately after `def fail()` (line 27–29):

```python
def syn_fail() -> str:
    """Books the synopsis pass could not settle (unreadable/DRM'd file, model refusal, an
    unparseable verdict). Same FAIL_COLS shape and the same self-clearing merge as classify's
    log — and for the same reason: it retires a book from the queue exactly as long as it stays
    blocked, so the sweep is finite on the failure side too."""
    return os.path.join(data_dir(), "synopsis_failures.csv")
```

and after `def classified_ids()` (the function ending at line 210):

```python
def synopsis_failed_ids() -> set:
    """Books the synopsis pass attempted and could not settle — the queue's failure-side cursor.
    Only the log: everything else the queue needs (the #synopsized stamp, the refresh clock) is
    library state, so there is no archive to glob here."""
    return _ids(syn_fail())
```

- [ ] **Step 4: Rewrite `sendable` and add the queue mode in `select.py`**

Replace `sendable` (currently lines 96–110) with:

```python
def sendable(con: sqlite3.Connection) -> set:
    """Books classify.gather() could actually send: a description of at least MIN_DESC chars —
    the same test gather applies. Descriptions only, deliberately: a thin-blurb book is not
    classify work, it is SYNOPSIS work, and it graduates here on its own once the pass has
    written it a real description (detection is a query, never a stored flag)."""
    from scourgify.booktext import strip_html
    return {b for b, t in con.execute("SELECT book, text FROM comments")
            if len(strip_html(t or "")) >= MIN_DESC}


def has_file(con: sqlite3.Connection) -> set:
    """Books with a format file on disk — a text source the synopsis pass could read.

    Optimistic on purpose, and cheap on purpose: the `data` table, not booktext.paths(), which
    resolves absolute paths and so needs CALIBRE_LIBRARY. extract() can still come back empty on
    a DRM'd or odd file; that book then earns a failure row and leaves the queue that way, which
    is the safe direction — a predicate that guessed pessimistically would silently drop books
    the pass could have rescued."""
    return {b for (b,) in con.execute("SELECT DISTINCT book FROM data")}


SYN_STAMP = "#synopsized"   # per-book datetime: when this book's synopsis was SETTLED — earned by
                            # generating one OR by inspecting and keeping an adequate existing blurb


def resynopsize(stamp, updated) -> bool:
    """Does this book need the synopsis pass? Pure — the refresh clock, in one place.

    True when it was never settled, or when the fic was site-updated after it was settled (it
    grew chapters, so the back cover is out of date). A book with no #updated NEVER auto-refreshes
    — a library where FFF doesn't fill that column would otherwise re-summarize itself forever.
    Deliberately not books.timestamp: a re-fetch bumps the added-date without changing the story,
    and re-reading a 200k-word fic on-device costs a minute of real compute."""
    if not _key(stamp): return True
    return _key(updated) > _key(stamp)
```

In `pick`'s docstring add the line:

```
      unsynopsized — no settled synopsis (or a stale one) and a text source; the pass's queue
```

and in `pick`'s body, immediately before `if mode == "unclassified":`:

```python
    if mode == "unsynopsized":
        # Three exits, all needed for the sweep to terminate: the #synopsized stamp (settled —
        # library state, so no artifact is involved), the failure log (attempted and blocked),
        # and having no text source at all (bucket C — thin blurb, no file: not work, ever).
        # The default for `seen` lives HERE like unclassified's, so every surface asks bare.
        if seen is None:
            from scourgify.artifacts import synopsis_failed_ids
            seen = synopsis_failed_ids()
        syn = read_custom_column(con, SYN_STAMP) or {}
        ok = sendable(con) | has_file(con)
        return [b for b in newest
                if b not in seen and b in ok and resynopsize(syn.get(b), upd.get(b))]
```

Also change `pick`'s signature — drop the `text_fallback` parameter entirely:

```python
def pick(con: sqlite3.Connection, mode: str = "incremental", n: int = 0,
         since: str = "", min_tags: int = 2, ids: list[int] | None = None,
         seen: set | None = None) -> list[int]:
```

and in the `unclassified` branch change `ok = sendable(con, text_fallback)` to `ok = sendable(con)`,
deleting the two `text_fallback` sentences from its comment block (replace the sentence
"…and `sendable` excludes books gather() would drop for thin text…" tail
"or text_fallback=False to price a run that won't sample book text." with
"A thin-text book is not outstanding classify work — it is synopsis work, and re-enters this
scope by itself once the synopsis pass gives it a description.").

- [ ] **Step 5: Run the queue tests**

Run: `uv run tests/test_synopsis_queue.py`
Expected: PASS (5 tests)

- [ ] **Step 6: Update `tests/test_selection.py` for the retirement**

Delete `test_unclassified_widens_when_text_fallback_can_rescue_a_book` (lines 146–153) and replace
it with:

```python
def test_a_thin_blurb_book_is_synopsis_work_not_classify_work():
    """The retirement of --text-fallback, pinned. A book with a file used to widen the classify
    backlog (sample its prose at tag time); now it leaves the classify backlog entirely and
    appears in the synopsis queue instead, graduating back once it has a real description."""
    con = _ucon()
    con.execute("INSERT INTO data VALUES(?,?,?)", (4, "EPUB", "book4"))
    con.commit()
    assert 4 not in select.pick(con, "unclassified", seen=set())
    assert 4 in select.pick(con, "unsynopsized", seen=set())
    assert 5 not in select.pick(con, "unsynopsized", seen=set())      # no file, no blurb: not work
```

In `test_unclassified_defaults_own_the_invariant` (lines 172–193), change the docstring's
"and text_fallback to True inside select" to "inside select", drop the two-line comment about
text-fallback, and change the two assertions so book 4 is no longer expected:

```python
        # no artifacts yet: every SENDABLE book is backlog. Book 4 is thin-blurbed — it is
        # synopsis work now, not classify work (the --text-fallback widening is gone).
        assert select.pick(con, "unclassified") == [3, 2, 1]
        # a pending proposal row counts as attempted (classified_ids), read by DEFAULT
        artifacts.write_proposal([{"book_id": 2, "title": "t", "added_tags": [], "proposed_new": []}])
        assert select.pick(con, "unclassified") == [3, 1]
```

- [ ] **Step 7: Verify both selection suites**

Run: `uv run tests/test_selection.py && uv run tests/test_synopsis_queue.py`
Expected: both PASS. (`test_classify_run.py` / `test_wizard_flow.py` will still fail here — Task 4
removes the `text_fallback=` call sites. That is expected and fixed there.)

- [ ] **Step 8: Commit**

```bash
git add src/scourgify/select.py src/scourgify/artifacts.py tests/test_synopsis_queue.py tests/test_selection.py
git commit -m "feat(select): the synopsis queue, and sendable() stops answering two questions"
```

---

### Task 2: The FanFicFare Comments guard

**Files:**
- Modify: `src/scourgify/setup.py` (the `[2] FanFicFare` block, lines 64–93; `REC` at 20–23)
- Create: `tests/test_synopsis.py` (the guard tests; later tasks append to this file)

**Interfaces:**
- Consumes: `common.ro_connect`, `common.GuardrailError`.
- Produces:
  - `setup.fff_settings(con) -> dict`
  - `setup.comments_protected(con) -> bool | None` — `None` = FFF has no config for this library
  - `setup.REC` gains `("#synopsized", "Synopsized", "datetime", False)`

- [ ] **Step 1: Write the failing test**

Create `tests/test_synopsis.py`:

```python
#!/usr/bin/env python3
"""The synopsis pass — the FanFicFare clobber guard, the pure prompt/parse helpers, and
settle() driven by a fake engine. No Calibre, no network, no on-device model.
No framework:  uv run tests/test_synopsis.py   (also pytest-collectable)."""
import json, os, sys, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scourgify import common, setup as setup_mod
from fixture_db import build

FFF_KEY = "namespaced:FanFicFarePlugin:settings"


def _con(prefs=None):
    d = tempfile.mkdtemp()
    con = build(os.path.join(d, "metadata.db"), [dict(id=1, added="2026-01-01 10:00:00")])
    if prefs is not None:
        con.execute("INSERT INTO preferences VALUES(?,?)", (FFF_KEY, json.dumps(prefs)))
        con.commit()
    return con


def test_comments_protected_reads_the_real_fff_pref_shape():
    """Verified 2026-08-23 against the author's library: FFF stores per-library prefs as JSON in
    the preferences table under namespaced:FanFicFarePlugin:settings, and std_cols_newonly is a
    dict of standard-column -> bool. Readable through an ordinary read-only connection, which is
    what makes this guard automatic instead of an interactive confirm."""
    on = {"std_cols_newonly": {"comments": True, "title": True}, "custom_cols": {}}
    off = {"std_cols_newonly": {"comments": False, "title": True}, "custom_cols": {}}
    assert setup_mod.comments_protected(_con(on)) is True
    assert setup_mod.comments_protected(_con(off)) is False
    assert setup_mod.comments_protected(_con({"custom_cols": {}})) is False   # key absent = unprotected
    assert setup_mod.comments_protected(_con(None)) is None                   # FFF unconfigured here
    assert setup_mod.fff_settings(_con(None)) == {}


def test_comments_protected_survives_a_corrupt_prefs_blob():
    con = _con()
    con.execute("INSERT INTO preferences VALUES(?,?)", (FFF_KEY, "{not json"))
    con.commit()
    assert setup_mod.fff_settings(con) == {}
    assert setup_mod.comments_protected(con) is None


def test_synopsized_is_a_recommended_column():
    assert ("#synopsized", "Synopsized", "datetime", False) in setup_mod.REC


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run tests/test_synopsis.py`
Expected: FAIL — `AttributeError: module 'scourgify.setup' has no attribute 'comments_protected'`

- [ ] **Step 3: Add the readers and the health-check line**

In `src/scourgify/setup.py`, add at module level after `REC` (line 23):

```python
def fff_settings(con) -> dict:
    """FanFicFare's per-library prefs blob — the ONE parser of it, so setup's health check and
    the synopsis pass's pre-flight guard can never read it differently. {} when FFF has never
    been configured against this library (or the blob is unreadable)."""
    import json
    row = con.execute("SELECT val FROM preferences WHERE key='namespaced:FanFicFarePlugin:settings'").fetchone()
    if not row: return {}
    try: return json.loads(row[0]) or {}
    except (ValueError, TypeError): return {}


def comments_protected(con) -> bool | None:
    """Is FFF's Standard Columns → Comments → 'New Only' switch on for this library?

    True/False from `std_cols_newonly`; **None** when FFF has no config here at all — there is
    then nothing to clobber and nothing to assert, so the synopsis guard must not fire. Absent
    key means unprotected (FFF's own default is off, measured 2026-08-23)."""
    s = fff_settings(con)
    if not s: return None
    return bool((s.get("std_cols_newonly") or {}).get("comments"))
```

Add `#synopsized` to `REC` (after the `#wrangled` row):

```python
REC = [("#fandoms", "Fandoms", "text", True), ("#characters", "Characters", "text", True),
       ("#relationships", "Relationships", "text", True), ("#genres", "Genres", "text", True),
       ("#status", "Status", "text", False), ("#updated", "Updated", "datetime", False),
       ("#wrangled", "Wrangled", "datetime", False),
       ("#synopsized", "Synopsized", "datetime", False)]
```

In `setup()`, replace the two lines that read the blob by hand (currently lines 73–74)

```python
    row = con.execute("SELECT val FROM preferences WHERE key='namespaced:FanFicFarePlugin:settings'").fetchone()
    settings = _json.loads(row[0]) if row else {}
```

with

```python
    settings = fff_settings(con)                  # the ONE parser (the synopsis guard reads it too)
```

(and delete `import json as _json` from the `import subprocess, shutil, json as _json` line at the
top of `setup()`, leaving `import subprocess, shutil`).

Add the Comments issue to the `issues` list, after the `#genres` check (line 85):

```python
        if not (settings.get("std_cols_newonly") or {}).get("comments"):
            issues.append("Comments not newonly-protected  (a metadata re-fetch would overwrite the "
                          "synopses `scourgify synopsis` writes into the description)")
```

and to the queued fix (after the `#genres` line inside the `elif _ask(...)` branch, line 92):

```python
            s.setdefault("std_cols_newonly", {})["comments"] = True
```

Widen that prompt's wording so it still describes what it does:

```python
        elif _ask("  → Fix these now (map #fandoms←category, drop include_in_series, protect #genres + Comments)?"):
```

- [ ] **Step 4: Run the test**

Run: `uv run tests/test_synopsis.py`
Expected: PASS (3 tests)

- [ ] **Step 5: Verify setup's other users still pass**

Run: `uv run tests/test_wizard.py && uv run tests/test_paths.py && uv run tests/test_plugin_safety.py`
Expected: PASS. (`test_wizard.py` checks column health against `COLS`; if it asserts a count,
update it to include `#synopsized` in the same commit.)

- [ ] **Step 6: Commit**

```bash
git add src/scourgify/setup.py tests/test_synopsis.py tests/test_wizard.py
git commit -m "feat(setup): read FFF's Comments 'New Only' switch, and warn when it is off"
```

---

### Task 3: `synopsis.py` — the pure core and `settle()`

**Files:**
- Create: `src/scourgify/synopsis.py`
- Modify: `tests/test_synopsis.py` (append)
- Modify: `tests/test_plugin_safety.py` (`CORE`, line 20)

**Interfaces:**
- Consumes: `select.SYN_STAMP`, `setup.comments_protected`, `booktext.extract`,
  `booktext.strip_html`, `engines.ENGINES`, `engines.ask_retry`, `common.GuardrailError`.
- Produces:
  - `synopsis.STAMP: str` (== `select.SYN_STAMP`), `CHUNK`, `MAX_CHUNKS`, `NOTE_CAP`,
    `MIN_JUDGE`, `MIN_SYNOPSIS`, `EXTRACT`
  - `synopsis.chunks(text, size=CHUNK, cap=MAX_CHUNKS) -> list[str]`
  - `synopsis.clean(s) -> str`
  - `synopsis.verdict(resp) -> str` ∈ `{"keep", "generate", ""}`
  - `synopsis.guard_comments(con, force=False) -> None`
  - `synopsis.settle(title, blurb, path, ask) -> tuple[str, str]` — `(synopsis, error)`;
    `("", "")` means *keep the existing blurb*

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_synopsis.py`, before the `if __name__` block:

```python
from scourgify import synopsis


# ---- chunking: sized for the apple engine's measured 4096-token ceiling ----
def test_chunks_fit_the_measured_context_window():
    """Measured 2026-08-23 against afm.swift with real EPUB prose: 17,000 chars pass, 18,000
    fail at 4,165 tokens against a hard limit of 4,096 (prompt AND answer share it). CHUNK must
    stay well under that — the prompt template and the model's own reply also have to fit."""
    assert synopsis.CHUNK <= 12_000
    parts = synopsis.chunks("x" * 25_000)
    assert [len(p) for p in parts] == [synopsis.CHUNK, synopsis.CHUNK, 25_000 - 2 * synopsis.CHUNK]
    assert "".join(parts) == "x" * 25_000                 # nothing lost when under the cap


def test_chunks_sample_evenly_once_past_the_cap_but_always_keep_the_opening():
    """A 500k-word fic is ~300 slabs; reading every one on-device costs ~10 minutes for ONE book.
    Past the cap we spread across the whole story — but slabs 0 and 1 are always in, because the
    premise, the cast and the hook (the back cover's whole job) live in the opening."""
    text = "".join(f"{i:05d}".ljust(synopsis.CHUNK, "y") for i in range(100))
    got = synopsis.chunks(text)
    assert len(got) == synopsis.MAX_CHUNKS
    idx = [int(p[:5]) for p in got]
    assert idx[:2] == [0, 1]                              # the opening, always
    assert idx == sorted(idx) and len(set(idx)) == len(idx)
    assert idx[-1] > 80                                   # ...and it reaches the end of the book


def test_chunks_of_nothing_is_nothing():
    assert synopsis.chunks("") == []


# ---- parsing ----
def test_clean_strips_the_lead_in_and_refuses_an_error_line():
    assert synopsis.clean("Here is the blurb: A boy meets a wolf.") == "A boy meets a wolf."
    assert synopsis.clean("Sure! Here's a back-cover summary:  A boy meets a wolf. ") == "A boy meets a wolf."
    assert synopsis.clean("A boy meets a wolf.") == "A boy meets a wolf."
    assert synopsis.clean('"A boy meets a wolf."') == "A boy meets a wolf."
    assert synopsis.clean("ERR: exceededContextWindowSize(...)") == ""
    assert synopsis.clean("") == "" and synopsis.clean(None) == ""


def test_verdict_is_three_state_because_guessing_is_wrong_in_both_directions():
    """Guessing 'keep' stamps a bad blurb as settled forever; guessing 'generate' AI-rewrites a
    healthy author-written one. An unreadable answer settles nothing — it becomes a failure row,
    which is self-clearing when the book is retried on another engine."""
    assert synopsis.verdict("YES") == "keep"
    assert synopsis.verdict("  yes.") == "keep"
    assert synopsis.verdict("NO") == "generate"
    assert synopsis.verdict("No, the description is just an author's note.") == "generate"
    assert synopsis.verdict("It depends on what you mean by adequate.") == ""
    assert synopsis.verdict("") == "" and synopsis.verdict(None) == ""


# ---- the pre-flight guard ----
def test_guard_refuses_when_fanficfare_would_clobber_the_synopsis():
    off = {"std_cols_newonly": {"comments": False}, "custom_cols": {}}
    try:
        synopsis.guard_comments(_con(off))
        assert False, "expected a GuardrailError"
    except common.GuardrailError as e:
        assert "New Only" in str(e) and "--force" in str(e)


def test_guard_passes_when_protected_unconfigured_or_forced():
    on = {"std_cols_newonly": {"comments": True}, "custom_cols": {}}
    off = {"std_cols_newonly": {"comments": False}, "custom_cols": {}}
    synopsis.guard_comments(_con(on))              # protected
    synopsis.guard_comments(_con(None))            # FFF unconfigured: nothing to clobber
    synopsis.guard_comments(_con(off), force=True) # degraded self-healing mode, opted into


def test_the_guard_is_not_a_systemexit():
    """Job-reachable code may never raise SystemExit — Calibre's ThreadedJob catches only
    Exception, so it would kill the worker thread silently (CLAUDE.md, test_plugin_safety)."""
    off = {"std_cols_newonly": {"comments": False}, "custom_cols": {}}
    try:
        synopsis.guard_comments(_con(off))
    except common.GuardrailError:
        pass
    except SystemExit:
        assert False, "guard_comments must raise GuardrailError, never SystemExit"


# ---- settle(): one book, one fake engine ----
class FakeAsk:
    """prompt -> (text, err), keyed by what the prompt is asking for. Records every call so a
    test can assert the pass did NOT read the book when the blurb was already good."""
    def __init__(self, judge="YES", note="Notes about this excerpt.", back="A real back cover.", err=""):
        self.judge, self.note, self.back, self.err = judge, note, back, err
        self.calls = []

    def __call__(self, prompt):
        self.calls.append(prompt)
        if self.err: return "", self.err
        if "exactly one word" in prompt: return self.judge, ""
        if "Excerpt" in prompt: return self.note, ""
        return self.back, ""


GOOD_BLURB = "A sheriff who used to be a monster keeps the peace in a town of exiled fairy tales. " * 2


def _epub(text="the story went on and on. " * 900):
    """A tiny real EPUB (a zip of one XHTML member) so booktext.extract has something to read."""
    import zipfile
    p = os.path.join(tempfile.mkdtemp(), "b.epub")
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("ch1.xhtml", f"<html><body><p>{text}</p></body></html>")
    return p


def test_an_adequate_blurb_is_kept_and_the_book_is_never_read():
    """Decision Q4: author voice is preserved on the ~7.5k healthy books. ('', '') means SETTLED
    with nothing to write — not a failure, and not an empty synopsis."""
    ask = FakeAsk(judge="YES")
    assert synopsis.settle("Fables", GOOD_BLURB, _epub(), ask) == ("", "")
    assert len(ask.calls) == 1                     # the judge only: no excerpts, no generation


def test_a_bad_blurb_triggers_whole_book_generation():
    ask = FakeAsk(judge="NO", back="Bigby Wolf keeps the peace. Themes: redemption, noir, exile.")
    out, err = synopsis.settle("Fables", GOOD_BLURB, _epub(), ask)
    assert err == "" and out == "Bigby Wolf keeps the peace. Themes: redemption, noir, exile."
    assert sum("Excerpt" in c for c in ask.calls) >= 1


def test_a_blurb_too_thin_to_judge_goes_straight_to_generation():
    ask = FakeAsk(back="A generated back cover long enough to be worth storing in the library.")
    out, err = synopsis.settle("Fables", "see inside", _epub(), ask)
    assert err == "" and out.startswith("A generated back cover")
    assert not any("exactly one word" in c for c in ask.calls)     # nothing to judge


def test_an_unreadable_file_is_a_failure_not_a_silent_skip():
    """Bucket B stays finite only if a book that cannot be read leaves the queue via the log."""
    out, err = synopsis.settle("Fables", "see inside", "/nonexistent/book.epub", FakeAsk())
    assert out == "" and "no readable text" in err


def test_an_engine_error_is_reported_verbatim_enough_to_act_on():
    out, err = synopsis.settle("Fables", "see inside", _epub(), FakeAsk(err="quota: 429 Too Many Requests"))
    assert out == "" and err.startswith("quota")


def test_an_unparseable_verdict_settles_nothing():
    ask = FakeAsk(judge="Well, it depends.")
    out, err = synopsis.settle("Fables", GOOD_BLURB, _epub(), ask)
    assert out == "" and "verdict" in err
    assert len(ask.calls) == 1                     # it did NOT fall through and rewrite a healthy blurb


def test_a_model_that_returns_nothing_usable_is_a_failure_not_an_empty_description():
    """The write path must never be handed '' for comments — that is a wipe, not a synopsis."""
    out, err = synopsis.settle("Fables", "see inside", _epub(), FakeAsk(back="ok"))
    assert out == "" and "no usable synopsis" in err
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run tests/test_synopsis.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'scourgify.synopsis'`

- [ ] **Step 3: Write `src/scourgify/synopsis.py` (the pure core half)**

```python
#!/usr/bin/env python3
"""Settle every book's synopsis: a spoiler-safe back cover in Calibre's own description field.

    scourgify synopsis                       # dry run: resolve the queue, cost it, send NOTHING
    scourgify synopsis --apply               # generate + write (Calibre CLOSED)
    scourgify synopsis --apply --step        # ...reviewing each new synopsis 1-by-1
    scourgify synopsis --apply --batch 100   # chew through the queue a chunk at a time

Two jobs per book, and the cheap one runs first: if the existing description is already an
adequate, spoiler-safe blurb the model says so in one word and the book is stamped with its
author's own text UNTOUCHED (never AI-flatten a library of fine descriptions into one beige
voice). Only a bad blurb earns the expensive path — read the book's own prose in slabs, note each,
then fold the notes into a back cover: premise, characters, stakes, hook, and a themes line.
Never plot outcomes, never endings; the description stays safe to browse.

**`#synopsized`** (datetime) is the whole state: "synopsis SETTLED on this date", earned either
way. It is the queue (unstamped = outstanding), the provenance, and the refresh clock
(`#updated` newer than the stamp = the fic grew chapters, re-settle it). Queue membership itself
lives in `select.pick("unsynopsized")` — never re-derived here.

The synopsis is written into the BUILT-IN description because improving it is half the point:
Calibre and every reader display that field. FanFicFare would overwrite it on a metadata
re-fetch, so the pass refuses to start unless FFF's Comments → "New Only" switch is on
(`--force` accepts the degraded self-healing mode: a clobbered book simply re-enters the queue).

Engine: `apple` — free, on-device, single-threaded, and slow, which is fine; this sweep is meant
to run in the background for weeks. A cloud engine is an explicit opt-in for a book that stalls.
Unlike classify, a BARE run costs nothing at all: without --apply nothing is generated, because
there is no intermediate proposal artifact to produce — resume is off the stamp."""
import argparse
import copy
import os
import re

from scourgify import booktext, select
from scourgify.artifacts import merge_failures, read_rows, syn_fail, write_failures
from scourgify.booktext import strip_html
from scourgify.common import (GuardrailError, custom_column_id, op_create_column, op_set_field,
                              op_stamp_now, ro_connect, run_writer, titles as book_titles)
from scourgify.engines import ENGINES, ask_retry
from scourgify.setup import comments_protected

STAMP = select.SYN_STAMP     # one name for the stamp; select owns the queue that reads it

# Measured 2026-08-23 against afm.swift with real EPUB prose: the on-device model's context is a
# hard 4096 tokens shared by prompt AND answer, and English prose runs ~4.3 chars/token (18,000
# chars = 4,165 tokens = refused). 10k chars is ~2,300 tokens, leaving generous room for the
# template and the reply. Re-measure if the bundled model changes.
CHUNK = 10_000
MAX_CHUNKS = 12          # slabs actually read per book — see chunks()
NOTE_CAP = 500           # chars kept from each slab's note, so MAX_CHUNKS*NOTE_CAP fits ONE reduce prompt
EXTRACT = 2_000_000      # chars pulled from the file before sampling (bounds memory on a huge fic)
MIN_JUDGE = 120          # below this there is no blurb worth judging — go straight to generation
MIN_SYNOPSIS = 120       # a shorter answer than this is the model failing, not a back cover
JUDGE_CAP = 4_000        # blurb chars sent to the adequacy judge

JUDGE_P = (
    "You are judging whether a fanfiction's existing description works as a back-cover blurb.\n"
    "GOOD: says what the story is about — premise, main characters, what is at stake — in a "
    "sentence or more, and gives away no ending.\n"
    "BAD: author's notes, update schedules, a dump of tags, 'summary inside', one vague line, "
    "cross-posting boilerplate, or nothing about the story at all.\n\n"
    'Title: {title}\nDescription: {blurb}\n\n'
    "Reply with exactly one word: YES if it is good, NO if it is not.")

NOTE_P = (
    'Excerpt {i} of {n} from the fanfiction "{title}".\n'
    "In two sentences, note the characters, the setting, the situation and the tone here. "
    "Plain prose, no preamble.\n\n{chunk}")

BACK_P = (
    'Notes taken while reading the fanfiction "{title}", in order:\n\n{notes}\n\n'
    "Write its back-cover blurb: three to five sentences of flowing prose covering the premise, "
    "the main characters and what is at stake, ending on a hook. Then one final line reading "
    "'Themes: a, b, c' with three short theme or trope words.\n"
    "SPOILER-SAFE: never reveal how the story ends or how its conflicts resolve.\n"
    "No preamble, no title, no headings — the blurb only.")


def chunks(text: str, size: int = CHUNK, cap: int = MAX_CHUNKS) -> list:
    """`text` in <=size-char slabs; past `cap` slabs, an even spread that always keeps the first two.

    ponytail: even sampling rather than every chapter. The back cover needs the opening (premise,
    cast, hook — always slabs 0 and 1) plus a sense of the whole, and reading all ~300 slabs of a
    500k-word fic on-device costs ~10 minutes for ONE book against a 7,949-book library. Raise
    `cap` if generated synopses read thin; the ceiling is wall-clock, not quality-by-design."""
    parts = [text[i:i + size] for i in range(0, len(text), size)]
    if len(parts) <= cap: return parts
    step = len(parts) / cap
    keep = sorted({0, 1} | {int(i * step) for i in range(cap)})[:cap]
    return [parts[i] for i in keep]


def clean(s) -> str:
    """One engine answer as storable prose: whitespace collapsed, a chatty lead-in dropped,
    surrounding quotes stripped. '' for an error line or nothing usable — and '' is what the
    callers test, because handing '' to a set_field on `comments` would be a WIPE, not a synopsis."""
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    if not s or s.startswith("ERR"): return ""
    s = re.sub(r"^(sure[,!.]?\s*)?here(?:'s| is)[^:]{0,60}:\s*", "", s, flags=re.I)
    return s.strip().strip('"').strip()


def verdict(resp) -> str:
    """The adequacy answer as 'keep' | 'generate' | '' (unreadable). Pure.

    Three states, not two, because a guess is wrong in BOTH directions: guessing 'keep' stamps a
    bad blurb as settled forever, and guessing 'generate' AI-rewrites an author's healthy prose.
    '' settles nothing — the book earns a failure row and is retried later, on another engine if
    need be. Only a leading YES/NO counts; the prompt asks for exactly one word."""
    s = str(resp or "").strip()
    if re.match(r"\W*yes\b", s, re.I): return "keep"
    if re.match(r"\W*no\b", s, re.I): return "generate"
    return ""


def guard_comments(con, force: bool = False) -> None:
    """Pre-flight: refuse to start unless FanFicFare cannot overwrite what we are about to write.

    GuardrailError, never SystemExit — this is reachable from a Calibre job, where a SystemExit
    would kill the worker thread silently. Runs before any generation, not before the write: the
    point is not to spend hours of on-device compute on synopses a re-fetch will erase."""
    if force: return
    if comments_protected(con) is False:
        raise GuardrailError(
            "FanFicFare would overwrite every synopsis this pass writes.\n"
            "  The synopsis lives in the built-in description (Comments), and FFF re-fetches it.\n"
            "  Fix: Calibre → Preferences → Plugins → FanFicFare → Customize → Standard Columns\n"
            "       → tick 'New Only' beside Comments.   (`scourgify setup` offers the same fix.)\n"
            "  --force accepts the degraded mode instead: a clobbered book re-enters the queue "
            "and is re-summarized — wasted free compute, not lost data.")


def settle(title: str, blurb: str, path, ask) -> tuple:
    """One book -> (synopsis, error).

    ('', '')   the existing blurb is adequate — SETTLED, nothing to write, author voice kept.
    (text, '') a generated back cover to write into the description.
    ('', err)  attempted and blocked; the caller files a failure row so the queue stays finite.

    `ask` is prompt -> (text, err) — the injected seam (engines.ask_retry in production, a fake
    in tests), the same shape as promote.run(ask=) and classify.Plan.run(ask=)."""
    if len(blurb) >= MIN_JUDGE:
        v, err = ask(JUDGE_P.format(title=title, blurb=blurb[:JUDGE_CAP]))
        if err: return "", err
        d = verdict(v)
        if d == "keep": return "", ""
        if not d: return "", f"parse: no yes/no in the adequacy verdict ({clean(v)[:80]!r})"
    text = booktext.extract(path, limit=EXTRACT)
    if not text: return "", "no readable text (file missing, DRM'd, or empty)"
    slabs = chunks(text)
    notes = []
    for i, c in enumerate(slabs, 1):
        n, err = ask(NOTE_P.format(i=i, n=len(slabs), title=title, chunk=c))
        if err: return "", err
        n = clean(n)
        if n: notes.append(n[:NOTE_CAP])
    if not notes: return "", "engine returned nothing usable for any excerpt"
    out, err = ask(BACK_P.format(title=title, notes="\n".join(f"- {n}" for n in notes)))
    if err: return "", err
    out = clean(out)
    return (out, "") if len(out) >= MIN_SYNOPSIS else ("", "engine returned no usable synopsis")
```

- [ ] **Step 4: Run the tests**

Run: `uv run tests/test_synopsis.py`
Expected: PASS (all 16 tests)

- [ ] **Step 5: Add `synopsis` to the plugin-safety CORE list**

In `tests/test_plugin_safety.py`, line 20:

```python
CORE = ["common", "ops", "editlog", "select", "artifacts", "overrides", "engines", "booktext",
        "wrangle", "classify", "synopsis", "promote", "staleness", "setup", "report", "cli"]
```

- [ ] **Step 6: Prove it imports with rich blocked and raises no `SystemExit`**

Run: `uv run tests/test_plugin_safety.py`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/scourgify/synopsis.py tests/test_synopsis.py tests/test_plugin_safety.py
git commit -m "feat(synopsis): the pure core — chunking sized to the measured 4096-token window"
```

---

### Task 4: `synopsis.Plan`, the write path, the CLI, and the `--text-fallback` retirement

**Files:**
- Modify: `src/scourgify/synopsis.py` (append `Plan`, `step`, `plan`, `default_opts`,
  `build_parser`, `main`)
- Modify: `src/scourgify/classify.py` (delete the flag and its plumbing)
- Modify: `src/scourgify/booktext.py` (docstring)
- Modify: `src/scourgify/cli.py` (dispatch)
- Modify: `tests/test_synopsis.py` (append)
- Modify: `tests/test_classify_run.py`, `tests/test_booktext.py` (retirement fallout)

**Interfaces:**
- Consumes: everything from Task 3, plus `select.pick`, `common.run_writer`, `common.op_*`.
- Produces:
  - `synopsis.Plan` with `.opts`, `.todo: list[int]`, `.blurbs: dict`, `.files: dict`,
    `.titles: dict`, `.have_stamp: bool`, `.run(ask=None) -> None`, `.preview() -> None`
  - `synopsis.plan(a) -> Plan`
  - `synopsis.step(made: dict, titles: dict) -> dict` — the 1-by-1 review
  - `synopsis.default_opts(**overrides) -> argparse.Namespace`
  - `synopsis.main() -> None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_synopsis.py` before the `if __name__` block:

```python
import contextlib, io


@contextlib.contextmanager
def _env(**kv):
    saved = {k: os.environ.get(k) for k in kv}
    for k, v in kv.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    try: yield
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


THIN = "see inside"
FAT = "A sheriff who used to be a monster keeps the peace in a town of exiled fairy tales. " * 2
PREFS_ON = {"std_cols_newonly": {"comments": True}, "custom_cols": {}}


@contextlib.contextmanager
def lib(books, syn=None, prefs=PREFS_ON):
    """A throwaway library with real EPUB files on disk, so booktext.paths() resolves and
    extract() actually reads. NEVER the user's library — CALIBRE_LIBRARY points into a tempdir."""
    import zipfile
    with tempfile.TemporaryDirectory() as td:
        root = os.path.join(td, "library"); os.makedirs(root)
        con = build(os.path.join(root, "metadata.db"), books,
                    custom=[("updated", {}), ("synopsized", syn or {})])
        if prefs is not None:
            con.execute("INSERT INTO preferences VALUES(?,?)", (FFF_KEY, json.dumps(prefs)))
        for b in books:
            d = os.path.join(root, f"b{b['id']}"); os.makedirs(d, exist_ok=True)
            con.execute("UPDATE books SET path=? WHERE id=?", (f"b{b['id']}", b["id"]))
            con.execute("INSERT INTO data VALUES(?,?,?)", (b["id"], "EPUB", "f"))
            with zipfile.ZipFile(os.path.join(d, "f.epub"), "w") as z:
                z.writestr("ch1.xhtml", "<html><body><p>%s</p></body></html>"
                           % ("the story went on and on. " * 900))
        con.commit(); con.close()
        with _env(SCOURGIFY_HOME=os.path.join(td, "home"), CALIBRE_LIBRARY=root):
            os.makedirs(common.data_dir(), exist_ok=True)
            yield


BOOKS2 = [dict(id=1, added="2026-01-01 10:00:00", title="Good Blurb", desc=FAT),
          dict(id=2, added="2026-01-02 10:00:00", title="Thin Blurb", desc=THIN)]


def test_a_bare_run_sends_nothing_and_writes_nothing():
    """The cost pin, and the lesson CLAUDE.md records the hard way about classify: a read-only
    check must be genuinely free. Here there is no proposal artifact to build, so a dry run is
    the queue report and nothing else — no engine, no library write."""
    with lib(BOOKS2):
        p = synopsis.plan(synopsis.default_opts())
        assert sorted(p.todo) == [1, 2]
        boom = lambda prompt: (_ for _ in ()).throw(AssertionError("dry run must reach no engine"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            p.run(ask=boom)
        assert "--apply" in buf.getvalue()


def test_apply_keeps_a_good_blurb_writes_a_bad_one_and_stamps_both():
    """The write contract in one assertion: comments is set ONLY for the generated book, while
    #synopsized stamps every book the run settled — a kept blurb is settled too, and an unstamped
    one would be re-read on every future sweep."""
    recorded = []
    with lib(BOOKS2):
        saved = synopsis.run_writer
        synopsis.run_writer = lambda ops, force=False, **kw: recorded.append(ops)
        try:
            p = synopsis.plan(synopsis.default_opts(apply=True))
            with contextlib.redirect_stdout(io.StringIO()):
                p.run(ask=FakeAsk(judge="YES", back="A generated back cover, long enough to store "
                                                    "in a library. Themes: exile, noir, duty."))
        finally:
            synopsis.run_writer = saved
    (ops,) = recorded
    sets = {o["field"]: o["values"] for o in ops if o["op"] == "set_field"}
    assert list(sets["comments"]) == [2]                       # only the thin-blurb book is rewritten
    assert sets["comments"][2].startswith("A generated back cover")
    (stamp,) = [o for o in ops if o["op"] == "stamp_now"]
    assert stamp["field"] == "#synopsized" and sorted(stamp["books"]) == [1, 2]
    assert any(o["op"] == "create_column" and o["label"] == "synopsized" for o in ops) is False


def test_the_stamp_column_is_created_on_first_run():
    recorded = []
    with lib(BOOKS2, prefs=PREFS_ON) as _:
        pass
    # a library that has no #synopsized column at all
    import zipfile
    with tempfile.TemporaryDirectory() as td:
        root = os.path.join(td, "library"); os.makedirs(root)
        con = build(os.path.join(root, "metadata.db"), BOOKS2, custom=[("updated", {})])
        con.execute("INSERT INTO preferences VALUES(?,?)", (FFF_KEY, json.dumps(PREFS_ON)))
        for b in BOOKS2:
            d = os.path.join(root, f"b{b['id']}"); os.makedirs(d)
            con.execute("UPDATE books SET path=? WHERE id=?", (f"b{b['id']}", b["id"]))
            con.execute("INSERT INTO data VALUES(?,?,?)", (b["id"], "EPUB", "f"))
            with zipfile.ZipFile(os.path.join(d, "f.epub"), "w") as z:
                z.writestr("c.xhtml", "<html><body><p>%s</p></body></html>" % ("prose. " * 3000))
        con.commit(); con.close()
        with _env(SCOURGIFY_HOME=os.path.join(td, "home"), CALIBRE_LIBRARY=root):
            os.makedirs(common.data_dir(), exist_ok=True)
            saved = synopsis.run_writer
            synopsis.run_writer = lambda ops, force=False, **kw: recorded.append(ops)
            try:
                p = synopsis.plan(synopsis.default_opts(apply=True))
                with contextlib.redirect_stdout(io.StringIO()):
                    p.run(ask=FakeAsk(judge="YES"))
            finally:
                synopsis.run_writer = saved
    (ops,) = recorded
    assert ops[0]["op"] == "create_column" and ops[0]["label"] == "synopsized"


def test_a_failed_book_lands_in_the_synopsis_failure_log_and_is_not_stamped():
    """Not stamped, because a blocked book must stay retryable; in the log, because otherwise it
    re-occupies the head of every batch forever."""
    recorded = []
    with lib([dict(id=2, added="2026-01-02 10:00:00", title="Thin", desc=THIN)]):
        saved = synopsis.run_writer
        synopsis.run_writer = lambda ops, force=False, **kw: recorded.append(ops)
        try:
            p = synopsis.plan(synopsis.default_opts(apply=True))
            with contextlib.redirect_stdout(io.StringIO()):
                p.run(ask=FakeAsk(err="quota: 429"))
        finally:
            synopsis.run_writer = saved
        rows = common.__dict__ and read_syn_failures()
        assert [int(r["book_id"]) for r in rows] == [2]
        assert "quota" in rows[0]["reason"]
    assert recorded == []                              # nothing settled -> nothing written


def read_syn_failures():
    from scourgify import artifacts
    return artifacts.read_rows(artifacts.syn_fail())


def test_batch_caps_the_run():
    with lib(BOOKS2):
        p = synopsis.plan(synopsis.default_opts(batch=1))
        assert len(p.todo) == 1 and p.todo == [2]      # newest-added-first


def test_the_guard_fires_before_any_work():
    off = {"std_cols_newonly": {"comments": False}, "custom_cols": {}}
    with lib(BOOKS2, prefs=off):
        try:
            synopsis.plan(synopsis.default_opts(apply=True))
            assert False, "expected the pre-flight guard to refuse"
        except common.GuardrailError as e:
            assert "New Only" in str(e)
    with lib(BOOKS2, prefs=off):
        synopsis.plan(synopsis.default_opts(apply=True, force=True))   # opted into the degraded mode
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run tests/test_synopsis.py`
Expected: FAIL — `AttributeError: module 'scourgify.synopsis' has no attribute 'plan'`

- [ ] **Step 3: Append `Plan` and the CLI to `src/scourgify/synopsis.py`**

```python
class Plan:
    """ONE resolved synopsis run: guard → scope → todo, computed once so a confirm and the work
    that follows it are over the same set (mirrors wrangle.Plan and classify.Plan; the CLI and
    the wizard drive the same object). Owns a COPY of the caller's options — steering a resolved
    plan goes through `p.opts`, never by mutating the caller's namespace."""

    def __init__(self, a: argparse.Namespace):
        self.opts = a = copy.copy(a)
        con = ro_connect()
        guard_comments(con, a.force)        # before any work: hours of compute a re-fetch would erase
        if a.books is not None:
            ids = select.pick(con, "ids", ids=select.parse_books(a.books))
        elif a.last:
            ids = select.pick(con, "last", n=a.last)
        else:
            ids = select.pick(con, "unsynopsized")
        self.scope = ("named ids" if a.books is not None else
                      f"last {a.last} added" if a.last else "the synopsis queue")
        self.blurbs = {b: strip_html(t) for b, t in con.execute("SELECT book, text FROM comments")}
        self.files = booktext.paths(con)
        self.titles = book_titles(con)
        self.have_stamp = custom_column_id(con, STAMP) is not None
        con.close()
        self.todo = ids[:a.batch] if a.batch else ids

    def preview(self) -> None:
        """What a run would do, without doing any of it — the free check. Unlike classify there
        is nothing to send here for a proposal, so this really is zero cost, not zero WRITES."""
        from scourgify import report
        n = len(self.todo)
        judged = sum(1 for b in self.todo if len(self.blurbs.get(b, "")) >= MIN_JUDGE)
        report.say(f"  scope: {self.scope} -> {n} book(s), engine={self.opts.engine}")
        report.table("what this run would do", ["books", "step"],
                     [[str(judged), "have a blurb to judge (one cheap call; a good one is kept as-is)"],
                      [str(n - judged), "go straight to whole-book generation"],
                      [str(n), f"stamped {STAMP} either way"]], right=(0,))
        report.say(f"\nDry run — nothing sent, nothing written. To run it: "
                   f"scourgify synopsis --apply   (Calibre closed)")

    def run(self, ask=None) -> None:
        """Execute. Without --apply this is preview() and stops — see the module docstring."""
        from scourgify import report
        a = self.opts
        if not a.apply:
            self.preview(); return
        if not self.todo:
            report.say("every book's synopsis is settled ✓"); return
        if ask is None:
            eng = ENGINES[a.engine](a.model, a.timeout)
            ask = lambda prompt: ask_retry(eng, prompt)
        report.say(f"  {len(self.todo)} book(s) · engine={a.engine} "
                   f"({'free, on-device — slow is fine' if a.engine == 'apple' else 'billed per book'})")
        made, kept, failures = {}, [], []
        for i, b in enumerate(self.todo, 1):
            title = str(self.titles.get(b, ""))
            out, err = settle(title, self.blurbs.get(b, ""), self.files.get(b), ask)
            if err:
                failures.append([b, title, err]); mark = f"✗ {err[:60]}"
            elif out:
                made[b] = out; mark = f"wrote {len(out)} chars"
            else:
                kept.append(b); mark = "kept the existing blurb"
            report.say(f"  [{i}/{len(self.todo)}] #{b} {title[:40]:<40} {mark}")
        # the failure log is rewritten every run, not only when this one failed: a book recovered
        # on another engine has to LEAVE the list or it reads as blocked forever. Books outside
        # this run's scope are carried through — the log is library-wide, the run is not.
        processed = set(made) | set(kept) | {r[0] for r in failures}
        write_failures(merge_failures(read_rows(syn_fail()), processed, failures), syn_fail())
        if failures:
            report.say(f"  {len(failures)} failed -> {os.path.basename(syn_fail())} "
                       f"(retry with another engine: scourgify synopsis --apply --engine openai)")
        if a.step and made:
            made = step(made, self.titles)
        if not (made or kept):
            report.say("(nothing settled — nothing written.)"); return
        ops = []
        if not self.have_stamp:
            ops.append(op_create_column("synopsized", "Synopsized", "datetime"))
        if made:
            ops.append(op_set_field("comments", made))
        # stamp EVERY settled book, generated or kept — an unstamped kept blurb would be re-read
        # (and re-judged) on every future sweep, which is the classify no-tag bug in a new field.
        ops.append(op_stamp_now(STAMP, sorted(set(made) | set(kept))))
        run_writer(ops, tool="synopsis", scope=f"{len(made)} written, {len(kept)} kept")
        report.say(f"settled {len(made) + len(kept)} book(s): {len(made)} new synopses, "
                   f"{len(kept)} existing blurbs kept.")


def step(made: dict, titles: dict) -> dict:
    """1-by-1 review of the generated synopses -> the ACCEPTED subset ({} = nothing decided).

    Lives here, not in the wizard: CLAUDE.md's rule is that a wizard stage calls the same engine
    function the subcommand does, so `synopsis --apply --step` and the wizard share one path. An
    unticked book gets neither its new description NOR the stamp — it stays in the queue, which
    is the point of rejecting it."""
    from scourgify import ui
    if not ui.interactive():
        raise GuardrailError("--step needs an interactive terminal (omit it to write every synopsis).")
    ids = sorted(made)
    acc, _, action = ui.checklist(
        "new synopses — untick one to leave that book's description alone",
        [f"[bold]#{b}[/] {str(titles.get(b, ''))[:36]:<36} [dim]{made[b][:120]}…[/]" for b in ids])
    if action in ("skip", "quit"): return {}
    return {ids[i]: made[ids[i]] for i in acc}


def plan(a: argparse.Namespace) -> Plan:
    """Resolve a synopsis run ONCE — the wizard confirms over this plan and run() executes the
    SAME one."""
    return Plan(a)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Settle every book's synopsis into its description (free + on-device by default; "
                    "dry-run until --apply).")
    p.add_argument("--apply", action="store_true",
                   help="generate and write (Calibre closed). Without it NOTHING is sent — unlike "
                        "classify, a bare run here really is free.")
    p.add_argument("--step", action="store_true",
                   help="with --apply: review each new synopsis 1-by-1 (untick to leave a description alone)")
    p.add_argument("--engine", default="apple", choices=sorted(ENGINES),
                   help="apple = on-device, free, slow (default; the engine this pass is designed for)")
    p.add_argument("--books", default=None, metavar="SPEC",
                   help="only these books: '1,2,3', '10-20', '@ids.txt', or a combination "
                        "(re-settles them whatever their stamp says)")
    p.add_argument("--last", type=int, default=0, metavar="N",
                   help="only the N most recently added books (the same N as classify --last)")
    p.add_argument("--batch", type=int, default=0, metavar="N",
                   help="settle only N books this run — the queue advances, so re-run to continue")
    p.add_argument("--force", action="store_true",
                   help="run even though FanFicFare may overwrite the descriptions (a clobbered "
                        "book re-enters the queue and is re-summarized)")
    p.add_argument("--model", default="", help="override the per-engine default model")
    p.add_argument("--timeout", type=int, default=120, metavar="S", help="per-request HTTP timeout")
    return p


def default_opts(**overrides) -> argparse.Namespace:
    """The non-CLI entry to a run's options: parser defaults + keyword overrides. The argparse
    parser stays the single schema; the wizard is the second adapter that fills it."""
    a = build_parser().parse_args([])
    for k, v in overrides.items(): setattr(a, k, v)
    return a


def main() -> None:
    a = build_parser().parse_args()
    if a.books is not None and a.last:
        raise GuardrailError("--books and --last are two ways to name the same thing — pick one.")
    plan(a).run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Dispatch it from the CLI**

In `src/scourgify/cli.py`, after the `staleness` branch (line 38):

```python
    if argv and argv[0] == "synopsis":
        from scourgify import synopsis
        sys.argv = ["scourgify synopsis", *argv[1:]]
        return synopsis.main()
```

Update the module docstring's dispatch line to
`classify -> classify; synopsis -> synopsis; staleness -> staleness.`

- [ ] **Step 5: Retire `--text-fallback` from `classify.py`**

Five edits, all deletions:

1. Line 271–272 — the `--unclassified` branch:
```python
    elif a.unclassified:                          # the advancing scope: never attempted, and sendable
        ids = select.pick(con, "unclassified")    # seen + the sendable filter default inside pick
        scope = f"never classified ({len(ids)} outstanding)"
```
2. Lines 282–288 — `bookfile` and `text_for` collapse to a plain read:
```python
    # a thin description is SYNOPSIS work now, not a raw-prose sample at tag time (#69):
    # `scourgify synopsis` writes those books a real description and they graduate back here.
    targets = [(b, strip_html(desc.get(b, ""))) for b in ids]
```
   (delete the `bookfile = …` line and the whole `def text_for` block).
3. Line 296 — the drop note:
```python
        print(f"  note: {len(targets) - len(kept)} dropped (description under 40 chars — "
              "`scourgify synopsis` gives these books a real one)")
```
4. Line 320 — the resume condition:
```python
                if not self.needs(bid):           # re-process only books explicitly scoped this run
```
5. Line 476 — delete the `--text-fallback` argument entirely; line 499 — delete
   `a.text_fallback = True` and its comment from `bakeoff_cli`.

Finally, drop `booktext` from classify's imports if nothing else uses it
(`grep -n booktext src/scourgify/classify.py` must come back empty; keep `strip_html`, which
classify imports from `booktext` — adjust the import to
`from scourgify.booktext import strip_html` if it currently imports the module).

- [ ] **Step 6: Repoint `booktext.py`'s docstring**

Line 2 of `src/scourgify/booktext.py`:

```python
"""Book-text extraction for the synopsis pass — one deep interface over two strategies.

`paths(con)` discovers each book's best format file (EPUB preferred); `extract(path, limit=)`
pulls readable text from it: EPUBs are read directly as zips of XHTML (with a zip-bomb guard and
a nav-page heuristic); every other format shells out to Calibre's `ebook-convert` with a timeout.
No LLM, no library state — testable against a fixture EPUB. (It began as `classify
--text-fallback`'s prose sampler; that flag is retired — synopsis.py needs extraction more than
classify ever did, and reads whole books rather than a slice.)"""
```

Update `tests/test_booktext.py`'s docstring line 2 the same way
(`"""Pins booktext.py — the text extractor behind the synopsis pass. …`).

- [ ] **Step 7: Run every affected suite**

Run:
```bash
uv run tests/test_synopsis.py && uv run tests/test_classify_run.py && \
uv run tests/test_booktext.py && uv run tests/test_selection.py && \
uv run tests/test_synopsis_queue.py && uv run tests/test_plugin_safety.py
```
Expected: PASS. If `test_classify_run.py` references `text_fallback` anywhere, delete those
lines — the flag no longer exists.

- [ ] **Step 8: Smoke the CLI end to end against the fixture**

Run: `uv run scourgify synopsis --help`
Expected: the help text, with no `--text-fallback` anywhere in `uv run scourgify classify --help`.

- [ ] **Step 9: Commit**

```bash
git add src/scourgify/synopsis.py src/scourgify/classify.py src/scourgify/booktext.py \
        src/scourgify/cli.py tests/test_synopsis.py tests/test_classify_run.py tests/test_booktext.py
git commit -m "feat(synopsis): the pass, the write path, the CLI — and --text-fallback is retired"
```

---

### Task 5: The wizard stage

**Files:**
- Modify: `src/scourgify/wizard.py` (`snapshot` :42, `header` :80, `stage_classify` :249/:274,
  `TASKS` :477, `_task_hint` :523)
- Modify: `tests/test_wizard_flow.py`

**Interfaces:**
- Consumes: `synopsis.plan`, `synopsis.default_opts`, `select.pick(con, "unsynopsized")`.
- Produces: `wizard.stage_synopsis()`, `wizard._synopsis_options(n) -> list`,
  `snapshot()["unsynopsized"]`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_wizard_flow.py`, add before the `if __name__` block:

```python
def test_snapshot_reports_the_synopsis_queue():
    """The header must never claim 'up to date' while the synopsis sweep is outstanding — the
    same rule the never-classified backlog earned."""
    with wizard_lib():
        info = wizard.snapshot()
        assert info["unsynopsized"] == 2, info
        assert "2 awaiting synopsis" in wizard._task_hint("synopsis", info)


def test_synopsis_stage_skip_reaches_no_engine():
    """The wall-clock pin, the sibling of test_classify_scope_skip_reaches_no_engine: this pass
    is free but SLOW (~40s a book on-device), so a stage that runs when the user said skip costs
    days, not euros."""
    from scourgify import synopsis
    def boom(*a, **k):
        raise AssertionError("synopsis.plan must not run after a skip")
    saved = synopsis.plan
    synopsis.plan = boom
    try:
        with wizard_lib(), common.scripted_answers(["3"]), transcript() as buf:
            wizard.stage_synopsis()
    finally:
        synopsis.plan = saved
    assert "nothing settled" in buf.getvalue()


def test_the_wizard_never_drives_the_synopsis_checklist_itself():
    """CLAUDE.md's hard rule, enforced the way tests/test_cli.py enforces it: the wizard ASKS,
    the tool DOES — otherwise the CLI's --step and the wizard's review can diverge."""
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "src", "scourgify", "wizard.py")).read()
    assert "ui.checklist" not in src
```

Update the full-lap test: `synopsis` becomes slot 3, so classify..overrides shift by one.

```python
    lap = ["1", "n",                  # wrangle  (clean fixture: no apply menu) -> decline staleness
           "2", "n",                  # staleness (already consistent)          -> decline synopsis
           "3", "3", "n",             # synopsis -> skip (slot 3)               -> decline classify
           "4", "5", "n",             # classify -> scope skip (slot 5)         -> decline review
           "5", "2", "s", "s", "n",   # review -> 1-by-1 (slot 2), SKIP both    -> decline promote
           "6", "n",                  # promote (no candidates)                 -> decline backfill
           "7",                       # backfill (nothing to do; no successor)
           "8",                       # overrides (no rejects logged; not in the workflow)
           "q"]
```
and in the same test's marker list, add `("nothing settled", "synopsis")` after the staleness
row, and change the menu-count assertion to `== 9` (one more task).

- [ ] **Step 2: Run to verify they fail**

Run: `uv run tests/test_wizard_flow.py`
Expected: FAIL — `KeyError: 'unsynopsized'` / no `stage_synopsis`.

- [ ] **Step 3: Add the stage and wire the header**

In `src/scourgify/wizard.py`:

Import `synopsis` on line 23's import list:
```python
from scourgify import artifacts, common, engines, wrangle, classify, synopsis, staleness, select, promote, overrides
```

Add `"#synopsized"` to `COLS` (line 30):
```python
COLS = ["#fandoms", "#characters", "#relationships", "#genres", "#status", "#updated",
        "#wrangled", "#synopsized"]
```

In `snapshot()`, beside the `unclassified` probe:
```python
        try: unsynopsized = len(select.pick(con, "unsynopsized"))
        except Exception: unsynopsized = 0
```
and add `"unsynopsized": unsynopsized,` to the returned dict.

In `header()`, after the `classify` row:
```python
    g.add_row("synopsis", f"[cyan]{info['unsynopsized']:,} awaiting a synopsis[/]  → the synopsis step, "
                          "free and on-device" if info.get("unsynopsized")
              else "[green]every synopsis settled ✓[/]")
```

Add the stage after `stage_staleness` (before `_engines`):

```python
def _synopsis_options(n: int) -> list:
    """PURE half of the synopsis menu — one fixed slot layout whatever the queue holds."""
    return [
        ("1", "apply" if n else None, f"settle {n:,} books" if n else "settle — nothing outstanding",
         "judge each existing blurb; keep the good ones untouched, generate a back cover for the rest"
         if n else "every book's synopsis is already settled"),
        ("2", "step" if n else None, "review 1-by-1",
         "generate first, then walk each NEW synopsis; untick to leave that description alone"
         if n else "nothing to walk"),
        ("3", "skip", "skip", "leave descriptions unchanged (free, but slow — a chunk at a time is fine)"),
    ]


def stage_synopsis():
    """Asks; synopsis.py does the work — the pass, its guard, its checklist and its write all
    live there, so `scourgify synopsis` and this stage cannot diverge.

    Engine is apple by default and deliberately not offered here: free, on-device, and the engine
    this pass is designed for. A cloud engine is a per-stalled-book CLI opt-in (--engine), not a
    thing to fat-finger over a 7,949-book sweep."""
    con = ro_connect(); n = len(select.pick(con, "unsynopsized")); con.close()
    if n:
        ui.say(f"[cyan]{n:,}[/] book(s) have no settled synopsis. Good blurbs are kept as they are; "
               "only thin ones are generated from the book's own prose.", "dim")
    choice = ui.menu("synopsis", _synopsis_options(n), default="apply" if n else "skip")
    if choice == "skip":
        ui.say("(skipped — nothing settled)", "dim"); return
    batch = 0
    if n > BATCH_DEFAULT:
        # ~40 s a book on-device, so the whole queue is days of wall-clock: ask how much of it to
        # do now. N is a COUNT slicing an identity-keyed set — the next run resumes at the next N.
        batch = ui.ask_int(f"how many books this run?  {n:,} outstanding", BATCH_DEFAULT, lo=1, hi=n)
    p = synopsis.plan(synopsis.default_opts(apply=True, step=choice == "step", batch=batch))
    if not p.todo:
        ui.say("every book's synopsis is settled ✓", "green"); return
    p.run()
    ui.say("done ✓", "green")
```

Add the task row and renumber (`TASKS`, line 477):

```python
TASKS = [
    ("1", "wrangle",   "normalize raw tags/fandoms/characters/genres — deterministic cleanup first, so "
                       "junk tags don't hide books from the classifier", stage_wrangle, True),
    ("2", "staleness", "re-derive #status from #updated age (free, no API)", stage_staleness, True),
    ("3", "synopsis",  "settle every book's description — keep the good blurbs, write back covers for "
                       "the thin ones (free, on-device, slow)", stage_synopsis, True),
    ("4", "classify",  "AI content tagging — only books new/changed since the last run", stage_classify, True),
    ("5", "review",    "inspect the pending proposal, then apply it to the library", stage_review, True),
    ("6", "promote",   "adjudicate new-tag candidates against the master list — promote / alias / reject",
                       stage_promote, True),
    ("7", "backfill",  "apply vocab-promoted tags to the books that first suggested them (deterministic)",
                       stage_backfill, True),
    ("8", "overrides", "turn --step-rejected deterministic changes into personal override rules",
                       stage_overrides, False),
]
```

In `_task_hint`, before the `classify` branch:
```python
    if name == "synopsis":
        return f"{info['unsynopsized']:,} awaiting synopsis" if info.get("unsynopsized") else ""
```

Finally, in `stage_classify` (line 274) drop the retired kwarg and its stale comment:
```python
    # exactly one scope flag (whole-library reuses select.pick("all")). The plan is resolved ONCE
    # — the cost shown and confirmed below is over the same `todo` set classify_run executes
    # (never a re-gather). NB batch must be set BEFORE plan(): Plan.__init__ is its only reader.
    a = classify.default_opts(incremental=scope == "changed",
                              unclassified=scope == "unclassified", last=last, batch=batch,
                              **{"all": scope == "all"})
```
and at line 249 replace the comment `# bare pick = the wizard's own measure: text-fallback on,
seen owned by select` with `# bare pick = the wizard's own measure: the invariant lives in select`.

- [ ] **Step 4: Run the wizard flow suite**

Run: `uv run tests/test_wizard_flow.py`
Expected: PASS

- [ ] **Step 5: Run the wizard-adjacent suites**

Run: `uv run tests/test_wizard.py && uv run tests/test_cli.py && uv run tests/test_script.py`
Expected: PASS. `test_wizard.py` may assert on `COLS`/`_scope_options` counts — update those
numbers in this commit if so.

- [ ] **Step 6: Drive a real terminal once**

Run: `uv run tests/drive_wizard.py`
Expected: header shows the synopsis row, landing menu lists 8 tasks, clean quit.

- [ ] **Step 7: Commit**

```bash
git add src/scourgify/wizard.py tests/test_wizard_flow.py tests/test_wizard.py
git commit -m "feat(wizard): the synopsis stage, before classify, with the queue in the header"
```

---

### Task 6: Docs, version, and the full-suite gate

**Files:**
- Modify: `CONTEXT.md`, `AGENTS.md` (== `CLAUDE.md`), `README.md`, `USERGUIDE.md`,
  `CHANGELOG.md`, `pyproject.toml`

**Interfaces:** none (documentation and metadata only).

- [ ] **Step 1: `CONTEXT.md` — add the two settled terms and amend Backlog**

Replace the **Booktext** entry (lines 23–25) with:

```markdown
- **Booktext** — `booktext.py`, the text extractor behind the synopsis pass: `paths(con)` picks
  each book's best format (EPUB preferred), `extract(path, limit=)` reads prose (EPUB-as-zip, else
  `ebook-convert`).
- **Synopsis pass** — `synopsis.py`, the pass that gives every book a spoiler-safe back-cover
  description in Calibre's built-in `comments`. A good existing blurb is judged and KEPT (author
  voice preserved); only a bad one is generated, from the book's own prose in ≤`MAX_CHUNKS`
  slabs sized to the apple engine's measured 4,096-token window. Free, on-device, slow by design.
- **Settled** (`#synopsized`) — the per-book datetime meaning "this book's synopsis is settled",
  earned by generating one OR by inspecting and keeping an adequate blurb. One stamp, three jobs:
  queue membership (unstamped = outstanding), provenance, and the refresh clock (`#updated` newer
  than the stamp ⇒ the fic grew chapters, re-settle it; no `#updated` ⇒ never auto-refresh).
  _Avoid_: "summarized", "synopsis queue count" (as the number's name).
```

Amend the **Backlog** entry (lines 61–65) — the text-fallback clause is gone:

```markdown
- **Backlog** — the books classify has never *attempted* and could actually *send*: everything
  minus the attempted (classified_ids: applied archives + pending proposal + failure log) minus
  the unsendable (a description under `MIN_DESC`). A thin-blurb book is **not** in the backlog —
  it is [[Synopsis pass]] work, and re-enters the backlog by itself once it has a real
  description. Owned by `select.pick("unclassified")`, defaults included — every surface asks
  bare, so no two counters can disagree.
  _Avoid_: outstanding, unclassified count, never-classified (as the number's name).
```

Add to the **Artifact** list, after **Failures**:

```markdown
  - **Synopsis failures** (`synopsis_failures.csv`) — books the synopsis pass could not settle
    (unreadable file, refusal, unparseable verdict); same FAIL_COLS shape and the same
    self-clearing merge, so the sweep is finite on the failure side too.
```

- [ ] **Step 2: `AGENTS.md` — the architecture entry and the retirement**

Add a paragraph after the **`staleness.py`** paragraph:

```markdown
**`synopsis.py` — the synopsis pass** (roadmap #69): every book ends up with a *settled* synopsis
in Calibre's **built-in description** (`comments`) — the field Calibre and every reader show. The
cheap job runs first: a book whose blurb is already an adequate, spoiler-safe back cover is
judged in ONE call and KEPT untouched (never AI-flatten a library of fine author descriptions
into one beige voice); only a bad blurb earns whole-book generation — `booktext.extract` the
prose, `chunks()` it into ≤`MAX_CHUNKS` slabs of `CHUNK` chars (sized to the apple engine's
**measured 4,096-token** context — 18,000 chars of real prose is already 4,165 tokens and
refused), note each slab, then fold the notes into a back cover: premise, characters, stakes,
hook, themes line, **never** plot outcomes or endings. **`#synopsized`** (datetime) is the entire
state — queue membership, provenance, and the refresh clock (`#updated` > stamp ⇒ re-settle; no
`#updated` ⇒ never auto-refresh) — so resume needs no artifact. Failures go to
`synopsis_failures.csv` via `artifacts.py`, which is what keeps the queue finite on the failure
side (the same rule the classify backlog learned the hard way). **The pass refuses to start**
unless FanFicFare's Comments → "New Only" switch is on (`setup.comments_protected` reads
`std_cols_newonly` from the library db's FFF prefs blob — verified readable through
`ro_connect()` 2026-08-23); `--force` accepts the degraded self-healing mode. Engine: **apple**,
free and on-device and single-threaded, which is why the sweep is designed to run for weeks in
the background — a cloud engine is a per-stalled-book `--engine` opt-in, never the wizard's
default. **Unlike classify, a bare `scourgify synopsis` sends NOTHING** (there is no proposal
artifact to build), so the dry run really is free. **Transition rule: classify does NOT require
the stamp while the sweep runs** — decent-blurb books keep flowing to the tagger exactly as
today; requiring it on day one would park ~7.5k classifiable books behind a months-long sweep.
Once the sweep completes, making the stamp the classify gate is a follow-up decision.
```

Delete the `--text-fallback` clauses:
- in the `select.py` paragraph: `(select.sendable(): description ≥ MIN_DESC, or any book with a
  file to sample under --text-fallback)` → `(select.sendable(): description ≥ MIN_DESC)`; and the
  sentence "and `gather()` drops a thin-text book before it can reach a proposal, so without
  `sendable` it could never leave the set" keeps its point — append "(a thin-text book is
  synopsis work, and re-enters this scope once the pass gives it a description)".
- in the wizard paragraph: "and enables `--text-fallback` so thin descriptions get sampled rather
  than dropped" → delete; add **synopsis** to the stage order
  (`wrangle → staleness → synopsis → classify → review → promote → backfill`) in every place the
  order is spelled out (the wizard paragraph twice, and the maintenance-loop section).
- in the `classify.py` paragraph: `--text-fallback` samples the book's own prose… → delete the
  sentence; and in the flags list drop `--text-fallback`.
- in the `booktext.py` clause: "Book-text sampling for `--text-fallback` lives in **`booktext.py`**"
  → "Book-text extraction for the synopsis pass lives in **`booktext.py`**".

Add to the maintenance loop block, between staleness and classify:

```
          → uv run scourgify synopsis --apply --batch 200   # 2b. free + on-device; settle descriptions
```

Add to the read-only-checks list: `scourgify synopsis` (no `--apply`).

Add to the Verification paragraph: `uv run tests/test_synopsis.py` (the pure helpers, the FFF
guard and the write contract with `run_writer` stubbed) and `uv run tests/test_synopsis_queue.py`
(queue finiteness against `tests/fixture_db.py`).

- [ ] **Step 3: `README.md` + `USERGUIDE.md`**

`README.md:267` — replace
"`--text-fallback` samples the book's own prose for them. Always dry-run until `--apply`."
with
"Books with a thin description are **synopsis** work, not classify work: `scourgify synopsis`
writes them a real back-cover description from the book's own prose (free, on-device) and they
graduate into the classify backlog on their own. Always dry-run until `--apply`."

Add a `scourgify synopsis` section to both files' command lists, after `staleness`:

```markdown
### `scourgify synopsis`

Gives every book a spoiler-safe back-cover description in Calibre's own description field.
A blurb that is already good is judged in one call and kept exactly as the author wrote it;
only a thin or useless one is replaced by a synopsis generated from the book's own prose.

```bash
scourgify synopsis                        # dry run — resolves the queue, sends nothing, costs nothing
scourgify synopsis --apply --batch 200    # settle 200 books (Calibre closed)
scourgify synopsis --apply --step         # ...reviewing each new synopsis before it is written
scourgify synopsis --books 12,40-45 --apply   # re-settle named books whatever their stamp says
```

Free and on-device by default (`--engine apple`) and deliberately slow — around a minute a book —
so run it in chunks in the background. Progress lives in the library as the `#synopsized`
datetime column, so a run resumes wherever the last one stopped.

**Before the first run**, turn on FanFicFare → Customize → Standard Columns → Comments →
**New Only**, or FFF will overwrite the synopses on the next metadata re-fetch. The pass refuses
to start until you do (`scourgify setup` offers the same fix; `--force` overrides).
```

- [ ] **Step 4: Bump the version and the changelog**

`pyproject.toml` line 3: `version = "1.18.0"`.

Prepend to `CHANGELOG.md` (matching the file's existing entry style):

```markdown
## 1.18.0

- **New: `scourgify synopsis`** — every book gets a settled, spoiler-safe back-cover description
  in Calibre's own description field. Good author blurbs are judged and kept untouched; thin ones
  are generated from the book's own prose on-device (free). Progress is the new `#synopsized`
  datetime column, so runs resume; failures land in `synopsis_failures.csv`.
- **New wizard stage**, between staleness and classify, with the queue in the status header.
- `scourgify setup` now warns when FanFicFare's Comments → "New Only" switch is off (it would
  overwrite the synopses), and offers to turn it on. The synopsis pass refuses to start without it.
- **Removed: `classify --text-fallback`.** A thin-blurb book is synopsis work now, not a raw prose
  sample taken at tag time; it re-enters the classify backlog by itself once it has a description.
```

- [ ] **Step 5: Run every suite**

Run:
```bash
for f in tests/test_*.py; do uv run "$f" >/dev/null 2>&1 || echo "FAIL $f"; done; echo done
```
Expected: only `done` — no `FAIL` lines.

- [ ] **Step 6: Confirm the retirement is total**

Run: `grep -rn "text_fallback\|text-fallback" src tests README.md USERGUIDE.md CONTEXT.md AGENTS.md`
Expected: no matches. (`docs/superpowers/plans/` and `docs/superpowers/reports/` are dated
historical records — leave them.)

- [ ] **Step 7: Build the wheel and check its contents**

Run: `uv build && unzip -l dist/*.whl | grep -E "synopsis|defaults/|afm"`
Expected: `scourgify/synopsis.py` present; `scourgify/defaults/*` and `afm.swift` still present;
no `data/`, `overrides/`, or compiled `afm`.

- [ ] **Step 8: Commit**

```bash
git add CONTEXT.md AGENTS.md README.md USERGUIDE.md CHANGELOG.md pyproject.toml
git commit -m "docs: the synopsis pass, the settled stamp, and the text-fallback retirement"
```

---

### Task 7: Land it

**Files:** none (git + GitHub only).

- [ ] **Step 1: Open the PR against `develop`**

```bash
git push -u origin feat/69-synopsis-pass
gh pr create --base develop --head feat/69-synopsis-pass \
  --title "Synopsis pass (#69) — settled descriptions, and --text-fallback retired" \
  --body "$(cat <<'EOF'
Closes #69.

Every book ends up with a **settled** synopsis in Calibre's own description field: a good
author blurb is judged in one on-device call and kept untouched; a thin one is replaced by a
spoiler-safe back cover generated from the book's own prose. `#synopsized` (datetime) is the
whole state — queue, provenance, and refresh clock.

- New `scourgify synopsis` + wizard stage between staleness and classify.
- The queue lives in `select.pick("unsynopsized")`; failures in `synopsis_failures.csv` keep it finite.
- `scourgify setup` warns when FanFicFare's Comments → "New Only" is off; the pass refuses to
  start without it (`--force` accepts the degraded self-healing mode).
- **`classify --text-fallback` is deleted.** A thin-blurb book is synopsis work now and rejoins
  the classify backlog on its own.

Verified before building: FFF's `std_cols_newonly` IS readable through `common.ro_connect()`
(so the guard is automatic, not an interactive confirm), and the apple engine's context is a hard
4,096 tokens — 18,000 chars of real prose already exceeds it, which is what sizes `CHUNK`.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 2: Wait for CI, then rebase-merge**

Run: `gh pr checks --watch && gh pr merge --rebase --delete-branch`
Expected: both CI checks green; `develop` history stays linear.

- [ ] **Step 3: Close #69 with the deferred decision recorded**

```bash
gh issue close 69 --comment "$(cat <<'EOF'
Built and landed on `develop`.

**Shipped:** `scourgify synopsis` (new tool module + wizard stage between staleness and classify),
the `#synopsized` settled stamp, the `synopsis_failures` artifact, the FanFicFare Comments →
"New Only" guardrail plus its `scourgify setup` health-check line, and the full retirement of
`classify --text-fallback`.

**Both build-time facts verified before any code was written:**
- FFF's `std_cols_newonly` is readable through `common.ro_connect()` (JSON under
  `namespaced:FanFicFarePlugin:settings` in the `preferences` table), so the guard is **automatic**
  rather than the interactive-confirm fallback Q1 allowed for.
- The apple engine's context is a hard **4,096 tokens** shared by prompt and answer: 17,000 chars
  of real prose pass, 18,000 fail at 4,165 tokens. That sizes the map-reduce at 10,000 chars a
  slab, capped at 12 slabs a book.

**Deferred, as designed (Q3's transition rule):** classify does **not** require `#synopsized`
while the sweep runs — decent-blurb books keep flowing to the tagger exactly as today. Making the
stamp the standard classify gate is a **post-sweep follow-up decision**, not part of this build.
EOF
)"
```

- [ ] **Step 4: Journal the landing**

Invoke the `vault-journal` skill with a one-line pointer: the synopsis pass landed on `develop`
in `elfensky/scourgify` (#69 closed, v1.18.0), `--text-fallback` retired.

---

## Self-review

**Spec coverage.**

| Requirement (briefing / #69) | Task |
|---|---|
| New tool module + `scourgify synopsis` subcommand | 3, 4 |
| Dry-run default / `--apply` / `--step` / `--batch` / resume off the stamp / `plan()` | 4 |
| `#synopsized` datetime stamp, earned both ways | 3 (constant), 4 (write), 1 (queue) |
| Refresh clock `#updated > #synopsized`, degrading without `#updated` | 1 (`resynopsize`) |
| Storage = built-in `comments` | 4 (`op_set_field("comments", …)`) |
| FFF "New Only" guardrail + `--force` degraded mode | 2, 3 |
| `scourgify setup` health-check line | 2 |
| `synopsis_failures` artifact via `artifacts.py` | 1, 4 |
| Spoiler-safe back cover + themes line | 3 (`BACK_P`) |
| Adequacy fast path — judge the blurb only, keep on yes | 3 (`settle`) |
| Whole library scope, chapter-wise map-reduce sized to the engine | 3 (`chunks`, measured) |
| Engine = apple, cloud opt-in, €0 full sweep | 3/4 (`--engine` default), 5 (no picker in the stage) |
| Queue + backlog redefinition confined to `select.py` | 1 |
| Wizard header shows both numbers | 5 |
| Wizard stage before classify, asks only | 5 |
| `--text-fallback` deleted everywhere incl. docs | 4, 6 |
| classify's `MIN_DESC` gate stays (transition rule) | 4 (untouched) |
| Plugin safety: rich-clean import, `GuardrailError` | 3 (CORE list + guard tests) |
| Tests: queue, adequacy branch, refresh clock, guard, prompt round-trip | 1, 3, 4 |
| Existing suites green through the retirement | 1, 4, 5, 6 |
| Version bump, PR to develop, rebase-merge | 6, 7 |
| CONTEXT.md: Synopsis pass, Settled, Backlog amendment | 6 |
| Close #69 + record the deferred post-sweep decision | 7 |

**Out of scope, kept out:** the stamp as classify's gate; the hidden full-digest artifact; the
plugin's synopsis verb; per-library artifact namespacing.

**Placeholders:** none — every code step carries the actual code.

**Type consistency:** `settle()` returns `(str, str)` and is called that way in `Plan.run`;
`step(made: dict, titles: dict) -> dict` matches its one call site; `chunks/clean/verdict` are
used with the signatures Task 3 defines; `select.pick(..., seen=)` keyword matches every test;
`artifacts.write_failures(rows, path)` matches the existing positional signature
(`write_failures(rows: list, path: str | None = None)`).
