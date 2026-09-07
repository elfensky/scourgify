#!/usr/bin/env python3
"""The single `scourgify` command's argv dispatch — the routing table in cli.main().
No framework needed:  uv run tests/test_cli.py   (also collectable by pytest).
No Calibre, no library, no network: every tool's main() is stubbed with a recorder."""
import os, sys, io, contextlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from scourgify import cli, __version__
import scourgify.classify as classify, scourgify.staleness as staleness
import scourgify.promote as promote, scourgify.wrangle as wrangle, scourgify.common as common
import scourgify.overrides as overrides


def _run(argv, stubs=("classify", "staleness", "promote", "wrangle")):
    """Dispatch cli.main() with the given argv; each tool main()/cmd is a recorder.
    -> (dispatched_name, argv_seen_by_tool, printed_stdout)."""
    called = {}
    mods = {"classify": classify, "staleness": staleness, "promote": promote, "wrangle": wrangle}
    saved = {n: m.main for n, m in mods.items()}
    saved_overrides, saved_rollback = overrides.overrides_cmd, common.rollback_cmd
    def rec(name):
        def f(*a, **k): called["name"] = name; called["argv"] = list(sys.argv)
        return f
    try:
        for n in stubs:
            mods[n].main = rec(n)
        overrides.overrides_cmd = lambda a: called.update(name="overrides", argv=a)
        common.rollback_cmd = lambda a: called.update(name="rollback", argv=a)
        sys.argv = ["scourgify", *argv]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main()
    finally:
        for n, fn in saved.items(): mods[n].main = fn
        overrides.overrides_cmd, common.rollback_cmd = saved_overrides, saved_rollback
    return called.get("name"), called.get("argv"), out.getvalue()


def test_version_flag_prints_and_does_not_dispatch():
    for flag in ("--version", "-V"):
        name, _, out = _run([flag])
        assert name is None, f"{flag} must not dispatch to a tool"
        assert __version__ in out

def test_subcommands_route_to_their_tool():
    assert _run(["classify", "--incremental"])[0] == "classify"
    assert _run(["staleness", "--apply"])[0] == "staleness"
    assert _run(["promote", "--backfill"])[0] == "promote"

def test_overrides_and_rollback_use_cmd_handlers():
    name, argv, _ = _run(["overrides", "--apply"])
    assert name == "overrides" and argv == ["--apply"]          # gets argv tail, not sys.argv
    name, argv, _ = _run(["rollback", "--list"])
    assert name == "rollback" and argv == ["--list"]

def test_bare_and_unknown_fall_through_to_wrangle():
    assert _run([])[0] == "wrangle"                              # bare -> wrangle (which launches the wizard)
    assert _run(["audit"])[0] == "wrangle"                       # setup/audit/apply live in wrangle.main()
    assert _run(["totally-bogus"])[0] == "wrangle"               # unknown also falls through to wrangle

def test_subcommand_argv_is_reframed_for_the_tool():
    # cli rewrites sys.argv so the tool's own argparse sees prog + its own args, not "classify"
    _, argv, _ = _run(["classify", "--last", "30"])
    assert argv[0] == "scourgify classify" and argv[1:] == ["--last", "30"]


def test_every_reviewable_subcommand_exposes_step():
    """CLAUDE.md: a wizard stage calls the SAME engine function the subcommand does. So every
    stage that offers a 1-by-1 review must have a --step on the CLI too, or the wizard can do
    something the CLI cannot. promote/staleness/overrides shipped the wizard half first."""
    import inspect
    from scourgify import classify, promote, staleness, wrangle, overrides
    for mod in (classify, promote, staleness, wrangle, overrides):
        # staleness/wrangle/overrides build their parser inside main(), so check the source
        assert '"--step"' in inspect.getsource(mod), f"{mod.__name__} exposes no --step"


def test_the_wizard_asks_but_never_does_the_work():
    """The architectural line, made mechanical rather than remembered.

    The wizard is a front door: it may ASK (menus, prompts) and then hand off. It may never carry
    the work itself, because the CLI is the other front door into the same functions and anything
    the wizard does privately is invisible to it. Both halves of this were violated in the same
    week: the 1-by-1 checklists were written inline in wizard.py (so promote/staleness/overrides
    had no CLI --step at all), and stage_backfill assembled its own run_writer call (silently
    dropping promote.backfill's per-book preview, and bypassing any guard added there later).

    A rule in a document is a rule someone has to remember; this one is checked."""
    import inspect
    from scourgify import wizard
    src = inspect.getsource(wizard)
    body = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    for banned, why in (("ui.checklist", "drives a review checklist — move it into the tool module"),
                        ("run_writer(", "assembles a write — call the tool's function, which guards it"),
                        ("op_set_field(", "builds a write op — that belongs with the tool's write path")):
        assert banned not in body, f"wizard.py {why}"
    from scourgify import promote, staleness, overrides
    for fn in (promote.apply_decisions_step, promote.backfill_step, staleness.step, overrides.step_pick):
        assert callable(fn)


def test_report_glyphs_survive_a_reconfigured_cp1252_stream():
    """The Windows regression found on ci.yml run 34144520181 (test-windows job): rendering a
    report.py table containing U+2212 (report.py's "dropped" marker, − — not a plain hyphen)
    raised UnicodeEncodeError when the destination stream's encoding was cp1252, Windows' legacy
    console default. cli.main() fixes this by reconfiguring sys.stdout/sys.stderr to UTF-8 with
    errors="replace" before dispatch (Windows only; report.py itself must never touch process
    streams as an import-time side effect — it runs inside Calibre jobs too).

    This test does not depend on sys.platform == "win32" (rich's own "legacy windows console"
    detection wouldn't even fire on macOS/Linux) — it tests the RECONFIGURE MECHANISM ITSELF, so
    the assertion is meaningful on every platform this suite runs on: wrap an io.TextIOWrapper
    around an in-memory buffer with encoding="cp1252" (Windows' failure mode, reproduced without
    Windows), confirm writing report.py's own glyphs to it actually raises without the fix (the
    premise is not vacuous), then apply the exact fix (`stream.reconfigure(encoding="utf-8",
    errors="replace")`) and confirm a real report.table() render through that same stream no
    longer raises."""
    from scourgify import report

    if not report.RICH:
        return  # nothing to reconfigure without rich; the plain-text path is plain print()

    glyph_rows = [["3", "tags", "− also (dropped)"], ["1", "genres", "→ Angst · ✓ done"]]

    def render_through(stream):
        real_console = report.console
        report.console = report.Console(file=stream)
        try:
            report.table("mass folds — same change on 3+ books", ["books", "where", "change"], glyph_rows)
            stream.flush()
        finally:
            report.console = real_console

    # 1. Premise check: an UN-reconfigured cp1252 stream really does crash on these glyphs —
    #    otherwise this test would pass vacuously regardless of the fix.
    buf = io.BytesIO()
    cp1252_stream = io.TextIOWrapper(buf, encoding="cp1252")
    raised = False
    try:
        render_through(cp1252_stream)
    except UnicodeEncodeError:
        raised = True
    assert raised, "fixture glyphs must be un-encodable in cp1252, or this test proves nothing"

    # 2. The actual fix, applied directly to the same kind of stream (mirrors cli.main() exactly,
    #    without depending on sys.platform == "win32" to exercise it).
    buf2 = io.BytesIO()
    fixed_stream = io.TextIOWrapper(buf2, encoding="cp1252")
    fixed_stream.reconfigure(encoding="utf-8", errors="replace")
    render_through(fixed_stream)  # must not raise

    # sanity: the bytes that landed are valid UTF-8 and carry the glyphs, not silently dropped
    written = buf2.getvalue().decode("utf-8")
    assert "−" in written or "-" in written  # the "dropped" marker, in some form


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
