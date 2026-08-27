# Architecture Research — Calibre Plugin Integration (Phases 6–8)

**Domain:** Wiring a Qt GUI front door onto an existing Python core that already has one write
path (`ops.apply_ops` behind `common.write_ops`/`common.run_writer`), an edit log
(`editlog.py`), and a read-only `ThreadedJob` skeleton (`plugin/action.py`).
**Researched:** 2026-08-28
**Confidence:** HIGH (component boundaries, write-path gaps, build order) / MEDIUM (exact Qt
API names for progress polling and library-change signal — verified against upstream Calibre
source and docs, but not against a running Calibre instance)

This is integration research, not greenfield architecture: the layers already exist
(`.planning/codebase/ARCHITECTURE.md`) and phases 1–5 of the plugin are shipped. The question is
where eight new pieces attach without breaking the invariants `tests/test_plugin_source.py` and
`tests/test_plugin_safety.py` already enforce by source-grep. Everything below assumes that
enforcement stays in force — new modules extend its `MODULES`/`QT_MODULES` lists, they don't
work around them.

## Standard Architecture

### System Overview

```
┌──────────────────────────── GUI THREAD (Qt) ─────────────────────────────┐
│                                                                            │
│  action.build_menu()          plugin/dashboard.py           plugin/      │
│  (pure, <1ms, id list)   ◄──►  QDockWidget, header,          review.py    │
│         │                     library_changed() rebind       (marks +    │
│         │                                 │                  panel)      │
│         │                                 │                     │        │
│         ▼                                 ▼                     ▼        │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │        pure "what are the options" dialogs (Qt widgets)            │  │
│  │  render the SAME data report.py's menu-option functions compute    │  │
│  │  (moved out of wizard.py so wizard + Qt build choices from one     │  │
│  │  source — no core call here, no library touch)                     │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│         │ resolved choice (scope/engine/action) baked into job args      │
│         ▼                                                                 │
│  action._run(description, func, args, done)   ← THE ONE ThreadedJob site │
│         │ Dispatcher(done or _done)                                      │
└─────────┼──────────────────────────────────────────────────────────────┘
          │ dispatch                                    ▲ Dispatcher-wrapped
          ▼                                              │ callback (worker→GUI)
┌───────────────────────── WORKER THREAD (ThreadedJob) ─────────────────────┐
│                                                                            │
│  job_*(lib_path, lib_uuid, ..., api=None, now_uuid=None,                  │
│        abort=Event, log=Log, notifications=Queue)                         │
│         │                                                                 │
│         │  from scourgify import common, <tool>   ← imported HERE, never  │
│         │  common.set_library(lib_path); identity check vs now_uuid()     │
│         ▼                                                                 │
│  READ:  common.ro_connect()  (a second read-only sqlite handle — safe     │
│         alongside the live GUI, proven in phase 4)                        │
│  WRITE: common.write_ops(api, ops, ...)  → ops.apply_ops(api, ops, ...)   │
│         api = gui.current_db.new_api, passed in from the GUI thread at    │
│         dispatch time (a plain object reference, not a Qt call)           │
│         │                                                                 │
│         │  per-book: abort.is_set() check; notifications.put((frac,msg)) │
│         ▼                                                                 │
│  returns a plain dict/tuple  → job.result → Dispatcher(done)(job)         │
└────────────────────────────────────────────────────────────────────────┘
          │
          ▼
┌────────────────────────── Calibre's live library ─────────────────────────┐
│  gui.current_db.new_api (Cache)  — the ONE authoritative handle in-process │
│  legacy DB (db.new_api.legacy / DB(path)) — ONLY for create_column (B2.6)  │
│  and for db.set_marked_ids (B8.2 — new_api has no marked-books concept)   │
└────────────────────────────────────────────────────────────────────────┘
```

The shape is not new — it's `plugin/action.py`'s existing `job_inspect`/`job_db_smoke` pattern,
generalized. What's new is that **write** jobs and **multi-step decision** flows need to fit
inside the same "GUI thread does Qt only, worker thread does core only, nothing blocks either"
contract that read jobs already satisfy.

### Component Responsibilities

| Component | Responsibility | Where it lives | New / existing |
|-----------|----------------|-----------------|-----------------|
| `action.py` | Menu build (GUI thread, pure), the ONE `ThreadedJob(` call site, Dispatcher wrapping | `plugin/action.py` | existing, extended |
| Pure option/cost functions | "What can the user choose, and what will it cost/change" — computed from already-fetched data, zero core-side-effects | move from `wizard.py` into `report.py` (or a new sibling `menus.py`) | **moved**, shared |
| Qt scope/engine/review dialogs | Render the pure option data as Qt widgets; the GUI's *only* interactive surface | new `plugin/dashboard.py`, `plugin/review.py` | new |
| `dashboard.py` | QDockWidget: header snapshot, six stage buttons, settings/history/restore entries, per-library rebind | new `plugin/dashboard.py` | new |
| `review.py` | Marks snapshot/restore, per-item accept/reject panel, one write per accepted item | new `plugin/review.py` | new |
| `common.write_ops` / `ops.apply_ops` | The one in-process write executor: wipe guard, backup, edit log, (new) conflict re-check | `src/scourgify/common.py`, `src/scourgify/ops.py` | existing, **extended** (conflict check) |
| Write-run lock | Serializes writes per library; source for "greyed while a write runs" | new, in `src/scourgify/common.py` next to `_LIBRARY` | new |
| `DEFAULTS` resource seam | Bundled `defaults/*.csv` readable inside the zip | `src/scourgify/common.py` (`DEFAULTS`) + `build_plugin.py` (manifest) | new |
| Column creation + restart | First-run setup writes columns via the legacy DB, then prompts a restart | new `plugin/setup_dialog.py` (or folded into `dashboard.py`) driving existing `setup.py` | new wiring, existing core logic |
| `common.user_dir()` | Windows branch (`%APPDATA%`), library-uuid namespacing of `data/`/config | `src/scourgify/common.py` | existing, **extended** |
| `config.py` | Keys, key-first onboarding gating | `plugin/config.py` | existing, extended |
| `build_plugin.py` / `publish.yml` | Zip build joins the wheel's release flow | repo root / `.github/workflows/` | existing, extended |

## The load-bearing finding: the wizard's "ask, then act" shape does not port to Qt as-is

`wizard.py`'s stage functions (`stage_wrangle`, `stage_classify`, …) **interleave** computing a
plan, asking a blocking question (`ui.menu`/`ui.confirm`/`ui.checklist`), and acting — all inside
one Python call that runs synchronously on the terminal's one thread. NLSpec's own phase-6
amendment names this directly: *"Where today's stage functions interleave asking with doing,
phase 7 includes the refactor that moves the asking to the injected-callback seam
(`Plan.run(ask=…)` / `decide=…`) so Qt consumes them without forking sequencing logic."*

That refactor is more constrained than "swap `ui.py` for a Qt module" because of where the two
threads' rules collide:

- A blocking `Prompt.ask()`-equivalent **cannot run on the worker thread** (worker threads never
  touch Qt — `tests/test_plugin_source.py`'s Dispatcher check exists for exactly this).
- A blocking prompt **cannot run on the GUI thread either** if it's waiting on a decision that
  depends on a core computation the worker thread hasn't finished yet — blocking the GUI thread
  on anything is the failure phase 2/3 measured and gated (<100 ms dispatch budget).

So the "Qt backend for the `report.py`/`ui.py` seams" isn't one module with the same call
signatures as `ui.menu`/`ui.confirm`/`ui.checklist` swapped in by import selection. It's a
**two-phase job split**, which the GUI's own contract (B1.3, B4.5) already implies without
spelling out the mechanism:

1. **PLAN phase** — a cheap, read-only job (shape identical to today's `job_inspect`) resolves
   scope/cost/candidate data and returns it as a plain dict. This is exactly what
   `classify.plan()` / `wrangle.plan()` / `_scope_options()` / `_engine_options()` already
   compute in the CLI — none of that logic needs to change, only where it's called from.
2. **Qt picker (GUI thread, no core call)** — a dialog renders that returned dict using the
   *same pure option functions* `wizard.py` uses today (`_scope_options`, `_engine_options`,
   `_synopsis_options`, `_proposal_options`) — these are already pure (input: counts/costs,
   output: `(options, default)` tuples) and already independent of `ui.menu`'s rendering. They
   should move out of `wizard.py` into `report.py` (the file CLAUDE.md already names "the ONE
   owner of the rich-or-plain policy… tools describe WHAT to show, report.py decides HOW") so
   both front doors consume one source and can never diverge — the same principle that already
   keeps `promote.backfill_step`/`staleness.step`/`classify.apply_proposal_step` shared between
   the wizard and the CLI's `--step` flag.
3. **EXECUTE phase** — a second job, dispatched with the resolved choice baked into its args
   (engine name, scope ids, batch size — all plain data, no callback needed), does the actual
   work and write. This matches B4's own steps: "the cost shown is over the exact `todo` set the
   run bills" (computed once, in the PLAN phase) and "the count+price display *is* the gate; no
   second 'are you sure' dialog" (the Qt picker's action button *is* the confirm — there is no
   third round-trip).

This reframes "no confirmation dialogs" (B1) and "diff-after + undo-always instead of
dry-run-before" (B4.5) as *architectural consequences* of the two-phase split, not just UI copy:
the GUI's contract front-loads every decision into step 2 (before any job with side effects
exists) and defers everything else to a **result** the EXECUTE job hands back — a diff, or (for
B8) a review panel — never a mid-job prompt.

The one place a literal `ask=`/`decide=` **callback** injection is still the right shape is where
the CLI already has one: `promote.backfill(decide=…)`. In the plugin this callback does not need
to block on a human answer either — for `backfill`, the human decision already happened in the
review panel (B8) before backfill is invoked, so the GUI's `decide=` implementation can simply
answer from already-known per-book verdicts (a lookup, not a prompt). Where `Plan.step()` /
`ui.checklist()` needs a genuine per-item human decision mid-flow (wrangle's step review, for
example), the plugin's answer is architecturally different again: it is `review.py`'s per-item
panel (component 3) dispatching **one small write job per accepted item**, not one big job that
calls back into a checklist N times. `Plan.restrict(ids)` already narrows a plan's write set to
an arbitrary subset — exactly the primitive a per-item panel needs; it does not need a new
callback shape, just to be driven from the GUI's own event loop instead of a terminal loop.

**Practical shared-module split**, restating the above as file moves:

| Function today | Lives in | Move to | Consumed by |
|---|---|---|---|
| `_scope_options`, `_engine_options`, `_synopsis_options`, `_proposal_options`, `_promote_review_menu`'s table-building | `wizard.py` | `report.py` (pure half) | `wizard.py` (renders via `ui.menu`), Qt dialogs (render via `QComboBox`/`QListWidget`) |
| `_ask_engine`'s *rendering* loop | `wizard.py` | stays (it's `ui.menu`-specific) | `wizard.py` only |
| `_task_hint` / `snapshot()` | `wizard.py` | `report.py` or a shared `status.py` | `wizard.py` header, `dashboard.py` header (issue #74 names this directly: "one header snapshot for wizard + dashboard") |

## Where the single write-run lock lives

**It does not exist yet.** `write_ops()` (`common.py`) already does backup → wipe-guard →
edit-log → `apply_ops` for one caller at a time, but nothing stops two dispatched jobs from
calling it concurrently against the same library — the atomicity contract's "Concurrency: at
most one scourgify write-run per library at a time" (NLSpec, the atomicity contract section) is
new work, tracked implicitly by issue #71 ("unify the pre-write protocol of `write_ops` and
`run_writer`").

Recommended placement: a **process-global, library-uuid-keyed lock**, next to `_LIBRARY` in
`common.py` — the same ponytail reasoning `set_library()`'s docstring already gives for why a
process global is right here ("Calibre has one open library per GUI, every job re-asserts it at
its first line… if concurrent jobs against *different* libraries ever become real, this becomes
a ContextVar — same call sites, same seam"):

```python
# common.py, alongside _LIBRARY
_WRITE_LOCKS = {}          # {library_uuid: threading.Lock()}

def write_lock(library_uuid: str) -> threading.Lock:
    return _WRITE_LOCKS.setdefault(library_uuid, threading.Lock())
```

`write_ops()` acquires `write_lock(api.library_id)` with `blocking=False` at its top and raises
`GuardrailError` immediately if already held (never blocks a worker thread waiting on another
worker thread — that's a second way to freeze the illusion of responsiveness even though it's
off the GUI thread, and it defeats the point of a fast "already running" message). `run_writer()`
(CLI path) does the same keyed by `library_uuid(con)`, so a CLI run and a plugin write against
the *same* library (an edge case, but not an impossible one — a user could have a CLI window and
Calibre open against the same folder) are also mutually exclusive without either process needing
to know about the other's existence beyond the lock dict... except **that reasoning only holds
within one OS process**: a `threading.Lock` in `common.py`'s module state does not span the CLI
process and the Calibre GUI process. Cross-process exclusion is already what `calibre_open()`
covers on the CLI side (refuse to write while the GUI holds the library) — the *new* lock this
milestone needs only has to cover **two jobs inside the same Calibre process**, which is exactly
what a `threading.Lock` gives for free.

**Menu-side consequence** (B1.3, B4 edge case "two classify attempts overlapping → prevented by
the write lock"): `action.build_menu()` needs to know *before* building the menu whether a
write-run is currently active, so it can grey a WRITES verb with "a write is already running"
instead of dispatching a job that will immediately fail the lock. Since `build_menu()` runs on
the GUI thread and must stay core-import-free (`tests/test_plugin_source.py`'s stricter rule for
`action.py`), the lock's *state* (not the lock object itself) needs a GUI-thread-readable flag —
simplest: `action.py` tracks "is a write job currently dispatched" itself (a plain Python
attribute set when `_run` dispatches a WRITES-tagged job and cleared in `_done`), rather than
reaching into `common._WRITE_LOCKS` from the GUI thread at all. That keeps the invariant that
`action.py`'s menu-build path never imports the core, and it's actually a *tighter* guarantee for
the common case (one Calibre window) — the cross-job lock in `common.py` is the correctness net
for the rarer paths (CLI + GUI same library, or a future second write-capable Qt surface), the
`action.py` flag is the UX-responsiveness net for "the button you're about to click."

## Apply-time conflict checks — also not built yet

`ops.apply_ops()` today executes `set_field` unconditionally (`ops.py:66-69`); it has no
per-op "expected before value" parameter and does no comparison. The atomicity contract's
"apply-time conflict rule" (every plan carries the expected before-value; a mismatch is skipped
and reported, never silently overwritten) is required for:

- **B2's postcondition** generally,
- **B7 undo** (undo replays a logged `before` value; without a conflict check on the *forward*
  write too, the asymmetry is that only undo is safe),
- **B8's per-item review** (each accepted item applies immediately as its own op — between the
  proposal being computed and the user clicking accept, another run could have touched the same
  book).

The predicate already exists and is shared correctly: `editlog.conflict(current, expected,
multi)`. What's missing is threading an `expected` value through to `apply_ops` and having it
call that predicate before each `set_field`. The natural seam: `write_ops`/`run_writer` already
compute `before` (via `editlog.before_values`) *before* calling `apply_ops` — pass that same
`before` dict into `apply_ops` as the expected-value source, and read the *current* value through
`api.field_for`/`api.all_field_for` (in-process) or the freshly-`ro_connect()`ed sqlite (CLI)
immediately before each `set_field`. A conflicting op is skipped and its book id collected into
the return value (today `apply_ops` returns `None`; it needs a result object — `{applied: […],
skipped: [(book, field, current, expected)]}` — for the caller to report "497 of 500, named,"
per the atomicity contract's explicit anti-goal of a silent partial write).

This is core-module work (`ops.py`, `common.py`), not plugin-only — it strengthens the CLI path
identically, and per B2's parity requirement ("shadow replay… final metadata state diffed") any
change to `apply_ops` needs `tests/test_write_path.py` extended alongside it before either write
path ships the behavior.

## DEFAULTS resource seam — extract-once-to-cache, not `get_resources()`

Verified against Calibre's plugin API docs (manual.calibre-ebook.com/plugins.html): `Plugin.
load_resources(names)` takes an **explicit list of paths** and returns `{name: bytes}` — there is
no directory-enumeration or glob form. `defaults/` is not one file; it's ~15 files across a
nested `ao3/` subdirectory, including ~7 MB / 150k rows of generated CSVs
(`.planning/codebase/STRUCTURE.md`), and every existing reader (`wrangle.load_maps()`,
`overrides.py`'s delimiter-sniffing CSV reader, `common.read_lines()`) is written against
**paths**, not bytes — `open(path)`, `csv.reader(open(path))`, etc., all through call sites
scattered across `wrangle.py`/`classify.py`/`overrides.py`/`setup.py`.

Two ways to close the gap:
1. `get_resources()`/`load_resources()` with an enumerated manifest, and rewrite every reader to
   accept bytes/`io.StringIO` instead of a path.
2. **Extract-once to a version-keyed cache directory** under `user_dir()` (e.g.
   `user_dir()/vendor/defaults-<version>/`), and point `common.DEFAULTS` at that extracted path
   instead of `HERE/defaults` when running inside a zip.

**Recommendation: (2).** Every existing reader keeps working completely unchanged — the only
change is what `common.DEFAULTS` (and any `HERE`-relative lookup) resolves to. `build_plugin.py`
already enumerates every file under `CORE` when building the zip (`_files(root)` walks
`src/scourgify/defaults/`); the same list can be written into the zip once as a small manifest
(`scourgify/defaults/_manifest.json`, or simply re-derive the file list from `load_resources` at
extract time using `Plugin.zip_path`/the plugin's own zipfile handle) so the extraction step
copies every bundled file without a second hand-maintained list to drift from the first. Version
keying (`defaults-<version>/`) makes a plugin upgrade self-healing — a stale cache from an old
build is never silently reused — and the extraction is a cheap one-time job (bytes only, no
computation), dispatched like any other read job rather than run on the GUI thread. This also
directly fixes the classify-vocab under-quoting bug the phase-5 amendment found
(`classify.est_cost` silently prices from an empty `load_vocab()` inside the zip) — that bug and
the wrangle-empty-maps bug are the same root cause and the same fix.

## Component 6: `user_dir()` — Windows + multi-library namespacing

Verified against Calibre's own `constants.py` (github.com/kovidgoyal/calibre): Calibre's own
`config_dir` resolves Windows via `winutil.special_folder_path(winutil.CSIDL_APPDATA)` (falling
back to the home directory), landing at `%APPDATA%\calibre`. `common.user_dir()` should follow
the same *convention* — `%APPDATA%\scourgify` — but does **not** need to import
`calibre.utils.config` to get there: plain `os.environ.get("APPDATA")` resolves identically for
both the CLI (plain CPython on Windows) and the plugin (Calibre's bundled Python), and keeps
`common.py` free of a Calibre import at module level (already a hard constraint — `common.py`
must import clean under `calibre-debug`'s empty site-packages *and* under plain CPython with no
Calibre installed at all, which a `from calibre.utils.config import config_dir` would break for
the CLI-only, no-Calibre-installed case).

```python
def user_dir() -> str:
    if env := os.environ.get("SCOURGIFY_HOME"):
        return env
    if os.name == "nt":
        return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "scourgify")
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                        "scourgify")
```

**Multi-library namespacing** (NLSpec Constraints, folded into phase 6 by the owner's 2026-08-11
decision) is a related but separate change to the *same* function family: `data_dir()`,
`backups_dir()`, `rejects_path()`, `editlog.log_path()`, `artifacts.py`'s CSV paths, and
`load_config()`'s default path all currently resolve one flat tree under `user_dir()` — which is
provably wrong today (phase 4's own smoke test: book id 1 in a 6-book throwaway library reports
"applied from" an archive belonging to the *real* library, because ids collide across libraries
and there's no library-scoping at all). The fix is a `user_dir()/libraries/<uuid>/` level inserted
below the existing root, with keys/UI preferences staying at the root (global, per B5). This
gates **every** feature that reads `data/` — the dashboard header (component 2), review (3), and
even today's already-shipped Inspect verb — so it should land as foundational plumbing (see Build
Order) rather than bundled into any single feature's PR.

## Component 2: the dashboard — QDockWidget, not the built-in panel system

Verified against Calibre's current source (`gui2/central.py`): Calibre 9's own left/right/bottom
panels (Tag Browser, Book Details, Cover Browser, Quick View) are managed by a `CentralContainer`
with a **fixed, hard-coded set of panel names** (`Visibility` is a dataclass with five named
boolean fields; `set_widget(which, w)` assumes `which` is an existing attribute). This system is
closed to third-party plugins — there is no registry a plugin can add a sixth named panel to.
Calibre's own "Quick View" (`gui2/dialogs/quickview.py`) is *not* a usable precedent for a
plugin dock either: it hooks into that same closed `CentralContainer` slot system
(`gui.layout_container.set_widget('quick_view', self)`), which only exists because Quick View
ships with Calibre itself.

The correct mechanism for a third-party plugin is a plain **`QDockWidget`** added via
`self.gui.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)` — Calibre's main window
(`gui2/main.py`'s `Main`) is a `QMainWindow`, which natively supports arbitrary docks regardless
of `CentralContainer`'s closed panel set; this is the established pattern other third-party
Calibre plugins use for non-modal panels. Construction should be **lazy** (built on first "Open
dashboard…" click, not in `genesis()`/`initialization_complete()` — consistent with "nothing runs
at startup"), and the action should hold a reference so a second click toggles visibility
(`dock.toggleViewAction()` / `dock.setVisible(not dock.isVisible())`) rather than creating a
second dock.

**Per-library rebinding** (B6's edge case: "Library switched → the dashboard re-binds"): Calibre's
documented `InterfaceAction` API includes a `library_changed(db)` hook alongside `genesis()`,
`location_selected()`, `shutting_down()`, `initialization_complete()` — this is the hook the dock
should use to re-run its header snapshot job and clear any per-library UI state, rather than
inventing a polling mechanism. (Confirm the exact call signature against the installed Calibre
version during implementation — the manual describes it but the phase-4/5 work already
established the pattern of verifying disassembled behavior under the actual bundled interpreter
before trusting documentation on timing-sensitive hooks.)

**Live progress inside the dock** (mirroring `report.Dashboard`'s shape per B3.4): confirmed
against `gui2/threaded_jobs.py` — a job function's `notifications.put((frac, msg))` calls feed
`ThreadedJobServer`, which calls `job.consume_notifications()` and republishes changed jobs; this
is the same mechanism `job_inspect` already uses for its own progress line. A richer dock display
(tagged/failed/rate/sparkline, matching `report.Dashboard`) needs the job function to
periodically `notifications.put()` a small structured summary (not just a percent+string) and the
dock to poll the tracked job object on a `QTimer` — the same shape `plugin/selftest.py`'s own
16 ms GUI-thread heartbeat already proves is safe. Calibre's stock Jobs list cannot render this
richer shape (it renders percent + one string), so the dock needs its own polling widget rather
than relying on Calibre's built-in job display.

## Component 3: review-in-library-view — the highest-uncertainty piece

NLSpec calls this out itself ("prototype-first — this is the phase with no precedent anywhere").
Architecturally, three things are already settled by other components and should not be
re-litigated inside `review.py`:

- **Marks**: `db.set_marked_ids` is a **legacy DB** call (`new_api` has no marked-books concept —
  already noted in the codebase's B8.2). This means `review.py`'s job functions need the legacy
  DB handle passed in from the GUI thread the same way `new_api` is today — a plain attribute
  read (`gui.current_db` exposes both), not a reopen.
- **Per-item write**: each accepted item is its own `write_ops(api, [op])` call under the *same*
  review-run id (editlog's `run` field), which requires `write_ops`/`editlog.start` to support
  being called N times with a shared `run` id rather than always minting a fresh one — a small
  extension to `editlog.start`'s signature (accept an existing `run` id, or add an
  `editlog.append_op(run, op, before)` sibling for the "already inside a run" case).
- **Atomicity on close**: "decided items are applied+logged, undecided remain pending — recomputable
  at any time from the artifacts alone" (B8's edge case) means `review.py` must not hold any
  authoritative state in the panel itself — pending is always `proposal rows − decided rows`,
  computed fresh from `artifacts.py` + the edit log, exactly like every other stage's
  file-signal-driven state in `wizard.snapshot()`.

Given the "no precedent" framing, this component should be **prototyped standalone early**
(marks snapshot/restore + a compact per-book diff panel against a throwaway library, no real
write) to de-risk the Qt mechanics, independent of whether the write-path groundwork (write-run
lock, conflict checks) has landed yet — the prototype doesn't need real writes to prove the
marks/navigation/restore mechanics work.

## Component 4: in-plugin column creation + restart

`ops.apply_ops`'s `create_column` branch already documents why it's out of the in-process
contract: it needs the **legacy DB object**, and the reopen it requires "would desync a live
GUI's models" (`ops.py:56-60`). The plugin's answer is not to extend the in-process contract to
cover it — it's to run the *existing* `setup.py` column-creation path (today's CLI/wizard
`stage_setup()`) as a **one-time, pre-restart** operation: a job dispatches the legacy-DB
`create_custom_column` calls (safe here specifically *because* nothing else has read the GUI's
in-memory models yet in this session that would desync — this is the one write in the whole
plugin surface that gets to use the legacy DB directly, and it's safe only because the mandatory
restart resets everything downstream of it), then the result dialog's only action is "Restart
Calibre." No new column-creation logic is needed in `ops.py` — this is Qt wiring around
`setup.py`'s existing health-check + create flow, gated by the dashboard opening in "setup mode"
(B6.5) when required columns/config are missing.

## Data Flow

### Read path (existing, unchanged by this milestone)

```
click → build_menu() [GUI, <1ms, id list only]
      → action._run(desc, job_inspect, (lib, uuid, ids, current_uuid))
      → ThreadedJob worker: common.set_library(); identity check; common.ro_connect()
      → plain dict result → Dispatcher(_done) → info_dialog on GUI thread
```

### Write path (new — what components 2–4 add on top of)

```
[GUI] Qt picker resolves scope/engine/action from PLAN-job data (no core call)
      → action._run(desc, job_write, (lib, uuid, resolved_choice, api, current_uuid))
[worker] common.set_library(); identity check
      → tool.plan(...)  [same function the CLI/wizard call — unchanged]
      → common.write_ops(api, ops, tool=..., scope=..., engine=..., model=...)
           → write_lock(api.library_id) [non-blocking acquire; GuardrailError if held]
           → check_wipe (via populated_via_api)
           → backup_db() [Online Backup API]
           → editlog.start(...) [before-read via values_via_api]
           → ops.apply_ops(api, ops, expected=before)  [NEW: per-op conflict re-check]
           → editlog.finish(rec, outcome)
      → result dict: {applied: [...], skipped: [(book, field, reason)], ...}
[GUI, Dispatcher-wrapped] result dialog / dashboard refresh / review panel opens
```

### Progress / abort / log, concretely

- **Progress**: worker calls `notifications.put((frac, msg_or_summary))` at per-book granularity
  (already the shape `job_inspect` uses); `ThreadedJobServer` republishes to anything watching
  `job.consume_notifications()`; the dashboard's `QTimer` polls the tracked job for its dock
  display, Calibre's own Jobs list gets the plain percent+string for free.
- **Abort**: worker checks `abort.is_set()` between books (already the shape `job_inspect` uses);
  a cancelled write-run's footer is `"cancelled"` (editlog), and every op logged before the abort
  is real and undoable per the atomicity contract — no new mechanism needed, `editlog.finish`
  already takes an arbitrary outcome string.
- **Log**: unchanged — `write_ops` already calls `editlog.start`/`finish` around `apply_ops`;
  the only extension needed is the shared-`run`-id case for review's per-item applies (component
  3) and carrying `applied`/`skipped` book ids back in the job result so a result dialog or the
  review panel can name them (the atomicity contract's "never silently 497 of 500" — currently
  unenforceable because `apply_ops` returns nothing to report a skip with).

## Build Order

Numbered by dependency, not by the milestone's own component numbering — several of the
milestone's eight items share prerequisites that should land once, together.

### 0 — Foundational (blocks everything else; do first, in parallel where independent)

1. **DEFAULTS resource seam** (extract-once-to-cache) — independent of everything else; blocks
   any accurate cost estimate or wrangle/classify run in-plugin. No dependency on other work.
2. **`user_dir()` Windows branch + multi-library namespacing** — one function family, do
   together; blocks the dashboard header, review, and fixes the already-shipped Inspect verb's
   cross-library bleed. No dependency on other work.
3. **Write-run lock + apply-time conflict check in `ops.apply_ops`** — tightly coupled (both
   touch `write_ops`/`apply_ops`'s call shape), and together are what makes B2 ("one write path
   with guards") actually true for concurrent/conflicting in-plugin writes, not just true for a
   single uncontended write the way it is today. Blocks every WRITES-tagged menu verb.
4. **Pure option functions moved from `wizard.py` to `report.py`** — mechanical extraction, no
   behavior change (pin with the existing `tests/test_wizard_flow.py` before/after). Blocks the
   Qt scope/engine dialogs, since they consume the same functions.

### 1 — The write verbs (component: "Qt backend for report.py/ui.py", made concrete)

5. **Two-phase job wiring for Classify / Normalize fields / Re-derive status / Edit tags /
   Retry** — the PLAN-job → Qt picker → EXECUTE-job pattern, consuming (3) and (4). This is
   where the currently-greyed `SOON` verbs in `action.py` un-grey, one at a time; classify
   should go first (it exercises the engine picker + cost display, the highest-stakes UI) then
   the deterministic verbs (simpler — no engine choice).
6. **In-plugin first-run setup + restart prompt** (component 4) — depends on (1) (setup needs
   correct default column specs) but not on (5); can build in parallel with (5) once (0) lands.

### 2 — Surfaces built on the write path

7. **Dashboard shell** (component 2) — the QDockWidget, header (depends on (2)'s namespacing),
   `library_changed()` rebind, and greyed stage buttons can ship as soon as (0) lands, reusing
   today's read-only `job_inspect`-shaped header data; stage buttons un-grey incrementally as
   (5)'s jobs land underneath them. Don't gate the dock's *existence* on every write verb being
   done — B6.1's header numbers are already read-only.
8. **Review-in-library-view prototype** (component 3) — start early as a throwaway marks/panel
   spike (no real write) in parallel with (5)/(6), since it has no precedent and the mechanics
   (marks snapshot/restore, panel layout) are the risk, not the write plumbing. The *real* build
   (per-item applies through the write path) depends on (3) [conflict checks] and on classify's
   write verb existing (5) to have something to review.

### 3 — Polish and release

9. **Key-first onboarding** — small addition to existing `config.py` + whichever engine picker
   (5) produces; do once (5)'s classify verb exists, since the gating hint lives on that same
   picker.
10. **Release flow** (zip attached alongside the wheel) — mechanically independent of all of the
    above; do last so it packages the finished feature set, but there's no technical reason it
    couldn't happen earlier (it's `build_plugin.py`/`publish.yml` wiring, already 90% done).

## Anti-Patterns to Avoid

### Anti-Pattern 1: A second `ThreadedJob` call site for the dashboard or review panel

`tests/test_plugin_source.py`'s `test_only_one_module_dispatches_jobs` enforces exactly one
`ThreadedJob(` call site, in `action.py`. Every new module's job functions must be dispatched
*through* `action._run`, passing their own `done=` callback — not by constructing their own
`ThreadedJob`. This is already the pattern `plugin/config.py`'s `verify()` uses
(`self.action._run(..., done=self._verified)`); `dashboard.py`/`review.py` should follow it
identically, and both need to be added to `MODULES` (and `QT_MODULES` if they build menus/touch
Qt) in the test file as they're created.

### Anti-Pattern 2: Reimplementing `ui.py`'s blocking prompt loop against a worker thread

A tempting shortcut is a Qt-flavored `ui.menu`-equivalent that the worker thread "calls" and
which secretly does `QMetaObject.invokeMethod(..., BlockingQueuedConnection)` to pop a dialog on
the GUI thread and block the worker until it's answered. This technically avoids touching Qt off
the GUI thread, but it reintroduces exactly the frozen-responsiveness failure mode phase 2/3
exist to prevent for the *other* end of the round-trip: the worker (and therefore the job's
`abort`/cancellability) is now blocked on human input with no timeout, and Calibre's own Jobs
list will show it as "running" indefinitely. Use the two-phase PLAN/EXECUTE split instead — no
job should ever be waiting on a human mid-execution.

### Anti-Pattern 3: Reading `data/` paths without the per-library namespace

Any new code that calls `common.data_dir()`/`artifacts.py` functions before the multi-library
namespacing lands (Build Order step 2) will reproduce the already-observed cross-library bleed
(phase 4/5's own throwaway-library smoke test caught this once; it will recur in the dashboard
and review panel if they're built before the fix). Treat this as a blocking prerequisite, not a
"nice to have."

## Sources

- `.planning/codebase/ARCHITECTURE.md`, `.planning/codebase/STRUCTURE.md` (existing system map,
  2026-08-28)
- `docs/superpowers/specs/2026-08-06-calibre-plugin-nlspec.md` (v1.1.0 — the acceptance
  authority for plugin behavior; B1–B8, the atomicity contract)
- `plugin/action.py`, `plugin/config.py`, `plugin/__init__.py`, `plugin/selftest.py`,
  `build_plugin.py`, `tests/test_plugin_source.py` (current enforcement mechanism)
- `src/scourgify/common.py`, `src/scourgify/ops.py`, `src/scourgify/editlog.py`,
  `src/scourgify/report.py`, `src/scourgify/ui.py`, `src/scourgify/wizard.py` (current core/UI
  seams)
- [API documentation for plugins — calibre 9.13 docs](https://manual.calibre-ebook.com/plugins.html)
  — `Plugin.load_resources()` signature (explicit names, no directory enumeration); confirms the
  DEFAULTS extract-once-to-cache recommendation over `get_resources()`.
- [calibre/src/calibre/gui2/central.py (kovidgoyal/calibre, master)](https://github.com/kovidgoyal/calibre/blob/master/src/calibre/gui2/central.py)
  — `CentralContainer`/`Visibility` is a fixed, hard-coded panel set; confirms third-party docks
  must use a plain `QDockWidget`, not the built-in panel system.
- [calibre/src/calibre/gui2/dialogs/quickview.py (kovidgoyal/calibre, master)](https://github.com/kovidgoyal/calibre/blob/master/src/calibre/gui2/dialogs/quickview.py)
  — Calibre's own "Quick View" pane hooks the closed `CentralContainer` slot system, not a
  reusable precedent for a plugin dock.
- [calibre/src/calibre/gui2/threaded_jobs.py (kovidgoyal/calibre, master)](https://github.com/kovidgoyal/calibre/blob/master/src/calibre/gui2/threaded_jobs.py)
  — `notifications.put((frac, msg))` / `job.consume_notifications()` is the confirmed progress
  surface; `job.result`/`job.failed` population.
- [calibre/src/calibre/constants.py (kovidgoyal/calibre, master)](https://github.com/kovidgoyal/calibre/blob/master/src/calibre/constants.py)
  — `config_dir`'s per-platform resolution; confirms `%APPDATA%\calibre` on Windows via
  `winutil.special_folder_path(CSIDL_APPDATA)`, informing the `user_dir()` Windows branch.
- [API documentation for plugins — InterfaceAction hooks](https://manual.calibre-ebook.com/plugins.html)
  — confirms `library_changed()` as a documented `InterfaceAction` override point alongside
  `genesis()`/`location_selected()`/`shutting_down()`/`initialization_complete()`.

---
*Architecture research for: scourgify Calibre plugin, subsequent-milestone Qt integration*
*Researched: 2026-08-28*
