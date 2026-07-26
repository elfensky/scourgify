#!/usr/bin/env python3
"""Pins the artifact formats (artifacts.py) — the CSVs the tools hand each other. A drift here
would silently break the classify → review → promote → backfill handoffs.
No framework:  uv run tests/test_artifacts.py   (also pytest-collectable). No Calibre/library/network."""
import os, sys, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from scourgify import artifacts


def test_split_join_tags():
    assert artifacts.split_tags("Fluff; Angst;  ; Fix-It") == ["Fluff", "Angst", "Fix-It"]
    assert artifacts.split_tags("") == [] and artifacts.split_tags(None) == []
    assert artifacts.join_tags(["a", "b"]) == "a; b"


def test_proposal_round_trip():
    d = tempfile.mkdtemp(); p = os.path.join(d, "prop.csv")
    rows = [{"book_id": 7, "title": "Bend It", "added_tags": ["Fluff", "Angst"], "proposed_new": ["Gacha Mechanic"]},
            {"book_id": 8, "title": "No Match", "added_tags": [], "proposed_new": []}]
    artifacts.write_proposal(rows, p)
    back = artifacts.read_proposal(p)
    assert back == rows                                     # ints, lists, empties all survive the round trip
    assert artifacts.read_proposal(os.path.join(d, "missing.csv")) == []


def test_ranked_written_positionally_read_by_name():
    # the exact mismatch this module exists to kill: annotate_new writes lists, everyone else reads names
    d = tempfile.mkdtemp(); p = os.path.join(d, "rank.csv")
    artifacts.write_ranked([["Dragon Politics", 2, "", 0.0, "new"],
                            ["Slow-Burn", 5, "Slow Burn", 0.93, "near-duplicate"]], p)
    back = artifacts.read_ranked(p)
    assert back[0]["proposed_tag"] == "Dragon Politics" and back[0]["count"] == 2   # count is an int
    assert back[1]["verdict"] == "near-duplicate" and back[1]["nearest_existing"] == "Slow Burn"


def test_review_round_trip_and_archive():
    d = tempfile.mkdtemp(); p = os.path.join(d, "review.csv")
    artifacts.write_review([{"tag": "Soul Bond", "count": 3, "verdict": "promote", "target": "",
                             "reason": "novel", "confidence": "high", "contested": False}], p)
    back = artifacts.read_rows(p)
    assert back[0]["tag"] == "Soul Bond" and back[0]["contested"] == "False"
    arch = artifacts.archive(p, "applied")
    assert not os.path.exists(p) and os.path.exists(arch)
    assert os.path.basename(arch).startswith("review_applied_") and arch.endswith(".csv")


def test_merge_failures_drops_books_that_have_since_succeeded():
    """The failure log means "failed and not since recovered". Gemini blocked 7 books; re-running
    them through openai succeeded — the documented recovery — but the log still listed all 7,
    because it was only ever written when a run HAD failures and then overwrote wholesale."""
    prev = [{"book_id": "1", "title": "A", "reason": "blocked:PROHIBITED_CONTENT"},
            {"book_id": "2", "title": "B", "reason": "blocked:PROHIBITED_CONTENT"},
            {"book_id": "3", "title": "C", "reason": "timeout"}]
    # this run processed 1 and 2; only 1 failed again. 3 was not in scope and must survive.
    out = artifacts.merge_failures(prev, {1, 2}, [[1, "A", "timeout"]])
    assert out == [[1, "A", "timeout"], ["3", "C", "timeout"]]


def test_merge_failures_clean_run_clears_the_whole_log():
    prev = [{"book_id": "1", "title": "A", "reason": "blocked:PROHIBITED_CONTENT"}]
    assert artifacts.merge_failures(prev, {1}, []) == []


def test_merge_failures_keeps_untouched_books_when_nothing_ran():
    prev = [{"book_id": "1", "title": "A", "reason": "boom"}]
    assert artifacts.merge_failures(prev, set(), []) == [["1", "A", "boom"]]


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
