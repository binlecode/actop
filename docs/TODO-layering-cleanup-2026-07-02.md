# TODO: Layering Cleanup — TUI ← Data Points ← Data APIs

Status: planned · Owner: TBD · Created: 2026-07-02
Scope: `models.py`, `api.py`, `utils.py`, `soc_profiles.py`, `config.py`, new
`analytics.py`, `tui/*`; export extensions are a follow-up. No native-binding
(`ioreport.py`/`smc.py`/`gpu_registry.py`/`native_sys.py`) changes.

---

## 1. The intended layering

actop's architecture factors cleanly into three layers; each violation below
is a place where the implementation crosses a boundary the design implies:

- **L1 — data APIs (acquisition):** `ioreport.py`, `smc.py`,
  `gpu_registry.py`, `native_sys.py`, `sampler.py`, and `utils.py`'s raw
  queries (`sysctl`, `system_profiler`, RAM/process collection).
- **L2 — data points (typed contract + delivery):** `models.py`
  (`SystemSnapshot`, `CoreSample`), `api.py` (`Monitor`/`Profiler`/
  `AsyncMonitor`), `soc_profiles.py`, `power_scaling.py`.
- **L3 — presentation:** `tui/app.py`, `tui/widgets.py`, `export.py`.

**Contract:** L3 consumes *only* L2 types. L2 derives every data point a
consumer needs. L1 is invisible above L2.

**Why it matters concretely:** `export.py` — the other L3 consumer — can only
emit what `SystemSnapshot` carries, which is why exports today have no
processes, no alerts, no throttle flag, no session energy: those data points
are trapped inside the TUI. Symmetrically, every violation below is also the
reason some test must mount a widget instead of asserting against the API.

## 2. Violations inventory (verified against code, 2026-07-02)

| # | Violation | Evidence | Consequence |
|---|---|---|---|
| V1 | TUI imports L1 directly and stitches its own data bundle | `tui/app.py:18-23` imports `get_ram_metrics_dict`, `get_top_processes`, `get_soc_info`, `attribute_power`; worker at `app.py:326-342` ships `MetricsUpdated(snapshot, ram, processes)` — two untyped dicts beside the snapshot (`widgets.py:264-271`); `ActopApp.__init__` re-calls `get_soc_info()` (`app.py:275-283`) | `SystemSnapshot` is not the data contract; three parallel channels into the view |
| V2 | RAM sampled twice per frame via two paths | `api.py:96` (inside `get_snapshot`) **and** `app.py:332`; snapshot carries only `ram_used_gb`/`swap_used_gb` (`models.py:34-35`) while the TUI needs `total_GB`/`used_percent`/`swap_total_GB` from the raw dict (`widgets.py:521,633-636`) | Incomplete model → duplicate acquisition → two sources of truth for one metric in one frame; snapshot RAM fields are dead weight to the TUI |
| V3 | Processes never enter L2 | `get_top_processes` returns `{"cpu": [dict], "memory": [dict]}` with implicit string keys (`cpu_time_share`, `gpu_time_share`, `rss_mb`, `num_threads`) consumed at `app.py:489-520`; no `ProcessSample` model, no `Monitor`/`Profiler` path | TUI shows data a public-API user cannot obtain; dict keys form an unanchored `utils`↔`app` contract |
| V4 | Domain analytics implemented in the view | Throttle detection `_domain_throttling` + gates (`widgets.py:278-294`); alert engine + sustain counters `_compute_alerts` (`widgets.py:825-920`); session energy `_session_joules` (`widgets.py:563-565,922-927`) self-described as "mirroring `Profiler.total_package_joules`"; ANE% derived at render time (`widgets.py:520`); BW/package normalizations (`widgets.py:297-315`) | Hardware judgments (throttling, alerts) invisible to API/export; energy integration duplicated across layers; `ane_util_pct` exists only inside the TUI |
| V5 | Per-process power attribution executes at render time | `sort_processes` (`app.py:41-73`) and `_refresh_process_table` (`app.py:489-533`) call `attribute_power` during table refresh; comment at `app.py:498`: "computed here because the TUI owns cpu_watts/gpu_watts" | The watts↔process join can only happen in the view *because* the inputs arrive on different channels (V1/V3); Σ-reconciliation is more domain math in the same place |
| V6 | `utils.py` is a layer sandwich | L1 acquisition (`get_ram_metrics_dict`, `get_top_processes`), platform discovery (`get_soc_info`), and pure L2 domain math (`attribute_power`, `utils.py:131`) in one module, imported by both `api.py` and `tui/app.py` | Module has no assignable layer; domain math travels with acquisition code |
| V7 | Hardware constant outside the SoC-profile layer | `ane_max_power = 8.0` hardcoded in `config.py:50`, identical across M1→M4 | The ANE data point's denominator is a UI-config constant instead of per-SoC reference data (`soc_profiles.py` is where every other reference wattage lives) |
| V8 | View keeps its own native-unit data store | Watt/GB-s histories `_cpu_w_hist`/`_gpu_w_hist`/`_pkg_w_hist`/`_bw_gbps_hist` (`widgets.py:379-390`) + `_avg_max` (`widgets.py:714-732`) | Data aggregation (rolling watt stats) duplicates what L2 history (`Profiler`, `to_pandas`) exists for; chart-*percent* deques are legitimate presentation and stay |

## 3. Fix design, per violation

### F1 — `SystemSnapshot` becomes the single frame contract (V1, V2)

- Extend `SystemSnapshot` (`models.py`) with `ram_total_gb: float`,
  `ram_used_percent: float`, `swap_total_gb: float` (defaulted `0.0` —
  existing call sites stay valid, matching the `*_max_freq_mhz` precedent at
  `models.py:39-44`).
- `_sample_to_snapshot` (`api.py:12`) maps them from the ram dict it already
  receives (`api.py:59-60` pattern).
- TUI: delete the second `get_ram_metrics_dict()` call (`app.py:332`);
  `widgets.py` reads RAM/swap from the snapshot. Delete the `ram` field
  from `MetricsUpdated`. **Deleting that field forces migrating *all four*
  ram-dict reads in `widgets.py` in this same PR**, not just the RAM label:
  - `514` `ram = message.ram` (the binding itself — remove it);
  - `521` `ram.get("used_percent")` → `s.ram_used_percent`;
  - `633-644` RAM label (`total_GB`/`used_GB`/`swap_used_GB`/`swap_total_GB`)
    → `s.ram_total_gb`/`s.ram_used_gb`/`s.swap_used_gb`/`s.swap_total_gb`;
  - `575` swap-history append (`ram.get("swap_used_GB")`) → `s.swap_used_gb`;
  - `890` `ram.get("swap_total_GB")` inside `_compute_alerts` →
    `s.swap_total_gb` (that function only *moves* in LC-3, but its ram read
    must switch to the snapshot field now).

  Confirmed the mapping is total: `get_ram_metrics_dict` returns exactly
  `total_GB`/`used_percent`/`swap_total_GB`/`swap_used_GB`, so the three new
  fields plus existing `swap_used_gb` cover every read.
- `ActopApp.__init__`: stop re-calling `get_soc_info()`; thread the two
  missing display fields (`name`, `gpu_core_count`) through
  `DashboardConfig` (add `chip_name: str`, `gpu_core_count: int` — computed
  once in `create_dashboard_config`, which already receives `soc_info`).

### F2 — `ProcessSample` + processes through L2 (V3, V5)

- New `ProcessSample` dataclass in `models.py`: `pid`, `command`,
  `cpu_percent`, `cpu_time_share: float | None`,
  `gpu_time_share: float | None`, `rss_mb`, `num_threads`, and
  `attributed_w: float | None` (None ⇔ no CPU delta yet — today's `"–"`
  cell, `app.py:507-508`).
- **Decision (firm): single list.** Snapshot carries `processes:
  list[ProcessSample]` (default empty) sorted by the acquisition default (CPU).
  The current dict's `"memory"` ordering becomes a plain `sorted(...,
  key=lambda p: p.rss_mb, reverse=True)` in the L3 consumer (`sort_processes`);
  the dual-list shape is deleted, not carried into the model.
- Collection is opt-in to keep `Monitor` cheap for API users:
  `Monitor(interval_s, subsamples, include_processes=False,
  process_limit=50, process_filter=None)` sets defaults; `get_snapshot(*,
  process_filter=None)` overrides the filter per call (see the firm decision
  below). When enabled, `get_snapshot()`
  calls `get_top_processes` and computes `attributed_w` **in L2** — it has
  `cpu_watts`/`gpu_watts` and the shares in the same scope, dissolving the
  `app.py:498` comment.
- **Decision (firm): live filter via per-call arg.** `get_snapshot(*,
  process_filter=None)` takes the pattern each call; the worker loop passes
  `self._filter_regex` every tick (preserving the `app.py:443-444` liveness —
  the worker already re-reads that attribute each iteration). No mutable
  `Monitor` attribute, no locking — same guarantees as today. `process_limit`
  stays `50` (matches today's `process_display_count`; the TUI's
  table-height re-limit at `app.py:486` remains a pure display concern, so no
  behavior change).
- `attribute_power` moves out of `utils.py` (see F4); `sort_processes`
  (`app.py:41-73`) loses its watts parameters and becomes pure ordering over
  `ProcessSample.attributed_w` — it stays in the TUI (ordering for display is
  presentation). Σ-shown subtitle becomes `sum(p.attributed_w for visible)`
  — arithmetic over L2-computed values, acceptable in L3.
- `MetricsUpdated` shrinks to `MetricsUpdated(snapshot)` — completing F1.

### F3 — `analytics.py`: alerts, throttling, session energy (V4)

Extend `actop/analytics.py` (L2 — imports `models`, `power_scaling`, never
`tui/*`). **The module is created in LC-2, not here:** F4 moves
`attribute_power` into it, so it already exists with that function by the time
LC-3 starts; LC-3 adds the analytics below to it:

- `domain_throttling(util, freq, max_freq, temp, thermal_state, *,
  util_gate, temp_gate, freq_percent)` — moved from `widgets.py:282-294`
  with its gate constants; plus `bandwidth_percent(snapshot, max_total_bw)`
  and `package_power_percent(snapshot, package_ref_w)` from
  `widgets.py:297-315` (signatures take explicit refs, not the TUI config
  object).
- `class AlertEngine`: constructed from the threshold values
  (`alert_*` fields of `DashboardConfig` — pass values, not the config type,
  so `analytics` stays TUI-config-agnostic). `feed(snapshot) ->
  AlertFrame` where `AlertFrame` is a small frozen dataclass:
  `thermal_alert: bool`, `cpu_throttle: bool`, `gpu_throttle: bool`,
  `bw_alert: bool`, `pkg_alert: bool`, `swap_alert: bool`,
  `swap_rise_gb: float`, `session_energy_j: float`. It owns the sustain
  counters, swap history, and the energy integral — everything now in
  `widgets.py:825-920` + `563-565`. Snapshot deltas use
  `snapshot.timestamp` for the energy dt (more honest than the TUI's
  `interval` approximation at `widgets.py:563-565`). Note the **first
  `feed()` contributes 0 J** (no prior timestamp to diff against), a slight
  change from today's fixed-interval first frame — call it out in the
  CHANGELOG alongside the dt-source note in §7.
- The **status string stays a TUI concern**: `_compute_alerts` shrinks to
  `frame = self._alert_engine.feed(s)` + token formatting
  (`widgets.py:895-920` retained). Session-energy formatting
  (`_format_session_energy`) reads `frame.session_energy_j`.
- ANE utilization becomes a data point: `SystemSnapshot.ane_util_pct`
  computed in `_sample_to_snapshot` from `ane_watts` and the SoC profile's
  ANE reference (F5); `widgets.py:520` reads the field.
- Deletion rule (conformance): the widget-local copies
  (`_domain_throttling`, `_bandwidth_percent`, `_package_power_percent`,
  counters, `_session_joules`) are **deleted**, not shimmed.

### F4 — split `utils.py` along the L1/L2 seam (V6)

Minimal-churn split (repo style: small incremental moves, no big-bang):

- `attribute_power` (pure domain math) → `analytics.py`. **This move creates
  `analytics.py`** (in LC-2); LC-3 extends the same module. Update the two
  importers (`api.py` after F2; `app.py` import disappears entirely with F2).
- `utils.py` keeps acquisition + platform discovery (single L1 role); its
  docstring states the layer assignment. A rename/further split (e.g.
  `system_metrics.py`) is **not** worth the churn — record as non-goal.

### F5 — ANE reference power into `soc_profiles.py` (V7)

- Add `ane_max_w` as the trailing field on `SocProfile` (`ane_max_w: float =
  8.0`), plus every one of the 16 `KNOWN_SOC_PROFILES`, `GENERIC_APPLE_SILICON_
  PROFILE`, the 4 `TIER_FALLBACKS`, `_copy_with_name` (must copy the new
  field), and the `get_soc_info` dict (`utils.py:91-101`) — all defaulting to
  the current `8.0` (behavior-preserving; per-generation refinements are a
  separate research task — the field creates the slot). Frozen-dataclass call
  sites stay valid because profiles pass `name` positionally then kwargs, so a
  trailing defaulted field does not shift any positional arg.
- `create_dashboard_config` reads it from `soc_info` instead of the literal
  (`config.py:50`); `get_soc_profile` consumers unchanged.
- **`ane_util_pct` needs an L2 ANE reference that L2 does not have today.**
  `_sample_to_snapshot(sample, ram, interval_s)` and `Monitor` carry no SoC
  profile — the ANE denominator currently lives only in
  `DashboardConfig.ane_max_power` (`config.py:50`), which L2 never sees (the
  widget does `s.ane_watts / cfg.ane_max_power` at `widgets.py:520`). **Fix
  in this PR:** `Monitor.__init__` acquires `ane_max_w` once (call
  `get_soc_info()` and read the new key, storing `self._ane_max_w`) and passes
  it into `_sample_to_snapshot`, which sets
  `ane_util_pct = clamp_percent(ane_watts / ane_max_w * 100)`. `widgets.py:520`
  then reads `s.ane_util_pct` and drops its `cfg.ane_max_power` divide. This is
  the one place LC-1 is most likely to stall — resolve the plumbing before
  writing the field.

### F6 — rolling native-unit stats out of the widget (V8) — optional, last

- `analytics.RollingStats(window)` fed per snapshot; exposes
  `avg/max` per metric (watts, GB/s, percents). Widget keeps only
  chart-percent deques + per-core presentation history.
- Lowest payoff of the set; schedule opportunistically (it mostly relocates
  working code). If skipped, V8 stays documented here as accepted debt.

## 4. Delivery plan & sequencing with the other open TODOs

Every PR: branch from `main` → PR into `main` (no stacking), version bump +
CHANGELOG, `ruff check --fix . && ruff format .`, `pytest -q`, on-device run.

| PR | Content | Bump |
|---|---|---|
| **LC-1** ✅ shipped v1.2.4 (#21) | F1 (snapshot RAM completion, kill duplicate sampling, config chip fields) + F5 (`ane_max_w` slot) + `ane_util_pct` field | patch |
| **LC-2** ✅ shipped v1.3.0 | F2 (`ProcessSample`, opt-in collection, L2 attribution) + F4 (`attribute_power` move → `analytics.py`); `MetricsUpdated` → snapshot-only | **minor** (public API addition: `ProcessSample`, `Monitor(include_processes=...)`) |
| **LC-3** | F3 (extend `analytics.py`: `AlertEngine`, throttling, session energy; widget analytics deleted). Note `analytics.py` now **exists** (created in LC-2 with `attribute_power`); LC-3 adds the alert/throttle/energy analytics to it | patch |
| **LC-4** *(optional)* | F6 (`RollingStats`) | patch |

**Revised master queue across all open plans:**

1. `TODO-convergence-quick-wins` **Feature B** (fan `F{n}Mx`) — orthogonal, already first.
2. **LC-1 → LC-2 → LC-3** (this plan).
3. `TODO-tui-layout-redesign` **PR1 → PR2** — lands on a cleaner base:
   `MetricsUpdated(snapshot)` only, and PR1's `AlertsComputed` message
   carries formatting of an `AlertFrame` instead of re-homed widget math.
   **Update that doc's §5.1-6/§5.2-1 wording when LC-3 merges.**
4. `TODO-convergence-quick-wins` **Feature A** (palette cycle) — after the
   layout settles (bindings/help/compose churn zone).
5. LC-4 + net/disk I/O plan (`TODO-net-disk-io`) thereafter; note net/disk
   should add its data points to `SystemSnapshot` *from the start* under
   this plan's contract.

Rationale for slotting LC before the layout redesign: PR1 currently plans to
move `#status-line` + invent `AlertsComputed` around the *old* in-widget
alert math; doing LC-3 first means that move relocates a thin formatter, not
an analytics engine — strictly less churn than either order reversed.

## 5. Test plan (functional only, per CLAUDE.md)

- **LC-1**: `Monitor().get_snapshot()` carries `ram_total_gb > 0`,
  `0 ≤ ram_used_percent ≤ 100`, `ane_util_pct` consistent with `ane_watts`
  (`@pytest.mark.local`, real hardware — extend `tests/test_api.py`).
  `create_dashboard_config` exposes `chip_name`/`gpu_core_count`
  (`tests/test_config.py`, real merge). Dashboard: existing
  `tests/test_dashboard_metrics.py` harness updated for the
  `MetricsUpdated` shape; RAM row asserts against snapshot-fed values.
  `get_soc_profile` returns `ane_max_w` for known + tier-fallback chips
  (`tests/test_soc_profiles.py`).
- **LC-2**: `Monitor(include_processes=True).get_snapshot().processes` is a
  non-empty `list[ProcessSample]` with a plausible self-process entry
  (`local`); `attributed_w` partition property (Σ over all ≤ cpu+gpu watts)
  asserted *inside* that behavioral test, not standalone. Rewrite
  `tests/test_per_process_power.py`'s table assertions against typed samples
  through the same public `ActopApp.run_test()` path it already uses; delete
  any assertions that only pinned the old dict keys.
- **LC-3**: `AlertEngine` driven with sequences of real-shaped
  `SystemSnapshot`s through public `feed()`: sustain-threshold behavior
  (N-1 hot frames → no alert; N → alert), swap-rise, energy monotonicity;
  the TUI-rendered status line still shows `THERMAL`/`THROTTLING` tokens via
  the existing mounted-dashboard test. Delete/rewrite any test that reached
  the old widget-internal analytics.
- **CI parity**: hardware-dependent additions marked `@pytest.mark.local`
  (CI runs `-m "not local"`).

## 6. Docs to sync (with the PR that changes each fact)

- `docs/DESIGN-system.md`: data-flow section — snapshot as sole frame
  contract, `analytics.py` layer, opt-in process collection.
- `CLAUDE.md` + `README.md`: module tables (add `analytics.py`), data-flow
  paragraph, Python-API snippet if it gains `include_processes`.
- `docs/REVIEW-architecture-comparison.md`: public-API row (processes +
  alerts now API-accessible — a differentiator vs both peers).
- `docs/TODO-architecture-roadmap.md`: insert this plan into the priority
  list (queue in §4 above).
- `docs/TODO-tui-layout-redesign-2026-07-02.md`: §5.1-6/§5.2-1 reference
  `AlertFrame`; §4 table row for `MetricsUpdated`.
- Export follow-up (out of scope here): once LC-2/LC-3 land, `export.py` can
  emit processes/alerts/energy — file under the roadmap when planned.

## 7. Risks & mitigations

| Risk | Mitigation |
|---|---|
| `SystemSnapshot` constructor-arg growth breaks external callers | All new fields defaulted (repo precedent `models.py:39-48`); construction via `Monitor` unaffected |
| Opt-in process collection changes TUI timing (process walk now inside `get_snapshot`) | Same total work per frame as today (the walk just moves from `app.py:334` into the same worker's `get_snapshot` call); verify frame cadence on-device in LC-2 |
| Live filter-regex mutation raced through to L2 | Keep today's semantics: the worker re-reads the filter each tick (`app.py:443-444`); Monitor takes the pattern per call or reads a plain attribute — no locking added, same guarantees as now |
| `AlertEngine` timestamp-based energy differs slightly from interval-based | Difference is a correctness *improvement*; note in CHANGELOG; `Profiler.total_package_joules` should adopt the same dt source for consistency |
| Two plans editing `widgets.py`/`app.py` in flight | Sequencing in §4 is strict; layout PR1 does not start until LC-3 merges |

## 8. Acceptance criteria

1. `grep -n "from actop.utils import" actop/tui/*.py` returns nothing —
   the TUI imports only `models`/`api`/`config`/`analytics`/`power_scaling`
   types (plus Textual/stdlib).
2. One `get_ram_metrics_dict()` call per frame, inside L2.
3. `MetricsUpdated` carries exactly one field: the `SystemSnapshot`.
4. `Monitor(include_processes=True)` exposes typed, watt-attributed
   processes; export-visible parity gap for processes/alerts documented as
   follow-up.
5. No throttle/alert/energy math remains in `tui/`. Gate precisely — a bare
   grep for `sustain` false-positives on surviving legitimate refs (the help
   text at `app.py:157`, and `cfg.alert_*` reads that stay in the L3 token
   formatter at `widgets.py:913,917`). Grep instead for the internals being
   removed: `_THROTTLE_UTIL_GATE`, `_THROTTLE_TEMP_C`, `_session_joules`,
   `_high_bw_counter`, `_high_pkg_counter`, `_throttle_cpu_counter`,
   `_throttle_gpu_counter`, `_domain_throttling`, `_bandwidth_percent`,
   `_package_power_percent` under `tui/` → all must return nothing.
6. `pytest -q` green locally; `-m "not local"` green; ruff clean; TUI
   visually unchanged after each LC PR (these are refactors — captures
   compared before/after per the runbook in the layout plan §8).

## 9. Non-goals

- Renaming/splitting `utils.py` beyond the `attribute_power` move.
- Export format changes (follow-up).
- New metrics (net/disk, fan max — separate plans).
- Per-generation `ane_max_w` values (slot created, research separate).
- Textual/TUI layout changes (deliberately zero visual delta; the layout
  redesign plan owns those).
