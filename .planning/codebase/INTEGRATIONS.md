# External Integrations

**Analysis Date:** 2026-08-28

## APIs & External Services

**LLM Engines:**
- Claude (Anthropic) - Content tagging, new-tag verification
  - SDK/Client: urllib (via `engines.Claude` adapter)
  - Auth: `ANTHROPIC_API_KEY` env var
  - Endpoint: `https://api.anthropic.com/v1/messages`
  - Model: `claude-haiku-4-5-20251001` (default)
  - Traits: Best judge for new-tag candidates; ~1M+5M input+output tokens per book; refusals rare
  - Files: `src/scourgify/engines.py` (Claude class), `src/scourgify/classify.py`, `src/scourgify/promote.py`

- OpenAI (GPT) - Content tagging, cost-sensitive runs
  - SDK/Client: urllib (via `engines.OpenAI` adapter)
  - Auth: `OPENAI_API_KEY` env var
  - Endpoint: `https://api.openai.com/v1/chat/completions`
  - Model: `gpt-4o-mini` (default)
  - Traits: Cheapest usable engine; no content refusals observed
  - Files: `src/scourgify/engines.py` (OpenAI class), `src/scourgify/classify.py`

- Google Gemini - Content tagging, large-scale runs with reasoning
  - SDK/Client: urllib (via `engines.Gemini` adapter)
  - Auth: `GEMINI_API_KEY` or `GOOGLE_API_KEY` env var (falls back to either)
  - Endpoint: `https://generativelanguage.googleapis.com/v1beta/models/[model]:generateContent`
  - Model: `gemini-2.5-flash` (default)
  - Traits: Refuses ~14% mature content (measured); bills ~1,061 hidden reasoning tokens per book (costs ~14x more than headline price)
  - Safety: Custom `safetySettings` to allow mature content (library-specific exemption)
  - Files: `src/scourgify/engines.py` (Gemini class), `src/scourgify/classify.py`

- Mistral - Content tagging, alternative cost-sensitive runs
  - SDK/Client: urllib (via `engines.Mistral` adapter)
  - Auth: `MISTRAL_API_KEY` env var
  - Endpoint: `https://api.mistral.ai/v1/chat/completions`
  - Model: `mistral-small-latest` (default)
  - Traits: Untested on this library; cost profile similar to OpenAI
  - Files: `src/scourgify/engines.py` (Mistral class), `src/scourgify/classify.py`

- Apple Foundation Models (on-device) - Free, single-threaded content tagging
  - SDK/Client: swift subprocess bridge (`afm.swift` executable)
  - Auth: None (local, on-device; requires macOS 26+)
  - Model: System's default language model
  - Traits: Free, single-threaded (~40s/book), weak tagging, cannot judge new tags
  - Launch: Invoked as `swift afm.swift` or pre-built `afm` binary
  - Files: `src/scourgify/afm.swift`, `src/scourgify/engines.py` (Apple class), `src/scourgify/classify.py`

**Pricing & Retry:**
- Centralized in `src/scourgify/engines.py`: `PRICING` dict (list prices as $/MTok), `TRAITS` dict (engine capabilities), `ask_retry()` (backoff policy)
- Retryable errors: QUOTA, TIMEOUT, PARSE, ERROR (HTTP 429/408/504, JSON parse errors)
- Non-retryable: AUTH (401), PERMISSION (403), REFUSAL (content block — only retried on different engine)
- Key masking: `mask(key)` → first 8 + bullets + last 4; `unmask(typed, stored)` to detect edits in settings
- Redaction: `redact(msg, *secrets)` strips keys from all error messages before logging/CSV

## Data Storage

**Databases:**
- Calibre metadata.db (SQLite)
  - Connection: `ro_connect()` opens `file:metadata.db?mode=ro` URI for read-only access
  - Location: `$CALIBRE_LIBRARY/metadata.db` (folder set via `CALIBRE_LIBRARY` env or `common.set_library()`)
  - Read Path: Plain system Python 3 via sqlite3 stdlib (safe while Calibre is open)
  - Write Path: Subprocess to `calibre-debug -e _writer.py -- ops.json` (Calibre's bundled Python; reads/writes via `calibre.library.db`)
  - Files: `src/scourgify/common.py` (ro_connect, db operations), `src/scourgify/_writer.py` (write glue)
  - Custom Columns: Detected via `books_custom_column_{id}_link` table (multi-value) or inline (single-value)
  - Preferences: FanFicFare config blob in `preferences` table at key `namespaced:FanFicFarePlugin:settings`

**File Storage:**
- Local filesystem only
- User files: `$SCOURGIFY_HOME` (or `$XDG_CONFIG_HOME/scourgify` or `~/.config/scourgify`)
  - `config.toml` - Column map, behavior toggles
  - `overrides/` - User rules (fandoms.csv, characters.csv, etc., survival of upgrades)
  - `data/` - Proposals, intermediates, backups (gitignored)
    - `data/backups/` - metadata.db snapshots (before every write; Online Backup API, not byte-copy)
    - `data/rejects.csv` - Per-item step-review rejects
    - `classify_proposal.csv` / `classify_proposal_applied_*.csv` - Proposals and archived results
    - `promote_ledger.csv` - Promotion verdicts
    - `classify_failures.csv` - Engine failures (refusal class, reason)
    - `synopsis_failures.csv` - Synopsis pass failures
    - `edits.jsonl` - Append-only write log (one line per op set, never deleted)

**Caching:**
- None - every run recomputes the full scope

## Authentication & Identity

**Auth Provider:**
- Custom (env-var-based for cloud engines)
  - `resolve_keys(stored, env=)` merges stored keys from plugin settings with environment overrides
  - Environment WINS over stored (opposite of library path rule, deliberately)
  - `key_source(e, stored, env)` reports whether an engine's key came from "env", "stored", or ""
  - Keys never appear in logs, artifacts, or dialogs (redaction enforced)

**Calibre Plugin Settings:**
- Stored in Calibre's JSONConfig('plugins/scourgify') with chmod 0600
- Only LLM keys are stored (not library path, which is injected by GUI)
- Settings UI at `plugin/config.py` (widget built inside `config_widget()` to keep Qt off CLI)
- Plugin API: `common.set_library(path)` injects the open library path

## Monitoring & Observability

**Error Tracking:**
- None (application-specific, no external service)
- Local: `src/scourgify/editlog.py` writes `data/edits.jsonl` (append-only, before ops apply for crash safety)
- Failures: `classify_failures.csv`, `synopsis_failures.csv` (engine-specific)

**Logs:**
- Console (rich tables and panels in wizard/audit, plain stdout in CLI)
- Edit Log: `data/edits.jsonl` (JSONL, collision-proof run IDs, conflict-aware undo)
- CSV artifacts preserve state across reruns (proposal → review → ledger)

**Debugging:**
- Bandit security scan: `bandit -c pyproject.toml -r src/scourgify` (pre-commit: skips checked patterns)

## CI/CD & Deployment

**Hosting:**
- PyPI (Python Package Index)
- TestPyPI (staging)
- GitHub Releases (source)

**CI Pipeline:**
- GitHub Actions
  - `.github/workflows/ci.yml` - Runs tests on every push/PR to develop and main (Python 3.10/3.13/3.14)
  - `.github/workflows/publish.yml` - Auto-publish to TestPyPI on push to main (touching src/pyproject.toml); manual dispatch to PyPI
  - `.github/workflows/version.yml` - Version management workflow

**Publishing:**
- Trusted Publishing (OIDC) - No API tokens stored, environment-based authentication
- Build: `uv build` (sdist + wheel via hatchling)
- Publish: `uv publish --index testpypi --trusted-publishing always` or `uv publish --trusted-publishing always` (PyPI)
- Wheel includes: `src/scourgify/` (code + defaults + afm.swift), excludes: `afm` binary, `data/`, `overrides/`

## Environment Configuration

**Required env vars (depending on usage):**
- `CALIBRE_LIBRARY` - Path to library folder (Calibre metadata.db location)
- At least one LLM key: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`/`GOOGLE_API_KEY`, or `MISTRAL_API_KEY` (or swift/afm for apple)

**Optional env vars:**
- `SCOURGIFY_HOME` - Override config root (else XDG fallback)
- `SCOURGIFY_SCRIPT` - Canned answers for scripted runs (testing/automation)
- `CI`, `NONINTERACTIVE` - Suppress interactive prompts

**Secrets location:**
- LLM keys: environment variables (CI: GitHub Actions secrets)
- Plugin settings: Calibre's JSONConfig at `~/.local/share/calibre/plugins/scourgify/` (chmod 0600)
- Never: `.env` files, credentials.json, or any committed secrets

## Webhooks & Callbacks

**Incoming:**
- None

**Outgoing:**
- LLM API calls (HTTP POST to `https://api.anthropic.com`, `https://api.openai.com`, `https://generativelanguage.googleapis.com`, `https://api.mistral.ai`)
  - No webhooks; request/response only

## External CLIs

**Calibre Tools:**
- `calibre-debug -e _writer.py -- <ops.json>` - Write metadata.db changes (bundled Python)
  - Files: `src/scourgify/_writer.py`, `src/scourgify/ops.py`
  - Called by: `common.run_writer()` in standalone tools (wrangle/classify/staleness)
  - Environment: Calibre's bundled Python 3.14.6 (empty site-packages)

- `ebook-convert` - Extract text from non-EPUB formats (PDF/MOBI/DOCX/etc.)
  - Used by: `booktext.py:extract()` for synopsis text extraction
  - Timeout: 180 seconds
  - Falls back: Returns empty string on timeout/error (safe path)

- `calibre-customize -l` - Detect installed plugins (FanFicFare detection in setup)
  - Used by: `src/scourgify/setup.py:check_fff()`
  - Timeout: 30 seconds
  - Used to verify FanFicFare is installed before proceeding

**Build Tools:**
- `swift` or pre-built `afm` binary - Apple Foundation Models inference
  - Used by: `src/scourgify/engines.py:Apple` (subprocess pipe)
  - Built from: `src/scourgify/afm.swift` with `swiftc -O afm.swift -o afm`
  - Required for: `--engine apple` (macOS 26+)

## Data Format Standards

**CSV/TSV Artifacts (all use `"; "` delimiter and standard headers):**
- `classify_proposal.csv` - Pending tagging: book, description, proposed_new (headers: book_id, title, added_tags)
- `classify_proposal_applied_*.csv` - Archived proposals (timestamp in filename)
- `promote_review.csv` - New tag candidates under review (advocate, skeptic, human_verdict, verdict_reason)
- `promote_ledger.csv` - Promotion verdicts after apply (tag, verdict, fold_to)
- `classify_failures.csv` - Engine errors (book_id, title, reason, engine — reason prefixed with error class)
- `synopsis_failures.csv` - Synopsis pass errors (book_id, title, reason)
- `data/rejects.csv` - Step-review rejects (ts, stage, book, title, kind, column, before, after, class)

**JSONL Edit Log:**
- `data/edits.jsonl` - One JSON object per line
  - Run header: { "run": <id>, "stage": "wrangle"/"classify"/…, "ts": <ISO>, "library_uuid": <uuid>, "counts": {…} }
  - Op lines: { "run": <id>, "ts": <ISO>, "book": <id>, "field": <label>, "before": <value>, "after": <value>, "undo": true/false }
  - Run footer: { "run": <id>, "final_counts": {…}, "outcome": "ok"/"error" }
  - Missing footer = partial run (ops applied but process crashed; still undoable)

## FanFicFare Integration

**Detection & Configuration:**
- `calibre-customize -l` checks for plugin presence
- Reads FanFicFare prefs blob from Calibre DB: `preferences` table, key `namespaced:FanFicFarePlugin:settings`
- Column mapping (FFF field → Calibre custom column): `category`→`#fandoms`, `characters`→`#characters`, `ships`→`#relationships`, `genre`→`#genres`, `status`→`#status`
- "Comments → New Only" setting checked before synopsis pass (prevents overwriting author blurbs)

**Data Assumptions:**
- FanFicFare sets Calibre custom columns on import (tools create missing ones via setup)
- No reverse-sync: scourgify writes Calibre, not FanFicFare
- Files: `src/scourgify/setup.py` (FFF detection & config), `src/scourgify/synopsis.py` (Comments protection)

## AO3 (Archive of Our Own) Data Dump

**Source:**
- OTW ["Selective data dump for fan statisticians"](https://archiveofourown.org/admin_posts/18804) (2021-02-26 snapshot)
- Mechanical extraction: ~150k fandom/tag/character rows (canonical + merger pairs)
- LLM clustering (Haiku bulk → Sonnet verify → Opus referee) to group fandoms by universe

**Bundled Output:**
- `defaults/ao3/universes.csv` - Fandom master list (master,name,rel)
- `defaults/ao3/characters.csv` - Character master list (generated)
- `defaults/ao3/tags.csv` - Trope/genre master list (generated)
- `defaults/ao3_exceptions.txt` - Exclusions from generation (e.g., warning-shadow mergers)

**Build Process:**
- `build_ao3_layer.py` - Regenerates from raw data dump (never hand-edit generated files)
- Override: Curated exceptions in `defaults/ao3_exceptions.txt` (survives regeneration)
- Policy: Adapt AO3 everywhere except franchise unification (hand-curated re-points in `defaults/`)

**Vocab Generation:**
- `build_classify_seed.py` - Builds `defaults/classify_vocab_ao3.txt` (~120 highest-use AO3 freeform tropes)
- Merged with hand-curated `defaults/classify_vocab.txt` at load time
- User overrides via `overrides/classify_vocab.txt` (appended, deduplicated)

---

*Integration audit: 2026-08-28*
