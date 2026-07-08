#!/usr/bin/env python3
"""Build the shipped AO3 taxonomy layer (src/scourgify/defaults/ao3/) from the OTW dump.

    uv run build_ao3_layer.py [--dump ~/Downloads/20210226-stats/tags-20210226.csv]
    uv run build_ao3_layer.py --selftest

Maintainer-only (like build_defaults.py): needs the 2021 "Selective data dump for fan
statisticians" (https://archiveofourown.org/admin_posts/18804). The OUTPUT ships with
the package. Two products:

MECHANICAL (written directly):
  defaults/ao3/tags.csv         master,name,rel   canonical freeform <- its merger aliases
  defaults/ao3/characters.csv   master,name,rel   canonical character <- merger aliases
  data/ao3_build/universes_mechanical.csv         same, for single-canonical fandom stems
                                                  (staged; merged into defaults/ao3/universes.csv
                                                  together with the LLM-clustered part)
WORK-LIST for the LLM clustering phase:
  data/ao3_build/fandom_groups.json   stem-grouped canonical fandoms >= FLOOR uses,
                                      with per-fandom counts and alias lists

Floors: fandom/freeform masters need >= FLOOR (10) uses; aliases attach regardless
(they are mechanical wrangler knowledge). Characters: alias pairs only, no clustering.
"""
import argparse, csv, json, os, re, sys

FLOOR = 10
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "src", "scourgify", "defaults", "ao3")
BUILD = os.path.join(HERE, "data", "ao3_build")


def franchise_stem(name):
    """Same stem rule as scourgify.ao3_import (kept dependency-free for calibre-less runs)."""
    s = name
    if "|" in s: s = s.split("|")[-1]
    s = re.sub(r"\s*&\s*Related Fandoms\s*$", "", s.strip())
    s = re.sub(r"\s*\([^)]*\)\s*$", "", s)
    s = s.split(" - ")[0]
    s = re.sub(r"\s*\([^)]*\)\s*$", "", s)
    return s.strip() or name


def norm(s):
    s = str(s).strip().lower(); s = re.sub(r"[\[\]\(\)]", "", s); s = s.replace("&", "and")
    s = re.sub(r"[\s_\-/]+", " ", s); s = re.sub(r"[^\w ]", "", s, flags=re.UNICODE); return s.strip()


def read_dump(path):
    """One stream -> (canon {id: (type, name, count)}, aliases [(type, name, merger_id)])."""
    csv.field_size_limit(10_000_000)
    canon, aliases = {}, []
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        for r in csv.DictReader(f):
            t = r["type"]
            if t not in ("Fandom", "Character", "Freeform"): continue
            name = r["name"]
            if not name or name == "Redacted": continue
            if r["canonical"] == "true":
                canon[r["id"]] = (t, name, int(r["cached_count"] or 0))
            elif r["merger_id"]:
                aliases.append((t, name, r["merger_id"]))
    return canon, aliases


def exceptions():
    """Curated (variant, canonical) pairs excluded from the generated layer — see the file header."""
    p = os.path.join(HERE, "src", "scourgify", "defaults", "ao3_exceptions.txt")
    if not os.path.exists(p): return set()
    return {tuple(l.strip().split(",", 1)) for l in open(p) if l.strip() and not l.startswith("#")}


def alias_pairs(canon, aliases, typ, floor, skip=frozenset()):
    """[(master, alias_name)] for aliases whose canonical is of `typ` and >= floor uses."""
    out = []
    for t, name, mid in aliases:
        if t != typ: continue
        c = canon.get(mid)
        if c and c[0] == typ and c[2] >= floor and name != c[1] and (name, c[1]) not in skip:
            out.append((c[1], name))                               # name != c[1]: keep norm-equal spelling variants
    return sorted(set(out))


def fandom_groups(canon, aliases, floor):
    """Stem-grouped canonical fandoms for the clustering phase.
    -> {stem_norm: {"stem": str, "members": [{"name", "uses", "aliases": [...]}]}}"""
    als = {}
    for t, name, mid in aliases:
        if t == "Fandom": als.setdefault(mid, []).append(name)
    groups = {}
    for cid, (t, name, uses) in canon.items():
        if t != "Fandom" or uses < floor: continue
        stem = franchise_stem(name)
        g = groups.setdefault(norm(stem), {"stem": stem, "members": []})
        if len(stem) < len(g["stem"]): g["stem"] = stem            # shortest spelling of the stem
        g["members"].append({"name": name, "uses": uses, "aliases": sorted(set(als.get(cid, [])))[:12]})
    for g in groups.values():
        g["members"].sort(key=lambda m: -m["uses"])
    return groups


def write_pairs(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["master", "name", "rel"]); w.writerows(rows)
    print(f"  {os.path.relpath(path, HERE):44} {len(rows):7,} rows")


def build_genres(freeform_pairs):
    """genres.csv: masters = the curated allowlist; synonyms = AO3 freeform aliases whose
    canonical norm-matches a master (FFN's fixed genre list is already covered by the allowlist)."""
    allow_path = os.path.join(HERE, "src", "scourgify", "defaults", "genres_allow.txt")
    masters = {norm(l.strip()): l.strip() for l in open(allow_path) if l.strip() and not l.startswith("#")}
    rows = []
    for m, a in freeform_pairs:
        g = masters.get(norm(m))
        if g and a != g: rows.append((g, a, "alias"))
    return sorted(set(rows))


def build(dump, floor=FLOOR):
    canon, aliases = read_dump(dump)
    print(f"dump: {sum(1 for c in canon.values())} canonicals, {len(aliases)} alias rows")

    skip = exceptions()
    freeform = alias_pairs(canon, aliases, "Freeform", floor, skip)
    write_pairs(os.path.join(OUT, "tags.csv"), [(m, a, "alias") for m, a in freeform])
    write_pairs(os.path.join(OUT, "genres.csv"), build_genres(freeform))
    write_pairs(os.path.join(OUT, "characters.csv"),
                [(m, a, "alias") for m, a in alias_pairs(canon, aliases, "Character", floor)])

    groups = fandom_groups(canon, aliases, floor)
    single = {k: g for k, g in groups.items() if len(g["members"]) == 1}
    multi = {k: g for k, g in groups.items() if len(g["members"]) > 1}
    # single-canonical stems are mechanical: universe = stem; canonical + aliases fold to it
    mech = []
    for g in single.values():
        m = g["members"][0]
        if m["name"] != g["stem"]: mech.append((g["stem"], m["name"], "media"))
        mech.extend((g["stem"], a, "alias") for a in m["aliases"] if norm(a) != norm(g["stem"]))
    write_pairs(os.path.join(BUILD, "universes_mechanical.csv"), sorted(set(mech)))

    os.makedirs(BUILD, exist_ok=True)
    with open(os.path.join(BUILD, "fandom_groups.json"), "w") as f:
        json.dump({"floor": floor, "multi": sorted(multi.values(), key=lambda g: -sum(m["uses"] for m in g["members"])),
                   "singles": [{"stem": g["stem"], "name": g["members"][0]["name"], "uses": g["members"][0]["uses"]}
                               for g in sorted(single.values(), key=lambda g: -g["members"][0]["uses"])]},
                  f, ensure_ascii=False, indent=0)
    print(f"  {'data/ao3_build/fandom_groups.json':44} {len(multi):7,} multi-stem groups + {len(single):,} singles")

    # per-agent batch files for the LLM clustering workflow (each agent Reads exactly one)
    bdir = os.path.join(BUILD, "batches")
    os.makedirs(bdir, exist_ok=True)
    for f0 in os.listdir(bdir): os.unlink(os.path.join(bdir, f0))
    multis = sorted(multi.values(), key=lambda g: -sum(m["uses"] for m in g["members"]))
    for i in range(0, len(multis), 50):
        with open(os.path.join(bdir, f"multi_{i//50:03}.json"), "w") as f:
            json.dump(multis[i:i+50], f, ensure_ascii=False)
    singles_l = [{"name": g["members"][0]["name"], "stem": g["stem"]}
                 for g in sorted(single.values(), key=lambda g: -g["members"][0]["uses"])]
    for i in range(0, len(singles_l), 250):
        with open(os.path.join(bdir, f"singles_{i//250:03}.json"), "w") as f:
            json.dump(singles_l[i:i+250], f, ensure_ascii=False)
    print(f"  {os.path.relpath(bdir, HERE):44} {len(multis)//50+1:>4} multi + {len(singles_l)//250+1} singles batches")


def assemble(dump, result_path, floor=FLOOR):
    """Combine the LLM clustering result with the dump into defaults/ao3/universes.csv.

    Decisions are verdict-gated: merges/splits apply only when the adversarial verify
    (or the referee) confirmed them; unsure claims go to data/ao3_build/universes_review.csv."""
    wrapper = json.load(open(result_path))
    res = wrapper.get("result", wrapper)
    canon, aliases = read_dump(dump)
    als = {}                                                   # canonical fandom name -> [aliases]
    for t, name, mid in aliases:
        c = canon.get(mid)
        if t == "Fandom" and c and c[0] == "Fandom" and name != c[1]:
            als.setdefault(c[1], []).append(name)

    verdict = {}                                               # (kind, child.lower, parent.lower) -> confirm/refute/unsure
    vd = res["verdicts"]
    for c in res["claims"]:
        v = vd.get(c["id"], {}).get("verdict", "unsure")
        verdict[(c["kind"], c["child"].lower(), c["parent"].lower())] = v

    def merge_ok(child, parent):
        return verdict.get(("merge", child.lower(), parent.lower()), "confirm") == "confirm"

    root, review = {}, []                                      # name -> master (pre-flatten)
    for g in res["multi"]:
        unis = g["universes"]
        if len(unis) > 1:                                      # a claimed same-name split
            skey = ("split", g["stem"].lower(), " / ".join(u["master"] for u in unis).lower())
            v = verdict.get(skey, "confirm")
            if v == "refute":                                  # verifier says one franchise after all
                review.append(("split-refuted", g["stem"], g["stem"], ""))
                unis = [{"master": g["stem"], "members": [m for u in unis for m in u["members"]]     # plain shared name
                                              + [u["master"] for u in unis if u["master"] != g["stem"]]}]
            elif v == "unsure":
                review.append(("split-unsure", g["stem"], " / ".join(u["master"] for u in unis), ""))
        parent = g.get("parent") if g.get("parent") and merge_ok(g["stem"], g["parent"]) else None
        if g.get("parent") and not parent:
            review.append(("parent-dropped", g["stem"], g["parent"], vd.get("", {}).get("note", "")))
        for u in unis:
            master = parent if (parent and not u.get("reason")) else u["master"]
            if u.get("reason"):
                review.append(("kept-separate", u["master"], g["stem"], u["reason"]))
            for mname in u["members"]:
                if mname != master: root[mname] = master
            if u["master"] != master: root[u["master"]] = master
    for a in res["adaptations"]:
        v = verdict.get(("merge", a["name"].lower(), a["parent"].lower()), "unsure")
        if v == "confirm" and a["name"] != a["parent"]:
            root.setdefault(a["name"], a["parent"])
        elif v == "unsure":
            review.append(("adaptation-unsure", a["name"], a["parent"], a.get("reason", "")))

    def resolve(n, _seen=None):                                # flatten chains; cycle-safe
        _seen = _seen or set()
        while n in root and n not in _seen:
            _seen.add(n); n = root[n]
        return n

    grouped = {}                                               # every floor-passing canonical -> its final master
    for cid, (t, name, uses) in canon.items():
        if t != "Fandom" or uses < floor: continue
        if name in root:
            grouped[name] = resolve(name)
        else:                                                  # LLM didn't touch it: mechanical stem rule
            stem = franchise_stem(name)
            grouped[name] = resolve(stem) if name != stem else name

    rows = set()
    for name, master in grouped.items():
        if name == master: continue
        rows.add((master, name, "related" if name in root else "media"))
        for al in als.get(name, []):
            if al != master: rows.add((master, al, "alias"))
    for name in grouped:                                       # aliases of standalone masters too
        if grouped[name] == name:
            for al in als.get(name, []):
                if al != name: rows.add((name, al, "alias"))
    skip = exceptions()
    rows = {(m, n, r) for m, n, r in rows if (n, m) not in skip}
    masters = {m for m, _, _ in rows}
    rows = {(m, n, r) for m, n, r in rows if n not in masters} # acyclic: a master never appears as a name
    write_pairs(os.path.join(OUT, "universes.csv"), sorted(rows))
    rev_path = os.path.join(BUILD, "universes_review.csv")
    with open(rev_path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["kind", "subject", "target", "note"]); w.writerows(sorted(set(review)))
    print(f"  {os.path.relpath(rev_path, HERE):44} {len(set(review)):7,} rows (human review)")


def selftest():
    assert franchise_stem("A Song of Ice and Fire - George R. R. Martin") == "A Song of Ice and Fire"
    assert franchise_stem("X | Y | My Hero Academia (Anime)") == "My Hero Academia"
    canon = {"1": ("Fandom", "Overlord - Maruyama Kugane", 500), "2": ("Fandom", "Overlord (Video Game)", 60),
             "3": ("Fandom", "Naruto", 900), "4": ("Freeform", "Time Travel", 99), "5": ("Fandom", "Rare Thing", 3)}
    aliases = [("Fandom", "Overlord LN", "1"), ("Freeform", "Time-Travel", "4"), ("Fandom", "RT", "5")]
    assert alias_pairs(canon, aliases, "Freeform", 10) == [("Time Travel", "Time-Travel")]
    assert alias_pairs(canon, aliases, "Fandom", 10) == [("Overlord - Maruyama Kugane", "Overlord LN")]
    g = fandom_groups(canon, aliases, 10)
    assert len(g[norm("Overlord")]["members"]) == 2                # clash candidates grouped for the LLM
    assert g[norm("Overlord")]["members"][0]["name"] == "Overlord - Maruyama Kugane"   # usage-sorted
    assert g[norm("Naruto")]["members"][0]["aliases"] == []
    assert norm("Rare Thing") not in g                             # below floor
    print("selftest ok")


def main():
    p = argparse.ArgumentParser(description="Build the shipped AO3 taxonomy layer from the OTW dump.")
    p.add_argument("--dump", default=os.path.expanduser("~/Downloads/20210226-stats/tags-20210226.csv"))
    p.add_argument("--floor", type=int, default=FLOOR)
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--assemble", metavar="RESULT_JSON",
                   help="combine the LLM clustering result into defaults/ao3/universes.csv")
    a = p.parse_args()
    if a.selftest: return selftest()
    if not os.path.exists(a.dump):
        raise SystemExit(f"dump not found: {a.dump}")
    if a.assemble: return assemble(a.dump, a.assemble, a.floor)
    build(a.dump, a.floor)


if __name__ == "__main__":
    main()
