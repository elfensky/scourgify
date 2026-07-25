#!/usr/bin/env python3
"""Pins the overrides/ format owner (overrides.py): append-if-absent, header-on-create, and
delimiter sniffing — the rule that keeps promote's appends from corrupting a hand-made ';' file
(or a rejects→overrides comma file) now that every writer goes through one module.
No framework:  uv run tests/test_overrides.py   (also pytest-collectable)."""
import os, sys, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from scourgify import overrides
from scourgify.wrangle import read_tropes

HDR = ["variant", "canonical", "route"]


def test_append_rows_creates_with_header_and_dedupes():
    p = os.path.join(tempfile.mkdtemp(), "tropes.csv")
    assert overrides.append_rows(p, HDR, [["Slowburn", "Slow Burn", "tag"]]) == 1
    # re-appending the same row is a no-op; a new row still lands
    assert overrides.append_rows(p, HDR, [["Slowburn", "Slow Burn", "tag"], ["Fixit", "Fix-It", "tag"]]) == 1
    rows = read_tropes(p)
    assert ("Slowburn", "Slow Burn", "tag") in rows and ("Fixit", "Fix-It", "tag") in rows


def test_append_rows_honors_an_existing_semicolon_file():
    p = os.path.join(tempfile.mkdtemp(), "tropes.csv")
    open(p, "w").write("variant;canonical;route\nX;Y;tag\n")     # legacy ';' file (old promote format)
    overrides.append_rows(p, HDR, [["Amoral Deity", "Morality", "tag"]])
    rows = read_tropes(p)                                        # read_tropes sniffs the delimiter
    assert ("X", "Y", "tag") in rows and ("Amoral Deity", "Morality", "tag") in rows


def test_append_lines_dedupes():
    p = os.path.join(tempfile.mkdtemp(), "classify_vocab.txt")
    assert overrides.append_lines(p, ["Soul Bond", "Dream Logic"]) == ["Soul Bond", "Dream Logic"]
    assert overrides.append_lines(p, ["Dream Logic", "Gacha System"]) == ["Gacha System"]
    assert open(p).read().splitlines() == ["Soul Bond", "Dream Logic", "Gacha System"]


def test_ov_path_resolves_under_scourgify_home():
    with tempfile.TemporaryDirectory() as td:
        old = os.environ.get("SCOURGIFY_HOME"); os.environ["SCOURGIFY_HOME"] = td
        try:
            assert overrides.ov_path("x.csv") == os.path.join(td, "overrides", "x.csv")
        finally:
            os.environ.pop("SCOURGIFY_HOME", None) if old is None else os.environ.__setitem__("SCOURGIFY_HOME", old)


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
