# Calibre fanfiction library — cleanup toolkit

Scripts + review maps used to normalize the FanFicFare-imported library at
`/Users/andrei/Library/Mobile Documents/com~apple~CloudDocs/Calibre/fanfiction`
(tags, genres, fandoms, characters, relationships, status, authors, series, publisher).

All the heavy cleanup has already been applied. Keep this folder as the audit trail + for
re-running if the library drifts (e.g. after a bulk FanFicFare metadata re-fetch).

---

## Golden rules (READ BEFORE RUNNING ANYTHING THAT WRITES)

1. **Quit Calibre first.** Any script that writes opens the library through Calibre's API and will
   conflict/corrupt if the GUI is running. (Read-only generators are fine with it open.)
2. **Two-step every applier.** Run it **without** `--apply` first — it prints what it *would* change
   and runs the safety check, but writes nothing. Review, then re-run with `-- --apply`.
3. **Back up `metadata.db` before each `--apply`:**
   `cp "…/fanfiction/metadata.db" "/tmp/ff_metadata_$(date +%Y%m%d_%H%M).db"`
   (The master rollback is your full "Export all Calibre data" backup.)
4. **Appliers run through the bundled calibre-debug:**
   `/Applications/calibre.app/Contents/MacOS/calibre-debug -e <script>.py -- --apply`
5. **Generators/dry-run are plain Python** (read-only SQLite): `python3 <script>.py` — safe anytime.
6. `OUT` auto-resolves to **this folder** (scripts read/write the CSV maps here). `LIB` is the
   absolute library path, hardcoded in each script — edit it if the library ever moves.

## Safety model
Every applier computes the full new state in memory, asserts **no book loses its last fandom or
character** (`backfill-before-strip`), and aborts without writing if that check fails. "Junk-only"
fandoms (e.g. a book whose only fandom was `NSFW`) are allowed to end empty — that's not data loss.

---

## Scripts

### Generators — read-only, build the editable map CSVs (`python3 …`)
| Script | Produces | Notes |
|---|---|---|
| `generate_maps.py` | `tags_map.csv`, `genres_map.csv`, `fandoms_map.csv`, `characters_map.csv`, `relationships_map.csv`, `authors_map.csv` | First-pass classification of every value. Heuristics + allowlists; edit the `decision` column to override. |
| `generate_followups.py` | `relationships_rebuild.csv`, `tags_surface_dupes.csv`, `_input_chars_abbrev_by_fandom.csv`, `_input_genres_tail.csv` | Mechanical rebuilds + inputs for the knowledge passes. |
| `dryrun.py` | (prints) | Read-only simulation of `apply.py`: before/after distinct counts + the safety invariant. Run this to preview impact. |

### Appliers — write via calibre-debug, Calibre CLOSED (`calibre-debug -e … -- --apply`)
Run order matters (this is the order used). Each reads the maps in this folder.

| Script | Does | Reads |
|---|---|---|
| `apply.py` | Core pass: strip redundant tags (backfill first), move real genres→genres, fold tag dupes, drop junk, backfill fandoms/characters/status, merge authors | `tags_map`, `genres_map`, `fandoms_map`, `characters_map`, `*_surface*`, `authors_map` |
| `apply_relationships.py` | Rebuild `#relationships` from normalized participant names (`Harry P./Hermione G.` → `Harry Potter/Hermione Granger`); keeps `/` vs `&` | character maps |
| `apply_tropes.py` | Fold freeform tags → controlled trope vocab; route some → genres/characters/fandoms; drop noise | `freeform_trope_map.csv` |
| `apply_more.py` | Split combined genres (`Action/Adventure`→`Action,Adventure`) + normalize; non-English tags→English; consolidate fandoms (`Naruto SI`→Naruto); strip symbol-prefixed values | `nonenglish_tags_map.csv`, `fandoms_consolidate_map.csv`, `symbols_map.csv` |
| `apply_asciitags.py` | Final pass: ASCII-fold any remaining non-ASCII tags (smart quotes/accents → plain) | — |
| `apply_char_fixes.py` | Targeted character merges (hardcoded list, e.g. Bellatrix variants → Bellatrix Lestrange) | — |
| `apply_other.py` | Clear the fandom-duplicate Series column; normalize publishers; strip `mobi-asin` identifiers | — |
| `apply_fff_config.py` | **Root-cause fix** — edits the FanFicFare plugin config (see below) | — |

---

## Map CSVs (the reviewable artifacts)
Edit the **`decision`** column; pre-filled with the suggestion, change only what you disagree with.
Common decisions: `keep` / `keep-genres` / `keep-tags`, `strip`, `merge→X`, `→tags` / `→genres` /
`→fandoms` / `→characters` / `→status` / `→relationships`, `fold` (target), `drop`.

Final/canonical maps: `tags_map`, `genres_map`, `fandoms_map`, `characters_map`, `authors_map`,
`relationships_rebuild`, `tags_surface_dupes`, `freeform_trope_map`, `nonenglish_tags_map`,
`fandoms_consolidate_map`, `symbols_map`, `characters_fandom_aware`, `subgenres_proposal`.
`_*.csv` are regenerable intermediates (input lists, shard parts) — ignore/delete freely.

---

## Typical full run (from scratch)
```bash
CD="…/Calibre/@cleanup"; LIB="…/Calibre/fanfiction"; DBG=/Applications/calibre.app/Contents/MacOS/calibre-debug
# 1. generate + review maps (Calibre may be open)
python3 "$CD/generate_maps.py"; python3 "$CD/generate_followups.py"
python3 "$CD/dryrun.py"                       # preview impact
#    …edit decision columns in the CSVs as desired…
# 2. QUIT CALIBRE, then for EACH applier: dry first, back up, apply
cp "$LIB/metadata.db" "/tmp/ff_$(date +%s).db"
"$DBG" -e "$CD/apply.py"                       # pre-apply (no write)
"$DBG" -e "$CD/apply.py" -- --apply           # write
# …repeat for apply_relationships, apply_tropes, apply_more, apply_asciitags, apply_other…
```

---

## FanFicFare config (the root cause — already fixed)
`apply_fff_config.py` corrected the plugin config (stored in the live `metadata.db` pref
`namespaced:FanFicFarePlugin:settings`):
- Removed `include_in_series:category` from `personal.ini` → FFF `series` is now the **real** site
  (AO3) series instead of the fandom.
- Remapped `custom_cols` `#fandoms` ← `category` (was `series`).
- Set `custom_cols_newonly` `#genres` and `#status` → `true` so future updates **don't re-pollute**
  those cleaned columns.

Real series now fills in **going forward** as stories are added/updated. No bulk re-fetch was done
(it was the safe choice).

## Maintenance — after new downloads / updates
New/updated stories arrive **raw** (only the original set was normalized). `#fandoms`/`#characters`/
`#relationships`/`#genres`/`#status` are protected on *existing* books (`newonly:true`), but new books
get raw genres, and the **built-in `tags` column is never protected** — updates re-add junk
(`FanFiction`, status-as-tags, `no beta we die like…`, `cross-posted…`, `…to be added`).

To re-clean, run (Calibre closed, dry first):
```
calibre-debug -e apply_recents.py            # then -- --apply
calibre-debug -e apply_asciitags.py -- --apply
```
`apply_recents.py` has **reusable tag-junk patterns** (drops the meta-noise, strips status-tags) plus
a **specific `GENRE_FIX` list** for misfiled new genres — add new cases to that list as they appear
(`calibre-debug -e apply_recents.py` with no `--apply` shows what it catches).

**Do NOT** re-run the full pipeline (`generate_maps.py` → `apply.py` / `apply_more.py`) for routine
maintenance: those use a smaller genre allowlist and would **regress** the curated genre set
(Xianxia, Mecha, Isekai, sub-genres). Use the targeted `apply_recents.py` instead.
