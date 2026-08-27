# Technology Stack

**Analysis Date:** 2026-08-28

## Languages

**Primary:**
- Python 3.10+ - Core application, CLI tools, tests (`src/scourgify/`, plugins)
- Python 3.14.6 - Bundled by Calibre (the plugin target interpreter; newer than the repo floor)

**Secondary:**
- Swift 5.0+ - Apple Foundation Models bridge for on-device LLM inference (`src/scourgify/afm.swift`)
- TOML - Configuration files (`config.toml`)
- CSV - Data transport format (proposals, overrides, defaults, edit logs)

## Runtime

**Environment:**
- CPython 3.10 - Minimum floor (tested in CI)
- CPython 3.13 - Current release (tested in CI)
- CPython 3.14.6 - Calibre's bundled Python (plugin execution; tested in CI)

**Package Manager:**
- uv - Lock-based dependency management and tool installer
- Lockfile: `uv.lock` (present)

## Frameworks

**Core:**
- Python stdlib (collections, csv, sqlite3, urllib, zipfile, tempfile, subprocess, json, argparse, re, unicodedata, os, sys, time, glob, contextlib)
- No heavyweight framework dependencies - intentional design for Calibre plugin compatibility (Calibre's bundled Python has empty site-packages)

**UI/Display:**
- rich 13+ - Terminal formatting, tables, panels, live progress (the ONLY production dependency)
  - Used in: `wizard.py`, `ui.py`, `report.py`, `Dashboard` (live classify progress)
  - Hard-imported only in wizard-facing modules; `report.py` is the ONE owner of HOW output renders
  - Raises friendly install hint if missing when needed (UI surfaces fail gracefully)

**Testing:**
- pytest-compatible plain assert testing (no framework required)
- Located in `tests/` - runs via `python3` directly, no test runner needed
- Bandit - Security scanning with custom skips for this threat model

## Key Dependencies

**Critical:**
- rich 13+ - Display rendering (tables, progress bars, live Dashboard)
  - Impact: Terminal interactivity; falls back gracefully if missing
  - Never imported at module level in core tools (allows standalone calibre-debug execution)

**Infrastructure (Internal):**
- urllib - HTTP client for LLM APIs (urllib.request, urllib.error from stdlib)
- sqlite3 - Read-only library database access via `ro_connect()` with `mode=ro` URI
- zipfile - EPUB extraction in `booktext.py` (with zip-bomb guard)
- calibre.library.db (DB) - Write-path via subprocess only (`_writer.py` under `calibre-debug`)

## Configuration

**Environment:**
- `CALIBRE_LIBRARY` - Path to the Calibre library folder (containing `metadata.db`). Checked at runtime, never at import
- `SCOURGIFY_HOME` - Override for user config root (else XDG: `$XDG_CONFIG_HOME/scourgify` or `~/.config/scourgify`)
- `SCOURGIFY_SCRIPT` - Canned answers for automated/testing runs (e.g., `"w,3,n,q"`)
- `CI`, `NONINTERACTIVE` - Suppress interactive prompts

**LLM Engine Keys (environment takes precedence over stored):**
- `ANTHROPIC_API_KEY` - Claude API
- `OPENAI_API_KEY` - OpenAI API
- `GEMINI_API_KEY` or `GOOGLE_API_KEY` - Google Gemini API
- `MISTRAL_API_KEY` - Mistral API

**Build:**
- `pyproject.toml` - Package metadata, dependencies, build config (hatchling)
- `.python-version` - Expected runtime version (3.14 for Calibre plugin; 3.10 is floor)

## Platform Requirements

**Development:**
- macOS, Linux (no Windows support — `user_dir()` uses XDG)
- swift toolchain (macOS 26+) for local AFM binary build (optional; falls back to `swift` CLI invocation)
- Calibre (optional) - for local testing; available via `calibre-customize`, `calibre-debug`, `ebook-convert` CLI

**Production:**
- Calibre library (metadata.db SQLite database)
- For standalone tool: system Python 3.10+
- For plugin: Calibre 9.11+ (bundles Python 3.14.6)
- For `--engine apple`: swift toolchain OR pre-built `afm` binary (macOS 26+ only)

## Special Considerations

**Calibre Plugin Constraints:**
- Core modules must import cleanly under Calibre's bundled Python with empty site-packages
  - No rich/ui/wizard/report imports in core layers (`common.py`, `wrangle.py`, `classify.py`, `promote.py`, `staleness.py`, `synopsis.py`, `ops.py`)
  - Tested by `tests/test_plugin_safety.py` (AST enforcement)
- All write operations route through single `calibre-debug -e _writer.py` subprocess
- `_writer.py` is glue only; the actual executor (`ops.py`) is shared by both standalone and in-process paths

**Thread Safety:**
- Standalone: ThreadPoolExecutor for concurrent LLM requests (I/O-bound)
- Plugin: All work via `ThreadedJob` with Dispatcher-wrapped callbacks
- Apple engine: Single-threaded subprocess pipe (not parallel-safe)

**Data Bundled in Package:**
- `defaults/` - Read-only: fandoms.csv, characters.csv, tropes.csv, genres_*, junk.txt, classify_vocab.txt, ratings.txt, decompose.csv
- `defaults/ao3/` - Generated master taxonomy from OTW data dump (universes.csv, characters.csv, tags.csv, plus ao3_exceptions.txt exclusions)
- `afm.swift` - Apple Foundation Models bridge source
- Excluded: `data/`, `overrides/`, `afm` binary (these are user files in `$SCOURGIFY_HOME`)

---

*Stack analysis: 2026-08-28*
