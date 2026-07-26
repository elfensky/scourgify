#!/usr/bin/env python3
"""Owner of the data/ artifact files the tools hand each other: classify's proposal, the ranked
new-tag candidates, the failure log, and promote's review/ledger. Every cross-module reader and
writer of these formats goes through here, so the row schemas, the "; " in-cell list delimiter,
and the timestamped-archive convention are implementation — changed in one place, tested with
plain file round-trips. Stdlib-only (importable from every tool, like common; never _writer.py)."""
import csv
import glob
import os
import time

from scourgify.common import data_dir


# Artifact paths are FUNCTIONS (never import-time constants) so $SCOURGIFY_HOME set after
# import — the redirection every test relies on — reaches them too.
def prop() -> str:
    """classify's per-book proposal (added_tags / proposed_new)."""
    return os.path.join(data_dir(), "classify_proposal.csv")


def rank() -> str:
    """Aggregated proposed-new candidates for promotion review."""
    return os.path.join(data_dir(), "classify_newtags_ranked.csv")


def fail() -> str:
    """Books an engine errored on (retry with another engine)."""
    return os.path.join(data_dir(), "classify_failures.csv")


def review() -> str:
    """promote's adjudicated verdicts awaiting apply."""
    return os.path.join(data_dir(), "promote_review.csv")


def ledger() -> str:
    """Every decided candidate (skip on re-runs; feeds backfill)."""
    return os.path.join(data_dir(), "promote_ledger.csv")

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


def read_proposal(path: str | None = None) -> list:
    """Proposal rows with book_id as int and the tag columns as lists."""
    path = path or prop()
    return [{"book_id": int(r["book_id"]), "title": r.get("title", ""),
             "added_tags": split_tags(r.get("added_tags")), "proposed_new": split_tags(r.get("proposed_new"))}
            for r in read_rows(path)]


def write_proposal(rows: list, path: str | None = None) -> None:
    """rows: PROP_COLS dicts; the tag columns may be lists (joined here) or pre-joined strings.
    Written to a temp file + os.replace so a crash mid-write can never truncate an existing
    proposal (the rows are paid LLM results)."""
    path = path or prop()
    tmp = path + ".tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=PROP_COLS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            r = {k: r.get(k, "") for k in PROP_COLS}
            for k in ("added_tags", "proposed_new"):
                if isinstance(r[k], (list, tuple)): r[k] = join_tags(r[k])
            w.writerow(r)
    os.replace(tmp, path)


def write_ranked(rows: list, path: str | None = None) -> None:
    """rows: RANK_COLS-ordered lists (annotate_new's output)."""
    path = path or rank()
    with open(path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(RANK_COLS); w.writerows(rows)


def read_ranked(path: str | None = None) -> list:
    """Ranked-candidate rows by column name, count coerced to int."""
    path = path or rank()
    out = []
    for r in read_rows(path):
        try: cnt = int(r.get("count", 0) or 0)
        except ValueError: cnt = 0
        out.append({**r, "count": cnt})
    return out


def write_review(rows: list, path: str | None = None) -> None:
    """rows: REVIEW_COLS dicts (promote's verdicts)."""
    path = path or review()
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=REVIEW_COLS, extrasaction="ignore")
        w.writeheader()
        for r in rows: w.writerow({k: r.get(k, "") for k in REVIEW_COLS})


def append_ledger(tag: str, verdict: str, target: str, path: str | None = None) -> None:
    """Append one decided candidate to the promote ledger (header on first write)."""
    path = path or ledger()
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new: w.writerow(LEDGER_COLS)
        w.writerow([tag, verdict, target])


def merge_failures(prev: list, processed, failures: list) -> list:
    """prev (FAIL_COLS dict rows) + the books this run PROCESSED + this run's failures
    -> the new FAIL_COLS-ordered rows. Pure — see tests.

    The log means "failed and has not since succeeded". A book this run touched is re-stated
    only if it failed again, so recovering blocked books on another engine (the documented
    fix for Gemini's PROHIBITED_CONTENT) actually clears them. Books outside this run's scope
    are carried through untouched — the log is library-wide, the run is not."""
    done = {int(b) for b in processed}
    keep = [[r.get("book_id", ""), r.get("title", ""), r.get("reason", "")]
            for r in prev if str(r.get("book_id", "")).isdigit() and int(r["book_id"]) not in done]
    return list(failures) + keep


def write_failures(rows: list, path: str | None = None) -> None:
    """rows: FAIL_COLS-ordered lists — the books an engine errored on. Always rewrites the file
    (an empty `rows` clears it), so a clean run does not leave a stale log behind."""
    path = path or fail()
    with open(path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(FAIL_COLS); w.writerows(rows)


def archive(path: str, kind: str) -> str:
    """Set a consumed artifact aside as <name>_<kind>_<ts>.csv (kind: 'applied' | 'discarded'),
    so stale rows can never re-apply. -> the archive path."""
    arch = path.replace(".csv", f"_{kind}_{time.strftime('%Y%m%d-%H%M%S')}.csv")
    os.rename(path, arch)
    return arch


def applied_proposals() -> list:
    """Every archived applied proposal, oldest first — the naming convention is archive()'s,
    so the glob lives here with it (promote.backfill reads the book↔proposed_new record back)."""
    return sorted(glob.glob(prop().replace(".csv", "_applied_*.csv")))
