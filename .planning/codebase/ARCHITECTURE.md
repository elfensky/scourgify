<!-- refreshed: 2026-08-28 -->
# Architecture

**Analysis Date:** 2026-08-28

## System Overview

```text
┌─────────────────────────────────────────────────────────────────────────┐
│               Entry Points                                               │
│  CLI (cli.py)         │  Interactive (wizard.py)    │  Calibre Plugin   │
│  dispatch → tools     │  guided lifecycle             (plugin/action.py) │
└────────────┬──────────┴────────────┬─────────────────┴──────────┬────────┘
             │                       │                            │
┌────────────▼──────────────────────▼────────────────────────────▼────────┐
│                        Tool Modules (Pure Compute)                       │
│  wrangle.py (normalization) · classify.py (LLM tagging) · staleness.py  │
│  synopsis.py (description generation) · promote.py (tag adjudication)   │
│  select.py (which books) · setup.py (health checks)                      │
└────────────┬──────────────────────────────────────────────────────────────┘
             │
┌────────────▼──────────────────────────────────────────────────────────────┐
│                  Plan Pattern (compute once, read many)                    │
│  Plan = wrangle.plan / classify.plan / synopsis.plan()                    │
│  Properties: preview(), guard(), step() (review), write()                │
│  One compute, NO recompute — preview/guard/step/write all read it        │
└────────────┬──────────────────────────────────────────────────────────────┘
             │
┌────────────▼─────────────────────────────────────────────────────────────┐
│                         Read & Write Infrastructure                      │
│  Read: ro_connect() → read-only sqlite (safe while Calibre open)         │
│  Write: ops list → serialized JSON → calibre-debug -e _writer.py        │
│         (or in-process: write_ops() → ops.apply_ops() in-plugin)        │
└────────────┬──────────────────────────────────────────────────────────────┘
             │
┌────────────▼─────────────────────────────────────────────────────────────┐
│            Cross-Cutting: editlog, backup, guardrails                    │
│  editlog.py (audit trail: JSONL)                                         │
│  backup_db() (auto-snapshot before writes)                               │
│  check_wipe() (catastrophic change detection)                            │
│  calibre_open() (detect running GUI)                                     │
└─────────────────────────────────────────────────────────────────────────┘
```

## Component Responsibilities

| Component | Responsibility | File |
|-----------|----------------|------|
| **cli.py** | Single entry point; dispatches to tools by argv[0]; catches `GuardrailError` → `SystemExit` | `src/scourgify/cli.py` |
| **wizard.py** | Interactive guided lifecycle: status header, menu, stages (wrangle→staleness→synopsis→classify→review→promote→backfill) loop until quit | `src/scourgify/wizard.py` |
| **wrangle.py** | Deterministic normalization: loads maps (ao3←defaults←overrides), transforms all books once, reports diffs, guards/steps/writes changes | `src/scourgify/wrangle.py` |
| **classify.py** | LLM-based tagging: selects books (incremental/scope-filtered), sends to engine, parses responses, builds proposals, applies on --apply | `src/scourgify/classify.py` |
| **staleness.py** | Re-derives #status from #updated age: In-Progress/Hiatus/Abandoned by thresholds (2y/5y); idempotent | `src/scourgify/staleness.py` |
| **synopsis.py** | Description refinement: checks adequacy, extracts prose on need, generates back-cover text, stamps #synopsized | `src/scourgify/synopsis.py` |
| **promote.py** | Tag adjudication: advocates/skeptics for `proposed_new`, applies verdicts to overrides/, backlogs promoted tags onto source books | `src/scourgify/promote.py` |
| **select.py** | "Which books does this run operate on?" — shared owner (wizard, classify, wrangle, staleness all use it); never disagrees | `src/scourgify/select.py` |
| **common.py** | Shared core: library resolution, read-only sqlite, normalization, TOML config, run_writer() (write funnel), op constructors | `src/scourgify/common.py` |
| **ops.py** | THE write executor (used by CLI and plugin): coerces book ids, applies create_column/set_field/stamp_now/set_pref | `src/scourgify/ops.py` |
| **_writer.py** | CLI-only helper: glue between run_writer() subprocess and ops.apply_ops(); reads ops.json from temp file | `src/scourgify/_writer.py` |
| **engines.py** | LLM provider abstraction: one row per engine (apple/claude/openai/gemini/mistral), cost estimates, retry/backoff logic | `src/scourgify/engines.py` |
| **artifacts.py** | CSV format owner: proposal, ranked, review, ledger, failures, rejects (paths + readers/writers) | `src/scourgify/artifacts.py` |
| **editlog.py** | Write audit trail: JSONL with run headers/footers, op lines, conflict detection for undo | `src/scourgify/editlog.py` |
| **overrides.py** | User overrides dir handler: formats (delimiter sniffing, append-if-absent), vocab merge, alias resolution | `src/scourgify/overrides.py` |
| **report.py** | Output rendering: rich (wizard/dashboard) or plain (CI); shared policy owner — never hand-render in a tool | `src/scourgify/report.py` |
| **ui.py** | Interactive helpers: prompt, menu, checklist, confirmation; rich Console with friendly error on missing | `src/scourgify/ui.py` |
| **booktext.py** | Text extraction: EPUB as zip + ebook-convert fallback; timeout guard | `src/scourgify/booktext.py` |
| **setup.py** | FanFicFare health check + column creation (legacy DB path only); config wizard | `src/scourgify/setup.py` |
| **plugin/action.py** | Qt toolbar button: thin invariant-enforced layer. Job functions (worker thread) → core imports. No run_writer, subprocess, or module-level core. | `plugin/action.py` |
| **plugin/config.py** | Settings dialog: API keys (JSONConfig, 0600), plaintext banner, key sources (env wins) | `plugin/config.py` |
| **plugin/selftest.py** | SCOURGIFY_SMOKE=1 hook: db read/write under Calibre, GUI stall measurement | `plugin/selftest.py` |

## Pattern Overview

**Overall:** Layered pipeline with Plan abstraction and dual write paths.

**Key Characteristics:**
- **One-pass design:** wrangle.Plan computes the full-library transform ONCE (for global context — tagcanon majority spelling, known_chars); preview/guard/step/write all read from it (no recompute).
- **Deterministic first:** wrangle runs deterministically before classify; junk-normalized columns don't hide sparse books from the tagger.
- **Staged pipeline:** wrangle → staleness → synopsis → classify → review → promote → backfill (each dry-runs first, reports, asks before writing).
- **Dual write paths:** CLI (standalone `run_writer()` → subprocess calibre-debug) and in-process plugin (`write_ops()` → `ops.apply_ops()` on GUI's live handle). Same guards, same ops executor, different process model.
- **Selection (not scoping):** `select.py` owns "which books" — wizard header, classify scope flags, wrangle apply scoping all use the same logic, never disagree.
- **Audit-first:** every pass can dry-run read-only; writes refuse while Calibre is open; all writes auto-backup and log.

## Layers

**Entry Points:**
- Purpose: Accept user input and dispatch to the appropriate tool
- Location: `src/scourgify/cli.py` (dispatch), `src/scourgify/wizard.py` (guided lifecycle), `plugin/action.py` (Calibre toolbar)
- Contains: Argument parsing, flow control, user prompts (via `ui.py`), error conversion
- Depends on: wrangle, classify, staleness, synopsis, promote, setup, ui, common
- Used by: Users (CLI/GUI)

**Tool Layer (Pure Compute):**
- Purpose: Compute what SHOULD change, independent of how to read/write
- Location: `src/scourgify/wrangle.py`, `classify.py`, `staleness.py`, `synopsis.py`, `promote.py`, `setup.py`
- Contains: Algorithms (transform, LLM tagging, age-to-status, description checking), Plan objects, guardrail checks
- Depends on: common (for normalization, library reading), select, artifacts, engines (for classify), overrides, report (for output)
- Used by: Entry points and each other (wrangle before classify, promote after classify)

**Read Layer (Read-Only Sqlite):**
- Purpose: Query the library state safely while Calibre is open
- Location: `src/scourgify/common.py` (ro_connect, read_custom_column, column_values, _populated_books)
- Contains: Read-only connection factory, link-table-aware custom column reading, before-state capture for edit log
- Depends on: sqlite3 (stdlib)
- Used by: wrangle.read_library, classify.gather, staleness, synopsis, select, edit log capture

**Write Layer (Dual Path):**
- Purpose: Apply computed changes safely (snapshot, guard, log, execute)
- Location: 
  - CLI: `src/scourgify/common.py` (run_writer), `src/scourgify/_writer.py` (subprocess glue), `src/scourgify/ops.py` (executor)
  - Plugin: `src/scourgify/common.py` (write_ops), `src/scourgify/ops.py` (executor)
- Contains: Wipe guard (`check_wipe`), backup (`backup_db`), edit log start/finish, op execution
- Depends on: sqlite3, subprocess (CLI only), editlog, ops
- Used by: wrangle.Plan.write, classify.apply_proposal, staleness, synopsis, promote, setup

**Shared Infrastructure:**
- Purpose: Data formats, LLM interfaces, selection logic, caching
- Location:
  - artifacts.py (CSV formats: proposal, ranked, failures, rejects)
  - engines.py (LLM abstraction: traits, retry, key resolution)
  - select.py (book selection: changed, incremental, unclassified, unsynopsized)
  - editlog.py (audit trail: run records, conflict detection)
  - overrides.py (user config: formats, vocab merge)
- Depends on: common, sqlite3 (for select), json (for editlog)
- Used by: wrangle, classify, staleness, synopsis, promote, wizard, plugin

**Output Layer:**
- Purpose: Render changes in user-friendly format (rich interactive or plain text)
- Location: `src/scourgify/report.py` (tables, trees, dashboard), `src/scourgify/ui.py` (prompts, menus, checklists)
- Contains: Rich rendering (wizard, dashboard, audit report), plain fallback (CI), prompts and confirmations
- Depends on: rich (optional for interactive), common (for scripting)
- Used by: wizard, wrangle (audit), classify (dashboard), all interactive prompts

## Data Flow

### Primary Request Path (Guided Wizard)

1. User runs `scourgify` (no args) → `cli.py:_dispatch()` checks for bareword → imports `wizard`
2. `wizard.run()` → `snapshot()` reads library state (counts, stamps, pending work)
3. `header()` renders status panel
4. Calls `stage_setup()` if columns/config missing (else skips)
5. **Landing menu loop** (landing_menu):
   - User picks "full run" or single task (wrangle, classify, staleness, etc.)
   - Each stage:
     - Dry-runs (no write), shows report via `report.py`
     - `guard()` checks for catastrophic changes
     - Asks before writing
     - Calls `step()` if user picked review mode (writes to rejects.csv if any rejected)
     - `write()` via `run_writer()` (auto-backup + calibre-debug subprocess) or skip
   - Loop back to menu, re-snapshot, until quit

**For each stage (e.g., wrangle):**
- `stage_wrangle()` → `wrangle.plan(cfg, maps)` computes the Plan (full-library transform, ONCE)
- `p.preview()` shows what would change (mass folds + unique per-book changes + SAFETY)
- `p.guard()` checks data-loss invariants (no book loses its last fandom/character; tag mass-delete guard)
- If `--step` mode: `p.step()` → `ui.checklist()` walks each book's UNIQUE changes 1-by-1, appends rejects to data/rejects.csv
- `p.write()` → `run_writer([op_set_field(...)])` → snapshot + editlog start + subprocess + editlog finish

### Classify Request Path (New/Changed Books)

1. `classify.main()` (argparse) → selects books via `select.pick(con, mode, ...)`
   - Mode: "incremental" (new/changed), "last N", "since DATE", "all", "unclassified" (backlog), "ids" (explicit)
2. `classify.plan(con, cfg, engine, ...)` builds a proposal:
   - `gather()` filters books (description ≥ 40 chars, not already stamped if incremental)
   - `extract()` prose from EPUB / ebook-convert
   - `chunks()` text into ≤ 4096-token slabs
   - For each book: `ask_retry()` (engine call with backoff) → `parse_resp()` (JSON parsing) → `(added_tags, proposed_new)`
   - Write proposal CSV + ranked CSV (if --fresh or resumed)
3. Dry-run: print cost estimate ($ per engine)
4. With `--apply`:
   - `apply_proposal()` reads proposal → applies added_tags onto books → stamps #wrangled
   - Auto-creates #wrangled column if missing
   - archives proposal to classify_proposal_applied_<ts>.csv
5. Review: user edits data/classify_proposal.csv → re-run --apply

### Promote Path (Tag Adjudication)

1. `promote.main()` → reads data/classify_newtags_ranked.csv (aggregated proposed_new from all classify runs)
2. `plan()` drives advocate/skeptic decision per tag:
   - Uses same LLM + ask_retry for consistency
   - Writes data/promote_review.csv with verdicts
3. `--apply` → folds verdicts into overrides/promote_aliases.csv (candidate→target snaps)
4. `--backfill` → reads promoted verdicts + the archived proposal CSVs → applies promoted tags onto the books that proposed them (deterministic, no LLM)

### Staleness Path (Age-to-Status)

1. `staleness.compute()` reads #updated age per book
2. Re-derives #status: 
   - < 2y → In-Progress
   - 2-5y → Hiatus
   - ≥ 5y → Abandoned
   - (leaves Completed/Dropped/Rewritten untouched)
3. Dry-run shows changes
4. `--apply` writes via `run_writer()`

### Synopsis Path (Description Refinement)

1. `synopsis.main()` → reads books via `select.pick(con, "unsynopsized")`
   - Exits: no #synopsized stamp, no file to extract from, or #updated > #synopsized (refresh)
2. For each book:
   - `booktext.extract()` prose (up to measured chunk size)
   - `verdict(desc_text, model_answer)` checks if existing blurb is adequate ("is this ABOUT the story?")
     - No answer → unparseable (don't stamp, re-settle later)
     - "keep" → stamp #synopsized, move on (never rewrite author prose)
     - "generate" → chunk prose, ask for back-cover text (premise, characters, stakes, hook, themes; never plot outcomes)
3. Failures → synopsis_failures.csv (keeps queue finite)
4. No --apply writes changes immediately (sweeps can run for weeks in background); --apply with --no-write is genuinely free

## Key Abstractions

**wrangle.Plan:**
- Purpose: Compute the full-library normalization ONCE; preview/guard/step/write all read from it
- Location: `src/scourgify/wrangle.py` lines ~314-425
- Pattern: `plan = Plan(cfg, m); plan.preview(); plan.guard(); [plan.step()]; plan.write()`
- Key properties:
  - `changes` {column label: {book: new sorted values}} — what write() sends
  - `diffs` {book: {label: (gone, added)}} — what preview shows
  - `lost` {book: (lost_fandom, lost_char)} — SAFETY counters for data-loss detection
  - `restrict(ids)` narrows the write set (not the read); used by `apply --books` or wizard's "most recent N"

**classify.plan():**
- Purpose: Compute book selection + LLM run once; handles resume (skip already proposed), cost estimate
- Location: `src/scourgify/classify.py` lines ~220-280
- Pattern: `plan_obj = classify.plan(...); print plan_obj.cost_estimate(); plan_obj.run(); apply_proposal()`
- Key: Resume-aware (skip books already in proposal), resumable (partial run leaves proposal for next --apply)

**synopsis.plan():**
- Purpose: Compute the unsynopsized queue once; handle failures for finite queue
- Location: `src/scourgify/synopsis.py`
- Pattern: `plan_obj = synopsis.plan(); plan_obj.run(); plan_obj.write()`
- Key: No artifact (no cost estimate needed); failures logged so queue doesn't re-propose blocked books

**select.pick():**
- Purpose: Which books does this run operate on? Single owner so wizard header ≠ classify scope
- Location: `src/scourgify/select.py` lines ~135-190
- Modes: "incremental" (changed), "last N", "since DATE", "all", "ids" (explicit list), "unclassified" (backlog), "unsynopsized" (synopsis queue)
- Pattern: `books = select.pick(con, "incremental"); classify.run(books)`
- Key: changed() uses three clocks (added-date, #updated, #wrangled) so fic refetches are caught; unclassified subtracts seen + checks sendable (thin-blurb books are synopsis work)

**artifacts.py (CSV format owner):**
- Purpose: Single source of truth for proposal/ranked/failures/rejects/ledger shapes
- Location: `src/scourgify/artifacts.py`
- Formats: proposal (book_id, added_tags, proposed_new), ranked (tag, count, nearest, sim, verdict), failures (book_id, reason, reason_class), rejects (ts, stage, book, title, kind, column, before, after, class), ledger (book, field, before, after, verdict)
- Pattern: Tool reads/writes through these functions, never hand-reads CSVs elsewhere
- Key: Delimiter sniffing, append-if-absent on rejects, archiving (proposal_applied_<ts>.csv)

**engines.py (LLM abstraction):**
- Purpose: One row per provider; cost, traits, key resolution, retry, availability
- Location: `src/scourgify/engines.py`
- Pattern: `ask_retry(engine, prompt, ...)` hides provider-specific logic; PRICING/TRAITS define capabilities
- Key: Output token billing (reasoning models bill hidden thinking as output), key env > stored, failure classification (refusal/auth/permission/quota/timeout/parse/error)

**editlog.py (Audit trail):**
- Purpose: JSONL record of EVERY write: what changed, why (tool+scope), when, outcome
- Location: `src/scourgify/editlog.py`
- Pattern: `rec = editlog.start(tool, ops, before, scope); try: apply; finally: editlog.finish(rec, outcome)`
- Key: Op lines written BEFORE apply (crash-safe for undo); run id collision-proof; conflict detection (which ops landed vs expected)

**overrides.py (User config formats):**
- Purpose: Own the overrides/ directory and file formats (delimiter sniffing, append-if-absent, -term removal)
- Location: `src/scourgify/overrides.py`
- Pattern: `merge_vocab(vocab, path)` applies -term removal; `read_aliases()` for promote snaps
- Key: Never re-implement CSV reading elsewhere; delimiter sniffing (,|;) applies to every format

## Entry Points

**CLI (`scourgify` command):**
- Location: `src/scourgify/cli.py:main()` → `_dispatch()`
- Triggers: User runs `scourgify [command] [args]`
- Responsibilities:
  - Dispatch by argv[0] (classify, staleness, synopsis, promote, overrides, rollback, or wrangle for setup/audit/apply/wizard)
  - Catch `GuardrailError` → `SystemExit(str(e))` (so CLI exit codes are clean; in-plugin, GuardrailError is caught by wizard._stage_guard)
  - Lazy imports (--version stays cheap)

**Wizard (`scourgify` bare):**
- Location: `src/scourgify/wizard.py:run()`
- Triggers: User runs `scourgify` with no args and TTY is present
- Responsibilities:
  - Header: library name, column health, new/changed count, pending proposal, unclassified backlog, synopsis queue, Calibre-open warning
  - Setup: if columns/config missing
  - Landing menu: loop asking what to do (full run or single task)
  - Each stage: dry-run → report → ask → [step review] → write (or skip)
  - Re-snapshot after each write, loop until quit

**Calibre Plugin (phase 4, read-only):**
- Location: `plugin/action.py:InterfaceActionBase` subclass
- Triggers: User clicks the scourgify toolbar button
- Responsibilities:
  - Menu: dynamic based on selection (all-library vs selected books)
  - Job dispatch: read-only jobs (inspect, db_smoke) wrapped in ThreadedJob
  - Library guard: detect library change since click
  - GUI binding: Dispatcher-wrapped callbacks (no Qt off GUI thread)

## Architectural Constraints

- **Threading:** Single-threaded event loop (GUI is Qt single-threaded; CLI is sync). classify/promote use ThreadPoolExecutor for LLM calls (respects --workers).
- **Global state:** Module-level `_script` and `_LIBRARY` in `common.py` (scripting/plugin library injection); memoized vocab/aliases in `classify.py` (cleared by tests)
- **Circular imports:** Avoided by lazy imports (ui/wizard imported only after rich presence check; overrides imported inside wrangle for step review)
- **Sqlite:** Read-only connections (ro_connect) used everywhere except writes. In-process writes use `new_api` (GUI's live handle); CLI writes use subprocess. No two writers on the same library.
- **Rich dependency:** Hard-import in wizard/ui/report; CLI/core/plugin all gracefully degrade (ui.py raises catchable GuardrailError if rich missing)
- **Calibre version:** Bundled Python 3.14.6 (from Calibre 9.11); no tomllib (core.py ships minimal reader); legacy DB for column creation (phase 4+); new_api for reads/writes

## Error Handling

**Strategy:** Guardrails raise `GuardrailError` (catchable Exception, not SystemExit) so Calibre jobs don't die silently. CLI converts back to SystemExit. Exceptions from LLM engines are classified (refusal/quota/timeout/parse/error) and retried only on refusal.

**Patterns:**
- Data loss: `data_loss_guard()` checks lost fandom/character counts before write
- Tag mass-delete: `tag_loss_guard()` checks shrinkage >25% AND absolute-floor scaled to scope
- Catastrophic wipe: `check_wipe()` aborts if a column would drop >90% of populated books (on ≥100-book columns)
- Calibre open: `calibre_open()` detects GUI via pgrep/ps (fails CLOSED if pgrep absent)
- Backup failure: `backup_db()` reads the backup back to verify (not just size-check)
- Engine error: `classify_error()` classifies the HTTPError.code, logs reason with class prefix (readable by failure_class()), retries on refusal only

## Cross-Cutting Concerns

**Logging:** `editlog.py` is the ONE owner (JSONL with run headers, before-state, per-op lines, run footers, timestamps, library uuid). Written BEFORE apply (crash-safe). Includes conflict detection for undo.

**Validation:** Declarative (config.toml schema + load_config behavior), plus semantic guards (data_loss/tag_loss before wrangle write).

**Authentication:** Keys via environment (env > stored in plugin settings). `resolve_keys(stored, env=)` produces final mapping; `key_source()` says which won; `mask/unmask` redact in dialogs; redact() strips keys from every reason string before logging.

---

*Architecture analysis: 2026-08-28*
