---
phase: 02-write-verbs-on-a-selection
plan: 07
subsystem: plugin
tags: [calibre-plugin, promote, backfill, engines, engine-picker, decide-seam, write-path, edit-log]

# Dependency graph
requires:
  - phase: 02-write-verbs-on-a-selection
    provides: "02-02's write=None transport seam already on promote.backfill; 02-03's engine-construction/pricing/missing-column conventions (jobs._engine_ask, engine_rows/engine_options/default_engine_id); 02-04's (label, payload) item shape and the picker's Review 1-by-1 control (checklist_decide/backfill_decide); 02-06's two-dispatch harvest/apply pattern (job_execute_synopsis) and RefusalDialog/show_refusal conventions"
provides:
  - "The (text, err) ask envelope on promote.run's default ask/verify_ask (#73), and decide()'s
    one normalization point accepting either that tuple or a bare string, so a promote refusal's
    ledger reason carries a readable engines.failure_class the same way a classify refusal's does"
  - "promote.run(on_cand=, stop=) — the progress/abort seam every other engine pass in this phase
    already had, applied to the last one that needed it"
  - "jobs.job_plan_promote / jobs.job_execute_promote — the adjudication verb's PLAN job (a
    JUDGE-AWARE engine picker: a non-judge engine is always disabled, never a name comparison)
    and a two-dispatch EXECUTE (harvest verdicts with no ledger write, then replay the reviewer's
    ticks and fold only the ticked ones) — an unticked verdict gets no ledger row"
  - "jobs.job_plan_backfill / jobs.job_execute_backfill — the deterministic (no LLM, no engine
    picker) close of the loop: previews the per-book promoted/aliased tag additions and writes
    through common.write_ops via the picker's backfill-shaped decide (falsy aborts)"
  - "The two live menu slots: Adjudicate new tags and Backfill promoted tags — library-scope
    verbs, never gated on the selection, only on a running write"
affects: []

# Actuals (#2632)
actuals:
  tokens: 12140
  tasks: 3
  commits: 3

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "decide() normalizes its injected ask/verify_ask answer to (text, err) at ONE point (a
      nested closure at the top of the function), accepting either the real ask_retry tuple or a
      bare string — the same 'normalize once, read everywhere' discipline ui.checklist already
      applies to review items, applied here to an engine answer instead."
    - "A judge-aware engine picker built entirely from PLAN-job-supplied data: job_plan_promote
      overrides a non-judge engine's row to (ok=False, hint=engines.trait(e, 'unusable')) before
      handing the rows to engines.engine_options/default_engine_id(judge=True) — plugin/picker.py's
      EnginePicker needed ZERO changes, because it already renders disabled rows and a default
      generically from whatever data the job hands it (see Deviations)."
    - "backfill's decide= is a DIFFERENT shape from the checklist decide= (decide(chg, adds) ->
      chg_to_write, falsy aborts) — jobs.py never fabricates it (unlike _record_decide/
      _replay_decide for the checklist shape): action.py builds it directly from
      picker.backfill_decide(dlg) when reviewed, or an identity lambda when not, and hands the
      closure straight into job_execute_backfill's decide= parameter."

key-files:
  created: []
  modified:
    - src/scourgify/promote.py
    - tests/test_promote.py
    - plugin/jobs.py
    - plugin/action.py
    - tests/test_plugin_jobs.py

key-decisions:
  - "job_execute_backfill computes promote.backfill_plan() TWICE — once in the job body (to learn
    which books the reviewer's decide= actually accepted, since promote.backfill()'s own internal
    compute is opaque to the caller) and once again inside promote.backfill() itself for the real
    write. decide is a pure read of already-captured tick state, so calling it twice is safe; this
    mirrors job_execute_wrangle's own 'recompute rather than carry an ops list forward' choice."
  - "plugin/picker.py needed NO changes for Task 3's judge-aware engine picker — EnginePicker
    already derives every disabled row and its default purely from the PLAN result's usable/
    engine_limits/default_engine fields, so job_plan_promote's judge-aware row-building satisfies
    the requirement without touching the widget. See Deviations."
  - "Adjudicate/Backfill are library-scope menu slots (ids always []), never gated on 'nothing
    selected' unlike the four book-scoped verbs above them — only a running write greys them
    (Backfill also greys on a cached empty-plan reason, mirroring synopsis's _comments_reason)."

patterns-established:
  - "Pattern: decide() as the one ask-answer normalization point — see tech-stack.patterns above."
  - "Pattern: a judge-aware (or any capability-aware) engine picker built entirely from
    job-supplied row data, needing zero picker.py changes — see tech-stack.patterns above."

requirements-completed: [WRITE-05]

coverage:
  - id: D1
    description: "The user can adjudicate new-tag candidates from the menu on a judge-capable
      engine, review each verdict 1-by-1, and apply the accepted ones — an unticked verdict gets
      no ledger row and the candidate is offered again next run."
    requirement: "WRITE-05"
    verification:
      - kind: unit
        ref: "tests/test_plugin_jobs.py#test_job_execute_promote_first_dispatch_harvests_verdicts_and_writes_no_ledger_row"
        status: pass
      - kind: unit
        ref: "tests/test_plugin_jobs.py#test_job_execute_promote_second_dispatch_ticked_writes_exactly_ticked_ledger_rows"
        status: pass
      - kind: unit
        ref: "tests/test_plugin_jobs.py#test_job_plan_promote_disables_non_judge_engines_with_the_traits_own_reason"
        status: pass
    human_judgment: true
    rationale: "The Qt layer itself (the engine picker's disabled apple row, the Review 1-by-1
      table populated from a real promote job, the two-dispatch round trip through Calibre's own
      job list) has no Calibre/GUI in CI and was not run against a real Calibre — recorded as a
      WINDOWS.md unrun-verify entry. Every layer below the Qt widgets (both job bodies, the
      engine-judge gating, the record/replay decide= seam) is unit-tested and green."
  - id: D2
    description: "The user can run backfill from the menu: the book-to-tag additions are
      previewed before anything is written, and the write lands through common.write_ops with the
      snapshot and the wipe guard applied (both inherited from _write_run, unconditionally on
      every write path)."
    requirement: "WRITE-05"
    verification:
      - kind: unit
        ref: "tests/test_plugin_jobs.py#test_job_plan_backfill_previews_the_per_book_additions"
        status: pass
      - kind: unit
        ref: "tests/test_plugin_jobs.py#test_job_execute_backfill_writes_through_the_transport_and_reports_a_conflicting_book_as_skipped"
        status: pass
      - kind: unit
        ref: "tests/test_plugin_jobs.py#test_job_execute_backfill_a_falsy_decide_writes_nothing"
        status: pass
    human_judgment: true
    rationale: "Same Qt-layer caveat as D1 — the picker's preview table and the plain Run/Review
      1-by-1 round trip for backfill have not been exercised against a real Calibre GUI. The write
      path underneath (common.write_ops, the snapshot, the wipe guard, the conflict filter) is
      identical to every other verb in this phase and is unit-tested here at the job level."
  - id: D3
    description: "promote.run's default ask and verify-ask carry the full (text, err) envelope
      rather than discarding the error, and promote.decide records the engine failure class as
      the ledger reason's prefix, so engines.failure_class can read the class back out of a
      promote row exactly as it can out of a classify row."
    requirement: "WRITE-05"
    verification:
      - kind: unit
        ref: "tests/test_promote.py#test_decide_transport_failure_carries_the_failure_class_into_the_reason"
        status: pass
      - kind: unit
        ref: "tests/test_promote.py#test_decide_bare_string_ask_still_works_unchanged"
        status: pass
      - kind: unit
        ref: "tests/test_promote.py#test_decide_skeptic_response_as_a_tuple_refutes_a_promote"
        status: pass
    human_judgment: false
  - id: D4
    description: "The engine picker for adjudication offers only judge-capable engines as
      enabled; a non-judge engine is shown disabled with its own reason rather than hidden, and no
      engine is identified by comparing its name to a string."
    requirement: "WRITE-05"
    verification:
      - kind: unit
        ref: "tests/test_plugin_jobs.py#test_job_plan_promote_disables_non_judge_engines_with_the_traits_own_reason"
        status: pass
      - kind: other
        ref: "grep -v '^[[:space:]]*#' plugin/picker.py | grep -c \"== 'apple'\"  (returns 0)"
        status: pass
    human_judgment: false
  - id: D5
    description: "A running adjudication reports (fraction, message) tuples into Calibre's own
      job list and stops between candidates when the job is aborted; the candidates already
      decided keep their review rows."
    requirement: "WRITE-05"
    verification:
      - kind: unit
        ref: "tests/test_promote.py#test_run_stop_seam_leaves_already_decided_candidates_in_the_review"
        status: pass
      - kind: unit
        ref: "tests/test_promote.py#test_run_with_neither_on_cand_nor_stop_is_unchanged"
        status: pass
    human_judgment: false

duration: 30min
completed: 2026-09-09
status: complete
---

# Phase 2 Plan 7: Promote and Backfill — the Adjudication and Backfill Menu Verbs Summary

**Adjudication (advocate + skeptic, judge-capable engines only) and backfill (deterministic,
no LLM) go live on the toolbar menu, closing the classify → review → promote → backfill loop —
built on a `(text, err)` ask envelope (#73) that lets a promote refusal earn the same
cross-engine retry a classify refusal already has, and on a plugin/picker.py that needed zero
changes because its `EnginePicker` and `Picker` were already generic enough to render a
judge-aware, job-supplied engine list.**

## Performance

- **Duration:** ~30 min
- **Started:** 2026-09-09T13:21:00+02:00 (approx.)
- **Completed:** 2026-09-09T13:38:44+02:00
- **Tasks:** 3
- **Files modified:** 5 (3 commits)

## Accomplishments

- `promote.run`'s default `ask`/`verify_ask` now build the full `(text, err)` tuple —
  `ask_retry`'s own shape — instead of discarding the error half with `[0]`. `promote.decide`
  normalizes either that tuple or a bare string (the pre-#73 test-fixture shape used throughout
  `tests/test_promote.py`) at ONE point, and when the advocate's response is unparseable, prefixes
  the `error` verdict's reason with `engines.failure_class(err)` whenever the error half carries
  one — the same convention `ask_retry` already produces for classify, so the shared `reason`
  column carries the taxonomy without a new column.
- `promote.run(on_cand=, stop=)` — the progress/abort seam every other engine pass in this phase
  already had (`classify.Plan.run`, `synopsis.Plan.run`), checked at the head of each
  `as_completed` iteration; a truthy `stop()` stops submitting new work and shuts the executor
  down with `wait=False, cancel_futures=True`, while the review file still gets whatever
  candidates were already decided.
- `jobs.job_plan_promote`: reads `promote.candidates()` (the undecided `proposed_new` rows) and
  builds a **judge-aware** engine picker — a non-judge engine (`TRAITS['judge']` False, e.g.
  apple) is ALWAYS rendered disabled, carrying `engines.trait(e, 'unusable')` as its own reason,
  even when it otherwise has a runtime available. `engines.default_engine_id(opts, judge=True,
  usable=judge_usable)` picks the default. No candidates left is the D-04 empty result; no ranked
  artifact at all is a refused result.
- `jobs.job_execute_promote`: ONE function, two dispatches, distinguished by `ticks` (the same
  D-13 shape `job_execute_synopsis` uses). First (`ticks=None`): runs `promote.run()` for real on
  the engine the picker's button named, then harvests the verdicts as `(label, payload)` review
  items via `_record_decide()` — nothing is folded into the overrides directory or the ledger.
  Second (`ticks` a list): replays the reviewer's ticks through `promote.apply_decisions_step`,
  folding only the TICKED verdicts; an unticked verdict writes no ledger row, so
  `promote.candidates()` offers it again next run. Neither dispatch constructs a write transport —
  promote writes no book field, only plain files.
- `jobs.job_plan_backfill` / `jobs.job_execute_backfill`: the deterministic close of the loop, no
  engine picker in this flow. PLAN previews the per-book promoted/aliased tag additions via
  `promote.backfill_plan()`. EXECUTE calls `promote.backfill(decide=decide, write=_Writer(api))`,
  where `decide` is the caller's backfill-shaped adapter (`decide(chg, adds) -> chg_to_write`, a
  falsy return aborts) — `promote.backfill_plan()` is recomputed a SECOND time in the job body (to
  learn which books the caller's `decide` actually accepted, since `promote.backfill`'s own
  internal compute is opaque) so the result rows and `touched` list are accurate; a conflicting
  book (changed since the plan) is reported `skipped: changed since the plan`.
- `plugin/action.py`: `Adjudicate new tags` and `Backfill promoted tags` go live on the menu after
  `Settle descriptions`. Both are library-scope (ids always `[]`, `_ids` unused from `scope`) and
  never gated on "nothing selected"; only a running write greys them (backfill also greys on a
  cached `'nothing to backfill'` reason, mirroring synopsis's `_comments_reason`). Every hop —
  PLAN → engine picker → EXECUTE (harvest) → Picker/Review-1-by-1 → EXECUTE (apply ticks) for
  promote; PLAN → Picker → EXECUTE for backfill — goes through the ONE `self._run` site.
  `self._write_running` is set ONLY immediately before backfill's EXECUTE dispatch — the only one
  of the four promote/backfill dispatches that writes a book field.
- `plugin/picker.py` needed **no changes** for the judge-aware engine picker: `EnginePicker`
  already derives every disabled row (and its default) purely from the PLAN result's
  `usable`/`engine_limits`/`default_engine` fields — see Deviations.

## Task Commits

1. **Task 1: One ask envelope, so a promote failure carries its class to the ledger (#73)** -
   `b0dc7e7` (feat)
2. **Task 2: The promote and backfill PLAN and EXECUTE jobs** - `8e19c36` (feat)
3. **Task 3: The judge-aware engine picker and the two menu slots** - `6b52c3f` (feat)

_No plan-metadata commit yet — this SUMMARY and the STATE/ROADMAP/REQUIREMENTS updates are
committed together immediately after this file is written (per the atomic close-out invariant)._

## Files Created/Modified

- `src/scourgify/promote.py` - `run`'s `(text, err)` ask envelope, `decide`'s one normalization
  point + failure-class-prefixed error reason, `run(on_cand=, stop=)`.
- `tests/test_promote.py` - 5 new tests: the failure-class prefix, the bare-string fixture shape
  still working, the skeptic path unpacking the tuple too, and the `stop`/`on_cand` seam (both the
  cut-short case and the byte-identical-when-omitted case).
- `plugin/jobs.py` - `_promote_cost`, `job_plan_promote`, `job_execute_promote`,
  `job_plan_backfill`, `job_execute_backfill`.
- `tests/test_plugin_jobs.py` - `_promote_lib_ctx`/`_promote_ask` fixtures + 9 new tests covering
  both promote jobs (judge-gating, the empty/refused PLAN results, the harvest-writes-nothing
  contract, the ticked/unticked ledger-row narrowing) and both backfill jobs (the empty D-04
  result, the per-book preview, the write-through + conflict-skip, the falsy-decide no-op).
- `plugin/action.py` - `promote`/`_promote_plan_done`/`_start_promote_execute`/
  `_promote_review_done`/`_finish_promote`/`_promote_apply_done`, `backfill`/
  `_backfill_plan_done`/`_start_backfill_execute`, `_promote_ticks`, the two live menu slots,
  `self._backfill_reason`.

## Decisions Made

See `key-decisions` in the frontmatter — the double `backfill_plan()` compute in
`job_execute_backfill`, `picker.py` needing zero changes for the judge-aware engine picker, and
the library-scope (never selection-gated) menu-slot greying rule.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Task 3's own read_first named "the refused-result rendering added in plan
02-01" for `plugin/picker.py`'s `EnginePicker` extension point, which does not describe how the
widget actually works**
- **Found during:** Task 3, implementing "make `EnginePicker` judge-aware"
- **Issue:** Re-reading `plugin/picker.py` as landed by plans 02-01/02-03/02-04/02-06 shows
  `EnginePicker` ALREADY renders a row disabled purely from the PLAN result's `usable` set, and
  its label/tooltip purely from `engine_limits`/the row's own `label` string (which already embeds
  whatever `hint` the job handed `engines.engine_options`) — it never compares an engine id to a
  string literal and never derives capability itself. The task's own instruction ("the only change
  is to render a row that the PLAN job marked non-judge as a DISABLED button showing that row's
  own reason") describes behavior the widget already has, given the right input data.
- **Fix:** No `picker.py` edit. Instead, `job_plan_promote` (Task 2, already landed) builds its
  `rows`/`usable`/`engine_limits` so a non-judge engine is ALWAYS `(ok=False,
  hint=engines.trait(e, 'unusable'))` before handing them to `engines.engine_options` — the data
  contract `EnginePicker` already consumes generically. This satisfies every one of Task 3's
  literal acceptance criteria (`grep -c "== 'apple'"` is 0, the AST no-core-import check passes,
  exactly one review-table class remains, `test_plugin_source.py`'s one-`ThreadedJob`-site
  invariant is unaffected) without adding code that would have been dead weight duplicating logic
  the widget already had.
- **Files modified:** none beyond what Task 2 already touched (`plugin/jobs.py`).
- **Verification:** `test_job_plan_promote_disables_non_judge_engines_with_the_traits_own_reason`
  asserts apple is excluded from `usable` and its row's label starts with
  `engines.trait('apple', 'unusable')`; `plugin/picker.py`'s own tests
  (`test_plugin_source.py`) are unaffected since the file is untouched.
- **Committed in:** `8e19c36` (Task 2 commit — the fix lives entirely in the PLAN job's data,
  landed there; Task 3's commit records only the two menu slots).

---

**Total deviations:** 1 (a planner read_first assumption that did not match the codebase as it
exists — no scope change, no extra risk). **Impact on plan:** None on the outward contract: every
Task 3 acceptance criterion is satisfied; the fix is a smaller footprint than the plan anticipated,
not a larger one.

## Issues Encountered

None beyond the deviation documented above.

## Outstanding Manual Verification (Process Note)

Task 3's `<verify>` block carries a `<human-check>` (a real-Calibre GUI walkthrough: with a
throwaway library holding a ranked-candidates artifact and one cloud key, click `Adjudicate new
tags`, confirm the engine picker enables only judge-capable engines and disables the rest with
their own reason, run it, untick one verdict in the review, apply, then click `Backfill promoted
tags` and confirm the preview lists the per-book tag additions and the write refreshes those
rows). No Calibre installation is available in this environment, so this step could not be
performed. Every automated `<verify>` (task-level and plan-level) is green, and this plan's D1/D2
coverage entries are marked `human_judgment: true` for exactly this reason.

**Recorded in `.planning/WINDOWS.md`** (`unrun-verify`, phase 02) so it stays visible at ship
time, alongside the five prior open entries from plans 02-01/02-03/02-04/02-05/02-06. Before this
plan is considered production-verified: open a real Calibre with a throwaway library holding a
`classify_newtags_ranked.csv` and at least one usable cloud key, and walk the human-check above.

## Known Stubs

None — every code path is wired to real data (`promote.candidates()`, `promote.backfill_plan()`,
the real `engines.TRAITS`/`PRICING`). No hardcoded/mock values reach production behaviour.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- This is the LAST of the eight planned write-verb plans in this phase (02-01 through 02-07 have
  landed; the phase's own plan count is 8, so one more plan — the remaining scope, if any, or the
  phase-close verification pass — may follow per `.planning/phases/02-write-verbs-on-a-selection/`).
  The full maintenance loop (wrangle → staleness → synopsis → classify → review → promote →
  backfill) now has every step reachable from the toolbar menu.
- `requirements-completed: [WRITE-05]` — declared by this plan alone (confirmed via
  `requirements.ready-ids`; no sibling plan shares this ID), so it marks complete immediately.
- **Blocker (carried forward):** the real-Calibre human-checks for `Re-derive status` (02-01),
  classify's scope/engine pickers (02-03), the Review 1-by-1 control (02-04), `Normalize fields`
  (02-05), `Settle descriptions` (02-06), and now `Adjudicate new tags`/`Backfill promoted tags`
  (02-07) should all be run together before this phase ships — six open `unrun-verify` entries now
  sit in `.planning/WINDOWS.md`.

## Self-Check: PASSED

- `src/scourgify/promote.py`, `tests/test_promote.py`, `plugin/jobs.py`,
  `tests/test_plugin_jobs.py`, `plugin/action.py` — all exist on disk with the expected changes
  (`grep -n "def job_plan_promote\|def job_execute_promote\|def job_plan_backfill\|def job_execute_backfill" plugin/jobs.py`
  confirms all four defined; `grep -c "Adjudicate new tags\|Backfill promoted tags" plugin/action.py`
  confirms both menu slots present).
- Commits `b0dc7e7`, `8e19c36`, `6b52c3f` — all found in `git log --oneline`.
- All task-level `<acceptance_criteria>` re-verified: `inspect.signature(promote.run)` shows
  keyword-only `on_cand`/`stop`; `grep -c 'failure_class('` in `promote.py` is `1`; a `decide`
  driven with `("", "refusal: …")` yields `refusal` from `engines.failure_class`; a bare-string
  `decide` still produces its pre-#73 verdict; `test_wizard_flow.py` passes with no wizard edits
  (PUB-05); `plugin/jobs.py` defines all four job functions; `grep -c "judge=True"` in `jobs.py`
  (excluding comments) is `1`; neither promote job constructs a `_Writer(`; an unticked verdict
  produces no ledger row and the candidate is still returned by `promote.candidates()` afterward;
  the backfill EXECUTE job uses the backfill-shaped `decide` and a falsy return writes nothing;
  `plugin/action.py` adds both menu slots dispatching through `self._run`; `plugin/picker.py`
  imports only `qt.core` (AST-checked, unchanged from prior plans); it still defines exactly one
  review-table class; `test_plugin_source.py` still proves exactly one `ThreadedJob` call site.
- Plan-level `<verification>`: `tests/test_promote.py`, `tests/test_engines.py`,
  `tests/test_plugin_jobs.py`, `tests/test_plugin_source.py` all green; the terminal is unchanged
  (`tests/test_wizard_flow.py`, `tests/test_cli.py` pass without edits, PUB-05); full suite
  (`for t in tests/test_*.py; do uv run "$t" || exit 1; done`) green, no failures. No test in this
  plan makes a real engine call — every engine construction in the new tests is a fake `ask`
  closure or an injected `jobs._engine_ask`, and `engines._post_json` is never reached.
- Manual `<human-check>` NOT run (no Calibre in this environment) — see "Outstanding Manual
  Verification" above; recorded in `.planning/WINDOWS.md`, not silently marked done.

---
*Phase: 02-write-verbs-on-a-selection*
*Completed: 2026-09-09*
