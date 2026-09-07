#!/usr/bin/env python3
"""Pins the PURE helpers in wizard.py — the file-signal / menu-hint logic where a regression
would silently mis-flag pending work — plus the six pure option builders now relocated to their
owning tool modules (classify/engines/synopsis — FOUND-06/D-10). The rich-interactive shells
(menus, prompts, the live dashboard) are deliberately NOT tested — mocking a console is coverage
theater. No framework:  uv run tests/test_wizard.py  (also pytest-collectable). No Calibre/library/network."""
import os, subprocess, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from scourgify import wizard          # hard-imports rich (a declared dependency), so importable in any install
from scourgify import classify, engines, synopsis

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


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
