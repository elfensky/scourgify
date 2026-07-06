#!/usr/bin/env python3
"""Regression tests for tag promotion features.
No framework needed:  uv run tests/test_promote.py   (also collectable by pytest).
No Calibre, no library, no network."""
import os, sys, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))


def test_ask_retry_success_and_block():
    from scourgify.classify import ask_retry
    class OK:
        def ask(self, p): return "yes"
    assert ask_retry(OK(), "x") == ("yes", "")

    class Blocked:
        def ask(self, p): raise RuntimeError("blocked:PROHIBITED")
    out, err = ask_retry(Blocked(), "x")
    assert out == "" and err.startswith("blocked:")

    class Flaky:                                 # fails once, then succeeds — but tries=1 gives up immediately
        def __init__(self): self.n = 0
        def ask(self, p):
            self.n += 1
            if self.n == 1: raise ValueError("429")
            return "ok"
    out, err = ask_retry(Flaky(), "x", tries=1)
    assert out == "" and "ValueError" in err


def test_mistral_registered_and_keyguard():
    import os
    from scourgify import classify
    assert "mistral" in classify.ENGINES and "mistral" in classify.PRICING
    os.environ.pop("MISTRAL_API_KEY", None)
    try:
        classify.ENGINES["mistral"]("", 60); assert False, "expected SystemExit"
    except SystemExit as e:
        assert "MISTRAL_API_KEY" in str(e)


def test_parse_decision():
    from scourgify.promote import parse_decision
    assert parse_decision('{"verdict":"promote","reason":"novel","confidence":"high"}') == \
        {"verdict": "promote", "target": "", "reason": "novel", "confidence": "high"}
    # fenced + prose around it
    d = parse_decision('Sure!\n```json\n{"verdict":"alias","target":"Time Travel","reason":"same"}\n```')
    assert d["verdict"] == "alias" and d["target"] == "Time Travel" and d["confidence"] == "med"
    assert parse_decision('{"verdict":"alias","reason":"no target"}') is None    # alias needs target
    assert parse_decision('{"verdict":"maybe"}') is None                          # bad verdict
    assert parse_decision("not json") is None


def test_shortlist_and_prompts():
    from scourgify.promote import shortlist, advocate_prompt, skeptic_prompt
    existing = ["Time Travel", "Fluff", "Angst", "Post-Apocalypse", "Slow Burn"]
    near = shortlist("Post-Apocalyptic", existing, n=3)
    assert "Post-Apocalypse" in near and len(near) <= 3       # true synonym surfaced despite low string sim
    cand = {"tag": "Post-Apocalyptic", "count": 4, "examples": ["A ruined world story"]}
    ap = advocate_prompt(cand, near)
    assert "Post-Apocalyptic" in ap and "Post-Apocalypse" in ap and "A ruined world story" in ap
    sp = skeptic_prompt(cand, {"verdict": "promote", "reason": "novel"}, near)
    assert "refute" in sp.lower() and "Post-Apocalypse" in sp


def test_candidates_join_and_ledger_skip():
    import csv, tempfile
    from scourgify.promote import candidates
    d = tempfile.mkdtemp()
    ranked = os.path.join(d, "r.csv"); prop = os.path.join(d, "p.csv"); ledger = os.path.join(d, "l.csv")
    with open(ranked, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["proposed_tag", "count"])
        w.writerow(["Gacha Mechanic", "2"]); w.writerow(["Amoral Deity", "1"]); w.writerow(["Old Tag", "3"])
    with open(prop, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["book_id", "title", "added_tags", "proposed_new"])
        w.writerow(["1", "Rolls of Fate", "", "Gacha Mechanic"])
        w.writerow(["2", "Cruel God", "", "Amoral Deity; Gacha Mechanic"])
    with open(ledger, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["tag", "verdict", "target"]); w.writerow(["Old Tag", "reject", ""])
    cs = candidates(ranked, prop, ledger)
    assert [c["tag"] for c in cs] == ["Gacha Mechanic", "Amoral Deity"]        # Old Tag skipped, count-sorted
    assert cs[0]["count"] == 2 and set(cs[0]["examples"]) == {"Rolls of Fate", "Cruel God"}


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
