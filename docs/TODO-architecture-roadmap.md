# TODO — Architecture and Distribution Roadmap (2026+)

Roadmap for hardening `actop`'s core. We stay scoped to one thesis — **a fast, unprivileged, resource-efficient Apple Silicon telemetry monitor** — and reject feature creep into ML/APM frameworks.

The prior round of this roadmap (kernel-offset pinning, memory-stability guard, memory-bandwidth sampling, cross-platform ctypes guards, headless NDJSON/Prometheus export) shipped in full; see `docs/DESIGN-system.md` for the as-built design of each. Two items were evaluated and explicitly rejected (stand-alone binary, generic unknown-SoC voltage-estimator) — their rationale is preserved in `docs/DESIGN-system.md` §1.1 and §3.7, not repeated here.

---

## Must-Have — Hardware & Metric Coverage

Closes actop's two biggest feature gaps vs. `mactop` (per `docs/REVIEW-architecture-comparison.md`) that are cheap enough to justify before/around the next release.

*   [ ] **Fan RPM via SMC** — low overhead, reuses existing plumbing.
    *   `smc.py`'s key-discovery sweep (`_discover_temperature_keys`, `smc.py:251-288`) already does the hard part: `IOServiceOpen` + `IOConnectCallStructMethod` key iteration over every SMC key, filtering by 4-char prefix and SMC type. It currently only keeps `flt ` (4-byte float) keys prefixed `Tp`/`Te`/`Tg`.
    *   Fan keys (`F0Ac` actual RPM, `F0Mn`/`F0Mx` min/max, `FNum` fan count) live in the same key space, typically SMC type `fpe2` (fixed-point) rather than `flt ` — verify the exact type per chip generation during implementation, Apple Silicon SMC fan-key types haven't been confirmed here yet.
    *   Scope: one more type/prefix branch in the existing discovery loop, a new `get_fan_rpms()`-style reader mirroring `get_temperatures()`, and a TUI row/label. No new native library, no new privilege, no new subprocess.
    *   Graceful degradation: fanless Macs (MacBook Air) will simply discover zero fan keys — same "hide the row, don't fabricate a zero" pattern already used for `bandwidth_available` (`DESIGN-system.md` §3.5).
*   [ ] **Net / disk I/O via native ctypes** — moderate effort; deliberately widens scope, tracked here instead of silently declining it.
    *   `DESIGN-system.md` §3.6 currently lists this as a non-goal on the grounds that it's a `psutil`-shaped feature orthogonal to the IOReport-first SoC-power focus. This item overrides that stance as a **must-have**, on the condition it's built via native ctypes (matching `native_sys.py`'s existing pattern — `sysctlbyname`, direct struct unpacking), not by reintroducing `psutil`.
    *   Network: candidate source is `sysctl net.link.generic.system.stats` (or `getifaddrs` + `if_data` struct) for aggregate rx/tx bytes — same MIB-based approach `native_sys.py` already uses for swap (`vm.swapusage`) and process args (`KERN_PROCARGS2`).
    *   Disk: candidate source is IOKit `IOBlockStorageDriver`/`IOMedia` registry entries' `Statistics` property (bytes/ops read+write), read the same way `gpu_registry.py` walks `IOAccelerator` entries via `IORegistryEntryGetChildIterator`.
    *   Needs a feasibility spike before implementation (same discipline as the bandwidth work: confirm the exact channel/registry path in-process and unprivileged before committing to a data model) — do not assume the candidates above are final without verifying on-device.
    *   Update `DESIGN-system.md` §3.6 to remove the non-goal framing once this ships.

---

## Deferred — Post-Launch, Low Priority

*   [ ] **Menu bar mode** — explicitly deferred from the first market-promo push (`docs/RUNBOOK-launch-and-growth.md`); revisit only after the initial launch cycle, not before.
    *   Not a feature add — a second application surface. Textual is a terminal-render framework; a menu bar presence needs `NSStatusBar` (PyObjC or ctypes/Objective-C-runtime bridging, similar in spirit to `native_sys.py`'s existing `NSProcessInfo` bridge but a much larger API surface), a persistent background process, a `launchd` install, and IPC between a backgrounded sampler and the TUI.
    *   Real cost centers: application lifecycle management, icon/menu rendering, packaging (a `launchd` plist alongside the existing Homebrew/PyPI distribution), and a second UI to keep in sync with every future dashboard metric.
    *   Priority: low. `mactop` already owns this niche (native menu-bar + overlay HUD, per `docs/REVIEW-architecture-comparison.md`); actop's differentiator is the programmable Python API, not UI surface count. Do not start this until Tier 1 (fan RPM, net/disk I/O) ships and the launch runbook's post-launch loop is underway.
