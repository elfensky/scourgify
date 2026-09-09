---
schema_version: 1
open_count: 1
waived_count: 0
fixed_count: 0
total_count: 1
last_updated: 2026-09-09T08:15:35.653Z
---

# Broken Windows Ledger

> Cross-phase defect register. With `workflow.windows_enforce` enabled, `/gsd-ship` blocks while `open_count > 0`.
> Waive with `gsd-tools windows waive <id> "<reason>"` (reason required).
> Mark fixed with `gsd-tools windows fixed <id>`.

| id | phase | kind | file | line | description | status | reason | recorded_at | resolved_at |
|----|-------|------|------|------|-------------|--------|--------|-------------|-------------|
| 1 | 02 | unrun-verify | plugin/action.py |  | Task 1 (02-01) human-check not run: real-Calibre GUI verification of Re-derive status (picker/result dialog/row refresh) — no Calibre available in this environment; all automated checks pass. | open |  | 2026-09-09T08:15:35.653Z |  |

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
  }
]
````
