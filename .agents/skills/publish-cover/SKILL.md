---
name: publish-cover
description: >-
  "Publish, redeploy, or update the project landing page / cover to Cloudflare
  Pages. Trigger words: publish, redeploy, cover, landing page. Boundary: do
  NOT use for CI docs, internal-only artifacts, or README badges."
---

# publish-cover

Route + decision layer for publishing the project landing page (cover) to
Cloudflare Pages. **This file decides when and whether to act;** the
deterministic build work lives in the bundled
`scripts/build-cover-dist.sh`. The deploy step requires explicit human
confirmation — no auto-deploy.

## Decision gates (must pass before invoking build)

### Gate 1 — Source exists

```bash
ls "${COVER_SRC:-cover}/index.html" >/dev/null 2>&1
```

If `COVER_SRC` is unset, defaults to `cover/` at the repo root. The script
refuses to run if no `index.html` is found. If the cover source does not exist
yet, scaffold it first — this skill does not author content.

### Gate 2 — Curated subset

The build script copies `COVER_SRC` into `dist-cover/`, then injects dynamic
assets (version badge, build metadata). Before proceeding, list what will be
published:

```bash
find "${COVER_SRC:-cover}" -type f | sort
```

Ask: *"Publish this subset? Any files that should be excluded?"* Files
not intended for public deployment (drafts, notes, PSDs, unused assets) must be
removed or `.gitignore`d before the build.

### Gate 3 — Third-party material

If the cover references external fonts, images, or libraries (CDN or bundled):

- Confirm each has an explicit license and attribution path.
- If bundled, confirm the license file is included in the dist.

Ask: *"All third-party assets accounted for and licensed?"*

### Gate 4 — Untracked / dirty working tree

```bash
git diff --quiet && git diff --cached --quiet
```

The build script stamps the current commit hash into the dist. Publishing
uncommitted changes is a footgun. If the tree is dirty, flag it and ask
whether to proceed (the commit stamp will read `dirty`).

## Flow

```
Gate 1–4  →  Build  →  Audit  →  [MANUAL CONFIRM]  →  Deploy  →  Verify
```

### Step 1 — Build

```bash
bash .agents/skills/publish-cover/scripts/build-cover-dist.sh
```

What the script does deterministically:

| Phase | Action |
|---|---|
| Copy | `COVER_SRC` → `COVER_DIST` (wiped first) |
| Rewrite | Asset paths for deployment (relative → deployment-safe) |
| Inject | Version badge from `pyproject.toml` (or `package.json` fallback) |
| Stamp | `<!-- repo:<hash> built:<ISO-8601> -->` in every `.html` |
| Validate | Every `.gif` in dist: max file size, nonzero frame count |
| Scan | Secret scan across all dist files (rejects on match) |
| Report | Manifest + per-file sizes to stdout |

**Env overrides:**

| Env | Default | Purpose |
|---|---|---|
| `COVER_SRC` | `cover/` | Source directory |
| `COVER_DIST` | `dist-cover/` | Build output |
| `MAX_GIF_SIZE_MB` | `5` | Reject gifs larger than this |
| `SKIP_SECRET_SCAN` | `0` | `1` = bypass (escape hatch) |

### Step 2 — Audit

After build, inspect `dist-cover/`:

```bash
open dist-cover/index.html          # visual check
du -sh dist-cover/                   # size at a glance
diff -rq dist-cover/ dist-cover.prev/ 2>/dev/null  # vs last published (if saved)
```

This is a human eye pass — does the page render correctly? Are version
numbers right? Do gif references resolve? The script cannot judge visual
fidelity.

### Step 3 — Deploy (MANUAL, interactive)

**This step requires explicit human confirmation.** Cloudflare Pages deploys
use `wrangler pages deploy` with interactive browser-based OAuth. The agent
must pause here; the human runs this:

```bash
npx wrangler pages deploy dist-cover/ --project-name <project-name> --branch main
```

Confirm before running: *"Deploy dist-cover/ to Cloudflare Pages? This
overwrites the live site."*

Save the previous dist for diffing on the next redeploy:

```bash
rm -rf dist-cover.prev && cp -r dist-cover/ dist-cover.prev
```

### Step 4 — Verify

```bash
curl -sI https://<project>.pages.dev | head -5         # check 200
curl -s https://<project>.pages.dev | grep -o 'v[0-9]\+\.[0-9]\+\.[0-9]\+'  # version matches
```

Acceptance: HTTP 200, version badge matches source of truth, gifs animate,
all links resolve, no broken images.

## Prerequisites (one-time)

```bash
npm install -g wrangler
```

## Gotchas

- **Cloudflare login drift**: `wrangler` sessions expire. `npx wrangler login` first.
- **Secret scan fires**: remove the secret from `COVER_SRC` and rebuild. Dotfiles
  and `.gitignore`d paths inside `cover/` are skipped by the build copy.
- **Stale dist**: always rebuild before deploy. The script wipes `COVER_DIST` on each run.
- **Commit stamp drift**: if you build, commit, then deploy without rebuilding,
  the stamp is one commit behind — visually confusing but not breaking.
