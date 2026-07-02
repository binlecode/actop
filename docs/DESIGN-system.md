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
  - **`main` is strictly PR-only** (branch protection + `enforce_admins` + a local `.githooks/pre-push` guard); CI never pushes to `main`. Release mechanics and secret handling are documented in [`DESIGN-sdlc-cicd-release.md`](DESIGN-sdlc-cicd-release.md).
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
           ...
           ("compressor_page_count", ctypes.c_uint32),
           ("internal_page_count", ctypes.c_uint32),
           ...
       ]
   ```
4. **Activity Monitor Memory Logic**:
   $$\text{Used Bytes} = (\text{internal\_page\_count} - \text{purgeable\_count} + \text{wire\_count} + \text{compressor\_page\_count}) \times \text{page\_size}$$
   $$\text{Available Bytes} = \text{total\_ram} - \text{Used Bytes}$$

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
  already in GB/s — no division by the sample interval (`sampler._compute_bandwidth_gbps`).
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

1. **Exact match** — 16 hand-calibrated `KNOWN_SOC_PROFILES` entries spanning M1–M4 (base/Pro/Max/Ultra), each with real reference `cpu_chart_ref_w` / `gpu_chart_ref_w` / `cpu_max_bw` / `gpu_max_bw`, plus an `ane_max_w` field (ANE reference power, defaulted to `8.0 W` across M1–M4 pending per-generation calibration) that L2 reads as the denominator for `SystemSnapshot.ane_util_pct` — the LC-1 fix that moved the ANE ceiling out of `DashboardConfig` and into the profile layer where every other reference wattage lives.
2. **Generation-agnostic tier fallback** — `APPLE_M_SERIES_PATTERN = re.compile(r"^Apple M\d+")` matches *any* `Apple M<N>` string regardless of the generation number, so an unrecognized chip (M5, M6, M99, …) is still routed correctly by substring (`Ultra`/`Max`/`Pro`/else `base`) to `TIER_FALLBACKS`, without any code change. This routing is already future-proof; nothing here needs revisiting per chip launch.
3. **Generic catch-all** — a name that doesn't even match the `Apple M\d+` pattern (or is empty/`None`, normalized by `normalize_soc_name`) falls to `GENERIC_APPLE_SILICON_PROFILE` rather than raising.

**What tier fallback gets right vs. wrong.** The *routing* (never crashing, never missing a chart scale) is solved for all future generations by construction. What stays approximate is the *numbers*: `TIER_FALLBACKS` are pinned to the latest calibrated generation (currently M4-era reference wattages), so an M6 Ultra routed through the "Ultra" fallback is scaled against M4-Ultra-shaped ceilings, not M6-accurate ones. The exact fix is the same one used for all 16 shipped profiles — hand-add a `SocProfile` entry once real reference numbers exist for the new chip — not a bigger fallback engine.

**Rejected alternative: a dynamic voltage-state-derived estimator.** `native_sys.py`'s PMGR `voltage-states` reader (§3.2) already unpacks each DVFS table entry's `(freq_hz, voltage)` pair, but only `freq_hz` is kept — `voltage` is discarded. Deriving a power estimate from that discarded voltage word for unrecognized chips was considered and rejected: it needs per-generation calibration against real hardware to trust, and an uncalibrated "smart" guess would be less reliable than the current honest tier-default approximation, not more. Revisit only if a maintainer gets hardware to calibrate against, or exact per-chip profile lag becomes an actual reported user problem.

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

### 4.3 Fan RPM (shipped v1.2.2; current+max structured in v1.2.3)
Fan tachometers use a separate, simpler discovery path than temperature: the `"FNum"` key gives the fan count directly, so `_discover_fan_keys` builds the per-fan key names (`F0Ac`, `F1Ac`, ...) instead of sweeping the full key space. Verified on-device (Apple M4 Max) that actual-RPM keys are SMC type `"flt "` — the same 4-byte float type as temperature — not the `"fpe2"` fixed-point type originally guessed in the roadmap doc; `_read_float_cached` is reused unchanged. As of v1.2.3, discovery also probes the sibling **max-RPM key `F{n}Mx`** with the identical `flt`/size-4 guard (factored into the `_discover_flt4_key` helper), returning `{"ac": ..., "mx": ... | None}` per fan; a fan lacking the max key keeps `"mx": None` rather than being dropped. The min key `F{n}Mn` is intentionally not probed — peers read it only to clamp fan-set *writes*, which actop (read-only, unprivileged) never performs.

`SMCReader.read_fan_info()` returns one `FanReading(current, max)` per fan in index order (replacing the earlier bare-`list[float]` `read_fan_rpms()`). It does **not** filter out a `0.0` *current* value (unlike temperature's invalid-sentinel handling) — 0 RPM is a legitimate idle reading on modern Macs that spin fans down at rest — while `max` is `None` when the key is absent or reports `<= 0` (the "unknown" convention both peers use). `SMCReader.fan_available` reports whether any fan keys were discovered at all, independent of the current reading; this is the signal the TUI uses to hide the Fan row on fanless Macs (MacBook Air) rather than showing a phantom `0 RPM` — the same `bandwidth_available` hide-row pattern from §3.5, threaded through `SampleResult.fans` / `SampleResult.fan_available` → `SystemSnapshot.fans` / `fan_available`. `SystemSnapshot.fan_rpms` is retained as a derived current-only convenience (`[f.current for f in fans]`) so the `export.py` NDJSON/Prometheus contract is unchanged.

Reading `F{n}Mx` alone closes the only 2/2-converged peer gap for fans (both `mactop` and `macmon` read the max key). Target RPM (`F{n}Tg`) and mode (`F{n}Md`) are deliberately **not** read: they are mactop-only breadth that in mactop chiefly serve its root-gated *write* path, which actop excludes on security grounds.

---

## 5. TUI Layout & Rendering Engine (`tui/`)

The user interface is powered by Textual. The dashboard is four titled section
containers rendered under one of two layout presets (§5.1.1); the process table
is a fixed-width panel beside it and the thermal/alert status line is fixed app
chrome below both. Captures below are live frames on an Apple M4 Max.

**`grid`** (default) — two columns: the CPU section spans the left; `GPU · ANE`
/ `Memory` / `Power` stack on the right. Fits short terminals without scrolling.

```
                                           actop — v1.4.0 · Apple M4 Max · 4E+12P+40GPU                                    15:07:30
╭─ CPU ──────────────────────────────────────────────────────────╮╭─ GPU · ANE ────────────────────────────────────────────────────╮
│ P-CPU   2% @1152MHz (47°C)  avg 3% · max 4%                    ││ GPU 11% @338MHz (42°C)  avg 12% · max 13%                      │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ ││ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ ││ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡀⡀⡀⡀⡀⡀ │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ ││ GPU    [░░░░░░░░░░░░░░▒▒]  idle88 low12 mid0 high0             │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡀⡀⡀⡀⡀⡀ ││ ANE 0% (0.0W)  avg 0% · max 0%                                 │
│ P00   0% ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │ P01   0% ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ ││ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │
│ P02   0% ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │ P03   0% ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ ││ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │
│ P04   0% ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │ P05   0% ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │╰────────────────────────────────────────────────────────────────╯
│ P10  10% ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡀⡀⡀⡀⡀⡀ │ P11   8% ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡀⡀⡀⡀⡀⡀ │╭─ Memory ───────────────────────────────────────────────────────╮
│ P12   4% ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡀⡀⡀⡀⡀⡀ │ P13   3% ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡀⡀⡀⡀⡀⡀ ││ RAM 104.6/128.0GB sw:6.1/7.0GB  avg 81% · max 81%              │
│ P14   3% ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡀⡀⡀⡀⡀⡀ │ P15   1% ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡀⡀⡀⡀⡀⡀ ││ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡄⡄⡄⡄⡄⡄ │
│ P-CPU  [░░░░░░░░░░░░░░░░]  idle97 low2 mid1 high0              ││ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡇⡇⡇⡇⡇⡇ │
│ E-CPU  14% @787MHz (47°C)  avg 14% · max 16%                   ││ Mem BW 32.1 GB/s  avg 32.4 · max 32.7 GB/s                     │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ ││ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ ││ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡀⡀⡀⡀⡀⡀ │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │╰────────────────────────────────────────────────────────────────╯
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡆⡄⡄⡄⡄⡄ │╭─ Power ────────────────────────────────────────────────────────╮
│ E00  27% ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡀⡀⡀⡀⡀⡀ │ E01  17% ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡀⡀⡀⡀⡀⡀ ││ CPU 0.37W ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡀⡀⡀⡀⡀⡀  avg 0.6W · max 1.1W        │
│ E02  10% ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡀⡀⡀⡀⡀⡀ │ E03   5% ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡀⡀⡀⡀⡀⡀ ││ GPU 0.21W ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡀⡀⡀⡀⡀⡀  avg 0.2W · max 0.2W        │
│ E-CPU  [░░░░░░░░░░░░░░▒▓]  idle85 low4 mid8 high3              ││ Package Power 0.58W  avg 0.8W · max 1.3W                       │
╰────────────────────────────────────────────────────────────────╯│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │
                                                                  │ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡀⡀⡀⡀⡀⡀ │
                                                                  │ Fan 0/5777 · 0/5777 RPM                                        │
                                                                  ╰────────────────────────────────────────────────────────────────╯


span 2m04s  ·  energy 2mWh  ·  thermal: Nominal  alerts: none
 q Quit  p Pause  s Sort  g Glyph  l Layout  t Processes  ? Help
```

**`stack`** (`l` toggles) — the same four sections full-width in one scrollable
column; charts get the longest history span (blank chart bodies elided below):

```
                                           actop — v1.4.0 · Apple M4 Max · 4E+12P+40GPU                                    15:09:16
╭─ CPU ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ P-CPU   2% @1002MHz (47°C)  avg 2% · max 2%                                                                                      │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡀⡀⡀⡀ │
│ P00   0% ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │ P01   0% ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │
│ P02   0% ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │ P03   0% ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │
│ P04   0% ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │ P05   0% ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │
│ P10  11% ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡀⡀⡀⡀ │ P11   9% ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡀⡀⡀⡀ │
│ P12   5% ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡀⡀⡀⡀ │ P13   4% ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡀⡀⡀⡀ │
│ P14   3% ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡀⡀⡀ │ P15   1% ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡀⠀⠀⡀ │
│ P-CPU  [░░░░░░░░░░░░░░░░]  idle97 low2 mid1 high0                                                                                │
│ E-CPU  14% @906MHz (47°C)  avg 14% · max 17%                                                                                     │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡄⡄⡆⡄ │
│ E00  25% ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡀⡀⡀⡀ │ E01  18% ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡀⡀⡀⡀ │
│ E02   9% ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡀⡀⡀⡀ │ E03   7% ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡀⡀⡀⡀ │
│ E-CPU  [░░░░░░░░░░░░░░▓█]  idle85 low3 mid8 high4                                                                                │
╰──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
╭─ GPU · ANE ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ GPU 11% @338MHz (42°C)  avg 10% · max 11%                                                                                        │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡀⡀⡀⡀ │
│ GPU    [░░░░░░░░░░░░░░▒▒]  idle89 low11 mid0 high0                                                                               │
│ ANE 0% (0.0W)  avg 0% · max 0%                                                                                                   │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │
╰──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
╭─ Memory ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ RAM 104.9/128.0GB sw:6.1/7.0GB  avg 81% · max 81%                                                                                │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡄⡄⡄⡄ │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡇⡇⡇⡇ │
│ Mem BW 32.2 GB/s  avg 32.3 · max 32.6 GB/s                                                                                       │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡀⡀⡀⡀ │
╰──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
╭─ Power ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ CPU 0.29W ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡀⡀⡀⡀  avg 0.3W · max 0.6W                                                                          │
│ GPU 0.23W ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡀⡀⡀⡀  avg 0.2W · max 0.2W                                                                          │
│ Package Power 0.53W  avg 0.5W · max 0.8W                                                                                         │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡀⡀⡀⡀ │
│ Fan 0/5777 · 0/5777 RPM                                                                                                          │
╰──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯




span 4m16s  ·  energy 1mWh  ·  thermal: Nominal  alerts: none
 q Quit  p Pause  s Sort  g Glyph  l Layout  t Processes  ? Help
```

**Process table + live filter** — `t` shows the fixed 74-col table beside the
dashboard; `/` opens the regex filter bar (here `ollama`, matching each row's
full command line). Here the table leaves the dashboard under the grid width
threshold, so it has auto-degraded to `stack`:

```
                                           actop — v1.4.0 · Apple M4 Max · 4E+12P+40GPU                                    15:07:36
╭─ CPU ────────────────────────────────────────────────╮  ╭────────────────────────────────────────────────────────────────────────╮
│ P-CPU   3% @1252MHz (45°C)  avg 3% · max 4%          │  │ PID    Command             *CPU%  PWR    MEM (MB)  Threads             │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │  │ 62055  Ollama              0.0    0.00W  4553.8    22                  │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │  │ 62081  Ollama              0.0    0.00W  2904.1    22                  │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │  │ 94100  ollama              0.0    0.00W  44.7      24                  │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡀⡀⡀⡀⡀⡀⡀⡀⡀ │  │ 94107  ollama              0.0    0.00W  38.9      24                  │
│ P00   0% ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │ P01   0% ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │  │ 56077  python              0.0    0.00W  16.8      1                   │
│ P02   0% ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │ P03   0% ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │  │ 90042  zsh                 0.0    0.00W  3.3       1                   │
│ P04   0% ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │ P05   0% ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │  │                                                                        │
│ P10  11% ⠀⠀⠀⠀⠀⠀⡀⡀⡀⡀⡀⡀⡀⡀⡀ │ P11  11% ⠀⠀⠀⠀⠀⠀⠀⡀⡀⡀⡀⡀⡀⡀⡀⡀ │  │                                                                        │
│ P12   6% ⠀⠀⠀⠀⠀⠀⡀⡀⡀⡀⡀⡀⡀⡀⡀ │ P13   5% ⠀⠀⠀⠀⠀⠀⠀⡀⡀⡀⡀⡀⡀⡀⡀⡀ │  │                                                                        │
│ P14   3% ⠀⠀⠀⠀⠀⠀⡀⡀⡀⡀⡀⡀⡀⡀⡀ │ P15   0% ⠀⠀⠀⠀⠀⠀⠀⡀⡀⡀⡀⡀⡀⡀⡀⠀ │  │                                                                        │
│ P-CPU  [░░░░░░░░░░░░░░░░]  idle97 low1 mid2 high0    │  │                                                                        │
│ E-CPU  13% @740MHz (45°C)  avg 15% · max 22%         │  │                                                                        │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │  │                                                                        │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │  │                                                                        │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │▂▂│                                                                        │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡆⡄⡄⡄⡄⡄⡄⡇⡄ │  │                                                                        │
│ E00  23% ⠀⠀⠀⠀⠀⠀⡀⡀⡀⡀⡀⡀⡀⡀⡀ │ E01  17% ⠀⠀⠀⠀⠀⠀⠀⡀⡀⡀⡀⡀⡀⡀⡀⡀ │  │                                                                        │
│ E02   9% ⠀⠀⠀⠀⠀⠀⡀⡀⡀⡀⡀⡀⡀⡀⡀ │ E03   5% ⠀⠀⠀⠀⠀⠀⠀⡀⡀⡀⡀⡀⡀⡀⡀⡀ │  │                                                                        │
│ E-CPU  [░░░░░░░░░░░░░░▒▓]  idle86 low5 mid7 high2    │  │                                                                        │
╰──────────────────────────────────────────────────────╯  │                                                                        │
╭─ GPU · ANE ──────────────────────────────────────────╮  │                                                                        │
│ GPU 10% @338MHz (42°C)  avg 12% · max 14%            │  │                                                                        │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ │  │                                                                        │
│ ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡀⡀⡀⡀⡀⡀⡀⡀⡀ │  │                                                                        │
│ GPU    [░░░░░░░░░░░░░░▒▒]  idle89 low11 mid0 high0   │  │                                                                        │
│ ANE 0% (0.0W)  avg 0% · max 0%                       │  ╰───────────── Σ shown 0.0W / pkg CPU+GPU 0.6W · est CPU+GPU time share ─╯
span 1m44s  ·  energy 3mWh  ·  thermal: Nominal  alerts: none
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
- `t`: Show/hide the top processes table.
- `/`: Open the process regex filter bar.
- `?`: Show/hide the help overlay (`esc` / `q` also close it).

The application initiates a background thread via textual `@work(thread=True, exclusive=True)` to run the polling loop, delivering parsed snapshots to the main thread via a custom event, `MetricsUpdated`. A spinner splash covers the first sampler warm-up; the dashboard swaps in once the first snapshot arrives. The framework command palette is disabled (`ENABLE_COMMAND_PALETTE = False`).

The dashboard body is four titled section containers — `CPU`, `GPU · ANE`, `Memory`, `Power` (section titles live in the border, costing no content row). The thermal/alert status line is fixed **app chrome** below the dashboard (not inside its scrollable subtree), fed by an `AlertsComputed` message the dashboard posts each frame — so it stays visible even while a tall `stack` dashboard scrolls. CPU/GPU rail power collapse to single inline-sparkline rows (`CPU 6.59W <spark>  avg … · max …`); only Package Power keeps a full chart.

### 5.1.1 Layout presets (`grid` / `stack`)
The same four sections render under two presets, selected by `--layout` (default `grid`) and cycled live with `l` (`HardwareDashboard.set_layout_preset`, which never touches the history deques — switching mid-session loses no data). Presets are a pure CSS class swap in `HardwareDashboard.DEFAULT_CSS` (scoped to the widget); nothing about the data flow or metric computation differs between them.
- **`grid`**: a two-column CSS grid — the CPU section spans all three right-column rows (`row-span: 3`) while `GPU · ANE` / `Memory` / `Power` stack on the right. ~25 content rows; fits a 30-row terminal without scrolling.
- **`stack`**: all four sections full-width in a single scrollable column — the longest chart-history span (~47 rows, scrolls by design; the fixed status bar does not scroll with it).
- **Width auto-degrade**: below `_GRID_MIN_WIDTH` (96 cols) each grid column would fall under ~48 cols and stop being readable, so a requested `grid` silently renders as `stack` until the terminal widens again (`on_resize` → `_reconcile_layout`). `layout_preset` reports what was requested; `effective_layout_preset` reports what is applied. Width-adaptive Static rows (inline power sparks, core grids) re-render on the resize/preset swap so their spark widths track the new column width immediately rather than waiting for the next sample.

### 5.2 Custom Sparklines (`BrailleChart`)
The `BrailleChart` widget is designed to render charts efficiently inside Terminal constraints.
- Custom Rich formatting leverages Unicode **Braille patterns** (`\u2800` through `\u28FF`) or **Block elements** (`\u2582` through `\u2588`). One character is one time sample.
- **Braille Grid Scaling**: Each console row character contains a 2-column, 4-row dot matrix. A `height=2` chart provides $8$ discrete vertical steps per horizontal column, whereas a `height=4` chart provides $16$ steps.
- **Dynamic Heatmapping**: Every vertical column's element is styled along a sliding linear gradient mapping low utilization (Blue: `rgb(66, 135, 245)`) to extreme utilization (Red: `rgb(240, 70, 64)`).
- **Color tier degradation** (`resolve_color_mode` / `_pct_to_color`): the gradient adapts to terminal capability rather than always emitting truecolor. `resolve_color_mode()` honors `NO_COLOR` (https://no-color.org) unconditionally, then prefers the Textual console's detected `color_system`, falling back to `COLORTERM` / `TERM` inspection. The resolved tier maps each value to: `rgb()` (truecolor), the nearest 256-color cube index `color(N)` (256), a named blue\u2192green\u2192yellow\u2192red severity ramp (16), or no style at all (`none` \u2014 `NO_COLOR` / dumb terminals). The tier is resolved once at widget mount and threaded through rendering; `render()` is a thin wrapper over `_render_text(width, height)` so the colored output is exercisable without a live terminal.
- **Time-window labeling**: because one column is one sample, the visible span scales silently with terminal width. The status line leads with a `span <Ns/m/h>` token computed as chart width \u00D7 `--interval` (`_format_window_span` / `_chart_window_label`); it degrades to no token before layout, so the per-frame path never raises.

### 5.3 Metric Label Context (cur / avg / max)
Each live reading carries rolling context, matching frontier monitors (btop / bottom / macmon). The dashboard retains 500-sample deques per metric; histories are zero-padded for chart right-alignment, so avg/max ignore the leading padding (`_avg_max` reads only the last `_sample_count` real samples). Avg is taken over the `--avg` window; max is the session peak. Every stat carries its unit (`avg N% \u00B7 max N%`, watt labels show `W`, bandwidth shows `GB/s`) so it stays unambiguous beside a headline in a different unit (MHz / GB / W / GB/s). Applied to per-cluster CPU summary rows, GPU, ANE, RAM, memory-bandwidth, and CPU/GPU/package power labels.

The dashboard also surfaces two SoC-level headline metrics whose data already flowed through `SystemSnapshot` but was previously only consumed by alerts: **Mem BW** (unified-memory bandwidth in GB/s, the headline bottleneck for LLM inference) and **Package Power** (total SoC draw = CPU + GPU + ANE + other rails). Their chart percents reuse the same normalisation as the `MEM-BOUND>` / `PKG>` alerts (bandwidth vs summed CPU+GPU channel capacity; package vs `package_ref_w`). The Mem BW row is hidden when `SystemSnapshot.bandwidth_available` is false (no DCS channel on the platform).

The **Fan** row (shipped v1.2.2; current/max in v1.2.3) is a plain label with no sparkline or avg/max context — a tachometer reading doesn't warrant the chart-history machinery the power/BW rows use — showing each fan's `current/max` RPM when the max is known (`Fan 3200/6000 · 4100/6000 RPM` on a multi-fan Mac), falling back to bare current RPM (`Fan 1200 RPM`) when it isn't. Fans are joined with `·` so the inter-fan separator never collides with the `/` inside a single fan's `current/max`. It is hidden entirely when `SystemSnapshot.fan_available` is false, the same hide-on-unavailable treatment as Mem BW.

### 5.4 Help Overlay (`HelpScreen`)
A `ModalScreen` bound to `?` (toggle), `esc`, and `q` documents the keybindings, every metric label, and \u2014 critically \u2014 the otherwise-undocumented status-line tokens (`span`, `energy`, `THERMAL`, `THROTTLING:CPU/GPU`, `MEM-BOUND>`, `PKG>`, `SWAP+`) and the color-degradation / `NO_COLOR` behavior. The `THROTTLING` token fires when a silicon domain is busy yet held below its DVFS max frequency while hot (see §5.3 alert path). The `energy` token is the cumulative session energy (\u222b package power dt since launch, displayed in mWh/Wh), the live-TUI counterpart to `Profiler.total_package_joules`.

### 5.5 Alert Counters & Threshold Validation
Alert/throttle/session-energy analytics live in **L2** (`analytics.AlertEngine`) as of LC-3 (v1.3.1), not the view: the engine is constructed from threshold *values* (never a `DashboardConfig`, so `analytics` stays TUI-agnostic), and `feed(snapshot)` returns an immutable `AlertFrame` (thermal/cpu-throttle/gpu-throttle/bw/pkg/swap verdicts + `swap_rise_gb` + `session_energy_j`). `HardwareDashboard._compute_alerts` is now a thin formatter that turns the frame into status-line tokens — no alert math remains in `tui/`. The engine tracks:
- **Bandwidth Saturation**: Triggers when Memory bandwidth exceeds a configured percentage of the SoC's reference limit (defaults to `85%`). Normalised via `analytics.bandwidth_percent(snapshot, max_total_bw)`.
- **Power Peak Alert**: Triggers when Package Watts exceeds a configured percentage of the SoC's reference limit (defaults to `85%`). Normalised via `analytics.package_power_percent(snapshot, package_ref_w)`.
- **Throttle**: `analytics.domain_throttling(...)` flags a silicon domain busy + held below its DVFS ceiling + hot; sustained like the others.
- **Swap Rise**: Triggers when Swap space usage increases by a configured limit (defaults to `0.3 GB`) across the sustain window.
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

---

## 6. Verification and Testing Contract

Performance validation is maintained under `tests/` using three distinct verification scopes:
1. **CLI and Parameter Contracts (`test_cli_contract.py` / `test_sampler.py`)**: Asserts correct argument parsing boundaries (e.g. interval steps, regex patterns) and confirms that calculated metrics fall within valid physical bounds:
   - Utilizations: $0\% \le \text{util} \le 100\%$.
   - Wattage: $\ge 0.0\text{ W}$.
   - Frequencies: $> 0\text{ MHz}$.
2. **SMC Class Verification (`test_smc.py`)**: Asserts that temperature lists are not empty and that all active keys parse into valid float numbers; `read_fan_info()` readings, when `fan_available` is true, are asserted non-empty with each fan's `current` in a physical RPM range and `max` either `None` or a physical RPM.
3. **Runtime Consistency (`test_runtime_contracts.py`)**: Exercises the dynamic DVFS classification model to guarantee no division-by-zero occurrences and verifies correct hardware profile mappings across Apple's M1 through M4 series of processors.
