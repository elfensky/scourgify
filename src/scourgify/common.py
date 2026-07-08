#!/usr/bin/env python3
"""Shared core for the standalone tools (wrangle / classify / staleness).

Everything here runs under plain system python3. It owns the four things the tools
used to each carry a private copy of:
  - library resolution (CALIBRE_LIBRARY) — checked lazily, never at import time
  - read-only sqlite access + link-table-aware custom-column reading
  - normalization helpers (norm, ascii_fold) and the minimal TOML config reader
  - the single write funnel: run_writer() -> calibre-debug -e _writer.py
    (backs up metadata.db to /tmp before every write; refuses to run while Calibre is open)
"""
import os, re, sys, csv, time, sqlite3, collections, unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))   # the installed package dir (read-only)
DEFAULTS = os.path.join(HERE, "defaults")            # bundled generic maps — ship inside the package
# Per-run + per-user files live in the working directory, not site-packages: `data/`
# (proposals/intermediates), config.toml, and overrides/ are all resolved against CWD.
# When run from the repo via `uv run`, CWD == repo root, so dev layout is unchanged.
DATA = os.path.join(os.getcwd(), "data")             # personal review maps, proposals, intermediates (gitignored)
REJECTS = os.path.join(DATA, "rejects.csv")          # per-item rejects from `--step` review (see wrangle.overrides)
REJECT_COLS = ["ts", "stage", "book", "title", "kind", "column", "before", "after", "class"]


def log_rejects(rows):
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
def library():
    lib = os.path.expanduser(os.environ.get("CALIBRE_LIBRARY", ""))
    if not lib:
        raise SystemExit("Set CALIBRE_LIBRARY to your Calibre library folder (the one containing metadata.db).")
    return lib

def db_path():
    return os.path.join(library(), "metadata.db")

def ro_connect():
    return sqlite3.connect(f"file:{db_path()}?mode=ro", uri=True)


# ---------------- normalization ----------------
def norm(s):
    s = str(s).strip().lower(); s = re.sub(r"[\[\]\(\)]", "", s); s = s.replace("&", "and")
    s = re.sub(r"[\s_\-/]+", " ", s); s = re.sub(r"[^\w ]", "", s, flags=re.UNICODE); return s.strip()

def ascii_fold(s):
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').replace("…", "...").replace("–", "-").replace("—", "-")
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


# ---------------- custom columns (single- and multi-value; link-table aware) ----------------
def custom_column_id(con, label):
    r = con.execute("SELECT id FROM custom_columns WHERE label=?", (label.lstrip("#"),)).fetchone()
    return r[0] if r else None

def read_custom_column(con, label, multi=False):
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
def load_config(path=None):
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
def calibre_open():
    import subprocess, shutil
    if not shutil.which("pgrep"): return False
    out = subprocess.run(["pgrep", "-fl", "calibre"], capture_output=True, text=True).stdout
    return any("calibre" in l.lower() and not any(x in l for x in ("calibre-debug", "pgrep", "wrangle", "_writer", "classify"))
               for l in out.splitlines())

def run_writer(ops):
    """Apply a list of write-ops through Calibre by shelling out to `calibre-debug -e _writer.py`.
    Automatically snapshots metadata.db to /tmp first — every write path gets a rollback point for free."""
    import json, time, tempfile, subprocess, shutil
    ops = [o for o in ops if o.get("op") != "set_field" or o.get("values")]
    if not ops: print("  (nothing to write)"); return
    if calibre_open(): raise SystemExit("Calibre is running — close it first (it locks metadata.db), then re-run.")
    cb = shutil.which("calibre-debug") or "/Applications/calibre.app/Contents/MacOS/calibre-debug"
    if not (shutil.which("calibre-debug") or os.path.exists(cb)): raise SystemExit("calibre-debug not found (install Calibre's CLI tools).")
    bak = f"/tmp/ff_{int(time.time())}.db"
    shutil.copy2(db_path(), bak)
    print(f"  backup: {bak}")
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False); json.dump(ops, f); f.close()
    print("  → writing via calibre-debug …")
    rc = subprocess.run([cb, "-e", os.path.join(HERE, "_writer.py"), "--", f.name],
                        env={**os.environ, "CALIBRE_LIBRARY": library()}).returncode
    os.unlink(f.name)
    if rc != 0: raise SystemExit(f"writer failed (exit {rc}) — library backup at {bak}")
