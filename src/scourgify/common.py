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
import os, re, sys, csv, time, glob, sqlite3, collections, unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))   # the installed package dir (read-only)
DEFAULTS = os.path.join(HERE, "defaults")            # bundled generic maps — ship inside the package
# Per-run + per-user files live in the working directory, not site-packages: `data/`
# (proposals/intermediates), config.toml, and overrides/ are all resolved against CWD.
# When run from the repo via `uv run`, CWD == repo root, so dev layout is unchanged.
DATA = os.path.join(os.getcwd(), "data")             # personal review maps, proposals, intermediates (gitignored)
BACKUPS = os.path.join(DATA, "backups")              # metadata.db snapshots taken before every write (was /tmp)
BACKUP_KEEP = 20                                      # keep this many newest snapshots; older ones are pruned
REJECTS = os.path.join(DATA, "rejects.csv")          # per-item rejects from `--step` review (see wrangle.overrides)
REJECT_COLS = ["ts", "stage", "book", "title", "kind", "column", "before", "after", "class"]


def log_rejects(rows: list[dict]) -> int:
    """Append reject dicts (REJECT_COLS keys; ts auto-stamped) to data/rejects.csv, creating
    the header on first write. The "separate list" that `scourgify overrides` reads back."""
    rows = [r for r in rows if r]
    if not rows: return 0
    os.makedirs(DATA, exist_ok=True)
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    new = not os.path.exists(REJECTS)
    with open(REJECTS, "a", newline="") as f:
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


# ---------------- config (minimal TOML reader; no tomllib dependency) ----------------
def load_config(path: str | None = None) -> dict:
    cfg = {"columns": {"fandoms": "#fandoms", "characters": "#characters", "relationships": "#relationships",
                       "genres": "#genres", "status": "#status", "tags": "tags"},
           "behavior": {"fold_characters": True, "ascii_only_tags": True, "au_as": "genre", "crossover_as": "genre",
                        "reincarnation_as": "genre", "time_travel_as": "genre", "fold_ratings": False,
                        "keep_categories": True, "tropes_as": "tag"},
           "overrides": {"dir": "overrides"}}
    p = path or os.path.join(os.getcwd(), "config.toml")   # user config: CWD, not the package
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

def _backup_path():
    """A fresh, collision-proof snapshot path in BACKUPS: ff_<timestamp>[_N].db. The old /tmp path
    used whole-second granularity, so two writes in the same second (a guided wizard run fires
    several) silently overwrote one snapshot — the _N suffix guarantees each write keeps its own."""
    os.makedirs(BACKUPS, exist_ok=True)
    base = time.strftime("ff_%Y%m%dT%H%M%S")
    p = os.path.join(BACKUPS, base + ".db"); n = 2
    while os.path.exists(p):
        p = os.path.join(BACKUPS, f"{base}_{n}.db"); n += 1
    return p

def _prune_backups():
    """Keep only the BACKUP_KEEP newest snapshots (the timestamp name sorts chronologically)."""
    for p in sorted(glob.glob(os.path.join(BACKUPS, "ff_*.db")))[:-BACKUP_KEEP]:
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
    baks = sorted(glob.glob(os.path.join(BACKUPS, "ff_*.db")), reverse=True)   # newest first
    if not baks:
        raise SystemExit(f"no backups in {BACKUPS} — nothing to roll back to.")
    if a.list:
        print(f"backups in {BACKUPS} (newest first):")
        for b in baks: print(f"  {os.path.basename(b)}   ({os.path.getsize(b) // 1024} KiB)")
        return
    target = baks[0] if not a.file else (a.file if os.path.exists(a.file) else os.path.join(BACKUPS, a.file))
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
        if not sys.stdin.isatty(): raise SystemExit("non-interactive: re-run with --yes to restore.")
        if input("proceed? [y/N] ").strip().lower() not in ("y", "yes"):
            print("aborted (nothing changed)."); return
    cur = _backup_path(); shutil.copy2(db_path(), cur); _prune_backups()   # this rollback is itself reversible
    shutil.copy2(target, db_path())
    print(f"restored {os.path.basename(target)}; previous state saved to {os.path.basename(cur)}")
