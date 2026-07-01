# CLAUDE.md

This file is the single source of truth for repository guidelines, used by Claude Code and all AI coding agents.

## Project Overview

**actop** — Python-based performance monitoring CLI for Apple Silicon Macs (M1/M2/M3/M4 chips). An independent, original project inspired by `tlkh/asitop` (built to fill its whole-chip/sudoless/programmable gaps; not a code fork). Uses IOReport (in-process) for Apple Silicon power/frequency/residency metrics. Combines these with `psutil`, `sysctl`, and `system_profiler` into a real-time terminal dashboard showing CPU/GPU/ANE utilization, per-core frequency, memory/bandwidth, thermal state, and process info.

## Python Environment (Required)
- Always use the repository virtual environment at `.venv`.
- Prefer explicit executables over shell-global tools:
  - `.venv/bin/python`
  - `.venv/bin/pip`
  - `.venv/bin/pytest`
- Do not run `python`, `pip`, or `pytest` from the global environment for this repo.

## Build, Test, and Development Commands
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
| `actop/utils.py` | System queries: `psutil` RAM/swap metrics, `sysctl`/`system_profiler` SoC info, `psutil` process collection |
| `actop/soc_profiles.py` | 16 built-in M1–M4 SoC profiles with power/bandwidth reference values; tier fallbacks for unknown chips |
| `actop/power_scaling.py` | Power chart scaling: `auto` mode (rolling peak x1.25) vs `profile` mode (SoC reference wattage) |
| `actop/config.py` | `DashboardConfig` frozen dataclass; `create_dashboard_config()` merges CLI args with SoC info |
| `actop/models.py` | `SystemSnapshot` and `CoreSample` dataclasses (public API types) |
| `actop/api.py` | `Monitor`, `Profiler`, `AsyncMonitor` — public Python API for hardware profiling |
| `actop/tui/app.py` | `ActopApp`: Textual `App` with polling worker, process table, interactive sort/filter/pause |
| `actop/tui/widgets.py` | `HardwareDashboard` widget with braille `Sparkline` charts, core rows, and alert computation |
| `actop/tui/styles.tcss` | Textual CSS layout for the dashboard |

**Data flow**: `ioreport.py` provides ctypes bindings for IOReport snapshots and deltas → `sampler.py` subscribes to Energy Model / CPU Stats / GPU Stats channels, computes deltas between consecutive snapshots, reads SMC die temperatures via `smc.py`, and converts raw items into a `SampleResult` (power in watts, frequency in MHz, activity in percent, temperatures in °C) → `api.py` wraps the sampler in `Monitor` and maps `SampleResult` to `SystemSnapshot` (including `CoreSample` lists for per-core data) → `tui/widgets.py` feeds `SystemSnapshot` into Textual `Sparkline` charts and `tui/app.py` drives the render loop.

**SoC compatibility**: Explicit M1–M4 recognition (16 profiles). Unknown future chips fall back to tier defaults (base/Pro/Max/Ultra) using the latest known generation's reference values.

## Release Process

`main` is **PR-only** (branch protection + `.githooks/pre-commit` redaction check and `.githooks/pre-push` guard; run `git config core.hooksPath .githooks` once). Bump the version + CHANGELOG via a PR, merge, then tag with `scripts/tag_release.sh [version]`. The Homebrew formula lives in the separate tap repo `binlecode/homebrew-actop` (not this repo); CI syncs it on tag and publishes to PyPI via OIDC. See `docs/DESIGN-cicd-release.md` for the full runbook.

## SDLC & Architectural Documentation

The `docs/` directory contains essential system reviews, research, operations guides, and task lists:
- `docs/REVIEW-architecture-comparison.md`: Performance and architectural comparison between `actop` (Python) and `mactop` (Go).
- `docs/REVIEW-tui-frameworks.md`: Analysis of modern Python TUI frameworks and selection of Textual.
- `docs/TODO-native-polling.md`: Detailed implementation plan for migrating to native macOS APIs (replacing `psutil`).
- `docs/DESIGN-cicd-release.md`: CI/CD bottling and tap release operational runbook.

## Coding Style & Naming Conventions
- Follow existing Python style: 4-space indentation, snake_case for functions/variables, short focused modules.
- Never use `from x import *`; always import explicitly.
- Keep parser keys and metric field names consistent with existing patterns (for example, `P-Cluster_active`, `gpu_W`).
- Prefer small, incremental changes in existing files over large refactors.
- No formatter/linter config is checked in; match surrounding code style when editing.

## Testing Guidelines
- All tests are located in the `tests/` folder.
- Run `.venv/bin/pytest -q` for all code changes.
- Run a single test file: `.venv/bin/pytest tests/test_cli_contract.py -q`
- Run a single test function: `.venv/bin/pytest tests/test_cli_contract.py::test_name -q`
- **Functional tests only (enforced).** Every test MUST validate behavior through a public or runtime entrypoint, and MUST exercise a production-relevant failure mode, regression risk, or external contract. This rule binds to *all* tests, not just new ones: when touching the suite, delete or rewrite any existing test that violates it rather than leaving it in place.
- **Reject a test (it is structural — do not add it; remove it if present) when it:**
  - calls an underscore-prefixed (private) function or method as the unit under test (e.g. `_avg_max`, `_inline_spark`, `_format_core_entry`);
  - reads or writes private attributes to arrange or assert state (e.g. `dash._sample_count = 5`, `dash._core_hist`, `dash._chart_glyph`);
  - asserts internal implementation details, helper math constants, or a private function's output in isolation;
  - uses a mock, fake, monkeypatch, or a synthetic subclass/harness that overrides real behavior to fake layout, I/O, or data;
  - exists only to raise coverage.
- **Accept a test (it is functional) when it drives a public surface:** CLI invocation (`subprocess` / `build_parser().parse_args`), the public API (`Monitor` / `Profiler`), the real config merge (`create_dashboard_config`), documented public module functions (e.g. `power_to_percent`, `get_soc_profile`), real export/format contracts (NDJSON / Prometheus), real hardware/file I/O paths, or a real widget rendered through its public path (`BrailleChart.render()` via the `data` setter, or a `HardwareDashboard` mounted with Textual `App.run_test()` and fed real `SystemSnapshot`s through `update_metrics`).
- A minimal Textual host `App` used solely to mount a real widget is allowed (it is a mount point, not a fake); faking the data or the logic under test is not.
- Minimum checks before opening a PR:
  - `.venv/bin/python -m actop.actop --help`
  - `.venv/bin/pytest -q`
  - Run `actop` on Apple Silicon and confirm gauges/charts update without crashes.
- For parser or metric changes, include a reproducible sample input/output note in the PR description.

## Commit & Pull Request Guidelines
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
