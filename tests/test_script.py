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


# ---- _parse_script: how a raw SCOURGIFY_SCRIPT value becomes the answer queue ----
# `_script` itself is read once at import (real shell use sets the var before launch, so a live
# re-import isn't how this is exercised) — these pin the pure parse helper the initializer calls,
# which is what's actually testable and is the whole of the parsing logic.

def test_parse_script_blank_or_whitespace_yields_empty_queue():
    """An empty or whitespace-only value (e.g. SCOURGIFY_SCRIPT="$KEYS" with $KEYS unset) must
    parse to [] — 'this run is scripted, and it has no answers' — not [''] (a one-element queue
    holding a blank 'press enter' answer, which would silently walk every prompt's default: apply,
    full maintenance run). Naive ''.split(',') gives [''], which is the bug this pins against."""
    assert common._parse_script("") == []
    assert common._parse_script("   ") == []
    assert common._parse_script("\t") == []


def test_parse_script_nonblank_parses_as_before_trailing_blank_kept():
    """A non-empty value still splits on ',' and strips each entry; a genuine trailing blank
    (e.g. "4,") is kept as a real answer meaning 'press enter, take the default' — that's intended,
    interactive-equivalent behaviour, not the empty-queue case above. Do not strip it."""
    assert common._parse_script("w,s,n,q") == ["w", "s", "n", "q"]
    assert common._parse_script("4,") == ["4", ""]
    assert common._parse_script(" a , b ") == ["a", "b"]


def test_blank_script_is_scripted_with_empty_queue_raising_on_first_prompt():
    """The reviewer's hazard, end to end: a blank/whitespace-only SCOURGIFY_SCRIPT must still mark
    the run as scripted (scripted() True — CI/NONINTERACTIVE must not veto it either) but must
    raise at the very FIRST prompt rather than silently walking the wizard's apply/full-run
    defaults. scripted_answers() with the parsed (empty) queue is the equivalent of the module
    initializer having parsed SCOURGIFY_SCRIPT="" at import."""
    with common.scripted_answers(common._parse_script("")):
        assert common.scripted() is True
        try:
            common.script_next("first prompt")
            assert False, "a blank SCOURGIFY_SCRIPT must raise at the very first prompt"
        except common.ScriptError:
            pass


def test_absent_script_env_var_is_not_scripted():
    """No SCOURGIFY_SCRIPT at all must give None (not scripted), distinct from the blank-but-present
    case above ([] , scripted). The initializer only calls _parse_script when the key IS present, so
    'absent' never reaches the parser — this pins that the two cases stay distinguishable via
    scripted(), which the whole design (script_next / interactive) keys off."""
    assert "SCOURGIFY_SCRIPT" not in os.environ
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


OPTS = [("1", "apply", "apply", "write it"), ("2", "review", "review", "1-by-1"),
        ("3", "skip", "skip", "nothing")]


def test_menu_returns_the_symbolic_id_not_the_key():
    """The key is what you type; the id is what the caller dispatches on. Keeping them apart is
    what stops a conditional row from making a digit mean two different things."""
    with common.scripted_answers(["2"]), transcript() as buf:
        assert ui.menu("proposal", OPTS) == "review"
    out = buf.getvalue()
    assert "apply" in out and "review" in out      # still rendered in full — scripting is not silencing
    assert "2" in out.split("choose")[-1]          # and the answer is echoed, so the log reads like a session


def test_menu_blank_answer_takes_the_default():
    with common.scripted_answers([""]), transcript():
        assert ui.menu("proposal", OPTS, default="skip") == "skip"
    with common.scripted_answers([""]), transcript():
        assert ui.menu("proposal", OPTS) == "apply"   # no default -> the first row, as when unscripted


def test_menu_rejects_a_default_that_is_not_an_enabled_row():
    """rich hands a default straight back without checking it against the choices, so a stale one
    used to escape on the interactive path only — green CI, broken wizard. Caught at build time,
    and as a ValueError rather than an assert so -O cannot strip it."""
    for bad in ("nope", "1"):                      # a wrong id, and a KEY passed where an id belongs
        try:
            with transcript(): ui.menu("proposal", OPTS, default=bad)
            assert False, f"default {bad!r} must raise"
        except ValueError as e:
            assert "default" in str(e)


def test_menu_disabled_row_keeps_its_slot_but_cannot_be_chosen():
    """An inapplicable row greys out instead of vanishing, so the numbering never shifts under a
    user (or a script) that has memorised it."""
    opts = [("1", None, "unavailable", "nothing to do"), ("2", "go", "go", "")]
    with common.scripted_answers(["2"]), transcript() as buf:
        assert ui.menu("t", opts) == "go"
    assert "unavailable" in buf.getvalue()         # still rendered, just dimmed
    with common.scripted_answers(["1"]), transcript():
        try:
            ui.menu("t", opts); assert False, "a disabled row must not be selectable"
        except common.ScriptError:
            pass


def test_ask_int_raises_on_garbage_when_scripted():
    """Interactive: re-ask. Scripted: RAISE — re-asking would silently eat the next canned answer
    and shift the queue by one, the drift this seam exists to make loud."""
    with common.scripted_answers([""]), transcript():
        assert ui.ask_int("how many", 100) == 100          # blank takes the default
    with common.scripted_answers(["250"]), transcript():
        assert ui.ask_int("how many", 100) == 250
    for bad in ("abc", "0", "9999"):
        with common.scripted_answers([bad]), transcript():
            try:
                ui.ask_int("how many", 100, lo=1, hi=500)
                assert False, f"{bad!r} must raise, not re-ask"
            except common.ScriptError:
                pass


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
        assert ui.menu("proposal", OPTS, also=("q",)) == "q"   # unrendered aliases stand for themselves


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


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
