#!/usr/bin/env python3
"""Selection semantics against a real (throwaway) metadata.db — the logic that decides
which books an incremental/--last/--since run operates on.
No framework needed:  uv run tests/test_selection.py   (also collectable by pytest).
No Calibre, no library, no network."""
import os, sys, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from scourgify import select
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


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
