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
release (see [`docs/DESIGN-sdlc-cicd-release.md`](docs/DESIGN-sdlc-cicd-release.md)). We credit
reporters unless you ask to remain anonymous.
