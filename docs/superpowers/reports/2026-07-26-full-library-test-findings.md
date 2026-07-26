# scourgify — full-library test run, findings

**Date:** 2026-07-26
**Library:** 7,949 books, real (iCloud Drive), backed up by the owner beforehand
**Engines:** gemini + openai (no `ANTHROPIC_API_KEY` / `MISTRAL_API_KEY` on this machine)
**Scope sample:** 50 book ids, seed 20260726, drawn from the 5,681 books with a description
over 200 chars — `ids50.txt`
**Cost:** well under €1 (50 books × 2 engines, one bake-off-sized probe, 12 promote candidates)

Run alongside the `--books` feature this test motivated (see
`2026-07-26-book-scope-and-full-library-test-design.md`).

---

## Findings

### 1 — `apply --apply` is not a single-pass fixed point · **fixed (partly)**

Running `apply --apply` twice showed the second pass still wanting changes, on a tool whose
own maintenance loop documents that step as *"idempotent"*.

Three distinct causes, found by instrumenting `transform` by hand:

**(a) junk was only ever tested against the source tag, never the fold target.** A mapping
`X → Y` with `Y` in `junk.txt` kept `Y`; `junk.txt` removed it on the *next* run. Same end
state, one pass later. **3,862 real mappings have this shape.** — **FIXED** (`65dc0f3`),
regression test `test_trope_fold_target_that_is_junk_is_dropped_in_one_pass`.

**(b) `route == "drop"` had no branch in the route chain.** The value fell through to
`else: nT.add(canon)` and was re-added as a tag, so every such rule silently did nothing.
**FIXED** (`3699028`) after establishing intent, which took three independent checks:

- `read_tropes` **allowlists** `drop` as one of five valid routes ([wrangle.py:30](../../../src/scourgify/wrangle.py#L30)); anything
  unrecognized is normalized to `tag` right there, so `drop` surviving that filter was deliberate.
- All 43 rules live in the user's own `overrides/tropes.csv`; the shipped defaults have **zero**.
- **No code path writes them** — `promote --apply` has zero overlap with them across all five
  archived review files, and `synth_reject` emits route `tag`. They are hand-written.
- Their content is what one hand-writes to delete something: *"The Company Fucks Everyone"*
  (23×), *"Cunning; resourceful and ambitious"* (11×), `Rewrite`, `Translation`, `Completed`,
  *"Smug As Fuck"*, *"Humor If You Squint"*.

Applied: **91 assignments across 89 books**, every one the unambiguous `X;X;drop` shape (the
ambiguous `rename + drop` rows in the file match no book). Verified afterwards: the named tags
are gone, 7,949 books intact. A `variant,canonical,drop` row drops the variant rather than
renaming it — the route beats its own canonical, pinned by
`test_trope_route_drop_beats_its_own_rename_target`, since renaming instead would resurrect the
value under a different name.

*Process note:* the first draft of this report warned that enabling these would delete "ANBU,
Avatar, Completed" — three of the most legitimate-sounding names out of 25, which misrepresented
a list that is overwhelmingly junk. Reading the full impact set before advising, rather than a
sample of it, would have avoided that.

**(c) fold-then-route chains still take two passes.** A tag folded to `Y` whose *own* trope
entry routes to `#fandoms`/`#characters` is not re-resolved in the same pass —
`resolve_trope_chains` flattens variant chains at load but does not propagate the terminal's
*route*. Observed on book 9922: `House Targaryen (A Song of Ice and Fire)` → tag
`House Targaryen` (pass 1) → `#fandoms` (pass 2). **NOT fixed** — same class of decision as (b).

**Measured convergence: 2 passes, consistently — and after (a) and (b) were both fixed, `apply` reached a true single-pass fixed point on the settled library.** It always converged; it was
never a loop.

### 2 — the redundancy-strip and cross-column routes were invisible to `audit` · **fixed**

`if norm(tt) in homes: continue` stripped a tag with no `note()`, and the `fandom`/`character`
trope routes emitted nothing when `canon == t`. The audit's examples are read from that
decision log, so tags vanished with **nothing at all** explaining them — on a tool whose stated
selling point is being audit-first. Diagnosing finding 1 required instrumenting the engine by
hand precisely because the report could not say where a tag had gone.

Strips are now their own decision kind, reported under a separate heading from junk drops, and
tags routed to `#fandoms`/`#characters` are noted like every other cross-column move.
**FIXED** (`65dc0f3`), regression test
`test_redundancy_strip_and_cross_column_routes_are_explained`.

### 3 — `staleness --books` reported real books as absent · **fixed**

The absent-id note tested membership against the `#status` column instead of the books table,
so every book without a status counted as "not in the library". Against the real library it
reported **10 of 50 ids missing when all 50 existed** — about a fifth of a FanFicFare library
has no `#status`, which matches exactly (6,373 of 7,949).

Informational only — it never affected what was written — but it lied about the user's own ids.
**FIXED** (`26ffceb`), regression test
`test_absent_note_counts_the_library_not_the_status_column`.

### 4 — `classify_failures.csv` is never cleared · **open**

Gemini blocked 7 of 50 books as `PROHIBITED_CONTENT`. Re-running the same 50 through openai —
**the exact recovery CLAUDE.md recommends** — classified all 7 successfully, but the failures
CSV still lists them. A `--fresh` run does not reset it and a success does not clear the book's
row, so the log only ever grows and cannot be used to see what is still outstanding.

### 5 — Gemini's block rate is 14× the documented figure · **observation, not a bug**

CLAUDE.md says Gemini hard-blocks *"~1% of extreme content"*. On this sample it was **7/50 =
14%**. Deterministic and correctly logged; openai recovered every one. Worth correcting the
figure in the docs — a user planning a full-library run should expect to route roughly one book
in seven to a second engine, not one in a hundred.

### 6 — `promote --backfill --apply` gives a misleading error · **open, minor**

`--backfill` writes with `--yes`, not `--apply`. Passing `--apply` routes into the
promote-review branch and fails with *"no review to apply (promote_review.csv not found — run
promote first)"* — after `promote --apply` has just archived that very file. The dry-run's own
hint says `--yes`, so the fix is to make `--backfill --apply` either work or say so.

### 7 — `--books` on `audit` is rejected, on `classify --apply` is rejected · **fixed pre-run**

Both were caught by the final whole-branch review before this run and fixed. Also fixed there:
**`--books ""` failed *open* to the whole library** on a write path (all three tools shared an
`if a.books:` idiom against `default=""`), while `--books " "` correctly selected nothing.
Verified live: every `--books` error path now exits with a clean one-line message and no
traceback.

### 8 — cosmetic: classify's `scope:` line collides with the live dashboard border · **open**

`╰────────╯  scope: 50 book(s) by id -> 50 books` — the scope print lands on the dashboard
panel's closing line.

---

## Engine comparison — same 50 books, identical input

| | gemini | openai |
|---|---|---|
| wall-clock (50 books, 8 workers) | 45 s | **8.9 s** |
| books tagged | 41 | **49** |
| blocked (`PROHIBITED_CONTENT`) | **7** | 0 |
| distinct tags used | **89** | 67 |
| mean tags/book | 5.37 | 5.05 |
| new candidates proposed | 82 | **110** |

**Mean per-book tag overlap (Jaccard): 0.24.** Across the 43 books both engines processed, they
agreed on 81 tags and disagreed on 286. Engine choice materially changes the result — these are
not interchangeable.

**The qualitative difference matters more than the counts.** openai applies `Angst` to **34/50
(68%)** and `Fluff` to **27/50 (54%)** — those are close to defaults rather than judgments.
Gemini's most frequent tag is `Canon Divergence` at 16/43 (37%), and its vocabulary is wider
(89 vs 67 distinct). For this library gemini produces more discriminating tags; openai produces
broader coverage and never blocks. A gemini pass followed by an openai pass over the blocked
remainder gets both.

---

## Verified working

- **Guardrails.** `data_loss_guard` and `tag_loss_guard` both correct; the wipe guard's
  threshold arithmetic checked by hand (89 lost vs a 7,801 trigger — correctly silent).
- **Scoped SAFETY counters** (the point of the `--books` feature): scoping to the 13 books that
  actually changed gave `97 → 180` tag assignments, a **+83 delta exactly matching** the
  library-wide `30902 → 30985`.
- **Write path.** Every proposed tag landed (0 missing of 250+), all 50 books stamped
  `#wrangled`, proposal archived.
- **Auto-backup** fired before every write; `rollback --list` grew each time.
- **Rollback reversibility** — the headline claim: `31207 → rollback → 31200 → rollback →
  31207`. Exact round trip, because the restore snapshots the current db first.
- **promote**, cross-model (gemini advocate, openai skeptic): 12 candidates → 2 aliases, 10
  rejects, folded into `overrides/`, review archived.
- **backfill**: 7 books, verified in the DB afterwards.
- **Plain rendering with `rich` uninstalled** — identical numbers, clean aligned table, no
  traceback.
- **Wizard TUI** — `tests/drive_wizard.py` passes all 11 checks including the
  skip-all-leaves-the-proposal-byte-identical data-loss pin.
- **`promote` with no `ANTHROPIC_API_KEY`** (its default engine) — clean message, no traceback.
- **Library integrity**: 7,949 books and 7,899 comments unchanged throughout; every untouched
  column byte-identical.

## Not covered

- **The Calibre-open write refusal was never exercised.** Both attempts made while Calibre was
  running had empty change-sets, so `run_writer` short-circuited before reaching the check. The
  guard is untested by this run.
- **`--apply --step` / the 1-by-1 reject flow and `rejects.csv`**, and therefore
  `scourgify overrides` (rejects → override lines). The checklist *rendering* is covered by
  `drive_wizard.py`; the reject round trip on real data is not.
- **A live-library PTY wizard driver** (`tests/drive_wizard_live.py`) was planned and not built.
  The wizard's write path is covered only indirectly, via the CLI paths the stages call.
- **The `--force` guardrail override**, inconclusive: the synthetic over-broad junk rule never
  took effect, so no abort was provoked to override. (An empty `overrides/junk.txt` created
  during that attempt was removed; `overrides/` is back to its original file set.)
- `--bakeoff`, `apple` engine, `--since`, `--last`.

## Reproducing

```bash
export SCOURGIFY_TEST_IDS=ids50.txt     # 50 ids, seed 20260726, books with >200-char descriptions
uv run scourgify apply --books @ids50.txt        # scoped dry-run
uv run scourgify classify --books @ids50.txt --engine gemini --text-fallback --fresh
```
