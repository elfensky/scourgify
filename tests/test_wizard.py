#!/usr/bin/env python3
"""Pins the PURE helpers in wizard.py — the file-signal / menu-hint logic where a regression
would silently mis-flag pending work — plus the six pure option builders now relocated to their
owning tool modules (classify/engines/synopsis — FOUND-06/D-10) and the decide= injection seam
on all seven review-checklist call sites (D-11). The rich-interactive shells (menus, prompts, the
live dashboard) are deliberately NOT tested — mocking a console is coverage theater.
No framework:  uv run tests/test_wizard.py  (also pytest-collectable). No Calibre/library/network
except where a fixture library is explicitly built below (ro_connect()-backed helpers)."""
import contextlib, os, subprocess, sys, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scourgify import wizard          # hard-imports rich (a declared dependency), so importable in any install
from scourgify import artifacts, classify, common, engines, overrides, promote, staleness, synopsis
import fixture_db

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")


def test_proposal_counts_splits_pending_from_stamp_only():
    # a book with added_tags will gain tags (pending); a no-match row awaits only a stamp (to_stamp),
    # so it is NOT counted as pending — the invariant that keeps no-match books from being re-sent forever.
    # rows come from artifacts.read_proposal, so added_tags is a list.
    rows = [{"added_tags": ["Fluff", "Angst"]}, {"added_tags": []}, {"added_tags": []}, {"added_tags": ["Fix-It"]}]
    assert wizard._proposal_counts(rows) == (2, 2)
    assert wizard._proposal_counts([]) == (0, 0)
    assert wizard._proposal_counts([{"added_tags": ["x"]}]) == (1, 0)


def test_task_hint_review_prefers_pending_over_stamp():
    # review shows "to apply" when any book will gain tags, else falls back to the stamp-only count
    assert wizard._task_hint("review", {"pending": 3, "to_stamp": 9}) == "3 books to apply"
    assert wizard._task_hint("review", {"pending": 0, "to_stamp": 9}) == "9 to stamp"
    assert wizard._task_hint("review", {"pending": 0, "to_stamp": 0}) == ""


def test_task_hint_promote_joins_candidates_and_verdicts():
    assert wizard._task_hint("promote", {"candidates": 4, "verdicts_pending": True}) == "4 candidates · verdicts ready to apply"
    assert wizard._task_hint("promote", {"candidates": 0, "verdicts_pending": True}) == "verdicts ready to apply"
    assert wizard._task_hint("promote", {"candidates": 0, "verdicts_pending": False}) == ""


def test_task_hint_classify_surfaces_the_backlog():
    """The never-classified backlog is the largest outstanding work in most libraries and used to
    be invisible: snapshot() did not carry it, so the wizard said "up to date" with thousands of
    books never attempted. Reported live 2026-07-31 at 7,829 books."""
    assert wizard._task_hint("classify", {"changed": 12, "unclassified": 0}) == "12 new/changed"
    assert wizard._task_hint("classify", {"changed": 0, "unclassified": 7829}) == "7,829 never classified"
    assert wizard._task_hint("classify", {"changed": 12, "unclassified": 7829}) \
        == "12 new/changed · 7,829 never classified"
    assert wizard._task_hint("classify", {"changed": 0, "unclassified": 0}) == ""


def test_task_hint_other_tasks_and_unknown():
    assert wizard._task_hint("classify", {"changed": 12}) == "12 new/changed"
    assert wizard._task_hint("classify", {"changed": 0}) == ""
    assert wizard._task_hint("backfill", {"backfill": 5}) == "5 books to backfill"
    assert wizard._task_hint("overrides", {"rejects": 2}) == "2 rejects to convert"
    assert wizard._task_hint("bogus", {}) == ""


# ---------------- relocated builders (FOUND-06/D-10) ----------------
# classify.scope_options / classify.proposal_options / engines.engine_options /
# engines.default_engine_id / engines.engine_rows / synopsis.options — moved verbatim out of
# wizard.py. wizard.py keeps no private copy and no compatibility alias (asserted in test_cli.py's
# source-reading ban and via the acceptance-criteria hasattr check in the plan; not duplicated here).

def test_scope_options_slots_are_stable_in_every_library_state():
    """THE Part-C invariant. Rows are conditional, so if an empty one vanished the numbering would
    shift and '2' would mean 'never classified' in one state and 'whole library, real money' in
    another. Every row keeps its slot; an inapplicable one greys out (id=None) instead."""
    changed, empty = classify.scope_options({1: "new", 2: "updated", 3: "new"}, 100, 50)[0], \
                     classify.scope_options({}, 1234, 0)[0]
    for opts in (changed, empty):
        assert [k for k, _, _, _ in opts] == ["1", "2", "3", "4", "5"]     # slots never move
        assert [i for _, i, _, _ in opts][2:] == ["last", "all", "skip"]   # ... and neither do meanings
    assert [i for _, i, _, _ in changed][:2] == ["changed", "unclassified"]
    assert [i for _, i, _, _ in empty][:2] == [None, None]                 # both greyed, still present
    assert "3 books" in changed[0][2] and "2 new" in changed[0][3]
    assert "1,234 books" in empty[3][2]


def test_scope_options_last_row_is_always_offered():
    """The targeted redo ("do the newest N again") must be reachable from the wizard, not just
    from `classify --last N` on the CLI. It is offered in every state a library has books."""
    assert classify.scope_options({}, 100, 0)[0][2][1] == "last"
    assert classify.scope_options({1: "new"}, 100, 50)[0][2][1] == "last"
    assert classify.scope_options({}, 0, 0)[0][2][1] is None            # empty library: greyed


def test_scope_options_default_follows_availability():
    assert classify.scope_options({1: "new"}, 100, 50)[1] == "changed"      # cheapest useful scope
    assert classify.scope_options({}, 100, 50)[1] == "unclassified"         # nothing changed -> the backlog
    assert classify.scope_options({}, 100, 0)[1] == "all"                   # nothing left -> only a full pass


def test_scope_options_empty_library_greys_last_row_and_defaults_whole_library():
    """Acceptance criterion: an empty changed-set AND a zero-book library greys the 'most recent
    N' row (id=None) and defaults to the whole-library scope."""
    opts, default = classify.scope_options({}, 0, 0)
    assert opts[2][1] is None
    assert default == "all"


def test_engine_options_price_the_billed_set():
    """cost_fn is INJECTED (classify.est_cost in production) so engines.py never imports
    classify — classify already imports engines, and the reverse edge would be a cycle
    (REVIEW: Codex agreed concern 5)."""
    engs = [("apple", True, "free, on-device"), ("claude", True, "key set ✓")]
    cost_fn = lambda n, e: 0.0 if e == "apple" else 0.001 * n
    opts = engines.engine_options(engs, 10, cost_fn)
    assert opts[0][:3] == ("1", "apple", "apple") and "free" in opts[0][3]
    assert opts[1][1] == "claude" and "~$" in opts[1][3] and "for 10 books" in opts[1][3]


def test_engines_cloud_usable_iff_key_in_env():
    engs = engines.engine_rows(env={})                              # env injected — no os.environ juggling
    assert [n for n, _, _ in engs] == ["apple", "claude", "openai", "gemini", "mistral"]  # all surfaced, in order
    eng = {n: (o, h) for n, o, h in engs}
    assert eng["claude"] == (False, "no API key in env")                 # no key -> unusable, honest hint
    eng = {n: (o, h) for n, o, h in engines.engine_rows(env={"ANTHROPIC_API_KEY": "sk-t"})}
    assert eng["claude"] == (True, "key set ✓")                          # key present -> usable
    assert all(h for _, _, h in engines.engine_rows(env={}))             # every engine always carries a hint


def test_engine_rows_env_empty_returns_all_unusable_with_hints():
    """Acceptance criterion: engines.engine_rows(env={}) returns one row per engine in
    engines.ENGINES, none usable, each carrying a non-empty hint. apple is on-device and needs no
    key, so it must also be denied its toolchain here (mirrors test_engines.py's
    test_apple_is_still_absent_in_platform_without_a_toolchain) — otherwise this assertion is only
    true by accident of the host running the suite."""
    import shutil
    real_which, real_shipped = shutil.which, engines._shipped_dir
    shutil.which = lambda name: None
    engines._shipped_dir = lambda: os.path.join(os.sep, "no", "such", "shipped", "dir")
    try:
        rows = engines.engine_rows(env={})
    finally:
        shutil.which = real_which
        engines._shipped_dir = real_shipped
    assert [n for n, _, _ in rows] == list(engines.ENGINES)
    assert not any(ok for _, ok, _ in rows)
    assert all(h for _, _, h in rows)


OPTS3 = [("1", "apple", "apple", "h"), ("2", "claude", "claude", "h"), ("3", "openai", "openai", "h")]


def test_default_engine_id_judge_prefers_first_judge_capable():
    """The promote picker's default: first judge-capable engine (apple is not). An ENGINE NAME,
    not a key — the default has to survive the row order changing."""
    assert engines.default_engine_id(OPTS3, judge=False) == "apple"
    assert engines.default_engine_id(OPTS3, judge=True) == "claude"
    assert engines.default_engine_id([("1", "apple", "apple", "h")], judge=True) == "apple"


def test_default_engine_id_never_defaults_to_an_unusable_engine():
    """⏎ on the promote picker used to land on claude with no ANTHROPIC_API_KEY set — the picker
    rejected its own default and re-asked. The default must be usable."""
    opts = OPTS3 + [("4", "compare", "compare", "h")]
    usable = {"apple", "openai"}                                  # no claude key in env
    assert engines.default_engine_id(opts, judge=True, usable=usable) == "openai"
    assert engines.default_engine_id(opts, judge=False, usable=usable) == "apple"
    assert engines.default_engine_id(opts, judge=False, usable={"openai"}) == "openai"
    assert engines.default_engine_id(opts, judge=True, usable=set()) == "apple"      # nothing usable: first
    # `compare` is not an engine, but engines.trait() falls back to the cloud defaults for any
    # unlisted name and so reports it judge-capable — the usable filter is what keeps it out.
    assert engines.default_engine_id(opts, judge=True, usable=usable) != "compare"


def test_proposal_menu_has_one_slot_layout_tagged_or_not():
    """Two menus once shared the title "proposal" with different rows, so the same key meant
    'review 1-by-1' in one and 'discard' in the other — a keystroke that silently archived a
    proposal. One layout now; 'review 1-by-1' greys out when no book got tags."""
    full, stamp = classify.proposal_options(10, 4), classify.proposal_options(10, 0)
    for opts in (full, stamp):
        assert [k for k, _, _, _ in opts] == ["1", "2", "3", "4"]
        assert [i for _, i, _, _ in opts][::3] == ["apply", "discard"]   # slots 1 and 4 fixed
    assert full[1][1] == "step" and stamp[1][1] is None                  # slot 2 kept, just disabled


def test_synopsis_options_slot_layout_stable_with_and_without_a_queue():
    for n in (0, 42):
        opts = synopsis.options(n)
        assert [k for k, _, _, _ in opts] == ["1", "2", "3"]
        assert opts[2][1] == "skip"                                     # skip always offered
    assert synopsis.options(0)[0][1] is None and synopsis.options(0)[1][1] is None   # nothing to do: greyed
    assert synopsis.options(42)[0][1] == "apply" and synopsis.options(42)[1][1] == "step"


def test_engines_does_not_import_classify():
    """T-01-27 / Codex agreed concern 5: classify.py already imports engines, so the reverse edge
    would be a cycle. engine_options takes an injected cost_fn instead of importing classify."""
    import ast
    import inspect
    src_path = os.path.join(SRC, "scourgify", "engines.py")
    tree = ast.parse(open(src_path, encoding="utf-8").read())
    names = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            names |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            names.add(n.module)
    assert not any(m == "classify" or m == "scourgify.classify" or m.endswith(".classify") for m in names), names
    assert "cost_fn" in inspect.signature(engines.engine_options).parameters


def test_no_relocated_builder_introduces_an_import_cycle():
    """Importing each owning module FIRST, alone, in a fresh interpreter — the order the wizard's
    own import list would otherwise mask (OpenCode MEDIUM)."""
    checks = [
        ("classify", "from scourgify import classify; classify.scope_options({}, 0, 0)"),
        ("engines", "from scourgify import engines; engines.engine_rows(env={})"),
        ("synopsis", "from scourgify import synopsis; synopsis.options(0)"),
    ]
    for name, code in checks:
        p = subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, {SRC!r})\n{code}"],
                           capture_output=True, text=True)
        assert p.returncode == 0, f"importing {name} alone failed:\n{p.stdout}{p.stderr}"


# ---------------- decide= seam on the seven review-checklist call sites (D-11) ----------------
@contextlib.contextmanager
def _env(**kv):
    saved = {k: os.environ.get(k) for k in kv}
    for k, v in kv.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    try:
        yield
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


@contextlib.contextmanager
def _fixture_env(books=None):
    """A throwaway fixture library + a redirected $SCOURGIFY_HOME — for the three sites that read
    titles through ro_connect() (classify.apply_proposal_step, overrides._step_walk,
    staleness.step). Clears the classify vocab cache and the uuid memo both ways."""
    books = books or [{"id": 1, "added": "2026-01-01 10:00:00", "title": "Book One"}]
    with tempfile.TemporaryDirectory() as td:
        lib = os.path.join(td, "lib"); os.makedirs(lib)
        fixture_db.build(os.path.join(lib, "metadata.db"), books).close()
        with _env(SCOURGIFY_HOME=os.path.join(td, "home"), CALIBRE_LIBRARY=lib):
            common.clear_uuid_cache()
            os.makedirs(common.data_dir())
            classify.clear_caches()
            try:
                yield td
            finally:
                classify.clear_caches()
                common.clear_uuid_cache()


def _no_ui_import(fn):
    """Run the zero-arg callable `fn` with scourgify.ui popped from sys.modules first; assert it
    was never re-imported as a side effect of the call (T-01-19 / Codex plan-05 LOW), restoring
    the saved entry afterward regardless of outcome."""
    saved = sys.modules.pop("scourgify.ui", None)
    try:
        result = fn()
        assert "scourgify.ui" not in sys.modules, "decide= must not import the interactive module"
        return result
    finally:
        if saved is not None:
            sys.modules["scourgify.ui"] = saved


def test_apply_proposal_step_decide_seam():
    with _fixture_env([{"id": 1, "added": "2026-01-01 10:00:00", "title": "Book One", "desc": "x"}]):
        artifacts.write_proposal([{"book_id": 1, "title": "Book One", "added_tags": ["Fluff"], "proposed_new": []}])
        recorded = {}
        def stub(title, items, subtitle=""):
            recorded["title"], recorded["items"] = title, items
            return [0], [], "apply"                        # accept the one tag
        recorded_ops = []
        saved_rw = classify.run_writer
        classify.run_writer = lambda ops, force=False, **kw: recorded_ops.append(ops)
        try:
            _no_ui_import(lambda: classify.apply_proposal_step(decide=stub))
        finally:
            classify.run_writer = saved_rw
        assert recorded["items"] == ["Fluff"]                          # what the interactive path would see
        assert "#1" in recorded["title"] and "Book One" in recorded["title"]
        (ops,) = recorded_ops
        assert any(o.get("op") == "set_field" and o.get("field") == "tags"
                  and o["values"].get("1") == ["Fluff"] for o in ops)


def test_step_walk_decide_seam():
    with _fixture_env([{"id": 1, "added": "2026-01-01 10:00:00", "title": "Book One"}]):
        recorded = {}
        def stub(title, items, subtitle=""):
            recorded["title"], recorded["items"] = title, items
            return [0], [], "apply"                        # accept — no rejects to log
        unique = {1: [("rename", "fandoms", "OldF", "NewF")]}
        rejects = _no_ui_import(lambda: overrides._step_walk(
            {}, {}, {"fandoms": "fandoms"}, {}, {}, unique, set(), {}, decide=stub))
        assert rejects == []
        assert recorded["items"] == [overrides._edit_label("rename", "fandoms", "OldF", "NewF")]
        assert "#1" in recorded["title"] and "Book One" in recorded["title"]


def test_step_pick_decide_seam():
    recorded = {}
    def stub(title, items, subtitle=""):
        recorded["title"], recorded["items"] = title, items
        return [0], [], "apply"
    auto = {"fandoms.csv": ["A,A"], "tropes.csv": ["B,B,tag"]}
    result = _no_ui_import(lambda: overrides.step_pick(auto, decide=stub))
    pairs = [(fn, l) for fn in sorted(auto) for l in sorted(set(auto[fn]))]
    assert recorded["items"] == [f"[dim]{fn}[/]  {l}" for fn, l in pairs]
    assert result == {pairs[0]}


def test_apply_decisions_step_decide_seam():
    with _fixture_env():
        review_path = os.path.join(common.data_dir(), "promote_review.csv")
        row = {"tag": "Foo", "count": "3", "verdict": "promote", "target": "",
              "reason": "r", "confidence": "high", "contested": "False"}
        artifacts.write_review([row], review_path)
        recorded = {}
        def stub(title, items, subtitle=""):
            recorded["title"], recorded["items"] = title, items
            return [0], [], "apply"                        # accept the one verdict
        n = _no_ui_import(lambda: promote.apply_decisions_step(review_path, decide=stub))
        assert n["promote"] == 1
        assert recorded["items"] == [promote.verdict_line(row)]
        assert not os.path.exists(review_path)                     # fully applied -> removed, not archived-with-leftover


def test_backfill_step_decide_seam():
    recorded = {}
    def stub(title, items, subtitle=""):
        recorded["title"], recorded["items"] = title, items
        return [0], [], "apply"
    chg = {1: ["A", "B"]}
    adds = {1: {"B"}}
    titles = {1: "Book One"}
    result = _no_ui_import(lambda: promote.backfill_step(chg, adds, titles, decide=stub))
    assert result == chg
    expected_items = [f"[bold]#{b}[/] {str(titles.get(b, ''))[:44]}  + "
                      f"[cyan]{', '.join(sorted(adds[b]))}[/]" for b in sorted(adds)]
    assert recorded["items"] == expected_items


def test_staleness_step_decide_seam():
    with _fixture_env([{"id": 1, "added": "2026-01-01 10:00:00", "title": "Book One"}]):
        recorded = {}
        def stub(title, items, subtitle=""):
            recorded["title"], recorded["items"] = title, items
            return [0], [], "apply"
        rows = [(1, "Hiatus", "Abandoned", 6.0)]
        result = _no_ui_import(lambda: staleness.step("#status", rows, decide=stub))
        assert result == rows
        assert recorded["items"] == [staleness.status_line(rows[0], "Book One")]


def test_synopsis_step_decide_seam():
    recorded = {}
    def stub(title, items, subtitle=""):
        recorded["title"], recorded["items"] = title, items
        return [0], [], "apply"
    made = {2: "A generated synopsis " * 3, 1: "Another one " * 3}
    titles = {1: "Book One", 2: "Book Two"}
    result = _no_ui_import(lambda: synopsis.step(made, titles, decide=stub))
    ids = sorted(made)
    expected_items = [f"[bold]#{b}[/] {str(titles.get(b, ''))[:36]:<36} [dim]{made[b][:120]}…[/]" for b in ids]
    assert recorded["items"] == expected_items
    assert result == {ids[0]: made[ids[0]]}


def test_all_seven_decide_sites_carry_the_parameter():
    """Acceptance criterion: every one of the seven functions has a `decide` parameter."""
    import inspect
    fns = [classify.apply_proposal_step, overrides._step_walk, overrides.step_pick,
          promote.apply_decisions_step, promote.backfill_step, staleness.step, synopsis.step]
    assert all("decide" in inspect.signature(f).parameters for f in fns)


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
