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
  - Creates the GitHub Release for vX.Y.Z (gh release create) with the notes
    from the matching CHANGELOG.md section
  - Builds the sdist + wheel and publishes them to PyPI from THIS machine

RULE: a release cut REQUIRES the GitHub Release, not just the git tag. This
script always creates both; a bare `git tag` + push is NOT a release (it never
surfaces on /releases or as Latest). If the gh step fails, fix auth and finish
the release manually before moving on.

PyPI publishing is LOCAL-ONLY and part of this script — no workflow publishes to
PyPI (there is no publish-pypi.yml; OIDC Trusted Publishing is not used). The
upload is hooked to the tagging event here so a cut release and a published
release are the same act, and it runs on the maintainer's machine with the
long-lived project-scoped token.

Credentials come from the environment: TWINE_USERNAME=__token__ and
TWINE_PASSWORD are exported by ~/.zshrc. This script verifies both are present
BEFORE it pushes anything, because a credential failure discovered after the tag
is pushed leaves a half-cut release. It never prints their values.

This tag push triggers guard-release.yml (verifies every v* tag has a
GitHub Release). Releases are published to PyPI and installed via repo install.sh.

NOTE: PyPI version strings are permanent. They can be yanked but never reused,
so a completed run of this script burns vX.Y.Z on PyPI for good.
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

# ---------------------------------------------------------------------------
# Publish pre-flight. Everything the local PyPI upload needs is verified HERE,
# before any push, tag, or release. The upload is the last step but its
# prerequisites are checked first: discovering a missing token after the tag is
# pushed would leave a tagged, released, unpublished version -- and the tag
# cannot simply be re-cut, since the GitHub Release already exists.
# ---------------------------------------------------------------------------
PY=".venv/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "Error: ${PY} not found. This repo publishes from its own virtualenv." >&2
  echo "  python3 -m venv .venv && .venv/bin/python -m pip install -e \".[dev,release]\"" >&2
  exit 1
fi

for mod in build twine; do
  if ! "$PY" -c "import ${mod}" >/dev/null 2>&1; then
    echo "Error: python module '${mod}' is not installed in .venv (needed to publish)." >&2
    echo "  ${PY} -m pip install -e \".[release]\"" >&2
    exit 1
  fi
done

# Presence only -- never echo, log, or interpolate these values.
if [[ -z "${TWINE_USERNAME:-}" || -z "${TWINE_PASSWORD:-}" ]]; then
  echo "Error: TWINE_USERNAME / TWINE_PASSWORD are not both set in the environment." >&2
  echo "They are exported by ~/.zshrc from ~/env-secrets/api_keys/; that export uses" >&2
  echo "2>/dev/null, so a missing or renamed token file yields an EMPTY value here." >&2
  echo "Check with: env | grep -c TWINE_PASSWORD   (expect 1)" >&2
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
export ACTOP_TAG_RELEASE=1
git push origin "${TAG}"

echo "Creating GitHub Release ${TAG}..."
if ! command -v gh >/dev/null 2>&1; then
  echo "Error: gh CLI not found — the GitHub Release (required for a release cut) was not created." >&2
  echo "The tag was pushed, but the release is incomplete. Install gh and run:" >&2
  echo "  gh release create \"${TAG}\" --title \"actop ${VERSION}\" --notes \"\$(awk '/^## \\[${VERSION}\\]/ {f=1; next} f && /^## \\[/ {exit} f' CHANGELOG.md)\"" >&2
  exit 1
fi

RELEASE_NOTES="$(awk -v ver="${VERSION}" '
  $0 ~ "^## \\[" ver "\\]( -.*)?$" { f=1; next }
  f && /^## \[/ { exit }
  f { print }
' CHANGELOG.md)"

if [[ -z "$RELEASE_NOTES" ]]; then
  echo "No CHANGELOG.md section found for ${VERSION}; using generated notes."
  gh release create "${TAG}" --generate-notes
else
  gh release create "${TAG}" --title "actop ${VERSION}" --notes "${RELEASE_NOTES}"
fi

echo "Tag ${TAG} pushed and GitHub Release created."

# ---------------------------------------------------------------------------
# Local PyPI publish. Hooked to the tagging event: cutting a release and
# publishing it are one act, run from this machine. No workflow does this.
# ---------------------------------------------------------------------------
echo "Building sdist + wheel for ${VERSION}..."
# Wipe dist/ first. `twine upload dist/*` is a glob, so stale artifacts from an
# earlier version sitting in dist/ would be uploaded alongside this release.
rm -rf dist
"$PY" -m build

# The build must have produced exactly this version. Guards against publishing a
# stale artifact if the build silently picked up a different version.
missing=""
for f in "dist/actop-${VERSION}.tar.gz" "dist/actop-${VERSION}-py3-none-any.whl"; do
  [[ -f "$f" ]] || missing="${missing} ${f}"
done
if [[ -n "$missing" ]]; then
  echo "Error: expected build artifacts not found:${missing}" >&2
  echo "dist/ contains:" >&2
  ls -1 dist >&2 || true
  echo "The tag and GitHub Release for ${TAG} already exist; fix the build and" >&2
  echo "publish manually with: ${PY} -m twine upload --skip-existing dist/*" >&2
  exit 1
fi

# Metadata / README rendering check -- catches what PyPI would reject on upload.
echo "Checking artifact metadata..."
"$PY" -m twine check dist/*

# --skip-existing makes a re-run after a partial failure safe: an already
# uploaded file is skipped rather than failing the whole upload.
echo "Uploading to PyPI (credentials from the environment)..."
if ! "$PY" -m twine upload --skip-existing dist/*; then
  echo "Error: PyPI upload failed." >&2
  echo "The tag and GitHub Release for ${TAG} already exist, so DO NOT re-cut the tag." >&2
  echo "Fix the cause and re-run just the upload:" >&2
  echo "  ${PY} -m twine upload --skip-existing dist/*" >&2
  exit 1
fi

echo
echo "Done. ${TAG} tagged, released, and published to PyPI."
echo "Tag ${TAG} released and published to PyPI."
echo "Verify:  pipx install actop && actop --version"
