#!/usr/bin/env python3
"""Headless smoke test under Calibre's OWN interpreter — the spike of 2026-08-06, made permanent.

    calibre-debug -e tests/smoke_calibre.py            # read paths too, if CALIBRE_LIBRARY is set

Not named test_*.py on purpose: CI has no Calibre, so the glob must not pick it up. This is the
manual pre-release check for the one property CI cannot assert — that the core runs under
Calibre's bundled Python (3.14.6 in Calibre 9.11) with an empty site-packages. Its CI-runnable
half (every core module imports with rich absent) lives in tests/test_plugin_safety.py.

READ-ONLY. It opens the library through common.ro_connect() and never writes — safe to run
against $CALIBRE_LIBRARY with Calibre open. Exits non-zero on the first real failure so it can
gate a release.
"""
import os, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))     # calibre-debug -e puts only tests/ on the path

FAILED = []


def check(label, fn):
    """Run one probe. A failure is recorded and the script continues — one broken read path
    should not hide the other nine."""
    t0 = time.perf_counter()
    try:
        got = fn()
    except BaseException as e:                    # BaseException on purpose: a stray SystemExit
        FAILED.append(label)                      # is exactly what this script exists to catch
        print(f"  FAIL  {label}: {type(e).__name__}: {e}")
        return None
    ms = (time.perf_counter() - t0) * 1000
    print(f"  ok    {label}{'' if got is None else f' -> {got}'}   ({ms:.0f} ms)")
    return got


def main():
    print(f"python      {sys.version.split()[0]}")
    print(f"site-pkgs   {[p for p in sys.path if 'site-packages' in p] or 'none on path'}")
    try:
        import rich                                # noqa: F401
        print("rich        present (not the plugin's environment — run this via calibre-debug)")
    except ImportError:
        print("rich        ABSENT  (as inside Calibre)")

    print("\nimports")
    for m in ("common", "ops", "editlog", "select", "artifacts", "overrides", "engines", "booktext",
              "wrangle", "classify", "promote", "staleness", "setup", "report", "cli"):
        check(f"core  {m}", lambda m=m: __import__(f"scourgify.{m}", fromlist=[m]) and None)

    # ui/wizard genuinely need rich. Refusing is correct; refusing with a CATCHABLE error is the
    # property under test — a SystemExit here would take the host process down.
    from scourgify.common import GuardrailError
    for m in ("ui", "wizard"):
        def probe(m=m):
            try:
                __import__(f"scourgify.{m}", fromlist=[m])
                return "imported (rich available)"
            except GuardrailError:
                return "refused with GuardrailError (catchable)"
        check(f"ui    {m}", probe)

    lib = os.environ.get("CALIBRE_LIBRARY")
    if not lib:
        print("\nread paths   skipped (CALIBRE_LIBRARY not set)")
    else:
        print(f"\nread paths   {lib}   (read-only; safe with Calibre open)")
        from scourgify import common, select, artifacts, wrangle
        con = check("common.ro_connect", common.ro_connect)
        if con is None: return _report()
        try:
            check("common.book_count", lambda: common.book_count(con))
            check("select.pick(last,5)", lambda: select.pick(con, "last", 5))
            check("select.changed", lambda: len(select.changed(con)))
            check("select.sendable", lambda: len(select.sendable(con)))
            check("select.pick(unclassified)", lambda: len(select.pick(con, "unclassified")))
            check("artifacts.classified_ids", lambda: len(artifacts.classified_ids()))
            check("wrangle.load_maps", lambda: sorted(wrangle.load_maps(common.load_config())))
            check("common.norm", lambda: common.norm("Fate/stay night"))
            # the edit log's before-read, on the real library: the guard already reads these
            # columns and discards the values, so this is the extra join undo depends on
            check("common.library_uuid", lambda: common.library_uuid(con))
            ids = select.pick(con, "last", 5)
            check("common.column_values(tags)", lambda: len(common.column_values(con, "tags", ids)))
            check("common.column_values(#status)", lambda: len(common.column_values(con, "#status", ids)))
        finally:
            con.close()

    return _report()


def _report():
    print()
    if FAILED:
        print(f"FAILED: {len(FAILED)} — {', '.join(FAILED)}")
        raise SystemExit(1)
    print("smoke ok")


main()
