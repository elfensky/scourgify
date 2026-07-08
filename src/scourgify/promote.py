#!/usr/bin/env python3
"""scourgify promote — adversarially decide whether each novel tag candidate from classify's
proposed_new list should be promoted to the vocab, aliased to an existing tag, or rejected.

  scourgify promote                 # dry run -> data/promote_review.csv (advocate + skeptic)
  scourgify promote --apply         # fold verdicts into overrides/ (vocab, tropes, aliases)
  scourgify promote --verify-with openai   # run the skeptic on a different engine (cross-model)

Reasons each candidate against a difflib shortlist of the master tag list (curated vocab ∪ ao3_vocab)
plus the example books that proposed it. Audit-first: verdicts are a reviewed artifact you apply."""
import argparse, csv, glob, json, os, re, time, collections
from concurrent.futures import ThreadPoolExecutor, as_completed
import difflib

from scourgify.classify import RANK, PROP, ENGINES, existing_terms, ask_retry
from scourgify.common import DATA, library, norm, ro_connect, run_writer

LEDGER = f"{DATA}/promote_ledger.csv"
REVIEW = f"{DATA}/promote_review.csv"
ALIASES = os.path.join(os.getcwd(), "overrides", "promote_aliases.csv")
VERDICTS = ("promote", "alias", "reject")


def _desanitize(s):
    return s.lstrip("=+-@ ").strip()


def parse_decision(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m: return None
    try: obj = json.loads(m.group(0))
    except Exception: return None
    v = str(obj.get("verdict", "")).strip().lower()
    if v not in VERDICTS: return None
    target = _desanitize(str(obj.get("target", "")))
    if v == "alias" and not target: return None
    conf = str(obj.get("confidence", "med")).strip().lower()
    if conf not in ("low", "med", "high"): conf = "med"
    reason = _desanitize(str(obj.get("reason", "")).strip()[:200])
    return {"verdict": v, "target": target if v == "alias" else "",
            "reason": reason, "confidence": conf}


def shortlist(tag, existing=None, n=15):
    existing = existing_terms() if existing is None else existing
    elow = {e.lower(): e for e in existing}
    return [elow[k] for k in difflib.get_close_matches(tag.lower(), list(elow), n=n, cutoff=0.0)]


_SCHEMA = ('Return ONLY a JSON object: {"verdict": "promote"|"alias"|"reject", '
           '"target": "<existing tag>" (required iff alias), '
           '"reason": "<one sentence>", "confidence": "low"|"med"|"high"}.')


def _ctx(cand, near):
    ex = " | ".join(cand.get("examples", [])[:5])[:600]
    return (f'CANDIDATE TAG: "{cand["tag"]}" (proposed for {cand["count"]} book(s))\n'
            f"NEAREST EXISTING MASTER TAGS: {', '.join(near)}\n"
            f"EXAMPLE BOOKS THAT USED IT: {ex}\n")


def advocate_prompt(cand, near):
    return ("You curate a controlled fanfiction tag vocabulary. Decide whether this NEW candidate tag "
            "should be PROMOTED (a genuinely new, reusable trope/theme not covered by an existing tag), "
            "ALIASED to one of the existing tags (same meaning, different words), or REJECTED "
            "(plot-specific, a character/fandom name, or noise).\n\n" + _ctx(cand, near) + "\n" + _SCHEMA)


def skeptic_prompt(cand, proposed, near):
    return ("You are a SKEPTIC. Another curator proposed the verdict below. Try to REFUTE a promote: "
            "is there an existing master tag that already covers this candidate (=> alias), or is it "
            "plot-specific / a character or fandom name / noise (=> reject)? Default to skeptical when "
            "unsure.\n\n" + _ctx(cand, near) +
            f'\nPROPOSED VERDICT: {proposed.get("verdict")} — {proposed.get("reason","")}\n\n' + _SCHEMA)


def _ledger_tags(path):
    if not os.path.exists(path): return set()
    return {r["tag"] for r in csv.DictReader(open(path))}


def candidates(ranked_path=RANK, proposal_path=PROP, ledger_path=LEDGER):
    if not os.path.exists(ranked_path):
        raise SystemExit(f"no candidates ({os.path.basename(ranked_path)} not found — run a classify pass first).")
    decided = _ledger_tags(ledger_path)
    examples = {}                                              # tag -> [titles]
    if os.path.exists(proposal_path):
        for r in csv.DictReader(open(proposal_path)):
            for t in (r.get("proposed_new", "") or "").split("; "):
                t = t.strip()
                if t: examples.setdefault(t, []).append(r.get("title", ""))
    out = []
    for r in csv.DictReader(open(ranked_path)):
        tag = r["proposed_tag"].strip()
        if not tag or tag in decided: continue
        out.append({"tag": tag, "count": int(r.get("count", 0) or 0),
                    "examples": [t for t in examples.get(tag, []) if t]})
    out.sort(key=lambda c: -c["count"])
    return out


def _finalize(base, dec, existing):
    """Guard an alias verdict against the master list. A weak model told to 'alias' often echoes the
    candidate as its own target or invents one that isn't a real tag — either would write a junk fold.
    A self-alias or an unknown target is downgraded to reject (with a visible reason) so apply can't
    write garbage; a valid target is normalized to its canonical existing spelling."""
    if dec.get("verdict") == "alias":
        elow = {norm(e): e for e in existing}
        tnorm = norm(dec.get("target", ""))
        if not tnorm or tnorm == norm(base["tag"]) or tnorm not in elow:
            dec = {**dec, "verdict": "reject", "target": "", "confidence": "low",
                   "reason": f"[unverified alias target {dec.get('target', '')!r}] " + dec.get("reason", "")}
        else:
            dec = {**dec, "target": elow[tnorm]}                    # canonical spelling of the real master tag
    return {**base, **dec}


def decide(cand, ask, verify_ask=None, existing=None):
    if existing is None: existing = existing_terms()
    near = shortlist(cand["tag"], existing)
    base = {"tag": cand["tag"], "count": cand.get("count", 0)}
    adv = parse_decision(ask(advocate_prompt(cand, near)))
    if adv is None:
        return {**base, "verdict": "reject", "target": "", "contested": False,
                "reason": "advocate response unparseable", "confidence": "low"}
    if adv["verdict"] != "promote":
        return _finalize(base, {**adv, "contested": False}, existing)   # alias/reject (alias target validated)
    sk = parse_decision((verify_ask or ask)(skeptic_prompt(cand, adv, near)))
    if sk and sk["verdict"] in ("alias", "reject"):
        return _finalize(base, {**sk, "contested": True}, existing)     # skeptic refuted the promote
    if sk is None:
        return {**base, **adv, "contested": False,                 # skeptic inconclusive
                "confidence": "low", "reason": adv.get("reason", "") + " [skeptic inconclusive]"}
    return {**base, **adv, "contested": False}                     # promote stands


def _append_line(path, line):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a") as f: f.write(line + "\n")


def _append_row(path, header, row, delim=","):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.writer(f, delimiter=delim)
        if new: w.writerow(header)
        w.writerow(row)


def apply_decisions(review_path=REVIEW, vocab_path=None, tropes_path=None,
                    aliases_path=ALIASES, ledger_path=LEDGER):
    vocab_path = vocab_path or os.path.join(os.getcwd(), "overrides", "classify_vocab.txt")
    tropes_path = tropes_path or os.path.join(os.getcwd(), "overrides", "tropes.csv")
    if not os.path.exists(review_path):
        raise SystemExit(f"no review to apply ({os.path.basename(review_path)} not found — run promote first).")
    n = {"promote": 0, "alias": 0, "reject": 0}
    for r in csv.DictReader(open(review_path)):
        tag, target = r["tag"], r.get("target", "")
        v = r["verdict"].strip().lower()
        if v not in VERDICTS:
            print(f"  skipped {tag}: unknown verdict {r['verdict']!r}")
            continue
        if v == "promote":
            _append_line(vocab_path, tag)
        elif v == "alias":
            if not target.strip() or norm(target) == norm(tag):    # hand-edited self/empty alias: never write a junk fold
                print(f"  skipped {tag}: alias needs a distinct target (got {target!r})")
                continue
            _append_row(tropes_path, ["variant", "canonical", "route"], [tag, target, "tag"], delim=";")
            _append_row(aliases_path, ["candidate", "target"], [tag, target])
        n[v] = n.get(v, 0) + 1
        _append_row(ledger_path, ["tag", "verdict", "target"], [tag, v, target])
    arch = review_path.replace(".csv", f"_applied_{time.strftime('%Y%m%d-%H%M%S')}.csv")
    os.rename(review_path, arch)
    print(f"applied: {n['promote']} promoted, {n['alias']} aliased, {n['reject']} rejected; "
          f"review archived -> {os.path.basename(arch)}")
    return n


# ---------------- backfill: apply promoted/aliased tags to the books that first proposed them ----------------
# The classifier only writes vocab `added_tags`; a `proposed_new` candidate is never written to its book.
# So promoting/aliasing a candidate grows the vocab but leaves the source books un-tagged (and they're
# stamped #wrangled, so --incremental skips them). Backfill closes that loop deterministically — no LLM —
# by reading the book↔proposed_new record kept in the (archived) proposals and the ledger's verdicts.
def resolve_ledger(rows):
    """[{tag,verdict,target}] -> {candidate_lower: tag_to_apply}. promote->itself, alias->target, reject->skip."""
    res = {}
    for r in rows:
        v = (r.get("verdict") or "").strip().lower(); tag = (r.get("tag") or "").strip()
        if not tag: continue
        if v == "promote": res[tag.lower()] = tag
        elif v == "alias" and (r.get("target") or "").strip(): res[tag.lower()] = r["target"].strip()
    return res


def backfill_wanted(resolution, proposal_rows):
    """resolution + proposal rows [{book_id, proposed_new}] -> {book_id:int : set(tags to apply)}. Pure."""
    want = collections.defaultdict(set)
    for r in proposal_rows:
        try: b = int(r["book_id"])
        except (KeyError, ValueError, TypeError): continue
        for c in (r.get("proposed_new") or "").split("; "):
            t = resolution.get(c.strip().lower())
            if t: want[b].add(t)
    return dict(want)


def _proposal_files():
    """Every file carrying the book↔proposed_new record: archived applied proposals + the current one."""
    fs = sorted(glob.glob(f"{DATA}/classify_proposal_applied_*.csv"))
    if os.path.exists(PROP): fs.append(PROP)
    return fs


def backfill_plan(ledger_path=LEDGER):
    """-> (chg {str(book): sorted full tag set}, adds {book:int : set(new tags)}) for books that
    should carry a promoted/aliased tag but don't yet. Reads the ledger + all proposals + live tags."""
    if not os.path.exists(ledger_path):
        return {}, {}
    res = resolve_ledger(list(csv.DictReader(open(ledger_path))))
    rows = [r for pf in _proposal_files() for r in csv.DictReader(open(pf))]
    want = backfill_wanted(res, rows)
    if not want: return {}, {}
    con = ro_connect(); cur = collections.defaultdict(set)
    for b, t in con.execute("SELECT l.book, t.name FROM books_tags_link l JOIN tags t ON t.id=l.tag"): cur[b].add(t)
    chg, adds = {}, {}
    for b, w in want.items():
        new = w - cur.get(b, set())
        if new: chg[str(b)] = sorted(cur.get(b, set()) | w); adds[b] = new
    return chg, adds


def backfill(yes=False):
    """CLI entry: preview, confirm, then write the promoted/aliased tags onto their source books."""
    chg, adds = backfill_plan()
    if not chg:
        print("backfill: nothing to do — source books already carry their promoted tags ✓"); return 0
    total = sum(len(v) for v in adds.values())
    print(f"backfill: {len(chg)} book(s) gain {total} promoted/aliased tag-assignment(s), e.g.:")
    for b in list(adds)[:8]: print(f"  #{b}: + {', '.join(sorted(adds[b]))}")
    if len(adds) > 8: print(f"  … +{len(adds) - 8} more books")
    if not yes:
        import sys
        if not sys.stdin.isatty():
            print("  non-interactive: re-run with --yes to write."); return 0
        if input("apply this backfill? (Calibre closed) [y/N] ").strip().lower() not in ("y", "yes"):
            print("aborted (nothing written)."); return 0
    run_writer([{"op": "set_field", "field": "tags", "values": chg}])
    print(f"backfilled promoted tags onto {len(chg)} book(s).")
    return len(chg)


REVIEW_COLS = ["tag", "count", "verdict", "target", "reason", "confidence", "contested"]


def run(a, ranked_path=RANK, proposal_path=PROP, review_path=REVIEW, existing=None):
    if os.path.exists(review_path) and not getattr(a, "yes", False):
        raise SystemExit(f"a pending review exists at {review_path} — apply it (scourgify promote --apply), "
                         f"delete it, or re-run with --yes to overwrite.")
    cands = candidates(ranked_path, proposal_path)
    if a.limit: cands = cands[:a.limit]
    if a.batch: cands = cands[:a.batch]
    if not cands:
        print("no undecided candidates — nothing to do."); return
    eng = ENGINES[a.engine](a.model, a.timeout)
    veng = ENGINES[a.verify_with]("", a.timeout) if a.verify_with else None
    ask = lambda p: ask_retry(eng, p)[0]
    verify_ask = (lambda p: ask_retry(veng, p)[0]) if veng else None
    print(f"engine={a.engine}{'  verify-with='+a.verify_with if veng else ''}  candidates: {len(cands)}")
    rows = []
    with ThreadPoolExecutor(max_workers=1 if a.engine == "apple" else a.workers) as ex:
        futs = [ex.submit(decide, c, ask, verify_ask, existing) for c in cands]
        for fut in as_completed(futs): rows.append(fut.result())
    rows.sort(key=lambda r: (r["verdict"] != "promote", -r["count"]))   # promotes first, by count
    os.makedirs(DATA, exist_ok=True)
    with open(review_path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(REVIEW_COLS)
        for r in rows: w.writerow([r.get(k, "") for k in REVIEW_COLS])
    tally = {v: sum(1 for r in rows if r["verdict"] == v) for v in VERDICTS}
    print(f"  {tally['promote']} promote, {tally['alias']} alias, {tally['reject']} reject "
          f"-> {os.path.basename(review_path)} (review, then `scourgify promote --apply`)")


def build_parser():
    p = argparse.ArgumentParser(description="Adversarially decide promote/alias/reject for classify's proposed-new tags.")
    p.add_argument("--engine", default="claude", choices=sorted(ENGINES))
    p.add_argument("--verify-with", default="", choices=[""] + sorted(ENGINES),
                   help="run the skeptic on a different engine (cross-model check)")
    p.add_argument("--model", default="")
    p.add_argument("--workers", type=int, default=8, metavar="N")
    p.add_argument("--batch", type=int, default=0, metavar="N")
    p.add_argument("--limit", type=int, default=0, metavar="N")
    p.add_argument("--timeout", type=int, default=60, metavar="S")
    p.add_argument("--yes", "-y", action="store_true")
    p.add_argument("--apply", action="store_true", help="fold data/promote_review.csv into overrides/")
    p.add_argument("--backfill", action="store_true",
                   help="apply promoted/aliased tags to the books that first proposed them (deterministic, no LLM; Calibre closed)")
    return p


def normalize(a):
    library()                                       # fail fast with the clear CALIBRE_LIBRARY message
    os.makedirs(DATA, exist_ok=True)
    return a


def main():
    a = normalize(build_parser().parse_args())
    if a.apply:
        apply_decisions()
        if a.backfill: backfill(yes=a.yes)
    elif a.backfill:
        backfill(yes=a.yes)
    else:
        run(a)


if __name__ == "__main__":
    main()
