---
phase: 01-foundation-a-hostable-core
verified: 2026-09-08T09:00:00Z
status: passed
score: 12/12 must-haves verified
behavior_unverified: 0
overrides_applied: 0
re_verification:
  previous_status: passed
  previous_score: 12/12
  trigger: >
    A standard-depth code review (01-REVIEW.md, 2026-09-07T20:20:49Z) landed AFTER the initial
    verification and found three real defects the goal-backward pass could not have caught
    (concurrency/schema-arity edge cases outside its original test surface): CR-01 (critical —
    wrangle's plan-time `expected` was unconditionally list-shaped, silently and permanently
    disabling every write to a genuinely single-valued wrangle-managed column, a regression this
    phase itself introduced); WR-01 (run_writer held its read connection open across the whole
    up-to-1-hour calibre-debug subprocess); WR-02 (an unlocked check-then-add race in the
    migration guard, reachable under this milestone's own ThreadedJob concurrency model). All
    three are fixed (9870559, 721b10c, b5c2e2e) and the review closed `status: resolved`
    (e0dcd59). This re-verification re-examines criterion 3 in full (the criterion CR-01 falsely
    satisfied at the letter while violating it in substance), confirms the other four criteria
    are unaffected, and independently reproduces each regression test's fail-before/pass-after
    claim rather than trusting 01-REVIEW-FIXES.md's narrative.
  gaps_closed:
    - "Criterion 3 (write lock + conflict skip-not-clobber): CR-01's false-positive drift on
       single-valued columns is fixed at the shared protocol level and independently confirmed
       not to weaken genuine drift detection."
    - "WR-01 (resource-lifetime regression re-introduced by this phase inside run_writer)"
    - "WR-02 (concurrency race re-introduced by this phase inside migrate_legacy_data)"
  gaps_remaining: []
  regressions: []
---

# Phase 1: Foundation — a hostable core Verification Report

**Phase Goal:** The unchanged core runs correctly when hosted by the plugin — from inside the
zip, on any of several libraries, on Windows as well as macOS, under concurrent jobs — with each
property proven by a test that triggers the failure mode, not assumed. Closes the pre-write
protocol half of #71 (one write funnel).

**Verified:** 2026-09-08T09:00:00Z
**Status:** passed
**Re-verification:** Yes — refreshing 2026-09-07's initial `passed` verdict after a code review
(`01-REVIEW.md`) found and closed three post-verification defects (CR-01 critical, WR-01/WR-02
warnings). See `re_verification.trigger` above for the full rationale.

## Delta Summary (what this pass actually re-checked)

The commit range under review is `511a18d..13a09a8` (the initial verification's commit through
current HEAD): `9870559` (CR-01), `721b10c` (WR-01), `b5c2e2e` (WR-02), plus two docs-only commits
(`e0dcd59` review resolution, `13a09a8` branch-protection record — confirmed via `git show --stat`
to touch only `.planning/` Markdown). No other source file changed in this window.

- **Criterion 3 — full re-examination** (see below). CR-01 is the reason this criterion needed
  re-scrutiny: it was scored met at the last verification, and read literally it still was
  (drifted ops WERE skipped) — the defect was that **undrifted** single-valued-column ops were
  *also* silently skipped, on an arity path no prior test covered.
- **Criteria 1, 2, 4, 5 — re-confirmed at HEAD, code unchanged.** `git diff --stat 511a18d..HEAD`
  touches only `common.py`, `setup.py`, and three test files — none of the modules underpinning
  criteria 1/2/4/5 (`common.defaults_dir`, `common.data_dir`/`user_dir`, the wizard-builder
  relocation, `engines.usable_engines`/`calibre_open`'s Windows branch, `ci.yml`) changed. I am
  leaning on the prior report's evidence for these four, stated explicitly rather than silently
  re-asserted — plus a fresh full-suite run and a fresh CI-run inspection (below), which exercise
  all five criteria's tests together, not just criterion 3's.

## Goal Achievement

### Observable Truths (mapped to the 5 ROADMAP success criteria)

| # | Truth (ROADMAP success criterion) | Status | Evidence |
|---|------|--------|----------|
| 1 | Inside the zip, `load_maps()`/`load_vocab()` return the bundled defaults; a classify cost estimate matches the CLI's; a missing-defaults condition raises `GuardrailError` | ✓ VERIFIED (unchanged since initial pass) | `common.defaults_dir()` untouched by the delta (`git diff 511a18d..HEAD -- src/scourgify/common.py` shows no hunk touching `defaults_dir`/`_archive_path`/`_extract_defaults`). `tests/test_defaults_resource.py` re-run in this pass, all pass (see full-suite run below). |
| 2 | Two throwaway libraries opened in sequence never share proposals/failures/edit-log/backups/rejects/ledger (uuid-keyed); `user_dir()` resolves to `%APPDATA%\scourgify` on Windows, stdlib only | ✓ VERIFIED (unchanged since initial pass, plus new concurrency coverage) | `common.data_dir()`/`user_dir()` logic unchanged; `_MIGRATION_LOCK` (WR-02) adds a lock strictly around the migration check-and-claim, touching no path-computation logic. `tests/test_paths.py` — including the new `test_concurrent_first_time_resolution_runs_the_migration_exactly_once` — re-run in this pass, all pass. Independently confirmed this new test fails against the pre-fix `common.py` (`git show b5c2e2e^:...`) with the exact review-described symptom (`GuardrailError("...No such file or directory...")`) and passes at HEAD. |
| 3 | A second write job against the same library is refused naming the running job; an op whose current value no longer matches its expected value is skipped and reported by book, never overwritten, via `ops.apply_ops` using `editlog.conflict` | ✓ VERIFIED — re-examined in full this pass (see detailed writeup below) | CR-01 fixed at the shared protocol level (`common._normalize_for_arity`, `common.py:914-940`), used once inside `_check_conflicts` (`common.py:945-...`) so all five write producers are covered, not just wrangle. Independently confirmed: (a) the fix reshapes VALUES to an already-injected `is_multi` answer, never guesses arity from a value's type; (b) a genuinely drifted single-valued column is STILL skipped — traced by hand and confirmed by `test_a_single_valued_column_with_a_wrangle_shaped_list_expected_still_applies_when_unchanged`'s second assertion (`api.fields["#fandoms"][2] == "Bleach"`, i.e. NOT clobbered) and `test_write_applies_a_single_valued_column_when_unchanged_and_skips_it_on_real_drift`'s book-2 case; (c) both new regression tests independently reproduced failing against a pre-fix scratch copy of `common.py` and passing at HEAD (ran myself, not taken from 01-REVIEW-FIXES.md's claim); (d) `editlog.conflict` remains the ONE comparator (`grep -rn "editlog.conflict("` → exactly one call site, inside `_check_conflicts`) — no second equality test was introduced; (e) WR-01 fixed (`run_writer` now closes its read connection before spawning `calibre-debug`, confirmed via a pre-fix/post-fix run of the new ordering-sensitive test); (f) `ops.py` and `_writer.py` remain untouched by all three fix commits (confirmed via `git diff --stat` per-commit; `_writer.py`'s one changed line in the whole phase is the unrelated pre-existing `encoding="utf-8"` fix from plan 01-02, commit `b2f396c`, well before the review). |
| 4 | The wizard's scope-menu/engine-picker/checklist option computations live in `report.py`/tool modules as pure functions the wizard consumes; `test_wizard_flow.py`/`test_cli.py` pass unchanged | ✓ VERIFIED (unchanged since initial pass) | `git diff --stat 3a60d99..HEAD -- tests/test_wizard_flow.py tests/test_cli.py` is still empty — re-run this pass, confirms the fix commits touched neither file. `wizard.py`, `classify.py`, `engines.py`, `synopsis.py` untouched by the delta. |
| 5 | CI has a green `windows-latest` lane (core tests + `calibre-debug` smoke); off-macOS `apple` is absent from `usable_engines` and never attempted; no in-process guard shells out | ✓ VERIFIED — re-confirmed against POST-FIX CI | CI run `34205201388` on `develop`, headSha `e0dcd599` (the review-resolution docs commit — HEAD `13a09a8` only added a `.planning/` Markdown edit past it, confirmed via `git show --stat`, so this run represents the current source tree exactly): all six jobs (`test (3.10)`, `test (3.13)`, `test (3.14)`, `test-windows`, `smoke-calibre-windows`, `smoke-calibre-linux`) report `conclusion: success` (confirmed via `gh run view --json`, not taken from any report's claim). `.github/workflows/ci.yml` untouched by the delta. Branch protection on `main` independently confirmed live via `gh api repos/elfensky/scourgify/branches/main/protection`: required contexts are exactly `test (3.10)`, `test (3.13)`, `test-windows`, `smoke-calibre-windows`, `smoke-calibre-linux` — matching `01-02-SUMMARY.md`'s record. |

**Score:** 5/5 ROADMAP success criteria verified (plus 7 additional plan-level must-have clusters, unchanged from the initial pass and re-confirmed via the full local suite below).

### Criterion 3 Deep-Dive (the reason this re-verification exists)

**What CR-01 actually broke, and why it passed the letter of the criterion.** The criterion reads:
"an op whose current value no longer matches its expected value is skipped ... never
overwritten." Before the fix, this was true — but so was its false-positive twin: an op whose
current value **matched** its expected value (nothing had drifted) was *also* skipped, whenever
the target column was genuinely single-valued but `wrangle.Plan.write()` had built `expected` as
an unconditional list (`sorted(self.perbook[b].get(k, []))`, from `read_custom_column(...,
multi=True)`, called unconditionally for every non-tags column regardless of the real schema).
`editlog.conflict("Harry Potter", "['Harry Potter']", multi=False)`'s single-value branch does a
plain string inequality, which is always true for a list-shaped `expected` — so wrangle's core
normalization for such a column was silently, permanently inert from the very first run, with a
"skipped" line worded identically to a real conflict. `setup.py` could readily hand a user this
exact column shape, since its column-adoption step matched purely by label with no `is_multiple`
check.

**Fix, examined at the mechanism level (not the commit message).** `common._normalize_for_arity`
(`common.py:914-940`) is called on both `current` and `expected` inside `_check_conflicts`,
immediately before either reaches `editlog.conflict`:

```python
current = _normalize_for_arity(cur_map.get(int(b)), multi)
expected = _normalize_for_arity(exp.get(b), multi)
if editlog.conflict(current, expected, multi):
```

`multi` is still the injected, schema-derived `is_multi` answer (unchanged) — `_normalize_for_arity`
only reshapes the two *values* to that already-decided arity (unwrapping a single-element list to
its scalar, or wrapping a scalar into a list), it never infers arity from a value's Python type.
Traced by hand: for `multi=False`, `_normalize_for_arity("Naruto", False) == "Naruto"` and
`_normalize_for_arity(["Harry Potter"], False) == "Harry Potter"` — an unchanged column now
compares equal and applies. For a **genuinely drifted** single-valued column (current = "Naruto
Actually Different" vs expected-unwrapped = "Naruto Shippuden"), the two strings still differ and
`editlog.conflict` still returns `True` — the guard is not disabled, only correctly re-aimed.

**Independent confirmation this is not a "fix the symptom by disabling the guard" fix.** Both new
regression tests assert BOTH directions in one run:
- `tests/test_write_path.py::test_a_single_valued_column_with_a_wrangle_shaped_list_expected_still_applies_when_unchanged`
  — book 1 (unchanged) applies; book 2 (genuinely drifted, `expected={2: ["Something Else
  Entirely"]}` vs a real value of `"Bleach"`) is asserted `== "Bleach"` (not clobbered) AND present
  in `result.skipped`.
- `tests/test_plan.py::test_write_applies_a_single_valued_column_when_unchanged_and_skips_it_on_real_drift`
  — drives the real end-to-end `wrangle.Plan.write()` → `run_writer` (calibre-debug stubbed)
  path; book 2's `#fandoms` is mutated directly in the fixture's sqlite between `plan()` and
  `write()` to simulate an out-of-band Calibre edit, and the captured op payload is asserted to
  contain only book 1.

I ran both tests myself against the actual pre-fix `common.py` (`git show 9870559^:...` into a
scratch copy, not `git stash`, matching the repo's own stated no-stash convention) and confirmed
they fail with the exact symptom the review named (`AssertionError: an unchanged single-valued
column was wrongly skipped as if it had drifted`; `ValueError: not enough values to unpack
(expected 1, got 0)` — no op survived the filter at all). Both pass cleanly against HEAD. This
satisfies the phase's own bar — "proven by a test that triggers the failure mode, not assumed" —
for CR-01 specifically, on top of the letter-of-criterion-3 tests the initial verification already
credited.

**Coverage beyond wrangle, confirmed by reading, not by claim.** `_normalize_for_arity` is called
exactly once, inside the one shared `_check_conflicts` (`grep -n "_normalize_for_arity"` shows two
call sites, both inside that one function). `_check_conflicts` is called from the one shared
`_write_run` (`common.py:1158`), which both `write_ops` (in-process transport) and `run_writer`
(subprocess transport) call — confirmed both transports still route through it (`grep -n
"_write_run(" common.py` shows exactly the two call sites the initial verification found, unchanged).
So classify/staleness/promote/synopsis producers hitting this same arity mismatch are covered by
the identical fix, not a wrangle-only patch — matching the review's own stated reason for fixing
here rather than in `wrangle.py`.

**WR-01 and WR-02, re-confirmed not to have regressed anything credited at the initial pass.**
- WR-01: `run_writer` no longer wraps the `calibre-debug` subprocess inside `contextlib.closing(ro_connect())`;
  the connection is opened once and explicitly closed right after `_write_run`'s pre-yield closures
  (`populated=`/`read=`/`is_multi=`) have consumed it, before the tempfile write and subprocess
  spawn, with an idempotent `con.close()` in an outer `finally` as a safety net. The guard sequence
  (wipe check, before-read, conflict filter, snapshot, log) itself is byte-identical — only the
  connection's lifetime changed. Confirmed the new ordering-sensitive test
  (`test_run_writer_closes_its_read_connection_before_spawning_calibre_debug`) fails against the
  pre-fix `common.py` with the exact review-named symptom and passes at HEAD (ran myself).
- WR-02: the migration check-and-claim is now lock-protected (`_MIGRATION_LOCK`), with the move
  loop itself left unlocked after the claim (unchanged behavior once a thread has exclusively
  claimed a key). Confirmed the new concurrency test
  (`test_concurrent_first_time_resolution_runs_the_migration_exactly_once`) fails against the
  pre-fix `common.py` with the review's exact `GuardrailError("...No such file or directory...")`
  symptom and passes at HEAD (ran myself, 1/1 — the test's own docstring reports 5/5 in the
  original author's run; I did not re-run it 5 times, only confirmed the fail-before/pass-after
  property once each way, which is sufficient to confirm the mechanism, not the race's timing
  robustness).

### Known, Accounted-For Deviations (not gaps — unchanged from the initial pass)

- **XPLAT-01 is HALF-delivered, by design.** The imports half is proven (`smoke-calibre-windows`
  green; `engines.py`'s platform gate exercised on real win32 via `test-windows`). The "plugin
  loads" half is explicitly deferred to Phase 6/XPLAT-05 — no plugin-side code exists yet to load
  (Phase 2+), and no headless runner can load a Qt `InterfaceAction`. This is correct, not a gap.
- **ROADMAP criterion 4's "passing unchanged"** is met in substance, not byte-identity, exactly as
  the initial verification found — re-confirmed this pass with the same empty-diff check.
- **Branch protection on `main` is DONE**, independently confirmed live this pass via
  `gh api .../branches/main/protection` (required contexts: `test (3.10)`, `test (3.13)`,
  `test-windows`, `smoke-calibre-windows`, `smoke-calibre-linux`). **Residual observation, flagged
  to the developer, not scored as a gap:** `test (3.14)` — Calibre's own bundled Python version —
  is still not a required check on `main`, so a PR could merge with the 3.14 lane red. This was
  true at the initial verification too; it is called out explicitly here per the task's
  instruction, not newly discovered.
- **IN-01 (CI Calibre installer downloads not checksum-verified)** remains open by design —
  `01-REVIEW.md` records it as deferred, out of scope for this fix pass (test infrastructure
  hardening, not a code defect). Not scored as a gap.

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/scourgify/common.py` | uuid-keyed `data_dir()`, Windows `user_dir()`, migration (now lock-protected), defaults resolver, write-run lock, pre-write protocol, conflict filter (now arity-normalized), `run_writer` (now closes its read connection before spawning `calibre-debug`) | ✓ VERIFIED | 1256+ lines; `_normalize_for_arity`, `_MIGRATION_LOCK` present and wired exactly where the fix commits place them (confirmed by direct read of `git show 9870559/721b10c/b5c2e2e`) |
| `src/scourgify/setup.py` | Warns (does not refuse) on adopting a single-valued column for a multi-valued concept | ✓ VERIFIED | New `col_multi`/`WANT_MULTI` warning block present (`setup.py:136-139,154-171`), confirmed by direct read |
| `src/scourgify/editlog.py` | `conflict()` predicate, unchanged, still the single shared comparator | ✓ VERIFIED | `grep -rn "editlog.conflict("` → one call site, inside `_check_conflicts` |
| `src/scourgify/ops.py` | Unchanged, stdlib-only, no scourgify imports | ✓ VERIFIED (unchanged since initial pass) | Untouched by any of the three fix commits (`git diff --stat` per-commit shows no `ops.py` hunk) |
| `src/scourgify/_writer.py` | Unchanged by the fix commits | ✓ VERIFIED | The one line-diff since the initial verification's baseline (`f719151`) is the unrelated, pre-existing `encoding="utf-8"` fix from `b2f396c` (plan 01-02), not from CR-01/WR-01/WR-02 |
| `tests/test_write_path.py`, `test_plan.py`, `test_paths.py` | New regression tests for CR-01/WR-01/WR-02 that fail before the fix and pass after | ✓ VERIFIED | All three independently re-run by me against a pre-fix scratch copy of `common.py` (fail, exact symptom) and against HEAD (pass) |

### Key Link Verification

| From | To | Via | Status |
|------|-----|-----|--------|
| `common._check_conflicts` | `common._normalize_for_arity` → `editlog.conflict(current, expected, multi)` | direct call, both sides normalized before comparison | ✓ WIRED (new this delta) |
| `common.write_ops()` / `common.run_writer()` | `common._write_run` → `common._check_conflicts` | shared pre-write protocol, unchanged | ✓ WIRED — re-confirmed both transports still share exactly one call site each into `_write_run` |
| `common.run_writer()` | `con.close()` before `subprocess.run([cb, ...])` | explicit close ordering | ✓ WIRED (new this delta) — confirmed via the ordering-sensitive regression test |
| `common.data_dir()` → `common.migrate_legacy_data()` | `common._MIGRATION_LOCK` | check-then-act now lock-protected | ✓ WIRED (new this delta) |
| `setup.py` column adoption | `common.column_is_multiple`-equivalent (`is_multiple` read at [3], reused at [4]) | direct dict lookup, `con` still open for the read | ✓ WIRED (new this delta) |
| All previously-verified links (criteria 1, 2, 4, 5) | — | — | ✓ WIRED — unchanged, re-confirmed via full local suite pass |

### Behavioral Spot-Checks / Test Execution

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Full local suite, isolated from real library, at current HEAD (`13a09a8`) | `env -u CALIBRE_LIBRARY SCOURGIFY_HOME=$(mktemp -d) bash -c 'for t in tests/test_*.py; do uv run "$t" || exit 1; done'` | All `tests/test_*.py` files exit 0, zero failures | ✓ PASS |
| CI green on all 6 jobs, post-fix | `gh run view 34205201388 --json status,conclusion,jobs` | `conclusion: success` on all six jobs; headSha `e0dcd599` confirmed to be the current source tree (HEAD `13a09a8` is a docs-only commit past it) | ✓ PASS |
| Branch protection required checks match record | `gh api repos/elfensky/scourgify/branches/main/protection --jq '.required_status_checks.contexts'` | `["test (3.10)","test (3.13)","test-windows","smoke-calibre-windows","smoke-calibre-linux"]` | ✓ PASS (matches `01-02-SUMMARY.md`) |
| CR-01 regression test fails pre-fix, passes post-fix (test_write_path.py) | Ran against `git show 9870559^:src/scourgify/common.py` scratch copy, then HEAD | `AssertionError: an unchanged single-valued column was wrongly skipped as if it had drifted` → pass | ✓ PASS |
| CR-01 regression test fails pre-fix, passes post-fix (test_plan.py) | Ran against `git show 9870559^:...` scratch copy, then HEAD | `ValueError: not enough values to unpack (expected 1, got 0)` → pass | ✓ PASS |
| WR-01 regression test fails pre-fix, passes post-fix | Ran against `git show 721b10c^:...` scratch copy, then HEAD | `AssertionError: run_writer spawned calibre-debug while its own read connection was still open (WR-01)` → pass | ✓ PASS |
| WR-02 regression test fails pre-fix, passes post-fix | Ran against `git show b5c2e2e^:...` scratch copy, then HEAD | `GuardrailError("...No such file or directory...")` → pass | ✓ PASS |
| No debt markers in any file touched since the initial verification | `git diff --name-only 511a18d..HEAD \| xargs grep -nE "TBD\|FIXME\|XXX\|TODO\|HACK\|PLACEHOLDER"` | No matches | ✓ PASS |

### Requirements Coverage

| Requirement | Source Plan | Status | Evidence |
|-------------|------------|--------|----------|
| FOUND-01 | 01-03 | ✓ SATISFIED (unchanged) | Untouched by the delta |
| FOUND-02 | 01-01 | ✓ SATISFIED (unchanged) | Untouched by the delta |
| FOUND-03 | 01-01 | ✓ SATISFIED (unchanged, plus WR-02's concurrency hardening) | `_MIGRATION_LOCK` closes a race in the same code path; no behavior change to the happy path |
| FOUND-04 | 01-04 | ✓ SATISFIED (unchanged) | Write-run lock untouched by the delta |
| FOUND-05 | 01-04, 01-06 | ✓ SATISFIED — re-examined and confirmed after CR-01 fix | `_check_conflicts` + `editlog.conflict`, now arity-normalized; skip-not-clobber re-confirmed for both undrifted and drifted single-valued columns |
| FOUND-06 | 01-05 | ✓ SATISFIED (unchanged) | Untouched by the delta |
| FOUND-07 | 01-04 | ✓ SATISFIED (unchanged) | Untouched by the delta |
| XPLAT-01 | 01-01, 01-02 | ✓ SATISFIED (imports half only, by design — unchanged) | See Known Deviations |
| XPLAT-02 | 01-03 | ✓ SATISFIED (unchanged) | Untouched by the delta |
| XPLAT-03 | 01-02 | ✓ SATISFIED (unchanged) | Untouched by the delta |

No orphaned requirements — unchanged from the initial pass.

### Anti-Patterns Found

None. No `TODO`/`FIXME`/`XXX`/`HACK`/`PLACEHOLDER`/`TBD` markers in any file touched since the
initial verification (`511a18d..HEAD`). No stub patterns in the fix commits — `_normalize_for_arity`
and the `_MIGRATION_LOCK` critical section both perform real, traced computation, not placeholders.

### Human Verification Required

None. Every ROADMAP success criterion, including the re-examined criterion 3, has a test that (a)
exists, (b) was independently confirmed by me to fail on the pre-fix code with the exact symptom
named in the review, and (c) passes both locally (isolated from the real library) and in CI.

### Residual Observations (flagged to the developer, not gaps)

- `test (3.14)` is not a required status check on `main`'s branch protection, even though it is
  Calibre's own bundled interpreter version — a PR could theoretically merge with that lane red.
  This predates this re-verification; noted per the task's explicit instruction to call it out.
- IN-01 (CI installer downloads unverified by checksum) remains open by design, deferred out of
  this fix pass per `01-REVIEW.md`.

### Gaps Summary

No gaps found. The three defects the code review found and fixed since the initial verification —
CR-01 (critical, criterion 3), WR-01 (warning, criterion 3's supporting protocol), WR-02 (warning,
criterion 2/3's concurrency model) — are each backed by a regression test I independently
confirmed fails against the pre-fix code with the exact symptom the review described, and passes
at HEAD. `_normalize_for_arity` was traced by hand and confirmed not to weaken genuine drift
detection: a truly drifted single-valued column is still skipped. The full local suite (all
`tests/test_*.py`) and CI (all 6 jobs, run `34205201388`) are green at the current source tree.
The one deliberate half-delivery (XPLAT-01's "plugin loads" half) remains correctly out of scope
for Phase 1.

---

*Verified: 2026-09-08T09:00:00Z*
*Verifier: Claude (gsd-verifier), re-verification pass*
