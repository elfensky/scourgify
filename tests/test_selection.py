#!/usr/bin/env python3
"""Selection semantics against a real (throwaway) metadata.db — the logic that decides
which books an incremental/--last/--since run operates on.
No framework needed:  uv run tests/test_selection.py   (also collectable by pytest).
No Calibre, no library, no network."""
import os, sys, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from scourgify import common, select
from fixture_db import build

STAMP = "2026-06-26 20:43:39+00:00"           # the one classify-apply so far

# The scenarios that produced the real-world bug, one book each:
#   1 unchanged        — stamped, nothing newer                       -> excluded
#   2 new              — added after the stamp, never stamped         -> "new"
#   3 late fetch       — site updated BEFORE the stamp but re-downloaded after
#                        (FanFicFare bumps the added-date)            -> "re-fetched"
#   4 site update      — #updated newer than the stamp                -> "updated"
#   5 own-write bump   — only last_modified newer (scourgify's write) -> excluded
#   6 undated + sparse — stamped, no #updated, zero tags              -> excluded from incremental
BOOKS = [
    dict(id=1, added="2026-06-01 10:00:00+00:00", tags=["a", "b"]),
    dict(id=2, added="2026-07-02 10:51:00+00:00", tags=["a"]),
    dict(id=3, added="2026-07-02 09:00:00+00:00"),
    dict(id=4, added="2026-06-01 11:00:00+00:00"),
    dict(id=5, added="2026-06-01 12:00:00+00:00", last_modified="2026-07-03 09:00:00+00:00"),
    dict(id=6, added="2026-06-20 08:00:00+00:00"),
]
UPDATED = {1: "2026-05-01", 3: "2026-06-23", 4: "2026-06-30", 5: "2026-05-01"}
STAMPED = {1: STAMP, 3: STAMP, 4: STAMP, 5: STAMP, 6: STAMP}


def _con(link=False):
    path = os.path.join(tempfile.mkdtemp(), "metadata.db")
    return build(path, BOOKS, custom=[("updated", UPDATED), ("wrangled", STAMPED)],
                 link_labels=("updated",) if link else ())


def test_changed_reasons():
    assert select.changed(_con()) == {2: "new", 3: "re-fetched", 4: "updated"}


def test_changed_link_table_storage():                 # both custom-column shapes must read identically
    assert select.changed(_con(link=True)) == {2: "new", 3: "re-fetched", 4: "updated"}


def test_own_writes_are_invisible():                   # last_modified is NOT a change clock
    assert 5 not in select.changed(_con())


def test_stamped_no_tag_book_not_reselected():         # stamp-all-processed keeps no-tag books quiet
    assert 6 not in select.changed(_con())


def test_pick_incremental_newest_first():
    assert select.pick(_con(), "incremental") == [2, 3, 4]


def test_pick_last():
    assert select.pick(_con(), "last", n=2) == [2, 3]
    assert select.pick(_con(), "last", n=99) == [2, 3, 6, 5, 4, 1]


def test_pick_since_matches_added_or_updated():
    assert select.pick(_con(), "since", since="2026-06-30") == [2, 3, 4]   # 2,3 by added; 4 by #updated
    assert select.pick(_con(), "since", since="2026-01-01") == [2, 3, 6, 5, 4, 1]


def test_pick_sparse():
    assert select.pick(_con(), "sparse", min_tags=2) == [2, 3, 6, 5, 4]    # book 1 has 2 tags
    assert select.pick(_con(), "sparse", min_tags=1) == [3, 6, 5, 4]


def test_pick_all_and_unknown_mode():
    assert select.pick(_con(), "all") == [2, 3, 6, 5, 4, 1]
    try:
        select.pick(_con(), "bogus"); assert False, "expected ValueError"
    except ValueError:
        pass


def test_changed_pure_day_granularity():               # date-only #updated on the stamp day stays conservative
    added = {1: "2026-06-01 10:00:00+00:00"}
    assert select.changed_pure(added, {1: "2026-06-26"}, {1: STAMP}) == {}
    assert select.changed_pure(added, {1: "2026-06-27"}, {1: STAMP}) == {1: "updated"}


def test_parse_books_forms():
    assert select.parse_books("3,1,2") == [3, 1, 2]            # order preserved, not sorted
    assert select.parse_books("10-13") == [10, 11, 12, 13]     # inclusive range
    assert select.parse_books("7") == [7]
    assert select.parse_books("5, 1-3 ,5,2") == [5, 1, 2, 3]   # whitespace tolerated, de-duplicated


def test_parse_books_at_file():
    path = os.path.join(tempfile.mkdtemp(), "ids.txt")
    open(path, "w").write("# a comment\n7\n8,9\n\n10-11   # trailing comment\n")
    assert select.parse_books(f"@{path},1") == [7, 8, 9, 10, 11, 1]


def test_parse_books_rejects_garbage():
    nested = os.path.join(tempfile.mkdtemp(), "loop.txt")
    open(nested, "w").write("@%s\n" % nested)
    binary = os.path.join(tempfile.mkdtemp(), "cover.jpg")
    open(binary, "wb").write(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00")
    for bad in ("x", "1,2x", "9-3", "1-", "@/nonexistent/ids.txt", f"@{nested}", f"@{binary}"):
        try:
            select.parse_books(bad)
            assert False, f"expected a refusal for {bad!r}"
        except common.GuardrailError:
            pass


def test_pick_ids():
    assert select.pick(_con(), "ids", ids=[4, 2, 99]) == [2, 4]   # newest-added-first; 99 absent -> dropped
    assert select.pick(_con(), "ids", ids=[]) == []


# ---- the advancing scope: never attempted, and actually sendable ----
LONG = "A description comfortably past the forty-character floor. " * 2
UBOOKS = [dict(id=1, added="2026-01-01 10:00:00+00:00", desc=LONG),
          dict(id=2, added="2026-01-02 10:00:00+00:00", desc=LONG),
          dict(id=3, added="2026-01-03 10:00:00+00:00", desc=LONG),
          dict(id=4, added="2026-01-04 10:00:00+00:00", desc="too short"),   # under MIN_DESC
          dict(id=5, added="2026-01-05 10:00:00+00:00")]                     # no description at all


def _ucon():
    d = tempfile.mkdtemp()
    return build(os.path.join(d, "metadata.db"), UBOOKS, custom=[("updated", {}), ("wrangled", {})])


def test_unclassified_excludes_attempted_and_unsendable():
    """The two filters that make this scope FINITE. Without the sendable half, a book gather()
    would drop for thin text can never reach a proposal, so it never leaves the set: every batch
    re-selects it and the set never empties."""
    con = _ucon()
    assert select.pick(con, "unclassified", seen=set()) == [3, 2, 1]      # newest-added-first
    assert select.pick(con, "unclassified", seen={2}) == [3, 1]           # attempted -> retired
    # 4 (thin) and 5 (no description) are never sendable on descriptions alone
    assert 4 not in select.pick(con, "unclassified", seen=set())
    assert 5 not in select.pick(con, "unclassified", seen=set())


def test_unclassified_widens_when_text_fallback_can_rescue_a_book():
    """--text-fallback samples the book's prose, so a thin/absent description is no longer
    disqualifying — but only for a book that actually has a file to sample."""
    con = _ucon()
    con.execute("INSERT INTO data VALUES(?,?,?)", (4, "EPUB", "book4"))
    con.commit()
    assert 4 in select.pick(con, "unclassified", seen=set(), text_fallback=True)
    assert 5 not in select.pick(con, "unclassified", seen=set(), text_fallback=True)   # no file


def test_unclassified_batches_are_disjoint_and_the_set_shrinks():
    """The property that makes chunked sweeping correct, and the one the rejected positional
    design could only assert on a frozen snapshot: simulate applying a batch by adding it to
    `seen`, and the next batch must not overlap while the remainder strictly shrinks."""
    con = _ucon()
    seen, batches = set(), []
    while True:
        todo = select.pick(con, "unclassified", seen=seen)[:2]      # --batch 2
        if not todo: break
        batches.append(todo); seen |= set(todo)                    # apply -> archived -> attempted
    flat = [b for chunk in batches for b in chunk]
    assert flat == [3, 2, 1], flat                                  # every sendable book, exactly once
    assert len(flat) == len(set(flat))                              # ... and no overlap between batches
    assert select.pick(con, "unclassified", seen=seen) == []        # the set EMPTIES


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
