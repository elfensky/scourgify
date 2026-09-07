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
import os, re, sys, csv, time, glob, hashlib, sqlite3, contextlib, collections, unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))   # the installed package dir (read-only)
# Deprecated import-compat alias ONLY — nothing may read this for a runtime path any more.
# Runtime reads of every shipped read-only file (defaults/, classify_vocab*.txt, afm.swift) go
# through defaults_dir() below, which resolves correctly whether HERE is a real directory (a
# normal install) or a zip-internal path (the Calibre plugin, where os.path.exists() is False
# for everything under it).
DEFAULTS = os.path.join(HERE, "defaults")


def user_dir() -> str:
    """The one root for every user-owned file (config.toml, overrides/, data/).
    Precedence, ONE total order on every OS: $SCOURGIFY_HOME wins (tests, experiments); else,
    on Windows (os.name == "nt") with %APPDATA% set and non-empty, %APPDATA%\\scourgify (mirrors
    Calibre's own %APPDATA%\\calibre convention); else XDG: ($XDG_CONFIG_HOME or ~/.config)/scourgify.
    `APPDATA` is read with os.environ.get, never subscripted — an nt host without it (rare, but
    possible) falls through to the XDG branch rather than raising."""
    home = os.environ.get("SCOURGIFY_HOME")
    if home:
        return home
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return os.path.join(appdata, "scourgify")
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "scourgify")


# ---------------- bundled read-only resources (defaults/, afm.swift) ----------------
def _core_version() -> str:
    """The cache key for defaults_dir()'s extraction under user_dir()/cache/<core version>/ —
    never empty, and sanitized for use as a directory name (a version string reaches the
    filesystem here). In order: the zip's own stamped scourgify/_plugin_version.py (written by
    build_plugin.py into every zip, so it exists by construction whenever THIS module is actually
    running from inside one); else importlib.metadata's installed-package version; else
    scourgify.__version__'s own fallback; else the literal 0.0.0+local (matching
    src/scourgify/__init__.py's own fallback). The cache is only ever POPULATED on the zip path,
    where _plugin_version exists by construction, so a wheel install and a zip install can never
    end up sharing one cache directory by accident (REVIEW: OpenCode agreed concern 2)."""
    v = None
    try:
        from scourgify import _plugin_version
        v = _plugin_version.VERSION
    except ImportError:
        v = None
    if not v:
        try:
            import importlib.metadata
            v = importlib.metadata.version("scourgify")
        except importlib.metadata.PackageNotFoundError:
            v = None
    if not v:
        try:
            from scourgify import __version__ as pkg_version
            v = pkg_version
        except ImportError:
            v = None
    if not v:
        v = "0.0.0+local"
    for sep in (os.sep, "/", "\\"):
        v = v.replace(sep, "-")
    return v


def _archive_path() -> str | None:
    """The zip this module was imported from, or None on a normal (directory) install.

    Asks the IMPORT SYSTEM first, the filesystem second (REVIEW: Codex agreed concern 2 — the
    loader's own answer does not depend on the zip's internal layout, so it goes first even
    though the walk-up happens to work for this repo's). Every return path is guarded by
    os.path.isfile — a result that does not exist on disk is never returned; it becomes None
    instead of a guess."""
    if os.path.isdir(HERE):
        return None
    loader = getattr(globals().get("__spec__"), "loader", None) or globals().get("__loader__")
    candidate = getattr(loader, "archive", None)
    if not candidate:
        p = HERE
        while True:
            parent = os.path.dirname(p)
            if not parent or parent == p:
                candidate = None
                break
            if os.path.isfile(parent):
                candidate = parent
                break
            p = parent
    return candidate if candidate and os.path.isfile(candidate) else None


def _extract_defaults(archive: str, cache_root: str, version: str) -> None:
    """Windows-safe, race-safe extraction of every shipped read-only file under the zip's
    scourgify/defaults/ prefix (plus scourgify/afm.swift — extracted to cache_root/afm.swift,
    a sibling of cache_root/defaults/, mirroring HERE's own layout of afm.swift beside defaults/)
    into a UNIQUE sibling temp directory, moved into `cache_root` with a single os.rename ONLY
    when `cache_root` is still absent.

    Never a directory-replacing rename (the os.replace flavor) — replacing an existing directory is not portable to
    Windows, and would let one process pull a tree out from under another's concurrent readers.
    If the rename raises (another process won the race and populated cache_root first), the temp
    tree is discarded and the now-warm cache_root is used as-is — concurrent extraction by two
    Calibre jobs is benign, not merely unlikely. Every member's destination is checked against
    os.path.realpath(temp_root) before it is opened, so a zip-slip name (or a symlinked
    intermediate directory) is refused with GuardrailError rather than written (CWE-22)."""
    import zipfile, shutil, uuid as _uuid_mod
    cache_parent = os.path.dirname(cache_root)
    os.makedirs(cache_parent, exist_ok=True)
    temp_root = os.path.join(cache_parent, f".tmp-{version}-{os.getpid()}-{_uuid_mod.uuid4().hex[:8]}")
    try:
        os.makedirs(temp_root)
        try:
            zf = zipfile.ZipFile(archive)
        except (OSError, zipfile.BadZipFile) as e:
            raise GuardrailError(
                f"scourgify's bundled data layer could not be opened from {archive} ({e}) — "
                "refusing to normalize against an empty taxonomy.")
        with zf:
            names = [n for n in zf.namelist()
                     if not n.endswith("/") and
                     (n.startswith("scourgify/defaults/") or n == "scourgify/afm.swift")]
            if not names:
                raise GuardrailError(
                    f"{archive} holds no scourgify/defaults members — refusing to normalize "
                    "against an empty taxonomy.")
            temp_real = os.path.realpath(temp_root)
            for name in names:
                rel = name[len("scourgify/"):]           # "defaults/..." or "afm.swift"
                dest = os.path.join(temp_root, *rel.split("/"))
                dest_real = os.path.realpath(dest)
                if not (dest_real == temp_real or dest_real.startswith(temp_real + os.sep)):
                    raise GuardrailError(
                        f"refusing to extract {name!r} from {archive} — its destination escapes "
                        "the extraction root.")
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with zf.open(name) as src, open(dest, "xb") as out:
                    shutil.copyfileobj(src, out)
        # The marker is written LAST, inside the temp tree, so a half-extraction is never
        # mistaken for a warm cache.
        with open(os.path.join(temp_root, ".extracted"), "w", encoding="utf-8") as f:
            f.write("ok\n")
    except BaseException:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise
    if os.path.exists(cache_root):
        shutil.rmtree(temp_root, ignore_errors=True)      # someone else already won — discard ours
        return
    try:
        os.rename(temp_root, cache_root)                  # never a directory-replacing rename
    except OSError:
        shutil.rmtree(temp_root, ignore_errors=True)       # lost the race — use the warm cache


def defaults_dir() -> str:
    """THE resolver for every shipped read-only file the core opens at runtime — defaults/
    (including defaults/ao3/), classify_vocab*.txt, and afm.swift. On a normal install, returns
    the package's own defaults/ directory unchanged and creates nothing on disk. Inside the
    Calibre plugin zip, extracts once into user_dir()/cache/<core version>/defaults/ (see
    _extract_defaults — afm.swift lands one level up, at user_dir()/cache/<core version>/afm.swift,
    the sibling relationship engines.py's afm lookup relies on) and returns that directory. A
    FUNCTION, never memoized at the user_dir() level: $SCOURGIFY_HOME set after import must still
    redirect the cache location. Raises GuardrailError — never SystemExit — if the archive cannot
    be opened or holds no defaults members; this sits on the read path of a Calibre job, where
    SystemExit would escape ThreadedJob's `except Exception` and kill the worker thread silently."""
    archive = _archive_path()
    if archive is None:
        return os.path.join(HERE, "defaults")
    version = _core_version()
    cache_root = os.path.join(user_dir(), "cache", version)
    marker = os.path.join(cache_root, ".extracted")
    if not os.path.isfile(marker):
        _extract_defaults(archive, cache_root, version)
        if not os.path.isfile(marker):
            raise GuardrailError(
                f"scourgify's bundled data layer failed to extract from {archive} into "
                f"{cache_root} — refusing to normalize against an empty taxonomy.")
    return os.path.join(cache_root, "defaults")


# Per-run + per-user files live under user_dir(), not site-packages nor the invoking CWD:
# config.toml, overrides/, and data/ (proposals/intermediates + backups) all resolve there,
# so an installed copy has a stable home instead of writing relative to wherever it's launched.
# Paths are FUNCTIONS, never import-time constants — $SCOURGIFY_HOME set after import (tests)
# must still redirect the whole tree.
BACKUP_KEEP = 20                                      # keep this many newest snapshots; older ones are pruned
# ...but a snapshot is a whole metadata.db, so the COUNT cap alone sets no size ceiling: a 25 MB db
# × 20 is 500 MB, a 100 MB one is 2 GB. The byte budget is the real cap — prune drops the oldest
# until the snapshots fit, so the steady state is always under it and the wizard's nudge (below)
# is something the user can actually act on instead of a permanent banner.
BACKUP_BUDGET = 1024 * 1024 * 1024                    # total bytes of ff_* snapshots to keep
BACKUP_MIN = 3                                        # ...but never prune below this many, however big they are
BACKUP_WARN = BACKUP_BUDGET                           # wizard nudges past this — i.e. only for files prune can't touch
REJECT_COLS = ["ts", "stage", "book", "title", "kind", "column", "before", "after", "class"]


# The library uuid is the primary key for every piece of operational state (proposals, failures,
# edits.jsonl, backups, rejects, ledger) — a plugin opens whatever library the GUI has open and
# switches libraries mid-session, so a path built from user_dir() alone would let a throwaway
# library read and write the real library's history (observed once in plugin phases 4-5).
#
# _UUID_CACHE is memoized by library PATH, not cleared by set_library(): a Calibre library's
# library_id.uuid does not change over the life of that folder, so a library switch cannot make
# an entry stale, and re-opening sqlite on every switch for no gain would be wasted work. The ONE
# case that can go stale is a test/fixture harness that rebuilds a DIFFERENT db at a path it
# already resolved — that calls clear_uuid_cache() deliberately, the one supported reset.
_UUID_CACHE: dict[str, str] = {}


def _resolve_uuid() -> str:
    """This process's memoized answer to "which library's data/<uuid>/ tree are we in".

    Opens a short-lived ro_connect() and reads library_uuid() (the ONE uuid reader — do not add a
    second). A db with no library_id table (a hand-built fixture) keys on
    nouuid-<sha1(library path)[:12]> instead of refusing — the fallback exists for fixtures, not
    for real Calibre libraries, which always carry the table. library() raises GuardrailError
    when nothing is resolvable, so data_dir() inherits that refusal for free rather than ever
    returning a library-less path."""
    lib = library()
    if lib not in _UUID_CACHE:
        con = ro_connect()
        try:
            u = library_uuid(con)
        finally:
            con.close()
        _UUID_CACHE[lib] = u or f"nouuid-{hashlib.sha1(lib.encode()).hexdigest()[:12]}"
    return _UUID_CACHE[lib]


def clear_uuid_cache() -> None:
    """Empty the per-process library-uuid memo (mirrors classify.clear_caches()).

    set_library() deliberately does NOT call this — the memo is keyed by library PATH and a
    library's uuid does not change over its life, so switching libraries cannot make an entry
    stale. This is the one supported reset, for a test/fixture harness that rebuilds a different
    db at a path already resolved this process."""
    _UUID_CACHE.clear()


def data_dir() -> str:
    """user_dir()/data/<library uuid>/ — personal review maps, proposals, intermediates
    (gitignored), namespaced per library so two libraries opened in one process never share an
    artifact path. Raises GuardrailError (never a library-less path) when no library resolves.
    A FUNCTION, never memoized at the user_dir() level: $SCOURGIFY_HOME set after import must
    still redirect the whole tree. Runs the one-time guarded migration of the legacy flat data/
    tree (migrate_legacy_data()) on every resolution, until its marker exists — cheap after the
    first call (an os.path.exists check) and self-disabling once the tree has moved."""
    d = os.path.join(user_dir(), "data", _resolve_uuid())
    migrate_legacy_data()
    return d


# Attempts are keyed by (user_dir(), resolved uuid), NOT a single process-global boolean: a
# throwaway library resolved first fails the owner proof, and a global flag would then
# permanently suppress migration for the real library opened later in the same Calibre session
# — exactly the multi-library case this phase exists for.
_MIGRATION_TRIED: set[tuple[str, str]] = set()


def _same_bytes(a: str, b: str) -> bool:
    try:
        with open(a, "rb") as fa, open(b, "rb") as fb:
            return fa.read() == fb.read()
    except OSError:
        return False


def migrate_legacy_data() -> str | None:
    """One-time guarded auto-move of the legacy flat data/ tree into data/<library uuid>/ (D-02).

    Owner proof (D-03): move ONLY when the uuid read from the newest data/backups/ff_*.db equals
    the resolving library's uuid. No legacy backup, an unreadable one, one with no library_id
    table (library_uuid() returns None — NOT a proof, treated exactly like a mismatch), or a
    mismatched uuid: move NOTHING, print the reason, return None. A throwaway library resolved
    first must never claim the real library's history.

    The marker (data/MIGRATED) is the ONLY "already migrated" gate — never data/<uuid>/ merely
    existing, which a rolled-back or interrupted attempt can leave behind; skipping on it would
    make a split tree permanent and silent. When data/<uuid>/ exists AND legacy entries are still
    present AND there is no marker, this is a resumable prior attempt: the owner proof runs again
    and the remaining entries move in.

    The move is all-or-nothing: each legacy entry renames in one at a time (never a copy — the
    real tree is hundreds of MB), journalled as it succeeds. Any failure rolls every already-moved
    entry back and raises GuardrailError (never SystemExit — this runs on the read path of every
    Calibre job) naming what failed and what was restored. The marker is written LAST, so a
    failed or rolled-back attempt never gets marked "done"."""
    home = user_dir()
    uid = _resolve_uuid()
    key = (home, uid)
    if key in _MIGRATION_TRIED:
        return None
    _MIGRATION_TRIED.add(key)

    legacy_root = os.path.join(home, "data")
    marker = os.path.join(legacy_root, "MIGRATED")
    if os.path.exists(marker):
        return None
    if not os.path.isdir(legacy_root):
        return None

    # A legacy entry is a FILE directly under data/, or the "backups" directory itself — never
    # the destination uuid/nouuid- directory a (possibly interrupted) prior attempt already made.
    def _is_legacy(name: str) -> bool:
        if name in ("MIGRATED",):
            return False
        full = os.path.join(legacy_root, name)
        if os.path.isdir(full):
            return name == "backups"
        return True

    legacy_entries = sorted(n for n in os.listdir(legacy_root) if _is_legacy(n))
    if not legacy_entries:
        return None

    # ---- owner proof ----
    candidates = glob.glob(os.path.join(legacy_root, "backups", "ff_*.db"))
    if not candidates:
        print(f"scourgify: no legacy backup under {os.path.join(legacy_root, 'backups')} to prove "
              f"ownership of the legacy data/ tree — leaving it untouched.")
        return None
    newest = max(candidates, key=os.path.getmtime)
    try:
        con = sqlite3.connect(f"file:{newest}?mode=ro", uri=True)
        try:
            backup_uuid = library_uuid(con)
        finally:
            con.close()
    except Exception as e:
        print(f"scourgify: could not read {newest} ({e}) to prove ownership of the legacy data/ "
              f"tree — leaving it untouched.")
        return None
    if backup_uuid is None:
        print(f"scourgify: {newest} carries no library_id table — ownership of the legacy data/ "
              f"tree could not be proved; leaving it untouched.")
        return None
    if backup_uuid != uid:
        print(f"scourgify: legacy backup uuid {backup_uuid!r} does not match this library's uuid "
              f"{uid!r} — leaving the legacy data/ tree untouched.")
        return None

    # ---- the move: all-or-nothing, journalled ----
    # Journal entries are (src, dst, kind): kind="moved" needs an os.rename(dst, src) reversal on
    # failure; kind="deduped" (a resumed attempt's destination already held byte-identical
    # content, so src was removed rather than moved) must NOT be reversed — dst pre-dates this
    # call and reversing would both resurrect a duplicate at src and destroy the prior attempt's
    # already-correct file at dst.
    dest_root = os.path.join(legacy_root, uid)
    os.makedirs(dest_root, exist_ok=True)
    journal: list[tuple[str, str, str]] = []
    try:
        for name in legacy_entries:
            src, dst = os.path.join(legacy_root, name), os.path.join(dest_root, name)
            if os.path.exists(dst):
                if os.path.isdir(src) or os.path.isdir(dst):
                    raise GuardrailError(f"migrate_legacy_data: refusing — {dst} already exists "
                                         f"(a directory collision is never silently merged).")
                if not _same_bytes(src, dst):
                    raise GuardrailError(f"migrate_legacy_data: refusing — {dst} already exists "
                                         f"with different content than {src}.")
                os.remove(src)          # a prior attempt already moved this one; tidy the duplicate
                journal.append((src, dst, "deduped"))
                continue
            os.rename(src, dst)
            journal.append((src, dst, "moved"))
    except BaseException as e:
        restored, unrestorable = [], []
        for src, dst, kind in reversed(journal):
            if kind == "deduped":
                continue             # dst pre-dates this call — never reverse a dedupe
            try:
                os.rename(dst, src)
                restored.append(src)
            except OSError as re_err:
                unrestorable.append((src, dst, str(re_err)))
        msg = f"migrate_legacy_data: aborted moving the legacy data/ tree ({e}). Restored: {restored}."
        if unrestorable:
            msg += f" COULD NOT RESTORE (manual recovery needed): {unrestorable}."
        raise GuardrailError(msg) from e

    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with open(marker, "w", encoding="utf-8") as f:
        f.write(f"uuid: {uid}\nts: {ts}\nmoved:\n")
        for name in legacy_entries:
            f.write(f"  - {name}\n")
    print(f"scourgify: migrated the legacy data/ tree ({', '.join(legacy_entries)}) into {dest_root} "
          f"(marker: {marker}).")
    return dest_root


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
    with open(rejects_path(), "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=REJECT_COLS, extrasaction="ignore")
        if new: w.writeheader()
        for r in rows: w.writerow({"ts": ts, **r})
    return len(rows)


# ---------------- library resolution (lazy — importing this module never exits) ----------------
_LIBRARY = None      # in-process override; None means "read $CALIBRE_LIBRARY" (the CLI's world)


def set_library(path: str | None) -> None:
    """Point the whole core at `path` — the ONE seam a Calibre plugin needs.

    In-plugin the library is whatever `gui.current_db` has open, and the environment cannot say:
    a Calibre launched from the Dock inherits no environment at all, and a $CALIBRE_LIBRARY that
    *is* set may name a different library than the one the GUI holds. So the injected path WINS
    over the env var — the opposite of B5's key rule, and deliberately so: a key is user config,
    the library path is a fact about the host process.

    os.environ is never mutated (B5.2's reasoning applies to the path as much as to keys — a
    concurrent job or a CLI running alongside must not observe this), and the 15-odd call sites of
    library()/db_path() need no change.

    ponytail: a process global, because Calibre has one open library per GUI, every job re-asserts
    it at its first line, and the uuid captured at click time is validated against it. If
    concurrent jobs against *different* libraries ever become real, this becomes a ContextVar —
    same call sites, same seam. Pass None to hand the process back to $CALIBRE_LIBRARY.
    """
    global _LIBRARY
    _LIBRARY = os.path.expanduser(path) if path else None


def library() -> str:
    """The library folder — set_library() if a host injected one, else $CALIBRE_LIBRARY.
    Raises GuardrailError (not SystemExit) when unset: every read path in
    the repo reaches this function, so inside a Calibre job a SystemExit here would escape
    ThreadedJob's `except Exception` and kill the worker thread silently. cli.main converts it
    back, so the CLI message and exit code are unchanged."""
    lib = _LIBRARY or os.path.expanduser(os.environ.get("CALIBRE_LIBRARY", ""))
    if not lib:
        raise GuardrailError("Set CALIBRE_LIBRARY to your Calibre library folder (the one containing metadata.db).")
    return lib

def db_path() -> str:
    return os.path.join(library(), "metadata.db")

def ro_connect() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path()}?mode=ro", uri=True)


class GuardrailError(Exception):
    """A safety guard refused a change-set (wipe guard, failed backup, …).

    Deliberately an ordinary Exception, not a SystemExit. The same guards have to stop two very
    different runs: a CLI run, where run_writer() converts this straight back to SystemExit so
    exit codes and messages are unchanged; and a Calibre job, where SystemExit would escape
    ThreadedJob's `except Exception` and kill the worker thread SILENTLY — a guard that trips
    invisibly is worse than no guard. wizard._stage_guard catches both."""


# ---------------- interaction policy (the ONE answer to "is a human at the terminal") ----------------
class ScriptError(Exception):
    """A scripted run's answers don't fit the flow (queue exhausted, or an answer that isn't on
    offer). Deliberately NOT a SystemExit: wizard._stage_guard absorbs SystemExit — that's the
    guardrail-skips-a-stage path — which would swallow exactly the failure this seam exists to
    make loud, and hand back a green test that asserted nothing."""


def _parse_script(raw: str) -> list:
    """Parse a raw SCOURGIFY_SCRIPT value into a canned-answer queue. A blank or whitespace-only
    value (e.g. an interpolated-but-unset shell var, `SCOURGIFY_SCRIPT="$KEYS"`) means "scripted,
    with no answers" -> [] , so the very first prompt raises instead of silently walking every
    prompt's default (which for the wizard's landing menu is "full maintenance run" and for its
    apply prompts is "yes"). A non-blank value still splits on ',' exactly as before — a blank
    ENTRY within it (e.g. the trailing "" in "4,") is a real scripted answer meaning "press enter,
    take the default", not this empty-queue case."""
    return [a.strip() for a in raw.split(",")] if raw.strip() else []


# Canned answers for a scripted run: `SCOURGIFY_SCRIPT=w,3,n,q scourgify` for ad-hoc shell use,
# or common.scripted_answers([...]) in tests. Read ONCE at import — tests use the context manager,
# so this only constrains shell use, where the variable is set before launch anyway.
# ponytail: split on ',', so a checklist multi-toggle in an env-var script uses spaces ("1 3").
_script = (_parse_script(os.environ["SCOURGIFY_SCRIPT"])
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
    return [l.rstrip("\n") for l in open(path, encoding="utf-8")] if os.path.exists(path) else []


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
        for raw in open(p, encoding="utf-8"):
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
    """True if the Calibre GUI appears to be running (it holds metadata.db open, so writes must
    wait). Two checks, in order: are we ourselves inside the GUI, then best-effort pgrep → ps.
    If NEITHER binary exists we cannot tell, so fail CLOSED (report open) rather than let a write
    silently race a live library — the old code returned False (open the gate) here, which
    disabled the guard entirely on any host without pgrep.

    Deliberately NOT a lock probe: measured 2026-08-05, `BEGIN IMMEDIATE` against metadata.db
    succeeds while the GUI is running. Calibre keeps a connection open but holds no write lock at
    rest, so SQLITE_BUSY would report "safe to write" — the exact answer this guard exists to
    prevent. The hazard is a live GUI that may write at any moment, not a lock held right now."""
    import subprocess, shutil, sys
    # Are we running INSIDE the GUI (i.e. a Calibre plugin)? Then it is open by definition, and
    # the scan below cannot tell us so: measured 2026-08-05 from a plugin, `pgrep -fl calibre`
    # exits 0 and lists only the calibre-parallel workers — the GUI's own process line is absent,
    # every remaining line is correctly rejected by _is_calibre_gui, and the answer comes back
    # False. That is the fail-OPEN direction, in the one context where a second writer is
    # guaranteed to be racing a live library. calibre.gui2 is imported only by the GUI (plain
    # `calibre-debug -e` does not pull it in), so it identifies that context exactly.
    if any(m == "calibre.gui2" or m.startswith("calibre.gui2.") for m in sys.modules): return True
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

def _prune_backups(dirpath: str | None = None, keep: int | None = None, budget: int | None = None):
    """Keep the `keep` (default BACKUP_KEEP) newest snapshots, then drop the oldest of those until
    they fit in `budget` bytes — never going below BACKUP_MIN, so a library whose db alone busts
    the budget still keeps a usable rollback history. The timestamp name sorts chronologically."""
    dirpath = dirpath or backups_dir()
    keep = BACKUP_KEEP if keep is None else keep
    budget = BACKUP_BUDGET if budget is None else budget
    snaps = sorted(glob.glob(os.path.join(dirpath, "ff_*.db")))    # oldest first
    def drop(p):
        try: os.remove(p); return True
        except OSError: return False
    for p in snaps[:-keep] if keep else snaps: drop(p)
    snaps = snaps[-keep:] if keep else []
    sizes = {p: os.path.getsize(p) for p in snaps if os.path.exists(p)}
    snaps = [p for p in snaps if p in sizes]
    while len(snaps) > BACKUP_MIN and sum(sizes.values()) > budget:
        p = snaps.pop(0)                                            # oldest first
        if drop(p): sizes.pop(p)

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
    """Set of book ids currently holding a non-empty value for `field` (builtin tags or a custom column).
    The read-only-sqlite half of the guard's before-read; populated_via_api() is the in-process twin."""
    if field == "tags":
        return {b for (b,) in con.execute("SELECT DISTINCT book FROM books_tags_link")}
    return set(read_custom_column(con, field) or {})

def column_is_multiple(con: sqlite3.Connection, field: str) -> bool:
    """Is `field` multi-valued? From `custom_columns.is_multiple` — the schema's own answer, not a
    guess from the storage shape (a SINGLE-value column may use a link table too, so the shape
    proves nothing). `tags` is multi by definition."""
    if field == "tags": return True
    r = con.execute("SELECT is_multiple FROM custom_columns WHERE label=?", (field.lstrip("#"),)).fetchone()
    return bool(r and r[0])


def column_values(con: sqlite3.Connection, field: str, books=None) -> dict:
    """{book_id: current value} — the edit log's before-read, off read-only sqlite.

    The wipe guard reads the same column and throws the values away (`_populated_books` keeps
    only the keys, and short-circuits `tags` to DISTINCT book); capturing them is the extra join.
    `books=None` returns the whole column; a book with no value maps to None — a real
    before-state ("nothing"), not a missing row."""
    if field == "tags":
        vals = {b: sorted(t) for b, t in current_tags(con).items()}
    else:
        vals = read_custom_column(con, field, multi=column_is_multiple(con, field)) or {}
    return vals if books is None else {int(b): vals.get(int(b)) for b in books}


def values_via_api(api, field, books) -> dict:
    """The same read through Calibre's live new_api — the in-process twin of column_values().
    In-process the GUI's in-memory cache is the authoritative state; a second sqlite handle can
    be behind it by any number of unflushed edits."""
    return {int(b): v for b, v in api.all_field_for(field, [int(b) for b in books]).items()}


def library_uuid(con: sqlite3.Connection) -> str | None:
    """Calibre's own library id, the edit log's per-library key (records from one library must
    never mix into another's history). None if the table is absent — a hand-built fixture db is
    not a reason to refuse a write."""
    try:
        r = con.execute("SELECT uuid FROM library_id").fetchone()
    except sqlite3.OperationalError:
        return None
    return r[0] if r else None


def populated_via_api(api, field) -> set:
    """The same set, read through Calibre's live new_api instead of a second sqlite handle.

    In-process the guard MUST read here: the GUI's in-memory cache is the authoritative state,
    and a raw read-only connection can be behind it by any number of unflushed GUI edits — the
    guard would then judge a change-set against a library that no longer exists."""
    return {b for b, v in api.all_field_for(field, list(api.all_book_ids())).items() if v}

def check_wipe(ops: list[dict], populated) -> None:
    """Raise GuardrailError if `ops` would catastrophically empty a populated column.

    `populated` (field -> set of book ids currently holding a value) is INJECTED because the two
    callers must read the before-state from different places — the CLI from read-only sqlite
    (_populated_books), a plugin from the live handle (populated_via_api). The verdict itself
    stays one function, so both writers refuse exactly the same change-sets."""
    setf = collections.defaultdict(dict)
    for o in ops:
        if o.get("op") == "set_field": setf[o["field"]].update(o["values"])
    for field, values in setf.items():
        before = populated(field)
        after = _predict_populated(before, values)
        if _is_wipe(len(before), len(after)):
            raise GuardrailError(f"ABORT: writing would empty '{field}' from {len(before)} populated books "
                                 f"down to {len(after)} — a runaway change-set? Nothing was written. "
                                 "Re-run with --force if this is intentional.")


def backup_db(dst: str | None = None, src: str | None = None) -> str:
    """Snapshot metadata.db (default: a fresh backups_dir() path) and return the snapshot's path.

    Uses sqlite's **Online Backup API**, not shutil.copy2. In-process a Calibre plugin shares the
    library with a GUI that may write at any moment, and a byte copy of a database being written
    is a torn snapshot — a rollback point that silently isn't one. The backup API takes a
    consistent copy under sqlite's own locking, so the one code path is correct for both callers.

    Verified by reading the book count back out of the copy: the old size-equality check cannot
    survive a page-level backup, and never proved the file was readable in the first place.
    Any failure (I/O, full disk, unreadable result) raises GuardrailError — the write must not
    proceed without a rollback point."""
    src = src or db_path()
    prune = dst is None
    dst = dst or _backup_path()
    try:
        s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        try:
            d = sqlite3.connect(dst)
            try: s.backup(d)
            finally: d.close()
        finally: s.close()
        con = sqlite3.connect(f"file:{dst}?mode=ro", uri=True)
        try: n = con.execute("SELECT count(*) FROM books").fetchone()[0]
        finally: con.close()
    except Exception as e:
        raise GuardrailError(f"backup failed ({dst}: {e}) — aborting before any write.")
    if not n:
        raise GuardrailError(f"backup verify failed ({dst} holds no books) — aborting before any write.")
    if prune: _prune_backups()
    return dst


def write_ops(api, ops: list[dict], force: bool = False, out=print,
              tool: str = "plugin", scope=None, engine=None, model=None) -> None:
    """Apply write-ops IN-PROCESS through Calibre's live handle — the plugin's write path.

    This is why a plugin never needs run_writer(), which shells out to a SECOND process writing a
    library the GUI holds open (#53 — and the process-scan guard reads False from inside the GUI,
    so it cannot be relied on to catch that mistake; this function removes the possibility).

    Same guards as the CLI, by construction: the wipe guard (read through new_api), a snapshot
    before any write, and the edit log — all shared functions. calibre_open() is skipped BY
    DESIGN — in-process there is no second writer to detect; we are the writer it exists to keep
    alone."""
    from scourgify import editlog
    from scourgify.ops import apply_ops
    ops = [o for o in ops if o.get("op") != "set_field" or o.get("values")]
    if not ops: out("  (nothing to write)"); return
    if not force: check_wipe(ops, lambda f: populated_via_api(api, f))
    before = editlog.before_values(lambda f, bs: values_via_api(api, f, bs), ops)
    out(f"  backup: {backup_db()}   (restore: scourgify rollback)")
    rec = editlog.start(tool, ops, before, scope=scope, library=getattr(api, "library_id", None),
                        engine=engine, model=model)
    try:
        apply_ops(api, ops, out=out)
    except BaseException:
        editlog.finish(rec, "failed"); raise
    editlog.finish(rec, "ok")


def run_writer(ops: list[dict], force: bool = False, tool: str = "scourgify", scope=None) -> None:
    """Apply a list of write-ops through Calibre by shelling out to `calibre-debug -e _writer.py`.
    Automatically snapshots metadata.db to data/backups/ first — every write path gets a rollback
    point for free (restore with `scourgify rollback`). Refuses (before writing) a change-set that
    would catastrophically empty a populated column; --force overrides. Appends the edit log.

    The guard, the snapshot and the log are the SHARED functions (check_wipe / backup_db /
    editlog) an in-process plugin writer uses too — see write_ops(). Only the process model
    differs. GuardrailError is converted back to SystemExit here so CLI exit codes and messages
    are unchanged.

    The log is captured on THIS side of the subprocess (before-values read from read-only sqlite,
    lines written before `calibre-debug` is spawned), so a test can pin the record shape with the
    subprocess stubbed and no Calibre installed. `tool` names the caller — run_writer cannot know
    it, and a log that cannot say which pass made a change answers none of the questions it
    exists for."""
    import json, time, tempfile, subprocess, shutil
    from scourgify import editlog
    ops = [o for o in ops if o.get("op") != "set_field" or o.get("values")]
    if not ops: print("  (nothing to write)"); return
    if calibre_open(): raise SystemExit("Calibre is running — close it first (it locks metadata.db), then re-run.")
    try:
        con = ro_connect()
        try:
            if not force:                           # last-line wipe guard, before any backup/write
                check_wipe(ops, lambda f: _populated_books(con, f))
            # ...but the log's before-read is NOT conditional on the guard: --force means "skip
            # the guard", not "write blind", and a forced run is the one most likely to need undo.
            before = editlog.before_values(lambda f, bs: column_values(con, f, bs), ops)
            lib_uuid = library_uuid(con)
        finally:
            con.close()
        cb = shutil.which("calibre-debug") or "/Applications/calibre.app/Contents/MacOS/calibre-debug"
        if not (shutil.which("calibre-debug") or os.path.exists(cb)):
            raise SystemExit("calibre-debug not found (install Calibre's CLI tools).")
        bak = backup_db()
    except GuardrailError as e:
        raise SystemExit(str(e))
    print(f"  backup: {bak}   (restore: scourgify rollback)")
    rec = editlog.start(tool, ops, before, scope=scope, library=lib_uuid)
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False); json.dump(ops, f); f.close()
    print("  → writing via calibre-debug …")
    try:
        # generous ceiling: a real batch write finishes in seconds/minutes — this only
        # catches a wedged calibre-debug so a scripted/CI run can't hang forever.
        rc = subprocess.run([cb, "-e", os.path.join(HERE, "_writer.py"), "--", f.name],
                            env={**os.environ, "CALIBRE_LIBRARY": library()}, timeout=3600).returncode
    except subprocess.TimeoutExpired:
        editlog.finish(rec, "timeout")
        raise SystemExit(f"writer timed out after 1h (calibre-debug wedged?) — library backup at {bak}")
    finally:
        os.unlink(f.name)
    editlog.finish(rec, "ok" if rc == 0 else "failed")
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
