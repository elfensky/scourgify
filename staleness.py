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


def main():
    p = argparse.ArgumentParser(description="Re-derive #status from #updated age (activity family only).")
    p.add_argument("--apply", action="store_true", help="write #status (Calibre closed)")
    p.add_argument("--stale-years", type=float, default=2)
    p.add_argument("--dead-years", type=float, default=5)
    a = p.parse_args()
    today = datetime.date.today()

    con = ro_connect()
    status_label = load_config()["columns"].get("status") or "#status"
    status = read_custom_column(con, status_label)
    updated = read_custom_column(con, "#updated")
    if status is None or updated is None:
        missing = [l for l, v in ((status_label, status), ("#updated", updated)) if v is None]
        raise SystemExit(f"missing column(s): {', '.join(missing)} — run `python3 wrangle.py setup` first.")

    def age(b):
        v = updated.get(b)
        try: return (today - datetime.date.fromisoformat(str(v)[:10])).days / 365.25
        except Exception: return None

    def derive(b):
        s = status.get(b)
        if s not in ACTIVITY: return s                 # final/explicit/blank -> unchanged
        yrs = age(b)
        if yrs is None: return s                       # no date -> can't assess
        return "In-Progress" if yrs < a.stale_years else "Hiatus" if yrs < a.dead_years else "Abandoned"

    changes = {b: (status[b], derive(b)) for b in status if derive(b) != status.get(b)}
    trans = collections.Counter(f"{o} -> {n}" for o, n in changes.values())
    print(f"staleness audit  (today={today}, stale>={a.stale_years}y, dead>={a.dead_years}y)")
    print(f"  books reclassified: {len(changes)}")
    for k, c in trans.most_common(): print(f"    {k:24} {c}")
    print("  examples:")
    for b, (o, n) in list(changes.items())[:10]:
        print(f"    {o:12}->{n:12} ({age(b):.1f}y) #{b}")

    if a.apply:
        run_writer([{"op": "set_field", "field": status_label, "values": {str(b): n for b, (o, n) in changes.items()}}])
        print(f"re-derived {status_label} for {len(changes)} books.")
    else:
        print("\nDry run. To write: python3 staleness.py --apply   (Calibre closed)")


if __name__ == "__main__":
    main()
