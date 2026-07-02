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

### Core Architecture Pillars:
1. **Direct Memory Access via `ctypes`**: Zero spawning of shell commands. All virtual memory, swap space, and process listings are pulled directly from memory in microsecond ranges.
2. **Private API Interop**: Uses the private C library `libIOReport.dylib` to capture real-time Energy Model (Joules), DVFS (residency/frequencies), and core active percentages.
3. **Zero Sudo Requirements**: Does not require root privileges. By querying the `AppleSMC` service and targeting the safe non-root `IOReport` channels, the tool runs securely under ordinary user accounts.
4. **Cross-Platform-Safe Imports**: All four native ctypes modules (`ioreport.py`, `native_sys.py`, `smc.py`, `gpu_registry.py`) guard their `ctypes.cdll.LoadLibrary` calls under `sys.platform == "darwin"`, so `import actop` and `python -m actop.actop --help` succeed on non-Darwin CI runners; public entry points degrade to empty/unavailable sentinels off-Darwin instead of crashing at import time.

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
- **Network / disk I/O is a non-goal.** Present in `psutil`-based tools (mactop / btop) but orthogonal to the IOReport-first SoC-power focus; adding it would reintroduce the `psutil` dependency surface actop is moving away from.
- **Per-process CPU power *is* attributed (since v1.0.2); GPU / ANE / true-energy per process are not.** actop partitions `SystemSnapshot.cpu_watts` across processes by each PID's CPU-time share (the `PWR` column, see §5.7) — an estimate, since a P-core-second draws more than an E-core-second, but one that reconciles to package CPU power by construction. This is white space no direct peer (asitop / mactop / macmon) fills. What remains unavailable sudoless: per-process **GPU / ANE** power, and a true hardware per-process **energy** counter (`proc_pid_rusage`'s `ri_*_energy` fields stay flat at 0 for ordinary compute — a Phase-0 spike disproved that path). Per-process CPU/RSS/threads come from the native process enumeration in §2.3.
- **GPU per-core metrics** are a hardware limitation, not a scope choice — see §3.4.

### 3.7 SoC Profile Resolution & Fallback (`soc_profiles.py`)

Chart scaling (`--power-scale profile`) and alert thresholds (§5.5) need a reference wattage/bandwidth ceiling per chip. `get_soc_profile(raw_name)` resolves the `sysctl`-reported chip brand string to one of three tiers of specificity, and is a **total function** — every path returns a valid `SocProfile`; none raise:

1. **Exact match** — 16 hand-calibrated `KNOWN_SOC_PROFILES` entries spanning M1–M4 (base/Pro/Max/Ultra), each with real reference `cpu_chart_ref_w` / `gpu_chart_ref_w` / `cpu_max_bw` / `gpu_max_bw`.
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

### 4.3 Fan RPM (shipped v1.2.2)
Fan tachometers use a separate, simpler discovery path than temperature: the `"FNum"` key (`ui8`) gives the fan count directly, so `_discover_fan_keys` builds the per-fan key names (`F0Ac`, `F1Ac`, ...) instead of sweeping the full key space. Verified on-device (Apple M4 Max) that actual-RPM keys are SMC type `"flt "` — the same 4-byte float type as temperature — not the `"fpe2"` fixed-point type originally guessed in the roadmap doc; `_read_float_cached` is reused unchanged.

`SMCReader.read_fan_rpms()` returns one RPM value per fan in index order, and does **not** filter out `0.0` (unlike temperature's invalid-sentinel handling) — 0 RPM is a legitimate idle reading on modern Macs that spin fans down at rest. `SMCReader.fan_available` reports whether any fan keys were discovered at all, independent of the current reading; this is the signal the TUI uses to hide the Fan row on fanless Macs (MacBook Air) rather than showing a phantom `0 RPM` — the same `bandwidth_available` hide-row pattern from §3.5, threaded through `SampleResult.fan_available` → `SystemSnapshot.fan_available`.

---

## 5. TUI Layout & Rendering Engine (`tui/`)

The user interface is powered by Textual. It is structured into a dynamic multi-pane top-like display.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  actop  v1.0.0 · [CPU Brand]    E-cores: X  P-cores: Y        [HH:MM:SS]     │
├──────────────────────────────────────┬───────────────────────────────────────┤
│ P-CPU 24% @3200MHz (48°C) avg 19% max 61% │ PID  Command   CPU%  PWR   MEM THD │
│ ⠋⠙⠹⠸⠼⠴⠦ (Braille util chart)             │  ──────────────────────────────   │
│ P00 12% ⠴⠦ │ P01 10% ⠴⠂                   │ 2041 ollama    112  18.7W 8.4G 18 │
│ E-CPU  4% @1200MHz (42°C) avg 6% max 22%  │ 1025 python     45   3.1W 120M  4 │
│ ⠋⠙⠹⠸⠼⠴⠦ (Braille util chart)             │ 502  WindowSrv   5   0.6W 350M  3 │
│ GPU 5% @900MHz  avg 8% · max 47%          │ Σ shown 22.4W / pkg CPU 25.3W est │
│ ANE 0% (0.0W) · RAM 18/32GB                │                                  │
│ Mem BW 120 GB/s · CPU/GPU/Package Power…  │                                  │
│ span 2m08s  ·  thermal: Nominal  alerts: none                                 │
└──────────────────────────────────────┴───────────────────────────────────────┘
```

### 5.1 Textual Application State (`app.py`)
`ActopApp` handles TUI setup and maintains keybindings:
- `q`: Quit.
- `p`: Pause / resume the sampling thread.
- `s`: Cycle process sorting column (`CPU%` \u2192 `PWR` \u2192 `RSS` \u2192 `PID`).
- `g`: Toggle charts between Braille dots and block glyphs.
- `t`: Show/hide the top processes table.
- `/`: Open the process regex filter bar.
- `?`: Show/hide the help overlay (`esc` / `q` also close it).

The application initiates a background thread via textual `@work(thread=True, exclusive=True)` to run the polling loop, delivering parsed snapshots to the main thread via a custom event, `MetricsUpdated`. A spinner splash covers the first sampler warm-up; the dashboard swaps in once the first snapshot arrives. The framework command palette is disabled (`ENABLE_COMMAND_PALETTE = False`).

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

The **Fan** row (shipped v1.2.2) is a plain label with no sparkline or avg/max context — a tachometer reading doesn't warrant the chart-history machinery the power/BW rows use — showing each fan's RPM (`Fan 1200/980 RPM` for multi-fan Macs). It is hidden entirely when `SystemSnapshot.fan_available` is false, the same hide-on-unavailable treatment as Mem BW.

### 5.4 Help Overlay (`HelpScreen`)
A `ModalScreen` bound to `?` (toggle), `esc`, and `q` documents the keybindings, every metric label, and \u2014 critically \u2014 the otherwise-undocumented status-line tokens (`span`, `energy`, `THERMAL`, `THROTTLING:CPU/GPU`, `MEM-BOUND>`, `PKG>`, `SWAP+`) and the color-degradation / `NO_COLOR` behavior. The `THROTTLING` token fires when a silicon domain is busy yet held below its DVFS max frequency while hot (see §5.3 alert path). The `energy` token is the cumulative session energy (\u222b package power dt since launch, displayed in mWh/Wh), the live-TUI counterpart to `Profiler.total_package_joules`.

### 5.5 Alert Counters & Threshold Validation
To alert users of resource bottlenecks, the `HardwareDashboard` monitors and tracks resource usage:
- **Bandwidth Saturation**: Triggers when Memory bandwidth exceeds a configured percentage of the SoC's reference limit (defaults to `85%`).
- **Power Peak Alert**: Triggers when Package Watts exceeds a configured percentage of the SoC's reference limit (defaults to `85%`).
- **Swap Rise**: Triggers when Swap space usage increases by a configured limit (defaults to `0.3 GB`).
- **Alert Sliding Window**: To prevent intermittent spikes from causing noisy notifications, alerts are validated using a sliding window. The metric must exceed the threshold for a sustained count of sequential intervals (defaults to `3` samples) before updating the status line.

### 5.6 Headless Export Modes (`export.py`)
The same `Monitor` sampling layer feeds two non-TUI output modes, routed from `main()` ahead of the TUI, turning actop from a viewer into an observability source:
- `--json`: streams metrics as NDJSON to stdout (`dataclasses.asdict` over `SystemSnapshot`), one line per sample.
- `--serve PORT`: runs a stdlib `ThreadingHTTPServer` exposing Prometheus `/metrics` (scalar plus per-core labelled gauges), backed by a warm background sampler.

> The export modes are `SystemSnapshot`-only today; per-process rows (§5.7) are **not** exported. Adding them means bounding cardinality (top-N, `comm` label not `pid`) — a deliberate non-goal until a concrete consumer needs it, not yet built.

### 5.7 Per-Process Power Attribution (`PWR`) — CPU shipped v1.0.2, GPU shipped v1.2.0
The process table's `PWR` column answers "which process is drawing the watts" sudoless — Activity Monitor's "Energy Impact" without `sudo`. The CPU half reuses the per-PID CPU-time deltas already computed for `CPU%` (§2.3); the GPU half adds one new native binding, `gpu_registry.py`.
- **CPU model**: `PWR_cpu = (proc CPU-time Δ / Σ all-procs CPU-time Δ) × SystemSnapshot.cpu_watts`. This is a **partition** of package CPU power, so `Σ(PWR_cpu)` reconciles to `cpu_watts` by construction.
- **GPU model**: `gpu_registry.get_gpu_time_by_pid()` reads each `AGXDeviceUserClient`'s `IOUserClientCreator`/`AppUsage` properties off every `IOAccelerator`-matched service (`IOServiceMatching(b"IOAccelerator")` + `IORegistryEntryGetChildIterator`), summing `accumulatedGPUTime` ns per pid across every client and every accelerator (multi-die safe). `utils.get_top_processes()` deltas this the same way it deltas CPU time — via a shared `_delta_ns()` helper factored out of the CPU pass — into `gpu_time_share`, a partition of `Σ all-procs GPU-time Δ` mirroring the CPU model. `utils.attribute_power(share_cpu, share_gpu, cpu_watts, gpu_watts)` combines both into the final `PWR` value: `PWR = share_cpu × cpu_watts + share_gpu × gpu_watts`. `Σ(PWR)` now reconciles to `cpu_watts + gpu_watts`, surfaced as a `Σ shown N.NW / pkg CPU+GPU M.MW` token in the table's border subtitle.
- **Denominator/visibility symmetry**: IOKit's registry has no same-UID restriction, so it sees privileged system processes (e.g. `WindowServer`) that `native_sys.get_native_processes()` silently drops. Those pids can never get a process-table row, so the GPU pass excludes them from `total_gpu_delta_ns` too (skip caching/summing any pid absent from that poll's `native_procs`) — otherwise every visible process's `gpu_time_share` would be diluted against GPU time no row could ever claim, breaking the "numerator and denominator drawn from the same visible set" invariant the CPU pass relies on.
- **Labelled estimate**: attribution is by wall time, so a process pinned to E-cores is over-attributed and one on P-cores under-attributed (DVFS scales it further); GPU has no equivalent per-core skew. The token carries an `est` marker and the `HelpScreen` documents the caveat. A cycle-/per-core-power-weighted refinement is a future improvement.
- **Lifecycle**: `cpu_time_share` is `None` (pending, first sample) or a real share; `gpu_time_share` is `0.0` (real — never opened a GPU client) or `None` (pending — has a client, no delta yet) or a real share. The `PWR` cell's `–` (first-sample) rule triggers on `cpu_time_share is None` alone — every process eventually gets a CPU reading, most never get a GPU one at all, so GPU stays the secondary/additive signal. A fully idle poll (Σ Δ = 0 in either domain) yields all-zero shares with no divide-by-zero.
- **Where it lives**: `utils.get_top_processes` emits `cpu_time_share`/`gpu_time_share` (watts stay out of `utils`); the process table (`ActopApp._refresh_process_table` in `tui/app.py`) calls `attribute_power` and adds the `SORT_POWER` sort mode (now `cpu_watts`/`gpu_watts`-aware, since ordering by attributed watts isn't equivalent to ordering by `cpu_time_share` alone once GPU is involved). Rejected alternatives (`proc_pid_rusage` energy fields, `TASK_POWER_INFO_V2`) and the validating spike are recorded in git history (PR #11). ANE has no per-process registry entry (confirmed via a full `ioreg -l` scan) and stays out of scope.

---

## 6. Verification and Testing Contract

Performance validation is maintained under `tests/` using three distinct verification scopes:
1. **CLI and Parameter Contracts (`test_cli_contract.py` / `test_sampler.py`)**: Asserts correct argument parsing boundaries (e.g. interval steps, regex patterns) and confirms that calculated metrics fall within valid physical bounds:
   - Utilizations: $0\% \le \text{util} \le 100\%$.
   - Wattage: $\ge 0.0\text{ W}$.
   - Frequencies: $> 0\text{ MHz}$.
2. **SMC Class Verification (`test_smc.py`)**: Asserts that temperature lists are not empty and that all active keys parse into valid float numbers; fan RPM readings, when `fan_available` is true, are asserted non-empty and within a physical RPM range.
3. **Runtime Consistency (`test_runtime_contracts.py`)**: Exercises the dynamic DVFS classification model to guarantee no division-by-zero occurrences and verifies correct hardware profile mappings across Apple's M1 through M4 series of processors.
