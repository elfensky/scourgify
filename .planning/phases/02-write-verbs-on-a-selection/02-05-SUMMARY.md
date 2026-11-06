---
phase: 02-write-verbs-on-a-selection
plan: 05
subsystem: plugin
tags: [calibre-plugin, write-path, wrangle, decide-seam, review-checklist, job-runner]

# Dependency graph
requires:
  - phase: 02-write-verbs-on-a-selection
    provides: "02-01's Qt-free job layer (plugin/jobs.py), the ONE verb-parameterised picker and result dialog; 02-02's write= transport seam on wrangle.Plan.write; 02-04's (label, payload) item shape and wrangle.Plan.step's decide= seam, plus the picker's Review 1-by-1 control and checklist_decide/backfill_decide adapters"
provides:
  - "The two generic decide= fabricators (_record_decide/_replay_decide, D-02) — sequence-aware
    so they cover both a single-call review (staleness.step) and a per-book, multi-call review
    (wrangle.Plan.step via _step_walk)"
  - "job_plan_wrangle / job_execute_wrangle — the live 'Normalize fields' verb: PLAN previews the
    per-book edits + SAFETY line and guards before any dialog opens; EXECUTE recomputes, replays
    the reviewer's ticks through the SAME _step_walk the terminal drives, and writes in-process"
  - "staleness's PLAN/EXECUTE jobs now drive staleness.step itself instead of re-deriving its item
    shape — both deterministic verbs share one review mechanism"
  - "plugin/action.py's _start_execute gains an optional dlg parameter (threaded through
    _plan_done, which stays the ONE completion path for every verb) and the new _wrangle_ticks
    helper, regrouping the picker's flat review table into per-book ticks"
affects: [02-06, 02-07, 02-08]

# Actuals (#2632)
actuals:
  tokens: 8143
  tasks: 2
  commits: 2

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Record-and-replay decide= (D-02): a PLAN job's callback records every _step_walk-style
      call's (title, subtitle, items) in order and answers 'skip' (the object it walks is
      discarded at the end of the job anyway); the matching EXECUTE job's callback replays a
      reviewer's recorded (accepted_idx, rejected_idx, action) ticks, one per invocation, falling
      back to accept-everything once exhausted — the empty-ticks fallback IS the one-click Run
      path's contract, not a degenerate case."
    - "A tool function whose decide= is called ONCE PER BOOK (wrangle.Plan.step) cannot share the
      Qt layer's single-call adapter (picker.checklist_decide) — that one returns the SAME global
      table-row indices on every invocation, which is only correct when decide() is called
      exactly once. Per-book review sites need their own regrouping (plugin/action.py's
      _wrangle_ticks), converting a flat all-books tick table into one local-indexed triple per
      book run."
    - "job_plan_wrangle overrides plan_result's own 'empty' computation (`not items`) with
      `p.n_books == 0`: wrangle's per-book review items cover only the UNIQUE (non-mass) edits,
      so a mass-only change-set (real work, zero review items) must not read as D-04 empty."

key-files:
  created: []
  modified:
    - plugin/jobs.py
    - plugin/action.py
    - tests/test_plugin_jobs.py

key-decisions:
  - "job_execute_staleness's new ticks= parameter is a trailing keyword (after now_uuid, default
    ()), not inserted between carry and api — plugin/action.py's existing generic _EXECUTE_JOBS
    dispatch builds a fixed 6-tuple positional args for every verb in that table, and inserting
    ticks earlier would have silently misrouted the now_uuid callable into the ticks position for
    every existing staleness dispatch site. wrangle's own EXECUTE job (dispatched through its own
    branch, not the generic table) puts ticks between carry and api instead, matching the plan's
    literal target shape."
  - "_plan_done gained a small, backward-compatible addition (threading the just-shown Picker
    object into on_run's closure) rather than a new wrangle-specific completion handler — every
    verb still funnels through the SAME _plan_done/_start_execute pair; only wrangle's own branch
    inside _start_execute reads the extra dlg argument. staleness's behaviour through this path is
    byte-identical."
  - "wrangle EXECUTE deliberately recomputes the plan rather than carrying an ops list forward
    (CONTEXT.md's Claude's Discretion default) — see job_execute_wrangle's docstring for the full
    resolution: _step_walk is what writes reject rows and recomputes a book's net change on a
    partial untick, and Plan.write is what builds each op's expected from perbook; duplicating
    either in the plugin would put a second place in the codebase deciding what a reject is."

patterns-established:
  - "Pattern: record-and-replay decide= (D-02) — see tech-stack.patterns above."
  - "Pattern: per-book tick regrouping for a multi-call review site — see tech-stack.patterns
    above; the next verb needing a per-item (not per-book) multi-call review can follow the same
    shape."

requirements-completed: []  # WRITE-01/02/07 are shared across multiple plans in this phase
                            # (requirements.ready-ids gate) — marked complete by whichever
                            # sibling plan finishes last, per the shared-ID gate (#2388).

coverage:
  - id: D1
    description: "'Normalize fields' runs end-to-end from the menu: PLAN job previews the
      per-book edits and the SAFETY line, guards before any dialog opens, and the write lands
      through common.write_ops with the snapshot and the wipe guard applied — proven at the job
      layer against a fixture library and a controlled overrides/junk.txt rule."
    requirement: "WRITE-01"
    verification:
      - kind: unit
        ref: "tests/test_plugin_jobs.py#test_job_plan_wrangle_produces_per_book_reviewable_items_in_step_walks_own_order"
        status: pass
      - kind: unit
        ref: "tests/test_plugin_jobs.py#test_job_execute_wrangle_writes_through_write_ops_and_the_fake_apis_state_changes"
        status: pass
    human_judgment: true
    rationale: "The Qt layer itself (the live menu slot, the picker's SAFETY line rendering, the
      result dialog, the row refresh) has no Calibre/GUI in CI and was NOT run against a real
      Calibre — recorded as WINDOWS.md entry 4 (unrun-verify). Every layer below the Qt widgets is
      unit-tested and green."
  - id: D2
    description: "A data-loss-shaped change-set (a fandom alias folding to empty) is refused with
      the guard's own sentence and writes nothing, before any dialog opens (PLAN) and again before
      any write (EXECUTE) — the refusal renders as a refused result, never a job failure."
    requirement: "WRITE-02"
    verification:
      - kind: unit
        ref: "tests/test_plugin_jobs.py#test_job_plan_wrangle_a_fandom_emptying_change_set_is_refused_with_no_dialog"
        status: pass
      - kind: unit
        ref: "tests/test_plugin_jobs.py#test_job_execute_wrangle_refuses_cleanly_on_a_guard_trip"
        status: pass
    human_judgment: false
  - id: D3
    description: "A book deferred (whole-book skip) in the review keeps no reject row and is left
      untouched; an explicit untick-then-apply of a single edit does write a declared reject row
      to data/rejects.csv, exactly as the terminal's --step review does — the same _step_walk both
      front doors drive."
    requirement: "WRITE-07"
    verification:
      - kind: unit
        ref: "tests/test_plugin_jobs.py#test_job_execute_wrangle_a_skipped_book_is_deferred_and_writes_no_reject_row"
        status: pass
      - kind: unit
        ref: "tests/test_plugin_jobs.py#test_job_execute_wrangle_an_explicit_untick_writes_a_declared_reject_row"
        status: pass
    human_judgment: false
  - id: D4
    description: "Running the wrangle verb twice on the same selection: the second run's PLAN job
      finds no remaining change and returns the D-04 empty result (no dialog opens)."
    verification:
      - kind: unit
        ref: "tests/test_plugin_jobs.py#test_a_second_plan_after_a_successful_wrangle_write_returns_the_d04_empty_result"
        status: pass
    human_judgment: false
  - id: D5
    description: "staleness's PLAN/EXECUTE jobs now drive staleness.step itself via the two
      generic decide= helpers instead of re-deriving the item/payload shape a second time — both
      deterministic verbs share one review mechanism, and every pre-existing staleness job test
      (written before this plan) still passes unchanged."
    verification:
      - kind: unit
        ref: "tests/test_plugin_jobs.py#test_job_plan_staleness_returns_the_expected_items_and_consequence"
        status: pass
      - kind: unit
        ref: "tests/test_plugin_jobs.py#test_a_second_identical_execute_reports_every_row_skipped_since_the_plan"
        status: pass
    human_judgment: false

duration: 55min
completed: 2026-09-09
status: complete
---

# Phase 2 Plan 5: The Wrangle Write Verb Summary

**`Normalize fields` runs end-to-end through the Qt-free job layer — `job_plan_wrangle` /
`job_execute_wrangle`, the record-and-replay `decide=` helpers both deterministic verbs now
share, and per-book tick regrouping in the picker's Review 1-by-1 control — with wrangle's own
data-loss guards refusing cleanly instead of opening a dialog.**

## Performance

- **Duration:** ~55 min
- **Started:** 2026-09-09T09:55:00Z (approx.)
- **Completed:** 2026-09-09T10:49:00Z
- **Tasks:** 2
- **Files modified:** 3 (2 commits)

## Accomplishments

- `_record_decide()` / `_replay_decide(ticks)` (D-02): the two generic `decide=` fabricators a
  PLAN job and its matching EXECUTE job share — sequence-aware, covering both a single-call
  review (`staleness.step`) and a per-book, multi-call review (`wrangle.Plan.step` via
  `_step_walk`) with the same two functions.
- `staleness`'s PLAN/EXECUTE jobs now drive `staleness.step` itself (via the recorder/replayer)
  instead of re-deriving its `status_line`/payload shape a second time — both deterministic
  verbs share one review mechanism. `job_execute_staleness` gained a trailing, backward-compatible
  `ticks=()` keyword (every existing call site is unchanged).
- `job_plan_wrangle`: `wrangle.plan(cfg, m).restrict(ids)`, its SAFETY numbers and `n_books` read
  BEFORE any walk, guarded (`p.guard(force=False)`) before any dialog opens, then the per-book
  review items harvested via the recorder — flattened in `_step_walk`'s own newest-id-first call
  order. A data-loss-shaped change-set (a fandom alias folding to empty) surfaces as a refused
  result carrying the guard's own sentence; an already-normalized library returns the D-04 empty
  result. `plan_result`'s own `not items` empty test is deliberately overridden with `p.n_books ==
  0`, since a mass-only change-set has real work to write with zero per-book review items.
- `job_execute_wrangle`: recomputes the same plan over `carry['ids']`, guards again, replays
  `ticks` through the SAME `_step_walk` the terminal's `apply --step` drives, then writes via the
  in-process transport. Deliberately recomputes rather than carrying an ops list forward — the
  discretion resolution is recorded in the function's own docstring.
- `plugin/action.py`: `Normalize fields` is now a live menu verb, greyed on an empty selection or
  a running write exactly like `Re-derive status`. `_start_execute` gained an optional `dlg`
  parameter (threaded through `_plan_done`, which stays the ONE completion path for every verb);
  the new `_wrangle_ticks` helper regroups the picker's flat, all-books review table into per-book
  `(accepted_idx, rejected_idx, action)` triples — `wrangle.Plan.step` calls its `decide=` once
  PER BOOK, unlike every other verb's single-call review that `picker.checklist_decide` already
  covers, so wrangle cannot reuse that adapter.
- Tests: a skipped book is deferred (untouched, no reject row); an explicit untick writes a
  declared reject row with the payload's `kind`/`class`; a second PLAN job after a successful
  write returns the D-04 empty result; a guard-refused EXECUTE returns `refused=True` with the
  fake api untouched; `_replay_decide([])` accepts everything for any item list.

## Task Commits

1. **Task 1: The record-and-replay decide helpers, and the wrangle PLAN job** - `1564e91` (feat)
2. **Task 2: The wrangle EXECUTE job and the live Normalize fields slot** - `93a34a1` (feat)

_No plan-metadata commit yet — this SUMMARY and the STATE/ROADMAP/REQUIREMENTS updates are
committed together immediately after this file is written (per the atomic close-out invariant)._

## Files Created/Modified

- `plugin/jobs.py` - `_record_decide`, `_replay_decide`, `job_plan_wrangle`, `job_execute_wrangle`;
  `job_plan_staleness`/`job_execute_staleness` rewired onto the same two helpers.
- `plugin/action.py` - live `Normalize fields` slot, `wrangle()` dispatcher, `_plan_done`'s `dlg`
  passthrough, `_start_execute`'s wrangle branch, the new `_wrangle_ticks` helper.
- `tests/test_plugin_jobs.py` - the decide-helper unit tests, the wrangle PLAN/EXECUTE round trip
  against `fixture_db` + a controlled `overrides/junk.txt`/`overrides/fandoms.csv`, the SAFETY
  refusal, the deferred-book/declared-reject distinction, and the second-PLAN-is-empty case.

## Decisions Made

See `key-decisions` in the frontmatter — `job_execute_staleness`'s `ticks=` position (trailing
keyword, not inserted before `now_uuid`, to keep the generic `_EXECUTE_JOBS` dispatch tuple
intact), `_plan_done`'s `dlg` passthrough (kept as the ONE completion path rather than adding a
wrangle-specific handler), and wrangle EXECUTE's deliberate recompute.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `job_execute_staleness`'s new `ticks=` parameter placed after `now_uuid`, not
between `carry` and `api` as the plan's interfaces block literally showed for wrangle**
- **Found during:** Task 1, wiring `_replay_decide` into `job_execute_staleness`
- **Issue:** `plugin/action.py`'s existing generic `_EXECUTE_JOBS` dispatch table builds a fixed
  positional 6-tuple `(lib, uuid, ids, carry, api, now_uuid)` for every verb registered in it
  (staleness is the only one so far). Inserting `ticks` between `carry` and `api` — the position
  the plan's interfaces block used for wrangle's own, differently-dispatched EXECUTE job — would
  have silently misrouted `self.current_uuid` (the `now_uuid` callable) into the `ticks` position
  on every existing staleness dispatch, corrupting the identity-check seam without any test
  catching it (the existing tests call the job function directly with 5 positional args, not
  through the dispatch tuple).
- **Fix:** kept `now_uuid` in its original 6th positional slot and added `ticks=()` immediately
  after it, as a keyword the generic dispatch never has to supply. `abort`/`log`/`notifications`
  are always injected by Calibre's `ThreadedJob` as keywords, never positionally, so their
  position was never at risk.
- **Files modified:** `plugin/jobs.py`
- **Verification:** every pre-existing `job_execute_staleness` test (5-positional-arg calls, no
  `ticks`) passes unchanged; full suite green.
- **Committed in:** `1564e91` (Task 1 commit)

**2. [Rule 1 - Bug] `plan_result`'s own `'empty': not items` computation is wrong for wrangle**
- **Found during:** Task 1, designing `job_plan_wrangle`'s return value
- **Issue:** `plan_result` (shared by every verb, unchanged in this plan) computes `empty` as
  `not items`. Staleness/classify's `items` are exactly their full change-set, so that's correct
  for them. Wrangle's `items` are only the per-book UNIQUE edits (the ones `_step_walk` reviews
  1-by-1) — mass folds (the same edit on 3+ books) are applied without a review item. A wrangle
  plan whose ENTIRE change-set is mass-only would have real work to write (`p.n_books > 0`) but
  zero review items, and the shared `plan_result` helper would have marked that D-04 empty,
  silently discarding real, guard-checked work with no dialog and no way to run it.
  `plan_result`'s signature/behaviour was not touched (used by staleness/classify unchanged);
  `job_plan_wrangle` overrides the returned dict's `empty` key with the correct `p.n_books == 0`
  condition, computed before `p.step()`'s recorder-driven walk mutates `p.changes`.
- **Files modified:** `plugin/jobs.py`
- **Verification:** `test_job_plan_wrangle_returns_the_d04_empty_result_for_an_already_normalized_library`
  and `test_job_plan_wrangle_produces_per_book_reviewable_items_in_step_walks_own_order` both pass;
  the SAFETY/summary numbers are captured before `p.step()` runs, per the task's own stated order.
- **Committed in:** `1564e91` (Task 1 commit)

---

**Total deviations:** 2 auto-fixed (both Rule 1 — bugs a literal reading of the plan's interfaces
block would have introduced). **Impact on plan:** Both fixes were necessary for correctness
(neither is scope creep): the first prevents a silent regression in every existing staleness
dispatch site, the second prevents a mass-only wrangle change-set from being unreachable through
the plugin.

## Issues Encountered

None beyond the two deviations documented above.

## Outstanding Manual Verification (Process Note)

Task 2's `<verify>` block carries a `<human-check>` (a real-Calibre GUI walkthrough: select books
with messy tags, click `Normalize fields`, confirm the picker shows the per-book edits and the
SAFETY counts, apply, confirm the result dialog and the refreshed rows, then run the same verb
again and confirm the one-line "nothing to change" notice). No Calibre installation is available
in this environment, so this step could not be performed; every automated `<verify>` (task-level
and plan-level) is green, and this plan's D1 coverage entry is marked `human_judgment: true` for
exactly this reason.

**Recorded in `.planning/WINDOWS.md`** (`unrun-verify`, phase 02, entry id 4) so it stays visible
at ship time — the fourth open entry in this phase alongside `Re-derive status` (02-01), the
classify scope/engine pickers (02-03), and the Review 1-by-1 control (02-04). All four should be
run together in a real Calibre before this phase ships.

Also note: `_wrangle_ticks` (`plugin/action.py`) — the logic that regroups the picker's flat
review-table ticks into per-book replay triples for a real Review-1-by-1 session — is Qt-only and
therefore untested in this CI. Its correctness on a real, multi-book Review 1-by-1 session is part
of the same outstanding manual verification above; the underlying `job_execute_wrangle` replay
mechanism it feeds is fully unit-tested by calling `ticks` directly.

## Known Stubs

None — every code path is wired to real data; the plugin verb operates on the real
`wrangle.plan()` compute, never a hardcoded/mock value.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- The record-and-replay `decide=` pattern (D-02) is now proven for both a single-call review
  (staleness) and a per-book multi-call review (wrangle) — plans 02-06/02-07/02-08 (classify's
  step review, promote, synopsis) can reuse whichever shape matches their own tool function's
  `decide=` call cadence.
- `requirements-completed` is intentionally empty: WRITE-01/02/07 are declared by multiple plans
  in this phase and stay `Pending` in REQUIREMENTS.md until the last sibling plan's SUMMARY lands
  (shared-ID gate, #2388).
- **Blocker (carried forward):** the real-Calibre human-checks for `Re-derive status` (02-01),
  classify's scope/engine pickers (02-03), the Review 1-by-1 control (02-04), and now `Normalize
  fields` (02-05) should all be run together before this phase ships — four open `unrun-verify`
  entries now sit in `.planning/WINDOWS.md`.

## Self-Check: PASSED

- `plugin/jobs.py`, `plugin/action.py`, `tests/test_plugin_jobs.py` — all exist on disk with the
  expected changes (`grep -n "^def _record_decide\|^def _replay_decide\|^def job_plan_wrangle\|^def job_execute_wrangle"`
  confirms all four defined; `grep -n "jobs\.job_plan_wrangle\|jobs\.job_execute_wrangle"` confirms
  both are dispatched from `plugin/action.py`).
- Commits `1564e91`, `93a34a1` — both found in `git log --oneline`.
- All task-level `<acceptance_criteria>` re-verified: the SAFETY refusal returns `refused=True`
  with the guard's own sentence and `items` empty; the PLAN job's `items` order matches
  `_step_walk`'s own newest-id-first call order; `_replay_decide([])` accepts everything for any
  item list; a skipped book produces no rejects.csv row and an unticked individual edit does; a
  second PLAN job after a successful write returns the empty result; `tests/test_plugin_source.py`
  still proves exactly one `ThreadedJob` call site and zero core imports in `plugin/action.py`.
- Plan-level `<verification>`: `tests/test_plugin_jobs.py`, `tests/test_plan.py`,
  `tests/test_plugin_source.py`, `tests/test_overrides.py` all green individually; full suite
  (`for t in tests/test_*.py; do uv run "$t" || exit 1; done`) green, no failures.
- Manual `<human-check>` NOT run (no Calibre in this environment) — see "Outstanding Manual
  Verification" above; recorded in `.planning/WINDOWS.md` entry 4, not silently marked done.

---
*Phase: 02-write-verbs-on-a-selection*
*Completed: 2026-09-09*
