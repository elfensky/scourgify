# Codebase Concerns

**Analysis Date:** 2026-08-28

## Performance Bottlenecks

**Apple engine single-threaded execution:**
- Problem: `--engine apple` is single-threaded by design; measured ~40 seconds per book on real library
- Files: `src/scourgify/engines.py` (line 34–39), `src/scourgify/synopsis.py` (line 385–387)
- Impact: Large scopes (hundreds of books) take hours; unacceptable for interactive use on full library
- Improvement path: Acceptable only for background sweeps; design synopsis pass for weeks-long execution; for interactive classify runs, must use cloud engines
- Mitigation: Default to cloud for classify, document apple as "free but slow"; wizard gates full-library classify behind confirmation

**Gemini hard-blocks mature content at 14% rate:**
- Problem: Gemini's PROHIBITED_CONTENT filter blocks ~1 in 7 books on fanfiction libraries
- Files: `src/scourgify/engines.py` (line 47–52), `src/scourgify/classify.py` logs to `classify_failures.csv`
- Impact: Measured 2026-07-26 at 7/50 (14%), not the ~1% initially claimed; only safety settings allow partial relaxation of HARM categories (non-configurable for PROHIBITED_CONTENT)
- Improvement path: Route blocked books to OpenAI or Claude; CLAUDE.md documents the recovery workflow
- Current mitigation: `ask_retry` classifies failures, user re-runs with `--engine openai` to recover; failures CSV is merged on each run (fixed in 0597bdc)

**Book-text extraction timeout for non-EPUB formats:**
- Problem: Non-EPUB files route through `ebook-convert` with 180-second timeout; wedged process kills the run
- Files: `src/scourgify/booktext.py` (line 19, 54)
- Impact: A single large PDF or MOBI can halt a whole synopsis batch
- Improvement path: Increase timeout with a flag, or pre-filter large files; measure real worst-case on library
- Current mitigation: Timeout is hard-coded; fallback to empty text (synopsis is skipped)

## Measured Parameters at Risk

**Synopsis CHUNK size and adequacy prompt ratio:**
- Problem: Both are measured, not chosen; they break if bundled model changes
- CHUNK (10,000 chars = ~2,300 tokens) measured 2026-08-23: real prose runs ~4.3 chars/token; 18,000 chars fail (4,165 tokens against 4,096 limit)
- JUDGE_P phrasing measured 2026-08-23: "is this ABOUT the story?" keeps 12/20, vs "is this GOOD?" rejected 12/13; prompt change would rewrite thousands of good descriptions
- Files: `src/scourgify/synopsis.py` (line 46–71)
- Impact: If Apple updates bundled model or context size changes, synopsis quality degrades or pass becomes impossibly slow
- Fix approach: Re-measure both on any model update; keep measured values in source comments; add regression test on fixture library

**Output token estimates per engine:**
- Problem: `engines.TRAITS['out_tokens']` drives cost estimates; measured against real library but can drift
- Files: `src/scourgify/engines.py` (line 24–26, 47)
- Gemini: ~50 visible + ~1,061 hidden THINKING tokens billed as output (measured 2026-07-30)
- Impact: Cost estimates can be wrong by 5–10x if a reasoning model's thinking overhead changes
- Fix approach: Re-measure on each engine update; document in TRAITS where/when measured

## Fragile Areas

**calibre_open() detection can fail silently:**
- Problem: Uses pgrep → ps; if neither binary exists, fails CLOSED (reports "open") rather than let a write race
- Files: `src/scourgify/common.py` (line 338–365)
- Issue 1: When running inside Calibre plugin, `pgrep -fl calibre` lists only workers, not the GUI; fixed 2026-08-05 by checking `calibre.gui2` in `sys.modules` first
- Issue 2: If both pgrep and ps are missing, guard assumes Calibre is open (line 365); no way to override
- Current mitigation: Detection now checks `sys.modules` first; fallback is fail-closed
- Fix approach: Document the assumption; provide a `--bypass-open-check` flag for systems without pgrep/ps (dangerous but needed for some setups)

**Wipe guard thresholds are arbitrary:**
- Problem: Aborts if >90% of a ≥100-book column would be cleared; `--force` bypasses
- Files: `src/scourgify/common.py` (line 404–405, 474–490)
- Threshold (90% / 100 books) is coarse; legitimate change-sets (e.g., narrowing from 7,949 books to 50) can trigger it
- Impact: `Plan.restrict(ids)` narrows the write set after the full compute, so a scoped run's shrink_floor can be low (~195 assignments on a 50-book scope); the 25% floor works, but the blanket 100-book cutoff may over-guard
- Risk: Over-reliance on `--force` erodes the guard's effectiveness; operators may learn to bypass it

**FanFicFare Comments "New Only" guard is imperfect:**
- Problem: Synopsis pass refuses to start unless FFF's Comments → "New Only" is on, to prevent overwrite-on-fetch
- Files: `src/scourgify/synopsis.py` (line 124–138), `src/scourgify/setup.py` (comments_protected)
- Mitigation is detectable: `setup.comments_protected()` reads `std_cols_newonly` out of FFF prefs blob in library db
- Risk: If FFF updates and changes the pref location or format, detection silently fails; run proceeds, descriptions get clobbered
- Fix approach: Add explicit confirmation `scourgify setup` if pref cannot be verified; test against multiple FFF versions

**Three-state verdict() leaves books in queue indefinitely:**
- Problem: `verdict()` deliberately returns '' (unreadable) to avoid guessing "keep" (stamps bad blurb forever) or "generate" (overwrites good prose)
- Files: `src/scourgify/synopsis.py` (line 111–121)
- Impact: An unparseable answer (model hallucination, encoding error) returns no-op; book stays in queue forever on that engine
- Current mitigation: `ask_retry` captures the raw answer; user can inspect `synopsis_failures.csv` and re-run with `--engine openai`
- Risk: Silent failure loop if one engine is default and always fails in same way

**TOML reader is custom-coded:**
- Problem: Calibre's bundled Python has no tomllib; `common.py` has a minimal TOML reader
- Files: `src/scourgify/common.py` (no tomllib import)
- Risk: Parser edge cases (nested tables, complex escapes) may not be handled; quotes to allow `#` in values are a known quirk
- Mitigation: `load_config()` reads the standard-shaped config.toml; hand-edited files can break it
- Fix approach: Add tests for all config shapes used in the wild; document format constraints

## Plugin Architecture Constraints

**Calibre's bundled Python has empty site-packages (hard constraint):**
- Problem: Plugin code must import with only stdlib + Calibre's own APIs; `rich` is confined to wizard/ui/report
- Files: `src/scourgify/*.py` (all core modules), `plugin/*.py`
- Enforcement: `tests/test_plugin_safety.py` AST-checks that no job-reachable module imports rich, ui, wizard, report
- Risk: Any future dependency (even optional) breaks the plugin; core must stay ultra-minimal
- Impact: Affects every new tool or feature — third-party libs are out

**Threading via Calibre's ThreadedJob is mandatory:**
- Problem: Any full-library operation on the GUI thread freezes Calibre; plugin architecture forces all work through jobs
- Files: `plugin/action.py` (the one ThreadedJob call site), `plugin/__init__.py`
- Constraint: No `run_writer()` calls from inside the GUI (would shell out to a second process against an open library); must use `common.write_ops(api, ops)` instead
- Risk: Violating this silently produces data corruption or locks; `tests/test_plugin_source.py` AST-checks the plugin source for forbidden patterns
- Fix approach: Enforcement is structural (AST checks); document in CLAUDE.md as non-negotiable

**GuardrailError must never become SystemExit in job-reachable code:**
- Problem: Calibre's ThreadedJob catches only `Exception`, not `SystemExit`; a SystemExit in a job silently kills the worker thread with no error dialog
- Files: All core modules; enforcement in `tests/test_plugin_safety.py`
- Consequence: CLI exemptions for `run_writer()` and `rollback_cmd()` — they are never reached from a job
- Risk: If a future refactor moves CLI-only code into a shared path, silent thread death
- Mitigation: All job-reachable modules tested to raise GuardrailError only

**JSONConfig chmod 0600 may fail on some systems:**
- Problem: Plugin stores API keys in Calibre's JSONConfig; code chmod's the file after writing to lock it down
- Files: `plugin/config.py` (line 216)
- Risk: chmod can raise OSError on systems with ACLs or unusual permission models (Windows via WSL, some network mounts)
- Current mitigation: `except` passes silently (line 218); keys stay world-readable if chmod fails
- Fix approach: Log the failure; suggest manual chmod or key location verification

**Key priority: environment WINS over stored (reverse of library path):**
- Problem: Deliberately opposite direction from library path (env is user config, library path is host fact)
- Files: `src/scourgify/engines.py` (line 164–178)
- Risk: Confusing if not documented; scripts that set env vars expect them to take effect, but stored value would override them
- Impact: Good for CLI, but plugin settings UI can be surprising
- Fix approach: Document in plugin settings widget as "environment key wins if set"

## Known Issues (Already Fixed)

All documented here for prevention; fixes already in tree.

**Junk rules tested against source, not alias target (fixed 65dc0f3):**
- Example: `X → Y` with `Y` in `junk.txt` kept `Y` on pass 1, dropped on pass 2
- Impact: 3,862 real mappings affected; `apply --apply` was not a fixed point
- Test: `test_trope_fold_target_that_is_junk_is_dropped_in_one_pass`

**Route `drop` had no code path (fixed 3699028):**
- Example: `X;X;drop` fell through to default, re-added as tag
- Cause: 43 hand-written override rules in user's `overrides/tropes.csv`; no production code path for them
- Impact: 91 assignments across 89 books stayed in library despite explicit drop rule
- Test: `test_trope_route_drop_beats_its_own_rename_target`

**Trope chain resolution lost terminal's route (fixed 0597bdc):**
- Example: `House Targaryen → tag` on pass 1, → `#fandoms` on pass 2 (depending which spelling started)
- Impact: 2,160 rules changed route after fix, but no books (purely preventive)
- Test: Simulated against live library before commit

**classify_failures.csv never cleared (fixed 0597bdc):**
- Problem: Blocked books listed forever; re-running on different engine doesn't clear old failures
- Fix: `merge_failures()` now applied on every run (only re-state if failed again)
- Test: Live verification with seeded log

**backfill and wrangle fought forever (fixed 0597bdc):**
- Problem: backfill proposed tags already in structured columns; wrangle stripped them as redundant; backfill added them back
- Fix: `backfill_drop_redundant()` filters proposed tags against existing `#genres`/`#characters`/`#relationships`
- Impact: Most serious defect of 2026-07-26 full-library test; only appeared after earlier fixes reached fixed point
- Test: Reproduced live on 4 books; verified after fix

## Security Considerations

**API keys stored in plaintext JSONConfig:**
- Risk: Calibre's JSONConfig is user-readable without proper chmod; plugin code attempts chmod 0600 but it can fail
- Mitigation: chmod 0600 on write; keys also injectable via environment (env wins over stored)
- Best practice: Document that users should use env vars in CI/server deployments

**No content filtering for mature work:**
- Risk: Classify can refuse mature content (Gemini 14%) by design, but other engines have different limits
- Impact: User must be aware that `--engine openai` can also refuse some books (documented, but not enforced by UI)
- Mitigation: Log failures with reason; document per-engine refusal rates

**Library path injection (plugin context only):**
- Risk: Plugin resolves library via injected path from Calibre GUI, not `$CALIBRE_LIBRARY`
- Mitigation: `common.set_library(path)` is the one seam; environment never consulted in plugin
- Impact: Correct isolation (library path is host fact, not user config)

## Scaling Limits

**Backup retention: last 20 snapshots or BACKUP_BUDGET:**
- Files: `src/scourgify/common.py` (line 380–397)
- Default: 20 backups, up to ~10GB total (BACKUP_BUDGET)
- Risk: Large library (metadata.db > 500MB) could hit budget quickly; older snapshots pruned
- Mitigation: Documented in `rollback` help; users can manually prune `data/backups/`
- Improvement: Add flag to configure retention

**Book-text extraction capped at EXTRACT = 2M:**
- Files: `src/scourgify/booktext.py` (line 53), `src/scourgify/synopsis.py` (line 53)
- Risk: Huge ebook (megafic, 2M+ char) silently truncated; synopsis only reads first part
- Mitigation: Extract still pulls 6,000 chars from truncated text; synopsis accounts for it
- Impact: Rare (most fanfic < 1M chars), but edge case exists

**Concurrency via ThreadPoolExecutor capped by engine traits:**
- Files: `src/scourgify/engines.py` (line 65–67)
- Apple: forced to 1 worker (single-threaded subprocess)
- Cloud engines: default `--workers 4`, user-configurable
- Risk: No queue depth management; rapid-fire requests can exhaust API quota
- Mitigation: `ask_retry` with backoff handles quota (429); user must size `--workers` for API limits

## Test Coverage Gaps

**Plugin safety enforcement not at runtime:**
- What's not tested: AST check runs at test time; runtime violations (plugin that imports rich at startup) not caught until module load
- Files: `tests/test_plugin_source.py` (AST only)
- Risk: A subtle bug in the build process could slip forbidden code into dist
- Fix: Add runtime smoke test that loads the plugin zip and imports key modules

**Calibre compatibility not tested:**
- What's not tested: Plugin code never runs against real Calibre outside manual smoke-test
- Files: `tests/smoke_calibre.py` (manual pre-release check, not in CI)
- Risk: Calibre 9.11 → 9.12 API change silently breaks plugin
- Fix: Run smoke_calibre.py in CI (requires Calibre installation in CI image)

**CHUNK and JUDGE_P measurements not regression-tested:**
- What's not tested: If the prompt or model changes, measured values may be stale; no test catches drift
- Files: `src/scourgify/synopsis.py` (constants with measured values)
- Risk: Next Calibre update or model change silently degrades synopsis quality or speed
- Fix: Add a regression test that measures CHUNK adequacy on a fixture EPUB; flag if throughput drifts >20%

**Windows support not tested:**
- What's not tested: Code uses `/tmp` paths, `pgrep`, `ps`, Calibre-specific Unix assumptions
- Files: `src/scourgify/common.py` (backups_dir uses XDG_CONFIG_HOME), `src/scourgify/booktext.py` (ebook-convert path)
- Risk: Plugin will not work on Windows without rework
- Status: CLAUDE.md states "mac + Linux; no Windows" — intentional, but not documented in code

## Dependency & Version Risks

**Python 3.14 requirement for Calibre plugin:**
- Problem: Calibre 9.11 bundles Python 3.14.6; plugin code imports under it
- Files: All plugin code
- CI status: Tests run 3.10 / 3.13; 3.14 not in matrix yet
- Risk: 3.14 can break code (e.g., deprecated stdlib removals); CI doesn't catch it
- Fix: Add 3.14 to CI matrix before plugin ships

**Custom TOML reader lacks edge-case handling:**
- Problem: Minimal parser; complex TOML (arrays, inline tables, multiline strings) will break
- Files: `src/scourgify/common.py` (load_config implementation)
- Risk: Hand-edited config.toml with nested sections will silently produce wrong results
- Fix: Add stricter validation; document supported config shape; add test fixtures for all real configs

**ebook-convert availability varies by platform:**
- Problem: Non-EPUB text extraction depends on `ebook-convert` from Calibre
- Files: `src/scourgify/booktext.py` (line 54)
- Risk: ebook-convert not in PATH, or timeout insufficient for some formats
- Mitigation: Fallback to empty text; synopsis is skipped
- Fix: Pre-check availability; warn user if non-EPUB books cannot be processed

## Missing Features & Roadmap Debt

**Calibre plugin is incomplete (as of 2026-08-28):**
- Status: Phase 4 (read-only skeleton) not started; phase 5 (settings) and beyond not designed
- Planned phases:
  - Phase 1: in-process write path (`common.write_ops()` working)
  - Phase 2: plugin safety & CI (3.14 matrix, smoke tests)
  - Phase 3: read-only UI (scope picker, preview, no writes)
  - Phase 4: writable jobs (full workflow in Calibre)
  - Phase 5: settings widget (key management, engine picker)
  - Phase 6: resource bundling (`DEFAULTS` seam for ship data)
- Files: `plugin/`, `docs/superpowers/plans/2026-08-06-calibre-plugin.md`
- Blockers: Phase 1 seam complete; phases 2–6 design and implementation pending

**DEFAULTS resource seam for plugin (phase 6):**
- Problem: Plugin cannot read `common.HERE` (points inside zip); bundled data inaccessible
- Files: `src/scourgify/common.py` (load_maps refuses to open empty maps from zip)
- Risk: Plugin cannot load fandoms.csv, tropes.csv, etc. without redesign
- Fix: Implement a `load_resource()` seam; use zipfile internals to read from inside the plugin zip

**No Windows support:**
- Problem: Paths, `pgrep`, `ps`, XDG_CONFIG_HOME, `ebook-convert` all Unix-centric
- Status: Intentional per CLAUDE.md; not blocking but limits user base
- Fix: Requires refactor of path handling, process detection, fallbacks for missing tools

---

*Concerns audit: 2026-08-28*
