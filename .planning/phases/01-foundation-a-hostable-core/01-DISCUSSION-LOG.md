# Phase 1: Foundation — a hostable core - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-09-05
**Phase:** 1-foundation-a-hostable-core
**Areas discussed:** Per-library state layout and migration, Lock reach and conflict reporting, Home of the pure option functions, Windows CI lane shape

---

## Per-library state layout and migration

**Q: Where does the library-uuid level sit in the user_dir() tree?**

| Option | Description | Selected |
|--------|-------------|----------|
| data/<uuid>/ only | Only data/ gains a level; config.toml and overrides/ stay global; one function (data_dir) changes | ✓ |
| libraries/<uuid>/ holds everything | Full per-library subtree incl. config.toml and overrides/ | |
| data/<uuid>/ plus per-library config.toml | Artifacts per library, config per library with root fallback, overrides global | |

**Q: What happens to the existing single-library data/ tree on first per-library resolve?**

| Option | Description | Selected |
|--------|-------------|----------|
| Auto-move once, guarded | Rename legacy files into data/<uuid>/ for the first library resolved, leave data/MIGRATED marker | ✓ |
| Legacy fallback read, never move | Readers fall back to the legacy root; two locations live forever | |
| Explicit migrate step | `scourgify migrate` on request; legacy ignored until then | |

**Q: How does the auto-move prove the legacy tree belongs to the library being resolved?**

| Option | Description | Selected |
|--------|-------------|----------|
| Match uuid inside newest backup | Read library uuid from the newest legacy ff_*.db backup; move only on match; else leave and log | ✓ |
| First library resolved wins | No proof; wrong if a throwaway library is opened first | |
| Match uuid, else ask | Same proof; on mismatch the CLI/wizard asks once, the plugin leaves it | |

**Notes:** Real tree inspected during discussion: 527 MB, 22 backups, 14 classify archives, 10 promote archives, no edits.jsonl yet. Backups and prune budget become per library as a consequence of the layout choice.

---

## Lock reach and conflict reporting

**Q: How far does the write-run lock reach?**

| Option | Description | Selected |
|--------|-------------|----------|
| In-process, keyed by uuid | Dict of threading locks in common.py with (tool, description, started_at); refusal names the running job | ✓ |
| Cross-process file lock | data/<uuid>/write.lock with pid; needs stale-lock detection (tasklist on Windows) | |
| Both: in-process lock plus advisory lock file | Gate in-process; file only so a CLI run can refuse with a hint | |

**Q: Where does the apply-time conflict check run?**

| Option | Description | Selected |
|--------|-------------|----------|
| In the shared pre-write funnel | Compare the existing write-time read to plan-time expected values; drop conflicts before log/apply; the #71 unification; near side of calibre-debug | ✓ |
| Inside ops.apply_ops, per op | Literal FOUND-05; needs a result JSON back from _writer.py for the CLI | |
| Both layers | Funnel filters, apply_ops re-checks; two reads, two places to disagree | |

**Q: How is a skipped conflict recorded in edits.jsonl and shown to the user?**

| Option | Description | Selected |
|--------|-------------|----------|
| Footer list plus one console line per book | Survivors-only op lines; footer gains skipped list + count; funnel returns result naming books | ✓ |
| Per-op skipped lines | Every op logged, conflicts with skipped: true; record shape gains a third state | |
| Footer count only | Count in footer, books on console only; History cannot show which books | |

**Notes:** Expected values are carried on each set_field op from plan time by both doors (CLI and plugin) so the op shape stays one.

---

## Home of the pure option functions

**Q: Where do the pure option functions live once they leave wizard.py?**

| Option | Description | Selected |
|--------|-------------|----------|
| Each owning tool module | classify.scope_options/proposal_options, engines.engine_options/default_engine_id, synopsis.options; no new module | ✓ |
| report.py | One home beside table/tree/say; mixes content with rendering policy | |
| New options.py module | One rich-free module; a new file whose only reason is "not wizard.py" | |

**Q: What shape makes the seven ui.checklist call sites drivable without ui?**

| Option | Description | Selected |
|--------|-------------|----------|
| decide= injection at all 7 sites | Default ui.checklist; PLAN job records items via a decide answering skip, EXECUTE job replays ticks | ✓ |
| Split each step into items() + apply(ticked) | Seven pure items() + seven apply(); cleanest for a Qt panel; seven refactors before any consumer | |
| Menu builders now, checklist seam per verb in Phase 2 | Least speculative; narrows FOUND-06 as written | |

---

## Windows CI lane shape

**Q: Is the windows-latest lane blocking from day one, or advisory first?**

| Option | Description | Selected |
|--------|-------------|----------|
| Blocking, but split in two jobs | Core tests required; calibre-debug smoke required, demotable alone if flaky | ✓ |
| Advisory first (continue-on-error) | Runs but cannot fail the PR until green for a while | |
| Blocking, one job | Core tests and smoke in one job | |

**Q: How does Calibre land on the windows-latest runner?**

| Option | Description | Selected |
|--------|-------------|----------|
| Pinned official MSI, cached | calibre-64bit-<ver>.msi (9.11), msiexec /qn, actions/cache by version | ✓ |
| Chocolatey latest | choco install calibre; tracks latest release | |
| Portable build zip | Extract portable build; a second install shape users do not have | |

**Q: Does the Linux lane gain the same calibre-debug smoke now, and which Python versions run on Windows?**

| Option | Description | Selected |
|--------|-------------|----------|
| Smoke on both OSes; Windows core tests on 3.14 only | Smoke on ubuntu (pinned linux installer) and windows; Windows core tests on Calibre's 3.14 only | ✓ |
| Windows only, full matrix | Smoke on windows; Windows core tests on 3.10/3.13/3.14 | |
| Windows only, 3.14 only | Literal XPLAT-03; ubuntu unchanged | |

---

## Claude's Discretion

- Defaults cache location (`user_dir()/cache/<version>/`), zipfile-based extraction, coverage of `afm.swift`, `load_vocab` fail-closed
- Uuid memoization and the key for a uuid-less fixture db
- Header/footer for an all-skipped run
- `platforms` trait for apple; `tasklist` branch in `calibre_open()`
- Lock refusal wording; `expected` key name on the op dict
- `calibre-debug.exe` path on the Windows runner

## Deferred Ideas

- Checklist items as (label, payload) tuples for a Qt before/after picker — Phase 2
- Advisory lock file for CLI-vs-plugin awareness — only if a cross-process case appears
- Per-library config.toml — only if a second library needs a different column map
