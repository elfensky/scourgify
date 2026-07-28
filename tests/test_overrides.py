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


def _home(td):
    """Context: point $SCOURGIFY_HOME at td for the duration (restores the old value)."""
    import contextlib
    @contextlib.contextmanager
    def cm():
        old = os.environ.get("SCOURGIFY_HOME"); os.environ["SCOURGIFY_HOME"] = td
        try: yield
        finally:
            os.environ.pop("SCOURGIFY_HOME", None) if old is None else os.environ.__setitem__("SCOURGIFY_HOME", old)
    return cm()


def test_ov_path_honors_config_overrides_dir():
    """The dir resolution wrangle/setup already honor (cfg[overrides].dir) must be the one the
    override WRITERS use too — one owner, or promote --apply writes where wrangle never reads."""
    with tempfile.TemporaryDirectory() as td, _home(td):
        open(os.path.join(td, "config.toml"), "w").write("[overrides]\ndir = \"myrules\"\n")
        assert overrides.ov_path("x.csv") == os.path.join(td, "myrules", "x.csv")


def test_promote_writes_land_where_wrangle_reads():
    """End-to-end pin of the config-divergence bug: with a relocated overrides dir, a trope row
    appended through the format owner must be visible to wrangle.load_maps' default resolution."""
    from scourgify.common import load_config
    from scourgify import wrangle as wr
    with tempfile.TemporaryDirectory() as td, _home(td):
        open(os.path.join(td, "config.toml"), "w").write("[overrides]\ndir = \"myrules\"\n")
        overrides.append_rows(overrides.ov_path("tropes.csv"), HDR, [["Slowburn", "Slow Burn", "tag"]])
        dflt = os.path.join(td, "empty_defaults"); os.makedirs(dflt)
        m = wr.load_maps(load_config(), defaults_dir=dflt)       # production overrides-dir resolution
        assert m["trope"].get("Slowburn") == ("Slow Burn", "tag"), \
            "append_rows wrote to a dir load_maps doesn't read"


def test_read_aliases_sniffs_delimiter():
    """promote_aliases.csv is written by a ';'-honoring writer — the reader must sniff too."""
    td = tempfile.mkdtemp()
    pc = os.path.join(td, "comma.csv")
    open(pc, "w").write("candidate,target\nSlowburn,Slow Burn\n")
    ps = os.path.join(td, "semi.csv")
    open(ps, "w").write("candidate;target\nFix-it;Fix-It\n")
    assert overrides.read_aliases(pc) == {"slowburn": "Slow Burn"}
    assert overrides.read_aliases(ps) == {"fix-it": "Fix-It"}
    assert overrides.read_aliases(os.path.join(td, "absent.csv")) == {}


def test_classify_reads_semicolon_alias_file():
    """classify.load_aliases goes through the sniffing reader — a ';' file is not invisible."""
    from scourgify import classify
    with tempfile.TemporaryDirectory() as td, _home(td):
        os.makedirs(os.path.join(td, "overrides"))
        open(os.path.join(td, "overrides", "promote_aliases.csv"), "w").write(
            "candidate;target\nSlowburn;Slow Burn\n")
        classify.clear_caches()
        try:
            assert classify.load_aliases().get("slowburn") == "Slow Burn"
        finally:
            classify.clear_caches()


def test_merge_vocab_minus_semantics():
    """The overrides vocab format: a plain line appends (case-insensitive dedup), '-term' removes
    (later lines win), comments/blanks ignored. Owned next to the writer that appends to the file."""
    td = tempfile.mkdtemp()
    p = os.path.join(td, "classify_vocab.txt")
    open(p, "w").write("# comment\n-slow burn\nFound Family\nfound family\n")
    assert overrides.merge_vocab(["Slow Burn", "Time Loop"], p) == ["Time Loop", "Found Family"]
    assert overrides.merge_vocab(["A"], os.path.join(td, "absent.txt")) == ["A"]


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
