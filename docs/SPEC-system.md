# `actop` System Design

actop is a sudoless, subprocess-free Apple Silicon performance monitor built on
pure-Python `ctypes` bindings to macOS kernel frameworks.

---

## 1. Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                                  TEXTUAL TUI                                 │
│          (app.py / widgets.py: HardwareDashboard, ProcessTable)              │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       │  SystemSnapshot (sole contract)
                                       ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                              API / MONITOR LAYER                             │
│                  (api.py: Monitor / Profiler / AsyncMonitor)                 │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       │  SampleResult
                                       ▼
┌──────────────────────────────────────┴───────────────────────────────────────┐
│                           METRIC SAMPLING ENGINE                             │
│       (sampler.py: IOReportSampler + utils.py: RAM/CPU/GPU aggregators)      │
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

### 1.1 Core principles

- **ctypes, no subprocesses.** All metrics pulled directly from kernel memory
  (`mach_host_self`, `sysctl`, `proc_pidinfo`). No `psutil`, no `powermetrics`.
- **Three-layer data flow (L1→L2→L3).**
  - **L1 — acquisition:** `ioreport.py`, `smc.py`, `gpu_registry.py`,
    `native_sys.py`, `sampler.py`, `utils.py` produce raw `SampleResult`s.
  - **L2 — data points:** `models.py` (`SystemSnapshot`), `api.py`
    (`Monitor`/`Profiler`), `analytics.py` (`AlertEngine`→`AlertFrame`,
    power attribution, throttle detection), `soc_profiles.py`,
    `power_scaling.py`. `SystemSnapshot` is the **sole per-frame contract**.
  - **L3 — presentation:** `tui/*` and `export.py` consume only L2 types.
    No per-frame L1 acquisition or domain math in the view layer.
- **Cross-platform-safe imports.** All `ctypes.cdll.LoadLibrary` calls are
  guarded by `sys.platform == "darwin"`; public entry points degrade to
  sentinels on non-Darwin CI.
- **Zero sudo.** Queries `AppleSMC` and non-root `IOReport` channels only.

### 1.2 Identity & distribution

actop = "Apple Chip top" — whole-chip monitor (CPU/GPU/ANE/memory/power/
thermal). Renamed from agtop at v1.0.0 (no compatibility shim). Distributions:
PyPI via OIDC Trusted Publishing, Homebrew via dedicated tap
`binlecode/homebrew-actop`. Three differentiators: first-class Python API
(`Monitor`/`Profiler`), 16 M1–M4 SoC profiles with real reference wattages,
session energy as a metric.

### 1.3 System parameters

| Parameter | CLI flag | Default | Notes |
|---|---|---|---|
| Sample interval | `--interval` | 2 s | |
| Avg window | `--avg` | 30 s | Rolling average for `avg N · max N` labels |
| Subsamples | `--subsamples` | 1 | Internal IOReport deltas per interval |
| Chart glyph | `--chart-glyph` | `dots` | Braille (2 samples/char) or `block` (1) |
| Layout | `--layout` | `grid` | Two-column; `stack` single-column |
| Palette | `--palette` | `thermal` | blue→red; `viridis` (colorblind-safe); `mono` |
| Theme | `--theme` | `textual-dark` | 8 Textual themes, orthogonal to `--palette` |
| Power scale | `--power-scale` | `profile` | SoC reference; `auto` = rolling peak |
| Show cores | `--show-cores` | `False` | Per-core panels inside cluster boxes |
| Show residency | `--show-residency` | `True` | DVFS residency distribution rows |
| Process filter | `--proc-filter` | `""` | Regex, implies `--show-processes` |
| Show processes | `--show-processes` | `False` | Process panel; works in export mode too |
| JSON processes | `--json-processes` | `False` | Per-process rows in `--json` output |
| Serve processes | `--serve-processes` | `False` | Per-process rows in `--serve` output |

### 1.4 Alert thresholds

| Parameter | CLI flag | Default | Range |
|---|---|---|---|
| Bandwidth saturation | `--alert-bw-sat-percent` | 85% | 1–100 |
| Package power peak | `--alert-package-power-percent` | 85% | 1–100 |
| Throttle frequency | `--alert-throttle-freq-percent` | 90% | 1–100 |
| Swap rise | `--alert-swap-rise-gib` | 0.3 GiB | ≥0 |
| Sustain window | `--alert-sustain-samples` | 3 | ≥1 |

All alerts are sustain-counted: the condition must hold for `sustain_samples`
consecutive frames before the `AlertFrame` reports it. Throttle detection
additionally gates on fixed constants:
`_THROTTLE_UTIL_GATE = 80%` (cluster must be busy) and
`_THROTTLE_TEMP_C = 90°C` (die-temp fallback when thermal state is Nominal).

### 1.5 SoC profile reference values

`package_ref_w = max(cpu_chart_ref_w + gpu_chart_ref_w + ane_max_w, 1.0)`.
`ane_max_w` defaults to 8.0 W across M1–M4 pending per-generation calibration.
`max_mem_bw` is the peak unified-memory bandwidth in GB/s (decimal, Apple's
unit). 16 profiles ship for M1–M4 base/Pro/Max/Ultra; unknown M-series chips
fall back to tier defaults (Ultra/Max/Pro/base) pinned to M4-era wattages.

---

## 2. Native Bindings (`native_sys.py`)

Loads `libSystem.B.dylib`, `libobjc.A.dylib`, `IOKit.framework`,
`CoreFoundation.framework` as singletons.

### 2.1 Memory & swap

RAM via Mach VM statistics (`host_statistics64`, flavor `HOST_VM_INFO64`):
$$\text{Used} = (\text{internal} - \text{purgeable} + \text{wire} + \text{compressor}) \times \text{page\_size}$$

Swap via `sysctl("vm.swapusage")` → `struct xsw_usage` (32 bytes).

**Unit rule:** Every memory quantity crosses seams as raw bytes
(`ram_used_bytes`, `swap_used_bytes`, `ProcessSample.rss_bytes`).
Binary prefixes (GiB/MiB) applied only at display time. Bandwidth is the
deliberate exception — `bandwidth_gbps` is decimal GB/s (Apple's own unit
for the bus). The deprecated `*_gb`/`*_GB`/`*_gigabytes` fields were
always GiB under a decimal name; removed in 1.8.0.

### 2.2 Process enumeration

`proc_listpids(type=1)` → `proc_pidinfo(flavor=2)` for `ProcTaskAllInfo`:
name (`pbi_name`), RSS/VMS bytes, CPU time (`pti_total_user` +
`pti_total_system` deltas drive CPU% and power share), thread count,
start time (PID-reuse guard: key on `(pid, start_tvsec)`).

### 2.3 Command-line & thermal state

`KERN_PROCARGS2` sysctl for full command lines. Thermal state via
Objective-C bridge: `objc_msgSend([NSProcessInfo processInfo], thermalState)`
→ `Nominal`/`Fair`/`Serious`/`Critical`.

### 2.4 Network & disk I/O (native ctypes)

Both readers return **cumulative counters**; the sampler deltas them against
the previous poll and divides by elapsed seconds — the same delta-over-interval
pattern as memory bandwidth (§3.5). Rates cross seams in **bytes/s**.

**Network** — `native_sys.read_network_totals()`: `getifaddrs()` → walk the
linked list, keep entries with `ifa_addr->sa_family == AF_LINK` and without
`IFF_LOOPBACK` (`lo0` excluded, so loopback traffic never moves the rate),
cast `ifa_data` → `struct if_data`, sum `ifi_ibytes`/`ifi_obytes`/
`ifi_ipackets`/`ifi_opackets`. `freeifaddrs()` every call (one allocation per
tick — no leak over a long run). `uint32` counters can wrap under sustained
>1 GB/s transfers; the sampler's `max(0, delta)` guard absorbs the wrap.

**Disk** — `disk_registry.read_disk_totals()`: `IOServiceMatching
("AppleAPFSVolume")` → sum each volume's `Statistics` dict
(`"Bytes read from block device"` / `"Bytes written to block device"` + request
counts); **fallback** to `IOBlockStorageDriver` (keys `"Bytes (Read)"` /
`"Bytes (Write)"` / `"Operations (Read)"` / `"Operations (Write)"`) only when
no APFS volume matches. Same IOKit/CF traversal as `gpu_registry.py` — no
new binding classes. `uint64` CFNumber counters, so wrap is theoretical; the
same `max(0, …)` guard is applied for uniformity.

**Provenance & maintenance.** Both paths were verified live on-device,
unprivileged, and cross-checked against mactop's shipped implementation —
including two disproved guesses the spike corrected: `net.link.generic.system.stats`
does not exist on macOS, and the disk fallback order is APFS-first, not
`IOBlockStorageDriver`-first. The `ifaddrs`/`if_data` struct layouts were
transcribed from the live SDK headers (`<net/if.h>`, `<net/if_var.h>`,
`#pragma pack(4)`) and are version-sensitive — re-verify the offsets on a macOS
SDK bump.

**Availability** — `net_available`/`disk_available` on `SystemSnapshot` mirror
`bandwidth_available`: `False` (with zeroed rates) on non-Darwin or when no
usable counters exist; the TUI hides the whole `Network` / `Disk` section
(§6.5) rather than render a phantom `0 B/s`. `hw.memsize`/`hw.pagesize` are module-cached (fixed until reboot) so
the per-tick sysctl floor stays low under the added reader calls.

---

## 3. Telemetry Sampling (`sampler.py` & `ioreport.py`)

### 3.1 IOReport channels

Private `libIOReport.dylib` subscriptions: `Energy Model`, `CPU Stats` /
`CPU Core Performance States`, `GPU Stats` / `GPU Performance States`,
`PMP` / `DCS BW` (bandwidth). Pipeline: `IOReportCopyChannelsInGroup` →
`IOReportCreateSubscription` → `IOReportCreateSamples` →
`IOReportCreateSamplesDelta`.

### 3.2 DVFS classification

Reads `AppleARMIODevice`→`pmgr`→`voltage-states` (binary `<II` pairs:
freq Hz, voltage). Classifies tables by entry count and max frequency:
P-core (≥15 entries, >2 GHz), E-core (5–12 entries), GPU (10–20 entries).

### 3.3 Residency → utilization

State residencies are cumulative nanoseconds per P-state/V-state.
$$\text{Weighted Freq} = \frac{\sum(\text{State Freq} \times \text{Residency})}{\text{Active Duration}}$$
$$\text{Active\%} = \frac{\text{Active Duration}}{\text{Total Duration}} \times 100$$

### 3.4 GPU: single unified channel

IOReport exposes one `GPUPH` (GPU Performance Handler) channel — no per-core
ALU metrics. Apple Silicon's GPU acts as a monolithic co-processor under a
unified voltage/clock domain. Global utilization and average frequency only.

### 3.5 Memory bandwidth (`PMP`/`DCS BW`)

32 bandwidth-bucket residency channels (labels like `"32GB/s"`, `"64GB/s"`).
Weighted-mean computation identical to DVFS (§3.3). Multi-die SoCs: one
`AMCC RD+WR` channel per controller die, summed for whole-chip total.

Per-agent channels (`EACC`/`PACC`/`AGX`) hard-cap at 32 GB/s while `AMCC`
spans ~1 TB/s — per-agent attribution is unreliable at high bandwidths,
so only the `AMCC` aggregate ships. `_keep_states()` filters per-state
extraction to `AMCC*` channels only: +0.39% CPU vs +0.70% unfiltered.

`SystemSnapshot.bandwidth_available` is `False` when no DCS BW channel exists
→ Mem BW row hidden in TUI.

### 3.6 GPU dual-source utilization (`gpu_registry.py`)

| | Primary (`gpu_util_pct`) | Driver (`device`/`renderer`/`tiler`) |
|---|---|---|
| Source | IOReport residency (interval-averaged) | IOKit `IOAccelerator`→`PerformanceStatistics` (instantaneous) |
| Needs DVFS table | Yes | No |
| Cost | Included in subscription | ~0.025 ms/call |

Driver read is a fallback (when GPU DVFS table unclassified) plus a
Renderer-vs-Tiler breakdown IOReport cannot express. Residency stays
primary because it is interval-averaged. Fallback rule lives in
`api._sample_to_snapshot`, not `analytics.py`.

### 3.7 SoC profiles (`soc_profiles.py`)

`get_soc_profile(raw_name)` is a total function (never raises):
1. 16 exact-match `KNOWN_SOC_PROFILES` (M1–M4 base/Pro/Max/Ultra) with
   `cpu_chart_ref_w`, `gpu_chart_ref_w`, `max_mem_bw`, `ane_max_w`.
2. Tier fallback: `re.compile(r"^Apple M\d+")` matches any future chip,
   routed by tier substring (Ultra/Max/Pro/base) to M4-era reference values.
3. Generic catch-all for unrecognized strings.

Tier fallback routing is future-proof; the reference wattages are approximate
for unknown chips until a hand-calibrated entry is added.

### 3.8 Deliberate non-goals

- Per-process GPU/ANE power: unavailable sudoless (`proc_pid_rusage`
  `ri_*_energy` fields flat at 0).
- GPU per-core metrics: hardware limitation (§3.4).

---

## 4. SMC Interface (`smc.py`)

### 4.1 Temperature

IOKit `AppleSMC` service discovery → key sweep (`#KEY` count → iterate).
Sensor classification: `Tp*`/`Te*` = CPU, `Tg*` = GPU. Reports **max** per
cluster (not mean — a throttling hotspot must not be masked). Per-cluster,
not per-core: Apple Silicon exposes ~112 CPU sensors for 16 cores with no
1:1 mapping; peers like btop also spread cluster-level averages across cores.

### 4.2 Fan RPM

`FNum` key → discover `F{n}Ac` (current RPM) and `F{n}Mx` (max RPM).
Both are SMC type `flt` (4-byte float). `fan_available` reports whether any
fan keys exist — fanless Macs hide the Fan row entirely. 0 RPM is a
legitimate idle reading (not filtered out).

---

## 5. Export Backends (`export.py`)

Two non-TUI modes, routed from `main()` ahead of the TUI:
- `--json`: NDJSON to stdout (`dataclasses.asdict` over `SystemSnapshot`).
- `--serve PORT`: Prometheus `/metrics` via stdlib `ThreadingHTTPServer`.

### 5.1 Per-process rows

`Monitor(include_processes=True)` fills `SystemSnapshot.processes`.
`snapshot_to_dict` carries them via `dataclasses.asdict`. CLI flags:
`--show-processes`, `--json-processes`, `--serve-processes`, `--proc-filter`.
Per-PID Prometheus gauges are unbounded cardinality — NDJSON is the safe
choice for process-level profiling; Prometheus consumers should pair with
a top-N scrape config.

### 5.2 Alert/throttle/energy

`run_json_stream` / `serve_prometheus` construct an `AlertEngine` from the
same CLI threshold flags the TUI uses, feed each snapshot through it, and
merge the `AlertFrame` into the output.

**Design decision.** AlertEngine lives in the export run loops, not inside
`Monitor`/`SystemSnapshot`. The engine is stateful and threshold-configured
— sitting inside a stateless per-frame snapshot contract is the wrong home.
The export loops already own the `Monitor` lifecycle; adding AlertEngine
beside it is a natural extension.

**Config plumbing.** `_run_export` resolves the SoC profile via
`get_soc_info()` → `max_total_bw` + `package_ref_w`, bundles with CLI
alert flags into `alert_engine_kwargs`, passes to export functions.
Lazy import — zero overhead when not opted in.

**NDJSON contract.** 10 top-level keys merged into each record:
`alert_thermal`, `alert_cpu_throttle`, `alert_gpu_throttle`,
`alert_mem_bound`, `alert_package_power`, `alert_swap_rise`,
`alert_swap_rise_gib`, `session_energy_j`, `effective_max_bw_gbps`,
`effective_max_package_w`.

**Prometheus contract.** 10 `actop_alert_*` / `actop_session_energy_joules`
/ `actop_effective_max_*` gauges. Boolean alerts emit `0`/`1` (never
`True`/`False`).

**Session energy.** `AlertEngine` integrates `Σ package_watts × dt` using
real `snapshot.timestamp` deltas. First feed = 0 J. The same primitive as
`Profiler.total_package_joules`.

**Serve loop.** Background sample thread stores `(snapshot, frame)` under
lock; `/metrics` handler reads both → `snapshot_to_prometheus(snap,
alert_frame=frame)`.

## 6. TUI (`tui/`)

### 6.1 Keybindings & layout

`App.run()` starts background polling thread via Textual `@work(exclusive=True)`.
Keybindings: `q` quit, `Space` pause, `s` sort, `g` glyph toggle, `l` layout
cycle (grid/stack), `c` cores toggle, `p` process table, `t` theme cycle
(8 themes), `/` regex filter, `?` help.

`grid` is four rows: P-CPU | E-CPU, GPU·ANE | Memory, Network | Disk, then
Power spanning both columns. Paired boxes are height-matched so their bottom
borders align; below `_GRID_MIN_WIDTH` (96 cols) grid degrades to `stack`.

All letter actions answer uppercase (Caps Lock/Shift — same Textual key).

### 6.2 Sparklines (`BrailleChart`)

Unicode Braille (`⠀`–`⣿`, 2 samples/char in `dots` mode) or
Block (`▂`–`█`, 1 sample/char in `block` mode). `fill="down"` mirrors the
trace about its top edge (top-anchored, hanging downward) — used for the lower
half of the I/O mirror charts (§6.5). In `dots` the mirror is exact (a second
cumulative bit table); in `block` the downward half quantizes to 2 levels per
row, since Block Elements ships only `▀` and `█` as upper fills and the
quarter-blocks at U+1FB82/U+1FB85 are absent from many terminal fonts. Color gradient:
blue→red, with palette selection (`thermal`/`viridis`/`mono` via
`--palette`) and terminal-color-tier degradation (truecolor→256→16→none,
honoring `NO_COLOR`).

### 6.3 Metric labels & alerts

Each headline carries rolling `avg N · max N` context (500-sample deques).
Avg over `--avg` window, max is session peak. Every stat carries its unit.

Alert/throttle/energy analytics live in L2 (`analytics.AlertEngine`).
The engine is constructed from threshold values (never `DashboardConfig`),
`feed(snapshot)` returns an `AlertFrame`. `HardwareDashboard._compute_alerts`
is a thin token formatter — no alert math in `tui/`. Alert types: bandwidth
saturation, package-power peak, thermal/cpu/gpu throttle, swap rise — all
sustain-counted (default 3 samples). Session energy displayed as
`energy NmWh` token in the status bar.

### 6.4 Help overlay

`ModalScreen` bound to `?` documents all keybindings, metric labels,
status-line tokens (`span`, `energy`, `THERMAL`, `THROTTLING:CPU/GPU`,
`MEM-BOUND>`, `PKG>`, `SWAP+`), and `NO_COLOR` behavior.

### 6.5 Network / Disk I/O sections

Two titled sections (`Network`, `Disk`), each a **mirrored pair**: the
inbound chart (`↓ In` / `↓ Read`) fills upward and the outbound one
(`↑ Out` / `↑ Write`) is stacked directly beneath it filling downward, so the
seam between them reads as a shared zero axis without spending a row drawing
one (the btop/vnstat convention). Labels sandwich the pair — inbound above,
outbound below — so each direction keeps its own `avg N · max N` at the same
box height two separate label+chart stacks would cost. Both start hidden and
are revealed by the first snapshot reporting `net_available` /
`disk_available`; a machine with no counters never shows an empty box. The two
hide independently: when only one reports counters the survivor occupies the
left grid column at half width, which is accepted rather than span-corrected
(a runtime `column-span` toggle for a rare case is not worth the machinery).

Charts are **always** auto-scaled — there is no SoC reference throughput for a
NIC or NVMe controller, so `--power-scale profile` has no analogue here.
`analytics.io_rate_percent(rate, peak, floor)` divides by
`max(floor, rolling_peak x1.25)`, where the peak spans **both directions** of a
box so ↓/↑ stay visually comparable. The floors
(`NET_RATE_FLOOR_BPS` = 1 MB/s, `DISK_RATE_FLOOR_BPS` = 10 MB/s) exist to stop
idle background chatter (mDNS keepalives, APFS journaling) from dividing by
itself and painting a full-scale chart. No alert threshold: neither bus has a
knowable ceiling to saturate against, so `AlertEngine` is not involved.

### 6.6 Per-process power attribution (`PWR`)

CPU: `PWR_cpu = (pid CPU-time Δ / Σ CPU-time Δ) × cpu_watts`.
GPU: `gpu_registry.get_gpu_time_by_pid()` → per-pid GPU-time Δ / Σ GPU-time Δ.
Combined: `analytics.attribute_power(share_cpu, share_gpu, cpu_watts,
gpu_watts)` → `ProcessSample.attributed_w`. Σ(PWR) reconciles to
`cpu_watts + gpu_watts`. Labelled estimate — wall-time attribution is
approximate (core-type/DVFS skew).

Lives in L2 (`api._sample_to_snapshot`), not render-time math. GPU pass
excludes pids invisible to `native_sys.get_native_processes()` to keep
denominator/visibility symmetry with the CPU pass.

---

## 7. Testing Contract

Tests live in `tests/`. Functional-only: every test verifies a specific
production behavior, not output shape. See `CLAUDE.md` → Testing Guidelines
for the full mandate. Key scopes:
- **CLI contracts** (`test_cli_contract.py`): argument parsing, physical bounds.
- **SMC** (`test_smc.py`): temperature/fan key discovery and parsing.
- **Runtime** (`test_runtime_contracts.py`): DVFS classification, profile mapping.
- **Export** (`test_export.py`): NDJSON/Prometheus format, AlertEngine pipeline.
