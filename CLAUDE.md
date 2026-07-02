# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

calibre-wrangler normalizes a [FanFicFare](https://github.com/JimmXinu/FanFicFare)-imported
[Calibre](https://calibre-ebook.com) library — consolidating tags, fandoms, characters, relationships,
genres, and status. It is data-driven (bundled `defaults/` + per-user `overrides/` + `config.toml`),
audit-first, and reversible. Python stdlib + Calibre's own CLI + `rich`; tests in `tests/` (plain asserts,
no framework needed). **rich dependency rules by surface:** `wizard.py`/`ui.py` may hard-import rich (the
wizard is rich-first; `ui.py` raises a friendly install hint if missing). The core tools
(`wrangle`/`classify`/`staleness`) import rich under `try/except` and every rich use there needs a plain
fallback (scripting/CI without rich must keep working). `_writer.py` runs under `calibre-debug` (Calibre's
bundled Python has empty site-packages) — never import rich (or `ui`/`wizard`) there.

## Running it

Everything keys off `CALIBRE_LIBRARY` (the folder containing `metadata.db`):

```bash
export CALIBRE_LIBRARY="$HOME/Calibre/fanfiction"
uv run wrangle.py                                    # no args = the interactive wizard (rich required; TTY only)
uv run wrangle.py setup                              # interactive health check + setup (FanFicFare, columns, config)
uv run wrangle.py audit                              # read-only dry-run of every pass
uv run wrangle.py apply --apply                      # write changes (Calibre CLOSED for the write step)
```

**`wizard.py`** (launched by bare `wrangle.py`, or directly) is a guiding menu wizard: on launch it
detects an un-set-up library (missing columns / no config.toml) and routes to setup; the header shows
books, column health, new/changed-since-last-run count, pending proposal, Calibre-open warning; the
menu default adapts via `recommend()` (setup → review-pending → maintenance → audit). Item 0
(`act_flow`) is a guided maintenance run sequencing wrangle → staleness → classify → review with
per-step explanations and skips. It calls the same engine functions the subcommands do (previews →
confirm → write), so guardrails and auto-backup apply identically; guardrail `SystemExit`s return to
the menu. `ui.py` holds the shared rich Console + prompt helpers (lintle `term.py` pattern). classify
runs render a live dashboard (`classify._Dashboard`: progress, tagged/failed/rate, throughput
sparkline, rising candidates).

**Everything runs under normal CPython** — `uv run` (pyproject manages the venv + rich; `[tool.uv] package
= false`, this is a scripts repo, not an installable package) or plain `python3` with rich installed. The
core operating rule is about *reads vs writes*, not which interpreter:
- **Reads** (audit, classify proposal, setup health check) — read-only `sqlite3 ... mode=ro`; fine while Calibre is open.
- **Writes** — the standalone tool computes the change-set, serializes it to JSON, and shells out **once** to
  `calibre-debug -e _writer.py -- ops.json` (Calibre's API is the only fast batch-write path; `calibredb set_metadata`
  is one book per process). `run_writer()` (in **common.py**; imported by wrangle/classify/staleness) does this,
  **automatically snapshots metadata.db to `/tmp/ff_<ts>.db` first** (prints the path — that's the rollback), and
  **refuses to run while Calibre is open** (it locks the DB). The user never types `calibre-debug`. Master rollback =
  the full "Export all Calibre data" backup.
- **`_writer.py`** is the only file that imports Calibre — a generic ops executor (`create_column` / `set_field` /
  `stamp_now` / `set_pref`).
- **`common.py`** is the shared core: lazy `CALIBRE_LIBRARY` resolution (importing any module never exits),
  `ro_connect()`, link-table-aware `read_custom_column()`, `norm`/`ascii_fold`, the minimal TOML `load_config()`,
  and `run_writer()`. Don't re-implement any of these in a tool script.

Verification: `uv run tests/test_core.py` (plain asserts, pytest-compatible, no library/network needed) pins the
pure core — `transform`, trope-chain resolution, `parse_resp`, the TOML reader. `wrangle.py audit` remains the
against-your-library check: full new state, before/after counts, and SAFETY lines asserting **no book loses its
last fandom or character** plus a **tag mass-deletion guardrail** (`apply` aborts if tags would shrink >25% and
>200 assignments — the signature of an over-broad junk rule; `--force` overrides).

## Maintenance loop (after new FanFicFare downloads)

**Order matters: deterministic cleanup (wrangle) FIRST, content tagging (classify) second** — raw
junk tags inflate a book's tag count and would hide it from the classifier's sparse-book targeting.

```
FFF fetch → uv run wrangle.py apply --apply      # 1. junk-drop/canonicalize the new raw tags (idempotent)
          → uv run staleness.py --apply          # 2. free; re-derive #status from #updated age
          → uv run classify.py --incremental     # 3. cheap; only books changed since last wrangle
          → review data/classify_proposal.csv    # 4.
          → uv run classify.py --apply           # 5. Calibre closed (writes shell to calibre-debug)
```

(Or the wizard: `uv run wrangle.py` → menu 3 → 4 → 5 → 6.)

**⚠️ Cost:** a full Gemini `classify --fresh` pass over the library ≈ **€50** in tokens. Never run `--fresh`
casually — use `--incremental` (only changed/new books), `--batch N`, or `--engine apple` (free, on-device).
Confirm with the user before any full cloud run (classify itself gates cloud runs >200 books behind a
confirmation / `--yes`). **Do NOT bulk re-fetch FFF metadata** — it re-pollutes columns not protected by
`custom_cols_newonly`.

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
Proposals/outputs live in `data/` (gitignored); `--apply` archives the proposal to
`classify_proposal_applied_<ts>.csv` so stale rows never re-add hand-removed tags.

**`staleness.py`** — re-derives `#status` for the activity family {In-Progress, Hiatus, Abandoned} from
`#updated` age (`<2y`→In-Progress, `2–5y`→Hiatus, `≥5y`→Abandoned); idempotent + self-correcting on re-run.
Completed/Dropped/Rewritten and date-less books are never touched.

**`build_defaults.py`** — maintainer tool: regenerates `defaults/` from the source library's gitignored
review-map CSVs (in `data/`). Curated cross-library knowledge (e.g. franchise unification) lives in its `CURATED_FAN`.

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
- **No `tomllib`** under `calibre-debug`'s Python — `common.py` ships a minimal TOML reader (quote-aware so
  values can contain `#`; tolerates trailing comments on section headers).

## Repo conventions

- **Personal library data is gitignored and lives in `data/`** (review maps, proposals, cluster
  intermediates); `.gitignore` also ignores stray `*.csv` **except** `!defaults/*.csv`, plus `*.db`,
  `overrides/`, the compiled `/afm` binary. Only the generic `defaults/` ship.
- **`attic/`** holds the original single-purpose pipeline (`apply_*.py`, `generate_*.py`, `dryrun.py`,
  `recover_xianxia.py`), kept as provenance — see `attic/README.md`. `wrangle.py` supersedes it; the attic
  scripts read CSVs from their own directory and predate the auto-backup, so prefer the live tools.
- `afm.swift` is the Apple Foundation Models bridge for `classify.py --engine apple`; build with
  `swiftc -O afm.swift -o afm` (requires macOS 26+ / Apple Intelligence).
