# Roadmap: scourgify Calibre plugin — v1 (the terminal goes away)

## Overview

This milestone turns the shipped read-only plugin skeleton (spec phases 1–5, #54–#58) into the
fully working Calibre plugin: the whole maintenance loop (wrangle → staleness → synopsis →
classify → review → promote → backfill) from a toolbar button, on the live library, with every
guard, backup and undo the CLI has — then proves it by hand on macOS and Windows and publishes
it for other FanFicFare users. The order is plumbing-first. The four cross-cutting
prerequisites every research pass converged on (defaults readable from the zip, per-library
namespacing with a Windows `user_dir()`, a write-run lock with apply-time conflict checks, the
wizard's option functions made pure) land together with the Windows CI lane before any write
verb exists, because each one reproduces an already-observed bug if skipped. Then the write
verbs on a selection (spec phase 6), in-plugin setup for a non-developer, the dashboard (spec
phase 7), review-in-library-view plus undo (spec phase 8), and finally the recorded
two-platform pass and the release.

**Numbering note:** phase numbers here are this milestone's (1–6). The NLSpec
(`docs/superpowers/specs/2026-08-06-calibre-plugin-nlspec.md`) and tracking issue #62 number
the plugin's phases 1–8; spec phases 6 / 7 / 8 (#59 / #60 / #61) map to roadmap Phases
2 / 4 / 5. The NLSpec is the acceptance authority for plugin behaviour (B1–B8, the atomicity
contract).

**Standing rules — every phase's success criteria inherit these (a violation is a bug, not a
trade-off):** nothing runs on the GUI thread; every core call goes through the ONE
`ThreadedJob` call site (`action._run`) with Dispatcher-wrapped callbacks; `GuardrailError`,
never `SystemExit`, anywhere a job can reach; the plugin never calls `run_writer` or spawns a
second writer process; the core imports clean under Calibre's bundled Python with empty
site-packages; `env`/`set_library` injection, `os.environ` never mutated; the CLI and wizard
stay ONE flow behind two front doors and behave unchanged. All of it is enforced by the
existing source-reading tests (`tests/test_plugin_source.py`, `tests/test_plugin_safety.py`,
`tests/test_cli.py`), which every phase keeps green and extends.

## Phases

**Phase Numbering:**

- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Foundation — a hostable core** - Defaults readable from the zip, per-library state, Windows `user_dir()`, write-run lock + conflict checks, pure option functions, Windows CI lane (#71)
- [ ] **Phase 2: Write verbs on a selection** - Every wizard stage as a menu verb: PLAN job → Qt picker → EXECUTE job through `write_ops`, price on the control, per-op edit log, diff-after (spec phase 6, #59, #72, #73)
- [ ] **Phase 3: In-plugin setup and onboarding** - Setup mode, columns created from the plugin with one restart, key-first onboarding off-mac, one `config.toml` for both front doors (B5/B6.5)
- [ ] **Phase 4: The dashboard** - Non-modal panel: outstanding-work header from the one shared snapshot, library-switch rebind, job progress + abort, History (spec phase 7, #60, #74, #50)
- [ ] **Phase 5: Review in the library view and undo** - Proposals reviewed as marks on the real books with per-item accept/reject, marks snapshot/restore, conflict-aware undo of a run from History (spec phase 8, #61, #51)
- [ ] **Phase 6: Verified and published** - Recorded manual pass on macOS + Windows via the repo handoff prompt, zip on the GitHub release, user guide, MobileRead announcement, CLI/wizard unchanged

## Phase Details

### Phase 1: Foundation — a hostable core

**Goal**: The unchanged core runs correctly when hosted by the plugin — from inside the zip, on any of several libraries, on Windows as well as macOS, under concurrent jobs — with each property proven by a test that triggers the failure mode, not assumed. Nothing here demos; every later phase depends on at least one piece of it, and each piece reproduces a bug already observed once in phases 4–5 if skipped. Closes the pre-write protocol half of #71 (one write funnel).
**Depends on**: Nothing (first phase; builds on the shipped plugin phases 1–5, #54–#58)
**Requirements**: FOUND-01, FOUND-02, FOUND-03, FOUND-04, FOUND-05, FOUND-06, FOUND-07, XPLAT-01, XPLAT-02, XPLAT-03
**Success Criteria** (what must be TRUE):

  1. From inside the installed plugin zip, `load_maps()` and `load_vocab()` return the bundled defaults (a classify cost estimate computed in the plugin equals the CLI's for the same scope), and a missing-defaults condition raises `GuardrailError` — never a silently empty map.
  2. Two throwaway libraries opened in sequence in one Calibre session never see each other's proposals, failures, edit log, backups, rejects or ledger (state keyed by library uuid), and `user_dir()` resolves to `%APPDATA%\scourgify` on Windows with stdlib only.
  3. A second write job against the same library while one is running is refused with a visible reason naming the running job, and an op whose current value no longer matches its expected before-value is skipped and reported by book — never overwritten — through `ops.apply_ops` using `editlog.conflict`.
  4. The wizard's scope-menu, engine-picker and checklist option computations live in `report.py`/tool modules as pure functions that the wizard now consumes, with `tests/test_wizard_flow.py` and `tests/test_cli.py` passing unchanged.
  5. CI has a green `windows-latest` lane running the core tests and the `calibre-debug` smoke against a downloaded Calibre; off-macOS the apple engine is absent from `usable_engines` and never attempted; no in-process guard shells out (`calibre_open()` stays CLI-only; Windows-side detection uses `tasklist`, never `pgrep`/`ps`).

**Plans**: 6 plans

Plans:
**Wave 1**

- [ ] 01-01-PLAN.md — Per-library state: uuid-keyed `data_dir()`, Windows `user_dir()`, all-or-nothing guarded legacy migration behind a one-way decision gate (wave 1, tracer)
- [ ] 01-02-PLAN.md — Windows + Linux CI lanes: core tests on `windows-latest` and the `calibre-debug` smoke (wave 1)

**Wave 2** *(blocked on Wave 1 completion)*

- [ ] 01-03-PLAN.md — Defaults readable from inside the zip, and apple absent off its platform (wave 2)

**Wave 3** *(blocked on Wave 2 completion)*

- [ ] 01-04-PLAN.md — One write funnel, the library-uuid write-run lock, and non-shelling guards (wave 3)
- [ ] 01-05-PLAN.md — Pure option functions in the tool modules and a `decide=` seam on every checklist (wave 3)

**Wave 4** *(blocked on Wave 3 completion)*

- [ ] 01-06-PLAN.md — Apply-time conflict checks: `expected` on the op, skip-and-report by book (wave 4)

### Phase 2: Write verbs on a selection

**Goal**: Every wizard stage is a verb on the selected books from the toolbar menu — preview computed in a PLAN job, decision taken in a Qt picker that makes no core call, write done in an EXECUTE job through `common.write_ops` — with the price on the control instead of a confirmation dialog, every write logged per op, and a diff-after that names what was skipped. Spec phase 6 (#59); lands the plugin job runner's per-job ceremony (#72) and the one ask envelope that carries failure classes to the ledger (#73). Build classify first (the engine picker and cost display are the highest-stakes UI), then the deterministic verbs.
**Depends on**: Phase 1
**Requirements**: WRITE-01, WRITE-02, WRITE-03, WRITE-04, WRITE-05, WRITE-06, WRITE-07, WRITE-08
**Success Criteria** (what must be TRUE):

  1. With books selected, the user can run wrangle and staleness from the menu: a per-book preview of field edits (wrangle) or `#status` changes (staleness) is shown first, and the write lands through `common.write_ops` with the snapshot, wipe guard and SAFETY guards applied — a run that would strip a book's last fandom or character is refused with the reason.
  2. The user can run classify on a chosen scope (selection / new-changed / never-classified with a batch size / whole library); the engine picker lists each usable engine with its price over the exact resolved scope and its failure modes (from `engines.TRAITS`/`PRICING`, showing the measurement date), and the run starts on click — no confirmation dialog.
  3. The user can run synopsis on the selection with any usable engine (apple default where present) and it refuses to start while FanFicFare's Comments "New Only" switch is off unless the degraded self-healing mode is chosen explicitly; promote and backfill run from the plugin with the same per-candidate verdict review the wizard offers (an unticked verdict gets no ledger row).
  4. After any write the user sees a diff-after notice — counts, skipped conflicts by book, failures grouped by `failure_class()` with a "retry on <engine>" verb where a refusal occurred — the touched rows refresh in the library view, and the per-library `edits.jsonl` holds a run header, one before/after line per `(book, field)` and a footer in the same record shape as a CLI run (engine + model filled in for classify).
  5. `tests/test_plugin_source.py` still proves the plugin never calls `run_writer`, never spawns a second writer process, and dispatches every job through the ONE `action._run`; a write verb greys out, naming the running job, while another write-run holds the library's lock.

**Plans**: TBD
**UI hint**: yes

### Phase 3: In-plugin setup and onboarding

**Goal**: A non-developer on a fresh library — macOS, Windows or Linux — gets from "plugin installed" to "verbs available" without a terminal: the plugin detects the missing columns or config, creates the columns itself with a single restart, asks for an API key where no on-device engine exists, and writes the same `config.toml` the CLI reads. B5 / B6.5; the one flow allowed to end in a "restart Calibre" prompt. The column-creation half can start in parallel with Phase 2 once Phase 1 lands; the key-gating half needs Phase 2's engine picker.
**Depends on**: Phase 2 (the engine picker the key-gating hint lives on) and Phase 1 (per-library config, apple absent off-mac)
**Requirements**: SETUP-01, SETUP-02, SETUP-03, SETUP-04, SETUP-05
**Success Criteria** (what must be TRUE):

  1. Opening the plugin on a library missing custom columns or config puts it in setup mode: every verb is greyed with the reason and a setup entry is offered; nothing is attempted silently, and the check runs as a job.
  2. The user can create the missing custom columns from the plugin (Calibre's staged `must_restart()` pattern) and is prompted to restart Calibre once; before the restart no other Calibre panel reflects the new column, and after it the verbs un-grey.
  3. On a host with no on-device engine (Windows, Linux, or macOS < 26), setup asks for at least one API key, and classify and synopsis stay greyed with "add a key in settings" until `usable_engines` is non-empty.
  4. The engine picker and the settings dialog list only usable engines, each with its key source (env vs stored) and TRAITS-derived role/limits text; keys are masked in the dialog and `redact()`ed from every log, artifact and notice.
  5. The `config.toml` the plugin writes is read unchanged by `scourgify audit` in a terminal against the same library — one configuration behind two front doors.

**Plans**: TBD
**UI hint**: yes

### Phase 4: The dashboard

**Goal**: A non-modal scourgify panel that is the wizard given a surface: the outstanding-work header from the ONE snapshot function shared with the wizard header (#74), the stages in wizard order driving the Phase 2 verbs (a stage with nothing to do greys out rather than vanishing), a running job's progress with abort, and History (#50) — rebinding on every library switch so state from one library never renders against another. Spec phase 7 (#60). Whether the panel truly docks into Calibre's main window is a spike, not an assumption: acceptance holds with a non-modal top-level window.
**Depends on**: Phase 3 (setup mode is what the panel shows on an unhealthy library), Phase 2 (the verbs and the jobs it shows)
**Requirements**: DASH-01, DASH-02, DASH-03, DASH-04, DASH-05
**Success Criteria** (what must be TRUE):

  1. The user can open a non-modal scourgify panel (a dock widget if the spike proves it against Calibre's main window; a non-modal top-level window otherwise) and keep browsing and editing the library while a job runs in it.
  2. The panel header shows books, column health, new/changed, never-classified backlog, unsynopsized, pending proposal and undecided candidates from ONE snapshot function the wizard header also uses, computed in a job on open and after every completed scourgify job — and never says "up to date" while the backlog is non-zero.
  3. Switching libraries rebinds the panel: every number, list and stage button reads the newly opened `gui.current_db`, and nothing from the previous library renders (verified with two throwaway libraries opened in sequence).
  4. A running job shows its progress in the panel and can be aborted there; a cancelled run's footer is marked `cancelled` and the ops it applied remain logged and undoable.
  5. The user can browse History: runs from the edit log with tool, scope, engine, counts and outcome, plus a per-book edit history; records from another library uuid never mix in, `undo: false` records say so, and an empty log renders as a clean empty state.

**Plans**: TBD
**UI hint**: yes

### Phase 5: Review in the library view and undo

**Goal**: A pending classify proposal is reviewed on the real books — marked in the library view so the user pages through them with Calibre's own navigation, decided item by item in a compact panel, each accept written and logged immediately so a half-finished review leaves the library consistent — and any recorded run can be undone from History, conflict-aware (#51). Spec phase 8 (#61): the piece with no precedent anywhere. Prototype first: a throwaway spike of the marks read-back (`db.data.marked_ids` is the unverified best guess), `set_marked_ids` and the snapshot/restore on a throwaway library — runnable any time after Phase 1, before this phase's plan is finalized.
**Depends on**: Phase 4 (History is where undo lives; the panel's "Review N" is a trigger), Phase 2 (the classify verb that produces proposals), Phase 1 (apply-time conflict checks)
**Requirements**: REVIEW-01, REVIEW-02, REVIEW-03, REVIEW-04, REVIEW-05
**Success Criteria** (what must be TRUE):

  1. With a pending classify proposal, the user can start a review: the proposal's books are marked in the library view and a compact panel shows, per book, current vs proposed tags with per-item accept/reject.
  2. The user's pre-existing marks (and active virtual library / search) are snapshotted before review and restored after — including after a force-quit mid-review, where the persisted snapshot is restored on the next open (tested on the abnormal-close path, not only clean close).
  3. Each accepted item is written immediately through the write path as its own logged op under the review's run id; rejects land in `data/rejects.csv` exactly as CLI rejects do; closing the panel mid-review leaves decided items applied and undecided ones pending (pending = proposal rows minus decided rows, recomputable from the artifacts alone).
  4. The user can keep the proposal for later or discard it (archived to `*_discarded_*.csv`) as the wizard's review stage does, and the dashboard's pending count follows; a proposal row whose book was deleted is skipped and reported.
  5. From History, the user can undo one recorded run: a dry-run names the books whose field changed since (skipped, listed), everything else reverts through the write path as a new logged run that is itself undoable; `stamp_now`/`set_pref`/`create_column` are stated as excluded, not silently dropped.

**Plans**: TBD
**UI hint**: yes

### Phase 6: Verified and published

**Goal**: The finished plugin is proven by hand in real Calibre on macOS and Windows and shipped to other FanFicFare users: the zip attached to the GitHub release from the same version as the wheel, a guide a non-developer can follow, and a ready-to-post MobileRead announcement — with the CLI and wizard shown unchanged by the whole milestone. The Windows pass runs on the user's own desktop through a handoff prompt kept in the repo; a recorded manual pass is this phase's acceptance.
**Depends on**: Phase 5 (packages the complete feature set)
**Requirements**: XPLAT-04, XPLAT-05, PUB-01, PUB-02, PUB-03, PUB-04, PUB-05
**Success Criteria** (what must be TRUE):

  1. A handoff checklist/prompt in `docs/` lets the Windows desktop (with Claude installed) run the manual acceptance pass repeatably, and the full loop has been run by hand in real Calibre on macOS and on Windows with the results recorded in the repo (what ran, on which build, what failed).
  2. Cutting a release attaches `scourgify-plugin-<version>.zip` to the GitHub release beside the wheel, both from one source tree and one version; the settings dialog shows the embedded core version and the plugin refuses to load a mismatched core.
  3. A ready-to-post MobileRead announcement lives in the repo (`[GUI Plugin]` title, description, feature bullets naming the differentiators, Calibre version line, install steps, screenshots list, changelog block, attribution) with "no confirmation dialogs" stated adjacent to, and distinct from, the review panel.
  4. `docs/USERGUIDE.md` (or a plugin guide) takes a non-developer through install, first-run setup, keys and the loop from the GUI.
  5. `scourgify` and the wizard behave exactly as before the milestone: the existing source-reading tests and `tests/test_wizard_flow.py` pass unchanged, and every stage is still ONE flow behind two front doors.

**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6. Phase 3's column-creation work and Phase 5's marks spike may start in parallel once Phase 1 lands.

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Foundation — a hostable core | 0/6 | Not started | - |
| 2. Write verbs on a selection | 0/TBD | Not started | - |
| 3. In-plugin setup and onboarding | 0/TBD | Not started | - |
| 4. The dashboard | 0/TBD | Not started | - |
| 5. Review in the library view and undo | 0/TBD | Not started | - |
| 6. Verified and published | 0/TBD | Not started | - |

---
*Roadmap created: 2026-08-28*
