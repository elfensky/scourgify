# 1-by-1 sequential review (`--step`) + rejects→overrides

**Status:** implemented (2026-07-08)
**Date:** 2026-07-06

## Problem

The wizard and the `apply` / `classify` subcommands present changes in aggregate
and write them in one bulk pass. There is no way to walk changed books one at a
time, see each book's proposed changes, and accept or reject them individually.
Two cases motivate this:

- **classify** produces LLM-guessed tags. The AI hallucinates; you want to verify
  per book before writing.
- **wrangle** produces deterministic normalizations. Most are high-confidence, but
  the occasional per-book oddity (a "unique change") deserves a look on incremental
  runs.

## The spine: two reject philosophies

The whole architecture follows one distinction the user drew:

- **A wrangle reject is a bug in the deterministic rules.** It must be *actionable*:
  logged, then turned into a personal override (or, for the maintainer, a master
  rule) so the same wrong change never recurs.
- **A classify reject is filtering an AI hallucination.** There is no rule to fix —
  just log it (so a repeatedly-wrong tag is visible) and drop it from the proposal.

So one review UI feeds two back-ends: wrangle rejects flow to `scourgify overrides`;
classify rejects are log-only.

## Scope

**In scope (this spec):**
- A reusable per-book checklist review primitive.
- `--step` mode for `wrangle apply` (unique changes only) and `classify --apply`.
- A rejects log (`data/rejects.csv`).
- `scourgify overrides` — synthesizes override lines from logged wrangle rejects,
  with an honest "manual" bucket for the kinds that can't be auto-expressed.
- Wizard per-stage offer (`review / apply-all / skip`) + CLI `--step` flag.

**Out of scope (YAGNI / future):**
- `staleness --step` and `promote --step`. The primitive is generic so they can
  adopt it later; promote is already per-candidate and staleness is deterministic
  and free, so neither is worth wiring now.
- A raw-tty / arrow-key TUI. The checklist uses readline-style prompts only.
- Auto-suppressing the structurally-un-overridable reject kinds (junk un-drop,
  redundancy-strip, decompose). These require a new "keep/force" layer in the map
  model and are a separate, deeper change. `overrides` lists them for hand-editing.

## Components

### 1. `ui.checklist()` — the review primitive

A readline-style numbered checklist. Rich for output (`Table`), `Prompt.ask` for
input — no raw-tty, consistent with the existing `ui.menu` / `ui.confirm`.

- Input: a title/subtitle and a list of items, each with a display string. All items
  start **ticked** (pre-accepted).
- Render:
  ```
  #4821  Naruto: The Lost Chronicles
    1 [x] tags        + time-travel
    2 [x] tags        − "chapter 1 up!" (dropped)
    3 [x] #fandoms    Naruto → Naruto (anime)
    toggle #s to reject · ⏎ apply ticked · a all · s skip book · q quit (keep rest)
  ```
- Commands: type item numbers (space/comma separated) to toggle their tick; `⏎`
  applies the currently-ticked items; `a` ticks all and applies; `s` skips the book
  (nothing applied); `q` quits the walk, leaving all remaining books untouched.
- Returns `(accepted_items, rejected_items)` for the book, plus a signal for
  skip/quit. It is a **pure widget** — no stage knowledge, no logging, no writing.
- Lives in `ui.py`.

Because `--step` is inherently interactive and rich-dependent, `wrangle.py` and
`classify.py` **lazy-import `ui` only inside the `--step` branch**, guarded by
`ui.interactive()`. The non-step paths keep their rich-optional behavior, honoring
the "core tools must work without rich" rule. If `--step` is passed in a
non-interactive / rich-less context, fail with a friendly message.

### 2. `wrangle apply --step`

- Scope is **unique changes only**. Mass folds (the same change on `MASS_MIN`+ books)
  auto-apply exactly as today — high-confidence, and keeping them out of the
  checklist keeps it short.
- The dry-run report (`_preview_report`) already separates mass from unique via
  `_classify_edits`. `--step` walks the per-book `unique` edits through
  `ui.checklist()`.
- **Reconstruction (accept only some edits):** start from the full `transform()`
  result `nd` for the book and **invert only the rejected edits**:
  - rejected `rename` (before→after): remove `after`, add `before` back.
  - rejected `drop` (before): re-add `before`.
  - rejected `add` (after): remove `after`.
  - rejected `move` (src→dst): remove from dst, add back to src.
  Then compute, per column, whether the reverted set still differs from the original;
  write only the columns that differ. This "revert-rejected-from-full-result"
  approach is chosen over re-deriving a partial transform because mass edits are
  already baked into `nd` and we only touch the few rejected values — robust and
  simple. (Edge: a simultaneous fold-on-move or a value collision between an accepted
  and rejected edit in the same column is rare and accepted as a known limitation.)
- Accepted books go through the normal `run_writer()` path (auto-backup, refuses
  while Calibre open) — guardrails unchanged. Rejected edits are appended to the
  rejects log with `class=auto|manual` (see §5, computed the same way `overrides`
  classifies them).

### 3. `classify --apply --step`

- Walk each proposal row that has `added_tags`. Each proposed tag is a checklist item
  shown against the book's title + a description snippet (Calibre `comments`, the same
  source classify already reads).
- Accepted tags stay in the row → applied + the book stamped (`#wrangled`), as today.
- Rejected tags are removed from the row and appended to the rejects log with
  `class=ai`.
- **Skip** a book → its row stays pending in the proposal (no write, no stamp), so
  it is re-offered next run. **Quit** → remaining rows stay pending.
- Implementation reuses `apply_proposal()` on the accepted subset; skipped/pending
  rows are written back to the proposal CSV.

### 4. Wizard per-stage offer

- `stage_wrangle`: after the dry run, if there are unique changes, offer a menu
  `[r]eview 1-by-1 · [a]pply all · [s]kip` instead of the current single confirm.
  `r` runs the `--step` walk; `a`/`s` behave as today.
- `stage_review` (classify): add a `[s]tep` choice alongside `apply / keep / discard`
  that runs the per-book walk.
- After a run that produced wrangle rejects, the wizard prints a one-line pointer to
  `scourgify overrides`.

### 5. The rejects log — `data/rejects.csv`

One gitignored CSV (matches the "personal library data lives in `data/`" convention).
Columns:

| column | meaning |
|--------|---------|
| `ts` | ISO timestamp of the reject |
| `stage` | `wrangle` or `classify` |
| `book` | Calibre book id |
| `title` | book title (for human reading) |
| `kind` | `rename` / `move` / `drop` / `add` (wrangle); `add` (classify) |
| `column` | the affected column label |
| `before` | the value removed/renamed-from (or empty for `add`) |
| `after` | the value added/renamed-to (or empty for `drop`) |
| `class` | `auto` (identity-suppressible), `manual` (needs hand edit), or `ai` |

A small append helper lives in `common.py` (stdlib csv; safe to call from
wrangle/classify). This is the "separate list somewhere."

### 6. `scourgify overrides` — rejects → rules

A new subcommand (dispatched in `cli.py`; logic in `wrangle.py`, which already owns
all the map/override-format knowledge). **Dry-run by default; `--apply` writes.** It
only appends to override text/CSV files — no Calibre, no `calibre-debug`.

Flow:
1. Read `data/rejects.csv`, take the `stage=wrangle` rows, dedup identical
   `(kind, column, before, after)`.
2. Classify each:
   - **auto-suppressible** → synthesize the exact identity-override line and group by
     target file:
     | reject | override written |
     |--------|------------------|
     | `#fandoms` rename `X→Y` | `overrides/fandoms.csv`: `X,X` |
     | `#characters` fold `X→Y` | `overrides/characters.csv`: `X,X,` (+ fandom if scoped) |
     | `#genres` canon `X→Y` | `overrides/genres_canon.csv`: `X,X` |
     | `#genres` split `X→…` | `overrides/genres_split.csv`: `X,X` |
     | tag trope-fold `X→Y` | `overrides/tropes.csv`: `X,X,tag` |
     | `#genres → tags` move | add `X` to `overrides/genres_allow.txt` |

     These work because overrides load **last** and `.update()` / union wins, and the
     fandom chain-flattening resolves an identity map (`X→X`) to a no-op. (Verified
     against `load_maps` load order.) Note: suppressing a genre *canon* rename can
     leave the raw value non-allowlisted (→ it would then route to tags); when that is
     the case, `overrides` also offers the matching `genres_allow.txt` line.
   - **manual** (structurally not expressible as an additive override): junk-drop
     un-drop, redundancy-strip, `#fandoms → tags` via blocklist, genre/tag →
     character rescue moves, decompose. **Listed with the reason and the file to
     hand-edit.** These stay in the log.
3. Show a grouped preview (per target file). On `--apply`, append the lines,
   de-duplicating against what the file already contains.
4. `--master` targets `defaults/` instead of `overrides/` — the maintainer path;
   checkout-only, with a printed warning (installed copies have read-only defaults).
5. Consumed (auto) rejects are archived to `data/rejects_applied_<ts>.csv` so they
   don't reappear; `manual` and `ai` rows remain in `data/rejects.csv`.

## Files touched

No new files.

- `ui.py` — add `checklist()`.
- `wrangle.py` — `--step` reconstruction (revert-rejected-from-full-result); the
  `overrides` synthesis + classification; expose per-book unique edits to the walker.
- `classify.py` — `--step` walk over the proposal; write-back of pending rows.
- `wizard.py` — per-stage `review / apply-all / skip` offer; pointer to `overrides`.
- `cli.py` — `--step` flag on `apply` / `classify`; new `overrides` subcommand.
- `common.py` — reject-log append helper.

## Testing

Both new pieces of logic are pure and testable in `tests/test_core.py` (plain
asserts, no library/network):

- **Reconstruction:** given a full `transform()` result and a set of rejected edits,
  the reverted per-column sets are correct for each kind (rename / drop / add / move),
  and unchanged columns are not written.
- **Synthesis:** each auto-suppressible reject kind maps to the correct override line;
  each manual kind is classified `manual` (not silently mis-synthesized).

Round-trip check worth including: applying a synthesized identity override actually
makes `transform()` stop producing that change (load the override, re-transform, the
edit is gone).

## Non-goals restated

The `manual` bucket is deliberate. Some deterministic rejects cannot be expressed as
an override under today's additive map model without a new "keep/force" layer. Rather
than pretend, `overrides` lists them for hand-editing. Making them auto-suppressible
is a separate, larger change.
