# TODO — Architecture and Distribution Roadmap (2026+)

Roadmap for hardening `agtop`'s core. We stay scoped to one thesis — **a fast, unprivileged, resource-efficient Apple Silicon telemetry monitor** — and reject feature creep into ML/APM frameworks.

---

## Tier 1 — Core Hardening & Metrics (Budget: <0.5% Idle CPU)

Correctness, stability, and metric-coverage work on the sampling core. The
`<0.5% idle CPU` figure is a **budget** every item must respect, not a single
objective — new metrics earn their place only if they stay within it.

*   [x] **Pin and guard kernel struct offsets** — done.
    *   `get_native_processes` (`native_sys.py`) unpacks `proc_taskallinfo` at offsets verified on Sonoma/Sequoia arm64. A kernel struct change silently returns garbage if unguarded.
    *   ✅ Offsets are pinned as named module constants (`_PTAI_SIZE`, `_OFF_COMM`, `_OFF_NAME`, `_OFF_PROC_METRICS`, `_OFF_THREADS`) with the struct layout documented in one place.
    *   ✅ The `tests/test_native_sys.py` guard is hardened: asserts ≥5 processes report non-zero RSS, ≥5 report ≥1 thread, RSS stays under a 1 TB ceiling, and the current process decodes a printable name — offset drift fails loudly.
    *   ✅ The guard surfaces the running macOS version (`platform.mac_ver()`) in its failure messages, so drift on a new macOS major reads as a version-coverage gap rather than a bare assertion failure.
*   [x] **Memory-stability test** — done (`tests/test_sampler.py::test_sampler_resident_memory_stays_flat_over_many_cycles`).
    *   CFRelease/IOObjectRelease handling is already correct on inspection; what was missing is a regression guard against a dropped `cf_release` leaking a CFDict per cycle.
    *   ⚠️ Could not drive this through `Monitor`/`Profiler`: `Monitor.__init__` floors the interval at `max(1, int(interval_s))` and `get_snapshot()` sleeps it, so 10k cycles would take ~2.7 h. The test instead builds the sampler via the public `create_sampler(interval=1, subsamples=1)` factory and loops `sample()` directly (a real hardware-I/O entrypoint, no `time.sleep` on the `subsamples <= 1` path).
    *   📐 Cycle count tuned to the empirical signal, not raw volume: each cycle is a real ~37 ms kernel round-trip (10k cycles = ~7 min), while a clean run grows only ~0.03 MB / 3000 cycles and a real leak is ~128 KB/cycle. So 2000 cycles against a 5 MB `getrusage(RUSAGE_SELF).ru_maxrss` bound separates leak from noise by >100x in ~80 s. Verified the guard fires: inducing the leak grew RSS 64 MB over 500 cycles. Marked `@pytest.mark.local`.
*   [x] **Memory bandwidth sampling** — done. The Mem BW row is live: total DRAM bandwidth is read in-process/unprivileged from the **`PMP` / `DCS BW`** IOReport group. Full spike + evidence: [SPIKE-ioreport-dcs-bandwidth.md](SPIKE-ioreport-dcs-bandwidth.md).
    *   [x] **(a) Spike — confirm the in-process source.** ✅ Source is `PMP`/`DCS BW`, unprivileged. The data is a **residency histogram** (32 bandwidth buckets per channel), *not* the byte counter the prior scoping assumed — GB/s = Σ(bucket·time)/Σ(time), no interval division.
    *   [x] **(b) Implement.** ✅ `("PMP","DCS BW")` added to the subscription; `_convert` sums all `AMCC* RD+WR` residency histograms into `total_gbps` (`_compute_bandwidth_gbps`), multi-die safe; `api.py` maps it straight to `bandwidth_gbps`. The stub byte-counter keys are gone.
    *   ⚠️ **Per-agent breakdown dropped (deliberately).** The per-agent DCS channels (`EACC/PACC/AGX/AVE/…`) **hard-cap at 32 GB/s** while `AMCC` spans ~1 TB/s — under a 350 GB/s load both P-clusters peg at 32, so per-agent attribution is unreliable at the bandwidths that matter. Only the AMCC total is trustworthy, so only the total ships.
    *   📐 **Budget held via state-extraction filter.** Subscribing to the 94-channel group is the irreducible kernel cost; the dominant *parse* cost is per-state extraction. `IOReportSubscription.delta()` now takes an `extract_states` predicate so only `AMCC*` states are read (the other ~90 channels are skipped). Measured marginal cost: **+0.39% @1s with the filter** vs. +0.70% unfiltered — the filter is what keeps it under the `<0.5% idle CPU` budget.

---

## Tier 2 — Distribution & Packaging

*   [x] **Recommend `uv tool install`** in docs/install instructions — sandboxed envs, no interpreter drift. (Option A below — done.)
*   [ ] **Stand-alone binary** via `Nuitka`/`PyInstaller` (bundling Textual), published from the GitHub Release pipeline so users can run without Python installed. (Option B below — deferred, conditional.)

### Analysis: `uv tool install` (A) vs. stand-alone binary (B)

Both items target the same gap — letting users run `agtop` without managing a Python install — but sit at very different points on the effort/reward curve.

**Option A — Recommend `uv tool install`**

| | |
|---|---|
| Effort | ~Zero. A docs change. No build pipeline. |
| Requires Python on host? | No — `uv` fetches and manages its own CPython. |
| Solves interpreter drift? | Yes — each tool gets a sandboxed env (the point of `uv tool`). |
| ctypes/dylib risk | None. Real CPython against system frameworks (`libIOReport.dylib`, CoreFoundation, IOKit) exactly as today. |
| Textual risk | None. Normal wheel-style install; package data ships as usual. |
| Update story | `uv tool upgrade`. Trivial. |
| Maintenance burden | Negligible — it's documentation. |

Catch: the user must have `uv` installed — but `uv` is the de-facto standard and a one-liner to get.

**Option B — Stand-alone binary (Nuitka / PyInstaller)**

| | |
|---|---|
| Effort | High. Per-arch CI build, codesigning/notarization (else Gatekeeper friction), smoke-test, release-asset upload. |
| Requires Python on host? | No — fully self-contained. The only thing it does that A doesn't. |
| ctypes/dylib risk | Low-but-real. System-dylib `CDLL` loads are fine (resolved at runtime); risk is our own dynamically-referenced modules / hidden imports. |
| Textual risk | Moderate. Textual ships `.tcss`/data files and lazy imports; bundlers need hooks to pull them. Nuitka handles this better than PyInstaller. |
| Codesigning | Required in practice — needs an Apple Developer cert in CI secrets. |
| Update story | Manual re-download; no `upgrade`. |
| Artifact size | Tens of MB. Ongoing breakage on Textual/Python/macOS-notarization changes. |

**Decision: ship A now; treat B as conditional.** A delivers ~90% of the "no Python hassle" benefit for ~1% of the effort and zero new failure surface — pure upside. B only earns its keep if a user segment genuinely *cannot* install `uv`/Python (e.g. locked-down corporate Macs); for them a signed binary is the only path, but it brings a recurring notarization + CI tax and is the riskiest thing to bundle given our ctypes + Textual stack. We also already ship a **Homebrew** formula, which itself gives a Python-free install on macOS — so between `brew` (Homebrew users) and `uv` (everyone else), B's unique audience is narrow. If B is ever revived, prefer **Nuitka** and budget for codesigning/notarization from day one.

---

## Tier 3 — Hardware & Metric Coverage

*   [ ] **Unknown-SoC fallback engine** — estimate reference package power/bandwidth from PMGR voltage states when `soc_profiles.py` has no match for a new chip (M5/M6+).
*   [ ] **Local JSON telemetry socket** — unprivileged socket/named pipe streaming metrics, so menu-bar apps and widgets can consume them without a full TUI.
