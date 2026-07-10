#!/usr/bin/env python3
"""The single `scourgify` command's argv dispatch — the routing table in cli.main().
No framework needed:  uv run tests/test_cli.py   (also collectable by pytest).
No Calibre, no library, no network: every tool's main() is stubbed with a recorder."""
import os, sys, io, contextlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from scourgify import cli, __version__
import scourgify.classify as classify, scourgify.staleness as staleness
import scourgify.promote as promote, scourgify.wrangle as wrangle, scourgify.common as common


def _run(argv, stubs=("classify", "staleness", "promote", "wrangle")):
    """Dispatch cli.main() with the given argv; each tool main()/cmd is a recorder.
    -> (dispatched_name, argv_seen_by_tool, printed_stdout)."""
    called = {}
    mods = {"classify": classify, "staleness": staleness, "promote": promote, "wrangle": wrangle}
    saved = {n: m.main for n, m in mods.items()}
    saved_overrides, saved_rollback = wrangle.overrides_cmd, common.rollback_cmd
    def rec(name):
        def f(*a, **k): called["name"] = name; called["argv"] = list(sys.argv)
        return f
    try:
        for n in stubs:
            mods[n].main = rec(n)
        wrangle.overrides_cmd = lambda a: called.update(name="overrides", argv=a)
        common.rollback_cmd = lambda a: called.update(name="rollback", argv=a)
        sys.argv = ["scourgify", *argv]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main()
    finally:
        for n, fn in saved.items(): mods[n].main = fn
        wrangle.overrides_cmd, common.rollback_cmd = saved_overrides, saved_rollback
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


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
