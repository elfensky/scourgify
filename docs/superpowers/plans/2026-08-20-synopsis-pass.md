# Synopsis pass — build briefing

Kickoff briefing for **#69** (the synopsis pass) and the retirement of `--text-fallback`.
Design decided 2026-08-20 in a grilled session; **issue #69 is the decision authority** — where
this briefing and the issue disagree, the issue wins. This document exists so a fresh session
can build the feature without re-deriving any decision.

## Mission

Every book ends up with a **settled synopsis**: a spoiler-safe, back-cover-style description a
human wants to read and the classifier can tag from. Thin blurbs are replaced by generated
synopses (whole book read chapter-by-chapter, free on-device engine, slow is fine); decent
blurbs are inspected and kept untouched. The raw-slice `--text-fallback` path is retired.

## The decisions (all settled — do not re-litigate)

1. **Storage: the built-in description (`comments`)** — the field Calibre and every reader
   displays. Protected by FanFicFare's own per-column switch: FFF → Standard Columns →
   Comments → **"New Only"** (`std_cols_newonly`; verified present in the installed plugin,
   restored-on-update in its `fff_plugin.py`). Rejected: a `#synopsis` column (hides the
   improvement); column + mirror (two copies with a sync rule).
2. **Stamp: `#synopsized`** (datetime custom column, house pattern like `#wrangled`), meaning
   "**synopsis settled** on this date" — earned by *generating* a synopsis OR by
   *inspecting-and-keeping* an adequate existing blurb. One stamp: queue membership
   (unstamped = synopsis queue), provenance, and the refresh clock
   (re-synopsize when `#updated` > `#synopsized`).
3. **Content: spoiler-safe back cover.** Premise, characters, stakes, hook, thematic hints
   ("themes: found family, slow burn") — never plot outcomes or endings. Rejected: full-story
   digest in the description; dual output (hidden full digest for the tagger) is a *later*
   upgrade only if tagging on back-cover text proves measurably weaker.
4. **Scope: the whole library**, with a cheap fast path — for a book whose blurb is already
   adequate, the model judges the *blurb only* ("adequate, spoiler-safe synopsis?"): yes →
   stamp, touch nothing (author voice preserved); no → full whole-book generation. Never
   AI-rewrite a healthy blurb.
5. **`--text-fallback` is retired entirely** (no demoted CLI flag). Thin-blurb books wait for
   their synopsis; there is no tag-now-off-a-random-slice path. `booktext.py` stays — the
   synopsis pass needs extraction more than classify did.
6. **Transition rule: classify must NOT require the stamp while the sweep runs.** Decent-blurb
   books keep flowing to classify exactly as today; only thin-blurb books wait. Requiring the
   stamp on day one would park ~7.5k classifiable books behind a months-long sweep. Once the
   sweep completes, the stamp becomes the standard gate (a follow-up decision, not this build).
7. **Guard: the FFF setting is enforced, not assumed.** `scourgify setup`'s FanFicFare health
   check warns if Comments isn't "New Only"; the synopsis pass runs the same check as a
   pre-flight guardrail (`GuardrailError`) that aborts with the exact fix; `--force` accepts
   degraded self-healing mode (clobber → re-enters queue → re-summarized). **Verify first**
   (see Facts below) that the checkbox is readable read-only; if not, degrade the guard to a
   one-time interactive confirm.
8. **Engine: apple** (free, on-device, single-threaded) is the engine of this pass; cloud is
   an explicit opt-in per stalled book, never a default. A failed attempt (unreadable/DRM'd
   file, model refusal) is recorded in a `synopsis_failures` artifact (via `artifacts.py`,
   mirroring `classify_failures.csv`) so the queue is finite on the failure side — the same
   rule the classify backlog learned the hard way.

## Facts to verify before writing code

- **FFF prefs readability**: FanFicFare stores per-library prefs in the library database.
  Confirm `std_cols_newonly` is reachable through `common.ro_connect()` (likely the
  `preferences` table / plugin-prefs namespace). This decides whether the guard is automatic
  or interactive-confirm (decision 7).
- **Apple engine context window**: measure what `afm.swift` accepts per request; it sizes the
  chapter-chunk map-reduce (summarize chapters → summarize the summaries).
- **`#updated` presence**: the refresh clock degrades gracefully for books without `#updated`
  (never auto-refresh those; the stamp alone governs).

## Shape of the change (follow the house architecture)

- **New tool module** (suggested name: `synopsis.py`; subcommand `scourgify synopsis`) built
  like the other tools: dry-run default, `--apply` writes, `--step` for 1-by-1 review
  (`ui.checklist` driven from the tool, never the wizard), `--batch N` chunking, resume off
  the stamp, `plan()`-style resolve-once if a confirm precedes work. Writes go through
  `run_writer` with `op_set_field("comments", …)` + `op_stamp_now("#synopsized", …)` +
  `op_create_column` on first run — backup, wipe guard, and edit log arrive free.
- **Scope ownership stays in `select.py`**: the synopsis queue ("unsynopsized": no stamp ∧ has
  text source, newest-first) and the classify backlog redefinition (drop the `text_fallback`
  concept) both live there — #70 made this a one-file change; keep it that way. The wizard
  header shows both numbers ("N to classify · M awaiting synopsis").
- **Text extraction stays in `booktext.py`** (extend for chapter-wise iteration as needed);
  **prompts live beside the pass**; **CSV formats in `artifacts.py`**; **wizard stage** slots
  into `WORKFLOW` before classify (order: wrangle → staleness → **synopsis** → classify → …)
  and only asks — the tool does.
- **Retirement touchpoints** (delete, don't deprecate): the `--text-fallback` flag and
  `text_fallback` plumbing in `classify.py` (gather/text_for), `select.py`
  (`sendable`/`pick`), `wizard.py` (scope menu), plus every doc mention (CLAUDE.md, README,
  USERGUIDE, help strings). classify's `MIN_DESC` gate stays — it is the transition-rule gate.
- **Plugin safety is a hard constraint**: the new module must import clean under Calibre's
  bundled Python (no rich; `report.py` for rendering), raise `GuardrailError` (never
  `SystemExit`) anywhere job-reachable — `tests/test_plugin_safety.py` enforces both.
- **CONTEXT.md on landing**: add **Synopsis pass** and **Settled** (`#synopsized`); update
  **Backlog** (the text-fallback clause changes).

## Verification bar

- New `tests/test_synopsis*.py`: queue semantics against `tests/fixture_db.py`, the
  adequacy-vs-generate branch and refresh clock as pure functions, the FFF guard (stubbed),
  prompt/parse round-trip with the transport monkeypatched (`engines._post_json` seam).
- Existing suites must stay green through the retirement — expect deliberate updates in
  `test_selection.py` (text_fallback cases become synopsis-queue cases), `test_classify_run`,
  `test_wizard_flow`, `test_cli`.
- `uv run tests/test_*.py` all pass; version bumped (guard fires on `src/**`); PR to
  `develop`, rebase-merge (linear history).
- Cost sanity: the pass is apple-only by default — a full-library sweep must cost €0. Any
  cloud opt-in goes through the existing spend-gate pattern.

## Out of scope (recorded so it stays out)

- Making the stamp the classify gate (post-sweep follow-up decision).
- The hidden full-digest-for-the-tagger artifact (only if back-cover tagging underperforms).
- The plugin's synopsis verb (phase 6+, #62 owns sequencing).
- Per-library artifact namespacing (phase 6, tracked in the NLSpec).
