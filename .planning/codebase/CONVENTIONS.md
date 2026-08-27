# Coding Conventions

**Analysis Date:** 2026-08-28

## Naming Patterns

**Files:**
- Module files: lowercase with underscores (`common.py`, `classify.py`, `_writer.py`)
- Shebang line: `#!/usr/bin/env python3` on all module files

**Functions:**
- Public functions: `lowercase_with_underscores`
- Private/internal functions: prefix with `_` (`_post_json`, `_extract_tropes`)
- Injection seam parameters: lowercase (`env=None`, `ask=None`, `decide=None`)

**Variables:**
- Module constants: `UPPER_CASE` (`HERE`, `DEFAULTS`, `BACKUP_KEEP`, `MAX_WORKERS`)
- Local variables: `lowercase_with_underscores`
- Private module state: prefix with `_` (`_LIBRARY`, `_script`)

**Types:**
- Use modern Python type hints: `str | None`, `tuple[int, int]`, `list[dict]`
- Return type annotations required for public functions
- Parameter type hints required

**Classes:**
- PascalCase (`FakeEngine`, `GuardrailError`, `ScriptError`)
- Private helper classes: prefix with `_` (e.g., `_Chat`)

## Code Style

**Formatting:**
- No explicit formatter configured; implicitly follows PEP 8
- Shebang + module docstring at top of every file
- 4-space indentation

**Linting:**
- Bandit security scan configured (see pyproject.toml `[tool.bandit]`)
- No ruff/pylint/flake8 config — tests are the gate
- 10 specific security checks exempted (reviewed and safe per CLAUDE.md)

**Docstrings:**
- Module docstrings: explain the module's purpose and ownership (e.g., "The ONE owner of...")
- Function docstrings: start with one-line summary, then details if needed
- Include implementation details and edge cases (e.g., "Deliberately an ordinary Exception, not a SystemExit")
- Class docstrings: explain purpose and when to use

## Import Organization

**Order:**
1. Standard library (`os`, `sys`, `sqlite3`, `json`, etc.)
2. Third-party (`rich`, `urllib`)
3. Local scourgify modules (`from scourgify import ...`, `from scourgify.common import ...`)

**Path Aliases:**
- No path aliases configured; always use full `scourgify.module` paths

**Rich handling:**
- Core modules: lazy import under try/except; `RICH = False` if ImportError
- `ui.py` and `wizard.py`: hard-import rich (refusal with GuardrailError if missing)
- `_writer.py` and `ops.py`: NEVER import rich, ui, wizard, or report
- Test patterns: `sys.path.insert(0, ...)` for src discovery

## Error Handling

**GuardrailError (not SystemExit):**
- All guards, refusals, and validation errors raise `common.GuardrailError`
- Reason: job-reachable code (Calibre plugin paths) must be catchable by `except Exception`
- SystemExit in plugin code escapes ThreadedJob and kills the worker thread silently
- Exceptions: `run_writer` and `rollback_cmd` (CLI-only funnels; plugin cannot reach them)
- Example: `raise common.GuardrailError("Set CALIBRE_LIBRARY to...")`

**Rich ImportError handling:**
- In `ui.py`: catch ImportError, raise `common.GuardrailError` (not SystemExit)
- In `report.py`: catch ImportError, set `RICH = False`, provide fallback renderers
- Rationale: Calibre's bundled Python has empty site-packages; rich is optional there

**Stdlib-only under calibre-debug:**
- Files `_writer.py` and `ops.py` run under `calibre-debug -e`
- Never import: rich, ui, wizard, report, or any scourgify module
- Only safe imports: core pure modules (common, select, artifacts, etc. if they depend only on stdlib)

## Injection Seams

**env parameter (key resolution):**
- Every engine class constructor: `def __init__(self, model, timeout, env=None)`
- `env` defaults to `os.environ` if None, allowing tests and plugin key override
- Pattern: `self.key = (os.environ if env is None else env).get(self.ENV)`
- Used to inject test keys without mutating global os.environ

**ask parameter (prompt callback):**
- Promote's adversarial refereeing: `def backfill(decide=None)`
- Plans use `ask=` for confirmation prompts
- Test pattern: pass a lambda or mock function to override behavior

**decide parameter (decision callback):**
- Similar to `ask=`; allows callers to inject custom decision logic
- Example: `common.scripted_answers([...])` context manager replaces global `_script`

## Logging and Debugging

**Logging framework:** None
- Use `print()` for console output, wrapped by `report.say()` for styled output
- Rich console in `report.py` (shared) and `ui.py` (wizard-only)
- No logger module; output is direct to stdout/stderr

**Comments:**
- When to comment: non-obvious logic, design trade-offs, rationale for a guard or check
- Example: `# a Script that runs short RAISES rather than exiting 0.`
- Avoid comments restating code (e.g., `i += 1  # increment i`)

**Module-level comments:**
- Explain module's ownership and responsibility
- Reference CLAUDE.md constraints (e.g., "runs under calibre-debug")

## Function Design

**Size:** Functions typically 10–50 lines; complex flows broken into named helpers
- Example: `transform()` is the core but delegates to `resolve_trope_chains()`, `is_junk()`, etc.

**Parameters:**
- Keep parameter count low (≤5); use dicts or objects for complex inputs
- Use type hints for all parameters
- Injection seams (`env=`, `ask=`, `decide=`) are keyword-only (defaults to None)

**Return Values:**
- Single return type per function (never mixed types)
- Multi-value returns use tuples: `return (tagged, failed, proposed_new)`
- Unpack at call site: `tagged, failed = transform(...)`

## Module Design

**Exports:**
- Public API: functions and classes without leading `_`
- Private helpers: leading `_` (e.g., `_post_json` is the transport seam, not public)
- Everything else is implementation detail

**Barrel Files:**
- No `__all__` declarations
- Import only what you need: `from scourgify.common import norm, ascii_fold`

**Module State:**
- Lazy initialization; never exit at import time (guard with `try/except` for optional deps)
- Per-run state: file-based (`data/`, `overrides/`) not module globals
- Shared state: `_script`, `_LIBRARY` are module-level variables (commented as process-global)

**Initialization Functions:**
- `load_maps()`: flattens and injects the config layers (defaults ← config.toml ← overrides)
- `clear_caches()`: used in tests to reset vocab/config caches between runs
- Library resolution is LAZY: `common.library()` raises GuardrailError if unset, never at import

## Testing Seams and Patterns

**Monkeypatching:**
- Save real: `real = engines._post_json`
- Replace: `engines._post_json = lambda url, headers, payload, timeout: ...`
- Restore in finally: `engines._post_json = real`

**Dict injection:**
- ENGINES: `ENGINES["fake"] = FakeEngine` for test engines; delete after: `del ENGINES["fake"]`
- Module module-level dicts support runtime test overrides

**Fixture functions:**
- `fixture_db.build(path, books, custom=[], link_labels=(), multi_labels=())`: throwaway metadata.db
- Returns sqlite3.Connection; close after use or let tempdir cleanup

**Context managers:**
- `common.scripted_answers([...])`: sets global `_script` queue for prompt replay
- `env(**kv)`: temporarily override environment variables, restore on exit
- `@contextlib.contextmanager`: used throughout for setup/teardown

**Source reading (AST-based tests):**
- `inspect.getsource(module)`: read module source; verify architectural lines (e.g., wizard.py never calls ui.checklist)
- `ast.parse()`: parse source into AST tree
- `ast.walk()`, `ast.unparse()`: traverse and validate constraints
- Used in: test_cli.py, test_plugin_source.py, test_plugin_safety.py

---

*Convention analysis: 2026-08-28*
