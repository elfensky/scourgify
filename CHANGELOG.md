# Changelog

All notable changes to scourgify are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

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
