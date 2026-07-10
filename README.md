# scourgify

[![PyPI](https://img.shields.io/pypi/v/scourgify)](https://pypi.org/project/scourgify/)
[![Python](https://img.shields.io/pypi/pyversions/scourgify)](https://pypi.org/project/scourgify/)
[![CI](https://github.com/elfensky/scourgify/actions/workflows/ci.yml/badge.svg)](https://github.com/elfensky/scourgify/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/pypi/l/scourgify)](https://github.com/elfensky/scourgify/blob/main/LICENSE)

The tag-wrangler / canonizer for your fanfiction library — normalize and consolidate **tags,
fandoms, characters, relationships and genres** in a
[FanFicFare](https://github.com/JimmXinu/FanFicFare)-imported [Calibre](https://calibre-ebook.com)
library. Data-driven from **~1,700 bundled generic defaults**, fully customizable, audit-first and
reversible.

```bash
pipx install scourgify                                # or: uv tool install scourgify  (one dependency: rich)

export CALIBRE_LIBRARY="$HOME/Calibre/fanfiction"     # folder containing metadata.db
scourgify                                             # ← the wizard: the whole lifecycle, guided
```

Requires **Calibre installed** — the tool reads via read-only sqlite and shells out to Calibre's own
`calibre-debug` for writes. From a checkout, `uv run scourgify` (or `uvx --from . scourgify`) runs it
without installing; [uv](https://docs.astral.sh/uv/) handles the environment (one dependency: rich).

**The wizard** (no arguments) is the intended way in. It opens on a **status header** (book count,
column health, how many books are **new/changed**, any pending proposal / new-tag candidates / rejects)
and then **asks what you want to do** — run the whole guided lifecycle, or jump straight to a single
task — with any unfinished work flagged inline on the menu. A single task then offers to continue to its
natural next step, so you can flow onward from wherever you jumped in. On a fresh library it detects
missing columns/config and runs **setup** first. The guided run walks the lifecycle in order — **wrangle →
staleness → classify → review → promote → backfill** — where every stage dry-runs first, shows its
report, and asks *apply-all / review 1-by-1 / skip* before writing. The classify stage targets only
new/changed books, prices each engine for the run (public list prices), runs cloud requests **8-wide**,
and can **compare engines on a 5-book sample** before you commit; runs show a **live dashboard**
(progress + tagged/failed/rate + throughput sparkline + rising tag candidates). Every write previews
first, asks for confirmation, and auto-backs-up `metadata.db`.

**Review 1-by-1** (`--step`) walks the changed books one at a time — a per-item checklist, everything
pre-ticked, untick to reject — for both the wrangle stage's per-book oddities and the classify stage's
AI-guessed tags. A rejected *deterministic* (wrangle) change is a rule bug: it's logged so `scourgify
overrides` can fold it into a personal override that stops it recurring. A rejected *classify* tag is
just an AI miss: dropped and logged, nothing to fix.

Each step is also a plain scriptable subcommand:

```bash
scourgify setup                                      # interactive health check + setup (FanFicFare, columns, config)
scourgify audit                                      # read-only dry-run report of every pass
scourgify apply --apply                              # write changes (Calibre CLOSED for this step)
scourgify apply --step                               # review each changed book 1-by-1 (untick items to reject)
scourgify overrides                                  # turn rejected deterministic changes into personal override rules
```

Everything runs under plain `python3`. The tool reads via read-only sqlite and, for the actual writes,
shells out **once** to `calibre-debug -e _writer.py` (Calibre's API is the only fast batch-write path) —
so any command that writes (`apply --apply`, `setup` creating columns, `classify.py --apply`) needs
Calibre **closed**, and refuses to run while it's open. You never invoke `calibre-debug` yourself.

**`setup` is the first-run wizard + re-runnable health check.** It verifies, with `✓/⚠/✗` status and
`Y/n` prompts (default-yes; `--yes` to auto-accept): the library; that the **FanFicFare plugin is
installed and configured**, flagging + offering to fix the known gotchas (fandom-vs-series mapping,
`include_in_series:category`, unprotected `#genres`); that every needed column exists (`#fandoms`,
`#characters`, `#relationships`, `#genres`, `#status`, plus `#updated` and `#wrangled` for staleness /
incremental classification), creating any that are missing; and writes `config.toml` (preserving your
behavior toggles). Safe to re-run anytime.

**rich** is required for the wizard and powers the live dashboards/tables everywhere else; the plain
subcommands still degrade to text without it (rich is `try/except`-imported in the core tools, so
scripting/CI without rich keeps working, and `_writer.py` under Calibre's bundled Python needs none).

The engine reads, first to last (later wins):
- **`defaults/ao3/`** — the bundled **master taxonomy**, generated from AO3's official
  [tag dump](https://archiveofourown.org/admin_posts/18804): ~15 years of volunteer tag-wrangler
  knowledge as `master,name,rel` pair rows — **universes** (one name per franchise, media splits and
  renamed adaptations folded in: `Game of Thrones (TV)` → `A Song of Ice and Fire`), **tags** (93k
  canonical-spelling folds), **characters** (38k name folds), **genres** (3.5k synonyms). Covers
  *every* fandom above ~10 AO3 uses, not just one library. Machine-generated — never hand-edit.
- **`defaults/`** — curated generic knowledge on top (franchise taste, junk rules, the genre
  allowlist, `ao3_exceptions.txt` for AO3 mergers deliberately not followed). Edit **here** to change
  behavior for everyone; a re-point of a generated master cascades over its whole subtree.
- **`config.toml`** — your column mapping + opinionated behavior toggles (generated by `setup`).
- **`overrides/`** *(optional)* — your own files (same formats) that **win over everything** and
  survive pip upgrades. For personal taste that shouldn't ship.

Data from the [OTW's Selective data dump for fan statisticians](https://archiveofourown.org/admin_posts/18804)
(2021-02-26), released for public reuse — thank you, Tag Wrangling volunteers.

---

## FanFicFare → Calibre columns (how the linking works)

FanFicFare scrapes metadata fields from each story and writes them into Calibre columns. The mapping
lives in the FFF Calibre-plugin config (stored per-library in the `metadata.db` preference
`namespaced:FanFicFarePlugin:settings` → key `custom_cols`). scourgify's `setup` reads that
mapping, and creates any recommended columns you're missing.

**Recommended mapping** (FFF metadata field → Calibre column):

| FanFicFare field | Calibre column | Type | Holds |
|---|---|---|---|
| `category`   | `#fandoms`       | text, multiple | fandom(s) — **map this to `category`, not `series`** (see gotcha) |
| `characters` | `#characters`    | text, multiple | characters |
| `ships`      | `#relationships` | text, multiple | pairings |
| `genre`      | `#genres`        | text, multiple | genres |
| `status`     | `#status`        | text           | In-Progress / Completed / … |
| `series`     | **Series** (built-in) | series    | the real site/AO3 series |
| `numWords`   | `#words`         | int            | word count |
| `numChapters`| `#chapters`      | int            | chapter count |
| `dateUpdated`| `#updated`       | datetime       | last-updated date |
| `storyUrl`   | `#storyurl`      | text           | source URL (also stored as the `url` identifier) |
| *subject tags* | `tags` (built-in) | —            | freeform tags (what this tool normalizes most) |

### ⚠️ The fandom-vs-series gotcha
FanFicFare's `personal.ini` setting **`include_in_series:category`** stuffs the *fandom* into FFF's
`series` field. If your `custom_cols` then maps **`#fandoms ← series`**, two things break: your
**Series** column fills with fandom names (not real series), and **#fandoms** is fed from that
fandom-stuffed series field. Fix:
1. Remove `include_in_series:category` from `personal.ini` → `series` becomes the real (e.g. AO3) series.
2. Map `#fandoms ← category` (the true fandom field).

`wrangle.py setup` detects and offers to fix both, plus the protection below (the original
`attic/apply_fff_config.py` does the same standalone). After that, real series fills in going
forward and fandoms come from `category`.

**Why fandom-as-series is especially bad:** Calibre's **Series** is a *numbered* field — every book
gets a `series_index` (`A Fandom Name [1]`, `[2]`, …). So fandom-as-series doesn't just duplicate the
fandom, it invents a bogus **ordered hierarchy**: dozens of unrelated stories become "book 1, book 2…
of Harry Potter," a sequence that reflects nothing real. Clearing it (see `attic/apply_other.py`) and
mapping `#fandoms ← category` removes the fake ordering; real series (where the index is meaningful,
e.g. a genuine 3-part AO3 series) then populate correctly.

### Franchise unification (fandom granularity)
Related works in one universe (e.g. `Fate/stay night`, `Fate/Zero`, `Fate/Grand Order`) are distinct
titles but one fandom. The bundled `defaults/fandoms.csv` unifies the obvious franchises to a single
canonical — **the Fate/Nasuverse works all map to `Type-Moon`** (the studio/umbrella name the
Nasuverse fandom uses). Prefer an **English title** as canonical wherever one exists (e.g.
`The Saga of Tanya the Evil`, not `Youjo Senki`; `Puella Magi Madoka Magica`, not the romaji). This
is a granularity *preference*: if you'd rather keep `Fate/Zero` separate from `Fate/stay night`,
remove those rows from your `overrides/fandoms.csv` (or leave them unmapped). Note some franchises
should **stay split** — Disney works are mostly standalone worlds (keep `DuckTales`, don't fold to a
`Disney` mega-fandom), and `Overlord (Game)` vs `Overlord (Anime)` are unrelated. Curated
unifications live in `build_defaults.py`'s `CURATED_FAN`.

### Protecting your cleanup from re-pollution
FFF's **`custom_cols_newonly`** (`{column: bool}`) controls overwrite-on-update: when `true`, FFF
only writes that column **if it's empty**, so a metadata refresh won't clobber your normalized
values. Recommended: `newonly:true` for `#genres`; leave `#status` **writable** so FanFicFare refreshes it on fetch (`staleness.py` re-derives the activity inference). The **built-in `tags` column is
never protected** by this — so new downloads/updates re-add raw tag junk, and you re-run
`wrangle.py` to clean it (see *Maintenance*).

---

## Customizing

**`config.toml`** — column map + behavior toggles (all have sane, opinionated defaults):

| Toggle | Default | Effect |
|---|---|---|
| `fold_characters` | `true` | apply abbreviation→full-name defaults |
| `ascii_only_tags` | `true` | transliterate non-ASCII tags to plain ASCII |
| `au_as` / `crossover_as` / `reincarnation_as` / `time_travel_as` | `genre` | put these tropes in `#genres` (`tag` to keep in tags) |
| `fold_ratings` | `false` | fold `Erotica`→`Smut`, `Adult`→`Mature` |
| `keep_categories` | `true` | keep `Multi`/`Gen`/`F/M` tags (`false` drops them) |

**`overrides/`** — drop in `characters.csv`, `fandoms.csv`, `tropes.csv`, `junk.txt`,
`genres_allow.txt`, … (same formats as `defaults/`). Anything here is merged on top of the bundled
defaults and wins on conflicts. This is where *your* preferences live — the code stays generic.

**`defaults/` formats:**
- `characters.csv` — `variant,canonical,fandom` (blank fandom = global; set = homonym-scoped, e.g. `Luke C.` differs in Marvel vs PJO)
- `fandoms.csv` — `alias,canonical`
- `tropes.csv` — `variant,canonical,route` (route = `tag`|`genre`|`character`|`fandom`)
- `genres_split.csv` (`combined,atoms`), `genres_canon.csv` (`variant,canonical`), `genres_allow.txt` (the genre vocabulary)
- `junk.txt` — drop list (plain line = case-insensitive exact; `re:<regex>` = regex)
- `ratings.txt` — content-rating/warning vocabulary

---

## Safety model
`audit` and `apply` compute the full new state in memory and assert **no book loses its last fandom
or character** (backfill-before-strip), aborting without writing if that fails. A second guardrail
aborts if tags would **mass-shrink** (>25% of assignments and >200 lost — the signature of an
over-broad `junk.txt` rule; `--force` overrides after you've checked). A redundant tag is only
stripped when the concept already lives in that book's structured column. `audit` is read-only
(plain `python3`, fine with Calibre open); `apply`/`setup` use the Calibre API (Calibre **closed**).
**Every write automatically snapshots `metadata.db` to `data/backups/ff_<timestamp>.db` first**
(pruned to the last 20) and prints the path — `scourgify rollback` restores the newest, or
`scourgify rollback --list` to pick one; the current db is itself snapshotted before a restore, so a
rollback is reversible too (master rollback = a full "Export all Calibre data" backup).

## Maintenance — after new downloads / updates
New stories arrive **raw** (junky subject tags, unfolded names). **Order matters — deterministic
cleanup first, content tagging second**, because raw junk tags inflate a book's tag count and would
hide it from the classifier's "sparsely tagged" targeting:

```bash
scourgify apply --apply                # 1. wrangle FIRST: junk-drop/canonicalize the new raw tags (idempotent)
scourgify staleness --apply            # 2. free: re-derive #status from #updated age (independent, any time)
scourgify classify --incremental       # 3. cheap: content-tag only new/changed books -> proposal
                                       # 4. review data/classify_proposal.csv
scourgify classify --apply             # 5. apply the reviewed tags + stamp #wrangled
scourgify promote --apply --backfill   # 6. grow the vocab from new-tag candidates, AND apply the
                                       #    promoted/aliased tags back onto the books that suggested them
```

Or just `scourgify` — the wizard runs exactly this loop, guided. Need a specific redo instead?
`scourgify classify --last 30` (the 30 most recently added) or `--since 2026-06-01` (added or
site-updated since a date). Re-running wrangle is always safe — it's idempotent and won't regress
curated genres (it uses the full `genres_allow.txt`).

---

## Content-based tagging — `scourgify classify`
Reads each book's description (`#comments`) and produces **two outputs**: (1) `added_tags` — tags chosen
from the **controlled vocabulary** (`defaults/classify_vocab.txt`), which get applied; and (2) `proposed_new`
— short novel tags *not* in the vocab, aggregated by frequency into `classify_newtags_ranked.csv` so you can
review and **promote** the recurring ones into the vocab. Grows the tag set deliberately, without freeform noise.

```bash
scourgify classify --engine apple --limit 50        # propose -> data/classify_proposal.csv (dry-run, read-only)
scourgify classify --apply                          # add the proposed tags (Calibre CLOSED)
scourgify classify --apply --step                   # review each book's tags 1-by-1 first (untick to reject)
```
- `--engine apple` — on-device **Apple Foundation Models** via `afm.swift` (free, private; macOS 26+,
  Apple Intelligence). Ships as source; a `swift` toolchain runs it as-is, or from a checkout build the
  faster binary once: `swiftc -O src/scourgify/afm.swift -o src/scourgify/afm`. Lower quality — prone to over-tagging,
  so the prompt caps at `--max-tags 6` and dumps (>2× cap) are rejected.
- `--engine claude|openai|gemini|mistral` — cloud APIs (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` /
  `GEMINI_API_KEY` / `MISTRAL_API_KEY`); defaults `claude-haiku-4-5` / `gpt-4o-mini` / `gemini-2.5-flash` /
  `mistral-small-latest`, override with `--model`. Sharper; cheap.
- `--bakeoff` — run a few sample books through every usable engine and print the comparison, then exit
  (writes nothing). The quick "which engine tags my library best?" check before committing to a full run.
- **Scope — which books a run touches** (`select.py` owns this; newest-added-first, so `--batch`/`--limit`
  caps hit the new books first): `--incremental` = only new/changed books; `--last N` = the N most recently
  added; `--since DATE` = added or site-updated on/after DATE; no scope flag = books with `< --min-tags`
  (default 2) tags. Books whose description is too thin (<40 chars) are reported, not silently dropped —
  `--text-fallback` samples the book's own prose for them. Always dry-run until `--apply`.
- The allowed tag set is the hand-curated `defaults/classify_vocab.txt` **∪ a frequency-gated AO3 seed**
  (`defaults/classify_vocab_ao3.txt` — the ~120 highest-use AO3 freeform tropes, generated by
  `build_classify_seed.py` from the OTW dump so the classifier isn't under-tagging out of the box) **plus
  your `overrides/classify_vocab.txt`** (a line appends a term, `-term` removes one — and can trim a
  seeded term) — editable even for a pipx/uv-tool install, where the bundled files live read-only in site-packages.
- Long runs **save incrementally and resume** on re-run (skip books already in the proposal; `--fresh`
  to restart). `--batch N` processes only N new books per run — handy for pacing API spend/rate limits.
  Cloud engines run `--workers` requests concurrently (default 8); `apple` is single-threaded (one
  on-device pipe). A **spend gate** asks for confirmation (or `--yes`) before sending more than 200 books to a cloud engine.
- `--apply --step` reviews the proposal **1-by-1** before writing: each book's proposed tags as a
  checklist you untick to reject. Accepted tags are applied and the book stamped; rejected tags are
  dropped and logged (an AI miss, not a rule bug); *skip*/*quit* leave a book pending for a later run.
- Proposals/outputs live in `data/` (gitignored): `classify_proposal.csv`, `classify_newtags_ranked.csv`,
  `classify_failures.csv`. On `--apply` the proposal is **archived** to `classify_proposal_applied_<ts>.csv`,
  so a later apply can never re-add tags you've since hand-removed in Calibre.
- **Incremental maintenance (`--incremental`):** after new FanFicFare downloads, (re)tags only **new/changed**
  books — never classified, `#updated` newer than their own **`#wrangled`** marker, or **re-fetched** (added-date
  newer, which FanFicFare bumps on re-download — catching updates fetched late) — cents instead of a full pass.
  `--apply` auto-creates the marker column and stamps **every processed book** (tagged or not, so no-tag books
  aren't re-sent forever). State lives *in the library* (travels with `metadata.db`, no external file). A full
  cloud `--fresh` run is expensive; reserve it for vocab changes.

### Growing the vocab — `scourgify promote`
After a classify run, novel tag candidates land in `classify_newtags_ranked.csv`. `scourgify promote`
adjudicates each one **adversarially against the master tag list**: an advocate proposes
promote / alias / reject, a skeptic (optionally a *different* engine via `--verify-with openai`) tries
to refute a promote, and the difflib shortlist of nearest master tags grounds both. Verdicts land in
`data/promote_review.csv` for review; `scourgify promote --apply` folds them into `overrides/`
(promotes → vocab, aliases → `tropes.csv` + a snap-map that stops re-proposal). Engines and keys are
exactly classify's (`--engine claude|openai|gemini|mistral|apple`; your own API key in the env; `apple`
is free/on-device). To grow the *shipped* vocab: run `scourgify promote`, review, then `--apply`
(writes to `overrides/classify_vocab.txt`); manually copy the keeper terms into
`src/scourgify/defaults/classify_vocab.txt` and commit that file.

**Close the loop — `promote --backfill`.** The classifier only writes vocab tags; a `proposed_new`
candidate is never written to its book, so promoting/aliasing one grows the vocab but leaves the books
that *suggested* it un-tagged (and they're stamped, so `--incremental` skips them). `scourgify promote
--backfill` fixes that **deterministically, with no API calls**: it reads the book↔`proposed_new` record
kept in the (archived) proposals and the ledger's verdicts, and applies each promoted tag (or an alias's
target) onto exactly the books that first proposed it — union with their current tags, previewed and
confirmed, Calibre closed. Combine with apply as `promote --apply --backfill`, or run it alone anytime to
retro-fix past promotions.

## Custom maps from your library (`overrides/`)
The bundled `defaults/` are generic. Two helper workflows mined library-specific maps into `overrides/`
(gitignored): AO3-style tag clustering (`overrides/tropes.csv`) and fandom **universe-unification**
(`overrides/fandoms.csv`, e.g. `Avengers`/`Captain America (Movies)` → `Marvel`, `Game of Thrones (TV)`
→ `A Song of Ice and Fire`). The engine loads these on top of the defaults automatically.

`scourgify overrides` grows these files the easy way: it reads the deterministic changes you rejected
in `apply --step` (logged to `data/rejects.csv`) and synthesizes the exact identity-override lines that
suppress them — a rejected fandom fold → `fandoms.csv: X,X`, a rejected genre canon → `genres_canon.csv:
X,X`, and so on. Dry-run by default; `--apply` appends the lines (de-duped), `--master` targets the
shipped `defaults/` (maintainer, checkout only). Rejects it can't express as an additive override (junk
un-drops, cross-column rescues) are listed for hand-editing rather than faked.

## Repo layout
The package lives in **`src/scourgify/`**; the single `scourgify` command (`cli.py`) dispatches
bare → wizard, `setup`/`audit`/`apply`/`overrides` → wrangle, `classify`, `staleness`, `promote`, and `rollback`.
- **`cli.py`** — the `scourgify` entry point (argv dispatcher over the tools below)
- **`wrangle.py`** — the engine: `setup` / `audit` / `apply`; with no command it launches the wizard
- **`wizard.py` + `ui.py`** — the interactive wizard and its rich terminal helpers (the one
  rich-required surface)
- **`classify.py`** — content-based tagging (LLM engines) · **`staleness.py`** — `#status` re-derivation
- **`common.py`** — shared core: library resolution, read-only sqlite + custom-column reading,
  config, and `run_writer()` (the single write funnel, with automatic backup)
- **`_writer.py`** — the only file that imports Calibre; a generic ops executor invoked under
  `calibre-debug` by `run_writer()`, never by hand
- **`defaults/`** — bundled generic maps, shipped inside the package (read-only at runtime)
- Per-user files resolve against the **working directory**: `config.toml`, `overrides/` (your maps,
  gitignored), and `data/` (proposals/intermediates, gitignored)
- **`build_defaults.py`** (repo root) — maintainer tool: regenerates `defaults/` from the review maps in `data/`
- **`tests/`** — `uv run tests/test_core.py`, no library needed
- **`attic/`** — the original single-purpose pipeline, kept as provenance (see `attic/README.md`);
  `scourgify` supersedes it.

The per-library review-map CSVs in `data/` are gitignored (they contain your library's actual data);
only the generic `defaults/` ship with the repo.

## Development
`develop` is the integration branch (all work + PRs, **rebase-merged so its history stays linear** — no merge
commits; CI runs the tests on Python 3.10 + 3.13). `main` is release-only and branch-protected — one
`Release vX.Y.Z` merge commit per release, each arcing back to the `develop` commit it was cut from.
Publishing is automated on release via **Trusted Publishing** (OIDC, no tokens): a merge to `main` ships to
TestPyPI, a manual workflow dispatch promotes to PyPI. Tests need no Calibre or network:
`uv run tests/test_core.py` and `uv run tests/test_selection.py`. Release steps live in
`CLAUDE.md → Branching & releases`.
