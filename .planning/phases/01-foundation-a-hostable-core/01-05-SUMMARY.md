---
phase: 01-foundation-a-hostable-core
plan: 05
subsystem: cli
tags: [wizard, refactor, injection-seam, calibre-plugin-prep]

# Dependency graph
requires:
  - phase: 01-03
    provides: "engines.TRAITS platforms row and engines._shipped_dir() — engine_options/engine_rows/default_engine_id are added beside them without duplicating the platform-gate concept"
  - phase: 01-04
    provides: "common._write_run()/WriteResult — unrelated to this plan's write paths, but the shared baseline this plan builds beside in the same phase"
provides:
  - "classify.scope_options / classify.proposal_options — pure classify-scope and proposal-menu builders, relocated out of wizard.py"
  - "engines.engine_options / engines.default_engine_id / engines.engine_rows — pure engine-menu builders, relocated out of wizard.py; engine_options takes an injected cost_fn so engines.py never imports classify"
  - "synopsis.options — pure synopsis-menu builder, relocated out of wizard.py"
  - "decide=None keyword on classify.apply_proposal_step, overrides._step_walk, overrides.step_pick, promote.apply_decisions_step, promote.backfill_step, staleness.step, synopsis.step — an injectable review-checklist callback defaulting to ui.checklist, with the ui import moved behind that default"
affects: [phase-2-plan-job-and-qt-picker]

# Actuals (#2632)
actuals:
  tokens: 13143
  tasks: 2
  commits: 2

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Verbatim function relocation out of an interactive shell into the tool module that owns the domain — a phase-1 prerequisite for phase 2's PLAN-job/Qt-picker split, which can only consume a pure function from a tool module, never a private wizard helper"
    - "Cycle-breaking by injection: engine_options(engs, n_todo, cost_fn) takes the per-book cost as a parameter instead of importing classify.est_cost, because classify.py already imports engines and the reverse edge would be a cycle"
    - "decide=None optional-callback seam (generalizing the existing Plan.run(ask=)/promote.backfill(decide=) shape) on every ui.checklist call site, with the lazy `from scourgify import ui` moved behind the None-check so an injected decide never imports the interactive module"

key-files:
  modified:
    - src/scourgify/wizard.py
    - src/scourgify/classify.py
    - src/scourgify/engines.py
    - src/scourgify/synopsis.py
    - src/scourgify/promote.py
    - src/scourgify/overrides.py
    - src/scourgify/staleness.py
    - tests/test_wizard.py

key-decisions:
  - "engines.engine_options gained a THIRD, injected cost_fn parameter (engines.engine_options(engs, n_todo, cost_fn)) rather than importing classify.est_cost directly — classify.py already does `from scourgify import engines as engines_mod`, so the reverse edge would be an import cycle. wizard._ask_engine now calls engines.engine_options(engs, n_todo, classify.est_cost), keeping D-10's locked module placement while breaking the cycle at the one call site where both domains legitimately meet."
  - "No compatibility alias was kept in wizard.py for any of the six relocated names (D-10, orchestrator-resolved) — an import of an old private name now fails loudly. tests/test_wizard.py's ~9 call sites were repointed in the same commit as the relocation, per the plan's explicit instruction."
  - "Every decide=None site keeps its pre-existing ui.interactive() guard (where one existed) INSIDE the `if decide is None:` branch, not removed — an injected decide bypasses the interactive-terminal requirement entirely (it has its own caller-supplied answering logic), while the default path is unchanged from before this plan."

requirements-completed: [FOUND-06]

coverage:
  - id: D1
    description: "The six pure builders (classify.scope_options, classify.proposal_options, engines.engine_options, engines.default_engine_id, engines.engine_rows, synopsis.options) live in their owning tool modules; wizard.py imports them and keeps no private copy or compatibility alias"
    requirement: "FOUND-06"
    verification:
      - kind: unit
        ref: "tests/test_wizard.py#test_scope_options_slots_are_stable_in_every_library_state"
        status: pass
      - kind: unit
        ref: "tests/test_wizard.py#test_scope_options_last_row_is_always_offered"
        status: pass
      - kind: unit
        ref: "tests/test_wizard.py#test_scope_options_default_follows_availability"
        status: pass
      - kind: unit
        ref: "tests/test_wizard.py#test_scope_options_empty_library_greys_last_row_and_defaults_whole_library"
        status: pass
      - kind: unit
        ref: "tests/test_wizard.py#test_engine_options_price_the_billed_set"
        status: pass
      - kind: unit
        ref: "tests/test_wizard.py#test_engines_cloud_usable_iff_key_in_env"
        status: pass
      - kind: unit
        ref: "tests/test_wizard.py#test_engine_rows_env_empty_returns_all_unusable_with_hints"
        status: pass
      - kind: unit
        ref: "tests/test_wizard.py#test_default_engine_id_judge_prefers_first_judge_capable"
        status: pass
      - kind: unit
        ref: "tests/test_wizard.py#test_default_engine_id_never_defaults_to_an_unusable_engine"
        status: pass
      - kind: unit
        ref: "tests/test_wizard.py#test_proposal_menu_has_one_slot_layout_tagged_or_not"
        status: pass
      - kind: unit
        ref: "tests/test_wizard.py#test_synopsis_options_slot_layout_stable_with_and_without_a_queue"
        status: pass
    human_judgment: false
  - id: D2
    description: "engines.py gains no import of classify (at any nesting level); engine_options takes an injected cost_fn instead, so the relocation adds no engines->classify edge to a graph where classify already imports engines"
    requirement: "FOUND-06"
    verification:
      - kind: unit
        ref: "tests/test_wizard.py#test_engines_does_not_import_classify"
        status: pass
      - kind: unit
        ref: "tests/test_wizard.py#test_no_relocated_builder_introduces_an_import_cycle"
        status: pass
      - kind: unit
        ref: "tests/test_plugin_safety.py#test_every_core_module_imports_without_rich"
        status: pass
    human_judgment: false
  - id: D3
    description: "All seven ui.checklist call sites (classify.apply_proposal_step, overrides._step_walk, overrides.step_pick, promote.apply_decisions_step, promote.backfill_step, staleness.step, synopsis.step) take decide=None defaulting to ui.checklist, with the lazy ui import moved behind that default"
    requirement: "FOUND-06"
    verification:
      - kind: unit
        ref: "tests/test_wizard.py#test_all_seven_decide_sites_carry_the_parameter"
        status: pass
      - kind: unit
        ref: "tests/test_wizard.py#test_apply_proposal_step_decide_seam"
        status: pass
      - kind: unit
        ref: "tests/test_wizard.py#test_step_walk_decide_seam"
        status: pass
      - kind: unit
        ref: "tests/test_wizard.py#test_step_pick_decide_seam"
        status: pass
      - kind: unit
        ref: "tests/test_wizard.py#test_apply_decisions_step_decide_seam"
        status: pass
      - kind: unit
        ref: "tests/test_wizard.py#test_backfill_step_decide_seam"
        status: pass
      - kind: unit
        ref: "tests/test_wizard.py#test_staleness_step_decide_seam"
        status: pass
      - kind: unit
        ref: "tests/test_wizard.py#test_synopsis_step_decide_seam"
        status: pass
    human_judgment: false
  - id: D4
    description: "tests/test_wizard_flow.py and tests/test_cli.py pass completely unchanged (byte-identical diff) — the wizard's observable behaviour, menu slots and source-level bans survived the relocation (ROADMAP success criterion 4)"
    requirement: "FOUND-06"
    verification:
      - kind: unit
        ref: "tests/test_wizard_flow.py (all 15 tests, unedited)"
        status: pass
      - kind: unit
        ref: "tests/test_cli.py#test_the_wizard_asks_but_never_does_the_work"
        status: pass
      - kind: other
        ref: "git diff --stat tests/test_wizard_flow.py tests/test_cli.py (empty)"
        status: pass
    human_judgment: false
duration: ~40min
completed: 2026-09-07
status: complete
---

# Phase 1 Plan 5: Foundation — a hostable core Summary

**Six pure wizard-menu builders (classify/engines/synopsis scope, proposal, engine and synopsis options) relocated verbatim into the tool modules that own their domain, and all seven `ui.checklist` review sites gained an injectable `decide=` callback with the interactive import moved behind it — the two seams phase 2's PLAN-job/Qt-picker split needs.**

## Performance

- **Duration:** ~40 min
- **Tasks:** 2 of 2 completed
- **Files modified:** 8 (7 production modules, 1 test file)
- **Commits:** 2

## Accomplishments

- `classify.scope_options(ch, total, outstanding)` and `classify.proposal_options(n_rows, n_tagged)` moved verbatim out of `wizard._scope_options`/`wizard._proposal_options` (D-10): same fixed-slot layout, same greyed-row rule, same default logic. `classify.scope_options({}, 0, 0)` returns the 'most recent N' row greyed with a `None` id and defaults to the whole-library scope.
- `engines.engine_rows(env=None)` and `engines.default_engine_id(opts, judge=, usable=)` moved verbatim out of `wizard._engines`/`wizard._default_engine_id`. `engines.engine_rows(env={})` returns one row per engine in `engines.ENGINES`, in order, none usable, each carrying a non-empty hint.
- `engines.engine_options(engs, n_todo, cost_fn)` moved out of `wizard._engine_options` with ONE signature change: the per-book cost function is now an injected third parameter instead of a direct call to `classify.est_cost`. `classify.py` already does `from scourgify import engines as engines_mod`, so a verbatim move would have created an `engines -> classify` import cycle; the injection breaks it while keeping D-10's locked module placement. `wizard._ask_engine` now calls `engines.engine_options(engs, n_todo, classify.est_cost)` — the one call site where both domains legitimately meet. An AST walk of `engines.py` confirms it imports nothing named `classify` at any nesting level, and `engine_options`' signature carries `cost_fn`.
- `synopsis.options(n)` moved verbatim out of `wizard._synopsis_options` — the same three-row layout (settle / review 1-by-1 / skip) whether or not the queue holds books.
- `wizard.py` keeps NO private copy and NO compatibility alias for any of the six relocated names — an import of an old private name now fails loudly, per the orchestrator-resolved decision for this phase. `wizard.py`'s five call sites (`stage_synopsis`, `_ask_engine`, `stage_classify`, `_do_proposal`'s two callers via `stage_review`) were repointed to the new owning-module names in the same commit.
- `tests/test_wizard.py`'s ~9 direct call sites of the old private names were repointed to `classify.*`/`engines.*`/`synopsis.*` in the relocation commit, alongside three new tests the relocation makes possible in their proper homes: an explicit empty-scope/empty-engine-env assertion, `test_engines_does_not_import_classify` (an AST walk of `engines.py` plus a `cost_fn`-in-signature check), and `test_no_relocated_builder_introduces_an_import_cycle` (a fresh child-process import of each owning module ALONE, the order the wizard's own combined import list would otherwise mask).
- All seven review-checklist functions — `classify.apply_proposal_step`, `overrides._step_walk`, `overrides.step_pick`, `promote.apply_decisions_step`, `promote.backfill_step`, `staleness.step`, `synopsis.step` — gained an optional keyword-only `decide=None`, defaulting to `ui.checklist`, following the exact optional-callback shape `wrangle.Plan.run(ask=)` and `promote.backfill(decide=)` already use. At every site, the lazy `from scourgify import ui` moved BEHIND the `decide is None` check, so a front door injecting its own callback never imports the interactive module — the property phase 2's PLAN job and its Qt picker both need, and the property that keeps every one of these modules importable with rich blocked.
- Each site's pre-existing `ui.interactive()` guard (where one existed — `apply_proposal_step`, `step_pick`, `apply_decisions_step`, `staleness.step`, `synopsis.step`) stayed INSIDE the `decide is None` branch: an injected `decide` has its own caller-supplied answering logic and bypasses the interactive-terminal requirement entirely, exactly as `promote.backfill(decide=)` already does today. `overrides._step_walk` and `promote.backfill_step` never had their own interactive check (the callers — `wrangle.Plan.step()` and `promote.backfill()` respectively — already gate it), so neither gained one.
- `tests/test_wizard.py` gained one stub-`decide` test per family (7 new tests + a signature-presence check), each proving: (a) the function's observable effect matches the stub's answer (a tag gets applied, a status change is accepted, a book is skipped, etc.), (b) the recorded items the stub was handed are exactly the display strings `ui.checklist` would have received (computed via the same helper functions — `overrides._edit_label`, `staleness.status_line`, `promote.verdict_line` — rather than hardcoded, so a format drift fails the right test), and (c) `sys.modules` gained no `scourgify.ui` entry as a result of the call — verified by popping it first and restoring it in a `finally`, per Codex's plan-05 LOW review note (a bare assertion without the pop-first fixture would pass vacuously or fail randomly depending on what an earlier test already imported).
- `tests/test_wizard_flow.py` and `tests/test_cli.py` pass completely unchanged — `git diff --stat` on both is empty — the proof that the wizard's behaviour, its menu slots, and its source-level bans (`ui.checklist`, `run_writer(`, `op_set_field(` absent from `wizard.py`'s body) all survived the relocation intact.

## Task Commits

Each task was committed atomically:

1. **Task 1: Relocate the six pure builders to their owning modules and repoint their tests** - `4d50f6f` (feat)
2. **Task 2: A decide= seam on all seven review-checklist call sites** - `67e2289` (feat)

_No plan metadata commit yet — this SUMMARY.md is committed separately per the executor protocol._

## Files Created/Modified

- `src/scourgify/wizard.py` - the six relocated functions removed (no alias); five call sites repointed to `classify.*`/`engines.*`/`synopsis.*`
- `src/scourgify/classify.py` - `scope_options()`, `proposal_options()` (new, relocated); `apply_proposal_step(decide=None)`
- `src/scourgify/engines.py` - `engine_rows()`, `default_engine_id()`, `engine_options(engs, n_todo, cost_fn)` (new, relocated)
- `src/scourgify/synopsis.py` - `options()` (new, relocated); `step(made, titles, decide=None)`
- `src/scourgify/promote.py` - `apply_decisions_step(review_path=None, decide=None)`, `backfill_step(chg, adds, titles, decide=None)`
- `src/scourgify/overrides.py` - `_step_walk(..., decide=None)`, `step_pick(auto, decide=None)`
- `src/scourgify/staleness.py` - `step(status_label, rows, decide=None)`
- `tests/test_wizard.py` - repointed relocation call sites + 13 new tests (order/slot/empty-scope assertions, engines-does-not-import-classify, import-cycle probe) + 8 new decide-seam tests (7 stub-decide tests + 1 signature-presence check)

## Decisions Made

- **`engines.engine_options` takes an injected `cost_fn` rather than importing `classify.est_cost`.** `classify.py` already does `from scourgify import engines as engines_mod`, so a verbatim body move into `engines.py` would have created an `engines -> classify` import cycle (Codex agreed concern 5, MEDIUM). D-10 locks the module placement, so the fix is injection: `engine_options(engs, n_todo, cost_fn)`, called as `engines.engine_options(engs, n_todo, classify.est_cost)` from `wizard._ask_engine` — the one place both domains legitimately meet.
- **No compatibility alias for any of the six relocated names.** The orchestrator-resolved decision for this phase: an import of `wizard._scope_options` (etc.) now raises `AttributeError` rather than quietly working through a second name. `tests/test_wizard.py`'s call sites were repointed in the same commit as the relocation, bounding the churn Codex flagged as expected (the six names are private and had no callers outside `wizard.py` and that test file).
- **Every `decide=None` site keeps its own pre-existing interactive guard inside the `if decide is None:` branch**, rather than hoisting it above the check or dropping it. An injected `decide` supplies its own answering logic (it may not even be interactive — a PLAN-job callback records and answers "skip" unconditionally), so requiring a TTY for it would be wrong; the unchanged default path preserves today's CLI/wizard behaviour exactly.
- **`overrides._step_walk` and `promote.backfill_step` gained `decide=None` with no interactive guard of their own** — their callers (`wrangle.Plan.step()`, `promote.backfill()`) already gate interactivity before reaching them, and adding a redundant check inside would duplicate that responsibility without changing behaviour.

## Deviations from Plan

None - plan executed exactly as written, including the one signature change (`cost_fn`) the plan itself specified in advance to break the `engines -> classify` cycle.

## Issues Encountered

None.

## Threat Flags

None. Every new surface this plan touches (the `decide=` injection point, the relocated builders' new homes) is exactly what this plan's own `threat_model` (T-01-17, T-01-18, T-01-19, T-01-27) already covers, and no new network endpoint, auth path, file-access pattern, or schema change was introduced.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- The six relocated builders and the seven `decide=`-carrying functions are now exactly the pure, tool-owned source phase 2's PLAN job and Qt picker were designed to consume — no further relocation work is needed before that phase begins.
- `wizard.py` is a pure consumer of `classify`/`engines`/`synopsis` for these six computations; no private copy remains to drift out of sync with a future change to any of them.
- Full local suite green in isolation: `env -u CALIBRE_LIBRARY SCOURGIFY_HOME=$(mktemp -d) bash -c 'for t in tests/test_*.py; do uv run "$t" || exit 1; done'`.
- `git diff --stat tests/test_wizard_flow.py tests/test_cli.py` is empty — both files verified byte-unchanged across both commits.
- No blockers for the next plan in this phase.

## Self-Check: PASSED

- `src/scourgify/wizard.py`, `src/scourgify/classify.py`, `src/scourgify/engines.py`, `src/scourgify/synopsis.py`, `src/scourgify/promote.py`, `src/scourgify/overrides.py`, `src/scourgify/staleness.py`, `tests/test_wizard.py` — all confirmed present on disk with the expected new/changed symbols.
- Both task commits (`4d50f6f`, `67e2289`) confirmed present in `git log`.
- `uv run tests/test_wizard.py` — 26/26 pass. `uv run tests/test_wizard_flow.py` — 15/15 pass. `uv run tests/test_cli.py` — passes (incl. `test_the_wizard_asks_but_never_does_the_work`). `uv run tests/test_plugin_safety.py` — 11/11 pass.
- Full suite green: `env -u CALIBRE_LIBRARY SCOURGIFY_HOME=$(mktemp -d) bash -c 'for t in tests/test_*.py; do uv run "$t" || exit 1; done'` exits 0.
- `git diff --stat tests/test_wizard_flow.py tests/test_cli.py` — empty, confirmed after both commits.
- `uv run python -c "from scourgify import wizard; print(any(hasattr(wizard,n) for n in ('_scope_options','_proposal_options','_engine_options','_default_engine_id','_engines','_synopsis_options')))"` prints `False`.
- An AST walk of `src/scourgify/engines.py` finds no import of `classify` at any nesting level; `cost_fn` confirmed in `engine_options`'s signature.

---
*Phase: 01-foundation-a-hostable-core*
*Completed: 2026-09-07*
