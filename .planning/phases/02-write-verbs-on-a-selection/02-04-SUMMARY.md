---
phase: 02-write-verbs-on-a-selection
plan: 04
subsystem: plugin
tags: [calibre-plugin, decide-seam, review-checklist, picker, ui-checklist, wrangle, synopsis]

# Dependency graph
requires:
  - phase: 02-write-verbs-on-a-selection
    provides: "02-01's Qt-free job layer (plugin/jobs.py) and the ONE verb-parameterised picker; 02-02's write= transport seam across all six producers; 02-03's picker conventions (job-supplied enrichment, no core import in picker.py)"
provides:
  - "The (label, payload) item shape (D-03) at all seven decide= review-checklist sites — staleness.step, synopsis.step, promote.apply_decisions_step, promote.backfill_step, classify.apply_proposal_step, overrides._step_walk, overrides.step_pick — with ui.checklist as the ONE place the shape is normalized"
  - "The last two decide= gaps closed: wrangle.Plan.step(decide=None) and synopsis.Plan.run(..., decide=None) (D-13's per-book synopsis review)"
  - "plugin/picker.py's Review 1-by-1 control (D-02) and the two decide= adapters — checklist_decide / backfill_decide — exported for the later per-verb plans to wire in"
affects: [02-05, 02-06, 02-07, 02-08]

# Actuals (#2632)
actuals:
  tokens: 12688
  tasks: 3
  commits: 2

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "ui.checklist as the ONE item-shape normalization point: it accepts either a plain display
      string or a (label, payload) tuple and reads only the label, so every producer's rendering
      and the (accepted_idx, rejected_idx, action) return contract stay byte-identical whichever
      shape is emitted, and the wizard needed zero edits (PUB-05)."
    - "payload is a plain, JSON-serializable dict: book/title/field/before/after, plus wrangle's
      kind/class computed from the SAME synth_reject() call the reject log uses
      (overrides._edit_payload), so a review item and its eventual reject row can never disagree."
    - "The picker's Review 1-by-1 table captures ticks on the GUI thread at review-close time;
      checklist_decide/backfill_decide read that captured state and return a Qt-free closure —
      safe to hand to a tool function's decide= and invoke later from a job's worker thread."

key-files:
  created: []
  modified:
    - src/scourgify/ui.py
    - src/scourgify/staleness.py
    - src/scourgify/synopsis.py
    - src/scourgify/promote.py
    - src/scourgify/classify.py
    - src/scourgify/overrides.py
    - src/scourgify/wrangle.py
    - plugin/picker.py
    - tests/test_wizard.py
    - tests/test_overrides.py

key-decisions:
  - "synopsis.step gains a blurbs= parameter so the payload's 'before' value comes from the
    caller's already-read self.blurbs (Plan.run) rather than a new ro_connect() read inside
    step() — step() has no library connection of its own and shouldn't grow one."
  - "synopsis.Plan.run's internal review now runs when made is non-empty AND (decide is not None
    OR a.step) — CLI behaviour is unchanged (decide=None + no --step = no review); the plugin can
    request the review by passing decide= without needing --step."
  - "overrides.step_pick's items carry book=None (override lines aren't tied to one book) with
    kind='override'/class='auto' — every line step_pick is ever handed is auto-suppressible by
    construction (build_overrides only populates the auto dict with cls=='auto' rows)."
  - "The picker's Review 1-by-1 control and on_run converge on the SAME callback: apply/all/skip
    all close the dialog and call on_run(carry) exactly like the plain Run path. A later verb
    plan tells the two apart by calling checklist_decide(dlg)/backfill_decide(dlg) on the closed
    (but not destroyed) dialog to read the ticks the reviewer actually left."

patterns-established:
  - "Pattern: (label, payload) pairs at every decide= review-checklist seam, normalized once in
    ui.checklist — see tech-stack.patterns above."
  - "Pattern: the picker's two decide= adapters (checklist-shape vs promote.backfill's distinct
    shape) built as Qt-free closures over GUI-thread-captured state — see tech-stack.patterns."

requirements-completed: [WRITE-01, WRITE-02, WRITE-04, WRITE-05]

coverage:
  - id: D1
    description: "Every review checklist in the core (staleness.step, synopsis.step,
      promote.apply_decisions_step, promote.backfill_step, classify.apply_proposal_step,
      overrides._step_walk, overrides.step_pick) hands its decide callback (label, payload) pairs;
      ui.checklist is the ONE place the shape is normalized and the terminal renders exactly as
      before."
    requirement: "WRITE-01"
    verification:
      - kind: unit
        ref: "tests/test_wizard.py#test_checklist_renders_pairs_and_plain_strings_identically"
        status: pass
      - kind: unit
        ref: "tests/test_wizard.py#test_staleness_step_decide_seam"
        status: pass
      - kind: unit
        ref: "tests/test_wizard.py#test_synopsis_step_decide_seam"
        status: pass
      - kind: unit
        ref: "tests/test_wizard.py#test_apply_decisions_step_decide_seam"
        status: pass
      - kind: unit
        ref: "tests/test_wizard.py#test_backfill_step_decide_seam"
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
        ref: "tests/test_wizard_flow.py (full file — wizard unchanged, no edits made to it)"
        status: pass
    human_judgment: false
  - id: D2
    description: "wrangle.Plan.step and synopsis.Plan.run both gain an injected decide= — the two
      review-checklist sites that previously always hit the interactive terminal. An injected
      decide never requires an interactive terminal and never imports scourgify.ui, proven with
      rich BLOCKED in a subprocess (not merely unimported)."
    requirement: "WRITE-02"
    verification:
      - kind: unit
        ref: "tests/test_wizard.py#test_wrangle_step_and_synopsis_run_carry_decide"
        status: pass
      - kind: unit
        ref: "tests/test_wizard.py#test_wrangle_plan_step_decide_seam_needs_no_rich"
        status: pass
      - kind: unit
        ref: "tests/test_wizard.py#test_synopsis_run_decide_seam_runs_review_without_step"
        status: pass
    human_judgment: false
  - id: D3
    description: "plugin/picker.py's Picker gains a Review 1-by-1 control (D-02): a QTableWidget
      of the PLAN result's items, all pre-ticked, with book/title/field/before/after columns as
      plain text. checklist_decide(dialog) and backfill_decide(dialog) are exported adapters
      producing the two distinct decide= shapes the core exposes."
    requirement: "WRITE-04"
    verification:
      - kind: unit
        ref: "tests/test_plugin_source.py (full file — no core import, one ThreadedJob site, source invariants unchanged)"
        status: pass
      - kind: other
        ref: "python3 -c \"import ast;...\" — plugin/picker.py imports only qt.core"
        status: pass
    human_judgment: true
    rationale: "plugin/*.py cannot be imported or exercised in this CI (no Qt/Calibre installed),
      so the Review 1-by-1 table's actual rendering, tick-toggling, and end-to-end write-narrowing
      cannot be run here. Recorded as an open unrun-verify item in .planning/WINDOWS.md; see
      'Outstanding Manual Verification' below."
duration: 20min
completed: 2026-09-09
status: complete
---

# Phase 2 Plan 4: Review Items Become (label, payload) Pairs, Everywhere Summary

**All seven decide= review-checklist sites now pass `(label, payload)` pairs instead of plain
strings — normalized once in `ui.checklist` — the two remaining `decide=` gaps
(`wrangle.Plan.step`, `synopsis.Plan.run`) are closed, and the picker gets its Review 1-by-1
tick-list with two exported decide= adapters.**

## Performance

- **Duration:** ~20 min
- **Started:** 2026-09-09T10:10:44Z
- **Completed:** 2026-09-09T10:28:32Z
- **Tasks:** 3
- **Files modified:** 10 (2 commits)

## Accomplishments

- `ui.checklist` now accepts EITHER a plain display string or a `(label, payload)` pair per item,
  reading only the label — the ONE normalization point, so the wizard's rendering and its
  `(accepted_idx, rejected_idx, action)` return contract are byte-identical whichever shape a
  producer emits. No edits to the wizard itself (PUB-05); `tests/test_wizard_flow.py` passes
  unchanged.
- All seven decide= sites converted: `staleness.step`, `synopsis.step` (gained a `blurbs=`
  parameter so `before` comes from the caller's already-read description, not a fresh library
  read), `promote.apply_decisions_step` (payload adds `target`/`reason`), `promote.backfill_step`,
  `classify.apply_proposal_step` (one item per proposed tag, `before`/`after` = current tags
  without/with that tag), `overrides._step_walk` (payload's `kind`/`class` computed by the SAME
  `synth_reject()` call the reject log uses, via the new `overrides._edit_payload` helper — the
  two can't drift), and `overrides.step_pick`.
- `wrangle.Plan.step(decide=None)` and `synopsis.Plan.run(..., decide=None)` close the last two
  `decide=` gaps: an injected `decide` never requires an interactive terminal and never imports
  `scourgify.ui` — proven for `wrangle.Plan.step` with rich BLOCKED in a subprocess, mirroring
  `tests/test_plugin_safety.py`'s own technique. `synopsis.Plan.run`'s internal per-book review
  (D-13) now runs when `decide is not None OR --step`, extending CLAUDE.md's 1-by-1 review
  invariant to synopsis; CLI behaviour with neither is unchanged.
- `plugin/picker.py`'s `Picker` gains a second control, "Review 1-by-1" (D-02): a `QTableWidget`
  of the PLAN result's items, ALL pre-ticked (the checkbox state is set from a constant, never a
  filter), rendering book/title/field/before/after as plain text (untrusted FanFicFare data,
  T-02-03/T-02-16). `apply ticked` / `all` / `skip` mirror `ui.checklist`'s own three outcomes.
  `checklist_decide(dialog)` and `backfill_decide(dialog)` are the two exported adapters —
  Qt-free closures over GUI-thread-captured tick state, safe to hand to a tool function's
  `decide=` and invoke later from a worker thread. Wiring them into the actual PLAN/EXECUTE
  dispatch is left to the later per-verb plans (02-05..02-08), per this plan's own scope.

## Task Commits

1. **Tasks 1+2: (label, payload) item shape at all seven decide= sites, plus the two missing
   decide= seams** - `6697bcc` (feat)
2. **Task 3: The picker's Review 1-by-1 control and its two decide= adapters** - `5ad0a03` (feat)

_No plan-metadata commit yet — this SUMMARY and the STATE/ROADMAP/REQUIREMENTS updates are
committed together immediately after this file is written (per the atomic close-out invariant)._

## Files Created/Modified

- `src/scourgify/ui.py` - `checklist` accepts plain strings or `(label, payload)` pairs; reads
  only the label.
- `src/scourgify/staleness.py` - `step` emits pairs.
- `src/scourgify/synopsis.py` - `step` emits pairs and gains `blurbs=`; `Plan.run` gains `decide=`
  threaded to its internal review.
- `src/scourgify/promote.py` - `apply_decisions_step` and `backfill_step` emit pairs.
- `src/scourgify/classify.py` - `apply_proposal_step` emits pairs, one per proposed tag.
- `src/scourgify/overrides.py` - `_step_walk` and `step_pick` emit pairs via the new
  `_edit_payload` helper (also used by `_reject_row`, now built on top of it); fixes a
  pre-existing `only=` narrowing bug in `build_overrides` (see Deviations).
- `src/scourgify/wrangle.py` - `Plan.step` gains `decide=None`, threaded to `_step_walk`.
- `plugin/picker.py` - `Picker`'s Review 1-by-1 control; `checklist_decide`/`backfill_decide`.
- `tests/test_wizard.py` - updated the seven decide-seam tests for the pair shape; new tests for
  the `ui.checklist` round-trip, the wrangle/synopsis `decide=` seams (including the
  rich-blocked subprocess proof), and the synopsis review-without-`--step` behaviour.
- `tests/test_overrides.py` - new tests for `step_pick`'s pair shape + unticked-item-writes-
  nothing (which surfaced the `build_overrides(only=...)` bug) and `_edit_payload`/`_reject_row`
  agreement.

## Decisions Made

See `key-decisions` in the frontmatter — the `synopsis.step` `blurbs=` parameter, `Plan.run`'s
review-trigger condition, `step_pick`'s book=None/class='auto' payload shape, and the picker's
`on_run`/adapter convergence design.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `overrides.build_overrides(only=...)` raised `UnboundLocalError`**
- **Found during:** Task 2, writing the new `step_pick` unticked-item test
  (`tests/test_overrides.py::test_step_pick_decide_seam_pairs_and_unticked_item_writes_nothing`)
- **Issue:** `auto = {fn: [l for l in lines if (fn, l) in only] for fn in auto}` referenced an
  undefined `lines` variable — a real bug, not a test-authoring gap. Every call site that narrows
  a 1-by-1 override review (`overrides --apply --step` and the wizard's `stage_overrides`) would
  crash the instant an item was left ticked, since `only` is only ever passed non-`None` from
  that path. No prior test exercised `only=` at all.
- **Fix:** `auto = {fn: [l for l in auto[fn] if (fn, l) in only] for fn in auto}` — read from the
  dict being narrowed, not an undefined name.
- **Files modified:** `src/scourgify/overrides.py`
- **Verification:** `tests/test_overrides.py::test_step_pick_decide_seam_pairs_and_unticked_item_writes_nothing`
  passes; confirms an unticked line is excluded from both the returned plan and the file actually
  written.
- **Committed in:** `6697bcc` (Tasks 1+2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug). **Impact on plan:** Necessary for this plan's own
Task 2 acceptance criterion ("an unticked item produces no override line") to be provably true;
no scope creep — the fix is a one-line correction inside a file this plan already modifies.

## Issues Encountered

None beyond the deviation documented above.

## Outstanding Manual Verification (Process Note)

Task 3's `<verify>` block carries a `<human-check>` (a real-Calibre GUI walkthrough: `Re-derive
status` on 5 books, click Review 1-by-1, untick two rows, apply, confirm three written and two
unchanged). No Calibre installation is available in this environment, so this step could not be
performed; every automated `<verify>` (task-level and plan-level) is green, and this plan's D3
coverage entry is marked `human_judgment: true` for exactly this reason.

**Recorded in `.planning/WINDOWS.md`** (`unrun-verify`, phase 02, entry id 3) so it stays visible
at ship time. Also note: wiring the Review 1-by-1 control's ticks all the way through to an actual
narrowed write (via `checklist_decide`/`backfill_decide` feeding a verb's `decide=`) is explicitly
the LATER per-verb plans' job (02-05..02-08) — this plan built the picker control and the two
adapters in isolation, matching its own file scope (`plugin/picker.py` only).

## Known Stubs

None — every code path is wired to real data; the picker's adapters operate on the real PLAN
result's items, never a hardcoded/mock value.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Every review checklist in the core is now interceptable by an injected `decide=` and emits
  structured `(label, payload)` items — the shape Phase 4's History and Phase 5's review panel
  will read. `checklist_decide`/`backfill_decide` are ready for 02-05..02-08 to wire into their
  own verb's PLAN/EXECUTE round trip.
- `requirements-completed: [WRITE-01, WRITE-02, WRITE-04, WRITE-05]` — declared only by this plan
  (confirmed via `requirements.ready-ids`; no sibling plan shares these IDs), so they mark
  complete immediately.
- **Blocker (carried forward):** the real-Calibre human-checks for `Re-derive status` (02-01),
  classify's scope dialog/engine picker (02-03), and this plan's Review 1-by-1 control (02-04)
  should all be run together before this phase ships — three open `unrun-verify` entries now sit
  in `.planning/WINDOWS.md`.

## Self-Check: PASSED

- `src/scourgify/ui.py`, `staleness.py`, `synopsis.py`, `promote.py`, `classify.py`,
  `overrides.py`, `wrangle.py`, `plugin/picker.py`, `tests/test_wizard.py`,
  `tests/test_overrides.py` — all exist on disk with the expected changes.
- Commits `6697bcc`, `5ad0a03` — both found in `git log --oneline`.
- All task-level `<acceptance_criteria>` re-verified: `ui.checklist` round-trips both item shapes;
  each of the seven seams passes pairs (`isinstance` proven via tuple-destructuring in each test);
  every payload survives `json.dumps`; `inspect.signature` shows `decide` on both
  `wrangle.Plan.step` and `synopsis.Plan.run`; `grep -c 'decide=decide'` in `wrangle.py` >= 1;
  `wrangle.Plan.step(decide=<recorder>)` completes with rich blocked in a subprocess;
  `synopsis.Plan.run(decide=<recorder>)` runs the review without `--step`, and `Plan.run()` with
  neither runs no review; `plugin/picker.py` defines `checklist_decide`/`backfill_decide` and
  imports only `qt.core`; the review table's ticks are set from a constant; `checklist_decide`'s
  index lists partition `range(len(items))` by single-pass construction.
- Plan-level `<verification>`: `tests/test_wizard.py`, `tests/test_wizard_flow.py`,
  `tests/test_cli.py`, `tests/test_promote.py`, `tests/test_overrides.py`,
  `tests/test_synopsis.py`, `tests/test_plugin_safety.py`, `tests/test_plugin_source.py` all
  green; full suite (`for t in tests/test_*.py; do uv run "$t" || exit 1; done`) green, no
  failures.
- Manual `<human-check>` NOT run (no Calibre in this environment) — see "Outstanding Manual
  Verification" above; recorded in `.planning/WINDOWS.md`, not silently marked done.

---
*Phase: 02-write-verbs-on-a-selection*
*Completed: 2026-09-09*
