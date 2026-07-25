#!/usr/bin/env python3
"""Tests for user-file path resolution — where config.toml / overrides/ / data/ live.
No framework needed:  uv run tests/test_paths.py   (also collectable by pytest).
No Calibre, no library, no network."""
import os, sys, tempfile, contextlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from scourgify import common
from scourgify.common import user_dir, backups_size


@contextlib.contextmanager
def env(**kv):
    """Set/clear env vars for the block, restoring the prior values after (value None = unset)."""
    saved = {k: os.environ.get(k) for k in kv}
    for k, v in kv.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    try:
        yield
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def test_user_dir_scourgify_home_wins():
    with env(SCOURGIFY_HOME="/tmp/sg-home", XDG_CONFIG_HOME="/tmp/xdg"):
        assert user_dir() == "/tmp/sg-home"          # explicit override beats XDG


def test_user_dir_xdg_config_home():
    with env(SCOURGIFY_HOME=None, XDG_CONFIG_HOME="/tmp/xdg"):
        assert user_dir() == os.path.join("/tmp/xdg", "scourgify")


def test_user_dir_default_is_dot_config():
    # both unset -> ~/.config/scourgify (whatever ~ expands to on this machine)
    with env(SCOURGIFY_HOME=None, XDG_CONFIG_HOME=None):
        assert user_dir() == os.path.join(os.path.expanduser("~/.config"), "scourgify")


def test_data_paths_follow_scourgify_home_after_import():
    """The whole data/ tree late-binds through user_dir() — $SCOURGIFY_HOME set AFTER import
    still redirects every artifact path (no import-time freeze, no monkeypatching module globals)."""
    from scourgify import artifacts
    with env(SCOURGIFY_HOME="/tmp/sg-late"):
        assert common.data_dir() == "/tmp/sg-late/data"
        assert common.backups_dir() == "/tmp/sg-late/data/backups"
        assert common.rejects_path() == "/tmp/sg-late/data/rejects.csv"
        assert artifacts.prop() == "/tmp/sg-late/data/classify_proposal.csv"
        for p in (artifacts.rank(), artifacts.fail(), artifacts.review(), artifacts.ledger()):
            assert p.startswith("/tmp/sg-late/data/")


def test_applied_proposal_archives_found_by_owner():
    """The 'which proposals were applied' glob lives WITH the archive convention (artifacts.py) —
    promote.backfill can't silently miss archives because a filename convention changed elsewhere."""
    from scourgify import artifacts, promote
    with tempfile.TemporaryDirectory() as td, env(SCOURGIFY_HOME=td):
        os.makedirs(os.path.join(td, "data"))
        artifacts.write_proposal([{"book_id": 1, "title": "A", "added_tags": ["X"], "proposed_new": []}])
        arch = artifacts.archive(artifacts.prop(), "applied")
        assert artifacts.applied_proposals() == [arch]
        artifacts.write_proposal([{"book_id": 2, "title": "B", "added_tags": [], "proposed_new": []}])
        assert promote._proposal_files() == [arch, artifacts.prop()]   # archives + the live proposal


def test_backups_size_counts_and_sums():
    with tempfile.TemporaryDirectory() as td, env(SCOURGIFY_HOME=td):
        d = os.path.join(td, "data", "backups"); os.makedirs(d)
        for name, blob in (("ff_1.db", b"a" * 10), ("ff_2.db", b"b" * 25), ("notes.txt", b"x" * 99)):
            open(os.path.join(d, name), "wb").write(blob)
        assert backups_size() == (2, 35)          # only the two *.db files, txt ignored


def test_backups_size_empty():
    with tempfile.TemporaryDirectory() as td, env(SCOURGIFY_HOME=td):
        assert backups_size() == (0, 0)


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
