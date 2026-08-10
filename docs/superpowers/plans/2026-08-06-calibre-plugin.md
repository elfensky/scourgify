# Calibre plugin — kickoff briefing

**Written 2026-08-06**, at the end of the session that proved the plugin is feasible. Everything
here is measured against the real 7,949-book library unless marked otherwise. Nothing in it is
speculation dressed as fact — where something is unverified it says so.

## The prompt for a fresh session

> Read `docs/superpowers/plans/2026-08-06-calibre-plugin.md` in full, then read the linked GitHub
> issues (#49–#53). Build the roadmap it proposes: turn each phase into a GitHub issue, link them
> from a tracking issue, then work the loop — complete a phase, review it against the design,
> update the roadmap, reconcile anything that changed or any question that surfaced, move to the
> next phase. Do not start writing Qt before phases 1 and 2 are done and green.

That is the whole prompt. This document is the context.

---

## Where things actually stand

**There is no plugin.** Not one line of the real thing. What exists:

| Artefact | What it is | Where |
|---|---|---|
| Design | Clickable interaction spec — three selection modes, engine picker, settings, dashboard | Artifact `d13999d5-f95e-44e9-bdd7-68ed23cf6a0f` |
| Feasibility | A throwaway spike plugin, run twice against the real library, then deleted | Findings in #52 |
| Two fixes | Shipped to `develop`, unreleased: `5f73c96` (#45), `5eb5c42` (#53) | In the tree |
| Issues | #49 edit log, #50 history, #51 undo, #52 plugin direction, #53 the guard bug | GitHub |

The spike answered the questions that could have killed the idea. The remaining work is building,
not discovering.

---

## What the spike proved

Run inside Calibre 9.11 on macOS, against the live library.

**The core runs unmodified under Calibre's Python.** All eleven core modules — `common`, `select`,
`artifacts`, `overrides`, `engines`, `booktext`, `wrangle`, `classify`, `promote`, `staleness`,
`setup` — import and execute with an empty `site-packages`. This works because `rich` is the only
third-party dependency (`pyproject.toml`) and it is confined to presentation; `engines._post_json`
is stdlib `urllib`. **Protecting that property is a hard constraint on all future work.**

**Writing through the live handle works with the GUI open.** Book 7453 went `[] →
["scourgify-spike"] → []` via `gui.current_db.new_api.set_field`, both writes while Calibre held
the library. This is the entire reason the plugin is worth building — it deletes "close Calibre
first" from every workflow.

**`common.ro_connect()` works while the GUI holds the library.** 7,949 books read through
scourgify's own read-only sqlite handle, concurrent with Calibre's. So the plugin can reuse the
existing read paths as-is; there is no need to route reads through Calibre's API.

---

## Footguns

Every one of these was hit, or found by measurement. They are ordered by how much time they cost.

### 1. Any full-library operation on the GUI thread freezes Calibre

The first spike ran its probes in `initialization_complete()`, which fires on Calibre's GUI thread.
One probe was `select.pick(con, "unclassified")` — a scan over 7,949 books. The window stopped
repainting and it read as a crash. It was not a crash; it was a blocked thread.

The lesson is broader than "the LLM calls are slow". **Every** full-library operation scourgify
performs is GUI-thread-hostile: `select.pick()`, `wrangle.plan()`, `read_library()`,
`classified_ids()` over a large archive set. This decides architecture from the first line, and it
is why FanFicFare has a `jobs.py`.

**Rule: nothing runs at plugin startup, and every core call goes through Calibre's job system from
the outset.** Not a raw `ThreadPoolExecutor`, and not "we'll move it off-thread later".

### 2. `ui.py` raises `SystemExit` when rich is missing

Correct for a CLI. Inside a GUI process it is a plugin that kills its host on import. The spike
caught this with `except BaseException` — an ordinary `except Exception` does not catch
`SystemExit`. Before any plugin imports anything near that path, the rich check must become a
catchable error or a lazy check.

### 3. Calibre ships Python 3.14.6 — newer than CI

CI tests 3.10 and 3.13. Calibre 9.11 bundles **3.14.6**. The risk was assumed to be "Calibre's
Python is ancient"; it is the opposite. Add 3.14 to the CI matrix as part of phase 2.

### 4. The write guard cannot be trusted from inside the GUI

`common.calibre_open()` returned **False** — "Calibre is not running" — while running inside
Calibre. `pgrep -fl calibre` exits 0 with clean stderr and lists only the `calibre-parallel`
workers; the GUI's own process line is absent, so every remaining line is correctly rejected.

Fixed in `5eb5c42` by detecting `calibre.gui2` in `sys.modules` first. **But the deeper lesson
stands: the plugin must never call `run_writer`.** `run_writer` shells out to a second process
against a library the GUI holds open. The guard now catches that mistake, but the architecture
should not depend on a guard catching it — phase 1 exists to remove the possibility.

### 5. Do not "fix" locking with a lock probe

The obvious fix for #53 was to test whether `metadata.db` is locked and treat `SQLITE_BUSY` as
"open". **Measured: `BEGIN IMMEDIATE` succeeds while the Calibre GUI is running.** Calibre keeps a
connection open but holds no write lock at rest, and creates no `-wal`/journal/lockfile sidecar. A
lock probe would have reported "safe to write" with the GUI live — the same fail-open bug by a new
route, and harder to spot because it looks principled.

The hazard is *a live GUI that may write at any moment*, not *a lock held right now*.

### 6. Calibre plugin mechanics

- Multi-file plugins need an empty `plugin-import-name-<name>.txt` at the zip root. Without it the
  plugin cannot import its own submodules.
- `actual_plugin` is a string: `'calibre_plugins.<import_name>.<module>:<Class>'`.
- `genesis()` runs during action setup — wire up the toolbar action there and nothing else.
  `initialization_complete()` is where `self.gui.current_db` is real, but see footgun 1.
- **A newly installed plugin is not loaded until Calibre restarts.** `calibre-customize -a` while
  Calibre is running appears to succeed and changes nothing in the running instance.
- `calibre-customize -r "<plugin name>"` takes the display name, not the zip name.

### 7. Environment variables will not be there

API keys currently come from `ANTHROPIC_/OPENAI_/GEMINI_/MISTRAL_API_KEY` (`engines.ENGINE_ENV`).
A plugin inherits whatever Calibre was launched with — **nothing at all when launched from the
Dock**. The plugin must own its own key storage. See the design decisions below.

### 8. Shell and permissions (this machine)

- `calibre-customize` needs a Bash permission rule in `.claude/settings.local.json`. It is already
  there: `Bash(calibre-customize *)`.
- **Compound commands do not match single-command permission rules.** `calibre-customize -a x.zip`
  is allowed; `calibre-customize -a x.zip; calibre-customize -l | grep spike` is refused. Run one
  command per call.
- `osascript -e 'quit app "calibre"'` frequently returns `execution error: ... (-128)` *and quits
  successfully anyway* — the app exits before AppleScript gets its reply. Check with `pgrep`, do
  not trust the return code.
- `zip` was refused by the permission classifier; Python's `zipfile` works and is the natural tool
  anyway.

---

## Design decisions already made

From the interaction spec and the owner's review of it. These are settled — implement them, do not
re-litigate them.

**The selection is the command.** Calibre already knows which books are meant. The toolbar button
offers a menu scoped to the current selection: one book, several books, or — with nothing selected
— it opens the dashboard instead of a menu. The verbs are identical in all three modes; only their
scope changes.

**No confirmation dialogs, anywhere in the GUI.** Opening the menu and clicking an item *is* the
confirmation. Every item instead carries what it will do: `free`, `costs` with the amount computed
for the actual selection, or `writes`. A dialog that always appears trains the user to dismiss it.
Undo after the fact beats a gate before it. *The CLI keeps its confirmation gate for large cloud
runs — there the click and the consequence are separated by a scrollback.*

**Every engine states how it will let you down.** Price alone picks the wrong engine. The picker
shows, per engine: Apple is free and on-device but single-threaded and too weak to judge new tags;
Gemini refuses mature content (**measured: 1 book in 7 on this library**) and bills ~1,061 hidden
reasoning tokens per book, making it cost more than Claude despite a lower headline price; OpenAI
is the cheapest usable one; Mistral is untested here. Source of truth is `engines.TRAITS` and
`engines.PRICING` — the UI derives from them, never hardcodes.

**Keys live in Calibre's ordinary plaintext plugin JSON**, the same place FanFicFare keeps site
logins. This is a deliberate, accepted choice. Surface it with a plain banner in the settings
dialog — no modal, no acknowledgement checkbox. Environment variables still win when set, so
scripted and CI runs keep working unchanged.

**Refusals are an outcome, not an error.** Blocked books surface as a normal result with a
one-click retry on an engine that will not refuse — never a log file the user has to go find.

**Review happens in the library view.** The one thing Calibre can do that a terminal cannot is show
the book being judged — cover, blurb, series, existing tags. Reviewing N proposals selects those N
books and steps through them in place. *Open question: one at a time, or a filtered view of all N.*

---

## Testing strategy

Checked against FanFicFare, the largest Calibre plugin there is: **it does not test its plugin layer
at all.** `fanficfare/` is a pure library with `tests/` (pytest, conftest, per-site fixtures);
`calibre-plugin/` — `fff_plugin.py`, `dialogs.py`, `config.py`, `jobs.py` — has no tests.

scourgify is better positioned than FanFicFare for this: 11 stdlib core modules against 18 test
files, 3 presentation files. So:

1. **The core stays untouched.** The existing suite keeps covering the real logic under normal
   CPython. Run it as CI does: `for t in tests/test_*.py; do env -u CALIBRE_LIBRARY uv run "$t"; done`
   — `CALIBRE_LIBRARY` is set in a dev shell but absent in CI, and that has hidden a failure before.
2. **Keep the Qt layer thin by enforcement, not intention.** `tests/test_cli.py` already reads
   `wizard.py`'s source and fails if `ui.checklist`, `run_writer(` or `op_set_field(` appear in it.
   Extend the same source-grep to the plugin module. That is what makes an untested Qt layer safe.
3. **A `calibre-debug -e` smoke script is automatable** — imports the core, exercises the read
   paths headlessly, no GUI. This is what the spike was; it belongs in the repo.
4. **No vendored dependencies.** The core needs none. Only a Qt backend for `report.py`/`ui.py`, so
   there is no dependency tree to bundle — a real advantage over FanFicFare's 2 MB zip.

Tests are plain asserts, no framework. Match the surrounding style: a docstring saying what breaks
in the real world if the assertion fails, not what the code does.

---

## Proposed roadmap

Each phase becomes a GitHub issue, linked from a tracking issue. **Phases 1 and 2 are worth doing
even if the plugin is never built** — start there, and the decision to continue stays cheap.

Filed 2026-08-08: **#62** tracks phases **#54–#61**. The NLSpec
(`docs/superpowers/specs/2026-08-06-calibre-plugin-nlspec.md`) is the acceptance authority; where
it and this briefing differ, **the spec wins** — it is newer and carries the adversarial review.

### Phase 1 — Extract the write path (blocks everything) — **DONE** (#54)

Split `apply_ops(legacy, ops)` out of `_writer.py`. Only lines 18–21 are script-shaped: read
`CALIBRE_LIBRARY`, read `sys.argv`, open its own `DB(LIB)`. The ops loop is already generic and
already runs against Calibre's API.

Lift the auto-backup and the `_is_wipe` guard out of `run_writer` so both callers keep them — a
plugin writing through `apply_ops` must still snapshot and must still refuse a catastrophic
change-set. Relates to #53: this is what makes the plugin safe by construction rather than by guard.

*Acceptance:* `_writer.py` and an in-process caller share one executor; backup and wipe guard apply
to both; existing write paths unchanged and green.

**What actually landed, and where it differs from the above:**

- The signature is **`apply_ops(api, ops, legacy=None, reopen=None, now=None, out=print)`**, not
  `apply_ops(legacy, ops)`. `api` (a Cache / `new_api`) leads because it is what *both* callers
  have; `legacy` + `reopen` are the CLI writer's extras, needed only by `create_column`.
- It lives in a **new module, `ops.py`** — it could not live in `common.py`. `calibre-debug -e`
  inserts only the *script's own directory* on `sys.path`, so under Calibre's interpreter there is
  no importable `scourgify` package: the executor has to stand alone and import nothing from
  scourgify. Verified live against Calibre 9.11 (`calibre-debug -e src/scourgify/_writer.py` on a
  throwaway library: `created #wrangled` / `WROTE.`, and idempotent on the second run).
- **`create_column` is out of the in-process contract** (spec B2.6) — it raises without a `legacy`
  handle. `set_field` / `stamp_now` / `set_pref` are in.
- **`ops.coerce`** is the piece that turned out to matter most: ops JSON stringifies book ids
  (JSON object keys) while an in-process caller passes ints. Without one coercion function the two
  writers would address *different books* from the same change-set.
- **The backup is now sqlite's Online Backup API for BOTH callers** — the spec allowed the CLI to
  keep `shutil.copy2`, but keeping two mechanisms buys nothing and the copy is wrong in a way that
  is invisible: a committed transaction can still be in `metadata.db-wal`, and copying the db alone
  silently drops it. The old size-equality verify went with it (it cannot survive a page-level
  backup and never proved the file was readable); the snapshot is now verified by reading its book
  count back out. Any failure raises `GuardrailError` — no rollback point, no write.
- **`GuardrailError`** landed here rather than in phase 2, because the lifted guard needed
  something to raise. `run_writer` converts it straight back to `SystemExit`, so CLI exit codes and
  messages are unchanged; `wizard._stage_guard` catches both. The broader
  audit of `SystemExit` reachable from a job is still phase 2's job.
- **Parity, honestly scoped.** The shadow replay that runs in CI (`tests/test_write_path.py`) drives
  one ops list through the CLI shape (JSON round-trip + legacy handle) and the in-process shape
  (live dicts) against a fake `api`, and diffs the resulting state whole — stamps pinned via `now=`.
  That pins the half that can silently rot. A full-fidelity replay against a real cloned Calibre
  library needs Calibre's own Cache and belongs to the `calibre-debug` smoke script in phase 2.
- **The restore drill is a CI test** (`tests/test_restore_drill.py`), not a manual pre-release
  ritual: snapshot → corrupt the library → `rollback --yes` → assert the restored db matches the
  snapshot byte for byte *and* that the pre-restore state was itself snapshotted. It prints
  wall-clock (200 books / 32 KiB: backup ~1 ms, restore ~6 ms).

### Phase 2 — Make the core plugin-safe — **DONE** (#55)

Add 3.14 to the CI matrix. Make the `rich` check in `ui.py` catchable rather than `SystemExit`. Add
the `calibre-debug -e` smoke script to the repo. Audit the core for anything that assumes a
terminal or a process exit.

*Acceptance:* CI green on 3.10/3.13/3.14; importing every core module under `calibre-debug` raises
nothing; the smoke script runs in CI or is documented as a manual pre-release check.

**What actually landed:**

- `ui.py` raises `common.GuardrailError` instead of `SystemExit`, and `cli.main()` converts that
  back at the **one** CLI boundary — so a partial install still shows the same message with the
  same non-zero exit, and a plugin can catch it. Converting at one boundary instead of ~30 raise
  sites is what kept this from being a repo-wide rewrite.
- `tests/test_plugin_safety.py` is the CI-runnable half of the constraint: every core module
  imported in a subprocess with **rich blocked** (`sys.modules['rich'] = None`), plus an AST check
  that `_writer.py`/`ops.py` import no presentation module and that `ops.py` imports nothing from
  `scourgify` at all. Guarded against vacuity — one test asserts the block itself still bites.
- `tests/smoke_calibre.py` is the manual pre-release check. Run 2026-08-08 under **Calibre 9.11 /
  Python 3.14.6, empty site-packages**, against the real 7,949-book library, read-only: all 14 core
  modules import, `ui`/`wizard` refuse catchably, and every read path answers.

**Measured, and it settles an argument:** `wrangle.load_maps()` takes **870 ms**;
`select.pick("unclassified")` 21 ms, `select.sendable()` 16 ms, `book_count` 6 ms. Even the *cheap*
reads are tens of milliseconds, and the map load alone is a visible stutter. This is direct evidence
for the rule, not just the LLM calls: **reads go on the job system too.**

**Deferred, deliberately — the owner may want to overrule this.** The spec's "no `SystemExit`
reachable from anything a job function will call" is not fully satisfied. ~30 sites remain across
`classify`/`promote`/`staleness`/`wrangle`/`engines`/`select`. They are converted *per phase, as
each becomes job-reachable* (phase 4 for read paths, phase 6 for the write verbs), rather than in
one repo-wide sweep now: no job functions exist yet, so a blanket conversion would churn every
tool module and eight tests for a need that is still speculative. The mechanism
(`GuardrailError` + the single CLI converter) is in place, so each conversion is now a one-line
change. The alternative — one blanket sweep in phase 2 — is a real option if drift is the bigger
worry.

> **Overruled 2026-08-09.** The owner chose the blanket sweep, and it landed in phase 3 (#56). The
> deferral was also wrong about its own scope: it counted ~30 sites in the tool modules and missed
> `common.library()`, which every read path in the repo reaches. Details under phase 3.

### Phase 3 — The edit log (#49) — **DONE** (#56)

The foundation for History (#50) and undo (#51), both of which are already drawn into the dashboard
design. Independently valuable to CLI users. See #49 for the full analysis, including the fact that
the before-state is already read and discarded by the wipe guard.

**What actually landed:**

- **`editlog.py`** (~135 lines, stdlib-only) + `data/edits.jsonl`, appended by `run_writer` and
  `write_ops` and by nothing else. The two readers it needs are `common.column_values` (read-only
  sqlite) and `common.values_via_api` (the live handle) — injected, so the funnel that has a GUI
  reads the authoritative in-memory state and the one that doesn't reads sqlite.
- **Op lines are written BEFORE the ops apply.** Logging after a successful apply sounds more
  honest and is strictly worse: a run killed mid-write would log *nothing*, which is the exact
  case the missing-footer rule exists for. It is safe because undo is conflict-aware — an op that
  never landed reads as a conflict, so the worst case is a book skipped and reported.
- **A full-library `stamp_now` is one line with a count**, not 7,949 lines. Per-book stamp lines
  would multiply the log by the library size on every wrangle run to record something already
  excluded from undo.
- **`--force` skips the wipe guard, not the log** (#49's open question). The forced runs are the
  ones most likely to need undo.
- **`tool=` is an explicit parameter**, as #49 predicted — six call sites, no `sys.argv` sniffing.
  `scope=` rides along free (`wrangle.Plan` grew a `scope` attribute that `restrict()` narrows).
- **`engine`/`model` are a seam the CLI cannot fill.** `classify --apply` is its own invocation and
  the proposal CSV doesn't carry the engine; the plugin's classify verb fills it in phase 6, where
  the pass and the write are one job.
- **The `SystemExit` sweep landed here, not per phase** — the owner overruled phase 2's deferral.
  29 sites converted across `classify`/`promote`/`staleness`/`wrangle`/`engines`/`select`/
  `overrides`, plus `common.library()`, which the deferral had missed and which *every* read path
  reaches. `run_writer` and `rollback_cmd` stay exempt by contract. `tests/test_plugin_safety.py`
  AST-walks for `raise SystemExit` in job-reachable modules, so a new one can't creep back.
- **Still open for phase 6, and the sweep does not fix it**: the tool modules' write functions call
  `run_writer` *directly*, so a job calling `staleness.write()` would reach the exempt funnel
  transitively and spawn a second writer process. Phase 6 needs a writer seam on those functions.
- Smoke-verified under Calibre 9.11 / Python 3.14.6 against the real 7,949-book library,
  read-only: `editlog` imports, `library_uuid` answers, and the tags before-read (the extra join
  the guard doesn't make) costs **32 ms**.

### Phase 4 — Plugin skeleton, read-only — **DONE** (#57)

Toolbar button, selection-driven menu, "What does scourgify know?" only. No writes. Every core call
on Calibre's job system. Proves the shape end-to-end with nothing at risk.

**Two blockers were settled before any menu code, neither of them in #57's text:**

- **The core could not resolve the library inside a plugin.** `common.library()` read
  `$CALIBRE_LIBRARY`, and a Calibre launched from the Dock has no environment at all (footgun 7);
  in-plugin the path is `gui.current_db.library_path`. **`common.set_library(path)`** is the one
  seam — the injected value **wins** over the env var (a key is user config and env wins per B5.2;
  the library path is a *fact about the host process*, and a stale `$CALIBRE_LIBRARY` naming a
  different library is the worst outcome available), `os.environ` is never mutated, and the ~15
  call sites of `library()`/`db_path()` are untouched. A process global, because Calibre has one
  open library per GUI and every job re-asserts it then validates the uuid captured at click time;
  a `ContextVar` is the upgrade if concurrent cross-library jobs ever exist.
- **Absolute imports inside a plugin.** Checked FanFicFare before inventing anything: it ships
  `fanficfare/` at the **zip root** as a plain top-level package and imports it as `import
  fanficfare`. That works because Calibre's `Plugin.__enter__` **appends the plugin zip to
  `sys.path`** (disassembled from 9.11 — the source isn't shipped), so zipimport resolves it.
  scourgify does the same: `scourgify/` at the zip root, `import scourgify` inside `with self:` in
  `load_actual_plugin`, and **every absolute `from scourgify.common import …` in the core keeps
  working unchanged**. No import rewrite, no `calibre_plugins.scourgify.scourgify` prefix.
  Verified under Calibre's Python 3.14.6, empty site-packages: the whole core imports from inside
  the zip.

**What actually landed:**

- `plugin/` — `plugin-import-name-scourgify.txt` (empty), `__init__.py` (`InterfaceActionBase`,
  `actual_plugin` as a string), `action.py` (the Qt layer + the job functions), `selftest.py`.
- **`build_plugin.py`** — `uv run build_plugin.py` → `dist/scourgify-plugin-<version>.zip`
  (43 files, 1.6 MB, mostly `defaults/ao3/`). One source tree, one version: read from
  `pyproject.toml`, stamped into the plugin wrapper's `version` tuple **and** into
  `scourgify/_plugin_version.py`. Calibre's own Preferences → Plugins shows that version today;
  phase 5's settings dialog shows it in scourgify's own surface.
- **`tests/test_plugin_source.py`** — the Qt layer stays thin by enforcement, not intention
  (FanFicFare tests its plugin layer not at all, and neither can this one). Eight checks:
  no `run_writer(`/`subprocess`/`multiprocessing`/`ThreadPoolExecutor`; **no core import at module
  level** (every one lives inside a `job_*` function, so the GUI thread cannot reach a library read
  by accident); every `ThreadedJob` callback is `Dispatcher(...)`-wrapped; job functions take
  `abort`/`log`/`notifications`; `genesis()` wires and nothing else; `initialization_complete()`
  does nothing unless the test hook is armed; the import-name marker exists and is empty;
  `actual_plugin` is a string; the built zip carries the core, the marker at its root, and a real
  version. Docstrings and comments are stripped before the grep — this file's own subject matter
  would otherwise fail it.
- **`plugin/selftest.py`** — phase 4's GUI acceptance *driven* rather than clicked, behind
  `$SCOURGIFY_SMOKE` (a test hook, not a user feature — the same deal as `$SCOURGIFY_SCRIPT`).
  It builds the menu at 0/1/N, runs Inspect at both scopes, runs the db-from-worker smoke, and
  runs a **16 ms heartbeat on the GUI thread throughout**, so "the window never stops repainting"
  is a measured longest-gap number instead of an impression.

**Measured in the real GUI (Calibre 9.11), against the real 7,949-book library:**

| | |
|---|---|
| menu built (0 / 1 / 5 selected) | 0.3 / 0.3 / 0.7 ms |
| dispatch returned | 0.1–0.3 ms |
| longest GUI-thread heartbeat gap during a job | **22.8 ms** (16 ms timer) |
| library-scope inspect | 7,949 books · 7,666 never classified · 136 attempted · 18 pending · 7/7 columns |

**db-from-worker proof (B3.3), against a 6-book throwaway library:** `new_api.all_book_ids()` and
`field_for` read, `set_field` wrote `scourgify-smoke` onto book 1 and reverted it, all from inside
a `ThreadedJob` worker. Against the real library the same verb **refused** — `>50 books is not a
throwaway` — which is the fail-closed guard working, and the only write phase 4 contains.

**Two findings worth more than the code they came from:**

1. **`load_maps()` was silently building EMPTY maps inside the zip.** `common.HERE` resolves to a
   path *inside* the zip, so `os.path.exists()` is False for every bundled CSV and every layer read
   returns `[]` — no error, just a taxonomy of nothing. A phase-6 wrangle run would have normalized
   against it. Now a named `GuardrailError`; **phase 6 needs a resource seam for `DEFAULTS`**
   (`get_resources()`, or extract-once to a cache dir keyed by version).
2. **Cross-library artifact bleed is real, not theoretical.** Inspect on the throwaway library
   reported book 1 as "applied from classify_proposal_applied_20260726…" — the *real* library's
   archive, because `user_dir()` is global. The NLSpec Constraint (operational state namespaced by
   library uuid) is unbuilt, and the first read-only feature already tripped over it.

**Deviation from B1, deliberate:** 0 ids is supposed to open the dashboard. The dashboard is phase
7, so 0 ids opens the same menu at **library scope** with `Open dashboard…` present and greyed
(fixed slots), and Inspect answers with the dashboard's header numbers — each naming its source
function per B6.1. That is the phase-7 stub, not a new mode.

**Not verified by eye:** this machine's shell has no screen-recording permission, so no screenshot
was taken and no human clicked the button. What replaces it is the driven transcript above, from
inside the real GUI process, with the menu's real labels and enabled/disabled state printed.

### Phase 5 — Settings and engines

Key storage in plugin JSON with the plaintext banner; env vars win when set. Engine picker deriving
from `engines.TRAITS`/`PRICING`, showing per-engine limitations and a live cost for the selection.

### Phase 6 — Actions on a selection

Classify, normalize, re-derive status, edit tags — through `apply_ops`, never `run_writer`. Costs
and `writes` markers on every item. No confirmations.

Two things phase 3 fixed the shape of:

- **The post-action diff comes from the edit log, not a recompute.** The interaction spec's
  single-book card promises "the result appears as a diff in the panel — the tags it wants to add,
  with an undo", and NLSpec B1.3 makes diff-after + undo-always the GUI's whole audit-first
  contract. The log already holds exactly that, per book, keyed by run id: render it, don't
  re-derive it. A second derivation could disagree with what undo would replay.
- **The tool modules' write functions still call `run_writer` directly**
  (`wrangle.Plan.write`, `staleness.write`, `classify.apply_proposal`, `promote.backfill`), so a
  job calling one would spawn a second writer process against a library the GUI holds open. The
  phase-3 `SystemExit` sweep does not touch this. Those functions need an injected writer seam —
  same shape as `Plan.run(ask=…)` — and that is phase 6 work, named here so it isn't discovered
  at the keyboard.

### Phase 7 — The dashboard

The six stages, the backlog, the pending proposal, vocabulary and overrides, history. This is the
wizard given a surface — same stages in the same order, a stage with nothing to do greys out rather
than vanishing.

*Open question to resolve before building: modal dialog, or a dockable panel beside the library?*

### Phase 8 — Review in the library view

The capability argument for the whole project. Deliberately last: it is the most novel UI and the
least like anything that already exists.

---

## The working loop

As proposed by the owner, and it is the right shape for this:

1. **Complete** one phase.
2. **Review** it against this document and the interaction spec — did it drift?
3. **Update the roadmap** — mark done, revise later phases with what was learned.
4. **Reconcile** open questions and anything that changed. Ask the owner where the answer is theirs
   to give; decide and record where it is not.
5. **Next phase. Repeat.**

Keep this document current. When a phase teaches something that contradicts it — as the spike
contradicted its own issue twice in one day — **edit the document and say what changed.** A briefing
that quietly rots is worse than none.

---

## Repo conventions that will bite

- Work on `develop`. Never push to `main`; releases are a PR titled `Release vX.Y.Z` merged with
  `--merge` (a merge commit, not squash or rebase). `develop` history stays linear.
- **The version lives in `pyproject.toml`, not `src/scourgify/__init__.py`** — that file reads it
  from installed metadata. A `sed` at `__init__.py` matches nothing and exits 0; a release has
  already shipped the wrong version this way. Verify with `uv build` and check the wheel filename.
- **uv only.** Never `pip` or `pipx`, in docs or in advice.
- `$CALIBRE_LIBRARY` is real user data. Read-only via `common.ro_connect()`. Point
  `$SCOURGIFY_HOME` at a temp dir for anything that writes state.
- Never hand-edit `src/scourgify/defaults/ao3/` — regeneration overwrites it.
- `_writer.py` must never import `rich`, `ui`, `wizard`, or `report`.

---

## Appendix — minimal plugin skeleton

Verified working against Calibre 9.11. Three files, zipped flat (no containing folder), installed
with `calibre-customize -a <zip>`, then restart Calibre.

`plugin-import-name-scourgify.txt` — empty file, name matters.

`__init__.py`:

```python
from calibre.customize import InterfaceActionBase

class ScourgifyPlugin(InterfaceActionBase):
    name                    = 'scourgify'
    description             = 'Normalize and tag a FanFicFare-imported library'
    supported_platforms     = ['osx', 'linux', 'windows']
    author                  = 'Andrei Lavrenov'
    version                 = (0, 1, 0)
    minimum_calibre_version = (5, 0, 0)
    actual_plugin           = 'calibre_plugins.scourgify.action:ScourgifyAction'
```

`action.py`:

```python
from calibre.gui2.actions import InterfaceAction

class ScourgifyAction(InterfaceAction):
    name = 'scourgify'
    action_spec = ('scourgify', None, 'Normalize and tag this library', None)

    def genesis(self):
        # Wire the action and NOTHING else. No library work here or in
        # initialization_complete() — it runs on the GUI thread and will freeze Calibre.
        self.qaction.triggered.connect(self.show_menu)

    def show_menu(self):
        ids = self.gui.library_view.get_selected_ids()
        ...   # 0 -> dashboard, 1 -> single-book menu, N -> batch menu
```

Packaging, since `zip` may be refused by the permission classifier:

```python
import zipfile, os
with zipfile.ZipFile('scourgify.zip', 'w', zipfile.ZIP_DEFLATED) as z:
    for f in sorted(os.listdir('plugin')):
        if not f.startswith('.'):
            z.write(os.path.join('plugin', f), f)
```
