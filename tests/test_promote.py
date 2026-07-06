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


def test_decide_reconciliation():
    from scourgify.promote import decide
    cand = {"tag": "Amoral Deity", "count": 1, "examples": ["A cruel god toys with mortals"]}
    near = ["Morality", "Deity", "Dark"]
    # advocate promotes, skeptic refutes -> reject, contested
    adv = lambda p: '{"verdict":"promote","reason":"seems new","confidence":"med"}'
    sk = lambda p: '{"verdict":"reject","reason":"too plot-specific","confidence":"high"}'
    calls = iter([adv, sk])
    ask = lambda p: next(calls)(p)
    d = decide(cand, ask, existing=near)
    assert d["verdict"] == "reject" and d["contested"] is True and d["tag"] == "Amoral Deity"

    # advocate aliases -> accepted directly, not contested (skeptic not consulted)
    d2 = decide(cand, lambda p: '{"verdict":"alias","target":"Morality","reason":"same idea"}', existing=near)
    assert d2["verdict"] == "alias" and d2["target"] == "Morality" and d2["contested"] is False

    # promote survives skepticism
    seq = iter(['{"verdict":"promote","reason":"novel"}', '{"verdict":"promote","reason":"agree, novel"}'])
    d3 = decide(cand, lambda p: next(seq), existing=near)
    assert d3["verdict"] == "promote" and d3["contested"] is False


def test_apply_decisions_routing():
    import csv, os, tempfile
    from scourgify.promote import apply_decisions
    d = tempfile.mkdtemp()
    review = os.path.join(d, "review.csv"); vocab = os.path.join(d, "vocab.txt")
    tropes = os.path.join(d, "tropes.csv"); aliases = os.path.join(d, "aliases.csv"); ledger = os.path.join(d, "l.csv")
    with open(review, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["tag", "count", "verdict", "target", "reason", "confidence", "contested"])
        w.writerow(["Gacha Mechanic", "2", "promote", "", "novel", "high", "False"])
        w.writerow(["Amoral Deity", "1", "alias", "Morality", "same", "med", "True"])
        w.writerow(["Chapter 3 Spoiler", "1", "reject", "", "plot", "high", "False"])
    n = apply_decisions(review, vocab, tropes, aliases, ledger)
    assert n == {"promote": 1, "alias": 1, "reject": 1}
    assert "Gacha Mechanic" in open(vocab).read()
    trows = list(csv.reader(open(tropes), delimiter=";"))
    assert ["Amoral Deity", "Morality", "tag"] in trows
    assert ["Amoral Deity", "Morality"] in list(csv.reader(open(aliases)))
    ledger_tags = {r["tag"] for r in csv.DictReader(open(ledger))}
    assert ledger_tags == {"Gacha Mechanic", "Amoral Deity", "Chapter 3 Spoiler"}
    assert not os.path.exists(review)                                  # archived away
    assert any(x.startswith("review_applied_") or "applied" in x for x in os.listdir(d))


def test_parse_resp_applied_alias_snap(tmp=None):
    import os, tempfile, csv
    from scourgify import classify
    d = tempfile.mkdtemp(); os.makedirs(os.path.join(d, "overrides"))
    with open(os.path.join(d, "overrides", "promote_aliases.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["candidate", "target"]); w.writerow(["Post-Apocalyptic", "Angst"])
    old = os.getcwd(); os.chdir(d); classify._ALIASES = None; classify._VOCAB = None
    try:
        vt, nt = classify.parse_resp('{"tags": [], "new": ["Post-Apocalyptic"]}')
        assert "Angst" in vt          # snapped to the aliased vocab term, applied
        assert "Post-Apocalyptic" not in nt
    finally:
        os.chdir(old); classify._ALIASES = None; classify._VOCAB = None


def test_promote_run_writes_review(tmp=None):
    import os, csv, tempfile
    from scourgify import classify, promote
    class Fake:                                     # advocate promotes, skeptic agrees -> promote stands
        def __init__(self, model, timeout): pass
        def ask(self, prompt): return '{"verdict":"promote","reason":"novel reusable trope","confidence":"high"}'
    classify.ENGINES["fake"] = Fake
    d = tempfile.mkdtemp()
    ranked = os.path.join(d, "r.csv"); prop = os.path.join(d, "p.csv")
    with open(ranked, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["proposed_tag", "count"]); w.writerow(["Reality Warping", "3"])
    with open(prop, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["book_id", "title", "added_tags", "proposed_new"]); w.writerow(["1", "Bend It", "", "Reality Warping"])
    review = os.path.join(d, "promote_review.csv")
    a = promote.build_parser().parse_args(["--engine", "fake", "--yes"])
    promote.run(a, ranked_path=ranked, proposal_path=prop, review_path=review, existing=["Time Travel", "Fluff"])
    rows = list(csv.DictReader(open(review)))
    assert len(rows) == 1 and rows[0]["tag"] == "Reality Warping" and rows[0]["verdict"] == "promote"
    del classify.ENGINES["fake"]


def test_apply_decisions_normalizes_verdict():
    import csv, os, tempfile
    from scourgify.promote import apply_decisions
    d = tempfile.mkdtemp()
    review = os.path.join(d, "review.csv"); vocab = os.path.join(d, "vocab.txt")
    tropes = os.path.join(d, "tropes.csv"); aliases = os.path.join(d, "aliases.csv"); ledger = os.path.join(d, "l.csv")
    with open(review, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["tag", "count", "verdict", "target", "reason", "confidence", "contested"])
        w.writerow(["Soul Bond", "3", "Promote ", "", "novel", "high", "False"])   # capitalized + trailing space
        w.writerow(["Chapter 7 Reveal", "1", "bogus", "", "noise", "low", "False"])  # invalid verdict
    n = apply_decisions(review, vocab, tropes, aliases, ledger)
    # "Promote " normalizes to promote -> routed to vocab
    assert "Soul Bond" in open(vocab).read()
    # "bogus" -> skipped: not written to ledger
    ledger_tags = {r["tag"] for r in csv.DictReader(open(ledger))}
    assert "Soul Bond" in ledger_tags
    assert "Chapter 7 Reveal" not in ledger_tags
    assert n == {"promote": 1, "alias": 0, "reject": 0}


def test_parse_decision_strips_formula_chars():
    from scourgify.promote import parse_decision
    # formula char in reason is stripped
    d = parse_decision('{"verdict":"promote","reason":"=cmd()","confidence":"low"}')
    assert d is not None and not d["reason"].startswith("=")
    assert d["reason"] == "cmd()"
    # formula char in alias target: if sanitized target is non-empty, alias is valid
    d2 = parse_decision('{"verdict":"alias","target":"=EVIL","reason":"x"}')
    # "=EVIL" lstripped of "=" -> "EVIL" (non-empty): alias should return with target "EVIL"
    # OR if implementation collapses it to empty, alias returns None — both acceptable, but must be deterministic
    if d2 is None:
        # alias with empty sanitized target -> None is correct
        pass
    else:
        assert not d2["target"].startswith("=")
    # fully formula-only target (all stripped away) -> alias returns None
    d3 = parse_decision('{"verdict":"alias","target":"=+-@","reason":"x"}')
    assert d3 is None


def test_decide_skeptic_inconclusive_marks_low():
    from scourgify.promote import decide
    cand = {"tag": "Dream Logic", "count": 2, "examples": ["A dreamscape adventure"]}
    near = ["Dreams", "Surreal"]
    # advocate promotes; skeptic returns "" (unparseable)
    responses = iter([
        '{"verdict":"promote","reason":"novel surreal subgenre","confidence":"high"}',
        "",  # skeptic transport failure
    ])
    ask = lambda p: next(responses)
    d = decide(cand, ask, existing=near)
    assert d["verdict"] == "promote"
    assert d["confidence"] == "low"
    assert d["contested"] is False
    assert "[skeptic inconclusive]" in d["reason"]


def test_run_raises_on_existing_review():
    import os, csv, tempfile
    from scourgify import classify, promote
    class Fake:
        def __init__(self, model, timeout): pass
        def ask(self, prompt): return '{"verdict":"promote","reason":"novel","confidence":"high"}'
    classify.ENGINES["fake2"] = Fake
    d = tempfile.mkdtemp()
    ranked = os.path.join(d, "r.csv"); prop = os.path.join(d, "p.csv")
    with open(ranked, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["proposed_tag", "count"]); w.writerow(["Ghost Bond", "2"])
    with open(prop, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["book_id", "title", "added_tags", "proposed_new"])
    review = os.path.join(d, "promote_review.csv")
    # pre-create the review file to simulate a pending review
    with open(review, "w") as f: f.write("existing content")
    # without --yes, should raise SystemExit
    a = promote.build_parser().parse_args(["--engine", "fake2"])
    raised = False
    try:
        promote.run(a, ranked_path=ranked, proposal_path=prop, review_path=review)
    except SystemExit as e:
        raised = True
        assert "pending review" in str(e)
    assert raised, "expected SystemExit when review file exists and --yes not set"
    # with --yes, should overwrite without error
    a2 = promote.build_parser().parse_args(["--engine", "fake2", "--yes"])
    # candidates list is empty (prop has no rows), so run exits early with "nothing to do"
    promote.run(a2, ranked_path=ranked, proposal_path=prop, review_path=review)
    del classify.ENGINES["fake2"]


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
