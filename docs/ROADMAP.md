# ROADMAP — actop

> Orders the open hardware/metric-coverage gaps and records what is deliberately out of
> scope. Track letters are stable ids referenced from TODOs and git history (N1, E2, U1…).
>
> **Standing rule: one line and a pointer, never a narrative.** "Why" → the linked design
> plan. "What shipped" → `CHANGELOG.md`. The argument that was had → git history. An **open**
> gap gets a TODO and a sequence row, not a paragraph.

**Baseline (v1.4.x):** Apple Silicon IOReport-based CLI + TUI telemetry monitor; CPU/GPU/ANE
utilization, per-core frequency, memory/bandwidth, thermal state, fan RPM, per-process GPU
time, SMC die temperatures; NDJSON/Prometheus export; Textual dashboard with sparklines;
public Python API (`Monitor`, `Profiler`, `AsyncMonitor`); Homebrew + PyPI distribution;
16 built-in M1–M4 SoC profiles; accessibility palettes (`--palette`). Net/disk I/O
shipped v1.8.0. No menu-bar mode, no in-app update notice.

## State — 2026-08

- **Shipped:** v1.8.0 — **net/disk I/O (N track):** getifaddrs/if_data network +
  IOKit AppleAPFSVolume disk rates, surfaced in TUI/NDJSON/Prometheus; plus the
  **reading-plane audit closes (F track):** deprecated `*_gb`/`*_GB`/`rss_mb`/
  `*_gigabytes` aliases and the `--alert-swap-rise-gb` CLI alias removed;
  v1.7.2 — **export parity (E track):** AlertEngine integrated into
  `--json`/`--serve`, `--json-processes`/`--serve-processes` flags,
  alerts/throttle/session energy reach both backends; v1.7.1 — `--theme` flag +
  live theme cycling; layering cleanup LC-1→LC-3; fan RPM, per-process GPU,
  accessibility palettes, GPU Renderer/Tiler via `IOAccelerator`, reading-plane
  audit. → `CHANGELOG.md` `[1.2.x]`–`[1.8.0]`
- **Board:** one open track — **S** sudo elevation for per-process GPU/energy
  attribution. **First move is S1:** the `--sudo` re-exec + `u`-key password
  modal per `docs/TODO-sudo-mode.md`. **E** (export parity) shipped v1.7.2;
  **L** (LC-4 rolling stats) dropped 2026-08-11.

## Tracks

### Open

- **S — Sudo elevation for per-process GPU/energy attribution.** Without root,
  `gpu_time_share` and `attributed_w` read `0.0`/`None` — the underlying
  `powermetrics` process-attribution path needs elevated access. Two paths: `--sudo`
  CLI flag re-executes under `sudo`; `u` keybinding in the TUI pops a password
  modal, validates, and restarts the session with state preserved via a temp file.
  Additive opt-in — unprivileged mode stays the default and is unchanged.
  → `docs/TODO-sudo-mode.md`

### Later / unscheduled

- **U — Update-available notice.** Detect when a newer stable `actop` has been published
  (PyPI JSON API → `info.version` vs `importlib.metadata`), surface as a startup-splash
  banner + status-bar token. Isolated in a new `version_check.py` module; off the render
  path (background thread, short timeout, fail-silent); opt-out via `--no-update-check`
  + env var; cached with ~24h TTL. Nice-to-have, not a launch gate.
  → `docs/TODO-version-check-2026-08-09.md` (plan to be written)

### Deliberately not doing (decision records)

- **Menu bar mode.** A second application surface (PyObjC/ctypes `NSStatusBar` bridge,
  persistent background process, `launchd` plist, IPC, packaging). mactop already owns
  this niche; actop's differentiator is the programmable Python API. Revisit after the
  initial launch cycle, not before.
- **ML/APM frameworks.** actop stays scoped to one thesis: a fast, unprivileged,
  resource-efficient Apple Silicon telemetry monitor. No ML inference profiling, no
  APM/tracing, no cluster monitoring.
- **i18n.** English-only; no locale infrastructure. Act on demand signals, not upfront.
- **Process-kill from the dashboard.** mactop feature; actop is a monitor, not a process
  manager.
- **Battery telemetry.** OS-level (not SoC-level), breaks the "unprivileged + thin"
  constraint. `mactop` covers it.
- **Config persistence for dashboard layout.** The dashboard layout is CLI-driven
  (`--layout`, `--no-cores`); no config file, no serialization of TUI state.
- **Built-in synthetic load generator (e.g. `macmon stress`).** actop is a monitor, not a
  stress tool. The existing `scripts/ane_load.py` is a dev helper — not a shipped CLI
  subcommand — and that's the right place for it.
- **Concurrent NDJSON + Prometheus from one process.** `--json` and `--serve` are
  mutually exclusive run modes today. macmon's `serve` exposes both on one process
  (1/2-converged, weakly). Making them composable is plausible (a single `serve` that
  also streams NDJSON to stdout, or keeps `/metrics` alive while writing lines), but
  no consumer has asked for it — defer until real demand appears.
