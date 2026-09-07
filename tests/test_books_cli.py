#!/usr/bin/env python3
"""Pins the CLI-boundary `--books` semantics shared by wrangle/classify/staleness — the thing that
was never tested and is exactly why the three tools drifted apart (default="" + `if a.books:` made
an explicitly-passed empty spec indistinguishable from "flag absent", so it fell back to the
UNSCOPED default: `apply --apply --books ""` wrote the whole library instead of nothing).

Covers: parse_books rejects an empty/blank spec; a rejected --books never reaches the writer
(the fail-open regression — the most important test here); `wrangle audit --books` is rejected
(audit has no scope); `classify --books --apply` is rejected (scoping which proposal ROWS get
applied is a different feature, not a review fix); a scoped apply write really is scoped to
exactly its ids; omitting --books keeps the normal unscoped path working.

No framework:  uv run tests/test_books_cli.py   (also pytest-collectable). No Calibre, no network,
no real Calibre library ever touched — every test points CALIBRE_LIBRARY at a temp fixture and
intercepts the writer."""
import contextlib, os, sqlite3, sys, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from fixture_db import build
from scourgify import classify, common, select, wrangle


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
def library(books):
    """A throwaway fixture library + redirected $SCOURGIFY_HOME, like test_classify_run.py's harness.
    NEVER the user's real Calibre library — CALIBRE_LIBRARY always points into a tempdir here."""
    with tempfile.TemporaryDirectory() as td:
        lib = os.path.join(td, "library"); os.makedirs(lib)
        build(os.path.join(lib, "metadata.db"), books).close()
        with env(SCOURGIFY_HOME=os.path.join(td, "home"), CALIBRE_LIBRARY=lib):
            os.makedirs(common.data_dir())
            classify.clear_caches()
            try:
                yield td
            finally:
                classify.clear_caches()


@contextlib.contextmanager
def argv(*args):
    saved = sys.argv
    sys.argv = ["scourgify"] + list(args)
    try:
        yield
    finally:
        sys.argv = saved


def expect_exit(fn, *a, **kw):
    try:
        fn(*a, **kw)
        assert False, f"expected a refusal from {fn!r}"
    except common.GuardrailError:
        pass


def _intercept(mod):
    """Swap mod.run_writer for a recorder; returns (recorded_list, restore()) like test_classify_run.py."""
    recorded = []
    saved = mod.run_writer
    mod.run_writer = lambda ops, force=False, **kw: recorded.append(ops)
    return recorded, lambda: setattr(mod, "run_writer", saved)


TAGGED = [dict(id=i, added=f"2026-01-0{i}", tags=["also"]) for i in (1, 2, 3)]   # "also" is a real junk.txt drop


def test_parse_books_rejects_empty_and_blank_specs():
    for bad in ("", "   "):
        expect_exit(select.parse_books, bad)


def test_wrangle_apply_books_empty_fails_closed_and_writes_nothing():
    """THE fail-open regression: --books "" must SystemExit and must never fall back to the
    unscoped whole-library write."""
    with library(TAGGED):
        recorded, restore = _intercept(wrangle)
        try:
            with argv("apply", "--apply", "--books", ""):
                expect_exit(wrangle.main)
        finally:
            restore()
        assert recorded == [], "a rejected --books spec reached the writer"


def test_wrangle_apply_books_empty_closes_its_read_connection():
    """The Windows regression found on ci.yml run 34142905483 (test-windows job, the lane's
    first real run): wrangle.read_library() opened a read-only sqlite connection and never
    called .close() on it. CPython's refcounting happens to close the underlying file the
    instant the connection object becomes unreachable, so on macOS/Linux the OS-level lock was
    released fast enough that this was invisible — but an explicitly-open sqlite3.Connection
    holds a lock Windows won't release for an unlink, and tempfile.TemporaryDirectory()'s cleanup
    right after this exact call path failed with WinError 32 (PermissionError) on metadata.db.

    Asserting this from macOS/Linux without forcing a timing race: capture the connection(s)
    wrangle.read_library() actually opens (via a thin wrapper around wrangle.ro_connect) and hold
    OUR OWN extra reference to them for the duration of the test. That extra reference means
    CPython's refcounting can never close the connection for us — only an explicit .close() (the
    fix: `with contextlib.closing(ro_connect()) as con:`) can. So the assertion is a direct check
    of "did the code call close()", not a race against garbage collection, and it fails
    identically on every platform if the explicit close regresses."""
    with library(TAGGED):
        captured = []
        real_ro_connect = wrangle.ro_connect

        def capturing(*a, **kw):
            con = real_ro_connect(*a, **kw)
            captured.append(con)
            return con

        wrangle.ro_connect = capturing
        recorded, restore = _intercept(wrangle)
        try:
            with argv("apply", "--apply", "--books", ""):
                expect_exit(wrangle.main)
        finally:
            restore()
            wrangle.ro_connect = real_ro_connect
        assert captured, "expected wrangle.main() to open at least one read-only connection"
        for con in captured:
            try:
                con.execute("SELECT 1")
                assert False, ("a connection opened on the --books '' path was never explicitly "
                                "closed — it would hold a lock on Windows (WinError 32)")
            except sqlite3.ProgrammingError:
                pass                                    # closed, as expected


def test_wrangle_audit_books_is_rejected():
    """audit has no scope (its report reads a decision log with no book ids) — accept-and-discard
    is the wrong shape for an audit-first tool, so it's a hard SystemExit instead."""
    with library(TAGGED):
        with argv("audit", "--books", "2,3"):
            expect_exit(wrangle.main)


def test_classify_books_with_apply_is_rejected():
    """--books scopes which books get CLASSIFIED, not which proposal rows get applied — that
    combination is a real feature needing its own design, not something to silently scope."""
    with library([dict(id=1, added="2026-01-01", desc="x")]):
        with argv("--books", "1", "--apply"):
            expect_exit(classify.main)


def test_wrangle_apply_books_scopes_the_write_exactly():
    with library(TAGGED):
        recorded, restore = _intercept(wrangle)
        try:
            with argv("apply", "--apply", "--books", "1,3"):
                wrangle.main()
        finally:
            restore()
        (ops,) = recorded
        (tags_op,) = [o for o in ops if o["op"] == "set_field" and o["field"] == "tags"]
        assert set(tags_op["values"]) == {"1", "3"}, tags_op["values"]   # book 2 untouched


def test_wrangle_apply_omitting_books_stays_unscoped():
    """Proves the fix didn't break the normal (no --books) path: every changed book still writes."""
    with library(TAGGED):
        recorded, restore = _intercept(wrangle)
        try:
            with argv("apply", "--apply"):
                wrangle.main()
        finally:
            restore()
        (ops,) = recorded
        (tags_op,) = [o for o in ops if o["op"] == "set_field" and o["field"] == "tags"]
        assert set(tags_op["values"]) == {"1", "2", "3"}


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
