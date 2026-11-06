---
phase: 02-write-verbs-on-a-selection
plan: 08
subsystem: plugin
tags: [calibre-plugin, classify, engines, failure-taxonomy, retry, result-dialog, selftest]

# Dependency graph
requires:
  - phase: 02-write-verbs-on-a-selection
    provides: "02-01's Qt-free job layer (plugin/jobs.py) and diff-after result dialog (D-09/D-11);
      02-03's engine seam (jobs._engine_ask, engines.engine_options' three-case price fragment,
      classify.est_cost); 02-06's RefusalDialog/show_refusal precedent for a picker.py addition
      outside a plan's declared files_modified; every prior plan's own EXECUTE job shape
      (execute_result/engine_failures)"
provides:
  - "jobs.failure_groups(rows, env=None) — the ONE derivation of which recovery a failure earns,
    read by both the result dialog's retry buttons and the persistent menu slot: a refusal offers
    every usable engine except any known to have refused; a retryable class (quota/timeout/parse/
    error) offers only the same engine; auth/permission offer nothing"
  - "jobs.job_retry_classify / jobs.job_retry_targets — the one classify retry entry point
    (job_execute_classify restricted to an explicit id list on a named engine) and the read job
    the menu's 'Retry on another engine' slot needs"
  - "job_execute_classify's own diff-after now names every failed book individually (state
    '<class> on <engine>') instead of silently dropping it from the row table, and carries
    book/title/reason triples plus the computed groups for the dialog to render"
  - "plugin/result_dialog.py: one section per failure class present, one priced retry button per
    target the taxonomy supports, dispatching straight to on_retry(engine_id, book_ids) with no
    engine picker in between"
  - "plugin/picker.py: RetryChooser/show_retry_chooser — the menu half of the same retry contract"
  - "The live 'Retry on another engine' menu slot, backed by a cache the action refreshes on every
    menu open and after every completed write"
  - "plugin/selftest.py: one driven PLAN -> picker-data -> EXECUTE -> result-data round trip per
    write verb, a capture-and-defer shim over every picker/result dialog, and a genuine whole-run
    worst GUI-thread heartbeat gap"
  - "tests/test_plugin_source.py: the phase sweep proving every write verb reaches a job"
affects: []

# Actuals (#2632)
actuals:
  tokens: 9782
  tasks: 3
  commits: 3

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "One shared failure-class-to-recovery-verb derivation (jobs.failure_groups), read by both a
      result dialog's retry buttons and a persistent menu slot's chooser, so the two surfaces
      cannot disagree about what is retryable — the same 'one derivation, two front doors'
      discipline this phase already applied to decide= (D-02) and engine-row enrichment."
    - "A JOB computes plain retry-target data (labels included, reusing engine_options' own price
      fragment); the Qt dialog only ever renders it — extending D-01's 'no core call from the
      widget' contract to the retry controls, not just the picker/engine-picker."
    - "A Qt capture shim that must CONTINUE a multi-dialog flow (Run/an engine button/a scope
      choice) defers via QTimer.singleShot(0, ...) rather than calling the callback synchronously
      — several real call sites assign their own `dlg = show_picker(...)` return value INTO the
      very closure being invoked, so a synchronous call reads that name before it is bound."

key-files:
  created: []
  modified:
    - plugin/jobs.py
    - plugin/result_dialog.py
    - plugin/picker.py
    - plugin/action.py
    - plugin/selftest.py
    - tests/test_plugin_jobs.py
    - tests/test_plugin_source.py

key-decisions:
  - "Only classify computes and renders retry groups/targets — synopsis's engine failures stay on
    their pre-existing per-book row text ('failed: reason'), not grouped, and get no retry button.
    The plan's own task 1(b) names exactly one retry job (job_retry_classify); wiring a synopsis
    retry would need a second job this plan does not build, and rendering a priced-but-disabled
    button for a verb with no handler would be worse than not rendering one at all."
  - "job_execute_classify's engine_failures grew from (book, reason) 2-tuples to (book, title,
    reason) 3-tuples — matching the interfaces block's own documented shape (`execute_result`'s
    docstring, carried since plan 02-01) that no prior plan's classify/synopsis code actually
    produced. Fixed on classify (this plan's own scope); synopsis's matching 2-tuple shape is left
    as-is, out of this plan's declared files_modified."
  - "failure_groups' refusal-target exclusion and retryable-target selection both read an OPTIONAL
    'engine' key on a failure row rather than a required parameter: job_execute_classify (which
    ran exactly one engine this batch) can stamp it, but the persistent classify_failures.csv
    job_retry_targets reads has no engine column and never will inside this plan's file scope —
    without a known engine, refusal offers every usable engine (never risk excluding the wrong
    one) and a retryable class offers nothing (never guess which engine to name)."
  - "plugin/picker.py gained RetryChooser/show_retry_chooser despite not being in this plan's
    declared files_modified — the same discretion plan 02-06 exercised for RefusalDialog: a Qt
    widget bound by D-01's no-core-import contract has nowhere else to live."
  - "plugin/selftest.py's whole-run 'longest GUI-thread heartbeat gap' claim (task 3's own
    read_first) was not actually true of the pre-existing code — self.beats is reset per-verb by
    _run_verb/_menu, so no existing call computed a genuine cross-step maximum. Added an
    incremental worst_gap_ms tracked independently of any reset, reported in finish()."

patterns-established:
  - "Pattern: one shared failure-class -> recovery-verb derivation, job-computed / dialog-rendered
    — see tech-stack.patterns above."
  - "Pattern: QTimer.singleShot(0, ...) to defer a capture shim's auto-continue past the caller's
    own dlg-in-closure assignment — see tech-stack.patterns above; any future selftest capture of
    a `dlg = show_X(...); callback` call site needs the same deferral."

requirements-completed: [WRITE-07, WRITE-08]

coverage:
  - id: D1
    description: "After a classify write, the diff-after never collapses failures into one number:
      every failed book gets its own row ('<class> on <engine>') and a failure-class section names
      its book count, with a priced retry button per target the taxonomy supports (refusal ->
      other usable engines; quota/timeout/parse/error -> the same engine; auth/permission -> no
      control at all)."
    requirement: WRITE-07
    verification:
      - kind: unit
        ref: "tests/test_plugin_jobs.py#test_failure_groups_auth_and_permission_classes_yield_no_targets"
        status: pass
      - kind: unit
        ref: "tests/test_plugin_jobs.py#test_failure_groups_refusal_targets_exclude_the_refusing_engine"
        status: pass
      - kind: unit
        ref: "tests/test_plugin_jobs.py#test_failure_groups_retryable_class_targets_only_the_same_engine"
        status: pass
      - kind: unit
        ref: "tests/test_plugin_jobs.py#test_job_retry_classify_second_attempt_recovers_and_clears_the_failure_row"
        status: pass
    human_judgment: true
    rationale: "The Qt layer itself (the grouped result dialog's sections and priced buttons, the
      Retry-on-another-engine menu slot's live enabling) has no Calibre/GUI in CI and was NOT run
      against a real Calibre — recorded as WINDOWS.md entry 6 (unrun-verify). Every layer below the
      Qt widgets (failure_groups' class/target derivation, both retry jobs, the enriched EXECUTE
      result) is unit-tested and green."
  - id: D2
    description: "job_retry_classify is the ONE retry entry point, reachable from both the result
      dialog's own retry buttons and the menu's 'Retry on another engine' chooser through the same
      self._run dispatch site — running it twice over the same books leaves at most one failure row
      per book, a book recovered on the retry leaving the log rather than accumulating a duplicate."
    requirement: WRITE-07
    verification:
      - kind: unit
        ref: "tests/test_plugin_jobs.py#test_job_retry_classify_second_attempt_recovers_and_clears_the_failure_row"
        status: pass
      - kind: unit
        ref: "tests/test_plugin_jobs.py#test_job_retry_targets_reads_the_failure_log_scoped_to_ids"
        status: pass
    human_judgment: true
    rationale: "The dispatch path from a real button click (dialog or menu chooser) into
      action._retry_classify has no Calibre/GUI in CI — WINDOWS.md entry 6. The job function both
      surfaces call is unit-tested directly."
  - id: D3
    description: "The plugin still never calls run_writer, never spawns a second writer process,
      and dispatches every job (including the two new retry jobs) through the ONE action._run site
      — WRITE-08 holds across every verb this phase built, not just the ones this plan touched."
    requirement: WRITE-08
    verification:
      - kind: unit
        ref: "tests/test_plugin_source.py#test_the_plugin_never_spawns_a_second_writer"
        status: pass
      - kind: unit
        ref: "tests/test_plugin_source.py#test_only_one_module_dispatches_jobs"
        status: pass
      - kind: unit
        ref: "tests/test_plugin_source.py#test_no_core_import_can_run_on_the_gui_thread"
        status: pass
      - kind: unit
        ref: "tests/test_plugin_source.py#test_every_write_verb_dispatches_a_plan_and_an_execute_job"
        status: pass
    human_judgment: false
  - id: D4
    description: "plugin/selftest.py drives one PLAN -> picker/engine-picker/scope-dialog data ->
      EXECUTE -> result data round trip per write verb (staleness/wrangle/classify/synopsis/
      promote/backfill/retry), captured rather than shown, never reaching a cloud engine, and
      reports the true longest whole-run GUI-thread heartbeat gap."
    verification:
      - kind: unit
        ref: "tests/test_plugin_source.py#test_every_write_verb_dispatches_a_plan_and_an_execute_job"
        status: pass
      - kind: unit
        ref: "tests/test_plugin_source.py#test_job_functions_accept_abort_log_and_notifications"
        status: pass
    human_judgment: true
    rationale: "selftest.py only ever runs under SCOURGIFY_SMOKE=1 against a real Calibre — not
      exercised in this environment (no Calibre installed). Recorded as WINDOWS.md entry 7
      (unrun-verify). Its source structure (one step per verb, the usable_engines() lock, the
      capture-and-defer shim) is reviewed and syntax/AST-checked, not driven."
  - id: D5
    description: "Two completed EXECUTE runs render independently: neither result shares a mutable
      object with the other, and both survive json.dumps after the second lands."
    verification:
      - kind: unit
        ref: "tests/test_plugin_jobs.py#test_two_execute_results_are_independent"
        status: pass
    human_judgment: false

duration: 30min
completed: 2026-09-09
status: complete
---

# Phase 2 Plan 8: The Grouped Diff-After, the Classify Retry Job, and the Phase's selftest Sweep Summary

**Every classify write now ends in a diff-after grouped by `engines.failure_class` — a failed book
never silently drops off the row table, a refusal offers every other usable engine, a
quota/timeout/parse/error failure offers the same engine again, and an auth/permission failure
offers nothing — reachable from a result dialog's own retry buttons AND a live "Retry on another
engine" menu slot through the ONE `jobs.job_retry_classify` entry point, plus a driven PLAN ->
picker -> EXECUTE -> result round trip for every write verb this phase built, in `plugin/selftest.py`.**

## Performance

- **Duration:** ~30 min
- **Started:** 2026-09-09T11:41:00Z (approx.)
- **Completed:** 2026-09-09T12:09:02Z
- **Tasks:** 3
- **Files modified:** 7 across 3 commits

## Accomplishments

- `jobs.failure_groups(rows, env=None)`: the ONE derivation of which recovery verb a failure
  earns, from the taxonomy alone — `engines.RETRYABLE`, `engines.REFUSAL`, and the `auth`/
  `permission` no-target rule — never a name test. A row may carry an optional `'engine'` key
  (the engine that produced it); `job_execute_classify` always knows and stamps it (one engine per
  run), while the persistent `classify_failures.csv` `job_retry_targets` reads never has it —
  without a known engine, a refusal offers every usable engine rather than risk excluding the
  wrong one, and a retryable class offers nothing rather than guess which engine to retry on.
  Target labels reuse `engines.engine_options`' own three-case price fragment (`_retry_label`) —
  free / sub-cent / usual — so one rounding rule for a price exists in the codebase.
- `jobs.job_retry_classify` / `jobs.job_retry_targets`: `job_retry_classify` is
  `job_execute_classify` restricted to an explicit book-id list on a named engine — the ONE retry
  dispatch both the dialog and the menu call, relying on `classify.Plan.run`'s own existing
  per-run failure-log rewrite to keep a recovered book from accumulating a second row.
  `job_retry_targets` is the read job the menu's own slot needs.
- `job_execute_classify`'s diff-after: a failed book now gets its own row (`'<class> on
  <engine>'`) instead of a silent `continue`; `engine_failures` carries `(book, title, reason)`
  triples (was `(book, reason)`, matching the interfaces block's own documented shape no prior
  plan actually produced); the computed `groups` ride along in the result for the dialog to
  render. `execute_result` gained a `groups=()` parameter, defaulting to `[]` for every other verb.
- `plugin/result_dialog.py`: one section per failure class present in a result's `groups` — the
  class, its book count, a titles sample, and one button per target (the job-built label,
  including the price) — dispatching straight to `on_retry(engine_id, book_ids)` with no engine
  picker in between. A group with no targets renders an explanatory line, no control. Renders
  plain data only — no core import, AST-checked.
- `plugin/picker.py`: `RetryChooser`/`show_retry_chooser` — the menu half of the same contract,
  listing the same groups/targets a result dialog would render.
- `plugin/action.py`: the "Retry on another engine" slot goes live, backed by
  `self._retry_groups`/`self._retry_reason`, refreshed by dispatching `jobs.job_retry_targets` on
  every menu open and after every completed write. `self._retry_classify` is the ONE method both
  the result dialog's own retry buttons (wired for the classify verb in `_execute_done`) and the
  menu's chooser (`_open_retry_chooser`) call, through the ONE `self._run` site.
- `plugin/selftest.py`: `step_staleness`/`step_wrangle`/`step_classify`/`step_synopsis`/
  `step_promote`/`step_backfill`/`step_retry` — one driven PLAN -> picker/engine-picker/
  scope-dialog data -> EXECUTE -> result data round trip per verb, via a new capture-and-defer
  shim over `show_picker`/`show_scope_dialog`/`show_engine_picker`/`show_refusal`/
  `show_retry_chooser`/`show_result` (extending the existing `info_dialog` capture the same way).
  The engine-spending steps never reach a cloud engine — `apple` is the only engine this harness
  will ever choose, gated by `usable_engines()` and a defense-in-depth check inside the
  engine-picker capture; promote's own judge-aware picker never offers `apple` at all, so that
  step always ends up correctly skipped. The whole-run "longest GUI-thread heartbeat gap" is now
  a genuine cross-step maximum (`worst_gap_ms`, tracked independently of the pre-existing
  per-verb `self.beats` reset) rather than a per-step approximation of it.
- `tests/test_plugin_source.py`: `test_every_write_verb_dispatches_a_plan_and_an_execute_job` —
  the phase sweep asserting every verb's job pair is both defined in `jobs.py` and referenced from
  `action.py`, catching a verb wired to a dialog but never to a job.
- `tests/test_plugin_jobs.py`: `test_two_execute_results_are_independent` plus the new
  `failure_groups`/`job_retry_classify`/`job_retry_targets` coverage (10 new tests total).

## Task Commits

1. **Task 1: The failure-grouping helper and the one retry job both surfaces call** - `cddd9a9`
   (feat) — also carries `job_execute_classify`'s enriched diff-after (failed-book rows, the
   `engine_failures` shape fix, the computed `groups`), since D-01's "job computes, dialog
   renders" discipline places that logic here, feeding task 2's rendering.
2. **Task 2: The grouped result dialog, its retry controls, and the live Retry slot** - `b73022a`
   (feat)
3. **Task 3: One driven round trip per verb in selftest.py, and the phase's source-test sweep** -
   `59db5b8` (test) — also carries `test_two_execute_results_are_independent` in
   `tests/test_plugin_jobs.py`, since it lives in the same file `cddd9a9` already touched.

_No plan-metadata commit yet — this SUMMARY and the STATE/ROADMAP/REQUIREMENTS updates are
committed together immediately after this file is written (per the atomic close-out invariant)._

## Files Created/Modified

- `plugin/jobs.py` - `failure_groups`, `_retry_label`, `job_retry_classify`, `job_retry_targets`;
  `execute_result(groups=())`; `job_execute_classify`'s failed-book rows and enriched
  `engine_failures`.
- `plugin/result_dialog.py` - the grouped failure-class sections and per-target retry buttons.
- `plugin/picker.py` - `RetryChooser`/`show_retry_chooser` (menu half of D-10).
- `plugin/action.py` - the live `Retry on another engine` slot, `_refresh_retry_targets`/
  `_retry_targets_done`/`_open_retry_chooser`/`_retry_classify`, `_execute_done`'s `on_retry`
  wiring for the classify verb.
- `plugin/selftest.py` - the capture-and-defer shim over every picker/result dialog, the 7 new
  write-verb steps, `_no_usable_engine`/`_run_verb_chain`, the genuine whole-run `worst_gap_ms`.
- `tests/test_plugin_jobs.py` - 10 new tests: `failure_groups` (auth/permission empty targets,
  refusal excludes the refusing engine, retryable targets only the same engine, an
  unknown-engine row offers nothing), `_retry_label`'s price-fragment reuse, `job_retry_classify`'s
  recovery/log-clearing round trip, `job_retry_targets`'s scoping, and
  `test_two_execute_results_are_independent`.
- `tests/test_plugin_source.py` - `test_every_write_verb_dispatches_a_plan_and_an_execute_job`.

## Decisions Made

See `key-decisions` in the frontmatter — the classify-only scope for retry groups/targets, the
`engine_failures` shape fix, the optional-per-row `engine` key (known vs. unknown-engine
degradation), the new `picker.py` addition outside this plan's declared `files_modified`, and the
`worst_gap_ms` fix for the whole-run heartbeat claim.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] `plugin/picker.py`'s `RetryChooser`/`show_retry_chooser` — not
in this plan's declared `files_modified`**
- **Found during:** Task 2, implementing "the menu's `Retry on another engine` slot ... opens a
  small chooser listing the same targets the dialog would render"
- **Issue:** The plan's task 2(c) requires a menu-side chooser dialog, but no existing widget in
  `plugin/picker.py` (or elsewhere) fits, and `picker.py` was not listed in this plan's
  `files_modified`.
- **Fix:** Added `RetryChooser`/`show_retry_chooser` to `plugin/picker.py`, following the exact
  precedent plan 02-06 set for `RefusalDialog` in the same situation: a Qt widget bound by D-01's
  no-core-import contract has nowhere else to live.
- **Files modified:** `plugin/picker.py`
- **Verification:** AST check confirms `picker.py` still imports nothing outside `qt.core`;
  `tests/test_plugin_source.py` unaffected (no new core import, no second `ThreadedJob` site).
- **Committed in:** `b73022a` (Task 2 commit)

**2. [Rule 1 - Bug] `job_execute_classify`'s `engine_failures` carried `(book, reason)` 2-tuples,
not the `(book, title, reason)` shape `execute_result`'s own docstring (since plan 02-01) already
documented**
- **Found during:** Task 1, designing `failure_groups`' expected row shape
- **Issue:** `failure_groups` needs a `title` per failure row to render a group's sample; classify
  and synopsis's own EXECUTE jobs both built `engine_failures` as `(book, reason)` only, missing
  the `title` the interfaces block always claimed.
- **Fix:** `job_execute_classify` now builds `engine_failures` as `(book, title, reason)`,
  reading `title` from the failure log row (falling back to the book's own title) — scoped to
  classify only (this plan's own scope); synopsis's matching 2-tuple shape is left as-is.
- **Files modified:** `plugin/jobs.py`
- **Verification:** `test_job_retry_classify_second_attempt_recovers_and_clears_the_failure_row`
  asserts every `engine_failures` row has exactly 3 elements.
- **Committed in:** `cddd9a9` (Task 1 commit)

**3. [Rule 1 - Bug] `plugin/selftest.py`'s "gap() already spans the whole run" claim (task 3's own
read_first) did not match the pre-existing code**
- **Found during:** Task 3, implementing the whole-run heartbeat report
- **Issue:** `self.beats` is reset to `[]` at the start of every `_run_verb`/`_menu` call (and my
  new `_run_verb_chain`), so `self.gap()`'s `max()` over it only ever reflects ONE step, never
  the whole run — nothing in the pre-existing file computed a genuine cross-step maximum, despite
  the plan's own read_first asserting it did.
- **Fix:** Added `worst_gap_ms`, updated incrementally on every heartbeat tick regardless of any
  `self.beats` reset, reported in `finish()` as the phase's own measured "GUI-thread portion of
  any dispatch < 100 ms" number.
- **Files modified:** `plugin/selftest.py`
- **Verification:** Syntax-checked; the mechanism is independent of `self.beats` by construction
  (a separate, never-reset accumulator).
- **Committed in:** `59db5b8` (Task 3 commit)

---

**Total deviations:** 3 auto-fixed (1 missing critical, 2 bugs). **Impact on plan:** All three
were necessary for the plan's own literal requirements (a Qt widget the menu chooser needs, the
`title` field `failure_groups` needs, an honest whole-run gap number) to hold; no scope creep — all
three land inside the same files the corresponding task already touches.

## Known Traceability Check

Per this plan's own instructions: `WRITE-04` was checked before plan 02-06 actually built the
synopsis verb (02-04's SUMMARY over-declared it). Confirmed now genuinely satisfied — 02-06's
SUMMARY documents `job_plan_synopsis`/`job_execute_synopsis`/the live `Settle descriptions` menu
slot, all unit-tested. Not corrected here (a prior plan's already-committed SUMMARY is out of this
plan's scope to rewrite); flagged for visibility only, as instructed.

## Known Stubs

None — every code path this plan added is wired to real data (`failure_groups`' targets come from
`engines.CLASSES`/`RETRYABLE`/`usable_engines`, never a hardcoded list; the result dialog and
retry chooser both render plain data the job already computed).

## Issues Encountered

None beyond the three deviations documented above.

## Outstanding Manual Verification (Process Note)

Both Task 2 and Task 3 carry a `<human-check>` in their `<verify>` blocks — a real-Calibre GUI
walkthrough of the grouped result dialog / retry controls / menu slot (task 2), and a
`SCOURGIFY_SMOKE=1 calibre --with-library <throwaway>` transcript read (task 3). No Calibre
installation is available in this environment, so neither could be performed. Every automated
`<verify>` (task-level and plan-level) is green.

**Recorded in `.planning/WINDOWS.md`** as entries 6 and 7 (`unrun-verify`, phase 02) — alongside
the five prior open entries this plan was told not to close (from plans 02-01/02-03/02-04/02-05/
02-06). All seven should be run together in a real Calibre before this phase ships. Before this
plan is considered production-verified: open a real Calibre with a throwaway library, run a
classify pass on books that produce at least one engine refusal, and confirm the result dialog
groups failures by class with a working priced retry button (and no control for an auth-class
group), that the "Retry on another engine" menu slot enables/greys correctly, and that
`SCOURGIFY_SMOKE=1` drives all seven new steps to a terminal capture with the reported whole-run
heartbeat gap under 100 ms.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- This is the LAST of the eight planned write-verb plans in phase 02 — every stage of the full
  maintenance loop (wrangle -> staleness -> synopsis -> classify -> review -> promote -> backfill)
  is now reachable from the toolbar menu, with a truthful grouped diff-after and a working retry
  path for classify's own failures.
- `requirements-completed: [WRITE-07, WRITE-08]` — both declared only by this plan (confirmed via
  `requirements.ready-ids`; no sibling plan shares either ID), so both mark complete immediately.
  Every WRITE-* requirement in phase 02 is now `Complete`.
- **Blocker (carried forward, now 7 entries):** the real-Calibre human-checks for `Re-derive
  status` (02-01), classify's scope/engine pickers (02-03), the Review 1-by-1 control (02-04),
  `Normalize fields` (02-05), `Settle descriptions` (02-06), and now the grouped result dialog /
  retry controls and the `selftest.py` sweep (02-08) should all be run together before this phase
  ships.

## Self-Check: PASSED

- `plugin/jobs.py`, `plugin/result_dialog.py`, `plugin/picker.py`, `plugin/action.py`,
  `plugin/selftest.py`, `tests/test_plugin_jobs.py`, `tests/test_plugin_source.py` — all exist on
  disk with the expected changes (`grep -c 'def failure_groups(\|def job_retry_classify(\|def
  job_retry_targets(' plugin/jobs.py` confirms all three defined).
- Commits `cddd9a9`, `b73022a`, `59db5b8` — all found in `git log --oneline`.
- All task-level `<acceptance_criteria>` re-verified: `grep -c 'def failure_groups('/'def
  job_retry_classify('/'def job_retry_targets('` each return 1; `grep -v comments | grep -c
  RETRYABLE` returns 2; the AST import check on `result_dialog.py` lists only `qt.core`; `grep -c
  'jobs.job_retry_classify'` in `action.py` is 2; `grep -v comments | grep -c '_write_running'` in
  `action.py` is 13; `grep -v comments | grep -c 'usable_engines'` in `selftest.py` is 3; both new
  test functions are defined.
- Plan-level `<verification>`: `tests/test_plugin_source.py`, `tests/test_plugin_safety.py`,
  `tests/test_plugin_jobs.py`, `tests/test_engines.py`, `tests/test_artifacts.py` all green;
  `tests/test_wizard_flow.py`, `tests/test_wizard.py`, `tests/test_cli.py` pass with no edits to
  the wizard (PUB-05, confirmed via `git status` — no `wizard.py` diff); full suite (`for t in
  tests/test_*.py; do uv run "$t" || exit 1; done`) green, no failures. No test in this plan makes
  a real engine call — every engine construction in the new tests is a fake `_engine_ask` closure,
  and `engines._post_json` is never reached.
- Manual `<human-check>`s (tasks 2 and 3) NOT run (no Calibre in this environment) — see
  "Outstanding Manual Verification" above; recorded in `.planning/WINDOWS.md` entries 6 and 7, not
  silently marked done.

---
*Phase: 02-write-verbs-on-a-selection*
*Completed: 2026-09-09*
