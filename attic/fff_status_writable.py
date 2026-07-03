"""Let FanFicFare keep #status current: set custom_cols_newonly['#status'] = False so a re-fetch writes
the source's real status (In-Progress / Completed). Pairs with staleness.py, which re-overlays the
activity inference (stale In-Progress -> Hiatus/Abandoned) on its next run. #genres stays protected.

  calibre-debug -e fff_status_writable.py            # pre-apply (no write)
  calibre-debug -e fff_status_writable.py -- --apply # write (Calibre CLOSED)
"""
import os, sys, copy
APPLY = "--apply" in sys.argv
LIB = os.path.expanduser(os.environ.get("CALIBRE_LIBRARY", ""))
if not LIB: raise SystemExit("Set CALIBRE_LIBRARY to your Calibre library folder.")
from calibre.library import db as DB
api = DB(LIB).new_api
KEY = 'namespaced:FanFicFarePlugin:settings'
s = copy.deepcopy(api.pref(KEY))
print("#status newonly  before:", s['custom_cols_newonly'].get('#status'),
      "| #genres newonly:", s['custom_cols_newonly'].get('#genres'))
s['custom_cols_newonly']['#status'] = False          # FFF may now update #status on fetch
print("#status newonly  after :", s['custom_cols_newonly'].get('#status'))
if APPLY:
    api.set_pref(KEY, s)
    print("WROTE. verify #status newonly =", api.pref(KEY)['custom_cols_newonly'].get('#status'))
else:
    print("Re-run with -- --apply to write (Calibre closed).")
