#!/usr/bin/env python3
# Targeted character merges (user corrections). Run: calibre-debug -e apply_char_fixes.py
from calibre.library import db as DB
LIB = "/Users/andrei/Library/Mobile Documents/com~apple~CloudDocs/Calibre/fanfiction"
lib = DB(LIB).new_api
merges = {
    "Andromeda Black Tonks": "Andromeda Tonks",
    "Bellatrix Black Lestrange": "Bellatrix Lestrange",
    "Bellatrix": "Bellatrix Lestrange",
    "OC child": "SI/OC",
    "OC Child Character - Character": "SI/OC",
}
gi = lib.get_item_ids('#characters', list(merges))       # {name: id|None}
ren = {iid: merges[name] for name, iid in gi.items() if iid is not None}
print("will merge:")
for name, iid in gi.items():
    print(f"   {'FOUND ' if iid else 'MISSING'} {name!r} -> {merges[name]!r}")
lib.rename_items('#characters', ren)
print(f"\nrenamed {len(ren)} items. done.")
