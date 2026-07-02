#!/usr/bin/env python3
import os
# Final pass: ASCII-fold any remaining non-ASCII tags (smart quotes/accents -> plain).
import sys, unicodedata
APPLY = "--apply" in sys.argv
LIB = os.path.expanduser(os.environ.get("CALIBRE_LIBRARY", ""))
if not LIB:
    raise SystemExit("Set CALIBRE_LIBRARY to your Calibre library folder (the one containing metadata.db).")
from calibre.library import db as DB
lib = DB(LIB).new_api
def fold(s):
    s = s.replace("’","'").replace("‘","'").replace("“",'"').replace("”",'"').replace("…","...").replace("–","-").replace("—","-")
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
names = [n for n in lib.all_field_names('tags') if any(ord(c) > 127 for c in n)]
ids = lib.get_item_ids('tags', names)
ren = {}
for name, iid in ids.items():
    f = fold(name).strip()
    if iid and f and f != name: ren[iid] = f
print("APPLY" if APPLY else "PRE-APPLY", "- non-ASCII tags to fold:", len(ren))
for name, iid in list(ids.items())[:5]:
    print("   %r -> %r" % (name[:40], fold(name)[:40]))
if APPLY:
    lib.rename_items('tags', ren)
    left = sum(1 for n in lib.all_field_names('tags') if any(ord(c) > 127 for c in n))
    print("WROTE. non-ASCII tags remaining:", left)
