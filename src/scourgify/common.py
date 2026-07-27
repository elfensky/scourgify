#!/usr/bin/env python3
"""Shared core for the standalone tools (wrangle / classify / staleness).

Everything here runs under plain system python3. It owns the four things the tools
used to each carry a private copy of:
  - library resolution (CALIBRE_LIBRARY) — checked lazily, never at import time
  - read-only sqlite access + link-table-aware custom-column reading
  - normalization helpers (norm, ascii_fold) and the minimal TOML config reader
  - the single write funnel: run_writer() -> calibre-debug -e _writer.py
    (backs up metadata.db to data/backups/ before every write; refuses to run while Calibre is open)
"""
import os, re, sys, csv, time, glob, sqlite3, contextlib, collections, unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))   # the installed package dir (read-only)
DEFAULTS = os.path.join(HERE, "defaults")            # bundled generic maps — ship inside the package


def user_dir() -> str:
    """The one root for every user-owned file (config.toml, overrides/, data/).
    $SCOURGIFY_HOME wins (tests, experiments); else XDG:
    ($XDG_CONFIG_HOME or ~/.config)/scourgify. mac + Linux only — no Windows."""
    return os.environ.get("SCOURGIFY_HOME") or os.path.join(
        os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "scourgify")


# Per-run + per-user files live under user_dir(), not site-packages nor the invoking CWD:
# config.toml, overrides/, and data/ (proposals/intermediates + backups) all resolve there,
# so an installed copy has a stable home instead of writing relative to wherever it's launched.
# Paths are FUNCTIONS, never import-time constants — $SCOURGIFY_HOME set after import (tests)
# must still redirect the whole tree.
BACKUP_KEEP = 20                                      # keep this many newest snapshots; older ones are pruned
BACKUP_WARN = 500 * 1024 * 1024                       # wizard nudges to trim past this many bytes of snapshots
REJECT_COLS = ["ts", "stage", "book", "title", "kind", "column", "before", "after", "class"]


def data_dir() -> str:
    """data/ under user_dir() — personal review maps, proposals, intermediates (gitignored)."""
    return os.path.join(user_dir(), "data")


def backups_dir() -> str:
    """metadata.db snapshots taken before every write."""
    return os.path.join(data_dir(), "backups")


def rejects_path() -> str:
    """Per-item rejects from `--step` review (see overrides.py)."""
    return os.path.join(data_dir(), "rejects.csv")


def backups_size() -> tuple[int, int]:
    """(count, total_bytes) of the metadata.db snapshots in backups_dir(); (0, 0) if none."""
    files = glob.glob(os.path.join(backups_dir(), "*.db"))
    return len(files), sum(os.path.getsize(f) for f in files)


def log_rejects(rows: list[dict]) -> int:
    """Append reject dicts (REJECT_COLS keys; ts auto-stamped) to data/rejects.csv, creating
    the header on first write. The "separate list" that `scourgify overrides` reads back."""
    rows = [r for r in rows if r]
    if not rows: return 0
    os.makedirs(data_dir(), exist_ok=True)
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    new = not os.path.exists(rejects_path())
    with open(rejects_path(), "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=REJECT_COLS, extrasaction="ignore")
        if new: w.writeheader()
        for r in rows: w.writerow({"ts": ts, **r})
    return len(rows)


# ---------------- library resolution (lazy — importing this module never exits) ----------------
def library() -> str:
    lib = os.path.expanduser(os.environ.get("CALIBRE_LIBRARY", ""))
    if not lib:
        raise SystemExit("Set CALIBRE_LIBRARY to your Calibre library folder (the one containing metadata.db).")
    return lib

def db_path() -> str:
    return os.path.join(library(), "metadata.db")

def ro_connect() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path()}?mode=ro", uri=True)


# ---------------- interaction policy (the ONE answer to "is a human at the terminal") ----------------
class ScriptError(Exception):
    """A scripted run's answers don't fit the flow (queue exhausted, or an answer that isn't on
    offer). Deliberately NOT a SystemExit: wizard._stage_guard absorbs SystemExit — that's the
    guardrail-skips-a-stage path — which would swallow exactly the failure this seam exists to
    make loud, and hand back a green test that asserted nothing."""


# Canned answers for a scripted run: `SCOURGIFY_SCRIPT=w,s,n,q scourgify` for ad-hoc shell use,
# or common.scripted_answers([...]) in tests. Read ONCE at import — tests use the context manager,
# so this only constrains shell use, where the variable is set before launch anyway.
# ponytail: split on ',', so a checklist multi-toggle in an env-var script uses spaces ("1 3").
_script = ([a.strip() for a in os.environ["SCOURGIFY_SCRIPT"].split(",")]
           if "SCOURGIFY_SCRIPT" in os.environ else None)


def scripted() -> bool:
    """Is this run answering its own prompts? ([] still counts — the run IS scripted, it has
    merely run out, and the next prompt must raise rather than fall back to a keyboard.)"""
    return _script is not None


def script_next(what: str) -> str:
    """Pop the next canned answer. Exhausted = a real error: the script under-specifies the flow.
    `what` names the prompt so the failure says which one went unanswered."""
    if not _script:
        raise ScriptError(f"scripted run: no answer left for {what}")
    return _script.pop(0)


def script_bool(msg: str, default: bool) -> bool:
    """Pop a y/n answer ('' = the default, i.e. pressing enter). The ONE parser — ui.confirm and
    confirm() below both route here so the two prompts can't disagree about what 'y' means."""
    a = script_next(f"confirm {msg!r}").strip().lower()
    if a == "": return default
    if a in ("y", "yes"): return True
    if a in ("n", "no"): return False
    raise ScriptError(f"confirm {msg!r}: {a!r} is not y/n (or '' for the {default} default)")


@contextlib.contextmanager
def scripted_answers(answers):
    """Drive a scripted run from Python (tests). Restores the previous queue on exit, so a test
    that raises mid-flow can't leak its leftovers into the next one."""
    global _script
    prev, _script = _script, [str(a) for a in answers]
    try:
        yield
    finally:
        _script = prev


def interactive() -> bool:
    """stdin AND stdout are real TTYs, and no CI/NONINTERACTIVE override. Every tool asks this
    function — ui.interactive re-exports it — so the tools can't disagree about interactivity."""
    if _script is not None: return True     # a scripted run answers its own prompts; CI must not veto it
    if os.environ.get("CI") or os.environ.get("NONINTERACTIVE"):
        return False
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        return False


def confirm(msg: str, default: bool = False) -> bool:
    """The one plain y/n prompt (ui.confirm is its rich twin for wizard surfaces).
    Off a TTY / on EOF: the default. 3 retries on garbage input."""
    if scripted(): return script_bool(msg, default)
    if not interactive(): return default
    for _ in range(3):
        try: a = input(f"{msg} [{'Y/n' if default else 'y/N'}] ").strip().lower()
        except EOFError: return default
        if a == "": return default
        if a in ("y", "yes"): return True
        if a in ("n", "no"): return False
        print("  please answer y or n.")
    return default


def read_lines(path: str) -> list:
    """Lines of a text file without trailing newlines; [] if missing — the shared list-file
    reader (allow/block/junk lists). The CSV twin is artifacts.read_rows."""
    return [l.rstrip("\n") for l in open(path)] if os.path.exists(path) else []


# ---------------- normalization ----------------
def norm(s) -> str:
    s = str(s).strip().lower(); s = re.sub(r"[\[\]\(\)]", "", s); s = s.replace("&", "and")
    s = re.sub(r"[\s_\-/]+", " ", s); s = re.sub(r"[^\w ]", "", s, flags=re.UNICODE); return s.strip()

def ascii_fold(s: str) -> str:
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').replace("…", "...").replace("–", "-").replace("—", "-")
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


# ---------------- custom columns (single- and multi-value; link-table aware) ----------------
def custom_column_id(con: sqlite3.Connection, label: str) -> int | None:
    r = con.execute("SELECT id FROM custom_columns WHERE label=?", (label.lstrip("#"),)).fetchone()
    return r[0] if r else None

def read_custom_column(con: sqlite3.Connection, label: str, multi: bool = False) -> dict | None:
    """{book: value} (or {book: [values]} with multi=True) for a custom column; None if it doesn't exist.
    Handles both storage shapes: a books_custom_column_N_link table, or an inline `book` column."""
    i = custom_column_id(con, label)
    if i is None: return None
    link = f"books_custom_column_{i}_link"
    has_link = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (link,)).fetchone()
    q = (f"SELECT l.book, v.value FROM {link} l JOIN custom_column_{i} v ON v.id=l.value" if has_link
         else f"SELECT book, value FROM custom_column_{i}")
    out = collections.defaultdict(list) if multi else {}
    for b, v in con.execute(q):
        if multi: out[b].append(v)
        else: out[b] = v
    return dict(out)


IN_CAP = 500   # above this many ids, fetch all titles instead of building an IN() list (SQLite variable cap)

def titles(con: sqlite3.Connection, ids=None) -> dict:
    """{book_id: title}. ids=None (or a large set — see IN_CAP) fetches the whole table;
    the ONE owner of the title lookup every preview/report used to hand-roll."""
    ids = list(ids) if ids is not None else None
    if ids is not None and not ids: return {}
    if ids is None or len(ids) > IN_CAP:
        return dict(con.execute("SELECT id, title FROM books"))
    return dict(con.execute(f"SELECT id, title FROM books WHERE id IN ({','.join('?' * len(ids))})", ids))


def book_count(con: sqlite3.Connection) -> int:
    return con.execute("SELECT count(*) FROM books").fetchone()[0]


def current_tags(con: sqlite3.Connection) -> dict:
    """{book: set(tag names)} from the builtin tags link table — the read half of an
    add-tags union (classify apply, promote backfill)."""
    out = collections.defaultdict(set)
    for b, t in con.execute("SELECT l.book, t.name FROM books_tags_link l JOIN tags t ON t.id=l.tag"):
        out[b].add(t)
    return dict(out)


# ---------------- config (minimal TOML reader; no tomllib dependency) ----------------
def load_config(path: str | None = None) -> dict:
    cfg = {"columns": {"fandoms": "#fandoms", "characters": "#characters", "relationships": "#relationships",
                       "genres": "#genres", "status": "#status", "tags": "tags"},
           "behavior": {"fold_characters": True, "ascii_only_tags": True, "au_as": "genre", "crossover_as": "genre",
                        "reincarnation_as": "genre", "time_travel_as": "genre", "fold_ratings": False,
                        "keep_categories": True, "tropes_as": "tag"},
           "overrides": {"dir": "overrides"}}
    p = path or os.path.join(user_dir(), "config.toml")    # user config: user_dir(), not the package
    if os.path.exists(p):
        sec = None
        for raw in open(p):
            ln = raw.strip()
            if not ln or ln.startswith("#"): continue
            if ln.startswith("["): sec = ln[1:ln.index("]")].strip(); cfg.setdefault(sec, {}); continue
            if "=" in ln and sec:
                k, v = ln.split("=", 1); k = k.strip(); v = v.strip()
                if v[:1] in ("\"", "'"):                 # quoted string -> value between the quotes (# allowed inside)
                    v = v[1:].split(v[0], 1)[0]
                else:                                    # bool/number -> strip any trailing inline comment
                    v = v.split("#", 1)[0].strip()
                    if v.lower() in ("true", "false"): v = v.lower() == "true"
                cfg[sec][k] = v
    return cfg


# ---------------- the write funnel ----------------
# Op constructors: the ops-JSON shape (_writer.py's docstring) is built ONLY here, next to the
# run_writer guard that parses it back — producers call these instead of hand-writing dicts,
# so book-id stringification and key names are decided once.
def op_set_field(field: str, values: dict) -> dict:
    """values: {book_id: new value} (ids of any type — stringified here for JSON)."""
    return {"op": "set_field", "field": field, "values": {str(b): v for b, v in values.items()}}

def op_create_column(label: str, name: str, datatype: str, is_multiple: bool = False) -> dict:
    return {"op": "create_column", "label": label, "name": name, "datatype": datatype, "is_multiple": is_multiple}

def op_stamp_now(field: str, books: list | None = None) -> dict:
    """books=None stamps the whole library."""
    return {"op": "stamp_now", "field": field, "books": books}

def op_set_pref(key: str, value) -> dict:
    return {"op": "set_pref", "key": key, "value": value}


def _is_calibre_gui(line):
    """A process line that means the Calibre GUI is holding the library — excludes the CLI tools
    (calibredb / calibre-debug / calibre-server / …) and our own helper scripts, whose paths may
    themselves contain the word 'calibre'."""
    l = line.lower()
    return "calibre" in l and not any(x in l for x in (
        "calibre-debug", "calibredb", "calibre-server", "calibre-parallel",
        "pgrep", "wrangle", "_writer", "classify"))

def calibre_open() -> bool:
    """True if the Calibre GUI appears to be running (it locks metadata.db, so writes must wait).
    Best-effort via pgrep, then ps. If NEITHER exists we cannot tell, so fail CLOSED (report open)
    rather than let a write silently race a live library — the old code returned False (open the
    gate) here, which disabled the guard entirely on any host without pgrep."""
    import subprocess, shutil
    for cmd in (["pgrep", "-fl", "calibre"], ["ps", "-Ao", "command"]):
        if not shutil.which(cmd[0]): continue
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
        except Exception:
            continue
        return any(_is_calibre_gui(l) for l in out.splitlines())
    return True   # ponytail: no pgrep/ps → undetectable → assume open; never fail open on the safety guard

def _backup_path(dirpath: str | None = None):
    """A fresh, collision-proof snapshot path (default backups_dir()): ff_<timestamp>[_N].db. The old
    /tmp path used whole-second granularity, so two writes in the same second (a guided wizard run
    fires several) silently overwrote one snapshot — the _N suffix guarantees each write keeps its
    own. `dirpath` is a parameter so tests point at a temp dir instead of mutating the global."""
    dirpath = dirpath or backups_dir()
    os.makedirs(dirpath, exist_ok=True)
    base = time.strftime("ff_%Y%m%dT%H%M%S")
    p = os.path.join(dirpath, base + ".db"); n = 2
    while os.path.exists(p):
        p = os.path.join(dirpath, f"{base}_{n}.db"); n += 1
    return p

def _prune_backups(dirpath: str | None = None, keep: int | None = None):
    """Keep only the `keep` (default BACKUP_KEEP) newest snapshots (the timestamp name sorts
    chronologically)."""
    dirpath, keep = dirpath or backups_dir(), keep or BACKUP_KEEP
    for p in sorted(glob.glob(os.path.join(dirpath, "ff_*.db")))[:-keep]:
        try: os.remove(p)
        except OSError: pass

# Defense-in-depth write guard: refuse a change-set that would catastrophically empty a populated
# column. wrangle's semantic guards (data_loss/tag_loss) fire far earlier; this is the last-line net
# covering EVERY run_writer caller (classify/promote/staleness/setup), so "every write is guarded" is
# structural, not per-caller discipline. Deliberately coarse (90% wipe of a >=100-book column) — no
# legitimate write approaches it; --force overrides.
WRITE_WIPE_FLOOR = 100    # only guard columns that currently hold a value for >= this many books
WRITE_WIPE_FRAC = 0.90    # ... and abort if a write would clear more than this fraction of them

def _predict_populated(before_books, values):
    """Which books still hold a value after a set_field REPLACE: untouched books keep theirs;
    a touched book keeps a value only if its new one is non-empty. Pure — see tests."""
    touched = {int(b) for b in values}
    return (set(before_books) - touched) | {int(b) for b, v in values.items() if v}

def _is_wipe(n_before, n_after):
    """-> True if shrinking a column from n_before to n_after populated books is a catastrophic
    wipe worth aborting (a big column losing most of its values). Pure — see tests."""
    return n_before >= WRITE_WIPE_FLOOR and n_after < n_before * (1 - WRITE_WIPE_FRAC)

def _populated_books(con, field):
    """Set of book ids currently holding a non-empty value for `field` (builtin tags or a custom column)."""
    if field == "tags":
        return {b for (b,) in con.execute("SELECT DISTINCT book FROM books_tags_link")}
    return set(read_custom_column(con, field) or {})

def run_writer(ops: list[dict], force: bool = False) -> None:
    """Apply a list of write-ops through Calibre by shelling out to `calibre-debug -e _writer.py`.
    Automatically snapshots metadata.db to data/backups/ first — every write path gets a rollback
    point for free (restore with `scourgify rollback`). Refuses (before writing) a change-set that
    would catastrophically empty a populated column; --force overrides."""
    import json, time, tempfile, subprocess, shutil
    ops = [o for o in ops if o.get("op") != "set_field" or o.get("values")]
    if not ops: print("  (nothing to write)"); return
    if calibre_open(): raise SystemExit("Calibre is running — close it first (it locks metadata.db), then re-run.")
    if not force:                                   # last-line wipe guard, before any backup/write
        setf = collections.defaultdict(dict)
        for o in ops:
            if o.get("op") == "set_field": setf[o["field"]].update(o["values"])
        if setf:
            con = ro_connect()
            try:
                for field, values in setf.items():
                    before = _populated_books(con, field)
                    after = _predict_populated(before, values)
                    if _is_wipe(len(before), len(after)):
                        raise SystemExit(f"ABORT: writing would empty '{field}' from {len(before)} populated books "
                                         f"down to {len(after)} — a runaway change-set? Nothing was written. "
                                         "Re-run with --force if this is intentional.")
            finally: con.close()
    cb = shutil.which("calibre-debug") or "/Applications/calibre.app/Contents/MacOS/calibre-debug"
    if not (shutil.which("calibre-debug") or os.path.exists(cb)): raise SystemExit("calibre-debug not found (install Calibre's CLI tools).")
    bak = _backup_path()
    shutil.copy2(db_path(), bak)
    if os.path.getsize(bak) != os.path.getsize(db_path()):
        raise SystemExit(f"backup verify failed ({bak} size mismatch) — aborting before any write.")
    _prune_backups()
    print(f"  backup: {bak}   (restore: scourgify rollback)")
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False); json.dump(ops, f); f.close()
    print("  → writing via calibre-debug …")
    try:
        # generous ceiling: a real batch write finishes in seconds/minutes — this only
        # catches a wedged calibre-debug so a scripted/CI run can't hang forever.
        rc = subprocess.run([cb, "-e", os.path.join(HERE, "_writer.py"), "--", f.name],
                            env={**os.environ, "CALIBRE_LIBRARY": library()}, timeout=3600).returncode
    except subprocess.TimeoutExpired:
        raise SystemExit(f"writer timed out after 1h (calibre-debug wedged?) — library backup at {bak}")
    finally:
        os.unlink(f.name)
    if rc != 0: raise SystemExit(f"writer failed (exit {rc}) — library backup at {bak}")


def rollback_cmd(argv: list[str]) -> None:
    """`scourgify rollback [--list] [FILE]` — restore metadata.db from a data/backups/ snapshot.
    No FILE = the newest. The current db is itself snapshotted first, so a rollback is reversible."""
    import argparse, shutil
    ap = argparse.ArgumentParser(prog="scourgify rollback",
                                 description="Restore metadata.db from a scourgify backup (data/backups/).")
    ap.add_argument("file", nargs="?", help="backup to restore (path or basename); default = newest")
    ap.add_argument("--list", action="store_true", help="list available backups and exit")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    a = ap.parse_args(argv)
    baks = sorted(glob.glob(os.path.join(backups_dir(), "ff_*.db")), reverse=True)   # newest first
    if not baks:
        raise SystemExit(f"no backups in {backups_dir()} — nothing to roll back to.")
    if a.list:
        print(f"backups in {backups_dir()} (newest first):")
        for b in baks: print(f"  {os.path.basename(b)}   ({os.path.getsize(b) // 1024} KiB)")
        return
    target = baks[0] if not a.file else (a.file if os.path.exists(a.file) else os.path.join(backups_dir(), a.file))
    if not os.path.exists(target): raise SystemExit(f"no such backup: {a.file}")
    if calibre_open():
        raise SystemExit("Calibre is running — close it first (it locks metadata.db), then roll back.")
    try:                                          # never clobber the live db with a non-Calibre file
        con = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        n = con.execute("SELECT count(*) FROM books").fetchone()[0]; con.close()
    except Exception as e:
        raise SystemExit(f"{target} is not a readable Calibre DB ({e}) — refusing to restore.")
    print(f"about to restore {os.path.basename(target)} ({n} books) OVER {db_path()}")
    if not a.yes:
        if not interactive(): raise SystemExit("non-interactive: re-run with --yes to restore.")
        if not confirm("proceed?"):
            print("aborted (nothing changed)."); return
    cur = _backup_path(); shutil.copy2(db_path(), cur); _prune_backups()   # this rollback is itself reversible
    shutil.copy2(target, db_path())
    print(f"restored {os.path.basename(target)}; previous state saved to {os.path.basename(cur)}")
