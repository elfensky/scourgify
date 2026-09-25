---
phase: "2"
slug: "write-verbs-on-a-selection"
# status lifecycle: draft (seeded by plan-phase) → validated (set by validate-phase §6)
# audit-milestone §5.5 distinguishes NOT-VALIDATED (draft) from PARTIAL (validated + nyquist_compliant: false) (#2117)
status: draft
nyquist_compliant: false
wave_0_complete: false
created: "2026-09-08"
---

# Phase 2 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | Plain-assert Python files, pytest-compatible, no framework required |
| **Config file** | none — `.planning/config.json` `workflow.test_command` |
| **Quick run command** | `uv run tests/test_plugin_source.py` |
| **Full suite command** | `for t in tests/test_*.py; do uv run "$t" \|\| exit 1; done` |
| **Estimated runtime** | ~30 seconds (full suite); sub-second (quick) |

---

## Sampling Rate

- **After every task commit:** Run `uv run tests/test_plugin_source.py` plus the unit test file the task touched
- **After every plan wave:** Run `for t in tests/test_*.py; do uv run "$t" || exit 1; done`
- **Before `/gsd-verify-work`:** Full suite must be green, and `SCOURGIFY_SMOKE=1 calibre --with-library <throwaway>` run manually at least once against a real Calibre GUI
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| TBD — filled by /gsd-validate-phase after plans exist | | | | | | | | | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_write_path.py` (or new file) — pin the `write=` / `engine=` / `model=` keyword parameters on all six write-producing functions, asserting default behaviour (no `write=` still calls `run_writer`) is unchanged
- [ ] `tests/test_editlog.py` (extend) — pin that `write_ops(..., engine=, model=)` reaches the edit-log run header (WRITE-06's core half; cheapest test in the phase)
- [ ] `tests/test_plugin_source.py` (extend `MODULES`) — structural assertions for the new picker / result-dialog modules once they exist
- [ ] `plugin/selftest.py` (extend) — one PLAN → picker data → EXECUTE → result data round trip per verb, following the existing `_run_verb` pattern

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Menu greying while a write-run holds the lock; dialog contents render; click dispatches; library view refreshes | WRITE-01..08 | CI has no Calibre and no GUI | `SCOURGIFY_SMOKE=1 calibre --with-library <throwaway>` driving `plugin/selftest.py` |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
