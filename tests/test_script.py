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
