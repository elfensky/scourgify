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
import collections, os, sqlite3

from scourgify.common import GuardrailError, read_custom_column

STAMP = "#wrangled"        # per-book datetime: when classify last processed it (stamped on apply)


def _key(v):
    """Datetime-ish -> lexicographically comparable 'YYYY-MM-DD HH:MM:SS' prefix ('' if unset).
    Calibre stores everything UTC, so string comparison is order-correct; date-only values
    (like a bare #updated day) sort before any same-day timestamp, keeping > conservative."""
    return str(v)[:19] if v else ""


def _tokens(spec: str, depth: int = 0):
    """Comma-separated tokens, with '@file' expanded to its contents (one level deep).
    ponytail: one level is the ceiling — a file that references another file is a loop
    waiting to happen, and nobody needs an include graph to name fifty books."""
    for raw in spec.split(","):
        t = raw.strip()
        if not t: continue
        if not t.startswith("@"):
            yield t; continue
        if depth:
            raise GuardrailError(f"--books: '@file' inside a file is not supported ({t})")
        path = os.path.expanduser(t[1:])
        try: text = open(path).read()
        except (OSError, UnicodeDecodeError) as e:
            raise GuardrailError(f"--books: cannot read {path}: {e}")
        yield from _tokens(",".join(ln.split("#")[0] for ln in text.splitlines()), depth + 1)


def parse_books(spec: str) -> list[int]:
    """'1,2,3' | '10-20' | '@ids.txt' | any comma-combination -> de-duplicated [book_id ...],
    order preserved. A file holds ids one per line (or comma-separated); '#' starts a comment.
    SystemExit on anything unparseable — this is user input, not an internal invariant."""
    out = []
    for t in _tokens(spec):
        if "-" in t:
            lo, _, hi = t.partition("-")
            try: lo, hi = int(lo), int(hi)
            except ValueError: raise GuardrailError(f"--books: bad range {t!r} (expected 'LOW-HIGH')")
            if hi < lo: raise GuardrailError(f"--books: empty range {t!r} (high is below low)")
            out.extend(range(lo, hi + 1))
        else:
            try: out.append(int(t))
            except ValueError: raise GuardrailError(f"--books: {t!r} is not a book id")
    out = list(dict.fromkeys(out))          # de-dup, first-seen order
    if not out:
        raise GuardrailError(f"--books: {spec!r} names no book ids")
    return out


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


MIN_DESC = 40           # classify.gather() keeps a book only if its text reaches this many chars


def sendable(con: sqlite3.Connection, text_fallback: bool = False) -> set:
    """Books classify.gather() could actually send: a description of at least MIN_DESC chars —
    the same test gather applies — or, with --text-fallback, any book with a format file whose
    prose can be sampled instead.

    Deliberately a cheap DB-only predicate, not an extraction pass: it is optimistic on the
    fallback side (extract() can still come back empty on a DRM'd or odd file), so the scope may
    keep a handful of books that turn out unsendable. Being optimistic is the safe direction —
    it can leave a book in the set, never silently drop one that was classifiable."""
    from scourgify.booktext import strip_html
    ok = {b for b, t in con.execute("SELECT book, text FROM comments")
          if len(strip_html(t or "")) >= MIN_DESC}
    if text_fallback:                             # a file to sample is enough on its own. Asking the
        ok |= {b for (b,) in con.execute(         # data table directly, not booktext.paths(), which
            "SELECT DISTINCT book FROM data")}    # resolves absolute paths and so needs CALIBRE_LIBRARY
    return ok


def pick(con: sqlite3.Connection, mode: str = "incremental", n: int = 0,
         since: str = "", min_tags: int = 2, ids: list[int] | None = None,
         seen: set | None = None, text_fallback: bool = True) -> list[int]:
    """[book_id ...] newest-added-first for one scope:
      incremental — changed() books only            last   — the n most recently added
      since       — added OR site-updated >= date   sparse — fewer than min_tags tags
      all         — everything                      ids    — exactly these (absent ones dropped)
      unclassified — never attempted and sendable; both defaults live HERE (see below)"""
    added, upd, stamped = _clocks(con)
    newest = sorted(added, key=lambda b: (_key(added[b]), b), reverse=True)
    if mode == "unclassified":
        # Two filters, and BOTH are what make this scope finite — the property that lets it be
        # chunked. `seen` (artifacts.classified_ids) retires books already attempted; `sendable`
        # excludes books gather() would drop for thin text, which would otherwise sit in "never
        # classified" forever, re-selected at the head of every batch and never able to leave.
        # A book with no usable text is not outstanding work, it is unclassifiABLE.
        #
        # Both defaults live HERE, not at the call sites: seen=None means classified_ids(), and
        # text_fallback defaults to True — the scope a wizard-driven run actually resolves. Every
        # counter (wizard header, plugin, smoke check, a future dashboard) gets the same number by
        # asking bare; composing the invariant by hand is what let the plugin's count silently
        # diverge from the wizard's. Pass seen= to override (tests pin the filter logic that way),
        # or text_fallback=False to price a run that won't sample book text.
        if seen is None:
            from scourgify.artifacts import classified_ids
            seen = classified_ids()
        ok = sendable(con, text_fallback)
        return [b for b in newest if b not in seen and b in ok]
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
    if mode == "ids":
        want = set(ids or ())
        return [b for b in newest if b in want]
    if mode == "all":
        return newest
    # internal invariant guard: `mode` comes from argparse choices, so an unknown value is a
    # programmer error (ValueError), not a user-facing failure — hence not the house SystemExit.
    raise ValueError(f"unknown scope mode: {mode!r}")
