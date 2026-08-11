# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

scourgify normalizes a [FanFicFare](https://github.com/JimmXinu/FanFicFare)-imported
[Calibre](https://calibre-ebook.com) library — consolidating tags, fandoms, characters, relationships,
genres, and status. It is data-driven (bundled `defaults/` + per-user `overrides/` + `config.toml`),
audit-first, and reversible. Python stdlib + Calibre's own CLI + `rich`; tests in `tests/` (plain asserts,
no framework needed). **rich dependency rules by surface:** `wizard.py`/`ui.py` may hard-import rich (the
wizard is rich-first; `ui.py` raises a friendly install hint if missing). The core tools
(`wrangle`/`classify`/`staleness`) render through **`report.py`** — the ONE owner of the rich-or-plain
policy (`table`/`tree`/`say` + the live classify `Dashboard`); tools describe WHAT to show, report.py
decides HOW, so no call site ever hand-writes a second plain renderer (scripting/CI without rich must
keep working). `_writer.py` and `ops.py` run under `calibre-debug` (Calibre's bundled Python has empty
site-packages) — never import rich (or `ui`/`wizard`/`report`) there. The whole core must keep importing
clean under Calibre's bundled Python 3.14.6 with empty site-packages: that is what makes a Calibre
plugin possible at all (roadmap #62), and it is a hard constraint on every change, not a nice-to-have.

## Running it

Everything keys off `CALIBRE_LIBRARY` (the folder containing `metadata.db`):

```bash
export CALIBRE_LIBRARY="$HOME/Calibre/fanfiction"
uv run scourgify                                     # no args = the interactive wizard (rich required; TTY only)
uv run scourgify setup                               # interactive health check + setup (FanFicFare, columns, config)
uv run scourgify audit                               # read-only dry-run of every pass
uv run scourgify apply --apply                       # write changes (Calibre CLOSED for the write step)
```

(`uv run scourgify` from a checkout; an installed copy — `uv tool install scourgify` — drops the `uv run`.
**uv is the only supported installer** — never `pip`/`pipx`, in docs or in advice to the user.)

**`wizard.py`** (launched by bare `scourgify`) is a **guided lifecycle behind a landing menu**: header
(books, column health, new/changed count via `select.changed`, the **never-classified backlog**
(`select.pick("unclassified")` — usually the largest outstanding work, and the header must never say
"up to date" while it is non-zero), pending proposal, Calibre-open warning) → setup if columns/config are missing → then a **menu** (`landing_menu`) that asks what to do —
the **full maintenance run** (`run_workflow` over `WORKFLOW`) or a **single task** (`TASKS`), with
unfinished work flagged inline from cheap file signals in `snapshot()` (pending proposal, undecided
new-tag candidates, `--step` rejects, backfillable promotions). The menu loops (re-`snapshot` after each
task) until quit. The guided run is the stages in order — **wrangle → staleness → classify → review →
promote → backfill** — each dry-running first, showing its report, and asking before writing (a clean
stage auto-skips). There is no separate audit step — the wrangle stage's dry run IS the audit;
`scourgify audit` stays for the full per-value detail. The wrangle stage drives one
**`wrangle.plan()`** object (preview → guard → optional step → write; never a recompute). The
classify stage opens with a **scope menu** on fixed slots — new/changed (the cheap default),
**never classified** (the backlog; asks how many to do this run and sets `--batch`), **most recent
N books** (a targeted redo; asks N and sets `--last`, which re-sends books already classified),
**whole library** (a full pass), skip — resolves the run ONCE via
**`classify.plan()`** (so the € the user confirms is over the exact `todo` set the run bills, and
the expensive text extraction never runs twice), shows per-engine cost estimates
(`classify.est_cost`, list prices in `classify.PRICING`, per-engine output tokens in
`engines.TRAITS['out_tokens']` — a reasoning model bills hidden thinking as output, so gemini is
~14x its visible answer and costs MORE per book than claude despite a lower per-token price), offers an engine **bake-off**
(`classify.bakeoff`: the same ~5 sample books through every usable engine, display-only), and
enables `--text-fallback` so thin descriptions get sampled rather than dropped. The review stage offers apply / keep / discard (discard archives to
`*_discarded_*.csv`). **EVERY stage that proposes a list offers a 1-by-1 review** (`ui.checklist`, always slot 2) — wrangle (a book's field edits), review (a book's proposed tags), promote (a candidate's verdict), staleness (a book's #status change), backfill (a book gaining tags), overrides (a rule line). All-or-nothing on a reviewed artifact defeats the point of reviewing it; an unticked promote verdict gets no ledger row, so the candidate stays undecided and is offered again. Each has a CLI equivalent — `apply --step`, `classify --apply --step`, `promote --apply/--backfill --step`, `staleness --apply --step`, `overrides --apply --step` — and the wizard DELEGATES to the same function (`promote.apply_decisions_step` / `promote.backfill_step` / `staleness.step` / `overrides.step_pick`), so `ui.checklist` is never driven from wizard.py and the two surfaces cannot diverge. (`ui.checklist`,
CLI `apply --step` / `classify --apply --step`): walk each book's changes, untick to reject
individual items. Rejects land in `data/rejects.csv` (see `docs/superpowers/specs/2026-07-06-…`);
`scourgify overrides` turns the deterministic (wrangle) ones into identity-override lines so the same
change never recurs (dry-run default, `--apply` writes, `--master` targets `defaults/`), while
classify rejects are log-only (an AI hallucination, not a rule bug). **The wizard ASKS; the tool modules DO.** A stage may render menus/prompts and then hand off — it may never drive a review checklist or assemble a write itself, because the CLI is the other front door into the same functions and anything the wizard does privately is invisible to it (and to any guard added later). Where a flow needs a different question, the tool takes an injected decision callback (`promote.backfill(decide=…)`, the same seam as `Plan.run(ask=…)`), so there is ONE flow and the doors differ only in how they ask. `tests/test_cli.py` enforces this by reading wizard.py's source: `ui.checklist`, `run_writer(` and `op_set_field(` may not appear in it. Stages call the same engine functions the subcommands do (previews → confirm → write), so guardrails and auto-backup apply identically; guardrail `SystemExit`s skip the stage, not
the run. `ui.py` holds the shared rich Console + prompt helpers (lintle `term.py` pattern). classify
runs render a live dashboard (`report.Dashboard`: progress, tagged/failed/rate, throughput
sparkline, rising candidates).

**`select.py`** — the one owner of "which books does this run operate on"; classify's scope flags and
the wizard header both go through it, so they can never disagree.
**`--unclassified` is the only scope that ADVANCES** — the one to chunk a backlog with
(`--unclassified --batch N`, apply, repeat). It selects books classify has never *attempted*
(`artifacts.classified_ids()`: applied archives + the pending proposal + **the failure log**) and
could actually send (`select.sendable()`: description ≥ `MIN_DESC`, or any book with a file to
sample under `--text-fallback`). Both filters are what make it finite: an errored book gets no
proposal row on purpose, so without counting failures it re-occupies the head of every batch
forever; and `gather()` drops a thin-text book before it can reach a proposal, so without
`sendable` it could never leave the set. Discarded archives are deliberately NOT counted — the user
threw those results away. `--last N` / `--since` / `--all` do NOT advance (they re-pick the same
set), and `--all` additionally suppresses the resume by marking every book explicit.
`parse_books()` owns the `--books` spec grammar (`1,2,3`, `10-20`, `@ids.txt`, or any
comma-combination; `@file` expands one level deep) and the `ids` pick mode selects exactly those
books — the one way `classify`, `wrangle apply` and `staleness` are pointed at a named set. Those same three
also take **`--last N`** (the N most recently added), which resolves through `select.pick("last")` to ids and
then reuses the `--books` path — so one grammar, one meaning of "N", across every book-scoped command; the two
flags are mutually exclusive. The wizard exposes it too (wrangle apply / staleness / classify scope menus).
A book is new/changed iff unstamped
∨ `#updated` > stamp ∨ added-date (`books.timestamp`) > stamp — the added-date clock catches re-fetches
(FanFicFare bumps it) while staying immune to scourgify's own writes (`last_modified` is deliberately
NOT used). All pickers return newest-added-first.

**Packaging.** The code is a proper installable package under `src/scourgify/` (hatchling; on PyPI as
`scourgify`). The single `scourgify` console command (`cli.py`) dispatches argv to the tools: bare → wizard,
`setup`/`audit`/`apply` → wrangle, `classify`, `staleness`, `rollback` → common. Bundled `defaults/` (and `_writer.py`, `afm.swift`)
ship **inside** the package (read-only at runtime); per-user `config.toml`, `overrides/`, and `data/`
(including `data/backups/`) resolve against **`common.user_dir()`** — `$SCOURGIFY_HOME` if set, else
`$XDG_CONFIG_HOME/scourgify`, else `~/.config/scourgify` (mac + Linux; no Windows) — so an installed copy
has one stable home instead of writing under whatever CWD it's launched from, and `uv run` from the repo
reads that same central location. `common.HERE` is the package dir (use it only for shipped read-only
files); anything user-writable keys off `common.user_dir()` (the single owner — never re-derive it or
reach for `os.getcwd()`). `$SCOURGIFY_HOME` also lets tests point the whole tree at a temp dir.

**The Calibre plugin** lives in **`plugin/`** (roadmap #62; phase 4 = the read-only skeleton, phase 5 = settings) and is built
by **`uv run build_plugin.py`** → `dist/scourgify-plugin-<version>.zip`, whose version is read from
`pyproject.toml` — one source tree, one version for wheel and zip. Layout: the empty
`plugin-import-name-scourgify.txt` **at the zip root** (without it a multi-file plugin can't import its own
submodules), `__init__.py` (`InterfaceActionBase`; `actual_plugin` is a **string**), `action.py`, and the
core copied in as a plain top-level **`scourgify/`** package — FanFicFare's layout. That works because
Calibre's `Plugin.__enter__` appends the plugin zip to `sys.path`, so zipimport resolves
`from scourgify.common import …` **unchanged**; Calibre's bundled Python has an empty site-packages, so an
installed wheel is invisible to it and the core must ship inside the zip. Two consequences: files bundled
beside the code are **not** readable there (`common.HERE` points inside the zip — `load_maps()` refuses
rather than silently loading empty maps; a `DEFAULTS` resource seam is phase-6 work), and the library path
cannot come from the environment — **`common.set_library(path)`** is the one injection seam
(`gui.current_db.library_path`; the injected value wins over `$CALIBRE_LIBRARY`, `os.environ` is never
mutated). **Nothing runs at plugin startup**, every core call — reads included — goes through a
`ThreadedJob`, and every job callback is `Dispatcher`-wrapped. That is enforced, not intended:
`tests/test_plugin_source.py` reads the plugin's source (the mechanism `tests/test_cli.py` uses on
`wizard.py`) and fails on `run_writer(`/`subprocess`/`multiprocessing`/`ThreadPoolExecutor`, on any core
import at module level (they belong inside `job_*` functions), on an unwrapped callback, on work in
`genesis()`/`initialization_complete()`, and on a second `ThreadedJob` call site — there is exactly ONE
(`action._run`, which wraps an optional `done=` callback), so every other plugin module dispatches through
it and cannot forget the `Dispatcher`. **Settings** (`plugin/config.py`, hung off `InterfaceActionBase`
via `is_customizable`/`config_widget`/`save_settings`, the widget imported INSIDE `config_widget()` so Qt
stays out of command-line use) keep API keys in `JSONConfig('plugins/scourgify')`, chmod 0600, with a
plaintext banner. **For KEYS the environment WINS over the stored value** (`engines.resolve_keys`) — the
opposite of the library path's rule, deliberately: a key is user config, the library path is a fact about
the host process. Do not harmonize them. `SCOURGIFY_SMOKE=1 calibre --with-library <throwaway>` runs
`plugin/selftest.py`, which drives the menu at 0/1/N, saves and probes a key, and measures the GUI
thread's longest stall — a test hook, not a user feature.

**Everything runs under normal CPython** — the installed `scourgify` command, `uv run scourgify`, or plain
`python3` with rich installed. The core operating rule is about *reads vs writes*, not which interpreter:
- **Reads** (audit, classify proposal, setup health check) — read-only `sqlite3 ... mode=ro`; fine while Calibre is open.
- **Writes** — the standalone tool computes the change-set, serializes it to JSON, and shells out **once** to
  `calibre-debug -e _writer.py -- ops.json` (Calibre's API is the only fast batch-write path; `calibredb set_metadata`
  is one book per process). `run_writer()` (in **common.py**; imported by wrangle/classify/staleness) does this,
  **automatically snapshots metadata.db to `data/backups/ff_<ts>.db` first** (`backup_db()` — sqlite's **Online
  Backup API**, not a byte copy: a committed transaction can still be sitting in `metadata.db-wal`, and a
  `shutil.copy2` of the db alone silently loses it. Pruned to the last 20; `scourgify rollback [--list]` restores
  one, and the current db is snapshotted before a restore so rollback is reversible), and
  **refuses to run while Calibre is open** (`calibre_open()` detects via pgrep→ps and fails *closed* if neither
  exists — it locks the DB). It also **refuses, before writing, a change-set that would catastrophically empty a
  populated column** (`check_wipe`/`_is_wipe`/`_predict_populated`: >90% of a ≥100-book column) — a coarse last-line
  net that covers *every* writer (classify/promote/staleness/setup), not just wrangle's semantic guards; `--force`
  overrides. The user never types `calibre-debug`. Master rollback = the full "Export all Calibre data" backup.
- **There is ONE ops executor, in `ops.py`** — `apply_ops(api, ops, legacy=…, reopen=…, now=…, out=…)`, handling
  `create_column` / `set_field` / `stamp_now` / `set_pref`. **`_writer.py` is glue only** (resolve the library, read
  the ops JSON, open a `DB`, call `apply_ops`). A second executor would be a second set of coercion rules, and the
  two would eventually disagree about what "set tags on book 6585" means — `ops.coerce` is what makes a JSON
  change-set (book ids arrive as *strings*) and an in-process one (ints) address the same books. `ops.py` imports
  **nothing** from scourgify and nothing from Calibre at import time (`calibre.utils.date` is lazy, inside the one
  branch that needs it): `calibre-debug -e` puts only the *script's own directory* on `sys.path`, so under that
  interpreter there is no importable `scourgify` package.
- **In-process writes go through `common.write_ops(api, ops)`** — the Calibre-plugin path (roadmap: #62). It runs
  the *same* `check_wipe` and `backup_db` as `run_writer`, then the same `apply_ops`; it skips `calibre_open()` by
  design (in-process there is no second writer to detect) and **never calls `run_writer`**, which would shell out to
  a second process against a library the GUI holds open (#53). `create_column` is deliberately **out** of the
  in-process contract — the legacy-DB reopen it needs would desync a live GUI's models, so it raises there; columns
  are created by `scourgify setup` with Calibre closed.
- **Every write is logged, in `editlog.py`** — `data/edits.jsonl`, appended by BOTH funnels and by nothing else.
  One JSONL line per applied `(book, field)` `set_field` op (before + after), bracketed by a run-header (tool,
  scope, library uuid, ts; engine+model for a classify run) and a run-footer (counts, outcome), all sharing a
  **collision-proof run id** (`<ts>-<8 hex>` — a bare timestamp cost an archive in #45). **A missing footer marks
  a partial run; its op lines are still real and still undoable.** Op lines are written *before* the ops apply —
  the only ordering that survives a crash — which is safe because undo is conflict-aware: an op that never landed
  reads as a conflict and is skipped, not clobbered. `editlog.conflict(current, expected, multi)` is THE shared
  predicate (multi-value: sets differ, order-insensitive, raw values, no alias/case folding; single-value: string
  inequality) — apply-time checks and undo must never disagree about what a conflict is. `stamp_now`/`set_pref`/
  `create_column` are logged with `"undo": false` and excluded from replay (a full-library stamp is ONE line with
  a count, not 7,949 lines). **`--force` skips the wipe guard, NOT the log** — a forced run is the one most likely
  to need undo. Capture is on the near side of the `calibre-debug` subprocess, so `tests/test_editlog.py` pins the
  record shape with the subprocess stubbed and no Calibre installed. Retention: unbounded (it is tiny; the backups
  prune, and a log outliving its snapshots is the point).
- **Guards raise `common.GuardrailError`, never `SystemExit`**, anywhere a Calibre job could reach them: Calibre's
  `ThreadedJob` catches only `Exception`, so a `SystemExit` guard would kill the worker thread *silently* — no
  error dialog, no completion, a progress bar that never finishes. This is now repo-wide, not per-guard: **no
  job-reachable module raises `SystemExit`**, and `tests/test_plugin_safety.py` AST-checks it. The only two
  exemptions are `run_writer` and `rollback_cmd` — CLI-only funnels a plugin can never enter. `cli.main()` is the
  ONE place `GuardrailError` becomes a process exit, so CLI messages and exit codes are unchanged;
  `wizard._stage_guard` catches both.
- **`common.py`** is the shared core: lazy `CALIBRE_LIBRARY` resolution (importing any module never exits),
  `ro_connect()`, link-table-aware `read_custom_column()`, `norm`/`ascii_fold`, the minimal TOML `load_config()`,
  and `run_writer()`. Don't re-implement any of these in a tool script.

Verification: `uv run tests/test_core.py` (plain asserts, pytest-compatible, no library/network needed) pins the
pure core — `transform`, trope-chain resolution, `parse_resp`, the TOML reader — and
`uv run tests/test_selection.py` pins the selection semantics against a throwaway sqlite `metadata.db` built
by `tests/fixture_db.py` (covers both custom-column storage shapes). CI runs every `tests/test_*.py`
by glob, on **3.10 / 3.13 / 3.14** — a new test file is in CI by existing. (3.14 is not
future-proofing: Calibre 9.11 bundles Python **3.14.6**, so the plugin's interpreter is *newer* than
this repo's floor, not older.) `uv run tests/test_plugin_safety.py` pins the constraint that makes a
plugin possible — every core module imports with **rich blocked** (Calibre's site-packages is empty),
and `ui`/`wizard` refuse with a catchable `GuardrailError` rather than a `SystemExit` that would take
the host process down. `uv run tests/test_write_path.py` shadow-replays one ops list through both
write shapes; `uv run tests/test_editlog.py` pins the edit-log record shape (and the conflict
predicate) with the `calibre-debug` subprocess stubbed; `uv run tests/test_restore_drill.py` is
the snapshot→corrupt→restore drill.
**`calibre-debug -e tests/smoke_calibre.py`** is the manual pre-release check for what CI cannot
assert — the core actually running under Calibre's own interpreter. Read-only (`ro_connect()`), safe
with Calibre open; set `CALIBRE_LIBRARY` to also exercise the read paths. `uv run tests/test_wizard_flow.py` drives the real
wizard stages in-process with canned answers (`common.scripted_answers`) against a fixture library —
the interaction flows (no-write paths, classify scope-skip spending nothing, a skip-all step review
leaving the proposal byte-identical) are pinned in CI in milliseconds. The same seam drives the wizard
from a shell: `SCOURGIFY_SCRIPT="w,3,n,q" scourgify` answers each prompt in order — a **test hook, not
a user feature** (no `--help` entry). A blank answer (an empty entry, e.g. the trailing one in
`"4,"`) means "press enter" and takes the prompt's default — and the wizard's defaults are apply /
full maintenance run, so a blank is a real "yes" here, not a no-op. An empty or whitespace-only
`SCOURGIFY_SCRIPT` (e.g. an interpolated-but-unset var) is instead parsed as an empty queue, so the
very first prompt raises rather than silently walking those defaults. Menu keys are **digits** on a fixed slot per row — a row that does not apply greys out rather
than vanishing, so a number never comes to mean something else; only the landing menu's `w`/`q`
and `ui.checklist`'s `a`/`s`/`q` (where digits already mean "toggle item N") stay letters.
`ui.menu` returns a row's **symbolic id**, never its key, so no call site dispatches on a
position. A script that runs short or names a key that isn't on offer raises `common.ScriptError`, which is deliberately NOT a
`SystemExit`: `wizard._stage_guard` absorbs those, and swallowing a scripting failure would hand
back a green run that asserted nothing.
`uv run tests/drive_wizard.py` (NOT in CI; a few seconds) stays the pre-release check that a real PTY
works at all — header, landing menu, clean quit. `scourgify audit` remains the
against-your-library check: full new state, before/after counts, and SAFETY lines asserting **no book loses its
last fandom or character** (`apply` aborts if any book would end with an empty `#fandoms`/`#characters` it started
with — a bad `fandoms.csv` alias→"" or an empty `decompose` payload; a blocklisted non-fandom relocated to tags is
preserved and not counted) plus a **tag mass-deletion guardrail** (`apply` aborts if tags would shrink >25%
AND more than a floor — the signature of an over-broad junk rule). The floor **scales with the run**
(`_shrink_floor`: `min(200, max(20, assignments//2))`), because a flat 200 silently disarmed the guard on a
scoped run — `restrict()` narrows the counts, so at ~4 tags/book a 50-book scope holds ~195 assignments and
wiping ALL of them stayed under the floor. Library-wide behaviour is unchanged (the fraction dominates long
before the floor); a small scope is no longer a blind spot. `--force` overrides both.

## Maintenance loop (after new FanFicFare downloads)

**Order matters: deterministic cleanup (wrangle) FIRST, content tagging (classify) second** — raw
junk tags inflate a book's tag count and would hide it from the classifier's sparse-book targeting.

```
FFF fetch → uv run scourgify apply --apply           # 1. junk-drop/canonicalize the new raw tags (idempotent)
          → uv run scourgify staleness --apply       # 2. free; re-derive #status from #updated age
          → uv run scourgify classify --incremental  # 3. cheap; only new/changed books (see select.py)
          → review data/classify_proposal.csv        # 4.
          → uv run scourgify classify --apply        # 5. Calibre closed (writes shell to calibre-debug)
          → scourgify promote                         # adjudicate new-tag candidates → review → promote --apply
          → scourgify promote --backfill              # apply promoted/aliased tags onto the books that proposed them (deterministic, no LLM)
```

(Or the wizard: `uv run scourgify` walks exactly this loop, guided. Targeted redo:
`classify --books …` / `--since DATE`; work through a backlog with `classify --unclassified --batch N`,
which is the one scope that ADVANCES — it selects books classify has never attempted
(`artifacts.classified_ids`: applied archives + the pending proposal + the failure log) and can
actually send (`select.sendable`), so each apply strictly shrinks it.)

**⚠️ "Dry run" does NOT mean "no engine call".** Without `--apply`, `classify` does not write to
the library — but it still SENDS every selected book to the engine. There is no such thing as a
free `classify` smoke test: use **`classify --scope-only`**, which resolves the scope, prints what
would be sent and what each engine would cost, and stops before any engine call. (Learned the hard
way 2026-07-30: `classify --unclassified` was run as a "read-only" check and started grinding
through 7,681 books.) The read-only checks that cost nothing: `scourgify audit`,
`scourgify staleness` (no `--apply`), `scourgify rollback --list`, `classify --scope-only`, and
`SCOURGIFY_SCRIPT="q" scourgify`.

**⚠️ Testing engines:** `--engine apple` is the DEFAULT because it is free and on-device — which
also makes it the safe failure mode when a command is run by accident. But it is **not good enough
for this work yet** (weak tagging, and single-threaded, so a large scope takes hours). When
verifying behaviour, use `--engine openai` (cheapest usable) or `gemini`; keep the scope small
(`--books` / `--batch`) and treat apple only as a free-but-low-quality fallback.

**⚠️ Cost:** a full Gemini `classify --fresh` pass over the library is **tens of euros** — measured
2026-07-30 against a 7,949-book library at list price: **≈$25** for `gemini-2.5-flash`
(~1,020 input + ~1,111 output tokens/book, of which **~1,061 are hidden THINKING tokens** billed as
output — `engines.TRAITS['out_tokens']` carries that per engine, and `est_cost` used to assume a flat
80 and so quoted gemini at a fifth of its real price). `--text-fallback` pushes input higher still.
Never run `--fresh`
casually — use `--incremental` (only changed/new books), `--batch N`, or `--engine apple` (free, on-device).
Confirm with the user before any full cloud run (classify itself gates cloud runs >200 books behind a
confirmation / `--yes`). **Do NOT bulk re-fetch FFF metadata** — it re-pollutes columns not protected by
`custom_cols_newonly`.

## Architecture

**`wrangle.py` — the unified engine.** Subcommands `audit` / `apply` (the `setup` subcommand lives in
**`setup.py`** — the FanFicFare health check + config writer share nothing with normalization). Loads
the data layers (first to last, later wins): **`defaults/ao3/`** (generated master lists — see below)
← `defaults/` (curated generic taste) ← `config.toml` (column map + behavior toggles) ← `overrides/`
(per-user, **gitignored**, same file formats, survives upgrades). `load_maps()` builds the
in-memory maps (fandom and trope chains are flattened, so a curated re-point of a generated master
cascades; dirs are injectable params for tests); `transform()` is the per-book core: fandom
alias→canonical, character folding (global + fandom-scoped), genre split→canon→route, tag junk-drop /
trope-route / redundancy-strip. Strips a redundant tag only when the concept already lives in that
book's structured column (**backfill-before-strip**). Pass `log=` and transform appends its per-value
**decisions** (kind, where, before, after) — the audit's examples read this log, never a re-derivation
of the rules. **`wrangle.plan(cfg, maps) → Plan`** runs the full-library transform ONCE; `preview()` /
`guard()` / `step()` / `write()` all read that one plan (the CLI and the wizard drive the same object).
`Plan.restrict(ids)` narrows the WRITE set (`changes`/`diffs` and the per-book SAFETY counters)
*after* the full compute — `read_library` stays library-wide because `transform()` needs global
context (tagcanon majority spelling, `known_chars`), so scoping the read would change the answer
for the selected books. `apply --books` uses it; `audit` is deliberately library-wide (its report
reads transform's decision log, whose tuples carry no book id).

**The FFF→Calibre column model** (see README "FanFicFare → Calibre columns"): `category`→`#fandoms`,
`characters`→`#characters`, `ships`→`#relationships`, `genre`→`#genres`, `status`→`#status`, real
`series`→builtin Series. Two gotchas the tool exists to fix: `include_in_series:category` stuffing fandoms
into the numbered Series field, and aggressive franchise unification (e.g. all Fate/Nasuverse → `Type-Moon`).

**`classify.py` — content-based tagging** (separate from the deterministic engine; uses an LLM).
The LLM engine adapters + retry + availability live in **`engines.py`** (one seam: `_post_json` is the
HTTP transport tests monkeypatch; `ENGINES`/`ENGINE_ENV`/`PRICING`/`TRAITS`/`usable_engines(env=…)`/
`ask_retry` are the single source the tools and the wizard derive from — traits (`is_free`/
`max_workers`/`trait`) replace name string-tests, so adding an engine is one row, and a capability claim a
UI wants to make becomes a trait first: `role`/`limits`/`refuses` are what the plugin's settings dialog and
engine picker render). **Keys reach an engine by injection, never the environment**: every constructor takes
`env=<mapping>` (defaulting to `os.environ`, so the CLI is unchanged) and `resolve_keys(stored, env=)`
produces that mapping with **env winning over stored**; `key_source()` says which won, `mask`/`unmask` are
the settings field's display and edit rules. Engine failures are classified — `classify_error(exc)` →
refusal/auth/permission/quota/timeout/parse/error off `HTTPError.code` — and `ask_retry` **prefixes the
recorded reason with that class** (read it back with `failure_class()`, never by hand-splitting) so the one
shared `reason` column carries the taxonomy without a new column in the CLI-shared failures CSV; only
`refusal` earns a cross-engine retry, and only quota/timeout/parse/error back off. `redact()` strips keys
where every reason is built — no key may reach a log, artifact, or dialog. The cross-tool CSV formats (proposal /
ranked / review / ledger / failures, the `"; "` delimiter, timestamped archiving) live in
**`artifacts.py`** — never hand-read/write those files elsewhere. The user's overrides dir (config
`[overrides] dir`) and its file formats (headers, delimiter sniffing, append-if-absent, the vocab
`-term` removal) live in **`overrides.py`** (`overrides_dir`/`ov_path`/`append_lines`/`append_rows`/
`merge_vocab`/`read_aliases`) — promote's folds and the rejects→overrides flow write through it,
wrangle/classify/setup read through it; never re-derive the dir or hand-read those files. Book-text
sampling for `--text-fallback` lives in **`booktext.py`** (`paths(con)` + `extract(path)`: EPUB-as-zip
with a zip-bomb guard, else `ebook-convert` with a timeout — testable against a fixture EPUB). Two outputs
per book: `added_tags` (chosen from the controlled vocab — hand-curated `defaults/classify_vocab.txt` ∪ the
frequency-gated AO3 seed `defaults/classify_vocab_ao3.txt` (the ~120 highest-use AO3 freeform tropes,
generated by `build_classify_seed.py` from `data/ao3_vocab.csv` so the classifier isn't under-tagging out of
the box — regenerable, never hand-edit it) merged with the user's `overrides/classify_vocab.txt` via
`load_vocab()`, lazily + dedup-on-merge, so installed copies stay overridable → applied) and `proposed_new`
(novel candidates → aggregated to `classify_newtags_ranked.csv` for review→promotion, so the vocab grows
without freeform noise). Engines `--engine apple|claude|openai|gemini|mistral` (keys via env:
`ANTHROPIC_/OPENAI_/GEMINI_/MISTRAL_API_KEY`; `--bakeoff` compares them on sample books); `apple` = on-device, free, single-threaded. Concurrency
via `ThreadPoolExecutor` (`--workers`), retry/backoff, incremental save + resume. `--text-fallback` samples
the book's own prose (EPUB via zipfile, other formats via `ebook-convert`) when the description (Calibre's
built-in `comments` table) is too thin. Scope flags (`--all` / `--incremental` / `--last N` / `--since DATE`) go through
`select.pick` and select ONLY their books (`--all` = the whole library, every book regardless of tag count — the
wizard's whole-library option sets it); the sparse-book default (`< --min-tags`) applies only with no scope
flag. `--apply` auto-creates the **`#wrangled`** datetime marker and stamps **every processed book** (a no-tag
book left unstamped would be re-sent to the LLM forever) — state lives in the library, no external file.
Proposals/outputs live in `data/` (gitignored); `--apply` archives the proposal to
`classify_proposal_applied_<ts>.csv` so stale rows never re-add hand-removed tags. `est_cost`/`PRICING` hold
the public list prices behind the wizard's per-engine estimates; `bakeoff()` is the sample comparison. **`promote.py`** reuses classify's engines/`ask_retry`/`existing_terms` to adjudicate `proposed_new` (advocate→skeptic, `--verify-with` for cross-model, human review is the referee), writes `data/promote_review.csv`, and `--apply` folds into `overrides/` + feeds `parse_resp`'s alias snap.
`--backfill` closes the loop deterministically (no LLM): `proposed_new` candidates are never written to
books, so a promotion/alias otherwise leaves the source books un-tagged (and stamped, so `--incremental`
skips them). `backfill_plan()` reads the book↔`proposed_new` record from the (archived) proposals + the
`promote_ledger.csv` verdicts (`resolve_ledger`/`backfill_wanted` are pure, tested) and applies each
promoted tag / alias target onto exactly the books that proposed it (union, previewed, Calibre closed).

**`staleness.py`** — re-derives `#status` for the activity family {In-Progress, Hiatus, Abandoned} from
`#updated` age (`<2y`→In-Progress, `2–5y`→Hiatus, `≥5y`→Abandoned); idempotent + self-correcting on re-run.
Completed/Dropped/Rewritten and date-less books are never touched.

**`defaults/ao3/` — the generated master taxonomy** (universes/tags/characters/genres as `master,name,rel`
pair rows; ~150k rows, ~7MB, ships in the wheel). Built by **`build_ao3_layer.py`** from the OTW
["Selective data dump for fan statisticians"](https://archiveofourown.org/admin_posts/18804) (2021-02-26):
mechanical extraction of canonical+merger pairs, then an LLM batch workflow clusters fandoms
one-universe-per-franchise (Haiku bulk → Sonnet adversarial verify → Opus referee;
`--assemble <result.json>` combines the verdict-gated decisions into `universes.csv`). NEVER hand-edit
these files — regeneration overwrites them; hand decisions go in curated `defaults/` (re-points cascade)
or `defaults/ao3_exceptions.txt` (pairs excluded from generation, with reasons — e.g. AO3 warning-shadow
mergers that don't transfer to a Calibre library, since Calibre has no warnings field). Policy: **adapt
AO3 everywhere except franchise unification** — curated/override rows that merely fight AO3 spellings
get pruned, not kept.

**`build_defaults.py`** — maintainer tool: regenerates `defaults/` from the source library's gitignored
review-map CSVs (in `data/`). Curated cross-library knowledge (e.g. franchise unification) lives in its `CURATED_FAN`.

## Gotchas worth knowing before editing

- **Column creation needs the legacy DB object**, then a reopen: `DB(LIB).create_custom_column(...)` →
  re-instantiate `DB(LIB).new_api` before the new column is usable in the same process. `Cache` has no
  `all_field_keys` — use `api.field_metadata.all_field_keys()`.
- **Single-value columns may use a link table.** Read a custom column by detecting
  `books_custom_column_{id}_link`; fall back to the `book` column in `custom_column_{id}` if absent.
- **`tropes.csv` is parsed leniently** (`read_tropes` + `resolve_trope_chains` in `wrangle.py`):
  delimiter-sniffed (`,` or `;`), positional columns, unknown route → `tag` (so freeform notes don't crash),
  and variant→canonical chains/cycles are resolved to a terminal at load. Hand-editing it is expected.
- **Gemini hard-blocks a material share of mature content** as `PROHIBITED_CONTENT` — measured at
  **7 of 50 books (14%)** on a random sample of the source library (2026-07-26), not the ~1% this
  file used to claim. Budget for routing roughly one book in seven to a second engine on a full
  run. (Non-configurable; `safetySettings`
  only relaxes the 4 HARM categories). It's deterministic — recover those books with `--engine openai` or
  `--engine apple`. `classify.py` logs failures to `classify_failures.csv`.
- **No `tomllib`** under `calibre-debug`'s Python — `common.py` ships a minimal TOML reader (quote-aware so
  values can contain `#`; tolerates trailing comments on section headers).

## Repo conventions

- **Personal library data is gitignored and lives in `data/`** (review maps, proposals, cluster
  intermediates); `.gitignore` also ignores stray `*.csv` **except** `!src/scourgify/defaults/*.csv`, plus
  `*.db`, `overrides/`, the compiled `afm` binary (`/afm` and `src/scourgify/afm`), and build artifacts
  (`/dist/`, `*.egg-info/`). Only the generic `defaults/` ship (bundled inside the package).
- **`attic/`** holds the original single-purpose pipeline (`apply_*.py`, `generate_*.py`, `dryrun.py`,
  `recover_xianxia.py`), kept as provenance — see `attic/README.md`. `scourgify` supersedes it; the attic
  scripts read CSVs from their own directory and predate the auto-backup, so prefer the live tools.
- `src/scourgify/afm.swift` is the Apple Foundation Models bridge for `scourgify classify --engine apple`;
  it ships in the package (a `swift` toolchain runs it as-is), or build the faster binary with
  `swiftc -O src/scourgify/afm.swift -o src/scourgify/afm` (requires macOS 26+ / Apple Intelligence).
- **Publishing is automated** via `.github/workflows/publish.yml` (PyPI/TestPyPI **Trusted Publishing** —
  OIDC, no stored tokens): a push to `main` touching `src/**`/`pyproject.toml` auto-publishes to **TestPyPI**;
  production **PyPI** is a manual `gh workflow run publish.yml -f target=pypi`. Local dry-run before a layout
  change: `uv build` then `unzip -l dist/*.whl` — the wheel must contain `scourgify/defaults/*`, `_writer.py`,
  `afm.swift` and **not** `data/`, `overrides/`, or the `afm` binary. Full release flow: **Branching & releases** below.

## Branching & releases

Git-flow-lite (mirrors the sibling `lintle` repo):
- **`develop`** — the integration branch and your everyday working branch. All work (features, fixes, docs, vocab)
  lands here via PR; CI (`ci.yml` — tests on Python 3.10 + 3.13) runs on every push/PR to `develop` or `main`.
  **`develop` history stays LINEAR** — land feature PRs with `gh pr merge --rebase` (or `git merge --ff-only`),
  never a merge commit: history should read as if the commits were made on `develop` directly. Merge commits
  are reserved for Release PRs into `main` (below), where the 2nd-parent arc is the point.
- **`main`** — release-only and **branch-protected**: PRs required (0 approvals, so you self-merge), both CI
  checks must pass, no force-push/deletion, **enforced for admins** — i.e. *no direct pushes, even for the owner*.
  Its **first-parent history is exactly one `Release vX.Y.Z` commit per release**; each is a `--no-ff` merge of
  `develop`, so the merge's 2nd parent arcs back to the exact develop commit it was cut from (visible in any git
  GUI). `git log --first-parent main` is the clean release ledger; full `git log main` still reaches every commit
  via those 2nd parents — nothing is lost.
- **GitHub default branch is `main`** (so the repo homepage shows the released state). GitHub therefore defaults a
  new PR's base to `main` — **open feature PRs against `develop`**; only release PRs target `main`.

**Cut a release** (all from `develop`; `main` is only ever reached through a PR merge):
1. Bump `__version__` in `src/scourgify/__init__.py` — versions are immutable on PyPI, always bump. Commit + push `develop`.
2. `gh pr create --base main --head develop --title "Release vX.Y.Z"`; let CI pass, then
   `gh pr merge --merge --subject "Release vX.Y.Z"` (a **merge commit** — not squash/rebase; the 2nd-parent arc is
   the point). The merge lands on `main` → `publish.yml` auto-publishes to **TestPyPI**.
3. Tag it: `git fetch origin main && git tag -a vX.Y.Z origin/main -m "scourgify X.Y.Z" && git push origin vX.Y.Z`
   (annotated, on the Release commit; tags aren't branch-protected).
4. Promote to **PyPI**: `gh workflow run publish.yml -f target=pypi`, then
   `gh release create vX.Y.Z --title "scourgify X.Y.Z" --notes …`.

`main` was migrated to this shape once via a `git commit-tree` snapshot (tree = the released 1.0.0; parents =
[repo root, develop tip]); `develop` kept the full granular history. To temporarily bypass protection for an
emergency fix, edit the rule at *Settings → Branches* (or `gh api -X DELETE …/branches/main/protection`).

## Life cockpit

Tracked in the life-cockpit vault under `#personal` (tracker: `elfensky/scourgify`). The cockpit is
the control plane (what to work on); this repo is where the work happens. Report progress by
opening/closing issues and PRs as usual — the cockpit pulls from the tracker on its next `/sync`.
Nothing to update in the vault; don't mirror cockpit state (milestones, due dates) here.
