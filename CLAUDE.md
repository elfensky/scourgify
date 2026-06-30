# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

calibre-wrangler normalizes a [FanFicFare](https://github.com/JimmXinu/FanFicFare)-imported
[Calibre](https://calibre-ebook.com) library — consolidating tags, fandoms, characters, relationships,
genres, and status. It is data-driven (bundled `defaults/` + per-user `overrides/` + `config.toml`),
audit-first, and reversible. Pure Python stdlib + Calibre's own CLI; no test suite. `rich` is an **optional**
dependency (progress bars + tables in `audit`/`classify`): imported under `try/except`, so it's present in the
system-`python3` paths but absent under `calibre-debug` (Calibre's bundled Python has empty site-packages) — every
rich use must have a plain fallback. Never make rich a hard import.

## Running it

Everything keys off `CALIBRE_LIBRARY` (the folder containing `metadata.db`):

```bash
export CALIBRE_LIBRARY="$HOME/Calibre/fanfiction"
python3 wrangle.py audit                              # read-only dry-run report — safe while Calibre is OPEN
calibre-debug -e wrangle.py -- setup                  # first-run wizard: detect/create columns + write config.toml
calibre-debug -e wrangle.py -- apply                  # pre-apply (no write); add `--apply` to actually write
```

**Two execution modes — this distinction is the core operating rule:**
- **Read-only (audit / proposal generation):** plain `python3 <script>.py`, opens `metadata.db` via
  `sqlite3 ... mode=ro`. Fine while Calibre is open. This is how `audit` and `classify.py` (proposal phase) run.
- **Writes (`--apply`, `setup`):** `calibre-debug -e <script>.py -- <args>` — uses Calibre's Python API.
  **Calibre MUST be closed** (it locks the DB) and the library must be fully downloaded (it's typically on
  iCloud Drive). Always `cp "$CALIBRE_LIBRARY/metadata.db" /tmp/ff_$(date +%s).db` first. Master rollback =
  the user's full "Export all Calibre data" backup.

There is no test framework. `wrangle.py audit` *is* the verification step: it computes the full new state
and prints before/after counts + a SAFETY line asserting **no book loses its last fandom or character**
(`apply` aborts if that fails). Scripts also carry small inline `python3 -c` self-checks.

## Maintenance loop (after new FanFicFare downloads)

```
FFF fetch → calibre-debug -e staleness.py -- --apply        # free; re-derive #status from #updated age
          → python3 classify.py --incremental                # cheap; only books changed since last wrangle
          → review classify_proposal.csv
          → calibre-debug -e classify.py -- --apply           # Calibre closed
```

**⚠️ Cost:** a full Gemini `classify --fresh` pass over the library ≈ **€50** in tokens. Never run `--fresh`
casually — use `--incremental` (only changed/new books), `--batch N`, or `--engine apple` (free, on-device).
Confirm with the user before any full cloud run. **Do NOT bulk re-fetch FFF metadata** — it re-pollutes
columns not protected by `custom_cols_newonly`.

## Architecture

**`wrangle.py` — the unified engine.** Subcommands `audit` / `apply` / `setup`. Loads three layers:
`defaults/` (generic, shipped) ← `config.toml` (column map + behavior toggles) ← `overrides/` (per-user,
**gitignored**, same file formats, wins on conflict). `load_maps()` builds the in-memory maps; `transform()`
is the per-book core: fandom alias→canonical, character folding (global + fandom-scoped), genre
split→canon→route, tag junk-drop / trope-route / redundancy-strip. Strips a redundant tag only when the
concept already lives in that book's structured column (**backfill-before-strip**).

**The FFF→Calibre column model** (see README "FanFicFare → Calibre columns"): `category`→`#fandoms`,
`characters`→`#characters`, `ships`→`#relationships`, `genre`→`#genres`, `status`→`#status`, real
`series`→builtin Series. Two gotchas the tool exists to fix: `include_in_series:category` stuffing fandoms
into the numbered Series field, and aggressive franchise unification (e.g. all Fate/Nasuverse → `Type-Moon`).

**`classify.py` — content-based tagging** (separate from the deterministic engine; uses an LLM). Two outputs
per book: `added_tags` (chosen from the controlled vocab `defaults/classify_vocab.txt` → applied) and
`proposed_new` (novel candidates → aggregated to `classify_newtags_ranked.csv` for review→promotion, so the
vocab grows without freeform noise). Engines `--engine apple|claude|openai|gemini` (keys via env:
`ANTHROPIC_/OPENAI_/GEMINI_API_KEY`); `apple` = on-device, free, single-threaded. Concurrency via
`ThreadPoolExecutor` (`--workers`), retry/backoff, incremental save + resume. `--text-fallback` samples the
book's own prose (EPUB via zipfile, other formats via `ebook-convert`) when the `#comments` description is
too thin. `--incremental` re-tags only books whose `#updated` is newer than their per-book **`#wrangled`**
datetime marker (auto-created and stamped on `--apply`) — state lives in the library, no external file.

**`staleness.py`** — re-derives `#status` for the activity family {In-Progress, Hiatus, Abandoned} from
`#updated` age (`<2y`→In-Progress, `2–5y`→Hiatus, `≥5y`→Abandoned); idempotent + self-correcting on re-run.
Completed/Dropped/Rewritten and date-less books are never touched.

**`build_defaults.py`** — maintainer tool: regenerates `defaults/` from the source library's gitignored
review-map CSVs. Curated cross-library knowledge (e.g. franchise unification) lives in its `CURATED_FAN`.

## Gotchas worth knowing before editing

- **Column creation needs the legacy DB object**, then a reopen: `DB(LIB).create_custom_column(...)` →
  re-instantiate `DB(LIB).new_api` before the new column is usable in the same process. `Cache` has no
  `all_field_keys` — use `api.field_metadata.all_field_keys()`.
- **Single-value columns may use a link table.** Read a custom column by detecting
  `books_custom_column_{id}_link`; fall back to the `book` column in `custom_column_{id}` if absent.
- **`tropes.csv` is parsed leniently** (`read_tropes` + `resolve_trope_chains` in `wrangle.py`):
  delimiter-sniffed (`,` or `;`), positional columns, unknown route → `tag` (so freeform notes don't crash),
  and variant→canonical chains/cycles are resolved to a terminal at load. Hand-editing it is expected.
- **Gemini hard-blocks ~1% of extreme content** as `PROHIBITED_CONTENT` (non-configurable; `safetySettings`
  only relaxes the 4 HARM categories). It's deterministic — recover those books with `--engine openai` or
  `--engine apple`. `classify.py` logs failures to `classify_failures.csv`.
- **No `tomllib`** under `calibre-debug`'s Python — `wrangle.py` ships a minimal TOML reader (quote-aware so
  values can contain `#`; tolerates trailing comments on section headers).

## Repo conventions

- **Personal library data is gitignored**: `.gitignore` ignores `*.csv` **except** `!defaults/*.csv`, plus
  `*.db`, `overrides/`, `classify_*.csv`, the compiled `/afm` binary. Only the generic `defaults/` ship.
- **`apply_*.py`, `generate_*.py`, `dryrun.py`, `recover_xianxia.py`** are the original single-purpose
  pipeline, kept as provenance. `wrangle.py` supersedes most; `apply_fff_config.py` (FFF config fix) and
  `apply_relationships.py` (ship rebuild) remain useful standalone.
- `afm.swift` is the Apple Foundation Models bridge for `classify.py --engine apple`; build with
  `swiftc -O afm.swift -o afm` (requires macOS 26+ / Apple Intelligence).
