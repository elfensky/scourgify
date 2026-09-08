# Phase 2: Write verbs on a selection - Context

**Gathered:** 2026-09-08
**Status:** Ready for planning

<domain>
## Phase Boundary

Every wizard stage becomes a verb on the selected books in the toolbar menu: wrangle,
staleness, classify, synopsis, promote and backfill. Each verb is a three-step round trip —
preview computed in a PLAN job, decision taken in a Qt picker that makes no core call, write
done in an EXECUTE job through `common.write_ops` — with the price or consequence on the
control instead of a confirmation dialog, every write logged per op, and a diff-after that
names what was skipped and what failed. Requirements WRITE-01..08 (NLSpec phase 6, #59).
Lands the plugin job runner's per-job ceremony (#72) and the one ask envelope that carries
failure classes to promote's ledger (#73). Classify is built first: the engine picker and the
cost display are the highest-stakes UI. No setup flow (Phase 3), no dashboard (Phase 4), no
review-in-library-view or undo button (Phase 5). The CLI and wizard stay unchanged (PUB-05).

</domain>

<decisions>
## Implementation Decisions

### The picker between PLAN and EXECUTE (WRITE-01, WRITE-02, WRITE-04, WRITE-05)
- **D-01:** ONE verb-parameterised picker `QDialog` serves every write verb. The PLAN job
  hands it plain data — summary rows, the SAFETY line, the consequence label for the Run
  button, the review items — and verbs differ only in that data. A new verb gets the dialog
  for free; it is the third adapter that makes the #72 job-runner seam real.
- **D-02:** Default view is **summary + Run**, with the consequence on the button (`writes ·
  37 books`, or the price). A **"Review 1-by-1"** control expands the per-book tick-list,
  all pre-ticked, mirroring `ui.checklist` (apply ticked / all / skip). Both paths go through
  Phase 1's `decide=` seam (D-11 there): the PLAN job's `decide` records the items and answers
  `skip`; the EXECUTE job's `decide` replays the ticks — all ticked on the one-click path.
  The one-click path stays one click; the review path costs one expand.
- **D-03:** Review items become **`(label, payload)` tuples**. `label` is the one-line string
  the wizard prints today; `payload` is a dict carrying book id, title, field/column, before,
  after (plus wrangle's `kind`/`class` where the reject log needs them). `ui.checklist` keeps
  reading only the label, so the wizard is unchanged; the Qt list renders before/after
  columns from the payload. — **Reversibility:** costly — the shape change touches all seven
  `decide=` sites (classify, promote ×2, overrides ×2, staleness, synopsis) and the tests that
  pin them, and Phase 4 History and Phase 5 review will read the same payload fields.
- **D-04:** A **clean stage opens no dialog**: the PLAN job returns empty and the user sees a
  one-line notice ("nothing to change for these N books"). Mirrors the wizard's auto-skip.

### Classify from the menu (WRITE-03)
- **D-05:** The engine pass and the write are **ONE job**: tags land, every processed book
  stamps, and the edit-log header carries `engine` + `model` through `write_ops(engine=,
  model=)` — the seam the B7 phase-3 amendment left for exactly this. The applied proposal is
  still archived in the CLI's `artifacts.py` format so `classified_ids()`, History and the CLI
  agree. Diff-after + undo is the net, not a dry-run-before. — **Reversibility:** reversible —
  a "keep as proposal" path is the same job minus the write (deferred below).
- **D-06:** **Two steps, price after resolve.** Step 1 is the scope dialog on the fixed slots
  `classify.scope_options` already defines (selection / new-changed / never-classified with a
  batch size / whole library; the menu's selection is the natural default when N > 0). Step 2
  is a PLAN job that resolves the todo set **once** through `classify.plan()` / `select.py`.
  Step 3 is the engine picker over that exact set. The cost is `classify.est_cost` over the
  resolved todo, labelled an estimate; the core's >200-book cloud gate (`ask=`) is answered
  by the scope step (B4.5) — no second dialog.
- **D-07:** The engine picker is **one button per engine**, built from `engines.engine_rows`
  / `engine_options`: name, `~$0.42 for 37 books` or `free`, the TRAITS failure-mode text with
  its measurement date. **Clicking the button IS the run.** Engines without a key are greyed
  with "no key"; apple is absent off-macOS (XPLAT-02). No radio list, no separate Run button.
- **D-08:** A running classify reports through **Calibre's jobs panel only**:
  `notifications.put(fraction, "tagged 12 · failed 1 · 3.1 books/min")` fed by classify's
  per-book callback; abort comes from the job's `abort` and is honoured between books, with
  the footer marked `cancelled`. No new progress widget — the live surface is Phase 4
  (DASH-04).

### Diff-after and retry (WRITE-07)
- **D-09:** The result is a **non-modal result `QDialog` with per-book rows**: a summary line
  (written / skipped-by-conflict / failed, grouped by `engines.failure_class`), then a table of
  rows — book, field, before → after; or `skipped: changed since the plan`; or `refusal on
  gemini`. It reuses the picker's row widget. Its data is `common.WriteResult` (the Phase 1
  D-08 `skipped` list) plus the run's failures. This dialog is where Phase 5 attaches undo.
- **D-10:** **"Retry on <engine>" lives in both places.** In the result dialog: one button per
  refused group per non-refusing usable engine (`Retry 5 on openai — ~$0.03`), **direct
  dispatch with the price on the button, no picker in between**; apple's button says free. In
  the menu: the existing fixed slot comes alive, keyed off refusal-class rows in the failure
  log for the selection. Both call the same job function. Non-refusal classes (auth / quota /
  timeout / parse) get a plain "Retry" on the same engine, per B1.
- **D-11:** A **guard refusal renders in the same result dialog as a refused state**: the
  `GuardrailError` text verbatim ("would strip the last fandom from 3 books"; "a write-run is
  already live: classify 37 books"; "FanFicFare Comments is not set to New Only"), zero rows,
  the job ending cleanly. The job wrapper converts `GuardrailError` to a result; only a real
  exception still reaches `gui.job_exception`.

### Plan-phase additions (settled 2026-09-09, from 02-RESEARCH.md open questions)
- **D-12:** The job bodies live in a **Qt-free `plugin/jobs.py`**. `action.py` keeps the ONE
  `ThreadedJob` site (`_run`), the menu, and the Qt wiring; `jobs.py` holds each verb's PLAN and
  EXECUTE logic and the plain-dict result builders, importing no Qt. That is what makes
  WRITE-01..07's job-level behaviour (ops assembly, the diff-after result shape) reachable from a
  plain-assert CI test with no Calibre and no GUI, instead of only through `plugin/selftest.py`
  under a real Calibre. `jobs.py` joins `MODULES` in `tests/test_plugin_source.py`.
- **D-13:** The **synopsis verb offers a per-book review before the write** — D-02's tick-list over
  the proposed descriptions, untick to reject — rather than running straight through like classify.
  This extends CLAUDE.md's 1-by-1 review invariant to synopsis. Accepted with its cost understood:
  the pass is a ~40 s/book background sweep, so a per-book gate is a real human bottleneck; the
  batch size is what keeps a review session finite.
- **D-14:** #72's import-rule amendment resolves by **tightening the code to its docstring**:
  `tests/test_plugin_source.py`'s `action.py` check drops the `func.startswith("_")` half, so a core
  import in `action.py` must sit inside a `job_*` function, full stop. Today only `_open()` uses that
  exemption, and D-12 moves it to `jobs.py` with the job bodies — after the split the tightening
  should cost nothing. If something in `action.py` genuinely still needs the core, that is a signal
  it belongs in `jobs.py`, not a reason to keep the exemption.

### Claude's Discretion
- **PLAN → EXECUTE hand-off:** the PLAN job returns a plain, Qt-free result (summary, items
  with payload, the ops list with `expected` values, the consequence label). The EXECUTE job
  applies exactly those ops through `write_ops` — no recompute; the apply-time conflict filter
  is the staleness net. Classify's PLAN result carries the resolved todo ids and the per-engine
  cost table; its EXECUTE job runs the engine and writes, in one job. Exact result dict shape
  is the planner's.
- **Job runner (#72):** extend `action._run` upward so a job function becomes `(con, ids) →
  result` with open/bind/close and the result contract owned in one place; collapse
  `_library_scope`'s eight-argument signature; amend `tests/test_plugin_source.py`'s action.py
  import rule to match its own docstring. `_run` stays the ONE `ThreadedJob` site.
- **Lock greying (success criterion 5):** `build_menu` may not import the core, so the menu
  must learn a write-run is live without a core call — action-side state set at dispatch and
  cleared in the Dispatcher-wrapped completion is sufficient (the plugin is the only in-process
  writer). The core lock (Phase 1 D-06) remains the authoritative refusal, reached in the
  EXECUTE job and rendered via D-11.
- **Writer seam on the tool modules:** `wrangle.Plan.write`, `staleness.write`,
  `classify.apply_proposal`, `promote.backfill`, `synopsis.Plan` write, and `setup` still call
  `run_writer` directly; they gain an injected `write=` (default `run_writer`) so a job passes
  a `write_ops`-bound callable — the shape NLSpec B3's phase-3 amendment named. Source tests
  keep `run_writer(` out of the plugin.
- **#73:** promote's `ask`/`verify_ask` adopt classify's `(text, err)` envelope so `decide()`
  records the failure class in the ledger reason; no shared driver.
- **Synopsis (WRITE-04):** when FanFicFare's Comments "New Only" is off, the verb greys with
  that reason and the picker offers the degraded self-healing mode as an explicit choice; the
  engine picker is D-07's widget (apple default where present). Wording is the planner's.
- **Promote / backfill (WRITE-05):** the adjudication engine comes from D-07's picker with the
  `judge` trait (apple greyed `cannot_judge`); the per-candidate verdict review is D-02's
  tick-list; an unticked verdict gets no ledger row. Backfill previews the book ↔ tag adds.
- **Library-view refresh:** which Calibre call refreshes the touched ids from the
  Dispatcher-wrapped callback; dialog parenting and non-modality details; cancelled-run
  rendering in the result dialog; batch-size default for never-classified; workers count;
  the "Classify the never-classified here" shortcut on a mixed selection.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Acceptance and scope
- `.planning/ROADMAP.md` — Phase 2 goal, success criteria 1–5, "build classify first"
- `.planning/REQUIREMENTS.md` — WRITE-01..08 (this phase); SETUP-*/DASH-*/REVIEW-* for what
  consumes this phase's dialogs and results; PUB-05 standing rule
- `.planning/PROJECT.md` — constraints, Out of Scope (no confirmation dialogs), engine facts
- `docs/superpowers/specs/2026-08-06-calibre-plugin-nlspec.md` — "The atomicity contract";
  B1 (menu, fixed slots, consequence on the item, "Retry on <engine>" keyed off refusal
  class); B2 (one write path, guards, row refresh); B3 (job system, progress, the phase-3
  amendment naming the `write=` seam and the phase-5 failure taxonomy); B4 (scope resolves
  once, engine picker from TRAITS/PRICING, cost contract, cloud gate satisfied by the scope
  step, refusals are an outcome); B7 (edit-log shape; engine+model header fields the plugin
  fills)
- GitHub issue `elfensky/scourgify#59` — phase 6 scope and acceptance checklist
- GitHub issue `elfensky/scourgify#72` — plugin job runner: own the per-job ceremony
- GitHub issue `elfensky/scourgify#73` — one ask envelope so failure classes reach the ledger

### Prior decisions this phase builds on
- `.planning/phases/01-foundation-a-hostable-core/01-CONTEXT.md` — D-06 lock, D-07/D-08
  conflict filter and `skipped` reporting, D-09 `expected=` on ops, D-10 option builders'
  home, D-11 `decide=` at every checklist site (this phase's PLAN/EXECUTE split is a `decide`)
- `.planning/phases/01-foundation-a-hostable-core/01-06-SUMMARY.md` — `op_set_field(...,
  expected=)`, `common._check_conflicts`, `common.WriteResult.skipped`, `editlog.finish(...,
  skipped=)`, the all-skipped early exit
- `.planning/phases/01-foundation-a-hostable-core/01-05-SUMMARY.md` — the relocated pure
  builders (`classify.scope_options`, `engines.engine_options(cost_fn)`, `synopsis.options`)

### Repo rules and existing plugin code
- `CLAUDE.md` — write path, edit log, `GuardrailError` rule, plugin invariants, uv-only,
  linear `develop`
- `plugin/action.py` — the ONE `ThreadedJob` site (`_run(done=)`), `_open` identity check,
  `build_menu` fixed slots and `_verb`, `job_inspect` as the read-job template
- `plugin/config.py` — the `done=` callback pattern a dialog uses to own its completion
- `plugin/selftest.py` — the GUI-thread stall measurement every new dispatch must keep < 100 ms
- `.planning/codebase/CONCERNS.md` — wipe-guard coarseness on scoped runs, FFF guard
  fragility, apple single-threaded, gemini refusal rate
- `.planning/codebase/INTEGRATIONS.md` — engine endpoints, artifact formats, edit-log shape

### Pinned shapes to keep green (and extend)
- `tests/test_plugin_source.py` — no `run_writer(`/`subprocess`/`ThreadPoolExecutor` in the
  plugin, one `ThreadedJob` site, Dispatcher-wrapped callbacks, job signature, no module-level
  core import (rule to be amended per #72)
- `tests/test_plugin_safety.py` — rich-blocked imports, no `SystemExit` job-reachable
- `tests/test_cli.py`, `tests/test_wizard_flow.py`, `tests/test_wizard.py` — wizard unchanged
  through the `(label, payload)` change
- `tests/test_editlog.py`, `tests/test_write_path.py` — record shape with `skipped`, one ops
  list through both write shapes
- `tests/smoke_calibre.py` — the `calibre-debug` smoke in CI on Windows and Linux

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `plugin/action.py::_run(description, func, args, done=None)`: the one dispatch; a picker
  or result dialog owns its completion through `done=` exactly as `config.py`'s key probe does.
- `plugin/action.py::_open(lib_path, lib_uuid, now_uuid)`: library-identity check every job
  reuses; `job_inspect` is the template for a read job's shape and abort/notifications use.
- `classify.scope_options(ch, total, outstanding)`, `classify.proposal_options`,
  `engines.engine_rows(env)`, `engines.engine_options(engs, n_todo, cost_fn)`,
  `engines.default_engine_id(opts, judge=, usable=)`, `synopsis.options(n)`: pure row
  builders the Qt dialogs render verbatim (fixed slots, hints, prices).
- `classify.plan(a) → Plan` with `Plan.run(ask=)`, `classify.est_cost(n, engine)`,
  `classify.apply_proposal`, `classify.apply_proposal_step(decide=)`: scope-once, cost, write.
- `wrangle.plan(cfg, m) → Plan` with `preview()/guard()/step()/write()/restrict(ids)`;
  `staleness.step(label, rows, decide=)` / `staleness.write`; `promote.apply_decisions_step(
  decide=)`, `promote.backfill_step(decide=)`, `promote.backfill(decide=)`; `synopsis.plan(a)`,
  `synopsis.step(made, titles, decide=)`; `overrides.step_pick(auto, decide=)`.
- `common.write_ops(api, ops, force=, out=, engine=, model=)` → `common.WriteResult` with
  `skipped`; `common.op_set_field(field, values, expected=)`; `common._acquire_write_lock`
  refusal text names the running job.
- `engines.failure_class(reason)`, `engines.RETRYABLE`, `engines.redact`, `engines.TRAITS`
  (`is_free`, `judge`, `platforms`, `out_tokens`, hint text): every label in the picker and
  the result dialog derives from these.
- `artifacts.read_rows(artifacts.fail())`, `artifacts.classified_ids()`,
  `artifacts.applied_proposals()`: the refusal-keyed menu verb and the archive read from these.
- `setup.comments_protected(con)`: the FFF "New Only" guard synopsis refuses on.
- `select.pick(con, mode)` modes `ids` / `changed` / `unclassified` / `last` / `all`: the
  scope dialog's slots map onto these.

### Established Patterns
- Nothing on the GUI thread but Qt: core imports live inside `job_*` functions; dispatch is
  measured in `selftest.py`. The picker and the result dialog are pure Qt over plain data.
- Fixed menu slots greyed with a reason, never hidden; `ui.menu` and the row builders return
  symbolic ids, never positions.
- Injection over environment: `set_library`, `env=`, `ask=`, `decide=`; this phase adds
  `write=` in the same shape.
- Guards raise `GuardrailError`; the job wrapper turns it into a result, not a job failure.
- One ops executor, one edit log, one conflict predicate; log before apply.
- Source-reading tests enforce architecture; plain-assert tests under `tests/`, in CI by glob.

### Integration Points
- `plugin/action.py`: verbs on the existing `SOON` slots become live; the job runner grows
  out of `_run`; action-side live-write-run state greys verbs.
- New plugin modules for the picker dialog and the result dialog (Qt only; added to
  `tests/test_plugin_source.py`'s MODULES list).
- `wrangle.py`, `staleness.py`, `classify.py`, `promote.py`, `synopsis.py`, `setup.py`:
  `write=` seam on each write function; `(label, payload)` at each `decide=` site.
- `common.write_ops`: called from the EXECUTE job with `gui.current_db.new_api`.
- `engines.py` / `promote.py`: the `(text, err)` envelope (#73).
- `tests/`: picker-data and result-data builders testable without Qt; the job functions
  testable as `(con, ids) → result`.

</code_context>

<specifics>
## Specific Ideas

- The price is literally on the control: an engine button reads like
  `openai — ~$0.42 for 37 books · cheapest usable · measured 2026-07-30`; a retry button
  reads `Retry 5 on openai — ~$0.03`; a deterministic verb's Run button reads
  `Write 37 books` or `Re-derive status on 12 books`.
- The scope dialog reuses the wizard's own row wording from `classify.scope_options`
  (`new/changed — 14 books`, `never classified — 7,681 books`), so the two front doors say the
  same thing.
- The result dialog's rows and the picker's rows are the same widget: before → after per
  `(book, field)`, with a state column (written / skipped: changed since the plan / refusal on
  gemini).
- A refused run is a normal result, not a crash: the SAFETY reason the CLI prints is the text
  the dialog shows.

</specifics>

<deferred>
## Deferred Ideas

- **"Keep as proposal" switch on the classify picker** (run the pass, write nothing, leave a
  pending proposal for review) — revisit in Phase 5 if review-in-the-library-view needs a
  plugin-produced pending proposal; today the pending proposal it reviews comes from the CLI.
- **Edit tags… verb** (a vocab-completing editor emitting `set_field` ops) — in #59's scope
  but not in WRITE-01..08; the slot stays greyed "not built yet" this phase.
- **A live progress dialog mirroring `report.Dashboard`** — Phase 4 (DASH-04).
- **Undo button on the result dialog** — Phase 5 (REVIEW-05); the dialog is built so it can
  carry one.
- **Advisory lock file for cross-process "a plugin job is writing"** — carried from Phase 1;
  still not needed while `calibre_open()` refuses CLI writes with the GUI open.

</deferred>

---

*Phase: 02-write-verbs-on-a-selection*
*Context gathered: 2026-09-08*
