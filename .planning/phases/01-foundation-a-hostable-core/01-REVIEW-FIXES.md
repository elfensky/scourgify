---
phase: 01-foundation-a-hostable-core
type: review-fixes
review: 01-REVIEW.md
resolved: 2026-09-08
status: complete
---

# Phase 01: Code Review Gap-Closure — Fixes

Gap-closure pass on `01-REVIEW.md`'s three verified findings (CR-01 critical, WR-01/WR-02
warnings). Not new feature work — phase 01 had already passed verification 5/5 before this
review found these three defects, none of which the phase's own verification loop could have
caught (all three are concurrency/schema-arity edge cases outside its test surface at the time).

IN-01 (info: CI Calibre installer downloads not checksum-verified) was not assigned to this pass
and remains open — see the note in `01-REVIEW.md`.

## CR-01 (critical) — arity mismatch in the apply-time conflict filter

**Commit:** `9870559`

**What changed:** `common._check_conflicts` now runs both the funnel's before-read and the
producer's plan-time `expected` through a new `common._normalize_for_arity(value, multi)` before
either reaches `editlog.conflict`. `multi` is still the injected, schema-derived answer
(`is_multi`) — this only reshapes the two VALUES to that already-decided arity, never guesses
arity from a value's Python type.

**Why here and not in `wrangle.py`:** the review named the fix site explicitly — normalizing at
the one point both sides of the comparison meet covers all five write-producing tools (wrangle,
classify, staleness, promote, synopsis) at once. Fixing only `wrangle.py`'s `Plan.write()` would
have left four latent copies of the same bug (any future producer that builds a wrongly-shaped
`expected` hits the identical failure mode).

**setup.py decision:** setup's column adoption (section [4], `for label, key in (...)`) now reads
each existing column's real `is_multiple` (captured while the read-only connection is still open,
in section [3]) and prints a one-line warning when it adopts a single-valued column for a concept
scourgify treats as multi-valued (fandoms/characters/relationships/genres). **Decision: warn, not
refuse, and no schema recreation here.** The write-path bug this review found is now fixed at the
protocol level (CR-01 above), so adopting such a column is safe to WRITE — it is still
semantically lossy (Calibre itself can only ever hold the book's last value in a single-valued
column), and a one-line warning at setup time is cheap, in keeping with setup's existing
`{WARN}`-glyph output style, and gives the user the information to recreate the column themselves
if it matters to their library. Recreating the column automatically would need real schema work
(a legacy-DB reopen, data migration for existing values) — out of scope for a review-fix pass;
recorded here as the honest reason it was not attempted, not silently skipped.

**Regression tests (verified failing before the fix, passing after):**
- `tests/test_write_path.py::test_a_single_valued_column_with_a_wrangle_shaped_list_expected_still_applies_when_unchanged`
  — exercises the shared `write_ops` transport directly (not wrangle's own code), proving the fix
  lives in `_check_conflicts` and covers any producer, not just wrangle.
- `tests/test_plan.py::test_write_applies_a_single_valued_column_when_unchanged_and_skips_it_on_real_drift`
  — drives the real `wrangle.Plan.write()` end to end against a genuinely single-valued fixture
  column (`is_multiple=0`), through `run_writer` with `calibre-debug` stubbed. Confirms both
  directions: an unchanged single-valued column applies; a genuinely drifted one is still skipped.

Both were run against the pre-fix `_check_conflicts` (via a scratch copy of the pre-fix
`common.py`, per the "no `git stash`" constraint) and confirmed to fail with the exact symptom
the review described, then re-run against the fix and confirmed to pass.

## WR-01 (warning) — `run_writer` held its read connection open across the whole subprocess

**Commit:** `721b10c`

**What changed:** `run_writer` no longer wraps the `calibre-debug` subprocess call inside
`contextlib.closing(ro_connect())`. The connection is opened once, and closed explicitly right
after entering `_write_run`'s context (as soon as the injected `populated=`/`read=`/`is_multi=`
closures — which all run synchronously before `_write_run`'s single `yield` — are done with it),
before the tempfile is written and `calibre-debug` is spawned. An idempotent `con.close()` in an
outer `finally` is a safety net for any exception path raised earlier. Nothing captured or the
guard sequence changes — only how long the handle stays open.

**Regression test (verified failing before the fix, passing after):**
- `tests/test_write_path.py::test_run_writer_closes_its_read_connection_before_spawning_calibre_debug`
  — a tracking `sqlite3.Connection` subclass records each connection's own closed state (there is
  a second, legitimate, already-short-lived `ro_connect()` call inside `_resolve_uuid()` in the
  same run, so the assertion is "every connection opened so far is closed", not "exactly one was
  opened"). The stubbed `subprocess.run` asserts this holds at the moment `calibre-debug` would be
  spawned — proving the ORDER, not just that `close()` is eventually called.

## WR-02 (warning) — unlocked check-then-add race in the migration guard

**Commit:** `b5c2e2e`

**What changed:** the check ("have I tried this key?") and the claim ("mark it tried") on
`common._MIGRATION_TRIED` are now atomic under a new module-level `common._MIGRATION_LOCK`. A
fast, lock-free check stays at the top (cheap on every call once a key is claimed, per the
existing docstring's "self-disabling" property); if that check is inconclusive, the lock is taken
and the check is repeated inside it before claiming. Only the winner of the race ever proceeds
into the all-or-nothing move loop; the loser returns `None` immediately instead of racing
`os.rename()` against the winner. The move loop itself stays unlocked after the claim, since the
claim alone already guarantees no second thread for that key will ever reach it.

**Regression test (verified failing before the fix, passing after — 5/5 runs each way):**
- `tests/test_paths.py::test_concurrent_first_time_resolution_runs_the_migration_exactly_once` —
  two threads drive a cold `data_dir()` for the same library. A `_Rendezvous` wrapper
  double-barriers `_MIGRATION_TRIED`'s `__contains__`/`add()` calls (entry AND exit) so both
  threads' checks and claims genuinely interleave rather than merely starting close together (an
  earlier, simpler "pause on entry only" attempt let the winner run to completion before the loser
  was ever rescheduled, and never reproduced the race — recorded here as the reason this version
  is more elaborate than a single barrier). A keyed `os.rename` wrapper (`_RaceRename`) then forces
  two threads that both reach the move loop to collide on the identical file, the way the real bug
  does; a lone caller (the fixed code's normal case — only one thread ever gets past the claim)
  times out quickly (150ms) and proceeds solo. Confirmed reproducing the exact
  `GuardrailError("...No such file or directory...")` symptom from the review on the pre-fix code,
  5 runs in a row, and passing cleanly on the fixed code, 5 runs in a row.

## Verification

- Full suite green in isolation on every commit:
  `env -u CALIBRE_LIBRARY SCOURGIFY_HOME=$(mktemp -d) bash -c 'for t in tests/test_*.py; do uv run "$t" || exit 1; done'`
- `git diff --stat tests/test_wizard_flow.py tests/test_cli.py` — empty (unchanged, per constraint).
- `ops.py`/`_writer.py` untouched (per constraint) — neither appears in any of the three commits' diffs.
- Each finding's regression test was independently verified against a pre-fix scratch copy of
  `common.py` (via `git show <pre-fix-commit>:src/scourgify/common.py`, never `git stash`, per the
  stated constraint) to fail before the fix and pass after.
