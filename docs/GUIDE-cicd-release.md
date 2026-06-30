# Release Operations Guide

This document serves two purposes:

1. **Tutorial** — explains the Homebrew packaging model, CI/CD component ownership, and end-to-end release flow so that new contributors (human or AI) can understand how releases work without reading CI workflow files.
2. **Runbook** — provides step-by-step release instructions, operational rules, and failure recovery playbooks for day-to-day release execution.

> **Architecture note (since v1.0.0):** `main` of `binlecode/actop` is **strictly PR-only** (branch protection + a local `.githooks/pre-push` guard). CI never pushes to `main`. The Homebrew formula lives in a **separate tap repo**, `binlecode/homebrew-actop`, and the release workflow syncs it there. PyPI is published via **OIDC Trusted Publishing** on tag.

## Homebrew Packaging Model

`actop` uses Homebrew's Python virtualenv formula pattern:

- The formula (`Formula/actop.rb`) lives in the tap repo **`binlecode/homebrew-actop`**, not in this code repo.
- It includes `Language::Python::Virtualenv` and depends on `python@3.13`.
- `virtualenv_install_with_resources` creates a `libexec` venv and pip-installs the declared `resource` blocks (`textual` and its dependencies: `rich`, `markdown-it-py`, `mdurl`, `pygments`, `linkify-it-py`, `uc-micro-py`, `mdit-py-plugins`, `platformdirs`, `typing-extensions`).
- Users install with `brew tap binlecode/actop && brew install actop` (Homebrew maps the tap name `binlecode/actop` → `binlecode/homebrew-actop`).

## Component Ownership

| Component | Responsibility |
| --- | --- |
| **Maintainer** | Bumps `pyproject.toml` version + `CHANGELOG.md` **via a PR**, runs local checks, merges, then triggers the release tag |
| **`scripts/tag_release.sh`** | Verifies clean tree, validates version vs `pyproject.toml`, confirms local `main` matches `origin/main`, creates and pushes `vX.Y.Z` tag. Does **not** modify any formula |
| **`main-ci.yml`** | Runs on `main` push **and on `pull_request` to `main`**. Installs the package + dev tools, runs lint/format/help/tests. (Required for PR validation.) |
| **`release-formula.yml`** | Runs on `v*` tag push (or manual `workflow_dispatch`). Validates tag/version match, computes the source tarball SHA256, updates `url` + `sha256` (+ regenerates `resource` blocks) in **`binlecode/homebrew-actop`** and pushes there using the `HOMEBREW_TAP_TOKEN` secret. Never touches `main` of this repo |
| **`publish-pypi.yml`** | Runs on `v*` tag push (or manual). Validates tag/version, builds sdist+wheel, publishes to PyPI via **OIDC Trusted Publishing** (`skip-existing`) |

### One-time setup (prerequisites)

- **Tap repo:** `binlecode/homebrew-actop` exists with `Formula/actop.rb`.
- **`HOMEBREW_TAP_TOKEN`** secret on `binlecode/actop`: a fine-grained PAT scoped to `binlecode/homebrew-actop` with **Contents: Read/write**. Set via `gh secret set HOMEBREW_TAP_TOKEN --repo binlecode/actop`.
- **PyPI Trusted Publisher:** on pypi.org add a publisher for project `actop`, owner `binlecode`, repo `actop`, workflow `publish-pypi.yml`, environment `pypi` (see the header of `publish-pypi.yml`). Until the project exists, add it as a *pending* publisher.

## End-to-End Flow

```text
PR: bump pyproject.toml version + CHANGELOG.md
  -> merge PR to main (squash/merge)
  -> git pull --ff-only           # local main now has the bump
  -> scripts/tag_release.sh        # pushes tag vX.Y.Z only (main is already in sync)
       -> release-formula (tag push) -> formula sync commit in binlecode/homebrew-actop
       -> publish-pypi   (tag push) -> sdist+wheel published to PyPI via OIDC
```

## Release Steps

### 1. Bump version and changelog via a PR

`main` is PR-only — do not commit the bump directly.

```bash
git switch -c release-vX.Y.Z
# edit pyproject.toml ([project].version) and CHANGELOG.md (move Unreleased -> new version + date)
git commit -am "Release vX.Y.Z"
git push -u origin release-vX.Y.Z
gh pr create --base main --fill
```

### 2. Run checks (locally and/or on the PR)

```bash
.venv/bin/python -m ruff check --fix .
.venv/bin/python -m ruff format .
.venv/bin/python -m actop.actop --help
.venv/bin/pytest -q
```

### 3. Merge and sync

```bash
gh pr merge --merge --delete-branch
git checkout main && git pull --ff-only
```

### 4. Tag the release

```bash
scripts/tag_release.sh "X.Y.Z"
```

### 5. Monitor CI

```bash
gh run list -R binlecode/actop --limit 10
```

Wait for `release-formula` and `publish-pypi` to complete successfully.

### 6. Verify

```bash
# PyPI
pipx install actop && actop --version        # or: pip install actop

# Homebrew (formula now updated in the tap repo)
brew update && brew upgrade binlecode/actop/actop
brew info binlecode/actop/actop
```

## Rules

**Do:**
- Bump version/changelog through a **PR** (never push `main` directly).
- Use `scripts/tag_release.sh` for all release tags.
- Let `release-formula.yml` own formula sync commits (in the tap repo).
- Verify `release-formula` and `publish-pypi` after each release.
- Keep `refresh_resources=true` (default) for normal releases.

**Do not:**
- Push directly to `main` (blocked by branch protection + pre-push hook).
- Manually edit `Formula/actop.rb` in the tap repo during releases.
- Push a tag before the version/changelog PR is merged.
- Force-push `main` (ever).
- Disable resource refresh except for emergency reruns.

## Homebrew Core Submission Guide

To make `actop` natively trusted without users running `brew tap`, it can be submitted to Homebrew Core:

1. **Popularity Requirements**: meet Homebrew Core's bar (usually 75+ stars, 30+ watchers, notable usage).
2. **Formula Audit**: `brew audit --new-formula actop`.
3. **Open a PR** to [homebrew-core](https://github.com/Homebrew/homebrew-core). Once merged, `actop` becomes an official formula, implicitly trusted globally.

## Failure Playbooks

### `tag_release.sh` fails because local `main` is behind/diverged

The version-bump PR may not be merged/pulled yet.

```bash
git fetch origin && git checkout main && git pull --ff-only origin main
scripts/tag_release.sh "X.Y.Z"
```

### `release-formula` fails with tag/version mismatch

Tag `vX.Y.Z` does not match `pyproject.toml` in the tag commit. Fix the version in a new PR, merge, then create a new tag (do not reuse the old tag).

### `release-formula` fails at the tap checkout / push

Usually a missing or under-scoped `HOMEBREW_TAP_TOKEN`. Confirm the secret exists on `binlecode/actop` and the PAT has **Contents: Read/write** on `binlecode/homebrew-actop`. Re-run the failed job.

### `publish-pypi` fails with OIDC / trusted-publisher error

The pending publisher isn't configured (or names don't match). Verify the publisher on pypi.org matches repo `binlecode/actop`, workflow `publish-pypi.yml`, environment `pypi`. `skip-existing` means re-runs after a successful upload are safe.

### Emergency rerun without resource refresh

Use `workflow_dispatch` on `release-formula` with `refresh_resources=false` as a temporary workaround. Follow up with a normal run (`refresh_resources=true`) to restore full synchronization.

## Quick Reference

```bash
gh run list -R binlecode/actop --limit 12          # recent CI runs
gh run view -R binlecode/actop <RUN_ID>            # inspect run
gh run view -R binlecode/actop <RUN_ID> --log-failed
git ls-remote --tags origin "v*"                   # remote tags
gh secret list -R binlecode/actop                  # confirm HOMEBREW_TAP_TOKEN
```

**Source of truth:** version in `pyproject.toml`, notes in `CHANGELOG.md`, formula in the tap repo `binlecode/homebrew-actop` (`Formula/actop.rb`), tag helper in `scripts/tag_release.sh`, CI in `.github/workflows/`.
