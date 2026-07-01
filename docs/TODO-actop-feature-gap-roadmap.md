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

### 2. Bandwidth as % of SoC peak + saturation indicator ⭐ *the LLM answer*
- **What**: render memory bandwidth not just as GB/s but as **% of this chip's theoretical peak**, with a saturation/`MEM-BOUND` indicator.
- **Why white space**: most tools omit bandwidth entirely; none frames it as the *"am I memory-bandwidth-bound?"* decision metric that governs LLM inference.
- **Build on**: existing `bandwidth_gbps` (sampler/models) ÷ reference peak bandwidth in `soc_profiles.py` (add a `peak_bandwidth_gbps` field per profile if not present); display in `tui/widgets.py`; reuse the alert path for a `MEM-BOUND` state.
- **Effort**: S–M (data already sampled).
- **Acceptance**: on a bandwidth-heavy workload, the % climbs toward 100% of the SoC's known peak and the saturation indicator fires.

### 3. Thermal-throttle indicator
- **What**: an explicit **`THROTTLING`** state — "GPU/CPU capped at N% of max frequency right now," not just a temperature number.
- **Why white space**: everyone shows temps; **nobody clearly says you're being throttled** and by how much.
- **Build on**: per-core/GPU current frequency (sampler/models) vs max freq from `soc_profiles.py`; correlate with die temps from `smc.py`; surface via the existing alert/status path in `tui/widgets.py`.
- **Effort**: S–M.
- **Acceptance**: under sustained load that induces throttling, the indicator reflects the frequency cap; clears when thermals recover.

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
**throttle state**, **DVFS residency**.

---

# References (prior art)

- **[plasma-umass/scalene](https://github.com/plasma-umass/scalene)** (13.5k★) — Python CPU+GPU+memory+**energy** profiler; bar for per-process energy reporting (feature #1, #5).
- **macOS "Energy Impact"** (Activity Monitor) + `task_power_info` / `proc_pid_rusage` — the per-process energy precedent and the API to read it (#1).
- **[jetperch/pyjoulescope_ui](https://github.com/jetperch/pyjoulescope_ui)** (106★) — power/energy UX, marker stats; reference for presenting watts/energy (#1, #2).
- **Peers / coverage benchmarks**: [Atoptool/atop], [aristocratos/btop], [Syllo/nvtop] (now Apple-aware), macmon, mactop, silitop — for parity scope (Tier 3) and positioning.
- **[google/perfetto](https://github.com/google/perfetto)** + **NVTX / torch.profiler** — marks/annotations model, *if* the optional `Profiler.mark()` workload-correlation is pursued.

# Suggested overall order
1. ~~Rename to `actop`~~ ✅ shipped as `v1.0.0` (record in [`DESIGN-system.md` §1.1](DESIGN-system.md)) — cleared install friction + set the brand.
2. Ship **Tier 1** (#1–#3) as the launch story ("the `*top` that shows what others don't"). #1 shipped in v1.0.2 ([`DESIGN-system.md` §5.7](DESIGN-system.md)).
3. Then **Tier 2** (#4–#5); **Tier 3** only as parity demand arises.
