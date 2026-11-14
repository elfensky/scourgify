---
phase: 02-write-verbs-on-a-selection
plan: 06
subsystem: plugin
tags: [calibre-plugin, synopsis, engines, engine-picker, decide-seam, review-checklist, write-path]

# Dependency graph
requires:
  - phase: 02-write-verbs-on-a-selection
    provides: "02-02's write= transport seam already on synopsis.Plan.run; 02-03's engine-construction/pricing/missing-column conventions (jobs._engine_ask, jobs._require_column, engines.engine_options); 02-04's (label, payload) item shape and synopsis.Plan.run's own decide= seam (D-13); 02-05's _record_decide/_replay_decide record-and-replay helpers and the PLAN/EXECUTE job pattern"
provides:
  - "synopsis.Plan.run(on_book=, stop=) and Plan.cancelled — the progress/abort seam every other engine pass in this phase already has, applied to the last one that needed it"
  - "jobs.job_plan_synopsis / jobs.job_execute_synopsis — the synopsis verb's PLAN job (prices every usable engine over the resolved todo set, sends nothing) and a TWO-DISPATCH EXECUTE job (harvest generated descriptions as review items with a write that touches nothing, then replay the reviewer's ticks and write for real) — the shape D-13's per-book review needs when the review items are GENERATED text that doesn't exist until the engine has run"
  - "jobs._NullWriter / jobs._synopsis_cost — the harvest dispatch's no-op write= transport, and a deliberately PESSIMISTIC (never-under-quote) per-book cost estimate for the engine picker"
  - "picker.RefusalDialog / picker.show_refusal — the refused-result rendering with an optional, explicitly-labelled degraded-mode control, reused nowhere else in this phase but built generically enough to be"
  - "The live 'Settle descriptions' menu slot: PLAN -> engine picker -> EXECUTE (harvest, no write) -> the existing Picker/Review-1-by-1 table -> EXECUTE (write, replaying the reviewer's ticks) -> the shared result dialog"
affects: []

# Actuals (#2632)
actuals:
  tokens: 10400
  tasks: 3
  commits: 3

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Two dispatches sharing ONE EXECUTE job function, distinguished by a `ticks` argument that
      is `None` (harvest: run the engine pass for real, write nothing, return the generated
      descriptions as review items) or a list (replay: re-run the SAME pass, write for real,
      narrowed to exactly the reviewer's ticks) — the shape a per-book review needs when the
      review items are ENGINE OUTPUT that does not exist until the pass has actually run, unlike
      every other verb's PLAN job, which harvests items for free before any dialog opens."
    - "A pessimistic (never-under-quote) per-book cost estimate for the engine picker
      (jobs._synopsis_cost): every book is priced as the FULL generation path (one judge call +
      MAX_CHUNKS note calls + one back-cover call), since whether a book's existing blurb turns
      out to be adequate is not knowable before the judge call runs — the same lesson CLAUDE.md
      already records for classify.est_cost's out_tokens fix, applied to a pass with no
      per-book-split cost model of its own."

key-files:
  created: []
  modified:
    - src/scourgify/synopsis.py
    - tests/test_synopsis.py
    - plugin/jobs.py
    - tests/test_plugin_jobs.py
    - plugin/action.py
    - plugin/picker.py

key-decisions:
  - "The second EXECUTE dispatch RE-RUNS the full engine pass rather than caching the harvest
    dispatch's generated text — synopsis.Plan carries no cross-dispatch state, and caching would
    mean growing `carry` with the actual generated prose (JSON-serialized, per the plan/execute
    result contract) or duplicating Plan.run's post-generation write logic in the plugin, which
    CLAUDE.md's 'the wizard ASKS; the tool modules DO' rule exists to prevent. This is the same
    'recompute rather than carry a result forward' choice job_execute_wrangle already makes for
    its own deterministic pass, generalised to a non-deterministic one: an engine's answer for
    the SAME prompt can in principle differ between the two runs, so replaying `ticks` by
    POSITION assumes the same set of generated books reappears in the same order. True in
    practice for a fixed judge/generation prompt against unchanged text (and exactly what every
    test here exercises with a deterministic FakeAsk); batch size — not a cache — is what keeps a
    real review session small and low-risk, matching the plan's own stated mitigation."
  - "picker.RefusalDialog/show_refusal is NEW, not an extension of pre-existing picker.py
    rendering — see Deviations. Built generically (any refusal + optional degraded control) but
    wired only at synopsis's PLAN-refusal call site in this plan; every other verb's PLAN refusal
    still renders via the existing plain info_dialog, to keep this plan's footprint scoped to
    what WRITE-04 actually needs."
  - "jobs._synopsis_cost is a standalone (n_todo, engine) -> float function, not a closure over
    the resolved Plan — engines.engine_options's cost_fn contract takes no per-book knowledge, and
    a pessimistic worst-case-per-book estimate needs none: every book is priced as the full
    generation path regardless of whether its existing blurb might turn out adequate."

patterns-established:
  - "Pattern: two dispatches sharing one EXECUTE job function, keyed on a ticks=None/list
    argument — for the one write verb in this phase whose review items are generated engine
    output rather than a free-to-compute diff. See tech-stack.patterns above."

requirements-completed: [WRITE-04]

coverage:
  - id: D1
    description: "The engine picker prices every usable engine over the exact resolved todo set
      (never the selection size), with the on-device engine pre-selected as default_engine_id's
      answer where the platform has one, using the same never-under-quote cost-estimate
      discipline classify.est_cost's out_tokens fix established."
    requirement: "WRITE-04"
    verification:
      - kind: unit
        ref: "tests/test_plugin_jobs.py#test_job_plan_synopsis_prices_every_usable_engine_over_the_resolved_todo_set"
        status: pass
      - kind: unit
        ref: "tests/test_plugin_jobs.py#test_job_plan_synopsis_sends_nothing_to_an_engine"
        status: pass
    human_judgment: false
  - id: D2
    description: "The synopsis verb refuses to start while FanFicFare's Comments New Only switch
      is off, naming that as the reason, and offers the degraded self-healing mode only as an
      explicit, labelled control the user clicks — never a default, never silent."
    requirement: "WRITE-04"
    verification:
      - kind: unit
        ref: "tests/test_plugin_jobs.py#test_job_plan_synopsis_refuses_when_fanficfare_would_clobber_the_synopsis"
        status: pass
      - kind: unit
        ref: "tests/test_plugin_jobs.py#test_job_plan_synopsis_proceeds_with_force_true"
        status: pass
    human_judgment: true
    rationale: "The picker-side rendering of the degraded-mode control (picker.RefusalDialog) has
      no Calibre/Qt in CI and was not exercised end-to-end — recorded as WINDOWS.md unrun-verify
      entry 5. Every layer below the Qt widget (the job's refusal shape, the degraded re-dispatch
      path) is unit-tested and green."
  - id: D3
    description: "A synopsis run on a library that has never had the #synopsized column is
      refused with a GuardrailError-derived clean result naming `scourgify setup` as the fix,
      before any op reaches the write path — both at PLAN and at EXECUTE."
    requirement: "WRITE-04"
    verification:
      - kind: unit
        ref: "tests/test_plugin_jobs.py#test_job_plan_synopsis_refuses_cleanly_without_the_synopsized_column"
        status: pass
      - kind: unit
        ref: "tests/test_plugin_jobs.py#test_a_missing_synopsized_column_refuses_execute_too"
        status: pass
    human_judgment: false
  - id: D4
    description: "Every generated description passes a per-book review before it is written
      (D-13): an unticked book receives neither its generated description nor its #synopsized
      stamp, so it stays in the queue — the review reuses the SAME Picker/Review-1-by-1 table and
      checklist_decide adapter every other verb uses."
    requirement: "WRITE-04"
    verification:
      - kind: unit
        ref: "tests/test_plugin_jobs.py#test_job_execute_synopsis_first_dispatch_harvests_review_items_and_writes_nothing"
        status: pass
      - kind: unit
        ref: "tests/test_plugin_jobs.py#test_job_execute_synopsis_second_dispatch_writes_only_the_ticked_books"
        status: pass
    human_judgment: true
    rationale: "The Qt layer itself (the picker's Review 1-by-1 table populated from a real
      job's items, tick-toggling, the two-dispatch round trip through Calibre's own job list) has
      no Calibre/GUI in CI and was not run against a real Calibre — WINDOWS.md unrun-verify entry
      5. Every layer below the Qt widgets (both job bodies, the record/replay decide= seam) is
      unit-tested and green."
  - id: D5
    description: "A running synopsis reports (fraction, message) tuples into Calibre's own job
      list and stops between books when the job is aborted; the partial run's settled books are
      still written and its failures are still recorded, so the queue stays finite."
    requirement: "WRITE-04"
    verification:
      - kind: unit
        ref: "tests/test_synopsis.py#test_run_reports_progress_and_stops_between_books"
        status: pass
      - kind: unit
        ref: "tests/test_synopsis.py#test_run_with_neither_on_book_nor_stop_is_unchanged"
        status: pass
    human_judgment: false
  - id: D6
    description: "A book whose existing blurb the pass judges adequate is kept untouched (its
      comments field never rewritten) and is still stamped #synopsized alongside any generated
      book in the same run."
    requirement: "WRITE-04"
    verification:
      - kind: unit
        ref: "tests/test_plugin_jobs.py#test_job_execute_synopsis_a_book_with_an_adequate_blurb_is_stamped_and_kept_untouched"
        status: pass
    human_judgment: false
  - id: D7
    description: "'Settle descriptions' is a live menu slot after 'Re-derive status', greyed on
      an empty selection, a running write, or (once a completed PLAN job has reported it) the
      FanFicFare reason — cleared automatically once a later PLAN succeeds, no Calibre restart
      needed."
    verification: []
    human_judgment: true
    rationale: "Qt-only rendering (menu build, greying, tooltip text) has no Calibre/GUI in CI —
      proven only by AST/source checks (test_plugin_source.py) that the slot exists, dispatches
      through self._run, and the module holds zero core imports. The real menu-greying behaviour
      is part of WINDOWS.md unrun-verify entry 5."

duration: 55min
completed: 2026-09-09
status: complete
---

# Phase 2 Plan 6: Synopsis — The Engine Picker, the FanFicFare Guard as a Choice, and Per-Book Review Summary

**`Settle descriptions` runs end-to-end from the toolbar — an engine picker priced over the exact
resolved todo set, the FanFicFare Comments guard rendered as a refusal with an explicit,
labelled degraded-mode control, and a two-dispatch EXECUTE job (`ticks=None` harvests generated
descriptions as review items with a write that touches nothing; `ticks=[...]` replays the
reviewer's ticks and writes for real) feeding the same Picker/Review-1-by-1 table every other
write verb in this phase already uses.**

## Performance

- **Duration:** ~55 min
- **Started:** 2026-09-09T13:00:00Z (approx.)
- **Completed:** 2026-09-09T13:16:37Z
- **Tasks:** 3
- **Files modified:** 6 (3 commits)

## Accomplishments

- `synopsis.Plan.run(ask=None, *, write=None, decide=None, on_book=None, stop=None)` gains the
  progress/abort seam every other engine pass in this phase already had (`classify.Plan.run`,
  plan 02-03): `stop()` is checked at the head of each loop iteration, sets `self.cancelled`, and
  a cancelled sweep still writes whatever it settled and still rewrites the failure log — the
  queue's three exits stay finite even mid-abort.
- `jobs.job_plan_synopsis`: resolves the selection through `synopsis.plan()` (which runs
  `guard_comments` in its own constructor), refuses cleanly if `#synopsized` doesn't exist yet
  (`_require_column`, the same pre-flight classify uses), and — the load-bearing behaviour —
  converts the FanFicFare guard's `GuardrailError` into a refused result carrying
  `degraded_available: True` plus the guard's own remedy sentence, so the picker can offer
  `--force`'s degraded self-healing mode as a control the user actually clicks. Prices every
  usable engine over the exact resolved `todo` set via a deliberately pessimistic
  never-under-quote cost estimate (`jobs._synopsis_cost`) and sends nothing to an engine — this
  step is genuinely free, unlike classify's own PLAN job.
- `jobs.job_execute_synopsis`: ONE function, two dispatches, distinguished by `ticks`. First
  (`ticks=None`): runs the real engine pass through a `_NullWriter` that never touches the
  library, harvesting the generated descriptions as `(label, payload)` review items via
  `_record_decide()` — nothing is written. Second (`ticks` a list): re-runs the same pass with the
  real in-process writer and `decide=_replay_decide(ticks)`, narrowing the write to exactly the
  reviewer's ticks. An unticked book receives neither its description nor its `#synopsized`
  stamp. The second dispatch deliberately RE-RUNS the engine rather than caching the first
  dispatch's output — see Decisions Made.
- `picker.RefusalDialog` / `picker.show_refusal`: the refused-result rendering — the guard's
  message verbatim, plus, only when the result carries `degraded_available`, ONE additional
  control labelled with what the degraded mode COSTS ("a clobbered book just re-enters the
  queue") rather than a generic yes/no. A refusal without `degraded_available` renders exactly as
  a plain message. This did not already exist in `picker.py` — see Deviations.
- `plugin/action.py`: `Settle descriptions` is live on the menu, placed after `Re-derive status`.
  Its disabled reason chain is `nothing selected` → a running write → the cached FanFicFare
  reason (`self._comments_reason`, mirroring `self._write_running`'s own caching so `build_menu`
  makes no core call). The full dispatch chain — PLAN → engine picker → EXECUTE (harvest) →
  Picker/Review-1-by-1 → EXECUTE (write) — goes through the ONE `self._run` site at every hop.
  `_synopsis_ticks` reuses `picker.checklist_decide` (D-04) unchanged rather than re-deriving the
  tick-reading logic, since `synopsis.step` is a single-call review like staleness's, not a
  per-book one like wrangle's.

## Task Commits

1. **Task 1: A progress and abort seam on the synopsis pass** - `0185013` (feat)
2. **Task 2: The synopsis PLAN and EXECUTE jobs, with the FanFicFare guard as an explicit choice** - `a0ac959` (feat)
3. **Task 3: The Settle descriptions slot and the degraded-mode control** - `8305769` (feat)

_No plan-metadata commit yet — this SUMMARY and the STATE/ROADMAP/REQUIREMENTS updates are
committed together immediately after this file is written (per the atomic close-out invariant)._

## Files Created/Modified

- `src/scourgify/synopsis.py` - `Plan.run(on_book=, stop=)`, `Plan.cancelled`.
- `tests/test_synopsis.py` - `test_run_reports_progress_and_stops_between_books`,
  `test_run_with_neither_on_book_nor_stop_is_unchanged`.
- `plugin/jobs.py` - `_NullWriter`, `_synopsis_cost`, `job_plan_synopsis`, `job_execute_synopsis`.
- `tests/test_plugin_jobs.py` - 12 new tests covering `job_plan_synopsis`/`job_execute_synopsis`
  (the FFF-guard refusal + degraded mode, the missing-column refusal at both PLAN and EXECUTE,
  pricing, the harvest-writes-nothing contract, the ticked/unticked write narrowing, the
  kept-book stamp) plus a fixture-isolation fix (see Deviations).
- `plugin/action.py` - the live `Settle descriptions` slot, `synopsis`/`_synopsis_plan_done`/
  `_start_synopsis_execute`/`_synopsis_review_done`/`_finish_synopsis`, `_synopsis_ticks`,
  `self._comments_reason`.
- `plugin/picker.py` - `RefusalDialog`, `show_refusal`.

## Decisions Made

See `key-decisions` in the frontmatter — the second EXECUTE dispatch re-running the engine pass
rather than caching the harvest dispatch's generated text (and why), `RefusalDialog` being new
rather than an extension, and `_synopsis_cost`'s standalone (not plan-closure) shape.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] A prior synopsis job dispatch's sticky `common._LIBRARY` override broke a
LATER test's own fixture setup**
- **Found during:** Task 2, writing the third `job_execute_synopsis` test
  (`test_job_execute_synopsis_a_book_with_an_adequate_blurb_is_stamped_and_kept_untouched`)
- **Issue:** `jobs.job_plan_synopsis`/`job_execute_synopsis` call `common.set_library()` (via
  `_open`), which — by design (NLSpec: the injected path wins over `$CALIBRE_LIBRARY`) — is a
  process-global override that does NOT reset itself when a later test's fixture sets a fresh
  `$CALIBRE_LIBRARY` env var. `tests/test_synopsis.py`'s own `lib()` fixture (reused here for its
  EPUB-building setup) never calls `common.set_library(None)`, so its own
  `os.makedirs(common.data_dir(), exist_ok=True)` setup step — run while the PRIOR test's synopsis
  job call had left `common._LIBRARY` stuck on a now-deleted temp library — created the wrong
  (stale) library's data directory. The FRESH library's own directory was then never created,
  and `synopsis.Plan.run`'s later `write_failures()` call crashed with `FileNotFoundError` the
  moment a SECOND test in the same process reused the fixture. This exact class of gap is already
  worked around elsewhere in this file (`test_job_plan_classify_unclassified_shortcut_restricts_to_the_selection`'s
  own `common.set_library(lib)` call, with the comment "`_LIBRARY` may still point at a prior
  test's library") — this plan's own new tests are the first to combine `test_synopsis.lib()`
  with a job dispatch that calls `set_library()`, surfacing the same gap in a new combination.
- **Fix:** `tests/test_plugin_jobs.py`'s new `_synopsis_lib_ctx()` helper calls
  `common.set_library(None)` before entering `test_synopsis.lib()`, resetting the process-global
  override back to env-var mode so the fixture's own `data_dir()` setup resolves the FRESH
  library.
- **Files modified:** `tests/test_plugin_jobs.py`
- **Verification:** all `job_execute_synopsis` tests pass in sequence within the same process
  (previously the third one crashed); full suite green.
- **Committed in:** `a0ac959` (Task 2 commit)

**2. [Rule 2 - Missing Critical] `picker.py`'s "refused-result rendering added in plan 02-01"
(named in this plan's own read_first) did not exist — built from scratch, scoped to this plan's
own need**
- **Found during:** Task 3, implementing the degraded-mode control
- **Issue:** This plan's task 3 read_first claimed `plugin/picker.py` already carried "the
  refused-result rendering added in plan 02-01." Reading `picker.py` as landed by every prior
  plan in this phase shows no such rendering — every existing PLAN-time refusal
  (`_plan_done`/`_scope_rows_done`/`_classify_plan_done` in `plugin/action.py`) renders via a
  plain `info_dialog(...)` call; the only EXECUTE-time refused-result renderer
  (`result_dialog.ResultDialog`, D-11) lives in a DIFFERENT file (`plugin/result_dialog.py`,
  outside this plan's `files_modified`) and has no degraded-mode concept. Without a
  picker.py-resident refusal surface, task 3(b)'s "extend the refused-result rendering... so a
  refusal carrying degraded_available shows... ONE additional control" had nothing to extend.
- **Fix:** Added `RefusalDialog`/`show_refusal` to `plugin/picker.py` as a new, generic
  refusal-rendering dialog (message verbatim + an optional labelled degraded-mode button),
  wired only at the synopsis PLAN-refusal call site (`action._synopsis_plan_done`) — every other
  verb's PLAN refusal is unchanged, keeping this plan's footprint scoped to WRITE-04. The
  planner's read_first claim is not correct for the codebase as it exists; building the surface
  fresh (rather than trying to "extend" nonexistent code) satisfies the plan's own literal
  acceptance criteria (the degraded control appears only when `degraded_available` is present; a
  refusal without it renders as one text block, no controls).
- **Files modified:** `plugin/picker.py`
- **Verification:** `python3 -c "import ast;..."` confirms `picker.py` imports only `qt.core`;
  `tests/test_plugin_source.py` unaffected (no new core import, no second `ThreadedJob` site).
- **Committed in:** `8305769` (Task 3 commit)

---

**Total deviations:** 2 auto-fixed (1 bug, 1 missing critical). **Impact on plan:** Both were
necessary for this plan's own stated acceptance criteria and test contract to hold; no scope
creep — the picker refusal surface is wired only at the one call site this plan's requirement
(WRITE-04) needs.

## Issues Encountered

- `WRITE-04` in `.planning/REQUIREMENTS.md` was already marked `[x]` Complete before this plan
  ran — plan `02-04`'s SUMMARY frontmatter listed `WRITE-04` in its own `requirements-completed`
  (alongside `WRITE-01`/`WRITE-02`/`WRITE-05`), even though 02-04's actual scope was the
  `(label, payload)` item-shape plumbing, not a working synopsis verb — `job_plan_synopsis`,
  `job_execute_synopsis`, and the `Settle descriptions` menu slot did not exist until this plan.
  Not corrected here (out of this plan's scope to rewrite a prior plan's already-committed
  SUMMARY); `requirements.mark-complete WRITE-04` below is a no-op against the already-checked
  box. Flagged for visibility, not fixed.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- `Settle descriptions` is the sixth of eight planned write-verb plans in this phase, and the
  last one needing its own new engine-picker/review shape: the two-dispatch EXECUTE pattern
  (`ticks=None`/`ticks=list` sharing one function) is new to this plan and specific to synopsis's
  own constraint (review items are generated engine output, not a free diff) — no later plan in
  this phase is expected to need it, but it is available if one does.
- `requirements-completed: [WRITE-04]` is declared here per this plan's own frontmatter; the
  requirement was already checked off by 02-04 (see Issues Encountered) — `mark-complete` is
  idempotent against an already-`[x]` row.
- **Blocker (carried forward):** the real-Calibre human-checks for `Re-derive status` (02-01),
  classify's scope/engine pickers (02-03), the Review 1-by-1 control (02-04), `Normalize fields`
  (02-05), and now `Settle descriptions` (02-06) should all be run together before this phase
  ships — five open `unrun-verify` entries now sit in `.planning/WINDOWS.md`.

## Self-Check: PASSED

- `src/scourgify/synopsis.py`, `tests/test_synopsis.py`, `plugin/jobs.py`,
  `tests/test_plugin_jobs.py`, `plugin/action.py`, `plugin/picker.py` — all exist on disk with the
  expected changes (`grep -n "def job_plan_synopsis\|def job_execute_synopsis\|class _NullWriter\|def _synopsis_cost" plugin/jobs.py`
  confirms all four defined; `grep -n "class RefusalDialog\|def show_refusal" plugin/picker.py`
  confirms both defined; `grep -n "def synopsis\|def _synopsis_ticks" plugin/action.py` confirms
  both defined).
- Commits `0185013`, `a0ac959`, `8305769` — all found in `git log --oneline`.
- All task-level `<acceptance_criteria>` re-verified: `inspect.signature(synopsis.Plan.run)` shows
  keyword-only `write`, `decide`, `on_book`, `stop`; `test_synopsis.py` defines
  `test_run_reports_progress_and_stops_between_books`; a stopped run still writes its settled
  books and still rewrites the failure log; `test_synopsis_queue.py` passes unchanged;
  `plugin/jobs.py` defines both job functions; `grep -v '^[[:space:]]*#' plugin/jobs.py | grep -c
  'comments_protected'` returns 1; the FanFicFare refusal test asserts `degraded_available`; the
  unticked-book test asserts neither the description nor the stamp landed; the adequate-blurb
  test asserts the description is unchanged and stamped; `plugin/action.py` adds the
  `Settle descriptions` slot dispatching both jobs through `self._run`; `picker.py` imports only
  `qt.core` (AST-checked); the degraded control appears only with `degraded_available`;
  `picker.py` defines no second review table; `tests/test_plugin_source.py` still proves exactly
  one `ThreadedJob` call site.
- Plan-level `<verification>`: `tests/test_synopsis.py`, `tests/test_synopsis_queue.py`,
  `tests/test_plugin_jobs.py`, `tests/test_plugin_source.py`, `tests/test_plugin_safety.py` all
  green; full suite (`for t in tests/test_*.py; do uv run "$t" || exit 1; done`) green, no
  failures. No test in this plan makes a real engine call — every engine construction in the new
  tests is a `FakeAsk`/monkeypatched `jobs._engine_ask`, and `engines._post_json` is never
  reached.
- Manual `<human-check>` NOT run (no Calibre in this environment) — see "Next Phase Readiness"
  above; recorded in `.planning/WINDOWS.md` entry 5, not silently marked done.

---
*Phase: 02-write-verbs-on-a-selection*
*Completed: 2026-09-09*
