#!/usr/bin/env python3
"""Pins wrangle's Plan (ONE full-library compute feeding preview/guards/step/write) and
transform's decision log — the single explanation source the audit report reads.
No framework:  uv run tests/test_plan.py   (also pytest-collectable). No Calibre, no network."""
import os, sys, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from fixture_db import build
from scourgify import wrangle
from scourgify.common import load_config, norm

BEH = load_config(path="/nonexistent/config.toml")["behavior"]


def maps(**over):
    m = {"char": {}, "char_fd": {}, "fan": {}, "fanvals": set(), "trope": {}, "fan_block": set(),
         "decompose": {}, "gsplit": {}, "gcanon": {}, "gallow": set(), "rating": set(),
         "junk_exact": set(), "junk_rx": []}
    m.update(over)
    m.setdefault("fanvals", {norm(v) for v in m["fan"].values()})
    return m


def test_transform_decision_log_kinds():
    log = []
    m = maps(fan={"HP": "Harry Potter"}, char={"Harry P.": "Harry Potter"},
             gsplit={"Action/Adventure": ["Action", "Adventure"]}, gallow={"action", "adventure"},
             junk_exact={"complete"})
    d = {"fandoms": ["HP"], "characters": ["Harry P."], "genres": ["Action/Adventure", "Fluffy"],
         "tags": ["Complete"]}
    wrangle.transform(d, m, BEH, log=log)
    dec = set(log)
    assert ("canon", "fandoms", "HP", "Harry Potter") in dec
    assert ("fold", "characters", "Harry P.", "Harry Potter") in dec
    assert ("split", "genres", "Action/Adventure", "Action|Adventure") in dec
    assert ("move", "genres → tags", "Fluffy", "") in dec
    assert ("drop", "tags", "Complete", "") in dec


def test_transform_without_log_is_unchanged():
    nd, lf, lc = wrangle.transform({"fandoms": ["HP"]}, maps(fan={"HP": "Harry Potter"}), BEH)
    assert nd["fandoms"] == ["Harry Potter"] and not lf and not lc


def test_plan_single_compute_feeds_changes_guards_and_decisions():
    lib = tempfile.mkdtemp()
    build(os.path.join(lib, "metadata.db"),
          [dict(id=1, added="2026-01-01", tags=["Complete", "Keeper"]),
           dict(id=2, added="2026-01-02", tags=["Keeper"])]).close()
    old = os.environ.get("CALIBRE_LIBRARY"); os.environ["CALIBRE_LIBRARY"] = lib
    try:
        cfg = load_config(path="/nonexistent/config.toml")
        p = wrangle.plan(cfg, maps(junk_exact={"complete"}))
        assert p.n_books == 1                                    # only book 1 changes
        assert p.changes["tags"][1] == ["Keeper"]                # the write-set the wizard/CLI send
        assert ("drop", "tags", "Complete", "") in set(p.decisions)   # the audit's explanation source
        assert (p.tagsB, p.tagsA) == (3, 2)
        p.guard()                                                # no data-loss shape -> no SystemExit
    finally:
        os.environ.pop("CALIBRE_LIBRARY", None) if old is None else os.environ.__setitem__("CALIBRE_LIBRARY", old)


def test_plan_keeps_loss_accounting_per_book():
    """The SAFETY counters are per-book so a scoped write can be judged on its own books
    (see restrict()). The aggregate properties keep the old names and the old meaning."""
    lib = tempfile.mkdtemp()
    build(os.path.join(lib, "metadata.db"),
          [dict(id=1, added="2026-01-01", tags=["Complete"]),
           dict(id=2, added="2026-01-02", tags=["Complete", "Keeper"])],
          custom=[("fandoms", {1: "Ghost", 2: "Real Fandom"})]).close()
    old = os.environ.get("CALIBRE_LIBRARY"); os.environ["CALIBRE_LIBRARY"] = lib
    try:
        cfg = load_config(path="/nonexistent/config.toml")
        p = wrangle.plan(cfg, maps(fan={"Ghost": ""}, junk_exact={"complete"}))
        assert p.lost == {1: (True, False)}          # only book 1 loses its last fandom
        assert p.tagn == {1: (1, 0), 2: (2, 1)}      # every book carries its tag counts
        assert (p.lostF, p.lostC) == (1, 0)          # aggregates unchanged in name and meaning
        assert (p.tagsB, p.tagsA) == (3, 1)
    finally:
        os.environ.pop("CALIBRE_LIBRARY", None) if old is None else os.environ.__setitem__("CALIBRE_LIBRARY", old)


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
