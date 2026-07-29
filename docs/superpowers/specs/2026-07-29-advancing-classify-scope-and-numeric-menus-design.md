# Advancing classify scope + numeric wizard menus

**Date:** 2026-07-29
**Ships as:** 1.6.0 (feature)
**Status:** approved design, not yet implemented
**Supersedes:** the positional `--window` design of the same date (rejected — see "Rejected: positional windows")

## Problem

**1. No classify scope advances.** The wizard offers `new/changed`, `whole library`, `skip`. A full
cloud pass is ≈ €50, so "whole library" is not a casual button and there is nothing between it and
nothing. Verified against the live 7,949-book library, **no existing mechanism lets you chip
through in chunks**:

| Mechanism | Why it does not advance |
| --- | --- |
| `--last 50` | `newest[:50]` — re-run picks the same 50 forever |
| `--all --batch 200` | `--all` puts every book in `explicit`, so `needs()` is True, so `done` is never populated and the resume is suppressed (`classify.py:257`, `:299`). Re-run picks the same 200 |
| `--incremental` | All 7,949 books are stamped, so `changed()` is 0 |

The stamp cannot serve as the cursor: `#wrangled` is backfilled across the whole library on first
run by design (`classify.py:189-191`, inside `if not have_wrangled`), so it does not distinguish
"classified" from "was present when the column was created". Measured: **7,949 stamped, 7,668 ever
in a proposal, 281 never classified.**

**2. Menu keys are inconsistent.** The landing menu is numbered; every menu below it is lettered.

## Part A — one new scope: `unclassified`

The durable, identity-keyed record of what has actually been classified already exists: the
archived applied proposals. `promote.backfill_plan()` already reads them through
`artifacts.applied_proposals()`, so this is an established pattern, not new machinery.

Add one pick mode, owned by `select.py` (CLAUDE.md's sole owner of "which books does this run
operate on"):

```python
# select.py
def unclassified(con, seen: set[int]) -> list[int]:
    """Books with no row in ANY proposal, archived or pending — newest-added-first.
    `seen` is injected (artifacts is classify's vocabulary, not select's) so this stays pure."""
```

wired as `pick(con, "unclassified", ids=seen)` and exposed as `classify --unclassified`.

**Why this is the correct cursor:**

- **Identity-keyed** — book IDs, so it is immune to the reordering that sank the window design
- **Self-advancing** — `--apply` archives the proposal, so those books leave the set permanently
- **Deletion-safe** — a deleted book simply isn't in `books` any more. This matters: the library has
  **1,965 missing IDs** in range 16–9,929, so deletions demonstrably happen here
- **Addition-safe** — a new book has no proposal row, so it joins the set
- **Re-fetch-safe** — FanFicFare bumping `timestamp` changes ordering, not membership
- **Has a completion invariant** — the set empties. "How much is left?" is answerable, which no
  positional scheme could do

Chunk with the existing `--batch N`. `classify --unclassified --batch 200`, apply, repeat: each run
strictly shrinks the remaining set. Today that is 281 books, ≈ €2.

`--fresh` remains the (expensive, documented) way to deliberately re-classify already-done books.

**Precedence:** `--books` (explicit IDs) still wins over everything, then `--all`,
`--incremental`, `--unclassified`, `--last`, `--since`. `--unclassified` joins the `explicit`
predicate at `classify.py:257` — that line is edited, not just the branch above it (the earlier
draft missed it and would have raised `AttributeError` on every run).

**Nothing else changes.** `--last` keeps `type=int` and its current meaning. `--books`,
`staleness`, and `wrangle apply` are untouched.

## Part B — the wizard scope menu

```
classify scope
  1  new/changed — 12 books
  2  never classified — 281 books        ← new
  3  whole library — 7,949 books · full pass
  4  skip
```

Row 2 appears only when the set is non-empty, and carries its count so the cost is legible before
selection. Choosing it sets `unclassified=True`; `--batch` is not exposed in the wizard — the row's
own count is the batch, and the existing spend confirmation prices it.

No new prompt primitive is needed. `ui.ask_text` is dropped along with the window grammar.

## Part C — digits everywhere except `w` and `q`

| Menu | Now | Becomes |
| --- | --- | --- |
| wrangle apply | `a` `r` `s` | `1` `2` `3` |
| classify scope | `n` `a` `s` | `1`–`4` (rows are conditional — see below) |
| review (stamp-only) | `a` `d` | `1` `2` |
| review (full) | `a` `r` `k` `d` | `1` `2` `3` `4` |
| promote verdicts | `a` `k` `d` | `1` `2` `3` |
| engine picker | `1..N` + `c` | `1..N` + `N+1` |
| landing menu | `w` `1`–`7` `q` | unchanged |

**Digits are presentation only.** Menu builders return `(key, id, label, hint)` and every caller
dispatches on the **symbolic `id`**, never on the digit. This is mandatory, not stylistic: rows are
conditional (`_scope_options` emits `new/changed` only when there are changed books), so a digit's
meaning is data-dependent. A positional `scope == "1"` comparison silently means "new/changed" in
one library state and **"whole library, ≈€50"** in the other. The engine picker gets the same
treatment, and its `extra` rows keep returning the caller's symbolic id (`"c"`) — a naive renumber
there raises `KeyError: 'compare'`, which is not `SystemExit`, so `_stage_guard` does not absorb it
and the whole session dies.

**`ui.menu` gains `assert default in keys`.** A stale letter default currently escapes validation on
the interactive path only — rich's `PromptBase` returns the default *before* `check_choice`, so
`default="d"` left behind at `wizard.py:319` would silently fall through to "keep" while the
scripted path still raised and CI stayed green.

**One deliberate exception: `ui.checklist` keeps `a` / `s` / `q`.** Digits there already mean
"toggle item N".

**Accepted cost of the hard switch:** letters were position-independent, so `s` meant "do nothing"
everywhere. Digits are not: `3` is "skip" in the wrangle menu but **"discard"** in the verdicts
menu, and `2` is "review 1-by-1" in the full review menu but **"discard"** in the stamp-only one.
The user chose the hard switch over `also=` aliases with this understood.

## Testing

- `unclassified` — pure, given an injected `seen` set: excludes proposed books, includes new ones,
  survives a deleted book, and is newest-added-first
- the set strictly shrinks after an apply archives a proposal (the property that makes chunked
  sweeping correct — and unlike the rejected design's, it is testable, because it depends on
  membership rather than on the DB not changing underneath)
- `--unclassified` reaches `explicit` at `classify.py:257` — a regression test for the
  `AttributeError` class of bug
- every menu builder's `(key, id, …)` mapping, and that callers dispatch on `id`: specifically that
  the classify scope menu picks the right scope in **both** library states
- `ui.menu` rejects a default not in `keys`
- `_ask_engine` returns the symbolic id for an `extra` row, with 5 engines
- renumbered flows in `test_wizard_flow.py`

`test_script.py` needs no change — it exercises `ui.menu` generically against local fixture options.
`tests/drive_wizard.py` needs no change either: its only lap is the landing menu, which is untouched
(the earlier draft wrongly listed it as breaking).

## Breaking changes

Old letter keys stop working. Updated in the same commit: `test_wizard_flow.py`, CLAUDE.md's
`SCOURGIFY_SCRIPT="w,s,n,q"` example and its "defaults are apply / full run" sentence,
`docs/USERGUIDE.md:85-89` (a literal engine-picker transcript, which also shows 3 engines where
there are now 5), and `wizard.py:180`'s in-code hint string.

## Rejected: positional windows

The first draft made `--last` into `--window`, a 1-indexed window over newest-added-first, wired
into `classify`, `staleness`, and `wrangle apply`. Adversarial review against the live library
killed it. The arithmetic was sound — disjointness and union verified exhaustively over all 7,949
books at seven chunk sizes — but:

- **It disabled a safety guardrail.** `tag_loss_guard` fires on `lost > max(200, 25%)`, an
  **absolute** floor. At the measured 3.90 tags/book, a 50-book window holds ~195 assignments, so a
  100% wipe of it loses ~195 — under the floor. The guard is unreachable below ~51 books, and the
  design's own UI defaulted to 50. An over-broad `junk.txt` rule that `apply` correctly aborts
  would sail through 160 looped chunks, each printing a reassuring `SAFETY` line.
- **Deletions lost books silently and permanently.** Positions shift up past a cursor that never
  goes back; the books are stamped and unchanged, so `--incremental` cannot recover them. Simulated
  at 5 deletions/chunk: 187 books never processed, with no signal. The library's 1,965 missing IDs
  show deletions are real here.
- **Its correctness test was a tautology** — "consecutive windows are disjoint and their union is
  the library" can only be tested on a static snapshot, the one condition under which it cannot
  fail. The hazard was concurrent mutation.
- **`--batch`/`--limit` silently truncated a window**, producing a false completion record.
- Windowing `wrangle apply` and `staleness` had no motivation anyway: both are deterministic, free,
  and finish in seconds. The whole cost argument is classify's.

Positions are the wrong key for a durable cursor. `--books` stays ID-based for exactly this reason;
the sweep now uses IDs too, via proposal membership, so the tool has one identity model instead of
two.

## Out of scope

- `--window` in any form
- Windowing `staleness` or `wrangle apply`
- A sort-key choice — added date, matching every existing picker
- Making the `#wrangled` stamp distinguish "classified" from "first-run backfill". It is worth
  knowing that it cannot (281 books are stamped but unclassified), but proposal membership answers
  the question without a migration.
