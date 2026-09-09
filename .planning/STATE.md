---
gsd_state_version: 1.0
milestone: v1
milestone_name: (the terminal goes away)
current_phase: 02
current_phase_name: Write verbs on a selection
status: executing
stopped_at: Completed 02-07-PLAN.md
last_updated: "2026-09-09T11:41:07.992Z"
last_activity: 2026-09-09
last_activity_desc: Phase 02 execution started
state_head: 6b52c3f0290c7403cda0a51658694577b80191b9
progress:
  total_phases: 6
  completed_phases: 1
  total_plans: 14
  completed_plans: 13
  percent: 17
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-08-28)

**Core value:** The full maintenance loop (wrangle → staleness → synopsis → classify → review → promote → backfill) runs from a Calibre toolbar button, on the live library, with every guard, backup and undo the CLI has — and never freezes or corrupts Calibre.
**Current focus:** Phase 02 — Write verbs on a selection

## Current Position

Phase: 02 (Write verbs on a selection) — EXECUTING
Plan: 8 of 8
Status: Ready to execute
Last activity: 2026-09-09 — Phase 02 execution started

Progress: [██░░░░░░░░] 17%

## Performance Metrics

**Velocity:**

- Total plans completed: 6
- Average duration: -
- Total execution time: 0.0 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 01 | 6 | - | - |

**Recent Trend:**

- Last 5 plans: -
- Trend: -

*Updated after each plan completion*
**Per-Plan Metrics:**

| Plan | Duration | Tasks | Files |
|------|----------|-------|-------|
| Phase 01 P01 | 1h 35min | 5 tasks | 10 files |
| Phase 01 P02 | 2h 3min | 3 tasks | 31 files |
| Phase 01 P03 | 45min | 3 tasks | 6 files |
| Phase 01 P04 | 50 min | 3 tasks | 4 files |
| Phase 01-foundation-a-hostable-core P05 | 40 min | 2 tasks | 8 files |
| Phase 01 P06 | 55 min | 3 tasks | 14 files |
| Phase 02 P01 | 40min | 3 tasks | 10 files |
| Phase 02-write-verbs-on-a-selection P02 | 55min | 2 tasks | 7 files |
| Phase 02-write-verbs-on-a-selection P03 | 25min | 3 tasks | 10 files |
| Phase 02-write-verbs-on-a-selection P04 | 20min | 3 tasks | 10 files |
| Phase 02 P05 | 55min | 2 tasks | 3 files |
| Phase 02-write-verbs-on-a-selection P06 | 55min | 3 tasks | 6 files |
| Phase 02 P07 | 30min | 3 tasks | 5 files |

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- [Roadmap]: Plumbing-first — FOUND-01..07 plus the Windows CI lane (XPLAT-01..03) land in Phase 1 before any write verb; each piece reproduces an already-observed phase-4/5 bug if skipped, and a Windows lane landed first guards every later Qt phase instead of a late handoff discovering it.
- [Roadmap]: Cross-platform hardening folded into Phase 1, not a late phase — FOUND-02/07 are already Windows work and SETUP-03 (key-first onboarding) depends on XPLAT-02 (apple absent off-mac).
- [Roadmap]: Roadmap phases 2 / 4 / 5 are spec phases 6 / 7 / 8 (#59 / #60 / #61); the NLSpec stays the acceptance authority for plugin behaviour.
- [Roadmap]: Phase 4 dashboard acceptance does not depend on true QDockWidget docking (spike; non-modal window is the fallback); Phase 5 review carries an explicit early marks/snapshot spike (no precedent anywhere).
- [Roadmap]: PUB-05 (CLI/wizard unchanged) is verified in Phase 6 but is a standing rule inherited by every phase's success criteria.
- [Phase 01]: Task 3 decision: a-guarded-auto-move approved with verified Time Machine + APFS snapshot backup; real ~/.config/scourgify/data (527MB) migrated intact into data/<uuid>/ during Task 5
- [Phase 01]: 01-02: Windows console-encoding fix (Fix 6) goes in cli.main(), not report.py -- report.py runs inside Calibre jobs too; stripping report.py's non-ASCII glyphs was considered and rejected as degrading the interface on every platform for one legacy console. — report.py is the ONE owner of render policy; reconfiguring process streams there would be an import-time side effect that could mutate a live Calibre job's own stdout/stderr.
- [Phase 01]: 01-02: two Windows test failures (test_user_dir_default_is_dot_config, test_set_library_redirects_the_core...) were test-authoring gaps that proved plan 01-01's user_dir()/set_library()/db_path() correct, not production bugs. — Neither test neutralized the environment variable (APPDATA) or path-separator behavior that a real Windows host exercises; fixed the tests, not the production code.
- [Phase 01]: 01-03: afm.swift is resolved via os.path.dirname(common.defaults_dir()), not a second cache path — defaults_dir() stays literally HERE/defaults on a normal install while the extracted cache mirrors afm.swift's real sibling position. — Keeps defaults_dir()'s contract exact (matches the plan's own acceptance criterion) while still routing afm.swift through the ONE resolver.
- [Phase 01]: 01-03: overrides.py's common.DEFAULTS import (scourgify overrides --master, a maintainer-only checkout-only write target) left unchanged — out of scope, not a runtime read of a shipped file, never job-reachable. — Repointing it would be scope creep onto a tool this plan's must_haves/acceptance criteria never named.
- [Phase 01]: [Phase 01]: 01-04: the write-run lock's key is _resolve_uuid() (matching backups_dir()'s own identity), with the caller-supplied lib_uuid as a fallback only — guarantees the lock and the snapshot directory never disagree about library identity.
- [Phase 01]: [Phase 01]: 01-04: run_writer's subprocess-timeout edit-log outcome collapses from a distinct 'timeout' string into the shared protocol's generic 'failed' -- _write_run's finally is a deliberate binary ok/failed flag, and no test pinned the old 'timeout' string.
- [Phase 01]: [Phase 01]: 01-04: the plan's acceptance-criteria grep for zero 'except BaseException' in common.py could not be satisfied literally -- two pre-existing, unrelated occurrences from plans 01-01/01-03 (rollback/cleanup handlers) are out of this plan's scope; this plan's own write-funnel code has zero occurrences, verified in isolation.
- [Phase 01]: 01-05: engines.engine_options gained an injected cost_fn parameter instead of importing classify.est_cost — classify.py already imports engines, so a verbatim relocation would have created an engines->classify import cycle; the injection breaks it while keeping D-10's locked module placement.
- [Phase 01]: 01-05: no compatibility alias kept in wizard.py for any of the six relocated builders (orchestrator-resolved) — an import of an old private name now fails loudly; tests/test_wizard.py's call sites were repointed in the same commit.
- [Phase 01]: 01-06: check_wipe runs on the unfiltered change-set BEFORE the conflict filter — A guard a race can disarm is not a guard
- [Phase 01]: 01-06: an all-skipped run is a dedicated early-exit inside _write_run (header+footer, no snapshot) rather than routing an empty ops list through the normal apply path — Makes 'no snapshot, still a footer' an exact special case instead of a general property every caller has to reason about
- [Phase 01]: 01-06: classify's expected comes from a fresh cur=current_tags(con) read at the head of apply_proposal, not the proposal's own state — The proposal schema has no before-state column; a proposal-time expected would permanently retire an edited book from the --unclassified backlog untagged, since its #wrangled stamp carries no expected
- [Phase 01]: 01-06: synopsis.Plan keeps raw_blurbs (unstripped) alongside blurbs (stripped); the comments op's expected is built from raw_blurbs — Comparing the stripped text against the stored HTML before-value would make every op read as a conflict and silently disable the whole pass
- [Phase 02]: 02-01: staleness.write gains write= (default run_writer); the plugin's jobs._Writer passes a write_ops-bound callable — the pattern plan 02-02 replicates across wrangle/classify/promote/synopsis/setup.
- [Phase 02]: 02-01: plugin/action.py reduced to zero core imports (D-12/D-14) — every job body lives in plugin/jobs.py, importing scourgify only inside functions; tests/test_plugin_source.py's MODULES/QT_MODULES are now glob-derived.
- [Phase 02]: 02-02: write= sentinel is write=None + late lookup, not an eagerly-bound write=run_writer default (also fixed on staleness.write from 02-01) — A default-argument value is bound once at function-definition time; the module.run_writer test-monkeypatches already used by tests/test_wizard_flow.py, tests/test_wizard.py, tests/test_synopsis.py mutate the module attribute AFTER def time, so an eager default silently keeps calling the original run_writer and falls through to a real calibre-debug subprocess. write=None + a late lookup performs a live lookup at call time instead, matching this codebase's existing ask=None/decide=None convention.
- [Phase 02]: 02-03: engine_options' price fragment is three cases (free / sub-cent / usual) — a real cost under half a cent must never render as '~$0.00' (which reads as free). — A user reading '~$0.00' assumes no charge; the sub-cent label makes the real, nonzero cost visible.
- [Phase 02]: 02-03: job_plan_classify hands the engine picker usable/engine_limits as sibling keys — engine_options' own tuple carries neither, and plugin/picker.py may import nothing from scourgify. — D-01's picker contract (no core import) means TRAITS-derived text must arrive as plain data from the job, not be derived by the Qt dialog.
- [Phase 02]: 02-03: restrict_to_selection is an explicit scope_spec flag (only the never-classified shortcut sets it), not an implicit ids-non-empty inference. — An implicit rule would have silently restricted the ScopeDialog's own never-classified row too, whose label already promises the whole-library backlog count.
- [Phase 02]: 02-04: ui.checklist is the ONE item-shape normalization point — accepts plain strings or (label, payload) pairs, reads only the label, so the wizard needed zero edits (PUB-05). — Every decide= producer can emit structured payload without any front door having to special-case the shape; Phase 4/5 read the same payload keys.
- [Phase 02]: 02-04: synopsis.step gains blurbs= so payload's 'before' comes from the caller's already-read description, not a new library read inside step(). — step() has no library connection of its own and shouldn't grow one just to fill a review column.
- [Phase 02]: 02-05: _record_decide/_replay_decide (D-02) are sequence-aware generic decide= fabricators shared by staleness (single-call) and wrangle (per-book multi-call) review sites — a PLAN job records items and answers 'skip'; the matching EXECUTE job replays a reviewer's ticks, falling back to accept-everything when ticks is exhausted (the one-click Run path's contract).
- [Phase 02]: 02-05: job_execute_staleness's new ticks= sits after now_uuid (not before, unlike wrangle's own EXECUTE job) so plugin/action.py's existing generic _EXECUTE_JOBS dispatch tuple stays intact — inserting it earlier would have silently misrouted now_uuid into the ticks position on every existing staleness dispatch.
- [Phase 02]: 02-05: job_plan_wrangle overrides plan_result's own 'not items' empty test with p.n_books==0 — wrangle's review items cover only per-book UNIQUE edits, so a mass-only change-set (real work, zero review items) must not read as D-04 empty.
- [Phase 02]: 02-05: wrangle EXECUTE deliberately recomputes the plan rather than carrying an ops list forward — _step_walk is what writes reject rows and recomputes a book's net change on a partial untick, and duplicating that in the plugin would violate 'the wizard ASKS, the tool modules DO'.
- [Phase 02-write-verbs-on-a-selection]: 02-06: job_execute_synopsis shares ONE function across two dispatches (ticks=None harvests generated descriptions via a no-op writer; ticks=list replays the reviewer's ticks and writes for real) — the shape D-13's per-book review needs when review items are engine-generated text, not a free-to-compute diff. — The second dispatch RE-RUNS the engine pass rather than caching the first dispatch's generated text (no cross-dispatch state on synopsis.Plan) — mirrors job_execute_wrangle's own recompute-not-carry-forward choice, generalised to a non-deterministic pass; batch size (not a cache) bounds the cost.
- [Phase 02-write-verbs-on-a-selection]: 02-06: jobs._synopsis_cost prices every book as the full-generation worst case (never under-quote) since a book's judge verdict isn't knowable before the call — the same lesson CLAUDE.md already records for classify.est_cost's out_tokens fix. — A flat/optimistic per-book estimate would under-quote every book that turns out to need generation, the same direction classify's own gemini out_tokens miscalibration went before it was fixed.
- [Phase 02]: 02-07: decide() normalizes an injected ask's answer (real (text,err) tuple or bare string) at ONE point, prefixing the error verdict's reason with engines.failure_class(err) — a promote refusal now earns the same cross-engine retry a classify refusal does (#73). — ask_retry returns (text, err); truncating to [0] silently dropped the failure class the ledger needs to be readable back out.
- [Phase 02]: 02-07: plugin/picker.py needed zero changes for the judge-aware engine picker — EnginePicker already derives every disabled row and default purely from job-supplied usable/engine_limits/default_engine data; job_plan_promote overrides a non-judge engine's row to disabled+trait('unusable') before handing it to engine_options. — The widget was already generic; adding capability logic to it would have duplicated what the data contract already lets the job express.
- [Phase 02]: 02-07: job_execute_backfill recomputes promote.backfill_plan() twice (once to learn which books the caller's decide accepted, once again inside promote.backfill for the real write) rather than carrying an ops list forward. — decide is a pure read of already-captured tick state, so a second compute is cheap and safe; matches job_execute_wrangle's own recompute-not-carry-forward choice.

### Pending Todos

None yet.

### Blockers/Concerns

- [Phase 1]: XPLAT-03's `calibre-debug.exe` resolution on `windows-latest` is conventional, not verified on a real Windows install — the CI lane is where it gets settled.
- [Phase 4]: QDockWidget main-window integration has no worked third-party example; plan around the non-modal-window fallback.
- [Phase 5]: The exact read-back call for current marked ids (`db.data.marked_ids`?) is unverified against a live Calibre 9.11 — a five-minute spike before the snapshot/restore helper's shape is finalized.
- [Phase 3]: `CreateCustomColumn.must_restart()`'s exact call sequence needs a direct Calibre source re-read at implementation time.
- Branch protection on main is NOT updated. Lane is green (run 34150694155) but adding test-windows/smoke-calibre-windows/smoke-calibre-linux as required status checks needs a separate developer confirmation, per standing instruction. Command recorded in 01-02-SUMMARY.md.
- 02-01: the tracer task's real-Calibre human-check (Re-derive status GUI walkthrough) was not run — no Calibre available in this environment; recorded in .planning/WINDOWS.md (unrun-verify, phase 02).
- 02-05: the tracer/verb human-checks for Re-derive status (02-01), classify scope/engine pickers (02-03), Review 1-by-1 (02-04), and now Normalize fields (02-05) all need a real Calibre GUI walkthrough — no Calibre available in this environment; four open unrun-verify entries in .planning/WINDOWS.md.

## Deferred Items

Items acknowledged and deferred at milestone close, most recent first:

| Category | Item | Status | Deferred At | Milestone |
|----------|------|--------|-------------|-----------|
| *(none)* | | | | |

## Session Continuity

Last session: 2026-09-09T11:41:07.941Z
Stopped at: Completed 02-07-PLAN.md
Resume file: None
