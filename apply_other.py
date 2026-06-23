#!/usr/bin/env python3
# Other-columns cleanup: clear series, normalize publishers, strip mobi-asin. Run: calibre-debug -e apply_other.py [-- --apply]
import sys
APPLY = "--apply" in sys.argv
LIB = "/Users/andrei/Library/Mobile Documents/com~apple~CloudDocs/Calibre/fanfiction"
from calibre.library import db as DB
lib = DB(LIB).new_api
ids = lib.all_book_ids()

# 1. clear series (fandom-duplicate junk) + reset index
ser_clear = {b: '' for b in ids if lib.field_for('series', b)}
idx_reset = {b: 1.0 for b in ser_clear}

# 2. normalize publisher variants -> one canonical per site
pub_merges = {"FanFiction.net": "www.fanfiction.net", "Adult-FanFiction.org": "www.adult-fanfiction.org"}
gi = lib.get_item_ids('publisher', list(pub_merges))
pub_ren = {iid: pub_merges[name] for name, iid in gi.items() if iid is not None}

# 3. strip mobi-asin identifiers (keep url/uri)
idc = {}
for b in ids:
    d = lib.field_for('identifiers', b) or {}
    if 'mobi-asin' in d:
        idc[b] = {k: v for k, v in d.items() if k != 'mobi-asin'}

print("APPLY" if APPLY else "PRE-APPLY (no write)")
print(f"  series: clear on {len(ser_clear)} books")
print(f"  publisher merges: {[(n) for n,i in gi.items() if i]} -> canonical")
print(f"  mobi-asin: strip from {len(idc)} books")
if APPLY:
    lib.set_field('series', ser_clear)
    lib.set_field('series_index', idx_reset)
    if pub_ren: lib.rename_items('publisher', pub_ren)
    lib.set_field('identifiers', idc)
    print("\nWROTE all three.")
else:
    print("\nRe-run with -- --apply to write.")
