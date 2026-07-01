# TODO — `actop` feature-gap roadmap (`*top`-driven differentiation)

Status: **The `agtop → actop` rename shipped as `v1.0.0` (2026-06-30).** Its durable
record — identity, naming rationale, positioning, and the PyPI/Homebrew/PR-only
distribution model — now lives in [`DESIGN-system.md` §1.1](DESIGN-system.md). This file
is the single source of truth for the **mission-specific feature roadmap** that
justifies `actop` staying a `*top`. The flagship item (#1) shipped in v1.0.2; its
as-built design lives in [`DESIGN-system.md` §5.7](DESIGN-system.md) and the
decision trail (rejected paths, validating spike) is in git history (PR #11).

## Mission (first principles)

> **The complete, sudoless Apple-Silicon `*top` — it shows what no other monitor does.**

`actop` stays a live terminal monitor you *watch* (the `*top` use case). It wins not by
leaving the category but by **covering the whole chip and surfacing decision-grade
signals the others can't** — per-process power, bandwidth saturation, throttle state,
DVFS residency — all **in-process and without `sudo`**. The Python API
(`Monitor`/`Profiler`, `to_pandas()`) stays as the programmable layer underneath, not
the headline.

Positioning line: *"the Apple-Silicon `*top` that answers the questions the others
can't — which process is drawing the watts, am I memory-bandwidth-bound, am I being
throttled right now."*

---

# The feature gap (white space)

Each item: **what · why it's white space · data/module to build on · effort · acceptance.**
Everything below is feasible on the existing **sudoless in-process** stack.

## Tier 1 — headline differentiators (build on what exists; ship these as "why actop")

### 1. Per-process power / energy attribution ⭐ *the flagship* — ✅ **SHIPPED (v1.0.2)** · [as-built → `DESIGN-system.md` §5.7](DESIGN-system.md)
- **What**: an **Energy/Power (`PWR`) column** in the process table — "which process is drawing the watts." Activity Monitor's "Energy Impact," but in a sudoless TUI.
- **Why white space**: asitop/mactop/macmon/silitop show *system-total* power and a CPU%/RSS process list; **none attributes power/energy per process**. Nobody does it.
- **As built** (PR #11, `d659853`): **no new native binding.** A Phase-0 spike disproved the original `proc_pid_rusage`/`RUsageInfoV4` energy path (`ri_billed_energy`/`ri_serviced_energy` stay flat at 0 for ordinary compute). Instead `PWR` partitions `SystemSnapshot.cpu_watts` by each process's **CPU-time share** — reusing the `cpu_time_ns` already gathered by `PROC_PIDTASKALLINFO` for the `CPU%` column. Data flow `native_sys → utils.get_top_processes → tui/app.py` (process table lives in `ActopApp._refresh_process_table`, not `tui/widgets.py`). Ships with `SORT_POWER`, a `Σ shown / pkg CPU` reconciliation token, and a labelled P-vs-E estimate caveat.
- **Effort**: S–M (as delivered). **Acceptance met**: `PWR` tracks a known busy process and Σ(per-proc CPU power) reconciles to package CPU power by construction. **Remaining (optional):** `export.py` per-process output — bounded cardinality (top-N, `comm` label not `pid`); NDJSON can carry a bounded `processes` array. (The `DESIGN-system.md` fold-in is now done — §5.7.)

### 2. Bandwidth as % of SoC peak + saturation indicator ⭐ *the LLM answer* — ✅ **SHIPPED**
- **What**: render memory bandwidth not just as GB/s but as **% of this chip's peak**, with a saturation/`MEM-BOUND` indicator.
- **Why white space**: most tools omit bandwidth entirely; none frames it as the *"am I memory-bandwidth-bound?"* decision metric that governs LLM inference.
- **As built**: `_bandwidth_percent()` (`tui/widgets.py`) normalises `SystemSnapshot.bandwidth_gbps` against the **summed CPU+GPU channel references** (`cfg.max_cpu_bw + cfg.max_gpu_bw`, from `soc_profiles.py`) — *not* a separate `peak_bandwidth_gbps` field; that field was never added, the summed-refs figure is the reference. The percent drives a dedicated `Mem BW` chart with rolling avg/max and, once it holds above `--alert-bw-sat-percent` for `--alert-sustain-samples` frames, fires the sustained **`MEM-BOUND>N%`** status-line alert (`_compute_alerts`, `tui/widgets.py`; help-overlay token documented in `tui/app.py`).
- **Effort**: S–M (data already sampled).
- **Acceptance met**: on a bandwidth-heavy workload the % climbs toward the SoC's summed-channel reference and the `MEM-BOUND>` indicator fires; clears when it falls back below threshold.
- **Deviation from original plan**: normalises against summed CPU+GPU channel refs (aggregate `bandwidth_gbps` is all the sampler exposes; per-channel breakdown deferred — see `DESIGN-system.md` §5.3 / the bandwidth note in the sampler design). Indicator was renamed from the interim `BW>N%` token to `MEM-BOUND>N%`.

### 3. Thermal-throttle indicator — ✅ **SHIPPED**
- **What**: an explicit **`THROTTLING:CPU/GPU`** status token — "CPU/GPU held below max frequency under load right now," not just a temperature number.
- **As built**: `_domain_throttling()` (`tui/widgets.py`) fires per silicon domain on the decided **"busy + slow + hot"** rule (util ≥ 80% AND freq < `--alert-throttle-freq-percent`% of DVFS max AND thermal pressure elevated OR die temp ≥ 90°C), sustained over `alert_sustain_samples`. DVFS max is surfaced through new `SystemSnapshot.{ecpu,pcpu,gpu}_max_freq_mhz` fields (sampler → api). Token documented in the `?` help overlay.
- **Why white space**: everyone shows temps; **nobody clearly says you're being throttled**.
- **Detection rule (decided — "busy + slow + hot", strict)**: fire per silicon domain (P-cluster CPU, GPU) when, sustained over `alert_sustain_samples` frames, **all** hold:
  1. **busy** — cluster utilization ≥ a load gate (default **80%**); without this, an idle chip at low freq reads as throttled.
  2. **slow** — current cluster freq < `alert_throttle_freq_percent`% of the cluster's DVFS max (default **90%**).
  3. **hot** — `thermal_state` not in {`Nominal`, `Unknown`} **OR** die temp ≥ a temp gate. The `OR` keeps the rule alive where sudoless SMC die temps read `0.0`: the OS thermal-pressure signal (no sensor needed) can still satisfy it.
- **Build on (corrected sources)**:
  - current freq — `SystemSnapshot.{pcpu,gpu}_freq_mhz` (already sampled).
  - **max freq — the DVFS table (`native_sys.get_dvfs_tables_native()` → `IOReportSampler._dvfs`), *not* `soc_profiles.py`** (which has no frequency field). `max(table)` is the per-machine silicon ceiling.
  - thermals — `SystemSnapshot.thermal_state` + `{cpu,gpu}_temp_c` (`smc.py`). (Note: `native_sys.py` `throttled_count` is a **memory-page** counter, unrelated — do not use.)
  - surface via the existing alert/status path in `tui/widgets.py._compute_alerts`.
- **Plumbing required (none exists yet)**:
  - add `pcpu_max_freq_mhz` / `ecpu_max_freq_mhz` / `gpu_max_freq_mhz` to `SystemSnapshot`; sampler emits them in the cluster-metrics dict (mirroring `P-Cluster_freq_Mhz`), `api.py` maps them — keeps max freq testable through the public API.
  - add `alert_throttle_freq_percent` to `DashboardConfig` + a `--alert-throttle-freq-percent` CLI flag (default 90), mirroring `alert_bw_sat_percent`; load gate + temp gate as module constants.
  - `_throttle_counter` reusing `alert_sustain_samples`; append a `THROTTLING` token (optionally `THROTTLING:CPU`/`:GPU`); document it in the `?` help overlay (`app.py`) and `DESIGN-system.md`.
- **Effort**: S–M.
- **Acceptance**: under sustained load that induces throttling, the indicator fires and names the domain; clears when freq recovers or thermals fall back to Nominal. **Functional test**: mount a `HardwareDashboard`, feed `SystemSnapshot`s (busy + capped freq + elevated thermal) via `update_metrics`, assert the token appears; feed a recovered snapshot, assert it clears.

## Tier 2 — deep-silicon signals (unique to in-process IOReport; harder)

### 4. DVFS P-state residency distribution
- **What**: per-cluster **time-in-each-frequency-state** histogram (how the silicon actually behaved, not just instantaneous freq).
- **Why white space**: only your in-process IOReport access makes this cheap; powermetrics-based tools (asitop/silitop) can't easily match it.
- **Build on**: the **DVFS table discovery already in `sampler.py`** + residency data in `ioreport.py`; new compact widget in `tui/widgets.py`.
- **Effort**: M.
- **Acceptance**: residency distribution shifts toward high-freq states under load and idle states at rest.

### 5. Per-process GPU / ANE attribution (stretch)
- **What**: extend #1 to GPU/ANE share per process.
- **Why white space + caveat**: nobody does it; macOS exposes per-process GPU only partially → expect **approximate** attribution. Ship clearly labeled as estimated.
- **Effort**: L (research-y).

## Tier 3 — completeness parity (secondary; NOT white space)
Network I/O, disk I/O, fan RPM, SSD/battery temps. mactop/silitop already have these — they're *parity*, not differentiation. Add only after Tier 1–2; don't let them displace the wins.

## Supporting (keep, don't headline)
- **Python API** (`api.py` `Monitor`/`Profiler`, `to_pandas()`) and **exports** (`export.py` NDJSON/Prometheus) stay — they *feed* the differentiators (e.g. per-process energy in exports, workload correlation via an optional `Profiler.mark()`).

## Explicit non-goals (scope discipline)
- **Stay a terminal `*top`** — no menu-bar app, no web UI.
- **Silicon-focused** — we instrument the *chip*, not the whole box; Tier-3 system metrics are parity-only.
- Not a capture/replay oscilloscope (that was the abandoned `siliscope` direction) — `actop` is a live monitor.

## Update the comparison table
Add rows to README `## Where actop fits` for the white-space metrics so the table reads
as "things **only actop** has": **per-process power/energy**, **bandwidth % of peak**,
**throttle state**, **DVFS residency**. — ✅ **done** for the three shipped Tier-1 rows
(per-process power, bandwidth %, throttle state); DVFS residency row deferred until
Tier 2 #4 ships.

---

# References (prior art)

- **[plasma-umass/scalene](https://github.com/plasma-umass/scalene)** (13.5k★) — Python CPU+GPU+memory+**energy** profiler; bar for per-process energy reporting (feature #1, #5).
- **macOS "Energy Impact"** (Activity Monitor) + `task_power_info` / `proc_pid_rusage` — the per-process energy precedent and the API to read it (#1).
- **[jetperch/pyjoulescope_ui](https://github.com/jetperch/pyjoulescope_ui)** (106★) — power/energy UX, marker stats; reference for presenting watts/energy (#1, #2).
- **Peers / coverage benchmarks**: [Atoptool/atop], [aristocratos/btop], [Syllo/nvtop] (now Apple-aware), macmon, mactop, silitop — for parity scope (Tier 3) and positioning.
- **[google/perfetto](https://github.com/google/perfetto)** + **NVTX / torch.profiler** — marks/annotations model, *if* the optional `Profiler.mark()` workload-correlation is pursued.

# Suggested overall order
1. ~~Rename to `actop`~~ ✅ shipped as `v1.0.0` (record in [`DESIGN-system.md` §1.1](DESIGN-system.md)) — cleared install friction + set the brand.
2. Ship **Tier 1** (#1–#3) as the launch story ("the `*top` that shows what others don't"). ✅ **All shipped**: #1 per-process power (v1.0.2, [`DESIGN-system.md` §5.7](DESIGN-system.md)); #2 bandwidth % + `MEM-BOUND`; #3 `THROTTLING`. Tier 1 is the complete launch differentiator set.
3. Then **Tier 2** (#4–#5); **Tier 3** only as parity demand arises.
