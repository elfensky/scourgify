#!/usr/bin/env python3
"""Regression tests for the pure core — the functions where a data-loss bug would live.
No framework needed:  uv run tests/test_core.py   (also collectable by pytest).
No Calibre, no library, no network."""
import os, sys, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from scourgify import common
from scourgify.common import norm, ascii_fold, load_config
from scourgify.wrangle import transform, resolve_trope_chains, build_tagcanon, is_junk
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


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
