#!/usr/bin/env python3
"""The edit log (#49, plugin phase 3 — NLSpec B7 + the atomicity contract). No framework:
  uv run tests/test_editlog.py   (also pytest-collectable). No Calibre, no network.

What breaks in the real world if these fail: scourgify writes to a live library with no record
of what it did to which book — and the GUI's "no confirmation dialogs" stance (B1) stops being
honest, because undo (#51) replays exactly these lines. A shape change here silently breaks
history and undo, which read the file and cannot ask it what version it is."""
import os, io, sys, json, time, shutil, sqlite3, tempfile, contextlib, subprocess

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scourgify import common, editlog
import fixture_db

LIB_UUID = "b4366c4a-0000-0000-0000-000000000000"


def _lib(n=3):
    """A throwaway library: tags on every book, a multi custom column and a single one."""
    d = tempfile.mkdtemp()
    con = fixture_db.build(os.path.join(d, "metadata.db"),
                           [{"id": b, "tags": ["Harem", "Mature"]} for b in range(1, n + 1)],
                           custom=[("fandoms", {1: ["Naruto", "Bleach"], 2: ["Worm"]}),
                                   ("status", {1: "In-Progress", 2: "Hiatus"})],
                           link_labels=("fandoms",), multi_labels=("fandoms",), uuid=LIB_UUID)
    con.commit(); con.close()
    return d


@contextlib.contextmanager
def _pointed_at(lib):
    home = tempfile.mkdtemp()
    old = {k: os.environ.get(k) for k in ("CALIBRE_LIBRARY", "SCOURGIFY_HOME")}
    os.environ["CALIBRE_LIBRARY"], os.environ["SCOURGIFY_HOME"] = lib, home
    try: yield home
    finally:
        for k, v in old.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v


def _read(home=None):
    """`home` is accepted (and ignored) for call-site compatibility — the log path is uuid-scoped
    now, so it is resolved through editlog.log_path() (which reads the CALIBRE_LIBRARY still set
    by the enclosing _pointed_at() block) rather than a literal os.path.join(home, "data", ...)."""
    p = editlog.log_path()
    return [json.loads(l) for l in open(p)] if os.path.exists(p) else []


def _quiet(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf): fn(*a, **k)
    return buf.getvalue()


# ---------------- the run id (#45) ----------------
def test_run_ids_never_collide_within_a_second():
    """#45 cost an archive to whole-second filenames. A run id is worse: two runs sharing one id
    merge in every history query and undo replays both halves as one."""
    ids = {editlog.new_run_id() for _ in range(500)}
    assert len(ids) == 500, "two run ids collided"
    # the timestamp leads, so plain string order is chronological order (history reads it that way)
    assert all(i[:15] == time.strftime("%Y%m%dT%H%M%S")[:15] and i[15] == "-" for i in ids)


# ---------------- the record shape ----------------
def test_one_line_per_book_field_op_bracketed_by_header_and_footer():
    """The atomicity contract's unit of logging. One record per RUN would be unbounded at 7,949
    books; one line per (book, field) keeps every record small and a crash recoverable."""
    with _pointed_at(_lib()) as home:
        ops = [common.op_set_field("tags", {1: ["Harem", "Isekai"], 2: ["Mature"]}),
               common.op_set_field("#status", {1: "Hiatus"})]
        before = {"tags": {1: ["Harem"], 2: ["Mature"]}, "#status": {1: "In-Progress"}}
        rec = editlog.start("wrangle", ops, before, scope="2 books", library=LIB_UUID)
        editlog.finish(rec, "ok")
        lines = _read(home)
    assert [l["kind"] for l in lines] == ["run", "op", "op", "op", "end"]
    head, foot = lines[0], lines[-1]
    assert head["tool"] == "wrangle" and head["scope"] == "2 books" and head["library"] == LIB_UUID
    assert all(l["run"] == head["run"] for l in lines), "lines of one run don't share its id"
    assert foot["outcome"] == "ok" and foot["ops"] == 3 and foot["books"] == 2
    tags1 = [l for l in lines if l["kind"] == "op" and l.get("book") == 1 and l["field"] == "tags"][0]
    assert tags1["before"] == ["Harem"] and tags1["after"] == ["Harem", "Isekai"] and tags1["undo"] is True


def test_stamps_and_prefs_are_logged_but_marked_un_undoable():
    """Spec B7.2: logged, excluded from replay, and SAID so rather than silently dropped. A
    full-library stamp is one line with a count — per-book lines would multiply the log by the
    library size on every wrangle run and give undo nothing it can use."""
    with _pointed_at(_lib()) as home:
        rec = editlog.start("classify", [common.op_stamp_now("#wrangled", [1, 2, 3]),
                                         common.op_set_pref("scourgify:x", {"a": 1})], {})
        editlog.finish(rec)
        ops = [l for l in _read(home) if l["kind"] == "op"]
    assert [l["op"] for l in ops] == ["stamp_now", "set_pref"]
    assert all(l["undo"] is False for l in ops)
    assert ops[0]["books"] == 3


def test_a_killed_run_leaves_its_ops_without_a_footer():
    """The partial-run marker. Op lines go down BEFORE the ops apply, so a process killed
    mid-write still names what it was doing; the missing footer is what says so."""
    with _pointed_at(_lib()) as home:
        editlog.start("wrangle", [common.op_set_field("tags", {1: ["x"]})], {})   # no finish()
        lines = _read(home)
    assert [l["kind"] for l in lines] == ["run", "op"]
    assert not [l for l in lines if l["kind"] == "end"], "a killed run must not have a footer"


# ---------------- the before-read ----------------
def test_before_values_read_tags_and_both_column_shapes():
    """The guard reads this column and throws the values away (`_populated_books` short-circuits
    tags to DISTINCT book). If the extra join is wrong, undo restores the wrong values — worse
    than no undo, because it looks like it worked."""
    lib = _lib()
    con = sqlite3.connect(os.path.join(lib, "metadata.db"))
    try:
        assert common.column_values(con, "tags", [1, 3]) == {1: ["Harem", "Mature"], 3: ["Harem", "Mature"]}
        assert common.column_values(con, "#fandoms", [1, 2]) == {1: ["Naruto", "Bleach"], 2: ["Worm"]}
        assert common.column_values(con, "#status", [1, 3]) == {1: "In-Progress", 3: None}
        assert common.column_is_multiple(con, "#fandoms") and not common.column_is_multiple(con, "#status")
        assert common.library_uuid(con) == LIB_UUID
    finally:
        con.close()


def test_before_values_covers_only_the_books_the_ops_touch():
    lib = _lib(50)
    con = sqlite3.connect(os.path.join(lib, "metadata.db"))
    try:
        got = editlog.before_values(lambda f, bs: common.column_values(con, f, bs),
                                    [common.op_set_field("tags", {7: ["x"], 9: ["y"]})])
    finally:
        con.close()
    assert set(got["tags"]) == {7, 9}, "the log read the whole library to describe two books"


# ---------------- the funnels ----------------
class _Api:
    """The five methods the in-process path calls (mirrors tests/test_write_path.py's FakeApi)."""
    library_id = LIB_UUID

    def __init__(self, books=(1, 2, 3)):
        self.books, self.fields, self.prefs, self.boom = list(books), {"tags": {1: ("Harem",)}}, {}, False
        self.field_metadata = type("M", (), {
            "all_metadata": lambda s: {"tags": {"is_multiple": True}},
            "all_field_keys": lambda s: {"tags"}})()

    def all_book_ids(self): return list(self.books)
    def all_field_for(self, field, ids): return {b: self.fields.get(field, {}).get(b) for b in ids}
    def set_field(self, field, vals):
        if self.boom: raise RuntimeError("calibre exploded mid-write")
        self.fields.setdefault(field, {}).update(vals)
    def set_pref(self, key, value): self.prefs[key] = value


def test_write_ops_logs_before_and_after_from_the_live_handle():
    """In-process the GUI's in-memory cache is the authoritative before-state; a second sqlite
    handle can be behind it by any number of unflushed edits."""
    api = _Api()
    with _pointed_at(_lib()) as home:
        _quiet(common.write_ops, api, [common.op_set_field("tags", {1: ["Harem", "Isekai"]})],
               tool="plugin", scope="1 book", engine="openai", model="gpt-5-mini")
        lines = _read(home)
    assert lines[0]["library"] == LIB_UUID and lines[0]["engine"] == "openai"
    assert lines[1]["before"] == ["Harem"] and lines[1]["after"] == ["Harem", "Isekai"]
    assert lines[-1]["outcome"] == "ok"


def test_a_failed_apply_closes_the_run_as_failed():
    api = _Api(); api.boom = True
    with _pointed_at(_lib()) as home:
        try:
            _quiet(common.write_ops, api, [common.op_set_field("tags", {1: ["x"]})])
        except RuntimeError:
            pass
        else:
            raise AssertionError("write_ops swallowed the writer's exception")
        lines = _read(home)
    assert lines[-1]["kind"] == "end" and lines[-1]["outcome"] == "failed"


def _stub_writer(rc=0):
    """Stub the calibre-debug subprocess AND the lookup that finds it — CI has no Calibre, and a
    stub that only replaces subprocess.run passes on a developer's machine and dies on the runner.
    The log is captured on THIS side of the subprocess, which is the whole reason the seam exists
    (#49's testing note)."""
    saved = (subprocess.run, shutil.which, common.calibre_open)
    subprocess.run = lambda *a, **k: type("P", (), {"returncode": rc})()
    shutil.which = lambda name, *a, **k: "/bin/true" if "calibre" in name else saved[1](name, *a, **k)
    common.calibre_open = lambda: False

    def restore():
        subprocess.run, shutil.which, common.calibre_open = saved
    return restore


def test_run_writer_logs_around_the_subprocess():
    lib = _lib()
    restore = _stub_writer()
    try:
        with _pointed_at(lib) as home:
            _quiet(common.run_writer, [common.op_set_field("tags", {1: ["Harem", "Isekai"]})],
                   tool="wrangle", scope="library")
            lines = _read(home)
    finally:
        restore()
    assert lines[0]["tool"] == "wrangle" and lines[0]["library"] == LIB_UUID
    assert lines[1]["book"] == 1 and lines[1]["before"] == ["Harem", "Mature"]
    assert lines[-1]["outcome"] == "ok"


def test_a_forced_run_still_captures_before_values():
    """--force skips the WIPE GUARD, not the log. A forced run is the most dangerous kind and the
    one most likely to need undo; logging it after-only would make undo silently useless there."""
    lib = _lib()
    restore = _stub_writer()
    try:
        with _pointed_at(lib) as home:
            _quiet(common.run_writer, [common.op_set_field("tags", {1: []})], force=True, tool="wrangle")
            lines = _read(home)
    finally:
        restore()
    assert lines[1]["before"] == ["Harem", "Mature"] and lines[1]["after"] == []


def test_a_failing_writer_closes_the_run_as_failed():
    lib = _lib()
    restore = _stub_writer(rc=3)
    try:
        with _pointed_at(lib) as home:
            try: _quiet(common.run_writer, [common.op_set_field("tags", {1: ["x"]})], tool="wrangle")
            except SystemExit: pass
            lines = _read(home)
    finally:
        restore()
    assert lines[-1]["outcome"] == "failed", "a failed write left a clean-looking footer"


# ---------------- the shared conflict predicate ----------------
def test_conflict_is_set_wise_for_multi_and_string_wise_for_single():
    """THE predicate — apply-time checks (atomicity contract) and undo replay share it, so
    "conflict" cannot come to mean two things. Order is not an edit (Calibre reorders tags);
    case IS (the user retyped it)."""
    assert not editlog.conflict(["b", "a"], ["a", "b"], multi=True)      # order-insensitive
    assert editlog.conflict(["a", "b"], ["a"], multi=True)               # a value was added
    assert editlog.conflict(["a"], ["A"], multi=True)                    # raw values: no case folding
    assert not editlog.conflict((), None, multi=True)                    # both empty
    assert not editlog.conflict("Hiatus", "Hiatus", multi=False)
    assert editlog.conflict("Hiatus", "Abandoned", multi=False)
    assert not editlog.conflict(None, "", multi=False)                   # absence is absence
    assert editlog.conflict(None, "Hiatus", multi=False)


def test_an_op_that_never_applied_reads_as_a_conflict():
    """Why logging BEFORE applying is safe: if the run died before this op landed, the book still
    holds `before`, and undo (which expects `after`) sees a conflict and skips it — reported,
    never clobbered."""
    with _pointed_at(_lib()) as home:
        editlog.start("wrangle", [common.op_set_field("tags", {1: ["Harem", "Isekai"]})],
                      {"tags": {1: ["Harem"]}})
        line = _read(home)[1]
    assert editlog.conflict(["Harem"], line["after"], multi=True), \
        "an unapplied op looked like a clean undo candidate"


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
