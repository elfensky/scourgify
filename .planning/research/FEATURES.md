# Feature Research

**Domain:** Calibre GUI plugin — long-running library-maintenance tool (metadata normalization, LLM content-tagging, paid-API cost exposure) for a niche but large (FanFicFare/fanfiction) user base
**Researched:** 2026-08-28
**Confidence:** MEDIUM (mostly cross-corroborated web/GitHub/MobileRead sources; no official Calibre API docs coverage of ThreadedJob/JobManager specifics — that gap is already closed by this repo's own phase-2/3/4 measurements, which this research does not re-litigate per the milestone brief)

This file does **not** re-research scourgify's own core (already spec'd in
`docs/superpowers/specs/2026-08-06-calibre-plugin-nlspec.md`, B1–B8). It answers a narrower
question: given how mature, widely-installed Calibre plugins actually behave, which of scourgify's
planned/possible plugin behaviors are **table stakes** (expected, penalized if missing),
**differentiators** (genuinely uncommon — worth calling out in the MobileRead post), or
**anti-features** (look tempting, but the ecosystem's own precedent argues against them). Plugins
examined: FanFicFare, Count Pages, Find Duplicates, Quality Check, Generate Cover, Reading List,
Extract ISBN, Action Chains (all via GitHub source/READMEs + their MobileRead threads), plus
Ebook-Translator-Calibre-Plugin as the closest existing analog to a multi-engine paid-API plugin.

## Feature Landscape

### Table Stakes (Users Expect These)

Features every mature Calibre plugin in this survey has, in some form. Missing these reads as
"not a real Calibre plugin" to a MobileRead audience that has used FanFicFare/Quality
Check/Count Pages for over a decade.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Toolbar button + right-click/menu entry, user-placeable | Every surveyed plugin (FanFicFare, Count Pages, Extract ISBN, Quality Check, Action Chains) is added to the toolbar by the user via Preferences→Toolbar or at install time; nobody expects it pinned automatically | LOW | scourgify's B1 (selection→menu) already matches this; the installer's toolbar-placement prompt is Calibre's own, not plugin work |
| Single item = runs now, N items = background job | Extract ISBN (kiwidude): 1 selected book runs immediately, >1 queues as a background job with a configurable batch size. Same split pattern across kiwidude's other batch plugins | LOW–MEDIUM | scourgify already goes further (everything, even 1 book, is a `ThreadedJob` — B3) which is *stricter* than the norm, and correctly so given classify's cost/latency |
| "Customize plugin" dialog via Preferences→Plugins→User interface action | Universal: Count Pages, Extract ISBN, Quality Check, FanFicFare, Ebook-Translator all wire `is_customizable`/`config_widget`/`save_settings` per Calibre's own documented three-method contract, storing state in `JSONConfig('plugins/<unique_name>')` | LOW | scourgify's B5 already follows this exactly (`plugin/config.py`) |
| Keyboard shortcut registration via `action_spec` | Calibre's own plugin API bakes a default-shortcut slot into every `InterfaceAction`; Quality Check exposes a "Visible Menus" toggle plus per-check shortcuts | LOW | Already available via `action_spec`; low-risk to add once verbs stabilize (phase 6+) |
| Per-check / per-verb "greyed out, not hidden, with a reason" menu items | Quality Check's "Visible Menus" config and Extract ISBN's disabled-when-no-selection pattern are the closest analogs; neither states a *reason* inline the way scourgify's fixed-slot rule (B1) does | LOW | scourgify's fixed-slot-with-reason is stricter/better than the norm — still counts as table stakes because *some* graying convention is universal, scourgify's flavor is the differentiator (see below) |
| "Get New Plugins" catalog listing + a MobileRead announcement thread as the canonical support/changelog channel | Every plugin surveyed is distributed exactly this way; users' first move when something breaks is the MobileRead thread, not GitHub issues | LOW (process, not code) | Already the plan (PROJECT.md "Published") |
| Restart-to-load after install/major config change | Universal Calibre constraint, not a plugin choice — "Ctrl+R is a convenient shortcut for this" per the canonical "Introduction to plugins" thread | N/A (host constraint) | scourgify's B6.5 (column creation → one-time restart prompt) matches the ecosystem's own accepted exception to "don't make the user do anything extra" |
| Batch-size / background-job threshold as a user setting | Extract ISBN exposes exactly this in its Customize dialog | LOW | Classify's `--batch`/`--workers` equivalents already exist in the core; exposing them in the GUI scope dialog is table stakes, not novel |
| Per-library persisted state for anything that isn't a global preference | Find Duplicates keeps a **per-library exemption list** so marking a false-positive duplicate doesn't leak into a different library | LOW–MEDIUM | Directly validates scourgify's own Constraints section: operational state (proposals, failure log, edit log, stamps) must be namespaced by library uuid (already planned, phase 6) |
| A results/failure notice the user can act on, not a silent log | Extract ISBN's "Display failure dialog" toggle; FanFicFare's per-book failure reporting | LOW | Already scourgify's plan (B1's "Retry on \<engine\>", B3.6 failure taxonomy) |

### Differentiators (Competitive Advantage)

Genuinely uncommon in the surveyed ecosystem — worth stating explicitly in the MobileRead post,
because a MobileRead reader who has used Quality Check or Ebook-Translator will not assume these
exist.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Per-run price shown on the control before an irreversible paid call** (B4.3) | Reviewed Ebook-Translator-Calibre-Plugin's `setting.py` directly (the closest existing analog — a multi-engine paid-API Calibre plugin: ChatGPT/OpenAI, Gemini, Azure ChatGPT, DeepL): **no cost estimator, no usage/spend tracking, no free-vs-paid visual distinction between engines exists anywhere in its settings UI.** No other surveyed plugin shows a price before spending money. scourgify's `classify.est_cost()`-on-the-engine-picker is a real gap it fills, not a reinvention | Already built (core) | MEDIUM confidence — verified by reading source directly, but only one comparable plugin was found to compare against |
| **Per-item review of AI-proposed content, not just duplicate/merge candidates** (B8) | Find Duplicates is the only surveyed plugin with a marks-based review flow at all, and it reviews *structural* duplicates (author spelling variants, binary-identical files) with three outcomes (delete/merge/exempt) — not free-text AI output with per-tag accept/reject. Reviewing LLM-proposed tags/descriptions one book at a time, in the real library view, with per-item write-through, has no found precedent in this survey | HIGH (explicitly flagged in the NLSpec as "no precedent anywhere") | This is the plugin's single most novel UX surface — worth a screenshot in the MobileRead post |
| **Conflict-aware, per-run undo backed by a durable edit log** (B7) | None of the surveyed plugins (Count Pages, Extract ISBN, Quality Check, Find Duplicates, FanFicFare) implement true undo of their own writes — they rely on Calibre's own metadata-editing history (which is coarse and not conflict-aware) or nothing at all. Find Duplicates' "exemption" list is a mitigation, not an undo | Already built (editlog.py, phase 3) | Frame this explicitly in the MobileRead post: "unlike most plugins, every write this plugin makes can be undone individually, even if you've edited the book since" |
| **Non-modal dashboard with a derived, always-current outstanding-work header** (B6) | Surveyed plugins are single-purpose dialogs (Extract ISBN's scan dialog, Quality Check's check-results grid) — none maintain a persistent "here's what's left to do" surface across sessions the way scourgify's dashboard/backlog counters do | MEDIUM–HIGH (already spec'd, phase 7) | Positions the plugin as a maintenance *companion* rather than a one-shot tool — closer to a status bar than a wizard |
| **Key-first, price-and-failure-mode-aware engine picker** (B4.2, B5) | Ebook-Translator lets you add API keys per engine but treats them uniformly; it does not surface an engine's *failure mode* (e.g., "refusal-prone", "cannot judge") as a first-class UI fact the way scourgify's `TRAITS`-derived picker does | Already built (engines.py TRAITS) | Directly answers "why does this engine cost more/refuse more" — a real point of confusion in Ebook-Translator's own community threads about engine choice |
| **Guaranteed no-op safety net on the live library** (single write-run lock, wipe guard, sqlite Online Backup snapshot before every write, fail-closed) | Quality Check and Find Duplicates write directly via Calibre's own metadata API with no independent backup/guard layer; scourgify's snapshot-before-write is a genuinely stronger safety story for a plugin that does *bulk, opinionated* rewrites (not just single-field edits) | Already built | Good MobileRead-post selling point for a fandom library owner who has been burned by a bad bulk edit before |

### Anti-Features (Commonly Requested, Often Problematic)

These map onto PROJECT.md's existing "Out of Scope" list; this research corroborates each with
ecosystem precedent rather than re-deciding them.

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|------------------|-------------|
| Confirmation dialog before every write/spend | Feels "safer"; users coming from other software expect an "Are you sure?" | The surveyed AI-adjacent plugin (Ebook-Translator) and scourgify's own CLI history both show this becomes reflex-clicked noise, not a real gate — a MobileRead reader would call this "the usual pointless popup." scourgify's existing choice (price/consequence on the control itself, B1.3/B4.5) is the ecosystem's better pattern, just rarer | Keep price/consequence-on-the-control; the undo net (B7) is the actual safety mechanism, matching "diff-after + undo-always" instead of "confirm-before" |
| OS keyring / encrypted key storage | Feels like better security hygiene for API keys | No surveyed plugin (Ebook-Translator included) does this — plaintext-with-banner in `JSONConfig` is the FanFicFare/DeDRM-era precedent for a single-owner desktop library, and building keyring integration is real, untested surface for a threat model (multi-tenant library) this plugin doesn't have | Plaintext JSONConfig + 0600 + a visible banner (already the plan) |
| Local-endpoint engines (Ollama/LM Studio) | Free, private, no API key friction | None of the plugins surveyed that call cloud AI/translation APIs (Ebook-Translator included) ship a local-endpoint engine as a first-class option; it's a support-burden multiplier (network config, model management) for a niche this library's existing free/on-device `apple` engine already serves on macOS | Keep the `apple` engine as the free/on-device path; local-endpoint stays a future `engines.py` row if ever wanted |
| A literal Qt port of the wizard's prompt-by-prompt choreography | "Just make the GUI ask the same questions in the same order" feels like the least-risk path | Every surveyed plugin's settings/scan flow is a single dialog or dashboard, not a linear wizard-in-a-window; porting prompt sequencing 1:1 fights Qt's own idioms (dockable panels, greyed rows) and was explicitly rejected in scourgify's own design review | The dashboard (B6) as "the wizard given a surface" — same underlying `Plan`/task functions, different (non-linear, always-current) rendering |
| Live in-GUI snapshot restore | Feels more "in the app" than closing Calibre | No surveyed plugin performs a live database swap under an open GUI connection — this is exactly the two-writers hazard the whole write-path design (B2) exists to prevent | Close/restore/reopen flow (B6.6), already the plan |

## Feature Dependencies

```
Settings: API keys stored (B5)
    └──required-by──> Engine picker shows usable engines + price (B4.2/B4.3)
                          └──required-by──> Classify verb offered on selection (B1/B4)

First-run setup: columns + config created (B6.5, restart prompt)
    └──required-by──> Any write verb (wrangle/staleness/classify/synopsis) being offered at all
    └──required-by──> Dashboard leaving "setup mode" (B6.5)

Job system: everything on ThreadedJob, Dispatcher-wrapped (B3)
    └──required-by──> Every read (Inspect, dashboard header numbers)
    └──required-by──> Every write verb
    └──required-by──> Cost estimate (B4.1/B4.3 — scope resolves via a job before price renders)

One write path: apply_ops + backup + wipe guard + edit log (B2, B7's logging half)
    └──required-by──> Any write verb landing at all
    └──required-by──> Undo (B7's undo half) — cannot undo what wasn't logged
    └──required-by──> Review-in-library-view (B8) — per-item accept applies through B2

Edit log (B7 logging) ──enhances──> Undo (B7 replay)
Edit log (B7 logging) ──enhances──> History view (B7 read side, free)

Per-library namespacing (Constraints — proposals/failures/edit-log/config keyed by uuid)
    └──required-by──> Multi-library correctness of: Inspect, dashboard header, Review (B8),
                       History (B7), Restore-a-snapshot listing (B6.6)
    (already tripped once in phase 4 without it — see NLSpec Constraints amendment)

Marks-based review (B8) ──conflicts-with──> "no confirmation dialogs" being read as
    "no review surface" — B8 is NOT a confirmation dialog, it's a per-item accept/reject
    panel; the two ideas must stay visibly distinct in the MobileRead post and in the UI
    copy, or users will assume the "no confirmations" claim means "you can't review anything"
```

### Dependency Notes

- **Cost estimate requires the job system, not just settings.** The engine picker's price is
  `classify.est_cost()` over the *resolved* scope (B4.1) — which is itself a job-executed read.
  A GUI that shows a price before resolving scope on a background thread would either block the
  GUI thread (violates B3) or show a stale number. This is why B4's steps are ordered the way
  they are in the NLSpec; this research found no ecosystem plugin that has to solve this ordering
  problem at all, because none of them price anything.
- **Per-library namespacing is a cross-cutting dependency, not a phase-6-only concern.** Find
  Duplicates' per-library exemption list is the direct ecosystem precedent that this is a solved,
  expected pattern — Calibre users routinely run multiple libraries, and state bleed across them
  reads as a bug, not an edge case. Every phase-7/8 feature (dashboard, review, history) inherits
  this dependency.
- **The "no confirmation dialogs" anti-feature and the review-panel differentiator must not be
  conflated in messaging.** A reader skimming the MobileRead post could easily misread "no
  confirmation dialogs" as "you can't review before it writes" — the opposite of true. The post
  should state both explicitly and adjacently.

## MVP Definition

Framed against this milestone's already-committed scope (NLSpec B1–B8, PROJECT.md Active list),
not a fresh product decision — this section says what's load-bearing for the milestone to be
worth shipping vs. what can slip to a fast-follow without diminishing the release.

### Launch With (this milestone)

- [x] Selection→menu (B1), one write path (B2), job system (B3) — already the acceptance-critical
  behaviors (target 1.0) per the NLSpec; nothing in this research changes that priority.
- [ ] Classify with a real, per-selection cost estimate on the engine picker (B4) — this
  research raises its priority further: it's the plugin's clearest, verifiably-uncommon
  differentiator versus every comparable plugin found.
- [ ] Settings with key-first onboarding + TRAITS-derived failure-mode text (B5) — table stakes
  shape (Customize dialog, JSONConfig), differentiator content (price/failure mode per row).
- [ ] Dashboard in setup mode + first-run column creation with restart prompt (B6, B6.5) —
  table-stakes restart pattern, but the persistent outstanding-work header is what makes it more
  than "another settings dialog."
- [ ] Edit log + per-run undo (B7) — this research found this is the single feature category no
  surveyed plugin has; do not let it slip, it is the actual argument for "no confirmation
  dialogs" being safe rather than reckless.
- [ ] Review in the library view, per-item accept via marks (B8) — highest complexity, explicitly
  "no precedent anywhere" per the spec; this research confirms that assessment (Find Duplicates
  is the nearest analog and it is materially simpler: three fixed outcomes on structural
  duplicates, not free-text AI content).

### Add After Validation (v1.x)

- [ ] Keyboard shortcuts on the stabilized verb set (`action_spec` defaults) — cheap, but only
  worth wiring once the verb list stops moving; premature shortcut assignment gets renamed and
  annoys users who've already memorized one.
- [ ] "Visible Menus" style per-verb hide/show preference (Quality Check's pattern) — nice once
  the menu has enough verbs that some users want a shorter list; not needed at current verb count.
- [ ] Bake-off inside the GUI engine picker (already exists in the CLI/wizard; B4 doesn't
  explicitly require it in-GUI) — genuinely useful but not required for B4's acceptance.

### Future Consideration (v2+)

- [ ] A richer History view (per-book timeline visualizations) beyond the NLSpec's plain
  list/detail — no surveyed plugin does more than a flat list; not worth inventing ahead of
  demand.
- [ ] Local-endpoint engine support — explicitly out of scope; revisit only if `engines.py`'s
  one-row-per-engine cost of adding it stops being the blocker (e.g., real user demand on
  MobileRead post-launch).
- [ ] Windows/Linux parity beyond "best-effort" for Linux — already scoped as macOS+Windows
  acceptance, Linux best-effort; no ecosystem evidence changes that trade-off.

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|----------------------|----------|
| Cost-on-the-control engine picker (B4) | HIGH | LOW (core already built) | P1 |
| Edit log + per-run undo (B7) | HIGH | MEDIUM (log built; undo replay remains) | P1 |
| Review in library view, per-item marks (B8) | HIGH | HIGH (no precedent, prototype-first) | P1 |
| First-run setup + restart prompt (B6.5) | HIGH | MEDIUM (legacy-DB reopen constraint) | P1 |
| Dashboard with derived outstanding-work header (B6) | MEDIUM–HIGH | MEDIUM | P1 |
| Key-first onboarding with TRAITS failure text (B5) | MEDIUM | LOW (mostly built) | P1 |
| Per-library namespacing of all artifacts (Constraints) | HIGH (correctness) | MEDIUM | P1 |
| Keyboard shortcuts on stable verbs | LOW–MEDIUM | LOW | P2 |
| Per-verb visible/hide preference | LOW | LOW | P3 |
| In-GUI bake-off | MEDIUM | LOW–MEDIUM | P2 |
| Local-endpoint engine (Ollama) | LOW (no evidenced demand) | MEDIUM–HIGH | P3 (explicitly out of scope) |
| Richer History visualizations | LOW | MEDIUM | P3 |

## Competitor Feature Analysis

| Feature | Find Duplicates (kiwidude) | Ebook-Translator (bookfere) | Quality Check (kiwidude) | scourgify's Approach |
|---------|------------------------------|-------------------------------|----------------------------|------------------------|
| Batch job UX | Runs as one scan, then marks-based paging through result groups | Runs per-book/batch translation with a progress bar; no scope-cost step | Runs checks against library or current search restriction, presents a results grid | Job-system (B3) for every action incl. reads; scope resolved once (B4.1), cost shown before commit |
| Settings dialog | Standard Customize dialog: algorithm thresholds | Standard Customize dialog: per-engine API keys, concurrency limit | Standard Customize dialog: max tags, excluded tags, visible menus | Standard Customize dialog (B5) + masked-key/verified-state rows + TRAITS-derived role text |
| Cost display before spend | N/A (free) | **None found** — no estimator, no usage tracking | N/A (free) | Per-selection price from `classify.est_cost()`, shown on the engine-picker control (B4.3) |
| Review UX for proposed changes | Marks-based paging (marked:duplicate_group_NNNN), 3 fixed outcomes per group | None — translation applies directly, no proposal/review stage | Results grid with "apply fix" per finding, no marks | Marks-based paging into the real library view, per-item accept/reject panel, applies each accepted item immediately and logged (B8) |
| Undo of the plugin's own writes | None (only a per-library "exemption" mitigation) | None found | None found | Per-run, conflict-aware undo from a durable edit log (B7) |
| Per-library state | Yes — exemption list is per-library | Not applicable (global engine config) | Not clearly namespaced per-library in available docs | Explicit constraint: all operational artifacts namespaced by library uuid |
| First-run / setup flow | None needed (no required columns) | API key entry is the only "setup" | None needed | Setup mode in the dashboard: column health check, config write, one-time restart prompt (B6.5) |

## Sources

- FanFicFare Calibre plugin — MobileRead thread [t=259221](https://www.mobileread.com/forums/showthread.php?t=259221); wiki [CalibrePlugin](https://github.com/JimmXinu/FanFicFare/wiki/CalibrePlugin); [GitHub repo](https://github.com/jimmxinu/fanficfare) — MEDIUM confidence (cross-corroborated across wiki + thread search)
- Count Pages plugin — [source](https://github.com/kiwidude68/calibre_plugins/blob/main/count_pages/action.py), [README](https://github.com/kiwidude68/calibre_plugins/blob/main/count_pages/README.md), [wiki](https://github.com/kiwidude68/calibre_plugins/wiki/Count-Pages) — MEDIUM
- Find Duplicates plugin — [GitHub repo](https://github.com/kiwidude68/calibre_plugins/blob/main/find_duplicates/README.md), [wiki](https://github.com/kiwidude68/calibre_plugins/wiki/Find-Duplicates), MobileRead [t=131017](https://www.mobileread.com/forums/showthread.php?t=131017), [t=373506](https://www.mobileread.com/forums/showthread.php?t=373506) — MEDIUM
- Quality Check plugin — [README](https://github.com/kiwidude68/calibre_plugins/blob/main/quality_check/README.md), [wiki](https://github.com/kiwidude68/calibre_plugins/wiki/Quality-Check), MobileRead [t=125428](https://www.mobileread.com/forums/showthread.php?t=125428) — MEDIUM
- Generate Cover plugin — [README](https://github.com/kiwidude68/calibre_plugins/blob/main/generate_cover/README.md), MobileRead [t=124219](https://www.mobileread.com/forums/showthread.php?t=124219) — LOW–MEDIUM (thin snippets only)
- Reading List plugin — [DeepWiki summary](https://deepwiki.com/kiwidude68/calibre_plugins/6.3-reading-list-plugin), [kiwidude68/calibre_plugins repo](https://github.com/kiwidude68/calibre_plugins) — LOW (secondary/aggregator source)
- Extract ISBN plugin — [README](https://github.com/kiwidude68/calibre_plugins/blob/main/extract_isbn/README.md), [wiki](https://github.com/kiwidude68/calibre_plugins/wiki/Extract-ISBN) — MEDIUM
- Action Chains plugin — MobileRead [t=334974](https://www.mobileread.com/forums/showthread.php?t=334974) (first post fetched directly) — MEDIUM
- Ebook-Translator-Calibre-Plugin (bookfere) — [repo](https://github.com/bookfere/Ebook-Translator-Calibre-Plugin), [setting.py source](https://raw.githubusercontent.com/bookfere/Ebook-Translator-Calibre-Plugin/master/setting.py) fetched and read directly — MEDIUM (direct source read, single comparable plugin found)
- Calibre official plugin developer docs — [manual.calibre-ebook.com/creating_plugins.html](https://manual.calibre-ebook.com/creating_plugins.html) — MEDIUM (official doc domain, but classified web-tier by this session's tooling since it wasn't fetched via context7/ref; did not cover ThreadedJob/JobManager specifics — that gap is already closed by this repo's own B3 amendments)
- MobileRead "Introduction to plugins" (canonical install/toolbar/restart conventions) — [t=118680](https://www.mobileread.com/forums/showthread.php?t=118680) — MEDIUM
- MobileRead Plugins forum index — [f=237](https://www.mobileread.com/forums/forumdisplay.php?f=237) — LOW (index only, used for triangulation)

---
*Feature research for: Calibre GUI plugin ecosystem conventions (scourgify plugin milestone)*
*Researched: 2026-08-28*
