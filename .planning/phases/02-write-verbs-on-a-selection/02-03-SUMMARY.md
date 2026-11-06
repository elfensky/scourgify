---
phase: 02-write-verbs-on-a-selection
plan: 03
subsystem: plugin
tags: [calibre-plugin, classify, engines, engine-picker, scope-dialog, write-path, edit-log]

# Dependency graph
requires:
  - phase: 02-write-verbs-on-a-selection
    provides: "02-01's Qt-free job layer (plugin/jobs.py, D-12), the ONE verb-parameterised picker pattern, the per-job ceremony (_ceremony/#72), and 02-02's write=None + late-lookup transport seam already on classify.apply_proposal"
provides:
  - "An honest sub-cent price label on engines.engine_options — a real but tiny estimate never rounds down to 'free'"
  - "classify.Plan.run(on_book=, stop=) — the progress/abort seam a Calibre job's notifications/abort objects drive without the core learning a Calibre type"
  - "common.write_ops(outcome=) / _write_run(ok_outcome=) — a caller-named success-path footer outcome, for a cancelled run's footer to read 'cancelled' instead of 'ok'"
  - "The relocated Qt-free stored-key store (jobs._prefs/stored_keys/job_verify) — ONE owner reachable from both the settings dialog and a worker job"
  - "jobs._engine_ask / jobs._require_column — the engine-construction injection point and the missing-#wrangled-column pre-flight refusal"
  - "jobs.job_scope_rows / job_plan_classify / job_execute_classify — classify's PLAN/EXECUTE jobs, resolving the todo set once, pricing every usable engine over it, and running the engine pass + write as one job"
  - "plugin/picker.py: ScopeDialog and EnginePicker — D-06's two-step scope-then-price flow, D-07's one-button-per-engine run"
  - "plugin/action.py: 'Classify <these N books>' and 'Classify the never-classified here' live on the toolbar menu"
affects: [02-04, 02-05, 02-06, 02-07, 02-08]

# Actuals (#2632)
actuals:
  tokens: 15127
  tasks: 3
  commits: 3

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Job-supplied plain-data enrichment for a Qt-only dialog: engine_options' own 4-tuple
      carries no usability flag and no TRAITS 'limits' text, and plugin/picker.py may import
      NOTHING from scourgify (D-01's contract) — so job_plan_classify hands the picker
      `usable`/`engine_limits` as sibling keys instead of the picker deriving them itself."
    - "restrict_to_selection as an explicit scope_spec flag, not an implicit ids-non-empty
      inference: the ScopeDialog's own 'never classified' row and the dedicated 'Classify the
      never-classified here' shortcut both resolve mode='unclassified', but only the shortcut
      restricts to the current selection — an implicit inference off 'ids is non-empty' would
      have silently restricted the ScopeDialog's row too, whose label already promises the
      whole-library backlog count."
    - "stop()/on_book() checked at the head of each loop iteration in Plan.run — mirrors the
      existing KeyboardInterrupt branch's shutdown(wait=False, cancel_futures=True) + dump()
      + a `cancelled` flag, so a Calibre abort and Ctrl+C degrade the same way."

key-files:
  created: []
  modified:
    - src/scourgify/engines.py
    - src/scourgify/classify.py
    - src/scourgify/common.py
    - plugin/jobs.py
    - plugin/config.py
    - plugin/picker.py
    - plugin/action.py
    - tests/test_engines.py
    - tests/test_classify_run.py
    - tests/test_plugin_jobs.py

key-decisions:
  - "engine_options' price fragment is THREE cases, not two: exactly 0.0 -> 'free'; (0, 0.005) ->
    a dedicated sub-cent label ('~<$0.01'); otherwise the usual '~$%.2f' — a real but tiny cost
    must never render as free."
  - "The stored-key store (_prefs/stored_keys) and job_verify moved from plugin/config.py (which
    hard-imports qt.core) into plugin/jobs.py (Qt-free) — config.py now imports them back. This
    is what lets classify's PLAN/EXECUTE jobs call stored_keys() at all, since jobs.py must stay
    importable under plain CI Python with no Calibre/Qt installed."
  - "job_execute_classify rebuilds the plan from carry['todo_ids'] via a.books (mode 'ids'),
    never re-resolving the original scope — the set the engine bills is exactly the set the
    engine picker priced, even if the library changed between PLAN and EXECUTE."
  - "job_execute_classify sets p.opts.yes = True unconditionally before p.run() — the scope step
    already answered classify.spend_gate; a second gate here would be exactly the confirmation
    dialog this phase deletes."
  - "apply_proposal(write=writer) is called with rows=None (from disk), matching the CLI's own
    two-step classify -> classify --apply semantics: the EXECUTE job applies the WHOLE pending
    proposal file, not just this dispatch's todo_ids — carry['todo_ids'] scopes what gets SENT
    to the engine, not what gets APPLIED."

patterns-established:
  - "Pattern: job-supplied enrichment for Qt-only dialogs — see tech-stack.patterns above."
  - "Pattern: explicit restrict_to_selection flag over implicit inference — see tech-stack.patterns above."

requirements-completed: [WRITE-03, WRITE-06]

coverage:
  - id: D1
    description: "The engine picker's price is computed over the exact resolved todo set (never
      the selection size) and a real but tiny cost never rounds down to 'free'."
    requirement: "WRITE-03"
    verification:
      - kind: unit
        ref: "tests/test_engines.py#test_a_sub_cent_estimate_never_reads_as_free"
        status: pass
      - kind: unit
        ref: "tests/test_plugin_jobs.py#test_job_plan_classify_prices_over_the_resolved_todo_set_not_the_selection"
        status: pass
    human_judgment: false
  - id: D2
    description: "A key typed into the settings dialog and never exported to the environment
      reaches a classify run (jobs._engine_ask injects engines.resolve_keys(stored_keys()))."
    requirement: "WRITE-03"
    verification:
      - kind: unit
        ref: "tests/test_plugin_jobs.py#test_engine_ask_reaches_a_stored_only_key_never_touching_the_environment"
        status: pass
    human_judgment: false
  - id: D3
    description: "A classify run on a library that has never had the #wrangled column refuses
      cleanly with a GuardrailError naming the fix, before any op reaches the write path —
      never a bare ValueError reaching gui.job_exception."
    requirement: "WRITE-03"
    verification:
      - kind: unit
        ref: "tests/test_plugin_jobs.py#test_job_plan_classify_refuses_cleanly_without_the_wrangled_column"
        status: pass
      - kind: unit
        ref: "tests/test_plugin_jobs.py#test_a_missing_wrangled_column_refuses_execute_too"
        status: pass
    human_judgment: false
  - id: D4
    description: "Classify runs on a chosen scope (selection / new-changed / never-classified
      with a batch size / most-recent-N / whole library), resolved ONCE through classify.plan(),
      with the >200-book cloud spend gate answered by the scope step — no second confirmation
      dialog between the priced engine button and the spend."
    requirement: "WRITE-03"
    verification:
      - kind: unit
        ref: "tests/test_plugin_jobs.py#test_job_execute_classify_sets_yes_true_so_the_spend_gate_is_never_reached"
        status: pass
      - kind: unit
        ref: "tests/test_plugin_jobs.py#test_job_plan_classify_unclassified_shortcut_restricts_to_the_selection"
        status: pass
      - kind: unit
        ref: "tests/test_plugin_jobs.py#test_job_plan_classify_unclassified_without_a_selection_is_the_whole_backlog"
        status: pass
    human_judgment: true
    rationale: "The Qt layer itself (ScopeDialog rendering all five fixed slots correctly greyed,
      EnginePicker's buttons, click-to-run with no further dialog) has no Calibre/GUI in CI and
      was NOT run against a real Calibre — see 'Outstanding Manual Verification' below. Every
      layer below the Qt widgets (job bodies, scope resolution, pricing) is unit-tested and green."
  - id: D5
    description: "A running classify reports (fraction, message) tuples into Calibre's own job
      list and stops between books when the job is aborted; the aborted run's partial proposal
      is saved and its edit-log footer records the cancelled outcome while applied ops stay
      logged and undoable."
    requirement: "WRITE-06"
    verification:
      - kind: unit
        ref: "tests/test_classify_run.py#test_run_reports_progress_per_book_and_stops_when_asked"
        status: pass
      - kind: unit
        ref: "tests/test_classify_run.py#test_run_with_neither_on_book_nor_stop_is_unchanged"
        status: pass
      - kind: unit
        ref: "tests/test_plugin_jobs.py#test_job_execute_classify_closes_a_cancelled_run_as_cancelled_and_still_applies_it"
        status: pass
    human_judgment: false
  - id: D6
    description: "The classify write carries engine and model into the edit-log run header (via
      _Writer), and the applied proposal is archived in the CLI's artifacts format so
      artifacts.classified_ids() and a terminal run agree about what has been attempted."
    requirement: "WRITE-06"
    verification:
      - kind: unit
        ref: "tests/test_editlog.py#test_write_ops_records_the_engine_and_model_on_the_run_header"
        status: pass
      - kind: unit
        ref: "tests/test_plugin_jobs.py#test_job_execute_classify_sets_yes_true_so_the_spend_gate_is_never_reached"
        status: pass
    human_judgment: false

duration: 25min
completed: 2026-09-09
status: complete
---

# Phase 2 Plan 3: Classify — The Scope Dialog, the Engine Picker, and Live Menu Slots Summary

**Classify runs end-to-end from the toolbar on a chosen scope — a scope dialog on
`classify.scope_options`'s fixed slots, one PLAN job resolving the todo set ONCE, an engine
picker whose buttons ARE the run (price computed over that exact set, never rounded down to
free), and one EXECUTE job running the engine pass and the write together — with an injected key
mapping so a settings-only key reaches the run, and a clean refusal instead of a crash on a
library that has never run `scourgify setup`.**

## Performance

- **Duration:** ~25 min
- **Started:** 2026-09-09T09:42:42Z
- **Completed:** 2026-09-09T10:07:35Z
- **Tasks:** 3
- **Files modified:** 10 across 3 commits

## Accomplishments

- `engines.engine_options`'s price fragment is now three cases instead of two: exactly `0.0` ->
  `free`; a real cost under half a cent -> a dedicated sub-cent label (`~<$0.01`); otherwise the
  usual `~$%.2f` — a real but tiny estimate can no longer round down to "free".
- `classify.Plan.run(ask=None, *, on_book=None, stop=None)` — a progress/abort seam checked at
  the head of each loop iteration, mirroring the existing `KeyboardInterrupt` branch's
  `shutdown(wait=False, cancel_futures=True)` + `dump()` + a new `Plan.cancelled` flag. Both
  params default to `None`, so the CLI path is unchanged.
- `common.write_ops(outcome=)` / `_write_run(ok_outcome=)` — the SUCCESS-path footer outcome is
  now caller-named (default `"ok"`), letting a cancelled classify run's footer read `"cancelled"`
  while its already-applied ops stay logged and undoable exactly like an uninterrupted run's.
  `run_writer` (the CLI, no abort) grows no such parameter.
- The stored-key store (`_prefs`/`stored_keys`) and `job_verify` relocated from
  `plugin/config.py` (hard-imports `qt.core`) into `plugin/jobs.py` (Qt-free) — ONE owner
  reachable from both the settings dialog and a worker job; `config.py` imports them back.
- `jobs._engine_ask(engine_id, model, timeout)` — the ONE place this phase constructs an engine,
  injecting `engines.resolve_keys(stored_keys())` so a key typed into settings and never
  exported reaches a classify run (the "works for me" bug 02-RESEARCH.md flagged as most likely).
- `jobs._require_column(con, label, verb)` — a pre-flight refusing cleanly (`GuardrailError`)
  when `#wrangled` doesn't exist yet, instead of letting `ops.apply_ops`'s bare `ValueError`
  reach `gui.job_exception` as a raw traceback.
- `jobs.job_scope_rows` / `job_plan_classify` / `job_execute_classify` — resolve the classify
  scope ONCE via `classify.plan()`, price every usable engine over the exact resolved todo set,
  then rebuild the plan restricted to those exact ids for EXECUTE (never a re-resolve that could
  widen it). `p.opts.yes = True` makes the scope step the answer to `classify.spend_gate`.
- `plugin/picker.py`: `ScopeDialog` (step 1 of D-06 — one control per fixed `scope_options` slot,
  inline batch-size/most-recent-N spinboxes) and `EnginePicker` (step 3 of D-06, all of D-07 —
  one button per engine; clicking a button IS the run). Neither imports `scourgify` or names an
  engine by string equality.
- `plugin/action.py`: `Classify <these N books>` and `Classify the never-classified here` go
  live — scope dialog -> `job_plan_classify` -> engine picker -> `job_execute_classify`, each hop
  through the ONE `self._run` site; `_write_running` greys both slots (and staleness) while a
  write is live.

## Task Commits

1. **Task 1: Core seams classify's plugin verb needs** - `8940773` (feat)
2. **Task 2: The classify PLAN and EXECUTE jobs** - `9d59a4f` (feat)
3. **Task 3: The scope dialog, the engine picker, and the classify menu slots** - `0e1575f` (feat)

_No plan-metadata commit yet — this SUMMARY and the STATE/ROADMAP/REQUIREMENTS updates are
committed together immediately after this file is written (per the atomic close-out invariant)._

## Files Created/Modified

- `src/scourgify/engines.py` - `engine_options`'s three-case price fragment (sub-cent label).
- `src/scourgify/classify.py` - `Plan.run(on_book=, stop=)`, `Plan.cancelled`.
- `src/scourgify/common.py` - `write_ops(outcome=)` / `_write_run(ok_outcome=)`.
- `plugin/jobs.py` - relocated `_prefs`/`stored_keys`/`job_verify`; new `_engine_ask`,
  `_require_column`, `job_scope_rows`, `job_plan_classify`, `job_execute_classify`; `_Writer`
  gains `outcome=`.
- `plugin/config.py` - imports the stored-key store from `jobs` instead of owning it.
- `plugin/picker.py` - `ScopeDialog`, `EnginePicker`, `show_scope_dialog`, `show_engine_picker`.
- `plugin/action.py` - `classify`/`classify_backlog` and their PLAN-done/EXECUTE-dispatch chain;
  both classify menu slots live.
- `tests/test_engines.py` - `test_a_sub_cent_estimate_never_reads_as_free`.
- `tests/test_classify_run.py` - `test_run_reports_progress_per_book_and_stops_when_asked`,
  `test_run_with_neither_on_book_nor_stop_is_unchanged`.
- `tests/test_plugin_jobs.py` - 10 new tests covering `job_plan_classify`/`job_execute_classify`
  (pricing, missing-column refusal, spend-gate bypass, cancellation, the never-classified
  shortcut's selection restriction) and `_engine_ask`'s key injection.

## Decisions Made

See `key-decisions` in the frontmatter — the three-case price fragment, relocating the stored-key
store into `jobs.py`, EXECUTE rebuilding from `carry['todo_ids']` (never a re-resolve),
`p.opts.yes = True` as the spend-gate answer, and `apply_proposal(write=writer)` applying the
whole pending proposal file (CLI parity) rather than just this dispatch's todo set.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] `job_plan_classify` gained `usable`/`engine_limits` alongside `engines`**
- **Found during:** Task 3, designing `EnginePicker`
- **Issue:** The plan's task 3(b) requires the engine picker to render "the TRAITS-derived
  failure-mode hint" on every button, but `engines.engine_options`'s own 4-tuple carries no
  usability flag and no TRAITS `limits` text — and `plugin/picker.py` may import NOTHING from
  `scourgify` (D-01's own contract, enforced by this task's own AST acceptance criterion). The
  picker structurally cannot derive either value itself.
- **Fix:** `job_plan_classify` (which CAN import `scourgify.engines`) now also returns
  `result['usable']` (the usable-engine id list) and `result['engine_limits']`
  (`{engine_id: engines.trait(engine_id, 'limits')}`) as sibling keys alongside the existing
  `result['engines']` — additive, not a rename; every Task 2 acceptance criterion still holds.
  `EnginePicker` reads both as plain data with no core import and no string-equality engine test.
- **Files modified:** `plugin/jobs.py`, `plugin/picker.py`
- **Verification:** `tests/test_plugin_jobs.py`'s PLAN test asserts both keys are present and
  populated for every engine; the AST/grep acceptance criteria on `plugin/picker.py` still pass.
- **Committed in:** `0e1575f` (Task 3 commit)

**2. [Rule 1 - Bug] `restrict_to_selection` needed an explicit flag, not an implicit `ids`-non-empty inference**
- **Found during:** Task 3, implementing "Classify the never-classified here" on a mixed
  selection
- **Issue:** `job_plan_classify`'s `ids` parameter (the toolbar selection at click time) is
  non-empty for BOTH the dedicated "never-classified" shortcut AND the ordinary
  `ScopeDialog`-driven path whenever the user had books selected before opening the menu. An
  implicit "restrict to selection whenever `ids` is non-empty" rule would have silently
  restricted the ScopeDialog's own "never classified — N,NNN books" row too, whose label already
  promises the whole-library backlog count — the row's own text and its actual behaviour would
  have disagreed the moment a selection existed.
- **Fix:** Added an explicit `scope_spec['restrict_to_selection']` boolean, set only by the
  dedicated shortcut's dispatch (`action.classify_backlog`); the ScopeDialog's own "unclassified"
  row never sets it, so it always means the library-wide backlog regardless of the selection.
- **Files modified:** `plugin/jobs.py`, `plugin/action.py`
- **Verification:** `tests/test_plugin_jobs.py::test_job_plan_classify_unclassified_shortcut_restricts_to_the_selection`
  and `::test_job_plan_classify_unclassified_without_a_selection_is_the_whole_backlog` pin both
  behaviours as distinct.
- **Committed in:** `0e1575f` (Task 3 commit)

---

**Total deviations:** 2 auto-fixed (1 missing critical, 1 bug). **Impact on plan:** Both were
necessary to satisfy the plan's own literal task requirements (the picker's TRAITS-derived text,
the shortcut's "restricted to the selection" behaviour) without violating this task's own
acceptance criteria (no core import in `picker.py`, no engine named by string equality). No scope
creep — both fixes are additive to the already-committed Task 2 contract.

## Outstanding Manual Verification (Process Note)

Task 3's `<verify>` block carries a `<human-check>` (a real-Calibre GUI walkthrough: select 2
books, click `Classify these 2 books`, confirm the scope dialog's five fixed slots, the engine
picker's per-engine pricing and click-to-run, progress in Calibre's job list, and a clean
stop-between-books on abort). No Calibre installation is available in this environment, so this
step could not be performed. Every automated `<verify>` (task-level and plan-level) is green, and
this plan's D4 coverage entry is marked `human_judgment: true` for exactly this reason.

**Recorded in `.planning/WINDOWS.md`** (`unrun-verify`, phase 02) so it stays visible at ship
time. Before this plan is considered production-verified: open a real Calibre with a throwaway
library, select 2 books, open the scourgify menu, click `Classify these 2 books`, and confirm —
the scope dialog shows all five fixed slots with the inapplicable ones greyed; the engine picker
shows one button per usable engine with a price naming the resolved book count; clicking a button
starts the run with no further dialog; progress appears in Calibre's job list; pressing the job's
stop button ends the run between books with a result dialog rather than a traceback.

## Known Stubs

None — every code path is wired to real data (no hardcoded/mock values reach the UI). The engine
picker's price and failure-mode text both come from `engines.PRICING`/`TRAITS` via the PLAN job,
never a hand-rolled string in `plugin/picker.py`.

## Issues Encountered

None beyond the two deviations documented above.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Classify is the highest-stakes write verb in this phase, and it is now fully wired end-to-end
  (minus the real-Calibre human-check, tracked above): the scope-dialog -> PLAN -> engine-picker
  -> EXECUTE round trip, the injected key seam, the missing-column refusal, and the
  progress/abort seam are all proven by unit tests with no real engine call anywhere.
- `job_plan_classify`'s `usable`/`engine_limits` enrichment and the `restrict_to_selection` flag
  are new, additive PLAN-result/scope_spec conventions later plans (promote's engine picker,
  synopsis's engine picker) can reuse verbatim — both are D-07-shaped problems every remaining
  engine-picker verb will hit again.
- `requirements-completed: [WRITE-03, WRITE-06]` — both requirements are declared only by
  02-02/02-03 (WRITE-03) and 02-01/02-02/02-03 (WRITE-06), and all declaring plans now have a
  SUMMARY, so the shared-ID gate (#2388) clears them both with this plan's landing.
- **Blocker (carried forward):** the real-Calibre human-check for classify (see "Outstanding
  Manual Verification" above) should be run before this plan ships, alongside 02-01's still-open
  `Re-derive status` human-check — either by hand or via `plugin/selftest.py`'s `SCOURGIFY_SMOKE`
  hook extended to exercise both.

## Self-Check: PASSED

- `src/scourgify/engines.py`, `classify.py`, `common.py`, `plugin/jobs.py`, `plugin/config.py`,
  `plugin/picker.py`, `plugin/action.py`, `tests/test_engines.py`, `tests/test_classify_run.py`,
  `tests/test_plugin_jobs.py` — all exist on disk with the expected changes.
- Commits `8940773`, `9d59a4f`, `0e1575f` — all found in `git log --oneline`.
- All task-level `<acceptance_criteria>` re-verified: the sub-cent price label round-trip, the
  `Plan.run`/`write_ops` signatures, the six new `jobs.py` function definitions, the
  `resolve_keys(` / `from calibre_plugins.scourgify.jobs import` grep counts, `import plugin.jobs`
  with no Calibre installed, `class ScopeDialog`/`class EnginePicker` in `picker.py`, the AST
  no-core-import check, the `== 'apple'` zero-count, the `SOON`/`Edit tags…` menu-slot checks, and
  both `job_plan_classify`/`job_execute_classify` dispatched through `self._run` in `action.py`.
- Plan-level `<verification>`: `tests/test_engines.py`, `tests/test_classify_run.py`,
  `tests/test_plugin_jobs.py`, `tests/test_plugin_source.py`, `tests/test_plugin_safety.py` all
  green; full suite (`for t in tests/test_*.py; do uv run "$t" || exit 1; done`) green, no
  failures. No test in this plan makes a real engine call — every engine construction in the new
  tests is a `Capture`/`_FastNoMatchEngine` stand-in, and `engines._post_json` is never reached.
- Manual `<human-check>` NOT run (no Calibre in this environment) — see "Outstanding Manual
  Verification" above; recorded in `.planning/WINDOWS.md`, not silently marked done.

---
*Phase: 02-write-verbs-on-a-selection*
*Completed: 2026-09-09*
