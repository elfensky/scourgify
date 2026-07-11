# Centralized user config: `~/.config/scourgify/`

**Date:** 2026-07-11
**Status:** approved, ready for implementation
**Ships in:** scourgify 1.3.0

## Problem

Per-user files (`config.toml`, `overrides/`, `data/`) currently resolve against
`os.getcwd()`. That works when you `uv run` from the repo (CWD == repo root) but is
wrong for an installed copy (`uv tool install scourgify` / `pipx`): the tool writes
proposals and reads overrides relative to wherever the terminal happens to be, so a
global install has no stable home and can't find the user's config. The tool needs
one fixed, standard location.

## Decision

One folder holds **everything** user-owned:

```
~/.config/scourgify/
├── config.toml
├── overrides/            # fandoms.csv, tropes.csv, decompose.csv, classify_vocab.txt, promote_aliases.csv, …
└── data/                 # proposals, review maps, intermediates, rejects.csv
    └── backups/          # metadata.db snapshots
```

Backups live under this root too (not split off to `~/.local/share`) — one place to
find, back up, or wipe. The trade-off (config-dir holds sizeable binary snapshots) is
handled by surfacing the size in the wizard rather than by relocating (see §4).

Scope: **mac + Linux only.** No Windows (`%APPDATA%`) support — the user doesn't run it
there; revisit if that changes. Not a concern for this release.

## 1. The resolver

One function in `common.py`, the single owner of "where does user data live":

```python
def user_dir() -> str:
    """The one root for all user-owned files (config.toml, overrides/, data/).
    $SCOURGIFY_HOME wins (tests, experiments); else XDG ($XDG_CONFIG_HOME or ~/.config)/scourgify."""
    base = os.environ.get("SCOURGIFY_HOME")
    if not base:
        base = os.path.join(
            os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
            "scourgify",
        )
    return base
```

- `$SCOURGIFY_HOME` — explicit override, absolute path. Lets tests point at a temp dir
  and lets a power user relocate the whole tree. Highest precedence.
- `$XDG_CONFIG_HOME/scourgify` — the XDG spec's config location when the env var is set.
- `~/.config/scourgify` — the XDG default when it isn't.

`user_dir()` does **not** create the directory. Writers already `os.makedirs(..., exist_ok=True)`
before writing (backups, proposals); config write and override write get the same one-line
guard where they don't have it. Read paths that don't exist behave exactly as an absent
CWD file does today (empty maps, "no proposal") — no new failure mode.

## 2. The swap — replace all 12 `os.getcwd()` sites

Pure path substitution, no logic changes. The frozen-at-import constants repoint through
`user_dir()`:

**`common.py`**
- L19 `DATA = os.path.join(user_dir(), "data")`
- L20 `BACKUPS`, L22 `REJECTS` — already derived from `DATA`, follow automatically.
- L94 `load_config`: `p = path or os.path.join(user_dir(), "config.toml")`

**`wrangle.py`** — L55, L580 (`overrides/` dir), L488 (`write_config` → `config.toml`):
`os.getcwd()` → `user_dir()`.

**`classify.py`** — L60 (`overrides/classify_vocab.txt`), L76 (`overrides/promote_aliases.csv`):
`os.getcwd()` → `user_dir()`.

**`promote.py`** — L20 `ALIASES`, L153 (`classify_vocab.txt`), L154 (`tropes.csv`):
`os.getcwd()` → `user_dir()`. (`ALIASES` stays an import-time constant, just rooted at
`user_dir()` — same shape as `common.DATA`, and tests already monkeypatch these constants.)

**`overrides.py`** — L161 `tgt = DEF if master else os.path.join(user_dir(), "overrides")`.

**`wizard.py`** — L69 `setup_needed` config.toml existence check → `user_dir()`.

The other `data/`-derived constants (`classify.PROP/RANK/…`, `promote.REVIEW/LEDGER`) already
build off `common.DATA`, so they move for free once `DATA` repoints.

## 3. Migration (one-time, done for this user by hand)

No auto-migration code — single user, single machine. **Selective, not wholesale:** this repo's
`data/` is overloaded — it holds the package's *runtime* files (backups, proposals, ledger) **and**
the *maintainer workspace* (the AO3 dump derivatives `ao3_*.csv` / `ao3_build/`, the cluster
intermediates `_cluster_*` / `_funify_*`, and the `*_map.csv` review maps). The repo-root build
scripts (`build_ao3_layer.py`, `build_classify_seed.py`, `build_defaults.py`) read that workspace via
their own `HERE/data`, so it must **stay in the repo**. Only the user-config + runtime subset moves:

```bash
mkdir -p ~/.config/scourgify/data
mv config.toml ~/.config/scourgify/                      # if present
mv overrides   ~/.config/scourgify/                      # the crown jewels — curated maps
mv data/backups ~/.config/scourgify/data/               # ~120 MB of rollback snapshots
mv data/classify_* data/promote_* ~/.config/scourgify/data/ 2>/dev/null   # runtime proposals/ledger/archives
[ -f data/rejects.csv ] && mv data/rejects.csv ~/.config/scourgify/data/
```

Maintainer files (`ao3_*`, `_cluster_*`, `_funify_*`, `ao3_build/`, `*_map.csv`, and other one-off
analysis CSVs) are **left in `repo/data/`**. End state: `repo/data/` = maintainer workspace;
`~/.config/scourgify/data/` = runtime. Verify with `scourgify audit` (reads the moved overrides) and a
wizard launch (reads config + the moved backups, which the new header line now sizes).

`.gitignore` already ignores `overrides/`, `data/`, `config.toml` — no repo-hygiene change needed.

## 4. Wizard: backup size + cleanup nudge

Backups auto-prune to the newest 20 (`BACKUP_KEEP`), so the directory is bounded — but 20
snapshots of a large library is real disk, and now they sit under the config dir. Surface it.

**Helper (pure, testable) in `common.py`:**

```python
def backups_size() -> tuple[int, int]:
    """(count, total_bytes) of the snapshot files in BACKUPS. (0, 0) if none."""
    files = glob.glob(os.path.join(BACKUPS, "*.db"))
    return len(files), sum(os.path.getsize(f) for f in files)
```

**`wizard.snapshot()`** adds `"backups": common.backups_size()` to its dict.

**`wizard.header()`** gains a row when snapshots exist:
- `n, b = info["backups"]`; format bytes as MB/GB.
- Normal: `backups   14 snapshots · 340 MB   [dim]~/.config/scourgify/data/backups[/]`
- Large (`b > BACKUP_WARN`, a new constant = 500 MB): the size turns `[yellow]`, with
  `→ getting large; delete old snapshots to reclaim space` appended.

No auto-delete and no new subcommand — a nudge, as requested. The user trims by hand
(`rm` the oldest `data/backups/*.db`); `scourgify rollback --list` shows what's there.
If a one-touch trim is wanted later, that's a follow-up (`rollback --prune-to N`), out of
scope here.

## 5. Tests — `tests/test_paths.py` (new, no library/network)

- `user_dir()` returns `$SCOURGIFY_HOME` verbatim when set.
- With `SCOURGIFY_HOME` unset and `XDG_CONFIG_HOME` set → `<xdg>/scourgify`.
- With both unset → `~/.config/scourgify` (monkeypatch `expanduser`/`HOME`).
- `backups_size()` on a temp dir with N fake `*.db` files → `(N, sum_of_sizes)`; `(0, 0)`
  when empty. (Point `common.BACKUPS` at a temp dir via monkeypatch.)

`env` manipulation uses `monkeypatch.setenv/delenv` so tests don't leak. Run under
`uv run tests/test_paths.py`; add to CI alongside the existing two suites.

## 6. Docs

- **CLAUDE.md** — the "resolve against the current working directory" / `os.getcwd()`
  paragraph in the Packaging section becomes "resolve against `user_dir()` —
  `$SCOURGIFY_HOME` else `$XDG_CONFIG_HOME/scourgify` else `~/.config/scourgify` — so an
  installed copy has a stable home instead of writing under the invoking CWD." Note
  `common.user_dir()` as the single owner.
- **README** — the "FanFicFare → Calibre columns" / config sections that tell users where
  `config.toml` and `overrides/` live get the new path.

## 7. Release

Global installs need this to find `~/.config`, so it ships as a version bump:
`__version__` → `1.3.0`, then the standard git-flow-lite release (branch → PR to `develop`
→ Release PR to `main` → tag → `gh workflow run publish.yml -f target=pypi`).

## Non-goals

- Windows support.
- Auto-migration of an existing CWD tree (single user; done by hand).
- A backups cleanup command (nudge only).
- Any change to the bundled `defaults/` resolution — those stay `common.HERE`/`DEFAULTS`
  (read-only, inside the package), untouched.
