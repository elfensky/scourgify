#!/usr/bin/env python3
"""The edit log — what scourgify wrote, to which book, in which run (#49).

One JSONL file, `data/edits.jsonl`, appended at the write funnel (`common.run_writer` and
`common.write_ops`), never by a tool module. It is the record the backups cannot be: a snapshot
answers "restore everything to this moment" and expires on a disk-usage schedule, while this
answers "what did scourgify do to book 6585" and is small enough to keep forever.

**Line shapes** (one JSON object per line, `kind` discriminates):

    {"kind":"run","run":ID,"ts":…,"tool":"wrangle","scope":"library","library":UUID,
     "engine":…,"model":…}                      # header — engine/model only for classify runs
    {"kind":"op","run":ID,"op":"set_field","book":6585,"field":"tags",
     "before":["Harem"],"after":["Harem","Isekai"],"undo":true}
    {"kind":"op","run":ID,"op":"stamp_now","field":"#wrangled","books":112,"undo":false}
    {"kind":"end","run":ID,"ts":…,"outcome":"ok","ops":3,"books":112}
    {"kind":"end","run":ID,"ts":…,"outcome":"skipped","ops":0,"books":0,
     "skipped":[[6585,"tags"]],"n_skipped":1}     # every op conflicted — see below

**A missing footer marks a partial run** — the process died mid-write. Its op lines are still
real and still undoable.

**Op lines are written BEFORE the ops apply**, which is the only ordering that survives a crash:
the alternative logs nothing when the write dies halfway and the record is needed most. It means
a line can describe a write that never landed — which costs nothing, because undo is conflict-
aware: replaying an op that never applied finds `current == before != after`, i.e. a conflict,
and the book is skipped and reported rather than clobbered.

**`--force` does not skip the before-read.** Force means "skip the wipe guard", not "write
blind": a forced run is the most dangerous kind and the one most likely to need undo, so the log
reads before-values whether or not the guard did. Cost is one column read, the same one the
guard was making anyway.

A failure to write the log is NOT swallowed. It raises before any op applies (the header and op
lines go down first), so a log that cannot be written means the library is untouched — the same
fail-closed posture as a failed backup.

**An op whose current value no longer matches the `expected` it was planned against is SKIPPED,
not clobbered** (plan 01-06, FOUND-05): the apply-time conflict filter drops it before either the
snapshot or the log's op lines are written, using this module's own `conflict()` predicate — the
same one undo uses, so "conflict" cannot come to mean two things. A run where every op conflicted
still gets a header and a footer (with zero op lines and a `skipped` list) so the attempt is
visible in History rather than disappearing, and it costs no snapshot.

`stamp_now`/`set_pref`/`create_column` are logged with `"undo": false` and excluded from replay:
their before-state is worthless (a stamp) or structural (a column). Stamps are logged as ONE
line with a book count rather than one line per book — a full-library stamp is 7,949 books on
this library and would multiply the log by the library size on every wrangle run, buying nothing
that undo can use.
"""
import json, os, time, uuid


def log_path() -> str:
    from scourgify.common import data_dir      # lazy: common imports this module at call time
    return os.path.join(data_dir(), "edits.jsonl")


def new_run_id() -> str:
    """Sortable and collision-proof: `<ts>-<8 hex>`.

    NOT a bare timestamp. #45 cost an archive to whole-second filenames — two runs in the same
    second (a guided wizard run fires several) would share an id, and every history/undo query
    keyed on it would silently mix two runs together."""
    return time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]


def _jsonable(v):
    """Log values as JSON: multi-value fields as sorted lists (stable, diffable — the conflict
    predicate compares them as sets, so order carries no meaning), anything exotic (a Calibre
    datetime) as its string form."""
    if isinstance(v, (set, frozenset, tuple, list)):
        return sorted(str(x) for x in v)
    return v if v is None or isinstance(v, (str, int, float, bool)) else str(v)


def before_values(read, ops: list) -> dict:
    """{field: {book_id: current value}} for exactly the books `ops` touches.

    `read(field, books) -> {book: value}` is injected because the two writers read the
    before-state from different places — the CLI from read-only sqlite (`common.column_values`),
    a plugin from the live handle (`common.values_via_api`), which is the authoritative state
    when a GUI is holding the library."""
    out = {}
    for o in ops:
        if o.get("op") == "set_field" and o.get("values"):
            books = [int(b) for b in o["values"]]
            out.setdefault(o["field"], {}).update(read(o["field"], books))
    return out


def records(run: str, ops: list, before: dict) -> list:
    """The op lines for one run: one per applied `(book, field)` `set_field`, one per other op."""
    out = []
    for o in ops:
        kind = o["op"]
        if kind == "set_field":
            cur = before.get(o["field"], {})
            for b, after in o["values"].items():
                out.append({"kind": "op", "run": run, "op": kind, "book": int(b), "field": o["field"],
                            "before": _jsonable(cur.get(int(b))), "after": _jsonable(after), "undo": True})
        elif kind == "stamp_now":
            out.append({"kind": "op", "run": run, "op": kind, "field": o["field"],
                        "books": len(o["books"]) if o.get("books") is not None else None, "undo": False})
        elif kind == "set_pref":
            out.append({"kind": "op", "run": run, "op": kind, "key": o["key"], "undo": False})
        elif kind == "create_column":
            out.append({"kind": "op", "run": run, "op": kind, "field": "#" + o["label"].lstrip("#"),
                        "undo": False})
    return out


def append(lines: list, path: str | None = None) -> None:
    """Append JSON lines. One open/close per call — this runs twice per write run, not per book."""
    path = path or log_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for r in lines:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def start(tool: str, ops: list, before: dict, scope=None, library=None,
          engine=None, model=None, path: str | None = None) -> dict:
    """Write the run header + every op line; return the run record to hand back to `finish()`.

    `engine`/`model` are for classify runs — an LLM's output cannot be assumed reproducible, so
    the run that produced a tag has to name the model that produced it."""
    run = new_run_id()
    head = {"kind": "run", "run": run, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "tool": tool}
    for k, v in (("scope", scope), ("library", library), ("engine", engine), ("model", model)):
        if v is not None: head[k] = v
    lines = records(run, ops, before)
    append([head, *lines], path)
    books = {l["book"] for l in lines if "book" in l}
    return {"run": run, "ops": len(lines), "books": len(books), "path": path}


def finish(rec: dict, outcome: str = "ok", skipped: list | None = None) -> None:
    """Close the run. Any outcome but a written footer means partial — see the module docstring.

    `skipped` ([[book, field], ...], the apply-time conflict filter's drops — plan 01-06) is
    folded into the footer as `skipped` plus its count `n_skipped`, only when non-empty — an
    ordinary run's footer is byte-identical to before this parameter existed."""
    foot = {"kind": "end", "run": rec["run"], "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "outcome": outcome, "ops": rec["ops"], "books": rec["books"]}
    if skipped:
        foot["skipped"] = [[b, f] for b, f in skipped]
        foot["n_skipped"] = len(skipped)
    append([foot], rec.get("path"))


def conflict(current, expected, multi: bool) -> bool:
    """Has `current` drifted from the `expected` value a plan/undo was computed against?

    THE predicate — apply-time conflict checks and undo replay share it, so "conflict" cannot
    come to mean two things. Multi-value fields compare AS SETS (order-insensitive, raw values,
    no alias or case normalization — a tag reordered by Calibre is not an edit; a tag whose case
    the user changed IS). Single-value fields compare as strings, with None and "" the same
    absence. Pure — see tests."""
    if multi:
        return {str(x) for x in (current or ())} != {str(x) for x in (expected or ())}
    return ("" if current is None else str(current)) != ("" if expected is None else str(expected))
