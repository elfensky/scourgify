#!/usr/bin/env python3
"""Re-derive #status from activity (#updated age) for the activity family: In-Progress/Hiatus/Abandoned.
Idempotent & self-correcting — re-run after an #updated refresh and the status re-derives automatically.

  scourgify staleness                          # audit (read-only, no changes)
  scourgify staleness --apply                  # write #status (Calibre CLOSED)

Rule: <STALE yrs -> In-Progress | STALE..DEAD -> Hiatus | >=DEAD -> Abandoned. Tunable: --stale-years 2 --dead-years 5.
Completed/Dropped/Rewritten and books without an #updated date are NEVER changed."""
import argparse, datetime, collections
from scourgify.common import (load_config, ro_connect, read_custom_column, run_writer,
                              op_set_field, titles as book_titles)
from scourgify import select

ACTIVITY = {"In-Progress", "Hiatus", "Abandoned"}      # re-derived from activity
# everything else (Completed, Dropped, Rewritten, blank) is left untouched


def derive(current: str, age_years: float | None, stale_years: float, dead_years: float) -> str:
    """The pure rule: what should this book's status be, given its current status and age?"""
    if current not in ACTIVITY: return current         # final/explicit/blank -> unchanged
    if age_years is None: return current               # no date -> can't assess
    return "In-Progress" if age_years < stale_years else "Hiatus" if age_years < dead_years else "Abandoned"


def compute(stale_years: float = 2.0, dead_years: float = 5.0,
            books=None) -> tuple[str, list]:
    """-> (status_label, [(book, old, new, age_years), ...]) for books whose status would change.
    books: an iterable of ids to restrict to, or None for the whole library. Each book's status
    depends only on its own #updated age, so a plain filter is the whole of the scoping."""
    con = ro_connect()
    status_label = load_config()["columns"].get("status") or "#status"
    status = read_custom_column(con, status_label)
    updated = read_custom_column(con, "#updated")
    if status is None or updated is None:
        missing = [l for l, v in ((status_label, status), ("#updated", updated)) if v is None]
        raise SystemExit(f"missing column(s): {', '.join(missing)} — run `scourgify setup` first.")
    today = datetime.date.today()
    def age(b):
        try: return (today - datetime.date.fromisoformat(str(updated.get(b))[:10])).days / 365.25
        except Exception: return None
    want = None if books is None else set(books)
    if want is not None:
        # membership is the BOOKS table, not the #status column: a book with no status set is
        # still in the library (about a fifth of a real FanFicFare library), and counting it as
        # absent turned an informational note into a lie about the user's own ids.
        known = {r[0] for r in con.execute("SELECT id FROM books")}
        absent = [b for b in want if b not in known]
        if absent: print(f"  note: {len(absent)} requested id(s) not in the library")
    rows = []
    for b, s in status.items():
        if want is not None and b not in want: continue
        n = derive(s, age(b), stale_years, dead_years)
        if n != s: rows.append((b, s, n, age(b)))
    return status_label, rows


def status_line(r: tuple, title: str = "") -> str:
    """One #status change as a review line: (book, old, new, age). Pure."""
    b, old, new, age = r
    return f"[bold]#{b}[/] {title[:44]}  [dim]{old or '(none)'}[/] → [cyan]{new}[/]  [dim]{age:.1f}y[/]"


def step(status_label: str, rows: list) -> list:
    """1-by-1 review of the proposed #status changes -> the ACCEPTED rows ([] = nothing decided).
    Lives here, not in the wizard: CLAUDE.md's rule is that a wizard stage calls the same engine
    function the subcommand does, so `staleness --apply --step` and the wizard share one path."""
    from scourgify import ui
    if not ui.interactive():
        raise SystemExit("--step needs an interactive terminal (omit it to apply every change).")
    con = ro_connect(); titles = book_titles(con); con.close()
    acc, _, action = ui.checklist(f"{status_label} changes — untick to leave a book alone",
                                  [status_line(r, str(titles.get(r[0], ""))) for r in rows])
    return [] if action in ("skip", "quit") else [rows[i] for i in acc]


def write(status_label: str, rows: list) -> None:
    run_writer([op_set_field(status_label, {b: n for b, o, n, _ in rows})])


def show(label: str, rows: list) -> None:
    """The ONE dry-run renderer (CLI + wizard): transition counts + examples, rich-or-plain."""
    from scourgify import report
    trans = collections.Counter(f"{o} → {n}" for _, o, n, _ in rows)
    report.table(f"{label} re-derivations — {len(rows)} book(s)", ["transition", "books"],
                 [[k, str(c)] for k, c in trans.most_common()], right=(1,))
    if rows:
        report.say("examples: " + ", ".join(f"#{b} {o}→{n} ({yrs:.1f}y)" for b, o, n, yrs in rows[:5]), "dim")


def main() -> None:
    p = argparse.ArgumentParser(description="Re-derive #status from #updated age (activity family only).")
    p.add_argument("--apply", action="store_true", help="write #status (Calibre closed)")
    p.add_argument("--step", action="store_true",
                   help="with --apply: review each book's #status change 1-by-1 (untick to leave it alone)")
    p.add_argument("--stale-years", type=float, default=2)
    p.add_argument("--dead-years", type=float, default=5)
    p.add_argument("--books", default=None, metavar="SPEC",
                   help="only these books: '1,2,3', '10-20', '@ids.txt' (one id per line), or a combination")
    p.add_argument("--last", type=int, default=0, metavar="N",
                   help="only the N most recently added books (the same N as classify --last)")
    a = p.parse_args()

    if a.books is not None and a.last:
        raise SystemExit("--books and --last are two ways to name the same thing — pick one.")
    books = select.parse_books(a.books) if a.books is not None else None
    if a.last:
        from scourgify.common import ro_connect
        con = ro_connect(); books = select.pick(con, "last", n=a.last); con.close()
    label, rows = compute(a.stale_years, a.dead_years, books)
    print(f"staleness audit  (today={datetime.date.today()}, stale>={a.stale_years}y, dead>={a.dead_years}y"
          + (f", scoped to {len(books)} book(s)" if books is not None else "") + ")")
    show(label, rows)

    if a.apply:
        if a.step:
            rows = step(label, rows)
            if not rows:
                print("(nothing decided — nothing written.)"); return
        write(label, rows)
        print(f"re-derived {label} for {len(rows)} books.")
    else:
        print("\nDry run. To write: scourgify staleness --apply   (Calibre closed)")


if __name__ == "__main__":
    main()
