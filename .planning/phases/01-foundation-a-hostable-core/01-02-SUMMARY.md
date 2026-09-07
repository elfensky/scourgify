---
phase: 01-foundation-a-hostable-core
plan: 02
subsystem: infra
tags: [ci, github-actions, windows, calibre, cross-platform, encoding, sqlite]

# Dependency graph
requires:
  - phase: 01-01
    provides: "common.data_dir() library-uuid scoping, common.user_dir() Windows branch (both proven live on Windows by this plan's lane)"
provides:
  - "A blocking windows-latest test-windows job running the whole plain-assert suite on Python 3.14"
  - "smoke-calibre-windows and smoke-calibre-linux jobs proving the core imports/reads under Calibre's own bundled interpreter, against a pinned official installer"
  - "The settled, live-verified calibre-debug.exe location on windows-latest (C:\\Program Files\\Calibre2\\calibre-debug.exe, candidate #1 of the stated priority order)"
  - "Four real cross-platform defects in src/scourgify/ and tests/, found and fixed on this lane's first real runs, invisible on macOS"
  - "A widened open()-encoding regression guard covering both src/scourgify/*.py and tests/*.py"
affects: [01-03, 01-04, 01-05, 01-06]

# Actuals (#2632)
actuals:
  tokens: 26628
  tasks: 3
  commits: 11

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "contextlib.closing(ro_connect()) as the unconditional-close idiom for every read-only sqlite connection that can be skipped past by a raise"
    - "cli.main() as the one process-stream-reconfiguration point (UTF-8 stdout/stderr on Windows), kept out of report.py because report.py runs inside Calibre jobs too"
    - "AST-based (not regex/text) source scanning for the open()-encoding regression guard, so a docstring or f-string containing the literal substring open( can never be a false positive"

key-files:
  created: []
  modified:
    - .github/workflows/ci.yml
    - src/scourgify/_writer.py
    - src/scourgify/artifacts.py
    - src/scourgify/booktext.py
    - src/scourgify/classify.py
    - src/scourgify/cli.py
    - src/scourgify/common.py
    - src/scourgify/editlog.py
    - src/scourgify/overrides.py
    - src/scourgify/promote.py
    - src/scourgify/select.py
    - src/scourgify/setup.py
    - src/scourgify/staleness.py
    - src/scourgify/synopsis.py
    - src/scourgify/wizard.py
    - src/scourgify/wrangle.py
    - tests/drive_wizard.py
    - tests/test_artifacts.py
    - tests/test_backup.py
    - tests/test_books_cli.py
    - tests/test_cli.py
    - tests/test_core.py
    - tests/test_editlog.py
    - tests/test_layers.py
    - tests/test_overrides.py
    - tests/test_paths.py
    - tests/test_plugin_safety.py
    - tests/test_plugin_source.py
    - tests/test_promote.py
    - tests/test_selection.py
    - tests/test_wizard_flow.py

key-decisions:
  - "Fix 6 design decision (developer-directed): force UTF-8 stdout/stderr in cli.main() on Windows, errors='replace'. REJECTED alternative: stripping report.py's non-ASCII glyphs (−, →, ·, ✓, ⚠, ✗, box-drawing) to avoid the encoding problem entirely — rejected because report.py is the ONE owner of render policy and degrading it on every platform to accommodate one legacy console is the wrong trade."
  - "The fix for console encoding goes in cli.py, not report.py: report.py is imported by tools that also run inside Calibre jobs, and reconfiguring the host process's own stdout/stderr as an import-time side effect would mutate Calibre's own streams — exactly what tests/test_plugin_safety.py exists to prevent. ci.yml's test-windows job also carries PYTHONIOENCODING=utf-8, but that covers the TEST HARNESS only (tests call wrangle.main() etc. directly, bypassing cli.main()) — it is not the user-facing fix."
  - "The open()-encoding regression guard (tests/test_artifacts.py) was rewritten from a paren-balanced regex to a proper ast.walk() Call-node scan when its scope widened from src/scourgify/ to include tests/ — a regex would have flagged the guard's own docstring and f-string message (both contain the literal substring 'open(') as false positives; AST sees a string constant and an f-string, never a Call to open."
  - "Two Windows CI failures turned out to be test-authoring gaps, not production bugs, and are recorded as such rather than folded into the 'core defect' count: test_user_dir_default_is_dot_config (neutralized SCOURGIFY_HOME/XDG_CONFIG_HOME but not APPDATA, so a real Windows host correctly took the %APPDATA% branch per D-05 and the POSIX-only assertion failed) and test_set_library_redirects_the_core...'s db_path() comparison (compared an os.path.join() result to a hardcoded forward-slash literal). Both runs PROVED the production seams (user_dir(), set_library(), db_path()) correct rather than finding a bug in them."

requirements-completed: [XPLAT-03]

# XPLAT-01 is HALF proven by this plan, not fully — see the objective/must_haves note below and
# the Next Phase Readiness section. Not listed in requirements-completed because the "plugin
# loads" half remains open, deferred to Phase 6 (XPLAT-05).

coverage:
  - id: D1
    description: "A blocking windows-latest job (test-windows) runs the whole plain-assert suite on Python 3.14 on every push/PR to develop and main, including plan 01-03's platform-gate tests in tests/test_engines.py on a natively win32 host"
    requirement: "XPLAT-03"
    verification:
      - kind: e2e
        ref: "GitHub Actions run 34150694155, job test-windows"
        status: pass
    human_judgment: false
  - id: D2
    description: "smoke-calibre-windows and smoke-calibre-linux both run tests/smoke_calibre.py through calibre-debug -e against a Calibre 9.11.0 installed from the pinned official installer, proving the core imports and reads under Calibre's own bundled Python -- the core-imports half of XPLAT-01"
    requirement: "XPLAT-01"
    verification:
      - kind: e2e
        ref: "GitHub Actions run 34150694155, jobs smoke-calibre-windows and smoke-calibre-linux"
        status: pass
    human_judgment: true
    rationale: "smoke-calibre-windows/linux prove the core-imports half of XPLAT-01 on this run; the 'plugin loads' half is an explicit, stated deferral to Phase 6 (XPLAT-05) -- no plugin-side code exists yet and a headless runner cannot load a Qt InterfaceAction into a live Calibre GUI. A human (the developer, at Phase 6) must confirm that deferral is still the right call before XPLAT-01 is marked fully complete."
  - id: D3
    description: "The Calibre version is pinned exactly once, at workflow level (CALIBRE_VERSION: \"9.11.0\"), and the literal 9.11.0 appears nowhere else in ci.yml; both smoke jobs read it"
    requirement: "XPLAT-03"
    verification:
      - kind: unit
        ref: "python -c check: re.search(CALIBRE_VERSION literal) and t.count('9.11.0') == 1, run against ci.yml on disk"
        status: pass
    human_judgment: false
  - id: D4
    description: "The Windows smoke job resolves calibre-debug.exe by a stated priority order (conventional install dir -> PATH -> bounded Program Files search) and the location is now SETTLED by a live run, closing the STATE.md blocker"
    requirement: "XPLAT-03"
    verification:
      - kind: e2e
        ref: "GitHub Actions run 34150694155, job smoke-calibre-windows, step 'Resolve calibre-debug.exe' -- resolved via candidate #1"
        status: pass
    human_judgment: false
  - id: D5
    description: "Four real cross-platform defects in src/scourgify/ and tests/ were found and fixed by this lane's own first real runs, invisible on macOS: cp1252 file-read encoding (src/), an unclosed sqlite read connection breaking Windows temp-dir cleanup, console output encoding through rich's legacy Windows console renderer, and cp1252 file-read encoding (tests/)"
    requirement: null
    verification:
      - kind: unit
        ref: "tests/test_artifacts.py#test_ao3_defaults_decode_as_utf8_not_the_platform_locale_default"
        status: pass
      - kind: unit
        ref: "tests/test_books_cli.py#test_wrangle_apply_books_empty_closes_its_read_connection"
        status: pass
      - kind: unit
        ref: "tests/test_cli.py#test_report_glyphs_survive_a_reconfigured_cp1252_stream"
        status: pass
      - kind: e2e
        ref: "GitHub Actions run 34150694155, job test-windows (all four defect classes previously crashed this job; none recur)"
        status: pass
    human_judgment: false
  - id: D6
    description: "The three new CI jobs are made REQUIRED status checks on main's branch protection"
    requirement: null
    verification: []
    human_judgment: true
    rationale: "Deliberately NOT done. The developer's standing instruction is that required status checks are added only after the lane is green AND on a separate confirmation they have not yet given -- this is an outstanding human action, recorded below with the exact command."
duration: ~2h 3min
completed: 2026-09-07
status: complete
---

# Phase 1 Plan 2: Foundation — a hostable core Summary

**A blocking `windows-latest` core-tests job and two `calibre-debug` smoke jobs (Windows + Linux) went from zero to fully green in `.github/workflows/ci.yml`, and along the way this lane found and fixed four real cross-platform defects — a cp1252 file-encoding bug, an unclosed-sqlite-connection Windows file lock, a rich console-encoding crash, and a second cp1252 encoding site — plus two latent test-authoring gaps and three CI system-dependency gaps, all invisible on macOS until this lane's own first real runs surfaced them.**

## Performance

- **Duration:** ~2h 3min wall clock (first commit `f5f039e` 18:09 UTC+2 → last commit `4ae62c4` 20:12 UTC+2), across seven CI round trips
- **Tasks:** 2 of 2 `type="auto"` tasks completed as planned; Task 3 (`checkpoint:human-verify`) resolved interactively with the coordinator across the seven rounds below
- **Files modified:** 31 (1 CI workflow, 15 production modules, 15 test files)
- **Commits:** 11 task/fix commits (this SUMMARY is committed separately, per protocol)

## Accomplishments

- `.github/workflows/ci.yml` gained three new jobs: `test-windows` (blocking, `windows-latest`, Python 3.14 only, runs the whole `tests/test_*.py` glob including plan 01-03's platform-gate tests), `smoke-calibre-windows` and `smoke-calibre-linux` (both run `tests/smoke_calibre.py` through `calibre-debug -e` against a Calibre 9.11.0 installed from the pinned official installer). `CALIBRE_VERSION: "9.11.0"` is defined exactly once, at workflow level; the literal `9.11.0` appears nowhere else in the file.
- **The previously-unverified `calibre-debug.exe` location is now settled**: `smoke-calibre-windows` passed on its very first attempt, resolving via candidate #1 of the stated priority order — `C:\Program Files\Calibre2\calibre-debug.exe` — printing `calibre-debug.exe (calibre 9.11)`. The PATH and bounded-Program-Files fallbacks were never needed. This closes the STATE.md blocker recorded after plan 01-01.
- `smoke-calibre-linux` installs Calibre unprivileged (`install_dir=`/`isolated=y` into a workspace-local prefix under `$RUNNER_TEMP`, no root needed) and printed `calibre-debug (calibre 9.11)`; the binary lands one level deeper than the prefix itself (`$PREFIX/calibre/calibre-debug`), settled after one wrong assumption (Fix 5).
- **Four real cross-platform defects were found and fixed, all invisible on macOS, all surfaced by this lane on its first real Windows runs** — the concrete evidence for why XPLAT-03 exists:
  1. **cp1252 file-read encoding, `src/scourgify/`** — `open()` with no `encoding=` resolves the platform locale default (UTF-8 on macOS/Linux, cp1252 on Windows). The bundled AO3 taxonomy CSVs are UTF-8 with bytes (e.g. `0x90`) cp1252 cannot decode; `wrangle.load_maps()` crashed with `UnicodeDecodeError` reading its own shipped defaults. Fixed by adding `encoding="utf-8"` to 26 text-mode `open()` call sites across 10 files.
  2. **Unclosed sqlite read connection, Windows file lock** — `wrangle.read_library()` and roughly a dozen sibling `ro_connect()` call sites across `classify.py`, `staleness.py`, `promote.py`, `setup.py`, `overrides.py`, `synopsis.py` and `wizard.py` either never closed their connection or closed it only on the happy path. CPython's refcounting closes the underlying file immediately on macOS/Linux, so this was invisible there; an explicitly-open `sqlite3.Connection` holds an OS-level lock on Windows that `tempfile.TemporaryDirectory()` cleanup cannot get past (`PermissionError: [WinError 32]`). Fixed by wrapping every applicable site in `with contextlib.closing(ro_connect()) as con:`.
  3. **Console output encoding via rich's legacy Windows console** — `report.table()`/`report.say()` render glyphs (`−`, `→`, `·`, `✓`, `⚠`, `✗`, box-drawing) that have no cp1252 mapping; Windows' console defaults to cp1252, and rich's legacy-Windows renderer crashed with `UnicodeEncodeError` writing them. Fixed in `cli.main()` (not `report.py` — see Decisions) by reconfiguring `sys.stdout`/`sys.stderr` to UTF-8 with `errors="replace"` on Windows only.
  4. **cp1252 file-read encoding, `tests/`** — the same class recurred in a bare `open()` inside `tests/test_layers.py`, outside the original `src/scourgify/` sweep. Fixed by adding `encoding="utf-8"` to 55 text-mode `open()` call sites across 13 test files, applied via an AST-based script (not regex) so no test's assertions, structure, or names changed.
- **Two latent test-authoring gaps were exposed by the same real Windows host, and both PROVED the production code correct rather than finding a bug in it**:
  5. `test_user_dir_default_is_dot_config` neutralized `SCOURGIFY_HOME`/`XDG_CONFIG_HOME` but not `APPDATA`; on a real Windows host `os.name` is genuinely `"nt"` and `APPDATA` is always set, so `common.user_dir()` correctly took the `%APPDATA%\scourgify` branch per plan 01-01's D-05 — the test's POSIX-only expectation was wrong, not the code. `test_user_dir_xdg_config_home` had the identical shape and got the same fix.
  6. `test_set_library_redirects_the_core_without_touching_the_environment` compared `common.db_path()` (built via `os.path.join()`) against a hardcoded forward-slash literal; on Windows `os.path.join` inserts a backslash, so the literal never matched. `common.db_path()`/`common.set_library()` are correct — every other assertion in the same test (injected path winning, `os.environ` untouched, fallback to `None`) already passed on Windows.
- **Three CI-configuration gaps** (not code defects) were found and fixed on `smoke-calibre-linux`: missing `libegl1`/`libopengl0` (the official Linux installer refuses outright without `libEGL.so.1`; `ubuntu-latest` carries no GL stack by default), a second missing library `libxcb-cursor0` found on the re-attempt, and the `CALIBRE_BIN` install path (the installer places binaries at `$install_dir/calibre/`, one level deeper than the workflow originally assumed).
- **Plan 01-01's entire `test_paths.py` migration suite — all 27 tests — ran green against a real Windows filesystem** in the final run, migrating a fixture `data/` tree under `C:\Users\RUNNER~1\AppData\Local\Temp\...` correctly: the first genuine proof of that work off macOS.
- The `open()`-encoding regression guard in `tests/test_artifacts.py` now scans **both** `src/scourgify/*.py` and `tests/*.py`, rewritten from a paren-balanced regex to a proper `ast.walk()` Call-node scan (the durable defense against this defect class recurring — see Decisions for why the regex would have broken on its own widened scope).

## Task Commits

Each task was committed atomically; the checkpoint (Task 3) produced the fix commits below across seven CI round trips, all authorized in real time by the coordinator:

1. **Task 1: Blocking windows-latest core-tests job on Python 3.14** - `f5f039e` (feat)
2. **Task 2: calibre-debug smoke jobs on windows-latest and ubuntu-latest** - `64e3a78` (feat)
3. **Task 3 checkpoint round 1 fixes** - `80ee828` (fix: GL libraries), `b2f396c` (fix: cp1252 in src/)
4. **Task 3 checkpoint round 2 fixes** - `9a2c9b3` (fix: libxcb-cursor0), `3f2a9fd` (fix: sqlite handle leak)
5. **Task 3 checkpoint round 3 fixes** - `e26cb57` (fix: CALIBRE_BIN path), `1a39f97` (fix: console encoding)
6. **Task 3 checkpoint round 4 fix** - `e6afca8` (fix: cp1252 in tests/, widened guard)
7. **Task 3 checkpoint round 5 fix** - `23de0e2` (fix: test_paths.py APPDATA neutralization)
8. **Task 3 checkpoint round 6 fix** - `4ae62c4` (fix: db_path() os.path.join comparison)
9. **Task 3 checkpoint round 7: run 34150694155, all six jobs green**

_No plan metadata commit yet — this SUMMARY.md is committed separately per the executor protocol._

## Files Created/Modified

- `.github/workflows/ci.yml` - three new jobs (`test-windows`, `smoke-calibre-windows`, `smoke-calibre-linux`), `CALIBRE_VERSION` pinned once, GL library install step, `CALIBRE_BIN` path fix, `PYTHONIOENCODING` env for the test harness
- `src/scourgify/cli.py` - `main()` reconfigures `sys.stdout`/`sys.stderr` to UTF-8 on Windows before dispatch
- `src/scourgify/{artifacts,booktext,classify,common,editlog,overrides,select,setup,_writer}.py` - `encoding="utf-8"` added to 26 text-mode `open()` calls
- `src/scourgify/{wrangle,classify,staleness,promote,setup,overrides,synopsis,wizard}.py` - every applicable `ro_connect()` call wrapped in `with contextlib.closing(ro_connect()) as con:`
- `tests/{drive_wizard,test_artifacts,test_backup,test_core,test_editlog,test_layers,test_overrides,test_paths,test_plugin_safety,test_plugin_source,test_promote,test_selection,test_wizard_flow}.py` - `encoding="utf-8"` added to 55 text-mode `open()` calls
- `tests/test_artifacts.py` - regression guard widened to `src/scourgify/*.py` + `tests/*.py`, rewritten to AST-based scanning
- `tests/test_books_cli.py` - regression test for the sqlite handle leak (captures the connection, asserts `.close()` was called explicitly, verified to fail without the fix)
- `tests/test_cli.py` - regression test for the console-encoding fix (simulated cp1252 stream via `io.TextIOWrapper`, verified to fail without the fix)
- `tests/test_paths.py` - `APPDATA=None` added to two `env(...)` calls whose assertions assumed the POSIX branch
- `tests/test_plugin_safety.py` - one assertion changed to compare against `os.path.join(...)` instead of a hardcoded forward-slash literal

## Decisions Made

- **Fix 6 (console encoding) — considered and rejected an alternative.** Stripping `report.py`'s non-ASCII glyphs (`−`, `→`, `·`, `✓`, `⚠`, `✗`, box-drawing characters) would have avoided the cp1252 crash entirely, but was rejected: `report.py` is the ONE owner of render policy, and degrading it on every platform to accommodate one legacy console is the wrong trade. Force-UTF-8 was chosen instead.
- **The console-encoding fix lives in `cli.py`, never `report.py`.** `report.py` is imported by tools that also run inside Calibre jobs (`plugin/action.py`'s `ThreadedJob` path); reconfiguring the host process's own stdout/stderr as an import-time side effect there would mutate Calibre's own streams — exactly the class of thing `tests/test_plugin_safety.py` exists to forbid. `cli.main()` is CLI-only; the plugin never enters it.
- **`ci.yml`'s `PYTHONIOENCODING: utf-8` on `test-windows` is NOT the same fix as `cli.py`'s reconfigure** — it covers the TEST HARNESS only, because the tests call `wrangle.main()` and friends directly, bypassing `cli.main()` entirely (a real user never does that). Without the env var, `test-windows` would still fail on the identical defect for a reason a real user would never hit; `cli.py`'s change is the actual user-facing fix.
- **The `open()`-encoding regression guard was rewritten from regex to AST** when its scope widened from `src/scourgify/` to include `tests/`: a paren-balanced regex would have flagged the guard's own docstring and its `f"...open({args})..."` message text (both contain the literal substring `open(`) as false positives the moment `tests/` was in scope. `ast.walk()` over `Call` nodes sees a string constant and an f-string, never a `Call` to `open`, so the widened guard cannot trip on its own source.
- **Two Windows-only test failures were test-authoring gaps, not production bugs**, and are recorded separately from the four core defects: `test_user_dir_default_is_dot_config`/`test_user_dir_xdg_config_home` (didn't neutralize `APPDATA`, so a real Windows host correctly took `user_dir()`'s `%APPDATA%` branch per D-05) and `test_set_library_redirects_the_core...`'s `db_path()` comparison (compared an `os.path.join()` result to a forward-slash literal). Both runs are evidence the underlying seams (`user_dir()`, `set_library()`, `db_path()`) are correct, not defects in them.

## Deviations from Plan

### Auto-fixed Issues (all approved in real time by the coordinator across seven CI round trips)

**1. [Rule 1 - Bug] cp1252 file-read encoding in `src/scourgify/`**
- **Found during:** Task 3, round 1 — `test-windows`'s first real run (34142905483)
- **Issue:** `open()` with no `encoding=` resolves the platform locale default; the bundled AO3 CSVs are UTF-8 and crashed `wrangle.load_maps()` with `UnicodeDecodeError` on Windows.
- **Fix:** `encoding="utf-8"` added to 26 text-mode `open()` calls across 10 files (`_writer.py`, `artifacts.py`, `booktext.py`, `classify.py`, `common.py`, `editlog.py`, `overrides.py`, `select.py`, `setup.py`, `wrangle.py`).
- **Files modified:** the 10 listed above, plus `tests/test_artifacts.py` (new regression test).
- **Verification:** `tests/test_artifacts.py#test_ao3_defaults_decode_as_utf8_not_the_platform_locale_default`; confirmed green on real Windows run 34144520181 onward.
- **Commit:** `b2f396c`

**2. [Rule 2/3 - CI config] Missing GL system libraries on `smoke-calibre-linux`**
- **Found during:** Task 3, round 1 — the official Linux installer refused outright without `libEGL.so.1`; `ubuntu-latest` carries no GL stack by default.
- **Fix:** `apt-get install libegl1 libopengl0` (later `libxcb-cursor0` added in round 3 when the installer named a second missing library).
- **Files modified:** `.github/workflows/ci.yml`.
- **Verification:** `smoke-calibre-linux: success` on run 34150694155.
- **Commits:** `80ee828`, `9a2c9b3`

**3. [Rule 1 - Bug] Unclosed sqlite read connection breaking Windows tempdir cleanup**
- **Found during:** Task 3, round 2 — `test-windows` run 34142905483 crashed with `PermissionError: [WinError 32]` on `metadata.db` during `tempfile.TemporaryDirectory()` cleanup.
- **Issue:** `wrangle.read_library()` never closed its `ro_connect()` connection on any path; sibling sites in `classify.py`, `staleness.py`, `promote.py`, `setup.py`, `overrides.py`, `synopsis.py`, `wizard.py` closed only on the happy path, leaking if an earlier call raised first. Invisible on macOS/Linux because CPython's refcounting closes the file immediately there; Windows holds an OS-level lock on an explicitly-open connection.
- **Fix:** every applicable site wrapped in `with contextlib.closing(ro_connect()) as con:`.
- **Files modified:** `wrangle.py`, `classify.py`, `staleness.py`, `promote.py`, `setup.py`, `overrides.py`, `synopsis.py`, `wizard.py`, plus `tests/test_books_cli.py` (regression test) and `tests/test_core.py` (a `FakeCon` test double gained the `close()` method the fix now requires).
- **Verification:** `tests/test_books_cli.py#test_wrangle_apply_books_empty_closes_its_read_connection`, verified to fail without the fix (a captured extra reference proves `.close()` was never called) and pass with it; no `WinError 32` in any subsequent run.
- **Commit:** `3f2a9fd`

**4. [Rule 3 - CI config] `smoke-calibre-linux`'s `CALIBRE_BIN` path was wrong**
- **Found during:** Task 3, round 3 — the installer's own log said "Installing to `$install_dir/calibre`", one level deeper than the workflow assumed.
- **Fix:** `CALIBRE_BIN=$PREFIX/calibre/calibre-debug`; also prints the actual tree (`ls -R`) into the install log on a future path mismatch, so it self-diagnoses.
- **Files modified:** `.github/workflows/ci.yml`.
- **Verification:** `smoke-calibre-linux: success` on run 34150694155, `calibre-debug (calibre 9.11)` printed.
- **Commit:** `e26cb57`

**5. [Rule 1 - Bug] Console output encoding crash via rich's legacy Windows console**
- **Found during:** Task 3, round 3 — `test-windows` run 34144520181 crashed with `UnicodeEncodeError` on `−` (U+2212) rendered through `report.table()`.
- **Fix:** `cli.main()` reconfigures `sys.stdout`/`sys.stderr` to UTF-8 (`errors="replace"`) on Windows before dispatch; `ci.yml`'s `test-windows` job also gets `PYTHONIOENCODING: utf-8` to cover the test harness (which bypasses `cli.main()`). Non-ASCII glyphs in `report.py` were considered for removal and rejected (see Decisions).
- **Files modified:** `src/scourgify/cli.py`, `.github/workflows/ci.yml`, `tests/test_cli.py` (regression test).
- **Verification:** `tests/test_cli.py#test_report_glyphs_survive_a_reconfigured_cp1252_stream`, verified to fail without the fix and pass with it; no `UnicodeEncodeError` in any subsequent run.
- **Commit:** `1a39f97`

**6. [Rule 1 - Bug] cp1252 file-read encoding recurred in `tests/`**
- **Found during:** Task 3, round 4 — `test-windows` run 34145323639 crashed on `tests/test_layers.py:61`'s own bare `open()`, outside the original `src/scourgify/` sweep.
- **Fix:** `encoding="utf-8"` added to 55 text-mode `open()` calls across 13 test files, applied via an AST-based script; the regression guard in `tests/test_artifacts.py` widened to scan `tests/*.py` too and rewritten to AST-based scanning (see Decisions).
- **Files modified:** `drive_wizard.py`, `test_artifacts.py`, `test_backup.py`, `test_core.py`, `test_editlog.py`, `test_layers.py`, `test_overrides.py`, `test_paths.py`, `test_plugin_safety.py`, `test_plugin_source.py`, `test_promote.py`, `test_selection.py`, `test_wizard_flow.py`.
- **Verification:** widened guard verified to catch the exact regression (reverted `test_layers.py`'s fix locally, confirmed the assertion fails, restored it); full local suite green.
- **Commit:** `e6afca8`

**7. [Test-authoring gap] `test_user_dir_default_is_dot_config`/`test_user_dir_xdg_config_home` assumed a POSIX branch**
- **Found during:** Task 3, round 5 — `test-windows` run 34150153653 failed `test_user_dir_default_is_dot_config`.
- **Issue:** neither test neutralized `APPDATA`; on a real Windows host `user_dir()` correctly takes the `%APPDATA%\scourgify` branch (D-05), which the tests' POSIX-only expectations never accounted for. Verified `common.user_dir()` is correct as specified — not a production defect.
- **Fix:** `APPDATA=None` added to both tests' `env(...)` calls.
- **Files modified:** `tests/test_paths.py`.
- **Verification:** all 27 `test_paths.py` tests green on real Windows run 34150431892 onward, including plan 01-01's full migration suite.
- **Commit:** `23de0e2`

**8. [Test-authoring gap] `test_set_library_redirects_the_core...` compared `db_path()` to a forward-slash literal**
- **Found during:** Task 3, round 6 — `test-windows` run 34150431892 failed this one assertion; every other assertion in the same test passed on Windows.
- **Issue:** `common.db_path()` builds via `os.path.join()`; on Windows that inserts a backslash, so the hardcoded all-forward-slash literal never matched. Verified `common.db_path()`/`common.set_library()` are correct — not a production defect.
- **Fix:** compare against `os.path.join("/tmp/from-the-gui", "metadata.db")` instead of the literal.
- **Files modified:** `tests/test_plugin_safety.py`.
- **Verification:** `test-windows: success` on run 34150694155 — all six jobs green.
- **Commit:** `4ae62c4`

---

**Total deviations:** 8 auto-fixed across seven CI round trips (4 Rule-1 core code defects, 3 Rule-2/3 CI-configuration gaps, 2 test-authoring gaps that proved the production code correct — counted here as one paired deviation each with its companion fix, 9 items total per the coordinator's accounting when the two `test_user_dir_*` fixes are counted separately).
**Impact on plan:** All auto-fixes were necessary for correctness (the four core defects would ship broken behavior on Windows) or for the lane itself to be trustworthy (the test-authoring gaps and CI-configuration gaps). No scope creep — every fix stayed inside `src/scourgify/`, `tests/`, or `.github/workflows/ci.yml`, and every fix beyond `ci.yml` itself was explicitly authorized as a Rule-1 deviation by the coordinator before being applied, exactly as the deviation protocol requires.

## Issues Encountered

None beyond the deviations documented above, all of which are fully resolved and verified green on the final run.

## Threat Flags

None. No new security-relevant surface was introduced — the only new "surface" (the installer download in the two smoke jobs) is exactly what the plan's own `threat_model` (T-01-07/08/09) already covers.

## User Setup Required

None — no external service configuration required.

## Outstanding Human Action (NOT done by this plan, and deliberately not attempted)

**Branch protection on `main` is unchanged.** The developer's standing instruction: required status checks are added to `main` only after the lane is green AND on a separate confirmation they have not yet given. The lane IS now green (run [34150694155](https://github.com/elfensky/scourgify/actions/runs/34150694155), all six jobs `success`), but adding the required-checks entries is a distinct action requiring explicit go-ahead.

When that confirmation is given, the action is:

```bash
gh api -X PATCH repos/elfensky/scourgify/branches/main/protection/required_status_checks \
  -f strict=true \
  -f 'contexts[]=test-windows' \
  -f 'contexts[]=smoke-calibre-windows' \
  -f 'contexts[]=smoke-calibre-linux'
```

(Adjust `strict`/merge with any existing required contexts already on the rule — check `gh api repos/elfensky/scourgify/branches/main/protection/required_status_checks` first to avoid clobbering `test` if it or anything else is already required.)

## Next Phase Readiness

- **XPLAT-03 is fully proven**: the `windows-latest` lane exists, is blocking, and is green (`test-windows`, `smoke-calibre-windows`, `smoke-calibre-linux` all `success` on run 34150694155).
- **XPLAT-01's split, restated as the plan's objective required**: the "every core module imports and reads under Windows Calibre's bundled Python 3.14" half is now PROVEN — `smoke-calibre-windows` ran `tests/smoke_calibre.py` through `calibre-debug -e` on a real Windows host and passed. The "the plugin loads" half remains an explicit, structural deferral to Phase 6 (XPLAT-05): no plugin-side code exists until Phase 2's write verbs and Phase 4's panel, and a headless GitHub runner cannot load a Qt `InterfaceAction` into a live Calibre GUI. `requirements-completed` above lists only `XPLAT-03`, not `XPLAT-01`, to keep this distinction visible to any goal-backward verifier.
- **This run history IS the evidence XPLAT-03 exists to produce**: four genuine cross-platform defects (two encoding classes, a resource-lifetime bug, and a console-rendering crash) were invisible on macOS through this entire milestone until a real Windows host ran the suite for the first time. Every later phase in this milestone adds more Qt/Windows-facing code; this lane now catches the next one on the introducing commit, not at a late handoff.
- Plan 01-01's `data_dir()`/`user_dir()`/migration work (D-01 through D-05) is now proven on a real Windows filesystem, not just monkeypatched unit tests — all 27 `test_paths.py` tests green on `windows-latest`.
- No blockers for plan 01-03. Branch protection (above) is the only outstanding item, and it is explicitly not this plan's decision to make.

## Self-Check: PASSED

- `.github/workflows/ci.yml`, `src/scourgify/cli.py`, `tests/test_books_cli.py` — all confirmed present on disk.
- All 11 task/fix commits (`f5f039e`, `64e3a78`, `80ee828`, `b2f396c`, `9a2c9b3`, `3f2a9fd`, `e26cb57`, `1a39f97`, `e6afca8`, `23de0e2`, `4ae62c4`) plus this SUMMARY's commit (`de11553`) confirmed present in `git log`.
- CI run [34150694155](https://github.com/elfensky/scourgify/actions/runs/34150694155): all six jobs `success` (`test (3.10)`, `test (3.13)`, `test (3.14)`, `test-windows`, `smoke-calibre-windows`, `smoke-calibre-linux`).
- Local gate green: `env -u CALIBRE_LIBRARY SCOURGIFY_HOME=$(mktemp -d) bash -c 'for t in tests/test_*.py; do uv run "$t" || exit 1; done'` — 25/25 files, exit 0, run immediately before the final push.

---
*Phase: 01-foundation-a-hostable-core*
*Completed: 2026-09-07*
