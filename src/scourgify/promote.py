#!/usr/bin/env python3
"""scourgify promote — adversarially decide whether each novel tag candidate from classify's
proposed_new list should be promoted to the vocab, aliased to an existing tag, or rejected.

  scourgify promote                 # dry run -> data/promote_review.csv (advocate + skeptic)
  scourgify promote --apply         # fold verdicts into overrides/ (vocab, tropes, aliases)
  scourgify promote --verify-with openai   # run the skeptic on a different engine (cross-model)

Reasons each candidate against a difflib shortlist of the master tag list (curated vocab ∪ ao3_vocab)
plus the example books that proposed it. Audit-first: verdicts are a reviewed artifact you apply."""
import argparse, glob, json, os, re, collections, contextlib
from concurrent.futures import ThreadPoolExecutor, as_completed
import difflib

from scourgify.artifacts import (prop, rank, ledger, review, applied_proposals,
                                 append_ledger, read_rows, split_tags, write_review, archive)
from scourgify.classify import existing_terms
from scourgify.engines import ENGINES, ask_retry, max_workers as engine_workers
from scourgify.common import (GuardrailError, data_dir, library, norm, ro_connect, run_writer,
                              current_tags, titles as book_titles, op_set_field, interactive, confirm)
from scourgify.overrides import ov_path, append_lines, append_rows   # overrides/ formats live there

VERDICTS = ("promote", "alias", "reject")


def _desanitize(s):
    return s.lstrip("=+-@ ").strip()


def parse_decision(text: str) -> dict | None:
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


def shortlist(tag: str, existing: list | None = None, n: int = 15) -> list:
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


def advocate_prompt(cand: dict, near: list) -> str:
    return ("You curate a controlled fanfiction tag vocabulary. Decide whether this NEW candidate tag "
            "should be PROMOTED (a genuinely new, reusable trope/theme not covered by an existing tag), "
            "ALIASED to one of the existing tags (same meaning, different words), or REJECTED "
            "(plot-specific, a character/fandom name, or noise).\n\n" + _ctx(cand, near) + "\n" + _SCHEMA)


def skeptic_prompt(cand: dict, proposed: dict, near: list) -> str:
    return ("You are a SKEPTIC. Another curator proposed the verdict below. Try to REFUTE a promote: "
            "is there an existing master tag that already covers this candidate (=> alias), or is it "
            "plot-specific / a character or fandom name / noise (=> reject)? Default to skeptical when "
            "unsure.\n\n" + _ctx(cand, near) +
            f'\nPROPOSED VERDICT: {proposed.get("verdict")} — {proposed.get("reason","")}\n\n' + _SCHEMA)


def _ledger_tags(path):
    return {r["tag"] for r in read_rows(path)}


def candidates(ranked_path: str | None = None, proposal_path: str | None = None, ledger_path: str | None = None) -> list:
    ranked_path, proposal_path, ledger_path = ranked_path or rank(), proposal_path or prop(), ledger_path or ledger()
    if not os.path.exists(ranked_path):
        raise GuardrailError(f"no candidates ({os.path.basename(ranked_path)} not found — run a classify pass first).")
    decided = _ledger_tags(ledger_path)
    examples = {}                                              # tag -> [titles]
    for r in read_rows(proposal_path):
        for t in split_tags(r.get("proposed_new")):
            examples.setdefault(t, []).append(r.get("title", ""))
    out = []
    for r in read_rows(ranked_path):
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


def decide(cand: dict, ask, verify_ask=None, existing: list | None = None) -> dict:
    if existing is None: existing = existing_terms()
    near = shortlist(cand["tag"], existing)
    base = {"tag": cand["tag"], "count": cand.get("count", 0)}
    adv = parse_decision(ask(advocate_prompt(cand, near)))
    if adv is None:
        # NOT a reject: ask_retry returns ("", err) on a transport failure, so a network hiccup would
        # otherwise become a durable verdict in the ledger and the tag would never be adjudicated again.
        # "error" isn't in VERDICTS, so apply_decisions skips it and the candidate re-runs next time.
        return {**base, "verdict": "error", "target": "", "contested": False,
                "reason": "no usable response (transport failure or unparseable)", "confidence": "low"}
    if adv["verdict"] != "promote":
        return _finalize(base, {**adv, "contested": False}, existing)   # alias/reject (alias target validated)
    sk = parse_decision((verify_ask or ask)(skeptic_prompt(cand, adv, near)))
    if sk and sk["verdict"] in ("alias", "reject"):
        return _finalize(base, {**sk, "contested": True}, existing)     # skeptic refuted the promote
    if sk is None:
        return {**base, **adv, "contested": False,                 # skeptic inconclusive
                "confidence": "low", "reason": adv.get("reason", "") + " [skeptic inconclusive]"}
    return {**base, **adv, "contested": False}                     # promote stands


def apply_decisions(review_path: str | None = None, vocab_path: str | None = None, tropes_path: str | None = None,
                    aliases_path: str | None = None, ledger_path: str | None = None,
                    rows: list | None = None) -> dict:
    review_path = review_path or review()
    aliases_path = aliases_path or ov_path("promote_aliases.csv")
    ledger_path = ledger_path or ledger()
    vocab_path = vocab_path or ov_path("classify_vocab.txt")
    tropes_path = tropes_path or ov_path("tropes.csv")
    from_file = rows is None                   # explicit rows = the --step path; the caller archives
    if from_file:
        if not os.path.exists(review_path):
            raise GuardrailError(f"no review to apply ({os.path.basename(review_path)} not found — run promote first).")
        rows = read_rows(review_path)
    n = {"promote": 0, "alias": 0, "reject": 0, "skipped": 0}
    for r in rows:
        tag, target = r["tag"], r.get("target", "")
        v = r["verdict"].strip().lower()
        if v not in VERDICTS:
            print(f"  skipped {tag}: unknown verdict {r['verdict']!r}")
            n["skipped"] += 1
            continue
        if v == "promote":
            append_lines(vocab_path, [tag])
        elif v == "alias":
            if not target.strip() or norm(target) == norm(tag):    # hand-edited self/empty alias: never write a junk fold
                print(f"  skipped {tag}: alias needs a distinct target (got {target!r})")
                n["skipped"] += 1
                continue
            append_rows(tropes_path, ["variant", "canonical", "route"], [[tag, target, "tag"]])
            append_rows(aliases_path, ["candidate", "target"], [[tag, target]])
        n[v] = n.get(v, 0) + 1
        append_ledger(tag, v, target, ledger_path)
    arch = archive(review_path, "applied") if from_file else None
    # a skipped row gets no ledger entry ON PURPOSE (an 'error' verdict is a transport failure, not
    # a decision) — so it stays a candidate. Say so: otherwise apply reports success and the wizard's
    # "N candidates" hint survives the apply with nothing on screen explaining why.
    print(f"applied: {n['promote']} promoted, {n['alias']} aliased, {n['reject']} rejected"
          + (f", {n['skipped']} left UNDECIDED (offered again next run)" if n["skipped"] else "")
          + (f"; review archived -> {os.path.basename(arch)}" if arch else ""))
    return n


def verdict_line(r: dict) -> str:
    """One adjudicated candidate as a review line. Pure — the checklist is a dumb string widget."""
    v = (r.get("verdict") or "").strip().lower()
    what = {"promote": "[green]promote[/]", "alias": "[cyan]alias →[/] " + (r.get("target") or ""),
            "reject": "[dim]reject[/]"}.get(v, f"[red]{v}[/]")
    warn = " [yellow]⚠contested[/]" if str(r.get("contested")) == "True" else ""
    n = r.get("count") or "?"
    return f"[bold]{r.get('tag','')}[/]{warn}  ({n} book(s))  {what}  [dim]{(r.get('reason') or '')[:70]}[/]"


def apply_decisions_step(review_path: str | None = None, decide=None) -> dict:
    """1-by-1 review of the adjudicated verdicts: untick any you disagree with.

    Ticked verdicts are applied and land in the ledger. An unticked one gets NO ledger row, so the
    candidate stays undecided and is offered again — the same "this was not a durable decision"
    rule an engine error already follows. Before this, disagreeing with 3 of 50 verdicts meant
    'keep' and hand-editing a CSV; the whole point of an adjudicated list is judging it item by
    item, which is how the wrangle and classify reviews already work.

    `decide(title, items, subtitle=) -> (accepted_idx, rejected_idx, action)` defaults to
    ui.checklist (D-11) — the lazy import of ui moves BEHIND that default."""
    from scourgify.artifacts import archive_rows
    review_path = review_path or review()
    if not os.path.exists(review_path):
        raise GuardrailError(f"no review to apply ({os.path.basename(review_path)} not found — run promote first).")
    if decide is None:
        from scourgify import ui
        if not interactive():
            raise GuardrailError("--step needs an interactive terminal (omit it to apply the whole review).")
        decide = ui.checklist
    rows = read_rows(review_path)
    actionable = [r for r in rows if (r.get("verdict") or "").strip().lower() in VERDICTS]
    other = [r for r in rows if r not in actionable]          # errors: never applicable, stay pending
    if not actionable:
        print("(no applicable verdicts — nothing to review.)"); return {}
    acc, rej, action = decide("verdicts — untick any you disagree with",
                              [verdict_line(r) for r in actionable],
                              subtitle="ticked verdicts are applied; unticked ones stay undecided "
                                       "and are offered again")
    if action in ("skip", "quit") or not acc:
        print("(nothing decided — review left untouched.)"); return {}
    decided = [actionable[i] for i in acc]
    pending = [actionable[i] for i in rej] + other
    n = apply_decisions(review_path, rows=decided)            # explicit rows: no archive, no file read
    arch = archive_rows(decided, "applied", review_path, write_review)   # only what was applied
    if pending:
        write_review(pending, review_path)
        print(f"{len(pending)} verdict(s) left undecided -> {os.path.basename(review_path)}")
    else:
        os.remove(review_path)
    print(f"applied verdicts archived -> {os.path.basename(arch)}")
    return n


# ---------------- backfill: apply promoted/aliased tags to the books that first proposed them ----------------
# The classifier only writes vocab `added_tags`; a `proposed_new` candidate is never written to its book.
# So promoting/aliasing a candidate grows the vocab but leaves the source books un-tagged (and they're
# stamped #wrangled, so --incremental skips them). Backfill closes that loop deterministically — no LLM —
# by reading the book↔proposed_new record kept in the (archived) proposals and the ledger's verdicts.
def resolve_ledger(rows: list) -> dict:
    """[{tag,verdict,target}] -> {candidate_lower: tag_to_apply}. promote->itself, alias->target, reject->skip."""
    res = {}
    for r in rows:
        v = (r.get("verdict") or "").strip().lower(); tag = (r.get("tag") or "").strip()
        if not tag: continue
        if v == "promote": res[tag.lower()] = tag
        elif v == "alias" and (r.get("target") or "").strip(): res[tag.lower()] = r["target"].strip()
    return res


def backfill_wanted(resolution: dict, proposal_rows: list) -> dict:
    """resolution + proposal rows [{book_id, proposed_new}] -> {book_id:int : set(tags to apply)}. Pure."""
    want = collections.defaultdict(set)
    for r in proposal_rows:
        try: b = int(r["book_id"])
        except (KeyError, ValueError, TypeError): continue
        for c in split_tags(r.get("proposed_new")):
            t = resolution.get(c.lower())
            if t: want[b].add(t)
    return dict(want)


def backfill_drop_redundant(adds: dict, homes: dict) -> dict:
    """{book: set(tags)} minus the tags that already live in that book's structured columns.
    Pure — see tests. wrangle strips a tag whose concept is already in #fandoms/#characters/
    #genres/#relationships/#status (backfill-before-strip), so proposing one here starts a
    ping-pong: backfill adds it, wrangle strips it, backfill sees it missing and adds it again.
    Matching is norm()-based because wrangle's strip is."""
    out = {}
    for b, tags in adds.items():
        keep = {t for t in tags if norm(t) not in homes.get(b, set())}
        if keep: out[b] = keep
    return out


def backfill_drop_unstable(adds: dict, stable) -> dict:
    """{book: set(tags)} minus the tags `stable` rejects. Pure — see tests."""
    out = {}
    for b, tags in adds.items():
        keep = {t for t in tags if t in stable}
        if keep: out[b] = keep
    return out


def wrangle_stable(tags) -> set:
    """The subset of `tags` wrangle leaves alone AS TAGS. backfill_drop_redundant settles the
    fight for a tag wrangle MOVES into a structured column; this settles it for the other rule
    kinds — a trope rename (`X → Y`) or a junk-drop leaves the tag in no column at all, so the
    per-book `homes` check can't see it and backfill re-adds X forever. Rendered through the
    real transform() rather than by re-reading the maps, so it can't desync from the rules.
    ponytail: silent — a tag wrangle rewrites is simply never a valid backfill target, and the
    caller runs on every wizard menu refresh, so a note here would nag once per redraw."""
    from scourgify import wrangle
    from scourgify.common import load_config
    cfg = load_config(); m = wrangle.load_maps(cfg); beh = cfg.get("behavior", {})
    return {t for t in tags if wrangle.transform({"tags": [t]}, m, beh)[0].get("tags") == [t]}


def _homes(con) -> dict:
    """{book: set(norm'd values living in its structured columns)} — the same set wrangle's
    redundancy-strip tests against."""
    from scourgify.common import read_custom_column, load_config
    cols = [v for k, v in load_config()["columns"].items() if v and v != "tags"]
    homes = collections.defaultdict(set)
    for lab in cols:
        for b, vs in (read_custom_column(con, lab, multi=True) or {}).items():
            homes[b].update(norm(v) for v in vs)
    return homes


def _proposal_files():
    """Every file carrying the book↔proposed_new record: archived applied proposals + the current one."""
    fs = applied_proposals()                       # the archive-naming convention lives with archive()
    if os.path.exists(prop()): fs.append(prop())
    return fs


def backfill_plan(ledger_path: str | None = None) -> tuple[dict, dict]:
    """-> (chg {book: sorted full tag set}, adds {book: set(new tags)}) for books that
    should carry a promoted/aliased tag but don't yet. Reads the ledger + all proposals + live tags."""
    res = resolve_ledger(read_rows(ledger_path or ledger()))
    rows = [r for pf in _proposal_files() for r in read_rows(pf)]
    want = backfill_wanted(res, rows)
    if not want: return {}, {}
    with contextlib.closing(ro_connect()) as con:
        cur = current_tags(con)
        adds = {}
        for b, w in want.items():
            new = w - cur.get(b, set())
            if new: adds[b] = new
        # never propose a tag wrangle will strip as redundant — that is an endless add/strip loop
        adds = backfill_drop_redundant(adds, _homes(con))
    # …nor one it renames or junk-drops, which loops the same way with nothing in `homes` to catch it.
    # Last, and only over the handful of tags the cheap filters left: it loads the wrangle maps.
    if adds: adds = backfill_drop_unstable(adds, wrangle_stable({t for v in adds.values() for t in v}))
    chg = {b: sorted(cur.get(b, set()) | new) for b, new in adds.items()}
    return chg, adds


def backfill_step(chg: dict, adds: dict, titles: dict, decide=None) -> dict:
    """1-by-1 review of the backfill -> the ACCEPTED {book: tags} ({} = nothing decided). Shared by
    `promote --backfill --step` and the wizard stage, per CLAUDE.md's same-engine-function rule.

    `decide(title, items) -> (accepted_idx, rejected_idx, action)` defaults to ui.checklist
    (D-11) — the lazy import of ui moves BEHIND that default."""
    if decide is None:
        from scourgify import ui
        decide = ui.checklist
    books = sorted(adds)
    acc, _, action = decide("backfill — untick a book to leave it untagged",
                            [f"[bold]#{b}[/] {str(titles.get(b, ''))[:44]}  + "
                             f"[cyan]{', '.join(sorted(adds[b]))}[/]" for b in books])
    if action in ("skip", "quit"): return {}
    keep = {books[i] for i in acc}
    return {b: v for b, v in chg.items() if b in keep}


def backfill(yes: bool = False, step: bool = False, decide=None) -> int:
    """THE backfill flow — plan, preview, decide, guarded write — for every front door.

    `decide(chg, adds) -> chg to write` (falsy aborts) is the only thing that varies between them:
    the CLI's confirm/--step by default, the wizard's menu when it injects one. Same seam as
    Plan.run(ask=)/run(verify_ask=). The wizard used to assemble its own run_writer call here,
    which silently dropped this function's per-book preview — a wizard user saw less before a
    write than a CLI user, and any guard added here would have missed them entirely."""
    chg, adds = backfill_plan()
    if not chg:
        print("backfill: nothing to do — source books already carry their promoted tags ✓"); return 0
    total = sum(len(v) for v in adds.values())
    print(f"backfill: {len(chg)} book(s) gain {total} promoted/aliased tag-assignment(s), e.g.:")
    preview = list(adds)[:8]
    with contextlib.closing(ro_connect()) as con:
        titles = book_titles(con, preview)
    for b in preview: print(f"  #{b} {str(titles.get(b, ''))[:50]}: + {', '.join(sorted(adds[b]))}")
    if len(adds) > 8: print(f"  … +{len(adds) - 8} more books")
    if decide is not None:                     # a front door supplying its own question
        chg = decide(chg, adds)
        if not chg:
            print("(nothing decided — nothing written.)"); return 0
    elif step:                                 # the checklist IS the confirmation
        if not interactive():
            raise GuardrailError("--step needs an interactive terminal (omit it to apply the whole backfill).")
        with contextlib.closing(ro_connect()) as con:
            chg = backfill_step(chg, adds, book_titles(con))
        if not chg:
            print("(nothing decided — nothing written.)"); return 0
        print(f"  {len(chg)} book(s) accepted")
    elif not yes:
        if not interactive():
            print("  non-interactive: re-run with --yes to write."); return 0
        if not confirm("apply this backfill? (Calibre closed)"):
            print("aborted (nothing written)."); return 0
    run_writer([op_set_field("tags", chg)], tool="promote", scope=f"backfill, {len(chg)} books")
    print(f"backfilled promoted tags onto {len(chg)} book(s).")
    return len(chg)


def run(a: argparse.Namespace, ranked_path: str | None = None, proposal_path: str | None = None,
        review_path: str | None = None, existing: list | None = None,
        ask=None, verify_ask=None) -> None:
    """ask/verify_ask: prompt -> response text. Default to the configured engines; tests pass
    callables directly (the same seam decide() already has) instead of faking the registry."""
    review_path = review_path or review()
    if os.path.exists(review_path) and not getattr(a, "yes", False):
        raise GuardrailError(f"a pending review exists at {review_path} — apply it (scourgify promote --apply), "
                         f"delete it, or re-run with --yes to overwrite.")
    cands = candidates(ranked_path, proposal_path)
    if a.limit: cands = cands[:a.limit]
    if a.batch: cands = cands[:a.batch]
    if not cands:
        print("no undecided candidates — nothing to do."); return
    if ask is None:
        eng = ENGINES[a.engine](a.model, a.timeout)
        ask = lambda p: ask_retry(eng, p)[0]
    if verify_ask is None and a.verify_with:
        veng = ENGINES[a.verify_with]("", a.timeout)
        verify_ask = lambda p: ask_retry(veng, p)[0]
    print(f"engine={a.engine}{'  verify-with=' + a.verify_with if a.verify_with else ''}  candidates: {len(cands)}")
    rows = []
    with ThreadPoolExecutor(max_workers=engine_workers(a.engine, a.workers)) as ex:
        futs = [ex.submit(decide, c, ask, verify_ask, existing) for c in cands]
        for fut in as_completed(futs): rows.append(fut.result())
    rows.sort(key=lambda r: (r["verdict"] != "promote", -r["count"]))   # promotes first, by count
    os.makedirs(data_dir(), exist_ok=True)
    write_review(rows, review_path)
    tally = {v: sum(1 for r in rows if r["verdict"] == v) for v in VERDICTS}
    nerr = sum(1 for r in rows if r["verdict"] == "error")
    print(f"  {tally['promote']} promote, {tally['alias']} alias, {tally['reject']} reject"
          f"{f', {nerr} error (undecided — they re-run)' if nerr else ''} "
          f"-> {os.path.basename(review_path)} (review, then `scourgify promote --apply`)")


def build_parser() -> argparse.ArgumentParser:
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
    p.add_argument("--step", action="store_true",
                   help="with --apply: review each verdict 1-by-1 (untick one to leave it undecided); "
                        "with --backfill: review each book (untick one to leave it untagged)")
    return p


def normalize(a: argparse.Namespace) -> argparse.Namespace:
    library()                                       # fail fast with the clear CALIBRE_LIBRARY message
    os.makedirs(data_dir(), exist_ok=True)
    return a


def default_opts(**overrides) -> argparse.Namespace:
    """The non-CLI entry to a run's options: parser defaults + keyword overrides, normalized.
    The argparse parser stays the single schema; the wizard is the second adapter that fills it."""
    a = build_parser().parse_args([])
    for k, v in overrides.items(): setattr(a, k, v)
    return normalize(a)


def main() -> None:
    a = normalize(build_parser().parse_args())
    if a.apply and a.backfill:
        # --backfill --apply means "fold the verdicts in, then backfill". A missing review is
        # not an error here: `promote --apply` archives promote_review.csv, so the natural
        # follow-up run has none left and the backfill (which reads the ledger, not the review)
        # must still happen. It used to abort on the review and never reach the backfill.
        try: apply_decisions_step() if a.step else apply_decisions()
        except GuardrailError as e: print(f"{e}\n  (continuing to --backfill, which reads the ledger)")
        backfill(yes=a.yes or a.apply, step=a.step)
    elif a.apply:
        apply_decisions_step() if a.step else apply_decisions()
    elif a.backfill:
        backfill(yes=a.yes, step=a.step)
    else:
        run(a)


if __name__ == "__main__":
    main()
