#!/usr/bin/env python3
"""The ONE Calibre write-ops executor.

Two callers, one loop:
  - the CLI, via `calibre-debug -e _writer.py` against its own `DB(LIB)` handle;
  - in-process (a Calibre plugin), against the live `gui.current_db.new_api`.
They must not fork: a second executor is a second set of coercion rules, and the two would
disagree about what "set tags on book 6585" means on exactly the day it matters.

Deliberately stdlib-only AT IMPORT TIME — `calibre.*` is imported lazily inside the one branch
that needs it. Two reasons, both load-bearing:
  - the executor is testable under plain CPython with a fake `api`, so CI covers it without
    Calibre installed;
  - `_writer.py` can import it. `calibre-debug -e` inserts only the *script's own directory*
    on sys.path, so under that interpreter there is no importable `scourgify` package — this
    module has to stand alone, and therefore imports nothing from scourgify either.

Op shapes (`common.op_*` builds them; `_writer.py`'s docstring is the JSON contract):
  {"op":"create_column","label":"wrangled","name":"Wrangled","datatype":"datetime","is_multiple":false}
  {"op":"set_field","field":"tags","values":{"<book_id>": ["a","b"] | "scalar"}}
  {"op":"stamp_now","field":"#wrangled","books":[id,...] | null}     # null = all books
  {"op":"set_pref","key":"namespaced:...","value":{...}}
"""


def coerce(api, field, values: dict) -> dict:
    """{book_id: raw value} -> {int book_id: value shaped for the column}.

    Book ids arrive as STRINGS from the ops JSON (json object keys) and as ints from an
    in-process caller handing over live dicts. Both land on int here — otherwise the two write
    paths would address different books from the same change-set."""
    mult = bool(api.field_metadata.all_metadata().get(field, {}).get("is_multiple"))
    out = {}
    for b, v in values.items():
        if mult:
            out[int(b)] = tuple(v) if isinstance(v, list) else (v,)
        else:
            out[int(b)] = (v[0] if isinstance(v, list) and v else v) or None
    return out


def apply_ops(api, ops, legacy=None, reopen=None, now=None, out=print) -> None:
    """Apply write-ops through Calibre's API. `api` is a Cache (`new_api`).

    `legacy` + `reopen` are the CLI writer's extras: only the legacy DB object can create a
    custom column, and the column is unusable until the DB is re-instantiated (`reopen()` ->
    a fresh legacy DB). `create_column` is deliberately OUT of the in-process contract — that
    reopen would desync a live GUI's models — so a plugin passes neither and the op raises.

    `now` supplies the timestamp for `stamp_now` (default: Calibre's own `now()`, imported
    lazily so this module still imports without Calibre). `out` is where progress lines go —
    print for the CLI, the job log for a plugin."""
    for op in ops:
        kind = op["op"]
        if kind == "create_column":
            label = op["label"].lstrip("#")
            if legacy is None:
                raise ValueError(f"create_column (#{label}) is not available in-process — it needs the "
                                 "legacy DB object and a reopen, which would desync a live GUI's models. "
                                 "Create columns with `scourgify setup` (Calibre closed).")
            if "#" + label not in api.field_metadata.all_field_keys():
                legacy.create_custom_column(label, op["name"], op["datatype"], op.get("is_multiple", False))
                if reopen is not None:
                    legacy = reopen(); api = legacy.new_api      # reopen so the new column is usable
                out(f"  created #{label}")
        elif kind == "set_field":
            vals = coerce(api, op["field"], op["values"])
            api.set_field(op["field"], vals)
            out(f"  set {op['field']}: {len(vals)} books")
        elif kind == "stamp_now":
            stamp = now
            if stamp is None:
                from calibre.utils.date import now as stamp        # lazy: Calibre-only import
            books = op["books"] if op.get("books") is not None else list(api.all_book_ids())
            ts = stamp()
            api.set_field(op["field"], {int(b): ts for b in books})
            out(f"  stamped {op['field']}: {len(books)} books")
        elif kind == "set_pref":
            api.set_pref(op["key"], op["value"])
            out(f"  set pref {op['key']}")
        else:
            raise ValueError(f"unknown op {kind!r}")
