# Scriptable TUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the wizard's prompts be answered from a canned list instead of the keyboard, so its flows can be tested in-process in CI and driven ad-hoc from a shell.

**Architecture:** One module-level queue of answers in `common.py`, loaded either from `$SCOURGIFY_SCRIPT` (ad-hoc shell use) or a `scripted_answers([...])` context manager (tests). The five prompt functions in `ui.py` plus `common.confirm` render exactly as they do today, then pop the next answer instead of blocking. An exhausted queue or an answer that isn't an offered key raises `common.ScriptError` — deliberately **not** a `SystemExit`, because `wizard._stage_guard` catches `SystemExit` and would swallow it. `wizard.py` is not modified at all.

**Tech Stack:** Python 3.10+ stdlib, `rich` (already a dependency), plain-assert tests run by `uv run tests/test_*.py` (also pytest-collectable).

**Spec:** `docs/superpowers/specs/2026-07-27-scriptable-tui-design.md`

## Global Constraints

- **No new dependencies.** Python stdlib + `rich` only.
- **`_writer.py` must never import `rich`, `ui`, `wizard`, or `report`** — it runs under `calibre-debug`'s Python, which has empty site-packages. This plan does not touch it.
- **`common.py` must stay importable under `calibre-debug`'s Python** — stdlib only, no `rich`. `contextlib` is stdlib and fine.
- **Paths are functions, never import-time constants** — except `_script`, which reads `$SCOURGIFY_SCRIPT` once at import by design (tests use the context manager; shell use sets the variable before launch).
- **Tests use plain `assert`, no framework.** Each file ends with the repo's `if __name__ == "__main__":` runner block (copy it verbatim from `tests/test_wizard.py:78-82`).
- **Tests never touch a real Calibre library** — `CALIBRE_LIBRARY` always points into a `tempfile.TemporaryDirectory()`.
- **New `tests/test_*.py` files are in CI automatically** — `ci.yml` runs them by glob. No workflow edit needed.
- **Commit style:** conventional commits (`feat:`, `test:`, `docs:`, `refactor:`). Work lands on `develop`; history stays linear.
- **`SCOURGIFY_SCRIPT` is a test hook, not a user feature** — documented in `CLAUDE.md`, absent from `--help`.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `src/scourgify/common.py` | Modify — owns the answer queue, `ScriptError`, the y/n parser, and the `interactive()` override. The single source of "are we scripted". | 1 |
| `tests/test_script.py` | Create — pins the queue and the six prompt functions. No library, no network. | 1, 2 |
| `src/scourgify/ui.py` | Modify — the five prompt functions consult the queue. Rendering is untouched. | 2 |
| `tests/test_wizard_flow.py` | Create — drives real wizard stages against a fixture library, asserts on the transcript. | 3 |
| `tests/drive_wizard.py` | Modify — shrinks to a single real-PTY smoke lap. | 4 |
| `CLAUDE.md` | Modify — documents the hook. | 4 |

`wizard.py` appears nowhere. That is the point of the design.

---

## Task 1: The answer queue in `common.py`

**Files:**
- Modify: `src/scourgify/common.py` (add after the `interactive`/`confirm` block at lines 86-109)
- Create: `tests/test_script.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces, all in `scourgify.common`:
  - `class ScriptError(Exception)`
  - `scripted() -> bool`
  - `script_next(what: str) -> str`
  - `script_bool(msg: str, default: bool) -> bool`
  - `scripted_answers(answers: list[str])` — a `contextlib.contextmanager`
  - `interactive() -> bool` — existing, now returns `True` whenever `scripted()`

- [ ] **Step 1: Write the failing test**

Create `tests/test_script.py`:

```python
#!/usr/bin/env python3
"""Pins the scripted-answer seam — the queue in common.py and the prompt functions in ui.py that
pop from it. The seam exists so the wizard's flows can be driven without a keyboard; these tests
pin the part that makes that SAFE: a short or bogus script must fail LOUDLY, never quietly succeed.
No framework:  uv run tests/test_script.py   (also collectable by pytest). No library, no network."""
import contextlib, io, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from scourgify import common


def test_unscripted_by_default():
    assert common.scripted() is False


def test_scripted_answers_pops_in_order_then_restores():
    with common.scripted_answers(["a", "b"]):
        assert common.scripted() is True
        assert common.script_next("first") == "a"
        assert common.script_next("second") == "b"
    assert common.scripted() is False          # the context manager restores the previous state


def test_exhausted_queue_raises_naming_the_prompt():
    with common.scripted_answers([]):
        try:
            common.script_next("menu 'classify scope'")
            assert False, "an exhausted script must raise, not return"
        except common.ScriptError as e:
            assert "classify scope" in str(e)   # the message names what went unanswered


def test_script_error_is_not_a_systemexit():
    """The whole point of the seam. wizard._stage_guard catches SystemExit (that's the
    guardrail-skips-a-stage path) — if ScriptError were one, a bad script would be swallowed
    and the run would report success having tested nothing."""
    assert not issubclass(common.ScriptError, SystemExit)
    assert issubclass(common.ScriptError, Exception)


def test_scripted_run_counts_as_interactive_even_under_ci():
    """interactive() must check the script BEFORE the CI/NONINTERACTIVE override, or every
    scripted test dies at the wizard's TTY gate the moment it runs in CI."""
    saved = os.environ.get("CI")
    os.environ["CI"] = "1"
    try:
        assert common.interactive() is False
        with common.scripted_answers(["q"]):
            assert common.interactive() is True
    finally:
        os.environ.pop("CI", None) if saved is None else os.environ.__setitem__("CI", saved)


def test_script_bool_parses_yes_no_and_blank_default():
    with common.scripted_answers(["y", "n", "", "", "YES", "No"]):
        assert common.script_bool("go?", default=False) is True
        assert common.script_bool("go?", default=True) is False
        assert common.script_bool("go?", default=True) is True     # '' = press enter = the default
        assert common.script_bool("go?", default=False) is False
        assert common.script_bool("go?", default=False) is True    # case-insensitive
        assert common.script_bool("go?", default=True) is False


def test_script_bool_rejects_garbage():
    with common.scripted_answers(["maybe"]):
        try:
            common.script_bool("apply?", default=False)
            assert False, "a non-y/n answer must raise"
        except common.ScriptError as e:
            assert "maybe" in str(e) and "apply?" in str(e)


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run tests/test_script.py`
Expected: FAIL — `AttributeError: module 'scourgify.common' has no attribute 'scripted'`

- [ ] **Step 3: Write the implementation**

In `src/scourgify/common.py`, add `contextlib` to the import line at the top (line 12):

```python
import os, re, sys, csv, time, glob, sqlite3, contextlib, collections, unicodedata
```

Then insert this block **immediately before** the existing `def interactive()` (currently line 87, under the `# ---------------- interaction policy ...` comment):

```python
class ScriptError(Exception):
    """A scripted run's answers don't fit the flow (queue exhausted, or an answer that isn't on
    offer). Deliberately NOT a SystemExit: wizard._stage_guard absorbs SystemExit — that's the
    guardrail-skips-a-stage path — which would swallow exactly the failure this seam exists to
    make loud, and hand back a green test that asserted nothing."""


# Canned answers for a scripted run: `SCOURGIFY_SCRIPT=w,s,n,q scourgify` for ad-hoc shell use,
# or common.scripted_answers([...]) in tests. Read ONCE at import — tests use the context manager,
# so this only constrains shell use, where the variable is set before launch anyway.
# ponytail: split on ',', so a checklist multi-toggle in an env-var script uses spaces ("1 3").
_script = ([a.strip() for a in os.environ["SCOURGIFY_SCRIPT"].split(",")]
           if "SCOURGIFY_SCRIPT" in os.environ else None)


def scripted() -> bool:
    """Is this run answering its own prompts? ([] still counts — the run IS scripted, it has
    merely run out, and the next prompt must raise rather than fall back to a keyboard.)"""
    return _script is not None


def script_next(what: str) -> str:
    """Pop the next canned answer. Exhausted = a real error: the script under-specifies the flow.
    `what` names the prompt so the failure says which one went unanswered."""
    if not _script:
        raise ScriptError(f"scripted run: no answer left for {what}")
    return _script.pop(0)


def script_bool(msg: str, default: bool) -> bool:
    """Pop a y/n answer ('' = the default, i.e. pressing enter). The ONE parser — ui.confirm and
    confirm() below both route here so the two prompts can't disagree about what 'y' means."""
    a = script_next(f"confirm {msg!r}").strip().lower()
    if a == "": return default
    if a in ("y", "yes"): return True
    if a in ("n", "no"): return False
    raise ScriptError(f"confirm {msg!r}: {a!r} is not y/n (or '' for the {default} default)")


@contextlib.contextmanager
def scripted_answers(answers):
    """Drive a scripted run from Python (tests). Restores the previous queue on exit, so a test
    that raises mid-flow can't leak its leftovers into the next one."""
    global _script
    prev, _script = _script, [str(a) for a in answers]
    try:
        yield
    finally:
        _script = prev
```

Then change `interactive()` to check the script **first** — the `CI` guard must not win, or every scripted test dies at the wizard's TTY gate:

```python
def interactive() -> bool:
    """stdin AND stdout are real TTYs, and no CI/NONINTERACTIVE override. Every tool asks this
    function — ui.interactive re-exports it — so the tools can't disagree about interactivity."""
    if _script is not None: return True     # a scripted run answers its own prompts; CI must not veto it
    if os.environ.get("CI") or os.environ.get("NONINTERACTIVE"):
        return False
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        return False
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run tests/test_script.py`
Expected: PASS — `7 tests passed`

- [ ] **Step 5: Verify nothing else regressed**

Run: `for f in tests/test_*.py; do echo "== $f"; uv run "$f" || break; done`
Expected: every file passes. `interactive()` is load-bearing across the tools, so a regression here surfaces broadly.

- [ ] **Step 6: Commit**

```bash
git add src/scourgify/common.py tests/test_script.py
git commit -m "feat(common): a queue of canned answers for scripted runs

The one owner of 'is this run answering its own prompts'. ScriptError is
deliberately not a SystemExit — _stage_guard absorbs those, which would
swallow a bad script and hand back a green test that asserted nothing.
interactive() checks the queue before the CI override, or every scripted
test dies at the wizard's TTY gate."
```

---

## Task 2: The prompt functions consult the queue

**Files:**
- Modify: `src/scourgify/ui.py` (`clear` L22, `menu` L35-44, `confirm` L47-48, `pause` L51-55, `checklist` L58-83)
- Modify: `src/scourgify/common.py` (`confirm`, L98-109)
- Modify: `tests/test_script.py` (append tests)

**Interfaces:**
- Consumes from Task 1: `common.scripted()`, `common.script_next(what)`, `common.script_bool(msg, default)`, `common.scripted_answers(answers)`, `common.ScriptError`.
- Produces: no new names. `ui.menu`, `ui.confirm`, `ui.checklist`, `ui.pause`, `ui.clear`, `common.confirm` keep their exact current signatures and return types; they gain scripted behaviour only.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_script.py`, above the `if __name__ == "__main__":` block:

```python
# ---- the prompt functions (ui.py) ----
# ui hard-imports rich (a declared dependency), so this is importable in any install.
from scourgify import ui


@contextlib.contextmanager
def transcript():
    """Capture what the prompts render. rich resolves sys.stdout at write time, so redirecting
    it catches ui.console without reaching into rich's internals."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        yield buf


OPTS = [("a", "apply", "write it"), ("r", "review", "1-by-1"), ("s", "skip", "nothing")]


def test_menu_returns_the_scripted_key_and_echoes_it():
    with common.scripted_answers(["r"]), transcript() as buf:
        assert ui.menu("proposal", OPTS) == "r"
    out = buf.getvalue()
    assert "apply" in out and "review" in out      # still rendered in full — scripting is not silencing
    assert "r" in out.split("choose")[-1]          # and the answer is echoed, so the log reads like a session


def test_menu_blank_answer_takes_the_default():
    with common.scripted_answers([""]), transcript():
        assert ui.menu("proposal", OPTS, default="s") == "s"
    with common.scripted_answers([""]), transcript():
        assert ui.menu("proposal", OPTS) == "a"    # no default given -> the first key, as when unscripted


def test_menu_rejects_a_key_that_is_not_on_offer():
    """A typo must NOT walk a stray path. Unscripted, rich re-asks and eats the next answer,
    shifting everything after it by one — the silent-drift failure this seam removes."""
    with common.scripted_answers(["z"]), transcript():
        try:
            ui.menu("proposal", OPTS)
            assert False, "a key that isn't on offer must raise"
        except common.ScriptError as e:
            assert "z" in str(e) and "proposal" in str(e)


def test_menu_accepts_an_also_key():
    with common.scripted_answers(["q"]), transcript():
        assert ui.menu("proposal", OPTS, also=("q",)) == "q"   # unrendered aliases still validate


def test_confirm_routes_through_the_shared_parser():
    with common.scripted_answers(["y", "n", ""]), transcript():
        assert ui.confirm("apply?") is True
        assert ui.confirm("apply?", default=True) is False
        assert ui.confirm("apply?", default=True) is True


def test_common_confirm_is_scriptable_too():
    """promote.backfill() and rollback prompt through common.confirm, not ui.confirm.
    Unscripted off a TTY it returns the default; scripted it must obey the queue."""
    with common.scripted_answers(["y"]):
        assert common.confirm("restore?", default=False) is True


def test_checklist_skip_rejects_everything():
    with common.scripted_answers(["s"]), transcript():
        acc, rej, action = ui.checklist("#1 Book", ["Time Loop", "Fix-It"])
    assert action == "skip" and acc == [] and rej == [0, 1]


def test_checklist_toggles_then_applies():
    """Two pops: one to untick item 2, one blank to apply what's left ticked."""
    with common.scripted_answers(["2", ""]), transcript():
        acc, rej, action = ui.checklist("#1 Book", ["Time Loop", "Fix-It", "Angst"])
    assert action == "apply" and acc == [0, 2] and rej == [1]


def test_checklist_empty_items_never_prompts():
    with common.scripted_answers([]):                  # an empty queue proves nothing was popped
        assert ui.checklist("#1 Book", []) == ([], [], "apply")


def test_pause_and_clear_consume_nothing():
    """Neither asks a question — pause only waits and clear only wipes the screen. Making
    scripts carry a blank for them would be noise, and an ANSI clear mid-transcript is noise too."""
    with common.scripted_answers([]), transcript() as buf:
        ui.pause()
        ui.clear()
    assert buf.getvalue() == ""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run tests/test_script.py`
Expected: FAIL — the first new test raises `rich.prompt` `EOFError` or hangs on stdin, because `ui.menu` still calls `Prompt.ask`. (If it hangs, Ctrl-C; that hang *is* the failure.)

- [ ] **Step 3: Write the implementation**

In `src/scourgify/ui.py`, change the import at line 9 so the module can reach the queue:

```python
from scourgify import common
from scourgify.common import interactive    # re-export: the ONE tty policy lives in common
```

(`common` does not import `ui`, so there is no cycle.)

Replace `clear` (line 22):

```python
def clear():
    if common.scripted(): return          # an ANSI screen-wipe mid-transcript is noise
    console.clear()
```

Replace the tail of `menu` (line 43-44) so the render is untouched and only the read changes:

```python
    keys = [k for k, _, _ in options] + list(also)
    default = default or keys[0]
    if common.scripted():
        ans = common.script_next(f"menu {title!r} (choices: {'/'.join(keys)})").strip()
        if ans == "": ans = default                    # '' = pressing enter = take the default
        if ans not in keys:
            raise common.ScriptError(f"menu {title!r}: {ans!r} is not one of {'/'.join(keys)}")
        say(f"[dim]choose:[/] {ans}")                  # echo, so a captured run reads like a session
        return ans
    return Prompt.ask("choose", choices=keys, default=default, console=console)
```

Replace `confirm` (lines 47-48):

```python
def confirm(msg, default=False):
    if common.scripted():
        ans = common.script_bool(msg, default)
        say(f"[dim]{msg}[/] {'y' if ans else 'n'}")
        return ans
    return Confirm.ask(msg, default=default, console=console)
```

Replace `pause` (lines 51-55):

```python
def pause():
    if common.scripted(): return           # it asks nothing, it only waits
    try:
        Prompt.ask("[dim]enter to return to the menu[/]", default="", show_default=False, console=console)
    except (EOFError, KeyboardInterrupt):
        pass
```

In `checklist`, replace the single read line (line 75):

```python
        raw = Prompt.ask("choose", default="", show_default=False, console=console).strip().lower()
```

with:

```python
        if common.scripted():
            # ponytail: no key validation here — the toggle loop below already ignores anything
            # that isn't an in-range digit, and a script of pure garbage self-limits by exhausting
            # the queue into a ScriptError rather than spinning.
            raw = common.script_next(f"checklist {title!r}")
            say(f"[dim]choose:[/] {raw or '⏎'}")
        else:
            raw = Prompt.ask("choose", default="", show_default=False, console=console)
        raw = raw.strip().lower()
```

Finally, in `src/scourgify/common.py`, make `confirm` (lines 98-109) honour the queue — add one branch at the top of the body, leaving the rest exactly as it is:

```python
def confirm(msg: str, default: bool = False) -> bool:
    """The one plain y/n prompt (ui.confirm is its rich twin for wizard surfaces).
    Off a TTY / on EOF: the default. 3 retries on garbage input."""
    if scripted(): return script_bool(msg, default)
    if not interactive(): return default
    ...
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run tests/test_script.py`
Expected: PASS — `17 tests passed`

- [ ] **Step 5: Verify the seam end-to-end by hand**

Run: `SCOURGIFY_SCRIPT="q" uv run scourgify 2>&1 | tail -20`
Expected: the wizard's header panel and landing menu render, `choose: q` is echoed, and it exits 0 — where before this task the same command printed argparse help (`wrangle.py:550` sends non-interactive runs to `print_help`).

Then confirm the loud-failure path:

Run: `SCOURGIFY_SCRIPT="" uv run scourgify 2>&1 | tail -5; echo "rc=$?"`
Expected: a `ScriptError` traceback naming the landing menu, and a **non-zero** exit. A zero exit here means `ScriptError` is being swallowed — stop and fix it before continuing, that is the exact bug this design exists to prevent.

- [ ] **Step 6: Run the whole suite**

Run: `for f in tests/test_*.py; do echo "== $f"; uv run "$f" || break; done`
Expected: every file passes.

- [ ] **Step 7: Commit**

```bash
git add src/scourgify/ui.py src/scourgify/common.py tests/test_script.py
git commit -m "feat(ui): prompts pop a canned answer when the run is scripted

menu/confirm/checklist still render exactly as before — only the read
changes — and echo what they chose so a captured run reads like a session.
menu validates against its own keys: unscripted, rich re-asks on a bad key
and eats the next answer, shifting every later one by a place.
pause and clear consume nothing; neither asks a question."
```

---

## Task 3: In-process wizard flow tests

**Files:**
- Create: `tests/test_wizard_flow.py`

**Interfaces:**
- Consumes from Tasks 1-2: `common.scripted_answers`, `common.ScriptError`, and the scripted prompt behaviour of `ui.*`.
- Consumes existing: `fixture_db.build(path, books, custom=(), link_labels=())`, `common.data_dir()`, `wizard._run()`, `wizard.stage_classify()`, `wizard.stage_review()`, `classify.clear_caches()`.
- Produces: nothing imported elsewhere. Terminal task for the test surface.

**Note on flow tests:** each test drives the *smallest* entry point that exercises its concern — a single stage where possible, `_run()` only for the whole-lap test. A stage called directly has a short, predictable prompt sequence; the full lap does not, which is why only one test carries that risk.

- [ ] **Step 1: Write the failing test**

Create `tests/test_wizard_flow.py`:

```python
#!/usr/bin/env python3
"""Drives the REAL wizard stages against a throwaway fixture library, answering their prompts from
a canned list (common.scripted_answers) instead of a keyboard. In-process — no PTY, no subprocess,
milliseconds — so the interaction FLOW is finally pinned in CI. tests/drive_wizard.py still covers
the one thing this can't: that a real terminal works at all.

What it pins: no-write paths write nothing; a skipped classify scope reaches no engine (no spend);
a step review that skips every book leaves the proposal byte-identical; a stage guardrail skips the
stage, not the session; and a script that runs short RAISES rather than exiting 0.
No framework:  uv run tests/test_wizard_flow.py   (also collectable by pytest). No Calibre, no network."""
import contextlib, io, os, sys, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fixture_db import build
from scourgify import classify, common, wizard

COLS = ["fandoms", "characters", "relationships", "genres", "status", "updated", "wrangled"]
BOOKS = [{"id": 1, "title": "Fixture Book A", "added": "2026-01-01 10:00:00",
          "desc": "A description long enough to classify. " * 3, "tags": ["Keeper"]},
         {"id": 2, "title": "Fixture Book B", "added": "2026-02-01 10:00:00",
          "desc": "Another perfectly serviceable description. " * 3}]
PROPOSAL = ("book_id,title,added_tags,proposed_new\n"
            "1,Fixture Book A,Time Loop,\n"
            "2,Fixture Book B,Fix-It,\n")


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


@contextlib.contextmanager
def wizard_lib(proposal=None):
    """A throwaway library with every column the wizard checks, a minimal config.toml (so the
    wizard doesn't divert into setup), and optionally a pending classify proposal.
    NEVER the user's real library — CALIBRE_LIBRARY always points into a tempdir here."""
    with tempfile.TemporaryDirectory() as td:
        lib = os.path.join(td, "library"); os.makedirs(lib)
        build(os.path.join(lib, "metadata.db"), BOOKS, custom=[(c, {}) for c in COLS]).close()
        home = os.path.join(td, "home")
        with env(SCOURGIFY_HOME=home, CALIBRE_LIBRARY=lib, COLUMNS="100", NONINTERACTIVE=None):
            os.makedirs(common.data_dir())
            with open(os.path.join(home, "config.toml"), "w") as f:
                f.write('[columns]\n[behavior]\n[overrides]\ndir = "overrides"\n')
            prop = os.path.join(common.data_dir(), "classify_proposal.csv")
            if proposal:
                with open(prop, "w") as f: f.write(proposal)
            classify.clear_caches()
            try:
                yield prop
            finally:
                classify.clear_caches()


@contextlib.contextmanager
def transcript():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        yield buf


def test_classify_scope_skip_reaches_no_engine():
    """The cost pin. Picking 'skip' at the scope menu must send nothing — a regression here
    spends real money (a full Gemini pass over the library is ~EUR 50)."""
    def boom(*a, **k):
        raise AssertionError("classify.plan must not run after a scope-skip")
    saved = classify.plan
    classify.plan = boom
    try:
        with wizard_lib(), common.scripted_answers(["s"]), transcript() as buf:
            wizard.stage_classify()
    finally:
        classify.plan = saved
    assert "nothing tagged" in buf.getvalue()


def test_step_review_skipping_every_book_leaves_the_proposal_byte_identical():
    """The data-loss pin (the bug class fixed in ff74991/05d1dbe): deciding nothing must not
    rewrite, truncate, or archive the proposal."""
    with wizard_lib(PROPOSAL) as prop, common.scripted_answers(["r", "s", "s"]), transcript() as buf:
        wizard.stage_review()
    assert "nothing decided" in buf.getvalue()
    with open(prop) as f:
        assert f.read() == PROPOSAL


def test_review_discard_archives_without_writing():
    with wizard_lib(PROPOSAL) as prop, common.scripted_answers(["d"]), transcript() as buf:
        wizard.stage_review()
    assert "set aside" in buf.getvalue()
    assert not os.path.exists(prop)                                   # archived, not applied
    archived = [f for f in os.listdir(common.data_dir()) if "discarded" in f]
    assert len(archived) == 1


def test_review_keep_leaves_the_proposal_pending():
    with wizard_lib(PROPOSAL) as prop, common.scripted_answers(["k"]), transcript() as buf:
        wizard.stage_review()
    assert "kept pending" in buf.getvalue()
    with open(prop) as f:
        assert f.read() == PROPOSAL


def test_a_guardrail_skips_the_stage_not_the_session():
    """_stage_guard absorbs SystemExit so one refusing stage doesn't end the run. Pinned because
    Task 1 deliberately made ScriptError NOT a SystemExit — this is the behaviour that forced it."""
    def boom(): raise SystemExit("guardrail: refusing to empty a populated column")
    with wizard_lib(), transcript() as buf:
        assert wizard._stage_guard(boom) is False
        assert wizard._stage_guard(lambda: None) is True
    assert "guardrail" in buf.getvalue()


def test_a_script_error_is_not_absorbed_by_the_stage_guard():
    """The safety net itself. If _stage_guard ever swallows a scripting failure, every flow test
    above can pass while asserting nothing — the exact failure the seam was built to remove."""
    def short(): raise common.ScriptError("scripted run: no answer left for menu 'proposal'")
    with wizard_lib(), transcript():
        try:
            wizard._stage_guard(short)
            assert False, "_stage_guard must not absorb a ScriptError"
        except common.ScriptError:
            pass


def test_landing_menu_quits_cleanly():
    with wizard_lib(), common.scripted_answers(["q"]), transcript() as buf:
        wizard._run()
    out = buf.getvalue()
    assert "Fixture Book" in out or "2 books" in out          # the header read the fixture library
    assert "what would you like to do?" in out
    assert "pick up where you left off" in out                # the clean-exit line


def test_a_short_script_raises_instead_of_exiting_zero():
    """The single most important test in this file. Unscripted, running out of input raises
    EOFError, which wizard.run() catches and turns into a clean exit 0 — a test written that way
    stops halfway and reports success. A short script must be LOUD."""
    with wizard_lib(), common.scripted_answers([]), transcript():
        try:
            wizard._run()
            assert False, "a script with no answers must raise, not exit cleanly"
        except common.ScriptError as e:
            assert "no answer left" in str(e)


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run tests/test_wizard_flow.py`
Expected: FAIL. Before Tasks 1-2 it fails on `common.scripted_answers` not existing; after them, any failure is a genuine flow mismatch — see Step 3.

- [ ] **Step 3: Reconcile the answer sequences against the real flow**

The stage-level answer lists above are derived from reading `wizard.py`, not from running it. If a test raises `ScriptError`, **the message names the exact prompt that went unanswered** — append its answer to that test's list and re-run. Two known-tricky spots:

- `stage_review` with `PROPOSAL` reaches the four-key menu (`a`/`r`/`k`/`d`) because both rows carry tags. `"r"` routes into `classify.apply_proposal_step`, which raises one `ui.checklist` per tagged book — hence `["r", "s", "s"]` for two books.
- `test_landing_menu_quits_cleanly` reaches the landing menu directly only if `snapshot()["setup_needed"]` is False. The fixture writes `config.toml` and creates all seven columns for exactly that reason. If the run diverts into setup, the ScriptError will say so — check the column list matches `wizard.COLS`.

Assert on **short, distinctive substrings**. rich wraps at the console width, so a long phrase can be split across lines and fail a naive `in` check. `COLUMNS=100` is set in the harness to keep wrapping stable.

This step changes only `tests/test_wizard_flow.py` — if you find yourself editing `wizard.py`, stop: the design's central claim is that it needs no changes, and a required edit is a finding worth reporting rather than absorbing.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run tests/test_wizard_flow.py`
Expected: PASS — `8 tests passed`

- [ ] **Step 5: Confirm no real library was touched**

Run: `uv run tests/test_wizard_flow.py && ls ~/.config/scourgify/data 2>/dev/null | head`
Expected: tests pass, and no new proposal or archive files appear in the real user dir. Every test redirects `SCOURGIFY_HOME`; a stray file means one leaked.

- [ ] **Step 6: Commit**

```bash
git add tests/test_wizard_flow.py
git commit -m "test(wizard): pin the interaction flows in-process

Drives the real stages with canned answers instead of a PTY: no-write paths
write nothing, a skipped classify scope reaches no engine, a skip-all step
review leaves the proposal byte-identical, and a short script raises instead
of exiting 0. Milliseconds, so it lives in CI."
```

---

## Task 4: Shrink the PTY harness and document the hook

**Files:**
- Modify: `tests/drive_wizard.py` (replace `RULES` L27-35, `CHECKS` L37-47, `MENU_KEYS` L24, and the proposal-intact assertion L107-109)
- Modify: `CLAUDE.md` (the verification paragraph that currently describes `drive_wizard.py`)

**Interfaces:**
- Consumes from Task 3: the flow coverage that `drive_wizard.py` is handing over.
- Produces: nothing. Terminal task.

- [ ] **Step 1: Establish the baseline**

Run: `time uv run tests/drive_wizard.py`
Expected: PASS, ~15s. Record the wall time; Step 4 should be materially faster.

- [ ] **Step 2: Shrink it**

In `tests/drive_wizard.py`, replace the docstring (lines 1-13) with:

```python
#!/usr/bin/env python3
"""Manual PTY smoke test — proves the wizard starts under a REAL terminal. Deliberately NOT named
test_* — it shells out to `uv run`, so the CI glob skips it; run it locally before a release:

    uv run tests/drive_wizard.py

Scope is deliberately one lap: process starts, header renders, landing menu appears, `q` quits
cleanly. That covers what an in-process test cannot — rich's Prompt against a real tty, terminal
detection, the checklist's live redraw. The interaction FLOWS moved to tests/test_wizard_flow.py,
which drives the same stages with canned answers in milliseconds instead of regex-matching a
transcript for 15 seconds. Rendering aesthetics stay a human's job."""
```

Replace `MENU_KEYS` (line 24) and `RULES` (lines 27-35) with:

```python
# One lap: the landing menu appears, we quit. Flow coverage lives in tests/test_wizard_flow.py.
MENU_KEYS = ["q"]
RULES = []                                   # no mid-flow prompts to answer on a bare quit
```

Replace `CHECKS` (lines 37-47) with:

```python
CHECKS = [
    (r"Fixture Book|2 books", "header shows the fixture library"),
    (r"what would you like to do\?", "landing menu appeared under a real pty"),
    (r"pick up where you left off", "quit exits through the clean-exit path"),
]
```

Delete the proposal-intact assertion (lines 107-109) and its `ok &=` — that pin now lives in
`test_wizard_flow.py::test_step_review_skipping_every_book_leaves_the_proposal_byte_identical`:

```python
        print("=" * 72)
        if not ok:
```

The `prop_path` / `prop_before` setup (lines 62-65) can stay: a pending proposal is realistic
header state, and the header check reads it.

- [ ] **Step 3: Run it**

Run: `time uv run tests/drive_wizard.py`
Expected: PASS on all three checks, `rc=0`, and visibly faster than the Step 1 baseline (one menu round-trip instead of eight).

- [ ] **Step 4: Document the hook in `CLAUDE.md`**

In the "Verification:" paragraph, replace the sentence describing `drive_wizard.py` with:

```
`uv run tests/test_wizard_flow.py` drives the real wizard stages in-process with canned answers
(`common.scripted_answers`) against a fixture library — the interaction flows (no-write paths,
classify scope-skip spending nothing, a skip-all step review leaving the proposal byte-identical)
are pinned in CI in milliseconds. The same seam drives the wizard from a shell:
`SCOURGIFY_SCRIPT="w,s,n,q" scourgify` answers each prompt in order — a **test hook, not a user
feature** (no `--help` entry). A script that runs short or names a key that isn't on offer raises
`common.ScriptError`, which is deliberately NOT a `SystemExit`: `wizard._stage_guard` absorbs those,
and swallowing a scripting failure would hand back a green run that asserted nothing.
`uv run tests/drive_wizard.py` (NOT in CI; a few seconds) stays the pre-release check that a real
PTY works at all — header, landing menu, clean quit.
```

- [ ] **Step 5: Run the whole suite plus the PTY check**

Run: `for f in tests/test_*.py; do echo "== $f"; uv run "$f" || break; done && uv run tests/drive_wizard.py`
Expected: every test file passes, then the PTY lap passes.

- [ ] **Step 6: Commit**

```bash
git add tests/drive_wizard.py CLAUDE.md
git commit -m "test(wizard): shrink the PTY harness to a start-and-quit lap

The flows moved to test_wizard_flow.py, which drives the same stages in
milliseconds instead of regex-matching a transcript for 15s. What's left is
the one thing an in-process test can't do: prove a real terminal works.
Documents SCOURGIFY_SCRIPT as a test hook."
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §1 queue, `ScriptError`, `scripted_answers`, `interactive()` ordering | 1 |
| §2 `menu`/`confirm`/`checklist`/`pause`/`clear` + `common.confirm` | 2 |
| §2 `ScriptError` not a `SystemExit` + its own test | 1 (unit), 3 (`_stage_guard` behaviour) |
| §3 five pinned flows + the seam's own test | 3 |
| §4 `drive_wizard.py` shrink | 4 |
| Known edges: comma-split, import-time read, output-coupled assertions | 1 (`ponytail:` comment), 2 (`ponytail:` comment), 3 (Step 3 note) |
| Non-goal: `wizard.py` unmodified | enforced in Task 3 Step 3 |
| Non-goal: `SCOURGIFY_SCRIPT` out of `--help` | 4 Step 4 |

**Type consistency:** `scripted()→bool`, `script_next(what: str)→str`, `script_bool(msg: str, default: bool)→bool`, `scripted_answers(answers)` used identically in Tasks 2 and 3. `ui.menu`/`confirm`/`checklist` keep their existing signatures and return types (`str`, `bool`, `(list, list, str)`).

**Known risk, flagged rather than hidden:** Task 3's answer sequences are derived from reading `wizard.py`, not from executing it. Task 3 Step 3 is the reconciliation step, and `ScriptError` naming the unanswered prompt is what makes it a two-minute fix instead of a hunt. Stage-level tests were chosen over one big lap specifically to shrink this surface.
