# Phase 2: Write verbs on a selection - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-09-08
**Phase:** 2-write-verbs-on-a-selection
**Areas discussed:** Picker shape, Classify flow, Diff-after & retry

Areas offered but not selected: Job round trip (PLAN→EXECUTE hand-off, lock greying without a
core import, #72) — left to Claude's discretion.

---

## Picker shape

| Option | Description | Selected |
|--------|-------------|----------|
| Summary + Run, expand to 1-by-1 | Counts, SAFETY line, consequence on the Run button; a "Review 1-by-1" toggle expands the pre-ticked per-book list; same decide= seam either way | ✓ |
| Always the tick-list | Every verb opens the full checklist with Apply/Skip; literal port of ui.checklist | |
| Summary + Run only | No per-item review this phase; diff-after + undo as the net | |

**User's choice:** Summary + Run, expand to 1-by-1

| Option | Description | Selected |
|--------|-------------|----------|
| (label, payload) tuples | Items carry book id, title, field, before, after; Qt renders columns; ui.checklist reads the label only | ✓ |
| Display strings | Same one-line strings as the wizard; zero tool-module change | |

**User's choice:** (label, payload) tuples (closes the item deferred from Phase 1)

| Option | Description | Selected |
|--------|-------------|----------|
| One class, verb-parameterised | One QDialog fed by plain PLAN-job data; verbs differ only in data | ✓ |
| One per verb | Each verb owns its dialog | |

**User's choice:** One class, verb-parameterised

| Option | Description | Selected |
|--------|-------------|----------|
| No dialog, one-line notice | Empty PLAN result → status message; mirrors the wizard's auto-skip | ✓ |
| Show the dialog anyway | Zero counts, disabled Run | |

**User's choice:** No dialog, one-line notice

---

## Classify flow

| Option | Description | Selected |
|--------|-------------|----------|
| Write straight through | Pass + write ONE job; edit log gets engine+model; applied proposal archived in CLI format | ✓ |
| Land a pending proposal | Job writes classify_proposal.csv only; apply waits for Phase 5 review | |
| Picker chooses per run | A switch on the picker: apply on completion or keep as proposal | |

**User's choice:** Write straight through
**Notes:** the "keep as proposal" switch is recorded as a deferred idea for Phase 5.

| Option | Description | Selected |
|--------|-------------|----------|
| Two steps, price after resolve | Scope dialog → PLAN job resolves once via classify.plan() → engine picker priced over that set | ✓ |
| One dialog, live re-resolve | Scope radios + batch + engine rows in one dialog; re-resolve per change | |

**User's choice:** Two steps, price after resolve

| Option | Description | Selected |
|--------|-------------|----------|
| One button per engine, price on it | Name, price for N books or free, TRAITS failure mode with measurement date; click = run; no key = greyed | ✓ |
| Radio list + one Run button | Conventional form; one more click | |

**User's choice:** One button per engine, price on it

| Option | Description | Selected |
|--------|-------------|----------|
| Calibre's jobs panel only | notifications.put drives Calibre's job progress; abort via the jobs panel; dashboard surface is Phase 4 | ✓ |
| A live progress dialog now | Non-modal dialog mirroring report.Dashboard with its own abort | |

**User's choice:** Calibre's jobs panel only

---

## Diff-after & retry

| Option | Description | Selected |
|--------|-------------|----------|
| Result dialog with per-book rows | Non-modal QDialog: summary line, then per-book rows (before → after / skipped / failed by class); reuses the picker's row widget; undo attaches here in Phase 5 | ✓ |
| Today's info_dialog + details pane | Keep action._done; per-book lines as plain text in det_msg | |
| Status-bar notice + History later | One-line counts; detail waits for Phase 4 History | |

**User's choice:** Result dialog with per-book rows

| Option | Description | Selected |
|--------|-------------|----------|
| Both: result dialog + menu verb | Button per refused group in the result dialog AND the menu slot keyed off the failure log; same job function | ✓ |
| Result dialog only | Retry right after the run; menu slot stays greyed | |
| Menu verb only | Result names the refused books; user re-selects and uses the menu | |

**User's choice:** Both: result dialog + menu verb

| Option | Description | Selected |
|--------|-------------|----------|
| Direct dispatch, price on the button | Button names engine + price for exactly those books; click runs; no picker | ✓ |
| Open the engine picker | Reuse the classify picker scoped to the refused ids | |

**User's choice:** Direct dispatch, price on the button

| Option | Description | Selected |
|--------|-------------|----------|
| Same result dialog, refused state | GuardrailError text verbatim, zero rows; job ends cleanly | ✓ |
| Calibre's error dialog | Route through gui.job_exception; reads like a crash | |

**User's choice:** Same result dialog, refused state

---

## Claude's Discretion

- PLAN → EXECUTE hand-off shape (ops with expected values, no recompute)
- Job runner design (#72) and the amendment to test_plugin_source's import rule
- Lock greying without a core import on the GUI thread
- `write=` seam on the tool modules' write functions; #73 envelope in promote
- Synopsis FFF-guard wording and degraded-mode offer; promote's judge-engine picker
- Library-view refresh call, dialog parenting, cancelled-run rendering, batch-size and workers defaults

## Deferred Ideas

- "Keep as proposal" switch on the classify picker — Phase 5 if needed
- Edit tags… verb — in #59, not in WRITE-*; stays greyed
- Live progress dialog — Phase 4
- Undo button on the result dialog — Phase 5
- Advisory cross-process lock file — carried from Phase 1
