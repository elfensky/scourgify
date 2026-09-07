---
gsd_state_version: 1.0
milestone: v1
milestone_name: (the terminal goes away)
current_phase: 01
current_phase_name: Foundation — a hostable core
status: executing
stopped_at: Completed 01-01-PLAN.md
last_updated: "2026-09-07T15:59:18.313Z"
last_activity: 2026-09-07
last_activity_desc: Phase 01 execution started
state_head: 19ca385b81fe517aedeae4ef9be883ce500ee66a
progress:
  total_phases: 6
  completed_phases: 0
  total_plans: 6
  completed_plans: 1
  percent: 0
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-08-28)

**Core value:** The full maintenance loop (wrangle → staleness → synopsis → classify → review → promote → backfill) runs from a Calibre toolbar button, on the live library, with every guard, backup and undo the CLI has — and never freezes or corrupts Calibre.
**Current focus:** Phase 01 — Foundation — a hostable core

## Current Position

Phase: 01 (Foundation — a hostable core) — EXECUTING
Plan: 2 of 6
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

### Pending Todos

None yet.

### Blockers/Concerns

- [Phase 1]: XPLAT-03's `calibre-debug.exe` resolution on `windows-latest` is conventional, not verified on a real Windows install — the CI lane is where it gets settled.
- [Phase 4]: QDockWidget main-window integration has no worked third-party example; plan around the non-modal-window fallback.
- [Phase 5]: The exact read-back call for current marked ids (`db.data.marked_ids`?) is unverified against a live Calibre 9.11 — a five-minute spike before the snapshot/restore helper's shape is finalized.
- [Phase 3]: `CreateCustomColumn.must_restart()`'s exact call sequence needs a direct Calibre source re-read at implementation time.

## Deferred Items

Items acknowledged and deferred at milestone close, most recent first:

| Category | Item | Status | Deferred At | Milestone |
|----------|------|--------|-------------|-----------|
| *(none)* | | | | |

## Session Continuity

Last session: 2026-09-07T15:59:18.298Z
Stopped at: Completed 01-01-PLAN.md
Resume file: None
