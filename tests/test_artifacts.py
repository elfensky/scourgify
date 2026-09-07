#!/usr/bin/env python3
"""Pins the artifact formats (artifacts.py) — the CSVs the tools hand each other. A drift here
would silently break the classify → review → promote → backfill handoffs.
No framework:  uv run tests/test_artifacts.py   (also pytest-collectable). No Calibre/library/network."""
import os, sys, tempfile, contextlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scourgify import artifacts, common
import fixture_db


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


@contextlib.contextmanager
def _home(td):
    """Point the whole artifact tree at a tempdir (paths are functions, so this reaches them),
    behind a throwaway fixture library — data_dir() is uuid-scoped now, so a library must
    resolve for artifacts.*() to have anywhere to write. Restores SCOURGIFY_HOME/CALIBRE_LIBRARY
    and clears the uuid memo on exit so this test's fixture library can't leak into the next."""
    old = {k: os.environ.get(k) for k in ("SCOURGIFY_HOME", "CALIBRE_LIBRARY")}
    os.environ["SCOURGIFY_HOME"] = os.path.join(td, "home")
    lib = os.path.join(td, "lib")
    os.makedirs(lib, exist_ok=True)
    fixture_db.build(os.path.join(lib, "metadata.db"), [{"id": 1}], uuid="uuid-test-artifacts").close()
    os.environ["CALIBRE_LIBRARY"] = lib
    common.clear_uuid_cache()
    os.makedirs(common.data_dir(), exist_ok=True)
    try:
        yield
    finally:
        for k, v in old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        common.clear_uuid_cache()


def test_classified_ids_counts_applied_pending_and_failures_not_discarded():
    """The cursor a "what's left" scope reads. Each source is a decision:
      applied/pending  -> classification written, or in hand awaiting review.
      failures         -> ATTEMPTED but blocked. Without this an errored book gets no proposal
                          row (by design, so it can retry), sits at the head of every future
                          batch and is re-billed forever with zero progress.
      discarded        -> EXCLUDED. The user threw those results away; the books stay candidates."""
    with tempfile.TemporaryDirectory() as td, _home(td):
        artifacts.write_proposal([{"book_id": 1, "title": "p", "added_tags": [], "proposed_new": []}])
        artifacts.archive(artifacts.prop(), "applied")                       # 1 = applied
        artifacts.write_proposal([{"book_id": 2, "title": "q", "added_tags": [], "proposed_new": []}])
        artifacts.archive(artifacts.prop(), "discarded")                     # 2 = discarded
        artifacts.write_proposal([{"book_id": 3, "title": "r", "added_tags": [], "proposed_new": []}])
        artifacts.write_failures([[4, "blocked", "blocked:PROHIBITED_CONTENT"]])
        assert artifacts.classified_ids() == {1, 3, 4}                       # 2 stays a candidate


def test_archive_rows_files_only_what_it_is_given():
    """A partial apply must not name un-applied books in an *_applied_* archive."""
    with tempfile.TemporaryDirectory() as td, _home(td):
        rows = [{"book_id": i, "title": f"b{i}", "added_tags": ["T"], "proposed_new": []} for i in (1, 2, 3)]
        artifacts.write_proposal(rows)
        arch = artifacts.archive_rows(rows[:1], "applied")
        assert {r["book_id"] for r in artifacts.read_proposal(arch)} == {1}
        assert os.path.exists(artifacts.prop())                              # live file untouched
        assert {r["book_id"] for r in artifacts.read_proposal()} == {1, 2, 3}


def test_archives_in_the_same_second_never_overwrite_each_other():
    """*_applied_* archives are the classified_ids() cursor, not history. The name is stamped to
    the second, and a `--step` apply or `promote --apply --backfill` writes two in one second —
    losing the first un-retires those books, which the next --unclassified run re-bills."""
    with tempfile.TemporaryDirectory() as td, _home(td):
        for i in (1, 2, 3):                                  # all in the same wall-clock second
            artifacts.archive_rows([{"book_id": i, "title": f"b{i}",
                                     "added_tags": ["T"], "proposed_new": []}], "applied")
        files = artifacts.applied_proposals()                 # the glob must still see suffixed names
        assert len(files) == 3 and files == sorted(files)
        assert artifacts.classified_ids() == {1, 2, 3}        # the union, not just the last writer


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


def test_ao3_defaults_decode_as_utf8_not_the_platform_locale_default():
    """The Windows regression found on ci.yml run 34142222493 (test-windows job, first run):
    `open()` with no `encoding=` resolves the platform's LOCALE default — UTF-8 on macOS/Linux,
    but cp1252 on Windows. The bundled AO3 taxonomy CSVs (src/scourgify/defaults/ao3/*.csv) are
    UTF-8 and contain bytes cp1252 cannot decode (byte 0x90 crashed wrangle.load_maps() ->
    ao3_pairs() -> artifacts.read_rows() with UnicodeDecodeError on the real Windows runner).
    Every text-mode `open()` in src/scourgify/ must now pass `encoding="utf-8"` explicitly.

    Two checks, because a real Windows host isn't available to run this suite against:
    1. A static source-grep — the actual regression guard, host-independent — asserting no
       text-mode `open(` call in src/scourgify/*.py lacks `encoding=` (binary-mode "rb"/"wb"/"ab"
       opens are exempt; they carry no text encoding).
    2. A behavioral check that the real bundled AO3 characters.csv is genuinely non-ASCII
       (so check 1 isn't guarding an empty case) and that artifacts.read_rows() decodes it
       without raising, on whatever locale this host happens to run under.
    """
    import re as _re

    def _open_calls(src: str) -> list:
        """Every `open(...)` call's argument text, paren-balanced (a plain regex stops at the
        first ')', which is wrong the moment an argument is itself a call, e.g.
        `open(_ao3_vocab_path(), encoding="utf-8")`)."""
        out = []
        for m in _re.finditer(r"\bopen\(", src):
            depth, i = 1, m.end()
            start = i
            while depth and i < len(src):
                if src[i] == "(":
                    depth += 1
                elif src[i] == ")":
                    depth -= 1
                i += 1
            out.append(src[start:i - 1])
        return out

    core_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "scourgify")
    offenders = []
    for fn in sorted(os.listdir(core_dir)):
        if not fn.endswith(".py"):
            continue
        src = open(os.path.join(core_dir, fn), encoding="utf-8").read()
        for args in _open_calls(src):
            if "encoding=" in args:
                continue
            if any(mode in args for mode in ('"rb"', "'rb'", '"wb"', "'wb'", '"ab"', "'ab'")):
                continue                                    # binary mode — no text encoding to pin
            offenders.append(f"{fn}: open({args})")
    assert not offenders, f"text-mode open() missing encoding=\"utf-8\":\n  " + "\n  ".join(offenders)

    ao3_path = os.path.join(core_dir, "defaults", "ao3", "characters.csv")
    raw = open(ao3_path, "rb").read()
    assert any(b > 0x7F for b in raw), "fixture must contain non-ASCII bytes or this test proves nothing"
    rows = artifacts.read_rows(ao3_path)
    assert rows, "expected at least one row from the bundled AO3 characters.csv"
    assert any(ord(ch) > 0x7F for r in rows for v in r.values() for ch in v), \
        "expected at least one decoded row with a non-ASCII character"


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
