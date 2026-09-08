---
phase: 01-foundation-a-hostable-core
reviewed: 2026-09-07T20:20:49Z
depth: standard
files_reviewed: 17
files_reviewed_list:
  - src/scourgify/common.py
  - src/scourgify/editlog.py
  - src/scourgify/wrangle.py
  - src/scourgify/classify.py
  - src/scourgify/engines.py
  - src/scourgify/staleness.py
  - src/scourgify/promote.py
  - src/scourgify/synopsis.py
  - src/scourgify/overrides.py
  - src/scourgify/wizard.py
  - src/scourgify/setup.py
  - src/scourgify/select.py
  - src/scourgify/artifacts.py
  - src/scourgify/booktext.py
  - src/scourgify/cli.py
  - src/scourgify/_writer.py
  - .github/workflows/ci.yml
findings:
  critical: 1
  warning: 2
  info: 1
  total: 4
status: resolved
resolved: 2026-09-08T00:00:00Z
resolution_commits:
  CR-01: 9870559
  WR-01: 721b10c
  WR-02: b5c2e2e
resolution_summary: .planning/phases/01-foundation-a-hostable-core/01-REVIEW-FIXES.md
---

# Phase 01: Code Review Report

**Reviewed:** 2026-09-07T20:20:49Z
**Depth:** standard
**Files Reviewed:** 17
**Status:** issues_found

## Summary

Phase 01 lands the write-funnel unification (`common._write_run`), the library-uuid path
scoping, the `defaults_dir()` resource seam, the Windows CI lane, and the apply-time
conflict filter (`common._check_conflicts` / `editlog.conflict`). The bulk of the diff
(the cp1252 `encoding="utf-8"` sweep and the `contextlib.closing(ro_connect())` sweep) is
mechanical and clean — verified with an AST scan that no non-binary `open()` call in the
17 reviewed files is missing `encoding=`, and that every `ro_connect()` call site in the
reviewed files is closed.

The one finding that matters: **wrangle's new plan-time `expected` (D-09) is built with a
hardcoded multi-value shape, without checking whether the target column is actually
single-valued** (`common.column_is_multiple`/`field_is_multiple_via_api` say so correctly
at apply time, but `wrangle.Plan.write()` never asks). Where the two disagree, the new
conflict filter (this phase's own headline feature, meant to protect against silent
overwrites) instead silently and permanently disables writes to that column. The scenario
that triggers it is realistic: `setup.py`'s column adoption matches an existing Calibre
column purely by label, never checking `is_multiple`, so a user who already has a
single-valued `#fandoms`/`#characters`/`#relationships`/`#genres` column (common for
non-crossover libraries, or an import from a different tool) inherits the bug the first
time they run `scourgify apply --apply`.

Two further findings are resource-lifetime regressions that partially undercut this same
phase's own Windows-safety sweep (Plan 01-02) — one holds a read connection open for the
duration of an hour-long subprocess, the other is an unlocked check-then-act race on a
migration guard that is reachable exactly under the concurrency model (multiple Calibre
`ThreadedJob`s against one library) this milestone is building toward.

## Critical Issues

### CR-01: wrangle's plan-time `expected` is unconditionally list-shaped, silently disabling writes to any wrangle-managed column that is actually single-valued

**File:** `src/scourgify/wrangle.py:433-436`
**Issue:**

```python
for lab, ch in self.changes.items():
    k = next(key for key, label in self.cols.items() if label == lab)
    expected = {b: sorted(self.perbook[b].get(k, [])) for b in ch}
    ops.append(op_set_field(lab, ch, expected=expected))
```

`self.perbook[b][k]` is populated by `read_library()` (`wrangle.py:257-264`), which calls
`read_custom_column(con, label, multi=True)` **unconditionally for every non-tags column**
— so `self.perbook[b][k]` is always a `list`, even for a genuinely single-valued Calibre
column (`is_multiple=False`). `sorted(...)` therefore always produces a `list`, and that
list is what `op_set_field`'s new `expected=` parameter carries.

At apply time, `common._check_conflicts` (`common.py:893-936`) asks the REAL schema for
the column's multi-ness via the injected `is_multi` reader
(`column_is_multiple`/`field_is_multiple_via_api`), which correctly reports `False` for a
single-valued column. `editlog.conflict(current, expected, multi=False)` (`editlog.py:152-162`)
then takes the single-value branch:

```python
return ("" if current is None else str(current)) != ("" if expected is None else str(expected))
```

`current` (the real before-read, via `column_values()`'s custom-column branch, which
correctly reads the column as a scalar because it too consults `column_is_multiple`) is a
plain string, e.g. `"Harry Potter"`. `expected` is `["Harry Potter"]` (a Python list, from
the wrangle producer above). `str("Harry Potter") != str(["Harry Potter"])` is **always**
`True` — every single such op is reported as "current value no longer matches" and
silently dropped (`common.py:927-935`), even on the very first run, when nothing has
drifted at all.

**Concrete failure:** a library where the Calibre custom column backing `#fandoms` (or
`#characters`/`#relationships`/`#genres`) was created as single-valued — plausible for
anyone who created that column before adopting scourgify, since `setup.py`'s adoption
step (`for label, key in (("#fandoms","fandoms"), ...): if not colmap.get(key) and label
in have: colmap[key] = label`) matches purely by label and never checks `is_multiple`.
`scourgify apply --apply` will report changes in `preview()`/`audit()` exactly as before
(those read `self.changes`/`self.diffs`, untouched by this bug), the guard passes, the
snapshot is taken, and `run_writer` reports success — but **every op for that column is
silently skipped** by the new conflict filter, forever, on every subsequent run, with no
error and no distinguishing symptom (the "skipped" line is worded identically to a real
value-drifted case). Wrangle's core normalization for that column is permanently inert.

This did not exist before this phase: previously `op_set_field(lab, ch)` carried no
`expected` key at all, so `_check_conflicts`'s `"expected" not in o` branch passed every
op through untouched (`common.py:916-919`) and the write actually applied. This is a
regression introduced specifically by plan 01-06's `expected=` addition to `wrangle.py`.

No test in `tests/test_plan.py` or `tests/test_write_path.py` exercises a wrangle-managed
column that is single-valued (`is_multiple=False`) — the only single-valued column
wrangle's `cols` mapping includes today (`#status`) is never actually mutated by
`transform()` (`newd["status"] = st`, an unconditional pass-through — `wrangle.py:241`),
so the bug is currently latent for the shipped default config and fires only on a
misconfigured/adopted column, which is exactly why it slipped through this phase's
otherwise-thorough new conflict-filter test suite.

**Fix:** thread `is_multi`/`column_is_multiple` (or the schema's own multi-value flag)
into `Plan.write()`'s `expected` construction, mirroring what `_check_conflicts` already
does — e.g. store the value as a scalar (`self.perbook[b][k][0] if self.perbook[b].get(k)
else None`) when the target column is not multi-valued, or compute `expected` from
`common.column_values()`'s own already-correct shape instead of re-deriving it from
`self.perbook`. At minimum, add a `column_is_multiple`-aware branch, and a regression
test that builds a fixture library with a single-valued custom column mapped into
`cfg["columns"]` and asserts the write actually lands.

## Warnings

### WR-01: `run_writer` holds a read-only sqlite connection open across the entire (up to 1-hour) `calibre-debug` subprocess call

**File:** `src/scourgify/common.py:1183-1210`
**Issue:** `run_writer` wraps its whole body — including the `subprocess.run([cb, "-e",
..., "--", f.name], ..., timeout=3600)` call, which is `calibre-debug` writing to that
same `metadata.db` — inside `with contextlib.closing(ro_connect()) as con:`. Every
closure that actually reads through `con` (`populated=`, `read=`, `is_multi=` passed into
`_write_run`) is consumed synchronously by `_write_run` **before** its single `yield`
(`check_wipe`, `editlog.before_values`, `_check_conflicts` all run pre-yield —
`common.py:1096-1113`). Nothing in `run_writer`'s body touches `con` again after entering
`with _write_run(...) as state:`. The connection is therefore held open, unused, for the
entire duration of an external process that may run for up to an hour and is actively
rewriting the exact file this connection has open.

This directly regresses the resource-lifetime lesson this same phase's plan 01-02 fixed
in 8 other modules a few hours earlier (`wrangle.read_library`, `classify.gather`,
`staleness.compute`, etc. — all wrapped in `contextlib.closing(ro_connect())` specifically
*so the handle doesn't outlive its need*, because Windows will not let a `tempfile`/file
operation past an open handle). Before this phase's refactor, the pre-existing code
explicitly closed this same connection (`finally: con.close()`) right after reading
`lib_uuid`, well before `backup_db()`/the subprocess spawn.

**Concrete risk:** on Windows, an OS-level file handle to `metadata.db` stays open in the
scourgify process for the full write duration; while ordinary SQLite reader/writer
arbitration should tolerate this (no open transaction is held), it is an unnecessary,
untested widening of the exact hazard class (`PermissionError: [WinError 32]`-style file
locking) this phase's own CI run discovered and fixed elsewhere, and it was not called out
in any plan/summary as an intentional trade-off.

**Fix:** close `con` (or narrow the `with` block) immediately after the closures
`populated=`/`read=`/`is_multi=` are no longer needed — i.e. right after entering
`with _write_run(...) as state:` and reading `state["ops"]`/`state["backup"]`, before
constructing the tempfile and spawning `calibre-debug`. A `sqlite3.Connection` object
doesn't need to stay alive for lambdas that already ran to completion.

### WR-02: `migrate_legacy_data()`'s one-time-attempt guard is an unlocked check-then-add race

**File:** `src/scourgify/common.py:297-302`
**Issue:**

```python
key = (home, uid)
if key in _MIGRATION_TRIED:
    return None
_MIGRATION_TRIED.add(key)
```

`_MIGRATION_TRIED` is a plain module-level `set`, guarded by nothing. Two threads calling
`data_dir()` (which calls `migrate_legacy_data()` on every resolution, `common.py:257-259`)
for the **same** library, before either has resolved a library for the first time in this
process, can both observe `key not in _MIGRATION_TRIED` and both proceed into the
all-or-nothing move logic below, each independently `os.listdir()`-ing the same
`legacy_root` and looping `os.rename()` over the same files.

`os.rename` is a syscall and releases the GIL; the two threads' loops can interleave.
Whichever thread loses the race on a given entry sees `os.path.exists(dst)` become `True`
(the winner already moved it) while its own `src` has vanished (renamed away by the
winner). `_same_bytes(src, dst)` then tries `open(src, "rb")`, gets `OSError` (file gone),
and returns `False` — which the loser reads as "already exists with different content"
and raises `GuardrailError(...)` (`common.py:367-369`), a **misleading** message masking a
benign race, surfacing as an unexpected exception from what every artifact-path function
in the codebase treats as a plain, side-effect-free read path (`data_dir()`).

This is exactly the concurrency model — multiple Calibre `ThreadedJob`s dispatched against
one library — this whole milestone (`.claude/CLAUDE.md`) targets, and `data_dir()` is
called from nearly every read and write path in the core. `CONTEXT.md`'s D-02/D-03 discuss
the *multi-library* safety of this guard (keyed by `(user_dir(), uuid)` instead of a bare
boolean) at length, but nothing addresses same-library, concurrent-thread safety, and no
test in `tests/test_paths.py` exercises concurrent first-time resolution.

No actual data loss results (the winner's move still completes correctly, and the
all-or-nothing rollback protects the loser's own, empty-so-far journal), but a legitimate
read triggers a confusing, unnecessary crash under concurrency this phase is explicitly
being built to support.

**Fix:** guard the check-and-set with a `threading.Lock` (module-level, mirroring
`_WRITE_LOCKS`'s pattern but for reads), or make the migration attempt itself
lock-protected per `(home, uid)` key so only one thread ever performs the move for a given
library, with the others waiting for or observing its result rather than racing it.

## Info

### IN-01: CI's Calibre installer downloads are not checksum-verified

**File:** `.github/workflows/ci.yml` (both `smoke-calibre-windows` and `smoke-calibre-linux` jobs)
**Issue:** Both new smoke jobs fetch the Calibre installer over HTTPS
(`download.calibre-ebook.com`) and execute it (`msiexec /qn ...msi`, `sh linux-installer.sh`)
without verifying a checksum or signature against the pinned `CALIBRE_VERSION`. This is
test infrastructure, not shipped product, and HTTPS + a pinned version already narrow the
supply-chain surface considerably, but a compromised or mirror-served installer would run
unchecked on the runner.
**Fix:** if `download.calibre-ebook.com` publishes checksums/signatures for pinned
releases, verify them before executing; otherwise document the accepted risk.

**Status: deferred, out of scope for this fix pass.** CR-01/WR-01/WR-02 were the three findings
assigned for gap closure (see `01-REVIEW-FIXES.md`); IN-01 is test-infrastructure hardening, not
a code defect, and was not part of that assignment. Left open for a future CI-hardening pass.

---

_Reviewed: 2026-09-07T20:20:49Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
