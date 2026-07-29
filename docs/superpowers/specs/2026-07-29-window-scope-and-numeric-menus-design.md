# Window scope + numeric wizard menus

**Date:** 2026-07-29
**Ships as:** 1.6.0 (feature)
**Status:** approved design, not yet implemented

## Problem

Two unrelated complaints from live use, bundled because both land in the wizard.

**1. There is no way to chip through the library.** The classify scope menu offers exactly
`new/changed`, `whole library`, and `skip`. A full cloud pass over 7,949 books is ≈ €50, so
"whole library" is not a button anyone presses casually — and the alternative is nothing. The CLI
has `--last N`, but `--last 50` is `newest[:50]`: re-running it re-picks the *same* 50 books
forever. There is no offset, so there is no way to sweep a library in affordable chunks.

**2. Menu keys are inconsistent.** The landing menu is numbered (`1`–`7`) but every menu below it
is lettered (`a`/`r`/`k`/`d`/`s`/`n`), so the wizard switches alphabets as you descend into it.

## Decisions

| Decision | Choice | Why |
| --- | --- | --- |
| What "N books" selects | A **window** over newest-added-first, 1-indexed inclusive | `--last 50` alone can never advance; a window can |
| `--books` semantics | **Unchanged — book IDs** | Positions shift after every FanFicFare fetch. `--books 2551` must mean that book forever, and `@ids.txt` exists to round-trip IDs out of Calibre. Identity and sweeping are different jobs |
| Ambiguity fix | Rename `--last` → **`--window`** | `--books 10-20` (IDs) vs `--last 10-20` (positions) read identically. The flag name now carries the domain. `--last` stays as a hidden alias |
| Sort key | **Added date** (`books.timestamp`) | Already what every picker uses. `last_modified` is deliberately never used; `#updated` is the fic's site-update date and would disagree with `--incremental` and staleness |
| Old letter keys | **Hard switch to digits** | Explicitly chosen over hidden aliases. Every scripted test and doc example gets updated |
| Which commands get `--window` | `classify`, `staleness`, `wrangle apply` | The three that already take `--books`; one grammar across all of them |

## Part A — `--window`, defined once in `select.py`

`select.py` is the sole owner of "which books does this run operate on" (CLAUDE.md), so the
grammar lives there and every command adapts to it.

```python
def parse_window(spec: str) -> tuple[int, int]:
    """'50' -> (50, 0)  |  '51-100' -> (50, 50)  ->  (n, skip). 1-indexed, inclusive.
    SystemExit on anything unparseable — this is user input, not an internal invariant."""
```

| Spec | `(n, skip)` | Selects |
| --- | --- | --- |
| `50` | `(50, 0)` | 1st–50th newest |
| `1-50` | `(50, 0)` | identical to `50` |
| `51-100` | `(50, 50)` | 51st–100th newest |
| `7000-7050` | `(51, 6999)` | deep into the oldest end |

`pick()` gains one parameter and one line:

```python
def pick(con, mode="incremental", n=0, since="", min_tags=2, ids=None, skip=0):
    ...
    if mode == "last": return newest[skip:skip + n]
```

**Errors** (all `SystemExit`, matching `parse_books`): a non-integer token, a range whose high is
below its low, or a position below 1. A window running past the end of the library is **not** an
error — against 7,949 books, `--window 7900-9000` returns the 50 that exist (positions 7900–7949).

**Back-compat:** `--last` changes from `type=int` to a spec string and is registered as a second
flag onto the same argparse `dest` as `--window`, so the two can never drift. `--last 50`
therefore behaves exactly as it does today; only its ability to take a range is new.

**Precedence is unchanged** — `--books` (explicit IDs) still wins over every other scope flag,
then `--all`, `--incremental`, `--window`, `--since`, then the bare sparse default. Only the
`a.last` branch is renamed.

**Wiring per command** (all three resolve the window to IDs, then reuse the path `--books`
already takes, so no command grows a second scoping mechanism):

- `classify` — a branch in `gather()`, scope label `window 51-100 (by added date)`
- `wrangle apply` — resolve, then `Plan.restrict(ids)`; the read stays library-wide because
  `transform()` needs global context
- `staleness` — resolve, then `compute(books=ids)`

## Part B — the wizard scope menu

```
classify scope
  1  new/changed — 12 books
  2  whole library — 7,949 books · full pass
  3  window of recent books
  4  skip

> 3
which books? newest first, e.g. 50 or 51-100  [50]
> 201-400
→ 200 books, ~$1.40 (openai)
```

Row 3 is always offered (row 1 only when there are changed books, as today). The cost estimate
and spend confirmation run over the resolved window, unchanged — `classify.plan()` is still
resolved once, so the number the user confirms is the number that gets billed.

**One new primitive:** `ui.ask_text(msg, default)`, routed through `common.script_next` like every
other prompt so `SCOURGIFY_SCRIPT` can still drive the whole flow. It is a prompt, not a parser —
the answer goes to `select.parse_window`, and a bad answer re-asks rather than aborting the stage.

`_scope_options` stays the pure, testable half; the new row is part of its return value.

## Part C — digits everywhere except `w` and `q`

| Menu | Now | Becomes |
| --- | --- | --- |
| wrangle apply | `a` `r` `s` | `1` `2` `3` |
| classify scope | `n` `a` `s` | `1` `2` `3` `4` |
| review (stamp-only) | `a` `d` | `1` `2` |
| review (full) | `a` `r` `k` `d` | `1` `2` `3` `4` |
| promote verdicts | `a` `k` `d` | `1` `2` `3` |
| engine picker | `1..N` + `c` | `1..N` + `N+1` |
| landing menu | `w` `1`–`7` `q` | unchanged |

Defaults keep their current *meaning* (apply / full run), so a blank scripted answer is still a
real "yes" — only the key changes, e.g. default `"a"` becomes default `"1"`.

The engine picker's `extra` rows are numbered by `_ask_engine` itself, continuing the engine
numbering, and the digit is mapped back to the caller's symbolic id — so `stage_classify` keeps
comparing against `"c"` and never has to know how many engines exist.

**One deliberate exception: `ui.checklist` keeps `a` / `s` / `q`.** In that widget digits already
mean "toggle item N", so `1` cannot also mean "apply all". `q` is permitted by the rule anyway.

## Testing

- `parse_window` — pure: both spec forms, equivalence of `50` and `1-50`, and each rejection
  (non-integer, inverted range, position < 1, empty)
- windowed `pick` against the fixture db — that consecutive windows are disjoint and their union
  is the whole library, which is the property that makes chunked sweeping correct
- a window running past the end returns the remainder rather than raising
- `--last` still resolves identically to `--window` (alias pinned, so the docs' examples keep working)
- `_scope_options` — the new row is present with and without changed books
- `ui.ask_text` honours the scripting seam and its default
- renumbered flows in `test_wizard_flow.py`, driven by digits

`test_script.py` needs no change: it exercises `ui.menu` generically against its own local
fixture options, not the wizard's real menus.

## Breaking changes

Old letter keys stop working. Updated in the same commit: `test_wizard_flow.py`,
`tests/drive_wizard.py`, CLAUDE.md's `SCOURGIFY_SCRIPT="w,s,n,q"` example and its
"defaults are apply / full maintenance run" sentence, and the README/CLAUDE.md `--last`
references.

## Out of scope

- A sort-key choice (`#updated` vs added date) — one key, matching every existing picker
- `--window` on `audit`, which is deliberately library-wide (its report reads transform's decision
  log, whose tuples carry no book id)
- Persisting "where the last sweep stopped" — the window is stated per run, not remembered
