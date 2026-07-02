#!/usr/bin/env python3
"""Re-derive #status from activity (#updated age) for the activity family: In-Progress/Hiatus/Abandoned.
Idempotent & self-correcting — re-run after an #updated refresh and the status re-derives automatically.

  python3 staleness.py                          # audit (read-only, no changes)
  python3 staleness.py --apply                  # write #status (Calibre CLOSED)

Rule: <STALE yrs -> In-Progress | STALE..DEAD -> Hiatus | >=DEAD -> Abandoned. Tunable: --stale-years 2 --dead-years 5.
Completed/Dropped/Rewritten and books without an #updated date are NEVER changed."""
import argparse, datetime, collections
from common import load_config, ro_connect, read_custom_column, run_writer

ACTIVITY = {"In-Progress", "Hiatus", "Abandoned"}      # re-derived from activity
# everything else (Completed, Dropped, Rewritten, blank) is left untouched


def derive(current, age_years, stale_years, dead_years):
    """The pure rule: what should this book's status be, given its current status and age?"""
    if current not in ACTIVITY: return current         # final/explicit/blank -> unchanged
    if age_years is None: return current               # no date -> can't assess
    return "In-Progress" if age_years < stale_years else "Hiatus" if age_years < dead_years else "Abandoned"


def compute(stale_years=2.0, dead_years=5.0):
    """-> (status_label, [(book, old, new, age_years), ...]) for books whose status would change."""
    con = ro_connect()
    status_label = load_config()["columns"].get("status") or "#status"
    status = read_custom_column(con, status_label)
    updated = read_custom_column(con, "#updated")
    if status is None or updated is None:
        missing = [l for l, v in ((status_label, status), ("#updated", updated)) if v is None]
        raise SystemExit(f"missing column(s): {', '.join(missing)} — run `python3 wrangle.py setup` first.")
    today = datetime.date.today()
    def age(b):
        try: return (today - datetime.date.fromisoformat(str(updated.get(b))[:10])).days / 365.25
        except Exception: return None
    rows = []
    for b, s in status.items():
        n = derive(s, age(b), stale_years, dead_years)
        if n != s: rows.append((b, s, n, age(b)))
    return status_label, rows


def write(status_label, rows):
    run_writer([{"op": "set_field", "field": status_label, "values": {str(b): n for b, o, n, _ in rows}}])


def main():
    p = argparse.ArgumentParser(description="Re-derive #status from #updated age (activity family only).")
    p.add_argument("--apply", action="store_true", help="write #status (Calibre closed)")
    p.add_argument("--stale-years", type=float, default=2)
    p.add_argument("--dead-years", type=float, default=5)
    a = p.parse_args()

    label, rows = compute(a.stale_years, a.dead_years)
    trans = collections.Counter(f"{o} -> {n}" for _, o, n, _ in rows)
    print(f"staleness audit  (today={datetime.date.today()}, stale>={a.stale_years}y, dead>={a.dead_years}y)")
    print(f"  books reclassified: {len(rows)}")
    for k, c in trans.most_common(): print(f"    {k:24} {c}")
    print("  examples:")
    for b, o, n, yrs in rows[:10]:
        print(f"    {o:12}->{n:12} ({yrs:.1f}y) #{b}")

    if a.apply:
        write(label, rows)
        print(f"re-derived {label} for {len(rows)} books.")
    else:
        print("\nDry run. To write: python3 staleness.py --apply   (Calibre closed)")


if __name__ == "__main__":
    main()
