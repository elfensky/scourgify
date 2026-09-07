#!/usr/bin/env python3
"""Tests for user-file path resolution — where config.toml / overrides/ / data/ live, and
which library's data/<uuid>/ tree a given process resolution lands in.
No framework needed:  uv run tests/test_paths.py   (also collectable by pytest).
No Calibre, no library, no network."""
import os, sys, tempfile, contextlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scourgify import common
from scourgify.common import user_dir, backups_size
import fixture_db


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


def _fixture_lib(td: str, name: str, uuid=None, books=None) -> str:
    """A throwaway library dir (td/name) with a metadata.db carrying `uuid` (or no library_id
    table at all when uuid=None)."""
    d = os.path.join(td, name)
    os.makedirs(d, exist_ok=True)
    con = fixture_db.build(os.path.join(d, "metadata.db"), books or [{"id": 1}], uuid=uuid)
    con.close()
    return d


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
    still redirects every artifact path (no import-time freeze, no monkeypatching module
    globals) — now one level deeper, under the resolving library's uuid."""
    from scourgify import artifacts
    with tempfile.TemporaryDirectory() as td, env(SCOURGIFY_HOME=os.path.join(td, "home")):
        lib = _fixture_lib(td, "lib", uuid="uuid-a")
        common.clear_uuid_cache()
        with env(CALIBRE_LIBRARY=lib):
            dd = common.data_dir()
            assert dd == os.path.join(td, "home", "data", "uuid-a")
            assert common.backups_dir() == os.path.join(dd, "backups")
            assert common.rejects_path() == os.path.join(dd, "rejects.csv")
            assert artifacts.prop() == os.path.join(dd, "classify_proposal.csv")
            for p in (artifacts.rank(), artifacts.fail(), artifacts.review(), artifacts.ledger()):
                assert p.startswith(dd + os.sep)


def test_applied_proposal_archives_found_by_owner():
    """The 'which proposals were applied' glob lives WITH the archive convention (artifacts.py) —
    promote.backfill can't silently miss archives because a filename convention changed elsewhere."""
    from scourgify import artifacts, promote
    with tempfile.TemporaryDirectory() as td, env(SCOURGIFY_HOME=os.path.join(td, "home")):
        lib = _fixture_lib(td, "lib", uuid="uuid-b")
        common.clear_uuid_cache()
        with env(CALIBRE_LIBRARY=lib):
            os.makedirs(common.data_dir())
            artifacts.write_proposal([{"book_id": 1, "title": "A", "added_tags": ["X"], "proposed_new": []}])
            arch = artifacts.archive(artifacts.prop(), "applied")
            assert artifacts.applied_proposals() == [arch]
            artifacts.write_proposal([{"book_id": 2, "title": "B", "added_tags": [], "proposed_new": []}])
            assert promote._proposal_files() == [arch, artifacts.prop()]   # archives + the live proposal


def test_backups_size_counts_and_sums():
    with tempfile.TemporaryDirectory() as td, env(SCOURGIFY_HOME=os.path.join(td, "home")):
        lib = _fixture_lib(td, "lib", uuid="uuid-c")
        common.clear_uuid_cache()
        with env(CALIBRE_LIBRARY=lib):
            d = os.path.join(common.data_dir(), "backups"); os.makedirs(d)
            for name, blob in (("ff_1.db", b"a" * 10), ("ff_2.db", b"b" * 25), ("notes.txt", b"x" * 99)):
                open(os.path.join(d, name), "wb").write(blob)
            assert backups_size() == (2, 35)          # only the two *.db files, txt ignored


def test_backups_size_empty():
    with tempfile.TemporaryDirectory() as td, env(SCOURGIFY_HOME=os.path.join(td, "home")):
        lib = _fixture_lib(td, "lib", uuid="uuid-d")
        common.clear_uuid_cache()
        with env(CALIBRE_LIBRARY=lib):
            assert backups_size() == (0, 0)


# ---------------- uuid-keyed data_dir() (FOUND-03) ----------------

def test_every_artifact_path_is_library_scoped():
    """THE contract test: every artifact path function in the codebase, for two libraries opened
    in sequence in one process, resolves under that library's data/<uuid>/ — and the two
    libraries' path sets are disjoint. Reproduces the plugin phase-4/5 bleed (a throwaway library
    reporting a real library's applied-proposal archive) as a red-if-reintroduced test."""
    from scourgify import artifacts, editlog

    def paths_for(lib):
        with env(CALIBRE_LIBRARY=lib):
            return {
                "prop": artifacts.prop(), "rank": artifacts.rank(), "fail": artifacts.fail(),
                "syn_fail": artifacts.syn_fail(), "review": artifacts.review(), "ledger": artifacts.ledger(),
                "backups_dir": common.backups_dir(), "rejects_path": common.rejects_path(),
                "log_path": editlog.log_path(),
            }, common.data_dir()

    with tempfile.TemporaryDirectory() as td, env(SCOURGIFY_HOME=os.path.join(td, "home")):
        lib_a = _fixture_lib(td, "a", uuid="uuid-alpha")
        lib_b = _fixture_lib(td, "b", uuid="uuid-beta")

        common.clear_uuid_cache()
        paths_a, dd_a = paths_for(lib_a)
        common.clear_uuid_cache()
        paths_b, dd_b = paths_for(lib_b)

        assert dd_a != dd_b
        for name, p in paths_a.items():
            assert p.startswith(dd_a + os.sep), f"{name} ({p!r}) not under library A's data_dir"
        for name, p in paths_b.items():
            assert p.startswith(dd_b + os.sep), f"{name} ({p!r}) not under library B's data_dir"
        assert set(paths_a.values()).isdisjoint(paths_b.values()), \
            "the two libraries' artifact paths must never overlap"


def test_same_uuid_two_paths_share_one_tree():
    """Adjacency: two library FOLDERS whose dbs carry the SAME uuid resolve to the same data_dir()
    — identity is the uuid, never the filesystem path."""
    with tempfile.TemporaryDirectory() as td, env(SCOURGIFY_HOME=os.path.join(td, "home")):
        lib_1 = _fixture_lib(td, "one", uuid="uuid-shared")
        lib_2 = _fixture_lib(td, "two", uuid="uuid-shared")

        common.clear_uuid_cache()
        with env(CALIBRE_LIBRARY=lib_1):
            dd_1 = common.data_dir()
        common.clear_uuid_cache()
        with env(CALIBRE_LIBRARY=lib_2):
            dd_2 = common.data_dir()
        assert dd_1 == dd_2


def test_library_without_library_id_table_gets_a_nouuid_key():
    """A db built with uuid=None (no library_id table) resolves to a data/nouuid-... segment,
    rather than refusing — the fallback exists for hand-built fixtures."""
    with tempfile.TemporaryDirectory() as td, env(SCOURGIFY_HOME=os.path.join(td, "home")):
        lib = _fixture_lib(td, "nouuid-lib", uuid=None)
        common.clear_uuid_cache()
        with env(CALIBRE_LIBRARY=lib):
            dd = common.data_dir()
        assert os.path.basename(dd).startswith("nouuid-")


def test_data_dir_without_a_library_raises_guardrail():
    """With no resolvable library, data_dir() raises common.GuardrailError — never SystemExit,
    never a library-less path."""
    common.set_library(None)
    with env(SCOURGIFY_HOME="/tmp/sg-no-lib", CALIBRE_LIBRARY=None):
        try:
            common.data_dir()
        except common.GuardrailError:
            pass
        else:
            raise AssertionError("data_dir() with no resolvable library did not raise GuardrailError")


def test_open_order_does_not_change_either_libraries_paths():
    """Opening library B then library A in one process yields the same data_dir() for each as
    opening A then B — the per-process uuid memo is keyed by library path, so open order changes
    nothing."""
    with tempfile.TemporaryDirectory() as td, env(SCOURGIFY_HOME=os.path.join(td, "home")):
        lib_a = _fixture_lib(td, "order-a", uuid="uuid-order-a")
        lib_b = _fixture_lib(td, "order-b", uuid="uuid-order-b")

        common.clear_uuid_cache()
        with env(CALIBRE_LIBRARY=lib_a):
            first_pass_a = common.data_dir()
        with env(CALIBRE_LIBRARY=lib_b):
            first_pass_b = common.data_dir()

        common.clear_uuid_cache()
        with env(CALIBRE_LIBRARY=lib_b):
            second_pass_b = common.data_dir()
        with env(CALIBRE_LIBRARY=lib_a):
            second_pass_a = common.data_dir()

        assert first_pass_a == second_pass_a
        assert first_pass_b == second_pass_b


def test_set_library_does_not_clear_the_uuid_memo_but_clear_uuid_cache_does():
    """set_library() deliberately does NOT clear _UUID_CACHE (the memo is keyed by library PATH
    and a library's uuid does not change over its life); clear_uuid_cache() is the ONE supported
    reset. Pinned in both directions."""
    with tempfile.TemporaryDirectory() as td, env(SCOURGIFY_HOME=os.path.join(td, "home")):
        lib = _fixture_lib(td, "memo-lib", uuid="uuid-memo")
        common.clear_uuid_cache()
        try:
            with env(CALIBRE_LIBRARY=lib):
                dd = common.data_dir()
                assert lib in common._UUID_CACHE

                common.set_library(lib)          # same path — must NOT clear the memo
                assert lib in common._UUID_CACHE
                assert common.data_dir() == dd

                common.clear_uuid_cache()        # the ONE supported reset
                assert lib not in common._UUID_CACHE
        finally:
            common.set_library(None)


# ---------------- Windows branch of user_dir() (FOUND-02, XPLAT-01, D-05) ----------------

def _as_windows():
    """Context manager stand-in: monkeypatch os.name to 'nt' for the block, restoring after.
    Does not (and cannot) change the real path-separator semantics of os.path on this host —
    only user_dir()'s own `os.name == "nt"` branch check."""
    return _OsNameOverride("nt")


class _OsNameOverride:
    def __init__(self, name):
        self.name = name
    def __enter__(self):
        self.old = os.name
        os.name = self.name
        return self
    def __exit__(self, *a):
        os.name = self.old


def test_windows_scourgify_home_wins_over_appdata():
    with _as_windows(), env(SCOURGIFY_HOME="/tmp/sg-home", APPDATA=r"C:\Users\x\AppData\Roaming"):
        assert user_dir() == "/tmp/sg-home"


def test_windows_appdata_wins_over_xdg():
    with _as_windows(), env(SCOURGIFY_HOME=None, APPDATA=r"C:\Users\x\AppData\Roaming", XDG_CONFIG_HOME="/tmp/xdg"):
        assert user_dir() == os.path.join(r"C:\Users\x\AppData\Roaming", "scourgify")


def test_windows_appdata_unset_falls_through_to_xdg():
    """An nt host without APPDATA set (APPDATA read via os.environ.get, never subscripted) falls
    through to the XDG branch and returns a non-empty path, rather than raising."""
    with _as_windows(), env(SCOURGIFY_HOME=None, APPDATA=None, XDG_CONFIG_HOME="/tmp/xdg"):
        got = user_dir()
        assert got
        assert got == os.path.join("/tmp/xdg", "scourgify")


def test_windows_appdata_and_scourgify_home_both_unset_falls_through_to_home_config():
    """No SCOURGIFY_HOME, no APPDATA, no XDG_CONFIG_HOME on an nt host: falls through all the way
    to ~/.config/scourgify rather than raising."""
    with _as_windows(), env(SCOURGIFY_HOME=None, APPDATA=None, XDG_CONFIG_HOME=None):
        got = user_dir()
        assert got == os.path.join(os.path.expanduser("~/.config"), "scourgify")


def test_windows_path_uses_os_path_join_not_a_hardcoded_separator():
    """user_dir() builds the Windows branch with os.path.join, not string concatenation — a
    hardcoded '/' or '\\' would silently work on the developer's own OS and only break on the
    other, which a same-host CI run cannot otherwise catch."""
    with _as_windows(), env(SCOURGIFY_HOME=None, APPDATA=r"C:\fakeappdata", XDG_CONFIG_HOME=None):
        expected = os.path.join(r"C:\fakeappdata", "scourgify")
        assert user_dir() == expected
        # every join this host performs uses os.sep — there is no OTHER separator appended by
        # our own code (any backslash present is inside the injected APPDATA value itself).
        suffix = user_dir()[len(r"C:\fakeappdata"):]
        assert suffix == os.sep + "scourgify"


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
