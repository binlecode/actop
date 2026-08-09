# Contributing to actop

**PRs and issues are welcome.** Whether it's a bug report, a new SoC profile, a
metric fix, or a docs tweak — thanks for helping.

For anything larger than a small fix, please **open an issue first** so we can
agree on the approach before you invest time.

## Ground rules (the short version)

`CLAUDE.md` in the repo root is the **single source of truth** for repository
guidelines (used by human and AI contributors alike). This file is the
human-friendly summary; when in doubt, `CLAUDE.md` wins.

### Setup

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"      # editable install + dev deps
git config core.hooksPath .githooks              # once per clone: enable local hooks
```

### Make your change

- **Branch from `main`; open the PR into `main`.** Never stack a branch on another
  unmerged feature branch — wait for it to land and re-branch from `main`.
- Keep changes small and focused; match the surrounding Python style (4-space
  indent, snake_case, explicit imports — never `from x import *`).
- **Bump the version in every PR**: update `pyproject.toml` + move the
  `CHANGELOG.md` `[Unreleased]` entry into a new dated section. Patch bump by
  default; minor only for a milestone.

### Before opening the PR

```bash
.venv/bin/ruff check --fix . && .venv/bin/ruff format .
.venv/bin/python -m actop.actop --help
.venv/bin/pytest -q
```

Then run `actop` on Apple Silicon to confirm the gauges/charts update without
crashing, and include your tested macOS/chip details in the PR.

### Tests

**Functional tests only.** Every test must exercise behavior through a public or
runtime entrypoint (CLI, the `Monitor`/`Profiler` API, real config merge,
documented public functions, real export formats, or a widget rendered through
its public path). No tests of private functions, internal state, or mocks that
fake the logic under test. See the *Testing Guidelines* in `CLAUDE.md` for the
full accept/reject rules.

## Where to look

- `CLAUDE.md` — full contributor guidelines and coding conventions.
- `docs/SPEC-system.md` — architecture: native bindings, sampling, SoC
  profiles, TUI rendering, testing contract.

By contributing, you agree that your contributions are licensed under the
project's [MIT License](LICENSE).
