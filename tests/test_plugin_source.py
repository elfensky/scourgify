#!/usr/bin/env python3
"""The Qt layer is kept thin by ENFORCEMENT, not intention.

FanFicFare — the largest Calibre plugin there is — does not test its plugin layer at all, and
neither can this one: plugin/*.py import Qt and Calibre, which do not exist under CI's Python. So
this file reads their SOURCE, exactly as tests/test_cli.py reads wizard.py's, and fails on the four
mistakes that would actually hurt:

  - a second writer process against a library the GUI holds open (NLSpec B2.1 / briefing footgun 4)
  - a library read on the GUI thread (footgun 1: it froze Calibre and read as a crash)
  - a job callback that touches Qt from the worker thread (B3.5)
  - a job function that cannot be cancelled or report progress (B3.4)

Run: uv run tests/test_plugin_source.py
"""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(ROOT, "plugin")
MODULES = ["__init__.py", "action.py", "selftest.py"]

BANNED = ["run_writer(", "subprocess", "multiprocessing", "ThreadPoolExecutor", "os.system"]


def _src(name):
    return open(os.path.join(PLUGIN, name)).read()


def _tree(name):
    return ast.parse(_src(name), filename=name)


def _code(name):
    """The module's CODE, with comments and docstrings gone — so a doc line that names a banned
    call (this file's own subject matter) can't fail the grep, and a banned call can't hide in a
    comment either."""
    tree = _tree(name)
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                and isinstance(getattr(body[0], "value", None), ast.Constant) \
                and isinstance(body[0].value.value, str):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def test_the_plugin_never_spawns_a_second_writer():
    """run_writer shells out to calibre-debug against a library the GUI is holding open — the
    exact hazard the whole plugin exists to delete. A grep is the only thing that can't rot."""
    for name in MODULES:
        code = _code(name)
        for bad in BANNED:
            assert bad not in code, "%s: %r must not appear in the plugin" % (name, bad)


def _core_imports(tree):
    """[(module, enclosing function name or None)] for every import of the scourgify core."""
    out = []
    stack = []

    class V(ast.NodeVisitor):
        def visit_FunctionDef(self, node):
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        def visit_ImportFrom(self, node):
            if (node.module or "").split(".")[0] == "scourgify":
                out.append((node.module, stack[-1] if stack else None))

        def visit_Import(self, node):
            for a in node.names:
                if a.name.split(".")[0] == "scourgify":
                    out.append((a.name, stack[-1] if stack else None))

    V().visit(tree)
    return out


def test_no_core_import_can_run_on_the_gui_thread():
    """Every scourgify import lives inside a job_* function (or a helper only they call), so the
    GUI thread cannot reach a library read even by accident. Measured under Calibre 9.11: the
    CHEAPEST core read is 6 ms and wrangle.load_maps() is 870 ms — B3's <100 ms is a dispatch
    budget, and no core read qualifies. __init__.py is the one exception: its `import scourgify`
    is what puts the bundled core on sys.path, and it imports the package only, never a module
    that reads."""
    for module, func in _core_imports(_tree("action.py")):
        assert func is not None, "action.py imports %s at module level — that runs on the GUI thread" % module
        assert func.startswith("job_") or func.startswith("_"), \
            "%s imported in %s(): core imports belong in job_* functions" % (module, func)

    top = [m for m, f in _core_imports(_tree("__init__.py")) if f is None]
    assert top == [], "__init__.py must import the core only inside load_actual_plugin's `with self:`"


def test_nothing_runs_at_plugin_startup():
    """genesis() wires the action; initialization_complete() is where gui.current_db first exists
    and is therefore where the spike ran select.pick() and froze Calibre — the window stopped
    repainting and it read as a crash. Whatever lives there must be behind an env guard (the
    phase-4 test hook), never unconditional work."""
    fns = {n.name: n for n in ast.walk(_tree("action.py")) if isinstance(n, ast.FunctionDef)}
    genesis = ast.unparse(fns["genesis"])
    assert "scourgify" not in genesis and "ThreadedJob" not in genesis, \
        "genesis() wires the action and nothing else"
    body = [s for s in fns["initialization_complete"].body
            if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    assert len(body) == 1 and isinstance(body[0], ast.If) and "environ" in ast.unparse(body[0].test), \
        "initialization_complete() must do nothing unless a test hook is armed"


def test_every_job_callback_is_dispatcher_wrapped():
    """ThreadedJob.start_work calls `self.callback(self)` from the WORKER thread (disassembled,
    Calibre 9.11). An unwrapped callback therefore touches Qt off the GUI thread — the class of
    bug that shows up as a random crash days later."""
    calls = [n for n in ast.walk(_tree("action.py"))
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "ThreadedJob"]
    assert calls, "no ThreadedJob call found — the plugin must dispatch through Calibre's job system"
    for call in calls:
        cb = call.args[5] if len(call.args) > 5 else None
        assert isinstance(cb, ast.Call) and getattr(cb.func, "id", "") == "Dispatcher", \
            "ThreadedJob callback must be Dispatcher(...)-wrapped"


def test_job_functions_accept_abort_log_and_notifications():
    """ThreadedJob injects these three as kwargs. A job function missing them raises TypeError the
    first time a user clicks — and a job that ignores `abort` cannot be cancelled (B3.4)."""
    jobs = [n for n in ast.walk(_tree("action.py"))
            if isinstance(n, ast.FunctionDef) and n.name.startswith("job_")]
    assert jobs, "no job_* functions found"
    for fn in jobs:
        names = [a.arg for a in fn.args.args]
        for need in ("abort", "log", "notifications"):
            assert need in names, "%s() must accept %s" % (fn.name, need)


def test_the_import_name_marker_exists_and_is_empty():
    """A multi-file plugin without this file at the zip root cannot import its own submodules —
    and the failure is a silent non-load, not an error (briefing footgun 6)."""
    p = os.path.join(PLUGIN, "plugin-import-name-scourgify.txt")
    assert os.path.exists(p) and os.path.getsize(p) == 0


def test_actual_plugin_is_a_string_path():
    """`actual_plugin` must stay a 'module:Class' STRING so calibredb can read plugin metadata
    without importing Qt."""
    src = _src("__init__.py")
    assert "actual_plugin           = 'calibre_plugins.scourgify.action:ScourgifyAction'" in src


def test_the_zip_carries_the_core_and_the_marker_at_its_root():
    """Version coherence (NLSpec Constraints): one source tree, one version, and a zip that
    actually contains the core — Calibre's Python has an empty site-packages, so an installed
    scourgify is invisible to it."""
    import sys
    sys.path.insert(0, ROOT)
    import build_plugin
    import tempfile
    import zipfile

    with tempfile.TemporaryDirectory() as td:
        out = build_plugin.build(out_dir=td)
        with zipfile.ZipFile(out) as z:
            names = z.namelist()
            init = z.read("__init__.py").decode()
            stamped = z.read("scourgify/_plugin_version.py").decode()
    assert "plugin-import-name-scourgify.txt" in names
    assert "scourgify/common.py" in names and "scourgify/ops.py" in names
    assert any(n.startswith("scourgify/defaults/") for n in names)
    assert "__version__ = (0, 0, 0)" not in init, "build_plugin.py must stamp the real version"
    assert build_plugin.version() in stamped


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
