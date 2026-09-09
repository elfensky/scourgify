---
schema_version: 1
open_count: 4
waived_count: 0
fixed_count: 0
total_count: 4
last_updated: 2026-09-09T10:48:55.726Z
---

# Broken Windows Ledger

> Cross-phase defect register. With `workflow.windows_enforce` enabled, `/gsd-ship` blocks while `open_count > 0`.
> Waive with `gsd-tools windows waive <id> "<reason>"` (reason required).
> Mark fixed with `gsd-tools windows fixed <id>`.

| id | phase | kind | file | line | description | status | reason | recorded_at | resolved_at |
|----|-------|------|------|------|-------------|--------|--------|-------------|-------------|
| 1 | 02 | unrun-verify | plugin/action.py |  | Task 1 (02-01) human-check not run: real-Calibre GUI verification of Re-derive status (picker/result dialog/row refresh) — no Calibre available in this environment; all automated checks pass. | open |  | 2026-09-09T08:15:35.653Z |  |
| 2 | 02 | unrun-verify | plugin/picker.py |  | Task 3 human-check not run (no Calibre in this environment): real-Calibre walkthrough of classify's scope dialog + engine picker on a throwaway library (select 2 books, click Classify these 2 books). | open |  | 2026-09-09T10:07:28.323Z |  |
| 3 | 02 | unrun-verify | plugin/picker.py |  | Task 3 (02-04) human-check not run: real-Calibre walkthrough of the Review 1-by-1 control (Re-derive status on 5 books, untick two, apply, confirm the result dialog and library view) — no Calibre available in this environment; all automated checks pass. | open |  | 2026-09-09T10:28:51.483Z |  |
| 4 | 02 | unrun-verify | plugin/action.py |  | Task 2 (02-05) human-check not run: real-Calibre walkthrough of Normalize fields (select messy-tag books, confirm the picker's per-book edits + SAFETY line, apply, confirm result dialog + refreshed rows, re-run and confirm the one-line nothing-to-change notice) — no Calibre available in this environment; all automated checks pass. | open |  | 2026-09-09T10:48:55.726Z |  |

````json
[
  {
    "id": 1,
    "kind": "unrun-verify",
    "phase": "02",
    "file": "plugin/action.py",
    "line": null,
    "description": "Task 1 (02-01) human-check not run: real-Calibre GUI verification of Re-derive status (picker/result dialog/row refresh) — no Calibre available in this environment; all automated checks pass.",
    "status": "open",
    "reason": "",
    "recorded_at": "2026-09-09T08:15:35.653Z",
    "resolved_at": null
  },
  {
    "id": 2,
    "kind": "unrun-verify",
    "phase": "02",
    "file": "plugin/picker.py",
    "line": null,
    "description": "Task 3 human-check not run (no Calibre in this environment): real-Calibre walkthrough of classify's scope dialog + engine picker on a throwaway library (select 2 books, click Classify these 2 books).",
    "status": "open",
    "reason": "",
    "recorded_at": "2026-09-09T10:07:28.323Z",
    "resolved_at": null
  },
  {
    "id": 3,
    "kind": "unrun-verify",
    "phase": "02",
    "file": "plugin/picker.py",
    "line": null,
    "description": "Task 3 (02-04) human-check not run: real-Calibre walkthrough of the Review 1-by-1 control (Re-derive status on 5 books, untick two, apply, confirm the result dialog and library view) — no Calibre available in this environment; all automated checks pass.",
    "status": "open",
    "reason": "",
    "recorded_at": "2026-09-09T10:28:51.483Z",
    "resolved_at": null
  },
  {
    "id": 4,
    "kind": "unrun-verify",
    "phase": "02",
    "file": "plugin/action.py",
    "line": null,
    "description": "Task 2 (02-05) human-check not run: real-Calibre walkthrough of Normalize fields (select messy-tag books, confirm the picker's per-book edits + SAFETY line, apply, confirm result dialog + refreshed rows, re-run and confirm the one-line nothing-to-change notice) — no Calibre available in this environment; all automated checks pass.",
    "status": "open",
    "reason": "",
    "recorded_at": "2026-09-09T10:48:55.726Z",
    "resolved_at": null
  }
]
````
