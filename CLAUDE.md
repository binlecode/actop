# CLAUDE.md

This file is the single source of truth for repository guidelines, used by Claude Code and all AI coding agents.

## Project Overview

**actop** — Python-based performance monitoring CLI for Apple Silicon Macs (M1/M2/M3/M4 chips). An independent, original project inspired by `tlkh/asitop` (built to fill its whole-chip/sudoless/programmable gaps; not a code fork). Uses IOReport (in-process) for Apple Silicon power/frequency/residency metrics. Combines these with native `ctypes` syscalls (`native_sys.py`, replacing `psutil`), `sysctl`, and `system_profiler` into a real-time terminal dashboard showing CPU/GPU/ANE utilization, per-core frequency, memory/bandwidth, thermal state, and process info.

## Python Environment (Required)
- Always use the repository virtual environment at `.venv`.
- Prefer explicit executables over shell-global tools:
  - `.venv/bin/python`
  - `.venv/bin/pip`
  - `.venv/bin/pytest`
- Do not run `python`, `pip`, or `pytest` from the global environment for this repo.

## Build, Test, and Development Commands
- `git config core.hooksPath .githooks`: **run once per clone** to activate the local hooks (`pre-commit` secret redaction + `pre-push` `main` guard). Fresh clones have no hooks until this is set.
- `.venv/bin/python -m pip install -e ".[dev]"`: install editable + dev deps (ruff).
- `.venv/bin/python -m actop.actop --help`: validate CLI parsing and flags.
- `.venv/bin/python -m actop.actop --interval 2 --avg 30`: run the tool locally.
- `.venv/bin/python -m build`: build source/wheel artifacts.
- `.venv/bin/pytest -q`: run automated tests.

## Architecture & Core Components

| Module | Role |
|--------|------|
| `actop/actop.py` | CLI entry point (`cli()`), argument parsing, thin wrapper launching the Textual TUI |
| `actop/sampler.py` | `IOReportSampler`: subscription lifecycle, delta computation, `SampleResult` conversion, DVFS table discovery via native ctypes |
| `actop/ioreport.py` | ctypes bindings to `libIOReport.dylib` and CoreFoundation (`IOReportSubscription`, `cfstr`, `cf_release`) |
| `actop/smc.py` | SMC temperature reader: IOKit ctypes bindings to `AppleSMC`, key discovery, CPU/GPU die temperature reads |
| `actop/gpu_registry.py` | GPU accelerator registry reads via IOKit ctypes bindings: `get_gpu_time_by_pid()` sums `accumulatedGPUTime` off each `AGXDeviceUserClient`; `get_gpu_perf_stats()` reads device-level Device/Renderer/Tiler utilization % off the accelerator's own `PerformanceStatistics` dict (driver point reads — a fallback for `gpu_util_pct` plus the compute-vs-geometry breakdown IOReport cannot express; see `docs/DESIGN-system.md` §3.8) |
| `actop/utils.py` | L1 acquisition + platform discovery: native `ctypes` RAM/swap metrics and per-process CPU/GPU time collection (via `native_sys.py`), `sysctl`/`system_profiler` SoC info. Byte quantities cross layer seams as **raw bytes** (exact, prefix-free, Prometheus base-unit convention); GiB/MiB formatting is presentation and lives in `tui/`, and bandwidth stays decimal `GB/s` because that is Apple's unit for the bus — see `docs/DESIGN-system.md` §2.1. The `*_GB` keys and `rss_mb` are deprecated rounded views removed in 2.0.0. Holds no derived domain math |
| `actop/analytics.py` | L2 domain analytics over acquired data points (imports only `models`/`power_scaling`, never `tui/*`): per-process power attribution (`attribute_power`), throttle detection (`domain_throttling`), bandwidth/package-power normalization, and the stateful `AlertEngine` (sustain-counted alerts + session-energy integral → `AlertFrame`) |
| `actop/soc_profiles.py` | 16 built-in M1–M4 SoC profiles with power/bandwidth reference values; tier fallbacks for unknown chips |
| `actop/power_scaling.py` | Power chart scaling: `auto` mode (rolling peak x1.25) vs `profile` mode (SoC reference wattage) |
| `actop/config.py` | `DashboardConfig` frozen dataclass; `create_dashboard_config()` merges CLI args with SoC info |
| `actop/models.py` | `SystemSnapshot`, `CoreSample`, `ProcessSample` dataclasses (public API types) |
| `actop/api.py` | `Monitor`, `Profiler`, `AsyncMonitor` — public Python API for hardware profiling; `Monitor(include_processes=True)` opts into watt-attributed `ProcessSample`s |
| `actop/tui/app.py` | `ActopApp`: Textual `App` with polling worker, process table, app-level status bar (fed by `AlertsComputed`), interactive sort/filter/pause; `l` cycles the layout preset, `c` toggles the per-core panels, and `--layout`/`process-table` width live at app level |
| `actop/tui/widgets.py` | `HardwareDashboard` widget plus the custom `BrailleChart` sparkline widget, core rows, and alert computation. Composes five titled sections (`P-CPU`, `E-CPU`, `GPU · ANE`, `Memory`, `Power`) and owns the `grid`/`stack` layout presets (`set_layout_preset`, width auto-degrade below ~96 cols) plus the runtime per-core toggle (`set_show_cores`, hidden by default); posts the status string up via `AlertsComputed`. TUI CSS is embedded inline as each component's `DEFAULT_CSS` (no separate `.tcss` file). |

**Data flow**: `ioreport.py` provides ctypes bindings for IOReport snapshots and deltas → `sampler.py` subscribes to Energy Model / CPU Stats / GPU Stats channels, computes deltas between consecutive snapshots, reads SMC die temperatures via `smc.py`, and converts raw items into a `SampleResult` (power in watts, frequency in MHz, activity in percent, temperatures in °C) → `api.py` wraps the sampler in `Monitor` and maps `SampleResult` to `SystemSnapshot` (including `CoreSample` lists; the driver's `gpu_registry.get_gpu_perf_stats()` Device/Renderer/Tiler read, which also selects `gpu_util_source` — residency stays primary unless the GPU DVFS table went unclassified; and, when `include_processes=True`, watt-attributed `ProcessSample` lists computed in L2 via `analytics.attribute_power`) → `SystemSnapshot` is the **sole per-frame contract**: `tui/widgets.py` feeds it into custom `BrailleChart` sparkline widgets (arranged in five titled sections — `P-CPU`, `E-CPU`, `GPU · ANE`, `Memory`, `Power` — under a `grid` or `stack` layout preset) and posts the composed thermal/alert status up to `tui/app.py` via an `AlertsComputed` message for the fixed app-level status bar; `tui/app.py` reads processes off the snapshot and drives the render loop (the TUI imports no L1 acquisition/attribution).

**SoC compatibility**: Explicit M1–M4 recognition (16 profiles). Unknown future chips fall back to tier defaults (base/Pro/Max/Ultra) using the latest known generation's reference values.

## Release Process

`main` is **PR-only** (branch protection + `.githooks/pre-commit` redaction check and `.githooks/pre-push` guard; run `git config core.hooksPath .githooks` once). Bump the version + CHANGELOG via a PR, merge, then tag with `scripts/tag_release.sh [version]`. The Homebrew formula lives in the separate tap repo `binlecode/homebrew-actop` (not this repo); CI syncs it on tag and publishes to PyPI via OIDC. See `docs/DESIGN-sdlc-cicd.md` for the full runbook.

## SDLC & Architectural Documentation

The `docs/` directory contains essential system reviews, research, and operations guides:
- `docs/DESIGN-system.md`: Detailed system design reference — native bindings, sampling layer, SoC profile fallback, TUI rendering, testing contract. Kept in sync with the code on every PR that touches architecture.
- `docs/REVIEW-architecture-comparison.md`: Performance and architectural comparison between `actop` (Python) and `mactop` (Go).
- `docs/REVIEW-tui-frameworks.md`: Analysis of modern Python TUI frameworks and selection of Textual.
- `docs/DESIGN-sdlc-cicd.md`: CI/CD bottling and tap release operational runbook.
- `docs/TODO-architecture-roadmap.md`: Open hardware/metric-coverage gaps and their priority.

**Conformance auditing:** the `/audit-conformance` skill (`.claude/skills/audit-conformance/`) periodically judgment-scans the whole tree against 12 coding rules (layering, dead code, DRY, naming, swallowed errors) — the whole-codebase counterpart to diff-scoped `/code-review`. It writes an actionable `docs/TODO-conformance-YYYY-MM-DD.md` and never proposes structural/guard tests (they would violate the functional-tests-only mandate).

## Coding Style & Naming Conventions
- Follow existing Python style: 4-space indentation, snake_case for functions/variables, short focused modules.
- Never use `from x import *`; always import explicitly.
- Keep parser keys and metric field names consistent with existing patterns (for example, `P-Cluster_active`, `gpu_W`).
- Prefer small, incremental changes in existing files over large refactors.
- No formatter/linter config is checked in; match surrounding code style when editing.

## Testing Guidelines (HARD RULE — enforced at review)

- All tests are located in the `tests/` folder.
- Run `.venv/bin/pytest -q` for all code changes.
- Run a single test file: `.venv/bin/pytest tests/test_cli_contract.py -q`
- Run a single test function: `.venv/bin/pytest tests/test_cli_contract.py::test_name -q`

### The functional-only mandate

**Every test MUST answer "what production failure mode or regression risk does this
test catch that no other test catches?"** If you cannot give a concrete answer
(broken export format, wrong flag forwarding, missing process data, etc.), the test
is structural — do not add it; delete it if present. This rule binds to *all*
tests, not just new ones.

### Reject a test (it is structural — do not add it; remove it if present) when it:

- calls an underscore-prefixed (private) function or method as the unit under test
  (e.g. `_avg_max`, `_inline_spark`, `_format_core_entry`);
- reads or writes private attributes to arrange or assert state (e.g.
  `dash._sample_count = 5`, `dash._core_hist`, `dash._chart_glyph`);
- asserts internal implementation details, helper math constants, or a private
  function's output in isolation;
- uses a mock, fake, monkeypatch, or a synthetic subclass/harness that overrides
  real behavior to fake layout, I/O, or data;
- exists only to raise coverage;
- **is a standalone default-value assertion** (e.g.
  `parse_args([]).some_flag is False`) — what would break if this test were
  deleted? A behavioral test that uses a different value or exercises the same
  code path already guards that regression. Default-value checks are redundant,
  not load-bearing;
- **is a standalone `parse_args` "this flag accepts value X" test** when a
  behavioral test already drives that flag with that value — e.g.
  `parse_args(["--flag"]).flag is True` is redundant when
  `parse_args(["--json", "--flag"]).flag is True` exists and catches both
  the parsing and the forwarding;
- asserts only shape/bounds on real public data (e.g. "returns a non-negative
  dict", "values partition to ≤ 1.0") with no real workload behind it, when a
  behavioral test already exercises the same code path — the bounds/invariant
  becomes an assertion *inside* that behavioral test, not a standalone test of
  its own;
- **runs an identical subprocess call as another test** just to assert different
  substrings from the same stdout — consolidate the assertions into one test
  rather than paying the fork cost twice.

### Accept a test (it is functional) when it drives a public surface:

CLI invocation (`subprocess` / `build_parser().parse_args`), the public API
(`Monitor` / `Profiler`), the real config merge (`create_dashboard_config`),
documented public module functions (e.g. `power_to_percent`, `get_soc_profile`),
real export/format contracts (NDJSON / Prometheus), real hardware/file I/O paths,
or a real widget rendered through its public path (`BrailleChart.render()` via the
`data` setter, or a `HardwareDashboard` mounted with Textual `App.run_test()` and
fed real `SystemSnapshot`s through `update_metrics`).

A minimal Textual host `App` used solely to mount a real widget is allowed (it is
a mount point, not a fake); faking the data or the logic under test is not.

### Before writing a test, apply this litmus:

1. **Can I name the production failure this test will catch?** If the answer is
   "it protects against argparse breaking" for a default-value assertion, or
   "it catches a regression in the flag's boolean default," that failure mode is
   already covered by every behavioral test that parses the flag with a non-default
   value — skip it.
2. **Does another test already drive the same code path?** Two tests that both
   call `parse_args(["---flag"])` and both assert the flag is `True` are one
   too many. Keep the one that also asserts the flag's downstream effect.
3. **Is the assertion independently load-bearing?** If you can delete the
   assertion and no behavioral test would fail, it is cosmetic — inline it into
   a behavioral test or drop it.

### Minimum checks before opening a PR:

- `.venv/bin/python -m actop.actop --help`
- `.venv/bin/pytest -q`
- Run `actop` on Apple Silicon and confirm gauges/charts update without crashes.
- For parser or metric changes, include a reproducible sample input/output note
  in the PR description.

## Commit & Pull Request Guidelines
- **Branch from `main`; PR strictly into `main`.** Every branch forks from `main` and targets `main`. **Never fork a feature branch off another feature branch** (no stacked PRs): if you need work that is still on an unmerged branch, wait for it to merge and re-branch from `main`. This holds especially for CI/CD and release changes — they land via a single PR into `main`, never a chained branch.
- **Bump the version in every PR.** Each PR updates `pyproject.toml` version + moves `CHANGELOG.md` `[Unreleased]` into a new dated section, in the same PR: **patch bump by default, minor only for a milestone PR** (major reserved for breaking API/CLI changes). Tagging (`scripts/tag_release.sh`) is a separate step after merge and is what publishes — see `docs/DESIGN-sdlc-cicd.md`.
- Use concise, imperative commit subjects (as seen in history), e.g. `Add support for M1 Ultra` or `actop/utils.py: add bandwidth of M2`.
- Keep commits scoped to one logical change.
- Before every commit and before every push, always run:
  - `.venv/bin/ruff check --fix .`
  - `.venv/bin/ruff format .`
- PRs should include:
  - clear summary of behavior change,
  - tested macOS/chip details (for example, Ventura + M2),
  - commands used for validation,
  - screenshot or terminal capture for UI-visible changes.

## Security & Configuration Tips
- The IOReport backend runs unprivileged.
- Avoid introducing persistent privileged processes or unsafe temporary-file handling.
- Avoid introducing persistent privileged processes or shell commands that invoke `sudo`.
