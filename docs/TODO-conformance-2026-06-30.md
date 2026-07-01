# TODO — conformance audit (actop) · 2026-06-30

Scope: `actop/` (whole package) · Import edges scanned: 44 · Prior report folded: none

Whole-tree audit against the 12 rules in `.claude/skills/audit-conformance/SKILL.md`.
Mechanical passes (R4/R5/R6/R8/R9/R10-deps/R12) run by the orchestrator; judgment rules
(R1/R2/R3/R7/R10-dead/R11) fanned out to two read-only subagents. Every DELETE candidate
(R1) was blind-re-read against source — **one flipped** (`avg_window`, read via `getattr`,
dropped).

## Inventory (read-confirmed)

| rule | file:line | what | fix |
|------|-----------|------|-----|
| R1 | actop/config.py:14 | `DashboardConfig.usage_track_window` — written at config.py:49/72, no reader. **Wiring gap, not dead:** its obvious consumer, the history deques at `widgets.py:288`, hardcodes `maxlen = 500` instead of reading it. | **decide:** wire up (`maxlen=cfg.usage_track_window` — behavior change) or delete as "hardcoded 500 is intended" |
| R1 | actop/config.py:15 | `DashboardConfig.core_history_window` — written at config.py:50/73, no reader. **Wiring gap:** per-core deques at `widgets.py:665` use the hardcoded `self._CORE_HIST_MAXLEN` instead. | **decide:** wire up or delete (same as above) |
| R1 | actop/config.py:23 | `DashboardConfig.max_media_bw` — written at config.py:58/80, zero production readers (bandwidth uses `max_cpu_bw + max_gpu_bw`, see widgets.py:478) | delete field + computation + test refs |
| R1 | actop/config.py:42 | `DashboardConfig.proc_filter_raw` — written at config.py:94, zero production readers (filtering uses the compiled `process_filter_pattern`) | delete field + test refs |
| R1 | actop/api.py:68 | `Monitor.backend_name` — assigned in `__init__`, never read anywhere; value is always `'ioreport'` (sampler.py:320). *Public attr on a public class → nuance below.* | remove, or document + keep as intentional introspection surface |
| R6 | actop/ioreport.py:13,55 | module-scope `LoadLibrary` (CoreFoundation, libIOReport) with **no `_DARWIN` guard** — importing this module on non-Darwin raises `OSError` at import time | wrap loads in a platform guard, mirroring `native_sys.py:24` |
| R6 | actop/smc.py:16,59 | module-scope `LoadLibrary` (IOKit, libSystem) with no `_DARWIN` guard — same import-time break off-Darwin | same platform guard |
| R12 | actop/native_sys.py:496 | RAM read failure returns a **fabricated** `VirtualMemory(16GB total, 8GB avail)` — a plausible-but-wrong value the UI renders as real, not a visible "unavailable" sentinel | return a sentinel (e.g. `total=0`) the UI can render as `–`, matching the gpu-cores `"?"` pattern |
| R12 | actop/api.py:145 | alert callback exception silently `pass`ed — a broken user callback vanishes with no signal on the public `register_alert` path | re-raise, or surface once (the callback is user-supplied public API) |
| R2 | actop/sampler.py:41-42, 83-84, 91-92 | `_prev_sample` + `_prev_time` are one concept (the prior delta point) always written together; the reset at :312 nulls only `_prev_sample`, so they can even drift | collapse into one `_prev = (sample, time)` tuple/NamedTuple |
| R2 | actop/tui/app.py:253-254, 379-380, 392-393 | `_filter_regex_before_edit` + `_filter_text_before_edit` are one before-edit snapshot, always saved/restored together | collapse into one small state object |
| R11 | actop/tui/widgets.py:478-482 ↔ 717-722 | identical 5-line bandwidth-percent block (`total_bw_ref = max(cfg.max_cpu_bw + cfg.max_gpu_bw, 1.0)` → `clamp_percent(...)`) computed in both `update_metrics` and `_compute_alerts` | extract `_bandwidth_percent(s, cfg)` and call from both |
| R11 | actop/native_sys.py:26,62 ↔ smc.py:59,16 | `libSystem.B.dylib` and `IOKit` loaded independently in two modules (documented OS-cached, so cost is nil) | low value; only worth it if folded into the R6 guard fix |

**Dependency hygiene (R10-adjacent, low):** `rich` is imported at module scope
(`tui/widgets.py:6`) but declared only transitively via `textual` — not a direct dep in
`pyproject.toml`. Works today (textual always ships rich); brittle if textual ever vendors
it. Consider adding `rich` to `[project.dependencies]`.

### Cleared (checked, no violation)

- **R4 wrong module home / back-edge:** none. All MODULE-scope edges respect the layer
  order (native infra → sampler → api/models → tui; utils/soc_profiles/power_scaling/
  config/export foundational). The `export→api` and `actop→tui`/`export` edges are
  function-local (composition roots), correct direction.
- **R5 underscore leak:** none. The two PRIVATE-tagged edges are both `__version__` — a
  public dunder, not a `_private` leak (edge-map tag artifact).
- **R6 native_sys.py:** guarded by `if _DARWIN` and documents import safety — **not** a
  violation (it is the pattern smc/ioreport should copy).
- **R3, R7, R8, R9, R10 (dead code):** none found.
- **R12 (the other 7 handlers):** thermal→`"Unknown"`, cmdline→`""`, processes→`[]`,
  gpu-cores→`"?"`, and the three TUI layout/width guards all degrade to a **visible**
  sentinel — the acceptable best-effort pattern, not swallowed errors. `native_sys.py:521`
  swap→`(0,0,0)` and `actop.py:213` top-level `print(e)` are borderline-low (0-swap is a
  normal state; the CLI handler is visible but drops the traceback).

## This round — DONE ✅ (branch `chore/prune-dashboardconfig`)

The standout **recurrence class**: `DashboardConfig` accreted five write-only members,
resolved in two kinds:

**(a) Clean deletes — no consumer, behavior-preserving:**

- [x] **R1 config.py** — deleted `max_media_bw` (field + computation + ctor arg). Bandwidth
  uses `max_cpu_bw + max_gpu_bw` (widgets.py:478).
- [x] **R1 config.py** — deleted `proc_filter_raw` (field + ctor arg). Filtering uses the
  compiled `process_filter_pattern`.
- [x] **R1 api.py:68** — removed `Monitor.backend_name` (now `self._sampler, _ =
  create_sampler(...)`; `create_sampler` still returns the name, just unused).
- [x] **Tests** — dropped the kwargs from `tests/test_dashboard_metrics.py` and the
  assertions in `tests/test_config.py`. All 90 tests green.

**(b) Resolved by DELETE + named constant** (per the "500 is a width cap, not a time
window" analysis — the config fields measured the wrong quantity):

- [x] Deleted `usage_track_window` + `core_history_window` (fields, computation, ctor args).
- [x] `widgets.py` — replaced the bare `maxlen = 500` with a documented class constant
  `_CHART_HIST_MAXLEN = 500` (comment: "≥ widest chart width; a space cap, not a time
  window — independent of --avg"), and pointed `_CORE_HIST_MAXLEN` at it (killed the
  duplicate magic 500). One reconfigurable var now governs history depth.
- done: `.venv/bin/pytest -q` green (90). **No behavior change** — 500 retained.

## Deferred backlog

- **R6 import safety (×4, medium):** guard the module-scope `LoadLibrary` in `smc.py` and
  `ioreport.py` behind `if sys.platform == "darwin":`, mirroring `native_sys.py`. Folds in
  the R11 dylib-load duplication. actop is macOS-only (pyproject classifier), so this is
  robustness/consistency, not a live bug — hence deferred.
- **R12 fake RAM fallback (×1, correctness):** `native_sys.py:496` — return a sentinel
  instead of fabricated 16/8 GB. Low likelihood (native read rarely fails on Apple Silicon)
  but it is the one handler that lies rather than degrades visibly.
- **R12 alert-callback swallow (×1):** `api.py:145` — surface broken user callbacks.
- **R2 co-mutated state (×2):** sampler `_prev_*`, app `_filter_*_before_edit`.
- **R11 bandwidth-percent duplication (×1):** extract `_bandwidth_percent(s, cfg)`.
- **Dep hygiene:** declare `rich` directly in `pyproject.toml`.
