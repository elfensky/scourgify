#!/usr/bin/env python3
"""The two properties that make a Calibre plugin possible at all (phase 2, NLSpec B3 +
Constraints). No framework:  uv run tests/test_plugin_safety.py  (also pytest-collectable).
No Calibre, no library, no network.

What breaks in the real world if these fail: Calibre's bundled Python has an EMPTY site-packages,
so `rich` is never importable there. If a core module grows a hard rich dependency the plugin
stops loading; if an import-time failure is a SystemExit rather than an ordinary exception, the
plugin takes Calibre down with it — `except Exception` does not catch SystemExit, which is why
the 2026-08-06 spike needed `except BaseException` to survive its own import."""
import os, sys, subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

# The eleven core modules the spike proved run under Calibre's interpreter, plus the two that
# joined them since (ops, cli). report is here on purpose: it is the rich-or-plain OWNER, so it
# is the module most likely to acquire a hard rich import by accident.
CORE = ["common", "ops", "select", "artifacts", "overrides", "engines", "booktext",
        "wrangle", "classify", "promote", "staleness", "setup", "report", "cli"]

_BLOCK_RICH = """
import sys
for m in [m for m in sys.modules if m == 'rich' or m.startswith('rich.')]: del sys.modules[m]
sys.modules['rich'] = None          # any `import rich` from here on raises ImportError
"""


def _run(body):
    """Run a snippet in a fresh interpreter with rich blocked. -> (returncode, output)."""
    code = f"import sys; sys.path.insert(0, {SRC!r})\n{_BLOCK_RICH}\n{body}"
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr)


def _imports(path):
    """Every module name a file imports, anywhere (including inside functions and try blocks).
    Parsed, not grepped: the docstrings here talk ABOUT the forbidden imports."""
    import ast
    out = set()
    for n in ast.walk(ast.parse(open(path).read())):
        if isinstance(n, ast.Import): out |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module: out.add(n.module)
    return out


def test_rich_is_actually_blocked_in_the_probe():
    """Guard the guard: if the block stopped working, every test below would pass vacuously."""
    rc, out = _run("import rich")
    assert rc != 0 and "rich" in out, out


def test_every_core_module_imports_without_rich():
    """The hard constraint. rich is a presentation dependency and must stay one — the moment a
    core module needs it, the plugin (and `calibre-debug -e _writer.py`) stops importing."""
    rc, out = _run("import " + ", ".join(f"scourgify.{m}" for m in CORE) + "\nprint('ok')")
    assert rc == 0, f"a core module needs rich:\n{out}"


def test_ui_refuses_with_a_catchable_error_not_a_process_exit():
    """ui.py is the one surface that genuinely requires rich. Refusing is right; refusing with
    SystemExit is not — inside a GUI process that is a plugin killing its host on import."""
    rc, out = _run(
        "try:\n"
        "    import scourgify.ui\n"
        "except Exception as e:\n"
        "    print(type(e).__name__)\n"
        "else:\n"
        "    print('IMPORTED')\n")
    assert rc == 0, f"importing ui without rich escaped `except Exception`:\n{out}"
    assert "GuardrailError" in out, out


def test_the_wizard_too_since_it_imports_ui_at_module_level():
    rc, out = _run(
        "try:\n"
        "    import scourgify.wizard\n"
        "except Exception as e:\n"
        "    print(type(e).__name__)\n")
    assert rc == 0, f"importing the wizard without rich escaped `except Exception`:\n{out}"
    assert "GuardrailError" in out, out


def test_the_cli_still_reports_a_missing_rich_as_a_plain_refusal():
    """...and the conversion back must not change what a CLI user sees: a message and a non-zero
    exit, never a traceback. The wizard is what a bare `scourgify` launches."""
    rc, out = _run("import os, sys\n"
                   "os.environ['SCOURGIFY_SCRIPT'] = 'q'   # a subprocess is no TTY; this is the seam\n"
                   "sys.argv = ['scourgify']\n"
                   "from scourgify.cli import main\n"
                   "main()\n")
    assert rc != 0, "a partial install exited 0"
    assert "Traceback" not in out, f"a partial install shows a traceback:\n{out}"
    assert "rich" in out, out


def test_the_writer_never_imports_a_presentation_module():
    """`calibre-debug -e _writer.py` runs where rich cannot exist; ops.py additionally has to
    stand alone because that interpreter has no importable `scourgify` package at all."""
    for name in ("_writer.py", "ops.py"):
        for mod in _imports(os.path.join(SRC, "scourgify", name)):
            assert not mod.startswith(("rich", "scourgify.ui", "scourgify.wizard", "scourgify.report")), \
                f"{name} imports {mod}"
    for mod in _imports(os.path.join(SRC, "scourgify", "ops.py")):
        assert not mod.startswith("scourgify"), \
            f"ops.py imports {mod} — calibre-debug -e cannot resolve the scourgify package"


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
