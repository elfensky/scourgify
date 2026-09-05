# Phase 1: Foundation — a hostable core - Context

**Gathered:** 2026-09-05
**Status:** Ready for planning

<domain>
## Phase Boundary

The unchanged core runs correctly when the plugin hosts it: bundled defaults readable from
inside the zip, operational state keyed per library uuid, `user_dir()` on Windows, one
write-run lock per library with apply-time conflict checks in the shared write funnel, the
wizard's option and checklist decisions drivable without `ui`, and a Windows CI lane plus the
`calibre-debug` smoke in CI on both OSes. Requirements FOUND-01..07, XPLAT-01..03. Nothing
here demos; no write verb, no Qt, no setup flow (those are Phases 2–3). Closes the pre-write
protocol half of #71.

</domain>

<decisions>
## Implementation Decisions

### Per-library state (FOUND-02, FOUND-03)
- **D-01:** The library-uuid level sits at `data/<uuid>/` only. Proposals, failures,
  `edits.jsonl`, backups, rejects and ledger move under it. `config.toml` and `overrides/`
  stay global at the `user_dir()` root. — **Reversibility:** costly — every artifact path
  goes through `common.data_dir()`, so the change is one function, but moving config or
  overrides per library later means a second on-disk migration of user data.
- **D-02:** Migration of the existing single-library tree is a one-time guarded auto-move:
  when `data/<uuid>/` does not exist and legacy files sit directly under `data/`, rename
  them into `data/<uuid>/` and leave a `data/MIGRATED` marker. A rename, so reversible by
  hand.
- **D-03:** Owner proof for the auto-move: read the library uuid out of the newest legacy
  backup (`data/backups/ff_*.db` is a full `metadata.db`; `common.library_uuid()` reads it)
  and move only when it equals the uuid of the library being resolved. No legacy backup, or
  a different uuid: leave the legacy tree untouched and log why. A throwaway library opened
  first can never claim the real library's history.
- **D-04:** Backups and the prune budget (`BACKUP_KEEP`, `BACKUP_BUDGET`, `BACKUP_MIN`)
  become per library because `backups_dir()` lives under `data_dir()`. `scourgify rollback
  --list` lists the current library's snapshots only.
- **D-05:** Windows home is `%APPDATA%\scourgify`, stdlib only (locked by FOUND-02).
  `$SCOURGIFY_HOME` still wins everywhere.

### Write-run lock and apply-time conflicts (FOUND-04, FOUND-05)
- **D-06:** The lock is in-process: a module-level dict of `threading.Lock`s in `common.py`,
  keyed by library uuid, each holding a small record (tool, description, started_at). A
  second write-run against the same library raises `GuardrailError` whose message names the
  running job. No lock file, no pid probing. A CLI write is already refused while the GUI is
  open (`calibre_open()`), and two CLI processes racing is out of scope.
- **D-07:** The apply-time conflict check runs in the shared pre-write funnel (the sequence
  `write_ops` and `run_writer` duplicate today; #71 makes it one function). The funnel
  already reads every op's current value through `editlog.before_values`; it compares that
  read to the plan-time expected value carried on the op with `editlog.conflict` and drops
  the conflicting `(book, field)` entries before logging or applying. `ops.apply_ops` is
  unchanged. The check is on the near side of the `calibre-debug` subprocess, so the CLI
  needs no result plumbing back from `_writer.py`.
- **D-08:** Reporting shape: only ops that will apply get op lines (the crash-safe
  "log before apply" rule holds). The run footer gains `skipped: [[book, field], ...]` and a
  count. The funnel returns a result naming each skipped book; the CLI prints one line per
  skipped book; Phase 2's diff-after reads the same result. Undo never sees a line for a
  write that did not happen. — **Reversibility:** costly — `tests/test_editlog.py` pins the
  record shape and History (Phase 4) will read the footer field.
- **D-09:** Both doors carry expected values: `op_set_field` gains a plan-time `expected`
  mapping per book, populated by the tool that computes the plan (wrangle `Plan`, classify
  apply, staleness, promote backfill, synopsis). One op shape for the CLI and the plugin.
  `stamp_now`, `set_pref`, `create_column` carry none and are never conflict-checked.

### Home of the pure option functions (FOUND-06)
- **D-10:** The already-pure builders leave `wizard.py` for their owning tool modules:
  `classify.scope_options`, `classify.proposal_options`, `engines.engine_options`,
  `engines.default_engine_id`, `engines.engine_rows` (today `wizard._engines`),
  `synopsis.options`. No new module, nothing in `report.py`. `wizard.py` imports them; its
  behaviour and `tests/test_wizard_flow.py` / `tests/test_cli.py` stay unchanged.
- **D-11:** All seven `ui.checklist` call sites (classify.py, promote.py ×2, overrides.py ×2,
  staleness.py, synopsis.py) take `decide=None` defaulting to `ui.checklist`, the seam
  `Plan.run(ask=)` and `promote.backfill(decide=)` already use. The lazy
  `from scourgify import ui` moves behind that default. Phase 2's two-phase split is then a
  `decide`: the PLAN job passes one that records the items and answers `skip`; the EXECUTE
  job passes one that replays the picker's ticks. Items stay display strings in this phase.

### Windows CI lane and the Calibre smoke (XPLAT-03)
- **D-12:** Two required `windows-latest` jobs, both blocking from day one: core tests
  (uv, Python 3.14 only) and the `calibre-debug` smoke. If the Calibre download proves
  flaky, only the smoke job is demoted, as a visible edit to `ci.yml`.
- **D-13:** Calibre lands on the runner as the pinned official MSI
  (`calibre-64bit-<ver>.msi` from download.calibre-ebook.com, 9.11 today), installed with
  `msiexec /qn`, the installer cached with `actions/cache` keyed by version. The pin bumps
  in a commit.
- **D-14:** The same smoke (`tests/smoke_calibre.py`) runs on `ubuntu-latest` too, with the
  official Linux installer pinned to the same version. Windows core tests run on 3.14 only;
  the 3.10/3.13 floor stays proven on ubuntu.

### Claude's Discretion
- **Defaults seam (FOUND-01, locked as extract-once):** cache at
  `user_dir()/cache/<core version>/` (global, not per library); one resolver function replaces
  the `common.DEFAULTS` constant and `f"{HERE}/defaults/..."` string paths in `classify.load_vocab`
  and `engines`. It covers every shipped read-only file the core opens at runtime — `defaults/`
  including `defaults/ao3/`, `classify_vocab*.txt`, and `afm.swift` (the apple engine runs
  `swift {HERE}/afm.swift`, so the plugin's synopsis default needs it on macOS). Extraction reads
  the zip with stdlib `zipfile` from the archive path in `__file__`; on a normal install the
  resolver returns `HERE` directly. `load_vocab()` fails closed like `load_maps()` (an empty
  vocabulary under-quotes the engine picker, NLSpec phase-5 amendment).
- **Uuid resolution:** memoized per library path for the process; a db without a
  `library_id` table (hand-built fixtures) keys on `nouuid-<sha1(library path)[:12]>`;
  `data_dir()` with no resolvable library raises `GuardrailError`.
- **All-skipped run:** still writes a header and footer with zero op lines so History shows
  the attempt.
- **Apple off-macOS (XPLAT-02):** a `platforms` trait in `engines.TRAITS`, checked by
  `usable_engines` before the afm/swift probe. A name test (`== "apple"`) is not acceptable.
- **FOUND-07:** `calibre_open()` gains a `tasklist` branch for the CLI on Windows; the
  in-process funnel keeps skipping it. `tests/test_plugin_safety.py` grows an assertion that
  no job-reachable module calls `subprocess` outside `run_writer`/`rollback_cmd`/`booktext`/
  `engines.Apple`.
- **Lock refusal text and the `expected` key name** on the op dict.
- **`calibre-debug.exe` path on the runner** (`C:\Program Files\Calibre2\` is the
  conventional install dir; STATE.md lists it as unverified — the lane settles it).

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Acceptance and scope
- `.planning/ROADMAP.md` — Phase 1 goal, success criteria 1–5, standing rules every phase inherits
- `.planning/REQUIREMENTS.md` — FOUND-01..07, XPLAT-01..03 (this phase); WRITE-*/DASH-*/REVIEW-* for what consumes the seams
- `.planning/PROJECT.md` — constraints, key decisions, engine facts
- `docs/superpowers/specs/2026-08-06-calibre-plugin-nlspec.md` — "The atomicity contract" (lock, conflict rule, exact predicate); "Constraints → Multi-library" (per-library uuid, phase-4/5 bleed notes); "Constraints → Compatibility" phase-4/5 amendments (DEFAULTS seam, `load_vocab` under-quoting); B1 step 1 (uuid validation against `gui.current_db`)
- GitHub issue `elfensky/scourgify#71` — one write funnel: the pre-write protocol `write_ops`/`run_writer` duplicate; D-07 lands there

### Repo rules and prior designs
- `CLAUDE.md` — write path, edit log, `GuardrailError` rule, `user_dir()` ownership, uv-only, linear `develop`
- `docs/superpowers/specs/2026-07-11-centralized-config-design.md` — the `user_dir()` design D-01 extends
- `.planning/codebase/CONCERNS.md` — `calibre_open()` fragility, Windows gaps, JSONConfig chmod
- `.planning/codebase/ARCHITECTURE.md`, `.planning/codebase/TESTING.md` — module map and test conventions

### Pinned shapes to keep green (and extend)
- `tests/test_paths.py` — `user_dir()`/`data_dir()` resolution
- `tests/test_editlog.py` — record shape and `conflict` predicate
- `tests/test_write_path.py` — one ops list through both write shapes
- `tests/test_wizard_flow.py`, `tests/test_wizard.py`, `tests/test_cli.py` — wizard flows and the source-reading rule (`ui.checklist`, `run_writer(`, `op_set_field(` absent from wizard.py)
- `tests/test_plugin_safety.py`, `tests/test_plugin_source.py` — rich-blocked imports, no `SystemExit`, one `ThreadedJob` site
- `tests/smoke_calibre.py` — the `calibre-debug` smoke D-12/D-14 put in CI
- `.github/workflows/ci.yml`, `build_plugin.py`, `plugin/__init__.py` — the lane and the zip layout

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `common.data_dir()` / `backups_dir()` / `rejects_path()`, `artifacts.*()` path functions, `editlog.log_path()`: every artifact path is already a function over `data_dir()`, so D-01 is one function plus the migration helper.
- `common.library_uuid(con)`: reads `library_id.uuid`; D-03 applies it to a backup file.
- `common.backup_db()` / `_backup_path()` / `_prune_backups(dirpath=)`: already take a directory parameter.
- `editlog.conflict(current, expected, multi)`: THE predicate, pure, tested; `editlog.before_values(read, ops)` is the write-time read D-07 compares against.
- `common.write_ops` and `common.run_writer`: the two copies of the pre-write sequence #71 unifies; `run_writer` already passes `library=lib_uuid` to `editlog.start`.
- `wizard._scope_options`, `_engine_options`, `_synopsis_options`, `_proposal_options`, `_default_engine_id`, `_engines`: already pure with docstrings saying so; D-10 relocates them.
- `Plan.run(ask=)`, `promote.backfill(decide=)`: the injection-seam shape D-11 generalizes.
- `engines.TRAITS` / `trait()` / `usable_engines(env=)`: the row-per-engine table the `platforms` trait joins.
- `build_plugin.py`: asserts `scourgify/defaults/` ships in the zip; the extraction seam reads the same tree.

### Established Patterns
- Paths are functions, never import-time constants (`$SCOURGIFY_HOME` set after import must redirect the tree). The defaults resolver and `data_dir()` follow this.
- Guards raise `GuardrailError`, never `SystemExit`, in anything a job can reach; `cli.main()` converts.
- Injection over environment: `set_library()`, `env=`, `ask=`, `decide=`; `os.environ` never mutated.
- Source-reading tests enforce architecture (AST/grep over `wizard.py`, `plugin/*.py`).
- Plain-assert tests under `tests/`, in CI by glob; fixture libraries from `tests/fixture_db.py`.
- Menu rows keep fixed slots; `ui.menu` returns symbolic ids.

### Integration Points
- `common.py`: `user_dir()` Windows branch, `data_dir()` uuid level + migration, lock dict, defaults resolver, `op_set_field(expected=)`, funnel unification.
- `wrangle.load_maps`, `classify.load_vocab`, `engines.Apple`: consume the defaults resolver.
- `classify.py`, `promote.py`, `overrides.py`, `staleness.py`, `synopsis.py`: `decide=` parameter at each checklist site; option builders land in `classify`/`engines`/`synopsis`.
- `wizard.py`: imports the relocated builders; drops its private copies.
- `.github/workflows/ci.yml`: windows jobs, ubuntu smoke job, Calibre install steps.
- `plugin/action.py`: unchanged this phase; already captures `library_id` per dispatch, which the lock key reuses in Phase 2.

</code_context>

<specifics>
## Specific Ideas

- The real user tree at `~/.config/scourgify/data` holds 527 MB: 22 backups, 14
  `classify_proposal_applied_*.csv`, 10 `promote_review_applied_*.csv`, `promote_ledger.csv`,
  no `edits.jsonl` yet. The migration test fixture should mirror this shape (archives plus a
  backup carrying the uuid) and include the negative case (backup uuid differs → nothing moves).
- The phase-4/5 bleed to reproduce as a test: a 6-book throwaway library reporting book 1 as
  "applied from classify_proposal_applied_20260726-…csv" belonging to the real library.
- Success criterion 1's proof: a classify cost estimate computed from the extracted defaults
  equals the CLI's for the same scope.

</specifics>

<deferred>
## Deferred Ideas

- Checklist items as `(label, payload)` tuples so a Qt picker can render before/after
  columns — decide in Phase 2 when the first verb's picker is built.
- An advisory lock file so a CLI run can say "a plugin job is writing this library" — not
  needed while `calibre_open()` refuses CLI writes with the GUI open; revisit if a
  cross-process case appears.
- Per-library `config.toml` (NLSpec wording "config/column map per library") — rejected for
  now in favour of a global column map; revisit only if a second library needs different columns.

</deferred>

---

*Phase: 01-foundation-a-hostable-core*
*Context gathered: 2026-09-05*
