#!/usr/bin/env python3
"""Re-derive #status from activity (#updated age) for the activity family: In-Progress/Hiatus/Abandoned.
Idempotent & self-correcting — re-run after an #updated refresh and the status re-derives automatically.

  scourgify staleness                          # audit (read-only, no changes)
  scourgify staleness --apply                  # write #status (Calibre CLOSED)

Rule: <STALE yrs -> In-Progress | STALE..DEAD -> Hiatus | >=DEAD -> Abandoned. Tunable: --stale-years 2 --dead-years 5.
Completed/Dropped/Rewritten and books without an #updated date are NEVER changed."""
import argparse, datetime, collections
from scourgify.common import load_config, ro_connect, read_custom_column, run_writer, op_set_field

ACTIVITY = {"In-Progress", "Hiatus", "Abandoned"}      # re-derived from activity
# everything else (Completed, Dropped, Rewritten, blank) is left untouched


def derive(current: str, age_years: float | None, stale_years: float, dead_years: float) -> str:
    """The pure rule: what should this book's status be, given its current status and age?"""
    if current not in ACTIVITY: return current         # final/explicit/blank -> unchanged
    if age_years is None: return current               # no date -> can't assess
    return "In-Progress" if age_years < stale_years else "Hiatus" if age_years < dead_years else "Abandoned"


def compute(stale_years: float = 2.0, dead_years: float = 5.0) -> tuple[str, list]:
    """-> (status_label, [(book, old, new, age_years), ...]) for books whose status would change."""
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
    rows = []
    for b, s in status.items():
        n = derive(s, age(b), stale_years, dead_years)
        if n != s: rows.append((b, s, n, age(b)))
    return status_label, rows


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
    p.add_argument("--stale-years", type=float, default=2)
    p.add_argument("--dead-years", type=float, default=5)
    a = p.parse_args()

    label, rows = compute(a.stale_years, a.dead_years)
    print(f"staleness audit  (today={datetime.date.today()}, stale>={a.stale_years}y, dead>={a.dead_years}y)")
    show(label, rows)

    if a.apply:
        write(label, rows)
        print(f"re-derived {label} for {len(rows)} books.")
    else:
        print("\nDry run. To write: scourgify staleness --apply   (Calibre closed)")


if __name__ == "__main__":
    main()
