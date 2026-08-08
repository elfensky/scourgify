# NLSpec: scourgify Calibre plugin

## Meta
- Version: 1.1.0
- Author: Andrei Lavrenov
- Created: 2026-08-06
- Complexity: complicated
- Adversarial review: applied (22 challenges addressed, 2 dismissed — see Non-Goals)
- Inputs: `docs/superpowers/plans/2026-08-06-calibre-plugin.md` (kickoff briefing, post-spike),
  interaction-spec artifact `d13999d5-f95e-44e9-bdd7-68ed23cf6a0f`, issues #49–#53,
  multi-AI probe 1786034519 + codex adversarial review 1786035962 (results under
  `~/.claude-octopus/results/c4aa47b4-…/`)

## Purpose

Put scourgify's library-maintenance loop (normalize → staleness → classify → review → promote
→ backfill) inside Calibre as a first-class GUI plugin, writing through the live
`gui.current_db.new_api` handle so "close Calibre first" disappears from every workflow —
while the CLI/wizard keeps working unchanged off the same core modules.

## Actors

- **Library owner (GUI)**: the primary actor. Selects books in the library view, clicks the
  scourgify toolbar button, reads cost/writes markers, reviews proposals next to the actual
  books. Not necessarily a developer; every guard message must be a dialog they can act on,
  not a terminal string.
- **Calibre (host process)**: owns the GUI thread (freezes if any full-library work runs on
  it), the job system (`ThreadedJob` + `Dispatcher` — the only sanctioned place for
  scourgify's reads *and* writes), the live db handle, plugin loading (restart-to-load,
  zip + `plugin-import-name-scourgify.txt`), and multi-library switching. Bundles Python
  3.14.6 with empty site-packages — the core must run there unmodified.
- *(secondary, unchanged)* CLI/wizard user and the LLM engines behind `engines.py` — the
  plugin derives engine facts from `engines.TRAITS`/`PRICING`/`ENGINE_ENV`, never hardcodes;
  capability claims the UI makes ("too weak to judge", "refusal-prone") become TRAITS flags
  first, so the UI derives them like everything else.

## The atomicity contract (governs B2, B3, B7, B8)

The units below are the spec's single source of truth for write safety — every behavior
references them rather than redefining its own:

- **Unit of write**: one `(book, field)` op applied through `new_api`.
- **Unit of logging**: one edit-log line per applied `(book, field)` op, sharing a run id,
  bracketed by a run-header line (tool, scope, library uuid, ts) and a run-footer line
  (counts, outcome). A missing footer marks a partial run; the ops logged are still real,
  still undoable.
- **Unit of undo**: one run (with per-item review inside it).
- **Unit of cancellation**: between book ops — the job checks `abort` per book; a cancelled
  run has a footer marked `cancelled` and is undoable for the ops that applied.
- **Concurrency**: at most **one scourgify write-run per library at a time** — a library-
  scoped lock; while a write-run is live, menu/dashboard write verbs grey out (with the
  running job named). Read jobs may overlap. Artifact writes are serialized behind the same
  lock.
- **Apply-time conflict rule**: every plan carries the expected before-value per op; at
  write time each op re-checks the current value against it. Mismatch (the user or a later
  run edited the book since the plan was computed) → op skipped and reported, never
  silently overwritten. Same predicate as undo's (below). Nothing is ever "497 of 500,
  silently" — the result names the skipped books.
- **Conflict predicate** (exact): for multi-value fields, conflict iff the current value
  *as a set* differs from the expected set (order-insensitive, raw values — no alias/case
  normalization); for single-value fields, string inequality.

## Behaviors

### B1: The selection is the command (toolbar menu)
- **Trigger**: click on the scourgify toolbar button.
- **Preconditions**: plugin loaded (`genesis()` wired the action and nothing else).
- **Steps**:
  1. Resolve `gui.library_view.get_selected_ids()` **on the GUI thread, at click time**,
     into an immutable id list, captured together with the **library identity** (uuid).
     A dispatched job validates that identity against `gui.current_db` at start and aborts
     cleanly if the user switched libraries in between.
  2. 0 ids → open the dashboard (B6). 1 id → single-book menu. N ids → batch menu with
     identical verbs, scope-adjusted labels.
  3. Menu items carry their consequence: `free`, `costs` with the amount computed for this
     selection (per-book, thin descriptions cost more), or `writes`. **No confirmation
     dialogs anywhere in the GUI** — the click is the confirmation. For writes the net is
     undo (B7); for spend the gate is the price *on the control* (see B4.5 for how the
     core's >200-book cloud gate is satisfied). The GUI's audit-first contract is
     **diff-after + undo-always** in place of the CLI's dry-run-before: an action's result
     surfaces as the per-book diff of what was written, with undo one click away.
  4. Menu verbs and their meaning in the current state model:
     - **Classify** (B4).
     - **Normalize fields** / **Re-derive status** — the selection restricted through
       `wrangle.plan().restrict(ids)` / `staleness` scope, written through B2.
     - **Inspect — "What does scourgify know?"** — read job: stamp state, proposal/archive
       rows, failure-log entries for the selection.
     - **Classify the never-classified here** — offered when the selection contains
       never-attempted sendable books; a scope shortcut into B4 for exactly those ids.
       (The backlog is *derived* state — never-attempted ∧ sendable — there is no mutable
       queue to "add" to, and the menu never implies one.)
     - **Retry on <engine>** — offered only for books whose failure-log entries are
       **refusal-class**; other failure classes (auth/quota/timeout/parse) get a plain
       "Retry" on the same engine, with the auth taxonomy surfaced (B3.5).
     - **Edit tags…** — a thin wrapper: vocab-completing editor emitting `set_field` ops
       through B2.
- **Postconditions**: no library work has run on the GUI thread; any chosen action is
  dispatched per B3.
- **Edge cases**:
  - Selection changes while the menu is open → the id list captured at click time governs.
  - Library switched before click → identity check aborts with a plain message.
  - A verb that doesn't apply is greyed out with its reason, not hidden — a slot never
    changes meaning (mirrors the wizard's fixed-slot rule).

### B2: One write path — in-process `apply_ops` with the guards attached
- **Trigger**: any plugin action that writes; any CLI write (via `_writer.py`, unchanged).
- **Preconditions**: phase-1 extraction done: `apply_ops(api, ops)` split out of
  `_writer.py`; backup + wipe guard lifted out of `run_writer` so **both callers** keep them.
- **Steps**:
  1. The plugin **never calls `run_writer`** and never spawns a second writer process —
     enforced by a source-grep test on the plugin module (same mechanism as
     `tests/test_cli.py` on `wizard.py`), not by convention.
  2. Before writing: snapshot `metadata.db` using **sqlite's Online Backup API** (a live GUI
     connection makes `shutil.copy2` a torn-snapshot risk); the CLI path may keep the file
     copy since it only runs against a closed library. Backups reuse the existing retention
     policy (`_prune_backups`); a failed backup or insufficient disk → the write does not
     proceed (fail closed, message names the cause).
  3. The wipe guard (`_is_wipe`) runs for both callers; in-process it reads through
     `gui.current_db.new_api` (the authoritative in-memory state), not a second raw sqlite
     handle. `calibre_open()` is **skipped by design** in-process — there is no second
     process to protect against.
  4. Guards signal via a catchable **`GuardrailError(Exception)`** — never `SystemExit` —
     caught identically by `wizard._stage_guard` (which converts to the CLI's exit behavior,
     preserving CLI semantics) and the plugin's job wrapper (which converts to a result
     dialog).
  5. Ops apply per the **atomicity contract**: per-op conflict re-check, per-op logging,
     abort honored between books.
  6. `create_column` is **out of the in-process contract**: its legacy-DB reopen trick would
     desync the live GUI's models. `set_field`/`stamp_now`/`set_pref` are in. (First-run
     column setup: see B6.5.)
  7. Completion refreshes the affected rows in the library view via the Dispatcher-wrapped
     callback.
- **Postconditions**: CLI behavior **semantically identical** (same decisions, artifacts,
  messages, exit codes — "byte-identical" is not claimable across the SystemExit→
  GuardrailError change and is not the target); plugin writes carry backup + guard + log.
- **Parity acceptance (exact)**: shadow replay — real ops JSON captured from CLI runs,
  applied via both executors against cloned libraries, final metadata state diffed.
  Allowed divergence whitelist: `#wrangled`/stamp timestamps, backup filenames, edit-log
  timestamps and run ids. Everything else byte-equal. **Restore drill**: a scripted drill
  in the repo restores a snapshot onto a cloned library, asserts the restored db matches
  the snapshot, records wall-clock time; it must have run green at least once before any
  GUI write ships, and stays a documented pre-release check.
  > **Phase-1 amendment (2026-08-08, #54).** "Both executors" no longer exists as written:
  > the extraction leaves exactly ONE executor (`ops.apply_ops`), so the surviving
  > divergence risk is the two *call shapes* — JSON-serialized ops with a legacy handle
  > (CLI) vs live dicts without one (in-process). `tests/test_write_path.py` replays one
  > ops list through both shapes against a fake `api` and diffs the resulting state whole,
  > with stamps pinned. Replay against a real cloned Calibre library needs Calibre's own
  > Cache and is folded into phase 2's `calibre-debug -e` smoke script. The restore drill
  > landed as a CI test (`tests/test_restore_drill.py`) rather than a manual check — same
  > assertions, run on every push. Backups use sqlite's Online Backup API for **both**
  > callers (the spec permitted the CLI to keep the file copy; two mechanisms buy nothing
  > and the copy loses WAL-resident commits).
- **Edge cases**:
  - Guard trips mid-job → `GuardrailError` surfaces as a normal result dialog; the job ends
    cleanly, Calibre unaffected.
  - Conflicted ops → skipped + named in the result (atomicity contract).

### B3: Everything runs on the job system — reads included
- **Trigger**: any menu/dashboard action that touches the library.
- **Preconditions**: none — this holds from the first line of phase 4.
- **Steps**:
  1. Nothing runs at plugin startup; `initialization_complete()` does at most cheap wiring —
     anything that scans the library dispatches a job even there.
  2. All core calls — including read-only scans (`select.pick`, `classify.plan` scope
     resolution, `artifacts.classified_ids`) — run in a `ThreadedJob` (never raw
     `ThreadPoolExecutor` at the job boundary, never `multiprocessing` anywhere in-plugin).
  3. **The db-from-worker boundary is proven, not assumed**: phase 4's smoke plugin
     exercises `new_api` reads and a scratch write from a ThreadedJob worker against a
     throwaway library before any real feature relies on it. (FanFicFare precedent says
     it's safe; the smoke test makes it *this* repo's evidence.)
  4. Job functions accept `abort`/`log`/`notifications`; long stages are chunked,
     cancellable, and report progress (the Qt progress surface mirrors `report.Dashboard`'s
     shape: tagged/failed/rate/rising candidates, fed by classify's existing per-book
     callback).
  5. **Every completion callback that touches the GUI is `Dispatcher`-wrapped** — worker
     threads never touch Qt objects.
  6. Engine HTTP calls keep hard timeouts + backoff; failures are classified into a
     normalized taxonomy — refusal / auth (401) / permission (403) / quota (429) /
     timeout / parse — recorded in the failure log and driving which recovery verb B1
     offers.
- **Postconditions** (testable form): no scourgify code path executes a full-library scan
  on the GUI thread — enforced by construction (all core entry points reached only from job
  functions) and checked by the smoke script, which runs each menu action's dispatch path
  headlessly and asserts the GUI-thread portion completes in **<100 ms**.
- **Edge cases**:
  - Worker exception → `job.failed` path shows the error dialog; `SystemExit` cannot occur
    (B2.4) — `ThreadedJob` only catches `Exception`, so any surviving `SystemExit` would be
    a silent thread death.
  - User quits Calibre with a job running → jobs are killable; the atomicity contract
    bounds the damage (logged ops are real and undoable; missing footer = partial run).

### B4: Classify a selection (engine picker that states its failure modes)
- **Trigger**: "Classify" from the menu, or the dashboard's classify stage.
- **Preconditions**: at least one engine usable (key present or Apple available).
- **Steps**:
  1. Scope resolves ONCE through `classify.plan()` / `select.py` — the cost shown is over
     the exact `todo` set the run bills; text extraction never runs twice.
  2. The engine picker derives every row from `engines.TRAITS`/`PRICING`: per-book price ×
     this selection, and the engine's failure mode in words — Apple free/on-device but
     single-threaded and flagged `cannot_judge` (greyed out for promote); Gemini
     refusal-prone (measured 1-in-7 here) billing ~1,061 hidden reasoning tokens/book;
     OpenAI cheapest usable; Mistral untested here. These are TRAITS flags, not UI strings.
     Engines without keys are greyed out with "no key".
  3. **Cost estimate contract**: the displayed number is exactly `classify.est_cost()` over
     the resolved todo set at list prices — a deterministic formula, labelled an estimate.
     Text-fallback books are counted by the same `select.sendable`/`booktext.paths`
     resolution the run itself uses; a book whose extraction *fails at run time* drops out
     and is reported, so the estimate is an upper bound on the billed set.
  4. Runs as a job (B3) with live progress; results land as the normal proposal artifacts
     (`artifacts.py` — the CSV formats are shared with the CLI, unchanged), serialized
     behind the library write lock.
  5. **The core's cloud gate (>200 books) is satisfied by the scope step**: the tool
     module's confirmation seam (`ask=` callback) is answered by the GUI's scope dialog,
     where the user has just chosen the batch size with the full price rendered on the
     action button — the count+price display *is* the gate; no second "are you sure"
     dialog. Spend is irreversible and the spec treats the price-on-the-control as its
     only legitimate gate.
  6. **Refusals are an outcome, not an error**: blocked books surface in the result with a
     one-click "Retry on <non-refusing engine>" carrying its own cost label — never only a
     log file.
- **Postconditions**: proposal/failure artifacts identical in format to a CLI run,
  namespaced to the library (see Constraints); stamps applied on apply-semantics exactly as
  the CLI (a no-tag book still stamps).
- **Edge cases**:
  - All selected books already classified → the menu item says so (grey, count), no job.
  - Two classify attempts overlapping → prevented by the write lock; the second greys out.

### B5: Settings — keys in plugin JSON, env vars win
- **Trigger**: Preferences → Plugins → scourgify, or "API keys & engines" on the dashboard.
- **Steps**:
  1. Keys stored in Calibre's ordinary plugin `JSONConfig` — the FanFicFare/DeDRM precedent,
     a settled decision — with a plain banner ("saved in plain text… don't sync it somewhere
     public"), no modal, no acknowledgement checkbox. `chmod 0600` the config file on save.
  2. **Key resolution is a pure function** — env var first, JSONConfig second — evaluated
     per run and passed into `engines` explicitly. The plugin never mutates `os.environ`,
     so concurrent jobs and the CLI can never observe each other's keys.
  3. Per-engine rows show masked key, verified/not-configured state, and the engine's
     one-line role, derived from TRAITS (B4.2).
  4. Key verification is a cheap probe request run as a job; result shown inline, error
     classified per the auth taxonomy (B3.6).
- **Postconditions**: `engines.usable_engines(env=…)` reflects GUI-stored keys exactly as it
  reflects env keys; no key ever appears in a log, artifact, or error dialog.
- **Edge cases**:
  - Key present in both env and JSON → env wins, the row says so.
  - No keys at all → Apple remains usable; cloud rows grey out rather than vanish.

### B6: Dashboard — the wizard given a surface
- **Trigger**: toolbar click with nothing selected, or explicit "Open dashboard".
- **Steps**:
  1. Header mirrors the wizard snapshot; **every number names its source function**:
     books (`new_api.all_book_ids`), column health (`setup` check), new/changed
     (`select.changed`), never-classified backlog (`select.pick("unclassified")` — the
     header never says "up to date" while it is non-zero), pending proposal + candidates
     (`artifacts`). Numbers are recomputed by a read job **after every completed scourgify
     job** and on dashboard open — job completion is the invalidation boundary; the
     dashboard renders the last snapshot, never scans on the GUI thread.
  2. The six stages in wizard order — wrangle → staleness → classify → review → promote →
     backfill — each showing its pending count and consequence tags; a stage with nothing to
     do **greys out rather than vanishing**. Stage buttons drive the same tool-module plan
     functions the wizard drives (`wrangle.plan()`, `classify.plan()`, …) — the dashboard is
     a **third renderer behind the `report.py` seam**, iterating the same `wizard.TASKS`
     data. Where today's stage functions interleave asking with doing, phase 7 includes the
     refactor that moves the asking to the injected-callback seam (`Plan.run(ask=…)` /
     `decide=…`) so Qt consumes them without forking sequencing logic — this is named work,
     not assumed-free.
  3. Also here: Vocabulary & overrides, API keys & engines (B5), History (B7), Restore a
     snapshot (B6.6).
  4. Non-modal (dockable/tool window) so a running job and a browsing user coexist.
     *(Resolves the briefing's open question: not a modal dialog.)*
  5. **First-run / unhealthy library** (B6.5): when required columns or config are missing,
     the dashboard opens in setup mode — the same checks `scourgify setup` runs, as a job.
     Config is written per-library; **column creation is the one flow allowed to end in a
     "restart Calibre" prompt** (a one-time setup event — the restart-to-load world Calibre
     plugins already live in; a deliberate, accepted exception to "no handoffs").
  6. **Restore a snapshot** (B6.6): listing is free; an actual restore against the *open*
     library is **not performed live** — replacing `metadata.db` under the GUI is exactly
     the two-writers hazard this project exists to delete. The GUI restore flow closes the
     library (switch-away or guided restart), restores, and reopens; the current db is
     snapshotted first so restore stays reversible (CLI `rollback` semantics preserved).
- **Postconditions**: every number on the dashboard came from a job-executed read; stat
  sources and invalidation boundary as defined in step 1.
- **Edge cases**:
  - Library switched (Calibre is multi-library) → the dashboard re-binds: per-library state
    resolves against the library the GUI has open, keyed by library uuid — global config
    (keys) stays global. State from library A never renders against library B.

### B7: Edit log, history, undo (#49/#50/#51 — the GUI's safety story)
- **Trigger**: automatically on every write (log); "History" (read); "Undo this run" (write).
- **Preconditions**: phase 3 lands before any GUI write ships (phase 6). The **granularity
  contract is decided now**: per-op before/after lines per the atomicity contract —
  snapshot restore (`rollback`) stays the coarse whole-db tool; undo replays logged
  `before` values for one run.
- **Steps**:
  1. Log shape per the atomicity contract: run-header (run id — collision-proof, not a bare
     timestamp, see #45 — tool, scope, **library uuid**, engine+model for classify runs,
     because LLM re-execution can't be assumed reproducible), one line per applied
     `(book, field)` op with before/after, run-footer with outcome. The before-state is
     already read for the guard and the conflict re-check — captured, not discarded.
     Per-op lines keep any single record small at 7,949-book scale and make a crash
     recoverable (logged ops are real; missing footer = partial).
  2. Only `set_field` ops get true undo; `stamp_now`/`set_pref` are logged but excluded
     from replay (stated in the history view, not silently).
  3. Undo is **conflict-aware** using the shared predicate: a book whose field changed
     since that run is skipped and reported, offered in per-item review — never silently
     clobbered, never silently partial (the dry-run names the conflicted books before
     anything writes).
  4. Undo itself writes through B2, so it is backed up, guarded, logged — and undoable.
  5. History view: lifetime stats, per-book timeline, per-run detail — read-only, free.
     Records referencing books no longer in the library render as historical fact, flagged
     "book no longer present", and are excluded from undo. Records from a different
     library uuid never mix in.
- **Postconditions**: every GUI write is recoverable at run granularity without discarding
  later work; "no confirmations" (B1) is honest because this net exists.
- **Edge cases**:
  - Empty/missing log → clean empty state, not a traceback.
  - Undo of an undo → just another logged run; falls out of 4.

### B8: Review in the library view
- **Trigger**: "Review N" on the dashboard, or a pending proposal on menu open.
- **Preconditions**: B2 + B7 shipped (per-item accept exercises the write path one book at a
  time); prototype-first — this is the phase with no precedent anywhere.
- **Steps**:
  1. The user's current context — existing marked books, active virtual library / search —
     is **snapshotted before review and restored after**; review never clobbers a working
     set the user had.
  2. The N proposed books are marked via the **legacy db handle** (`db.set_marked_ids` —
     `new_api` has no marked-books concept) and scoped with `marked:true` / a temporary
     virtual library, so the user pages through them with Calibre's own navigation — cover,
     blurb, series, existing tags visible. Marks are ephemeral; durable review state lives
     in the artifacts/edit log, never in marks.
  3. A compact review panel (not an inline library-grid diff — the library view is not a
     diff grid) shows current vs proposed per book with per-item accept/reject — the same
     per-item semantics as `ui.checklist` flows; all-or-nothing defeats the review.
  4. **Review atomicity**: each accepted item applies immediately through B2 as its own
     logged op under the review's run id; rejected classify items land in the rejects log
     exactly as CLI rejects do; the proposal is archived **when the review session ends**
     (panel closed or all items decided) with decided rows marked. Pending =
     proposal rows minus decided rows — recomputable at any time from the artifacts alone.
- **Postconditions**: artifact semantics identical to the CLI; marks cleared and prior
  context restored; the dashboard's pending count follows the Pending definition above.
- **Edge cases**:
  - User closes the panel mid-review → decided items are applied+logged, undecided remain
    pending (per the review-atomicity rule); nothing half-writes.
  - Proposal references books deleted from the library since → skipped and reported.

## Constraints

- **Safety of the live library** (weighted highest): one write path (B2); never a second
  writer process; snapshot-before-write via a live-connection-safe mechanism; wipe guard on
  every writer; the atomicity contract (single write-run lock, apply-time conflict checks,
  per-op logging); conflict-aware undo; fail-closed on any guard/backup failure. Enforced by
  source-grep tests, not intention.
- **GUI responsiveness**: zero library work on the GUI thread — reads included; all core
  calls through ThreadedJob with abort/progress; Dispatcher-wrapped callbacks; no
  multiprocessing; GUI-thread portion of any dispatch <100 ms (smoke-checked).
- **Compatibility**: core runs unmodified on Calibre's bundled Python 3.14.6 with empty
  site-packages (protecting this is a hard constraint on all future work); CI matrix
  3.10/3.13/3.14; `calibre-debug -e` smoke script in-repo; no vendored dependencies — only a
  Qt backend for the `report.py`/`ui.py` seams; the CLI surface unchanged throughout.
  **Version coherence**: the plugin zip is built from the same source tree and release as
  the PyPI wheel, embeds the core version, and shows it in settings; the zip build joins
  the release flow so GUI and CLI cannot drift.
- **Scale**: 7,949 books today; every listed operation is full-library-capable (backlog
  7,799); classify runs are chunked/resumable; the edit log is per-op lines (bounded record
  size at any scale).
- **Multi-library**: operational state — proposals, failure log, edit log, stamps
  interpretation, config/column map — is **namespaced by library uuid** (the `user_dir()`
  tree gains a per-library level; the CLI keeps working for the single-library case by
  resolving the same way). Keys and UI preferences are global.

## Dependencies

- **External services**: LLM engines via `engines.py` (Apple on-device / Anthropic / OpenAI /
  Gemini / Mistral) — stdlib `urllib` transport, keys per B5.
- **Host**: Calibre ≥ 5.0 (plugin API: InterfaceAction, ThreadedJob, JSONConfig, marked
  books); developed against 9.11.
- **Libraries**: none beyond the host. `rich` remains CLI-only; `_writer.py` (CLI path)
  remains rich/ui/wizard/report-free.

## Non-Goals

- **Encrypted key storage / OS keyring / LLM gateway** — plaintext-with-banner is the
  settled, precedent-matching choice for a single-owner library (adversarial challenge
  dismissed: enterprise compliance posture is not this product).
- **Confirmation dialogs for spend** — challenged ("undo does not refund tokens") and
  resolved by design, not dismissed: the price on the control plus the scope step is the
  gate (B4.5); a separate reflex dialog is the pattern this design explicitly rejects.
- **Local-endpoint engines (Ollama etc.)** — `engines.py` is one row per engine when wanted;
  not a plugin concern.
- **A web UI, or a literal Qt port of the wizard's prompt choreography** — decided against
  in the design review.
- **Undo for `create_column`/`set_pref`/`stamp_now`** — logged, excluded from replay,
  documented (challenge addressed by scoping, not building).
- **Live in-GUI snapshot restore** — deliberately routed through close/restore/reopen
  (B6.6); a live restore is the two-writers hazard by another name.

## Acceptance Definition

- **Satisfaction target**: 0.90 (complicated).
- **Critical behaviors (must reach 1.0)**: B2 (one write path with guards — parity proven by
  shadow replay against the exact divergence whitelist + the scripted restore drill) and B3
  (nothing on the GUI thread, no SystemExit reachable from a job, db-from-worker boundary
  smoke-proven). These are phases 1–2, worth doing even if the plugin is never built.
- Phase gates follow the briefing's roadmap and working loop: each phase reviewed against
  this spec + the interaction spec, roadmap updated, contradictions edited into the
  documents — a briefing that quietly rots is worse than none.
