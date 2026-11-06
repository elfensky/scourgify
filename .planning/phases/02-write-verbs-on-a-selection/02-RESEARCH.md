# Phase 2: Write verbs on a selection - Research

**Researched:** 2026-09-08
**Domain:** Calibre GUI plugin write path (Qt/PyQt6 dialogs + Calibre's `ThreadedJob` system) wired onto an
already-shipped in-process write funnel (`common.write_ops`/`ops.apply_ops`) and six CLI/wizard tool modules
(`wrangle`, `staleness`, `classify`, `synopsis`, `promote`, `overrides`).
**Confidence:** HIGH for the core seams and existing plugin surface (all read from source this session);
MEDIUM for exact Qt/Calibre GUI-refresh call shapes (cross-checked against Calibre's public GitHub source,
not this machine's compiled app bundle); LOW/ASSUMED for anything requiring a live Calibre GUI session,
which this research session did not open (see Environment Availability).

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**The picker between PLAN and EXECUTE (WRITE-01, WRITE-02, WRITE-04, WRITE-05)**
- **D-01:** ONE verb-parameterised picker `QDialog` serves every write verb. The PLAN job hands it plain
  data — summary rows, the SAFETY line, the consequence label for the Run button, the review items — and
  verbs differ only in that data.
- **D-02:** Default view is **summary + Run**, with the consequence on the button (`writes · 37 books`, or
  the price). A **"Review 1-by-1"** control expands the per-book tick-list, all pre-ticked, mirroring
  `ui.checklist` (apply ticked / all / skip). Both paths go through Phase 1's `decide=` seam (D-11 there):
  the PLAN job's `decide` records the items and answers `skip`; the EXECUTE job's `decide` replays the
  ticks — all ticked on the one-click path. The one-click path stays one click; the review path costs one
  expand.
- **D-03:** Review items become **`(label, payload)` tuples**. `label` is the one-line string the wizard
  prints today; `payload` is a dict carrying book id, title, field/column, before, after (plus wrangle's
  `kind`/`class` where the reject log needs them). `ui.checklist` keeps reading only the label, so the
  wizard is unchanged; the Qt list renders before/after columns from the payload. — **Reversibility:**
  costly — the shape change touches all seven `decide=` sites and the tests that pin them, and Phase 4
  History and Phase 5 review will read the same payload fields.
- **D-04:** A **clean stage opens no dialog**: the PLAN job returns empty and the user sees a one-line
  notice ("nothing to change for these N books"). Mirrors the wizard's auto-skip.

**Classify from the menu (WRITE-03)**
- **D-05:** The engine pass and the write are **ONE job**: tags land, every processed book stamps, and the
  edit-log header carries `engine` + `model` through `write_ops(engine=, model=)`. The applied proposal is
  still archived in the CLI's `artifacts.py` format so `classified_ids()`, History and the CLI agree.
  Diff-after + undo is the net, not a dry-run-before. — **Reversibility:** reversible — a "keep as proposal"
  path is the same job minus the write (deferred).
- **D-06:** **Two steps, price after resolve.** Step 1 is the scope dialog on the fixed slots
  `classify.scope_options` already defines. Step 2 is a PLAN job that resolves the todo set **once** through
  `classify.plan()` / `select.py`. Step 3 is the engine picker over that exact set. The cost is
  `classify.est_cost` over the resolved todo, labelled an estimate; the core's >200-book cloud gate (`ask=`)
  is answered by the scope step — no second dialog.
- **D-07:** The engine picker is **one button per engine**, built from `engines.engine_rows` /
  `engine_options`: name, `~$0.42 for 37 books` or `free`, the TRAITS failure-mode text with its measurement
  date. **Clicking the button IS the run.** Engines without a key are greyed with "no key"; apple is absent
  off-macOS.
- **D-08:** A running classify reports through **Calibre's jobs panel only**:
  `notifications.put(fraction, "tagged 12 · failed 1 · 3.1 books/min")` fed by classify's per-book callback;
  abort comes from the job's `abort` and is honoured between books, with the footer marked `cancelled`.

**Diff-after and retry (WRITE-07)**
- **D-09:** The result is a **non-modal result `QDialog` with per-book rows**: a summary line (written /
  skipped-by-conflict / failed, grouped by `engines.failure_class`), then a table of rows — book, field,
  before → after; or `skipped: changed since the plan`; or `refusal on gemini`. It reuses the picker's row
  widget. Its data is `common.WriteResult` (the Phase 1 `skipped` list) plus the run's failures.
- **D-10:** **"Retry on <engine>" lives in both places.** In the result dialog: one button per refused group
  per non-refusing usable engine, direct dispatch with the price on the button, no picker in between. In the
  menu: the existing fixed slot comes alive, keyed off refusal-class rows in the failure log. Both call the
  same job function. Non-refusal classes get a plain "Retry" on the same engine.
- **D-11:** A **guard refusal renders in the same result dialog as a refused state**: the `GuardrailError`
  text verbatim, zero rows, the job ending cleanly. The job wrapper converts `GuardrailError` to a result;
  only a real exception still reaches `gui.job_exception`.

### Claude's Discretion
- **PLAN → EXECUTE hand-off:** the PLAN job returns a plain, Qt-free result (summary, items with payload,
  the ops list with `expected` values, the consequence label). The EXECUTE job applies exactly those ops
  through `write_ops` — no recompute; the apply-time conflict filter is the staleness net. Classify's PLAN
  result carries the resolved todo ids and the per-engine cost table; its EXECUTE job runs the engine and
  writes, in one job. Exact result dict shape is the planner's.
- **Job runner (#72):** extend `action._run` upward so a job function becomes `(con, ids) → result` with
  open/bind/close and the result contract owned in one place; collapse `_library_scope`'s eight-argument
  signature; amend `tests/test_plugin_source.py`'s action.py import rule to match its own docstring. `_run`
  stays the ONE `ThreadedJob` site.
- **Lock greying (success criterion 5):** `build_menu` may not import the core, so the menu must learn a
  write-run is live without a core call — action-side state set at dispatch and cleared in the
  Dispatcher-wrapped completion is sufficient (the plugin is the only in-process writer). The core lock
  (Phase 1 D-06) remains the authoritative refusal, reached in the EXECUTE job and rendered via D-11.
- **Writer seam on the tool modules:** `wrangle.Plan.write`, `staleness.write`, `classify.apply_proposal`,
  `promote.backfill`, `synopsis.Plan` write, and `setup` still call `run_writer` directly; they gain an
  injected `write=` (default `run_writer`) so a job passes a `write_ops`-bound callable. Source tests keep
  `run_writer(` out of the plugin.
- **#73:** promote's `ask`/`verify_ask` adopt classify's `(text, err)` envelope so `decide()` records the
  failure class in the ledger reason; no shared driver.
- **Synopsis (WRITE-04):** when FanFicFare's Comments "New Only" is off, the verb greys with that reason and
  the picker offers the degraded self-healing mode as an explicit choice; the engine picker is D-07's
  widget (apple default where present). Wording is the planner's.
- **Promote / backfill (WRITE-05):** the adjudication engine comes from D-07's picker with the `judge`
  trait (apple greyed `cannot_judge`); the per-candidate verdict review is D-02's tick-list; an unticked
  verdict gets no ledger row. Backfill previews the book ↔ tag adds.
- **Library-view refresh:** which Calibre call refreshes the touched ids from the Dispatcher-wrapped
  callback; dialog parenting and non-modality details; cancelled-run rendering in the result dialog; batch
  -size default for never-classified; workers count; the "Classify the never-classified here" shortcut on a
  mixed selection.

### Deferred Ideas (OUT OF SCOPE)
- "Keep as proposal" switch on the classify picker — revisit in Phase 5.
- Edit tags… verb — slot stays greyed "not built yet" this phase.
- A live progress dialog mirroring `report.Dashboard` — Phase 4 (DASH-04).
- Undo button on the result dialog — Phase 5 (REVIEW-05); the dialog is built so it can carry one.
- Advisory lock file for cross-process "a plugin job is writing" — carried from Phase 1; not needed while
  `calibre_open()` refuses CLI writes with the GUI open.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| WRITE-01 | Run wrangle on selection: preview + write through `common.write_ops` (snapshot + wipe guard + SAFETY) | `wrangle.Plan` (preview/guard/step/write/restrict) fully mapped below; `write=` seam gap identified; PLAN/EXECUTE split matches `Plan`'s existing method split |
| WRITE-02 | Run staleness on selection: per-book `#status` change shown before write | `staleness.compute/step/write` mapped; same `write=` gap as wrangle |
| WRITE-03 | Classify on a scope with an engine picker (price + failure modes, no confirmation dialog) | `classify.scope_options`/`Plan`/`est_cost`/`apply_proposal` fully mapped; **key engine-construction gap found** (see Common Pitfalls #1) |
| WRITE-04 | Synopsis on selection with usable engine (apple default); refuses without FFF "New Only" unless forced | `synopsis.Plan`/`guard_comments`/`setup.comments_protected` mapped; **monolithic Plan.run() gap found** (see Architecture Patterns) |
| WRITE-05 | Promote + backfill from plugin with same per-candidate verdict review as wizard | `promote.apply_decisions_step`/`backfill_step`/`backfill(decide=)` mapped; **`ask` envelope asymmetry found** (#73, see Common Pitfalls #2) |
| WRITE-06 | Every write appends per-op edit-log lines identical to CLI | `editlog.py` fully mapped (already shared by both write paths since Phase 1 — no new work needed beyond passing `engine=`/`model=`) |
| WRITE-07 | Diff-after notice: counts, skipped conflicts, failures by `failure_class()`, retry verb; library view refreshes | `common.WriteResult` mapped; retry-verb data sources identified; GUI refresh call confirmed against Calibre's public source |
| WRITE-08 | Plugin never calls `run_writer`, never spawns a second process, dispatches every job through ONE `action._run` | `tests/test_plugin_source.py` fully read — the exact assertions a new picker/result-dialog module must keep passing |
</phase_requirements>

## Summary

Phase 1 already built every non-Qt seam this phase needs: `common.write_ops(api, ops, force=, out=, tool=,
scope=, engine=, model=) -> WriteResult` is the in-process write funnel with snapshot, wipe guard, per-op
conflict filtering and edit-logging already wired identically to the CLI's `run_writer`. Five of the six
`decide=` sites the wizard's review checklists use (`staleness.step`, `synopsis.step`,
`promote.apply_decisions_step`, `promote.backfill_step`, `overrides.step_pick`, `overrides._step_walk`)
already accept an injected `decide=None` callback with the exact `(accepted_idx, rejected_idx, action)`
shape `ui.checklist` returns — this IS the seam a Qt picker plugs into. `promote.backfill(decide=)` and
`classify.Plan.run(ask=)`/`promote.run(ask=,verify_ask=)`/`synopsis.Plan.run(ask=)` are the equivalent seams
for the engine call and the backfill confirmation.

What Phase 1 did **not** build, and what this phase's plan must add, is threefold. First, **none of the six
write-producing functions accept an injected `write=`** — `wrangle.Plan.write`, `staleness.write`,
`classify.apply_proposal`, `promote.backfill`, `synopsis.Plan.run`'s tail, and `setup`'s column-creation path
all call `common.run_writer` directly by name, which is banned from the plugin (`tests/test_plugin_source.py`
greps for `run_writer(` in every plugin module). Second, `classify.apply_proposal(rows=None)` has no
`engine=`/`model=` parameters to pass through to `write_ops`, so D-05's "engine+model header fields" cannot
land without a signature change. Third — and this is the highest-value finding of this research — **the
default engine construction inside `classify.Plan.run()`, `promote.run()`, and `synopsis.Plan.run()` never
receives an `env=` mapping**, so it can only see `os.environ` keys, never a key the user typed into
`plugin/config.py`'s settings dialog. The plugin MUST always build its own engine object via
`ENGINES[engine](model, timeout, env=engines.resolve_keys(config.stored_keys()))` and pass it in as `ask=`
(exactly the pattern `config.job_verify` already uses) — never rely on the modules' own default construction.

The existing plugin surface (`plugin/action.py`, `plugin/config.py`) is Phase-1-thin: one `_run` dispatcher,
one `_open` identity check, one read-only `job_inspect`, and a settings dialog with its own `job_verify` job.
The six write verbs are greyed placeholders (`SOON = 'not built yet'`) on fixed menu slots. `_verb()`'s
`slot=None or disabled_reason` pattern, `_run(description, func, args, done=None)`'s Dispatcher-wrapping, and
`build_menu()`'s "capture ids+identity at click time" pattern are the exact scaffolding every new verb reuses.
`tests/test_plugin_source.py` is the enforcement mechanism — four AST assertions (no `run_writer`/subprocess/
`ThreadPoolExecutor`; no core import outside `job_*`/`_`-prefixed functions; every `ThreadedJob` callback
`Dispatcher`-wrapped; exactly one `ThreadedJob` call site) that any new module (a picker dialog, a result
dialog) must be added to `MODULES`/`QT_MODULES` and keep passing.

**Primary recommendation:** Build the six `write=`/`engine=`/`model=` seams on the core tool modules FIRST
(pure Python, testable in CI with `tests/fixture_db.py`, zero Qt), then build classify's engine-picker verb
(highest-stakes UI per the phase description), then the deterministic verbs (wrangle/staleness), then
promote/backfill, then synopsis last (it inherits every pattern the others establish and has the most open
questions — see Open Questions).

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Menu build (verb list, greyed reasons) | Browser-equivalent: Qt GUI thread (`plugin/action.py`) | — | Pure widget construction over an id list captured at click time; no core call (enforced by AST test) |
| PLAN (preview compute, cost estimate, scope resolution) | Backend/job worker (`ThreadedJob` off the GUI thread) | — | Reads the library (read-only or via live `new_api`); must never run on the GUI thread (measured: cheapest core read is 6 ms, `load_maps()` is 870 ms, both exceed the 100 ms *dispatch* budget) |
| Picker / result `QDialog` (decision UI) | GUI thread, Qt widgets over plain data | — | "makes no core call" per phase goal; renders `(label, payload)` tuples and summary/cost strings the PLAN job already computed |
| EXECUTE (write, engine call, edit-log) | Backend/job worker (`ThreadedJob`) | — | All library writes go through `common.write_ops` → `ops.apply_ops`, which touches Calibre's live `new_api`; must run off the GUI thread for the same reason as PLAN |
| Write funnel (snapshot, wipe guard, conflict filter, lock, edit-log) | Core/library tier (`common.py`, `ops.py`, `editlog.py`) | — | Shared unconditionally between CLI and plugin since Phase 1; this phase adds no new logic here beyond `write=`/`engine=`/`model=` plumbing |
| Engine HTTP calls (classify/synopsis/promote) | External service call, dispatched from the EXECUTE job (backend tier) | — | `engines.py`'s adapters do `urllib` I/O; must not block the GUI thread; results feed back into the SAME job's write |
| Library-view refresh after write | GUI thread (Dispatcher-wrapped callback) | — | Calibre's `library_view.model().refresh_ids(...)` is a Qt model call, callable only from the GUI thread — confirmed against Calibre's public source (see Code Examples) |
| Write-run lock (greying verbs) | Split: authoritative lock in core (`common._acquire_write_lock`), advisory mirror in action-side Qt state | Core tier owns the refusal; GUI tier owns the instant grey | `build_menu()` cannot call the core (AST-enforced), so it needs a Qt-side boolean/dict set at dispatch and cleared on Dispatcher-wrapped completion; the core lock remains the ground truth reached inside the EXECUTE job |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `qt.core` (Calibre's own Qt6 shim, PyQt6/PySide6 underneath) | bundled with Calibre ≥ 6.0 | `QDialog`, `QVBoxLayout`, `QPushButton`, `QLabel`, `QTableWidget`/`QListWidget` for the picker/result dialogs | Already the only Qt import path in this repo (`plugin/action.py:25`, `plugin/config.py:26`); Calibre plugins never import `PyQt6` directly — `qt.core` re-exports the binding Calibre itself was built against [VERIFIED: plugin/action.py:25, plugin/config.py:26] |
| `calibre.gui2.threaded_jobs.ThreadedJob` | bundled with Calibre | The one job-dispatch mechanism; runs `func(*args, **{'abort':…,'log':…,'notifications':…})` off the GUI thread | Already the sole dispatch site (`action._run`); a source-grep test (`test_only_one_module_dispatches_jobs`) forbids a second one [VERIFIED: plugin/action.py:302-312, tests/test_plugin_source.py:145-154] |
| `calibre.gui2.Dispatcher` | bundled with Calibre | Wraps a job callback so it is safe to touch Qt from — `ThreadedJob.start_work` calls `self.callback(self)` from the WORKER thread | Confirmed against Calibre's public GitHub source: `kwargs['notifications']`/`kwargs['abort']` are injected in `__init__`, and `self.callback(self)` is called after `self.result = self.func(...)` inside `start_work` [CITED: github.com/kovidgoyal/calibre/blob/master/src/calibre/gui2/threaded_jobs.py] |
| `scourgify.common.write_ops` | in-repo, Phase 1 | The in-process write funnel every EXECUTE job calls | Signature: `write_ops(api, ops, force=False, out=print, tool='plugin', scope=None, engine=None, model=None) -> WriteResult` [VERIFIED: src/scourgify/common.py:1188-1215] |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `scourgify.editlog` | in-repo, Phase 1 | `before_values`, `start`, `finish`, `conflict` — already called from inside `_write_run` (both `write_ops` and `run_writer` funnel through it) | No new call sites needed this phase; `engine=`/`model=` just need to reach `write_ops`'s existing kwargs |
| `scourgify.engines` | in-repo, Phase 1 | `TRAITS`, `PRICING`, `usable_engines`, `resolve_keys`, `failure_class`, `engine_rows`, `engine_options`, `default_engine_id`, `ask_retry` | Every string in the engine picker and result dialog derives from these — never hardcode a price or a failure-mode sentence |
| `scourgify.artifacts` | in-repo, Phase 1 | CSV formats for the classify proposal, ranked candidates, review, ledger, failures | `classify.apply_proposal`'s archive-on-success behaviour is reused unchanged by the plugin's classify EXECUTE job (D-05) |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `qt.core`'s plain `QDialog` + manual layout | Calibre's `calibre.gui2.dialogs.*` mixins (e.g. `Dialog` base class with saved geometry) | Calibre ships a `calibre.gui2.widgets2.Dialog` base that persists window geometry between opens; using it is a nicety, not a requirement — plain `QDialog` satisfies every locked decision. Flag as a **Claude's Discretion** item for the planner, not researched further (LOW priority, cosmetic) [ASSUMED] |
| Splitting `job_*` functions into a new Qt-free `plugin/jobs.py` | Keeping every `job_*` function inline in `action.py` (today's pattern) | A Qt-free `jobs.py` would be importable and unit-testable under plain CI Python (no Qt/Calibre needed) — a real gain for Validation Architecture — but it is a structural change beyond what CONTEXT.md's Claude's Discretion explicitly authorizes ("`_run` stays the ONE `ThreadedJob` site" says nothing about where `job_*` bodies live). Recommended as a strong option; not asserted as decided (see Validation Architecture and Open Questions) [ASSUMED — recommendation, not a locked decision] |

**Installation:** None — no new PyPI/npm packages this phase. `qt.core` and `calibre.gui2.*` ship inside
Calibre itself; `pyproject.toml`'s only production dependency remains `rich>=13`, and `rich` is never
imported by any plugin module [VERIFIED: pyproject.toml:dependencies].

**Version verification:** N/A — no package versions to verify; Calibre's own API surface (`ThreadedJob`,
`Dispatcher`, `qt.core`) is pinned to `minimum_calibre_version = (6, 0, 0)` already declared in
`plugin/__init__.py:32` [VERIFIED: plugin/__init__.py:32].

## Package Legitimacy Audit

**Not applicable this phase.** No new external packages are installed. Every import this phase adds is
either (a) `scourgify`'s own existing core modules, (b) Calibre's bundled `qt.core`/`calibre.gui2.*`, both
already used by the Phase-1 plugin skeleton, or (c) Python stdlib. `Package Legitimacy Audit` is skipped per
the RESEARCH.md contract's own instruction ("Required whenever this phase installs external packages").

## Architecture Patterns

### System Architecture Diagram

```
                    ┌─────────────────────────────────────────────────────┐
                    │  GUI THREAD (Qt)                                     │
                    │                                                       │
  click ──────────► │  build_menu()                                        │
                     │    captures: (lib_path, lib_uuid, ids) at click time │
                     │    renders: fixed verb slots, greyed by action-side  │
                     │             lock state (no core call)                │
                     │         │                                            │
                     │         │ user clicks a verb                         │
                     │         ▼                                            │
                     │  action._run(desc, PLAN_job_func, args,              │
                     │              done=picker.on_plan_done)               │
                     └─────────┬─────────────────────────────────────────────┘
                               │ ThreadedJob (worker thread)
                               ▼
                    ┌──────────────────────────────────────────┐
                    │  WORKER THREAD — PLAN job                 │
                    │  _open(lib_path, lib_uuid, now_uuid)       │
                    │  wrangle.plan(cfg, maps).restrict(ids)     │
                    │    .preview() / .guard()                   │
                    │  -OR- classify.plan(opts); est_cost(todo)  │
                    │  -OR- promote.candidates(); backfill_plan()│
                    │  → returns plain dict: summary, items      │
                    │    [(label,payload),...], ops w/ expected, │
                    │    consequence label, SAFETY line          │
                    └─────────┬──────────────────────────────────┘
                               │ Dispatcher(done)-wrapped callback
                               ▼
                    ┌─────────────────────────────────────────────────────┐
                    │  GUI THREAD — picker.on_plan_done(job)               │
                    │                                                       │
                    │  D-04: empty result → one-line notice, stop           │
                    │  else → ONE verb-parameterised QDialog (D-01):        │
                    │    summary + Run button (consequence/price on it)     │
                    │    "Review 1-by-1" expands a tick-list (D-02)         │
                    │                                                       │
                    │  user clicks Run  ──────────────────────────────────► │
                    │  action._run(desc, EXECUTE_job_func, args,            │
                    │              done=result.show)                       │
                    └─────────┬─────────────────────────────────────────────┘
                               │ ThreadedJob (worker thread)
                               ▼
                    ┌──────────────────────────────────────────┐
                    │  WORKER THREAD — EXECUTE job               │
                    │  _open(...) → live new_api                 │
                    │  [classify/synopsis/promote only:           │
                    │    build engine via ENGINES[e](m,t,env=…)   │
                    │    run engine calls, notifications.put(…) ] │
                    │  tool_module.write(..., write=lambda ops:   │
                    │      common.write_ops(api, ops, tool=...,   │
                    │                        engine=, model=))    │
                    │  → common.WriteResult{run_id, backup, ops,  │
                    │      books, skipped, outcome} + failures[]  │
                    └─────────┬──────────────────────────────────┘
                               │ Dispatcher(done)-wrapped callback
                               ▼
                    ┌─────────────────────────────────────────────────────┐
                    │  GUI THREAD — result.show(job)                       │
                    │  D-09: non-modal result QDialog:                      │
                    │    summary (written/skipped/failed by failure_class) │
                    │    per-book rows (before → after / skipped / refused)│
                    │    D-10: "Retry on <engine>" per refused group        │
                    │  refresh: gui.library_view.model().refresh_ids(ids)   │
                    │  clear action-side lock state                        │
                    └─────────────────────────────────────────────────────┘
```

### Recommended Project Structure
```
plugin/
├── __init__.py         # unchanged: InterfaceActionBase wrapper, settings hooks
├── action.py            # Qt shell: build_menu, _run, _verb, _done, job_* wrappers per verb
├── config.py            # unchanged: settings dialog, job_verify
├── picker.py             # NEW: the ONE verb-parameterised picker QDialog (D-01)
├── result_dialog.py      # NEW: the non-modal diff-after result QDialog (D-09)
├── selftest.py           # extended: drive each new verb's PLAN→picker→EXECUTE round trip
└── plugin-import-name-scourgify.txt   # unchanged, empty marker file
```
Both new files MUST be added to `tests/test_plugin_source.py`'s `MODULES` list (and `QT_MODULES`, since both
import `qt.core`) so the four architectural AST tests cover them [VERIFIED: tests/test_plugin_source.py:21-22].

### Pattern 1: The `write=` injection seam (the single biggest gap this phase must close)

**What:** Every write-producing tool function currently hardcodes a call to `common.run_writer`. None of
them can be called from the plugin as-is, because `run_writer(` is banned from every plugin module by
`tests/test_plugin_source.py::test_the_plugin_never_spawns_a_second_writer`.

**Verified call sites** [VERIFIED: grep across src/scourgify/*.py, confirmed by reading each function body]:
```
src/scourgify/wrangle.py:437    run_writer(ops, force=force, tool="wrangle", scope=self.scope)
src/scourgify/staleness.py:87   run_writer([op_set_field(...)], tool="staleness", scope=f"{len(rows)} books")
src/scourgify/classify.py:272   run_writer(ops, tool="classify", scope=f"{len(processed)} books")
src/scourgify/promote.py:403    run_writer([op_set_field("tags", chg, expected=expected)], tool="promote", ...)
src/scourgify/synopsis.py:268   run_writer(ops, tool="synopsis", scope=f"{len(made)} written, {len(kept)} kept")
src/scourgify/setup.py:184      run_writer(ops, tool="setup", scope="columns + prefs")
```

**When to use this pattern:** Every write-producing function above needs a keyword-only `write=run_writer`
parameter (default preserves CLI behaviour byte-for-byte) so the plugin can pass a bound callable:
```python
# EXECUTE job, illustrative (not existing code):
def _plugin_write(api, engine=None, model=None):
    def write(ops, force=False, tool="plugin", scope=None):
        return common.write_ops(api, ops, force=force, tool=tool, scope=scope,
                                engine=engine, model=model)
    return write
```
`classify.apply_proposal` additionally needs `engine=None, model=None` KEYWORD parameters threaded to its
`run_writer(...)` call site (line 272) — today that call passes neither, so D-05's "edit-log header carries
engine + model" cannot be satisfied without this signature change [VERIFIED: src/scourgify/classify.py:262-272,
comment at line 269-271 already anticipates this: "engine/model stay unset here: --apply is its own
invocation ... The plugin's classify verb runs the pass and the write in one job and passes both
(write_ops(engine=…, model=…))" — the comment names the exact seam but the parameter does not exist yet].

**Precedent this pattern follows:** `Plan.run(ask=None)` and `promote.backfill(decide=)` already use exactly
this "keyword-only callable, default preserves old behaviour" shape [VERIFIED: src/scourgify/classify.py:396,
src/scourgify/promote.py:365-372].

### Pattern 2: The `decide=` seam is ALREADY BUILT for five of six review checklists

**What:** Phase 1's plan 01-05 added `decide=None` to seven review-checklist call sites, all defaulting to
`ui.checklist`, all with the lazy `from scourgify import ui` import moved BEHIND the `decide is None` check
(so an injected `decide` never imports the interactive module — critical for plugin compatibility since
`ui.py` hard-imports `rich`, which is absent under Calibre's bundled Python) [VERIFIED:
.planning/phases/01-foundation-a-hostable-core/01-05-SUMMARY.md:172-173, cross-checked against source]:

| Function | Signature (verified) | File:line |
|----------|----------------------|-----------|
| `staleness.step` | `step(status_label, rows, decide=None)` | src/scourgify/staleness.py:64 |
| `synopsis.step` | `step(made, titles, decide=None)` | src/scourgify/synopsis.py:287 |
| `promote.apply_decisions_step` | `apply_decisions_step(review_path=None, decide=None)` | src/scourgify/promote.py:192 |
| `promote.backfill_step` | `backfill_step(chg, adds, titles, decide=None)` | src/scourgify/promote.py:347 |
| `promote.backfill` | `backfill(yes=False, step=False, decide=None)` — a DIFFERENT `decide` shape: `decide(chg, adds) -> chg` | src/scourgify/promote.py:365 |
| `classify.apply_proposal_step` | `apply_proposal_step(decide=None)` | src/scourgify/classify.py:280 |
| `overrides._step_walk` / `overrides.step_pick` | both `decide=None` | src/scourgify/overrides.py:166, 231 |

The `decide` callback shape for the checklist family is `decide(title, items, subtitle="") ->
(accepted_idx, rejected_idx, action)` where `action ∈ {'apply','skip','quit'}` — exactly `ui.checklist`'s
own return shape [VERIFIED: src/scourgify/ui.py:121-125]. `promote.backfill`'s `decide` is a DIFFERENT
shape — `decide(chg, adds) -> chg_to_write` (falsy aborts) — do not conflate the two when building the
picker's generic `decide=` adapter [VERIFIED: src/scourgify/promote.py:365-372, 383-386].

**What is NOT yet built:** `wrangle.Plan.step()` has no `decide=` parameter — it always calls
`overrides._step_walk(...)` with the module's own default (`ui.checklist`) [VERIFIED: src/scourgify/
wrangle.py:405-421]. `Plan.step()` needs a `decide=None` parameter threaded down to `_step_walk`'s own
`decide=` for the plugin's picker to intercept it.

### Pattern 3: Classify's and synopsis's engine construction bypasses injected keys — the concrete fix

**What goes wrong:** `classify.Plan.run(ask=None)`, `promote.run(ask=None, verify_ask=None)`, and
`synopsis.Plan.run(ask=None)` ALL build their default engine the same way when `ask` is not supplied:
```python
eng = ENGINES[a.engine](a.model, a.timeout)      # NO env= argument
ask = lambda prompt: ask_retry(eng, prompt)       # (classify/synopsis shape)
```
[VERIFIED: src/scourgify/classify.py:408-410, src/scourgify/synopsis.py:226-228, src/scourgify/promote.py:
422-427]. Every engine constructor's `env` parameter defaults to `None`, and every constructor does
`(os.environ if env is None else env).get(...)` [VERIFIED: src/scourgify/engines.py:92-96 (Apple, no key),
109-111 (_Chat base for Claude/OpenAI/Mistral), 153-156 (Gemini)]. So a key the user typed into
`plugin/config.py`'s settings dialog (stored via `JSONConfig`, never written to `os.environ` — deliberately,
per `plugin/config.py`'s own docstring) is **invisible** to this default construction path.

**How to avoid:** The plugin's EXECUTE job for classify/promote/synopsis must NEVER call `Plan.run()` /
`promote.run()` with `ask=None`. It must build the engine itself and pass a bound `ask`:
```python
from scourgify import config as plugin_config      # plugin/config.py — NOT scourgify.config (doesn't exist)
env = engines.resolve_keys(plugin_config.stored_keys())
eng = engines.ENGINES[engine_id](model, timeout, env=env)
ask = lambda prompt: engines.ask_retry(eng, prompt)          # classify/synopsis shape: (text, err)
plan_obj.run(ask=ask)
```
This is EXACTLY the pattern `plugin/config.py::job_verify` already uses at line 63: `eng =
engines.ENGINES[engine]('', 30, env={engines.ENGINE_ENV[engine][0]: key})` [VERIFIED:
plugin/config.py:59-69]. Reuse that pattern rather than inventing a new one.

**Warning signs this was missed:** classify/synopsis appear to work in `Settings → Verify` (which correctly
uses `job_verify`'s explicit `env=`) but raise `GuardrailError: <engine> engine needs <ENV_VAR>` the moment a
real classify/synopsis/promote run is dispatched with a GUI-stored-only key. This is the single most likely
"works for me" bug this phase can ship with, because the CLI wizard never hits it (its process environment
already carries any key the user cares to export) — it is a plugin-only failure mode with no CLI analogue to
catch it in existing tests.

### Pattern 4: `promote`'s `ask` envelope is (text) not (text, err) — the #73 gap

**What:** `promote.run()`'s default `ask` discards the error string: `ask = lambda p: ask_retry(eng, p)[0]`
[VERIFIED: src/scourgify/promote.py:422-424]. `promote.decide()` calls `parse_decision(ask(...))` expecting a
bare string [VERIFIED: src/scourgify/promote.py:118-137]. Classify's and synopsis's `ask` shape is the FULL
`(text, err)` tuple. CONTEXT.md's #73 discretion item ("promote's `ask`/`verify_ask` adopt classify's
`(text, err)` envelope so `decide()` records the failure class in the ledger reason") requires changing BOTH
the lambda AND `decide()`'s body — today an engine failure inside `promote.decide()` produces a bare
`{"verdict": "error", ..., "reason": "no usable response (transport failure or unparseable)"}` with no class
prefix [VERIFIED: src/scourgify/promote.py:122-128], so `engines.failure_class()` cannot read a class back
out of it and the picker cannot offer a "Retry on <engine>" for a promote refusal the way it can for a
classify refusal (`engines.failure_class(reason)` reads the class from a string PREFIX like `"refusal: ..."`,
which `ask_retry` already produces at the classify/synopsis call sites [VERIFIED: src/scourgify/
engines.py:320-336, esp. line 333]). This is real, non-cosmetic plumbing work, not a rename.

### Pattern 5: Synopsis's `Plan.run()` is monolithic — engine calls, review, AND write in one method

**What:** Unlike classify (`Plan.run()` does the engine pass and STOPS; `apply_proposal`/`apply_proposal_step`
are separate, later calls), `synopsis.Plan.run()` does the engine pass, conditionally calls `step()` for
review, AND writes — ALL inside one function, with `step()` called internally via `made = step(made,
self.titles)` with no `decide=` threaded through from `run()`'s own signature [VERIFIED: src/scourgify/
synopsis.py:218-268, esp. line 250-251]. D-05 explicitly makes this SAME shape ("the engine pass and the
write are ONE job") the DESIGN for classify — so synopsis's existing monolithic `Plan.run()` is arguably
already the right shape for the plugin's EXECUTE job, PROVIDED it also grows `write=` (Pattern 1) and either
(a) grows its own `decide=` for the internal `step()` call, or (b) the plugin's synopsis verb skips the
per-book review picker entirely and relies on diff-after + retry, mirroring classify. **This is an open
design question — see Open Questions #1.**

### Pattern 6: The lock-greying mechanism — action-side mirror, core-side authority

**What:** `common._acquire_write_lock`/`_WRITE_HOLDERS` are the authoritative, core-level lock — but
`build_menu()` is explicitly forbidden from making a core call (every core import must live inside a
`job_*`/`_`-prefixed function, enforced by `test_no_core_import_can_run_on_the_gui_thread`) [VERIFIED:
tests/test_plugin_source.py:82-99]. CONTEXT.md's discretion item resolves this: "action-side state set at
dispatch and cleared in the Dispatcher-wrapped completion is sufficient (the plugin is the only in-process
writer)." Concretely, this means a plain module-level dict on `action.py` (or an attribute on
`ScourgifyAction`), e.g. `self._write_running = {}` keyed by verb name or job description, set to `True`
right before `self._run(...)` for a write verb and cleared inside the wrapped `done` callback — read by
`build_menu()` with NO import, just a Python dict lookup. The authoritative refusal still happens inside the
EXECUTE job when it calls `write_ops` → `_write_run` → `_acquire_write_lock`, which raises `GuardrailError`
naming the actual holder if a race slips through (e.g. two clicks before the Qt-side flag updates) — that
`GuardrailError` renders via D-11's normal result-dialog path, so the race is caught by the core net even if
the Qt-side grey momentarily lags.

### Anti-Patterns to Avoid
- **Calling `Plan.run(ask=None)`/`promote.run(ask=None)` from a plugin job:** silently uses `os.environ`
  only — see Pattern 3. Always build and inject the engine explicitly.
- **A new module building its own `ThreadedJob(...)`:** forbidden by `test_only_one_module_dispatches_jobs`
  — route every dispatch through `action._run`, passing `done=` for a caller-owned completion (exactly how
  `config.py`'s `verify()` already does it) [VERIFIED: plugin/config.py:176-177].
- **Importing `scourgify.ui` from a picker/result dialog module:** `ui.py` hard-imports `rich`, which does
  not exist under Calibre's bundled Python. The picker renders `(label, payload)` tuples directly in Qt
  widgets; it never calls `ui.checklist` itself — it plays the role `ui.checklist` plays for the CLI, by
  being passed as the `decide=` callback.
- **A raw `shutil.copy2` or direct sqlite file operation for anything at all:** the whole write path already
  goes through `write_ops`/`ops.apply_ops`; nothing new needs raw file I/O this phase.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Per-op conflict detection at write time | A second "is this stale?" check in the picker or EXECUTE job | `common._check_conflicts` / `editlog.conflict` (already runs inside `write_ops` via `_write_run`) | THE shared predicate apply-time checks and undo both use; a second implementation risks disagreeing about what "conflict" means (explicitly the thing `editlog.conflict`'s docstring warns against) [VERIFIED: src/scourgify/editlog.py:152-163] |
| Snapshot-before-write | A `shutil.copy2` of `metadata.db` from the plugin | `common.backup_db` (already called inside `_write_run` for BOTH callers, using sqlite's Online Backup API — safe against a live GUI connection) | A live GUI connection can leave a committed transaction sitting in `metadata.db-wal`; a raw file copy silently loses it — this exact hazard is why `backup_db` exists and both `run_writer` and `write_ops` already funnel through it [VERIFIED: src/scourgify/common.py:995-1026, NLSpec B2 step 2] |
| Cost estimation per engine | A flat per-book price guess in the picker | `classify.est_cost(n_books, engine)` reading `engines.PRICING` / `TRAITS['out_tokens']` | A flat guess under-quoted gemini fivefold in an earlier version of this exact code — the `out_tokens` trait exists specifically because a reasoning model bills hidden thinking as output [VERIFIED: src/scourgify/classify.py:114-122, src/scourgify/engines.py:24-28] |
| Failure classification | String-matching an exception message in the picker/result dialog | `engines.classify_error(exc)` / `engines.failure_class(reason)` | The normalized 7-class taxonomy (refusal/auth/permission/quota/timeout/parse/error) already drives which recovery verb is offered; re-deriving it risks drifting from the CLI's failures CSV format [VERIFIED: src/scourgify/engines.py:274-307] |
| Engine key masking/redaction | A custom `key[:4] + "..."` display helper | `engines.mask`/`engines.unmask`/`engines.redact` | Already the B5 postcondition's enforcement point — "no key reaches a log, artifact, or dialog" is only true if every surface uses these, and `redact()` is what strips a key out of an HTTPError message that could otherwise echo it [VERIFIED: src/scourgify/engines.py:204-217, 310-317] |
| Review checklist walking | A bespoke Qt "accept/reject" loop per verb | The `decide=` seam (Pattern 2) + `(label, payload)` tuples (D-03) | This is the entire point of D-01's ONE verb-parameterised picker — a bespoke per-verb widget defeats it |

**Key insight:** Every hard problem in this phase (conflict detection, snapshotting, cost math, failure
classification, key hygiene) was already solved correctly in Phase 1 or earlier and is reachable through a
pure-Python function call. The plugin-specific work is almost entirely (a) threading a few new keyword
parameters through six functions so the plugin can reach those solved problems without going through
`run_writer`, and (b) Qt plumbing to present their outputs. Resist any temptation to re-derive a guard,
a cost formula, or a failure class inside `plugin/*.py` — every one of those already has exactly one owner.

## Common Pitfalls

### Pitfall 1: Engine construction skips injected/stored keys (Pattern 3, restated as a pitfall)
**What goes wrong:** A classify/synopsis/promote EXECUTE job calls `plan_obj.run()` with no `ask=`, and it
silently only sees `os.environ` keys.
**Why it happens:** `Plan.run`/`promote.run`'s "build a default engine" branch never accepts an `env=`
mapping — it was written for the CLI, where `os.environ` IS the only key source.
**How to avoid:** Always construct the engine explicitly with `env=engines.resolve_keys(config.stored_keys())`
and pass it as `ask=`, mirroring `plugin/config.py::job_verify`.
**Warning signs:** A key verified successfully in Settings but a classify run fails with a `GuardrailError`
naming the missing env var.

### Pitfall 2: `notifications.put(...)` takes ONE tuple argument, not two
**What goes wrong:** CONTEXT.md's D-08 phrasing — `notifications.put(fraction, "tagged 12 · failed 1 · 3.1
books/min")` — reads as two positional arguments. The EXISTING, working code in this exact repo calls it
with ONE tuple: `notifications.put((n / max(len(ids), 1), 'reading %d of %d' % (n + 1, len(ids))))`
[VERIFIED: plugin/action.py:94]. Calibre's `ThreadedJob.__init__` injects `kwargs['notifications'] =
self.notifications`, a queue Calibre's job-list UI drains and expects `(percent, message)` tuples from
[CITED: github.com/kovidgoyal/calibre/blob/master/src/calibre/gui2/threaded_jobs.py].
**How to avoid:** Follow `job_inspect`'s existing call shape exactly: `notifications.put((fraction, msg))`.
**Warning signs:** A `TypeError: put() takes 2 positional arguments but 3 were given` the first time classify
progress reporting is wired up.

### Pitfall 3: `create_column` cannot be reached from an in-process write
**What goes wrong:** `ops.apply_ops`'s `create_column` branch raises `ValueError` whenever `legacy is None`
— which is always true for the plugin's call, since the legacy-DB reopen trick "would desync a live GUI's
models" [VERIFIED: src/scourgify/ops.py:56-60]. `classify.apply_proposal`'s "first run" branch (`if not
have_wrangled: ops.append(op_create_column(...))`) would therefore CRASH the plugin's classify EXECUTE job on
a library that has never run `#wrangled` column creation.
**Why it happens:** Column creation is deliberately routed through `scourgify setup` (Calibre closed) —
Phase 3's scope (SETUP-01..05), not this phase's.
**How to avoid:** The plugin's classify verb should be greyed with a "run setup first" reason (mirroring
SETUP-01's design) whenever `#wrangled` doesn't exist yet, OR the plugin's `apply_proposal` call path must
special-case away from ever emitting `op_create_column` — check `custom_column_id(con, "wrangled") is not
None` up front and refuse cleanly with `GuardrailError` if not, rather than letting `apply_ops` raise a bare
`ValueError` (which is NOT a `GuardrailError` and could reach the job's `job.failed` path instead of D-11's
clean result-dialog path). **This is a concrete, testable edge case the plan should assign a task to.**
**Warning signs:** A classify run on a freshly-columned-but-never-classified library throws instead of
showing a clean refusal.

### Pitfall 4: `synopsis`'s `#synopsized` column has the identical create_column hazard
**What goes wrong:** `synopsis.Plan.run()`'s `if not self.have_stamp: ops.append(op_create_column(...))`
[VERIFIED: src/scourgify/synopsis.py:255-256] hits the exact same `ops.apply_ops` `ValueError` as Pitfall 3.
**How to avoid:** Same fix pattern as Pitfall 3 — greying/refusing cleanly rather than letting the write
raise.

### Pitfall 5: A `GuardrailError` from a bare `ops.apply_ops` `ValueError` is not caught by D-11's contract
**What goes wrong:** D-11 says "the job wrapper converts `GuardrailError` to a result; only a real exception
still reaches `gui.job_exception`." `ops.apply_ops`'s `create_column` branch raises plain `ValueError`, not
`GuardrailError` — by design, since `ops.py` "imports nothing from scourgify" and cannot raise
`common.GuardrailError` [VERIFIED: src/scourgify/ops.py:9-16 docstring, 56-60]. A plain `ValueError`
therefore DOES reach `job.failed` / `gui.job_exception`, producing an ugly traceback dialog instead of D-11's
clean refusal message.
**How to avoid:** Guard against ever emitting an in-process `create_column` op BEFORE calling `write_ops` —
catch this at the tool-module or job-function level, not inside `ops.py` (which must stay import-nothing).

### Pitfall 6: `apply_ops`'s `create_column`-adjacent write is otherwise safe — don't over-guard
**What goes wrong (inverse of the above):** Overzealous defensive code could refuse EVERY classify/synopsis
run on a library missing `#wrangled`/`#synopsized`, even though the column-creation branch is only reached
on the FIRST classify/synopsis run ever. `apply_proposal`'s `have_wrangled` check and `Plan.run`'s
`have_stamp` check already gate this correctly — the fix is a clean pre-flight refusal, not disabling the
whole verb whenever the column happens to be absent for any book.
**How to avoid:** Scope the guard exactly to "column doesn't exist AND this run needs to create it" — reuse
the existing `custom_column_id(con, "wrangled") is not None` / `custom_column_id(con, STAMP) is not None`
checks these modules already compute.

### Pitfall 7: The result dialog needs TWO different failure sources, not one
**What goes wrong:** `common.WriteResult.skipped` covers write-TIME conflicts (a book's field changed since
the PLAN job read it). Classify's/synopsis's ENGINE-call failures (refusal/auth/quota/timeout/parse/error)
are a SEPARATE list returned from `Plan.run()`/`settle()`, written to `classify_failures.csv`/
`synopsis_failures.csv` via `artifacts.py` — `WriteResult` carries neither of these; they live in the
EXECUTE job's own local `failures` list [VERIFIED: src/scourgify/classify.py:414, 443-451, src/scourgify/
synopsis.py:231, 245-246]. WRITE-07's diff-after ("counts, skipped conflicts by book, failures grouped by
`failure_class()`") needs the EXECUTE job's result dict to carry BOTH `WriteResult` and the engine-call
`failures` list explicitly — one does not subsume the other.
**How to avoid:** Design the EXECUTE job's return dict shape to include `write_result: WriteResult` AND
`engine_failures: [(book, reason), ...]` as siblings, from the start.

## Code Examples

### The `_run` dispatch pattern every new verb reuses
```python
# Source: plugin/action.py:302-312 (existing, unmodified)
def _run(self, description, func, args, done=None):
    """`done` lets a caller own its own completion (the settings dialog shows a probe result
    inline instead of in a dialog). It is wrapped here, so a caller cannot forget to — an
    unwrapped callback runs on the WORKER thread and touches Qt from it."""
    t0 = time.monotonic()
    job = ThreadedJob('scourgify', description, func, args, {},
                      Dispatcher(done or self._done))
    self.gui.job_manager.run_threaded_job(job)
```

### The identity-check pattern every PLAN/EXECUTE job reuses
```python
# Source: plugin/action.py:40-59 (existing, unmodified)
def _open(lib_path, lib_uuid, now_uuid=None):
    from scourgify import common
    common.set_library(lib_path)
    if lib_uuid and now_uuid is not None:
        try: current = now_uuid()
        except Exception: current = None
        if current and current != lib_uuid:
            return None, ('The library changed since you clicked, so nothing was read.\n'
                          'Open the scourgify menu again.')
    return common.ro_connect(), None
```
For an EXECUTE job that WRITES, the equivalent bind step must resolve `api = gui.current_db.new_api`
(passed in from `action.py`, mirroring `job_db_smoke`'s existing `(lib, uuid, self.gui.current_db.new_api,
self.current_uuid)` argument tuple pattern) [VERIFIED: plugin/action.py:297-300], NOT a fresh
`common.ro_connect()` — writes must go through the live handle so the GUI's in-memory cache stays
authoritative (this is why `common.write_ops` takes `api`, not a connection).

### The engine-verification pattern to reuse for every engine construction in this phase
```python
# Source: plugin/config.py:49-69 (existing, unmodified) — THE pattern for Pattern 3's fix
def job_verify(engine, key, abort=None, log=None, notifications=None):
    from scourgify import engines
    if engine not in engines.ENGINE_ENV:
        return {'engine': engine, 'ok': False, 'cls': '', 'detail': 'on-device — nothing to verify'}
    try:
        eng = engines.ENGINES[engine]('', 30, env={engines.ENGINE_ENV[engine][0]: key})
    except Exception as e:
        return {'engine': engine, 'ok': False, 'cls': engines.AUTH,
                'detail': engines.redact('%s' % e, key)}
    out, reason = engines.ask_retry(eng, 'Reply with the single word: ok', tries=1)
    return {'engine': engine, 'ok': bool(out and not reason), 'cls': engines.failure_class(reason),
            'detail': reason or (out or '').strip()[:80]}
```

### Calibre's GUI-refresh call after a write (cross-checked against Calibre's public source)
```python
# Pattern confirmed in Calibre's own edit_metadata.py (GUI thread, called from a
# Dispatcher-wrapped callback — never from the worker thread):
def refresh_books_after_metadata_edit(self, book_ids):
    m = self.gui.library_view.model()
    m.refresh_ids(list(book_ids))
    # optionally, preserving the current row:
    # cr = self.gui.library_view.currentIndex().row()
    # m.refresh_ids(list(book_ids), cr)
```
[CITED: github.com/kovidgoyal/calibre/blob/master/src/calibre/gui2/actions/edit_metadata.py — `refresh_ids`
calls confirmed in `refresh_books_after_metadata_edit`, `do_edit_bulk_metadata`, and `finalize_apply`]. The
plugin's EXECUTE job's Dispatcher-wrapped `done` callback should call `self.gui.library_view.model()
.refresh_ids(list(touched_book_ids))` after showing the result dialog (or before — order does not matter,
both run on the GUI thread synchronously inside the same callback).

### The `Plan.restrict(ids)` pattern for scoping a PLAN job to the selection
```python
# Source: src/scourgify/wrangle.py:373-389 (existing, unmodified)
def restrict(self, ids) -> "Plan":
    """Narrow this plan to `ids` and return self. The full-library compute STAYS — transform
    needs global context (tagcanon majority spelling, known_chars), so scoping the read would
    silently change the answer for the selected books. Only the write set narrows."""
    keep = set(ids)
    self.scope = f"{len(keep)} books"
    for lab in list(self.changes):
        kept = {b: v for b, v in self.changes[lab].items() if b in keep}
        if kept: self.changes[lab] = kept
        else: del self.changes[lab]
    self.diffs = collections.defaultdict(dict, {b: d for b, d in self.diffs.items() if b in keep})
    ...
    return self
```
The wrangle PLAN job for a selection is exactly: `wrangle.plan(cfg, maps).restrict(ids)`. **Cost note:**
`wrangle.plan()` itself is a FULL-LIBRARY compute (870 ms measured — see Common Pitfalls in the NLSpec
excerpt above), so restricting only narrows the WRITE set; the PLAN job's dispatch/compute cost is the same
whether one book or the whole library is selected. This is a known, accepted cost (matches the CLI's own
`audit`/`apply --books` behaviour) — not a regression to fix this phase.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|---------------|--------|
| CLI-only write path: `run_writer` shells to `calibre-debug -e _writer.py`, refuses if Calibre is open | In-process write path: `write_ops` writes through `gui.current_db.new_api` directly, no subprocess, no "close Calibre first" | Phase 1 (landed) | This phase is the FIRST to actually call `write_ops` from a real UI surface — Phase 1 built and unit-tested the funnel but wired zero write verbs to it |
| Review checklists (`ui.checklist`) hardcoded to the wizard's rich TTY prompts | `decide=` injection seam on 6 of 7 checklist sites, `(accepted_idx, rejected_idx, action)` shape | Phase 1 plan 01-05 (landed) | The Qt picker can now intercept every checklist except `wrangle.Plan.step()`, which still needs its own `decide=` threaded in this phase |
| Engine construction always reads `os.environ` | Engine constructors accept `env=` (Phase 1 plan 01-05, per NLSpec B5 amendment) but the tools' OWN default-engine-construction branches (`Plan.run`, `promote.run`) never pass one through | Constructor seam: Phase 1. Threading it through `Plan.run`'s default branch: **still open — this phase's job** | See Pattern 3 / Pitfall 1 — this is the single highest-value gap this research surfaced |
| Every write producer calls `run_writer(...)` by name | **Not yet changed** — `write=` injection is this phase's first task | This phase | Blocks WRITE-01 through WRITE-05 equally; should be the very first plan task |

**Deprecated/outdated:** Nothing in this phase deprecates prior work — it is additive: new Qt modules, new
keyword parameters on existing functions with backward-compatible defaults.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | A plain `QDialog` (not Calibre's `calibre.gui2.widgets2.Dialog` geometry-persisting base) satisfies every locked decision for the picker/result dialogs | Standard Stack, Alternatives Considered | Low — cosmetic only; window position/size won't persist between opens, easily fixed later without touching the data contract |
| A2 | Splitting `job_*` bodies into a new Qt-free `plugin/jobs.py` module is a viable, CI-testable architecture, not yet decided by CONTEXT.md | Architecture Patterns, Alternatives Considered, Validation Architecture | Medium — if the planner adopts this without confirming it doesn't conflict with `tests/test_plugin_source.py`'s existing MODULES/QT_MODULES assumptions or the "#72 job runner" discretion item's exact shape, rework may be needed; recommend a `checkpoint:human-verify` before committing to it |
| A3 | Synopsis's picker either (a) grows its own `decide=` for the internal `step()` review, or (b) skips per-book review entirely like classify (D-05's shape) | Architecture Patterns Pattern 5, Open Questions #1 | Medium — CONTEXT.md's Claude's Discretion for synopsis only says "engine picker is D-07's widget," leaving the review-step question genuinely open; guessing wrong means rework on the synopsis verb specifically, not a systemic risk |
| A4 | `calibre.gui2.threaded_jobs.ThreadedJob`'s exact constructor/callback behaviour (verified against Calibre's GitHub `master` branch, not the specific 9.11.0 version this repo pins in CI) matches what's installed locally | Standard Stack, Code Examples | Low — this repo's own `plugin/action.py` docstring already independently confirms the same behaviour "disassembled, Calibre 9.11," so this is a redundant cross-check, not the sole source |
| A5 | `refresh_ids(book_ids)` (no `current_row` arg) is sufficient for this phase's diff-after refresh; the `current_row`-preserving overload is a nicety | Code Examples | Low — omitting `current_row` just means the library view's current selection/row might jump after a refresh; a minor UX nit, not a data-safety issue |

**If this table is empty:** N/A — five assumptions logged above, all LOW-to-MEDIUM risk, none touching data
safety, security, or compliance.

## Open Questions

1. **Does the plugin's synopsis verb offer a per-book "Review 1-by-1" tick-list before writing, or does it
   follow classify's D-05 shape (engine pass + write in one job, diff-after is the net)?**
   - What we know: `synopsis.Plan.run()` today calls `step()` internally with NO externally injected
     `decide=`, conflating engine-call, optional review, and write into one method [VERIFIED: src/scourgify/
     synopsis.py:218-268]. CONTEXT.md's Claude's Discretion for WRITE-04 only settles the engine picker and
     the FFF-guard wording, not this.
   - What's unclear: Whether D-02's generic "Review 1-by-1" picker control should apply to synopsis (which
     would require plumbing a NEW `decide=` through `Plan.run()` down to its internal `step()` call — real,
     non-trivial work) or whether synopsis should mirror classify exactly (no pre-write review, diff-after +
     retry only).
   - Recommendation: Default to classify's shape (D-05) for consistency and less new plumbing, unless the
     planner has explicit guidance otherwise — flag as a `checkpoint:human-verify` decision point in the plan
     rather than resolving it silently either way, since it changes the shape of `Plan.run`'s signature.

2. **Should `job_*` function bodies move to a new Qt-free `plugin/jobs.py` for CI testability, or stay inline
   in `action.py` as today?**
   - What we know: Today's `job_*` functions live inside `action.py`, which imports `qt.core` at module
     level — meaning `action.py` cannot be imported under CI's plain Python, so `job_*` bodies are ONLY
     verified by AST source-reading (structure), never by actually calling them with a fixture db. A
     Qt-free `jobs.py` (no `from qt.core import ...` anywhere in the file) WOULD be importable and callable
     in CI with `tests/fixture_db.py`, closing a real coverage gap this phase otherwise inherits.
   - What's unclear: Whether this is in scope for #72's "extend `action._run` upward" discretion item, or
     whether it's scope creep beyond what CONTEXT.md authorized. It also interacts with
     `tests/test_plugin_source.py`'s `MODULES`/`QT_MODULES` lists and its "no core import outside
     `job_*`/`_`-prefixed functions in `action.py`" rule, which would need updating either way.
   - Recommendation: Worth raising with the user as a discuss-phase-style question before planning locks in
     the file layout — it materially changes how much of WRITE-01..08 is CI-testable versus
     `calibre-debug`/manual-only (see Validation Architecture below).

3. **The `#72` discretion item says "amend `tests/test_plugin_source.py`'s action.py import rule to match
   its own docstring" — what discrepancy does this refer to, precisely?**
   - What we know: The test module's own docstring bullet 2 says "Every core import lives inside a `job_*`
     function." The actual assertion (`test_no_core_import_can_run_on_the_gui_thread`) allows core imports
     inside EITHER a `job_*`-prefixed function OR any `_`-prefixed private helper — which is what today's
     `_open`, `_rejects_by_book`, `_archives_by_book`, `_library_scope` helpers already rely on (all import
     `scourgify` and are called BY `job_*` functions, never directly by Qt code) [VERIFIED: tests/
     test_plugin_source.py:82-99, plugin/action.py:40, 127, 149, 163].
   - What's unclear: Whether the fix is (a) loosening the DOCSTRING to say "job_* functions and their
     private helpers" (matching the actual, safe rule), or (b) tightening the ASSERTION to require literal
     `job_*` prefixing everywhere (which would break the four existing helpers above and force a rename).
   - Recommendation: (a) is almost certainly correct — the current rule is already safe (a `_`-prefixed
     helper is only reachable from a `job_*` function by construction, since nothing else calls it) — but
     confirm with the user before touching a passing, already-battle-tested test file's assertions.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Calibre GUI application | Manual verification of every Qt-visible behaviour this phase adds (menu items, dialogs, dispatch, refresh) | ✓ (`/Applications/calibre.app` present on this machine) | Not probed this session (app bundle inspected for file layout only, not launched) | `plugin/selftest.py` via `SCOURGIFY_SMOKE=1 calibre --with-library <throwaway>` — a manual, local-only pre-release check, not automatable in this research session or in CI |
| `calibre-debug` CLI | Headless core-import smoke (`tests/smoke_calibre.py`, already in CI) — does NOT load the plugin/Qt layer | ✓ (`/Applications/calibre.app/Contents/MacOS/calibre-debug` present) | Not probed this session | N/A — CI already covers this on Windows/Linux lanes; this phase adds no new CI dependency here |
| Qt (`qt.core`) with a real display / GUI event loop | Any interactive verification of the picker/result dialogs | Not probed this session — would require launching Calibre's GUI, which this research session did not do (per the phase's own cost/safety constraints, no live-library interaction was performed) | — | AST/source-grep tests (`tests/test_plugin_source.py`, `tests/test_plugin_safety.py`) cover STRUCTURE without a display; behaviour needs `plugin/selftest.py` run locally by a human |
| An LLM API key (any of `ANTHROPIC_/OPENAI_/GEMINI_/MISTRAL_API_KEY`) | Testing classify/promote/synopsis engine picker end-to-end | Not checked this session — out of scope for research (would risk a paid call per this phase's stated cost constraint) | — | `apple` engine (on-device, free) if a swift toolchain or the `afm` binary is present on macOS 26+; otherwise every cloud-engine verb greys with "no key" per existing `engines.usable_engines()` logic |

**Missing dependencies with no fallback:** None outright block PLANNING — `plugin/selftest.py`'s manual
check and a live GUI session are pre-release/execution-time concerns, not planning blockers.

**Missing dependencies with fallback:** A live Calibre GUI session (fallback: AST tests + `selftest.py` run
later, by a human, per the existing pattern this repo already uses for Phase 1's GUI-thread claims).

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | Plain-assert Python files, pytest-compatible, no framework required [VERIFIED: existing tests/test_*.py pattern, CLAUDE.md] |
| Config file | none — `for t in tests/test_*.py; do uv run "$t" || exit 1; done` (the repo's own `test_command`) [VERIFIED: .planning/config.json workflow.test_command] |
| Quick run command | `uv run tests/test_plugin_source.py` (source-grep, no Calibre/Qt needed, sub-second) |
| Full suite command | `for t in tests/test_*.py; do uv run "$t" || exit 1; done` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| WRITE-08 | Plugin never calls `run_writer`/subprocess/`ThreadPoolExecutor`; exactly one `ThreadedJob` site; every callback `Dispatcher`-wrapped | source-grep (AST) | `uv run tests/test_plugin_source.py` | ✅ exists — extend `MODULES` for new files |
| WRITE-01/02/03/04/05 (job-function LOGIC: PLAN preview data, EXECUTE ops assembly, `write=`/`engine=`/`model=` threading) | unit, against `tests/fixture_db.py` fixtures, calling the CORE tool-module functions directly (`wrangle.plan().restrict(ids).preview()`, `classify.apply_proposal(rows=..., write=fake_write, engine=..., model=...)`, etc.) — NOT the Qt `job_*` wrappers | unit | `uv run tests/test_classify_run.py`-style new/extended test files | ❌ Wave 0 — new assertions on the new `write=`/`engine=`/`model=` keyword parameters |
| WRITE-01/02/03/04/05 (Qt structure: picker/result dialog exist, follow D-01/D-02/D-09 shapes, don't build a second `ThreadedJob`, don't import `ui`) | source-grep (AST) | `uv run tests/test_plugin_source.py` (extended) | ❌ Wave 0 — new assertions for `picker.py`/`result_dialog.py` once they exist |
| WRITE-06 (edit-log header carries `engine=`/`model=` for a classify EXECUTE job) | unit — already provable without Qt: call `common.write_ops(fake_api, ops, engine="openai", model="gpt-4o-mini")` against a fake `api` and assert the `edits.jsonl` header carries both | unit | `uv run tests/test_editlog.py` (extended) or a new test | ❌ Wave 0 — this is the CHEAPEST, highest-confidence test in the whole phase; write it first |
| WRITE-07 (diff-after data shape: `WriteResult` + engine failures, both present in the EXECUTE job's result dict) | unit — testable on the plain-Python result-dict-building helper, if factored out of the Qt job wrapper (see Open Question #2) | unit | new test file, e.g. `tests/test_plugin_jobs.py` (Qt-free) | ❌ Wave 0 — depends on Open Question #2's resolution |
| WRITE-01..07 (actual GUI behaviour: menu greying, dialog contents render correctly, click dispatches, library view refreshes) | manual / `calibre-debug` smoke | `SCOURGIFY_SMOKE=1 calibre --with-library <throwaway>` driving `plugin/selftest.py` (extended with new steps per verb) | manual-only (not in CI; same status as today's Phase 1 steps) | ❌ Wave 0 — extend `plugin/selftest.py`'s step list with one round-trip per verb, following `step_menu_n`/`_run_verb`'s existing pattern |
| WRITE-08 (lock greying: a second write-run is refused with a visible reason naming the running job) | unit (core lock, already covered by Phase 1's tests) + manual (Qt-side instant grey) | `uv run tests/test_write_path.py` (core lock, already passing) + `plugin/selftest.py` (Qt-side grey, new step) | Partial — core half ✅ exists; Qt half ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `uv run tests/test_plugin_source.py` + the specific unit test file touched by the task (e.g. `uv run tests/test_classify_run.py` after touching `apply_proposal`'s signature).
- **Per wave merge:** `for t in tests/test_*.py; do uv run "$t" || exit 1; done` (full suite, CI-equivalent).
- **Phase gate:** Full suite green before `/gsd-verify-work`; `plugin/selftest.py` run manually at least once
  against a real Calibre GUI before the phase is considered UAT-complete (mirrors this repo's existing
  precedent — `plugin/selftest.py` was run and its measured numbers recorded in Phase 1's NLSpec amendments
  rather than merely asserted).

### Wave 0 Gaps
- [ ] `tests/test_write_path.py` or a new file — pin `write=`/`engine=`/`model=` keyword parameters on all
      six write-producing functions (Pattern 1), with default-argument behaviour asserted unchanged (a
      regression test that calling with no `write=` still calls `run_writer` exactly as before).
- [ ] `tests/test_editlog.py` (extend) — pin that `write_ops(..., engine=, model=)` reaches the edit-log
      header exactly as `run_writer` cannot for classify (WRITE-06's core-side half — cheapest test in the
      phase, should land before any Qt work starts).
- [ ] `tests/test_plugin_source.py` (extend `MODULES`/`QT_MODULES`) — once `picker.py`/`result_dialog.py`
      exist, add them to the lists and write the D-01/D-02/D-09/D-11 structural assertions (e.g. "the picker
      module never imports `scourgify.ui`," "exactly one `QDialog` subclass," if the planner wants
      structural pins beyond the four existing generic assertions).
- [ ] `plugin/selftest.py` (extend) — one round trip per verb (PLAN → picker data → EXECUTE → result data),
      following the existing `_run_verb`/`step_inspect` pattern; this is the ONLY mechanism that exercises
      real Qt dialogs and the real `ThreadedJob` machinery, and it already exists for the read-only Phase-1
      verb, so extending it is additive, not new infrastructure.
- [ ] A Wave-0 decision (not a test file) on Open Question #2 (Qt-free `jobs.py` split) — this determines
      whether WRITE-01..07's job-function-level behaviour is CI-testable at all, or entirely
      `selftest.py`/manual. Resolving it EARLY changes the shape of every subsequent task.

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | Single-user desktop app; no auth boundary this phase touches |
| V3 Session Management | no | N/A — no sessions |
| V4 Access Control | no | N/A — local process, single user, no privilege boundary |
| V5 Input Validation | yes | Book titles/descriptions rendered in Qt widgets are untrusted (FanFicFare-imported metadata) — use plain-text Qt widgets (`QLabel.setTextFormat(Qt.PlainText)` or plain string args, never `setText()` with rich-text mode enabled on unsanitized library data) so a title containing `<`/`&` cannot be mis-rendered as HTML inside the picker/result dialog. `GuardrailError` messages are already plain, program-authored strings — safe to render verbatim (D-11) |
| V6 Cryptography | n/a | No new crypto this phase; key storage (`JSONConfig`, plaintext-with-banner) is a Phase-1/settled decision, not reopened here |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| A key reaching a job description, log line, CSV, or error dialog | Information Disclosure | `engines.redact(msg, *secrets)` — already the single choke point every `ask_retry` failure reason passes through; new code (the result dialog rendering a refusal reason) must render `engines.redact`-ed text, never a raw exception string [VERIFIED: src/scourgify/engines.py:310-317, 320-336] |
| A second write process racing the GUI's live library handle | Tampering (data corruption) | Already closed by WRITE-08's own enforcement (`run_writer`/subprocess banned from the plugin, single `ThreadedJob` site, `_write_run`'s process-wide lock) — this phase's job is to NOT reopen it while wiring six new call sites |
| An unparsed `ValueError` from `ops.apply_ops` (Pitfall 3/5) reaching `gui.job_exception` with a raw traceback | Information Disclosure (implementation details) / availability annoyance, not a security-critical leak, but breaks D-11's "clean refusal" contract | Pre-flight-check for the `create_column`-needed case before calling `write_ops`, converting to `GuardrailError` at the tool-module boundary rather than letting a bare `ValueError` surface |
| A malicious/malformed book title rendered as rich text in a Qt label | Rendering-only, not a real security boundary (single-user desktop app, no cross-user trust boundary) — included for completeness per V5 | Plain-text rendering for any user-authored/imported string (title, description snippet) shown in the picker/result dialog |

## Sources

### Primary (HIGH confidence — read directly this session)
- `plugin/action.py`, `plugin/config.py`, `plugin/selftest.py`, `plugin/__init__.py` — full read
- `src/scourgify/common.py` (write funnel: `write_ops`, `run_writer`, `_write_run`, `WriteResult`,
  `_acquire_write_lock`, `check_wipe`, `backup_db`, `op_set_field`/`op_create_column`/`op_stamp_now`) — full
  read of the relevant sections
- `src/scourgify/ops.py`, `src/scourgify/editlog.py` — full read
- `src/scourgify/wrangle.py` (`Plan` class), `src/scourgify/staleness.py`, `src/scourgify/classify.py`,
  `src/scourgify/synopsis.py`, `src/scourgify/promote.py`, `src/scourgify/engines.py`, `src/scourgify/
  select.py`, `src/scourgify/ui.py`, `src/scourgify/setup.py` (partial), `src/scourgify/wizard.py` (partial)
  — full or targeted read of every write-producing / decide-seam / engine-construction call site
- `tests/test_plugin_source.py`, `tests/test_plugin_safety.py` — full read
- `.planning/phases/02-write-verbs-on-a-selection/02-CONTEXT.md`, `.planning/REQUIREMENTS.md`,
  `.planning/STATE.md`, `.planning/config.json` — full read
- `.planning/phases/01-foundation-a-hostable-core/01-05-SUMMARY.md`, `01-06-SUMMARY.md` — targeted read for
  Phase 1's exact decide=/write= precedent
- `docs/superpowers/specs/2026-08-06-calibre-plugin-nlspec.md` — full read (B1–B8, Constraints, the
  atomicity contract, all phase amendments)
- `.planning/codebase/CONCERNS.md`, `.planning/codebase/INTEGRATIONS.md` — targeted read

### Secondary (MEDIUM confidence — WebFetch against Calibre's public GitHub source)
- `github.com/kovidgoyal/calibre/blob/master/src/calibre/gui2/threaded_jobs.py` — `ThreadedJob.__init__`
  injecting `notifications`/`abort` into kwargs, `start_work` calling `self.callback(self)` from the worker
  thread (cross-checks this repo's own docstring claim, does not contradict it)
- `github.com/kovidgoyal/calibre/blob/master/src/calibre/gui2/actions/edit_metadata.py` — `refresh_ids(...)`
  call shapes after a metadata write, confirming the GUI-thread refresh pattern this phase's result dialog
  needs

### Tertiary (LOW confidence)
- None used as load-bearing claims — every factual claim above traces to either a direct source-read this
  session or a cross-checked, cited web fetch of Calibre's own public source.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new packages; every library involved is already imported and used by this
  exact repo today.
- Architecture: HIGH for the write-funnel/seam layer (fully read this session); MEDIUM for the exact Qt
  dialog/widget choices (locked decisions specify data/behaviour, not literal Qt class names — reasonable
  latitude for the planner within D-01/D-02/D-09).
- Pitfalls: HIGH — every pitfall traces to a specific, cited line in this repo's own source, not speculation.

**Research date:** 2026-09-08
**Valid until:** ~14 days (this is an actively-developed phase of a fast-moving milestone; Phase 3+ work or
further Phase 1 patches could shift the exact line numbers cited above, though the architectural findings
themselves — the `write=` gap, the engine-`env=` gap, the `ask` envelope asymmetry — are structural and will
outlive minor refactors).
