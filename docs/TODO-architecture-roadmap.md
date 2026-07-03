# TODO — Architecture and Distribution Roadmap (2026+)

Roadmap for hardening `actop`'s core. We stay scoped to one thesis — **a fast, unprivileged, resource-efficient Apple Silicon telemetry monitor** — and reject feature creep into ML/APM frameworks.

Completed work is **not** tracked here — its as-built design is folded into `docs/DESIGN-system.md` (with dated entries in `CHANGELOG.md`). This file holds only what remains open.

---

## Must-Have — Hardware & Metric Coverage

*   [ ] **Net / disk I/O via native ctypes** → **`docs/TODO-net-disk-io-2026-07-02.md`.** Moderate effort; deliberately widens scope. **Feasibility spike complete (2026-07-02)** — verified on-device (M4 Max, unprivileged) and cross-checked against `mactop`'s shipped implementation; the full impl-ready design (exact syscalls, struct layouts, IOKit matching strings, property keys, aggregation, and where each plugs into the sampler/models/TUI/API/export layers) lives in that dedicated plan. Summary of the verified approach: **network** via `getifaddrs()`/`freeifaddrs()` walking `AF_LINK` entries and casting `ifa_data` → `struct if_data` (one new ctypes binding in `native_sys.py`; the originally-guessed `net.link.generic.system.stats` MIB does not exist on-device); **disk** via IOKit `IOServiceMatching("AppleAPFSVolume")` summing each volume's `Statistics` dict, with `IOServiceMatching("IOBlockStorageDriver")` as the non-APFS fallback (read the way `gpu_registry.py` walks `IOAccelerator` — no new IOKit binding classes); both **aggregated** as `(current_total - previous_total) / elapsed_seconds`, the same delta-over-interval shape as `sampler._compute_bandwidth_gbps` (§3.5). Update `DESIGN-system.md` §3.6 to drop the non-goal framing once it ships.
    *   **Why a must-have despite being single-peer breadth:** per `docs/REVIEW-architecture-comparison.md` §4 this is *single-peer breadth* (mactop-only; macmon deliberately omits it), **not** a converged peer expectation. We keep it as a conscious bet on narrowing the gap to the breadth leader, tracked openly rather than silently declined. Post-launch — launch gates on none of it.

---

## Nice-to-Have — Distribution & UX

*   [ ] **Update-available notice** — detect when a newer stable `actop` has been published and surface it non-intrusively: a **startup-splash banner** (e.g. `update available: v1.5.0 — brew upgrade actop / pip install -U actop`) plus a compact token in the app **title/subtitle or status bar**. **Why:** the running version is shown (the subtitle already reads `v1.4.x · <chip> · <topo>`) but there is no signal that it is *stale*, so users on Homebrew/PyPI get no in-app nudge to upgrade. Low-risk, high-affordance.
    *   **Approach:** query the canonical publish target — PyPI's JSON API (`https://pypi.org/pypi/actop/json` → `info.version`) — and compare against `__version__` (`importlib.metadata`); the latest GitHub release tag is an equivalent fallback. Isolate the network behind a new small module (e.g. `version_check.py`) exposing a pure "latest stable, or None" function so the TUI never imports networking inline.
    *   **Constraints (non-negotiable — match actop's unprivileged, resource-light ethos):** the check must run **off the render path** (background thread/async with a short timeout) and **fail silent** — no network, DNS failure, or slow endpoint may ever delay startup, raise, or degrade the dashboard; when it can't resolve, show nothing. Make it **opt-out** (`--no-update-check` + honor an env var) and **cache** the last result with a TTL (~24h) in the user cache dir so every launch does not hit PyPI. A startup network call is a mild privacy/telemetry consideration — document it and keep the opt-out obvious.
    *   **Where it plugs in:** `version_check.py` (new, network-isolated) → `tui/app.py` splash (`_build_splash`), subtitle (`sub_title`), and the app-level `#status-line`; no sampler/model/API changes (this is presentation + a distribution check, not a hardware metric, so it stays out of the L1/L2 layers).
    *   **Priority:** nice-to-have; not a launch gate. Ships independently of net/disk I/O.

---

## Deferred — Post-Launch, Low Priority

*   [ ] **Menu bar mode** — explicitly deferred from the first market-promo push; revisit only after the initial launch cycle, not before.
    *   Not a feature add — a second application surface. Textual is a terminal-render framework; a menu bar presence needs `NSStatusBar` (PyObjC or ctypes/Objective-C-runtime bridging, similar in spirit to `native_sys.py`'s existing `NSProcessInfo` bridge but a much larger API surface), a persistent background process, a `launchd` install, and IPC between a backgrounded sampler and the TUI.
    *   Real cost centers: application lifecycle management, icon/menu rendering, packaging (a `launchd` plist alongside the existing Homebrew/PyPI distribution), and a second UI to keep in sync with every future dashboard metric.
    *   Priority: low. `mactop` already owns this niche (native menu-bar + overlay HUD, per `docs/REVIEW-architecture-comparison.md`); actop's differentiator is the programmable Python API, not UI surface count. Do not start this until net/disk I/O ships and the initial launch cycle's early engagement window has passed.
