# Changelog

All notable changes to scourgify are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **`scourgify synopsis` — the synopsis pass (#69).** Every book ends up with a *settled*
  spoiler-safe back-cover description in Calibre's own description field. A blurb that already
  tells you what the story is about is judged in one call and **kept untouched**; only a useless
  one is replaced, generated on-device from the book's own prose (premise, characters, stakes,
  hook, themes line — never the ending). Free, private, and deliberately slow: progress lives in
  the library as the new **`#synopsized`** datetime column, so runs resume and `--batch N` chews
  through a library over weeks. Failures land in `data/synopsis_failures.csv`, which is what keeps
  the sweep finite. Also available as a wizard stage, between staleness and classify.
- **`scourgify setup` checks FanFicFare's Comments → "New Only" switch** and offers to turn it on.
  Without it a metadata re-fetch overwrites the synopses; the synopsis pass refuses to start until
  it is set (`--force` accepts the degraded self-healing mode instead).

### Removed

- **`classify --text-fallback`.** A book with a description too thin to classify is *synopsis*
  work now, not a raw prose sample taken at tag time — the sample was disposable, unrepresentative,
  and left the library's own description just as bad. Such a book rejoins the classify backlog by
  itself once the synopsis pass has given it a real description.

### Changed

- **Repo conformance sweep.** Added `dependabot.yml` (pip + github-actions, minor/patch
  grouped), a version-bump gate workflow (advisory, PR-triggered), and `.python-version` (3.14).
  `AGENTS.md` is now the real agent file with `CLAUDE.md` symlinked to it; `graphify-out/` is
  generated locally and git-ignored. Dependabot security updates and GitHub-native secret scanning
  + push protection enabled repo-side (no third-party gitleaks); default branch set to `develop`.
- **Version source is now static in `pyproject.toml`.** Dropped the `[tool.hatch.version]`
  indirection that read `__version__` from `src/scourgify/__init__.py`; `pyproject.toml`'s
  `[project] version` is now the single source, and `__version__` resolves at runtime via
  `importlib.metadata` (the lintle pattern). Release-time bumps now edit `pyproject.toml`, not
  `__init__.py`.
