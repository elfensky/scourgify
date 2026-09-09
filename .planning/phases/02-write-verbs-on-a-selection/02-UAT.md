---
status: testing
phase: 02-write-verbs-on-a-selection
source: [02-VERIFICATION.md]
started: 2026-09-09T12:31:19Z
updated: 2026-09-09T12:31:19Z
---

## Current Test

number: 1
name: Tracer — "Re-derive status" end-to-end under real Calibre
expected: |
  With books selected, the toolbar menu's "Re-derive status" shows a per-book #status
  change list first; clicking Run writes it through common.write_ops with the snapshot,
  wipe guard and write-run lock applied; the result dialog names counts and any skipped
  conflicts by book; the touched rows refresh in the library view.
awaiting: user response

## Tests

### 1. Tracer — "Re-derive status" end-to-end under real Calibre
expected: Menu -> PLAN preview -> picker -> EXECUTE -> result dialog -> library-view refresh, whole chain. This is plan 02-01's blocking-human tracer gate, which was skipped during execution; every later plan builds on the rails it was meant to prove.
result: [pending]

### 2. Classify's scope dialog, engine picker, and the Review 1-by-1 control
expected: Scope dialog offers selection / new-changed / never-classified with a batch size / whole library. The engine picker lists each usable engine with its price over the exact resolved scope and its failure modes, with the measurement date shown. The run starts on click — no confirmation dialog. The Review 1-by-1 control walks items and an unticked item is excluded. (Plans 02-03, 02-04.)
result: [pending]

### 3. "Normalize fields" — SAFETY line, apply, and no-op re-run
expected: The preview shows the SAFETY line; a run that would strip a book's last fandom or character is refused with the reason; applying writes; re-running immediately reports a no-op rather than rewriting. (Plan 02-05.)
result: [pending]

### 4. "Settle descriptions" — FFF guard, live progress, per-book review
expected: The verb refuses to start while FanFicFare's Comments "New Only" switch is off, unless the degraded self-healing mode is chosen explicitly. Progress is legible in Calibre's job list and abortable mid-run. Per-book review works against a real on-device apple engine call. (Plan 02-06.)
result: [pending]

### 5. Grouped result dialog — failure sections, priced retry, menu greying
expected: After a classify write, failures are grouped by failure_class() with a "retry on <engine>" verb where a refusal occurred and no retry offered for auth/permission. Retry buttons show their price. A write verb greys out, naming the running job, while another write-run holds the library's lock, and un-greys on completion. (Plan 02-08.)
result: [pending]

### 6. Edit log record shape from a real plugin run
expected: After any plugin write, the per-library edits.jsonl holds a run header, one before/after line per (book, field), and a footer — the same record shape a CLI run writes, with engine + model filled in for a classify run. (Success criterion 4.)
result: [pending]

### 7. SCOURGIFY_SMOKE=1 driven round trip + GUI-thread heartbeat
expected: `SCOURGIFY_SMOKE=1 calibre --with-library <throwaway>` completes the driven round trip through all seven write-verb chains, restores the real stored API key on finish, and reports a longest GUI-thread stall under 100ms. Note: this path was broken by CR-01 (config.prefs did not exist) and fixed in commit fd9d1ac — this run is also the fix's first real exercise. (Plan 02-08.)
result: [pending]

## Summary

total: 7
passed: 0
issues: 0
pending: 7
skipped: 0
blocked: 0

## Gaps
