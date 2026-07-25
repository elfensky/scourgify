#!/usr/bin/env python3
"""Owner of the data/ artifact files the tools hand each other: classify's proposal, the ranked
new-tag candidates, the failure log, and promote's review/ledger. Every cross-module reader and
writer of these formats goes through here, so the row schemas, the "; " in-cell list delimiter,
and the timestamped-archive convention are implementation — changed in one place, tested with
plain file round-trips. Stdlib-only (importable from every tool, like common; never _writer.py)."""
import csv
import os
import time

from scourgify.common import DATA

PROP = f"{DATA}/classify_proposal.csv"        # classify's per-book proposal (added_tags / proposed_new)
RANK = f"{DATA}/classify_newtags_ranked.csv"  # aggregated proposed-new candidates for promotion review
FAIL = f"{DATA}/classify_failures.csv"        # books an engine errored on (retry with another engine)
REVIEW = f"{DATA}/promote_review.csv"         # promote's adjudicated verdicts awaiting apply
LEDGER = f"{DATA}/promote_ledger.csv"         # every decided candidate (skip on re-runs; feeds backfill)

SEP = "; "                                    # the in-cell list delimiter for tag columns
PROP_COLS = ["book_id", "title", "added_tags", "proposed_new"]
RANK_COLS = ["proposed_tag", "count", "nearest_existing", "similarity", "verdict"]
REVIEW_COLS = ["tag", "count", "verdict", "target", "reason", "confidence", "contested"]
LEDGER_COLS = ["tag", "verdict", "target"]
FAIL_COLS = ["book_id", "title", "reason"]


def split_tags(s) -> list:
    """One in-cell tag list, split — the only place that knows the delimiter."""
    return [t.strip() for t in str(s or "").split(SEP) if t.strip()]


def join_tags(ts) -> str:
    return SEP.join(ts)


def read_rows(path: str) -> list:
    """Raw DictReader rows; [] if the file doesn't exist."""
    return list(csv.DictReader(open(path))) if os.path.exists(path) else []


def read_proposal(path: str = PROP) -> list:
    """Proposal rows with book_id as int and the tag columns as lists."""
    return [{"book_id": int(r["book_id"]), "title": r.get("title", ""),
             "added_tags": split_tags(r.get("added_tags")), "proposed_new": split_tags(r.get("proposed_new"))}
            for r in read_rows(path)]


def write_proposal(rows: list, path: str = PROP) -> None:
    """rows: PROP_COLS dicts; the tag columns may be lists (joined here) or pre-joined strings."""
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=PROP_COLS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            r = {k: r.get(k, "") for k in PROP_COLS}
            for k in ("added_tags", "proposed_new"):
                if isinstance(r[k], (list, tuple)): r[k] = join_tags(r[k])
            w.writerow(r)


def write_ranked(rows: list, path: str = RANK) -> None:
    """rows: RANK_COLS-ordered lists (annotate_new's output)."""
    with open(path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(RANK_COLS); w.writerows(rows)


def read_ranked(path: str = RANK) -> list:
    """Ranked-candidate rows by column name, count coerced to int."""
    out = []
    for r in read_rows(path):
        try: cnt = int(r.get("count", 0) or 0)
        except ValueError: cnt = 0
        out.append({**r, "count": cnt})
    return out


def write_review(rows: list, path: str = REVIEW) -> None:
    """rows: REVIEW_COLS dicts (promote's verdicts)."""
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=REVIEW_COLS, extrasaction="ignore")
        w.writeheader()
        for r in rows: w.writerow({k: r.get(k, "") for k in REVIEW_COLS})


def append_ledger(tag: str, verdict: str, target: str, path: str = LEDGER) -> None:
    """Append one decided candidate to the promote ledger (header on first write)."""
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new: w.writerow(LEDGER_COLS)
        w.writerow([tag, verdict, target])


def write_failures(rows: list, path: str = FAIL) -> None:
    """rows: FAIL_COLS-ordered lists — the books an engine errored on."""
    with open(path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(FAIL_COLS); w.writerows(rows)


def archive(path: str, kind: str) -> str:
    """Set a consumed artifact aside as <name>_<kind>_<ts>.csv (kind: 'applied' | 'discarded'),
    so stale rows can never re-apply. -> the archive path."""
    arch = path.replace(".csv", f"_{kind}_{time.strftime('%Y%m%d-%H%M%S')}.csv")
    os.rename(path, arch)
    return arch
