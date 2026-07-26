# Explicit book scope (`--books`) + a full-library test run

**Date:** 2026-07-26
**Status:** approved, ready for implementation

## Problem

Two problems, one root cause.

**No tool can be pointed at a specific set of books.** `classify` has scope flags
(`--all` / `--incremental` / `--last N` / `--since DATE`), but none of them names books
directly. `wrangle` and `staleness` have no scope at all — `apply --apply` recomputes
the whole library and `staleness --apply` re-derives `#status` for every dated book.
There is no way to say "just these fifty".

**That makes the tool hard to test against a real library.** Exercising the write path
on a 7,949-book library means either rewriting all 7,949 books or resorting to a hack —
clearing the `#wrangled` stamp on N books so `--incremental` happens to pick exactly
those. The hack mutates library state to express a selection, is not reproducible, and
does nothing at all for `wrangle` or `staleness`.

A first-class book scope fixes both, and gives the user a targeted-redo capability
(`re-run just these books`) that the tool currently lacks.

## Decision

### One grammar, one parser

`select.parse_books(spec) -> list[int]` — pure, no DB, fully testable:

| spec | means |
|---|---|
| `1,2,3` | explicit ids |
| `10-20` | inclusive range |
| `@ids.txt` | one id per line; `#` comments and blank lines ignored |
| `1,5-9,@more.txt` | combinable, comma-separated |

Ids are de-duplicated. A malformed token raises `SystemExit` with the offending text —
the house convention for user-facing failure. Ids that parse but don't exist in the
library are dropped by the caller and reported, not fatal.

`select.py` already owns "which books does this run operate on", so the parser and the
new scope mode belong there and nowhere else.

### Three wirings

| Tool | Seam | Semantics |
|---|---|---|
| `classify` | new `"ids"` mode in `select.pick` | sits alongside the existing scope modes; `--books` is just another way to produce the target list |
| `wrangle audit` / `apply` | `Plan.restrict(ids)`, applied **after** the full compute | the read stays library-wide; only the write set narrows |
| `staleness` | filter `compute()`'s returned rows | per-book independent, so a plain filter is correct |

### Why `wrangle` restricts instead of reading less

`read_library`'s docstring is explicit that the whole library is read deliberately:
`transform()` needs global context — `tagcanon` majority spelling and `known_chars` are
both derived from every book. Scoping the *read* would silently change the transform's
output for the selected books, which is exactly the wrong failure mode for a tool whose
selling point is being audit-first.

So `Plan` computes the full pass as it does today, and `restrict(ids)` narrows
`self.changes` and `self.diffs` afterwards. The preview, the 1-by-1 step review, the
guards, and the write all already read those two structures, so nothing else changes.

### Guardrail correctness under scoping

This is the one non-trivial part of the change.

`Plan.__init__` accumulates `lostF` / `lostC` / `tagsB` / `tagsA` as scalars during the
full-library loop. A naive `restrict()` would leave them at their full-library values,
so a fifty-book write would be judged against library-wide totals — `tag_loss_guard`
would compare a handful of tag removals against 7,949 books' worth of assignments and
never fire, and `data_loss_guard` would abort a clean fifty-book write because some
*other* book elsewhere in the library would have lost its last fandom.

Fix: keep the four numbers per-book (`self.lost[book] = (lost_fandom, lost_char)`,
`self.tagn[book] = (before, after)`) and derive the scalars as properties summing over
whatever books the plan currently holds. `restrict()` then narrows those dicts along
with `changes`/`diffs`, and both guards read correct scoped numbers with no further
work.

`common._is_wipe` / `_predict_populated` need no change — they work off the ops list,
which is already narrowed by the time it reaches `run_writer`.

### Deliberately not built

- **`--random N`.** A single shell line writes an id file once, and a *file* is what
  makes a run reproducible across six tools. A random flag would produce a different
  selection per invocation, which is the opposite of what's wanted.
- **A wizard book-picker.** The wizard takes no argv and its stages are library-wide by
  design. Adding a selection UI is a separate feature with its own design.
- **Scope for `promote` / `promote --backfill`.** These are driven by the ledger and the
  archived proposals, not by a book set. A book scope there would mean something
  different and isn't needed.

### Tests

- `tests/test_selection.py` — `parse_books` cases (each spec form, combination,
  dedup, malformed token) and the `"ids"` mode against the fixture DB, including ids
  absent from the library.
- `tests/test_plan.py` — `restrict()` narrows changes/diffs, and the guards fire on
  scoped numbers rather than full-library ones (the regression this design exists to
  prevent).

## The test run this enables

Against the user's real 7,949-book library, with a backup taken beforehand. Gemini and
OpenAI keys are present; Anthropic and Mistral are not.

**0 · Baseline.** Confirm the backup, snapshot `metadata.db` to scratch, record
pre-state counts, write `ids50.txt` — fifty random book ids.

**1 · Build** the `--books` feature above. Full `tests/test_*.py` green before anything
touches the library.

**2 · Read-only at full scale.** `setup` health check, `audit` (SAFETY lines, both
guardrails), `staleness` dry run, `classify` proposal dry-run, `rollback --list`.
**Checkpoint:** the diff goes to the user for go/no-go before any library-wide write.

**3 · Library-wide writes** (gated on that go). `apply --apply`, then re-run to prove
the idempotency claim — a second run must produce zero changes. Same for
`staleness --apply`.

**4 · classify, fifty books, both engines.** `--bakeoff` across gemini/openai/apple on
~5 samples → `--books @ids50.txt --engine gemini --text-fallback` → `--apply`, verifying
tags landed and `#wrangled` was stamped → `--fresh --books @ids50.txt --engine openai` →
a head-to-head tag diff on identical input → `--apply --step` on a slice, rejecting a
few, verifying `data/rejects.csv`.

**5 · Downstream.** `promote` with a gemini advocate and an openai skeptic
(`--verify-with`), `promote --apply`, `promote --backfill`, `scourgify overrides`
dry-run then `--apply`.

**6 · Wizard, PTY-driven, full lap with writes.** `tests/drive_wizard.py` extended to a
real-library variant: landing menu → all seven tasks → guided run → scope menu →
bake-off → 1-by-1 checklist, answering *yes* at the classify/promote/backfill write
gates.

**7 · Rollback last, then roll forward.** `rollback` restores in place, so it runs at
the end. Because it snapshots the current db before restoring, the run rolls back and
then rolls back the rollback — which tests the reversibility claim itself.

**Edge cases explicitly exercised:** Calibre open → write must refuse; malformed and
nonexistent `--books` ids; `--force` overriding a guardrail; Gemini
`PROHIBITED_CONTENT` → `classify_failures.csv`; rich-absent plain rendering.

**Cost:** roughly €0.30–1.00. **Operational constraints:** Calibre stays closed for
every write step, and the library lives on iCloud Drive — sync state is checked before
each write.

## On bugs found

Fix on `develop` as they're found, with a `tests/test_*.py` pin for each, then one
findings report at the end.
