#!/usr/bin/env python3
"""Re-derive #status from activity (#updated age) for the activity family: In-Progress/Hiatus/Abandoned.
Idempotent & self-correcting — re-run after an #updated refresh and the status re-derives automatically.

  python3 staleness.py                          # audit (read-only, no changes)
  python3 staleness.py --apply                  # write #status (Calibre CLOSED)

Rule: <STALE yrs -> In-Progress | STALE..DEAD -> Hiatus | >=DEAD -> Abandoned. Tunable: --stale-years 2 --dead-years 5.
Completed/Dropped/Rewritten and books without an #updated date are NEVER changed."""
import os, sys, sqlite3, datetime, collections

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.expanduser(os.environ.get("CALIBRE_LIBRARY", ""))
if not LIB: raise SystemExit("Set CALIBRE_LIBRARY to your Calibre library folder.")
DB = os.path.join(LIB, "metadata.db")
def argval(flag, d): return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else d
STALE = float(argval("--stale-years", "2"))
DEAD = float(argval("--dead-years", "5"))
APPLY = "--apply" in sys.argv
TODAY = datetime.date.today()
ACTIVITY = {"In-Progress", "Hiatus", "Abandoned"}      # re-derived from activity
# everything else (Completed, Dropped, Rewritten, blank) is left untouched

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
cols = {lbl: cid for cid, lbl in con.execute("select id, label from custom_columns")}
def colvals(cid):
    tbl, link = f"custom_column_{cid}", f"books_custom_column_{cid}_link"
    has = con.execute("select count(*) from sqlite_master where type='table' and name=?", (link,)).fetchone()[0]
    q = (f"select l.book, c.value from {link} l join {tbl} c on c.id=l.value" if has
         else f"select book, value from {tbl}")
    return {b: v for b, v in con.execute(q)}
status = colvals(cols["status"])
updated = colvals(cols["updated"])

def age(b):
    v = updated.get(b)
    try: return (TODAY - datetime.date.fromisoformat(str(v)[:10])).days / 365.25
    except Exception: return None

def derive(b):
    s = status.get(b)
    if s not in ACTIVITY: return s                     # final/explicit/blank -> unchanged
    a = age(b)
    if a is None: return s                             # no date -> can't assess
    return "In-Progress" if a < STALE else "Hiatus" if a < DEAD else "Abandoned"

changes = {b: (status[b], derive(b)) for b in status if derive(b) != status.get(b)}
trans = collections.Counter(f"{o} -> {n}" for o, n in changes.values())
print(f"staleness audit  (today={TODAY}, stale>={STALE}y, dead>={DEAD}y)")
print(f"  books reclassified: {len(changes)}")
for k, c in trans.most_common(): print(f"    {k:24} {c}")
print("  examples:")
for b, (o, n) in list(changes.items())[:10]:
    a = age(b); print(f"    {o:12}->{n:12} ({a:.1f}y) #{b}")

if APPLY:
    from wrangle import run_writer                      # standalone: write shells out to calibre-debug
    run_writer([{"op": "set_field", "field": "#status", "values": {str(b): n for b, (o, n) in changes.items()}}])
    print(f"re-derived #status for {len(changes)} books.")
else:
    print("\nDry run. To write: python3 staleness.py --apply   (Calibre closed)")
