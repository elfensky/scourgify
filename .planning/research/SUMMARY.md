# Research Summary

**Project:** scourgify Calibre plugin — v1 (the terminal goes away)
**Researched:** 2026-08-28
**Overall Confidence:** MEDIUM-HIGH

## Executive Summary

This milestone wires spec phases 6-8 (write actions on a selection, a non-modal dashboard,
review-in-library-view), in-plugin setup, cross-platform hardening (macOS + Windows acceptance,
Linux best-effort), and publishing onto an existing, already-shipped read-only plugin skeleton
(phases 1-5). All four research passes converge on the same conclusion from four different
angles: there is no new library of dependencies to pick and no fresh product to define -- the
core (wrangle/classify/staleness/synopsis/promote, ops.apply_ops, editlog.py) is done and
correct. What's missing is entirely plumbing that lets the existing core run safely from a Qt
GUI thread/worker-thread split -- a bundled-resource seam so defaults/*.csv reads inside a zip,
per-library namespacing so data/config don't bleed across libraries, a write-run lock plus
apply-time conflict checks so concurrent GUI writes can't race each other, and a Windows branch
for user_dir()/process-detection. Three of those four are the same finding independently
surfaced by STACK (API surface), ARCHITECTURE (build order), and PITFALLS (first-party bugs
already caught once in phases 4-5) -- this is not speculative risk, it is observed behavior
repeating itself in adjacent territory.

The recommended approach is Qt-native, not a port of the wizard: wizard.py's "ask, then act"
interleaving cannot run across the GUI-thread/worker-thread boundary, so every write verb
becomes a two-phase PLAN job -> Qt picker (no core call) -> EXECUTE job split, with the pure
option-computing functions (_scope_options, _engine_options, etc.) moved from wizard.py into
report.py so both front doors -- wizard and Qt -- consume one source. Feature research
independently confirms the resulting shape (cost-on-the-control, undo-backed no-confirmation
writes, marks-based per-item review) is not just acceptable but a genuine, verifiably-uncommon
differentiator versus every comparable Calibre plugin surveyed (Find Duplicates, Quality Check,
Extract ISBN, Ebook-Translator) -- none of which show a price before an irreversible spend or
offer true per-run undo.

The key risks are not "will this work" but "will it silently misbehave once, the way it already
has": a guard that reads back its own write and can never fire (already happened once, B1's
identity check); resource loaders that return empty collections for "not found" instead of
raising (already happened once, load_maps()/load_vocab()); and global user_dir() state bleeding
across libraries (already reproduced twice, phases 4 and 5). All three have concrete, cheap
fixes and are explicitly ordered as foundational, do-first work in every research file that
touches build order -- the risk is sequencing, not feasibility.

## Key Findings

### Stack (see STACK.md)

- No new dependencies. Everything runs on Calibre's own plugin API (InterfaceAction,
  ThreadedJob, Dispatcher, qt.core, JSONConfig) plus Calibre's db layer (new_api/legacy DB) --
  all already partially wired in plugin/action.py/plugin/config.py.
- Phase 6's write verbs need no new Calibre API -- common.write_ops(api, ops) already exists
  and already runs the right executor; the work is wiring (menu verb -> plan().restrict(ids) ->
  write_ops), not discovery.
- Marked-books (set_marked_ids) is the one place phase 8 must reach past new_api into the
  legacy db wrapper -- new_api has no concept of marks at all.
- Column creation must NOT try to go live in-process (legacy-DB reopen desyncs the GUI's
  models) -- mirror Calibre's own CreateCustomColumn.must_restart() staged-restart pattern.
- Windows: no new dependency (platformdirs forbidden -- breaks the empty-site-packages
  constraint); a plain os.name == 'nt' branch reading %APPDATA%, and tasklist in place of
  pgrep/ps.
- Dashboard: build as a non-modal top-level window / QDockWidget for phase 7; true QDockWidget
  main-window integration is architecturally sound (confirmed against Calibre's own QMainWindow
  source) but should be a spike, not a load-bearing assumption.

### Features (see FEATURES.md)

- Table stakes (expected, low differentiation): toolbar/menu placement, single-item-runs-now
  vs. multi-item-job split, a Customize dialog via JSONConfig, restart-to-apply after column
  creation, per-library state namespacing (validated directly by Find Duplicates' own
  per-library exemption list), a results/failure notice.
- Differentiators, worth naming in the MobileRead post: per-run price shown before an
  irreversible paid call (no surveyed plugin, including the closest analog Ebook-Translator,
  does this at all); per-item review of AI-proposed content in the real library view (no
  precedent found anywhere); conflict-aware per-run undo from a durable edit log (no surveyed
  plugin implements true undo of its own writes); a persistent, always-current
  outstanding-work dashboard header (surveyed plugins are one-shot dialogs, not a maintenance
  companion).
- Anti-features to keep rejecting: confirmation dialogs before every write/spend (reads as
  reflex-clicked noise per ecosystem precedent -- the existing price-on-the-control + undo
  design is the better pattern); OS keyring; local-endpoint (Ollama) engines; a literal Qt port
  of the wizard's linear prompt choreography.
- MVP for this milestone = all of B1-B8 as scoped in PROJECT.md; nothing here argues for
  cutting scope, only for sequencing (cost-estimate and undo are flagged P1/highest-value,
  review-panel is flagged highest-complexity/no-precedent).
- Messaging risk: "no confirmation dialogs" must be stated adjacent to, and clearly
  distinguished from, the review-panel differentiator in any public post -- easy to misread as
  "you can't review anything," which is false.

### Architecture (see ARCHITECTURE.md)

- The system is GUI-thread-does-Qt-only / worker-thread-does-core-only, already enforced by
  tests/test_plugin_source.py. New components (dashboard, review, setup dialog) must dispatch
  through the ONE existing ThreadedJob call site in action.py -- never construct a second job.
- The load-bearing architectural finding: wizard.py's ask-then-act interleaving does not port
  to Qt as-is. The fix is a two-phase PLAN-job -> Qt picker -> EXECUTE-job split, with pure
  option functions moved from wizard.py to report.py so wizard and Qt consume one source and
  can never diverge.
- Four pieces of net-new infrastructure, none built yet, all cross-cutting: (1) a DEFAULTS
  resource seam (extract-once-to-cache under user_dir(), not get_resources() -- every existing
  reader stays path-based); (2) user_dir() Windows branch + per-library-uuid namespacing (one
  function family, do together); (3) a write-run lock (process-global, library-uuid-keyed
  threading.Lock, non-blocking acquire) plus apply-time conflict checks threaded through
  ops.apply_ops; (4) the pure-option-function move from wizard.py to report.py.
- Recommended build order (see Implications below) puts all four ahead of any write-verb or
  dashboard work -- they are prerequisites, not part of any single visible feature.
- Review-in-library-view (component 3) is explicitly the highest-uncertainty piece ("no
  precedent anywhere" per the spec itself) and should be prototyped standalone early,
  independent of whether the write-path groundwork has landed, since its risk is Qt/marks
  mechanics, not write plumbing.

### Pitfalls (see PITFALLS.md)

Top 5 (all FIRST-PARTY -- already observed once in this exact codebase during phases 1-5, not
hypothetical):

1. A guard that reads back its own write can never fire. Already happened once (B1's identity
   check compared a captured path to itself). Prevention: every new guard this milestone adds
   (marks snapshot/restore identity, write-lock ownership, dashboard per-library binding) must
   read the live source (gui.current_db), never a value captured earlier, and needs an
   adversarial test that actually triggers the failure mode, not just the happy path.
2. Resources bundled beside the code are silently empty inside a plugin zip. Already happened
   once (load_maps()/load_vocab() returning empty on a missing-inside-zip path, silently
   under-quoting classify cost). The DEFAULTS resource seam is a hard blocker before ANY UI
   surface renders a cost or runs wrangle/classify in-plugin.
3. Multi-library state bleeds across libraries via a global user_dir(). Already reproduced
   twice (phases 4 and 5). Every phase-6+ feature that reads data/ artifacts (dashboard,
   review) is wrong across two libraries until namespacing lands -- treat as a hard
   prerequisite, verify with two throwaway libraries open in sequence, not one.
4. create_column's legacy-DB reopen desyncs a live GUI -- there is no safe in-process fix. The
   one-time restart prompt is the only sanctioned flow; do not attempt to make column creation
   live no matter how clean it looks on a single-panel throwaway-library test.
5. Marks-based review can silently clobber the user's own manual marks. set_marked_ids replaces
   the whole global marked set with no namespace; the snapshot/restore (already required by
   spec B8.1) must be verified on the abnormal-close path (crash/force-quit), not just clean
   close -- this is unrecoverable after the fact if shipped wrong.

Also flagged: Qt5->Qt6 enum access breaks silently at runtime, not import time, in
rarely-exercised branches -- use fully-qualified enum paths in all new Qt code; cost/refusal-
rate numbers (engines.TRAITS) are measured-not-documented facts that go stale silently and must
show a measurement date in any new UI surface, since B4.3 makes the displayed number the only
spend gate.

## Convergent Finding: Foundational Plumbing First

All four research files independently arrive at the same four-item prerequisite list, from
different angles (STACK: what's missing from the API surface; ARCHITECTURE: what the build-
order dependency graph requires; PITFALLS: what has already broken once and will break again in
adjacent code; FEATURES: what's required before per-library correctness claims -- like the
review/dashboard differentiators -- can be trusted). This convergence is strong enough to treat
as settled, not merely suggested:

1. DEFAULTS resource seam (extract-once-to-cache) -- blocks any accurate cost estimate or
   wrangle/classify run in-plugin.
2. user_dir() Windows branch + per-library-uuid namespacing -- one function family; blocks the
   dashboard header, review, and fixes the already-shipped Inspect verb's cross-library bleed.
3. Write-run lock + apply-time conflict check in ops.apply_ops -- blocks every WRITES-tagged
   menu verb; the mechanism that makes "one write path" true under concurrency, not just under
   a single uncontended write.
4. Pure option functions moved from wizard.py to report.py -- mechanical extraction; blocks the
   Qt scope/engine dialogs.

None of these four has a feature-shaped payoff on its own -- they don't demo -- which is exactly
why research flags them as the item most likely to get skipped or deferred under schedule
pressure. They should be phase 0/1 of the roadmap, explicitly named and explicitly gated on
before any write-verb, dashboard, or review work begins.

## Implications for Roadmap

Suggested phase structure (dependency-ordered, following ARCHITECTURE.md's Build Order section,
corroborated by PITFALLS' phase-mapping and FEATURES' prioritization matrix):

1. Foundational plumbing (the convergent finding above: DEFAULTS seam, user_dir() Windows +
   namespacing, write-lock + conflict check, pure-option-function move). Rationale: every other
   phase depends on at least one of these four; none is independently visible/demoable, but
   skipping any one reproduces an already-observed bug in new territory. Avoids Pitfalls 1-3.

2. Write verbs on a selection (spec phase 6 core) -- the two-phase PLAN/EXECUTE job pattern,
   applied first to Classify (exercises the engine picker + cost display, the highest-stakes UI
   and clearest differentiator) then the deterministic verbs (wrangle/staleness -- simpler, no
   engine choice). Delivers: cost-on-the-control classify, Normalize fields, Re-derive status,
   Edit tags, Retry-on-engine. Depends on phase 1. Avoids Pitfall 10 (stale cost numbers --
   surface measurement date).

3. In-plugin first-run setup + restart prompt -- column creation via the legacy DB, one-time
   restart flag, mirroring Calibre's own CreateCustomColumn.must_restart() pattern. Can build in
   parallel with phase 2 once phase 1 lands (depends on phase 1's namespacing for correct
   default column specs, not on phase 2). Avoids Pitfall 4 explicitly -- verify a second
   unrelated Calibre panel does NOT reflect the new column pre-restart.

4. Dashboard shell (spec phase 7) -- QDockWidget or non-modal top-level window, header (depends
   on phase 1's namespacing), library_changed() rebind, greyed stage buttons that un-grey
   incrementally as phase 2's write verbs land underneath them. Don't gate the dock's existence
   on every write verb being done -- the header is read-only and can ship early. Avoids Pitfall
   5 (rebuild menu/dock state on library switch, never assume prior build state is safe).

5. Review-in-library-view (spec phase 8) -- start the marks/panel Qt mechanics as a throwaway
   prototype early (in parallel with phases 2-3, no real write needed) since it's the
   highest-uncertainty, no-precedent piece and its risk is UI mechanics, not write plumbing.
   The real build (per-item applies through the write path) depends on phase 1's conflict
   checks and on classify's write verb (phase 2) existing to have something to review. Avoids
   Pitfall 9 -- test the abnormal-close path explicitly, not just clean close.

6. Key-first onboarding off-mac -- small addition to plugin/config.py + whichever engine picker
   phase 2 produces; do once phase 2's classify verb exists, since the gating hint lives on
   that same picker.

7. Cross-platform hardening + Windows verification -- tasklist-based process detection, chmod-
   failure logging (not silent swallow), Qt6 enum-access grep/lint on all new Qt code from
   phases 3-5, manual Windows pass via the user's Windows desktop + repo handoff prompts.
   Avoids Pitfalls 6, 7, 8.

8. Release flow / publishing -- plugin zip attached to the GitHub release alongside the wheel
   (mechanically ~90% already built), plus the drafted MobileRead post. Do last so it packages
   the finished feature set, though it has no hard technical dependency on the phases above and
   could move earlier if useful.

### Research Flags

Needs deeper research/spike during planning:
- Phase 4 (dashboard): QDockWidget main-window-integration viability -- no worked third-party
  example found; plan around the non-modal-window fallback, treat true docking as a stretch
  spike.
- Phase 5 (review): the exact read-back call for "current marked ids" (db.data.marked_ids is
  the best guess, unverified against a live Calibre 9.11 session) -- a five-minute spike before
  the snapshot/restore helper's shape is finalized.
- Phase 3 (setup): CreateCustomColumn.must_restart()'s exact method-by-method call sequence --
  shape confirmed, literal mechanics need a direct source re-read at implementation time.
- Phase 7 (Windows): exact calibre-debug.exe resolution strategy on a real Windows install --
  conventional path known, not verified against the actual target machine; this is what the
  Windows handoff-prompt process exists to close.

Standard/well-documented patterns, skip additional research:
- Phase 1 plumbing: all four sub-items have concrete, cited implementations in ARCHITECTURE.md
  (code-level sketches for write_lock(), user_dir(), the DEFAULTS extract-once approach).
- Phase 2 write verbs: the PLAN/EXECUTE two-phase pattern is fully specified; write_ops/
  apply_ops already exist and only need the conflict-check extension from phase 1.
- Phase 6 key-first onboarding: engines.py TRAITS/resolve_keys already built; GUI wiring only.
- Phase 8 publishing: build_plugin.py/publish.yml already ~90% built per PROJECT.md itself.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | MEDIUM-HIGH | Calibre's plugin docs are thin/example-driven; every individual API claim is cross-checked against this repo's own proven code or Calibre's own source with line numbers. Weakest points: QDockWidget viability (no worked example) and exact Windows calibre-debug.exe resolution (conventional, not verified on a real box). |
| Features | MEDIUM | Cross-corroborated across 7 real Calibre plugins' source/READMEs/MobileRead threads; the single closest analog for the paid-API/multi-engine comparison (Ebook-Translator) was read directly at the source level, which is a real strength, but it is still only one comparable plugin. |
| Architecture | HIGH for component boundaries, write-path gaps, and build order (verified against this repo's own enforced test suite and Calibre's upstream source); MEDIUM for exact Qt API names around progress polling and the library-change signal (verified via source/docs, not a running Calibre instance). |
| Pitfalls | HIGH for the 6 FIRST-PARTY pitfalls (directly observed bugs in this codebase's own history, dated and issue-numbered); MEDIUM for the 4 ECOSYSTEM pitfalls (cross-checked web/MobileRead sources, single-source corroboration on some). |

### Gaps to Address

- QDockWidget real-world integration behavior (persistence across restart, interaction with
  Calibre's own Layout preferences) -- unverified; the roadmap should not make it load-bearing
  for phase 4's acceptance criteria.
- The exact read-back call for pre-existing marked-book ids (phase 5 prerequisite) -- small
  spike needed before that phase's plan is finalized, not before the milestone starts.
- Windows calibre-debug.exe resolution and general Windows manual-pass results -- deferred by
  design to the user's own Windows desktop via handoff prompts; the roadmap should schedule
  that handoff explicitly rather than assume it happens incidentally.
- No official Calibre documentation exists for ThreadedJob/JobManager internals beyond what
  this repo has already independently measured (phases 2-3) -- treat this repo's own prior
  measurements as the primary source going forward, not a documentation gap to re-research.

## Sources

Aggregated from all four research files -- see individual files for full citation lists with
per-source confidence tags:

- Primary/first-party (HIGH): this repo's own CLAUDE.md, .planning/codebase/*,
  docs/superpowers/specs/2026-08-06-calibre-plugin-nlspec.md v1.1.0, plugin/action.py,
  plugin/config.py, src/scourgify/{common,ops,editlog,report,ui,wizard}.py,
  tests/test_plugin_source.py, tests/test_plugin_safety.py.
- Calibre official docs: manual.calibre-ebook.com/plugins.html, /creating_plugins.html,
  /gui.html, /customize.html (MEDIUM -- official domain, fetched/summarized not raw-quoted in
  all cases).
- Calibre upstream source (github.com/kovidgoyal/calibre, master): customize/__init__.py,
  gui2/threaded_jobs.py, gui2/preferences/create_custom_column.py, gui2/central.py,
  gui2/dialogs/quickview.py, constants.py (HIGH -- line-numbered quotes).
- Ecosystem plugins surveyed: FanFicFare, Count Pages, Find Duplicates, Quality Check, Generate
  Cover, Reading List, Extract ISBN, Action Chains (all kiwidude68/JimmXinu repos + their
  MobileRead threads), Ebook-Translator-Calibre-Plugin (bookfere) -- source read directly for
  setting.py (MEDIUM).
- Community/forum: MobileRead "Plugin devs: Upcoming migration to Qt 6" (t=344064),
  "Introduction to plugins" (t=118680), Plugins subforum index (f=237) (MEDIUM-LOW, tribal
  knowledge cross-checked where possible).

---
*Synthesized from STACK.md, FEATURES.md, ARCHITECTURE.md, PITFALLS.md*
*Researched: 2026-08-28*
