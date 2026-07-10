#!/usr/bin/env python3
"""Regression tests for the pure core — the functions where a data-loss bug would live.
No framework needed:  uv run tests/test_core.py   (also collectable by pytest).
No Calibre, no library, no network."""
import os, sys, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from scourgify import common
from scourgify.common import norm, ascii_fold, load_config
from scourgify.wrangle import (transform, resolve_trope_chains, build_tagcanon, is_junk,
                               tag_loss_guard, data_loss_guard,
                               TAG_SHRINK_FLOOR, TAG_SHRINK_FRACTION)
from scourgify.classify import parse_resp, sparkline, load_vocab
VOCAB = load_vocab()
from scourgify.staleness import derive

BEH = load_config(path="/nonexistent/config.toml")["behavior"]     # shipped defaults

def maps(**over):
    m = {"char": {}, "char_fd": {}, "fan": {}, "fanvals": set(), "trope": {}, "fan_block": set(),
         "decompose": {}, "gsplit": {}, "gcanon": {}, "gallow": set(), "rating": set(),
         "junk_exact": set(), "junk_rx": []}
    m.update(over)
    m.setdefault("fanvals", {norm(v) for v in m["fan"].values()})
    return m


def test_norm():
    assert norm(" Harry  Potter ") == "harry potter"
    assert norm("Hurt/Comfort") == "hurt comfort"
    assert norm("A&B [x] (y)") == "aandb x y"

def test_ascii_fold():
    assert ascii_fold("café’s — test…") == "cafe's - test..."

def test_resolve_trope_chains():
    r = resolve_trope_chains({"A": ("B", "tag"), "B": ("C", "tag")})
    assert r["A"] == ("C", "tag") and r["B"] == ("C", "tag")          # chain follows to terminal
    r = resolve_trope_chains({"A": ("B", "tag"), "B": ("A", "genre")})
    assert r["A"][0] == "A" and r["B"][0] == "A"                       # cycle breaks deterministically (min)

def test_transform_fandom_alias():
    nd, lf, lc = transform({"fandoms": ["HP"]}, maps(fan={"HP": "Harry Potter"}), BEH)
    assert nd["fandoms"] == ["Harry Potter"] and not lf and not lc

def test_transform_blocklisted_fandom_routes_to_tags():
    m = maps(fan={}, fan_block={norm("Explicit")})
    nd, lf, _ = transform({"fandoms": ["Explicit"]}, m, BEH)
    assert nd["fandoms"] == [] and "Explicit" in nd["tags"] and not lf   # intentional, not a loss

def test_transform_character_fold():
    m = maps(char={"Harry P.": "Harry Potter"})
    nd, _, lc = transform({"characters": ["Harry P."]}, m, BEH)
    assert nd["characters"] == ["Harry Potter"] and not lc

def test_transform_character_fold_is_case_insensitive():
    # load_maps adds norm-keyed aliases; a book value that differs only in casing/punctuation still folds
    m = maps(char={"harry p": "Harry Potter"})                 # norm-keyed (as the alias load_maps adds)
    nd, _, lc = transform({"characters": ["HARRY P."]}, m, BEH)
    assert nd["characters"] == ["Harry Potter"] and not lc

def test_transform_trope_fold_is_case_insensitive():
    m = maps(trope={"fix it": ("Fix-It", "tag")})              # norm-keyed
    nd, _, _ = transform({"tags": ["Fix It!"]}, m, BEH)
    assert "Fix-It" in nd["tags"]

def test_load_maps_adds_norm_aliases_for_char_and_trope():
    from scourgify.wrangle import load_maps
    m = load_maps(load_config(path="/nonexistent/config.toml"))
    for name in ("char", "trope"):
        raw = next(k for k in list(m[name]) if k != norm(k))   # a key not already in normal form
        assert norm(raw) in m[name]                            # ... has a norm-keyed alias

def test_transform_genre_split_and_allowlist():
    m = maps(gsplit={"Action/Adventure": ["Action", "Adventure"]}, gallow={"action", "adventure"})
    nd, _, _ = transform({"genres": ["Action/Adventure", "Fluffy"]}, m, BEH)
    assert nd["genres"] == ["Action", "Adventure"]
    assert "Fluffy" in nd["tags"]                                      # not allowlisted -> tag

def test_transform_genre_misfiled_character_rescued():
    nd, _, _ = transform({"genres": ["Akeno Himejima"]}, maps(), BEH, known_chars={norm("Akeno Himejima")})
    assert nd["characters"] == ["Akeno Himejima"] and nd["genres"] == []

def test_transform_junk_drop_and_redundancy_strip():
    m = maps(junk_exact={"complete"})
    d = {"fandoms": ["Harry Potter"], "tags": ["Complete", "Harry Potter", "Keeper"]}
    nd, _, _ = transform(d, m, BEH)
    assert "Complete" not in nd["tags"]                                # junk dropped
    assert "Harry Potter" not in nd["tags"]                            # already in #fandoms -> stripped
    assert "Keeper" in nd["tags"]

def test_transform_trope_route_respects_genre_allowlist():
    m = maps(trope={"fixit": ("Fix-It", "genre")})
    nd, _, _ = transform({"tags": ["fixit"]}, m, BEH)
    assert "Fix-It" in nd["tags"] and "Fix-It" not in nd["genres"]     # genre route, not allowlisted -> tag
    m = maps(trope={"fixit": ("Fix-It", "genre")}, gallow={norm("Fix-It")})
    nd, _, _ = transform({"tags": ["fixit"]}, m, BEH)
    assert nd["genres"] == ["Fix-It"]

def test_transform_decompose():
    m = maps(decompose={norm("Fate SI"): {"fandoms": ["Type-Moon"], "characters": [], "tags": ["SI/OC"], "genres": []}})
    nd, lf, _ = transform({"fandoms": ["Fate SI"]}, m, BEH)
    assert nd["fandoms"] == ["Type-Moon"] and "SI/OC" in nd["tags"] and not lf

def test_transform_decompose_empty_alias_never_injects_blank_fandom():
    m = maps(fan={"Ghost": ""},
             decompose={norm("Ghost SI"): {"fandoms": ["Ghost"], "characters": [], "tags": [], "genres": []}})
    nd, _, _ = transform({"tags": ["Ghost SI"]}, m, BEH)
    assert "" not in nd["fandoms"]

def test_transform_ascii_and_tagcanon():
    nd, _, _ = transform({"tags": ["café", "time-travel"]}, maps(), BEH,
                         tagcanon={norm("time travel"): "Time Travel"})
    assert "cafe" in nd["tags"] and "Time Travel" in nd["tags"]

def test_transform_routed_genre_goes_through_tag_pipeline():
    # a genre outside the allowlist routes to tags AND trope-folds there — no raw/canonical duplicates
    m = maps(trope={"Overpowered Protagonist": ("Overpowered", "tag")}, gallow={"fantasy"})
    d = {"fandoms": ["X"], "characters": [], "genres": ["Fantasy", "Overpowered Protagonist"], "tags": []}
    nd, _, _ = transform(d, m, BEH, set(), {})
    assert nd["genres"] == ["Fantasy"]
    assert nd["tags"] == ["Overpowered"]                               # folded, not duplicated

def test_build_tagcanon_majority_spelling():
    tc = build_tagcanon(["Time Travel", "Time Travel", "time-travel"], maps())
    assert tc[norm("time travel")] == "Time Travel"

def test_is_junk_regex():
    import re
    m = maps(junk_rx=[re.compile(r"no beta", re.I)])
    assert is_junk("No Beta we die like men", m) and not is_junk("betamax", m)

def test_parse_resp_vocab_intersection():
    vt, nt = parse_resp('{"tags": ["%s", "Not In Vocab"], "new": ["Sentient Toaster Romance"]}' % VOCAB[0])
    assert vt == [VOCAB[0]] and nt == ["Sentient Toaster Romance"]

def test_parse_resp_rejects_list_echo_and_garbage():
    echo = '{"tags": %s, "new": []}' % str(VOCAB[:15]).replace("'", '"')
    assert parse_resp(echo)[0] == []                                   # echoed the list -> discard
    assert parse_resp("not json at all") == ([], [])

def test_parse_resp_rejects_formula_injection():
    _, nt = parse_resp('{"tags": [], "new": ["=SUM(A1:A9)", "+curse", "@cmd", "Sentient Toaster Romance"]}')
    assert nt == ["Sentient Toaster Romance"]

def test_parse_resp_null_arrays_dont_crash():
    # a model may emit `null` instead of `[]`; must degrade to empty, not raise TypeError (whole-run crash)
    assert parse_resp('{"tags": null, "new": null}') == ([], [])
    assert parse_resp('{"tags": null, "new": ["Sentient Toaster Romance"]}') == ([], ["Sentient Toaster Romance"])

def test_parse_resp_snaps_near_miss_into_vocab():
    assert "Slow Burn" in VOCAB and "Enemies to Lovers" in VOCAB       # guard: test data still valid
    vt, nt = parse_resp('{"tags": [], "new": ["Slow-Burn", "Enemies-To-Lovers", "Sentient Toaster Romance"]}')
    assert "Slow Burn" in vt and "Enemies to Lovers" in vt             # hyphen/case variants snap to canonical spelling
    assert nt == ["Sentient Toaster Romance"]                          # genuinely novel still surfaces as new

def test_annotate_new_verdict_split():
    import collections
    from scourgify.classify import annotate_new
    ranked = collections.Counter({"Slow-Burn": 5, "Dragon Politics": 2})
    rows = annotate_new(ranked, cutoff=0.86, existing=["Slow Burn", "Time Travel"])
    by = {r[0]: r for r in rows}
    assert by["Slow-Burn"][4] == "near-duplicate" and by["Slow-Burn"][2] == "Slow Burn"   # variant -> nearest existing
    assert by["Dragon Politics"][4] == "new"                          # no close match -> genuinely new
    assert rows[0][4] == "new"                                        # new sorted ahead of near-duplicates

def test_usable_engines_reflects_env_keys():
    from scourgify.classify import usable_engines, ENGINE_ENV
    saved = {k: os.environ.get(k) for keys in ENGINE_ENV.values() for k in keys}
    try:
        for keys in ENGINE_ENV.values():
            for k in keys: os.environ.pop(k, None)
        assert "claude" not in usable_engines()                        # no key -> engine not offered
        os.environ["ANTHROPIC_API_KEY"] = "sk-test"
        assert "claude" in usable_engines()                            # key present -> offered
    finally:
        for keys in ENGINE_ENV.values():
            for k in keys: os.environ.pop(k, None)
        for k, v in saved.items():
            if v is not None: os.environ[k] = v

def test_sparkline():
    assert sparkline([]) == ""
    assert sparkline([0, 0]) == "▁▁"                                   # flat zero series doesn't divide by zero
    s = sparkline([1, 4, 8])
    assert len(s) == 3 and s[-1] == "█" and s[0] < s[1] < s[2]         # monotonic input -> monotonic blocks
    assert len(sparkline(list(range(100)), width=28)) == 28            # windowed to the last `width` points

def test_staleness_derive():
    assert derive("Completed", 10.0, 2, 5) == "Completed"              # final statuses never touched
    assert derive("In-Progress", None, 2, 5) == "In-Progress"          # no date -> unchanged
    assert derive("In-Progress", 1.0, 2, 5) == "In-Progress"
    assert derive("In-Progress", 3.0, 2, 5) == "Hiatus"
    assert derive("Hiatus", 6.0, 2, 5) == "Abandoned"
    assert derive("Abandoned", 1.0, 2, 5) == "In-Progress"             # self-correcting on refresh

def test_load_config_toml_reader():
    with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
        f.write('[columns]\ntags = "#my tags"  # value contains a quote-protected hash\n'
                '[behavior]  # trailing comment on header\nfold_characters = false\n')
        p = f.name
    cfg = load_config(path=p); os.unlink(p)
    assert cfg["columns"]["tags"] == "#my tags"
    assert cfg["behavior"]["fold_characters"] is False
    assert cfg["behavior"]["ascii_only_tags"] is True                  # untouched default survives

def test_vocab_overrides_merge():
    from scourgify import classify
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "overrides"))
        with open(os.path.join(td, "overrides", "classify_vocab.txt"), "w") as f:
            f.write("# my terms\nSentient Toaster Romance\n-Time Travel\n")
        old = os.getcwd(); os.chdir(td); classify._VOCAB = None       # vocab is cached per CWD
        try:
            v = classify.load_vocab()
            assert "Sentient Toaster Romance" in v                     # appended
            assert "Time Travel" not in v                              # '-term' removed a bundled term
        finally:
            os.chdir(old); classify._VOCAB = None

def test_est_cost():
    from scourgify.classify import est_cost
    assert est_cost(100, "apple") == 0.0                               # on-device is free
    assert 0 < est_cost(100, "gemini") < est_cost(100, "claude")       # scales with list price
    assert est_cost(200, "gemini") == 2 * est_cost(100, "gemini")      # linear in books


# ---- 1-by-1 step review: reconstruct + rejects → overrides ----
def test_reconstruct_reverts_only_rejected():
    from scourgify.overrides import reconstruct
    nd = {"#fandoms": ["Naruto (anime)"]}; orig = {"#fandoms": ["Naruto"]}
    rename = ("rename", "#fandoms", "Naruto", "Naruto (anime)")
    assert reconstruct(nd, orig, []) == {"#fandoms": ["Naruto (anime)"]}   # accept all -> full change stands
    assert reconstruct(nd, orig, [rename]) == {}                           # reject -> back to original, nothing to write
    # drop / add invert to the original too
    assert reconstruct({"tags": []}, {"tags": ["WIP"]}, [("drop", "tags", "WIP", "")]) == {}
    assert reconstruct({"tags": ["Time Travel"]}, {"tags": []}, [("add", "tags", "", "Time Travel")]) == {}
    # move back across columns
    assert reconstruct({"#genres": [], "tags": ["Fluffy"]}, {"#genres": ["Fluffy"], "tags": []},
                       [("move", "#genres → tags", "Fluffy", "")]) == {}

def test_reconstruct_keeps_accepted_alongside_rejected():
    from scourgify.overrides import reconstruct
    nd = {"#fandoms": ["Naruto (anime)"], "tags": []}
    orig = {"#fandoms": ["Naruto"], "tags": ["WIP"]}
    # reject the fandom rename, keep the tag drop
    net = reconstruct(nd, orig, [("rename", "#fandoms", "Naruto", "Naruto (anime)")])
    assert net == {"tags": []}                                            # fandom reverted (==orig, unwritten); drop kept

def test_synth_reject_auto_kinds():
    from scourgify.overrides import synth_reject
    assert synth_reject("fandoms", "rename", "Naruto", "Naruto (anime)") == ("auto", [("fandoms.csv", "Naruto,Naruto")], "")
    assert synth_reject("characters", "rename", "Harry P.", "Harry Potter") == ("auto", [("characters.csv", "Harry P.,Harry P.,")], "")
    cls, actions, _ = synth_reject("genres", "rename", "Angsty", "Angst")
    assert cls == "auto" and ("genres_canon.csv", "Angsty,Angsty") in actions and ("genres_allow.txt", "Angsty") in actions
    assert synth_reject("tags", "rename", "fixit", "Fix-It") == ("auto", [("tropes.csv", "fixit,fixit,tag")], "")
    assert synth_reject("genres", "move", "Fluffy", "", dest="tags") == ("auto", [("genres_allow.txt", "Fluffy")], "")

def test_synth_reject_manual_kinds():
    from scourgify.overrides import synth_reject
    assert synth_reject("tags", "drop", "WIP", "")[0] == "manual"             # junk/redundancy drop
    assert synth_reject("tags", "add", "", "Time Travel")[0] == "manual"      # injected value
    assert synth_reject("characters", "move", "Akeno", "", dest="tags")[0] == "manual"   # cross-column rescue

def test_promote_backfill_targets():
    from scourgify.promote import resolve_ledger, backfill_wanted
    res = resolve_ledger([
        {"tag": "Gacha System", "verdict": "promote", "target": ""},
        {"tag": "Isekai'd", "verdict": "alias", "target": "Isekai"},
        {"tag": "Plot Specific", "verdict": "reject", "target": ""},   # rejects don't backfill
        {"tag": "", "verdict": "promote", "target": ""},               # blank ignored
    ])
    assert res == {"gacha system": "Gacha System", "isekai'd": "Isekai"}
    want = backfill_wanted(res, [
        {"book_id": "10", "proposed_new": "Gacha System; Plot Specific"},
        {"book_id": "11", "proposed_new": "Isekai'd; Unrelated"},
        {"book_id": "10", "proposed_new": "Isekai'd"},                 # accumulates across proposal files
        {"book_id": "bad", "proposed_new": "Gacha System"},           # unparseable id skipped
    ])
    assert want == {10: {"Gacha System", "Isekai"}, 11: {"Isekai"}}   # promoted->itself, aliased->target

def test_synth_identity_override_is_a_noop_in_transform():
    # the round-trip that the whole `overrides` design rests on: an identity map cancels the fold.
    def flatten(fan):                                    # mirrors load_maps' chain-flattening
        for a in list(fan):
            t, seen = fan[a], {a}
            while t in fan and t not in seen: seen.add(t); t = fan[t]
            fan[a] = t
        return fan
    fan = {"Naruto": "Naruto (anime)"}
    assert transform({"fandoms": ["Naruto"]}, maps(fan=dict(fan)), BEH)[0]["fandoms"] == ["Naruto (anime)"]
    fan.update({"Naruto": "Naruto"})                     # override loads LAST -> identity wins
    assert transform({"fandoms": ["Naruto"]}, maps(fan=flatten(fan)), BEH)[0]["fandoms"] == ["Naruto"]
    char = {"Harry P.": "Harry Potter"}; char.update({"Harry P.": "Harry P."})
    assert transform({"characters": ["Harry P."]}, maps(char=char), BEH)[0]["characters"] == ["Harry P."]


# ---- SAFETY guardrails (pure; CLAUDE.md invariants) ----
def test_tag_loss_guard_ceiling():
    # just under the ceiling: never aborts (need BOTH >floor lost AND >fraction shrink)
    tag_loss_guard(1000, 1000 - TAG_SHRINK_FLOOR, force=False)          # exactly floor lost -> ok
    tag_loss_guard(100, 0, force=False)                                 # 100% shrink but < floor lost -> ok
    # over the ceiling: aborts
    try:
        tag_loss_guard(1000, 1000 - (TAG_SHRINK_FLOOR + int(1000 * TAG_SHRINK_FRACTION)), force=False)
        assert False, "expected SystemExit on mass tag deletion"
    except SystemExit:
        pass
    # --force overrides, and tags_before == 0 is a no-op
    tag_loss_guard(1000, 0, force=True)
    tag_loss_guard(0, 0, force=False)

def test_data_loss_guard_aborts_on_last_value_lost():
    data_loss_guard(0, 0, force=False)                                  # nothing lost -> ok
    for lf, lc in ((1, 0), (0, 1), (2, 3)):
        try:
            data_loss_guard(lf, lc, force=False)
            assert False, f"expected SystemExit for lost_fandom={lf} lost_char={lc}"
        except SystemExit as e:
            assert "lose their last" in str(e)
        data_loss_guard(lf, lc, force=True)                            # --force overrides the abort

def test_transform_flags_a_book_that_loses_its_last_fandom():
    # the whole point of the guard: a fanfic always has a fandom, so ending with zero is a loss.
    _, lf, _ = transform({"fandoms": ["Naruto"]}, maps(fan={"Naruto": ""}), BEH)   # alias -> "" (typo)
    assert lf is True
    m = maps(decompose={norm("Fate SI"): {"fandoms": [], "characters": [], "tags": [], "genres": []}})
    _, lf, _ = transform({"fandoms": ["Fate SI"]}, m, BEH)                          # decompose -> empty payload
    assert lf is True

def test_transform_does_not_flag_relocation_or_normal_routing():
    # a blocklisted non-fandom moved to tags is preserved -> NOT a loss
    _, lf, _ = transform({"fandoms": ["Explicit"]}, maps(fan_block={norm("Explicit")}), BEH)
    assert lf is False
    # a plain rename keeps the fandom -> NOT a loss
    _, lf, _ = transform({"fandoms": ["HP"]}, maps(fan={"HP": "Harry Potter"}), BEH)
    assert lf is False
    # a book with no fandom to begin with can't lose one
    _, lf, _ = transform({"tags": ["WIP"]}, maps(), BEH)
    assert lf is False


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
