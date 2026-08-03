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


def _arch_path(path: str, kind: str) -> str:
    """The <name>_<kind>_<ts>[_N].csv archive name — the convention itself, in one place.

    The _N probe (same shape as common._backup_path) is load-bearing, not tidiness: an
    *_applied_* archive IS state — classified_ids() reads it as the "already attempted" cursor.
    The timestamp is whole-second, and two archives in one second are routine (`--apply --step`,
    `promote --apply --backfill`); overwriting the first un-retires up to a batch of books, which
    the next `--unclassified` run re-selects and re-bills against a paid API."""
    base = path.replace(".csv", f"_{kind}_{time.strftime('%Y%m%d-%H%M%S')}")
    p, n = base + ".csv", 2
    while os.path.exists(p):
        p = f"{base}_{n}.csv"; n += 1
    return p


def archive(path: str, kind: str) -> str:
    """Set a consumed artifact aside as <name>_<kind>_<ts>.csv (kind: 'applied' | 'discarded'),
    so stale rows can never re-apply. -> the archive path."""
    arch = _arch_path(path, kind)
    os.rename(path, arch)
    return arch


def archive_rows(rows: list, kind: str, path: str | None = None, writer=None) -> str:
    """Archive an EXPLICIT row set under the same convention, leaving the live file alone.

    For a partial apply (`--apply --step`, where the user skips or quits partway): an
    `*_applied_*` archive is read back as "these books were written", so it must name only the
    rows that actually were. Leaving the live proposal in place also makes the caller's ordering
    crash-safe — the full record survives until the caller replaces it with the leftovers."""
    path = path or prop()
    arch = _arch_path(path, kind)
    (writer or write_proposal)(rows, arch)      # `writer` picks the row schema (proposal vs review)
    return arch


def _ids(path: str, col: str = "book_id") -> set:
    out = set()
    for r in read_rows(path):
        try: out.add(int(r[col]))
        except (KeyError, ValueError, TypeError): pass
    return out


def classified_ids() -> set:
    """Books classify has already ATTEMPTED — the cursor a "what's left" scope reads.

    Three sources, and the choice of each is load-bearing:
      *_applied_* archives — classification actually written to the library.
      the pending proposal — results in hand, awaiting the review step.
      classify_failures.csv — attempted and BLOCKED (e.g. Gemini's PROHIBITED_CONTENT, a
        deterministic ~14% of a mature library). An errored book gets no proposal row on
        purpose so it can be retried, but it must not stay "outstanding" forever: it would
        re-occupy the head of every future batch and be re-billed with no progress. The log is
        self-clearing (merge_failures drops a book that later succeeds), so this retires a book
        exactly as long as it stays blocked.

    Deliberately NOT *_discarded_* — discarding means the user threw those results away, so
    those books must stay candidates."""
    seen = set()
    for f in applied_proposals(): seen |= _ids(f)
    seen |= _ids(prop())
    seen |= _ids(fail())
    return seen


def applied_proposals() -> list:
    """Every archived applied proposal, oldest first — the naming convention is archive()'s,
    so the glob lives here with it (promote.backfill reads the book↔proposed_new record back)."""
    return sorted(glob.glob(prop().replace(".csv", "_applied_*.csv")))
