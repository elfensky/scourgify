---
schema_version: 1
open_count: 2
waived_count: 0
fixed_count: 0
total_count: 2
last_updated: 2026-09-09T10:07:28.323Z
---

# Broken Windows Ledger

> Cross-phase defect register. With `workflow.windows_enforce` enabled, `/gsd-ship` blocks while `open_count > 0`.
> Waive with `gsd-tools windows waive <id> "<reason>"` (reason required).
> Mark fixed with `gsd-tools windows fixed <id>`.

| id | phase | kind | file | line | description | status | reason | recorded_at | resolved_at |
|----|-------|------|------|------|-------------|--------|--------|-------------|-------------|
| 1 | 02 | unrun-verify | plugin/action.py |  | Task 1 (02-01) human-check not run: real-Calibre GUI verification of Re-derive status (picker/result dialog/row refresh) — no Calibre available in this environment; all automated checks pass. | open |  | 2026-09-09T08:15:35.653Z |  |
| 2 | 02 | unrun-verify | plugin/picker.py |  | Task 3 human-check not run (no Calibre in this environment): real-Calibre walkthrough of classify's scope dialog + engine picker on a throwaway library (select 2 books, click Classify these 2 books). | open |  | 2026-09-09T10:07:28.323Z |  |

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
  }
]
````
