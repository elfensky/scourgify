---
phase: 01-foundation-a-hostable-core
plan: 03
subsystem: infra
tags: [zipimport, resource-bundling, calibre-plugin, cross-platform]

# Dependency graph
requires:
  - phase: 01-01
    provides: "common.data_dir() library-uuid scoping, common.user_dir() Windows branch — defaults_dir()'s cache is deliberately a GLOBAL sibling of data/, not per-library"
provides:
  - "common.defaults_dir() — the ONE resolver for every shipped read-only file the core opens at runtime (defaults/ including defaults/ao3/, classify_vocab*.txt, afm.swift)"
  - "wrangle.load_maps() and classify.load_vocab() read through defaults_dir(); load_vocab() now fails closed on an empty vocabulary"
  - "engines.Apple / engines.usable_engines() read afm/afm.swift through defaults_dir() (via engines._shipped_dir()), never common.HERE"
  - "engines.TRAITS platforms row: apple is darwin-only, gated in usable_engines() before the existing afm/swift probe, composing with it"
affects: [01-04, 01-05, 01-06]

# Actuals (#2632)
actuals:
  tokens: 10038
  tasks: 3
  commits: 3

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Resource resolver seam: a function (never an import-time constant) resolves to a real directory whether the package is a normal install or running from inside a zipimport archive, extracting once into a version-keyed cache on the zip path"
    - "Import-system-first path discovery: ask the module's own loader/spec for the archive path before ever walking the filesystem"
    - "Windows-safe cache install: unique sibling temp dir, marker written last inside it, single os.rename into place only when the target is absent, lost race silently discards the loser and reuses the warm cache — never a directory-replacing rename"

key-files:
  created:
    - tests/test_defaults_resource.py
  modified:
    - src/scourgify/common.py
    - src/scourgify/wrangle.py
    - src/scourgify/classify.py
    - src/scourgify/engines.py
    - tests/test_engines.py

key-decisions:
  - "afm.swift's shipped location, addressed via defaults_dir(): the extraction places defaults/* files under cache_root/defaults/ (so defaults_dir() can literally equal os.path.join(HERE, 'defaults') on a normal install, matching the plan's own acceptance criterion) and afm.swift as a SIBLING at cache_root/afm.swift (mirroring afm.swift's real on-disk position beside, not inside, src/scourgify/defaults/). engines._shipped_dir() = os.path.dirname(common.defaults_dir()) gives HERE on a normal install and the cache root inside the zip — one symmetric relationship, no second constant, and defaults_dir()'s literal contract (returns the defaults/ subdirectory) is never bent to accommodate afm.swift."
  - "overrides.py's `from scourgify.common import DEFAULTS as DEF` (used only by `scourgify overrides --master`, a maintainer-only, explicitly checkout-only write TARGET for regenerating the master defaults/ tree) is left unchanged. It is a WRITE destination, not a runtime READ of a shipped file, is never called from any Calibre-job-reachable code path, and is outside every must_have/acceptance criterion and file list this plan names — repointing it would be scope creep onto a maintainer tool this plan was never asked to touch."
  - "_core_version()'s cache key is populated only on the zip path, where build_plugin.py's scourgify/_plugin_version.py exists by construction; a wheel install falls through to importlib.metadata / __version__ / the literal 0.0.0+local, matching src/scourgify/__init__.py's own fallback chain, so a wheel and a zip can never share one cache directory by accident."
  - "Zip-slip defense compares os.path.realpath(dest) against os.path.realpath(temp_root) for every extracted member (both defaults/* and afm.swift), refusing with GuardrailError before any file is opened — covers both a `../` traversal name and a symlinked intermediate directory."

requirements-completed: [FOUND-01, XPLAT-02]

coverage:
  - id: D1
    description: "common.defaults_dir() returns HERE/defaults unchanged and extracts nothing on a normal install; inside a zip it extracts once into user_dir()/cache/<core version>/defaults/ and returns that — proved identical to the package defaults via a classify cost-estimate parity test (ROADMAP success criterion 1)"
    requirement: "FOUND-01"
    verification:
      - kind: unit
        ref: "tests/test_defaults_resource.py#test_normal_install_returns_the_package_defaults_and_extracts_nothing"
        status: pass
      - kind: unit
        ref: "tests/test_defaults_resource.py#test_zip_install_extracts_once_and_returns_the_cache"
        status: pass
      - kind: unit
        ref: "tests/test_defaults_resource.py#test_cost_estimate_is_identical_from_the_zip_and_from_the_package"
        status: pass
      - kind: integration
        ref: "tests/test_defaults_resource.py#test_a_real_zipimport_resolves_the_archive_and_the_cache (child-process real zipimport)"
        status: pass
    human_judgment: false
  - id: D2
    description: "A missing/unreadable defaults layer or an empty controlled vocabulary refuses with GuardrailError (never SystemExit, never a silently empty taxonomy) — covers load_maps' existing check plus load_vocab's newly-added fail-closed behavior"
    requirement: "FOUND-01"
    verification:
      - kind: unit
        ref: "tests/test_defaults_resource.py#test_a_zip_with_no_defaults_raises_guardrail"
        status: pass
      - kind: unit
        ref: "tests/test_defaults_resource.py#test_an_empty_vocabulary_refuses"
        status: pass
      - kind: unit
        ref: "tests/test_plugin_safety.py#test_no_job_reachable_code_raises_systemexit"
        status: pass
    human_judgment: false
  - id: D3
    description: "Extraction is Windows-safe and race-safe (unique sibling temp dir, marker written last, single os.rename only when the cache is absent, a lost race discards the loser and reuses the warm cache) and zip-slip-safe (every member's destination checked against the extraction root before it is opened)"
    requirement: "FOUND-01"
    verification:
      - kind: unit
        ref: "tests/test_defaults_resource.py#test_a_warm_cache_wins_a_lost_rename_race"
        status: pass
      - kind: unit
        ref: "tests/test_defaults_resource.py#test_a_member_escaping_the_cache_root_is_refused"
        status: pass
      - kind: unit
        ref: "tests/test_defaults_resource.py#test_second_call_does_not_re_extract"
        status: pass
    human_judgment: true
    rationale: "The rename-race test simulates the lost-race branch by stubbing os.rename in-process; it proves the code path is correct but has not been exercised by two genuinely concurrent OS processes racing a real filesystem, which is the scenario T-01-24 in the threat register describes."
  - id: D4
    description: "usable_engines() never returns apple off its platform (a TRAITS row lookup, never a name test), and the platform gate composes with — does not replace — the existing afm/swift runtime probe"
    requirement: "XPLAT-02"
    verification:
      - kind: unit
        ref: "tests/test_engines.py#test_apple_is_absent_off_its_platform"
        status: pass
      - kind: unit
        ref: "tests/test_engines.py#test_apple_is_still_absent_in_platform_without_a_toolchain"
        status: pass
      - kind: unit
        ref: "tests/test_engines.py#test_the_platform_gate_is_a_trait_not_a_name_test"
        status: pass
      - kind: unit
        ref: "tests/test_engines.py#test_cloud_engines_are_unaffected_by_the_platform_gate"
        status: pass
      - kind: e2e
        ref: "tests/test_engines.py#test_apple_is_absent_on_this_host_unless_it_is_darwin (unpatched sys.platform; will run on plan 01-02's windows-latest lane)"
        status: pass
    human_judgment: false
duration: ~45min
completed: 2026-09-07
status: complete
---

# Phase 1 Plan 3: Foundation — a hostable core Summary

**`common.defaults_dir()` is now the single resolver every shipped read-only file (defaults/, classify_vocab*.txt, afm.swift) reads through — correct from a normal install and from inside the Calibre plugin zip via a Windows-safe, zip-slip-safe, race-safe extract-once cache — and `usable_engines()` gates the on-device apple engine off its platform through a `TRAITS` row, never a name test.**

## Performance

- **Duration:** ~45 min
- **Tasks:** 3 of 3 completed
- **Files modified:** 6 (4 production modules, 2 test files — 1 test file newly created)
- **Commits:** 3

## Accomplishments

- `common.defaults_dir()`: on a normal install returns `HERE/defaults` unchanged, creating nothing on disk; inside the plugin zip, extracts every shipped read-only file once into `user_dir()/cache/<core version>/` (a version-keyed, library-independent cache — a version bump invalidates it and two libraries share one extraction) and returns the `defaults/` subdirectory of that cache.
- `common._archive_path()` asks the import system first (`zipimporter.archive` off the module's own `__spec__`/`__loader__`) and only falls back to walking up `HERE` for the first existing file — every return path is guarded by `os.path.isfile`, so a result that doesn't exist on disk is never returned as a guess.
- `common._core_version()` never returns an empty string: the zip's own `_plugin_version.VERSION` (written by `build_plugin.py` into every zip), else `importlib.metadata.version("scourgify")`, else `scourgify.__version__`, else the literal `0.0.0+local` — sanitized so the version string is safe as a directory name.
- Extraction (`common._extract_defaults()`) is Windows-safe and race-safe: a unique `.tmp-<version>-<pid>-<nonce>` sibling directory, a marker written LAST inside it, a single `os.rename` into place only when the target cache directory is absent, and a lost race (another process won first) discards the loser's temp tree and reuses the now-warm cache — `os.replace` on a directory is never used anywhere in the file (grep-enforced). Every extracted member's destination is checked with `os.path.realpath` against the extraction root before it is opened, refusing a zip-slip name or a symlinked intermediate directory with `GuardrailError`.
- `wrangle.load_maps()` resolves its production default via `common.defaults_dir()` at call time (never at import time); the injected `defaults_dir=` parameter `tests/test_layers.py` uses still wins.
- `classify.load_vocab()` reads `classify_vocab.txt`/`classify_vocab_ao3.txt` through `common.defaults_dir()` instead of a raw `HERE`-built string, and now fails closed: a vocabulary that resolves to zero terms raises `GuardrailError` rather than under-quoting the engine picker and turning every tag into a "new candidate".
- `engines._shipped_dir()` (new) derives the on-device engine's home directory from `common.defaults_dir()`'s parent — `HERE` on a normal install, the extraction cache root inside the zip — so `engines.Apple.__init__`'s fallback `swift` command and `usable_engines()`'s afm-binary probe both point at a real, readable location in either environment. `build_plugin.py` deliberately excludes the compiled `afm` binary from the zip and ships `afm.swift` itself, so this is what makes the synopsis pass's apple engine work inside the plugin at all.
- `engines.TRAITS`/`_TRAIT_DEFAULTS` gained a `platforms` trait: apple is `("darwin",)`, every other engine defaults to `None` (unconstrained). `usable_engines()` checks it BEFORE the existing afm/swift runtime probe, so the two compose — a darwin host with neither the binary nor a toolchain still yields no apple, and an in-platform-but-toolchain-less host is unaffected by the new gate's absence-path. No engine name is ever compared for equality with the string `"apple"`.
- `tests/test_defaults_resource.py` (new, 13 tests) and `tests/test_engines.py` (+5 tests) pin all of the above, including one real-zipimport subprocess proof, one build_plugin.py zip-layout pin, and one assertion in `test_engines.py` that patches nothing and reads the real `sys.platform` — the one that will prove ROADMAP success criterion 5 on plan 01-02's `windows-latest` CI lane against a genuinely non-darwin host.

## Task Commits

Each task was committed atomically:

1. **Task 1: common.defaults_dir() — one resolver, extract-once, fail-closed** - `832ddae` (feat)
2. **Task 2: Repoint load_maps and load_vocab, and make load_vocab fail closed** - `43bb2a4` (feat)
3. **Task 3: engines.py — afm.swift through the resolver, and a platforms trait that gates apple** - `d6ee0e9` (feat)

_No plan metadata commit yet — this SUMMARY.md is committed separately per the executor protocol._

## Files Created/Modified

- `src/scourgify/common.py` - `defaults_dir()`, `_core_version()`, `_archive_path()`, `_extract_defaults()`; `DEFAULTS` kept only as a deprecated import-compat alias
- `src/scourgify/wrangle.py` - `load_maps()` resolves its production default via `common.defaults_dir()` at call time
- `src/scourgify/classify.py` - `load_vocab()` reads through `common.defaults_dir()` and fails closed on an empty vocabulary
- `src/scourgify/engines.py` - `_shipped_dir()`; `Apple.__init__`/`usable_engines()` route afm/afm.swift through it; `platforms` trait row gates apple in `usable_engines()`
- `tests/test_defaults_resource.py` (new) - 13 tests: normal-install/zip-install/re-extraction/guardrail/zip-slip/`$SCOURGIFY_HOME`/version-key/degenerate-version/lost-race/real-zipimport-subprocess/build-plugin-layout/cost-parity/empty-vocab
- `tests/test_engines.py` - +5 tests: platform-absence, toolchain-composition, cloud-unaffected, trait-not-name-test, and one unpatched real-`sys.platform` assertion

## Decisions Made

- **afm.swift's location, resolved via `defaults_dir()`'s parent, not a second constant.** `afm.swift` physically ships as a sibling of `src/scourgify/defaults/`, not inside it. To keep `common.defaults_dir()`'s contract literal (`== os.path.join(HERE, "defaults")` on a normal install, per the plan's own acceptance criterion) while still routing afm.swift through the resolver, the extraction mirrors that same sibling relationship inside the cache (`cache_root/defaults/...` and `cache_root/afm.swift`), and `engines._shipped_dir()` computes `os.path.dirname(common.defaults_dir())` — `HERE` on a normal install, the cache root inside the zip. One symmetric relationship, tested on both sides, instead of a second cache path or a second resolver function.
- **`overrides.py`'s `common.DEFAULTS` import is left untouched.** It powers only `scourgify overrides --master`, a maintainer-only, explicitly "checkout only" write TARGET for regenerating the master `defaults/` tree — not a runtime read of a shipped file, never called from Calibre-job-reachable code, and outside every must_have/acceptance criterion this plan names. Repointing it would be scope creep onto a tool this plan was never asked to touch (see deviation rules' scope boundary).
- **`_core_version()`'s fallback chain matches `src/scourgify/__init__.py`'s own** (`importlib.metadata` → `__version__` → `0.0.0+local`), so the cache key is never empty and a wheel install can never accidentally share a cache directory with a zip install (the cache is only ever populated on the zip path, where `_plugin_version.py` exists by construction).

## Deviations from Plan

None - plan executed as written, including all `REVIEW:`-tagged incorporations from `01-REVIEWS.md` (loader-first `_archive_path()`, the real-zipimport subprocess test, the Windows-safe rename-only-when-absent extraction, and the version-key fallback chain).

## Issues Encountered

None.

## Threat Flags

None. All new surface (zip extraction, the extraction cache directory) is exactly what this plan's own `threat_model` (T-01-03, T-01-10, T-01-24, T-01-11, T-01-12) already covers.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- `common.defaults_dir()` is now the ONE seam every future plugin-facing plan builds on for reading shipped data; no module in `src/scourgify/` reads a runtime path from `common.HERE` or `common.DEFAULTS` any more (verified by repo-wide grep, `overrides.py --master`'s write-target use excepted and documented above).
- `engines.py`'s `platforms` trait is the pattern any future platform-constrained engine follows — a row, not a name test.
- Full local suite green in isolation (`env -u CALIBRE_LIBRARY SCOURGIFY_HOME=$(mktemp -d) bash -c 'for t in tests/test_*.py; do uv run "$t" || exit 1; done'`), `uv build` still produces a wheel containing `scourgify/defaults/*` and `afm.swift`, and `build_plugin.py` still produces a valid plugin zip.
- Plan 01-02's `windows-latest` `test-windows` job will now also exercise `tests/test_engines.py#test_apple_is_absent_on_this_host_unless_it_is_darwin` against a genuinely `win32` host on its next CI run, closing the loop on ROADMAP success criterion 5.
- No blockers for the next plan in this phase.

## Self-Check: PASSED

- `src/scourgify/common.py`, `src/scourgify/wrangle.py`, `src/scourgify/classify.py`, `src/scourgify/engines.py`, `tests/test_defaults_resource.py`, `tests/test_engines.py` — all confirmed present on disk.
- All 3 task commits (`832ddae`, `43bb2a4`, `d6ee0e9`) confirmed present in `git log`.
- `uv run tests/test_defaults_resource.py` — 13/13 pass. `uv run tests/test_engines.py` — 19/19 pass. `uv run tests/test_plugin_safety.py` — 8/8 pass.
- Full suite green: `env -u CALIBRE_LIBRARY SCOURGIFY_HOME=$(mktemp -d) bash -c 'for t in tests/test_*.py; do uv run "$t" || exit 1; done'` exits 0.
- `grep -v '^[[:space:]]*#' src/scourgify/common.py | sed 's/#.*//' | grep -c 'os\.replace('` prints `0`.
- `uv build` produces a wheel whose contents include `scourgify/afm.swift` and `scourgify/defaults/*`; `uv run build_plugin.py` produces a valid plugin zip.

---
*Phase: 01-foundation-a-hostable-core*
*Completed: 2026-09-07*
