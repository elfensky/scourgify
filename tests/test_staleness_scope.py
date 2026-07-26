#!/usr/bin/env python3
"""Pins staleness's book scope: --books narrows which books get re-derived, and narrows
nothing else. The pure `derive` rule is covered in test_core.py.
No framework:  uv run tests/test_staleness_scope.py   (also pytest-collectable)."""
import contextlib, datetime, io, os, sys, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from fixture_db import build
from scourgify import staleness


@contextlib.contextmanager
def library():
    """Three In-Progress books whose #updated ages put every one of them over the dead line,
    plus book 4 — a real book with NO #status at all (about a fifth of a real FanFicFare
    library looks like this). Book 4 exists; anything that treats "absent from #status" as
    "absent from the library" will misreport it."""
    with tempfile.TemporaryDirectory() as td:
        lib = os.path.join(td, "library"); os.makedirs(lib)
        long_ago = (datetime.date.today() - datetime.timedelta(days=8 * 365)).isoformat()
        build(os.path.join(lib, "metadata.db"),
              [dict(id=i, added="2026-01-01") for i in (1, 2, 3, 4)],
              custom=[("status", {i: "In-Progress" for i in (1, 2, 3)}),
                      ("updated", {i: long_ago for i in (1, 2, 3)})]).close()
        saved = os.environ.get("CALIBRE_LIBRARY")
        os.environ["CALIBRE_LIBRARY"] = lib
        try:
            yield
        finally:
            os.environ.pop("CALIBRE_LIBRARY", None) if saved is None else os.environ.__setitem__("CALIBRE_LIBRARY", saved)


def test_compute_whole_library_by_default():
    with library():
        label, rows = staleness.compute()
        assert label == "#status"
        assert sorted(b for b, _, _, _ in rows) == [1, 2, 3]
        assert {n for _, _, n, _ in rows} == {"Abandoned"}


def test_compute_books_narrows_the_rows():
    with library():
        _, rows = staleness.compute(books=[3, 1])
        assert sorted(b for b, _, _, _ in rows) == [1, 3]


def test_compute_books_empty_selects_nothing():
    with library():
        _, rows = staleness.compute(books=[])
        assert rows == []


def _note(books):
    """-> the 'not in the library' note compute() printed for this scope ('' if it printed none)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        staleness.compute(books=books)
    return "".join(l for l in buf.getvalue().splitlines() if "not in the library" in l)


def test_absent_note_counts_the_library_not_the_status_column():
    """Book 4 is in the library but has no #status. Counting absent ids against the #status
    column instead of the books table reported a fifth of a real library as "not in the
    library" — 10 of 50 ids that all existed."""
    with library():
        assert _note([1, 4]) == ""                 # both real: no note at all
        assert "1 requested id" in _note([1, 999])  # 999 really is absent
        assert "2 requested id" in _note([998, 999])


def test_absent_ids_do_not_affect_the_rows():
    """The note is informational — an absent id must not change what gets re-derived."""
    with library():
        _, rows = staleness.compute(books=[1, 4, 999])
        assert [b for b, _, _, _ in rows] == [1]   # 4 has no status to re-derive, 999 doesn't exist


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
