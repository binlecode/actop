#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/tag_release.sh [version]

Examples:
  scripts/tag_release.sh
  scripts/tag_release.sh 0.1.3
  scripts/tag_release.sh v0.1.3

Prerequisite:
  main is PR-only. Bump pyproject.toml version + CHANGELOG.md via a PR and merge
  it BEFORE tagging, then pull main locally. This script only tags an already-merged
  commit; it does not push commits to main (and cannot — branch protection blocks it).

Behavior:
  - Reads version from pyproject.toml by default
  - If version is provided, it must match pyproject.toml
  - Verifies working tree is clean
  - Fast-forwards local main from origin/main before tagging
  - Verifies tag does not already exist locally/remotely
  - Verifies local main is in sync with origin/main (no-op push)
  - Creates and pushes tag vX.Y.Z

This tag push triggers release-formula.yml (syncs the formula in the tap repo
binlecode/homebrew-actop) and publish-pypi.yml (publishes to PyPI via OIDC).
Formula sync is CI-driven and lives in the tap repo — there is no formula in this repo.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ $# -gt 1 ]]; then
  usage >&2
  exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  echo "Error: git is required" >&2
  exit 1
fi

if [[ ! -f pyproject.toml ]]; then
  echo "Error: pyproject.toml not found. Run from repository root." >&2
  exit 1
fi

PROJECT_VERSION="$(
  awk '
    /^\[project\]/ { in_project=1; next }
    /^\[/ && in_project { in_project=0 }
    in_project && $1 == "version" {
      gsub(/"/, "", $3)
      print $3
      exit
    }
  ' pyproject.toml
)"
if [[ -z "$PROJECT_VERSION" ]]; then
  echo "Error: could not parse [project].version from pyproject.toml" >&2
  exit 1
fi

if [[ $# -eq 1 ]]; then
  RAW_VERSION="$1"
  VERSION="${RAW_VERSION#v}"
  if [[ -z "$VERSION" ]]; then
    echo "Error: version is empty" >&2
    exit 1
  fi
  if [[ "$PROJECT_VERSION" != "$VERSION" ]]; then
    echo "Error: pyproject.toml version ($PROJECT_VERSION) does not match requested version ($VERSION)" >&2
    exit 1
  fi
else
  VERSION="$PROJECT_VERSION"
fi
TAG="v${VERSION}"

if [[ -n "$(git status --porcelain)" ]]; then
  echo "Error: working tree is not clean. Commit or stash changes first." >&2
  git status --short
  exit 1
fi

if git rev-parse -q --verify "refs/tags/${TAG}" >/dev/null; then
  echo "Error: local tag ${TAG} already exists" >&2
  exit 1
fi

if git ls-remote --exit-code --tags origin "refs/tags/${TAG}" >/dev/null 2>&1; then
  echo "Error: remote tag ${TAG} already exists on origin" >&2
  exit 1
fi

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$CURRENT_BRANCH" != "main" ]]; then
  echo "Error: current branch is ${CURRENT_BRANCH}. Switch to main first." >&2
  exit 1
fi

echo "Syncing local main with origin/main (fast-forward only)..."
git pull --ff-only origin main

if git ls-remote --exit-code --tags origin "refs/tags/${TAG}" >/dev/null 2>&1; then
  echo "Error: remote tag ${TAG} already exists on origin" >&2
  exit 1
fi

echo "Pushing main..."
git push origin main

echo "Creating tag ${TAG}..."
git tag "${TAG}"

echo "Pushing tag ${TAG}..."
git push origin "${TAG}"

echo "Done. Tag ${TAG} pushed."
echo "Release formula workflow should start from this tag push."
