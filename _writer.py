#!/usr/bin/env python3
"""Internal Calibre write helper — invoked UNDER calibre-debug by wrangle.py / classify.py, never by hand.

The standalone (system-python) tools compute everything via read-only sqlite, then shell out to:
    calibre-debug -e _writer.py -- <ops.json>
to perform the writes through Calibre's API — the only fast batched write path (calibredb's set_metadata is
one book per process). One generic op list keeps Calibre access in a single ~30-line stub.

ops.json = [ {op: ...}, ... ]:
  {"op":"create_column","label":"wrangled","name":"Wrangled","datatype":"datetime","is_multiple":false}
  {"op":"set_field","field":"tags","values":{"<book_id>": ["a","b"] | "scalar"}}   # shape coerced to the column
  {"op":"stamp_now","field":"#wrangled","books":[id,...] | null}                    # null = all books
  {"op":"set_pref","key":"namespaced:...","value":{...}}
"""
import os, sys, json
from calibre.library import db as DB

LIB = os.path.expanduser(os.environ.get("CALIBRE_LIBRARY", ""))
if not LIB: raise SystemExit("_writer: CALIBRE_LIBRARY not set")
ops = json.load(open(sys.argv[-1]))
legacy = DB(LIB); api = legacy.new_api

for op in ops:
    kind = op["op"]
    if kind == "create_column":
        if "#" + op["label"].lstrip("#") not in api.field_metadata.all_field_keys():
            legacy.create_custom_column(op["label"].lstrip("#"), op["name"], op["datatype"], op.get("is_multiple", False))
            legacy = DB(LIB); api = legacy.new_api          # reopen so the new column is usable
            print(f"  created #{op['label'].lstrip('#')}")
    elif kind == "set_field":
        field = op["field"]
        mult = bool(api.field_metadata.all_metadata().get(field, {}).get("is_multiple"))
        vals = {}
        for b, v in op["values"].items():
            vals[int(b)] = (tuple(v) if isinstance(v, list) else (v,)) if mult else ((v[0] if isinstance(v, list) and v else v) or None)
        api.set_field(field, vals)
        print(f"  set {field}: {len(vals)} books")
    elif kind == "stamp_now":
        from calibre.utils.date import now as cal_now
        books = op["books"] if op.get("books") is not None else list(api.all_book_ids())
        ts = cal_now(); api.set_field(op["field"], {int(b): ts for b in books})
        print(f"  stamped {op['field']}: {len(books)} books")
    elif kind == "set_pref":
        api.set_pref(op["key"], op["value"]); print(f"  set pref {op['key']}")
print("WROTE.")
