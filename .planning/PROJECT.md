# scourgify Calibre plugin — v1 (the terminal goes away)

## What This Is

scourgify normalizes a FanFicFare-imported Calibre library — consolidating tags, fandoms,
characters, relationships, genres and status, settling descriptions, and content-tagging books
with an LLM — data-driven, audit-first and reversible. Today it is a CLI + rich wizard. This
milestone turns the existing read-only plugin skeleton (roadmap #62, phases 1–5 done) into a
**fully working Calibre plugin**: the whole maintenance loop runs from inside Calibre, on the
live library, with no terminal — and it is published for other FanFicFare users on macOS,
Windows and Linux.

## Core Value

The full maintenance loop (wrangle → staleness → synopsis → classify → review → promote →
backfill) runs from a Calibre toolbar button, on the live library, with every guard, backup
and undo the CLI has — and never freezes or corrupts Calibre.

## Requirements

### Validated

<!-- Shipped and confirmed valuable — inferred from the codebase (see .planning/codebase/). -->

- ✓ Deterministic normalization engine (`wrangle.plan()` → preview/guard/step/write) with SAFETY
  guards (no book loses its last fandom/character; tag mass-deletion floor) — existing
- ✓ `#status` re-derivation from `#updated` age (`staleness.py`) — existing
- ✓ Synopsis pass: three-state adequacy verdict, whole-book generation sized to the apple
  engine's 4,096-token window, `#synopsized` stamp as the entire state — existing (1.18.0)
- ✓ LLM content tagging (`classify.py`) over five engines (`engines.py`: apple/claude/openai/
  gemini/mistral), controlled vocab + `proposed_new` candidates, per-engine cost estimates,
  bake-off, failure taxonomy — existing
- ✓ Promote (advocate→skeptic adjudication of new-tag candidates) + deterministic backfill —
  existing
- ✓ ONE write path: `ops.apply_ops` behind both `run_writer` (CLI, `calibre-debug`) and
  `common.write_ops` (in-process), with the wipe guard, sqlite Online-Backup snapshot, and
  `rollback` — existing (plugin phase 1)
- ✓ Edit log (`editlog.py`): per-op before/after lines, collision-proof run id, shared conflict
  predicate — existing (plugin phase 3)
- ✓ Core imports clean under Calibre's bundled Python 3.14.6 with empty site-packages; no
  `SystemExit` reachable from a job (`GuardrailError` everywhere); CI on 3.10/3.13/3.14 —
  existing (plugin phase 2)
- ✓ Read-only plugin skeleton: selection-driven menu, everything on `ThreadedJob` through the
  ONE `action._run`, Dispatcher-wrapped callbacks, `set_library()` injection seam,
  db-from-worker smoke, source-grep safety tests — existing (plugin phase 4, #57)
- ✓ Plugin settings: keys in `JSONConfig('plugins/scourgify')` chmod 0600 with plaintext
  banner, env wins over stored (`engines.resolve_keys`), `key_source`/`mask` — existing
  (plugin phase 5, #58)
- ✓ `build_plugin.py` → `dist/scourgify-plugin-<version>.zip`, version stamped from
  `pyproject.toml`; core ships at zip root as a plain `scourgify/` package — existing
- ✓ Rich-first wizard + scriptable TUI (`SCOURGIFY_SCRIPT`), `select.py` as the one owner of
  scope, `artifacts.py` / `overrides.py` as the one owners of their file formats — existing

### Active

<!-- Current scope. Building toward these. All hypotheses until shipped. -->

- [ ] **Spec phase 6 — actions on a selection (#59):** every wizard stage is a menu verb on
      the selected books; writes go through `common.write_ops` under a single write-run lock
      with apply-time conflict checks and per-op edit-log lines; the plugin never calls
      `run_writer` (source-grep enforced)
- [ ] **Bundled `defaults/` readable from inside the zip** — a `DEFAULTS` resource seam
      (`get_resources()` or extract-once to a version-keyed cache dir); `load_maps()` must
      never build empty maps silently
- [ ] **In-plugin first-run setup** — missing custom columns are created via Calibre's own GUI
      API, then a one-time restart prompt; no `scourgify setup` in a terminal
- [ ] **Key-first onboarding off-mac** — setup asks for at least one API key where no
      on-device engine exists; classify/synopsis grey out with an "add a key in settings" hint
      until one is present; the engine picker shows only usable engines with per-book price
- [ ] **Synopsis in the plugin, any engine** — the stage runs from the GUI and accepts a cloud
      engine through the normal engine picker (apple stays the default where available)
- [ ] **Spec phase 7 — the dashboard (#60):** non-modal dockable panel, setup mode,
      per-library namespacing, outstanding-work header (#74), History (#50)
- [ ] **Spec phase 8 — review in the library view (#61):** proposals reviewed as marks on the
      real books, snapshot/restore of marks, per-item accept/reject atomicity
- [ ] **Undo from the GUI (#51)** — revert one recorded run, conflict-aware, from History
- [ ] **Cross-platform core:** `common.user_dir()` on Windows (`%APPDATA%`), no `pgrep`
      assumption in-process, `afm.swift`/apple engine cleanly absent off-mac, path handling
      that survives Windows Calibre
- [ ] **Verified on macOS and Windows:** the full loop run by hand in real Calibre on both; a
      Windows CI lane for the core tests and the `calibre-debug` smoke; a repo handoff
      prompt/checklist so the Windows desktop (which has Claude installed) can run the
      manual pass
- [ ] **Published:** the plugin zip attached to a GitHub release by the existing release flow
      (same version as the wheel); a ready-to-post MobileRead plugin-thread announcement
      drafted in the repo (the user posts it)
- [ ] **CLI/wizard unchanged throughout** — every stage keeps ONE flow behind two front doors
      (`tests/test_cli.py`-style source tests keep them from diverging)

### Out of Scope

- **Encrypted key storage / OS keyring** — plaintext-with-banner is the settled choice for a
  single-owner library (spec non-goal; adversarial challenge dismissed)
- **Confirmation dialogs for spend or destructive actions** — the price on the control and
  the scope step are the gate; a reflex dialog is the pattern this design rejects
- **Local-endpoint engines (Ollama, LM Studio)** — key-first onboarding was chosen instead;
  `engines.py` is one row per engine if ever wanted
- **A web UI, or a literal Qt port of the wizard's prompt choreography** — decided against
  in the design review
- **Undo for `create_column` / `set_pref` / `stamp_now`** — logged, excluded from replay
- **Live in-GUI snapshot restore** — routed through close/restore/reopen; a live restore is
  the two-writers hazard by another name
- **Linux hands-on testing** — best-effort (the CLI already runs there); acceptance is macOS
  + Windows
- **Posting to MobileRead on the user's behalf** — the milestone delivers the post text; the
  forum account is the user's
- **Bumping `minimum_calibre_version` below 6.0** — Calibre 5 is PyQt5-only

## Context

- **The acceptance authority is the NLSpec** `docs/superpowers/specs/2026-08-06-calibre-plugin-nlspec.md`
  (v1.1.0, B1–B8, the atomicity contract, constraints). Where this document and the spec
  disagree, the spec wins on plugin behaviour; this document adds what the spec predates
  (synopsis stage, cross-platform, publishing, in-plugin setup).
- **Tracking issue #62** holds the 8-phase roadmap; phases 1–5 (#54–#58) are closed. Open
  groundwork issues: #71 (one write funnel: unify the pre-write protocol of `write_ops` and
  `run_writer`), #72 (plugin job runner owning per-job ceremony), #73 (one ask envelope so
  failure classes reach the ledger), #74 (one header snapshot for wizard + dashboard).
- **Codebase map** in `.planning/codebase/` (STACK, ARCHITECTURE, STRUCTURE, CONVENTIONS,
  TESTING, INTEGRATIONS, CONCERNS) — mapped 2026-08-28.
- **Standing rules (violations are bugs):** nothing runs at plugin startup; every core call —
  reads included — goes through the ONE `ThreadedJob` call site (`action._run`); every
  callback Dispatcher-wrapped; plugin never spawns a second writer process; `GuardrailError`
  never `SystemExit`; `env`/`set_library` injection, `os.environ` never mutated.
- **Known gaps the plugin exposes:** `common.HERE` inside a zip is not `open()`-able (defaults
  seam); `calibre_open()` is a CLI concept (in-process there is no second writer);
  `create_column` needs the legacy-DB reopen the in-process contract refuses (hence the
  restart prompt); `user_dir()` is mac/Linux-only.
- **Engine facts:** apple is free/on-device/single-threaded (~40 s a book for synopsis,
  macOS 26+ only); gemini refuses ~14% of mature content and bills hidden thinking as output;
  a full cloud pass is tens of euros — the engine picker must state price and failure modes
  (from `engines.TRAITS`/`PRICING`, never hardcoded).
- **The user's Windows desktop** has Calibre-capable hardware and Claude installed; manual
  Windows passes happen there via handoff prompts kept in the repo (or a tool installed there
  later).

## Constraints

- **Compatibility**: core runs unmodified under Calibre's bundled Python 3.14.6 with empty
  site-packages; no vendored deps; only a Qt backend for the `report.py`/`ui.py` seams — the
  plugin is impossible otherwise
- **Safety of the live library**: one write path, single write-run lock, snapshot-before-write,
  wipe guard on every writer, per-op logging, conflict-aware undo, fail-closed on any
  guard/backup failure — enforced by source-grep tests, not intention
- **GUI responsiveness**: zero library work on the GUI thread; GUI-thread portion of any
  dispatch < 100 ms (smoke-checked by `plugin/selftest.py`)
- **Platforms**: macOS + Windows are acceptance targets; Linux best-effort; Calibre ≥ 6.0
- **Version coherence**: zip and wheel cut from one source tree and one version; the zip
  joins the existing release flow (`publish.yml`, `main` release PRs, annotated tags)
- **Tooling**: uv only (never pip/pipx); tests are plain-assert files under `tests/`, in CI
  by existing; `develop` history stays linear
- **Cost**: no casual full cloud runs while testing — `--scope-only`, `--books`, small
  batches, apple where available

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| All three remaining spec phases (6, 7, 8) in this milestone | "Fully featured" — the review-in-library-view is what makes the GUI better than the CSV, not just equal | — Pending |
| In-plugin setup with a one-time restart prompt | `create_column` needs a legacy-DB reopen the in-process contract refuses; a restart is cheaper for a non-developer than a terminal | — Pending |
| macOS + Linux + Windows, with mac + Windows as acceptance | Calibre's real audience is Windows-heavy; a plugin "for others" that is mac-only isn't for others | — Pending |
| Key-first onboarding off-mac (no local-endpoint engine) | Keeps `engines.py` one row per engine; reopening the Ollama non-goal would add a whole engine to test | — Pending |
| Synopsis runs from the plugin on any engine | The stage postdates the spec; a per-book cloud opt-in is just the normal engine picker in a GUI | — Pending |
| Publish = GitHub release zip + a drafted MobileRead post | MobileRead is the index Calibre's Get Plugins reads; the forum account and the posting stay the user's | — Pending |
| Windows verification via the user's desktop + handoff prompts in the repo | Real Calibre on real Windows is the only proof; the prompts make the pass repeatable | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-08-28 after initialization*
