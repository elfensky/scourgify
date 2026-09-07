---
phase: 01-foundation-a-hostable-core
plan: 06
subsystem: infra
tags: [write-funnel, edit-log, conflict-detection, calibre-plugin, concurrency]

# Dependency graph
requires:
  - phase: 01-04
    provides: "common._write_run() — the ONE shared pre-write protocol both transports call — and the already-threaded but unused `is_multi` reader plus `common.WriteResult`'s empty `skipped` field, both defined there specifically so this plan would not need to re-touch either transport's signature"
provides:
  - "common.op_set_field(field, values, expected=None) — the op-constructor's plan-time before-value parameter, mirroring `values`'s shape and stringification"
  - "common._check_conflicts(ops, before, is_multi) — the apply-time conflict filter, wired into _write_run between the before-read and the snapshot"
  - "editlog.finish(rec, outcome, skipped=None) — the run footer's skipped/n_skipped fields"
  - "common.column_values()/_populated_books() comments branch — a real before-value for the builtin comments field"
  - "promote.backfill_plan()'s widened 3-tuple return (chg, adds, before)"
  - "synopsis.Plan.raw_blurbs — the unstripped comments text, kept alongside the stripped self.blurbs"
  - "expected= populated on every set_field op wrangle/classify/staleness/promote/synopsis construct"
affects: [phase-2-plan-job-and-qt-picker, phase-4-history-and-undo]

# Actuals (#2632)
actuals:
  tokens: 13500
  tasks: 3
  commits: 3

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Apply-time conflict filter as a pure function over already-read state — _check_conflicts takes the funnel's OWN before-read (editlog.before_values) and the injected is_multi reader, adding no new read; it reuses editlog.conflict, the same predicate undo will read in phase 4, so 'conflict' has exactly one meaning across apply and undo"
    - "Pinned no-snapshot-on-total-refusal branch inside a contextmanager: _write_run's all-skipped path writes header+footer and yields a sentinel state directly, bypassing the snapshot/apply/finally sequence entirely rather than applying an empty ops list through the normal path"
    - "Producer-specific expected sourcing: each of the five producers pulls its plan-time before-state from a DIFFERENT place it already had (perbook, the age-derived `old`, a head-of-function `cur` read, a backfill_plan return value, and a raw-vs-stripped text split) rather than a uniform helper — the plan text is explicit that assuming uniformity here was itself a bug"

key-files:
  modified:
    - src/scourgify/common.py
    - src/scourgify/editlog.py
    - src/scourgify/wrangle.py
    - src/scourgify/classify.py
    - src/scourgify/staleness.py
    - src/scourgify/promote.py
    - src/scourgify/synopsis.py
    - tests/test_editlog.py
    - tests/test_write_path.py
    - tests/test_plan.py
    - tests/test_promote.py
    - tests/test_synopsis.py
    - tests/test_staleness_scope.py
    - tests/test_classify_run.py

key-decisions:
  - "check_wipe runs on the change-set BEFORE the conflict filter, on the ops the tool asked for — a runaway rule is refused whether or not drift happens to trim it below the wipe threshold; a guard a race can disarm is not a guard."
  - "The all-skipped branch inside _write_run does its own editlog.start/finish and yields a sentinel dict directly (outcome='skipped', ops=[]), rather than routing an empty ops list through the normal snapshot/apply/finally sequence — this is what makes 'no snapshot, still a footer' an exact two-line special case instead of a general property callers have to reason about."
  - "classify's expected comes from `cur = current_tags(con)` read at the head of apply_proposal, NOT from the proposal row — the proposal schema (artifacts.PROP_COLS) has no before-state column and deliberately gets none added, because a proposal-time expected would skip any book edited since the LLM ran while its #wrangled stamp (carrying no expected) still applied, permanently retiring it from the --unclassified backlog untagged. Documented as an accepted, visible trade-off (the skipped list names the book) rather than a hidden edge case."
  - "synopsis.Plan keeps self.raw_blurbs (unstripped) alongside self.blurbs (stripped) from one query; the comments op's expected is built from raw_blurbs — comparing the stripped text against the stored HTML would make every op read as a conflict and silently disable the whole pass."
  - "promote.backfill_plan()'s return widened to a 3-tuple (chg, adds, before) rather than adding a fourth return or a second call — the discarded `cur` read it already made is exactly the before-state the conflict check needs; wizard.py's only other call site (`backfill_plan()[0]`) needed no change."

patterns-established:
  - "A plan-time `expected` mapping travels alongside `values` on every write op, sourced by the producer from the SAME state its write plan was computed against (never a fresh read at write time) — this is now the shape every future write-producing tool follows."

requirements-completed: [FOUND-05]

coverage:
  - id: D1
    description: "An op whose current value no longer matches its plan-time expected is skipped and reported by book (footer, WriteResult, CLI output) — never overwritten — verified via editlog.conflict as the sole predicate"
    requirement: "FOUND-05"
    verification:
      - kind: unit
        ref: "tests/test_write_path.py#test_an_op_whose_value_drifted_is_skipped_not_clobbered"
        status: pass
      - kind: unit
        ref: "tests/test_write_path.py#test_a_matching_expected_value_applies_normally"
        status: pass
      - kind: unit
        ref: "tests/test_write_path.py#test_an_op_without_an_expected_mapping_applies_unconditionally"
        status: pass
      - kind: unit
        ref: "tests/test_write_path.py#test_stamp_and_pref_ops_are_never_conflict_checked"
        status: pass
      - kind: unit
        ref: "tests/test_write_path.py#test_both_transports_skip_the_same_ops"
        status: pass
      - kind: unit
        ref: "tests/test_editlog.py#test_the_footer_carries_the_skipped_pairs_and_their_count"
        status: pass
    human_judgment: false
  - id: D2
    description: "Idempotency: applying the same ops list twice skips everything the second time (current now equals after, which no longer equals plan-time expected)"
    requirement: "FOUND-05"
    verification:
      - kind: unit
        ref: "tests/test_write_path.py#test_applying_the_same_ops_list_twice_skips_everything_the_second_time"
        status: pass
    human_judgment: false
  - id: D3
    description: "The all-skipped ordering is pinned: before-read -> conflict filter -> if nothing survives, header+footer with zero op lines, no snapshot, nothing applied"
    requirement: "FOUND-05"
    verification:
      - kind: unit
        ref: "tests/test_write_path.py#test_an_all_skipped_run_takes_no_snapshot"
        status: pass
      - kind: unit
        ref: "tests/test_editlog.py#test_an_all_skipped_run_still_writes_a_header_and_a_footer_with_no_op_lines"
        status: pass
    human_judgment: false
  - id: D4
    description: "The comments before-read fix (T-01-28): column_values()/_populated_books() read the builtin comments table directly, giving a synopsis set_field op a real before-value instead of None for every book"
    requirement: "FOUND-05"
    verification:
      - kind: unit
        ref: "tests/test_write_path.py#test_column_values_reads_the_builtin_comments_field"
        status: pass
      - kind: unit
        ref: "tests/test_write_path.py#test_a_comments_op_gets_a_real_before_value"
        status: pass
    human_judgment: false
  - id: D5
    description: "Every producer (wrangle, classify apply, staleness, promote backfill, synopsis) populates expected from its own named plan-time source, not a fresh read; mutating one book's value between plan and write skips exactly that book"
    requirement: "FOUND-05"
    verification:
      - kind: unit
        ref: "tests/test_plan.py#test_write_carries_expected_from_perbook_and_skips_drifted_books"
        status: pass
      - kind: unit
        ref: "tests/test_staleness_scope.py#test_write_carries_expected_from_the_computed_before_state_and_skips_a_drifted_book"
        status: pass
      - kind: unit
        ref: "tests/test_promote.py#test_backfill_plan_returns_the_before_tag_set"
        status: pass
      - kind: unit
        ref: "tests/test_synopsis.py#test_synopsis_expected_is_the_raw_stored_description_not_the_stripped_one"
        status: pass
      - kind: unit
        ref: "tests/test_classify_run.py#test_apply_proposal_ops_union_stamp_archive"
        status: pass
    human_judgment: false
  - id: D6
    description: "ops.py and _writer.py stay unchanged; the conflict check runs entirely on the near side of the calibre-debug subprocess boundary"
    verification:
      - kind: other
        ref: "git diff --stat src/scourgify/ops.py src/scourgify/_writer.py (empty across all three task commits)"
        status: pass
    human_judgment: false
duration: ~55min
completed: 2026-09-07
status: complete
---

# Phase 1 Plan 6: Foundation — a hostable core Summary

**`common._write_run` now refuses to overwrite a value that drifted since the plan was computed — an apply-time conflict filter over `editlog.conflict` drops the affected `(book, field)`, an all-conflicted run costs no snapshot but still leaves a header+footer in History, and all five write-producing tools (wrangle, classify, staleness, promote, synopsis) populate the `expected` their own plan was built against.**

## Performance

- **Duration:** ~55 min
- **Started:** 2026-09-07T21:40:00Z
- **Completed:** 2026-09-07T22:35:00Z
- **Tasks:** 3 of 3 completed
- **Files modified:** 14 (7 production modules, 7 test files)

## Accomplishments

- `common.op_set_field(field, values, expected=None)` gained the plan-time before-value parameter, mirroring `values`'s stringified book-id shape exactly so the two dicts round-trip through JSON with matching key types. Omitted entirely when `None` — an op with no `expected` key still applies unconditionally, so every not-yet-updated caller kept working throughout Task 1 and Task 2.
- `common._check_conflicts(ops, before, is_multi)` is the new apply-time conflict filter, wired into `_write_run` between the before-read (`editlog.before_values`) and the snapshot — the ONE insertion point for both transports. For each `set_field` op carrying `expected`, it compares the funnel's own before-read against the op's `expected` via `editlog.conflict(current, expected, is_multi(field))` — the SAME predicate undo will use in a later phase — and drops every conflicting `(book, field)` from both `values` and `expected`; an op emptied entirely by the filter is dropped, getting no op line and never applying. `stamp_now`/`set_pref`/`create_column` are excluded from the loop by construction (D-09) and pass through untouched regardless of `before`.
- `_write_run`'s pinned order (Codex's own recommended sequence, resolving the plan's self-contradiction concern): drop-empty → lock → `check_wipe` on the ops the tool asked for → before-read → conflict filter → **if nothing survives**: `editlog.start(tool, [], before, …)` + `editlog.finish(rec, "skipped", skipped=skipped)`, no snapshot, nothing applied, `WriteResult(backup=None, outcome="skipped")` → **otherwise**: snapshot → `editlog.start` with only the surviving ops → yield → apply → `editlog.finish(rec, outcome, skipped=skipped)`. Both `write_ops` and `run_writer` early-return on the `outcome == "skipped"` state without invoking `apply_ops`/the subprocess.
- `editlog.finish(rec, outcome="ok", skipped=None)` folds `skipped` (`[[book, field], ...]`) plus its count `n_skipped` into the footer only when non-empty, so an ordinary run's footer stays byte-identical to before this parameter existed (pinned by `test_a_footer_with_no_skipped_arg_is_unchanged_from_before_this_parameter_existed`).
- `common.column_values()`/`common._populated_books()` gained a `comments` branch reading the builtin table directly (`SELECT book, text FROM comments`) — before this fix, `comments` routed through the custom-columns lookup, which misses it and silently returns `{}`/`None` for every book, meaning every synopsis `set_field` op would have compared a real blurb against `None` and been skipped forever (T-01-28, the load-bearing finding from the plan's own grounding pass).
- Every producer now populates `expected` from the before-state its OWN write plan was computed against, never a fresh read: `wrangle.Plan.write` builds it from `self.perbook` (per-book state — NOT `self.before`, which is `{column key: set of distinct values}` for the library-wide audit, a Codex-caught wording error in the original plan text); `staleness.write` uses the `old` value already carried in its `(book, old, new, age)` rows; `classify.apply_proposal` uses the `cur = current_tags(con)` it already reads at its own head (with a documented, deliberate decision NOT to use the proposal's own state, since the proposal schema has no before-state column and adding one would create a worse failure mode); `promote.backfill_plan()` now returns a 3-tuple `(chg, adds, before)`, built from the `cur` read it previously discarded; `synopsis.Plan` keeps `self.raw_blurbs` (the unstripped stored text) alongside `self.blurbs` (stripped), and the comments op's `expected` comes from the raw one.
- The one stated consequence of stamps carrying no `expected` (D-09) — a conflict-skipped classify or synopsis book still gets its `#wrangled`/`#synopsized` stamp, leaving it settled-but-untagged/unwritten — is written into both producers as a comment, and made visible (not hidden) via the skipped list on the footer, the `WriteResult`, and the CLI output.
- Two pre-existing `tests/test_classify_run.py` assertions hardcoded the pre-`expected` op shape (`common.op_set_field("tags", {...})` with no `expected` key) and broke as a direct, in-scope consequence of Task 3's producer change; fixed to include the correct `expected` value (Rule 1 — see Deviations).

## Task Commits

Each task was committed atomically:

1. **Task 1: expected on the write op, the conflict filter, and the skipped report** - `b6486fd` (feat)
2. **Task 2: The comments before-read** - `a96f6ec` (fix)
3. **Task 3: Every producer populates expected from its own plan-time before-state** - `8dcb2d7` (feat)

_No plan metadata commit yet — this SUMMARY.md is committed separately per the executor protocol._

## Files Created/Modified

- `src/scourgify/common.py` - `op_set_field(expected=)`, `_check_conflicts()`, `_write_run`'s pinned order + all-skipped branch, `write_ops`/`run_writer`'s early-return on `outcome == "skipped"`, `column_values`/`_populated_books`'s `comments` branch
- `src/scourgify/editlog.py` - `finish(skipped=None)`, docstring updates for the new footer shape and the skip-not-clobber contract
- `src/scourgify/wrangle.py` - `Plan.write()` builds `expected` from `self.perbook`
- `src/scourgify/classify.py` - `apply_proposal()` builds `expected` from `cur`, with the documented reasoning for not using the proposal's own state
- `src/scourgify/staleness.py` - `write()` builds `expected` from the rows' `old` value
- `src/scourgify/promote.py` - `backfill_plan()` returns `(chg, adds, before)`; `backfill()` builds `expected` from `before` over the accepted books
- `src/scourgify/synopsis.py` - `Plan.__init__` keeps `self.raw_blurbs`; `run()` builds the comments op's `expected` from it
- `tests/test_editlog.py` - 2 new footer/all-skipped tests + 1 no-regression test
- `tests/test_write_path.py` - 7 conflict-check tests + 2 comments-before-read tests
- `tests/test_plan.py` - 1 new end-to-end wrangle expected/skip test
- `tests/test_promote.py` - 1 new `backfill_plan` before-state test
- `tests/test_synopsis.py` - 1 new raw-vs-stripped `expected` test
- `tests/test_staleness_scope.py` - 1 new end-to-end staleness expected/skip test
- `tests/test_classify_run.py` - 2 pre-existing assertions fixed for the new op shape (Rule 1)

## Decisions Made

See `key-decisions` in the frontmatter for the five load-bearing ones. In summary: `check_wipe` runs before the conflict filter on the unfiltered change-set; the all-skipped branch is a dedicated early-exit inside `_write_run` rather than routing an empty list through the normal path; classify's `expected` deliberately comes from a fresh-at-apply-time `cur` read (not the proposal), because the proposal schema has no before-state column and the alternative silently and permanently untags books; synopsis needs the raw (unstripped) blurb, not the stripped one, or every op would read as a conflict; `promote.backfill_plan()`'s return widened rather than duplicating the `current_tags` read.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Two `tests/test_classify_run.py` assertions broken by Task 3's producer change**
- **Found during:** Task 3, full-suite verification after updating `classify.apply_proposal`
- **Issue:** `test_apply_proposal_ops_union_stamp_archive` and `test_apply_proposal_skips_rows_for_deleted_books` asserted `common.op_set_field("tags", {...}) in ops` with no `expected` key — the pre-Task-3 op shape. Adding `expected` to the constructed op (as Task 3 requires) made these dict-equality checks fail.
- **Fix:** Added the correct `expected` value to each assertion (`{1: ["Old"]}` and `{1: []}` respectively, matching each fixture's actual starting tag state), so the assertions now pin the new, correct op shape.
- **Files modified:** `tests/test_classify_run.py`
- **Verification:** `uv run tests/test_classify_run.py` — 10/10 pass.
- **Committed in:** `8dcb2d7` (Task 3 commit)

---

**Total deviations:** 1 auto-fixed (1 bug — a pre-existing test broken by an in-scope, plan-mandated production change). **Impact:** None beyond the expected test churn from adding `expected` to every producer's op — no scope creep, no behavior change beyond what Task 3 specifies.

## Issues Encountered

While writing `tests/test_promote.py#test_backfill_plan_returns_the_before_tag_set`, the first candidate tag ("Time Travel") turned out to route to the `#genres` column under default wrangle config rather than staying a tag, which made `wrangle_stable` (correctly) exclude it from the backfill and broke the test's own expectations — not a production bug, a test-fixture-authoring gap. Resolved by switching the test's promoted tag to "Fluff", which default config leaves as a tag.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- ROADMAP success criterion 3 is fully met by this phase's two plans together: "A second write job against the same library while one is running is refused with a visible reason naming the running job" (delivered by plan 01-04's write-run lock) "and an op whose current value no longer matches its expected before-value is skipped and reported by book — never overwritten — through `ops.apply_ops` using `editlog.conflict`" (delivered by this plan — `_check_conflicts` runs entirely before `ops.apply_ops` is ever called, on the near side of the `calibre-debug` subprocess boundary, and `ops.py`/`_writer.py` are unchanged, confirmed empty `git diff --stat` across all three commits).
- Phase 4's History/undo feature has a stable footer shape to read (`skipped`/`n_skipped`) and a single, already-tested conflict predicate (`editlog.conflict`) to reuse for replay — no new predicate needed there.
- The two accepted, documented trade-offs (a conflict-skipped classify/synopsis book is still stamped, leaving it settled-but-unwritten) are visible via the skipped list on every reporting surface and are explicitly named as phase-2 diff-after follow-up work, not silently deferred.
- No blockers for the next phase.

## Self-Check: PASSED

- `src/scourgify/common.py`, `src/scourgify/editlog.py`, `src/scourgify/wrangle.py`, `src/scourgify/classify.py`, `src/scourgify/staleness.py`, `src/scourgify/promote.py`, `src/scourgify/synopsis.py` — all confirmed present on disk with the expected new/changed symbols (`_check_conflicts`, `op_set_field(expected=)`, `editlog.finish(skipped=)`, `Plan.raw_blurbs`, `backfill_plan()`'s 3-tuple).
- All 3 task commits (`b6486fd`, `a96f6ec`, `8dcb2d7`) confirmed present in `git log`.
- `uv run tests/test_editlog.py` — 16/16 pass. `uv run tests/test_write_path.py` — 35/35 pass. `uv run tests/test_plan.py` — 11/11 pass. `uv run tests/test_promote.py` — 23/23 pass. `uv run tests/test_synopsis.py` — 27/27 pass. `uv run tests/test_wizard.py` — 26/26 pass.
- Full local suite green in isolation: `env -u CALIBRE_LIBRARY SCOURGIFY_HOME=$(mktemp -d) bash -c 'for t in tests/test_*.py; do uv run "$t" || exit 1; done'` — 23 test files, exit 0.
- `git diff --stat src/scourgify/ops.py src/scourgify/_writer.py` — empty across all three commits.
- `git diff --stat tests/test_wizard_flow.py tests/test_cli.py` — empty.

---
*Phase: 01-foundation-a-hostable-core*
*Completed: 2026-09-07*
