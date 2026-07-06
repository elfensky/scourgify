# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

scourgify normalizes a [FanFicFare](https://github.com/JimmXinu/FanFicFare)-imported
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
uv run scourgify                                     # no args = the interactive wizard (rich required; TTY only)
uv run scourgify setup                               # interactive health check + setup (FanFicFare, columns, config)
uv run scourgify audit                               # read-only dry-run of every pass
uv run scourgify apply --apply                       # write changes (Calibre CLOSED for the write step)
```

(`uv run scourgify` from a checkout; an installed copy — `pipx install scourgify` — drops the `uv run`.)

**`wizard.py`** (launched by bare `scourgify`) is a **linear guided lifecycle — no menu**: header
(books, column health, new/changed count via `select.changed`, pending proposal, Calibre-open
warning) → setup if columns/config are missing → then the four stages in order, **wrangle →
staleness → classify → review**, each dry-running first, showing its report, and asking before
writing (a clean stage auto-skips). There is no separate audit step — the wrangle stage's dry run IS
the audit; `scourgify audit` stays for the full per-value detail. The classify stage auto-targets
new/changed books only, shows per-engine cost estimates (`classify.est_cost`, list prices in
`classify.PRICING`), offers an engine **bake-off** (`classify.bakeoff`: the same ~5 sample books
through every usable engine, display-only), and enables `--text-fallback` so thin descriptions get
sampled rather than dropped. The review stage offers apply / keep / discard (discard archives to
`*_discarded_*.csv`). Stages call the same engine functions the subcommands do (previews → confirm →
write), so guardrails and auto-backup apply identically; guardrail `SystemExit`s skip the stage, not
the run. `ui.py` holds the shared rich Console + prompt helpers (lintle `term.py` pattern). classify
runs render a live dashboard (`classify._Dashboard`: progress, tagged/failed/rate, throughput
sparkline, rising candidates).

**`select.py`** — the one owner of "which books does this run operate on"; classify's scope flags and
the wizard header both go through it, so they can never disagree. A book is new/changed iff unstamped
∨ `#updated` > stamp ∨ added-date (`books.timestamp`) > stamp — the added-date clock catches re-fetches
(FanFicFare bumps it) while staying immune to scourgify's own writes (`last_modified` is deliberately
NOT used). All pickers return newest-added-first.

**Packaging.** The code is a proper installable package under `src/scourgify/` (hatchling; on PyPI as
`scourgify`). The single `scourgify` console command (`cli.py`) dispatches argv to the tools: bare → wizard,
`setup`/`audit`/`apply` → wrangle, `classify`, `staleness`. Bundled `defaults/` (and `_writer.py`, `afm.swift`)
ship **inside** the package (read-only at runtime); per-user `config.toml`, `overrides/`, and `data/` resolve
against the **current working directory** — so `uv run` from the repo (CWD = repo root) behaves exactly as
before, while an installed copy writes proposals under wherever it's invoked. `common.HERE` is the package
dir (use it only for shipped read-only files); anything user-writable keys off `os.getcwd()`.

**Everything runs under normal CPython** — the installed `scourgify` command, `uv run scourgify`, or plain
`python3` with rich installed. The core operating rule is about *reads vs writes*, not which interpreter:
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
pure core — `transform`, trope-chain resolution, `parse_resp`, the TOML reader — and
`uv run tests/test_selection.py` pins the selection semantics against a throwaway sqlite `metadata.db` built
by `tests/fixture_db.py` (covers both custom-column storage shapes). CI runs both. `scourgify audit` remains the
against-your-library check: full new state, before/after counts, and SAFETY lines asserting **no book loses its
last fandom or character** plus a **tag mass-deletion guardrail** (`apply` aborts if tags would shrink >25% and
>200 assignments — the signature of an over-broad junk rule; `--force` overrides).

## Maintenance loop (after new FanFicFare downloads)

**Order matters: deterministic cleanup (wrangle) FIRST, content tagging (classify) second** — raw
junk tags inflate a book's tag count and would hide it from the classifier's sparse-book targeting.

```
FFF fetch → uv run scourgify apply --apply           # 1. junk-drop/canonicalize the new raw tags (idempotent)
          → uv run scourgify staleness --apply       # 2. free; re-derive #status from #updated age
          → uv run scourgify classify --incremental  # 3. cheap; only new/changed books (see select.py)
          → review data/classify_proposal.csv        # 4.
          → uv run scourgify classify --apply        # 5. Calibre closed (writes shell to calibre-debug)
          → scourgify promote                         # adjudicate new-tag candidates → review → promote --apply
```

(Or the wizard: `uv run scourgify` walks exactly this loop, guided. Targeted redo:
`classify --last 30` / `--since DATE`.)

**⚠️ Cost:** a full Gemini `classify --fresh` pass over the library ≈ **€50** in tokens. Never run `--fresh`
casually — use `--incremental` (only changed/new books), `--batch N`, or `--engine apple` (free, on-device).
Confirm with the user before any full cloud run (classify itself gates cloud runs >200 books behind a
confirmation / `--yes`). **Do NOT bulk re-fetch FFF metadata** — it re-pollutes columns not protected by
`custom_cols_newonly`.

## Architecture

**`wrangle.py` — the unified engine.** Subcommands `audit` / `apply` / `setup`. Loads the data layers
(first to last, later wins): **`defaults/ao3/`** (generated master lists — see below) ← `defaults/`
(curated generic taste) ← `config.toml` (column map + behavior toggles) ← `overrides/` (per-user,
**gitignored**, same file formats, survives pip upgrades). `load_maps()` builds the in-memory maps
(fandom and trope chains are flattened, so a curated re-point of a generated master cascades);
`transform()`
is the per-book core: fandom alias→canonical, character folding (global + fandom-scoped), genre
split→canon→route, tag junk-drop / trope-route / redundancy-strip. Strips a redundant tag only when the
concept already lives in that book's structured column (**backfill-before-strip**).

**The FFF→Calibre column model** (see README "FanFicFare → Calibre columns"): `category`→`#fandoms`,
`characters`→`#characters`, `ships`→`#relationships`, `genre`→`#genres`, `status`→`#status`, real
`series`→builtin Series. Two gotchas the tool exists to fix: `include_in_series:category` stuffing fandoms
into the numbered Series field, and aggressive franchise unification (e.g. all Fate/Nasuverse → `Type-Moon`).

**`classify.py` — content-based tagging** (separate from the deterministic engine; uses an LLM). Two outputs
per book: `added_tags` (chosen from the controlled vocab — bundled `defaults/classify_vocab.txt` merged with
the user's `overrides/classify_vocab.txt` via `load_vocab()`, lazily, so installed copies stay overridable →
applied) and `proposed_new` (novel candidates → aggregated to `classify_newtags_ranked.csv` for
review→promotion, so the vocab grows without freeform noise). Engines `--engine apple|claude|openai|gemini`
(keys via env: `ANTHROPIC_/OPENAI_/GEMINI_API_KEY`); `apple` = on-device, free, single-threaded. Concurrency
via `ThreadPoolExecutor` (`--workers`), retry/backoff, incremental save + resume. `--text-fallback` samples
the book's own prose (EPUB via zipfile, other formats via `ebook-convert`) when the description (Calibre's
built-in `comments` table) is too thin. Scope flags (`--incremental` / `--last N` / `--since DATE`) go through
`select.pick` and select ONLY their books; the sparse-book default (`< --min-tags`) applies only with no scope
flag. `--apply` auto-creates the **`#wrangled`** datetime marker and stamps **every processed book** (a no-tag
book left unstamped would be re-sent to the LLM forever) — state lives in the library, no external file.
Proposals/outputs live in `data/` (gitignored); `--apply` archives the proposal to
`classify_proposal_applied_<ts>.csv` so stale rows never re-add hand-removed tags. `est_cost`/`PRICING` hold
the public list prices behind the wizard's per-engine estimates; `bakeoff()` is the sample comparison. **`promote.py`** reuses classify's engines/`ask_retry`/`existing_terms` to adjudicate `proposed_new` (advocate→skeptic, `--verify-with` for cross-model, human review is the referee), writes `data/promote_review.csv`, and `--apply` folds into `overrides/` + feeds `parse_resp`'s alias snap.

**`staleness.py`** — re-derives `#status` for the activity family {In-Progress, Hiatus, Abandoned} from
`#updated` age (`<2y`→In-Progress, `2–5y`→Hiatus, `≥5y`→Abandoned); idempotent + self-correcting on re-run.
Completed/Dropped/Rewritten and date-less books are never touched.

**`defaults/ao3/` — the generated master taxonomy** (universes/tags/characters/genres as `master,name,rel`
pair rows; ~150k rows, ~7MB, ships in the wheel). Built by **`build_ao3_layer.py`** from the OTW
["Selective data dump for fan statisticians"](https://archiveofourown.org/admin_posts/18804) (2021-02-26):
mechanical extraction of canonical+merger pairs, then an LLM batch workflow clusters fandoms
one-universe-per-franchise (Haiku bulk → Sonnet adversarial verify → Opus referee;
`--assemble <result.json>` combines the verdict-gated decisions into `universes.csv`). NEVER hand-edit
these files — regeneration overwrites them; hand decisions go in curated `defaults/` (re-points cascade)
or `defaults/ao3_exceptions.txt` (pairs excluded from generation, with reasons — e.g. AO3 warning-shadow
mergers that don't transfer to a Calibre library, since Calibre has no warnings field). Policy: **adapt
AO3 everywhere except franchise unification** — curated/override rows that merely fight AO3 spellings
get pruned, not kept.

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
  intermediates); `.gitignore` also ignores stray `*.csv` **except** `!src/scourgify/defaults/*.csv`, plus
  `*.db`, `overrides/`, the compiled `afm` binary (`/afm` and `src/scourgify/afm`), and build artifacts
  (`/dist/`, `*.egg-info/`). Only the generic `defaults/` ship (bundled inside the package).
- **`attic/`** holds the original single-purpose pipeline (`apply_*.py`, `generate_*.py`, `dryrun.py`,
  `recover_xianxia.py`), kept as provenance — see `attic/README.md`. `scourgify` supersedes it; the attic
  scripts read CSVs from their own directory and predate the auto-backup, so prefer the live tools.
- `src/scourgify/afm.swift` is the Apple Foundation Models bridge for `scourgify classify --engine apple`;
  it ships in the package (a `swift` toolchain runs it as-is), or build the faster binary with
  `swiftc -O src/scourgify/afm.swift -o src/scourgify/afm` (requires macOS 26+ / Apple Intelligence).
- **Publishing is automated** via `.github/workflows/publish.yml` (PyPI/TestPyPI **Trusted Publishing** —
  OIDC, no stored tokens): a push to `main` touching `src/**`/`pyproject.toml` auto-publishes to **TestPyPI**;
  production **PyPI** is a manual `gh workflow run publish.yml -f target=pypi`. Local dry-run before a layout
  change: `uv build` then `unzip -l dist/*.whl` — the wheel must contain `scourgify/defaults/*`, `_writer.py`,
  `afm.swift` and **not** `data/`, `overrides/`, or the `afm` binary. Full release flow: **Branching & releases** below.

## Branching & releases

Git-flow-lite (mirrors the sibling `lintle` repo):
- **`develop`** — the integration branch and your everyday working branch. All work (features, fixes, docs, vocab)
  lands here via PR; CI (`ci.yml` — tests on Python 3.10 + 3.13) runs on every push/PR to `develop` or `main`.
  **`develop` history stays LINEAR** — land feature PRs with `gh pr merge --rebase` (or `git merge --ff-only`),
  never a merge commit: history should read as if the commits were made on `develop` directly. Merge commits
  are reserved for Release PRs into `main` (below), where the 2nd-parent arc is the point.
- **`main`** — release-only and **branch-protected**: PRs required (0 approvals, so you self-merge), both CI
  checks must pass, no force-push/deletion, **enforced for admins** — i.e. *no direct pushes, even for the owner*.
  Its **first-parent history is exactly one `Release vX.Y.Z` commit per release**; each is a `--no-ff` merge of
  `develop`, so the merge's 2nd parent arcs back to the exact develop commit it was cut from (visible in any git
  GUI). `git log --first-parent main` is the clean release ledger; full `git log main` still reaches every commit
  via those 2nd parents — nothing is lost.
- **GitHub default branch is `main`** (so the repo homepage shows the released state). GitHub therefore defaults a
  new PR's base to `main` — **open feature PRs against `develop`**; only release PRs target `main`.

**Cut a release** (all from `develop`; `main` is only ever reached through a PR merge):
1. Bump `__version__` in `src/scourgify/__init__.py` — versions are immutable on PyPI, always bump. Commit + push `develop`.
2. `gh pr create --base main --head develop --title "Release vX.Y.Z"`; let CI pass, then
   `gh pr merge --merge --subject "Release vX.Y.Z"` (a **merge commit** — not squash/rebase; the 2nd-parent arc is
   the point). The merge lands on `main` → `publish.yml` auto-publishes to **TestPyPI**.
3. Tag it: `git fetch origin main && git tag -a vX.Y.Z origin/main -m "scourgify X.Y.Z" && git push origin vX.Y.Z`
   (annotated, on the Release commit; tags aren't branch-protected).
4. Promote to **PyPI**: `gh workflow run publish.yml -f target=pypi`, then
   `gh release create vX.Y.Z --title "scourgify X.Y.Z" --notes …`.

`main` was migrated to this shape once via a `git commit-tree` snapshot (tree = the released 1.0.0; parents =
[repo root, develop tip]); `develop` kept the full granular history. To temporarily bypass protection for an
emergency fix, edit the rule at *Settings → Branches* (or `gh api -X DELETE …/branches/main/protection`).
