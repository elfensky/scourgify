---
phase: 02-write-verbs-on-a-selection
reviewed: 2026-09-09T12:20:17Z
depth: standard
files_reviewed: 27
files_reviewed_list:
  - plugin/__init__.py
  - plugin/action.py
  - plugin/config.py
  - plugin/jobs.py
  - plugin/picker.py
  - plugin/result_dialog.py
  - plugin/selftest.py
  - src/scourgify/classify.py
  - src/scourgify/common.py
  - src/scourgify/engines.py
  - src/scourgify/overrides.py
  - src/scourgify/promote.py
  - src/scourgify/setup.py
  - src/scourgify/staleness.py
  - src/scourgify/synopsis.py
  - src/scourgify/ui.py
  - src/scourgify/wrangle.py
  - tests/test_classify_run.py
  - tests/test_defaults_resource.py
  - tests/test_editlog.py
  - tests/test_engines.py
  - tests/test_overrides.py
  - tests/test_plugin_jobs.py
  - tests/test_plugin_source.py
  - tests/test_promote.py
  - tests/test_synopsis.py
  - tests/test_wizard.py
  - tests/test_write_path.py
findings:
  critical: 1
  warning: 1
  info: 1
  total: 3
status: issues_found
---

# Phase 02: Code Review Report

**Reviewed:** 2026-09-09T12:20:17Z
**Depth:** standard
**Files Reviewed:** 27
**Status:** issues_found

## Summary

This phase's core write-path logic (`plugin/jobs.py`, the six write-producing tool modules,
`common.py`'s shared pre-write protocol) is unusually mature and well-tested: every PLAN/EXECUTE
job body, the apply-time conflict filter, the write-run lock, the shadow-replay parity between the
CLI and in-process transports, and the key-resolution/redaction rules are pinned by direct unit
tests (`tests/test_plugin_jobs.py`, `tests/test_write_path.py`, `tests/test_engines.py`). No
architectural-invariant violations were found: `ops.py`/`_writer.py` were not part of this diff and
were left untouched; `plugin/action.py` holds zero core imports outside `job_*` dispatch (verified
by `tests/test_plugin_source.py`, which is itself sound); every job function accepts
`abort`/`log`/`notifications`; guards raise `GuardrailError`, never `SystemExit`; keys are injected
via `env=` and never touch `os.environ`; `redact()` is applied at the one point every failure
reason is built.

One genuine, reproducible defect was found in `plugin/selftest.py` (Critical — see CR-01): it
references a `config.prefs` attribute that `plugin/config.py` never defines (the module only
exposes the function `_prefs()`, imported from `jobs.py`). Because `plugin/selftest.py` is
explicitly exempt from the AST-based source checks in `tests/test_plugin_source.py` and is never
executed under CI, this bug is invisible to the test suite and will only surface the first time
someone runs the documented `SCOURGIFY_SMOKE=1` acceptance procedure — at which point it corrupts
the user's real stored API key and prevents the smoke harness from ever exercising the seven
write-verb chains (staleness/wrangle/classify/synopsis/promote/backfill/retry) this phase exists to
acceptance-test.

A minor duplication (`_synopsis_ticks`/`_promote_ticks` in `plugin/action.py`, byte-identical
bodies) is noted as a Warning, and a cosmetic mismatch in `job_verify`'s apple-engine refusal is
noted as Info.

## Critical Issues

### CR-01: `plugin/selftest.py` references `config.prefs`, which does not exist — crashes the smoke harness and corrupts the user's real stored API key

**File:** `plugin/selftest.py:327` and `plugin/selftest.py:465`
**Issue:**

`plugin/config.py` imports the stored-key store as a **function**, not an object:

```python
# plugin/config.py:34
from calibre_plugins.scourgify.jobs import job_verify, stored_keys, _prefs
```

`config.py` never defines (or re-exports under another name) a module-level attribute named
`prefs`. The only names it exposes related to storage are `stored_keys()` and the imported
`_prefs()` function (called as `_prefs()` at `config.py:185` and `config.py:187`).

`plugin/selftest.py`'s `step_settings()` nonetheless does:

```python
# plugin/selftest.py:327
mode = oct(os.stat(config.prefs.file_path).st_mode & 0o777)
```

and `finish()` does:

```python
# plugin/selftest.py:465
self.cfg.prefs['keys'] = self.saved_keys      # put the user's own keys back
```

Both raise `AttributeError: module 'calibre_plugins.scourgify.config' has no attribute 'prefs'`.

Trace the consequence in order:

1. `step_settings()` snapshots the user's real stored keys into `self.saved_keys` (this part
   works — it calls `config.stored_keys()`, which correctly delegates to `jobs._prefs()`).
2. It then types a fake key into the OpenAI field and calls `w.save_settings()`, which **succeeds**
   and persists the fake key into the real, on-disk `JSONConfig('plugins/scourgify')` store,
   **overwriting whatever real key was stored there**.
3. The very next line (`config.prefs.file_path`) raises `AttributeError`, which propagates out of
   `step_settings()`.
4. `Harness.next()`'s `try/except Exception` catches it, logs the exception, and sets
   `self.steps = [self.finish]`.
5. `finish()` runs next. Since `self.cfg` was set in step 1, it tries `self.cfg.prefs['keys'] =
   self.saved_keys` to restore the real key — and raises the **same** `AttributeError`.
6. `finish()` was itself invoked as a `step()` inside `next()`'s `try/except`, so this second
   exception is caught too, `self.steps` is reset to `[self.finish]` again, and `next()` is
   rescheduled — an infinite retry loop that fails at the identical line every time.

Net effect: the real stored API key is left permanently overwritten with the throwaway fake key
(`sk-fake-selftest-key-do-not-use-0000`), the harness never reaches `finish()`'s success path
(never writes `SCOURGIFY_SMOKE_OUT`, never restores the saved key, never quits Calibre), and — because
`step_settings` runs before `step_staleness`/`step_wrangle`/`step_classify`/`step_synopsis`/
`step_promote`/`step_backfill`/`step_retry` in `Harness.start()`'s step list — **none of this
phase's seven write-verb acceptance chains are ever exercised** by the documented
`SCOURGIFY_SMOKE=1 calibre --with-library <throwaway>` procedure (CLAUDE.md's own manual
pre-release check).

This is invisible to CI: `tests/test_plugin_source.py`'s AST-based structural checks explicitly
scope themselves to modules that matter for the four invariants they enforce, and neither
`selftest.py` nor `config.py`'s attribute surface is checked there; `tests/test_plugin_jobs.py`
never imports `plugin/config.py` or `plugin/selftest.py` at all (both require `qt.core`/Calibre).
A repo-wide grep and an AST cross-reference (this review's own tooling) confirm `config.prefs` is
referenced nowhere else and is the only such dangling cross-module attribute reference in `plugin/`.

**Fix:**
```python
# plugin/selftest.py — use the function that actually exists, exactly as config.py itself does
mode = oct(os.stat(config._prefs().file_path).st_mode & 0o777)
...
self.cfg._prefs()['keys'] = self.saved_keys      # put the user's own keys back
```
or, if a public accessor is preferred (keeping `_prefs` genuinely private), add one to
`plugin/config.py`:
```python
def prefs():
    """Public accessor for the lazily-constructed stored-key JSONConfig — selftest.py needs the
    file handle to check chmod and to restore the real keys after a probe."""
    return _prefs()
```
and call `config.prefs()` (as a function) from `selftest.py`. Either fix should be paired with a
regression: run `plugin/selftest.py`'s `Harness.step_settings`/`finish` logic (with `qt.core`
mocked out, mirroring how `tests/test_plugin_jobs.py` fakes the Calibre-only pieces) so this class
of dangling-attribute bug cannot reappear silently in a file CI never executes.

## Warnings

### WR-01: `_synopsis_ticks` and `_promote_ticks` are byte-identical functions

**File:** `plugin/action.py:648-680`
**Issue:** `_synopsis_ticks(dlg)` (lines 648-664) and `_promote_ticks(dlg)` (lines 667-680) have
word-for-word identical bodies — both guard on `dlg is None or not reviewed or dlg._table is None`,
both call `picker.checklist_decide(dlg)`, both call `decide('', dlg._items)`, both return
`[(list(acc), list(rej), action)]`. Only the docstrings differ (explaining, correctly, why each
verb's review is single-call-shaped). This is a real DRY violation: a future change to the
single-call tick-building logic (e.g. a new review outcome, or a change to how `dlg._items` is
captured) has to be made in two places, and nothing enforces that it is — a maintainer who fixes
one will plausibly miss the other, since the two functions look purpose-specific from their names
and doc comments even though the code is not.
**Fix:** Collapse to one function and call it from both dispatch sites:
```python
def _single_call_ticks(dlg):
    """Build the single-call `ticks` list a second EXECUTE dispatch replays — shared by synopsis
    and promote, whose reviews (unlike wrangle's per-book one) call `decide=` exactly once for the
    whole batch."""
    if dlg is None or not getattr(dlg, 'reviewed', False) or dlg._table is None:
        return []
    from calibre_plugins.scourgify.picker import checklist_decide
    decide = checklist_decide(dlg)
    acc, rej, action = decide('', dlg._items)
    return [(list(acc), list(rej), action)]
```
and update the two call sites (`_finish_synopsis`, `_finish_promote`) to call
`_single_call_ticks(dlg)`.

## Info

### IN-01: `job_verify('apple', ...)`'s refusal carries a class not present in `VERDICT`

**File:** `plugin/jobs.py:86-87`, `plugin/config.py:169`
**Issue:** `job_verify` returns `{'engine': 'apple', 'ok': False, 'cls': '', 'detail': 'on-device —
nothing to verify'}` for the on-device engine (it is refused before construction, matching the
`Verify` button being disabled for `apple` in the UI). `_verified()` in `config.py` renders the
failure via `VERDICT.get(r.get('cls'), 'failed')`; since `''` is not a key in `VERDICT`, this reads
as the generic `'failed'` rather than something like "on-device — nothing to verify". In practice
this is unreachable through the UI (the Verify button is disabled for `apple`), but
`plugin/selftest.py:step_probe_guards` calls `job_verify('apple', ...)` directly to exercise the
guard, and if that code path were ever wired to render `_verified()`'s text, the message shown
would be misleading.
**Fix:** Either give `apple`'s refusal a real class (e.g. reuse `engines.CLASSES`'s shape with a
dedicated `'unusable'` or `'skip'` sentinel) and add it to `VERDICT`, or have `_verified()` prefer
`r.get('detail')` over the class-derived text when `cls` is falsy.

---

_Reviewed: 2026-09-09T12:20:17Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
