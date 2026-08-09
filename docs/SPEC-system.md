# `actop` System Detailed Design

This document provides a highly detailed system design and implementation reference for `actop` (Apple Silicon Top), a terminal-based system monitoring tool. It is written to be strictly grounded in the project's source code and native macOS integration patterns.

---

## 1. System Overview

`actop` is a performance monitoring application for Apple Silicon platforms (macOS) designed to be **sub-millisecond fast, dependency-free, and subprocess-free**. Unlike traditional tools that rely on launching CLI commands (such as `powermetrics` or `ioreg`) or invoking high-overhead Python libraries like `psutil`, `actop` interfaces directly with the macOS kernel, CoreFoundation, and low-level system frameworks using pure-Python `ctypes` bindings.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                                  TEXTUAL TUI                                 │
│          (app.py / widgets.py: HardwareDashboard, ProcessTable, etc.)        │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       │
                                       ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                              API / MONITOR LAYER                             │
│                  (api.py: Monitor / Profiler Snapshot loops)                 │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       │
                                       ▼
┌──────────────────────────────────────┴───────────────────────────────────────┐
│                           METRIC SAMPLING ENGINE                             │
│       (sampler.py / utils.py: IOReportSampler, RAM/CPU/GPU aggregators)      │
└──────────┬─────────────────┬─────────────────┬─────────────────┬──────────┘
           │                 │                 │                 │
           ▼                 ▼                 ▼                 ▼
┌──────────────────┐┌──────────────────┐┌──────────────────┐┌──────────────────┐
│     ioreport      ││    native_sys    ││       smc        ││   gpu_registry   │
│ (ioreport.py:     ││ (native_sys.py:  ││ (smc.py: SMC-key ││ (gpu_registry.py:│
│  libIOReport.dylib││  libSystem,      ││  reads via       ││  IOAccelerator   │
│  bindings)        ││  sysctl, IOKit)  ││  AppleSMC service││  per-pid GPU time│
└──────────────────┘└──────────────────┘└──────────────────┘└──────────────────┘
```

> The diagram shows the runtime data *flow*; the middle is the **L2 data-points layer** (Pillar 5): `api.py` orchestrates the per-frame pull, `analytics.py` derives the judgments (power attribution, throttling, alerts, session energy), and `models.py` defines the `SystemSnapshot` contract the TUI/export consume. `sampler.py`/`utils.py` sit in L1 (acquisition) beneath it.

### Core Architecture Pillars:
1. **Direct Memory Access via `ctypes`**: Zero spawning of shell commands. All virtual memory, swap space, and process listings are pulled directly from memory in microsecond ranges.
2. **Private API Interop**: Uses the private C library `libIOReport.dylib` to capture real-time Energy Model (Joules), DVFS (residency/frequencies), and core active percentages.
3. **Zero Sudo Requirements**: Does not require root privileges. By querying the `AppleSMC` service and targeting the safe non-root `IOReport` channels, the tool runs securely under ordinary user accounts.
4. **Cross-Platform-Safe Imports**: All four native ctypes modules (`ioreport.py`, `native_sys.py`, `smc.py`, `gpu_registry.py`) guard their `ctypes.cdll.LoadLibrary` calls under `sys.platform == "darwin"`, so `import actop` and `python -m actop.actop --help` succeed on non-Darwin CI runners; public entry points degrade to empty/unavailable sentinels off-Darwin instead of crashing at import time.
5. **Three-Layer Data Flow (L1 → L2 → L3)**: Acquisition, data points, and presentation are separated by a strict seam (established by the LC-1/2/3 layering cleanup, v1.2.4–v1.3.1). **L1 — acquisition:** `ioreport.py`, `smc.py`, `gpu_registry.py`, `native_sys.py`, `sampler.py`, and `utils.py`'s raw `sysctl`/`system_profiler`/RAM/process queries produce raw `SampleResult`s. **L2 — data points:** `models.py` (`SystemSnapshot`, `ProcessSample`, `CoreSample`), `api.py` (`Monitor`/`Profiler`/`AsyncMonitor`), `analytics.py` (per-process power attribution, throttle detection, and the `AlertEngine` → `AlertFrame`), `soc_profiles.py`, and `power_scaling.py` turn raw samples into the typed `SystemSnapshot` — the **sole per-frame contract** — plus its derived judgments. **L3 — presentation:** `tui/*` and `export.py` consume *only* L2 types. This is why the TUI holds no acquisition or domain math, and why any API/export consumer can obtain the same data points the dashboard renders.

### 1.1 Identity, Naming & Distribution Model (since v1.0.0)

`actop` = **"Apple Chip top"** — a whole-chip Apple-Silicon `*top` (CPU / GPU / ANE / memory / power / thermal), with a second reading of *AC = power*. It was renamed from **`agtop`** ("Apple **G**PU top") at **v1.0.0 (2026-06-30)**: the old name undersold a whole-chip monitor, and the PyPI name `agtop` was squatted by an unrelated tool, blocking `pip install`.

- **Clean break — no `agtop` compatibility layer anywhere.** The command, Python package, import path (`actop.*`), Homebrew formula (`class Actop`), and the Prometheus metric prefix (`agtop_*` → `actop_*`) are all `actop`. There is no deprecated alias, module, or formula shim.
- **Mission / positioning.** The sudoless, in-process, whole-chip Apple-Silicon monitor that surfaces decision-grade signals peers don't — per-process attribution, bandwidth saturation, throttle state, DVFS residency — all without `powermetrics`/`sudo`. The Python API (`api.py` `Monitor` / `Profiler`) is the programmable layer underneath, not the headline.
- **Distribution model.**
  - **PyPI** (`pip install actop` / `pipx install actop`) published via **OIDC Trusted Publishing** — no stored token in CI.
  - **Homebrew** via a **dedicated tap repo `binlecode/homebrew-actop`** (`brew tap binlecode/actop && brew install actop`). The formula does **not** live in this repo; CI syncs it to the tap on each `v*` tag. The keg is self-contained on Homebrew's `python@3.13` (isolated `libexec` venv; the macOS system Python is never used).
  - **`main` is strictly PR-only** (branch protection + `enforce_admins` + a local `.githooks/pre-push` guard); CI never pushes to `main`. Release mechanics and secret handling are documented in `CLAUDE.md` → Release Process.
  - **Rejected alternative: a stand-alone binary** (Nuitka/PyInstaller, bundling Textual, published from the release pipeline). PyPI (`uv tool install` / `pip install`) already gives a Python-free path and Homebrew already gives a package-manager-free path, so a bundled binary's only unique value is a locked-down environment with no package-manager access at all — too narrow an audience to justify the recurring codesigning/notarization + per-arch CI tax on a single-maintainer project. Revisit only if that niche produces a concrete request; if revived, prefer Nuitka and budget for codesigning/notarization from day one.

---

## 2. Low-Level Native Bindings (`native_sys.py`)

The file `actop/native_sys.py` serves as the foundation for direct macOS kernel interop. It loads `libSystem.B.dylib`, `libobjc.A.dylib`, `IOKit.framework`, and `CoreFoundation.framework` as singletons.

### 2.1 Virtual Memory & Mach Page Calculations
RAM metrics bypass the standard Unix `sysctl` interface when calculating "Used RAM", mimicking macOS's Activity Monitor.
1. The page size is queried using `sysctlbyname("hw.pagesize")`.
2. A direct connection to the host port is established using `mach_host_self()`.
3. The host statistics are fetched using `host_statistics64` with flavor `4` (`HOST_VM_INFO64`), unpacking a 38-word `VMStatistics64` structure:
   ```python
   class VMStatistics64(ctypes.Structure):
       _fields_ = [
           ("free_count", ctypes.c_uint32),
           ("active_count", ctypes.c_uint32),
           ("inactive_count", ctypes.c_uint32),
           ("wire_count", ctypes.c_uint32),
           # ...
           ("compressor_page_count", ctypes.c_uint32),
           ("internal_page_count", ctypes.c_uint32),
           # ...
       ]
   ```
4. **Activity Monitor Memory Logic**:
   $$\text{Used Bytes} = (\text{internal\_page\_count} - \text{purgeable\_count} + \text{wire\_count} + \text{compressor\_page\_count}) \times \text{page\_size}$$
   $$\text{Available Bytes} = \text{total\_ram} - \text{Used Bytes}$$

**Units: byte counts cross every seam as bytes; prefixes are applied only for display.** Every memory quantity on the frame contract is a raw byte count — `SystemSnapshot.ram_used_bytes` / `ram_total_bytes` / `swap_used_bytes` / `swap_total_bytes`, `ProcessSample.rss_bytes`, the `actop_ram_used_bytes` family of Prometheus gauges, and the `*_bytes` keys on `utils.get_ram_metrics_dict()`. Three reasons:

1. **No prefix ambiguity.** GB-vs-GiB cannot be got wrong if no prefix is applied. `hw.memsize` is `137,438,953,472`; that number is exact and self-describing.
2. **No precision loss.** The rounded GiB view quantizes to ±50 MiB at one decimal — invisible in a TUI row, material to anyone computing quantized-weight headroom against it.
3. **It is the Prometheus/OpenMetrics convention**, which specifies base units (`bytes`, `seconds`) with the unit as the metric-name suffix, leaving formatting to the dashboard. `node_exporter` does the same (`node_memory_MemTotal_bytes`).

**Display uses binary prefixes.** `tui/widgets._to_gib` and the process table's MiB column format at render time, matching what modern monitors do for memory (btop, bottom, `free -h`, `nvidia-smi`, `docker stats`, Kubernetes `Mi`/`Gi`). Memory is addressed and manufactured in powers of two — `hw.memsize` is an exact multiple of 2³⁰ (128 GiB exactly on an M4 Max) and `hw.pagesize` is 2¹⁴ — so binary prefixes report the physical quantity as a round number where decimal GB would give 137.4. The legacy OS UIs that label binary values "GB" (Activity Monitor, Windows Explorer) are the outliers here, not the standard.

**Bandwidth is the one deliberate exception.** The DCS bucket labels are literally `"32GB/s"` / `"64GB/s"` and Apple publishes 546 GB/s for M4 Max decimally, so `bandwidth_gbps` is genuinely decimal (§3.5) — the bus has no power-of-two structure. This is why bandwidth is *not* normalized into the byte-count rule: it is a rate in the vendor's own unit, not a byte quantity actop measured.

This matters because actop's audience divides these fields against each other programmatically (`tokens/s ≈ effective_bandwidth / bytes_read_per_token`, RAM headroom vs. quantized weights): mixing a 2³⁰ memory figure with a 10⁹ bandwidth figure silently costs 7.4%. The older `ram_used_gb` / `swap_*_gb` / `rss_mb` fields, the `*_GB` dict keys and the `*_gigabytes` gauges are a **deprecated misnomer** — they always held GiB/MiB under a decimal name — kept as rounded views for one release and removed in 2.0.0. `--alert-swap-rise-gb` is likewise renamed `--alert-swap-rise-gib`, with the old spelling kept as a working alias.

### 2.2 Swap Memory via `XSWUsage`
To avoid process execution, swap statistics read the binary structure directly from the BSD sysctl kernel tree:
- Path name: `"vm.swapusage"`
- Unpacking alignment: Matches the C `struct xsw_usage` 32-byte layout:
  ```python
  class XSWUsage(ctypes.Structure):
      _fields_ = [
          ("xsu_total", ctypes.c_uint64),
          ("xsu_avail", ctypes.c_uint64),
          ("xsu_used", ctypes.c_uint64),
          ("xsu_pagesize", ctypes.c_uint32),
          ("xsu_encrypted", ctypes.c_uint32),
      ]
  ```

### 2.3 Process Enumeration & Traversal
Instead of traversing `/proc` (which doesn't exist on macOS) or spawning `ps`, `actop` queries BSD task information:
1. Calls `proc_listpids(type=1, typeinfo=0, buffer, buffersize)` (from libSystem) to fetch the array of active process IDs.
2. For each PID, calls `proc_pidinfo(pid, flavor=2, arg=0, buffer, buffersize)` which corresponds to `PROC_PIDTASKALLINFO`. This fills a `ProcTaskAllInfo` structure combining BSD information (`ProcBSDInfo`) and Mach task information (`ProcTaskInfo`):
   - **Name Extraction**: Unpacked from `pbi_name` (32 bytes) or fallback `pbi_comm` (16 bytes).
   - **RAM Extraction**: Unpacked from `pti_resident_size` (RSS bytes) and `pti_virtual_size` (VMS bytes) at offset 136.
   - **CPU Time**: Unpacked from accumulated microsecond durations `pti_total_user` and `pti_total_system`. The per-poll delta of this value (cached per PID) drives both the `CPU%` column and the per-process power share (see §5.7).
   - **Threads Count**: Unpacked from `pti_threads_count` at offset 220.
   - **Start time (PID-reuse guard)**: `pbi_start_tvsec` (`uint64` at offset 120) is read so the CPU-time cache can key on `(pid, start_tvsec)` — a reused PID with a changed start time is treated as a fresh first sample rather than yielding a bogus delta.

### 2.4 Command Line Parsing (`KERN_PROCARGS2`)
Command names are often truncated in process listings. `actop` resolves exact command-lines via sysctl:
1. Calls `sysctl` with the 3-integer Management Information Base (MIB): `[CTL_KERN (1), KERN_PROCARGS2 (49), pid]`.
2. The buffer contains:
   - An integer `argc` representing the argument count.
   - A null-terminated executable path.
   - Null padding.
   - A list of null-terminated arguments.
3. The parser reads `argc`, skips the padding byte offset, and joins the arguments:
   ```python
   argc = int.from_bytes(data[:4], byteorder=sys.byteorder)
   # Traverse null separators to cleanly reconstruct cmdline arguments
   ```

### 2.5 Thermal State Objective-C Bridge
The macOS system thermal pressure state is queried cleanly via the Objective-C runtime by querying `NSProcessInfo`:
- Objective-C classes and selectors are loaded natively:
  ```python
  _cls_NSProcessInfo = _objc.objc_getClass(b"NSProcessInfo")
  _sel_processInfo = _objc.sel_registerName(b"processInfo")
  _sel_thermalState = _objc.sel_registerName(b"thermalState")
  ```
- Executing msgSend calls yields the thermal integer states mapping to `"Nominal"`, `"Fair"`, `"Serious"`, or `"Critical"`.

---

## 3. Telemetry Sampling Layer (`sampler.py` & `ioreport.py`)

`actop` uses macOS private frameworks to fetch active frequency scaling and residency cycles.

### 3.1 `libIOReport` Channel Management
The `ioreport.py` module defines direct ctypes structures for accessing the private `libIOReport.dylib`. It creates subscriptions to low-level hardware performance channels:
- `"Energy Model"`: Tracks raw energy counters.
- `"CPU Stats"` / `"CPU Core Performance States"`: Handles CPU cores and clusters residency.
- `"GPU Stats"` / `"GPU Performance States"`: Monitors GPU performance states.
- `"PMP"` / `"DCS BW"`: DRAM controller bandwidth residency histograms — see §3.5.

The subscription pipeline coordinates raw state pointers via:
```python
_ior.IOReportCopyChannelsInGroup(group, subgroup, 0, 0, 0)
_ior.IOReportCreateSubscription(...)
_ior.IOReportCreateSamples(...)
_ior.IOReportCreateSamplesDelta(prev_sample, current_sample, ...)
```

### 3.2 Dynamic DVFS Parsing & Classification
On startup, `actop` accesses the IORegistry device tree node `"AppleARMIODevice"` to find the `"pmgr"` device. It reads the `"voltage-states"` property, which contains direct binary arrays mapping frequency states (Hz) to voltage steps:
- Unpacks frequency steps using struct format `<II` (4-byte frequency, 4-byte voltage).
- Divides by $1,000,000$ to get MHz tables.
- **Classification Engine**:
  - **P-core table**: The table with $\ge 15$ entries containing the highest maximum frequency ($> 2.0\text{ GHz}$).
  - **E-core table**: Small tables containing $5\text{--}12$ entries.
  - **GPU table**: Tables with $10\text{--}20$ entries, distinct from E-core/P-core patterns.

### 3.3 Frequency and Residency-Weighted Active Calculations
State residencies represent the cumulative nanoseconds the processor spent in various Power states (P-states / V-states) versus inactive states (`IDLE`, `OFF`, `DOWN`).
- For each performance state, the sampler maps the residency name (e.g. `V1P0` or `P3`) to its corresponding MHz limit in the classified DVFS table.
- **Weighted Frequency**:
  $$\text{Weighted Frequency} = \frac{\sum (\text{State Frequency}_{\text{MHz}} \times \text{State Residency}_{\text{ns}})}{\text{Active Duration}_{\text{ns}}}$$
- **Active Percentage**:
  $$\text{Active Percentage} = \frac{\text{Active Duration}_{\text{ns}}}{\text{Total Duration}_{\text{ns}}} \times 100$$

### 3.4 Why GPU Lacks Per-Core Metrics
In `actop/sampler.py`, CPU statistics are fetched via channel loops looking for individual core labels (e.g., `ECPU000` or `PCPU130`), allowing per-core breakdowns. 

In contrast, the GPU stats channel only exposes a single unified channel named **`GPUPH`** (GPU Performance Handler) inside `GPU Performance States`. Because Apple Silicon's GPU acts as a monolithic co-processor governed under a unified dynamic voltage/clock domain, macOS does not record or publish individual ALUs/cores metrics inside `libIOReport`. Therefore, only global GPU utilization and average frequencies can be derived.

### 3.5 Memory Bandwidth via `PMP` / `DCS BW`

Total DRAM bandwidth is read in-process and unprivileged, the same way DVFS residency is (§3.3) — this group was not part of the original three-group subscription; it was added after a feasibility spike confirmed a `GO` (findings folded in here; see git history for the original spike record).

- **Group / subgroup**: `"PMP"` / `"DCS BW"`, found by enumerating all ~11,400 IOReport channels (`IOReportCopyAllChannels(0, 0)`). Energy-group `DCS`/`DRAM`/`AMCC` channels exist too but report **mJ energy**, not bandwidth; the IOReport `"Bandwidth"` group is PCIe-only.
- **Not a byte counter.** `IOReportChannelGetUnitLabel` reports `"events"` and `IOReportSimpleGetIntegerValue` returns the sentinel `INT64_MIN` — these are **state/residency channels**, structurally identical to the DVFS P-state residencies already parsed (§3.3). Each channel has 32 states named as bandwidth buckets (`"32GB/s"`, `"64GB/s"`, …) whose *values* are nanoseconds of residency at that level.
  $$\text{GB/s} = \frac{\sum(\text{bucket GB/s} \times \text{residency}_{ns})}{\sum \text{residency}_{ns}}$$
  already in GB/s — no division by the sample interval (`sampler._channel_bandwidth_gbps`). Multi-die SoCs (Ultra) expose one `AMCC RD+WR` channel per memory-controller die; `sampler._compute_bandwidth_gbps` computes each die's weighted mean **independently and sums them**, so the reported total is whole-chip bandwidth rather than a single controller's rate.
- **Channel → agent mapping**: `AMCC RD/WR/RD+WR` = total DRAM controller (the authoritative total); `EACC0` = E-cores; `PACC0`/`PACC1` = P-clusters; `AGX` = GPU; `ANE0 L0/L1` = Neural Engine; `AVE*`/`AVD*`/`PRORES*`/`SCODEC*`/`JPEG*` = media; plus `ISP*`, `DISP*`, `ATC*` (Thunderbolt), `ANS` (storage) — none of the latter are surfaced today.
- **Per-agent breakdown was investigated and deliberately dropped, not deferred.** The per-agent channels (`EACC`/`PACC`/`AGX`/`AVE`/…) step in 1 GB/s buckets and **hard-cap at 32 GB/s**, while `AMCC` spans ~1 TB/s in 32 GB/s steps. Under an 8-worker `memcpy` load, `AMCC RD+WR` correctly read 350 GB/s while both P-cluster channels pegged at their 32 GB/s ceiling — per-agent attribution is unreliable at exactly the bandwidths that matter, so **only the `AMCC` total ships**; `SystemSnapshot.bandwidth_gbps` is a single aggregate by design, not a stopgap.
- **Cost control**: subscribing to the ~90-channel `PMP` group is the irreducible kernel cost, but extracting per-state residency for all of them is not. `sampler._keep_states()` filters `IOReportSubscription.delta()`'s per-state extraction to `AMCC*` channels only. Measured marginal idle-CPU cost @1s interval: **+0.39%** filtered vs. **+0.70%** unfiltered, against a 3-group baseline of ~0.54% — the filter is what keeps the whole sampler under actop's standing `<0.5%` idle-CPU budget.
- **Availability**: `SystemSnapshot.bandwidth_available` is `False` when the platform exposes no `DCS BW` channel, hiding the Mem BW row rather than showing a fabricated `0.0` (§5.3, §6).

### 3.6 Metric Coverage: Aggregation Limits and Deliberate Non-Goals

These boundaries are intentional and recorded here so they are not mistaken for oversights or re-litigated. actop's sampling layer deliberately captures only what the IOReport-first, unprivileged, SoC-power thesis can support cleanly:

- **Memory bandwidth is exposed as a single aggregate** (`SystemSnapshot.bandwidth_gbps`, the `AMCC` total) — see §3.5 for why the per-agent channels can't be attributed and are excluded.
- **Network / disk I/O — no longer a non-goal; overridden by the roadmap.** `docs/TODO-architecture-roadmap.md` promotes this to a must-have, gated on native ctypes rather than `psutil`. A 2026-07-02 feasibility spike confirmed on-device: network via `getifaddrs()`/`AF_LINK`/`if_data` (matching `mactop`'s approach — note `mactop` is Go/`cgo`, not `psutil`-based, correcting the assumption this bullet previously made); disk via IOKit `AppleAPFSVolume` `Statistics`, with `IOBlockStorageDriver` as a fallback for non-APFS systems. See `docs/TODO-net-disk-io-2026-07-02.md` for the exact structs/keys and the implementation-ready task plan (`docs/TODO-architecture-roadmap.md` links it and sets its priority); this bullet will be replaced with the as-built design once the feature ships.
- **Per-process CPU power *is* attributed (since v1.0.2); GPU / ANE / true-energy per process are not.** actop partitions `SystemSnapshot.cpu_watts` across processes by each PID's CPU-time share (the `PWR` column, see §5.7) — an estimate, since a P-core-second draws more than an E-core-second, but one that reconciles to package CPU power by construction. This is white space no direct peer (asitop / mactop / macmon) fills. What remains unavailable sudoless: per-process **GPU / ANE** power, and a true hardware per-process **energy** counter (`proc_pid_rusage`'s `ri_*_energy` fields stay flat at 0 for ordinary compute — a Phase-0 spike disproved that path). Per-process CPU/RSS/threads come from the native process enumeration in §2.3.
- **GPU per-core metrics** are a hardware limitation, not a scope choice — see §3.4.

### 3.7 SoC Profile Resolution & Fallback (`soc_profiles.py`)

Chart scaling (`--power-scale profile`) and alert thresholds (§5.5) need a reference wattage/bandwidth ceiling per chip. `get_soc_profile(raw_name)` resolves the `sysctl`-reported chip brand string to one of three tiers of specificity, and is a **total function** — every path returns a valid `SocProfile`; none raise:

1. **Exact match** — 16 hand-calibrated `KNOWN_SOC_PROFILES` entries spanning M1–M4 (base/Pro/Max/Ultra), each with real reference `cpu_chart_ref_w` / `gpu_chart_ref_w` and a single `max_mem_bw` (peak unified-memory bandwidth in GB/s — Apple Silicon shares one DRAM bus, so this is the lone denominator the bandwidth chart and MEM-BOUND alert normalize against), plus an `ane_max_w` field (ANE reference power, defaulted to `8.0 W` across M1–M4 pending per-generation calibration) that L2 reads as the denominator for `SystemSnapshot.ane_util_pct` — the LC-1 fix that moved the ANE ceiling out of `DashboardConfig` and into the profile layer where every other reference wattage lives.
2. **Generation-agnostic tier fallback** — `APPLE_M_SERIES_PATTERN = re.compile(r"^Apple M\d+")` matches *any* `Apple M<N>` string regardless of the generation number, so an unrecognized chip (M5, M6, M99, …) is still routed correctly by substring (`Ultra`/`Max`/`Pro`/else `base`) to `TIER_FALLBACKS`, without any code change. This routing is already future-proof; nothing here needs revisiting per chip launch.
3. **Generic catch-all** — a name that doesn't even match the `Apple M\d+` pattern (or is empty/`None`, normalized by `normalize_soc_name`) falls to `GENERIC_APPLE_SILICON_PROFILE` rather than raising.

**What tier fallback gets right vs. wrong.** The *routing* (never crashing, never missing a chart scale) is solved for all future generations by construction. What stays approximate is the *numbers*: `TIER_FALLBACKS` are pinned to the latest calibrated generation (currently M4-era reference wattages), so an M6 Ultra routed through the "Ultra" fallback is scaled against M4-Ultra-shaped ceilings, not M6-accurate ones. The exact fix is the same one used for all 16 shipped profiles — hand-add a `SocProfile` entry once real reference numbers exist for the new chip — not a bigger fallback engine.

**Rejected alternative: a dynamic voltage-state-derived estimator.** `native_sys.py`'s PMGR `voltage-states` reader (§3.2) already unpacks each DVFS table entry's `(freq_hz, voltage)` pair, but only `freq_hz` is kept — `voltage` is discarded. Deriving a power estimate from that discarded voltage word for unrecognized chips was considered and rejected: it needs per-generation calibration against real hardware to trust, and an uncalibrated "smart" guess would be less reliable than the current honest tier-default approximation, not more. Revisit only if a maintainer gets hardware to calibrate against, or exact per-chip profile lag becomes an actual reported user problem.

### 3.8 Dual GPU Utilization Sources (`gpu_registry.py`, shipped v1.6.0)

The GPU is measured two independent ways, and the distinction between them is load-bearing rather than redundant:

| | `gpu_util_pct` (primary) | `gpu_device_pct` / `gpu_renderer_pct` / `gpu_tiler_pct` |
|---|---|---|
| Source | IOReport `GPU Performance States` residency (§3.3) | IOKit `IOAccelerator` → `PerformanceStatistics` dict |
| Semantics | **Integrated over the sample interval** | Driver-maintained **instantaneous point read** |
| Needs the DVFS table? | Yes (§3.2 classification) | No |
| Cost | part of the existing subscription delta | 0.025 ms/call measured (M4 Max) |

**Why the residency reading stays primary.** Measured side-by-side at idle on an M4 Max, the two track loosely but diverge hard per-sample — `actop=40% @1232MHz` against `Device=91%` in the same frame — because they measure different things. Swapping in the driver's number would trade an interval-averaged value for an instantaneous one, which is a regression in sampling semantics for a sampling monitor. The driver read is therefore adopted as a **fallback plus a new breakdown**, never as a replacement.

**The new signal: Renderer vs Tiler.** `Renderer Utilization %` covers shader/compute work, `Tiler Utilization %` geometry work. For the ML/inference audience this separates an MLX/CoreML compute frame (Renderer high, Tiler ≈ 0) from a render-bound one — a distinction IOReport residency cannot express at all, since `GPUPH` is a single unified channel (§3.4). Surfaced as the `Rend N% · Tiler N%` row in the `GPU · ANE` section, hidden entirely when `gpu_perf_stats_available` is `False` (the §3.5 / §5.3 hide-row contract). `Device Utilization %` is deliberately **not** shown in the TUI — the GPU row already carries the headline percent, and a second, differently-measured whole-GPU number beside it reads as a contradiction; it stays available through the API and both exports.

**The fallback rule** lives in `api._sample_to_snapshot`, not `analytics.py`: choosing which acquisition path to trust is an adapter concern, not an L2 domain judgment.

```python
gpu_util, gpu_util_source = float(gm["active"]), "residency"
if int(gm.get("max_freq_MHz", 0)) <= 0 and perf.available:
    gpu_util, gpu_util_source = float(perf.device_pct), "ioaccelerator"
```

`gpu_max_freq_mhz == 0` means `_classify_dvfs_tables` (§3.2) failed to identify the GPU table, which makes both `gpu_util_pct` and `gpu_freq_mhz` meaningless — the failure mode that previously degraded GPU metrics silently to 0. `SystemSnapshot.gpu_util_source` records the provenance (`"residency"` | `"ioaccelerator"`) so no consumer has to infer it; the TUI renders `GPU N% (drv)` and drops the unmeasured `@NMHz` when the fallback is active. The fallback branch is unreachable on M1–M4 (all classify), so it is exercised by inspection rather than by an automated test — faking it would require a mock, which the testing contract (§6) forbids.

**Implementation notes.** `IOServiceMatching("IOAccelerator")` matches by class inheritance, so it reaches the chip-specific subclass (`AGXAcceleratorG16X` on M4 Max) with no per-chip table; `AGXAccelerator` is a narrower fallback. Several accelerator nodes can match on multi-die parts and idle ones report 0, so the entry with the **highest** `Device Utilization %` wins. `ioreg` is deliberately **not** shelled out to — a subprocess per frame would blow the idle-CPU budget §3.5 protects. At 0.025 ms/call (33× cheaper than the per-process GPU-time walk already in the loop) the service enumeration needs no caching, so the reader stays a stateless function like its `get_gpu_time_by_pid()` neighbour rather than adopting the `SMCReader` discover-once pattern.

---

## 4. System Management Controller Interface (`smc.py`)

To read on-die temperature values and fan tachometers, `actop` queries the macOS kernel SMC.

### 4.1 IOKit Key Management
1. The tool searches IORegistry matching the `"AppleSMC"` service using IOKit.
2. It establishes a structural connection using `IOServiceOpen`.
3. Commands and requests are sent using `IOConnectCallStructMethod` on connection port `2` (the designated port for SMC keys).

### 4.2 Key Discovery & classification
SMC uses 4-character tags to track system components. `actop` executes a fast key discovery sweep on startup:
- Retrieves the count of all system keys (from the `"#KEY"` registry identifier).
- Iterates through the indices, checking the data type. Keys holding temperature values are marked with the SMC type `"flt "` (4-byte IEEE 754 float).
- **Sensor classification**:
  - **CPU Temperature**: Keys starting with `"Tp"` (such as `Tpac`, `Tpg1`) or `"Te"`.
  - **GPU Temperature**: Keys starting with `"Tg"`.
- During active polling, the max temperature from the discovered CPU/GPU sensor sets is displayed to prevent performance-inhibiting single-sensor hotspots.

### 4.2.1 Why temperature is reported per-cluster, not per-core
actop attaches one die temperature to each **cluster** header row (`P-CPU`, `E-CPU`, `GPU`), not to individual cores. This is a deliberate, hardware-driven choice, not a missing feature:

- **No 1:1 core→sensor mapping exists.** Apple Silicon exposes far more thermal sensors than cores — on an M4 Max the `Tp*`/`Te*` sweep finds ~112 CPU sensors for 16 cores — scattered across the die with an undocumented layout. There is no reliable way to say "this sensor is core C7."
- **Peer tools don't expose real per-core temps either.** btop's macOS Apple Silicon path (`src/osx/sensors.cpp`) reads **IOHIDEventSystem** thermal sensors (`PMU tdie*`, and the `pACC*` / `eACC*` performance/efficiency *cluster* packages) — a different source than actop's SMC `Tp*`/`Te*` — averages them into a short list of a few cluster/die values, then fans that list across the visible core rows by proportional index (`sensor_index = core * n_sensors / coreCount`, in `btop_collect.cpp`). Adjacent cores therefore share one value, and btop's own source comments call the mapping a guess ("nobody knows which sensors mean what"). A btop per-core temperature column that reads ~identical across cores under wildly different load is this spreading, not real per-core measurement.
- **actop's choice.** Report the **max** of the discovered CPU sensor set (`sampler.py`: `max(cpu_temps)`) as one whole-CPU die figure surfaced on the cluster header rows, and likewise the max of `Tg*` for GPU. `max` (not mean) is used so a single throttling hotspot is never masked by cooler idle sensors — the same reading that feeds throttle detection and the `THERMAL` alert.

> Current limitation: the same whole-CPU max is shown on both the `P-CPU` and `E-CPU` header rows (`widgets.py` passes `s.cpu_temp_c` to both). Splitting into a distinct P-cluster max (`Tp*`) and E-cluster max (`Te*`) is a small, additive change if per-cluster differentiation is wanted later.

### 4.3 Fan RPM (shipped v1.2.2; current+max structured in v1.2.3)
Fan tachometers use a separate, simpler discovery path than temperature: the `"FNum"` key gives the fan count directly, so `_discover_fan_keys` builds the per-fan key names (`F0Ac`, `F1Ac`, ...) instead of sweeping the full key space. Verified on-device (Apple M4 Max) that actual-RPM keys are SMC type `"flt "` — the same 4-byte float type as temperature — not the `"fpe2"` fixed-point type originally guessed in the roadmap doc; `_read_float_cached` is reused unchanged. As of v1.2.3, discovery also probes the sibling **max-RPM key `F{n}Mx`** with the identical `flt`/size-4 guard (factored into the `_discover_flt4_key` helper), returning `{"ac": ..., "mx": ... | None}` per fan; a fan lacking the max key keeps `"mx": None` rather than being dropped. The min key `F{n}Mn` is intentionally not probed — peers read it only to clamp fan-set *writes*, which actop (read-only, unprivileged) never performs.

`SMCReader.read_fan_info()` returns one `FanReading(current, max)` per fan in index order (replacing the earlier bare-`list[float]` `read_fan_rpms()`). It does **not** filter out a `0.0` *current* value (unlike temperature's invalid-sentinel handling) — 0 RPM is a legitimate idle reading on modern Macs that spin fans down at rest — while `max` is `None` when the key is absent or reports `<= 0` (the "unknown" convention both peers use). `SMCReader.fan_available` reports whether any fan keys were discovered at all, independent of the current reading; this is the signal the TUI uses to hide the Fan row on fanless Macs (MacBook Air) rather than showing a phantom `0 RPM` — the same `bandwidth_available` hide-row pattern from §3.5, threaded through `SampleResult.fans` / `SampleResult.fan_available` → `SystemSnapshot.fans` / `fan_available`. `SystemSnapshot.fan_rpms` is retained as a derived current-only convenience (`[f.current for f in fans]`) so the `export.py` NDJSON/Prometheus contract is unchanged.

Reading `F{n}Mx` alone closes the only 2/2-converged peer gap for fans (both `mactop` and `macmon` read the max key). Target RPM (`F{n}Tg`) and mode (`F{n}Md`) are deliberately **not** read: they are mactop-only breadth that in mactop chiefly serve its root-gated *write* path, which actop excludes on security grounds.

---

## 5. TUI Layout & Rendering Engine (`tui/`)

The user interface is powered by Textual. The dashboard is five titled section
containers rendered under one of two layout presets (§5.1.1); the process table
is a fixed-width panel beside it and the thermal/alert status line is fixed app
chrome below both. Per-core panels are hidden by default (toggle with `c`), so
the cluster charts read as the prominent siblings. Captures below are live
frames on an Apple M4 Max.

**`grid`** (default) — two columns: the `P-CPU` / `E-CPU` cluster boxes share
the top row, `GPU · ANE` / `Memory` the next, and `Power` spans the full width
beneath. Fits short terminals without scrolling.

```
                                           actop — v1.6.0 · Apple M4 Max · 4E+12P+40GPU                                    19:03:53
╭─ P-CPU ────────────────────────────────────────────────────────╮╭─ E-CPU ────────────────────────────────────────────────────────╮
│ P-CPU   4% @1046MHz (56°C)  avg 6% · max 10%                   ││ E-CPU  17% @894MHz (56°C)  avg 24% · max 38%                   │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ ││ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ ││ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ ││ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢠⠀ │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣠⣀ ││ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣿⣶ │
│ P-CPU  [░░░░░░░░░░░░░░░░]  idle96 low2 mid2 high0              ││ E-CPU  [░░░░░░░░░░░░░▒▓█]  idle82 low6 mid6 high6              │
╰────────────────────────────────────────────────────────────────╯╰────────────────────────────────────────────────────────────────╯
╭─ GPU · ANE ────────────────────────────────────────────────────╮╭─ Memory ───────────────────────────────────────────────────────╮
│ GPU 10% @338MHz (49°C)  avg 12% · max 14%                      ││ RAM 49.7/128.0GiB  avg 39% · max 39%                           │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ ││ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀ ││ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣶⣶ │
│ GPU    [░░░░░░░░░░░░░░▒▒]  idle90 low10 mid0 high0             ││ Mem BW 16.2 GB/s  avg 16.4 · max 16.7 GB/s                     │
│ Rend 7% · Tiler 3%                                             ││ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │
│ ANE 0% (0.0W)  avg 0% · max 0%                                 ││ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀ │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ ││                                                                │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ ││                                                                │
╰────────────────────────────────────────────────────────────────╯╰────────────────────────────────────────────────────────────────╯
╭─ Power ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ CPU 0.47W ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀  avg 0.8W · max 1.2W                                                                          │
│ GPU 0.19W ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀  avg 0.2W · max 0.3W                                                                          │
│ Package Power 0.66W  avg 1.0W · max 1.5W                                                                                         │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀ │
│ Fan ⠏ 1344/5777 · ⠋ 1474/5777 RPM                                                                                                │
╰──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯

span 4m08s  ·  energy 2mWh  ·  thermal: Nominal  alerts: none
 q Quit  p Pause  s Sort  g Glyph  l Layout  c Cores  t Processes  ? Help
```

**`stack`** (`l` toggles) — the same five sections full-width in one scrollable
column; charts get the longest history span (blank chart bodies elided below):

```
                                           actop — v1.6.0 · Apple M4 Max · 4E+12P+40GPU                                    19:04:16
╭─ P-CPU ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ P-CPU   4% @1032MHz (53°C)  avg 4% · max 4%                                                                                      │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀ │
│ P-CPU  [░░░░░░░░░░░░░░░░]  idle96 low2 mid2 high0                                                                                │
╰──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
╭─ E-CPU ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ E-CPU  16% @937MHz (53°C)  avg 18% · max 23%                                                                                     │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣶⣷ │
│ E-CPU  [░░░░░░░░░░░░░▒▓█]  idle83 low6 mid5 high6                                                                                │
╰──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
╭─ GPU · ANE ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ GPU 10% @338MHz (48°C)  avg 10% · max 11%                                                                                        │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀ │
│ GPU    [░░░░░░░░░░░░░░▒▒]  idle90 low10 mid0 high0                                                                               │
│ Rend 6% · Tiler 3%                                                                                                               │
│ ANE 0% (0.0W)  avg 0% · max 0%                                                                                                   │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │
╰──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
╭─ Memory ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ RAM 49.7/128.0GiB  avg 39% · max 39%                                                                                             │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣶⣶ │
│ Mem BW 16.2 GB/s  avg 16.2 · max 16.2 GB/s                                                                                       │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀ │
╰──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
╭─ Power ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ CPU 0.45W ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀  avg 0.6W · max 0.8W                                                                          │
│ GPU 0.17W ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀  avg 0.2W · max 0.2W                                                                          │
│ Package Power 0.63W  avg 0.8W · max 1.0W                                                                                         │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀ │
│ Fan ⠏ 1340/5777 · ⠋ 1462/5777 RPM                                                                                                │
╰──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯









span 8m32s  ·  energy 1mWh  ·  thermal: Nominal  alerts: none
 q Quit  p Pause  s Sort  g Glyph  l Layout  c Cores  t Processes  ? Help
```

**Process table + live filter** — `t` shows the fixed 74-col table beside the
dashboard; `/` opens the regex filter bar (here `ollama`, matching each row's
full command line). Here the table leaves the dashboard under the grid width
threshold, so it has auto-degraded to `stack`:

```
                                           actop — v1.6.0 · Apple M4 Max · 4E+12P+40GPU                                    19:04:39
╭─ P-CPU ──────────────────────────────────────────────╮  ╭────────────────────────────────────────────────────────────────────────╮
│ P-CPU   6% @1234MHz (62°C)  avg 10% · max 13%        │  │ PID    Command         *CPU%  PWR     MEM (MiB)  Threads               │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │  │ 51154  Ollama          0.0    0.00W   87.3       22                    │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │  │ 51153  Ollama          0.0    0.00W   83.6       13                    │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │  │ 86360  zsh             0.0    0.00W   3.5        1                     │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣠⣠⣄ │  │                                                                        │
│ P-CPU  [░░░░░░░░░░░░░░░▓]  idle94 low2 mid4 high0    │  │                                                                        │
╰──────────────────────────────────────────────────────╯  │                                                                        │
╭─ E-CPU ──────────────────────────────────────────────╮  │                                                                        │
│ E-CPU  17% @947MHz (62°C)  avg 17% · max 23%         │  │                                                                        │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │  │                                                                        │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │  │                                                                        │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │  │                                                                        │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣷⣤⣶ │  │                                                                        │
│ E-CPU  [░░░░░░░░░░░░░▒▓█]  idle83 low4 mid7 high6    │  │                                                                        │
╰──────────────────────────────────────────────────────╯  │                                                                        │
╭─ GPU · ANE ──────────────────────────────────────────╮  │                                                                        │
│ GPU 15% @377MHz (53°C)  avg 39% · max 76%            │  │                                                                        │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢠⠀ │  │                                                                        │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣷⣿⣀ │  │                                                                        │
│ GPU    [░░░░░░░░░░░░░░▒▒]  idle85 low14 mid1 high0   │  │                                                                        │
│ Rend 12% · Tiler 5%                                  │  │                                                                        │
│ ANE 0% (0.0W)  avg 0% · max 0%                       │  │                                                                        │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │  │                                                                        │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │  │                                                                        │
╰──────────────────────────────────────────────────────╯  │                                                                        │
╭─ Memory ─────────────────────────────────────────────╮  │                                                                        │
│ RAM 68.1/128.0GiB  avg 49% · max 53%                 │  ╰───────────── Σ shown 0.0W / pkg CPU+GPU 1.1W · est CPU+GPU time share ─╯
span 3m28s  ·  energy 29mWh  ·  thermal: Nominal  alerts: none
▊▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▎
▊  ollama                                                                                                                          ▎
▊▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▎
 / Filter
```

### 5.1 Textual Application State (`app.py`)
`ActopApp` handles TUI setup and maintains keybindings:
- `q`: Quit.
- `p`: Pause / resume the sampling thread.
- `s`: Cycle process sorting column (`CPU%` \u2192 `PWR` \u2192 `RSS` \u2192 `PID`).
- `g`: Toggle charts between Braille dots and block glyphs.
- `l`: Cycle the dashboard layout preset (`grid` ⇄ `stack`).
- `c`: Show/hide the per-core panels inside the cluster boxes (hidden by default).
- `t`: Show/hide the top processes table.
- `/`: Open the process regex filter bar.
- `?`: Show/hide the help overlay (`esc` / `q` also close it).

Every letter action above also answers its **uppercase** form, via hidden aliases derived from the single `_LETTER_BINDINGS` list so the two cannot drift (the footer still shows one row per action). Caps Lock and Shift both deliver the uppercase character, which Textual names as a *different* key — so a lowercase-only table left every letter action silently dead under Caps Lock. That is the normal input path for a CJK user: with a Chinese input source selected, Caps Lock is how macOS forces direct ASCII. Two limits are inherent, not oversights: `Shift`+`q` therefore quits too (a terminal cannot distinguish Shift from Caps Lock — both arrive as `Q`), and a CJK input source with Caps Lock *off* still will not respond, because the IME consumes the letters before they reach the process. `check_action` gates by action name, so the aliases inherit its gating unchanged.

The application initiates a background thread via textual `@work(thread=True, exclusive=True)` to run the polling loop, delivering parsed snapshots to the main thread via a custom event, `MetricsUpdated`. A spinner splash covers the first sampler warm-up; the dashboard swaps in once the first snapshot arrives. The framework command palette is disabled (`ENABLE_COMMAND_PALETTE = False`).

The dashboard body is five titled section containers — `P-CPU`, `E-CPU`, `GPU · ANE`, `Memory`, `Power` (section titles live in the border, costing no content row). The `P-CPU` and `E-CPU` clusters are separate sibling boxes (not two halves of one "CPU" box) so each cluster chart stands out alongside GPU. The thermal/alert status line is fixed **app chrome** below the dashboard (not inside its scrollable subtree), fed by an `AlertsComputed` message the dashboard posts each frame — so it stays visible even while a tall `stack` dashboard scrolls. CPU/GPU rail power collapse to single inline-sparkline rows (`CPU 6.59W <spark>  avg … · max …`); only Package Power keeps a full chart. The per-core panels inside the `P-CPU` / `E-CPU` boxes are hidden by default and toggled live with `c` (`HardwareDashboard.set_show_cores`); `--show_cores` opts into showing them at startup.

### 5.1.1 Layout presets (`grid` / `stack`)
The same five sections render under two presets, selected by `--layout` (default `grid`) and cycled live with `l` (`HardwareDashboard.set_layout_preset`, which never touches the history deques — switching mid-session loses no data). Presets are a pure CSS class swap in `HardwareDashboard.DEFAULT_CSS` (scoped to the widget); nothing about the data flow or metric computation differs between them.
- **`grid`**: a two-column CSS grid — `P-CPU` / `E-CPU` share the top row, `GPU · ANE` / `Memory` the next, and `Power` spans both columns (`column-span: 2`) on the bottom row. Fits a short terminal without scrolling.
- **`stack`**: all five sections full-width in a single scrollable column — the longest chart-history span (scrolls by design; the fixed status bar does not scroll with it).
- **Width auto-degrade**: below `_GRID_MIN_WIDTH` (96 cols) each grid column would fall under ~48 cols and stop being readable, so a requested `grid` silently renders as `stack` until the terminal widens again (`on_resize` → `_reconcile_layout`). `layout_preset` reports what was requested; `effective_layout_preset` reports what is applied. Width-adaptive Static rows (inline power sparks, core grids) re-render on the resize/preset swap so their spark widths track the new column width immediately rather than waiting for the next sample.

### 5.2 Custom Sparklines (`BrailleChart`)
The `BrailleChart` widget is designed to render charts efficiently inside Terminal constraints.
- Custom Rich formatting leverages Unicode **Braille patterns** (`\u2800` through `\u28FF`) or **Block elements** (`\u2582` through `\u2588`). In `dots` mode each character packs **two** time samples \u2014 the earlier in the cell's left dot column, the later in its right \u2014 so a chart of width *W* renders *2W* samples with no blank gap between columns (btop-style density). `block` mode keeps **one** sample per character, since block glyphs cannot split horizontally.
- **Braille Grid Scaling**: Each console row character contains a 2-column, 4-row dot matrix. *Vertically*, a `height=2` chart provides $8$ discrete steps and `height=4` provides $16$ (4 dots per terminal row). *Horizontally*, the cell's two dot columns are the two samples above, filled from the shared `_braille_cell_bits` primitive (left column `_BRAILLE_FILL_BITS` / right column `_BRAILLE_FILL_BITS_R`) \u2014 so vertical resolution and horizontal density are independent.
- **Dynamic Heatmapping**: Every character is styled along a sliding linear gradient mapping low utilization (Blue: `rgb(66, 135, 245)`) to extreme utilization (Red: `rgb(240, 70, 64)`); a two-sample `dots` cell takes the color of its **hotter** sample.
- **Shared rendering primitive**: the inline single-row sparklines in the compact power rows and the per-core grid (`_inline_spark`) render through the same `_braille_cell_bits` packing as `BrailleChart` \u2014 2 samples per character in `dots`, 1 in `block`. They differ only in being single-row and monochrome (plain text spliced into a labeled `Static` line, no gradient), so there is one source of truth for how a dense braille cell is drawn.
- **Color tier degradation** (`resolve_color_mode` / `_pct_to_color`): the gradient adapts to terminal capability rather than always emitting truecolor. `resolve_color_mode()` honors `NO_COLOR` (https://no-color.org) unconditionally, then prefers the Textual console's detected `color_system`, falling back to `COLORTERM` / `TERM` inspection. The resolved tier maps each value to: `rgb()` (truecolor), the nearest 256-color cube index `color(N)` (256), a named blue\u2192green\u2192yellow\u2192red severity ramp (16), or no style at all (`none` \u2014 `NO_COLOR` / dumb terminals). The tier is resolved once at widget mount and threaded through rendering; `render()` is a thin wrapper over `_render_text(width, height)`, which dispatches to `_render_dots` (dense 2-samples-per-character braille) or `_render_block`, so the colored output is exercisable without a live terminal.
- **Color palettes** (`_PALETTES` / `--palette`, v1.4.1): the gradient stops are selectable, orthogonal to the tier above. `_pct_to_rgb(pct, palette)` interpolates piecewise-linearly across a palette's ordered RGB control points; `_pct_to_color(pct, mode, palette)` then applies the tier. Three palettes ship: `thermal` (default — literally `[_COOL_RGB, _HOT_RGB]`, so byte-for-byte identical to the pre-palette blue→red gradient), `viridis` (colorblind-safe, 5-stop perceptual ramp), and `mono` (grayscale intensity). The palette applies at the **truecolor** and **256** tiers (256 follows automatically — its cube index quantizes the palette RGB); the **16**-color named severity ramp and **none** are palette-independent. The palette is set once at construction from `DashboardConfig.palette` and passed eagerly into every `BrailleChart` (there is no runtime cycle — a set-once accessibility preference; a runtime keybind is a deferred, purely additive follow-on, and would fan out to `BrailleChart` widgets only since the inline power/core sparks are monochrome). The registry is insertion-ordered to preserve that future cycle order.
- **Time-window labeling**: the visible span scales silently with terminal width. The status line leads with a `span <Ns/m/h>` token computed as chart width \u00D7 samples-per-character \u00D7 `--interval` (`_format_window_span` / `_chart_window_label`) \u2014 2 samples/character in `dots`, 1 in `block`; it degrades to no token before layout, so the per-frame path never raises.

### 5.3 Metric Label Context (cur / avg / max)
Each live reading carries rolling context, matching frontier monitors (btop / bottom / macmon). The dashboard retains 500-sample deques per metric; histories are zero-padded for chart right-alignment, so avg/max ignore the leading padding (`_avg_max` reads only the last `_sample_count` real samples). Avg is taken over the `--avg` window; max is the session peak. Every stat carries its unit (`avg N% \u00B7 max N%`, watt labels show `W`, bandwidth shows `GB/s`) so it stays unambiguous beside a headline in a different unit (MHz / GiB / W / GB/s). Applied to per-cluster CPU summary rows, GPU, ANE, RAM, memory-bandwidth, and CPU/GPU/package power labels.

The dashboard also surfaces two SoC-level headline metrics whose data already flowed through `SystemSnapshot` but was previously only consumed by alerts: **Mem BW** (unified-memory bandwidth in GB/s, the headline bottleneck for LLM inference) and **Package Power** (total SoC draw = CPU + GPU + ANE + other rails). Their chart percents reuse the same normalisation as the `MEM-BOUND>` / `PKG>` alerts (bandwidth vs summed CPU+GPU channel capacity; package vs `package_ref_w`). The Mem BW row is hidden when `SystemSnapshot.bandwidth_available` is false (no DCS channel on the platform).

The **Rend / Tiler** row (shipped v1.6.0) is likewise a plain label with no sparkline or avg/max context, sitting under the GPU chart in the `GPU · ANE` section: `Rend 12% · Tiler 5%`. It carries the driver's compute-vs-geometry split (§3.8), and is hidden entirely when `SystemSnapshot.gpu_perf_stats_available` is false — the same hide-on-unavailable treatment as Mem BW. `Device Utilization %` is deliberately excluded here; see §3.8 for why a second whole-GPU percent beside the headline would misread.

The **Fan** row (shipped v1.2.2; current/max in v1.2.3) is a plain label with no sparkline or avg/max context — a tachometer reading doesn't warrant the chart-history machinery the power/BW rows use — showing each fan's `current/max` RPM when the max is known (`Fan 3200/6000 · 4100/6000 RPM` on a multi-fan Mac), falling back to bare current RPM (`Fan 1200 RPM`) when it isn't. Fans are joined with `·` so the inter-fan separator never collides with the `/` inside a single fan's `current/max`. It is hidden entirely when `SystemSnapshot.fan_available` is false, the same hide-on-unavailable treatment as Mem BW.

### 5.4 Help Overlay (`HelpScreen`)
A `ModalScreen` bound to `?` (toggle), `esc`, and `q` documents the keybindings, every metric label, and \u2014 critically \u2014 the otherwise-undocumented status-line tokens (`span`, `energy`, `THERMAL`, `THROTTLING:CPU/GPU`, `MEM-BOUND>`, `PKG>`, `SWAP+`) and the color-degradation / `NO_COLOR` behavior. The `THROTTLING` token fires when a silicon domain is busy yet held below its DVFS max frequency while hot (see §5.3 alert path). The `energy` token is the cumulative session energy (\u222b package power dt since launch, displayed in mWh/Wh), the live-TUI counterpart to `Profiler.total_package_joules`.

### 5.5 Alert Counters & Threshold Validation
Alert/throttle/session-energy analytics live in **L2** (`analytics.AlertEngine`) as of LC-3 (v1.3.1), not the view: the engine is constructed from threshold *values* (never a `DashboardConfig`, so `analytics` stays TUI-agnostic), and `feed(snapshot)` returns an immutable `AlertFrame` (thermal/cpu-throttle/gpu-throttle/bw/pkg/swap verdicts + `swap_rise_gb` + `session_energy_j`). `HardwareDashboard._compute_alerts` is now a thin formatter that turns the frame into status-line tokens — no alert math remains in `tui/`. The engine tracks:
- **Bandwidth Saturation**: Triggers when Memory bandwidth exceeds a configured percentage of the SoC's reference limit (defaults to `85%`). Normalised via `analytics.bandwidth_percent(snapshot, max_total_bw)`.
- **Power Peak Alert**: Triggers when Package Watts exceeds a configured percentage of the SoC's reference limit (defaults to `85%`). Normalised via `analytics.package_power_percent(snapshot, package_ref_w)`.
- **Throttle**: `analytics.domain_throttling(...)` flags a silicon domain busy + held below its DVFS ceiling + hot; sustained like the others.
- **Swap Rise**: Triggers when Swap space usage increases by a configured limit (defaults to `0.3 GiB`, `--alert-swap-rise-gib`) across the sustain window. The rise is computed from the exact `swap_used_bytes` counts, not the rounded `*_gb` view, so a 0.1 GiB threshold cannot trip on rounding alone.
- **Alert Sliding Window**: To prevent intermittent spikes from causing noisy notifications, alerts are validated using a sliding window. The metric must exceed the threshold for a sustained count of sequential intervals (defaults to `3` samples) before the frame reports the alert.
- **Session energy**: integrated as `Σ package_watts × dt` where `dt` is the real inter-frame delta from `snapshot.timestamp` (the first `feed()` has no prior timestamp, so it contributes 0 J) — the live-TUI counterpart to `Profiler.total_package_joules`.

### 5.6 Headless Export Modes (`export.py`)
The same `Monitor` sampling layer feeds two non-TUI output modes, routed from `main()` ahead of the TUI, turning actop from a viewer into an observability source:
- `--json`: streams metrics as NDJSON to stdout (`dataclasses.asdict` over `SystemSnapshot`), one line per sample.
- `--serve PORT`: runs a stdlib `ThreadingHTTPServer` exposing Prometheus `/metrics` (scalar plus per-core labelled gauges), backed by a warm background sampler.

> Since LC-2 (v1.3.0) per-process rows ride on `SystemSnapshot.processes` (§5.7), so the export modes *could* emit them — but still don't. Doing so means bounding cardinality (top-N, `comm` label not `pid`) — a deliberate follow-up until a concrete consumer needs it, not yet built.

### 5.7 Per-Process Power Attribution (`PWR`) — CPU shipped v1.0.2, GPU shipped v1.2.0
The process table's `PWR` column answers "which process is drawing the watts" sudoless — Activity Monitor's "Energy Impact" without `sudo`. The CPU half reuses the per-PID CPU-time deltas already computed for `CPU%` (§2.3); the GPU half adds one new native binding, `gpu_registry.py`.
- **CPU model**: `PWR_cpu = (proc CPU-time Δ / Σ all-procs CPU-time Δ) × SystemSnapshot.cpu_watts`. This is a **partition** of package CPU power, so `Σ(PWR_cpu)` reconciles to `cpu_watts` by construction.
- **GPU model**: `gpu_registry.get_gpu_time_by_pid()` reads each `AGXDeviceUserClient`'s `IOUserClientCreator`/`AppUsage` properties off every `IOAccelerator`-matched service (`IOServiceMatching(b"IOAccelerator")` + `IORegistryEntryGetChildIterator`), summing `accumulatedGPUTime` ns per pid across every client and every accelerator (multi-die safe). `utils.get_top_processes()` deltas this the same way it deltas CPU time — via a shared `_delta_ns()` helper factored out of the CPU pass — into `gpu_time_share`, a partition of `Σ all-procs GPU-time Δ` mirroring the CPU model. `analytics.attribute_power(share_cpu, share_gpu, cpu_watts, gpu_watts)` combines both into the final `PWR` value: `PWR = share_cpu × cpu_watts + share_gpu × gpu_watts`. `Σ(PWR)` now reconciles to `cpu_watts + gpu_watts`, surfaced as a `Σ shown N.NW / pkg CPU+GPU M.MW` token in the table's border subtitle.
- **Denominator/visibility symmetry**: IOKit's registry has no same-UID restriction, so it sees privileged system processes (e.g. `WindowServer`) that `native_sys.get_native_processes()` silently drops. Those pids can never get a process-table row, so the GPU pass excludes them from `total_gpu_delta_ns` too (skip caching/summing any pid absent from that poll's `native_procs`) — otherwise every visible process's `gpu_time_share` would be diluted against GPU time no row could ever claim, breaking the "numerator and denominator drawn from the same visible set" invariant the CPU pass relies on.
- **Labelled estimate**: attribution is by wall time, so a process pinned to E-cores is over-attributed and one on P-cores under-attributed (DVFS scales it further); GPU has no equivalent per-core skew. The token carries an `est` marker and the `HelpScreen` documents the caveat. A cycle-/per-core-power-weighted refinement is a future improvement.
- **Lifecycle**: `cpu_time_share` is `None` (pending, first sample) or a real share; `gpu_time_share` is `0.0` (real — never opened a GPU client) or `None` (pending — has a client, no delta yet) or a real share. The `PWR` cell's `–` (first-sample) rule triggers on `cpu_time_share is None` alone — every process eventually gets a CPU reading, most never get a GPU one at all, so GPU stays the secondary/additive signal. A fully idle poll (Σ Δ = 0 in either domain) yields all-zero shares with no divide-by-zero.
- **Where it lives (LC-2, v1.3.0)**: `utils.get_top_processes` (L1) emits `cpu_time_share`/`gpu_time_share` (watts stay out of `utils`); **L2** (`api._sample_to_snapshot` → `_processes_to_samples`) calls `analytics.attribute_power` once per process and stores the result on `ProcessSample.attributed_w`, so per-process power is a data point on `SystemSnapshot.processes` any API consumer gets (opt in via `Monitor(include_processes=True)`), not render-time math. The process table (`ActopApp._refresh_process_table` in `tui/app.py`) now just reads `attributed_w`; `sort_processes`'s `SORT_POWER` orders by it directly (no watts parameters). Rejected alternatives (`proc_pid_rusage` energy fields, `TASK_POWER_INFO_V2`) and the validating spike are recorded in git history (PR #11). ANE has no per-process registry entry (confirmed via a full `ioreg -l` scan) and stays out of scope.

### 5.8 Documentation capture (TUI → docs)
The TUI frames in this doc and the README are generated from **real** actop output, not hand-drawn — hand mockups drift from the code (wrong version, stale layout, invented labels) and can't reproduce the Braille sparklines. Two repo-local Claude Code skills own this, both keyed off the same public run path (`run-actop`: launch in `tmux`, wait for the ready marker, drive keybindings):
- **`capture-tui-diagram`** (`.agents/skills/capture-tui-diagram/`): captures a live frame via `tmux capture-pane`, cleans it (`clean_capture.py` strips the splash spinner, scrollbar thumbs, trailing pad), and splices it into a doc verbatim (never hand-transcribed). Produces the **still ASCII frames** in §5 here and the README. Single-column (`stack`) frames may be `--compress`ed; side-by-side (`grid`, dashboard+table) frames go in verbatim so box borders don't tear.
- **`record-tui-gif`** (`.agents/skills/record-tui-gif/`, since v1.4.6): records the **animated README hero** (`images/actop-demo.gif`) with [`vhs`](https://github.com/charmbracelet/vhs) — a scripted, reproducible terminal→GIF. `record.sh` drives a live GPU workload (`gpu_workload.py` against a llama.cpp or Ollama-compatible router — OpenAI protocol for llama.cpp at `:9040`, native `/api/generate` for the ollama fallback at `:11433`) so the gauges actually move, records the `.tape`, and **always stops the workload** (EXIT/INT/TERM trap). The tape warms actop up off-camera so the first visible frame is already live, and does layout switches off-camera (a `grid` frame at the taller `stack` height would leave a gap) while showing the glyph/process toggles on-camera. Re-run after any TUI/layout change.

Both are dev-time tooling only — no runtime dependency, and `vhs` is a maintainer prerequisite (`brew install vhs`), not shipped with actop.

---

## 6. Verification and Testing Contract

Performance validation is maintained under `tests/` using three distinct verification scopes:
1. **CLI and Parameter Contracts (`test_cli_contract.py` / `test_sampler.py`)**: Asserts correct argument parsing boundaries (e.g. interval steps, regex patterns) and confirms that calculated metrics fall within valid physical bounds:
   - Utilizations: $0\% \le \text{util} \le 100\%$.
   - Wattage: $\ge 0.0\text{ W}$.
   - Frequencies: $> 0\text{ MHz}$.
2. **SMC Class Verification (`test_smc.py`)**: Asserts that temperature lists are not empty and that all active keys parse into valid float numbers; `read_fan_info()` readings, when `fan_available` is true, are asserted non-empty with each fan's `current` in a physical RPM range and `max` either `None` or a physical RPM.
3. **Runtime Consistency (`test_runtime_contracts.py`)**: Exercises the dynamic DVFS classification model to guarantee no division-by-zero occurrences and verifies correct hardware profile mappings across Apple's M1 through M4 series of processors.
