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
    rows = [{"added_tags": "Fluff; Angst"}, {"added_tags": ""}, {"added_tags": "  "}, {"added_tags": "Fix-It"}]
    assert wizard._proposal_counts(rows) == (2, 2)
    assert wizard._proposal_counts([]) == (0, 0)
    assert wizard._proposal_counts([{"added_tags": "x"}]) == (1, 0)


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


def _clear_engine_keys():
    for keys in wizard.ENGINE_KEYS.values():
        for k in keys: os.environ.pop(k, None)


def test_engines_cloud_usable_iff_key_in_env():
    saved = {k: os.environ.get(k) for keys in wizard.ENGINE_KEYS.values() for k in keys}
    try:
        _clear_engine_keys()
        engs = wizard._engines()
        assert [n for n, _, _ in engs] == ["apple", "claude", "openai", "gemini", "mistral"]  # all surfaced, in order
        eng = {n: (o, h) for n, o, h in engs}
        assert eng["claude"] == (False, "no API key in env")                 # no key -> unusable, honest hint
        os.environ["ANTHROPIC_API_KEY"] = "sk-test"
        eng = {n: (o, h) for n, o, h in wizard._engines()}
        assert eng["claude"] == (True, "key set ✓")                          # key present -> usable
        assert all(h for _, _, h in wizard._engines())                       # every engine always carries a hint
    finally:
        _clear_engine_keys()
        for k, v in saved.items():
            if v is not None: os.environ[k] = v


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
