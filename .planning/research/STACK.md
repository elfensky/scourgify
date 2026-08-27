# Stack Research — Calibre Plugin API Surface (phases 6–8, cross-platform)

**Domain:** Calibre GUI plugin (InterfaceAction), Python 3.14.6 / Qt6, macOS + Windows + Linux
**Researched:** 2026-08-28
**Confidence:** overall MEDIUM-HIGH (Calibre's plugin docs are thin/example-driven rather than
a full API reference; every claim below is tagged HIGH/MEDIUM/LOW individually — see
Confidence notes per section. This does NOT re-research the existing core; it targets only the
plugin-surface gaps the milestone needs: write actions, dashboard, review-in-library-view,
in-plugin column creation, Windows.)

This is not an ecosystem-of-libraries research doc — there is exactly one host framework
(Calibre's own plugin API over Qt6) and the codebase already made every dependency decision
(stdlib core, `qt.core` for Qt, no vendored deps). What's researched here is **API surface**:
which Calibre/Qt calls exist, their exact module paths, and which of them are load-bearing for
the six remaining features.

## Recommended API Surface

### Core Plugin Framework

| API | Module | Purpose | Confidence |
|-----|--------|---------|------------|
| `InterfaceAction` | `calibre.gui2.actions` | Base class for the toolbar action; already in use (`plugin/action.py`) | HIGH (in codebase + [manual.calibre-ebook.com/plugins.html](https://manual.calibre-ebook.com/plugins.html)) |
| `InterfaceActionBase` | `calibre.customize` | The `__init__.py` wrapper plugin; `actual_plugin = "mod:cls"` string, never an import | HIGH (in codebase + [github.com/kovidgoyal/calibre customize/\_\_init\_\_.py](https://github.com/kovidgoyal/calibre/blob/master/src/calibre/customize/__init__.py) L730+) |
| `ThreadedJob` | `calibre.gui2.threaded_jobs` | The ONE background-job mechanism; already the single call site in `action._run` | HIGH (in codebase + [threaded_jobs.py](https://github.com/kovidgoyal/calibre/blob/master/src/calibre/gui2/threaded_jobs.py)) |
| `Dispatcher` | `calibre.gui2` | Wraps a callback so `ThreadedJob.start_work`'s call from the **worker thread** gets marshalled back to the GUI thread | HIGH — disassembly-verified in this repo's own comments (`action.py` docstring, "disassembled, Calibre 9.11") |
| `qt.core` | `qt.core` | Calibre's own Qt5/Qt6 abstraction shim — the ONLY sanctioned way to import Qt classes | HIGH (in codebase + confirmed via manual + MobileRead migration thread) |
| `JSONConfig` | `calibre.utils.config` | Global (cross-library) plugin settings; already in use (`plugin/config.py`, keys) | HIGH (in codebase, works today) |

### Write Path (phase 6 — actions on a selection)

| API | Module | Purpose | Confidence |
|-----|--------|---------|------------|
| `db.new_api.set_field(name, {book_id: value})` | `calibre.db.cache.Cache` | The one in-process write primitive — already proven in `job_db_smoke` (scratch write on a throwaway library, Calibre 9.11) | HIGH — proven in this repo, not just docs |
| `db.new_api.field_for(name, book_id)` | same | Read-back for the apply-time conflict re-check | HIGH — already used in `action.py` |
| `db.new_api.all_book_ids()` | same | Library-scope reads | HIGH — already used |
| `db.new_api.library_id` | same | The uuid identity check (`current_uuid()` in `action.py`) — a plain attribute, not a Qt call | HIGH — already used and documented as deliberately NOT re-derived from the db file (B1.1 amendment) |
| `gui.current_db` | `calibre.gui2.actions.InterfaceAction` (via `self.gui`) | The **legacy** `LibraryDatabase2`-shaped wrapper — needed for anything `new_api` doesn't expose (marked books, below) | HIGH |

**No new API needed for phase 6's write verbs.** `common.write_ops(api, ops)` already exists and
already runs `ops.apply_ops` against exactly this `new_api` handle — phase 6 is wiring
(menu verb → `wrangle.plan().restrict(ids)` / `staleness` scope → `write_ops`), not new Calibre
surface. The one still-open item CLAUDE.md and the NLSpec both name: `wrangle.Plan.write`,
`staleness.write`, `classify.apply_proposal`, `promote.backfill` still call `run_writer`
directly and need the same injected `write=`/`write_ops` seam `Plan.run(ask=…)` already models —
this is an internal refactor, not an unresearched Calibre API.

### Custom Column Creation (phase 6/7 — in-plugin setup)

| API | Module | Purpose | Confidence |
|-----|--------|---------|------------|
| `DB(lib_path).create_custom_column(label, name, datatype, is_multiple, editable=True, display={})` | `calibre.library.database2` (legacy `DB`) | Creates the column at the storage layer | HIGH — already a documented gotcha in this repo's own CLAUDE.md, exercised by `scourgify setup` today |
| Calibre's own `CreateCustomColumn.must_restart()` pattern | `calibre.gui2.preferences.create_custom_column` | **The sanctioned UX precedent to copy.** Calibre's own Preferences → "Add custom column" dialog does NOT write the column immediately and refresh live state — it stages the definition and sets a restart flag; `must_restart()` on the creator makes every subsequent attempt (from that dialog *and* from Preferences generally) refuse until the user restarts. The dialog's own copy: "Pressing OK will require restarting calibre even if nothing was changed." | MEDIUM — verified from Calibre's own source ([create_custom_column.py](https://github.com/kovidgoyal/calibre/blob/master/src/calibre/gui2/preferences/create_custom_column.py)), but the exact call sequence wasn't fully extractable via web fetch (large file); the *shape* (stage → flag → refuse further creates → prompt restart) is confirmed, the literal method names should be re-checked against the actual file before coding |

**Recommendation:** scourgify's in-plugin setup should NOT try to make a newly created column
usable in the same Calibre session — that is precisely the "legacy-DB reopen desyncs the live
GUI's models" problem the NLSpec already ruled out for the in-process contract (B2 step 6:
`create_column` is deliberately excluded from `common.write_ops`). Mirror Calibre's own pattern
instead of inventing one: call `common.write_ops`'s sibling path that shells to the *existing*
CLI mechanism (`DB(lib_path).create_custom_column` — same call `scourgify setup` already makes,
just triggered from a plugin job with Calibre closed-for-write semantics is wrong here since the
GUI has the library open) — concretely, run it against the **legacy `DB` object opened fresh
against `lib_path`** (not `gui.current_db`), exactly like `setup.py` does today, then set a
"restart required" flag surfaced in the dashboard/menu, refuse further column-creation attempts
until restart (copying `must_restart()`), and let Calibre's own restart-and-reload pick up the
new column — no attempt to hot-refresh `field_metadata` on the live `gui.current_db`.

### Marked Books (phase 8 — review in the library view)

| API | Module | Purpose | Confidence |
|-----|--------|---------|------------|
| `gui.current_db.set_marked_ids(id_or_id_set)` | legacy `LibraryDatabase2` wrapper (**not** `new_api`) | Marks books for the `marked:` search operator | HIGH — confirmed by search + matches this repo's own NLSpec B8.2 which independently specifies exactly this API |
| `gui.search.setEditText('marked:true')` + `gui.search.do_search()` | `calibre.gui2.actions` / main GUI | Scopes the library view to just the marked books, so the user pages through them with Calibre's own row/cover/series UI | HIGH — standard, documented pattern used by multiple existing calibre plugins |
| Snapshot/restore of prior marks + active search | none dedicated — read `db.data.marked_ids` (or re-run `marked:` search) before marking, restore after | NLSpec B8.1 requires this; there is no single "get current marks" call documented, so this needs its own small helper | MEDIUM — the *requirement* is confirmed (own NLSpec), the *exact read-back call* needs verification in a spike against Calibre 9.11's legacy db object (`db.data.marked_ids` is the most likely accessor per Calibre's `db/legacy.py`) |

**Important:** `new_api` (`Cache`) has **no concept of marked books** — this is the one place
phase 8 must reach past `new_api` into the legacy wrapper. That is consistent with the
in-process write contract (marks aren't a metadata write, they're GUI browsing state — no
backup/guard/log implications) and does not conflict with B2's "one write path" rule, which
governs `(book, field)` metadata ops, not view state.

### Windows Differences (cross-platform hardening)

| Concern | macOS/Linux (current) | Windows | Confidence |
|---------|------------------------|---------|------------|
| Config root | `common.user_dir()`: `$SCOURGIFY_HOME` → `$XDG_CONFIG_HOME/scourgify` → `~/.config/scourgify` | Calibre itself resolves its own config dir via `winutil.special_folder_path(winutil.CSIDL_APPDATA)` (i.e. `%APPDATA%`, typically `C:\Users\<user>\AppData\Roaming\calibre`), overridable by `CALIBRE_CONFIG_DIRECTORY`. scourgify's own `user_dir()` must add an `os.name == 'nt'` branch reading `%APPDATA%` the same way — **do not add `platformdirs`**, `common.py` must stay stdlib-only to keep importing clean under `calibre-debug`'s empty site-packages (the hard constraint CLAUDE.md states repo-wide) | HIGH for Calibre's own resolution ([constants.py](https://github.com/kovidgoyal/calibre/blob/master/src/calibre/constants.py)); HIGH for the "no new dependency" constraint (repo-documented); MEDIUM for the exact scourgify-side branch (not yet written) |
| Process detection (`calibre_open()`) | `pgrep` → `ps` fallback chain | Neither exists. Windows equivalent: `tasklist /FI "IMAGENAME eq calibre.exe" /NH` via `subprocess.run`, parsed for a non-empty result — same "fail closed if neither exists" posture, just a third branch, not a rewrite | MEDIUM — `tasklist` is the standard built-in Windows tool for this (stdlib-reachable via subprocess, no new dependency); exact Calibre process name (`calibre.exe`) confirmed by public process-info listings, not by testing on a real Windows box yet |
| `calibre-debug` location | macOS: inside the app bundle (`/Applications/calibre.app/Contents/MacOS/calibre-debug`, confirmed in the official manual). Linux: on `$PATH` after a normal install. | Windows: `calibre-debug.exe` ships alongside `calibre.exe` in the install directory (typically `C:\Program Files\Calibre2\`) and is **not guaranteed to be on PATH** — `run_writer()`'s subprocess call must resolve it explicitly (e.g. via the Windows registry install-path key, or by requiring it on PATH and documenting that Calibre's Windows installer offers a "add to PATH" option) rather than assuming a bare `calibre-debug` resolves | MEDIUM — the bundle-location fact for macOS is HIGH (official manual quote); the exact Windows install path is the conventional default, not verified against this project's own Windows box yet — flagged as a manual-verification item per PROJECT.md's Windows handoff plan |
| `os.chmod(..., 0o600)` on the keys file | Works (POSIX permission bits) | **No-op / meaningless on Windows** (NTFS ACLs, not POSIX mode bits) — `plugin/config.py`'s `save_settings()` already wraps this in `try/except OSError: pass`, so it degrades safely today; Windows key-file protection would need `icacls`/ACL calls to do properly, which the project's own Non-Goals section (no OS keyring) suggests is out of scope — the existing silent-degrade is the right behavior, not a gap to close | HIGH (Windows chmod semantics are well-established; existing code already tolerant) |
| Qt backend | `qt.core` shim over PyQt6/Qt6 (Calibre ≥ 6) | Identical — `qt.core` is Calibre's own cross-platform abstraction; no Windows-specific Qt code needed | HIGH |
| `afm.swift` / apple engine | macOS 26+ only | Absent entirely; `engines.usable_engines()` already derives availability from a binary/toolchain probe, so Windows naturally reports apple unusable without new platform-detection code — this is why "key-first onboarding off-mac" (PROJECT.md) is the chosen fix, not a fake apple stub | HIGH — mechanism already exists in `engines.py` |

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| `from PyQt5.QtWidgets import ...` / `from PyQt6.QtWidgets import ...` (direct binding imports) | Calibre 6+ ships PyQt6 only; Calibre added a *compatibility shim* so legacy plugins written against `PyQt5.*` names still resolve (confirmed on the MobileRead Qt6 migration thread) — but that shim exists for **old** plugins, not as sanctioned practice for new code, and it does not cover every enum rename (`QtCore.QEventLoop.ExcludeUserInputEvents`-style fully-qualified enum access changed under PyQt6) | `from qt.core import ...` exclusively — Calibre's own abstraction, already used throughout `plugin/action.py` and `plugin/config.py` |
| `QThread` / raw `threading.Thread` for library work | Bypasses Calibre's job manager entirely — no cancel button, no job-list visibility, no `Dispatcher` marshalling back to the GUI thread, and a raw thread touching Qt objects on completion is a use-after-free-class bug class in PyQt | `ThreadedJob` via the single `action._run` call site (already enforced by `tests/test_plugin_source.py`) |
| `multiprocessing`, `subprocess` (for a second writer), `ThreadPoolExecutor` inside the plugin | Explicitly forbidden by NLSpec B2.1/B3.2 and source-grep-enforced (`tests/test_plugin_source.py`) — a second process writing the same `metadata.db` the GUI holds open is the exact hazard this whole plugin milestone exists to delete | `ThreadedJob` (reads AND writes) + in-process `common.write_ops` |
| `run_writer()` / `calibre-debug -e` from inside the plugin | It shells to a second Python process against a library the GUI already has open — the two-writers hazard (#53) | `common.write_ops(api, ops)` — same `ops.apply_ops` executor, called in-process |
| `db.prefs` (Calibre's per-library preference store) for scourgify's own config/column-map | Tempting because it's genuinely per-library, but the project has already committed (PROJECT.md Constraints) to namespacing scourgify's *own* state — proposals, failure log, edit log, config/column map — under `user_dir()` keyed by library uuid, so the **same files** stay readable by the CLI outside Calibre. Splitting scourgify config between `db.prefs` (GUI-only) and `config.toml` (CLI-only) would fork the single source of truth the "two front doors, one flow" rule depends on | Keep `config.toml`/`overrides/` filesystem-based, add the per-library-uuid subdirectory level under the existing `user_dir()` tree |
| `os.path.exists()` / `open()` against `common.HERE` inside the plugin zip for `defaults/*.csv` | Already-diagnosed gap (CLAUDE.md, NLSpec phase-4/5 amendments): `common.HERE` points *inside the zip*, and both `wrangle.load_maps()` and `classify.load_vocab()` were silently building empty maps / under-quoting cost before this was caught. Not a new finding — flagging it here because it is exactly the kind of "looks like a stdlib file read, works in dev, breaks in the zip" trap this Windows/plugin-surface research exists to catch generally | `importlib.resources` (`get_resources()` in `calibre.customize` terms) or extract-once to a version-keyed cache dir — the `DEFAULTS` resource seam the NLSpec already names as required before phase 6's engine picker can show a cost |
| `platformdirs` or any new PyPI dependency for Windows path resolution | Violates the hard constraint (repeated throughout CLAUDE.md) that `common.py` must import cleanly under Calibre's bundled Python with **empty site-packages** — any dependency added to make Windows nicer breaks the plugin build for everyone | A manual `os.name == 'nt'` branch reading `os.environ['APPDATA']`, mirroring exactly how Calibre resolves its own config dir |
| `QDialog` opened with `exec()` / modal for the dashboard | NLSpec B6 step 4 requires non-modal so "a running job and a browsing user coexist" — a modal event loop blocks the rest of the GUI, defeating that requirement outright | A non-modal top-level window (`QDialog`/`QWidget` shown with `.show()`, not `.exec()`, and `Qt.WindowType.Window` rather than `Qt.WindowType.Dialog` if a dialog class is reused) — see Dashboard note below |

## Dashboard: Dockable vs. Non-Modal Window (open question, flagged not resolved)

PROJECT.md's requirement text says "non-modal dockable panel"; the NLSpec (B6 step 4) requires
only **non-modal**, explicitly citing "not a modal dialog" as the resolved open question — it
does not require true `QDockWidget` docking into Calibre's main window layout.

- Calibre's main window (`calibre.gui2.main.Main`) **is** a `QMainWindow` subclass, so
  `self.gui.addDockWidget(area, dock_widget)` is technically reachable from a plugin (it's
  inherited Qt API, not something Calibre blocks). — **Confidence: LOW.** No worked example of a
  third-party Calibre plugin doing this was found in this research pass; Calibre's own docked
  panels (Tag browser, cover grid, etc.) are managed by Calibre's own layout/QSS system, and an
  externally added dock widget's persistence across restarts, position saving, and interaction
  with Calibre's "Layout" preferences is unverified.
- The safer, precedent-backed pattern — used by other mature Calibre plugins for anything
  dashboard-shaped — is a **non-modal top-level window** (`QWidget`/`QDialog` shown via `.show()`
  rather than `.exec()`), which satisfies "non-modal" and "coexist with a running job" without
  betting on undocumented main-window docking behavior.

**Recommendation:** build the dashboard as a non-modal top-level window for phase 7 (it
satisfies the NLSpec's actual acceptance criterion), and treat true `QDockWidget` integration as
a stretch goal validated by a short spike (a few lines: add a dock widget from a test plugin,
restart Calibre, confirm it doesn't corrupt the saved layout) rather than a load-bearing
assumption for the phase-7 plan.

## Installation / Build

No new packages. The plugin has zero dependencies beyond the Calibre host (already stated in
`.planning/codebase/INTEGRATIONS.md` and the NLSpec Dependencies section: "none beyond the
host"). `build_plugin.py` continues to be the only build step:

```bash
uv run build_plugin.py    # → dist/scourgify-plugin-<version>.zip, version from pyproject.toml
```

No `pip install` / `npm install` equivalent applies — Calibre supplies Qt6, `qt.core`,
`ThreadedJob`, `JSONConfig`, and the db layer at runtime; nothing is bundled or vendored beyond
scourgify's own `scourgify/` package at the zip root.

## Version Compatibility

| Component | Compatible With | Notes |
|-----------|------------------|-------|
| Calibre ≥ 6.0 | Qt6 / PyQt6 only | Calibre 5 is PyQt5-only (already an explicit Out-of-Scope line in PROJECT.md — "Bumping minimum_calibre_version below 6.0"); `qt.core` is the abstraction that makes the plugin code itself version-agnostic across 6.x/7.x/8.x/9.x |
| Calibre 9.11 (dev target) | Python 3.14.6, empty site-packages | Matches this repo's CI floor for the plugin lane; every measurement cited above (menu build 0.3–0.7 ms, `wrangle.load_maps()` 870 ms, etc.) was taken against this exact version — re-measure if the dev target version moves |
| `minimum_calibre_version` in the plugin wrapper | Tuple, e.g. `(6, 0, 0)` | Set on `InterfaceActionBase`; Calibre refuses to load the plugin below this — this is the actual enforcement mechanism for the "Calibre ≥ 6.0" constraint, not just documentation |
| `qt.core` | Calibre ≥ ~5.next (introduced ahead of the Qt6 cutover) | Already what `plugin/action.py`/`plugin/config.py` import from; nothing to change |

## Sources

- [manual.calibre-ebook.com/plugins.html](https://manual.calibre-ebook.com/plugins.html) — InterfaceAction attributes/methods, JSONConfig, get_resources/get_icons, config_widget/save_settings — MEDIUM confidence (summarized via fetch, not the raw page; cross-check exact attribute names against the live page before coding a new one)
- [manual.calibre-ebook.com/creating_plugins.html](https://manual.calibre-ebook.com/creating_plugins.html) — genesis(), action_spec tuple shape, JSONConfig example, plugin zip layout, plugin-import-name file, minimum_calibre_version, qt.core convention, macOS command-line-tools bundle location — HIGH confidence (official tutorial, directly quoted)
- [github.com/kovidgoyal/calibre — customize/\_\_init\_\_.py](https://github.com/kovidgoyal/calibre/blob/master/src/calibre/customize/__init__.py) — `InterfaceActionBase`, `Plugin.version`/`minimum_calibre_version`/`supported_platforms`, `config_widget`/`save_settings`/`is_customizable` — HIGH confidence (source quoted with line numbers)
- [github.com/kovidgoyal/calibre — gui2/threaded_jobs.py](https://github.com/kovidgoyal/calibre/blob/master/src/calibre/gui2/threaded_jobs.py) — `ThreadedJob`/`ThreadedJobServer` — MEDIUM confidence (existence and location confirmed; exact `start_work` callback-from-worker-thread behavior is already independently verified in this repo's own code comments, which is the stronger source)
- [github.com/kovidgoyal/calibre — gui2/preferences/create_custom_column.py](https://github.com/kovidgoyal/calibre/blob/master/src/calibre/gui2/preferences/create_custom_column.py) — `CreateCustomColumn`, `must_restart()` staged-restart pattern — MEDIUM confidence (shape confirmed, exact method call sequence not fully extracted; re-read the file directly before implementing)
- [github.com/kovidgoyal/calibre — constants.py](https://github.com/kovidgoyal/calibre/blob/master/src/calibre/constants.py) — Windows config_dir via `winutil.special_folder_path(CSIDL_APPDATA)`, `CALIBRE_CONFIG_DIRECTORY` override — HIGH confidence for the mechanism, MEDIUM for exact code (summarized, not quoted)
- MobileRead Forums — ["Plugin devs: Upcoming migration to Qt 6"](https://www.mobileread.com/forums/showthread.php?t=344064) — PyQt5→PyQt6 compat shim behavior, enum fully-qualification changes, icon-loading breakage — MEDIUM confidence (community thread, but the primary/only detailed account of this migration found; corroborated by `qt.core`'s existence as the sanctioned fix)
- This repo's own `CLAUDE.md`, `.planning/codebase/STACK.md`, `.planning/codebase/INTEGRATIONS.md`, `plugin/action.py`, `plugin/config.py`, and `docs/superpowers/specs/2026-08-06-calibre-plugin-nlspec.md` — HIGH confidence (primary source; several claims above — e.g. `set_field`/`field_for`/`library_id` working from a `ThreadedJob` worker, `set_marked_ids` requiring the legacy db, the `create_custom_column` reopen gotcha — are already measured/proven in this codebase's own history, not merely documented elsewhere)

## Gaps / Needs a Spike Before Planning Commits To Them

- **`QDockWidget` viability** — unverified against a real Calibre session; the non-modal-window
  fallback is safe to plan around without this.
- **Exact Windows `calibre-debug.exe` resolution strategy** — the conventional install path is
  known, but this project's own Windows handoff process (PROJECT.md: "manual Windows passes
  happen there via handoff prompts") is the right place to confirm it against the real desktop,
  not this research pass.
- **The precise read-back call for "current marked ids"** (for B8.1's snapshot/restore) — the
  write side (`set_marked_ids`) is solid; the read side needs a five-minute check against
  Calibre 9.11's legacy db object before phase 8 planning finalizes the snapshot/restore helper's
  shape.
- **`CreateCustomColumn.must_restart()`'s exact call sequence** — the UX shape is confirmed
  (stage → flag → refuse → restart), but the literal method-by-method mechanics should be
  re-read from source at implementation time rather than assumed from this summary.

---
*Stack research for: Calibre plugin API surface (phases 6–8)*
*Researched: 2026-08-28*
