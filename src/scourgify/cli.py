"""Single `scourgify` command.

Dispatch: bare -> wizard (via wrangle); setup/audit/apply -> wrangle;
classify -> classify;
synopsis -> synopsis; staleness -> staleness. Each tool keeps its own argparse,
so we just hand off argv. Imports are lazy so `scourgify --version` stays cheap.
"""
import sys

from scourgify import __version__


def main():
    """The CLI boundary — and the ONE place GuardrailError becomes a process exit.

    Guards raise a catchable GuardrailError so a Calibre job can handle them (a SystemExit would
    escape ThreadedJob's `except Exception` and kill the worker thread silently). On the CLI that
    error must still read as a plain refusal with a non-zero exit, exactly as it always has —
    converted here rather than at 30 raise sites.

    Also the one place stdout/stderr get reconfigured to UTF-8 on Windows, whose console
    defaults to the legacy locale encoding (cp1252) — report.py's own glyphs (−, →, ·, ✓, ⚠, ✗,
    box-drawing) have no cp1252 mapping and would crash mid-render (UnicodeEncodeError). Done
    HERE, not in report.py: report.py is imported by tools that run inside Calibre jobs too, and
    reconfiguring the host process's own streams as an import-time side effect would be exactly
    the kind of thing tests/test_plugin_safety.py exists to forbid. cli.main() is CLI-only — the
    plugin never enters it. errors="replace" so an unencodable glyph degrades to a replacement
    character instead of crashing a run mid-report; any failure here is swallowed, never fatal."""
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                if hasattr(stream, "reconfigure") and (stream.encoding or "").lower() not in ("utf-8", "utf8"):
                    stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    from scourgify.common import GuardrailError
    try:
        return _dispatch()
    except GuardrailError as e:
        raise SystemExit(str(e))


def _dispatch():
    argv = sys.argv[1:]
    if argv and argv[0] in ("-V", "--version"):
        print(f"scourgify {__version__}")
        return
    if argv and argv[0] == "classify":
        from scourgify import classify
        sys.argv = ["scourgify classify", *argv[1:]]
        return classify.main()
    if argv and argv[0] == "staleness":
        from scourgify import staleness
        sys.argv = ["scourgify staleness", *argv[1:]]
        return staleness.main()
    if argv and argv[0] == "synopsis":
        from scourgify import synopsis
        sys.argv = ["scourgify synopsis", *argv[1:]]
        return synopsis.main()
    if argv and argv[0] == "promote":
        from scourgify import promote
        sys.argv = ["scourgify promote", *argv[1:]]
        return promote.main()
    if argv and argv[0] == "overrides":
        from scourgify import overrides
        return overrides.overrides_cmd(argv[1:])
    if argv and argv[0] == "rollback":
        from scourgify import common
        return common.rollback_cmd(argv[1:])
    # setup / audit / apply / (none -> wizard) all live in wrangle's main()
    from scourgify import wrangle
    return wrangle.main()


if __name__ == "__main__":
    main()
