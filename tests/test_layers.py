#!/usr/bin/env python3
"""Data-layer precedence and shipped-layer well-formedness.
No framework needed:  uv run tests/test_layers.py   (also collectable by pytest).
No Calibre, no library, no network."""
import csv, os, sys, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from scourgify import wrangle

AO3_DIR = os.path.join(os.path.dirname(wrangle.__file__), "defaults", "ao3")


def _write(path, header, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(header); w.writerows(rows)


def test_layer_precedence_ao3_defaults_overrides():
    """ao3 layer loads first; curated defaults beat it; user overrides beat everything."""
    with tempfile.TemporaryDirectory() as td:
        dflt, over = os.path.join(td, "defaults"), os.path.join(td, "overrides")
        _write(f"{dflt}/ao3/universes.csv", ["master", "name", "rel"],
               [["AO3 Master", "Alias A", "alias"], ["AO3 Master", "Alias B", "alias"]])
        _write(f"{dflt}/ao3/tags.csv", ["master", "name", "rel"],
               [["Fantasy", "Fantazy", "alias"], ["Slow Burn", "Slowburn", "alias"]])
        _write(f"{dflt}/ao3/genres.csv", ["master", "name", "rel"], [["Action", "Aksion", "alias"]])
        _write(f"{dflt}/ao3/characters.csv", ["master", "name", "rel"], [["Canon Name", "Nickname", "alias"]])
        _write(f"{dflt}/fandoms.csv", ["alias", "canonical"], [["Alias B", "Curated Master"]])
        _write(f"{dflt}/tropes.csv", ["variant", "canonical", "route"], [["Slowburn", "Slow Build", "tag"]])
        open(f"{dflt}/genres_allow.txt", "w").write("Action\nFantasy\n")
        _write(f"{over}/fandoms.csv", ["alias", "canonical"], [["Alias A", "User Master"]])

        old_def, old_home = wrangle.DEF, os.environ.get("SCOURGIFY_HOME")
        wrangle.DEF = dflt; os.environ["SCOURGIFY_HOME"] = td   # overrides/ resolves under user_dir()
        try:
            m = wrangle.load_maps({"columns": {}, "behavior": {}, "overrides": {"dir": "overrides"}})
        finally:
            wrangle.DEF = old_def
            os.environ.pop("SCOURGIFY_HOME", None) if old_home is None else os.environ.__setitem__("SCOURGIFY_HOME", old_home)

        assert m["fan"]["Alias A"] == "User Master"          # override beats ao3
        assert m["fan"]["Alias B"] == "Curated Master"       # curated default beats ao3
        assert m["char"]["Nickname"] == "Canon Name"         # ao3 character folds load
        assert m["trope"]["Fantazy"] == ("Fantasy", "genre")     # allowlisted canonical -> genre route
        assert m["trope"]["Slowburn"] == ("Slow Build", "tag")   # curated trope beats ao3 fold
        assert m["gcanon"]["Aksion"] == "Action"             # ao3 genre synonyms feed gcanon


def test_loader_tolerates_missing_ao3_layer():
    with tempfile.TemporaryDirectory() as td:
        dflt = os.path.join(td, "defaults")
        os.makedirs(dflt)
        open(f"{dflt}/genres_allow.txt", "w").write("Action\n")
        old_def, old_home = wrangle.DEF, os.environ.get("SCOURGIFY_HOME")
        wrangle.DEF = dflt; os.environ["SCOURGIFY_HOME"] = td
        try:
            m = wrangle.load_maps({"columns": {}, "behavior": {}, "overrides": {"dir": "overrides"}})
        finally:
            wrangle.DEF = old_def
            os.environ.pop("SCOURGIFY_HOME", None) if old_home is None else os.environ.__setitem__("SCOURGIFY_HOME", old_home)
        assert m["fan"] == {} and m["trope"] == {}           # no layer, no crash


def test_shipped_ao3_layer_well_formed():
    """The committed generated files: correct headers, no self-rows, no duplicate names per file."""
    if not os.path.isdir(AO3_DIR):
        print("  (ao3 layer not generated — skipping)"); return
    for fn in os.listdir(AO3_DIR):
        rows = list(csv.DictReader(open(os.path.join(AO3_DIR, fn))))
        assert rows and set(rows[0]) == {"master", "name", "rel"}, fn
        names = [r["name"] for r in rows]
        assert len(names) == len(set(names)), f"duplicate variant names in {fn}"
        assert all(r["name"] != r["master"] for r in rows), f"self-row in {fn}"


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
