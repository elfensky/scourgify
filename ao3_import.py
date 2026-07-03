#!/usr/bin/env python3
"""Mine AO3's official tag dump for YOUR library — generate review candidates for overrides/.

    uv run ao3_import.py [--dump ~/Downloads/20210226-stats/tags-20210226.csv]
    uv run ao3_import.py --selftest

Streams the OTW "Selective data dump for fan statisticians" (2021-02-26; ~14.5M rows:
id,type,name,canonical,cached_count,merger_id) twice, intersects it with the fandom/
character/tag values actually present in the CALIBRE_LIBRARY, and writes REVIEW files
to data/ (never straight into overrides/ — audit-first):

  ao3_fandoms.csv     variant,canonical,note   library fandoms that are AO3 aliases (or off-spellings)
  ao3_characters.csv  variant,canonical,note   same for characters — REVIEW: global folds, no fandom scoping
  ao3_tropes.csv      variant,canonical,uses   library tags that are aliases of canonical AO3 freeforms
  ao3_vocab.csv       name,uses                library tags confirmed as canonical AO3 freeforms (vocab candidates)
  ao3_unknown.csv     kind,value               fandoms/characters AO3 has never heard of (post-2021 or personal)

Adopt by appending reviewed rows to overrides/fandoms.csv / characters.csv / tropes.csv
(same variant,canonical[,route] formats) — wrangle previews everything before writing anyway."""
import argparse, csv, os, sys

from scourgify.common import DATA, norm, ro_connect, read_custom_column

TYPES = {"Fandom": "fandom", "Character": "character", "Freeform": "tag"}


def library_values(con):
    """{kind: {norm: [original spellings...]}} for the values actually in the library."""
    out = {k: {} for k in TYPES.values()}
    for label, kind in (("#fandoms", "fandom"), ("#characters", "character")):
        for vals in (read_custom_column(con, label, multi=True) or {}).values():
            for v in vals: out[kind].setdefault(norm(v), set()).add(v)
    for (t,) in con.execute("SELECT name FROM tags"):
        out["tag"].setdefault(norm(t), set()).add(t)
    return out


def match_dump(rows, lib):
    """rows: iterable of dump dicts. -> (validated, aliases, need_ids)
    validated[kind][norm] = (ao3_name, uses) for canonical rows matching a library value.
    aliases[kind][norm]   = merger_id       for alias rows matching a library value."""
    validated = {k: {} for k in TYPES.values()}
    aliases = {k: {} for k in TYPES.values()}
    for r in rows:
        kind = TYPES.get(r["type"])
        if not kind: continue
        name = r["name"]
        if not name or name == "Redacted": continue
        n = norm(name)
        if n not in lib[kind]: continue
        if r["canonical"] == "true":
            validated[kind].setdefault(n, (name, int(r["cached_count"] or 0)))
        elif r["merger_id"]:
            aliases[kind].setdefault(n, r["merger_id"])
    need = {mid for byk in (aliases[k] for k in aliases) for mid in byk.values()}
    return validated, aliases, need


def resolve_ids(rows, need_ids):
    """Second stream: {id: (name, uses)} for the canonical merger targets we actually need."""
    out = {}
    for r in rows:
        if r["id"] in need_ids and r["canonical"] == "true":
            out[r["id"]] = (r["name"], int(r["cached_count"] or 0))
    return out


def emit(lib, validated, aliases, canon_by_id):
    os.makedirs(DATA, exist_ok=True)
    def w(fn, header, data):
        p = os.path.join(DATA, fn)
        with open(p, "w", newline="") as f:
            cw = csv.writer(f); cw.writerow(header); cw.writerows(data)
        print(f"  {fn:20} {len(data):6} rows")
        return len(data)

    for kind, fn in (("fandom", "ao3_fandoms.csv"), ("character", "ao3_characters.csv")):
        rows = []
        for n, mid in sorted(aliases[kind].items()):
            if mid not in canon_by_id: continue                    # merger target redacted/missing
            canon = canon_by_id[mid][0]
            for variant in sorted(lib[kind][n]):
                if variant != canon: rows.append([variant, canon, "ao3 alias"])
        for n, (canon, _) in sorted(validated[kind].items()):      # right concept, off spelling/case
            if n in aliases[kind]: continue                        # alias mapping above is the stronger signal
            for variant in sorted(lib[kind][n]):
                if variant != canon: rows.append([variant, canon, "ao3 spelling"])
        w(fn, ["variant", "canonical", "note"], rows)

    trows = []
    for n, mid in sorted(aliases["tag"].items()):
        if mid not in canon_by_id: continue
        canon, uses = canon_by_id[mid]
        for variant in sorted(lib["tag"][n]):
            if variant != canon: trows.append([variant, canon, uses])
    w("ao3_tropes.csv", ["variant", "canonical", "uses"], trows)

    vrows = sorted(((name, uses) for name, uses in validated["tag"].values()), key=lambda x: -x[1])
    w("ao3_vocab.csv", ["name", "uses"], vrows)

    urows = [[kind, v] for kind in ("fandom", "character")
             for n, vs in sorted(lib[kind].items())
             if n not in validated[kind] and n not in aliases[kind] for v in sorted(vs)]
    w("ao3_unknown.csv", ["kind", "value"], urows)


def selftest():
    lib = {"fandom": {norm("GoT"): {"GoT"}, norm("Naruto"): {"Naruto"}, norm("My Own AU"): {"My Own AU"}},
           "character": {norm("Hiashi H."): {"Hiashi H."}},
           "tag": {norm("Time-Travel"): {"Time-Travel"}, norm("Fluff"): {"Fluff"}}}
    dump = [
        {"id": "1", "type": "Fandom", "name": "Game of Thrones (TV)", "canonical": "true", "cached_count": "9", "merger_id": ""},
        {"id": "2", "type": "Fandom", "name": "GoT", "canonical": "false", "cached_count": "3", "merger_id": "1"},
        {"id": "3", "type": "Fandom", "name": "Naruto", "canonical": "true", "cached_count": "8", "merger_id": ""},
        {"id": "4", "type": "Character", "name": "Hiashi H.", "canonical": "false", "cached_count": "2", "merger_id": "5"},
        {"id": "5", "type": "Character", "name": "Hyuuga Hiashi", "canonical": "true", "cached_count": "7", "merger_id": ""},
        {"id": "6", "type": "Freeform", "name": "Time-Travel", "canonical": "false", "cached_count": "4", "merger_id": "7"},
        {"id": "7", "type": "Freeform", "name": "Time Travel", "canonical": "true", "cached_count": "99", "merger_id": ""},
        {"id": "8", "type": "Freeform", "name": "Fluff", "canonical": "true", "cached_count": "50", "merger_id": ""},
        {"id": "9", "type": "Relationship", "name": "GoT", "canonical": "true", "cached_count": "1", "merger_id": ""},
    ]
    validated, aliases, need = match_dump(dump, lib)
    assert aliases["fandom"] == {norm("GoT"): "1"} and need >= {"1", "5", "7"}
    assert norm("Naruto") in validated["fandom"]                       # exact canonical -> validated, no row emitted
    assert aliases["character"] == {norm("Hiashi H."): "5"}
    assert aliases["tag"] == {norm("Time Travel"): "7"}            # alias row points at its merger target
    canon = resolve_ids(dump, need)
    assert canon["1"] == ("Game of Thrones (TV)", 9) and canon["5"] == ("Hyuuga Hiashi", 7)
    assert norm("Fluff") in validated["tag"] and norm("My Own AU") not in validated["fandom"]
    print("selftest ok")


def main():
    p = argparse.ArgumentParser(description="Generate overrides/ review candidates from the AO3 tag dump.")
    p.add_argument("--dump", default=os.path.expanduser("~/Downloads/20210226-stats/tags-20210226.csv"))
    p.add_argument("--selftest", action="store_true")
    a = p.parse_args()
    if a.selftest: return selftest()
    if not os.path.exists(a.dump):
        raise SystemExit(f"dump not found: {a.dump}  (download via archiveofourown.org/admin_posts/18804)")
    lib = library_values(ro_connect())
    print(f"library: {len(lib['fandom'])} fandoms, {len(lib['character'])} characters, {len(lib['tag'])} tags")
    with open(a.dump, newline="", encoding="utf-8", errors="replace") as f:
        validated, aliases, need = match_dump(csv.DictReader(f), lib)
    with open(a.dump, newline="", encoding="utf-8", errors="replace") as f:
        canon_by_id = resolve_ids(csv.DictReader(f), need)
    emit(lib, validated, aliases, canon_by_id)
    print("\nreview the data/ao3_*.csv files; adopt rows by appending them to overrides/fandoms.csv /")
    print("characters.csv / tropes.csv (drop the note/uses column, add a route for tropes: usually 'tag').")


if __name__ == "__main__":
    main()
