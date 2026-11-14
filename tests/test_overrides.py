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
    open(p, "w", encoding="utf-8").write("variant;canonical;route\nX;Y;tag\n")     # legacy ';' file (old promote format)
    overrides.append_rows(p, HDR, [["Amoral Deity", "Morality", "tag"]])
    rows = read_tropes(p)                                        # read_tropes sniffs the delimiter
    assert ("X", "Y", "tag") in rows and ("Amoral Deity", "Morality", "tag") in rows


def test_append_lines_dedupes():
    p = os.path.join(tempfile.mkdtemp(), "classify_vocab.txt")
    assert overrides.append_lines(p, ["Soul Bond", "Dream Logic"]) == ["Soul Bond", "Dream Logic"]
    assert overrides.append_lines(p, ["Dream Logic", "Gacha System"]) == ["Gacha System"]
    assert open(p, encoding="utf-8").read().splitlines() == ["Soul Bond", "Dream Logic", "Gacha System"]


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
        open(os.path.join(td, "config.toml"), "w", encoding="utf-8").write("[overrides]\ndir = \"myrules\"\n")
        assert overrides.ov_path("x.csv") == os.path.join(td, "myrules", "x.csv")


def test_promote_writes_land_where_wrangle_reads():
    """End-to-end pin of the config-divergence bug: with a relocated overrides dir, a trope row
    appended through the format owner must be visible to wrangle.load_maps' default resolution."""
    from scourgify.common import load_config
    from scourgify import wrangle as wr
    with tempfile.TemporaryDirectory() as td, _home(td):
        open(os.path.join(td, "config.toml"), "w", encoding="utf-8").write("[overrides]\ndir = \"myrules\"\n")
        overrides.append_rows(overrides.ov_path("tropes.csv"), HDR, [["Slowburn", "Slow Burn", "tag"]])
        dflt = os.path.join(td, "empty_defaults"); os.makedirs(dflt)
        m = wr.load_maps(load_config(), defaults_dir=dflt)       # production overrides-dir resolution
        assert m["trope"].get("Slowburn") == ("Slow Burn", "tag"), \
            "append_rows wrote to a dir load_maps doesn't read"


def test_read_aliases_sniffs_delimiter():
    """promote_aliases.csv is written by a ';'-honoring writer — the reader must sniff too."""
    td = tempfile.mkdtemp()
    pc = os.path.join(td, "comma.csv")
    open(pc, "w", encoding="utf-8").write("candidate,target\nSlowburn,Slow Burn\n")
    ps = os.path.join(td, "semi.csv")
    open(ps, "w", encoding="utf-8").write("candidate;target\nFix-it;Fix-It\n")
    assert overrides.read_aliases(pc) == {"slowburn": "Slow Burn"}
    assert overrides.read_aliases(ps) == {"fix-it": "Fix-It"}
    assert overrides.read_aliases(os.path.join(td, "absent.csv")) == {}


def test_classify_reads_semicolon_alias_file():
    """classify.load_aliases goes through the sniffing reader — a ';' file is not invisible."""
    from scourgify import classify
    with tempfile.TemporaryDirectory() as td, _home(td):
        os.makedirs(os.path.join(td, "overrides"))
        open(os.path.join(td, "overrides", "promote_aliases.csv"), "w", encoding="utf-8").write(
            "candidate;target\nSlowburn;Slow Burn\n")
        classify.clear_caches()
        try:
            assert classify.load_aliases().get("slowburn") == "Slow Burn"
        finally:
            classify.clear_caches()


def test_step_pick_decide_seam_pairs_and_unticked_item_writes_nothing():
    """(label, payload) pairs (D-03), and an unticked line must not reach build_overrides' write —
    step_pick narrows the accepted {(file, line)} set the caller (overrides_cmd / the wizard)
    passes as `only=`, so an unticked line is simply absent from what gets written."""
    auto = {"fandoms.csv": ["A,A"], "tropes.csv": ["B,B,tag", "C,C,tag"]}
    recorded = {}
    def stub(title, items, subtitle=""):
        recorded["items"] = items
        return [0, 2], [1], "apply"          # untick tropes.csv's "B,B,tag" (index 1)
    result = overrides.step_pick(auto, decide=stub)
    pairs = [(fn, l) for fn in sorted(auto) for l in sorted(set(auto[fn]))]
    assert [label for label, _ in recorded["items"]] == [f"[dim]{fn}[/]  {l}" for fn, l in pairs]
    assert all(payload["class"] == "auto" and payload["kind"] == "override"
              for _, payload in recorded["items"])
    assert result == {pairs[0], pairs[2]}
    assert ("tropes.csv", "B,B,tag") not in result       # the unticked line is excluded

    # end-to-end: build_overrides(only=result) writes exactly the accepted lines, never the rejected one
    td = tempfile.mkdtemp()
    old = os.environ.get("SCOURGIFY_HOME"); os.environ["SCOURGIFY_HOME"] = td
    try:
        # a rejects.csv that would synthesize the same three auto lines
        rejects = os.path.join(td, "rejects.csv")
        with open(rejects, "w", encoding="utf-8") as f:
            f.write("stage,book,title,kind,column,before,after,class\n"
                    "wrangle,1,T,rename,fandoms,A,A,auto\n"
                    "wrangle,2,T,rename,tags,B,B,auto\n"
                    "wrangle,3,T,rename,tags,C,C,auto\n")
        import unittest.mock
        with unittest.mock.patch("scourgify.common.rejects_path", return_value=rejects):
            written = overrides.build_overrides(True, master=False, only=result)
        assert "A,A" in written.get("fandoms.csv", [])
        assert "C,C,tag" in written.get("tropes.csv", [])
        assert "B,B,tag" not in written.get("tropes.csv", [])       # the unticked line never lands
        content = open(os.path.join(td, "overrides", "tropes.csv"), encoding="utf-8").read()
        assert "B,B,tag" not in content
    finally:
        os.environ.pop("SCOURGIFY_HOME", None) if old is None else os.environ.__setitem__("SCOURGIFY_HOME", old)


def test_edit_payload_matches_reject_row_kind_and_class():
    """The payload's kind/class must be the SAME values `_reject_row` would log for the identical
    edit — both now derive from `_edit_payload`'s one `synth_reject()` call, so they can't drift."""
    lab2key = {"fandoms": "fandoms"}
    payload = overrides._edit_payload(lab2key, 1, "Title", "rename", "fandoms", "OldF", "NewF")
    row = overrides._reject_row(lab2key, 1, "Title", "rename", "fandoms", "OldF", "NewF")
    assert payload["kind"] == row["kind"] == "rename"
    assert payload["class"] == row["class"]
    assert payload["field"] == row["column"]
    assert payload["before"] == row["before"] and payload["after"] == row["after"]


def test_merge_vocab_minus_semantics():
    """The overrides vocab format: a plain line appends (case-insensitive dedup), '-term' removes
    (later lines win), comments/blanks ignored. Owned next to the writer that appends to the file."""
    td = tempfile.mkdtemp()
    p = os.path.join(td, "classify_vocab.txt")
    open(p, "w", encoding="utf-8").write("# comment\n-slow burn\nFound Family\nfound family\n")
    assert overrides.merge_vocab(["Slow Burn", "Time Loop"], p) == ["Time Loop", "Found Family"]
    assert overrides.merge_vocab(["A"], os.path.join(td, "absent.txt")) == ["A"]


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
