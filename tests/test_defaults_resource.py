#!/usr/bin/env python3
"""Pins common.defaults_dir() — the ONE resolver for every shipped read-only file the core opens
at runtime (defaults/ including defaults/ao3/, classify_vocab*.txt, afm.swift), correct both from
a normal install and from inside the Calibre plugin zip. No framework:
uv run tests/test_defaults_resource.py   (also pytest-collectable). No Calibre, no library.

Most tests monkeypatch common._archive_path() to point at a hand-built fixture zip — cheap and
precise for exercising the extraction/cache/guard logic, but it does NOT prove real zipimport
behaviour (a monkeypatched resolver can't catch an import-system assumption that's wrong).
`test_a_real_zipimport_resolves_the_archive_and_the_cache` is the one test in this file that
proves the real thing, via a child process with a generated zip first on sys.path."""
import contextlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from scourgify import common


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


@contextlib.contextmanager
def _patched_archive(zip_path):
    """Point common._archive_path() at `zip_path` for the block, restoring after."""
    real = common._archive_path
    common._archive_path = lambda: zip_path
    try:
        yield
    finally:
        common._archive_path = real


def _write_test_plugin_zip(zip_path: str, extra_defaults: dict | None = None,
                            afm_swift: bool = True, plugin_version: str | None = "9.9.9-test") -> str:
    """Build a zip mirroring build_plugin.py's core layout — just enough for common.py's own
    resolver (and, in the real-zipimport test, a real `import scourgify.common`) to work against
    it: scourgify/__init__.py, scourgify/common.py (THIS repo's current source, so the code under
    test runs identically whether monkeypatched or truly zipimported), scourgify/defaults/*,
    scourgify/afm.swift, and scourgify/_plugin_version.py (omit via plugin_version=None to pin the
    degenerate case)."""
    with open(os.path.join(common.HERE, "common.py"), encoding="utf-8") as f:
        common_src = f.read()
    with open(os.path.join(common.HERE, "__init__.py"), encoding="utf-8") as f:
        init_src = f.read()
    defaults = {"genres_allow.txt": "Action\nFantasy\n", "classify_vocab.txt": "Angst\nFluff\n",
                "classify_vocab_ao3.txt": ""}
    defaults.update(extra_defaults or {})
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("plugin-import-name-scourgify.txt", "")
        z.writestr("scourgify/__init__.py", init_src)
        z.writestr("scourgify/common.py", common_src)
        if plugin_version is not None:
            z.writestr("scourgify/_plugin_version.py",
                      '"""Written by build_plugin.py."""\n' f"VERSION = {plugin_version!r}\n")
        for name, content in defaults.items():
            z.writestr(f"scourgify/defaults/{name}", content)
        if afm_swift:
            z.writestr("scourgify/afm.swift", "// test afm.swift stand-in\n")
    return zip_path


def test_normal_install_returns_the_package_defaults_and_extracts_nothing():
    with tempfile.TemporaryDirectory() as td:
        home = os.path.join(td, "home")
        with env(SCOURGIFY_HOME=home):
            d = common.defaults_dir()
            assert d == os.path.join(common.HERE, "defaults")
            assert not os.path.exists(os.path.join(home, "cache"))


def test_zip_install_extracts_once_and_returns_the_cache():
    with tempfile.TemporaryDirectory() as td:
        zp = _write_test_plugin_zip(os.path.join(td, "p.zip"))
        with env(SCOURGIFY_HOME=os.path.join(td, "home")), _patched_archive(zp):
            d = common.defaults_dir()
            assert os.path.isdir(d)
            assert os.path.isfile(os.path.join(d, "genres_allow.txt"))
            assert os.path.isfile(os.path.join(d, "classify_vocab.txt"))
            # afm.swift is a sibling of defaults_dir(), mirroring HERE's own layout
            assert os.path.isfile(os.path.join(os.path.dirname(d), "afm.swift"))


def test_second_call_does_not_re_extract():
    with tempfile.TemporaryDirectory() as td:
        zp = _write_test_plugin_zip(os.path.join(td, "p.zip"))
        with env(SCOURGIFY_HOME=os.path.join(td, "home")), _patched_archive(zp):
            calls = []
            real_extract = common._extract_defaults

            def counting(*a, **kw):
                calls.append(1)
                return real_extract(*a, **kw)

            common._extract_defaults = counting
            try:
                d1 = common.defaults_dir()
                d2 = common.defaults_dir()
            finally:
                common._extract_defaults = real_extract
            assert d1 == d2
            assert len(calls) == 1


def test_a_zip_with_no_defaults_raises_guardrail():
    with tempfile.TemporaryDirectory() as td:
        zp = os.path.join(td, "empty.zip")
        with zipfile.ZipFile(zp, "w") as z:
            z.writestr("scourgify/__init__.py", "")
        with env(SCOURGIFY_HOME=os.path.join(td, "home")), _patched_archive(zp):
            try:
                common.defaults_dir()
                assert False, "expected GuardrailError"
            except common.GuardrailError:
                pass


def test_a_member_escaping_the_cache_root_is_refused():
    with tempfile.TemporaryDirectory() as td:
        zp = os.path.join(td, "evil.zip")
        with zipfile.ZipFile(zp, "w") as z:
            z.writestr("scourgify/defaults/genres_allow.txt", "Action\n")
            z.writestr("scourgify/defaults/../../evil.txt", "pwned\n")
        home = os.path.join(td, "home")
        with env(SCOURGIFY_HOME=home), _patched_archive(zp):
            try:
                common.defaults_dir()
                assert False, "expected GuardrailError"
            except common.GuardrailError:
                pass
        assert not os.path.exists(os.path.join(home, "evil.txt"))
        assert not os.path.exists(os.path.join(td, "evil.txt"))
        assert not os.path.exists(os.path.join(os.path.dirname(td), "evil.txt"))


def test_the_cache_follows_scourgify_home_set_after_import():
    with tempfile.TemporaryDirectory() as td:
        zp = _write_test_plugin_zip(os.path.join(td, "p.zip"))
        home1, home2 = os.path.join(td, "home1"), os.path.join(td, "home2")
        with _patched_archive(zp):
            with env(SCOURGIFY_HOME=home1):
                d1 = common.defaults_dir()
                assert d1.startswith(home1 + os.sep)
            with env(SCOURGIFY_HOME=home2):
                d2 = common.defaults_dir()
                assert d2.startswith(home2 + os.sep)
        assert d1 != d2


def test_the_version_key_separates_two_core_versions():
    with tempfile.TemporaryDirectory() as td:
        zp = _write_test_plugin_zip(os.path.join(td, "p.zip"))
        with env(SCOURGIFY_HOME=os.path.join(td, "home")), _patched_archive(zp):
            real_cv = common._core_version
            try:
                common._core_version = lambda: "1.0.0"
                d1 = common.defaults_dir()
                common._core_version = lambda: "2.0.0"
                d2 = common.defaults_dir()
            finally:
                common._core_version = real_cv
            assert d1 != d2
            assert os.path.isdir(d1) and os.path.isdir(d2)


def test_a_zip_without_a_plugin_version_still_gets_a_key():
    with tempfile.TemporaryDirectory() as td:
        zp = _write_test_plugin_zip(os.path.join(td, "p.zip"), plugin_version=None)
        with env(SCOURGIFY_HOME=os.path.join(td, "home")), _patched_archive(zp):
            v = common._core_version()
            assert v and os.sep not in v and "/" not in v and "\\" not in v
            d = common.defaults_dir()
            assert os.path.isdir(d)
            assert os.path.isfile(os.path.join(d, "genres_allow.txt"))


def test_a_warm_cache_wins_a_lost_rename_race():
    with tempfile.TemporaryDirectory() as td:
        zp = _write_test_plugin_zip(os.path.join(td, "p.zip"))
        home = os.path.join(td, "home")
        version = "1.0.0"
        cache_root = os.path.join(home, "cache", version)
        os.makedirs(os.path.join(cache_root, "defaults"), exist_ok=True)
        with open(os.path.join(cache_root, "defaults", "genres_allow.txt"), "w", encoding="utf-8") as f:
            f.write("PreExisting\n")
        with open(os.path.join(cache_root, ".extracted"), "w", encoding="utf-8") as f:
            f.write("ok\n")

        real_rename = os.rename

        def failing_rename(src, dst):
            raise FileExistsError("simulated lost race")

        os.rename = failing_rename
        try:
            common._extract_defaults(zp, cache_root, version)
        finally:
            os.rename = real_rename

        # the warm cache was never clobbered, and the loser's temp tree left nothing behind
        with open(os.path.join(cache_root, "defaults", "genres_allow.txt"), encoding="utf-8") as f:
            assert f.read() == "PreExisting\n"
        assert os.path.isfile(os.path.join(cache_root, ".extracted"))
        leftovers = [n for n in os.listdir(os.path.join(home, "cache")) if n.startswith(".tmp-")]
        assert leftovers == []

        # and defaults_dir() itself, resolved through the normal path, agrees
        with env(SCOURGIFY_HOME=home), _patched_archive(zp):
            real_cv = common._core_version
            common._core_version = lambda: version
            try:
                d = common.defaults_dir()
            finally:
                common._core_version = real_cv
        assert d == os.path.join(cache_root, "defaults")


def test_a_real_zipimport_resolves_the_archive_and_the_cache():
    """The only assertion in this file that proves REAL zipimport rather than a monkeypatched
    resolver (REVIEW: Codex plan-03 MEDIUM) — a child process with the fixture zip first on
    sys.path imports scourgify.common from inside it and reports defaults_dir()."""
    with tempfile.TemporaryDirectory() as td:
        zp = _write_test_plugin_zip(os.path.join(td, "p.zip"))
        home = os.path.join(td, "home")
        code = (
            "import sys, json\n"
            f"sys.path.insert(0, {zp!r})\n"
            "from scourgify import common\n"
            "d = common.defaults_dir()\n"
            "print(json.dumps({'file': common.__file__, 'archive': common._archive_path(), "
            "'defaults_dir': d}))\n"
        )
        child_env = dict(os.environ)
        child_env["SCOURGIFY_HOME"] = home
        child_env.pop("CALIBRE_LIBRARY", None)
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           env=child_env, timeout=30)
        assert r.returncode == 0, f"child failed: {r.stderr}"
        out = json.loads(r.stdout.strip().splitlines()[-1])
        assert zp in out["file"]
        assert out["archive"] == zp
        assert os.path.isdir(out["defaults_dir"])
        assert os.path.isfile(os.path.join(out["defaults_dir"], "genres_allow.txt"))


def test_the_built_plugin_zip_holds_every_member_the_resolver_expects():
    """Keeps the resolver's expectations and build_plugin.py's own zip layout pinned to each
    other (REVIEW: Codex plan-03 suggestion)."""
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    import build_plugin

    with tempfile.TemporaryDirectory() as td:
        out = build_plugin.build(out_dir=td)
        with zipfile.ZipFile(out) as z:
            names = z.namelist()
        assert "plugin-import-name-scourgify.txt" in names
        assert "scourgify/_plugin_version.py" in names
        assert "scourgify/afm.swift" in names
        assert any(n.startswith("scourgify/defaults/") for n in names)
        assert any(n.startswith("scourgify/defaults/ao3/") for n in names)
        assert not any(n == "scourgify/afm" for n in names)


def _real_defaults_file(name: str) -> str:
    with open(os.path.join(common.HERE, "defaults", name), encoding="utf-8") as f:
        return f.read()


def test_cost_estimate_is_identical_from_the_zip_and_from_the_package():
    """ROADMAP success criterion 1: a classify cost estimate over a fixed scope, computed from the
    package defaults and from a zip extraction, must be identical — proof that classify.load_vocab()
    routes through the SAME resolver either way."""
    from scourgify import classify

    classify.clear_caches()
    cost_package = classify.est_cost(10, "openai")
    vocab_package = classify.load_vocab()
    assert len(vocab_package) > 100

    with tempfile.TemporaryDirectory() as td:
        zp = _write_test_plugin_zip(os.path.join(td, "p.zip"),
                                    extra_defaults={"classify_vocab.txt": _real_defaults_file("classify_vocab.txt"),
                                                     "classify_vocab_ao3.txt": _real_defaults_file("classify_vocab_ao3.txt")})
        with env(SCOURGIFY_HOME=os.path.join(td, "home")), _patched_archive(zp):
            classify.clear_caches()
            cost_zip = classify.est_cost(10, "openai")
            vocab_zip = classify.load_vocab()
    classify.clear_caches()

    assert vocab_zip == vocab_package
    assert cost_zip == cost_package


def test_an_empty_vocabulary_refuses():
    from scourgify import classify

    with tempfile.TemporaryDirectory() as td:
        zp = _write_test_plugin_zip(os.path.join(td, "p.zip"),
                                    extra_defaults={"classify_vocab.txt": "# nothing but a comment\n",
                                                     "classify_vocab_ao3.txt": "# also nothing\n"})
        with env(SCOURGIFY_HOME=os.path.join(td, "home")), _patched_archive(zp):
            classify.clear_caches()
            try:
                classify.load_vocab()
                assert False, "expected GuardrailError"
            except common.GuardrailError:
                pass
    classify.clear_caches()


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
