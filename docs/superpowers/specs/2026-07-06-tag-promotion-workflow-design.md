# Tag-promotion workflow — design

**Date:** 2026-07-06
**Status:** approved (brainstorming) → pending implementation plan
**Repo:** scourgify (branch off `develop`)

## Problem

`classify.py` emits two things per book: `added_tags` (chosen from the controlled vocab, applied)
and `proposed_new` (novel tag candidates the LLM thought applied but that aren't in the vocab).
Candidates aggregate by frequency into `data/classify_newtags_ranked.csv`.

A prior change (commit `6bf8017`) added a **string-similarity** dedup: `annotate_new` uses stdlib
`difflib` to attach `nearest_existing` / `similarity` / `verdict` to each candidate, and
`parse_resp` snaps near-miss spellings to their canonical vocab form. String similarity is
**semantically blind**, though — it scores `Amoral Deity → Morality` at 0.8 (wrong) and
`Post-Apocalyptic → Post-Apocalypse` at only 0.62 (a true synonym it nearly misses).

We want an **adversarial AI pass** that reasons each candidate *semantically* against the master
tag list and decides whether to add it — the reasoning `difflib` can't do.

## Decision

Per candidate, exactly one verdict:

- **promote** — a genuinely new, reusable concept not covered by the vocab or a close master →
  add to the classifier vocab so future books can be tagged with it.
- **alias → X** — semantically the same as an existing master/vocab tag `X` → fold to `X`, don't
  add to vocab.
- **reject** — plot-specific, character-name-ish, or noise → drop; remember the reason so it isn't
  re-adjudicated.

## Master-list access (the crux)

The master list is large (~14k AO3 canonical tags + the small `classify_vocab.txt`); an agent
can't hold it all. **Approach: fuzzy shortlist + semantic reasoning.** For each candidate, reuse
the existing `difflib` retrieval to pull the **top ~15 nearest master tags** (a wide net, not
top-1), then the agent reasons semantically over: the candidate + that shortlist + the example
books that proposed it. The agent's job is precisely to fix `difflib`'s semantic errors within a
generous candidate set.

## Architecture — shared core, two orchestration shells

### Shared reasoning core — `src/scourgify/promote.py` (in-package)

Consumed by both shells so the review format and apply path are identical.

- `candidates()` — read `classify_newtags_ranked.csv` (module const `classify.RANK`); join each
  candidate to the example book titles + descriptions that proposed it (from `classify.PROP`);
  **skip anything already in the decision ledger** (`data/promote_ledger.csv`) so re-runs don't
  re-adjudicate.
- `shortlist(cand)` — reuse `classify.load_ao3_vocab()` / `classify.existing_terms()` and
  `difflib.get_close_matches` (the machinery behind `classify.annotate_new`) to return the top-N
  nearest master tags. N default 15.
- Prompt builders: `advocate_prompt(cand, examples, shortlist, vocab)` and
  `skeptic_prompt(cand, proposed_verdict, shortlist, examples)`.
- Verdict schema (validated): `{verdict: promote|alias|reject, target?: str, reason: str, confidence: low|med|high}`.
- `apply_decisions(review_path, vocab_path, tropes_path)` — fold **promote** → append to
  `vocab_path`; **alias** → append `(candidate, target, tag)` to `tropes_path`; **reject** →
  record only. All three verdicts append to `data/promote_ledger.csv` (the skip-list). Archive the
  applied review to `promote_review_applied_<ts>.csv` (mirrors `classify.apply_proposal`). Runs
  only under `--apply`.

### Adversarial structure (shared logic)

- **Advocate** proposes a verdict from candidate + shortlist + example books.
- **Skeptic** independently tries to *refute promotion*: find a master/vocab tag that already
  subsumes the candidate (→ alias), or argue it's plot-specific/character/noise (→ reject).
  Defaults to skeptical under uncertainty.
- **Reconcile:** skeptic finds a subsumer → **alias**; skeptic says noise → **reject**; skeptic
  fails to refute → **promote**. Advocate/skeptic disagreement on a promote → **referee** (a third
  call / higher tier).

### Shell A — `scourgify promote` (ships; per-user; recurring)

- In-package subcommand. Runs advocate + skeptic (+ referee on disagreement) per candidate via
  classify's existing engine adapters (`classify.ENGINES`, `.ask(prompt)`), reusing the
  ThreadPoolExecutor + retry/backoff pattern currently a **nested closure `ask_retry` at
  `classify.py:407`** — factor that out into a reusable module-level helper (targeted improvement)
  so both classify and promote share it.
- Flags mirror classify: `--engine`, `--batch N`, `--workers`, `--limit`, `--dedup-cutoff`,
  `--yes`, plus `--apply`.
- Dry run writes `data/promote_review.csv` (candidate, verdict, target, reason, confidence, +
  advocate/skeptic notes). `scourgify promote --apply` calls `apply_decisions` targeting the user's
  `overrides/classify_vocab.txt` and `overrides/tropes.csv`. Same dry-run → confirm → write shape
  as the rest of the tool.
- CLI wiring: add a `promote` branch in `cli.py` (alongside `classify` / `staleness`) and a
  `promote.main()`.

### Shell B — maintainer Claude Code workflow (grows the *shipped* vocab)

- A Workflow-tool script (like the franchise-clustering one): Haiku advocates in batches → Sonnet
  skeptic-verifies every promote → Opus referees disagreements. Reads the same `candidates()`
  input, writes the **same `promote_review.csv` format**.
- The identical in-package `apply_decisions` folds that review into the **shipped**
  `src/scourgify/defaults/classify_vocab.txt` and `defaults/tropes.csv`. One review format, one
  apply path, two producers. The workflow script is authored/run in Claude Code (not shipped in the
  wheel); its result JSON is committed for reproducibility, matching `build_ao3_layer.py --assemble`.

## Feedback loop (why it compounds)

Applied **aliases** also feed `classify.parse_resp`: extend its `difflib` snap with a **hard alias
map** loaded from the applied aliases, so the next classify run auto-snaps e.g.
`Post-Apocalyptic → Post-Apocalypse` into an applied vocab tag instead of re-proposing it. Combined
with the reject ledger, the candidate pool **shrinks over runs** instead of resurfacing the same
tags. (Minimal version: aliases land in `tropes.csv` + ledger; the `parse_resp` hard-map is a small
extension in the same change.)

## Data flow

```
classify → classify_proposal.csv + classify_newtags_ranked.csv (difflib-annotated)
         → promote (advocate + skeptic + referee, over difflib shortlist + example books)
         → data/promote_review.csv           # AI verdicts + reasoning (dry run)
         → review / edit by human
         → promote --apply
             promote → vocab file (overrides/ for users; shipped for maintainer)
             alias   → tropes.csv (+ parse_resp hard-map)
             reject  → ledger only
             all     → promote_ledger.csv (skip-list) ; review archived
```

## Files

- **New:** `src/scourgify/promote.py`; `data/promote_review.csv`, `data/promote_ledger.csv`,
  `data/promote_review_applied_<ts>.csv` (all gitignored under `data/`); the maintainer Workflow
  script + its committed result JSON.
- **Changed:** `src/scourgify/cli.py` (dispatch `promote`); `src/scourgify/classify.py` (factor
  `ask_retry` out of `classify_run`; extend `parse_resp` with the applied-alias hard-map);
  `tests/test_core.py` or a new `tests/test_promote.py`.
- **Docs:** README + CLAUDE.md — the promotion step in the classify/vocab-growth loop.

## Testing

Pure-function, plain-assert (no network), matching the existing suites:
- `shortlist` returns expected neighbours from a fixture vocab/master set.
- verdict parsing/validation (well-formed, malformed, missing target on alias).
- `apply_decisions` routing: promote→vocab, alias→tropes, reject→ledger-only; archive written;
  ledger appended for all three.
- `candidates()` skips ledger entries (re-run idempotence).
- `parse_resp` snaps via the applied-alias hard-map.

## Non-goals / YAGNI

- No embedding index (fuzzy shortlist is enough; revisit only if it demonstrably misses).
- No auto-apply without human review (audit-first; `--apply` is always explicit).
- No per-book inline promotion (batch over the aggregated candidate list only).
- No suppression of the LLM's free generation — rejects simply aren't re-adjudicated, not blocked
  at the source.

## Verification

1. All plain-assert suites green (`test_core`, `test_selection`, `test_layers`, new `test_promote`).
2. `scourgify promote` over the real `classify_newtags_ranked.csv` (~96 candidates): produces a
   `promote_review.csv` whose alias/reject calls visibly beat difflib on the known failure cases
   (`Amoral Deity` not aliased to `Morality`; `Post-Apocalyptic` aliased to `Post-Apocalypse`).
3. `promote --apply` folds into `overrides/`, archives the review, appends the ledger; a second
   `promote` run skips the decided candidates.
4. Maintainer Workflow produces the same-format review; in-package `apply_decisions` folds it into
   the shipped vocab.
