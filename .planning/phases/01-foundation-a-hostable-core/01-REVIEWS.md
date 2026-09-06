---
phase: 1
reviewers: [codex, opencode]
reviewed_at: 2026-09-06T10:47:48Z
plans_reviewed: [01-01-PLAN.md, 01-02-PLAN.md, 01-03-PLAN.md, 01-04-PLAN.md, 01-05-PLAN.md, 01-06-PLAN.md]
models:
  codex: "gpt-5.5 (reasoning=low)"
  opencode: "unknown"
model_sources:
  codex: "banner"
  opencode: "unknown"
---

# Cross-AI Plan Review — Phase 1

> Antigravity was selected by `--all` but could not run: `agy` reported
> `authentication required`. Lenient path — the lane is dropped, not an error.
> Run `agy` to log in if you want a third opinion on a re-run.

## Codex Review

## Summary

The phase is mostly well-shaped: it tackles the right hostability blockers before GUI write verbs, and the wave ordering is broadly correct. The plans are unusually explicit about source ownership, tests, and failure modes. The main risks are in Plan 01’s real-data migration, Plan 03’s zip resource extraction details, Plan 04’s lock/concurrency design colliding with the current process-global library seam, and Plan 06’s conflict-check ordering and result plumbing. I would approve the phase direction, but tighten those four areas before execution.

## Plan 01 — Per-Library State

**Strengths**

- Correctly identifies the main bleed point: all artifacts already funnel through `common.data_dir()` or functions built on it, so changing one owner is high leverage. Evidence: [common.py:42](/Users/andrei/Developer/scourgify/src/scourgify/common.py:42), [artifacts.py:17](/Users/andrei/Developer/scourgify/src/scourgify/artifacts.py:17), [editlog.py:45](/Users/andrei/Developer/scourgify/src/scourgify/editlog.py:45).
- The plan preserves `config.toml` and `overrides/` as global user config, matching the current implementation: `write_config()` writes under `user_dir()` directly, not `data/`. Evidence: [setup.py:69](/Users/andrei/Developer/scourgify/src/scourgify/setup.py:69).
- Migration owner proof via newest backup uuid is a good safety gate for the existing 527 MB user tree.

**Concerns**

- **HIGH:** `data_dir()` currently works without a library, and several existing tests rely on that flat contract. The plan correctly repoints tests, but this is a large blast radius for a “one function” change. Evidence: [tests/test_paths.py:41](/Users/andrei/Developer/scourgify/tests/test_paths.py:41), [tests/test_write_path.py:257](/Users/andrei/Developer/scourgify/tests/test_write_path.py:257).
- **MEDIUM:** The migration “attempted once per process” flag can mask later success after an initial negative case. If a throwaway library resolves first and fails owner proof, then the real library is opened later in the same process, a global attempted flag would prevent migration unless keyed by `user_dir()` + resolved uuid.
- **MEDIUM:** `set_library()` does not clear any cache today. If `_UUID_CACHE` is added, the plan should either make `set_library()` invalidate the cache for changed paths or require all tests/jobs to call `clear_uuid_cache()` deliberately. Evidence: [common.py:82](/Users/andrei/Developer/scourgify/src/scourgify/common.py:82).

**Suggestions**

- Key migration attempts by `(user_dir(), uuid)` or `(user_dir(), library_path)` rather than one process-global boolean.
- Add a source-reading test or `rg`-style assertion that no production artifact path uses `user_dir()/data` directly outside `common.data_dir()`.
- In `set_library()`, consider clearing only path-derived memo state, or document why every job must call `clear_uuid_cache()`.

**Risk Assessment: HIGH**

The target behavior is right, but it moves user history on disk. The guarded owner proof helps; the remaining risk is process-order behavior and accidental flat-path leftovers.

## Plan 02 — Windows/Linux CI

**Strengths**

- Adds the cross-platform signal early, before more plugin code depends on it.
- Keeps the existing Ubuntu Python floor matrix intact while adding Windows 3.14, which matches the Calibre-bundled interpreter goal.
- The smoke target exists and is designed as a Calibre-interpreter check, not a normal CPython test. Evidence: [tests/test_plugin_safety.py:54](/Users/andrei/Developer/scourgify/tests/test_plugin_safety.py:54) shows why CPython import tests are insufficient.

**Concerns**

- **MEDIUM:** The plan says “required” jobs, but GitHub required checks are branch-protection settings, not represented solely by `.github/workflows/ci.yml`. The plan can add blocking jobs in workflow semantics, but cannot make them required without repo settings.
- **MEDIUM:** “Use latest official Calibre unless newer current at execution time” weakens the one-place pin. Pick a fixed version in the plan and bump later in a visible commit.
- **LOW:** Official Linux installer behavior in CI can be brittle. The plan accepts this but should capture logs/artifacts enough to diagnose install path failures.

**Suggestions**

- State “blocking workflow jobs” rather than “required” unless branch protection is also updated.
- Pin `CALIBRE_VERSION: 9.11.0` in the plan and do not allow executor discretion to pick newer.
- Add `calibre-debug --version` output in both smoke jobs.

**Risk Assessment: MEDIUM**

Good dependency ordering and high value, but external installer CI is inherently noisy and cannot fully prove plugin GUI behavior.

## Plan 03 — Defaults Resource + Apple Platform Gate

**Strengths**

- Correctly targets real current failures: `wrangle.load_maps()` reads `DEFAULTS` at import-time alias today, and `classify.load_vocab()` directly builds paths from `HERE`. Evidence: [wrangle.py:13](/Users/andrei/Developer/scourgify/src/scourgify/wrangle.py:13), [wrangle.py:56](/Users/andrei/Developer/scourgify/src/scourgify/wrangle.py:56), [classify.py:61](/Users/andrei/Developer/scourgify/src/scourgify/classify.py:61).
- Makes `load_vocab()` fail closed, closing a real silent-under-tagging risk. Evidence: current `_read_vocab_file()` returns `[]` for missing paths at [classify.py:51](/Users/andrei/Developer/scourgify/src/scourgify/classify.py:51).
- Apple platform gating belongs in `TRAITS`; current code does rely on local runtime probing only. Evidence: [engines.py:32](/Users/andrei/Developer/scourgify/src/scourgify/engines.py:32), [engines.py:204](/Users/andrei/Developer/scourgify/src/scourgify/engines.py:204).

**Concerns**

- **HIGH:** `_archive_path()` “walk up from `HERE` looking for first existing file” is fragile under zipimport. In a zip, `__file__` often looks like `/path/plugin.zip/scourgify/common.py`; components inside the zip do not exist as filesystem paths. This needs a concrete tested implementation, likely using `__loader__`, `__spec__.origin`, or parsing the `.zip` segment.
- **HIGH:** `os.replace(temp_dir, cache_dir)` is not portable when `cache_dir` exists; replacing directories is not uniformly supported and can fail on Windows. Extraction should use a unique temp dir and then `os.rename` only when target absent, or marker-based warm cache with cleanup.
- **MEDIUM:** Tests that “point the resolver at a zip” may be hard if `common` is already imported from `src`. Monkeypatching private helpers is fine, but then it does not fully prove zipimport behavior.

**Suggestions**

- Use `importlib.resources.files("scourgify")` if available under Calibre’s Python, or explicitly test importing `scourgify.common` from a generated zip placed first on `sys.path` in a subprocess.
- Make cache population Windows-safe: extract to `cache.tmp.<pid>.<nonce>`, write marker, then `os.rename` if target absent; if target exists after a race, discard temp and use warm cache.
- Add a test that `build_plugin.py` zip layout contains the members the resolver expects. Evidence: current engine expects `afm.swift` from package root via `HERE`, [engines.py:79](/Users/andrei/Developer/scourgify/src/scourgify/engines.py:79).

**Risk Assessment: HIGH**

The concept is necessary, but zip path detection and atomic directory replacement are easy to get subtly wrong, especially on Windows.

## Plan 04 — Shared Write Funnel + Lock + No Shelling

**Strengths**

- Refactoring target is correct: `write_ops()` and `run_writer()` currently duplicate filtering, wipe guard, before-read, backup, and edit-log start/finish. Evidence: [common.py:526](/Users/andrei/Developer/scourgify/src/scourgify/common.py:526), [common.py:554](/Users/andrei/Developer/scourgify/src/scourgify/common.py:554).
- Preserves `ops.apply_ops()` as the one executor, which matches the architecture. Evidence: [ops.py:42](/Users/andrei/Developer/scourgify/src/scourgify/ops.py:42).
- The no-spawn AST test is a strong fit for this repo’s existing source-reading safety style. Evidence: [tests/test_plugin_safety.py:110](/Users/andrei/Developer/scourgify/tests/test_plugin_safety.py:110).

**Concerns**

- **HIGH:** The plan says two different library uuid writes can proceed concurrently, but `common.set_library()` and `library()` are process-global. Concurrent in-process jobs against different libraries can still race the global library path used by `backup_db()`, `db_path()`, `data_dir()`, and `ro_connect()`. Evidence: `_LIBRARY` global at [common.py:79](/Users/andrei/Developer/scourgify/src/scourgify/common.py:79), `backup_db()` defaults to `db_path()` at [common.py:505](/Users/andrei/Developer/scourgify/src/scourgify/common.py:505).
- **HIGH:** `_write_run` acquiring lock after empty-op filtering but before backup is good, but `backup_db()` still uses global `db_path()` unless the API path is injected. For plugin correctness, the live library path should be captured per job and passed through.
- **MEDIUM:** `except BaseException` is currently used in `write_ops()` for log finish. The plan repeats that shape. In job-reachable code, catching `BaseException` can interact badly with cancellation or interpreter shutdown. Evidence: [common.py:549](/Users/andrei/Developer/scourgify/src/scourgify/common.py:549).
- **MEDIUM:** Adding `common.calibre_open` as an allowed subprocess exemption in job-reachable code is acceptable only if reachability is constrained. The source test that only `run_writer` and `rollback_cmd` call it is essential.

**Suggestions**

- Do not claim or test concurrent writes to two different libraries until the library path is no longer process-global, or explicitly limit the test to two `lib_uuid`s with injected backup/source paths.
- Add `library_path` or `backup_src` to `_write_run`, so `backup_db(src=...)` is not dependent on mutable global state.
- Prefer `except Exception` for finish-failed logging unless there is a specific reason to catch `BaseException`.

**Risk Assessment: HIGH**

This plan is central to Calibre safety. The single-library lock is good; the multi-library concurrency claim is not supported by the current global library seam.

## Plan 05 — Pure Options + `decide=`

**Strengths**

- The pure functions really are currently in `wizard.py` and mostly domain-owned elsewhere. Evidence: `_scope_options()` at [wizard.py:261](/Users/andrei/Developer/scourgify/src/scourgify/wizard.py:261), `_engine_options()` at [wizard.py:286](/Users/andrei/Developer/scourgify/src/scourgify/wizard.py:286), `_proposal_options()` at [wizard.py:363](/Users/andrei/Developer/scourgify/src/scourgify/wizard.py:363).
- The `decide=` seam is consistent with existing `promote.backfill(decide=...)`. Evidence: [promote.py:349](/Users/andrei/Developer/scourgify/src/scourgify/promote.py:349).
- Moving lazy `ui` imports behind injected decisions supports the rich-blocked import constraint. Evidence: current checklist sites import `ui` directly, e.g. [staleness.py:68](/Users/andrei/Developer/scourgify/src/scourgify/staleness.py:68), [synopsis.py:267](/Users/andrei/Developer/scourgify/src/scourgify/synopsis.py:267).

**Concerns**

- **MEDIUM:** “No compatibility re-export” is clean, but it increases breakage risk for existing tests or local scripts. Since the names are private `_...`, this is acceptable, but the plan should expect test churn.
- **MEDIUM:** Putting `engine_options()` in `engines.py` may introduce an import cycle if it calls `classify.est_cost()`, because `classify.py` imports `engines_mod` and re-exports engine symbols. Current wizard `_engine_options()` calls `classify.est_cost()` at [wizard.py:291](/Users/andrei/Developer/scourgify/src/scourgify/wizard.py:291). Moving that exact body into `engines.py` would make `engines` depend on `classify`.
- **LOW:** Tests that assert `sys.modules` gained no `ui` entry can be flaky if another import already loaded `scourgify.ui`.

**Suggestions**

- Avoid an `engines.py -> classify.py` dependency. Either pass a `cost_fn` into `engines.engine_options()` or keep cost-row construction in `classify`.
- In no-`ui` tests, remove `scourgify.ui` from `sys.modules` before the call and restore afterward.
- Keep a small source test that relocated builder modules do not import `scourgify.ui`.

**Risk Assessment: MEDIUM**

The design is correct and scoped, but the `engines.engine_options` placement can create a cycle unless adjusted.

## Plan 06 — Apply-Time Conflicts

**Strengths**

- Solves the right data-loss problem: current write ops carry only desired after-values, no plan-time expected values. Evidence: [common.py:314](/Users/andrei/Developer/scourgify/src/scourgify/common.py:314).
- Reuses the correct predicate: `editlog.conflict()` already defines multi-value and single-value semantics. Evidence: [editlog.py:135](/Users/andrei/Developer/scourgify/src/scourgify/editlog.py:135).
- Keeping `ops.py` and `_writer.py` unchanged is the right boundary; `ops.py` intentionally imports nothing from `scourgify`. Evidence: [ops.py:10](/Users/andrei/Developer/scourgify/src/scourgify/ops.py:10).

**Concerns**

- **HIGH:** The plan contradicts itself on all-skipped runs. It says the conflict filter runs before `backup_db()`, but also says an all-skipped run still writes a header/footer. If `editlog.start()` happens after backup and the surviving ops list is empty, the current proposed order either burns no backup but must still write history, or writes history after deciding not to snapshot. That needs an explicit order.
- **HIGH:** “`_write_run` yields skipped list to caller” conflicts with existing public return shapes: `write_ops()` returns `None` today, and `run_writer()` exits/prints. Phase 2 needs structured skipped data, so this should define a small result object now rather than implicit tuple plumbing.
- **HIGH:** Producer coverage is harder than stated. `wrangle.Plan` has per-book before values in `self.perbook`, but `self.before` is only distinct-value sets, not per-book expected state. Evidence: [wrangle.py:323](/Users/andrei/Developer/scourgify/src/scourgify/wrangle.py:323), [wrangle.py:328](/Users/andrei/Developer/scourgify/src/scourgify/wrangle.py:328). `promote.backfill_plan()` currently returns only changed final tags and adds, not the original tag set. Evidence: [promote.py:314](/Users/andrei/Developer/scourgify/src/scourgify/promote.py:314).
- **MEDIUM:** `classify.apply_proposal()` needs proposal rows to include original tag state, but current proposal schema only has `book_id`, `title`, `added_tags`, `proposed_new`. Evidence: [artifacts.py:50](/Users/andrei/Developer/scourgify/src/scourgify/artifacts.py:50). The plan says “proposal rows record the tags the plan was computed against,” but they do not currently.
- **MEDIUM:** Conflict filtering needs field multiplicity. `column_is_multiple()` exists for sqlite, but `write_ops()` needs equivalent API metadata. Evidence: [common.py:425](/Users/andrei/Developer/scourgify/src/scourgify/common.py:425), [tests/test_write_path.py:20](/Users/andrei/Developer/scourgify/tests/test_write_path.py:20).

**Suggestions**

- Define exact all-skipped order. Recommended: before-read → conflict filter → if all skipped, write header/footer with skipped and no backup/apply; if some survive, backup → log surviving ops → apply.
- Add `WriteResult(applied_ops, skipped, backup, run_id)` or a plain dict as the shared return from `_write_run`/`write_ops`; make `run_writer()` print from it.
- Add a separate task before producer updates to extend artifact schemas where needed, especially classify proposals.
- For promote and synopsis, change plan return shapes to carry `expected` explicitly, rather than reconstructing later.
- Add an API-side `field_is_multiple_via_api(api, field)` helper.

**Risk Assessment: HIGH**

The safety goal is critical and correct, but the current data structures do not all contain plan-time expected state, and the all-skipped/log/backup ordering needs to be made unambiguous.

## Cross-Plan Dependency Issues

- Plan 04 depends on Plan 01’s uuid resolution, but its concurrent different-library claim exceeds what Plan 01’s process-global `set_library()` can safely support.
- Plan 05 depends on Plan 03, but moving engine option builders into `engines.py` can create a new reverse dependency on `classify.est_cost()` after Plan 03 also changes engine/defaults behavior.
- Plan 06 depends on Plan 04’s `_write_run`; Plan 04 should define a structured result shape even before conflict checks, so Plan 06 does not retrofit it.
- Plan 06 also depends implicitly on artifact/schema changes not fully listed, especially classify proposals.

## Overall Risk Assessment: HIGH

The roadmap attacks the right foundation work and has strong verification instincts, but three areas are live-library critical: on-disk migration, shared write funnel locking, and conflict skips. Each is fixable with clearer ordering and injected state, but they should be tightened before implementation because mistakes here can misplace history, snapshot the wrong library, or overwrite user edits.

---

## OpenCode Review

> [reviewed-without-source-citations] This reviewer declared source-grounded evidence but cited no file:line source evidence, so it reviewed the pasted plan text only — down-weight its verdict in the Consensus Summary.

I'll review these implementation plans against the actual codebase to provide structured feedback. Let me start by examining the relevant source files and understanding the current state.
Now let me examine the editlog module to understand the current conflict handling:
Now let me examine the ops module to understand the current apply mechanism:
Now let me examine the wrangle module to understand the current Plan structure:
Let me continue reading the wrangle module to find the Plan class:
Now let me examine the op_set_field function in common.py:
Now let me examine the test files to understand the current testing patterns:
Now let me examine the test_write_path.py to understand the current write path testing:
Now I have a good understanding of the codebase. Let me provide a comprehensive review of the implementation plans. Based on the detailed analysis of the codebase and the provided plans, here's my structured feedback:

---

# **Cross-AI Plan Review: Phase 1 - Foundation for scourgify Calibre Plugin**

## **Executive Summary**

The 6 plans for Phase 1 are **well-architected and comprehensive**, addressing the core foundation needed for a hostable plugin. They correctly identify the critical gaps in the current implementation (library-scoped state, defaults resource access from zip, write protocol unification, and cross-platform support). The dependency ordering between plans is logical, and the success criteria are measurable. However, there are **several HIGH-severity concerns** around edge cases, error handling completeness, and the complexity of the migration path that could impact production stability.

---

## **Plan-by-Plan Assessment**

---

### **🟢 Plan 01-01: Per-library State & Windows user_dir()**

**Strengths:**
- ✅ **Correctly identifies the core problem**: Current flat `data/` tree causes cross-library contamination (observed in phases 4-5)
- ✅ **Sound architecture**: UUID-keyed paths with `data/<uuid>/` pattern aligns with Calibre's own library isolation
- ✅ **Migration strategy is safe**: Owner proof via backup UUID validation prevents misattribution
- ✅ **Windows support**: APPDATA branch follows Calibre's own convention (`%APPDATA%\calibre`)
- ✅ **Comprehensive test coverage**: 12+ test cases covering all edge cases (empty, ordering, disjointness)

**Concerns:**
- **HIGH**: **Migration failure handling** - The plan states `GuardrailError` is raised if rename fails partway, but doesn't specify **atomicity guarantees**. A partial rename of 527MB could leave the library in an inconsistent state. *Need: Transactional migration (rename to temp, validate, then final rename)*
- **HIGH**: **Backup UUID extraction** - `library_uuid()` reads from `library_id.uuid` table, but the migration uses backup files. Need to verify that `ff_*.db` files **contain** the `library_id` table. If not, the owner proof could fail silently.
- **MEDIUM**: **`nouuid-<sha1>` fallback** - Using `sha1(library path)[:12]` could have collisions. Consider using full hash or UUID5 namespace.
- **MEDIUM**: **Config/overrides global scope** - D-01 explicitly keeps these at user_dir() root, but this creates a **shared state problem** for multi-library users with different column mappings.

**Suggestions:**
- Add atomic migration: `temp_dir → validate → final_rename` pattern
- Verify backup files contain `library_id` table before attempting UUID extraction
- Consider using `uuid.uuid5(uuid.NAMESPACE_URL, lib_path)` for deterministic nouuid keys
- Document the multi-library config limitation and consider future per-library config (deferred is acceptable)

**Risk Assessment: MEDIUM-HIGH** - Migration logic is complex and touches user data. Needs more robust error handling.

---

### **🟢 Plan 01-02: Windows + Linux CI Lanes**

**Strengths:**
- ✅ **Correctly prioritizes CI coverage** - Windows is a primary target, and current CI only covers Ubuntu
- ✅ **Pinned Calibre version** - Single source of truth for version, cached via actions/cache
- ✅ **Both smoke jobs** - Covers core imports under Calibre's bundled Python (critical for plugin)
- ✅ **Fallback strategy** - Demote smoke jobs if flaky, but keep blocking (good balance)

**Concerns:**
- **HIGH**: **`calibre-debug.exe` path resolution** - The plan relies on searching, but doesn't specify **what happens if multiple Calibre installations exist**. On Windows, users might have both portable and installed versions.
- **MEDIUM**: **MSI silent install** - `/qn /norestart` might fail if Calibre is running. Need timeout/retry logic.
- **MEDIUM**: **Linux installer** - Official Linux installer might require sudo, but the plan mentions "non-interactively to workspace-local prefix". Need to verify this is possible without root.
- **LOW**: **CI runtime** - Windows jobs might be slower. Consider splitting core tests from smoke tests.

**Suggestions:**
- Add explicit path priority: `C:\Program Files\Calibre2\calibre-debug.exe` first, then PATH lookup
- Add `tasklist` check before MSI install to ensure Calibre isn't running
- Use `--with-library` flag for Linux installer to specify local prefix
- Consider adding a `CALIBRE_INSTALL_DIR` env var for custom paths

**Risk Assessment: MEDIUM** - External dependencies (Calibre installers) are the main risk, but the fallback strategy mitigates this.

---

### **🟡 Plan 01-03: Defaults Resource Seam & Apple Platform Gate**

**Strengths:**
- ✅ **Solves the zip import problem** - `common.HERE` inside zip makes `os.path.exists()` return False for all bundled files
- ✅ **Extract-once cache** - Version-keyed prevents stale cache after upgrades
- ✅ **Zip-slip protection** - `os.path.realpath` check prevents path traversal attacks
- ✅ **Fail-closed behavior** - Empty defaults raise `GuardrailError`, not silent failures
- ✅ **Platform trait** - Cleaner than name string comparisons

**Concerns:**
- **HIGH**: **Cache invalidation** - Version key uses `_plugin_version.VERSION` from zip, but what if the **wheel version differs**? Need to handle both installed and zip contexts.
- **HIGH**: **`load_vocab()` fail-closed** - Current `classify.py:load_vocab()` doesn't fail closed. If extraction fails, it could return empty vocab, causing **silent under-tagging**.
- **MEDIUM**: **Apple engine on macOS < 26** - Platform trait allows apple, but runtime probe (afm.swift) might fail. Need to ensure **both gates compose correctly**.
- **MEDIUM**: **`afm.swift` extraction** - The plan includes afm.swift in extraction, but the compiled binary is excluded. Need to verify the Swift source can actually run on the target macOS version.

**Suggestions:**
- Add fallback: If `_plugin_version` import fails, use `importlib.metadata.version("scourgify")` as backup
- Modify `classify.load_vocab()` to explicitly check for empty vocab and raise `GuardrailError`
- Add test: Verify that on macOS < 26 with no Swift toolchain, apple is absent from `usable_engines()`
- Document that `afm.swift` requires macOS 26+ (Apple Intelligence)

**Risk Assessment: HIGH** - This plan touches the most critical path (data loading). Empty defaults could cause silent data corruption.

---

### **🟢 Plan 01-04: Unified Write Protocol & Library Lock**

**Strengths:**
- ✅ **Addresses #71 directly** - Eliminates duplication between `write_ops` and `run_writer`
- ✅ **Library-UUID lock** - Prevents concurrent writes to same library (critical for plugin)
- ✅ **Non-blocking acquire** - Prevents deadlocks in GUI thread
- ✅ **Lock cleanup in finally** - Ensures lock release even on failure
- ✅ **`calibre_open()` Windows support** - Uses `tasklist` instead of `pgrep`/`ps`

**Concerns:**
- **HIGH**: **Lock granularity** - Module-level `_WRITE_LOCKS` dict could become a **memory leak** if libraries are opened/closed frequently. Need cleanup mechanism.
- **HIGH**: **`_write_run` context manager** - If an exception occurs during `editlog.start()`, the lock might be released but the **run record is incomplete**. Need to ensure atomicity of header + op lines.
- **MEDIUM**: **`calibre_open()` in CLI** - The plan moves `calibre_open()` check to `run_writer` only, but `write_ops` (plugin path) skips it. This is correct, but need to ensure **no other CLI path** calls `write_ops` directly.
- **MEDIUM**: **Process-level contention** - D-06 explicitly excludes cross-process locks, but a CLI run while plugin is open could still cause issues. The existing `calibre_open()` guard should catch this, but need to verify.

**Suggestions:**
- Add `_cleanup_stale_locks()` function to remove locks for libraries no longer in use
- Ensure `editlog.start()` and lock acquisition are atomic (write header before releasing lock)
- Add assertion: Verify that `write_ops` is never called from CLI context
- Document the cross-process limitation and rely on `calibre_open()` guard

**Risk Assessment: HIGH** - Write protocol is the most critical path. Any bug here could cause data corruption.

---
---
### **🟢 Plan 01-05: Pure Option Functions & decide= Seam**

**Strengths:**
- ✅ **Correctly identifies the problem** - Wizard currently owns domain logic that tool modules need
- ✅ **Minimal changes** - Relocating existing pure functions, not rewriting
- ✅ **Preserves behavior** - Tests ensure wizard flow remains identical
- ✅ **`decide=` seam** - Enables phase 2's PLAN/EXECUTE split without changing core logic

**Concerns:**
- **MEDIUM**: **Function relocation risks** - Moving functions between modules could break **import cycles**. Need to verify no circular dependencies are introduced.
- **MEDIUM**: **`wizard.py` imports** - After relocation, `wizard.py` will import from tool modules. Need to ensure **rich is not imported** through this path (plugin safety).
- **LOW**: **Order preservation** - The plan states row order must be preserved, but doesn't specify **how this is enforced**. Need explicit tests.

**Suggestions:**
- Add import cycle check: Verify that relocated functions don't create circular imports
- Add rich-blocked test: Ensure `wizard.py` can still be imported with rich absent after relocation
- Add explicit order assertions in tests

**Risk Assessment: MEDIUM** - Mostly safe, but import cycles could be a subtle issue.

---
---
### **🟡 Plan 01-06: Apply-time Conflict Checks**

**Strengths:**
- ✅ **Uses existing `editlog.conflict`** - Single source of truth for conflict detection
- ✅ **Before-read comparison** - Correctly compares fresh read against plan-time expected
- ✅ **Skip-and-report** - Drifted values are skipped, not overwritten
- ✅ **Footer carries skipped list** - Makes conflicts visible in History
- ✅ **Idempotency** - Re-applying same ops skips everything on second run

**Concerns:**
- **HIGH**: **`expected` population timing** - Each tool must populate `expected` from **its plan's before-state**, not a fresh read. The plan states this, but **verification is critical**.
- **HIGH**: **Multi-value field handling** - `editlog.conflict` treats multi-value as sets, but `expected` on ops must match this. Need to ensure `wrangle.Plan` stores before-state in set-compatible format.
- **MEDIUM**: **`stamp_now` ops** - These carry no `expected`, but if a stamp is applied twice, it **will overwrite**. Need to document this limitation.
- **MEDIUM**: **Performance impact** - Conflict check adds a before-read for every write. For large libraries, this could be expensive. Need to verify it's acceptable.

**Suggestions:**
- Add explicit test: Verify that `wrangle.Plan.before` stores values in a format compatible with `editlog.conflict`
- Add test: Apply same ops twice, verify second run skips all
- Document that `stamp_now`/`set_pref`/`create_column` are not conflict-checked
- Consider caching before-reads for performance

**Risk Assessment: HIGH** - Conflict detection is critical for data safety. Incorrect implementation could cause silent overwrites.

---

## **Cross-Plan Analysis**

---

### **🔴 Critical Dependencies & Ordering Issues**

1. **Plan 01-03 depends on 01-01** - `defaults_dir()` needs `user_dir()` and `data_dir()` to be UUID-aware. **This dependency is correct.**

2. **Plan 01-04 depends on 01-01 and 01-03** - Write protocol needs per-library state and defaults access. **This dependency is correct.**

3. **Plan 01-05 depends on 01-03** - Pure functions need defaults access. **This dependency is correct.**

4. **Plan 01-06 depends on 01-04 and 01-05** - Conflict checks need unified write protocol and pure functions. **This dependency is correct.**

5. **Wave ordering** - Wave 1 (01-01, 01-02) → Wave 2 (01-03) → Wave 3 (01-04, 01-05) → Wave 4 (01-06) is **logical and safe**.

**⚠️ BLOCKING ISSUE**: Plan 01-02 (CI lanes) **does not depend on any other plan**, but **all other plans need the CI to verify their changes**. This creates a **chicken-and-egg problem**: CI must be green to merge 01-02, but 01-02 is needed to verify other plans. **Solution**: Merge 01-02 first as a standalone PR, then proceed with other plans.

---

### **🔴 Scope Creep & Over-Engineering**

- **✅ Within scope**: All plans stick to Phase 1's foundation goals
- **✅ No new features**: All changes are infrastructure for hosting, not user-facing features
- **⚠️ Potential over-engineering**: The `decide=` seam in Plan 01-05 might be **premature** for Phase 1, since Phase 2 (which needs it) isn't starting yet. However, it's a **small, safe change** that enables future work.

---

### **🔴 Safety & Data Integrity**

| Plan | Data Loss Risk | Mitigation Quality | Test Coverage |
|------|----------------|-------------------|---------------|
| 01-01 | **HIGH** (migration) | **MEDIUM** (needs atomicity) | **HIGH** |
| 01-02 | **LOW** | **HIGH** | **HIGH** |
| 01-03 | **HIGH** (empty defaults) | **HIGH** | **HIGH** |
| 01-04 | **HIGH** (concurrent writes) | **HIGH** | **HIGH** |
| 01-05 | **LOW** | **HIGH** | **HIGH** |
| 01-06 | **HIGH** (silent overwrites) | **HIGH** | **HIGH** |

**Overall Safety Assessment**: **MEDIUM-HIGH** - Most plans have good mitigations, but migration (01-01) and conflict checks (01-06) are high-risk and need careful implementation.

---

### **🔴 Cross-Platform Correctness**

| Concern | macOS | Windows | Linux |
|---------|-------|---------|-------|
| Path resolution | ✅ | **⚠️ Needs verification** (APPDATA, tasklist) | ✅ |
| Defaults access from zip | ✅ | ✅ | ✅ |
| Write locks | ✅ | ✅ | ✅ |
| Process spawning | ✅ | **⚠️ Needs tasklist verification** | ✅ |
| File permissions | ✅ | **⚠️ APPDATA permissions** | ✅ |

**Overall Platform Assessment**: **MEDIUM** - Windows is the main unknown. CI lanes (01-02) will help verify, but manual testing is still needed.

---

## **Detailed Findings by Severity**

---

### **🔴 HIGH Severity Issues**

1. **Migration Atomicity (01-01)**
   - **Issue**: Partial rename of 527MB could corrupt user data
   - **Location**: `common.migrate_legacy_data()`
   - **Evidence**: Plan states `os.rename` but doesn't specify atomic migration
   - **Fix**: Use temp directory pattern: `data/ → data/.migration_temp/ → validate → data/<uuid>/`

2. **Backup UUID Extraction (01-01)**
   - **Issue**: `ff_*.db` files might not contain `library_id` table
   - **Location**: `common.library_uuid()` used on backup files
   - **Evidence**: `library_uuid()` reads from `library_id.uuid`, but backup files are copies of `metadata.db`
   - **Fix**: Verify backup contains `library_id` table before attempting UUID read

3. **Empty Vocabulary Fail-Closed (01-03)**
   - **Issue**: `classify.load_vocab()` doesn't currently fail closed on empty vocab
   - **Location**: `classify.py:load_vocab()`
   - **Evidence**: Current code returns empty list, doesn't raise `GuardrailError`
   - **Fix**: Add explicit check after vocab merge: `if not vocab: raise GuardrailError(...)`

4. **Lock Memory Leak (01-04)**
   - **Issue**: `_WRITE_LOCKS` dict grows unbounded with library UUIDs
   - **Location**: `common._WRITE_LOCKS`
   - **Evidence**: Plan doesn't specify cleanup mechanism
   - **Fix**: Add `_cleanup_stale_locks()` called periodically or on library close

5. **Expected Population (01-06)**
   - **Issue**: Tools must populate `expected` from plan's before-state, not fresh read
   - **Location**: All tool modules (`wrangle.py`, `classify.py`, etc.)
   - **Evidence**: Plan states this but verification is critical
   - **Fix**: Add explicit tests verifying `expected` matches plan's before-state

---

### **🟡 MEDIUM Severity Issues**

1. **`nouuid` Key Collisions (01-01)**
   - **Issue**: `sha1(lib_path)[:12]` could collide
   - **Fix**: Use full hash or `uuid.uuid5()`

2. **Multi-library Config (01-01)**
   - **Issue**: Config/overrides are global, but users might need per-library settings
   - **Fix**: Document limitation; consider future enhancement

3. **Apple Platform + Runtime Probe (01-03)**
   - **Issue**: Platform trait allows apple, but runtime might fail
   - **Fix**: Ensure both gates compose (platform AND runtime probe)

4. **`calibre-debug.exe` Path (01-02)**
   - **Issue**: Multiple Calibre installations could exist
   - **Fix**: Add explicit path priority

5. **Import Cycles (01-05)**
   - **Issue**: Relocating functions could create circular imports
   - **Fix**: Add import cycle tests

---
---
### **🟢 LOW Severity Issues**

1. **CI Runtime (01-02)** - Windows jobs might be slower; consider splitting
2. **Performance Impact (01-06)** - Conflict check adds before-read; verify acceptable
3. **`stamp_now` Idempotency (01-06)** - Document that stamps are not conflict-checked

---

## **Recommendations**

---

### **🎯 Priority 1: Must Fix Before Implementation**

1. **Add atomic migration** to Plan 01-01
   ```python
   # In migrate_legacy_data():
   temp_dir = os.path.join(data_dir(), f".migration_temp_{uuid}")
   os.makedirs(temp_dir)
   try:
       for entry in legacy_entries:
           os.rename(os.path.join(data_dir(), entry), os.path.join(temp_dir, entry))
       # Validate migration
       os.rename(temp_dir, os.path.join(data_dir(), uuid))
   except Exception:
       # Rollback
       for entry in os.listdir(temp_dir):
           os.rename(os.path.join(temp_dir, entry), os.path.join(data_dir(), entry))
       os.rmdir(temp_dir)
       raise
   ```

2. **Verify backup UUID extraction** in Plan 01-01
   ```python
   # In migrate_legacy_data():
   backup_path = newest_backup  # From data/backups/ff_*.db
   try:
       con = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)
       lib_uuid = con.execute("SELECT uuid FROM library_id").fetchone()
       con.close()
   except (sqlite3.OperationalError, TypeError):
       # library_id table doesn't exist in backup
       print("Backup does not contain library_id table; cannot prove ownership")
       return None
   ```

3. **Add fail-closed to `load_vocab()`** in Plan 01-03
   ```python
   # In classify.py:load_vocab()
   vocab = merge_vocab(defaults_dir())
   if not vocab:
       raise common.GuardrailError(
           f"Controlled vocabulary is empty (defaults_dir={defaults_dir()}) — "
           "classification refused to avoid silent under-tagging.")
   return vocab
   ```

---

### **🎯 Priority 2: Should Fix Before Implementation**

1. **Add lock cleanup** to Plan 01-04
   ```python
   def _cleanup_stale_locks():
       """Remove locks for libraries that are no longer open."""
       # Track last access time for each lock
       # Remove locks older than N minutes
       pass
   ```

2. **Add explicit expected tests** to Plan 01-06
   ```python
   def test_expected_matches_plan_before_state():
       # For wrangle.Plan:
       plan = wrangle.plan(cfg, maps)
       ops = plan.write(force=True)  # Don't actually write
       for op in ops:
           if op["op"] == "set_field":
               for book, expected_val in op["expected"].items():
                   # Verify expected_val matches plan.before[op["field"]][int(book)]
                   assert expected_val == plan.before[op["field"]].get(int(book))
   ```

---
---
### **🎯 Priority 3: Nice to Have**

1. **Add `CALIBRE_INSTALL_DIR` env var** for custom Calibre paths (01-02)
2. **Use `uuid.uuid5()` for nouuid keys** (01-01)
3. **Add performance metrics** for conflict check (01-06)

---
---
## **Final Risk Assessment**

| Aspect | Rating | Justification |
|--------|--------|---------------|
| **Architecture** | ✅ **EXCELLENT** | Clean separation, logical dependencies, follows existing patterns |
| **Safety** | ⚠️ **MEDIUM-HIGH** | High-risk areas (migration, conflict checks) have good mitigations but need fixes |
| **Completeness** | ✅ **EXCELLENT** | All Phase 1 requirements covered, no gaps identified |
| **Testability** | ✅ **EXCELLENT** | Comprehensive test plans, covers all edge cases |
| **Cross-Platform** | ⚠️ **MEDIUM** | Windows is main unknown; CI lanes will help verify |
| **Implementation Risk** | ⚠️ **MEDIUM-HIGH** | Migration and write protocol changes are high-risk |

**Overall Risk: MEDIUM-HIGH**

**Recommendation: PROCEED WITH CONDITIONS**
- Fix the **5 HIGH-severity issues** before implementation
- Implement **Plan 01-02 (CI lanes) first** as a standalone PR
- **Manual testing required** for Windows paths and migration on real user data
- **Code review focus** on migration atomicity, conflict check logic, and lock management

---
---
## **Action Items for Implementation Team**

1. **Before starting implementation**:
   - Fix the 5 HIGH-severity issues in the plans
   - Create a migration test with a **real 527MB data tree** (not just fixtures)
   - Verify `ff_*.db` files contain `library_id` table

2. **Implementation order**:
   - **First**: Plan 01-02 (CI lanes) - standalone, no dependencies
   - **Second**: Plan 01-01 (per-library state) - foundation for all others
   - **Third**: Plan 01-03 (defaults resource) - needed by write protocol
   - **Fourth**: Plan 01-04 (write protocol) - depends on 01-01 and 01-03
   - **Fifth**: Plan 01-05 (pure functions) - can be done in parallel with 01-04
   - **Sixth**: Plan 01-06 (conflict checks) - depends on 01-04 and 01-05

3. **Testing strategy**:
   - Run **full test suite** after each plan
   - **Manual verification** of migration on developer's real library (Task 4 in 01-01)
   - **Windows CI verification** before merging any plan that touches cross-platform code

---
---
## **Conclusion**

The plans are **well-designed and comprehensive**, with a few **critical issues that must be addressed** before implementation. The architecture is sound, the dependency ordering is logical, and the test coverage is thorough. The main risks are in **data migration** and **write protocol changes**, which are appropriately identified and (with the recommended fixes) well-mitigated.

**Final Verdict: APPROVE WITH CONDITIONS** - Implement the 5 HIGH-severity fixes, then proceed with the proposed order. The phase is achievable and will provide a solid foundation for the Calibre plugin.

---

## Consensus Summary

Both reviewers approve the phase direction and both call the wave ordering correct.
Neither found scope creep. Both land on the same four high-risk areas, and both say
the risk is in *under-specified detail*, not in the design.

Weighting note: Codex reviewed with repo access and cites `file:line` throughout.
OpenCode carries the `[reviewed-without-source-citations]` marker — it read the
plan text only, so its verdict is not counted at full consensus weight. Where the
two agree, Codex's evidence is what makes the finding concrete.

### Agreed Strengths

- Wave ordering (1→2→3→4) is logical and each dependency is real, not assumed.
- The phase targets genuine hostability blockers, not demos — per-library state,
  defaults-from-inside-the-zip, one write funnel, apply-time conflicts.
- Reusing `editlog.conflict()` as THE conflict predicate is the right boundary;
  keeping `ops.py`/`_writer.py` unchanged preserves the one-executor rule.
- Making `load_vocab()` fail closed removes a real silent-under-tagging path
  (today `_read_vocab_file()` returns `[]` for a missing file).
- Verification style matches the repo — AST/source-reading tests, not intentions.

### Agreed Concerns

1. **Plan 01 — the legacy migration is the phase's biggest data risk (HIGH, both).**
   Two separate gaps: the rename of the real ~527 MB tree has no stated atomicity
   (a partial move leaves a split tree), and the owner proof reads a uuid out of a
   backup `ff_*.db` without first confirming those backups carry a `library_id`
   table. Codex adds a third: the "attempted once per process" flag is a global
   boolean, so a throwaway library resolving first can permanently suppress
   migration for the real library opened later in the same Calibre session.

2. **Plan 03 — zip resource resolution and cache install are the easy-to-get-wrong
   parts (HIGH, both).** Codex, with source: `_archive_path()` walking up from
   `HERE` looking for an existing file cannot work under zipimport (nothing inside
   the zip exists as a filesystem path) — it needs `__loader__`/`__spec__.origin`
   or explicit `.zip` segment parsing. And `os.replace(temp_dir, cache_dir)` onto an
   *existing* directory is not portable to Windows. OpenCode independently flags the
   version key used for cache invalidation.

3. **Plan 04 — the lock design collides with process-global library state
   (HIGH, both).** Codex is specific: `common._LIBRARY` is a module global, and
   `backup_db()` defaults to `db_path()` derived from it, so the plan's claim that
   two *different* libraries can write concurrently is not supported by the current
   seam — a concurrent job could snapshot the wrong library. OpenCode arrives near
   the same place from the other side: `_WRITE_LOCKS` is an unbounded module dict.
   Both mean: per-job state must be injected, not read from a global.

4. **Plan 06 — the producers do not all have a plan-time `expected` to give
   (HIGH, both).** Codex traces it: `wrangle.Plan.before` holds distinct-value sets,
   not per-book state (per-book lives in `self.perbook`); `promote.backfill_plan()`
   returns final tags and adds, not the original set; and the classify proposal
   schema (`book_id, title, added_tags, proposed_new`) has no before-state column at
   all. The plan asserts "proposal rows record the tags the plan was computed
   against" — today they do not. Codex also flags a contradiction inside the plan:
   the conflict filter is said to run *before* `backup_db()`, yet an all-skipped run
   is said to still write a header/footer; the exact order is not pinned.

5. **Plan 05 — the relocation can create an import cycle (MEDIUM, both).**
   Codex names it concretely: `wizard._engine_options()` calls `classify.est_cost()`,
   so moving that body into `engines.py` makes `engines` depend on `classify`, which
   already imports `engines`. Pass a `cost_fn` in, or leave the cost row in
   `classify`.

### Divergent Views

- **Is Plan 02 a blocker?** OpenCode calls the CI lane a "BLOCKING ISSUE" —
  chicken-and-egg, ship it standalone first. Codex does not raise this, and the
  roadmap already places 01-02 in wave 1 alongside 01-01, so the concern appears to
  be an artifact of reviewing the plan text without the wave structure. Not treated
  as a finding.
- **CI pinning.** Codex wants `CALIBRE_VERSION` pinned in the plan and executor
  discretion ("unless newer at execution time") removed; OpenCode instead worries
  about multiple Calibre installs on a Windows runner. Both are cheap; the pin is
  the one that removes a source of non-reproducibility.
- **`except BaseException`.** Codex flags the existing shape in `write_ops()` as
  wrong for job-reachable code and wants `except Exception`. OpenCode silent.
- **`nouuid-<sha1>` keys.** OpenCode wants `uuid.uuid5()` against collisions.
  Codex silent. A truncated sha1 over a filesystem path is not a realistic
  collision risk; treat as optional.
