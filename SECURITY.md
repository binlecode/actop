# Security Policy

## Reporting a vulnerability

Please report security issues **privately** — do not open a public issue or PR for an
undisclosed vulnerability.

- **Preferred:** GitHub **private vulnerability reporting** — the *"Report a
  vulnerability"* button under this repository's **Security** tab.
  *(Maintainer: enable it once via Settings → Code security → "Private vulnerability
  reporting". It is free on public repositories.)*
- **Alternatively:** email the maintainer at the address on the project's commits.

Please include: the affected version (`actop --version`), your macOS version + Apple
Silicon chip, reproduction steps, and the impact. You can expect an acknowledgement
within a few days.

## Scope

`actop` is a **sudoless, in-process** monitor by design: it runs unprivileged, spawns
no persistent privileged processes, invokes no `sudo`, and reads Apple Silicon metrics
via ctypes bindings to system frameworks (IOReport, IOKit/SMC, `libproc`) plus `sysctl`
and `system_profiler`. Reports we especially want:

- unexpected privileged behavior or subprocess/`sudo` execution,
- unsafe temporary-file handling,
- a crash or denial-of-service triggered by untrusted local system state,
- leakage of secrets or credentials.

## Supported versions

Security fixes land on the latest released `1.x` line. Please reproduce on the current
release before reporting.

## Handling

Verified reports are fixed through the normal **PR-only** flow and shipped as a patch
release (see `CLAUDE.md` → Release Process). We credit reporters unless you ask to
remain anonymous.

## Release-path secrets (`HOMEBREW_TAP_TOKEN`)

`HOMEBREW_TAP_TOKEN` is the **only** long-lived secret in the release path (PyPI uses
tokenless OIDC). Treat it accordingly:

- **Least privilege — prefer a fine-grained PAT.** Scope it to **only**
  `binlecode/homebrew-actop` with **Contents: Read/write** and nothing else. A classic
  PAT (`repo`, `workflow` scopes) *works*, but its blast radius is every repo the account
  owns — if the CI secret leaks, so does write access to all of them. The fine-grained
  token caps the damage at the tap repo.
- **Set an expiry and rotate.** Give the token a bounded lifetime (e.g. 90–180 days) and
  rotate on schedule or on any suspicion of exposure. Rotation is a one-liner — no
  workflow change needed:
  ```bash
  # paste the new token at the prompt; never pass it as a CLI arg
  gh secret set HOMEBREW_TAP_TOKEN --repo binlecode/actop
  ```
- **Never put the token in a command argument.** Pipe it via **stdin** (as above) so it
  stays out of `ps`/argv and shell history. Do **not** use `--body "<token>"`, and do not
  `echo` it. `gh secret set` encrypts the value client-side (libsodium sealed box) with
  the repo's Actions public key *before* upload over TLS, so GitHub stores only ciphertext
  and never sees the plaintext.
- **Logs are masked, not a safety net.** GitHub auto-redacts the registered secret value
  in workflow logs, but obfuscation tricks can defeat masking — the real protections are
  least-privilege scope + `main` being PR-only (fork-PR runs don't receive secrets).
- **Storage.** The plaintext copy lives only in `~/env-secrets/` (never in the repo);
  GitHub keeps the encrypted-at-rest copy. When you rotate, revoke the old token on
  GitHub and update `~/env-secrets/`.
