#!/usr/bin/env python3
"""Pins the PURE helpers in wizard.py — the file-signal / menu-hint / engine-detection logic where a
regression would silently mis-flag pending work or hide a usable engine. The rich-interactive shells
(menus, prompts, the live dashboard) are deliberately NOT tested — mocking a console is coverage
theater. No framework:  uv run tests/test_wizard.py  (also pytest-collectable). No Calibre/library/network."""
import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from scourgify import wizard          # hard-imports rich (a declared dependency), so importable in any install


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


def test_task_hint_other_tasks_and_unknown():
    assert wizard._task_hint("classify", {"changed": 12}) == "12 new/changed"
    assert wizard._task_hint("classify", {"changed": 0}) == ""
    assert wizard._task_hint("backfill", {"backfill": 5}) == "5 books to backfill"
    assert wizard._task_hint("overrides", {"rejects": 2}) == "2 rejects to convert"
    assert wizard._task_hint("bogus", {}) == ""


def test_scope_options_slots_are_stable_in_every_library_state():
    """THE Part-C invariant. Rows are conditional, so if an empty one vanished the numbering would
    shift and '2' would mean 'never classified' in one state and 'whole library, real money' in
    another. Every row keeps its slot; an inapplicable one greys out (id=None) instead."""
    changed, empty = wizard._scope_options({1: "new", 2: "updated", 3: "new"}, 100, 50)[0], \
                     wizard._scope_options({}, 1234, 0)[0]
    for opts in (changed, empty):
        assert [k for k, _, _, _ in opts] == ["1", "2", "3", "4"]          # slots never move
        assert [i for _, i, _, _ in opts][2:] == ["all", "skip"]           # ... and neither do meanings
    assert [i for _, i, _, _ in changed][:2] == ["changed", "unclassified"]
    assert [i for _, i, _, _ in empty][:2] == [None, None]                 # both greyed, still present
    assert "3 books" in changed[0][2] and "2 new" in changed[0][3]
    assert "1,234 books" in empty[2][2]


def test_scope_options_default_follows_availability():
    assert wizard._scope_options({1: "new"}, 100, 50)[1] == "changed"      # cheapest useful scope
    assert wizard._scope_options({}, 100, 50)[1] == "unclassified"         # nothing changed -> the backlog
    assert wizard._scope_options({}, 100, 0)[1] == "all"                   # nothing left -> only a full pass


def test_engine_options_price_the_billed_set():
    engs = [("apple", True, "free, on-device"), ("claude", True, "key set ✓")]
    opts = wizard._engine_options(engs, 10)
    assert opts[0][:3] == ("1", "apple", "apple") and "free" in opts[0][3]
    assert opts[1][1] == "claude" and "~$" in opts[1][3] and "for 10 books" in opts[1][3]


def test_engines_cloud_usable_iff_key_in_env():
    engs = wizard._engines(env={})                              # env injected — no os.environ juggling
    assert [n for n, _, _ in engs] == ["apple", "claude", "openai", "gemini", "mistral"]  # all surfaced, in order
    eng = {n: (o, h) for n, o, h in engs}
    assert eng["claude"] == (False, "no API key in env")                 # no key -> unusable, honest hint
    eng = {n: (o, h) for n, o, h in wizard._engines(env={"ANTHROPIC_API_KEY": "sk-t"})}
    assert eng["claude"] == (True, "key set ✓")                          # key present -> usable
    assert all(h for _, _, h in wizard._engines(env={}))                 # every engine always carries a hint


OPTS3 = [("1", "apple", "apple", "h"), ("2", "claude", "claude", "h"), ("3", "openai", "openai", "h")]


def test_default_engine_id_judge_prefers_first_judge_capable():
    """The promote picker's default: first judge-capable engine (apple is not). An ENGINE NAME,
    not a key — the default has to survive the row order changing."""
    assert wizard._default_engine_id(OPTS3, judge=False) == "apple"
    assert wizard._default_engine_id(OPTS3, judge=True) == "claude"
    assert wizard._default_engine_id([("1", "apple", "apple", "h")], judge=True) == "apple"


def test_default_engine_id_never_defaults_to_an_unusable_engine():
    """⏎ on the promote picker used to land on claude with no ANTHROPIC_API_KEY set — the picker
    rejected its own default and re-asked. The default must be usable."""
    opts = OPTS3 + [("4", "compare", "compare", "h")]
    usable = {"apple", "openai"}                                  # no claude key in env
    assert wizard._default_engine_id(opts, judge=True, usable=usable) == "openai"
    assert wizard._default_engine_id(opts, judge=False, usable=usable) == "apple"
    assert wizard._default_engine_id(opts, judge=False, usable={"openai"}) == "openai"
    assert wizard._default_engine_id(opts, judge=True, usable=set()) == "apple"      # nothing usable: first
    # `compare` is not an engine, but engines.trait() falls back to the cloud defaults for any
    # unlisted name and so reports it judge-capable — the usable filter is what keeps it out.
    assert wizard._default_engine_id(opts, judge=True, usable=usable) != "compare"


def test_proposal_menu_has_one_slot_layout_tagged_or_not():
    """Two menus once shared the title "proposal" with different rows, so the same key meant
    'review 1-by-1' in one and 'discard' in the other — a keystroke that silently archived a
    proposal. One layout now; 'review 1-by-1' greys out when no book got tags."""
    full, stamp = wizard._proposal_options(10, 4), wizard._proposal_options(10, 0)
    for opts in (full, stamp):
        assert [k for k, _, _, _ in opts] == ["1", "2", "3", "4"]
        assert [i for _, i, _, _ in opts][::3] == ["apply", "discard"]   # slots 1 and 4 fixed
    assert full[1][1] == "step" and stamp[1][1] is None                  # slot 2 kept, just disabled


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
