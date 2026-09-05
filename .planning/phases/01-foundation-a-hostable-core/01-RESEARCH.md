# Phase 1: Foundation — a hostable core - Research

**Researched:** 2026-09-06
**Domain:** Cross-cutting plugin-hosting plumbing (resource bundling, per-library state, in-process concurrency, pure-function refactor, Windows CI) in a stdlib-only Python core
**Confidence:** HIGH (this phase is almost entirely a refactor of code already read in full this session; the only externally-sourced facts are Windows/Calibre CI conventions, tagged CITED below)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Per-library state (FOUND-02, FOUND-03)**
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

**Write-run lock and apply-time conflicts (FOUND-04, FOUND-05)**
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

**Home of the pure option functions (FOUND-06)**
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

**Windows CI lane and the Calibre smoke (XPLAT-03)**
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

### Deferred Ideas (OUT OF SCOPE)

- Checklist items as `(label, payload)` tuples so a Qt picker can render before/after
  columns — decide in Phase 2 when the first verb's picker is built.
- An advisory lock file so a CLI run can say "a plugin job is writing this library" — not
  needed while `calibre_open()` refuses CLI writes with the GUI open; revisit if a
  cross-process case appears.
- Per-library `config.toml` (NLSpec wording "config/column map per library") — rejected for
  now in favour of a global column map; revisit only if a second library needs different columns.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| FOUND-01 | Bundled `defaults/*.csv` readable from inside the plugin zip; `load_maps()`/`load_vocab()` never build empty maps silently | Architecture Patterns (Pattern-3 diagram + defaults resolver), Common Pitfalls #4 (afm.swift's independent `HERE` usage), Don't Hand-Roll (zipfile vs importlib.resources), Validation Architecture Wave-0 gap |
| FOUND-02 | `common.user_dir()` resolves on Windows (`%APPDATA%\scourgify`) with stdlib only | Standard Stack (A1), Environment Availability |
| FOUND-03 | Operational artifacts namespaced per library uuid | Architecture Patterns (Pattern-1), Code Examples (`data_dir()` sketch), Common Pitfalls #1 (the three broken tests), Runtime State Inventory |
| FOUND-04 | Process-global, library-uuid-keyed write-run lock | Architecture diagram, Don't Hand-Roll, Common Pitfalls #5, Validation Architecture Wave-0 gap |
| FOUND-05 | Apply-time conflict checks in the write funnel using `editlog.conflict` | Code Examples (conflict predicate, `check_wipe` injection precedent), Common Pitfalls #5, Open Question #3 (`expected` key shape) |
| FOUND-06 | Wizard's option/decision functions live in tool modules as pure functions | Architecture Patterns (Pattern-2, Pattern-3), Common Pitfalls #2 (`test_wizard.py` blast radius), Open Question #1 |
| FOUND-07 | In-process guards never shell out; Windows uses `tasklist`, never `pgrep`/`ps` | Standard Stack (A2), Common Pitfalls #3, Don't Hand-Roll, Validation Architecture Wave-0 gap |
| XPLAT-01 | Plugin loads and every core module imports under Windows Calibre's bundled Python 3.14 | Environment Availability, Validation Architecture |
| XPLAT-02 | Apple engine cleanly absent off-macOS | Anti-Patterns (platforms trait, not a name test), Validation Architecture Wave-0 gap |
| XPLAT-03 | CI has a `windows-latest` lane running core tests and the `calibre-debug` smoke | Common Pitfalls #3, Environment Availability, Validation Architecture (Phase gate) |
</phase_requirements>

## Summary

This phase adds no new runtime dependency and integrates no new library — it is a disciplined
refactor of `src/scourgify/common.py`, `ops.py`, `editlog.py`, `engines.py`, `wizard.py`, and six
tool modules (`classify.py`, `promote.py`, `overrides.py`, `staleness.py`, `synopsis.py`,
`wrangle.py`), plus a new `windows-latest` CI lane. Every one of the seven FOUND requirements has
a single, already-identified integration point in the current source (verified this session, not
inferred): defaults resolution is two call sites (`wrangle.DEFAULTS_DIR`, `classify.HERE`) plus
one more if `afm.swift`'s path is included (`engines.HERE`); per-library namespacing is exactly
one function, `common.data_dir()`; the write-run lock and apply-time conflict check land in the
pre-write funnel that today exists as **two** near-duplicate functions (`common.write_ops` and
`common.run_writer`) which this phase's roadmap goal explicitly says to unify; the pure
option-function relocation is six named functions in `wizard.py` moving to three owning modules;
and the Windows lane is new CI YAML plus two stdlib-only branches (`user_dir()`, `calibre_open()`).

The single highest-leverage finding from this session's code reading: making `common.data_dir()`
uuid-aware (D-01) turns a **pure path computation** into a function that must **resolve the
library and read `library_id.uuid` out of its sqlite** — and three existing tests
(`test_promote.py`, `test_selection.py`, `test_synopsis_queue.py`) call bare `data_dir()`-backed
functions today with only `$SCOURGIFY_HOME` set and **no** `$CALIBRE_LIBRARY`, which currently
works because `data_dir()` has zero library dependency. Per CONTEXT.md's own discretion note
("`data_dir()` with no resolvable library raises `GuardrailError`"), these three tests will start
raising on the very first line they hit after D-01 lands, unless the plan explicitly updates them
to point at a fixture library. This is not a hypothetical edge case — it is a reproducible test
failure identified by reading the exact lines below, and the planner should budget a task for it.

**Primary recommendation:** treat this phase as a "make `data_dir()`, `write_ops`/`run_writer`,
and the defaults path all take (or resolve) a library identity" refactor with a fixed,
enumerable blast radius — not a rewrite. Every new behavior (lock, conflict check, uuid
namespacing, Windows paths) is small, addable to existing functions without changing their public
call shape, and testable with the existing `fixture_db.py` + `scripted_answers` + monkeypatch
idioms already used throughout `tests/`.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Bundled defaults readable from the zip (FOUND-01) | Tool Layer (`common.py` resolver) | Read Layer (extraction reads the zip via stdlib `zipfile`) | `wrangle.load_maps`/`classify.load_vocab`/`engines.Apple` are pure-compute consumers; the extraction/caching is a filesystem concern that belongs beside `user_dir()`, not inside the tools |
| Per-library state namespacing (FOUND-03) | Shared Infrastructure (`common.data_dir()`) | Read Layer (uuid resolved via `ro_connect()` + `library_uuid()`) | One function is the seam every artifact path (`artifacts.py`, `editlog.py`, `classify.py`, `promote.py`) already funnels through |
| Write-run lock (FOUND-04) | Write Layer (`common.py`, in-process dict) | — | Lives beside `write_ops`/`run_writer`, the two funnels a lock must gate identically |
| Apply-time conflict check (FOUND-05) | Write Layer (the unified pre-write funnel) | Shared Infrastructure (`editlog.conflict`, already pure/tested) | Must run on the near side of the `calibre-debug` subprocess boundary so the CLI needs no result plumbing back — this pins it to the funnel, not to `ops.apply_ops` |
| Pure option functions (FOUND-06) | Tool Layer (`classify.py`, `engines.py`, `synopsis.py`) | Entry Points (`wizard.py` becomes a consumer, not an owner) | A Qt picker (Phase 2+) must read the same functions the wizard does — ownership belongs with the tool that knows the domain (scope, engine, proposal), not the interactive shell |
| In-process guard purity (FOUND-07) | Write Layer (`common.calibre_open()`) | Entry Points (`plugin/action.py` never calls it — already true) | `calibre_open()` is a CLI-only concept; the Windows branch is additive to an existing CLI-only function, not new plugin surface |
| Windows CI lane (XPLAT-01..03) | Cross-Cutting (CI config) | — | No production code tier — this is pipeline infrastructure |

## Standard Stack

### Core

No new library is added in this phase. Every requirement is satisfiable with stdlib:

| Capability | stdlib module | Why Standard |
|------------|--------------|--------------|
| Zip-internal resource extraction (FOUND-01) | `zipfile`, `os.path.dirname(__file__)`-derived archive path | The project's hard constraint (Calibre's bundled Python has empty site-packages) already forces stdlib-only in every core module; `zipfile` is the same mechanism Python's own `zipimport` uses internally |
| Per-library uuid read (FOUND-03) | `sqlite3` (already the project's only DB dependency) | `common.library_uuid()` already exists and does exactly this |
| Write-run lock (FOUND-04) | `threading.Lock` in a module-level `dict` | CONTEXT.md D-06 locks this in: "a module-level dict of `threading.Lock`s in `common.py`, keyed by library uuid" — no new dependency, and it is process-local by design (D-06 explicitly rules out a cross-process file lock) |
| Windows path (FOUND-02) | `os.environ.get("APPDATA")` | Matches Calibre's own convention: Calibre's config folder on Windows is `%APPDATA%\calibre` \[CITED: manual.calibre-ebook.com/customize.html, manual.calibre-ebook.com/faq.html\] — `%APPDATA%\scourgify` mirrors it exactly, as CONTEXT D-05 specifies |
| Windows process detection (FOUND-07) | `subprocess.run(["tasklist", "/FI", "IMAGENAME eq calibre.exe"])` | `tasklist /FI "IMAGENAME eq <name>"` is the documented Windows-native equivalent of `pgrep`/`ps` \[CITED: learn.microsoft.com/en-us/windows-server/administration/windows-commands/tasklist\] |

### Package Legitimacy Audit

**Not applicable this phase.** No external package is added, upgraded, or newly imported —
every requirement above is satisfied with modules already in the Python standard library and
already imported elsewhere in this codebase (`sqlite3`, `zipfile`, `threading`, `subprocess`,
`os`). `pyproject.toml`'s only runtime dependency remains `rich>=13`, unchanged
\[VERIFIED: pyproject.toml:28-30 — `dependencies = [\n    "rich>=13",\n]`\], and `rich` stays
confined to `wizard.py`/`ui.py`/`report.py` per the existing (and unchanged-by-this-phase)
architecture.

**Packages removed due to [SLOP] verdict:** none.
**Packages flagged as suspicious [SUS]:** none.

## Architecture Patterns

### System Architecture Diagram

```text
                    ┌───────────────────────────────────────────────────────┐
                    │  Entry points (unchanged by this phase)                │
                    │  cli.py · wizard.py · plugin/action.py                 │
                    └───────────────┬─────────────────┬───────────────┬─────┘
                                    │                 │               │
                     wizard imports │                 │ plugin will   │ CLI already
                     pure options   │                 │ call in Ph.2  │ calls today
                                    ▼                 ▼               ▼
        ┌───────────────────────────────────┐   ┌─────────────────────────────┐
        │ Tool modules (own their options)   │   │ common.write_ops /          │
        │  classify.scope_options()          │   │ common.run_writer           │
        │  classify.proposal_options()       │   │  (→ ONE pre-write funnel    │
        │  engines.engine_options()          │   │     this phase, per #71)    │
        │  engines.default_engine_id()       │   │                              │
        │  engines.engine_rows()             │   │  1. resolve library uuid    │
        │  synopsis.options()                │   │  2. acquire per-uuid lock   │
        └───────────────┬────────────────────┘   │     (FOUND-04) — refuse if  │
                        │                        │     held, naming the job    │
      decide= injection │ (7 ui.checklist sites) │  3. check_wipe()            │
                        ▼                        │  4. before-read (editlog)   │
        ┌───────────────────────────────────┐    │  5. apply-time conflict     │
        │ classify / promote / overrides /   │    │     check (FOUND-05):      │
        │ staleness / synopsis: decide=None   │    │     editlog.conflict(cur,  │
        │  → defaults to ui.checklist          │───▶     expected) per op,      │
        │  (unchanged behavior; Qt picker      │    │     drop conflicting ops   │
        │   injects its own in Phase 2)        │    │  6. backup_db()            │
        └───────────────────────────────────┘    │  7. editlog.start/apply/    │
                                                   │     finish (skipped list    │
                                                   │     in the footer)          │
                                                   │  8. release lock             │
                                                   └──────────────┬───────────────┘
                                                                  ▼
                                                   ┌─────────────────────────────┐
                                                   │ ops.apply_ops (UNCHANGED)    │
                                                   │  — stays the one executor    │
                                                   └─────────────────────────────┘

        ┌────────────────────────────────────────────────────────────────────┐
        │ Resource + state resolution (new this phase)                       │
        │                                                                      │
        │  common.defaults_dir()  ──▶ inside a normal install: HERE/defaults  │
        │       (FOUND-01)             inside the plugin zip: extract-once to │
        │                              user_dir()/cache/<core version>/       │
        │                              (zipfile, stdlib) — GuardrailError if  │
        │                              extraction fails, never an empty map   │
        │                                                                      │
        │  common.data_dir()      ──▶ user_dir()/data/<library-uuid>/         │
        │       (FOUND-03)             uuid via ro_connect()+library_uuid(),  │
        │                              memoized per library path for the       │
        │                              process; one-time guarded auto-move    │
        │                              of the legacy flat data/ tree (D-02/03)│
        │                                                                      │
        │  common.user_dir()      ──▶ $SCOURGIFY_HOME, else on Windows        │
        │       (FOUND-02)             %APPDATA%\scourgify, else XDG          │
        │                                                                      │
        │  common.calibre_open()  ──▶ sys.modules check, then pgrep/ps on     │
        │       (FOUND-07)             POSIX OR tasklist on Windows           │
        └────────────────────────────────────────────────────────────────────┘
```

### Recommended Project Structure

No new files/directories. Every change lands in an existing module:

```
src/scourgify/
├── common.py       # + defaults_dir(), data_dir() uuid-aware, _LOCKS dict, Windows user_dir()/calibre_open() branches,
│                    #   op_set_field(expected=), unified write funnel (write_ops/run_writer share one pre-write sequence)
├── editlog.py       # unchanged predicate (editlog.conflict already pure/tested); footer gains "skipped" list
├── engines.py       # + engine_options(), default_engine_id(), engine_rows() (was wizard._engines);
│                    #   + "platforms" trait; Apple's afm.swift path routes through common.defaults_dir()
├── classify.py      # + scope_options(), proposal_options() (was wizard._scope_options/_proposal_options);
│                    #   ui.checklist call site (line 230) gains decide=None; load_vocab via defaults_dir()
├── synopsis.py      # + options() (was wizard._synopsis_options); ui.checklist call site (line 271) gains decide=None
├── promote.py       # 2 ui.checklist call sites (lines 212, 341) gain decide=None
├── staleness.py      # 1 ui.checklist call site (line 72) gains decide=None
├── overrides.py     # 2 ui.checklist call sites (lines 181, 229) gain decide=None
├── wrangle.py        # load_maps() consumes common.defaults_dir() instead of common.DEFAULTS constant
├── wizard.py         # loses the 6 relocated pure functions; imports them from classify/engines/synopsis
└── ops.py            # UNCHANGED — apply_ops stays the one executor; conflict check lives in the funnel, not here

.github/workflows/
└── ci.yml            # + windows-latest job (core tests, uv, Python 3.14 only) + calibre-debug smoke
                       #   job on BOTH windows-latest and ubuntu-latest (new; today ubuntu has no smoke job at all)

tests/
├── test_paths.py     # + Windows user_dir() tests, uuid-level data_dir() tests, migration tests
├── test_editlog.py    # + skipped-conflict footer shape test
├── test_write_path.py # + lock-refusal test, apply-time conflict test
├── test_plugin_safety.py  # + assertion: no job-reachable module calls subprocess outside
│                          #   run_writer/rollback_cmd/booktext/engines.Apple (per D "FOUND-07" discretion)
├── test_wizard.py    # UPDATE: currently calls wizard._scope_options/_engine_options/_proposal_options/
│                      #   _default_engine_id/_engines directly (see Common Pitfalls #2) — must be repointed
├── test_promote.py, test_selection.py, test_synopsis_queue.py  # UPDATE: 3 tests call bare data_dir()-backed
│                      #   functions with no CALIBRE_LIBRARY set (see Common Pitfalls #1)
└── (new) test_ci_windows manual verification via the lane itself, not a test file
```

### Pattern 1: Path-as-function, extended to identity-dependent paths

**What:** Every user-writable path in this codebase is already a *function*, never an import-time
constant, specifically so `$SCOURGIFY_HOME` set after import still redirects the tree
\[VERIFIED: src/scourgify/common.py:29-30 — `# Paths are FUNCTIONS, never import-time constants —
$SCOURGIFY_HOME set after import (tests)\n# must still redirect the whole tree.`\]. This phase
extends that same pattern one level further: `data_dir()` becomes a function whose output depends
not just on an env var but on a **sqlite read** (the library's uuid). The precedent to follow is
`common.library_uuid(con)`, which already exists and is already tested against a fixture db
\[VERIFIED: src/scourgify/common.py:455-463\]:

```python
def library_uuid(con: sqlite3.Connection) -> str | None:
    """Calibre's own library id, the edit log's per-library key (records from one library must
    never mix into another's history). None if the table is absent — a hand-built fixture db is
    not a reason to refuse a write."""
    try:
        r = con.execute("SELECT uuid FROM library_id").fetchone()
    except sqlite3.OperationalError:
        return None
    return r[0] if r else None
```

**When to use:** `data_dir()`'s new implementation should open its own short-lived `ro_connect()`
to call this function, memoize the result per library path for the process (per CONTEXT's
discretion note), and fall back to `nouuid-<sha1(library path)[:12]>` when the table is absent
(a hand-built fixture db) — exactly the shape `library_uuid()`'s own docstring already anticipates.

### Pattern 2: `decide=` / `ask=` injection seam (already established, not invented this phase)

**What:** The codebase already has two working examples of "a function takes an optional
callback that defaults to the interactive implementation": `wrangle.Plan.run(ask=)` and
`promote.backfill(decide=)`. FOUND-06's checklist relocation (D-11) is explicitly this same
pattern, generalized to seven call sites.

**When to use:** Every `ui.checklist` call site. The existing `promote.backfill` precedent is
*not* a literal copy-paste template — its `decide(chg, adds) -> chg` signature is a
higher-level "give me the whole decision" shape \[VERIFIED: src/scourgify/promote.py:349,368-371 —
`def backfill(yes: bool = False, step: bool = False, decide=None) -> int:` ... `if decide is not
None:                     # a front door supplying its own question\n        chg =
decide(chg, adds)`\]. D-11's shape is simpler and closer to a straight default-swap: `decide`
defaults directly to `ui.checklist` itself, called with `ui.checklist`'s own existing signature:

```python
# ui.checklist's existing, unchanged signature and return shape:
def checklist(title, items, subtitle=""):
    """... Returns (accepted_idx, rejected_idx, action) where action ∈ {'apply','skip','quit'} ...
    `items` is a list of display strings."""
```
\[VERIFIED: src/scourgify/ui.py:121-125\]

```python
# The shape each of the 7 call sites moves to (illustrative — not existing code):
def apply_proposal_step(decide=None):
    if decide is None:
        from scourgify import ui
        decide = ui.checklist          # the lazy import moves BEHIND this default
    acc, rej, action = decide(title, items, subtitle=subtitle)
    ...
```

### Pattern 3: Source-reading tests as the architecture enforcement mechanism

**What:** This codebase does not rely on code review discipline to keep the wizard thin, the
plugin thin, or `SystemExit` out of job-reachable code — it greps/AST-walks the actual source in
CI. `tests/test_cli.py` already bans three literal strings from `wizard.py`'s body
\[VERIFIED: tests/test_cli.py:93-95 — `for banned, why in (("ui.checklist", "drives a review
checklist — move it into the tool module"),\n                        ("run_writer(", "assembles a
write — call the tool's function, which guards it"),\n                        ("op_set_field(",
"builds a write op — that belongs with the tool's write path")):`\].

**When to use:** After D-10/D-11 land, `ui.checklist` should *already* disappear from
`wizard.py`'s body as a side effect of the relocation — this existing test becomes the
regression guard for FOUND-06, not a new test to write. The planner should note this test
currently PASSES (the ban is already true) and must keep passing unchanged (Success Criterion 4).
For FOUND-07, CONTEXT.md's discretion item explicitly asks for a **new** assertion in
`tests/test_plugin_safety.py`: "no job-reachable module calls `subprocess` outside
`run_writer`/`rollback_cmd`/`booktext`/`engines.Apple`" — this is a genuinely new test (not a
relocation), and the exact JOB_REACHABLE module list to check against already exists
\[VERIFIED: tests/test_plugin_safety.py:113-114 — `JOB_REACHABLE = ["common", "editlog",
"select", "artifacts", "overrides", "engines", "booktext",\n                 "wrangle",
"classify", "promote", "staleness", "setup", "ops"]`\].

### Anti-Patterns to Avoid

- **Re-deriving the uuid inline at each call site instead of centralizing in `data_dir()`:**
  every artifact path already funnels through `data_dir()` (see verified call-site list in
  Common Pitfalls below) — touching any of those six other files to add uuid logic would be the
  "second executor" mistake this codebase explicitly avoids elsewhere (`ops.py`'s docstring:
  "a second executor is a second set of coercion rules, and the two would disagree… on exactly
  the day it matters" \[VERIFIED: src/scourgify/ops.py:7-8\]). D-01's own framing — "one function
  changes" — depends on this discipline being followed.
- **Adding a cross-process/file-based lock for FOUND-04:** CONTEXT.md D-06 explicitly rejects
  this ("No lock file, no pid probing") in favor of an in-process `threading.Lock` dict, because
  a CLI write is already refused by `calibre_open()` while the GUI is open, and two CLI processes
  racing is explicitly out of scope for this phase.
- **Checking conflicts inside `ops.apply_ops`:** the discussion log records this as a
  considered-and-rejected option ("needs a result JSON back from `_writer.py` for the CLI")
  \[VERIFIED: 01-DISCUSSION-LOG.md:57\] — the conflict check belongs in the funnel, on the CLI
  side of the `calibre-debug` subprocess boundary, so `_writer.py` and `ops.py` (which
  deliberately import nothing from `scourgify` — `calibre-debug -e` puts only the script's own
  directory on `sys.path` \[VERIFIED: src/scourgify/ops.py:14-16\]) stay untouched.
- **Using a bare `== "apple"` string test for the platform trait (XPLAT-02):** CONTEXT.md is
  explicit — "A name test (`== "apple"`) is not acceptable" — because `engines.py`'s whole design
  point is that adding/constraining an engine is "a row here, not a hunt for `== "apple"`
  scattered across the tools" \[VERIFIED: src/scourgify/engines.py:19-21\]. The `platforms` trait
  must be added to `TRAITS["apple"]` and checked inside `usable_engines()` before the afm/swift
  probe.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Reading which library a sqlite db belongs to | A second uuid-reading query somewhere else | `common.library_uuid(con)` (already exists, already tested) | One predicate, one place it can be wrong |
| Deciding if a value has drifted since a plan was computed | A second equality check for the apply-time conflict rule | `editlog.conflict(current, expected, multi)` (already exists, pure, tested — `tests/test_editlog.py:239-250`) | CONTEXT D-07 explicitly reuses this: "the funnel… compares that read to the plan-time expected value carried on the op with `editlog.conflict`" |
| Detecting a running GUI process | A bespoke Windows process scan | Extend `calibre_open()`'s existing two-branch structure with a third branch for `tasklist` | `calibre_open()` already has the "no detector available → fail closed" fallback (line 365) that a new Windows branch should preserve, not replace |
| Zip-internal resource extraction | A custom half-implementation of `importlib.resources` | Plain `zipfile` against the archive's own path (derivable from `__file__` when running from inside a zip) | The project's constraint is "no vendored deps… stdlib only" \[VERIFIED: CLAUDE.md — "The whole core must keep importing clean under Calibre's bundled Python 3.14.6 with empty site-packages"\]; `importlib.resources` needs the package to be *importable as a package* with proper `__init__.py`/metadata, which zipimport of a flat `scourgify/` directory (per `build_plugin.py`'s layout) may not present cleanly — plain `zipfile.ZipFile(archive_path)` reading by name is simpler and matches what `build_plugin.py` itself already does to construct the zip |

**Key insight:** every "don't hand-roll" item above is not a third-party library recommendation
(there is nothing external to add) — it is "don't hand-roll a second copy of a function that
already exists in this exact codebase." The single biggest risk in this phase is architectural
drift (two conflict checks, two uuid readers, two Windows-detection branches), not missing
functionality.

## Runtime State Inventory

This phase is a "rename/refactor of on-disk layout" phase for one specific case: D-01/D-02/D-03
move the existing flat `data/` tree into `data/<uuid>/`. The canonical question applies: *after
every file in the repo is updated, what runtime systems still have the old layout cached, stored,
or registered?*

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | The real user tree at `~/.config/scourgify/data` holds **527 MB**: 22 backups, 14 `classify_proposal_applied_*.csv`, 10 `promote_review_applied_*.csv`, `promote_ledger.csv`, no `edits.jsonl` yet \[CITED: 01-CONTEXT.md:183-186, from the discuss-phase session's direct inspection\] | Data migration: the one-time guarded auto-move (D-02) must relocate all of this into `data/<uuid>/`, proven by reading the uuid out of the newest `ff_*.db` backup (D-03) |
| Live service config | None — this project has no external service (n8n/Datadog-style) with config living outside git; `config.toml` and `overrides/` are explicitly staying **global**, not per-library (D-01: "the library-uuid level sits at `data/<uuid>/` only") | None |
| OS-registered state | None found. No Task Scheduler/launchd/systemd/pm2 registration exists for this CLI tool; Calibre itself is the only OS-level registered process, and it is not renamed or moved by this phase | None |
| Secrets/env vars | None affected — API keys live in the plugin's `JSONConfig` (Phase 3+ territory) or in environment variables (`ANTHROPIC_API_KEY` etc.), neither of which is touched by the `data/` layout change | None |
| Build artifacts | None — no installed package or compiled binary embeds the `data/` path; `common.HERE`/`DEFAULTS` point at the package install location, unrelated to the user's `data/` tree | None |

**Negative case to test (per D-03):** a throwaway/fixture library opened *first*, before the real
library, must **never** claim the real library's `data/` tree. The migration's "owner proof" —
reading the uuid out of the newest legacy backup and moving only on an exact match — is the
mechanism; the test should build a fixture db with a *different* uuid than a planted legacy
`data/backups/ff_*.db`, run the resolution, and assert the legacy tree is untouched and the
non-match is logged (not silently ignored).

## Common Pitfalls

### Pitfall 1: `data_dir()` becoming uuid-aware breaks three existing tests that call it with no `$CALIBRE_LIBRARY` set

**What goes wrong:** `common.data_dir()` today is a pure path computation with zero dependency on
a resolvable library — it calls only `user_dir()` \[VERIFIED: src/scourgify/common.py:42-44 —
`def data_dir() -> str:\n    """data/ under user_dir() — personal review maps, proposals,
intermediates (gitignored)."""\n    return os.path.join(user_dir(), "data")`\]. Three existing
tests exploit this by setting `$SCOURGIFY_HOME` but never `$CALIBRE_LIBRARY`, then calling
`common.data_dir()` or an `artifacts.*()` function bare:

- `tests/test_promote.py:348-349` — `os.makedirs(common.data_dir()); …\n            with
  open(artifacts.review(), "w") as f:` inside `test_apply_decisions_counts_rows_it_could_not_decide`
  \[VERIFIED: tests/test_promote.py:348-349\]
- `tests/test_selection.py:183` — `os.makedirs(common.data_dir(), exist_ok=True)` inside
  `test_unclassified_defaults_own_the_invariant` \[VERIFIED: tests/test_selection.py:183\]
- `tests/test_synopsis_queue.py:72` — `os.makedirs(common.data_dir(), exist_ok=True)` inside
  `test_queue_defaults_read_the_failure_log` \[VERIFIED: tests/test_synopsis_queue.py:72\]

**Why it happens:** CONTEXT.md's own discretion note locks the intended new behavior:
"`data_dir()` with no resolvable library raises `GuardrailError`" (01-CONTEXT.md:104). Once
`data_dir()` needs to read `library_id.uuid`, it must first call `library()`, which already
raises `GuardrailError` when `$CALIBRE_LIBRARY` is unset \[VERIFIED: src/scourgify/common.py:
104-113\]. None of the three tests above sets that variable.

**How to avoid:** budget an explicit task to update these three tests to either (a) point
`$CALIBRE_LIBRARY` at a `fixture_db.build(...)`-constructed throwaway library before calling
`data_dir()`/`artifacts.*()`, or (b) construct the expected uuid-keyed path by hand for the
assertion. This is a small, mechanical change per test (the fixture-building idiom is already
used throughout `tests/`), but it must be enumerated as a task — it is not covered by "one
function changes" in D-01's reversibility note, which is true for *production* code but not for
*test* code exercising the old contract.

**Warning signs:** running `uv run tests/test_promote.py`, `uv run tests/test_selection.py`, and
`uv run tests/test_synopsis_queue.py` after landing D-01 and seeing a `GuardrailError:
Set CALIBRE_LIBRARY…` traceback instead of the expected assertion failure/pass.

### Pitfall 2: `test_wizard.py` directly calls the six functions D-10 relocates — CONTEXT.md only promises `test_wizard_flow.py`/`test_cli.py` stay unchanged

**What goes wrong:** `tests/test_wizard.py` calls `wizard._scope_options(...)`,
`wizard._engine_options(...)`, `wizard._proposal_options(...)`, `wizard._default_engine_id(...)`,
and `wizard._engines(...)` directly, by that exact module-qualified name, in at least 9 test
functions \[VERIFIED: tests/test_wizard.py:54-129, confirmed via grep — e.g. line 58: `changed,
empty = wizard._scope_options({1: "new", 2: "updated", 3: "new"}, 100, 50)[0], \` and line 90:
`def test_engines_cloud_usable_iff_key_in_env():`\]. Phase 1's success criterion 4 promises only
that `tests/test_wizard_flow.py` and `tests/test_cli.py` pass unchanged
\[VERIFIED: .planning/ROADMAP.md:59\] — it says nothing about `test_wizard.py`.

**Why it happens:** D-10 relocates these functions to `classify.scope_options`,
`classify.proposal_options`, `engines.engine_options`, `engines.default_engine_id`,
`engines.engine_rows` — genuinely new names in genuinely new modules, not aliases. If `wizard.py`
does not keep a private re-export (`_scope_options = classify.scope_options`, etc.) purely for
`test_wizard.py`'s benefit, every one of those 9+ test functions breaks on `AttributeError`.

**How to avoid:** this is a real decision the planner must make, not something CONTEXT.md
resolved — pick one of: (a) keep thin re-export aliases in `wizard.py` so `test_wizard.py` needs
no changes (cheapest, but means `wizard.py`'s namespace still exposes the moved functions,
slightly undercutting FOUND-06's "genuinely relocated" intent), or (b) update `test_wizard.py`'s
~9 call sites to `classify.scope_options(...)` / `engines.engine_options(...)` / etc. (more
invasive, but matches the spirit of the relocation and gives the new owning modules their own
direct test coverage). Flag this as an Open Question below; either choice is small once decided.

### Pitfall 3: the "Windows msi/tasklist" facts are CITED, not VERIFIED — the CI lane is where they get proven

**What goes wrong:** `calibre-64bit-<ver>.msi` silent install via `msiexec /qn`, and
`tasklist /FI "IMAGENAME eq calibre.exe"` for process detection, are both well-documented
conventions \[CITED: silentinstallhq.com/calibre-silent-install-how-to-guide,
learn.microsoft.com/en-us/windows-server/administration/windows-commands/tasklist\] but **have
not been run against a real `windows-latest` GitHub Actions runner in this session** — no probe
was executed, so this is not a falsification, just unexercised community-documented behavior.
STATE.md already flags the related fact as unverified: "XPLAT-03's `calibre-debug.exe` resolution
on `windows-latest` is conventional, not verified on a real Windows install — the CI lane is where
it gets settled" \[VERIFIED: .planning/STATE.md:79\].

**Why it happens:** GitHub Actions `windows-latest` runners are not accessible for interactive
probing outside a real CI run.

**How to avoid:** plan the Windows CI lane as its own early task (not gated behind other Phase 1
work), so a red run surfaces path/installer problems immediately rather than at the end of the
phase. `C:\Program Files\Calibre2\` is the documented 64-bit install location
\[CITED: various Calibre/Windows sources; matches STATE.md's own "conventional" framing\] and is
a reasonable first guess for locating `calibre-debug.exe`, but the plan should not hard-fail if
it differs — search `actions/cache`'s restored path or `Get-Command` as a fallback.

**Warning signs:** the `windows-latest` core-tests job passing (pure Python, no Calibre needed)
while the `calibre-debug` smoke job fails specifically on "file not found" for the installer or
the debug binary — that is exactly the CITED-not-VERIFIED gap resolving itself.

### Pitfall 4: `engines.Apple`'s `afm.swift` path must also route through the new defaults resolver, not just `wrangle`/`classify`

**What goes wrong:** CONTEXT.md's Defaults seam discretion item explicitly includes
`afm.swift` in scope ("it covers every shipped read-only file the core opens at runtime —
`defaults/` including `defaults/ao3/`, `classify_vocab*.txt`, and `afm.swift`"), but
`engines.py`'s `Apple.__init__` currently builds its subprocess command directly off the raw
`HERE` constant: `cmd = [exe] if exe else ["swift", f"{HERE}/afm.swift"]`
\[VERIFIED: src/scourgify/engines.py:79-81\]. Inside the zip, `HERE` is a zip-internal path
(`build_plugin.py` explicitly excludes the compiled `afm` binary from the zip via `SKIP_NAMES =
{"afm"}` \[VERIFIED: build_plugin.py:15\] but ships `afm.swift` itself), so `os.path.exists(f"{HERE}/afm")`
correctly returns `False`, but the fallback `f"{HERE}/afm.swift"` is *also* not a real filesystem
path the `swift` CLI can open.

**Why it happens:** it is easy to fix `wrangle.load_maps` and `classify.load_vocab` (the two most
visible consumers named in Success Criterion 1) and miss `engines.py`'s independent `HERE` usage,
since it is a different failure mode (a subprocess launch failure, not a silently-empty map) and
does not show up in a classify cost-estimate comparison.

**How to avoid:** route `engines.py`'s `afm.swift` path through the same `common.defaults_dir()`
(or equivalent) resolver this phase introduces, not through the raw `HERE` constant, so the
synopsis pass's apple engine works identically inside and outside the plugin zip on macOS.

**Warning signs:** the classify cost-estimate parity test (Success Criterion 1) passes, but a
manual macOS plugin smoke test of the synopsis stage's apple engine fails with a `swift: error:
no such file` or equivalent.

### Pitfall 5: `write_ops` and `run_writer` are two separate functions today — "one shared funnel" is this phase's work, not a given

**What goes wrong:** planning the FOUND-04/FOUND-05 work as "add a lock check and a conflict
check to the write path" undersells the scope if it is read as "add it to one function" — today
there are genuinely two independent implementations of the pre-write sequence
(`common.write_ops` for the plugin, `common.run_writer` for the CLI), each separately calling
`check_wipe`, `editlog.before_values`, `backup_db`, `editlog.start`/`finish`
\[VERIFIED: src/scourgify/common.py:526-607, both functions read in full this session\]. CONTEXT
D-07 says the funnel "already reads every op's current value through `editlog.before_values`" and
implies the conflict check slots into that shared step, but the roadmap phase goal is explicit
that unifying them is itself part of this phase's work: "Closes the pre-write protocol half of
#71 (one write funnel)" \[VERIFIED: .planning/ROADMAP.md:52\].

**Why it happens:** the two functions already look similar (same guard order) which can make
"just add the check to both" seem sufficient, when the actual roadmap intent is to collapse them
into one function both callers invoke, so a future third property (e.g. a Phase 2 review-atomicity
rule) only needs to be added once.

**How to avoid:** scope a task explicitly for the extraction of a shared pre-write helper (taking
the difference between the two — subprocess-vs-in-process apply, `print`-vs-`out=` callback — as
parameters), verified by the existing shadow-replay test pattern (`tests/test_write_path.py`'s
`test_shadow_replay_cli_and_in_process_agree` is the precedent for "one ops list, both paths, same
result").

**Warning signs:** the plan ends up with the conflict check duplicated in two places with two
slightly different edge-case behaviors (e.g., one drops empty change-sets before the check, the
other after) — exactly the class of bug `ops.py`'s own docstring warns about.

## Code Examples

### `data_dir()` uuid resolution (illustrative sketch — not existing code; consistent with CONTEXT D-01/D-03 and the existing `library_uuid()` pattern)

```python
# common.py — sketch, not verified/existing code
import hashlib

_UUID_CACHE: dict[str, str] = {}     # library path -> resolved uuid, memoized per process

def _resolve_uuid() -> str:
    lib = library()                                    # raises GuardrailError if unresolvable
    if lib not in _UUID_CACHE:
        con = ro_connect()
        try:
            u = library_uuid(con)                       # existing, verified function
        finally:
            con.close()
        _UUID_CACHE[lib] = u or f"nouuid-{hashlib.sha1(lib.encode()).hexdigest()[:12]}"
    return _UUID_CACHE[lib]

def data_dir() -> str:
    return os.path.join(user_dir(), "data", _resolve_uuid())
```

### The existing `library_uuid()` this pattern depends on

```python
# Source: src/scourgify/common.py:455-463 (verified, read this session)
def library_uuid(con: sqlite3.Connection) -> str | None:
    """Calibre's own library id, the edit log's per-library key (records from one library must
    never mix into another's history). None if the table is absent — a hand-built fixture db is
    not a reason to refuse a write."""
    try:
        r = con.execute("SELECT uuid FROM library_id").fetchone()
    except sqlite3.OperationalError:
        return None
    return r[0] if r else None
```

### The conflict predicate FOUND-05 reuses verbatim

```python
# Source: src/scourgify/editlog.py:135-146 (verified, read this session)
def conflict(current, expected, multi: bool) -> bool:
    """Has `current` drifted from the `expected` value a plan/undo was computed against?

    THE predicate — apply-time conflict checks and undo replay share it, so "conflict" cannot
    come to mean two things. Multi-value fields compare AS SETS (order-insensitive, raw values,
    no alias or case normalization — a tag reordered by Calibre is not an edit; a tag whose case
    the user changed IS). Single-value fields compare as strings, with None and "" the same
    absence. Pure — see tests."""
    if multi:
        return {str(x) for x in (current or ())} != {str(x) for x in (expected or ())}
    return ("" if current is None else str(current)) != ("" if expected is None else str(expected))
```

### The existing `check_wipe` injection-of-reader pattern the lock/conflict work should mirror

```python
# Source: src/scourgify/common.py:474-490 (verified, read this session)
def check_wipe(ops: list[dict], populated) -> None:
    """Raise GuardrailError if `ops` would catastrophically empty a populated column.

    `populated` (field -> set of book ids currently holding a value) is INJECTED because the two
    callers must read the before-state from different places — the CLI from read-only sqlite
    (_populated_books), a plugin from the live handle (populated_via_api). The verdict itself
    stays one function, so both writers refuse exactly the same change-sets."""
```
This is the existing precedent for "one verdict function, two before-state readers injected by
the caller" — the same shape the unified write funnel (Pitfall 5) and the apply-time conflict
check should follow: one `_check_conflicts(ops, current_reader)` function, called identically from
both `write_ops` and `run_writer`, each injecting its own before-read (`values_via_api` vs
`column_values`, already both existing functions).

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|---------------|--------|
| `common.HERE`/`common.DEFAULTS` used directly by `wrangle`/`classify`/`engines` | A `common.defaults_dir()` resolver that extracts-once from the zip when needed | This phase (FOUND-01) | Every consumer of bundled data gets zip-safety for free instead of needing its own zip-awareness |
| `data_dir()` = pure path math | `data_dir()` = library-uuid-resolved path (one sqlite read, memoized) | This phase (FOUND-03) | See Pitfall 1 — this is the one behavior change with real test blast radius |
| `wizard.py` owns 6 pure option-computation functions | The owning tool module (`classify`/`engines`/`synopsis`) owns them; `wizard.py` is a pure consumer | This phase (FOUND-06) | Enables Phase 2's Qt-picker-consumes-the-same-function plan without touching `wizard.py` again |
| `write_ops`/`run_writer` each independently sequence guard→backup→log→apply | One shared pre-write sequence both call | This phase (FOUND-04/05, closing #71's pre-write half) | A future new pre-write property (e.g. Phase 2/5's review-atomicity needs) is added once |

**Deprecated/outdated:** nothing in this codebase is being deprecated by this phase — it is
additive namespacing and a refactor-for-shared-ownership, not a replacement of an existing
approach with a fundamentally different one.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `%APPDATA%\scourgify` is a reasonable literal implementation of FOUND-02's Windows path, matching Calibre's own `%APPDATA%\calibre` convention | Standard Stack, Architecture Patterns | Low — this is CITED against Calibre's own official docs, not purely assumed; residual risk is only in exact `os.environ["APPDATA"]` availability across all supported Windows versions, which is a decades-stable Windows convention |
| A2 | `tasklist /FI "IMAGENAME eq calibre.exe"` correctly identifies a running Calibre GUI process the way `pgrep -fl calibre` does today, including the `sys.modules` in-process short-circuit already present in `calibre_open()` | Standard Stack, Common Pitfalls #3 | Medium — the *syntax* is CITED (Microsoft Learn), but whether Calibre's Windows GUI process is literally named `calibre.exe` (vs. `calibre-parallel.exe` workers needing the same exclusion logic `_is_calibre_gui` already applies on POSIX) has not been verified against a real Windows Calibre install this session; the Windows CI lane (XPLAT-03) is explicitly where this gets settled per STATE.md's own blocker note |
| A3 | `C:\Program Files\Calibre2\` is where `calibre-debug.exe` lives on a fresh `windows-latest` GitHub Actions runner after the pinned MSI install | Common Pitfalls #3 | Medium — CITED from general Calibre/Windows documentation, but STATE.md already flags this specific fact as unverified and defers resolution to the CI lane itself; the plan should not hard-code this path without a fallback search |
| A4 | Plain `zipfile` reading (rather than `importlib.resources`) is the right mechanism for FOUND-01's extraction, given the zip's flat `scourgify/` layout | Don't Hand-Roll | Low — `build_plugin.py`'s own construction of the zip (verified, read this session) already treats it as a flat directory tree via `zipfile.ZipFile(...).write()`, so reading it back the same way is the lower-risk symmetric choice, but `importlib.resources` was not tested against this exact zip layout to rule it out definitively |

## Open Questions

1. **Does `wizard.py` keep re-export aliases for the six relocated pure functions, or does
   `test_wizard.py` get updated to call the new owning modules directly?**
   - What we know: CONTEXT.md's success criterion only requires `test_wizard_flow.py` and
     `test_cli.py` to pass unchanged; `test_wizard.py` calls the old names directly at ~9 sites
     (verified, see Pitfall 2).
   - What's unclear: CONTEXT.md does not state a preference between the two remediation paths.
   - Recommendation: default to updating `test_wizard.py`'s call sites to the new owning-module
     names (matches FOUND-06's intent that these functions genuinely move, not just get aliased)
     unless the planner judges the aliasing path meaningfully cheaper; either way, budget an
     explicit task for it.

2. **Is the run_writer/write_ops unification (Pitfall 5) a hard prerequisite for FOUND-05, or can
   the conflict check land in both functions separately as an interim step?**
   - What we know: the roadmap phase goal explicitly frames unification as in-scope ("Closes the
     pre-write protocol half of #71 (one write funnel)"); CONTEXT D-07 describes the conflict
     check as running "in the shared pre-write funnel" as if that funnel already exists as one
     thing.
   - What's unclear: whether the plan should sequence "unify the two functions" as its own task
     before "add the conflict check," or fold both into one task since they touch the same code.
   - Recommendation: sequence unification first (smaller, behavior-preserving refactor, easily
     verified against `test_write_path.py`'s existing shadow-replay test) then add the lock and
     conflict check to the single result — this ordering isolates risk and gives the planner two
     independently verifiable steps instead of one large one.

3. **What exact `expected` key shape does `op_set_field` carry (D-09), and does it apply
   per-book or as a single scalar for the whole op?**
   - What we know: D-09 says "`op_set_field` gains a plan-time `expected` mapping per book,"
     and CONTEXT's discretion list separately flags "the `expected` key name on the op dict" as
     undecided.
   - What's unclear: the exact key name (`expected`, `expect`, `before_expected`?) and whether it
     is `{book_id: expected_value}` mirroring `values`'s own shape, or something else.
   - Recommendation: mirror `values`'s existing shape exactly — `{"op": "set_field", "field": ...,
     "values": {...}, "expected": {book_id: expected_value, ...}}` — since every consumer that
     already reads `values` (coerce, apply_ops) would need near-identical handling for `expected`,
     and symmetry minimizes the chance of the two dicts drifting in key type (str vs int) the way
     `ops.coerce`'s docstring already warns about for `values` alone.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| `uv` | Every test run, CI | ✓ (already used throughout CI) | per `astral-sh/setup-uv@v8.1.0` in ci.yml | — |
| Python 3.10/3.13/3.14 | CI matrix (ubuntu, existing) | ✓ | pinned in ci.yml matrix | — |
| `windows-latest` GitHub Actions runner | XPLAT-03 | Not verified this session (no live CI run triggered) | — | The lane itself is the verification step; STATE.md already documents this as an open blocker |
| Calibre 64-bit MSI installer (`download.calibre-ebook.com`) | Windows `calibre-debug` smoke (D-13) | Not verified this session (no network fetch performed) | 9.11 pinned per D-13 | If flaky, D-13's own fallback: demote only the smoke job, keep core-tests blocking |
| Calibre Linux installer script (`download.calibre-ebook.com/linux-installer.sh`) | Ubuntu `calibre-debug` smoke (D-14, new) | Not verified this session | pinned to same version as Windows (9.11) | None specified — this is new work this phase (today's `ci.yml` has no Calibre install step at all on any OS) |
| `swift` toolchain / `afm` binary | macOS-only manual verification of Pitfall 4's fix | Not checked this session (local machine has no `git status` requirement to check this) | macOS 26+ | Not required for CI (apple engine is absent off-macOS by design, XPLAT-02) |

**Missing dependencies with no fallback:** none — every environment gap above either has a stated
fallback (demote the smoke job) or is itself the acceptance criterion being verified (the CI lane
existing and going green IS success criterion 5).

**Missing dependencies with fallback:** the Windows and Linux Calibre installer steps are new CI
work with no prior art in this repo's `ci.yml` (verified — the current file has no Calibre install
step on any OS) — treat both as first-time integration work, not "add a flag to an existing step."

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | Plain-assert Python, pytest-collectable (no framework dependency) \[VERIFIED: .planning/codebase/TESTING.md:5-11\] |
| Config file | none — CI globs `tests/test_*.py` \[VERIFIED: .github/workflows/ci.yml:31-33 — `for t in tests/test_*.py; do uv run "$t"; done`\] |
| Quick run command | `uv run tests/test_paths.py` (or any single `tests/test_*.py` file) |
| Full suite command | `for t in tests/test_*.py; do uv run "$t"; done` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| FOUND-01 | `load_maps()`/`load_vocab()` return bundled defaults from inside a simulated zip; missing defaults raises `GuardrailError` | unit | `uv run tests/test_layers.py` (extend) or a new `tests/test_defaults_resource.py` | ❌ Wave 0 — no test simulates a zip-packaged install today |
| FOUND-02 | `user_dir()` resolves to `%APPDATA%\scourgify` on a simulated Windows env | unit | `uv run tests/test_paths.py` (extend) | ❌ Wave 0 — `test_paths.py` has no Windows-env test today (verified, read the whole file) |
| FOUND-03 | Two fixture libraries opened in sequence never share `data_dir()`-derived paths; legacy-tree migration moves/doesn't-move correctly | unit | `uv run tests/test_paths.py` (extend) | ❌ Wave 0 |
| FOUND-04 | A second write-run against the same library-uuid lock is refused, naming the running job | unit | `uv run tests/test_write_path.py` (extend) | ❌ Wave 0 — no lock exists in `common.py` today |
| FOUND-05 | An op whose current value diverges from its expected value is skipped, not applied, and named in the result | unit | `uv run tests/test_write_path.py` (extend) or `tests/test_editlog.py` (footer shape) | ❌ Wave 0 |
| FOUND-06 | `classify.scope_options`/`engines.engine_options`/etc. exist and are pure (no `ui` import); `wizard.py` no longer defines them | unit + source-read | `uv run tests/test_wizard.py` (repointed, see Pitfall 2) + existing `tests/test_cli.py` source-grep (unchanged, already passes) | ⚠️ Partial — functions exist today under old names/location; test repointing is the gap |
| FOUND-07 | No job-reachable module calls `subprocess` outside the four named exemptions; `calibre_open()` has a `tasklist` branch on Windows | source-read + unit | `uv run tests/test_plugin_safety.py` (extend, new assertion per CONTEXT discretion) | ❌ Wave 0 — the specific subprocess-scope assertion does not exist yet (current test only checks `SystemExit`, not `subprocess` scope) |
| XPLAT-01 | Core modules import under a simulated Windows path environment | unit | `uv run tests/test_plugin_safety.py` (existing rich-blocked import test already proves import-cleanliness; a Windows-path-specific variant is new) | ⚠️ Partial |
| XPLAT-02 | `usable_engines()` never returns `"apple"` when the `platforms` trait excludes the current OS | unit | `uv run tests/test_engines.py` (extend) | ❌ Wave 0 — no `platforms` trait exists in `TRAITS` today (verified) |
| XPLAT-03 | Windows CI lane green (core tests + calibre-debug smoke); Linux gains the same smoke | manual-only (CI infrastructure) | The CI run itself; `tests/smoke_calibre.py` already exists and is CI-ready, just not wired into any CI job today (verified — it is deliberately named without a `test_` prefix so today's glob skips it) | N/A — this is infrastructure, not a unit test |

### Sampling Rate

- **Per task commit:** the single most relevant `tests/test_*.py` file for the function touched
  (e.g., `uv run tests/test_paths.py` after a `data_dir()` change).
- **Per wave merge:** `for t in tests/test_*.py; do uv run "$t"; done` (the existing full local
  suite — seconds, no Calibre needed).
- **Phase gate:** the full local suite green, PLUS the new `windows-latest` CI lane green
  (this phase's own deliverable — it cannot be sampled locally on a macOS/Linux dev machine).

### Wave 0 Gaps

- [ ] `tests/test_paths.py` — Windows `user_dir()` test (mock `os.environ["APPDATA"]`, no `os.name` branch exists to test against yet), uuid-level `data_dir()` tests, migration tests (D-02/D-03, including the negative "wrong uuid, don't move" case) — covers FOUND-02, FOUND-03
- [ ] `tests/test_write_path.py` — lock-refusal test (two write-runs, same uuid, second raises naming the first), apply-time conflict test (an op whose expected value no longer matches current is skipped and reported) — covers FOUND-04, FOUND-05
- [ ] `tests/test_editlog.py` — footer `skipped: [[book, field], ...]` shape test (D-08) — covers FOUND-05
- [ ] `tests/test_engines.py` — `platforms` trait exists and gates `usable_engines()` (not a name test) — covers XPLAT-02
- [ ] `tests/test_plugin_safety.py` — new subprocess-scope assertion (D "FOUND-07" discretion item) — covers FOUND-07
- [ ] A new or extended defaults-resource test simulating a zip-packaged install (e.g., build a real temp zip via `zipfile`, point the resolver at it, assert `load_maps()`/`load_vocab()` succeed and a missing-file case raises `GuardrailError`) — covers FOUND-01 and Success Criterion 1's classify-cost-parity claim
- [ ] `windows-latest` and Linux `calibre-debug` smoke CI jobs wired to run `tests/smoke_calibre.py` (which already exists and is already written to be CI-safe) — covers XPLAT-03

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | This phase touches no auth surface (API keys are Phase 3 territory, unaffected here) |
| V3 Session Management | No | N/A — no session concept in this desktop-local tool |
| V4 Access Control | Partial | The write-run lock (FOUND-04) is a concurrency-control mechanism, not an access-control one — it prevents two concurrent writers, not unauthorized access. No new access-control surface is introduced |
| V5 Input Validation | Partial | The apply-time conflict check (FOUND-05) is itself a form of input validation against library state drift — reuse `editlog.conflict`, already pure and tested; no new parsing of untrusted external input is introduced this phase |
| V6 Cryptography | No | No cryptographic operation is added or touched this phase |
| V12 File & Resources | Yes | The zip-internal resource extraction (FOUND-01) reads a bundled, trusted zip (the plugin's own archive, not user-supplied), so path-traversal from the zip's own contents is not a realistic threat — but the extraction target (`user_dir()/cache/<core version>/`) should be constructed from a **version string parsed from the package itself**, never from user input, to avoid an incidental path-construction bug |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Two libraries' operational state (proposals, failures, edit log, backups) bleeding into each other, causing a user to see or act on another library's data | Information Disclosure / Tampering | FOUND-03's uuid-keyed `data_dir()`, already the phase's core deliverable; the phase-4/5 bleed already observed once (a 6-book throwaway library reporting a real-library archive filename) is the concrete reproduction case CONTEXT.md names \[VERIFIED: 01-CONTEXT.md:187-188\] |
| A second concurrent write silently racing a live library write, corrupting `metadata.db` | Tampering | FOUND-04's write-run lock; this is a correctness/data-integrity control more than a classic "security" threat, but it is the phase's highest-consequence failure mode if skipped |
| A stale/conflicting write silently overwriting a user's more-recent edit | Tampering | FOUND-05's apply-time conflict check via `editlog.conflict`, skip-and-report rather than silent overwrite |
| Extracting an untrusted zip and following a crafted path out of the intended cache directory (zip-slip) | Tampering | Not a live threat here — the zip being extracted is the plugin's **own** signed/built archive (`build_plugin.py`'s own output), not an externally supplied file; still, the extraction code should join extracted member names with `os.path.join` and verify the resolved path stays under the cache root as defense-in-depth, since this pattern is a well-known CWE (CWE-22) regardless of the current low practical risk |

## Sources

### Primary (HIGH confidence — read directly this session)
- `src/scourgify/common.py` (full file, 644 lines) — `user_dir`, `data_dir`, `write_ops`, `run_writer`, `calibre_open`, `check_wipe`, `library_uuid`, op constructors
- `src/scourgify/editlog.py` (full file, 146 lines) — record shapes, `conflict` predicate, `before_values`
- `src/scourgify/ops.py` (full file, 83 lines) — the one write executor, `create_column` in-process refusal
- `src/scourgify/engines.py` (full file, 280 lines) — `TRAITS`, `PRICING`, `usable_engines`, `Apple`
- `src/scourgify/wizard.py` (lines 170-430) — the six pure option functions D-10 relocates
- `src/scourgify/wrangle.py` (lines 40-114) — `load_maps`, the `GuardrailError` fail-closed behavior
- `src/scourgify/classify.py` (lines 1-80, plus `est_cost` at 102-110) — `load_vocab`, cost estimate mechanism
- `src/scourgify/ops.py`, `src/scourgify/_writer.py` — the CLI/plugin write-path split
- `plugin/action.py`, `build_plugin.py` — plugin dispatch shape, zip layout
- `tests/test_paths.py`, `tests/test_editlog.py`, `tests/test_write_path.py`, `tests/test_plugin_safety.py`, `tests/test_plugin_source.py`, `tests/test_promote.py`, `tests/test_selection.py`, `tests/test_synopsis_queue.py`, `tests/test_wizard.py`, `tests/test_cli.py` (targeted sections) — pinned shapes and the three broken-test cases identified in Pitfall 1
- `.github/workflows/ci.yml` (full file) — confirmed no `windows-latest` lane and no `calibre-debug` smoke job exist today on any OS
- `docs/superpowers/specs/2026-08-06-calibre-plugin-nlspec.md` (lines 37-101, 534-632) — the atomicity contract, Multi-library and Compatibility constraint sections, including the phase-4/5 amendments this phase's success criteria directly reference
- `.planning/phases/01-foundation-a-hostable-core/01-CONTEXT.md`, `01-DISCUSSION-LOG.md`, `.planning/ROADMAP.md`, `.planning/REQUIREMENTS.md`, `.planning/STATE.md`, `.planning/PROJECT.md`, `.planning/codebase/ARCHITECTURE.md`, `.planning/codebase/CONCERNS.md`, `.planning/codebase/TESTING.md` — full reads

### Secondary (MEDIUM confidence — WebSearch, cross-checked against an authoritative source)
- Calibre config directory on Windows (`%APPDATA%\calibre`) — [manual.calibre-ebook.com/customize.html](https://manual.calibre-ebook.com/customize.html), [manual.calibre-ebook.com/faq.html](https://manual.calibre-ebook.com/faq.html)
- `tasklist /FI "IMAGENAME eq <name>"` syntax — [learn.microsoft.com/en-us/windows-server/administration/windows-commands/tasklist](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/tasklist)
- Calibre Windows MSI silent-install syntax (`msiexec /qn`) — [calibre-ebook.com/download_windows](https://calibre-ebook.com/download_windows), [silentinstallhq.com/calibre-silent-install-how-to-guide](https://silentinstallhq.com/calibre-silent-install-how-to-guide/)
- Calibre Linux installer script — [kovidgoyal/calibre/setup/linux-installer.sh](https://github.com/kovidgoyal/calibre/blob/master/setup/linux-installer.sh)

### Tertiary (LOW confidence — community sources, marked for validation in the CI lane itself)
- `C:\Program Files\Calibre2\` as the 64-bit `calibre-debug.exe` install location — general Calibre/Windows community documentation, not an official Calibre source; STATE.md already flags this as the specific item the CI lane must settle

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new dependency; every mechanism (stdlib `zipfile`/`threading`/`sqlite3`/`subprocess`) is already used elsewhere in this exact codebase
- Architecture: HIGH — every integration point was located by reading the actual source this session, not inferred from documentation or memory
- Pitfalls: HIGH for #1/#2/#4/#5 (all verified by reading exact file:line evidence this session); MEDIUM for #3 (Windows CI specifics are CITED, not run against a live runner this session)

**Research date:** 2026-09-06
**Valid until:** 30 days (stable, internal-refactor domain; re-verify the Windows/Calibre CI facts specifically if the phase's CI work stalls, since those are the only externally-sourced claims)
