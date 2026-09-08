#!/usr/bin/env python3
"""Pins wrangle's Plan (ONE full-library compute feeding preview/guards/step/write) and
transform's decision log — the single explanation source the audit report reads.
No framework:  uv run tests/test_plan.py   (also pytest-collectable). No Calibre, no network."""
import os, sys, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from fixture_db import build
from scourgify import common, wrangle
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


def test_trope_route_drop_deletes_the_tag():
    """`drop` is one of the five routes read_tropes allowlists, but the route chain had no branch
    for it, so every such rule fell through to the tag fold and silently did nothing. Honored
    now: the tag goes, and the decision log says so."""
    log = []
    m = maps(trope={"anbu": ("ANBU", "drop")})                      # keys are norm()'d
    nd, _, _ = wrangle.transform({"tags": ["ANBU", "Keeper"]}, m, BEH, log=log)
    assert nd["tags"] == ["Keeper"]
    assert ("drop", "tags", "ANBU", "") in set(log)


def test_trope_route_drop_beats_its_own_rename_target():
    """A `variant,canonical,drop` row drops the variant rather than renaming it — the route wins
    over the canonical. Pinned because the two readings are not obviously distinguishable and
    silently renaming instead would resurrect the value under a different name."""
    nd, _, _ = wrangle.transform({"tags": ["ANBU - Freeform"]},
                                 maps(trope={"anbu freeform": ("ANBU", "drop")}), BEH)
    assert nd["tags"] == []


def test_trope_fold_target_that_is_junk_is_dropped_in_one_pass():
    """junk was only ever tested against the SOURCE tag, never the fold target, so a mapping
    X -> Y with Y in junk.txt took two passes to settle. 3,862 real mappings had this shape."""
    log = []
    m = maps(trope={"wip": ("Work In Progress", "tag")}, junk_exact={"work in progress"})
    nd, _, _ = wrangle.transform({"tags": ["WIP"]}, m, BEH, log=log)
    assert nd["tags"] == []
    assert ("drop", "tags", "WIP", "") in set(log)


def test_redundancy_strip_and_cross_column_routes_are_explained():
    """Every value transform removes must appear in the decision log — the audit's examples are
    read from that log, so a silent strip is a change the user is never shown. A tag identical
    to a value already in a structured column vanished with no decision at all."""
    log = []
    m = maps(trope={"house targaryen": ("House Targaryen", "fandom")})
    d = {"fandoms": ["A Song of Ice and Fire"], "characters": ["Jon Snow"],
         "tags": ["House Targaryen", "Jon Snow", "A Song of Ice and Fire"]}
    wrangle.transform(d, m, BEH, known_chars={"jon snow"}, log=log)
    dec = set(log)
    assert ("move", "tags → fandoms", "House Targaryen", "") in dec     # routed across columns
    assert ("move", "tags → characters", "Jon Snow", "") in dec         # known-character rescue
    assert ("strip", "tags", "A Song of Ice and Fire", "") in dec       # redundant with #fandoms
    # a strip is not a junk drop — the audit reports them under separate headings
    assert ("drop", "tags", "A Song of Ice and Fire", "") not in dec


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


def test_restrict_narrows_the_write_set_and_the_guards():
    """restrict() narrows what gets WRITTEN, not what gets READ — transform still sees the whole
    library (tagcanon, known_chars). The guards then judge the scoped books: book 1's data loss
    must not abort a write that only touches book 2."""
    lib = tempfile.mkdtemp()
    build(os.path.join(lib, "metadata.db"),
          [dict(id=1, added="2026-01-01", tags=["Complete"]),
           dict(id=2, added="2026-01-02", tags=["Complete", "Keeper"])],
          custom=[("fandoms", {1: "Ghost", 2: "Real Fandom"})]).close()
    old = os.environ.get("CALIBRE_LIBRARY"); os.environ["CALIBRE_LIBRARY"] = lib
    try:
        cfg = load_config(path="/nonexistent/config.toml")
        m = maps(fan={"Ghost": ""}, junk_exact={"complete"})

        full = wrangle.plan(cfg, m)
        assert full.n_books == 2 and full.lostF == 1
        try:
            full.guard(); assert False, "expected the data-loss guard to abort the full plan"
        except common.GuardrailError:
            pass

        scoped = wrangle.plan(cfg, m).restrict([2])
        assert scoped.n_books == 1                       # only book 2 is in the write set
        assert set(scoped.diffs) == {2}
        assert 1 not in scoped.changes["tags"]
        assert (scoped.lostF, scoped.lostC) == (0, 0)    # book 1's loss is out of scope
        assert (scoped.tagsB, scoped.tagsA) == (2, 1)
        scoped.guard()                                   # no abort — this is the regression
        assert scoped.tagcanon == full.tagcanon          # global context survived the narrowing
    finally:
        os.environ.pop("CALIBRE_LIBRARY", None) if old is None else os.environ.__setitem__("CALIBRE_LIBRARY", old)


def test_restrict_to_nothing_is_a_clean_no_op():
    lib = tempfile.mkdtemp()
    build(os.path.join(lib, "metadata.db"),
          [dict(id=1, added="2026-01-01", tags=["Complete"])]).close()
    old = os.environ.get("CALIBRE_LIBRARY"); os.environ["CALIBRE_LIBRARY"] = lib
    try:
        cfg = load_config(path="/nonexistent/config.toml")
        p = wrangle.plan(cfg, maps(junk_exact={"complete"})).restrict([999])
        assert p.n_books == 0 and not p.diffs and (p.tagsB, p.tagsA) == (0, 0)
        p.guard()
    finally:
        os.environ.pop("CALIBRE_LIBRARY", None) if old is None else os.environ.__setitem__("CALIBRE_LIBRARY", old)


def test_write_carries_expected_from_perbook_and_skips_drifted_books():
    """Plan.write()'s op carries `expected` built from self.perbook (per-book state at compute
    time), NOT self.before (library-wide distinct-value SETS — Codex agreed concern 4, the
    previous plan text named self.before and was wrong). Mutating one book's tags between compute
    and write must skip exactly that book. Proved at the near side of the calibre-debug subprocess
    boundary (the JSON handed to the writer), since a real Calibre write isn't available here."""
    import contextlib, io, json, shutil, sqlite3, subprocess
    from scourgify import common
    lib = tempfile.mkdtemp()
    build(os.path.join(lib, "metadata.db"),
          [dict(id=1, added="2026-01-01", tags=["Complete", "Keeper"]),
           dict(id=2, added="2026-01-02", tags=["Complete", "Other"])]).close()
    home = tempfile.mkdtemp()
    old = {k: os.environ.get(k) for k in ("CALIBRE_LIBRARY", "SCOURGIFY_HOME")}
    os.environ["CALIBRE_LIBRARY"] = lib
    os.environ["SCOURGIFY_HOME"] = home
    saved = (subprocess.run, shutil.which, common.calibre_open)
    captured = []

    def fake_run(cmd, **kw):
        with open(cmd[-1], encoding="utf-8") as f:
            captured.append(json.load(f))
        return type("P", (), {"returncode": 0})()
    subprocess.run = fake_run
    shutil.which = lambda name, *a, **k: "/bin/true" if "calibre" in name else saved[1](name, *a, **k)
    common.calibre_open = lambda: False
    try:
        cfg = load_config(path="/nonexistent/config.toml")
        p = wrangle.plan(cfg, maps(junk_exact={"complete"}))
        assert p.changes["tags"][1] == ["Keeper"]
        assert p.changes["tags"][2] == ["Other"]
        # simulate a Calibre edit between compute() and write(): book 2 gains a brand-new tag,
        # drifting its raw tag SET away from the perbook state the plan was computed against.
        con = sqlite3.connect(os.path.join(lib, "metadata.db"))
        next_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM tags").fetchone()[0]
        con.execute("INSERT INTO tags VALUES (?, ?)", (next_id, "Hand-Added"))
        con.execute("INSERT INTO books_tags_link VALUES (2, ?)", (next_id,))
        con.commit(); con.close()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            p.write()
    finally:
        subprocess.run, shutil.which, common.calibre_open = saved
        for k, v in old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    (ops,) = captured
    (op,) = [o for o in ops if o["op"] == "set_field" and o["field"] == "tags"]
    assert set(op["values"]) == {"1"}, op["values"]        # book 2 dropped: its tags drifted
    # `expected` is the PLAN-TIME (pre-transform) raw tag set — what the column held when the
    # plan was computed, not the post-transform write value.
    assert op["expected"]["1"] == ["Complete", "Keeper"], op["expected"]


def test_write_applies_a_single_valued_column_when_unchanged_and_skips_it_on_real_drift():
    """CR-01 (code review 2026-09-07): `Plan.write()` builds `expected` from `self.perbook`,
    which `read_library()` populates via `read_custom_column(con, label, multi=True)`
    UNCONDITIONALLY for every non-tags column (wrangle.py's own read path, not this test's setup)
    — so `expected` is always list-shaped, even for a column the real Calibre schema reports as
    single-valued (`is_multiple=0`). The apply-time conflict filter's before-read
    (`common.column_values`), by contrast, correctly asks the schema and returns a plain scalar
    for such a column. Before the fix, comparing `"Naruto Shippuden"`-shaped scalars against
    `["Naruto"]`-shaped lists meant EVERY op for a single-valued wrangle-managed column
    (`#fandoms`/`#characters`/`#relationships`/`#genres` on a library that predates scourgify, or
    one imported from another tool) was reported as drifted and silently, permanently dropped —
    even on the very first run, when nothing had actually changed. This is the exact scenario
    `setup.py`'s label-only column adoption (section [4]) can hand a real user.

    Book 1's #fandoms is single-valued and unchanged since the plan was computed — the op must
    still apply. Book 2's #fandoms is ALSO single-valued, but is genuinely edited directly in
    Calibre between compute() and write() (simulating another process/the GUI) — that op must
    still be skipped, proving this fix does not simply disable the conflict filter for
    single-valued columns."""
    import contextlib, io, json, shutil, sqlite3, subprocess
    from scourgify import common
    lib = tempfile.mkdtemp()
    build(os.path.join(lib, "metadata.db"),
          [dict(id=1, added="2026-01-01"), dict(id=2, added="2026-01-02")],
          custom=[("fandoms", {1: "Naruto", 2: "Bleach"})])   # NOT in multi_labels -> is_multiple=0
    home = tempfile.mkdtemp()
    old = {k: os.environ.get(k) for k in ("CALIBRE_LIBRARY", "SCOURGIFY_HOME")}
    os.environ["CALIBRE_LIBRARY"] = lib
    os.environ["SCOURGIFY_HOME"] = home
    saved = (subprocess.run, shutil.which, common.calibre_open)
    captured = []

    def fake_run(cmd, **kw):
        with open(cmd[-1], encoding="utf-8") as f:
            captured.append(json.load(f))
        return type("P", (), {"returncode": 0})()
    subprocess.run = fake_run
    shutil.which = lambda name, *a, **k: "/bin/true" if "calibre" in name else saved[1](name, *a, **k)
    common.calibre_open = lambda: False
    try:
        cfg = load_config(path="/nonexistent/config.toml")   # cfg["columns"]["fandoms"] = "#fandoms"
        p = wrangle.plan(cfg, maps(fan={"Naruto": "Naruto Shippuden", "Bleach": "Bleach TYBW"}))
        assert p.changes["#fandoms"][1] == ["Naruto Shippuden"]
        assert p.changes["#fandoms"][2] == ["Bleach TYBW"]
        # Simulate a Calibre edit to book 2's SINGLE-valued #fandoms between compute() and
        # write(): a real, out-of-band change this write must not clobber.
        con = sqlite3.connect(os.path.join(lib, "metadata.db"))
        con.execute("UPDATE custom_column_1 SET value=? WHERE book=?", ("Bleach: Actually Different", 2))
        con.commit(); con.close()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            p.write()
    finally:
        subprocess.run, shutil.which, common.calibre_open = saved
        for k, v in old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    (ops,) = captured
    (op,) = [o for o in ops if o["op"] == "set_field" and o["field"] == "#fandoms"]
    assert set(op["values"]) == {"1"}, \
        f"book 1 (unchanged single-valued column) should have applied, got {op['values']}"
    assert op["values"]["1"] == ["Naruto Shippuden"]


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
