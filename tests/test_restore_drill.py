#!/usr/bin/env python3
"""The restore drill (NLSpec B2 acceptance): a snapshot taken by the write path can actually be
restored, and the restore is itself reversible. Runs against a throwaway library — never
$CALIBRE_LIBRARY.  uv run tests/test_restore_drill.py

Why this exists as a drill and not a claim: every guard in this codebase is justified by "the
snapshot is the net". A net nobody has ever pulled on is a story. This pulls on it, and prints
the wall-clock so the cost of the real thing at 7,949 books is a measured number rather than a
guess. It must be green before any GUI write ships (phase 6) and stays a pre-release check."""
import os, sys, time, glob, shutil, sqlite3, hashlib, tempfile, contextlib, io

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scourgify import common
import fixture_db


def _sha(path):
    with open(path, "rb") as f: return hashlib.sha256(f.read()).hexdigest()


def _books(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try: return con.execute("SELECT count(*) FROM books").fetchone()[0]
    finally: con.close()


def test_restore_drill():
    lib, home = tempfile.mkdtemp(), tempfile.mkdtemp()
    db = os.path.join(lib, "metadata.db")
    fixture_db.build(db, [{"id": i, "title": f"book {i}", "tags": ["a", "b"]} for i in range(1, 201)]).close()

    old = {k: os.environ.get(k) for k in ("CALIBRE_LIBRARY", "SCOURGIFY_HOME")}
    os.environ["CALIBRE_LIBRARY"], os.environ["SCOURGIFY_HOME"] = lib, home
    # The drill runs against a fixture library, so the "is the GUI holding it?" guard is not the
    # thing under test here — and on a dev machine Calibre may well be open. Neutralised, loudly.
    real_open, common.calibre_open = common.calibre_open, lambda: False
    try:
        t0 = time.perf_counter()
        snap = common.backup_db()
        t_backup = time.perf_counter() - t0
        assert _books(snap) == 200

        # a run goes wrong: half the library loses its rows
        con = sqlite3.connect(db); con.execute("DELETE FROM books WHERE id > 100"); con.commit(); con.close()
        assert _books(db) == 100

        t0 = time.perf_counter()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            common.rollback_cmd(["--yes", os.path.basename(snap)])
        t_restore = time.perf_counter() - t0

        assert _books(db) == 200, "the restore did not bring the library back"
        assert _sha(db) == _sha(snap), "the restored db does not match the snapshot byte for byte"

        # ... and the restore is itself reversible: the pre-restore state was snapshotted first
        pre = [p for p in glob.glob(os.path.join(common.backups_dir(), "ff_*.db")) if p != snap]
        assert pre, "rollback left no snapshot of the state it overwrote — a restore you can't undo"
        assert any(_books(p) == 100 for p in pre), "the pre-restore snapshot doesn't hold the state it replaced"

        size = os.path.getsize(snap) / 1024
        print(f"  drill: 200 books, {size:.0f} KiB — backup {t_backup * 1000:.0f} ms, "
              f"restore {t_restore * 1000:.0f} ms")
    finally:
        common.calibre_open = real_open
        for k, v in old.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
        shutil.rmtree(lib, ignore_errors=True); shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    test_restore_drill(); print("ok")
