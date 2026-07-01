# CI/CD & Release Design

This document serves two purposes:

1. **Design / tutorial** — explains the Homebrew packaging model, CI/CD component ownership, the CI validation matrix, and the end-to-end release flow so that new contributors (human or AI) can understand how releases work without reading CI workflow files.
2. **Runbook** — provides step-by-step release instructions, operational rules, and failure recovery playbooks for day-to-day release execution.

> **Architecture note (since v1.0.0):** `main` of `binlecode/actop` is **strictly PR-only** (branch protection + `.githooks/pre-commit` redaction check + `.githooks/pre-push` guard). CI never pushes to `main`. The Homebrew formula lives in a **separate tap repo**, `binlecode/homebrew-actop`, and the release workflow syncs it there using a **token-driven** push (`HOMEBREW_TAP_TOKEN`). PyPI publishing supports **two flows**: the current **tokenless OIDC Trusted Publishing** flow and a legacy **token-driven** (`twine` + API token) flow retained as fallback — both are detailed below.

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
| **`main-ci.yml`** | Runs on `main` push **and on `pull_request` to `main`**. `validate` job runs a Python **matrix (3.11, 3.12, 3.13, 3.14)**: installs `-e .` + `ruff`/`pytest`, runs `ruff check` + `ruff format --check`, runs `pytest -m "not local"` (CI-safe tests only), then the `--help` CLI smoke test. A non-blocking `canary-next-python` job (`continue-on-error`) repeats the checks on pre-release **3.15** for early warning. (Required for PR validation.) |
| **`release-formula.yml`** | Runs on `v*` tag push (or manual `workflow_dispatch`). Runs on `macos-latest` under a `formula-sync-tap` concurrency group. Validates tag/version match **from the tag commit**, computes the source tarball SHA256, updates `url` + `sha256` (and regenerates `resource` blocks via `brew update-python-resources` unless `refresh_resources=false`) in **`binlecode/homebrew-actop`**, and pushes there using the `HOMEBREW_TAP_TOKEN` secret (**token-driven**). Never touches `main` of this repo |
| **`publish-pypi.yml`** | Runs on `v*` tag push (or manual). Builds on Python 3.12 in the `pypi` environment with `id-token: write`. Validates tag/version, builds sdist+wheel, publishes to PyPI via **OIDC Trusted Publishing** (`skip-existing`). See both PyPI flows below |

### One-time setup (prerequisites)

- **Tap repo:** `binlecode/homebrew-actop` exists with `Formula/actop.rb`.
- **`HOMEBREW_TAP_TOKEN`** secret on `binlecode/actop`: a fine-grained PAT scoped to `binlecode/homebrew-actop` with **Contents: Read/write**. Set via `gh secret set HOMEBREW_TAP_TOKEN --repo binlecode/actop`.
- **PyPI Trusted Publishing (OIDC):** see the dedicated section below. The GitHub `pypi` environment is already created (tag-only deploys); the one remaining manual step is adding the trusted publisher on pypi.org.

### Secret hygiene (`HOMEBREW_TAP_TOKEN`)

`HOMEBREW_TAP_TOKEN` is the **only** long-lived secret in the release path (PyPI uses tokenless OIDC). Treat it accordingly.

- **Least privilege — prefer a fine-grained PAT.** Scope it to **only** `binlecode/homebrew-actop` with **Contents: Read/write** and nothing else. A classic PAT (`repo`, `workflow` scopes) *works*, but its blast radius is every repo the account owns — if the CI secret leaks, so does write access to all of them. The fine-grained token caps the damage at the tap repo.
- **Set an expiry and rotate.** Give the token a bounded lifetime (e.g. 90–180 days) and rotate on schedule or on any suspicion of exposure. Rotation is a one-liner — no workflow change needed:
  ```bash
  # paste the new token at the prompt; never pass it as a CLI arg
  gh secret set HOMEBREW_TAP_TOKEN --repo binlecode/actop
  ```
- **Never put the token in a command argument.** Pipe it via **stdin** (as above) so it stays out of `ps`/argv and shell history. Do **not** use `--body "<token>"`, and do not `echo` it. `gh secret set` encrypts the value client-side (libsodium sealed box) with the repo's Actions public key *before* upload over TLS, so GitHub stores only ciphertext and never sees the plaintext.
- **Logs are masked, not a safety net.** GitHub auto-redacts the registered secret value in workflow logs, but obfuscation tricks can defeat masking — the real protections are least-privilege scope + `main` being PR-only (fork-PR runs don't receive secrets).
- **Storage.** The plaintext copy lives only in `~/env-secrets/` (never in the repo); GitHub keeps the encrypted-at-rest copy. When you rotate, revoke the old token on GitHub and update `~/env-secrets/`.

> **Current state (v1.0.0):** the secret was bootstrapped from a **classic** PAT (`repo` + `workflow`). Replacing it with a tap-scoped **fine-grained** token is the recommended hardening follow-up.

## CI Validation (`main-ci.yml`)

Every push to `main` and every PR targeting `main` runs `main-ci.yml`. It has two jobs:

- **`validate`** (required) — a Python matrix over **3.11, 3.12, 3.13, 3.14** (`fail-fast: false`, so one version's failure does not cancel the others). Each leg:
  1. installs the package editable (`pip install -e .`) plus `ruff` and `pytest`;
  2. `ruff check .` and `ruff format --check .` (lint + format gate);
  3. `pytest -m "not local"` — runs the **CI-safe** subset only. Host-dependent tests (SMC/IOReport/Apple-Silicon-specific) are marked `@pytest.mark.local` and skipped in CI because the GitHub runners are not Apple Silicon;
  4. `python -m actop.actop --help` smoke test.
- **`canary-next-python`** (non-blocking, `continue-on-error: true`) — repeats install + `pytest -m "not local"` + `--help` on the **pre-release 3.15** toolchain. Since `requires-python` is uncapped (`>=3.11`), this is an early-warning signal for breakage on the next CPython before it ships; a red canary never blocks a merge.

> The Homebrew formula pins `python@3.13`; the matrix is the set actively verified, not a compatibility cap.

## End-to-End Flow

```text
PR: bump pyproject.toml version + CHANGELOG.md
  -> main-ci (pull_request)        # matrix lint/format/test/help must pass
  -> merge PR to main (squash/merge)
  -> main-ci (push to main)        # same checks on the merge commit
  -> git pull --ff-only            # local main now has the bump
  -> scripts/tag_release.sh        # pushes tag vX.Y.Z only (main is already in sync)
       -> release-formula (tag push) -> formula sync commit in binlecode/homebrew-actop  [token-driven: HOMEBREW_TAP_TOKEN]
       -> publish-pypi   (tag push) -> sdist+wheel published to PyPI                       [OIDC by default; token-driven fallback]
```

## PyPI Publishing — two flows

Publishing to PyPI can authenticate two ways. `actop` uses **OIDC Trusted Publishing**
as the default (no stored secret); the **token-driven** flow is documented as the
fallback and as the mechanism used to bootstrap the project name. Both produce the
identical artifacts (`sdist` + `wheel` from `python -m build`) and both should keep
`skip-existing: true` so re-running a tag whose version already exists is a safe no-op.

| | **OIDC Trusted Publishing (default)** | **Token-driven (fallback / bootstrap)** |
| --- | --- | --- |
| Credential | Short-lived OIDC identity minted per run | Long-lived PyPI API token stored as a secret |
| Stored secret | **None** | `PYPI_API_TOKEN` on `binlecode/actop` |
| Trust anchor | repo + workflow + environment must match the PyPI publisher | possession of the token |
| Setup | one-time publisher registration on pypi.org (browser) | generate a project-scoped token on pypi.org, store via `gh secret set` |
| Blast radius if compromised | none to steal — nothing at rest | token = publish rights until revoked |
| When used | every `v*` tag release | emergency fallback, or first-ever upload to claim the name |

### Flow A — OIDC Trusted Publishing (current default)

GitHub Actions mints a short-lived OIDC identity that PyPI verifies against the exact
repo + workflow + environment. `publish-pypi.yml` (with `id-token: write`,
`environment: pypi`) uses `pypa/gh-action-pypi-publish@release/v1` to do this on every
`v*` tag — **no API token is stored anywhere**.

**GitHub side — already configured (no action needed):**
- Environment **`pypi`** exists on `binlecode/actop`, and the job declares
  `permissions: id-token: write` (required to mint the OIDC token).
- Deployment policy: **custom, tag-only** — only refs matching **`v*` (type: tag)**
  can deploy to the `pypi` environment, so no branch can ever trigger a publish.
  Recreate/inspect with:
  ```bash
  gh api /repos/binlecode/actop/environments/pypi
  gh api /repos/binlecode/actop/environments/pypi/deployment-branch-policies
  ```
- *(Optional hardening)* add yourself as a required reviewer on the `pypi`
  environment for a manual approve-gate before each publish.

**PyPI side — one-time manual step (must be done in the browser as the project owner):**
PyPI has no token-authenticated API for managing trusted publishers, so add it via the UI:
1. Go to <https://pypi.org/manage/project/actop/settings/publishing/> → **Add a new publisher** (GitHub).
2. Enter these values **exactly** (they must match the workflow):
   | Field | Value |
   | --- | --- |
   | Owner | `binlecode` |
   | Repository name | `actop` |
   | Workflow name | `publish-pypi.yml` |
   | Environment name | `pypi` |
   | PyPI Project Name | `actop` |
   (If the project didn't yet exist you'd add it as a *pending* publisher; `actop`
   exists as of 1.0.0, so it's a normal publisher.)

### Flow B — Token-driven publish (fallback / bootstrap)

Used to **bootstrap** the project (`1.0.0` was uploaded this way to claim the name),
and retained as a break-glass fallback if OIDC is unavailable (e.g. PyPI publisher
config drift, or publishing from a machine/CI without OIDC).

**Manual, from a trusted machine** (token lives only in `~/env-secrets/`, never in argv):
```bash
python -m build                       # produces dist/*.tar.gz and dist/*.whl
python -m twine upload --skip-existing dist/*
# username: __token__   password: <PyPI project-scoped API token from ~/env-secrets/>
```
Prefer a `~/.pypirc` or `TWINE_USERNAME=__token__` + `TWINE_PASSWORD` env var over
typing the token so it stays out of shell history.

**In CI (only if OIDC must be bypassed):** store a **project-scoped** token as a secret
and pass it to the same publish action — never commit it, never echo it:
```bash
# paste the token at the prompt; stdin keeps it out of argv/history
gh secret set PYPI_API_TOKEN --repo binlecode/actop
```
```yaml
# variant of the Publish step in publish-pypi.yml (drop id-token/environment)
- name: Publish to PyPI (token)
  uses: pypa/gh-action-pypi-publish@release/v1
  with:
    password: ${{ secrets.PYPI_API_TOKEN }}
    skip-existing: true
```

**Migration / hygiene note:** OIDC supersedes the bootstrap token. Once trusted
publishing is confirmed on a release, **delete the account/project-scoped token** from
pypi.org and from `~/env-secrets/api_keys/` so no long-lived PyPI credential remains.
Only regenerate one if you must invoke Flow B again.

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

The trusted publisher isn't configured (or names don't match). Verify the publisher on pypi.org matches repo `binlecode/actop`, workflow `publish-pypi.yml`, environment `pypi` (see **PyPI Publishing — Flow A**). Also confirm the tag matches the `pypi` environment's `v*` tag deployment policy — a run from a non-`v*` ref won't be allowed into the environment. `skip-existing` means re-runs after a successful upload are safe. **Break-glass:** if OIDC cannot be fixed in time, publish that version manually with the token-driven **Flow B** (`twine upload --skip-existing`), then restore OIDC for subsequent releases.

### Emergency rerun without resource refresh

Use `workflow_dispatch` on `release-formula` with `refresh_resources=false` as a temporary workaround. Follow up with a normal run (`refresh_resources=true`) to restore full synchronization.

## Quick Reference

```bash
gh run list -R binlecode/actop --limit 12          # recent CI runs
gh run view -R binlecode/actop <RUN_ID>            # inspect run
gh run view -R binlecode/actop <RUN_ID> --log-failed
git ls-remote --tags origin "v*"                   # remote tags
gh secret list -R binlecode/actop                  # confirm HOMEBREW_TAP_TOKEN
gh api /repos/binlecode/actop/environments/pypi/deployment-branch-policies  # OIDC tag policy
```

**Source of truth:** version in `pyproject.toml`, notes in `CHANGELOG.md`, formula in the tap repo `binlecode/homebrew-actop` (`Formula/actop.rb`), tag helper in `scripts/tag_release.sh`, CI in `.github/workflows/`.
