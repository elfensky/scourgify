# Requirements: scourgify Calibre plugin — v1

**Defined:** 2026-08-28
**Core Value:** The full maintenance loop (wrangle → staleness → synopsis → classify → review →
promote → backfill) runs from a Calibre toolbar button, on the live library, with every guard,
backup and undo the CLI has — and never freezes or corrupts Calibre.

## v1 Requirements

Requirements for this milestone. Each maps to roadmap phases. The NLSpec
(`docs/superpowers/specs/2026-08-06-calibre-plugin-nlspec.md`, B1–B8) is the acceptance
authority for plugin behaviour; B-references below point at it.

### Foundation (cross-cutting plumbing — before any write verb)

- [ ] **FOUND-01**: Bundled `defaults/*.csv` are readable when the core runs from inside the
      plugin zip — a `DEFAULTS` resource seam (extract-once to a version-keyed cache under
      `user_dir()`); `load_maps()`/`load_vocab()` never build empty maps silently
- [ ] **FOUND-02**: `common.user_dir()` resolves on Windows (`%APPDATA%\scourgify`, mirroring
      Calibre's own `config_dir` convention) with stdlib only — no new dependency
- [ ] **FOUND-03**: All operational artifacts (proposals, failures, edit log, backups, rejects,
      ledger) are namespaced per library uuid, so two libraries never share state
- [ ] **FOUND-04**: A single process-global, library-uuid-keyed write-run lock — a second write
      job against the same library is refused with a visible reason, never queued silently
- [ ] **FOUND-05**: Apply-time conflict checks in `ops.apply_ops` using `editlog.conflict` — an
      op whose current value no longer matches its expected value is skipped and reported, not
      clobbered
- [ ] **FOUND-06**: The wizard's option/decision functions (scope menus, engine picker rows,
      checklist items) live in `report.py`/tool modules as pure functions, so a two-phase
      PLAN-job → Qt picker → EXECUTE-job split consumes the same source as the wizard
- [ ] **FOUND-07**: In-process guards never shell out — `calibre_open()` is a CLI-only concept;
      any Windows-side process detection uses `tasklist`, never `pgrep`/`ps`

### Write actions on a selection (spec phase 6, B1/B2/B4)

- [ ] **WRITE-01**: User can run **wrangle** on the selected books: preview of per-book field
      edits, then write through `common.write_ops` (snapshot + wipe guard + SAFETY guards apply)
- [ ] **WRITE-02**: User can run **staleness** on the selected books, with the per-book
      `#status` change shown before writing
- [ ] **WRITE-03**: User can run **classify** on a scope (selection / new-changed / never
      classified with a batch size / whole library) with an engine picker that shows, per
      usable engine, the per-run price over the exact resolved scope and its failure modes
      (from `engines.TRAITS`/`PRICING`, never hardcoded) — no confirmation dialog
- [ ] **WRITE-04**: User can run **synopsis** on the selected books with any usable engine
      (apple default where present), refusing to start unless FanFicFare's Comments "New Only"
      switch is on (or the degraded self-healing mode is chosen explicitly)
- [ ] **WRITE-05**: User can run **promote** (adjudicate new-tag candidates) and **backfill**
      from the plugin, with the same verdict-per-candidate review the wizard offers
- [ ] **WRITE-06**: Every write from the plugin appends per-op edit-log lines (run header,
      before/after per `(book, field)`, footer) — identical record shape to the CLI
- [ ] **WRITE-07**: After a write, the user sees a diff-after notice: counts, skipped conflicts,
      failures classed by `failure_class()` with a "retry on <engine>" verb where a refusal
      occurred; the library view refreshes the touched rows
- [ ] **WRITE-08**: The plugin never calls `run_writer`, never spawns a second writer process,
      and dispatches every job through the ONE `action._run` — enforced by source-grep tests

### Setup & onboarding (B5/B6.5)

- [ ] **SETUP-01**: On first use with missing custom columns or config, the plugin enters
      setup mode: verbs are greyed with the reason and the setup entry is offered
- [ ] **SETUP-02**: User can create the missing custom columns from the plugin via Calibre's
      staged `must_restart()` pattern, then is prompted to restart Calibre once — no terminal
- [ ] **SETUP-03**: Where no on-device engine exists (Windows/Linux, or macOS < 26), setup asks
      for at least one API key; classify/synopsis stay greyed with "add a key in settings"
      until a usable engine exists
- [ ] **SETUP-04**: The engine picker and settings list only usable engines (`usable_engines`)
      with key source (env vs stored) and TRAITS-derived role/limits text; keys are masked and
      `redact()`ed from every log, dialog and artifact
- [ ] **SETUP-05**: Config written by the plugin is the same `config.toml` the CLI reads, so
      the two front doors share one configuration

### Dashboard (spec phase 7, B6)

- [ ] **DASH-01**: User can open a non-modal scourgify panel (dock widget if the spike proves it
      against Calibre's main window; a non-modal top-level window otherwise)
- [ ] **DASH-02**: The panel header shows the outstanding-work numbers (books, column health,
      new/changed, never-classified backlog, unsynopsized, pending proposal, undecided
      candidates) from ONE snapshot function shared with the wizard header (#74) — computed in a
      job, never on the GUI thread
- [ ] **DASH-03**: The panel rebinds on library switch (`library_changed`) — every number, list
      and action reads the live `gui.current_db`, never a captured handle
- [ ] **DASH-04**: User can see a running job's progress and abort it from the panel
- [ ] **DASH-05**: User can browse History (#50): runs from the edit log with tool, scope,
      engine, counts, outcome, and per-book edit history

### Review & undo (spec phase 8, B7/B8)

- [ ] **REVIEW-01**: User can review a pending classify proposal in the library view: the
      proposal's books are marked, and a per-item panel shows each book's proposed tags
- [ ] **REVIEW-02**: The user's pre-existing marks are snapshotted before review and restored
      after — including after an abnormal close (snapshot persisted, restored on next open)
- [ ] **REVIEW-03**: User can accept or reject each proposed item; each accept is written
      immediately through the write path and logged, so a half-finished review leaves the
      library consistent
- [ ] **REVIEW-04**: User can keep the proposal for later or discard it (archived to
      `*_discarded_*.csv`), matching the wizard's review stage
- [ ] **REVIEW-05**: User can undo one recorded run from History (#51), conflict-aware: ops
      whose current value diverged are skipped and listed, everything else reverts through the
      write path

### Cross-platform

- [ ] **XPLAT-01**: The plugin loads and every core module imports under Windows Calibre's
      bundled Python 3.14 (no `pgrep`, no `chmod` assumptions, no POSIX-only paths)
- [ ] **XPLAT-02**: The apple engine is cleanly absent off-macOS (not listed, not attempted);
      `afm.swift` ships but is never invoked there
- [ ] **XPLAT-03**: CI has a `windows-latest` lane running the core tests and the
      `calibre-debug` smoke against a downloaded Calibre
- [ ] **XPLAT-04**: A handoff checklist/prompt lives in the repo (`docs/`) so the Windows desktop
      (with Claude installed) can run the manual acceptance pass repeatably
- [ ] **XPLAT-05**: The full loop has been run by hand in real Calibre on macOS and on Windows,
      and the results are recorded (what ran, on which build, what failed)

### Publishing

- [ ] **PUB-01**: The release flow builds `scourgify-plugin-<version>.zip` and attaches it to
      the GitHub release, cut from the same version as the wheel
- [ ] **PUB-02**: The plugin's settings dialog shows the embedded core version, and the zip
      refuses to load a mismatched core (version coherence)
- [ ] **PUB-03**: A ready-to-post MobileRead announcement is drafted in the repo (`[GUI Plugin]`
      title, description, feature bullets naming the differentiators, Calibre version line,
      install steps, screenshots list, changelog block, attribution) with "no confirmation
      dialogs" stated adjacent to, and distinct from, the review panel
- [ ] **PUB-04**: `docs/USERGUIDE.md` (or a plugin guide) covers install, first-run setup, keys,
      and the loop from the GUI, for a non-developer
- [ ] **PUB-05**: The CLI and wizard behave unchanged — every stage stays ONE flow behind two
      front doors, pinned by the existing source-reading tests plus `tests/test_wizard_flow.py`

## v2 Requirements

Deferred to a fast-follow. Tracked but not in the current roadmap.

### Polish

- **POLISH-01**: Default keyboard shortcuts on the stabilized verb set (`action_spec`)
- **POLISH-02**: Per-verb visible/hide preference (Quality Check's "Visible Menus" pattern)
- **POLISH-03**: In-GUI engine bake-off from the engine picker
- **POLISH-04**: Richer History visualizations (per-book timeline)
- **POLISH-05**: Linux as an acceptance target with a hands-on pass

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Confirmation dialogs for spend or writes | Price/consequence on the control + undo is the gate; reflex dialogs are the anti-pattern the design rejects |
| OS keyring / encrypted key storage | Plaintext-with-banner is the settled single-owner precedent (DeDRM, FanFicFare) |
| Local-endpoint engines (Ollama, LM Studio) | Key-first onboarding chosen; a support-burden multiplier for no evidenced demand |
| Literal Qt port of the wizard's prompt choreography | Rejected in design review; the dashboard is "the wizard given a surface" |
| Live in-GUI snapshot restore | The two-writers hazard by another name; routed through close/restore/reopen |
| Undo for `create_column` / `set_pref` / `stamp_now` | Logged with `undo: false`, excluded from replay |
| Posting to MobileRead on the user's behalf | The forum account and the post are the user's; the milestone delivers the text |
| Calibre < 6.0 | PyQt5-only; `minimum_calibre_version = (6, 0, 0)` stays |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| FOUND-01 | Phase 1 | Pending |
| FOUND-02 | Phase 1 | Pending |
| FOUND-03 | Phase 1 | Pending |
| FOUND-04 | Phase 1 | Pending |
| FOUND-05 | Phase 1 | Pending |
| FOUND-06 | Phase 1 | Pending |
| FOUND-07 | Phase 1 | Pending |
| WRITE-01 | Phase 2 | Pending |
| WRITE-02 | Phase 2 | Pending |
| WRITE-03 | Phase 2 | Pending |
| WRITE-04 | Phase 2 | Pending |
| WRITE-05 | Phase 2 | Pending |
| WRITE-06 | Phase 2 | Pending |
| WRITE-07 | Phase 2 | Pending |
| WRITE-08 | Phase 2 | Pending |
| SETUP-01 | Phase 3 | Pending |
| SETUP-02 | Phase 3 | Pending |
| SETUP-03 | Phase 3 | Pending |
| SETUP-04 | Phase 3 | Pending |
| SETUP-05 | Phase 3 | Pending |
| DASH-01 | Phase 4 | Pending |
| DASH-02 | Phase 4 | Pending |
| DASH-03 | Phase 4 | Pending |
| DASH-04 | Phase 4 | Pending |
| DASH-05 | Phase 4 | Pending |
| REVIEW-01 | Phase 5 | Pending |
| REVIEW-02 | Phase 5 | Pending |
| REVIEW-03 | Phase 5 | Pending |
| REVIEW-04 | Phase 5 | Pending |
| REVIEW-05 | Phase 5 | Pending |
| XPLAT-01 | Phase 1 | Pending |
| XPLAT-02 | Phase 1 | Pending |
| XPLAT-03 | Phase 1 | Pending |
| XPLAT-04 | Phase 6 | Pending |
| XPLAT-05 | Phase 6 | Pending |
| PUB-01 | Phase 6 | Pending |
| PUB-02 | Phase 6 | Pending |
| PUB-03 | Phase 6 | Pending |
| PUB-04 | Phase 6 | Pending |
| PUB-05 | Phase 6 | Pending |

**Coverage:**
- v1 requirements: 40 total
- Mapped to phases: 40
- Unmapped: 0 ✓

---
*Requirements defined: 2026-08-28*
*Last updated: 2026-08-28 after roadmap creation (traceability filled)*
