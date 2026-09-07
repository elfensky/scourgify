#!/usr/bin/env python3
"""Regression tests for tag promotion features.
No framework needed:  uv run tests/test_promote.py   (also collectable by pytest).
No Calibre, no library, no network."""
import os, sys, tempfile, contextlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scourgify import common
import fixture_db


@contextlib.contextmanager
def _fixture_library_env(td: str, uuid: str):
    """Point SCOURGIFY_HOME + CALIBRE_LIBRARY at a throwaway fixture library under `td` — several
    tests here reach artifacts.*() defaults (ledger()/review()), which resolve through
    common.data_dir(), now uuid-scoped and requiring a resolvable library. Restores both env vars
    and clears the uuid memo on exit."""
    old = {k: os.environ.get(k) for k in ("SCOURGIFY_HOME", "CALIBRE_LIBRARY")}
    os.environ["SCOURGIFY_HOME"] = os.path.join(td, "home")
    lib = os.path.join(td, "lib"); os.makedirs(lib, exist_ok=True)
    fixture_db.build(os.path.join(lib, "metadata.db"), [{"id": 1}], uuid=uuid).close()
    os.environ["CALIBRE_LIBRARY"] = lib
    common.clear_uuid_cache()
    try:
        yield
    finally:
        for k, v in old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        common.clear_uuid_cache()


def test_ask_retry_success_and_block():
    from scourgify.classify import ask_retry
    class OK:
        def ask(self, p): return "yes"
    assert ask_retry(OK(), "x") == ("yes", "")

    class Blocked:
        def ask(self, p): raise RuntimeError("blocked:PROHIBITED")
    out, err = ask_retry(Blocked(), "x")
    # the reason now carries its normalized class as a prefix (engines.failure_class reads it back);
    # only a refusal earns the GUI's "Retry on <other engine>", so the class has to survive the CSV
    from scourgify import engines
    assert out == "" and engines.failure_class(err) == engines.REFUSAL and "blocked:" in err

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
        classify.ENGINES["mistral"]("", 60); assert False, "expected a refusal"
    except common.GuardrailError as e:
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
    assert n == {"promote": 1, "alias": 1, "reject": 1, "skipped": 0}
    assert "Gacha Mechanic" in open(vocab).read()
    # fresh override files are comma-delimited (the overrides.py owner's default; appends to a
    # legacy ';' file would sniff and keep ';' — see test_overrides_append_honors_delimiter)
    trows = list(csv.reader(open(tropes)))
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
    old = os.environ.get("SCOURGIFY_HOME"); os.environ["SCOURGIFY_HOME"] = d   # overrides resolve under user_dir()
    classify.clear_caches()
    try:
        vt, nt = classify.parse_resp('{"tags": [], "new": ["Post-Apocalyptic"]}')
        assert "Angst" in vt          # snapped to the aliased vocab term, applied
        assert "Post-Apocalyptic" not in nt
    finally:
        os.environ.pop("SCOURGIFY_HOME", None) if old is None else os.environ.__setitem__("SCOURGIFY_HOME", old)
        classify.clear_caches()


def test_promote_run_writes_review(tmp=None):
    import os, csv, tempfile
    from scourgify import promote
    d = tempfile.mkdtemp()
    ranked = os.path.join(d, "r.csv"); prop = os.path.join(d, "p.csv")
    with open(ranked, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["proposed_tag", "count"]); w.writerow(["Reality Warping", "3"])
    with open(prop, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["book_id", "title", "added_tags", "proposed_new"]); w.writerow(["1", "Bend It", "", "Reality Warping"])
    review = os.path.join(d, "promote_review.csv")
    # candidates()'s default ledger_path (run() takes no ledger_path of its own) needs a
    # resolvable library now that data_dir() is uuid-scoped.
    with _fixture_library_env(d, "uuid-promote-run"):
        a = promote.build_parser().parse_args(["--yes"])
        # ask is an injected callable — the same seam decide() has; no engine registry to fake
        promote.run(a, ranked_path=ranked, proposal_path=prop, review_path=review, existing=["Time Travel", "Fluff"],
                    ask=lambda p: '{"verdict":"promote","reason":"novel reusable trope","confidence":"high"}')
        rows = list(csv.DictReader(open(review)))
        assert len(rows) == 1 and rows[0]["tag"] == "Reality Warping" and rows[0]["verdict"] == "promote"


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
    assert n == {"promote": 1, "alias": 0, "reject": 0, "skipped": 1}   # the bogus row is COUNTED, not silent


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
    from scourgify import promote
    fake_ask = lambda p: '{"verdict":"promote","reason":"novel","confidence":"high"}'
    d = tempfile.mkdtemp()
    ranked = os.path.join(d, "r.csv"); prop = os.path.join(d, "p.csv")
    with open(ranked, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["proposed_tag", "count"]); w.writerow(["Ghost Bond", "2"])
    with open(prop, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["book_id", "title", "added_tags", "proposed_new"])
    review = os.path.join(d, "promote_review.csv")
    # pre-create the review file to simulate a pending review
    with open(review, "w") as f: f.write("existing content")
    # without --yes, should refuse
    a = promote.build_parser().parse_args([])
    raised = False
    try:
        promote.run(a, ranked_path=ranked, proposal_path=prop, review_path=review, ask=fake_ask)
    except common.GuardrailError as e:
        raised = True
        assert "pending review" in str(e)
    assert raised, "expected a refusal when review file exists and --yes not set"
    # with --yes, should overwrite without error. prop has no rows, so candidates() is empty —
    # but candidates() still resolves its default ledger_path first, which needs a library now.
    a2 = promote.build_parser().parse_args(["--yes"])
    with _fixture_library_env(d, "uuid-run-raises"):
        # candidates list is empty (prop has no rows), so run exits early with "nothing to do"
        promote.run(a2, ranked_path=ranked, proposal_path=prop, review_path=review, ask=fake_ask)


def test_decide_downgrades_self_and_unknown_alias():
    from scourgify.promote import decide
    cand = {"tag": "Amoral Deity", "count": 1, "examples": ["a cruel god"]}
    existing = ["Morality", "Deity Worship", "Dark"]
    # self-alias (target == candidate) -> downgraded to reject, not written
    d = decide(cand, lambda p: '{"verdict":"alias","target":"Amoral Deity","reason":"same"}', existing=existing)
    assert d["verdict"] == "reject" and d["target"] == "" and "unverified alias target" in d["reason"]
    # alias to a target that isn't in the master list -> reject
    d2 = decide(cand, lambda p: '{"verdict":"alias","target":"Public Sex","reason":"?"}', existing=existing)
    assert d2["verdict"] == "reject"
    # alias to a real master (different casing) -> kept, normalized to canonical spelling
    d3 = decide(cand, lambda p: '{"verdict":"alias","target":"morality","reason":"same idea"}', existing=existing)
    assert d3["verdict"] == "alias" and d3["target"] == "Morality"


def test_apply_skips_hand_edited_self_alias():
    import csv, os, tempfile
    from scourgify.promote import apply_decisions
    d = tempfile.mkdtemp()
    review = os.path.join(d, "review.csv"); vocab = os.path.join(d, "v.txt")
    tropes = os.path.join(d, "t.csv"); aliases = os.path.join(d, "a.csv"); ledger = os.path.join(d, "l.csv")
    with open(review, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["tag", "count", "verdict", "target", "reason", "confidence", "contested"])
        w.writerow(["Self Ref", "1", "alias", "Self Ref", "oops", "low", "False"])   # self-alias
        w.writerow(["Empty Tgt", "1", "alias", "", "oops", "low", "False"])          # empty target
    apply_decisions(review, vocab, tropes, aliases, ledger)
    assert not os.path.exists(tropes) and not os.path.exists(aliases)   # nothing junk written
    assert not os.path.exists(ledger) or "Self Ref" not in open(ledger).read()


def test_decide_transport_failure_is_not_a_reject():
    """ask_retry returns ("", err) on a transport failure — and RuntimeError (Gemini's empty-parts
    case) gets zero retries. A reject would be ledgered and the candidate skipped forever, so a
    no-response must stay undecided. "error" is outside VERDICTS, which is what makes
    apply_decisions skip it without ledgering (see test_apply_decisions_normalizes_verdict)."""
    from scourgify.promote import decide, VERDICTS
    d = decide({"tag": "Slow Burn", "count": 9}, lambda p: "", existing=["Fluff"])
    assert d["verdict"] == "error" and d["verdict"] not in VERDICTS
    assert d["tag"] == "Slow Burn" and not d["target"]


def test_backfill_skips_tags_already_in_a_structured_column():
    """backfill and wrangle used to fight: backfill added a tag the book already carried in
    #genres, wrangle stripped it as redundant (backfill-before-strip), backfill re-added it —
    forever. Observed live on 4 books. backfill must not propose what wrangle will strip."""
    from scourgify.promote import backfill_drop_redundant
    homes = {1: {"alternate universe", "fantasy"}, 2: set()}
    adds = {1: {"Alternate Universe", "Time Loop"}, 2: {"Fantasy"}}
    kept = backfill_drop_redundant(adds, homes)
    assert kept == {1: {"Time Loop"}, 2: {"Fantasy"}}     # book 1 loses only the redundant one


def test_backfill_drop_redundant_removes_a_book_left_with_nothing():
    from scourgify.promote import backfill_drop_redundant
    assert backfill_drop_redundant({1: {"Fantasy"}}, {1: {"fantasy"}}) == {}


def test_backfill_drops_a_tag_wrangle_renames_or_junk_drops():
    """The other half of the same fight: a trope rename (X -> Y) or a junk-drop leaves the tag in
    NO column, so `homes` can't see it and backfill re-added X forever — the wizard's 'N books to
    backfill' hint outliving every backfill. Observed live 2026-07-28."""
    from scourgify.promote import backfill_drop_unstable
    adds = {1: {"Soul Bonded", "Time Loop"}, 2: {"Soul Bonded"}}
    assert backfill_drop_unstable(adds, {"Time Loop"}) == {1: {"Time Loop"}}   # book 2 drops out entirely


def test_wrangle_stable_rejects_a_renamed_tag(tmp=None):
    """wrangle_stable goes through the real transform(), so it can't desync from the maps."""
    import tempfile, os
    from scourgify.promote import wrangle_stable
    with tempfile.TemporaryDirectory() as d:
        old = os.environ.get("SCOURGIFY_HOME"); os.environ["SCOURGIFY_HOME"] = d
        try:
            os.makedirs(os.path.join(d, "overrides"))
            with open(os.path.join(d, "overrides", "tropes.csv"), "w") as f:
                f.write("variant,canonical,route\nSoul Bonded,Soul Bond,tag\n")
            keep = wrangle_stable({"Soul Bonded", "Soul Bond"})
            assert "Soul Bonded" not in keep, keep      # wrangle renames it -> never a backfill target
            assert "Soul Bond" in keep, keep            # the terminal survives
        finally:
            os.environ.pop("SCOURGIFY_HOME", None) if old is None else os.environ.__setitem__("SCOURGIFY_HOME", old)


def test_apply_decisions_counts_rows_it_could_not_decide():
    """An 'error' verdict / empty alias target writes no ledger row on purpose, so the candidate
    stays pending. apply must SAY so — else it reports success and the wizard's hint survives the
    apply with nothing on screen explaining why."""
    import tempfile, os
    from scourgify import promote, artifacts
    with tempfile.TemporaryDirectory() as d, _fixture_library_env(d, "uuid-apply-decisions"):
        os.makedirs(common.data_dir()); os.makedirs(os.path.join(d, "home", "overrides"))
        with open(artifacts.review(), "w") as f:
            f.write("tag,count,verdict,target,reason,confidence,contested\n"
                    "Good,3,promote,,ok,high,False\n"
                    "Flaky,2,error,,transport failure,low,False\n"
                    "Bad,1,alias,,empty target,low,False\n")
        n = promote.apply_decisions()
        assert n["promote"] == 1 and n["skipped"] == 2, n


def test_backfill_drop_redundant_is_case_and_punctuation_insensitive():
    """The strip wrangle performs is norm()-based, so the guard has to match on norm too or the
    loop comes straight back for 'Sci-Fi' vs 'sci fi'."""
    from scourgify.promote import backfill_drop_redundant
    from scourgify.common import norm
    assert backfill_drop_redundant({1: {"Sci-Fi"}}, {1: {norm("sci fi")}}) == {}


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
