# Testing Patterns

**Analysis Date:** 2026-08-28

## Test Framework

**Runner:**
- Plain Python with `uv run tests/test_*.py`
- Also pytest-compatible: `pytest tests/test_*.py`
- No test framework dependency (pytest optional, not required)
- Config: `.github/workflows/ci.yml` runs `uv run "$t"` for each test file

**Assertion Library:**
- Plain `assert` statements (no pytest fixtures, decorators, or special assertion methods)
- Comments explain what's being tested

**Run Commands:**
```bash
uv run tests/test_core.py              # Run one test file
uv run tests/test_core.py && echo ok   # Exit code on success
for t in tests/test_*.py; do uv run "$t"; done   # CI matrix: all tests
```

**CI Matrix (3.10 / 3.13 / 3.14):**
- Runs every test file on Python 3.10 (floor), 3.13 (current), 3.14 (Calibre's bundled version)
- New test file added by creating `tests/test_*.py` — CI picks it up automatically
- No test discovery config needed

## Test File Organization

**Location:**
- `tests/test_*.py` for test files (CI globs `tests/test_*.py`)
- `tests/fixture_db.py` for shared fixture builder (imported by test files)
- `tests/drive_wizard.py` for manual pre-release PTY check (not in CI)
- `tests/smoke_calibre.py` for manual pre-release under Calibre's Python

**Naming:**
- Test functions: `test_*` (plain functions, no class wrapping)
- Test files: `test_*.py` (lowercase with underscores)
- Helper functions: `_*` (leading underscore for private helpers)

**Entry Point Pattern:**
```python
if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
```
- Allows `uv run tests/test_file.py` to self-run
- Prints test names and count on completion
- Exit on first failure (no try/catch; failures stop the run)

## Test Structure

**Imports and Setup:**
```python
#!/usr/bin/env python3
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from scourgify import common, classify
```

**Helper Functions:**
- Keep test data builders as module-level functions (e.g., `maps(**over)` in test_core.py)
- Use nested context managers for setup/teardown
- Return only what tests need

**Test Functions:**
```python
def test_transform_fandom_alias():
    nd, lf, lc = transform({"fandoms": ["HP"]}, maps(fan={"HP": "Harry Potter"}), BEH)
    assert nd["fandoms"] == ["Harry Potter"] and not lf and not lc
```
- One assertion per test OR multiple related assertions (read top-to-bottom as the story)
- Docstring optional if test name is self-explanatory; use docstring for context/rationale

**Cleanup Pattern:**
```python
try:
    # test body
finally:
    # restore state
```

## Fixtures and Factories

**Throwaway metadata.db:**
- `fixture_db.build(path, books, custom=(), link_labels=(), multi_labels=(), uuid=None)`
- `books`: `[{"id": 1, "title": "...", "added": "...", "desc": "...", "tags": [...]}]`
- `custom`: `[(label, {book_id: value}), ...]` for custom columns
- `link_labels`: which custom columns use link-table storage shape
- Returns sqlite3.Connection (close after use, or rely on tempdir cleanup)
- Example (`test_selection.py`):
```python
def _con(link=False):
    path = os.path.join(tempfile.mkdtemp(), "metadata.db")
    return build(path, BOOKS, custom=[("updated", UPDATED), ("wrangled", STAMPED)],
                 link_labels=("updated",) if link else ())
```

**Fake API/Engine Classes:**
- Implement only the methods under test (minimal surface)
- Use descriptive names (e.g., `FakeApi`, `FakeEngine`, `FakeLegacy`)
- Example (`test_classify_run.py`):
```python
class FakeEngine:
    def __init__(self, model, timeout): pass
    def ask(self, prompt):
        if "MARKER-ERR" in prompt: raise RuntimeError("blocked:TEST")
        if "MARKER-HIT" in prompt: return '{"tags": [...]}'
        return "no json here"
```

**Environment and File Management:**
```python
@contextlib.contextmanager
def env(**kv):
    saved = {k: os.environ.get(k) for k in kv}
    for k, v in kv.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    try:
        yield
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
```
- Temporarily override env vars, restore on exit
- Used with `with tempfile.TemporaryDirectory()` for isolated runs

## Mocking Strategies

**Monkeypatch (_post_json):**
```python
def _with_transport(resp, fn):
    real = engines._post_json
    calls = []
    engines._post_json = lambda url, headers, payload, timeout: (calls.append((url, headers, payload)), resp)[1]
    try:
        return fn(), calls
    finally:
        engines._post_json = real
```
- Save the real implementation
- Replace with a recording mock
- Call test function
- Restore real implementation
- Return test result and recorded calls

**Module Dict Mutation (ENGINES):**
```python
ENGINES["fake"] = FakeEngine
try:
    p = classify.plan(...)
    p.opts.engine = "fake"
    classify.classify_run(p)
finally:
    del ENGINES["fake"]
```
- Add test engine to shared dict
- Run test
- Delete test engine (restore invariant)

**run_writer Stub:**
- Tests of wrangle/classify/staleness don't actually write to Calibre
- `common.run_writer` is replaced with a recording callback
- Example: see `test_write_path.py` for the `FakeApi` + `FakeLegacy` approach

## Scripted Answers and Prompts

**Pattern:**
```python
from scourgify import common
with common.scripted_answers(["w", "3", "n", "q"]):
    wizard.stage_classify()
```
- Context manager sets global `_script` queue
- Prompts pop answers from the queue in order
- Empty string `""` means "take the default" (press enter)
- Exhausted queue → `ScriptError`, not a silent default walk

**Script Format:**
- `SCOURGIFY_SCRIPT="w,3,n,q" uv run scourgify` for shell use
- `common.scripted_answers([...])` for test use
- Blank/whitespace-only value → empty queue (first prompt raises)
- Menu keys are digits; `q` for quit

**Test Hooks:**
- `common.scripted_answers()`: inject prompt answers (tests)
- `SCOURGIFY_SCRIPT=value`: inject from environment (shell)
- `SCOURGIFY_HOME=temp`: inject home dir (tests)
- `CALIBRE_LIBRARY=path`: inject library path (tests)

## Coverage and Verification

**No Explicit Coverage:**
- No coverage tracking configured; tests are the gate
- New test file added by existing → picked up by CI
- If a change breaks a test, CI fails

**What is Tested:**

**Unit/Pure Core (test_core.py):**
- Normalization: `norm()`, `ascii_fold()`
- Trope chain resolution
- Transform logic: fandom aliases, character folding, genre routing
- No Calibre, no library, no network

**Selection Semantics (test_selection.py):**
- Which books an incremental/--last/--since run operates on
- Throwaway metadata.db via fixture_db
- Both custom-column storage shapes (link-table and inline)

**CLI Dispatch (test_cli.py):**
- Routing of subcommands to tool modules
- Wizard architectural line: source-reading test that wizard.py never drives ui.checklist, run_writer, or op_set_field
- Subcommand argv reframing

**Plugin Source Constraints (test_plugin_source.py):**
- Plugin never spawns run_writer (grepped from source)
- Core imports never run on GUI thread
- No core import at module level in action.py (except in job_* functions)
- Every job callback Dispatcher-wrapped
- Job functions accept abort/log/notifications
- Only one ThreadedJob call site (action._run)
- Settings dialog imports lazily
- Plugin-import marker file exists

**Plugin Safety (test_plugin_safety.py):**
- Rich is actually blockable (guard test)
- Every core module imports without rich
- ui.py and wizard.py refuse with GuardrailError (not SystemExit)
- CLI reports missing rich as plain text (no traceback)
- _writer.py and ops.py never import presentation modules
- No job-reachable code raises SystemExit (only run_writer and rollback_cmd exempt)
- `set_library()` injection seam works (path wins over env, env never mutated)

**Engine Seam (test_engines.py):**
- Cloud adapters extract responses correctly
- Gemini blocked content is a no-retry RuntimeError
- Missing key message names the env var
- Engine tables (PRICING, ENGINE_ENV, TRAITS) agree

**Classify Pipeline (test_classify_run.py):**
- Plan resolves scope and resume once
- Plan is isolated from caller mutation
- Run records hits, no-matches, and failures
- FakeEngine with markers (MARKER-HIT, MARKER-ERR) in description

**Write Path (test_write_path.py):**
- Coercion of JSON book IDs and value shapes
- Shadow replay: CLI and in-process writes agree
- FakeApi and FakeLegacy stubs for ops testing

**Wizard Flow (test_wizard_flow.py):**
- No-write paths write nothing
- Classify scope-skip reaches no engine (no spend)
- Step review that skips every book leaves proposal unchanged
- Stage guardrail skips the stage, not the session
- Script that runs short raises (not exits 0)
- Uses fixture_db and common.scripted_answers

**Selection and Synopsis (test_selection.py, test_synopsis.py, test_synopsis_queue.py):**
- Selection logic with different column storage shapes
- Synopsis verdict states (keep/generate/unparseable)
- Queue membership and refresh semantics

**Manual Pre-Release Checks:**

**tests/drive_wizard.py** (NOT in CI; requires real PTY):
- Runs a real interactive wizard session
- Verifies header, landing menu, clean quit
- Takes a few seconds

**calibre-debug -e tests/smoke_calibre.py** (manual):
- Core actually importing and running under Calibre's bundled Python 3.14.6
- Set `CALIBRE_LIBRARY` to exercise read paths
- Read-only (`ro_connect()`); safe with Calibre open

## Architectural Tests (Source Reading)

**Pattern:**
```python
import inspect
from scourgify import wizard
src = inspect.getsource(wizard)
body = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
for banned, why in [("ui.checklist", "moves to tool module"), ...]:
    assert banned not in body, f"wizard.py {why}"
```

**Why AST/Source Reading:**
- Architectural rules that are not testable via behavior alone
- Example: wizard must never drive ui.checklist (but wizard could have a secret second checklist implementation)
- Inspected via: `inspect.getsource()`, `ast.parse()`, `ast.walk()`, `ast.unparse()`

**Enforced Constraints:**
- wizard.py: never calls ui.checklist, run_writer, or op_set_field
- Plugin: never calls run_writer, subprocess, multiprocessing, ThreadPoolExecutor, os.system
- __init__.py: imports scourgify only inside load_actual_plugin's `with self:`
- action.py: all scourgify imports in job_* or _ functions
- test_plugin_safety.py: AST-validates no job-reachable code raises SystemExit (except two exemptions)

## Common Test Patterns

**Throwaway Temp Directory:**
```python
with tempfile.TemporaryDirectory() as td:
    lib = os.path.join(td, "library")
    os.makedirs(lib)
    build(os.path.join(lib, "metadata.db"), BOOKS, custom=[...]).close()
    # test here
```

**Setup via Harness:**
```python
@contextlib.contextmanager
def harness(books, custom=(("wrangled", {}),)):
    with tempfile.TemporaryDirectory() as td:
        lib = os.path.join(td, "library")
        os.makedirs(lib)
        build(os.path.join(lib, "metadata.db"), books, custom=custom).close()
        with env(SCOURGIFY_HOME=os.path.join(td, "home"), CALIBRE_LIBRARY=lib):
            os.makedirs(common.data_dir())
            classify.clear_caches()
            try:
                yield td
            finally:
                classify.clear_caches()
```

**Assertion Examples:**
```python
assert norm(" Harry  Potter ") == "harry potter"
assert r["A"] == ("C", "tag")
assert nd["fandoms"] == ["Harry Potter"] and not lf and not lc
assert {b for b, _ in p.targets} == {1, 2, 3}
```

---

*Testing analysis: 2026-08-28*
