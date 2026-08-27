# Codebase Structure

**Analysis Date:** 2026-08-28

## Directory Layout

```
scourgify/
├── src/scourgify/              # Installable package (hatchling; on PyPI as 'scourgify')
│   ├── __init__.py             # Version + entry point (__main__ → cli.main)
│   ├── cli.py                  # Single `scourgify` command dispatcher
│   ├── wizard.py               # Interactive guided lifecycle (rich)
│   ├── wrangle.py              # Normalization: Plan pattern + transform + guards
│   ├── classify.py             # LLM-based tagging: proposal generation + apply
│   ├── staleness.py            # Re-derive #status from age
│   ├── synopsis.py             # Description refinement: adequacy check + generation
│   ├── promote.py              # Tag adjudication: advocate/skeptic + backfill
│   ├── select.py               # Book selection: changed/incremental/unclassified/etc.
│   ├── common.py               # Shared core: library resolution, sqlite, run_writer, ops
│   ├── ops.py                  # Write executor (used by CLI and plugin)
│   ├── _writer.py              # CLI-only subprocess glue: reads ops.json → calls ops.apply_ops
│   ├── engines.py              # LLM abstraction: apple/claude/openai/gemini/mistral
│   ├── artifacts.py            # CSV formats: proposal, ranked, failures, rejects, ledger
│   ├── editlog.py              # Audit trail: JSONL with conflict detection
│   ├── overrides.py            # User config: overrides/ dir + formats
│   ├── setup.py                # FanFicFare health check + column creation
│   ├── report.py               # Output rendering: rich tables/trees or plain text
│   ├── ui.py                   # Interactive helpers: prompts, menus, checklists (rich)
│   ├── booktext.py             # Text extraction: EPUB + ebook-convert
│   ├── defaults/               # Bundled read-only taxonomy (ships in wheel)
│   │   ├── ao3/                # Generated AO3 layer (build_ao3_layer.py)
│   │   │   ├── universes.csv   # master,name,rel pairs (fandoms)
│   │   │   ├── characters.csv  # variant,canonical,fandom (fandom-scoped fold)
│   │   │   ├── tags.csv        # variant,canonical (freeform trope fold)
│   │   │   └── genres.csv      # variant,canonical
│   │   ├── fandoms.csv         # alias,canonical (curated re-points over ao3)
│   │   ├── characters.csv      # variant,canonical (global folds)
│   │   ├── genres_canon.csv    # variant,canonical (curated genre synonyms)
│   │   ├── genres_allow.txt    # Allowlist: only these are #genres (else → tags)
│   │   ├── fandom_blocklist.txt# Non-fandoms that should become tags/relationships
│   │   ├── decompose.csv       # Contextual: "Fate SI" → fandom + tags
│   │   ├── genres_split.csv    # Combined genre → atoms (e.g. "Sci-Fi/Fantasy" splits)
│   │   ├── tropes.csv          # variant,canonical,route (tag/genre/character/fandom/drop)
│   │   ├── junk.txt            # Exact + regex patterns to drop
│   │   ├── ratings.txt         # Rating values (never become genres/tags)
│   │   ├── classify_vocab.txt  # Curated tagging vocabulary (hand-edited)
│   │   ├── classify_vocab_ao3.txt  # Generated high-frequency AO3 tags (do NOT hand-edit)
│   │   └── ao3_exceptions.txt  # Pairs excluded from generation (reasons documented)
│   └── afm.swift               # Apple Foundation Models bridge (compile to afm binary)
├── plugin/                      # Calibre plugin (read-only phase 4)
│   ├── __init__.py             # InterfaceActionBase subclass
│   ├── action.py               # Qt toolbar button: thin + invariant-enforced
│   ├── config.py               # Settings dialog: API keys (env > stored)
│   ├── selftest.py             # SCOURGIFY_SMOKE=1 smoke test (read + scratch write)
│   └── plugin-import-name-scourgify.txt  # Zipimport marker (required)
├── tests/                       # Test suite (pytest compatible, no framework)
│   ├── test_core.py            # Pure core: transform, trope-chain, parse_resp, TOML
│   ├── test_selection.py       # Selection: changed, incremental, unclassified
│   ├── test_plan.py            # wrangle.Plan + restrict
│   ├── test_layers.py          # Map loading + transform edge cases
│   ├── test_classify_run.py    # classify.plan + cost estimates
│   ├── test_promote.py         # promote: advocate/skeptic
│   ├── test_synopsis.py        # synopsis.verdict + chunk sizes
│   ├── test_synopsis_queue.py  # synopsis queue: stamp/failure/no-source exits
│   ├── test_engines.py         # Engine traits, cost calculation
│   ├── test_artifacts.py       # CSV formats: proposal, ledger, etc.
│   ├── test_editlog.py         # Edit log shape + conflict detection (subprocess stubbed)
│   ├── test_write_path.py      # ops.coerce + apply_ops (with fake api)
│   ├── test_wizard_flow.py     # Wizard interaction flows (in-process, canned answers)
│   ├── test_wizard.py          # Wizard stages (minimal)
│   ├── drive_wizard.py         # Manual: real PTY, real rich, real menu (NOT in CI)
│   ├── test_plugin_source.py   # AST check: no module-level core imports, no run_writer in action.py
│   ├── test_plugin_safety.py   # Rich unavailable: ui/wizard raise GuardrailError, not SystemExit
│   ├── test_backup.py          # backup_db + restore drill
│   ├── test_restore_drill.py   # Snapshot → corrupt → restore roundtrip
│   ├── test_script.py          # SCOURGIFY_SCRIPT scripting (in tests, also shell use)
│   ├── test_paths.py           # Paths resolve (user_dir, data_dir, etc.)
│   ├── test_overrides.py       # Overrides dir + formats
│   ├── test_booktext.py        # Text extraction + timeout
│   ├── test_books_cli.py       # --books spec parsing
│   ├── fixture_db.py           # Creates throwaway metadata.db for tests (custom columns + data)
│   ├── smoke_calibre.py        # Manual: `calibre-debug -e tests/smoke_calibre.py` (read-only)
│   └── conftest.py             # pytest fixtures (if any)
├── data/                        # Gitignored user data (archive/proposal/backups)
│   ├── ao3_build/              # Intermediates for AO3 layer generation
│   ├── rejects.csv             # Reject log from --step reviews
│   ├── classify_proposal.csv   # Current proposal (added_tags, proposed_new per book)
│   ├── classify_proposal_applied_<ts>.csv  # Archives after --apply
│   ├── classify_newtags_ranked.csv  # Aggregated proposed_new for review
│   ├── classify_failures.csv   # Failed books (engine errors)
│   ├── promote_review.csv      # Adjudication verdicts (advocate/skeptic)
│   ├── promote_ledger.csv      # Applied promote verdicts (for backfill)
│   ├── promote_aliases.csv     # Synonym snaps (candidate → target)
│   ├── synopsis_failures.csv   # Books whose synopsis pass failed (keeps queue finite)
│   ├── edits.jsonl             # Edit log: run records + per-op lines + conflict detection
│   ├── backups/
│   │   ├── ff_20260828T120000.db  # Auto-snapshots before every write
│   │   └── ff_20260828T120030.db  # (kept: last 20 or under budget/byte limit)
│   └── ao3_vocab.csv           # Per-library AO3 canonical freeforms (for reference in new-tag checks)
├── build_ao3_layer.py          # Maintainer: regenerate defaults/ao3/ from OTW dump + LLM clustering
├── build_classify_seed.py      # Maintainer: regenerate defaults/classify_vocab_ao3.txt from data/ao3_vocab.csv
├── build_defaults.py           # Maintainer: merge hand-curated defaults/ from review-map CSVs in data/
├── build_plugin.py             # Build script: uv run build_plugin.py → dist/scourgify-plugin-<version>.zip
├── ao3_import.py               # One-time: import AO3 canonical vocab into data/ao3_vocab.csv
├── pyproject.toml              # hatchling config + version source
├── CLAUDE.md                   # Architecture + lifecycle documentation (this file reads it)
├── README.md                   # User-facing overview
├── docs/                        # User documentation
│   └── superpowers/            # Implementation specs
├── attic/                       # Original single-purpose pipeline (provenance only)
├── .github/workflows/publish.yml  # CI: TestPyPI on develop, PyPI on tag
├── .pytest_cache/              # pytest artifacts
├── .venv/                       # Development virtualenv (uv)
└── dist/                        # Build artifacts (wheel + plugin zip)
```

## Directory Purposes

**`src/scourgify/`:**
- Purpose: The installable Python package (hatchling; wheels to PyPI)
- Contains: All runtime code (tools, core, UI, helpers)
- Key files:
  - Entry: `__init__.py` (version), `__main__.py` (→ cli.main), `cli.py` (dispatcher)
  - Tools: `wrangle.py`, `classify.py`, `staleness.py`, `synopsis.py`, `promote.py`
  - Core: `common.py`, `ops.py`, `_writer.py`
  - UI: `wizard.py`, `ui.py`, `report.py`
  - Data: `defaults/` (bundled), `artifacts.py`, `editlog.py`, `overrides.py`, `booktext.py`
- Shipped in wheel: Everything here + `defaults/` directory tree (read-only)

**`plugin/`:**
- Purpose: Calibre plugin (phase 4, read-only; user-installable .zip)
- Contains: Qt layer (action.py), settings (config.py), smoke test (selftest.py)
- Built by: `uv run build_plugin.py` → copies core into zip as top-level `scourgify/` package + zipimport marker
- Key constraint: No module-level core imports (all inside job functions); no subprocess/ThreadPoolExecutor; no run_writer

**`tests/`:**
- Purpose: Test suite (plain asserts, pytest-compatible, no framework dependencies)
- Contains: Unit tests (core, selection, plan, engines), integration tests (wizard flow, write path), safety checks (plugin source, rich unavailable)
- Run: `uv run pytest tests/` or per-file `uv run tests/test_*.py`
- Manual: `drive_wizard.py` (real PTY), `smoke_calibre.py` (under Calibre's Python)
- CI: runs on Python 3.10, 3.13, 3.14

**`data/`:**
- Purpose: User-owned (gitignored) working files and archives
- Contains:
  - Proposals: `classify_proposal.csv` (current), `classify_proposal_applied_<ts>.csv` (archives)
  - Rankings: `classify_newtags_ranked.csv` (aggregated from all classify runs for promotion)
  - Verdicts: `promote_review.csv`, `promote_ledger.csv`, `promote_aliases.csv` (snaps)
  - Failures: `classify_failures.csv`, `synopsis_failures.csv` (keeps queues finite)
  - Rejects: `rejects.csv` (from --step reviews)
  - Audit: `edits.jsonl` (immutable write log with run ids + conflict detection)
  - Backups: `backups/ff_*.db` (auto-snapshots before writes, pruned to last 20 or budget)
  - Reference: `ao3_vocab.csv` (per-library AO3 canonical freeforms, imported once)
  - Intermediates: `ao3_build/` (for AO3 layer generation, deleted after build)
- Never committed: These files are gitignored

**`defaults/`:**
- Purpose: Bundled read-only taxonomy (ships inside the wheel)
- Contains:
  - Generated: `ao3/` (universes.csv, characters.csv, tags.csv, genres.csv from OTW dump + LLM)
  - Curated: `fandoms.csv`, `characters.csv`, `genres_canon.csv`, `genres_allow.txt`, `fandom_blocklist.txt`
  - Rules: `decompose.csv`, `genres_split.csv`, `tropes.csv`, `junk.txt`, `ratings.txt`
  - Vocab: `classify_vocab.txt` (curated), `classify_vocab_ao3.txt` (generated, never hand-edit), `ao3_exceptions.txt`
- Build: Generated files built by `build_ao3_layer.py` (LLM batch workflow), never hand-edited
- User override: Entire layer can be overridden per-user via `overrides/` (same formats, user_dir location)

## Key File Locations

**Entry Points:**
- `src/scourgify/__main__.py`: `python -m scourgify` or installed `scourgify` command
- `src/scourgify/cli.py`: Argument dispatch logic
- `src/scourgify/wizard.py`: Bare `scourgify` command (interactive, rich required)
- `plugin/__init__.py`: Calibre plugin entry point (InterfaceActionBase)

**Configuration:**
- `pyproject.toml`: Version, dependencies, build config (hatchling)
- `src/scourgify/defaults/`: Bundled defaults (shipped in wheel)
- Per-user: `~/.config/scourgify/config.toml` (or `$SCOURGIFY_HOME/config.toml`)
- Per-user: `~/.config/scourgify/overrides/` (or `$SCOURGIFY_HOME/overrides/`)

**Core Logic:**
- `src/scourgify/common.py`: Library resolution, read-only sqlite, run_writer, op constructors
- `src/scourgify/ops.py`: Write executor (used by CLI and plugin)
- `src/scourgify/wrangle.py`: Normalization Plan pattern
- `src/scourgify/classify.py`: LLM tagging proposal + apply
- `src/scourgify/select.py`: Book selection logic (shared by wizard, classify, wrangle)
- `src/scourgify/editlog.py`: Write audit trail

**Testing:**
- `tests/fixture_db.py`: Creates throwaway metadata.db (used by many tests)
- `tests/test_*.py`: Individual test files (run by CI on 3.10, 3.13, 3.14)
- `tests/conftest.py`: pytest fixtures (if any)

## Naming Conventions

**Files:**
- Tools: lowercase, verb-noun (`wrangle.py`, `classify.py`, `promote.py`)
- Support: lowercase, noun (`common.py`, `ops.py`, `engines.py`, `artifacts.py`, `editlog.py`)
- UI: lowercase (`ui.py`, `report.py`, `wizard.py`)
- Build: lowercase, verb-noun (`build_ao3_layer.py`, `build_classify_seed.py`)
- Tests: `test_*.py` (pytest discovery)
- Single underscore prefix: Internal/private (`_writer.py`, `_step_walk`, `_lookup`)

**Directories:**
- Core package: `src/scourgify/`
- Plugin package: `plugin/`
- Tests: `tests/`
- Data: `data/` (gitignored)
- Shipped defaults: `src/scourgify/defaults/`
- Generated intermediates: `data/ao3_build/` (transient)
- Build outputs: `dist/` (wheel, plugin zip)

**Functions:**
- Public API: lowercase_snake_case (`load_config`, `run_writer`, `read_library`)
- Private: `_leading_underscore` (`_lookup`, `_step_walk`, `_populate_books`)
- Classes: PascalCase (`Plan`, `GuardrailError`, `Dashboard`)
- Op constructors: `op_*` (`op_set_field`, `op_create_column`, `op_stamp_now`)

**Variables:**
- Module globals (rare): `UPPERCASE` (`HERE`, `DEFAULTS`, `SPEND_GATE`, `TAG_SHRINK_FRACTION`)
- Config keys: `section[key]` (read from TOML)
- Short-lived: lowercase_snake_case (`con`, `cfg`, `m` for maps, `ops`, `rows`)

## Where to Add New Code

**New Normalization Rule:**
- Logic: Add to `defaults/tropes.csv` (route: tag/genre/character/fandom/drop)
  - Or `defaults/decompose.csv` (contextual compound)
  - Or `defaults/junk.txt` (exact or regex to drop)
- Per-user override: Add to `overrides/tropes.csv` (same format; survives upgrades)
- Test: Add to `tests/test_layers.py` or `tests/test_core.py` (transform behavior)

**New LLM Engine:**
- Add row to `ENGINES` dict in `src/scourgify/engines.py`
- Set `TRAITS`: `is_free`, `max_workers`, `out_tokens` (accounting for hidden thinking)
- Set `PRICING`: input $/1M tokens, output $/1M tokens
- Implement `_post_json` client call (or use existing if API-compatible)
- Set environment var in `ENGINE_ENV`
- Test: `tests/test_engines.py` (cost calculation, retry logic)
- Docs: Update CLAUDE.md and README

**New Output Format (Report):**
- Implementation: Add function to `src/scourgify/report.py` (table/tree/say)
- Rendering: Rich for TTY, plain fallback for CI
- Never hand-render in a tool (report.py owns the policy)
- Test: Interactive tests in `tests/test_wizard_flow.py` (if changing wizard) or via `drive_wizard.py`

**New Interactive Prompt:**
- Add function to `src/scourgify/ui.py` (`prompt`, `menu`, `checklist`, `ask_int`, `confirm`)
- Scripting support: Read via `common.script_next()` or `common.script_bool()`
- Test: Use `common.scripted_answers(...)` context manager in tests

**New Tool (Subcommand):**
- Module: `src/scourgify/<tool>.py` with `main()` function (argparse)
- Dispatcher: Add case in `src/scourgify/cli.py:_dispatch()`
- Pattern: Lazy import (keep --version cheap)
- Guards: All writes via `run_writer()` (auto-backup, edit log, wipe guard)
- Read: via `ro_connect()` (read-only sqlite)
- Test: Create `tests/test_<tool>.py`
- Docs: Add to CLAUDE.md "Maintenance loop"

**New Custom Column:**
- Creation: `ops.op_create_column(label, name, datatype, is_multiple)`
- Reading: `common.read_custom_column(con, label, multi=True)`
- Writing: `ops.op_set_field(field, values)` (same path as tags)
- Plugin: Do NOT create in-process (phase 4+ reads GUI would desync)
- Test: Add to `tests/fixture_db.py` if test needs it

**New Test:**
- File: Create `tests/test_<module>.py`
- Fixtures: Use `fixture_db.py` for a throwaway library (SQLite temporary database)
- Scripting: Use `common.scripted_answers(...)` for wizard interaction tests
- Mocking: Monkeypatch `engines._post_json` for LLM calls (no network in CI)
- CI: File is discovered by glob; runs on 3.10, 3.13, 3.14 automatically

## Special Directories

**`attic/`:**
- Purpose: Original single-purpose pipeline (preserved for provenance/reference)
- Generated: Predates scourgify's unified toolchain
- Status: Superseded (do not use; scourgify tools are the live implementation)
- Content: `apply_*.py`, `generate_*.py`, `dryrun.py`, `recover_xianxia.py`
- Committed: Yes (history)
- Read by: Developers only (for understanding the original approach)

**`graphify-out/`:**
- Purpose: Codebase knowledge graph output (from graphify skill)
- Generated: Auto-generated analysis
- Status: Not committed to repo logic (informational)
- Committed: No (gitignored or excluded)

**`.planning/`:**
- Purpose: GSD codebase mapping output (ARCHITECTURE.md, STRUCTURE.md, etc.)
- Generated: By `/gsd-map-codebase`
- Committed: Yes (working reference)

**`dist/`:**
- Purpose: Build artifacts
- Generated: `uv build` (wheel) and `uv run build_plugin.py` (plugin zip)
- Status: Gitignored
- Contents:
  - `scourgify-X.Y.Z-py3-none-any.whl` (installable via `uv tool install`)
  - `scourgify-plugin-X.Y.Z.zip` (Calibre plugin, user-installable)

---

*Structure analysis: 2026-08-28*
