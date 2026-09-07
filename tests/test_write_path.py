#!/usr/bin/env python3
"""The one write path: ops.apply_ops + the guards both writers share (phase 1 of the Calibre
plugin roadmap, NLSpec B2). No framework:  uv run tests/test_write_path.py  (also pytest-collectable).
No Calibre, no library, no network.

What breaks in the real world if these fail: the CLI writer (`calibre-debug -e _writer.py`,
ops arriving as JSON) and an in-process plugin writer (live dicts against gui.current_db.new_api)
stop agreeing about what a change-set means — and the plugin writes to a live library with a
guard or a backup that the CLI has and it doesn't."""
import os, io, sys, json, glob, sqlite3, tempfile, contextlib, threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scourgify import common, ops as ops_mod
import fixture_db


# ---------------- the smallest thing that stands in for Calibre's Cache ----------------
class FakeMeta:
    def __init__(self, fields, multi): self.fields, self.multi = set(fields), set(multi)
    def all_metadata(self): return {f: {"is_multiple": f in self.multi} for f in self.fields}
    def all_field_keys(self): return set(self.fields)


class FakeApi:
    """Only the five methods apply_ops actually calls, plus a diffable state dict. Deliberately
    NOT a Calibre emulator — the parity claim here is about the ops loop, not about Calibre's
    own set_field semantics (those are identical by definition: it is the same object at runtime)."""
    def __init__(self, books=(1, 2, 3), fields=("tags", "#status", "#wrangled"), multi=("tags",)):
        self.books = list(books); self.fields = {}; self.prefs = {}
        self.field_metadata = FakeMeta(fields, multi)
    def all_book_ids(self): return list(self.books)
    def all_field_for(self, field, ids): return {b: self.fields.get(field, {}).get(b) for b in ids}
    def set_field(self, field, vals): self.fields.setdefault(field, {}).update(vals)
    def set_pref(self, key, value): self.prefs[key] = value
    def state(self): return {"fields": self.fields, "prefs": self.prefs}


class _BlockingApi(FakeApi):
    """A FakeApi whose set_field() signals `ready` then parks on `release` — how the lock tests
    hold the write-run lock across a REAL second thread (parked inside apply_ops), matching a
    genuine concurrent write instead of re-entering _write_run directly from the same thread."""
    def __init__(self, *a, ready=None, release=None, **k):
        super().__init__(*a, **k)
        self._ready, self._release = ready, release

    def set_field(self, field, vals):
        if self._ready is not None:
            self._ready.set()
        if self._release is not None:
            self._release.wait(5)
        super().set_field(field, vals)


def _held_lock(lib, tool="wrangle"):
    """Start a write_ops run on a background thread and block it mid-apply, holding the
    write-run lock until the caller sets `api._release`. -> (thread, blocking_api)."""
    ready, release = threading.Event(), threading.Event()
    api = _BlockingApi(books=(1, 2, 3), ready=ready, release=release)
    t = threading.Thread(target=lambda: _quiet(common.write_ops, api,
                                               [common.op_set_field("tags", {1: ["x"]})], tool=tool))
    t.start()
    assert ready.wait(5), "writer thread never reached apply_ops — lock not held"
    return t, api


class FakeLegacy:
    """The CLI writer's extra handle: only it can create a column, and only a reopen makes the
    column usable."""
    def __init__(self, api): self.new_api = api; self.created = []
    def create_custom_column(self, label, name, datatype, is_multiple):
        self.created.append(label); self.new_api.field_metadata.fields.add("#" + label)


def _quiet(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf): fn(*a, **k)
    return buf.getvalue()


# ---------------- coercion ----------------
def test_coerce_lands_every_book_id_on_int():
    """Ops JSON stringifies book ids (JSON object keys); an in-process caller hands over ints.
    If coerce didn't unify them, the two writers would address DIFFERENT books from the same
    change-set — and only the JSON one would be right."""
    api = FakeApi()
    assert ops_mod.coerce(api, "tags", {"6585": ["a"], 7453: ["b"]}) == {6585: ("a",), 7453: ("b",)}


def test_coerce_shapes_the_value_to_the_column():
    api = FakeApi()
    assert ops_mod.coerce(api, "tags", {1: "solo"}) == {1: ("solo",)}          # multi: scalar -> 1-tuple
    assert ops_mod.coerce(api, "#status", {1: ["Hiatus"]}) == {1: "Hiatus"}    # single: list -> first
    assert ops_mod.coerce(api, "#status", {1: []}) == {1: None}               # single: empty -> cleared
    assert ops_mod.coerce(api, "#status", {1: ""}) == {1: None}


# ---------------- shadow replay (NLSpec B2 parity acceptance) ----------------
def test_shadow_replay_cli_and_in_process_agree():
    """The parity proof, at the granularity CI can carry: one ops list, applied the way the CLI
    applies it (serialized to JSON and back, exactly what run_writer's tempfile does, with the
    legacy handle attached) and the way a plugin applies it (live dicts, no legacy). Final state
    is diffed whole.

    Divergence whitelist per the spec: stamp timestamps (pinned here via `now=`), backup
    filenames and edit-log run ids (not part of library state). Everything else byte-equal.

    Full-fidelity replay against a real cloned Calibre library needs Calibre's own Cache and so
    lives in the `calibre-debug -e` drill (phase 2) — this pins the half that can silently rot."""
    live = [common.op_set_field("tags", {6585: ["Harem", "Adventure"], 7453: "Isekai"}),
            common.op_set_field("#status", {6585: ["In-Progress"], 7453: ""}),
            common.op_stamp_now("#wrangled", [6585, 7453]),
            common.op_set_pref("namespaced:scourgify", {"v": 1})]
    wire = json.loads(json.dumps(live))                       # the CLI's tempfile round-trip

    cli = FakeApi(books=(6585, 7453))
    _quiet(ops_mod.apply_ops, cli, wire, legacy=FakeLegacy(cli), now=lambda: "TS")
    plugin = FakeApi(books=(6585, 7453))
    _quiet(ops_mod.apply_ops, plugin, live, now=lambda: "TS")

    assert cli.state() == plugin.state(), f"writers diverged:\n  cli={cli.state()}\n  plugin={plugin.state()}"
    assert cli.state()["fields"]["tags"] == {6585: ("Harem", "Adventure"), 7453: ("Isekai",)}
    assert cli.state()["fields"]["#wrangled"] == {6585: "TS", 7453: "TS"}


def test_stamp_now_with_no_book_list_stamps_the_whole_library():
    api = FakeApi(books=(1, 2, 3))
    _quiet(ops_mod.apply_ops, api, [common.op_stamp_now("#wrangled")], now=lambda: "TS")
    assert api.fields["#wrangled"] == {1: "TS", 2: "TS", 3: "TS"}


# ---------------- create_column is CLI-only (NLSpec B2.6) ----------------
def test_create_column_is_refused_in_process():
    """A plugin must not create columns: the legacy-DB reopen that makes a new column usable
    would desync the live GUI's models. Refusing loudly beats a half-created column."""
    api = FakeApi()
    try:
        ops_mod.apply_ops(api, [common.op_create_column("newcol", "New", "text")])
    except ValueError as e:
        assert "not available in-process" in str(e), e
    else:
        raise AssertionError("create_column was accepted without a legacy handle")


def test_create_column_works_for_the_cli_writer():
    api = FakeApi(); legacy = FakeLegacy(api)
    _quiet(ops_mod.apply_ops, api, [common.op_create_column("wrangled2", "W2", "datetime")],
           legacy=legacy, reopen=lambda: legacy)
    assert legacy.created == ["wrangled2"]
    _quiet(ops_mod.apply_ops, api, [common.op_create_column("wrangled2", "W2", "datetime")],
           legacy=legacy, reopen=lambda: legacy)
    assert legacy.created == ["wrangled2"], "existing column was created twice"


def test_unknown_op_raises_rather_than_being_skipped():
    try:
        ops_mod.apply_ops(FakeApi(), [{"op": "delete_everything"}])
    except ValueError as e:
        assert "delete_everything" in str(e)
    else:
        raise AssertionError("an unrecognised op was silently ignored")


# ---------------- the guards (NLSpec B2.3/B2.4) ----------------
def test_guardrail_error_is_catchable_as_an_ordinary_exception():
    """The whole point of the SystemExit -> GuardrailError change: ThreadedJob catches only
    `Exception`, so a guard raising SystemExit inside a Calibre job kills the worker thread
    SILENTLY — a guard that trips invisibly is worse than no guard."""
    assert issubclass(common.GuardrailError, Exception)
    assert not issubclass(common.GuardrailError, SystemExit)
    try:
        raise common.GuardrailError("x")
    except Exception:
        pass
    else:
        raise AssertionError("GuardrailError escaped `except Exception`")


def test_check_wipe_gives_both_readers_the_same_verdict():
    """The guard's before-read differs by caller (read-only sqlite vs the live new_api); the
    VERDICT must not. A change-set the CLI refuses and the plugin accepts is the bug this
    whole phase exists to make impossible."""
    books = list(range(1, 201))
    wipe = [common.op_set_field("tags", {b: [] for b in books})]
    api = FakeApi(books=books)
    api.set_field("tags", {b: ("x",) for b in books})
    db_side = lambda f: set(books)                      # what _populated_books would return
    for name, populated in (("sqlite", db_side), ("new_api", lambda f: common.populated_via_api(api, f))):
        try:
            common.check_wipe(wipe, populated)
        except common.GuardrailError as e:
            assert "would empty 'tags'" in str(e)
        else:
            raise AssertionError(f"{name} reader let a 100% wipe of a 200-book column through")


def test_check_wipe_allows_an_ordinary_change_set():
    books = list(range(1, 201))
    ok = [common.op_set_field("tags", {b: ["kept"] for b in books})]
    common.check_wipe(ok, lambda f: set(books))         # must not raise


def test_populated_via_api_ignores_empty_values():
    api = FakeApi(books=(1, 2, 3))
    api.set_field("tags", {1: ("x",), 2: (), 3: None})
    assert common.populated_via_api(api, "tags") == {1}


def test_field_is_multiple_via_api_matches_the_sqlite_answer():
    """One meaning of "multi", two readers (tags, a multi custom column, a single-value custom
    column) — the in-process answer must never disagree with the sqlite one, or the apply-time
    conflict check plan 01-06 adds could compare a book's tags as a set on one path and as a
    string on the other."""
    d = tempfile.mkdtemp()
    con = fixture_db.build(os.path.join(d, "metadata.db"), [{"id": 1, "tags": ["a"]}],
                           custom=[("fandoms", {1: ["Naruto", "Bleach"]}), ("status", {1: "Hiatus"})],
                           multi_labels=("fandoms",))
    con.commit()
    api = FakeApi(fields=("tags", "#fandoms", "#status"), multi=("tags", "#fandoms"))
    try:
        for field in ("tags", "#fandoms", "#status"):
            assert common.field_is_multiple_via_api(api, field) == common.column_is_multiple(con, field), field
    finally:
        con.close()


# ---------------- the snapshot (NLSpec B2.2) ----------------
def _lib(n=5):
    """A throwaway library dir with a metadata.db — never the user's $CALIBRE_LIBRARY."""
    d = tempfile.mkdtemp()
    con = fixture_db.build(os.path.join(d, "metadata.db"), [{"id": i} for i in range(1, n + 1)])
    con.close()
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


def test_backup_captures_data_still_sitting_in_the_wal():
    """Why the Online Backup API and not shutil.copy2: a committed transaction can live in
    `metadata.db-wal` until a checkpoint, and a byte copy of metadata.db ALONE silently loses
    it — a rollback point missing the very edits it was taken to protect. In-process, with a
    live GUI writing, that is the normal case rather than the exotic one."""
    lib = _lib()
    hot = sqlite3.connect(os.path.join(lib, "metadata.db"))
    hot.execute("PRAGMA journal_mode=WAL")
    hot.execute("INSERT INTO books VALUES(99,'wal-only','', NULL, NULL)")
    hot.commit()                                        # committed, but still only in the -wal
    try:
        with _pointed_at(lib):
            snap = common.backup_db()
        con = sqlite3.connect(f"file:{snap}?mode=ro", uri=True)
        got = con.execute("SELECT count(*) FROM books WHERE id=99").fetchone()[0]
        con.close()
        assert got == 1, "the snapshot lost a committed row that was still in the WAL"
    finally:
        hot.close()


def test_backup_failure_is_fail_closed():
    """No rollback point -> no write. A backup that quietly fails is how a bad run becomes
    permanent."""
    lib = _lib()
    with _pointed_at(lib):
        try:
            common.backup_db(dst=os.path.join(lib, "no", "such", "dir", "snap.db"))
        except common.GuardrailError as e:
            assert "backup failed" in str(e)
        else:
            raise AssertionError("an impossible backup path did not stop the write")


def test_backup_verifies_the_snapshot_is_a_readable_library():
    lib = _lib(7)
    with _pointed_at(lib):
        snap = common.backup_db()
        assert os.path.basename(snap).startswith("ff_")
        con = sqlite3.connect(f"file:{snap}?mode=ro", uri=True)
        assert con.execute("SELECT count(*) FROM books").fetchone()[0] == 7
        con.close()


# ---------------- the in-process write path (NLSpec B2) ----------------
def test_write_ops_guards_snapshots_then_applies():
    """The plugin's whole write path in one call: guard (through new_api), snapshot, apply —
    and never run_writer, which would shell out to a second process against a library the GUI
    holds open (#53)."""
    lib = _lib()
    api = FakeApi(books=(1, 2, 3))
    with _pointed_at(lib) as home:
        out = _quiet(common.write_ops, api, [common.op_set_field("tags", {1: ["Isekai"]})])
        assert glob.glob(os.path.join(common.backups_dir(), "ff_*.db")), "no snapshot before the write"
    assert api.fields["tags"] == {1: ("Isekai",)}
    assert "backup:" in out


def test_write_ops_refuses_a_wipe_and_writes_nothing():
    lib = _lib()
    books = list(range(1, 201))
    api = FakeApi(books=books)
    api.set_field("tags", {b: ("x",) for b in books})
    with _pointed_at(lib) as home:
        try:
            common.write_ops(api, [common.op_set_field("tags", {b: [] for b in books})])
        except common.GuardrailError:
            pass
        else:
            raise AssertionError("write_ops applied a catastrophic wipe")
        assert not glob.glob(os.path.join(common.backups_dir(), "ff_*.db")), \
            "guard ran AFTER the snapshot — a refused change-set should cost nothing"
    assert api.fields["tags"][1] == ("x",), "the wipe was applied despite the guard"


def test_write_ops_does_nothing_for_an_empty_change_set():
    lib = _lib()
    api = FakeApi()
    with _pointed_at(lib) as home:
        out = _quiet(common.write_ops, api, [common.op_set_field("tags", {})])
        assert not glob.glob(os.path.join(common.backups_dir(), "ff_*.db")), \
            "an empty change-set still burned a snapshot"
    assert "nothing to write" in out
    assert api.fields == {}


# ---------------- the write-run lock (NLSpec B2, FOUND-04) ----------------
def test_a_second_write_run_against_one_library_is_refused_by_name():
    lib = _lib()
    with _pointed_at(lib):
        t, held_api = _held_lock(lib, tool="wrangle")
        try:
            try:
                common.write_ops(FakeApi(books=(1, 2, 3)), [common.op_set_field("tags", {2: ["y"]})],
                                 tool="classify")
            except common.GuardrailError as e:
                assert "wrangle" in str(e), str(e)
            else:
                raise AssertionError("a concurrent write run against the same library was not refused")
        finally:
            held_api._release.set(); t.join(5)


def test_a_refused_second_run_takes_no_snapshot_and_logs_nothing():
    from scourgify import editlog
    lib = _lib()
    with _pointed_at(lib):
        t, held_api = _held_lock(lib)
        try:
            try:
                common.write_ops(FakeApi(books=(1, 2, 3)), [common.op_set_field("tags", {2: ["y"]})],
                                 tool="classify")
            except common.GuardrailError:
                pass
            snaps = glob.glob(os.path.join(common.backups_dir(), "ff_*.db"))
            log_path = editlog.log_path()
            headers = [json.loads(l) for l in open(log_path, encoding="utf-8")
                      if json.loads(l).get("kind") == "run"] if os.path.exists(log_path) else []
        finally:
            held_api._release.set(); t.join(5)
    assert len(snaps) == 1, f"expected only the held run's snapshot, found {len(snaps)}"
    assert len(headers) == 1, "the refused run wrote a second run header"


def test_two_library_uuids_take_two_independent_locks():
    common._WRITE_LOCKS.clear(); common._WRITE_HOLDERS.clear()
    lock_a = common._acquire_write_lock("uuid-a", "wrangle", None)
    try:
        lock_b = common._acquire_write_lock("uuid-b", "classify", None)
        try:
            assert lock_a is not lock_b
            assert len(common._WRITE_LOCKS) == 2
        finally:
            common._release_write_lock("uuid-b", lock_b)
    finally:
        common._release_write_lock("uuid-a", lock_a)


def test_the_lock_is_held_across_the_whole_log_run():
    from scourgify import editlog
    lib = _lib()
    with _pointed_at(lib):
        t, held_api = _held_lock(lib, tool="wrangle")
        try:
            try:
                common.write_ops(FakeApi(books=(1, 2, 3)), [common.op_set_field("tags", {2: ["y"]})],
                                 tool="classify")
                refused = False
            except common.GuardrailError:
                refused = True
        finally:
            held_api._release.set(); t.join(5)
        lines = [json.loads(l) for l in open(editlog.log_path(), encoding="utf-8")]
    assert refused, "a second run attempted mid-first-run's log was not refused"
    kinds = [l["kind"] for l in lines]
    assert kinds.count("run") == 1 and kinds.count("end") == 1
    assert kinds[0] == "run" and kinds[-1] == "end", "a foreign line appeared between header and footer"


def test_the_lock_is_released_after_a_failed_run():
    from scourgify import editlog
    lib = _lib()
    api = FakeApi(books=(1, 2, 3))
    api.set_field = lambda field, vals: (_ for _ in ()).throw(RuntimeError("calibre exploded"))
    with _pointed_at(lib):
        try:
            common.write_ops(api, [common.op_set_field("tags", {1: ["x"]})], tool="wrangle")
        except RuntimeError:
            pass
        else:
            raise AssertionError("write_ops swallowed the writer's exception")
        lines = [json.loads(l) for l in open(editlog.log_path(), encoding="utf-8")]
        assert lines[-1]["kind"] == "end" and lines[-1]["outcome"] == "failed"
        ok_api = FakeApi(books=(1, 2, 3))
        _quiet(common.write_ops, ok_api, [common.op_set_field("tags", {1: ["y"]})], tool="wrangle")
        assert ok_api.fields["tags"] == {1: ("y",)}, "a subsequent run against the same library did not succeed"


def test_taking_one_librarys_lock_twice_raises_rather_than_deadlocking():
    lib = _lib()
    with _pointed_at(lib):
        t, held_api = _held_lock(lib)
        result = {}
        def attempt():
            try:
                common.write_ops(FakeApi(books=(1, 2, 3)), [common.op_set_field("tags", {2: ["y"]})],
                                 tool="classify")
            except Exception as e:
                result["error"] = e
        t2 = threading.Thread(target=attempt)
        t2.start(); t2.join(5)
        try:
            assert not t2.is_alive(), "a second acquire against the same library hung — must be non-blocking"
            assert isinstance(result.get("error"), common.GuardrailError), result.get("error")
        finally:
            held_api._release.set(); t.join(5)


def test_write_locks_is_bounded_by_library_count():
    common._WRITE_LOCKS.clear(); common._WRITE_HOLDERS.clear()
    lib_a, lib_b = _lib(), _lib()
    for lib, n in ((lib_a, 3), (lib_b, 2)):
        with _pointed_at(lib):
            for i in range(n):
                api = FakeApi(books=(1, 2, 3))
                _quiet(common.write_ops, api, [common.op_set_field("tags", {1: [f"t{i}"]})], tool="wrangle")
    assert len(common._WRITE_LOCKS) <= 2, f"expected at most 2 library locks, got {len(common._WRITE_LOCKS)}"
    assert common._WRITE_HOLDERS == {}, "a per-run holder record leaked past its run"


def test_a_write_run_still_prunes_past_the_backup_keep_budget():
    lib = _lib()
    old_keep = common.BACKUP_KEEP
    common.BACKUP_KEEP = 2
    try:
        with _pointed_at(lib):
            for i in range(4):
                api = FakeApi(books=(1, 2, 3))
                _quiet(common.write_ops, api, [common.op_set_field("tags", {1: [f"t{i}"]})], tool="wrangle")
            snaps = glob.glob(os.path.join(common.backups_dir(), "ff_*.db"))
    finally:
        common.BACKUP_KEEP = old_keep
    assert len(snaps) <= 2, f"expected pruning to BACKUP_KEEP=2, found {len(snaps)}"


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
