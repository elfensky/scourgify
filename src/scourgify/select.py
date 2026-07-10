#!/usr/bin/env python3
"""The one owner of "which books does this run operate on".

Used by classify (--incremental / --last / --since / default sparse mode) and by the
wizard's status header — one implementation, so the header count and the classify
target list can never drift apart.

A book is NEW/CHANGED since its classify stamp (#wrangled) iff
  - it has no stamp (never classified), or
  - #updated (the site-side update date FanFicFare writes) is newer than the stamp, or
  - books.timestamp (Calibre's added-date; FanFicFare re-downloads bump it) is newer —
    this catches fics whose site update predates the stamp but was fetched after.
books.last_modified is deliberately NOT a clock: scourgify's own writes bump it, so
using it would mark half the library "changed" after every wrangle apply.

Every picker returns ids newest-added-first, so --batch/--limit caps eat the new
books first instead of decade-old sparse ones.
"""
import collections, sqlite3

from scourgify.common import read_custom_column

STAMP = "#wrangled"        # per-book datetime: when classify last processed it (stamped on apply)


def _key(v):
    """Datetime-ish -> lexicographically comparable 'YYYY-MM-DD HH:MM:SS' prefix ('' if unset).
    Calibre stores everything UTC, so string comparison is order-correct; date-only values
    (like a bare #updated day) sort before any same-day timestamp, keeping > conservative."""
    return str(v)[:19] if v else ""


def changed_pure(added: dict, updated: dict, stamped: dict) -> dict:
    """{book: reason} for new/changed books; args are {book: datetime-ish} dicts. Pure — see tests."""
    out = {}
    for b, ts in added.items():
        w = _key(stamped.get(b))
        if not w: out[b] = "new"
        elif _key(updated.get(b)) > w: out[b] = "updated"
        elif _key(ts) > w: out[b] = "re-fetched"
    return out


def _clocks(con: sqlite3.Connection) -> tuple[dict, dict, dict]:
    added = dict(con.execute("SELECT id, timestamp FROM books"))
    return added, read_custom_column(con, "#updated") or {}, read_custom_column(con, STAMP) or {}


def changed(con: sqlite3.Connection) -> dict:
    """{book: reason} for books new/changed since their classify stamp."""
    return changed_pure(*_clocks(con))


def pick(con: sqlite3.Connection, mode: str = "incremental", n: int = 0,
         since: str = "", min_tags: int = 2) -> list[int]:
    """[book_id ...] newest-added-first for one scope:
      incremental — changed() books only            last   — the n most recently added
      since       — added OR site-updated >= date   sparse — fewer than min_tags tags
      all         — everything"""
    added, upd, stamped = _clocks(con)
    newest = sorted(added, key=lambda b: (_key(added[b]), b), reverse=True)
    if mode == "incremental":
        ch = changed_pure(added, upd, stamped)
        return [b for b in newest if b in ch]
    if mode == "last":
        return newest[:n]
    if mode == "since":
        return [b for b in newest if _key(added[b])[:10] >= since or _key(upd.get(b))[:10] >= since]
    if mode == "sparse":
        tagn = collections.Counter(b for (b,) in con.execute("SELECT book FROM books_tags_link"))
        return [b for b in newest if tagn[b] < min_tags]
    if mode == "all":
        return newest
    # internal invariant guard: `mode` comes from argparse choices, so an unknown value is a
    # programmer error (ValueError), not a user-facing failure — hence not the house SystemExit.
    raise ValueError(f"unknown scope mode: {mode!r}")
