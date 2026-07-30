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
        with wizard_lib(), common.scripted_answers(["4"]), transcript() as buf:
            wizard.stage_classify()
    finally:
        classify.plan = saved
    assert "nothing tagged" in buf.getvalue()


def test_step_review_skipping_every_book_leaves_the_proposal_byte_identical():
    """The data-loss pin (the bug class fixed in ff74991/05d1dbe): deciding nothing must not
    rewrite, truncate, or archive the proposal."""
    # NB: the file assertions live INSIDE wizard_lib — once it exits, the tempdir is gone and
    # common.data_dir() resolves back to the user's REAL ~/.config/scourgify.
    with wizard_lib(PROPOSAL) as prop:
        with common.scripted_answers(["2", "s", "s"]), transcript() as buf:
            wizard.stage_review()
        assert "nothing decided" in buf.getvalue()
        with open(prop) as f:
            assert f.read() == PROPOSAL


def test_review_discard_archives_without_writing():
    with wizard_lib(PROPOSAL) as prop:
        with common.scripted_answers(["4"]), transcript() as buf:
            wizard.stage_review()
        assert "set aside" in buf.getvalue()
        assert not os.path.exists(prop)                               # archived, not applied
        archived = [f for f in os.listdir(common.data_dir()) if "discarded" in f]
        assert len(archived) == 1


def test_review_keep_leaves_the_proposal_pending():
    with wizard_lib(PROPOSAL) as prop:
        with common.scripted_answers(["3"]), transcript() as buf:
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


def test_a_full_menu_lap_runs_every_task_without_writing():
    """One lap over the whole toolset — the coverage tests/drive_wizard.py used to carry before it
    shrank to a smoke check. Every task key 1..7 in turn, each answered onto a no-write outcome,
    then quit. Pins that the menu loop survives a full lap and that stage_staleness / stage_promote
    / stage_backfill / stage_overrides are reached at all — the four stages no other test drives.
    Nothing here may write to the library or reach an engine."""
    # menu answers are DIGITS (slot numbers, stable in every library state); the bare "s"/"n" are
    # ui.checklist skips and y/n confirms, which keep letters — see ui.checklist's exception.
    lap = ["1", "n",                  # wrangle  (clean fixture: no apply menu) -> decline staleness
           "2", "n",                  # staleness (already consistent)          -> decline classify
           "3", "4", "n",             # classify -> scope skip (slot 4)         -> decline review
           "4", "2", "s", "s", "n",   # review -> 1-by-1 (slot 2), SKIP both    -> decline promote
           "5", "n",                  # promote (no candidates)                 -> decline backfill
           "6",                       # backfill (nothing to do; no successor)
           "7",                       # overrides (no rejects logged; not in the workflow)
           "q"]
    with wizard_lib(PROPOSAL) as prop:
        with common.scripted_answers(lap), transcript() as buf:
            wizard._run()
        out = buf.getvalue()
        assert out.count("what would you like to do?") == 8       # the menu loop survived all 7 tasks
        for marker, stage in [("nothing to normalize", "wrangle"),
                              ("already consistent", "staleness"),
                              ("nothing tagged", "classify"),       # scope-skip honored: no engine, no spend
                              ("nothing decided", "review"),
                              ("no new-tag candidates yet", "promote"),
                              ("backfill applies", "backfill"),
                              ("no rejected changes logged", "overrides")]:
            assert marker in out, f"{stage} stage did not run"
        assert "pick up where you left off" in out                # quit landed on the clean-exit line
        with open(prop) as f:
            assert f.read() == PROPOSAL                           # skip-all review touched nothing
        # every write funnels through run_writer, which snapshots metadata.db first — so an empty
        # backups dir is proof no stage wrote, whatever it claimed on screen.
        assert not os.path.isdir(common.backups_dir()) or not os.listdir(common.backups_dir())


def test_a_short_script_raises_instead_of_exiting_zero():
    """The single most important test in this file. Unscripted, running out of input raises
    EOFError, which wizard.run() catches and turns into a clean exit 0 — a test written that way
    stops halfway and reports success. This drives wizard.run() itself (not the inner _run()),
    because run() is exactly the wrapper holding that except (KeyboardInterrupt, EOFError) clause —
    testing _run() would keep passing even if run()'s handler were carelessly widened to
    `except Exception`, which would silently swallow ScriptError and exit 0 again. A short script
    must be LOUD, and ScriptError must actually reach the caller through run()."""
    with wizard_lib(), common.scripted_answers([]), transcript():
        try:
            wizard.run()
            assert False, "a script with no answers must raise, not exit cleanly"
        except common.ScriptError as e:
            assert "no answer left" in str(e)


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
