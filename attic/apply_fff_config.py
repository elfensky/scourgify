import sys, copy
APPLY = "--apply" in sys.argv
LIB = os.path.expanduser(os.environ.get("CALIBRE_LIBRARY", ""))
if not LIB:
    raise SystemExit("Set CALIBRE_LIBRARY to your Calibre library folder (the one containing metadata.db).")
from calibre.library import db as DB
api = DB(LIB).new_api
KEY = 'namespaced:FanFicFarePlugin:settings'
s = copy.deepcopy(api.pref(KEY))

before = {"include_in_series_lines": sum(1 for ln in s['personal.ini'].splitlines() if ln.strip().lower()=='include_in_series:category'),
          "#fandoms_map": s['custom_cols'].get('#fandoms'),
          "genres_newonly": s['custom_cols_newonly'].get('#genres'),
          "status_newonly": s['custom_cols_newonly'].get('#status')}

# 1. remove include_in_series:category (active line only)
s['personal.ini'] = '\n'.join(ln for ln in s['personal.ini'].splitlines() if ln.strip().lower() != 'include_in_series:category')
# 2. remap #fandoms <- category
s['custom_cols']['#fandoms'] = 'category'
# 3. protect cleaned columns from future overwrite
s['custom_cols_newonly']['#genres'] = True
s['custom_cols_newonly']['#status'] = True

after = {"include_in_series_lines": sum(1 for ln in s['personal.ini'].splitlines() if ln.strip().lower()=='include_in_series:category'),
         "#fandoms_map": s['custom_cols']['#fandoms'],
         "genres_newonly": s['custom_cols_newonly']['#genres'],
         "status_newonly": s['custom_cols_newonly']['#status']}
print("APPLY" if APPLY else "PRE-APPLY")
print("  before:", before)
print("  after :", after)
if APPLY:
    api.set_pref(KEY, s)
    chk = api.pref(KEY)
    print("\nWROTE. verify: #fandoms=%r genres_newonly=%r status_newonly=%r include_in_series=%d" % (
        chk['custom_cols']['#fandoms'], chk['custom_cols_newonly']['#genres'], chk['custom_cols_newonly']['#status'],
        sum(1 for ln in chk['personal.ini'].splitlines() if ln.strip().lower()=='include_in_series:category')))
else:
    print("\nRe-run with -- --apply to write.")
