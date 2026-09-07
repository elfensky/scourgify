#!/usr/bin/env python3
"""The synopsis queue — who the pass operates on, and why it is FINITE.

Three exits from the queue and each is load-bearing: the #synopsized stamp (settled), the
failure log (attempted and blocked), and having no text source at all (not work). Without
all three the sweep re-selects the same books forever, exactly as the classify backlog did.
No framework:  uv run tests/test_synopsis_queue.py   (also pytest-collectable)."""
import os, sys, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scourgify import artifacts, common, select
from fixture_db import build

LONG = "A description comfortably past the forty-character floor. " * 2
#  1 good blurb, unstamped                 -> queued (to be inspected and kept)
#  2 good blurb, stamped, no #updated      -> settled, stays out
#  3 good blurb, stamped, #updated NEWER   -> re-queued (the refresh clock)
#  4 thin blurb, has an EPUB               -> queued (generate from the file)
#  5 thin blurb, no file                   -> never queued (bucket C: not work)
#  6 good blurb, stamped, #updated OLDER   -> settled, stays out
BOOKS = [dict(id=1, added="2026-01-01 10:00:00", desc=LONG),
         dict(id=2, added="2026-01-02 10:00:00", desc=LONG),
         dict(id=3, added="2026-01-03 10:00:00", desc=LONG),
         dict(id=4, added="2026-01-04 10:00:00", desc="too short"),
         dict(id=5, added="2026-01-05 10:00:00", desc="too short"),
         dict(id=6, added="2026-01-06 10:00:00", desc=LONG)]
STAMP = "2026-05-01 12:00:00+00:00"
SYN = {2: STAMP, 3: STAMP, 6: STAMP}
UPD = {3: "2026-06-01", 6: "2026-04-01"}


def _con(lib_dir=None, uuid=None):
    d = lib_dir or tempfile.mkdtemp()
    con = build(os.path.join(d, "metadata.db"), BOOKS,
                custom=[("updated", UPD), ("wrangled", {}), ("synopsized", SYN)], uuid=uuid)
    con.execute("INSERT INTO data VALUES(?,?,?)", (4, "EPUB", "book4"))
    con.commit()
    return con


def test_resynopsize_is_the_pure_refresh_clock():
    assert select.resynopsize("", "2026-01-01") is True            # never settled
    assert select.resynopsize(None, None) is True
    assert select.resynopsize(STAMP, None) is False                # no #updated -> never auto-refresh
    assert select.resynopsize(STAMP, "2026-04-01") is False        # site update predates the settling
    assert select.resynopsize(STAMP, "2026-06-01") is True         # grew new chapters since


def test_queue_is_unstamped_or_stale_and_has_a_text_source():
    con = _con()
    assert select.pick(con, "unsynopsized", seen=set()) == [4, 3, 1]   # newest-added-first
    assert 2 not in select.pick(con, "unsynopsized", seen=set())       # settled
    assert 6 not in select.pick(con, "unsynopsized", seen=set())       # settled, older #updated
    assert 5 not in select.pick(con, "unsynopsized", seen=set())       # thin AND no file


def test_a_failed_book_leaves_the_queue_until_it_succeeds():
    """The finiteness rule the classify backlog learned the hard way: a book that errored gets no
    stamp on purpose (it must be retryable), so without the failure log it re-occupies the head of
    every batch forever."""
    con = _con()
    assert select.pick(con, "unsynopsized", seen={4}) == [3, 1]


def test_queue_defaults_read_the_failure_log():
    """Bare pick("unsynopsized") is the CORRECT call — the invariant lives in select, so the
    wizard header, the CLI and any future dashboard cannot compose it differently."""
    old = {k: os.environ.get(k) for k in ("SCOURGIFY_HOME", "CALIBRE_LIBRARY")}
    os.environ["SCOURGIFY_HOME"] = tempfile.mkdtemp()
    lib_dir = tempfile.mkdtemp()   # artifacts.write_failures() below resolves through data_dir(),
                                   # which is uuid-scoped now and needs a resolvable library.
    os.environ["CALIBRE_LIBRARY"] = lib_dir
    common.clear_uuid_cache()
    try:
        con = _con(lib_dir=lib_dir, uuid="uuid-synopsis-queue")   # creates metadata.db first
        os.makedirs(common.data_dir(), exist_ok=True)
        assert select.pick(con, "unsynopsized") == [4, 3, 1]
        artifacts.write_failures([[4, "book 4", "no readable text"]], artifacts.syn_fail())
        assert artifacts.synopsis_failed_ids() == {4}
        assert select.pick(con, "unsynopsized") == [3, 1]
        # ...and the synopsis log is NOT the classify log
        assert artifacts.syn_fail() != artifacts.fail()
    finally:
        for k, v in old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        common.clear_uuid_cache()


def test_has_file_and_sendable_are_separate_predicates():
    """sendable() answers 'could classify send this' (description only). has_file() answers
    'is there prose to read'. The synopsis queue is their union; the classify backlog is
    sendable() alone — the split that replaces the old text_fallback boolean."""
    con = _con()
    assert select.sendable(con) == {1, 2, 3, 6}
    assert select.has_file(con) == {4}


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
