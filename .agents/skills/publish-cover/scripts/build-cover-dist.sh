#!/usr/bin/env bash
# build-cover-dist.sh — deterministic build for the landing page dist.
# All determinism lives here: path rewrite, version injection, gif validation,
# secret scan. The SKILL.md owns the decision gates; this script owns the build.
set -euo pipefail

COVER_SRC="${COVER_SRC:-cover}"
COVER_DIST="${COVER_DIST:-dist-cover}"
MAX_GIF_SIZE_MB="${MAX_GIF_SIZE_MB:-5}"
SKIP_SECRET_SCAN="${SKIP_SECRET_SCAN:-0}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

say()  { printf "${GREEN}[build-cover]${NC} %s\n" "$*"; }
warn() { printf "${YELLOW}[build-cover] WARN:${NC} %s\n" "$*" >&2; }
die()  { printf "${RED}[build-cover] FATAL:${NC} %s\n" "$*" >&2; exit 1; }

# ── preflight ───────────────────────────────────────────────────────────────

command -v python3 >/dev/null 2>&1 || die "python3 required (for version badge + secret scan)"
[ -f "${COVER_SRC}/index.html" ] || die "${COVER_SRC}/index.html not found (set COVER_SRC if not cover/)"

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo "$(pwd)")"
COMMIT_HASH="$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo 'unknown')"
BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# ── version ─────────────────────────────────────────────────────────────────

VERSION=""
if [ -f "$REPO_ROOT/pyproject.toml" ]; then
    VERSION="$(python3 -c "
import tomllib, pathlib
try:
    data = tomllib.loads(pathlib.Path('$REPO_ROOT/pyproject.toml').read_text())
    print(data.get('project', {}).get('version', ''))
except Exception as e:
    print('', end='')
" 2>/dev/null || echo '')"
fi

if [ -z "$VERSION" ] && [ -f "$REPO_ROOT/package.json" ]; then
    VERSION="$(python3 -c "
import json, pathlib
try:
    data = json.loads(pathlib.Path('$REPO_ROOT/package.json').read_text())
    print(data.get('version', ''))
except Exception:
    print('', end='')
" 2>/dev/null || echo '')"
fi

if [ -z "$VERSION" ]; then
    warn "no version found in pyproject.toml or package.json"
else
    say "version: ${VERSION}"
fi

# ── copy ─────────────────────────────────────────────────────────────────────

say "wiping ${COVER_DIST}"
rm -rf "$COVER_DIST"
mkdir -p "$COVER_DIST"

say "copying ${COVER_SRC} → ${COVER_DIST}"
# rsync skips dotfiles and gitignored paths, preserving structure
if command -v rsync >/dev/null 2>&1; then
    rsync -a --exclude='.*' --exclude='*.psd' --exclude='*.sketch' \
        --exclude='*.xcf' --exclude='*.ai' "$COVER_SRC/" "$COVER_DIST/"
else
    # fallback: cp, still skip dotfiles
    (cd "$COVER_SRC" && find . -type f ! -path './.*' ! -name '.*' -print0) \
        | while IFS= read -r -d '' f; do
        mkdir -p "$(dirname "$COVER_DIST/$f")"
        cp "$COVER_SRC/$f" "$COVER_DIST/$f"
    done
fi

FILE_COUNT=$(find "$COVER_DIST" -type f | wc -l | tr -d ' ')
say "copied ${FILE_COUNT} files"

# ── version badge injection ────────────────────────────────────────────────

if [ -n "$VERSION" ]; then
    say "injecting version badge: v${VERSION}"
    find "$COVER_DIST" -type f -name '*.html' -print0 | while IFS= read -r -d '' f; do
        # replace {{VERSION}} placeholders
        if grep -q '{{VERSION}}' "$f" 2>/dev/null; then
            if [[ "$OSTYPE" == "darwin"* ]]; then
                sed -i '' "s/{{VERSION}}/v${VERSION}/g" "$f"
            else
                sed -i "s/{{VERSION}}/v${VERSION}/g" "$f"
            fi
        fi
    done
else
    warn "skipping version injection (no version detected)"
fi

# ── build metadata stamp ────────────────────────────────────────────────────

say "stamping build metadata (commit=${COMMIT_HASH})"
find "$COVER_DIST" -type f -name '*.html' -print0 | while IFS= read -r -d '' f; do
    STAMP="<!-- repo:${COMMIT_HASH} built:${BUILD_TIME} -->"
    if [[ "$OSTYPE" == "darwin"* ]]; then
        sed -i '' "1s/^/${STAMP}\n/" "$f"
    else
        sed -i "1s/^/${STAMP}\n/" "$f"
    fi
done

# ── gif validation ──────────────────────────────────────────────────────────

GIFS=$(find "$COVER_DIST" -type f -name '*.gif' 2>/dev/null || true)
if [ -n "$GIFS" ]; then
    say "validating gif files (max ${MAX_GIF_SIZE_MB} MB)"
    while IFS= read -r gif; do
        SIZE_BYTES=$(stat -f%z "$gif" 2>/dev/null || stat -c%s "$gif" 2>/dev/null || echo 0)
        SIZE_MB=$(( SIZE_BYTES / 1048576 ))
        if [ "$SIZE_MB" -gt "$MAX_GIF_SIZE_MB" ]; then
            die "gif too large: ${gif} (${SIZE_MB} MB > ${MAX_GIF_SIZE_MB} MB limit)"
        fi
        # nonzero frame count
        if command -v gifsicle >/dev/null 2>&1; then
            FRAMES=$(gifsicle --info "$gif" 2>/dev/null | grep -c 'image #' || echo 0)
            if [ "$FRAMES" -eq 0 ]; then
                die "gif has zero frames: ${gif}"
            fi
            say "  ${gif##*/}: ${SIZE_MB} MB, ${FRAMES} frames"
        else
            say "  ${gif##*/}: ${SIZE_MB} MB (install gifsicle for frame count)"
        fi
    done <<< "$GIFS"
else
    say "no gif files to validate"
fi

# ── secret scan ─────────────────────────────────────────────────────────────

if [ "$SKIP_SECRET_SCAN" = "1" ]; then
    warn "secret scan skipped (SKIP_SECRET_SCAN=1)"
else
    say "running secret scan"
    HITS=$(python3 -c "
import re, pathlib, sys

root = pathlib.Path('$COVER_DIST')
# patterns that look like live secrets — broad enough to catch common leaks
# without excessive false positives on hex colors / base64 data uris
PATTERNS = [
    (r'sk-[a-zA-Z0-9]{32,}',           'Stripe secret key'),
    (r'(?i)api[_-]?key[\"\': ]*[a-zA-Z0-9_\-]{20,}', 'API key in plaintext'),
    (r'(?i)secret[\"\': ]*[a-zA-Z0-9_\-+/=]{20,}',   'secret in plaintext'),
    (r'(?i)token[\"\': ]*[a-zA-Z0-9_\-+/=]{20,}',    'token in plaintext'),
    (r'(?i)password[\"\': ]*[a-zA-Z0-9_\-!@#\$%^&*()]{8,}', 'password in plaintext'),
    (r'-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----', 'private key block'),
    (r'ghp_[a-zA-Z0-9]{36}',           'GitHub personal access token'),
    (r'(?i)cloudflare[._-]?(api[._-]?)?(token|key)[\"\': ]*[a-zA-Z0-9_\-]{20,}', 'Cloudflare credential'),
    (r'AKIA[0-9A-Z]{16}',              'AWS access key ID'),
]

total_hits = 0
for f in root.rglob('*'):
    if not f.is_file():
        continue
    try:
        text = f.read_text(errors='replace')
    except Exception:
        continue
    for pattern, label in PATTERNS:
        for m in re.finditer(pattern, text):
            total_hits += 1
            ctx = text[max(0,m.start()-20):m.end()+20].replace('\n',' ')
            print(f'  HIT: {f.relative_to(root)} [{label}] …{ctx}…', file=sys.stderr)
if total_hits:
    sys.exit(1)
else:
    print('  clean')
" 2>&1) || die "secret scan found ${HITS} potential leak(s) — remove secrets from source and rebuild (or set SKIP_SECRET_SCAN=1)"
    echo "$HITS"
fi

# ── manifest + summary ──────────────────────────────────────────────────────

say "build complete"
printf "${CYAN}%8s  %s${NC}\n" "SIZE" "FILE"
find "$COVER_DIST" -type f | sort | while IFS= read -r f; do
    SIZE=$(stat -f%z "$f" 2>/dev/null || stat -c%s "$f" 2>/dev/null || echo 0)
    REL="${f#$COVER_DIST/}"
    printf "%8s  %s\n" "$(numfmt --to=iec "$SIZE" 2>/dev/null || echo "${SIZE}B")" "$REL"
done

TOTAL=$(du -sh "$COVER_DIST" 2>/dev/null | cut -f1 || echo '?')
say "total dist size: ${TOTAL}"
say "ready for deploy → npx wrangler pages deploy ${COVER_DIST}/ --project-name <name> --branch main"
