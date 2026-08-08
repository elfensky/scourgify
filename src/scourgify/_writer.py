#!/usr/bin/env python3
"""Internal Calibre write helper — invoked UNDER calibre-debug by common.run_writer(), never by hand.

The standalone (system-python) tools compute everything via read-only sqlite, then shell out to:
    calibre-debug -e _writer.py -- <ops.json>
to perform the writes through Calibre's API — the only fast batched write path (calibredb's set_metadata is
one book per process).

This file is GLUE ONLY: resolve the library, read the ops JSON, open a DB handle, hand it to the shared
executor. The executor itself lives in ops.py because a Calibre plugin applies the *same* ops in-process
against the live gui.current_db.new_api, and two copies of that loop would eventually disagree.

ops.json = [ {op: ...}, ... ]:
  {"op":"create_column","label":"wrangled","name":"Wrangled","datatype":"datetime","is_multiple":false}
  {"op":"set_field","field":"tags","values":{"<book_id>": ["a","b"] | "scalar"}}   # shape coerced to the column
  {"op":"stamp_now","field":"#wrangled","books":[id,...] | null}                    # null = all books
  {"op":"set_pref","key":"namespaced:...","value":{...}}

Never import rich / ui / wizard / report here: Calibre's bundled Python has an empty site-packages.
"""
import os, sys, json
from calibre.library import db as DB
try:
    from scourgify.ops import apply_ops    # installed package (in-process callers, tests)
except ImportError:
    from ops import apply_ops              # calibre-debug -e: sys.path[0] is this file's own directory

LIB = os.path.expanduser(os.environ.get("CALIBRE_LIBRARY", ""))
if not LIB: raise SystemExit("_writer: CALIBRE_LIBRARY not set")
ops = json.load(open(sys.argv[-1]))
legacy = DB(LIB)
apply_ops(legacy.new_api, ops, legacy=legacy, reopen=lambda: DB(LIB))
print("WROTE.")
