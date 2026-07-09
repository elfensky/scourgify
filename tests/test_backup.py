#!/usr/bin/env python3
"""Pins the write-path safety helpers touched by the calibre_open fail-closed fix and the
backup-hardening/rollback work. No framework:  uv run tests/test_backup.py  (also pytest-collectable).
No Calibre, no library, no network."""
import os, sys, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from scourgify import common


def test_is_calibre_gui_matches_the_gui():
    assert common._is_calibre_gui("501 /Applications/calibre.app/Contents/MacOS/calibre")
    assert common._is_calibre_gui("12345 calibre")


def test_is_calibre_gui_ignores_cli_tools_and_our_own_helpers():
    # None of these mean "the GUI is holding the library" — they must NOT block a write.
    for line in ("999 calibre-debug -e _writer.py -- /tmp/ops.json",
                 "888 calibredb list", "777 calibre-server --port 8080", "666 calibre-parallel",
                 "555 python3 /home/u/calibre-tools/scourgify/wrangle.py apply --apply",
                 "444 python3 classify.py --incremental"):
        assert not common._is_calibre_gui(line), line


def test_backup_path_never_collides_within_a_second():
    common.BACKUPS = tempfile.mkdtemp()
    seen = set()
    for _ in range(6):                       # all in the same wall-clock second
        p = common._backup_path()
        open(p, "w").close()                 # occupy it, as a real snapshot would
        assert p not in seen, "backup path collided — a snapshot would have been overwritten"
        seen.add(p)


def test_prune_keeps_only_the_newest():
    common.BACKUPS = tempfile.mkdtemp()
    common.BACKUP_KEEP = 3
    for name in ("ff_20260101T000001.db", "ff_20260101T000002.db",
                 "ff_20260101T000003.db", "ff_20260101T000004.db", "ff_20260101T000005.db"):
        open(os.path.join(common.BACKUPS, name), "w").close()
    common._prune_backups()
    left = sorted(os.path.basename(p) for p in
                  __import__("glob").glob(os.path.join(common.BACKUPS, "ff_*.db")))
    assert left == ["ff_20260101T000003.db", "ff_20260101T000004.db", "ff_20260101T000005.db"], left


if __name__ == "__main__":
    test_is_calibre_gui_matches_the_gui()
    test_is_calibre_gui_ignores_cli_tools_and_our_own_helpers()
    test_backup_path_never_collides_within_a_second()
    test_prune_keeps_only_the_newest()
    print("ok")
