# TODO — Architecture and Distribution Roadmap (2026+)

Roadmap for hardening `actop`'s core. We stay scoped to one thesis — **a fast, unprivileged, resource-efficient Apple Silicon telemetry monitor** — and reject feature creep into ML/APM frameworks.

The prior round of this roadmap (kernel-offset pinning, memory-stability guard, memory-bandwidth sampling, cross-platform ctypes guards, headless NDJSON/Prometheus export) shipped in full; see `docs/DESIGN-system.md` for the as-built design of each. Two items were evaluated and explicitly rejected (stand-alone binary, generic unknown-SoC voltage-estimator) — their rationale is preserved in `docs/DESIGN-system.md` §1.1 and §3.7, not repeated here.

---

## Prioritization (2026-07-02) — convergence gaps before breadth

`docs/REVIEW-architecture-comparison.md` §4 draws a distinction this roadmap now adopts explicitly, because it changes the order of the work below. Of everything `mactop`/`macmon` have that `actop` lacks, only **two** capabilities are *converged* — implemented independently by **both** actively-developed peers, the signal that the market treats them as table stakes rather than one project's pet feature:

1.  **Runtime theme/color-cycling keybind** — mactop (`c`/`b`, party mode) and macmon (`c`, 6 themes) both have it; actop is static truecolor with `NO_COLOR`/tier degradation and no cycle key.
2.  **Richer per-fan metadata** (max RPM beyond a bare current-RPM list) — mactop (target + Auto/Manual mode) and macmon (name + max_rpm per fan) both read the max key. ✅ **Closed in v1.2.3:** `read_fan_info()` now returns `FanReading(current, max)` per fan. Only the theme-cycling keybind remains converged-and-open.

Both are cheap. **Everything else** mactop has that actop doesn't — net/disk I/O, Thunderbolt/RDMA, battery, i18n, menu-bar/overlay, process-kill, config persistence — is **single-peer breadth** (mactop pursuing a "do everything" scope neither macmon nor actop shares), not a convergence signal.

**The tension, stated plainly:** the *Net / disk I/O* item below is filed as a **must-have**, but §4 shows it is single-peer breadth (mactop-only; macmon deliberately doesn't do it), *not* a converged expectation. We keep it — it is a conscious bet on narrowing the gap to the breadth leader, and the roadmap tracks it openly rather than silently declining it — but we **sequence the two cheap converged wins first**, because they close every 2/2-converged gap in the comparison table for a fraction of the effort and directly answer real peer pressure, whereas net/disk answers scope ambition.

**Order of work (all post-launch — launch gates on none of it; see `docs/RUNBOOK-launch-and-growth.md`):**

1.  **Convergence quick wins** ✅ **DONE** (plan doc removed post-ship; as-built in `CHANGELOG.md` [1.4.1] + `docs/DESIGN-system.md` §5.2). Fan telemetry shipped v1.2.3; the color gap shipped v1.4.1 as the `--palette` accessibility MVP (colorblind-safe / mono), not the originally-scoped decorative runtime cycle keybind — that keybind was evaluated and **deliberately deferred** (peer parity with a decorative feature; the accessibility value is delivered by the startup flag). Both converged gaps addressed.
2.  **Net / disk I/O** → `docs/TODO-net-disk-io-2026-07-02.md` (spike complete, impl-ready). Larger surface, breadth bet, no convergence pressure. **Now the next item.**

---

## Must-Have — Hardware & Metric Coverage

Closes actop's biggest feature gaps vs. the peer field (per `docs/REVIEW-architecture-comparison.md`). Each item now has its own implementation-ready plan; this section is the index and the priority call (see the Prioritization note above).

*   [x] **Fan RPM via SMC** — shipped v1.2.2, current+max in v1.2.3. `smc.py` discovers per-fan actual-RPM keys (`F{n}Ac`, confirmed `flt ` type on Apple Silicon — not `fpe2` as originally guessed — count from `FNum`) plus the sibling max key `F{n}Mx`; `SMCReader.read_fan_info()` returns `FanReading(current, max)` per fan, `fan_available` mirrors the temperature reader, and the TUI's "Fan" row renders `current/max` (fans joined by `·`) and hides entirely on fanless Macs via the `bandwidth_available` hide-row pattern. See `docs/DESIGN-system.md` §4.3.
*   [x] **Convergence quick wins** — both converged (2/2-peer) gaps addressed; as-built in `CHANGELOG.md` [1.4.1] + `docs/DESIGN-system.md` §5.2 (plan doc removed post-ship). Fan telemetry (Feature B — `F{n}Mx` current+max) shipped v1.2.3, and the color gap shipped v1.4.1 as the `--palette {thermal,viridis,mono}` accessibility MVP. The originally-scoped runtime theme/color-cycling keybind (Feature A) was **deliberately deferred** — an ROI review found the peers converged on *decorative* cycling while actop's answer is *accessibility* palettes (a different, better feature), so the startup flag delivers the value and set-once is the right model; the keybind is a purely additive follow-on if peer parity is ever wanted.
*   [ ] **Net / disk I/O via native ctypes (do second)** → **`docs/TODO-net-disk-io-2026-07-02.md`.** Moderate effort; deliberately widens scope. **Feasibility spike complete (2026-07-02)** — verified on-device (M4 Max, unprivileged) and cross-checked against `mactop`'s shipped implementation; the full impl-ready design (exact syscalls, struct layouts, IOKit matching strings, property keys, aggregation, and where each plugs into the sampler/models/TUI/API/export layers) now lives in that dedicated plan. Summary of the verified approach: **network** via `getifaddrs()`/`freeifaddrs()` walking `AF_LINK` entries and casting `ifa_data` → `struct if_data` (one new ctypes binding in `native_sys.py`; the originally-guessed `net.link.generic.system.stats` MIB does not exist on-device); **disk** via IOKit `IOServiceMatching("AppleAPFSVolume")` summing each volume's `Statistics` dict, with `IOServiceMatching("IOBlockStorageDriver")` as the non-APFS fallback (read the way `gpu_registry.py` walks `IOAccelerator` — no new IOKit binding classes); both **aggregated** as `(current_total - previous_total) / elapsed_seconds`, the same delta-over-interval shape as `sampler._compute_bandwidth_gbps` (§3.5). Per §4 this is single-peer breadth, so it follows the convergence quick wins. Update `DESIGN-system.md` §3.6 to drop the non-goal framing once it ships.

---

## Deferred — Post-Launch, Low Priority

*   [ ] **Menu bar mode** — explicitly deferred from the first market-promo push (`docs/RUNBOOK-launch-and-growth.md`); revisit only after the initial launch cycle, not before.
    *   Not a feature add — a second application surface. Textual is a terminal-render framework; a menu bar presence needs `NSStatusBar` (PyObjC or ctypes/Objective-C-runtime bridging, similar in spirit to `native_sys.py`'s existing `NSProcessInfo` bridge but a much larger API surface), a persistent background process, a `launchd` install, and IPC between a backgrounded sampler and the TUI.
    *   Real cost centers: application lifecycle management, icon/menu rendering, packaging (a `launchd` plist alongside the existing Homebrew/PyPI distribution), and a second UI to keep in sync with every future dashboard metric.
    *   Priority: low. `mactop` already owns this niche (native menu-bar + overlay HUD, per `docs/REVIEW-architecture-comparison.md`); actop's differentiator is the programmable Python API, not UI surface count. Do not start this until Tier 1 (fan RPM, net/disk I/O) ships and the launch runbook's post-launch loop is underway.
