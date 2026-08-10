#!/usr/bin/env python3
"""Build the Calibre plugin zip from THIS source tree.

    uv run build_plugin.py            -> dist/scourgify-plugin-<version>.zip

Layout, and why (NLSpec Constraints: version coherence, no vendored deps):

    plugin-import-name-scourgify.txt   empty; without it Calibre cannot import the plugin's own
                                       submodules (briefing footgun 6)
    __init__.py                        the InterfaceActionBase wrapper, version stamped from
                                       pyproject.toml
    action.py                          the Qt layer
    scourgify/…                        the core, verbatim from src/scourgify/

The core sits at the zip ROOT as an ordinary top-level package, exactly as FanFicFare ships
`fanficfare/`. Calibre's `Plugin.__enter__` appends the plugin zip to sys.path (disassembled from
Calibre 9.11), so zipimport resolves `import scourgify` and every `from scourgify.common import …`
in the core keeps working with NO import rewrite. Calibre's bundled Python has an empty
site-packages, so a `uv tool install`ed scourgify is invisible to it — the core has to ship here.

One version, one source tree: the zip's version is read from pyproject.toml, the same string the
wheel is built from, and `scourgify/_plugin_version.py` carries it into the core for settings to
show. Cutting a release runs this and attaches the zip (see CLAUDE.md, Branching & releases).

ponytail: `zip` the CLI is refused by this machine's permission classifier and zipfile is the
natural tool anyway. No manifest, no staging dir, no incremental build — the whole thing is
~400 KB and takes a few hundred ms.
"""
import os
import re
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.join(HERE, "src", "scourgify")
PLUGIN = os.path.join(HERE, "plugin")
SKIP_DIRS = {"__pycache__"}
SKIP_EXT = {".pyc", ".pyo"}
SKIP_NAMES = {"afm"}                 # the compiled Swift binary; afm.swift itself does ship


def version() -> str:
    """The single version, read from pyproject.toml — never from src/scourgify/__init__.py, which
    reads it back from installed metadata (briefing: a release already shipped wrong this way)."""
    text = open(os.path.join(HERE, "pyproject.toml")).read()
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    if not m:
        raise SystemExit("no version in pyproject.toml")
    return m.group(1)


def _files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            if name.startswith(".") or os.path.splitext(name)[1] in SKIP_EXT or name in SKIP_NAMES:
                continue
            yield os.path.join(dirpath, name)


def build(out_dir="dist") -> str:
    v = version()
    tup = tuple(int(p) for p in re.findall(r"\d+", v)[:3])
    os.makedirs(os.path.join(HERE, out_dir), exist_ok=True)
    out = os.path.join(HERE, out_dir, "scourgify-plugin-%s.zip" % v)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for path in _files(PLUGIN):
            arc = os.path.relpath(path, PLUGIN)
            data = open(path, "rb").read()
            if arc == "__init__.py":                     # stamp the version into the wrapper
                data = data.replace(b"__version__ = (0, 0, 0)",
                                    ("__version__ = %r" % (tup,)).encode())
            z.writestr(arc, data)
        for path in _files(CORE):
            z.write(path, os.path.join("scourgify", os.path.relpath(path, CORE)))
        z.writestr("scourgify/_plugin_version.py",
                   '"""Written by build_plugin.py. The zip and the wheel are cut from one tree."""\n'
                   'VERSION = %r\n' % v)
    return out


def main():
    out = build()
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
    assert "plugin-import-name-scourgify.txt" in names, "the import-name marker must be at the ROOT"
    assert "scourgify/common.py" in names and "scourgify/ops.py" in names
    assert any(n.startswith("scourgify/defaults/") for n in names), "defaults/ must ship"
    assert not any(n.endswith(".pyc") or n == "scourgify/afm" for n in names)
    print("%s  (%d files, %.0f KB)" % (out, len(names), os.path.getsize(out) / 1024))
    print("install:  calibre-customize -a %s      # then RESTART Calibre" % out)


if __name__ == "__main__":
    sys.exit(main())
