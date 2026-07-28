# Scriptable TUI — driving and testing the wizard without a human

**Date:** 2026-07-27
**Status:** design approved, not yet implemented

## Problem

The wizard's interaction flow can't be exercised cheaply. Two consequences:

1. **No CI coverage of the flow.** `tests/test_wizard.py` pins only the pure helpers
   (`_scope_options`, `_task_hint`, `_engines`). Every stage's actual menu-and-confirm path is
   unpinned — including the ones where a regression loses data or spends money.
2. **No way to drive it from a shell.** After changing a stage there is no cheap way to run the
   wizard and read back what it did.

`tests/drive_wizard.py` covers the flow today by opening a pseudo-terminal, launching
`uv run scourgify` as a subprocess, and matching prompts with a table of nine regexes against the
accumulated transcript. It takes ~15s, is deliberately excluded from the CI glob, and stalls
silently when a prompt fails to match its pattern.

### Why the obvious cheap fix is not enough

Rich's `Prompt.ask`/`Confirm.ask` read piped stdin correctly (verified). So relaxing the TTY gate
at `wrangle.py:550` — one line — would let `printf '3\ns\nq\n' | scourgify` drive the wizard.

That one-liner is rejected because of how it fails:

- When the piped answers run out, rich raises `EOFError`. `wizard.run()` catches `EOFError` and
  **exits cleanly with status 0**. A script one answer short stops halfway and reports success — a
  test written that way passes while asserting nothing.
- On an invalid key, `Prompt.ask` re-asks, consuming the *next* piped line as the retry. Every
  answer after it shifts by one and the run walks a different path. Still exit 0.
- Tests would need a subprocess per flow (~1–2s each) and could only assert on stdout and files.

Silent-pass is worse than no test. The seam below exists to make both failures loud.

## Approach

A single queue of canned answers in `common.py`. The prompt functions render exactly as they do
now, then take the next answer off the queue instead of blocking on the keyboard.

`wizard.py` is not modified. Its stages keep their current mixed logic-and-I/O shape; that shape
stopped being a testability problem the moment the prompts became drivable. Peeling more stages
into pure/drive halves (the `_scope_options` pattern) is explicitly **not** part of this work —
do it later, per-stage, only if a specific stage resists testing.

## Design

### 1. The queue (`common.py`)

```python
class ScriptError(Exception):
    """A scripted run's answers don't fit the flow. Deliberately NOT SystemExit: wizard's
    _stage_guard absorbs SystemExit (that's the guardrail path), which would swallow exactly
    the failure this whole seam exists to make loud."""

# canned answers for scripted runs: tests (scripted_answers) and ad-hoc `SCOURGIFY_SCRIPT=w,s,n,q`
_script = ([a.strip() for a in os.environ["SCOURGIFY_SCRIPT"].split(",")]
           if "SCOURGIFY_SCRIPT" in os.environ else None)

def scripted() -> bool:
    return _script is not None

def script_next(what: str) -> str:
    """Pop the next canned answer. Exhausted = a real error — the script under-specifies the flow."""
    if not _script:
        raise ScriptError(f"scripted run: no answer left for {what}")
    return _script.pop(0)

@contextlib.contextmanager
def scripted_answers(answers):          # tests drive through this, not the env var
    global _script
    prev, _script = _script, list(answers)
    try: yield
    finally: _script = prev
```

`interactive()` gains one line at the top:

```python
if _script is not None: return True     # a scripted run answers its own prompts
```

This must come **before** the existing `CI`/`NONINTERACTIVE` check, or every scripted run under CI
dies at the wizard's TTY gate. It also makes bare `scourgify` launch the wizard instead of printing
help (`wrangle.py:550`), with no change needed there.

### 2. The prompt functions consult it (`ui.py`, `common.confirm`)

Each still renders its table/panel/question as today, then:

| function | scripted behaviour |
|---|---|
| `ui.menu` | pop; **validate against the option keys** (`keys` already computed, incl. `also`); echo `choose: <ans>` so the transcript reads like a session |
| `ui.confirm` | pop; `y`/`yes` → True, `n`/`no` → False, `""` → the default; anything else is an error |
| `common.confirm` | same as `ui.confirm` — it is the plain-terminal twin, used by `promote.backfill` and `rollback` |
| `ui.checklist` | pop one raw line per redraw iteration; `s` / `a` / `""` / `1 3` behave exactly as typed |
| `ui.pause` | no-op — it asks nothing, it only waits; forcing scripts to carry a blank for it is noise |
| `ui.clear` | no-op — an ANSI clear mid-transcript is noise |

Both failure modes — an exhausted queue and an answer that isn't an offered key — raise
`common.ScriptError`, naming the prompt and the accepted keys.

`ScriptError` must **not** derive from `SystemExit`. `wizard._stage_guard` catches `SystemExit`
(that is the guardrail-skips-a-stage path) and `wizard.run()` catches `KeyboardInterrupt`/`EOFError`;
a plain `Exception` subclass passes through both and aborts the run non-zero. Getting this wrong
reintroduces the silent-pass failure the seam exists to eliminate, so it warrants its own test:
a script that is one answer short must raise, not exit 0.

### 3. `tests/test_wizard_flow.py` (new, in the CI glob)

Builds a throwaway library with `tests/fixture_db.build`, points `SCOURGIFY_HOME` at a tmpdir,
seeds a `classify_proposal.csv`, then wraps `wizard._run()` in `common.scripted_answers([...])`
plus `contextlib.redirect_stdout(io.StringIO())` and asserts on the captured transcript.
`redirect_stdout` catches `report.py`'s console too, since rich resolves `sys.stdout` at write
time; the classify dashboard renders on stderr and is unaffected.

Flows pinned:

| flow | assertion |
|---|---|
| menu lap | `1,2,3,4,5,6,7,q` — every task's no-write path; the menu loop survives a full lap |
| data-loss pin | a step review that skips every book leaves the proposal file **byte-identical** |
| cost pin | classify scope-skip reaches no engine and sends nothing |
| workflow | `run_workflow` end to end with every stage skipped |
| resilience | a stage's guardrail `SystemExit` skips that stage, not the session |
| the seam itself | a script one answer short raises `ScriptError` — it does **not** exit 0 |

The first three carry over from `drive_wizard.py`'s current checks; the last three are new — the
PTY harness could not afford them.

### 4. `tests/drive_wizard.py` shrinks

Reduced to one lap under a real pseudo-terminal: process starts, header renders, landing menu
appears, send `q`, exit 0. Nine regex rules become one. It stays out of the CI glob and stays the
pre-release check. Its job is now *"a real TTY works at all"* — rich's `Prompt`, TTY detection,
checklist redraw — not *"the flows are correct"*, which the in-process tests now own.

## Known edges

- Env-var scripts split on `,`, so a checklist multi-toggle must use spaces (`"1 3"`). Test scripts
  pass a Python list and are unaffected. Mark with a `ponytail:` comment.
- `SCOURGIFY_SCRIPT` is read once at import. Tests use the context manager, so this only constrains
  ad-hoc shell use, where the variable is set before launch anyway.
- Assertions run against rendered output, so rewording a prompt can fail a test. Accepted: at this
  size the wording is the product, and a failing test is how you learn you changed it.

## Non-goals

- Structured state inspection (asking the wizard "what would you show me now" and getting data
  back). Not needed for either want.
- Extracting further pure halves out of `wizard.py`.
- Exposing `SCOURGIFY_SCRIPT` as a user feature. It is a test hook: documented in `CLAUDE.md`,
  absent from `--help`.

## Scope

~40 lines of production code across `common.py` and `ui.py`, one new test file, one file shrunk.
