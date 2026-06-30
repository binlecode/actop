# Changelog

All notable changes to `binlecode/agtop` should be documented in this file.

This project follows a Keep a Changelog-style format and uses version tags for releases.

## [Unreleased]

## [0.9.7] - 2026-06-30

### Fixed
- **`/` filter no longer a dead control when the process table is hidden:** the regex filter only applies to the process table, but in `t`-off mode (the default) `/` still opened an input box whose pattern was never read — the polling loop skips process collection when the table is hidden. The `/  Filter` binding is now hidden from the footer and inert while the table is off, and reappears when `t` shows the table. (Filtering is reachable only with the table visible; `t` cannot be pressed mid-filter since the focused input captures it as text.)

## [0.9.6] - 2026-06-30

### Added
- **Esc cancels the process filter:** pressing `/` opens a live regex filter that previously could only be closed with `Enter` (committing the typed pattern). `Esc` now cancels the in-progress edit — discarding the typed text, reverting the live-applied filter to the value active before the field was opened, and restoring focus — matching the htop/vim/fzf cancel convention. `Enter` still commits as before.

## [0.9.5] - 2026-06-29

### Added
- **Persistent core-topology header:** the Textual header sub-title now always shows the SoC core layout (e.g. `Apple M4 Max · 4E+12P+40GPU`), not just on the init splash — restoring the at-a-glance topology (including GPU core count) that the pre-Textual layout had. The `+NGPU` segment is omitted when the GPU core count is unavailable (unknown/future SoCs).

## [0.9.4] - 2026-06-29

### Added
- **Live memory bandwidth:** total DRAM bandwidth is now sampled in-process and unprivileged from the IOReport `PMP/DCS BW` group, so the `Mem BW N GB/s` row is live instead of always hidden. The value is a residency-weighted average over the `AMCC` bandwidth-bucket histogram (summed across memory-controller dies), exposed via `SystemSnapshot.bandwidth_gbps`. Held within the idle-CPU budget by a per-state extraction filter in the IOReport delta path (extracts only the channels actually parsed).
- **uv install option:** `uv tool install` documented for non-Homebrew users — a sandboxed per-tool environment with its own managed CPython, no system Python required.

### Changed
- **Pinned kernel struct offsets:** the `proc_taskallinfo` byte offsets in `native_sys.py` are now named module constants with the struct layout documented in one place, and the native-process guard tests are hardened against silent offset drift on new macOS releases.

## [0.9.3] - 2026-06-29

### Added
- **Memory-bandwidth chart + readout:** the unified-memory bandwidth sampled in `SystemSnapshot.bandwidth_gbps` (previously consumed only by the `BW>` alert) now has its own `Mem BW N GB/s` label and chart with rolling `avg/max` context — the headline saturation metric for LLM inference. The row hides itself on platforms that expose no bandwidth channel (`bandwidth_available` false), so no phantom `0 GB/s` is shown.
- **Package-power headline:** a `Package Power` label + chart for the total-SoC draw (CPU + GPU + ANE + other rails), alongside the existing CPU/GPU power charts. The figure already drove the `PKG>` alert but was never surfaced.
- **Session energy total:** the status line now carries an `energy` token — cumulative session energy integrated as ∫ package power dt since launch (rendered in mWh/Wh) — the live-TUI counterpart to `Profiler.total_package_joules`.

### Changed
- Test suite is now functional-only (enforced in `CLAUDE.md`): removed structural tests that asserted private helpers/internal state in isolation (`test_dashboard_stats.py`, two private-function tests in `test_braille_chart_render.py`); added `test_dashboard_metrics.py`, which mounts the real `HardwareDashboard` via Textual `App.run_test()` and drives the public `update_metrics` path with real `SystemSnapshot`s.

## [0.9.2] - 2026-06-29

### Added
- **Color tier degradation + `NO_COLOR`:** chart colors no longer always emit truecolor `rgb()`. `resolve_color_mode()` honors `NO_COLOR` (https://no-color.org) unconditionally, then prefers the terminal's detected color system, falling back to `COLORTERM`/`TERM`. The blue→red gradient degrades to a 256-color cube index, a named 16-color severity ramp, or no style at all on dumb terminals — fixing broken output on limited/`NO_COLOR` terminals.
- **Chart time-window label:** charts plot one sample per column, so the visible span scaled silently with terminal width. The status line now leads with a `span <Ns/m/h>` token (chart width × `--interval`), documented in the `?` help overlay.

### Changed
- Consolidated all TUI design and implementation details into `docs/DESIGN-system.md` (Section 5) and removed the completed `docs/TODO-tui-modernization.md`. Section 5 was also brought current (removed stale `v`/`space` keys, added `?` help, color tiers, and headless export modes).

## [0.9.1] - 2026-06-29

### Changed
- The cur/avg/max chart context now appends the unit to each stat (`avg 31% · max 88%`, `avg 9.1W · max 18.7W`). A bare number was ambiguous next to a headline carrying a different unit — most notably the RAM row, whose headline is in GB while its avg/max are percent.

## [0.9.0] - 2026-06-28

### Added
- **Chart context (cur/avg/max):** every metric label now shows a rolling average (over the `--avg` window) and the session peak alongside the live reading — e.g. `GPU 47% @1296MHz  avg 31 · max 88`. Percent metrics report `avg/max` in percent; power labels report watts. Covers the P/E-cluster summary rows and the GPU, ANE, RAM, and CPU/GPU power labels.
- **Help overlay (`?`):** a modal listing keybindings, metric-label meanings, and every status-line alert token (`THERMAL`, `BW>`, `PKG>`, `SWAP+`). Toggle with `?`, dismiss with `esc`/`q`.
- **Metrics export:** new `agtop/export.py` with two non-TUI backends. `--json` streams one NDJSON snapshot per interval to stdout (all `SystemSnapshot` fields, including per-core lists). `--serve PORT` runs a stdlib HTTP server exposing Prometheus gauges at `/metrics` (scalar gauges plus per-core `agtop_core_utilization_percent{cluster,core}`), kept warm by a background sampler so scrapes return immediately.

### Fixed
- Corrected the README interactive-keys reference, which still advertised the removed `v` (layout) and `space` (panel-collapse) bindings.

## [0.8.10] - 2026-06-29

### Changed
- Simplified the status bar: removed the rarely-used layout toggle (`v`) and dashboard-collapse (`space`) bindings and disabled the framework command palette (`^p`), leaving a focused set: `q` `p` `s` `g` `t` `/`.

## [0.8.9] - 2026-06-28

### Fixed
- The opening splash banner and the dashboard header now display the running version (e.g. `agtop v0.8.9`), sourced from a single `agtop.__version__`.

## [0.8.8] - 2026-06-28

### Fixed
- Hardened the native polling layer: removed dead BSD process structs, inlined the DVFS table passthrough, and added a sleep guard in `Monitor.get_snapshot` to avoid a frame with an inflated power scale.

### Changed
- Expanded the functional test suite (37 → 57 tests): added coverage for native process/DVFS parsing, the args→`DashboardConfig` merge, the SoC unknown-chip tier fallbacks, and the power-chart auto/profile scaling modes.

## [0.8.7] - 2026-06-14

### Changed
- Simplified Homebrew tap by removing pre-compiled binary bottling and moving to a pure, high-efficiency native source-build distribution model.

## [0.8.6] - 2026-06-14

### Changed
- Highly optimized native process scanning by introducing a two-tier lazy/on-demand KERN_PROCARGS2 lookup. This reduces process polling latency from 254ms to 21ms (a 91.5% speedup) and decreases peak heap allocations, making agtop incredibly battery-friendly.

## [0.8.5] - 2026-06-14

### Removed
- Removed `psutil` dependency across the entire codebase, making `agtop` 100% zero-dependency for process and memory monitoring.

### Changed
- Migrated RAM/swap calculations to Mach native `host_statistics64` and `sysctlbyname("vm.swapusage")` APIs, correcting over-reporting of memory and matching Activity Monitor precisely.
- Migrated process scanning to native `proc_listpids` and `proc_pidinfo` APIs with custom offset unpacking, reducing process traversal latency from 49ms to 5ms (a 10x speedup) and peak heap memory allocation by 96%.
- Added support for KERN_PROCARGS2 sysctl to parse full process command lines natively on macOS, maintaining full backwards compatibility for argument-level regex process filtering.

## [0.8.2] - 2026-06-14

### Changed
- Reorganized SDLC documentation (architecture reviews, TUI research, and operations guidelines) into a dedicated `docs/` folder.
- Added `tmp/` folder to `.gitignore` to keep scratch and workspace files untracked.

## [0.8.1] - 2026-03-03

### Changed
- Per-core inline spark rendering now uses the same shared chart glyph utility path as `BrailleChart`, so `dots`/`block` mode behavior is consistent and duplicate glyph logic is removed.

## [0.8.0] - 2026-03-03

### Added
- New TUI keybinding `v` toggles the main layout between horizontal (side-by-side) and vertical (stacked) when viewing hardware and processes.

## [0.7.0] - 2026-03-03

### Changed
- `BrailleChart` vertical-line coloring now uses a single color per sample column, derived from the current reading, instead of row-height gradient segments within a column.
- `RAM` chart now uses the same vertical scale (`height: 4`) as `P-CPU` and `E-CPU` charts.
- Charts now support two glyph modes: `dots` (braille) and `block` (square), switchable by CLI (`--chart-glyph`) or at runtime with the `g` key.

## [0.6.0] - 2026-03-03

### Changed
- Process panel is now excluded by default at startup. Top-process sampling is skipped until the panel is enabled.

### Added
- New CLI flag `--show-processes` to enable the process panel at launch.
- New TUI keybinding `t` to toggle the process panel on/off at runtime.

## [0.5.4] - 2026-03-02

### Fixed
- E-core cluster indices were offset by `p_count`, causing E-core metrics to be attributed to non-existent cores on chips with more than 4 P-cores.

### Changed
- `BrailleChart` rewritten: 1 sample per character, filled vertical pole from zero to value, blue→red gradient coloring per row segment. Replaces the old 2-samples-per-char alternating left/right dot design.
- P-CPU and E-CPU charts increased to height 4 (16 levels); other charts remain height 2 (8 levels).
- Layout: hardware dashboard and process table now split side-by-side in a horizontal container.

## [0.5.3] - 2026-03-02

### Fixed
- Correct GPU energy channel matching: use `"GPU Energy" in item.channel` instead of `"GPU" in item.channel` to avoid double-counting the mJ summary and nJ precision channels on M4 (and later) chips.

### Changed
- Power charts (`CPU Power`, `GPU Power`) now use `auto_scale=True` so low idle wattage is visible instead of rounding to zero in the braille bar math.
- Removed dead `clear_console()` function from `utils.py` (never called).
- Deleted stale `agtop/tui/styles.tcss` (superseded by `DEFAULT_CSS` embedded in `AgtopApp` since v0.5.1).

## [0.5.2] - 2026-03-02

### Fixed
- Thermal pressure now reads real macOS state (`NSProcessInfo.thermalState` via ObjC runtime ctypes) instead of always showing "Unknown". Returns Nominal / Fair / Serious / Critical.

### Changed
- Replaced Textual `Sparkline` with a custom `BrailleChart` widget: auto-scales bar count to terminal width (2 samples per character column), 500-sample rolling history.
- Added loading splash screen with chip name, core counts, interval, and a braille spinner while the sampler warms up on the first delta.
- Process table row count now adapts to available table height instead of a fixed limit.
- Core-row layout now adapts column count to widget width; entries separated by `│` with fixed-width formatting.
- Reduced chart height from 3 to 2 terminal rows for a more compact layout.
- Thread count column added to process table.

## [0.5.1] - 2026-03-02

### Fixed
- Embedded TUI CSS as `DEFAULT_CSS` in `AgtopApp` instead of a `CSS_PATH` file reference. `styles.tcss` was not included in the wheel, causing a `StylesheetError` crash on `brew install`.

## [0.5.0] - 2026-03-01

### Changed
- Replaced the `dashing` + `blessed` terminal dashboard with a [Textual](https://textual.textualize.io/) TUI. All charts are now braille `Sparkline` widgets; layout is declarative CSS; resize is clean.
- Per-core activity now sourced from IOReport CPU Core Performance States via `CoreSample` dataclass instead of `psutil.cpu_percent(percpu=True)`.
- Removed `--color` and `--core-view` CLI flags (subsumed by Textual's automatic color support and always-on sparkline history charts).

### Added
- Interactive runtime keys: `/` to open a live regex filter for processes, `s` to cycle sort (CPU% → RSS → PID), `p` to pause/resume polling, `space` to collapse the hardware panel.
- `--version` flag: `agtop --version` now prints the installed package version.
- `agtop/config.py`: extracted `DashboardConfig` frozen dataclass and `create_dashboard_config()` from the deleted `state.py`.
- `agtop/models.py`: `SystemSnapshot` and `CoreSample` dataclasses (public API).
- `agtop/api.py`: `Monitor`, `Profiler`, `AsyncMonitor` — public Python API for hardware profiling (programmatic use without TUI).
- `agtop/tui/`: Textual TUI package (`app.py`, `widgets.py`, `styles.tcss`).

### Removed
- Deleted legacy modules: `agtop/state.py`, `agtop/updaters.py`, `agtop/input.py`, `agtop/color_modes.py`, `agtop/gradient.py`.
- Removed `dashing` dependency from `pyproject.toml`; replaced with `textual>=0.60`.

## [0.4.4] - 2026-03-02

### Fixed
- Fixed GitHub Actions CI failure by correctly marking hardware-dependent E2E tests as macOS local-only.

## [0.4.3] - 2026-03-01

### Changed
- Replaced `test_input.py`, `test_state.py`, `test_updaters.py` with `test_e2e.py` and `test_integration.py` (QA test suite overhaul).
- Removed completed `TODO-agtop-improvements.md` planning document.

## [0.4.2] - 2026-03-01

### Changed
- Raised minimum history buffer size from 20 to 200 points (`usage_track_window`, `core_history_window` in `state.py`). Charts now fill with real data at default `--avg 30 --interval 2` settings instead of repeating the oldest sample across most of the chart width.

## [0.4.1] - 2026-03-01

### Added
- Adaptive widget title truncation: when terminal width is < 100 columns, the four long panel titles (`power_charts`, `cpu_power_chart`, `gpu_power_chart`, `memory_bandwidth_panel`) switch to compact forms that fit without mid-word clipping, while still showing key wattage and bandwidth values.
- Terminal resize awareness: render loop now uses `terminal.notify_on_resize()` and `InteractiveState.resize_pending` to trigger a full-clear redraw on resize; display is skipped when the terminal reports a degenerate size (< 2 cols/rows).

## [0.4.0] - 2026-03-01

### Changed
- Replaced all `os.popen("sysctl ...")`, `subprocess.run(["sysctl" ...])`, `subprocess.run(["ioreg" ...])`, and `system_profiler` shell calls with direct `ctypes` bindings to `libSystem.B.dylib`, `IOKit`, and `CoreFoundation`.
- Added `agtop/native_sys.py`: `get_sysctl_int`, `get_sysctl_string` (via `sysctlbyname`), `get_gpu_cores_native` (via `AGXAccelerator` IORegistry property), and `get_dvfs_tables_native` (via `IORegistryEntryCreateCFProperties` + `CFData` byte extraction, replacing `ioreg` XML/plist pipeline).
- Removed `import subprocess` and `import plistlib` from `sampler.py`; GPU core count and DVFS table reads now complete in microseconds instead of ~250 ms at startup.

## [0.3.2] - 2026-02-18

### Added
- Added runtime keyboard input: `q` to quit, `c`/`m`/`p` to toggle process sort by CPU%, RSS, or PID.
- Added sort indicator (`*`) in process column header and sort label in panel title.
- Added `agtop/input.py` module with `InteractiveState`, `handle_keypress()`, and `sort_processes()`.

## [0.3.1] - 2026-02-17

### Changed
- Extracted `DashboardState` and `DashboardConfig` dataclasses into `agtop/state.py` and metric/widget update functions into `agtop/updaters.py`, slimming `_run_dashboard()` to a focused render loop.
- Added `--subsamples` CLI option for sampler-level smoothing via multi-delta averaging within each interval.
- Added cross-platform tests for state factories, metric updates, and widget binding.

## [0.3.0] - 2026-02-17

### Added
- Added SMC temperature reader (`agtop/smc.py`) for CPU and GPU die temperatures via IOKit ctypes, no sudo required.
- Added CPU and GPU temperature display in gauge titles (e.g. "P-CPU Usage: 12% @ 3504 MHz (58°C)").

### Changed
- Replaced `vm_stat` subprocess with `psutil.virtual_memory()` for RAM metrics, eliminating fork/exec overhead every sample interval.
- Split test suite into CI-safe and macOS-local groups using pytest markers.

## [0.2.0] - 2026-02-17

### Added
- Added `GradientText` widget for per-line gradient coloring in the process panel based on CPU utilization.

### Changed
- Changed default `--interval` from 1s to 2s to reduce sampling overhead (aligned with btop's default).
- Changed default `--show_cores` to on for a full per-core dashboard out of the box (disable with `--no-show_cores`).
- Changed default `--power-scale` from `auto` to `profile` for stable, meaningful power chart percentages from the first frame.
- Replaced `psutil.virtual_memory()` with `os.sysconf` for total RAM and `vm_stat` for used RAM; psutil retained for swap and process metrics.
- Rewrote README with consolidated structure: Key Features, How It Works (OS-level API mechanism), Architecture (Mermaid system diagram), and Signal Sources.
- Revised release operations guide as combined tutorial and runbook.

### Fixed
- Fixed RAM usage to match Activity Monitor by using macOS `vm_stat` page counts (`internal - purgeable + wired + compressor`) instead of psutil's `total - available` which over-reports usage.
- Fixed swap percent calculation to use raw byte values instead of pre-rounded GB values.

## [0.1.10] - 2026-02-16

Initial IOReport-only release. All prior versions used a legacy backend and are not documented here.

---

## Release Notes Process

For each new release:
1. Move completed items from `Unreleased` into a new version section.
2. Add release date in `YYYY-MM-DD` format.
3. Keep entries concise and user-impact focused.
4. Tag and publish release after changelog update.
