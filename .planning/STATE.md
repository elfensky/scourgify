---
gsd_state_version: 1.0
milestone: v1
milestone_name: (the terminal goes away)
current_phase: 01
current_phase_name: Foundation — a hostable core
status: executing
stopped_at: Completed 01-05-PLAN.md
last_updated: "2026-09-07T19:31:46.369Z"
last_activity: 2026-09-07
last_activity_desc: Phase 01 execution started
state_head: 53b63a3d08ed208f83a7824588f373cfeca2a3cf
progress:
  total_phases: 6
  completed_phases: 0
  total_plans: 6
  completed_plans: 5
  percent: 0
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-08-28)

**Core value:** The full maintenance loop (wrangle → staleness → synopsis → classify → review → promote → backfill) runs from a Calibre toolbar button, on the live library, with every guard, backup and undo the CLI has — and never freezes or corrupts Calibre.
**Current focus:** Phase 01 — Foundation — a hostable core

## Current Position

Phase: 01 (Foundation — a hostable core) — EXECUTING
Plan: 6 of 6
Status: Ready to execute
Last activity: 2026-09-07 — Phase 01 execution started

Progress: [░░░░░░░░░░] 0%

## Performance Metrics

**Velocity:**

- Total plans completed: 0
- Average duration: -
- Total execution time: 0.0 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| - | - | - | - |

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

### Pending Todos

None yet.

### Blockers/Concerns

- [Phase 1]: XPLAT-03's `calibre-debug.exe` resolution on `windows-latest` is conventional, not verified on a real Windows install — the CI lane is where it gets settled.
- [Phase 4]: QDockWidget main-window integration has no worked third-party example; plan around the non-modal-window fallback.
- [Phase 5]: The exact read-back call for current marked ids (`db.data.marked_ids`?) is unverified against a live Calibre 9.11 — a five-minute spike before the snapshot/restore helper's shape is finalized.
- [Phase 3]: `CreateCustomColumn.must_restart()`'s exact call sequence needs a direct Calibre source re-read at implementation time.
- Branch protection on main is NOT updated. Lane is green (run 34150694155) but adding test-windows/smoke-calibre-windows/smoke-calibre-linux as required status checks needs a separate developer confirmation, per standing instruction. Command recorded in 01-02-SUMMARY.md.

## Deferred Items

Items acknowledged and deferred at milestone close, most recent first:

| Category | Item | Status | Deferred At | Milestone |
|----------|------|--------|-------------|-----------|
| *(none)* | | | | |

## Session Continuity

Last session: 2026-09-07T19:31:46.350Z
Stopped at: Completed 01-05-PLAN.md
Resume file: None
