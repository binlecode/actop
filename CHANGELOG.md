# Changelog

All notable changes to `binlecode/actop` should be documented in this file.

This project follows a Keep a Changelog-style format and uses version tags for releases.

## [Unreleased]

## [1.7.2] - 2026-08-11

### Added
- **Alert/throttle/energy in export backends (`--json` / `--serve`).** Both
  `run_json_stream` and `serve_prometheus` now construct an `AlertEngine` from
  the same alert-threshold CLI flags the TUI uses; each snapshot is fed through
  the engine and the resulting `AlertFrame` (thermal, cpu/gpu throttle, bandwidth
  saturation, package-power peak, swap rise, cumulative session energy) is merged
  into every NDJSON record and every Prometheus scrape. An LLM profiling session
  through `--json` now answers *did the chip throttle?* and *what did the run
  cost in total energy?* without opening the TUI.
- **`--json-processes` / `--serve-processes` CLI flags** for explicit per-process
  rows in export mode. The underlying `include_processes` pipe already existed;
  these flags close the discoverability gap.

### Changed
- **Tests restructured to functional-only.** Removed 8 format-contract tests
  (single-line JSON, TYPE header present, synthetic AlertFrame key assertions);
  merged fan/process tests; added 2 functional AlertEngine pipeline tests
  (mock `Monitor` → `AlertEngine` → verify computed alert values match sustain
  logic). 12 tests now (6 non-local, 6 local), all functional.
- **Dropped LC-4 (rolling-stats widget cleanup) from roadmap.** 4 deques in
  `HardwareDashboard` are pure code hygiene, not a user-visible defect, and
  track E does not depend on them.
- **CLAUDE.md:** added format-contract/shape-test rejection rule to the
  functional-only mandate.

## [1.7.1] - 2026-08-11

### Changed
- Updated hero demo GIF and cover page with theme switching, and corrected
  keybinding references (`t`=theme, `p`=processes, `Space`=pause).
- Updated vhs tape for GIF recording to reflect new keybindings.

## [1.7.0] - 2026-08-11

### Added
- **`--theme` flag** to select Textual app theme at launch (UI chrome: header,
  footer, borders, text colors). 8 curated built-in themes: `textual-dark`
  (default, unchanged), `textual-light`, `nord`, `dracula`, `tokyo-night`,
  `monokai`, `gruvbox`, `catppuccin-mocha`. Orthogonal to `--palette` (chart
  gradient) — any theme works with any palette.
- **`t` key binding** to cycle through themes live during a session with a toast
  notification on each change. Wraps around from the last theme back to the first.

### Changed
- **Process table toggle** moved from `t` → `p` (`p`rocesses).
- **Pause/resume** moved from `p` → `Space` (universal pause key).

## [1.6.9] - 2026-08-09

### Added
- **Guard-release CI workflow** (`guard-release.yml`): every `v*` tag push verifies
  all tags have a corresponding GitHub Release object, failing the run if any are
  missing — catches the silent link breakage that caused the v0.8.7–v1.6.6
  release-object gap.
- **Pre-push githook tag nudge**: warns when a bare `git push` of a `v*` tag is
  detected, reminding to use `scripts/tag_release.sh` instead.

### Changed
- README badge row gains a Website badge linking to the Cloudflare Pages coverpage
  (`actop.pages.dev`).

## [1.6.8] - 2026-08-09

### Changed
- **Cutting a release REQUIRES a GitHub Release (`gh release create`), not just
  a git tag.** `scripts/tag_release.sh` now creates both — it pushes the tag and
  then creates the GitHub Release with the notes from the matching
  `CHANGELOG.md` section (falling back to generated notes if the section is
  missing). A bare `git tag` + push never surfaces on `/releases` or as `Latest`,
  so a hand-tag-only release cut is now an explicit rule violation — codified in
  `CLAUDE.md` → Release Process, with a manual-recovery playbook for when the
  `gh release create` step fails.
- **% readouts and the fan spinner tint by utilization color.** The headline %
  in each metric row (and the fan-glyph) now wears the same severity color as
  the sparkline that traces it — a 90% reading reads red, an idle 5% stays cool.
  NO_COLOR / dumb terminals degrade to untinted, matching the charts.
- **README badge row** — PyPI version + supported Python versions, License,
  and CI badges at the top.
- **Docs reorg**: skills consolidated under `.agents/skills/`
  (`capture-tui-diagram`, `run-actop`, `record-tui-gif`, plus new
  `publish-cover` for the landing-page cover); `docs/DESIGN-system.md` renamed
  to `docs/SPEC-system.md`; the CI/CD + release runbook folded from the deleted
  `docs/DESIGN-sdlc-cicd.md` into `CLAUDE.md` → Release Process; README /
  CONTRIBUTING / SECURITY release references updated; SECURITY gains a
  `HOMEBREW_TAP_TOKEN` handling section; `.gitignore` covers the
  `dist-cover/` landing-page build output.

### Added
- Tests covering `domain_throttling` on an unknown DVFS ceiling (never
  throttling) and the die-temp fallback when the thermal state is nominal.

## [1.6.6] - 2026-08-09

### Changed
- **README hero GIF re-recorded** (`images/actop-demo.gif`) — refreshed after the
  GPU% process-table column landed; recorded under a live llama.cpp workload
  (OpenAI wire protocol on the llamacpp router, original `qwen3.6-35b-a3b`
  weights) instead of the ollama-router. `record-tui-gif` skill moved from
  `.claude/skills/` to `.agents/skills/`; `record.sh` now defaults to the
  llama.cpp router (`API=openai`), with the ollama-router preserved as a
  documented fallback (`API=ollama` + native `/api/generate`). Cover
  (`cover/`) re-deployed with the new GIF.

## [1.6.5] - 2026-08-09

### Added
- **`--json --samples N` — bounded NDJSON emission for agent/script one-shots.**
  `--json` previously streamed until interrupted, forcing an agent or script to
  kill the process after reading its record. `--samples N` (with `--json`) now
  emits exactly `N` snapshot records then exits 0; `--samples 0` (default)
  keeps the streaming behavior. The first record already carries real deltas
  (the `Monitor` primes the baseline at construction), so `--json --samples 1`
  is a clean single-snapshot tool call.

## [1.6.4] - 2026-08-09

### Fixed
- **Process CPU% was understated ~41.7x on every Apple Silicon machine.** The
  two CPU-time fields in `proc_pidinfo` (`pti_total_user` / `pti_total_system`)
  are mach absolute-clock ticks, not nanoseconds — on Apple Silicon the
  timebase is 125/3 (1 tick = 41.667 ns). `get_native_processes()` returned
  those raw ticks as `cpu_time_ns`, so every process's CPU% read ~2.4% of its
  real value (e.g. 1.2 s of burned CPU appeared as 0.03 s). `proc_pidinfo`'
  actually reports nanoseconds only by coincidence on Intel (timebase 1/1),
  which is why the offset-verified offsets never surfaced the unit. The module
  now reads `mach_timebase_info` once at import and converts via integer math
  (`_mach_ticks_to_ns`). Per-process `cpu_time_share` and watt attribution are
  ratio-based, so they were never affected. See Apple openradar FB9546856.

## [1.6.3] - 2026-08-08

### Added
- **GPU% column in the TUI process table** — `gpu_time_share` from the Monitor
  was always collected but never rendered. The process table now shows a `GPU%`
  column (between CPU% and PWR) displaying the per-process share of total GPU
  time as a percent. `–` when the first GPU delta is still pending. The `s` sort
  cycle now includes GPU% (CPU% → GPU% → PWR → RSS → PID).

## [1.6.2] - 2026-08-08

### Fixed
- **Export modes (`--json` / `--serve`) now honor `--show-processes` and
  `--proc-filter`** — they were silently ignored, so every NDJSON record carried
  `"processes":[]` even when the TUI (`t` key) showed populated per-process data.
  The CLI routing (`_run_export`) now forwards both flags to the NDJSON and
  Prometheus backends; `run_json_stream` and `serve_prometheus` pass them through
  to `Monitor(include_processes=True, process_filter=...)`. `--proc-filter`
  without `--show-processes` implies it, matching the Monitor's opt-in cost model
  where process collection stays off by default.
- **Per-process Prometheus gauges** (`actop_process_cpu_percent`,
  `actop_process_cpu_time_share`, `actop_process_gpu_time_share`,
  `actop_process_attributed_watts`, `actop_process_rss_bytes`,
  `actop_process_num_threads`): labelled by `pid` and `command`, emitted only
  when `include_processes=True`. The NDJSON path needs no format change —
  `dataclasses.asdict` already serialised processes when they were collected;
  the gap was purely that collection was never enabled.

## [1.6.1] - 2026-07-29

Docs only — no code change.

### Added
- **Landed `docs/TODO-layering-cleanup-2026-07-02.md`, the design record behind
  LC-1→LC-3** (shipped v1.2.4–v1.3.0: `SystemSnapshot` as the sole frame
  contract, `ProcessSample` through L2, and `analytics.py`'s `AlertEngine` /
  throttle / session-energy move out of the widget). The plan drove all three
  releases but lived only on an unmerged branch, so the code shipped while its
  rationale stayed unpublished — the violation inventory, per-violation fix
  design, and sequencing are now in the repo instead of one branch tip.

  §§1–9 are the July plan verbatim, so their line references point at July code.
  A new **§10** records status verified against `main`, and is the only open
  scope.
- **Two roadmap items promoted out of that plan** into
  `docs/TODO-architecture-roadmap.md`:
  - **Export parity** (§10.2) — per-process rows, throttle/alert flags, and
    session energy are still TUI-only and never reach `--json` / `--serve`, so
    profiling a local inference run through the export backends cannot answer
    which process drew the watts, whether the chip throttled, or what the run
    cost in energy. Both candidate designs and the recommendation are recorded,
    along with the per-PID Prometheus cardinality constraint that keeps process
    data NDJSON-only either way.
  - **LC-4** (§10.1) — the watt/GB-s history deques and `_avg_max` reducer never
    moved to an `analytics.RollingStats`. Explicitly low priority: it relocates
    working code, and the reason to do it is export parity needing the same
    aggregates outside the TUI.

  §10.3 records one acceptance criterion that does not literally pass and should
  be reworded rather than "fixed": `tui/app.py` still imports `get_soc_info` for
  a single construction-time call that builds `DashboardConfig`. The criterion
  targeted per-frame L1 acquisition in the view, which is gone; routing that one
  call through another module to satisfy a grep would add indirection for no
  layering gain.

## [1.6.0] - 2026-07-29

Reading-plane audit §8: adopt the GPU driver's `IOAccelerator`
`PerformanceStatistics` as a second, independent GPU utilization source.
Verified on live hardware (M4 Max / Darwin 25.5.0). As-built design in
`docs/DESIGN-system.md` §3.8.

### Added
- **Renderer/Tiler GPU breakdown — a metric actop could not previously
  express.** `Renderer Utilization %` (shader/compute work) and `Tiler
  Utilization %` (geometry work) are read off the accelerator's own
  `PerformanceStatistics` dict via IOKit ctypes. For local-inference profiling
  this separates an MLX/CoreML compute frame (Renderer high, Tiler ≈ 0) from a
  render-bound one — a split IOReport residency cannot report at all, because
  the GPU exposes a single unified `GPUPH` channel.

  New `SystemSnapshot` fields `gpu_device_pct` / `gpu_renderer_pct` /
  `gpu_tiler_pct` / `gpu_perf_stats_available` / `gpu_util_source`; new
  `actop_gpu_device_utilization_percent` /
  `actop_gpu_renderer_utilization_percent` /
  `actop_gpu_tiler_utilization_percent` Prometheus gauges; all five fields in
  NDJSON. `gpu_util_source` is a string and so is deliberately **not** a
  Prometheus gauge — emitting it as one would produce a non-numeric value line
  and break the whole scrape.
- **New public L1 reader** `gpu_registry.get_gpu_perf_stats()`, returning a
  `GPUPerfStats(device_pct, renderer_pct, tiler_pct, available)` namedtuple.
  Costs 0.025 ms/call measured — 33× less than the per-process GPU-time walk
  already running each frame — so it needs no caching and adds no measurable
  idle-CPU load. `ioreg` is deliberately not shelled out to.
- **A `Rend N% · Tiler N%` row in the `GPU · ANE` TUI section**, hidden entirely
  when the accelerator reports no statistics (the same hide-row contract as Mem
  BW and Fan) rather than showing a phantom `0/0`. `Device Utilization %` is
  deliberately kept out of the TUI: the GPU row already carries the headline
  percent, and a second, differently-measured whole-GPU number beside it reads
  as a contradiction. It remains available via the API and both exports.

### Changed
- **GPU utilization now degrades to the driver's reading instead of silently to
  zero.** `gpu_util_pct` and `gpu_freq_mhz` both depend on the GPU DVFS table
  being classified by `_classify_dvfs_tables`; when that fails there is no
  ceiling (`gpu_max_freq_mhz == 0`) and both values were meaningless but
  indistinguishable from a genuinely idle GPU. `api._sample_to_snapshot` now
  falls back to `Device Utilization %` in exactly that case and records which
  path was used in `gpu_util_source` (`"residency"` | `"ioaccelerator"`). The
  TUI renders `GPU N% (drv)` and drops the unmeasured `@NMHz` when the fallback
  is active.

  IOReport residency remains the primary metric on every recognized chip.
  Measured side-by-side, the two diverge hard per-sample (`actop=40% @1232MHz`
  vs `Device=91%` in one frame) because residency is integrated over the sample
  interval while the driver's number is an instantaneous point read; swapping
  them wholesale would be a regression in sampling semantics for a sampling
  monitor. The fallback branch is unreachable on M1–M4, so it is verified by
  inspection rather than by a test — forcing it would need a mock, which the
  testing contract forbids.

### Fixed
- **Every letter key went dead under Caps Lock, which broke the TUI outright for
  CJK input-source users.** Caps Lock and Shift deliver the uppercase character,
  and Textual names that key `"Q"`, not `"q"` — so the lowercase-only bindings
  simply never matched and `q`/`p`/`s`/`g`/`l`/`c`/`t` all stopped responding
  with no feedback. This is not a fringe case: with a Chinese input source
  selected, Caps Lock is *how* macOS forces direct ASCII, so uppercase is the
  normal way these keys arrive in that mode.

  Each letter action now carries a hidden uppercase alias, derived from a single
  `_LETTER_BINDINGS` list so the two cannot drift, and the footer still shows one
  row per action rather than fourteen. `check_action` gates by action name, so
  the aliases inherit its gating unchanged. The help overlay's own close keys get
  the same treatment.

  Two consequences worth knowing. **`Shift`+`q` now quits too** — a terminal
  delivers the same `Q` for Shift as for Caps Lock, so the two cannot be told
  apart and aliasing one aliases both. And a **CJK input source with Caps Lock
  off still will not respond**: the IME consumes the letters before they ever
  reach the process, which no in-app binding can reach. Caps Lock on — the case
  this fixes — is the documented way to get direct ASCII in that mode.

## [1.5.0] - 2026-07-29

Reading-plane audit remediation (`docs/TODO-reading-plane-audit-2026-07-29.md`
§§1-6), verified against live hardware on an M4 Max / Darwin 25.5.0. §8
(`IOAccelerator` Device/Renderer/Tiler utilization) is deferred to its own PR;
§3.5 (removing the deprecated `*_gb` fields) is breaking and rides 2.0.0.

### Added
- **Byte quantities are now exported as exact byte counts.** New
  `SystemSnapshot.ram_used_bytes` / `ram_total_bytes` / `swap_used_bytes` /
  `swap_total_bytes`, `ProcessSample.rss_bytes`, `*_bytes` keys on
  `utils.get_ram_metrics_dict()`, and `actop_ram_used_bytes` /
  `actop_ram_total_bytes` / `actop_swap_used_bytes` / `actop_swap_total_bytes`
  Prometheus gauges.

  Bytes rather than a GiB/GB prefix, for three reasons: the GB-vs-GiB question
  cannot be got wrong if no prefix is applied; byte counts are exact, whereas the
  old rounded fields quantize to ±50 MiB at one decimal; and base units are the
  Prometheus/OpenMetrics naming convention (`node_exporter` uses
  `node_memory_MemTotal_bytes`). Prefix formatting is a display concern and now
  happens only in the TUI.

  **Additive and non-breaking.** `ram_used_gb` / `ram_total_gb` / `swap_used_gb` /
  `swap_total_gb` / `rss_mb`, the `*_GB` dict keys, `convert_to_GB` and the
  `*_gigabytes` gauges all remain as rounded views with unchanged values.
  **They are deprecated and will be removed in 2.0.0.**
- `--alert-swap-rise-gib` replaces `--alert-swap-rise-gb`, which is kept as a
  **working alias** (same destination) until 2.0.0. The threshold was always
  compared against GiB values, so the old name was a misnomer rather than a
  different unit. `AlertFrame.swap_rise_gb` is likewise renamed
  `AlertFrame.swap_rise_gib`, and the alert token renders `SWAP+0.3Gi`.

### Fixed
- **Memory reported binary quantities under decimal names.** `convert_to_GB`
  divided bytes by 2^30 and called the result GB; `rss_mb` divided by 2^20 and
  called it MB. Per IEC 80000-13, `1 GB = 10^9` while `1 GiB = 2^30`, so both were
  wrong by standard. The mislabel reached the public API (`ram_used_gb`,
  `ram_total_gb`, `swap_*_gb`, `ProcessSample.rss_mb`), the `ram_used_gigabytes`
  Prometheus gauge, the NDJSON stream, and the TUI (`RAM 66.7/128.0GB`,
  `MEM (MB)`).

  Anyone dividing memory against the genuinely decimal `bandwidth_gbps` was
  picking up a silent **7.4% error** — and `actop`'s audience does exactly that
  (`tokens/s ~= effective_bandwidth / bytes_read_per_token`, RAM headroom vs.
  quantized weights). Fixing only the display string was considered and rejected:
  it would protect the casual reader while continuing to mislead the actual user.

  The TUI now displays **GiB** and **MiB**, matching modern monitors (btop,
  bottom, `free -h`, `nvidia-smi`, `docker stats`, Kubernetes `Mi`/`Gi`).
  **Bandwidth is deliberately left decimal**: the DCS bucket labels are literally
  `"32GB/s"` and Apple publishes 546 GB/s for M4 Max decimally, so `GB/s` is the
  vendor's own unit for the bus, not an inconsistency.
- The swap-rise alert now measures growth from the exact `swap_used_bytes` counts
  instead of the rounded `*_gb` view, so a 0.1 GiB threshold can no longer trip on
  rounding alone.
- **`_resolve_state_freq` returned `0` and `None` for different flavours of
  "unresolvable", and its two consumers disagreed about which meant what.** An
  out-of-range `V{n}P{m}` / `P{n}` index returned `0`, which
  `_compute_residency_metrics` counted as an *active* state (inflating
  `active_pct` while dragging `avg_freq` down) but
  `_compute_residency_distribution` bucketed as *idle* — so `gpu_util_pct` and
  `gpu_residency_pct`, displayed side by side, could contradict each other.
  Out-of-range now returns `None` and both consumers reject `freq <= 0`.
  **Latent on M1-M4:** a probe of all 316 real state entries across every
  `CPU Stats` / `GPU Stats` channel on an M4 Max hit the zero path 0 times. It
  triggers on a chip exposing more states than its DVFS table describes — the
  unknown-future-chip path the `soc_profiles` tier fallback exists to serve.
- **`bandwidth_available` reported that a channel exists, not that it carried
  data.** A present-but-silent `AMCC RD+WR` channel surfaced
  `bandwidth_available=True` with `bandwidth_gbps=0.0`, so the TUI showed
  `Mem BW 0.0 GB/s` instead of hiding the row — the misleading zero the hide-row
  logic exists to prevent. Availability now also requires non-zero residency.
  Verified no row flicker on the first frame or at idle for both `subsamples=1`
  and `subsamples=3`.
- **Percentages truncated instead of rounding.** `floor` is a biased estimator
  (expected error -0.5 units, max 1.0, for a uniform fractional part), so every
  percentage read systematically low and 99.9% displayed as `99%`. `clamp_percent`,
  the residency `avg_freq` / `active_pct`, and the RAM/swap used-percent now
  round. Deliberately left as `int()`:
  `sampler._largest_remainder_percentages`' floors, which are Hamilton's
  apportionment rather than rounding — `round()` there would let the remainder go
  negative and silently break the sum-to-100 guarantee.

  **This is not purely cosmetic.** `clamp_percent` feeds `bandwidth_percent` and
  `package_power_percent`, which feed the `MEM-BOUND` and `PKG` alert thresholds
  through `AlertEngine`, so a value sitting *exactly* on its threshold can now
  fire one sample earlier.
- `Hz -> MHz` conversion in `native_sys.get_dvfs_tables_native` rounds instead of
  flooring, so a 1,499,800,000 Hz state reads 1500 MHz rather than 1499.
  **No visible change on M1-M4** — every observed table entry is already an exact
  MHz multiple, and `get_dvfs_tables_native()` returns the same table set,
  lengths and values before and after on an M4 Max. This is pre-emptive
  correctness for chips whose tables are not exact, not a fix for a wrong number
  anyone is seeing today.

### Changed
- Documented that Apple's DVFS tables are **not monotonic**. The M4 Max GPU
  voltage-states table reads
  `[0, 338, ..., 1312, 1242, 1380, 1326, 1470, 1578]` — non-ascending, with
  `1182` appearing twice. Verified against raw `pmgr` bytes that the 8-byte
  `(freq_hz, voltage)` stride is correct and this is the table's genuine shape,
  not a stride bug. Three docstrings claimed ascending order, which would invite
  "optimizing" `max(freq_table)` into `freq_table[-1]` and silently break the
  DVFS ceiling. Docstrings and comments only; the code was already correct
  because it uses `max()` throughout.

## [1.4.16] - 2026-07-29

### Changed
- Lint: unpin `ruff` and bring the tree into `0.16` compliance, lifting the
  `>=0.15,<0.16` cap that 1.4.14 added as a stopgap when `0.16.0`'s expanded
  default rule set turned a previously-clean tree into 126 errors. The rule
  families are now codified in `[tool.ruff.lint]` rather than inherited from
  ruff's defaults, so a future default-select expansion can't break the CI gate
  again. Deliberately not selected: `BLE`/`S` (the native ctypes/IOKit/SMC reads
  intentionally catch broad `Exception` and fail silent) and `EXE`/`PLW` (skill
  and scratch helper scripts). `RUF001`-`003` are ignored because the TUI
  intentionally uses `—`, `·`, `×` and braille; `RUF012` because Textual's
  `BINDINGS = [...]` is the framework idiom. No runtime or API change — the
  code edits are mechanical modernizations (`endswith` tuples,
  `dict.fromkeys`, comprehension and import cleanups).

### Fixed
- `docs/DESIGN-system.md`: repair the `VMStatistics64` example, whose elision
  markers had been collapsed into the field list (`...("compressor_page_count",
  ...)` on one line, plus a stray `...,`), leaving the snippet syntactically
  invalid.

## [1.4.15] - 2026-07-29

### Fixed
- Memory bandwidth reading: each DCS BW histogram bucket is now represented by
  its **midpoint** instead of its upper edge. The `PMP` / `DCS BW` /
  `AMCC RD+WR` state names are bucket *upper* edges in 32 GB/s steps, so the
  bottom bucket spans 0-32 GB/s but scored a flat 32. On an idle machine ~99%
  of the residency sits in that bucket (measured on an M4 Max: 4351 of 4396
  samples), so `Mem BW` was pinned at a constant `32.0 GB/s` with a flat chart
  line and an `avg`/`max` barely above it — real idle traffic of a few GB/s was
  indistinguishable from 31 GB/s. Each bucket is now weighted by the mean of its
  own edge and the previous one (the first bucket's lower edge is 0), derived
  from consecutive edges rather than assuming a fixed step.

  **Reported values shift down by half a bucket width (~16 GB/s per
  controller die).** Measured on an M4 Max via `Monitor.get_snapshot()`: idle
  went from a constant `32.0` to `16.85 / 17.00 / 16.82 / 16.87` GB/s over
  consecutive samples — no longer pinned — and a 4-thread `memcpy` workload
  read `109.8` GB/s, so loaded readings still track proportionally.

  Note this does not give sub-bucket resolution: the bottom bucket is 32 GB/s
  wide and holds nearly all idle residency, so an idle machine now reads ~16
  GB/s (that bucket's midpoint) rather than its true few-GB/s traffic. The
  histogram simply carries no finer detail below 32 GB/s, so the idle chart
  stays relatively flat — just at a defensible value instead of the bucket's
  ceiling. Chart and `MEM-BOUND` alert scaling are unchanged; both still
  normalize against the session ratchet from 1.4.13.

## [1.4.14] - 2026-07-29

### Changed
- CI: bump the pinned GitHub Actions to their latest SHAs (checkout
  `v7.0.1`, setup-python `v7.0.0`, pypi-publish `v1.14.1`) via the
  dependabot github-actions group update (#39). No runtime or API change;
  release cut to keep `main` tagged and version-bumped per the repo's
  per-PR convention, which the dependabot PR bypassed.

### Fixed
- CI: pin `ruff` to `>=0.15,<0.16` in both `pyproject.toml` `[dev]` and
  `main-ci.yml`. The lint step installed `ruff` unpinned, so a fresh runner
  pulled the new `0.16.0`, whose expanded default rule set turned a
  previously-clean tree into 126 `ruff check` errors — breaking the lint gate
  on `main` with no code change. Capping below `0.16` restores a reproducible,
  green gate; bringing the code into `0.16` compliance is deferred to its own PR.

## [1.4.13] - 2026-07-04

### Changed
- Mem BW and Package Power charts/alerts now normalize against a session
  ratchet: `max(calibrated_reference, highest_observed_this_session)`. Both
  denominators are best-effort per-chip guesses (Apple doesn't publish exact
  bus-bandwidth or power-limit specs); a real sample above the guess raises
  the effective ceiling permanently for the session (increase-only — a later
  lower sample never lowers it back), so the chart self-corrects toward the
  true physical ceiling instead of silently saturating against an
  under-calibrated static guess. `AlertEngine.feed()` computes both ratchets
  via a small private `_Ratchet` helper and returns them as two new
  `AlertFrame` fields (`effective_max_bw`, `effective_max_package_w`) — the
  engine's one existing output contract — so the chart and its alert always
  read the same ratcheted value from the same frame.

## [1.4.12] - 2026-07-03

### Fixed
- Memory bandwidth chart scaling: the Mem BW chart and the `MEM-BOUND`
  saturation alert now normalize against the SoC's true **unified-memory
  bandwidth** (a single `max_mem_bw` per profile), replacing the old
  `cpu_max_bw + gpu_max_bw` sum. Apple Silicon has one shared DRAM bus, so
  summing the two overstated the ceiling (e.g. 800 GB/s vs. the real 546 GB/s
  on M4 Max) — the chart could never exceed ~68% and the 85%-default
  `MEM-BOUND` alert (680 GB/s) was physically unreachable. The chart now spans
  the real 0–100% range and the alert can fire.
- Memory bandwidth reading on multi-die SoCs (Ultra): `_compute_bandwidth_gbps`
  now computes each memory-controller die's residency-weighted average
  **independently and sums them**, instead of pooling every die's histogram
  into one mean. Pooling reported a single controller's rate as the whole-chip
  total, under-reporting multi-die bandwidth. Single-die SoCs are unaffected.
- Power chart `auto` scaling: the CPU/GPU power charts now normalize against a
  true **rolling** peak over the retained wattage history, matching the
  documented "rolling peak" behavior. Previously the peak was an all-time
  monotonic max, so a single transient spike permanently compressed the charts
  for the rest of the session.

### Changed
- `SocProfile` / `DashboardConfig`: replaced the unused `cpu_max_bw` /
  `gpu_max_bw` split (only ever consumed summed) with a single `max_mem_bw`
  unified-memory-bandwidth reference carrying real vendor headline specs.

### Docs
- `docs/DESIGN-system.md` §3.5 / §7: document the per-die sum and the
  single `max_mem_bw` normalization reference.

## [1.4.11] - 2026-07-03

### Changed
- TUI: the P-CPU and E-CPU clusters now render as **two separate titled boxes**
  (previously two halves of a single "CPU" box), so each cluster's chart stands
  out as a sibling alongside GPU. The `grid` preset lays them out as the top row,
  with GPU·ANE / Memory on the next row and Power spanning the full width beneath.
- TUI: **per-core panels are now hidden by default.** With cores off, the
  P-CPU / E-CPU / GPU charts read as the prominent sibling boxes. `--show_cores`
  still opts into showing them at startup; the new `c` key toggles them live.

### Added
- TUI `c` key: toggle the per-core panels on/off at runtime (independent of the
  `--show_cores` startup default). Painted immediately on show, no sample wait.

### Docs
- `docs/DESIGN-system.md` §5: sync the layout prose to the five-box grid
  (P-CPU / E-CPU / GPU·ANE / Memory / Power), the per-core toggle, and the
  same-row boundary alignment; `CLAUDE.md` architecture table updated to match.
- `docs/TODO-architecture-roadmap.md`: add a "Nice-to-Have — Distribution & UX"
  item for an **update-available notice** (startup banner + title/status token,
  PyPI-version check, off-render-path and fail-silent, opt-out + cached).

## [1.4.10] - 2026-07-03

### Changed
- TUI charts in `dots` glyph mode (the default) now pack **2 time samples per
  character** by using both braille dot columns (left = earlier sample, right =
  later), doubling horizontal density to a continuous btop-style line instead of
  the previous sparse single-column poles. Applies to both the section charts
  (`BrailleChart._render_dots`) and the inline per-core / power sparklines
  (`_inline_spark`), which now share one `_braille_cell_bits` primitive. `block`
  glyph mode is unchanged (1 sample/character). The chart time-span label accounts
  for the 2×-denser `dots` sampling.

### Added
- TUI Fan row: each fan's RPM reading is prefixed with a braille-cascade spinner
  (`⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏`) whose spin rate is proportional to RPM, driven by its own timer
  decoupled from the sampler poll cadence.

### Docs
- `docs/DESIGN-system.md`: document the dense 2-samples-per-character braille
  rendering (§5.2), refresh the grid/stack/process TUI capture frames, and add
  §4.2.1 explaining why temperature is reported per-cluster (not per-core) —
  including the Apple Silicon sensor-count reality and how btop's per-core
  temperature column actually spreads a few cluster sensors across rows.
- Rename `docs/DESIGN-sdlc-cicd-release.md` → `docs/DESIGN-sdlc-cicd.md`
  ("release" is already implied by CI/CD) and update all references.

### Tests
- `tests/test_runtime_contracts.py`: enforce the functional-tests-only mandate —
  remove `test_soc_info_contract` and `test_top_processes_contract` (pure
  shape/bounds assertions whose code paths are already covered behaviorally by
  `test_config.py` and `test_per_process_power.py`), and rewrite the RAM test
  into a native-parse sanity guard (the derived-field invariants it previously
  asserted were tautological; the load-bearing check is that the raw reading is
  physically sane).

## [1.4.9] - 2026-07-02

### Docs
- `pyproject.toml`: refresh the PyPI-facing `description` from the stale generic
  "Performance monitoring CLI tool for Apple Silicon" to match the current
  positioning already locked in for the GitHub repo description — sudoless,
  TUI + Python API, GPU/ANE/bandwidth, local-LLM profiling — so the PyPI project
  page summary is consistent with GitHub.
- `docs/TODO-architecture-roadmap.md`: fix a dangling reference to "the launch
  runbook's post-launch loop" (that runbook is private/local-only, removed from
  origin in v1.4.7) with a self-contained description, now that this file is
  directly linked from the README's new `## Roadmap` section and read by
  external contributors.

## [1.4.8] - 2026-07-02

### Docs
- README: add a `## Roadmap` section linking `docs/TODO-architecture-roadmap.md`,
  naming the one open must-have (net/disk I/O, feasibility spike done) and the
  deliberately-deferred item (menu bar mode). Closes the gap where the "Where actop
  fits" comparison table showed a competitive gap (network/disk I/O, menu bar) with
  no indication it was tracked rather than abandoned.

## [1.4.7] - 2026-07-02

### Chore
- Remove the private launch/growth runbook (`docs/RUNBOOK-launch-and-growth.md`) from
  version control — it is internal market-promo notes, not part of the shipped product.
  Now untracked and `.gitignore`d (kept locally only). Dangling references in
  `CLAUDE.md` and the `docs/TODO-*` files were removed; historical `CHANGELOG` mentions
  are left as-is.

## [1.4.6] - 2026-07-02

### Docs
- README hero is now an animated GIF (`images/actop-demo.gif`) — actop live under an
  Ollama workload: stack layout, braille→block chart glyphs, then the watt-attributed
  process panel. The `grid` still (`images/actop.png`) is kept below as the fallback;
  the redundant `stack`/process stills (`actop_stacked.png`, `actop_procs.png`) were
  removed (the GIF demonstrates them in motion).
- Add `CONTRIBUTING.md` and a README `## Contributing` section ("PRs and issues
  welcome") pointing to it; `CLAUDE.md` remains the full source of truth.
- Sync the design docs with the current repo/security posture:
  `DESIGN-sdlc-cicd.md` now documents Actions **pinned to commit SHAs**,
  branch-protection **required status checks**, and the enabled Dependabot
  **alerts + security updates** / **private vulnerability reporting**;
  `DESIGN-system.md` gains §5.8 documenting the TUI→docs capture skills
  (`capture-tui-diagram`, `record-tui-gif`).

### Chore
- Add the `record-tui-gif` Claude Code skill (`.agents/skills/record-tui-gif/`):
  reproducible hero-GIF recording via `vhs` driven by a live GPU workload, so the
  capture can be refreshed after any TUI/layout change.

## [1.4.5] - 2026-07-02

### Security
- Pin all GitHub Actions to full commit SHAs (with version comments) in the CI,
  PyPI-publish, and formula-release workflows — `actions/checkout` (v4.3.1),
  `actions/setup-python` (v5.6.0), and `pypa/gh-action-pypi-publish` (v1.14.0,
  previously the mutable `release/v1` branch in the OIDC-privileged publish job).
  Eliminates the mutable-ref supply-chain risk; Dependabot's `github-actions`
  ecosystem keeps the pins current via the version comments.

## [1.4.4] - 2026-07-02

### Chore
- Untrack `.DS_Store` files (repo root and `images/`) that predated the
  `.gitignore` rule, so macOS Finder cruft no longer ships in the repo. The
  existing `.gitignore` entry keeps them out going forward.

## [1.4.3] - 2026-07-02

### Docs
- README: refresh the `grid` hero to v1.4.2 (shows the aligned column bottoms) and
  add two screenshots side by side below it in a two-column table — the single-column
  `stack` preset (`images/actop_stacked.png`) and the process panel
  (`images/actop_procs.png`, `--show-processes` / `t`, with the watt-attributed
  per-process `PWR` column). A markdown table is used so the side-by-side layout
  renders on both GitHub and the PyPI project page.

## [1.4.2] - 2026-07-02

### Fixed
- Grid layout preset: the CPU section (left column, `row-span: 3`) now fills its
  spanned height so its bottom border aligns with the lowest right-column box
  (Power), instead of closing early and leaving dead space beneath it. Scoped to
  the grid preset only (`height: 100%` on `HardwareDashboard.layout-grid
  #section-cpu`); the vertical `stack` preset is unaffected.

## [1.4.1] - 2026-07-02

### Added
- `--palette {thermal,viridis,mono}` CLI flag and matching `DashboardConfig.palette`
  field: selects the chart gradient for the session. `thermal` (default) is the
  existing blue→red gradient, unchanged; `viridis` is a colorblind-safe perceptual
  ramp; `mono` is grayscale intensity. Applies at the truecolor and 256-color tiers
  (the 16-color severity ramp and `NO_COLOR`/`none` are palette-independent).
  Accessibility-first: a set-once startup preference, not a decorative toggle.

### Changed
- `--help` now surfaces every option's default value (via
  `ArgumentDefaultsHelpFormatter`), so the supported value set (from `choices`) and
  the default are documented for all arguments.

### Notes
- A runtime color-cycle keybind (peer parity with mactop/macmon's decorative theme
  cycling) was evaluated and deliberately deferred: the startup `--palette` flag
  delivers the accessibility value, and set-once is the right model for it. See
  `docs/DESIGN-system.md` §5.2. The palette registry is ordered so the keybind
  remains a purely additive follow-on if ever wanted.

## [1.4.0] - 2026-07-02

### Added
- TUI layout PR2 (layout presets) — the dashboard now ships two arrangements of
  the same four sections, switchable live:
  - **`grid`** (new default): two columns — the CPU section spans the full left
    column, GPU·ANE / Memory / Power stack in the right column. Fits short
    terminals without scrolling (~28 rows incl. chrome).
  - **`stack`**: the previous single full-width scrolling column — longest chart
    history span.
- `--layout {grid,stack}` CLI flag (default `grid`) and a matching
  `DashboardConfig.layout` field.
- `l` key cycles the layout preset (grid ⇄ stack) live, with zero data loss
  (history deques are untouched by a switch); documented in the help overlay
  and footer.
- Auto-degrade: a requested `grid` narrower than ~96 columns renders as `stack`
  automatically and recovers when the terminal widens, so the two columns never
  squeeze below readability. `HardwareDashboard.effective_layout_preset` exposes
  the applied preset vs. the requested `layout_preset`.

### Changed
- The process table is now a fixed 74-column panel (was `1fr`); the dashboard
  absorbs the remaining width. Dashboard/section CSS moved into
  `HardwareDashboard.DEFAULT_CSS` (scoped to the widget) — the presets are a CSS
  class swap there, not app-level layout.
- Width-adaptive rows (inline power sparks, core grids) now re-render on a
  terminal resize or preset swap instead of waiting for the next sample.

## [1.3.3] - 2026-07-02

### Changed
- TUI layout PR1 (sectioned dashboard) — the dashboard is now four titled,
  bordered section containers (`CPU`, `GPU · ANE`, `Memory`, `Power`) instead of
  one undifferentiated bordered stack; section titles live in the border (no
  content-row cost). This is the first of two layout PRs; the grid/stack preset
  switch (`--layout`, `l` key) follows in the next milestone.
- Power section compaction — the CPU and GPU power blocks (label + 3-row chart
  each) collapse into single inline-sparkline rows
  (`CPU 6.59W <spark>  avg … · max …`), reclaiming ~4 rows. Package Power keeps
  its full chart; the `g` glyph toggle re-renders the inline power sparks too.
- The RAM chart shrinks from 4 rows to 2 (slow-moving signal).
- The thermal/alerts status line moved out of the (scrollable) dashboard into
  fixed app chrome, via a new `AlertsComputed` message posted by
  `HardwareDashboard` and rendered by `ActopApp` — so it stays visible while a
  tall dashboard scrolls. Same string format and tokens; no export/API change.
- The first post-splash paint is deferred until after the dashboard is laid out
  (`call_after_refresh`), so width-adaptive rows (cluster summary, core grid,
  power rows) no longer flash as a single truncated character on the first frame.

## [1.3.2] - 2026-07-02

### Changed
- Docs: synced `docs/DESIGN-system.md` with the as-built LC-1/2/3 layering — §1
  now documents the L1→L2→L3 data-flow pillar and names `analytics.py` as the L2
  judgments module; §3.7 documents the `SocProfile.ane_max_w` reference field.
- Removed the completed `docs/TODO-layering-cleanup-2026-07-02.md` plan (LC-1/2/3
  all shipped in v1.2.4–v1.3.1; the as-built design now lives in `DESIGN-system.md`).

## [1.3.1] - 2026-07-02

### Changed
- Layering cleanup LC-3 — alert, throttle, and session-energy analytics moved
  out of the TUI widget into L2 (`actop.analytics`). New `AlertEngine` owns the
  per-alert sustain counters, swap-rise window, and cumulative energy integral;
  `AlertEngine.feed(snapshot)` returns an `AlertFrame` (thermal/throttle/bw/pkg/
  swap verdicts + `swap_rise_gb` + `session_energy_j`). `domain_throttling`,
  `bandwidth_percent`, and `package_power_percent` are now module-level L2
  functions taking explicit reference values. The dashboard widget's
  `_compute_alerts` shrank to formatting the frame into status-line tokens; no
  alert/throttle/energy math remains in `tui/`. No visual change.
- Session energy is now integrated over the real inter-frame dt derived from
  `SystemSnapshot.timestamp` instead of the fixed sample interval. This is a
  correctness improvement (honest elapsed time), with one behavior note: the
  **first frame contributes 0 J** (no prior timestamp to diff against), whereas
  the old fixed-interval integral counted the first frame.

## [1.3.0] - 2026-07-02

### Added
- Layering cleanup LC-2 — per-process data is now a public API type. New
  `actop.models.ProcessSample` (pid, command, cpu_percent, cpu/gpu time shares,
  rss_mb, num_threads, and watt-attributed `attributed_w`) rides on
  `SystemSnapshot.processes`. `Monitor` gained opt-in collection:
  `Monitor(include_processes=True, process_limit=50, process_filter=None)`, with
  `get_snapshot(*, include_processes=None, process_filter=...)` per-call
  overrides. Collection stays **off by default** so metrics-only API consumers
  pay no process-enumeration cost. This closes the gap where the TUI showed
  per-process power a public-API user could not obtain.
- New `actop.analytics` module (L2 domain analytics). Per-process power
  attribution (`attribute_power`) moved here from `utils.py`; watt attribution
  now happens in L2 (`api._sample_to_snapshot`) instead of at render time.

### Changed
- `MetricsUpdated` shrank to `MetricsUpdated(snapshot)` — processes now travel
  on the snapshot, completing the single-frame-contract goal begun in LC-1. The
  TUI reads typed `ProcessSample`s off `snapshot.processes`; `sort_processes`
  lost its `cpu_watts`/`gpu_watts` parameters and is pure ordering over the
  precomputed `attributed_w`. The TUI no longer imports process acquisition or
  attribution from `actop.utils`.
- `utils.py` is now single-role L1 acquisition (its docstring states the layer
  assignment); domain math lives in `actop.analytics`.
- No behavior or visual change: the process table, PWR column, `–` first-sample
  cell, and Σ-reconciliation token render exactly as before.

## [1.2.4] - 2026-07-02

### Changed
- Layering cleanup LC-1 — `SystemSnapshot` is now the single per-frame data
  contract for memory. Added `ram_total_gb`, `ram_used_percent`,
  `swap_total_gb`, and `ane_util_pct` (all defaulted, so external
  `SystemSnapshot(...)` callers are unaffected). `Monitor.get_snapshot()`
  populates them in L2, so the TUI no longer makes a second
  `get_ram_metrics_dict()` call per frame and no longer derives ANE% at render
  time — it reads the fields off the snapshot. `MetricsUpdated` dropped its
  `ram` dict argument (now `MetricsUpdated(snapshot, processes)`).
- ANE reference power moved into the SoC-profile layer: `SocProfile.ane_max_w`
  (defaulted `8.0` across M1–M4 for now — the slot for future per-generation
  refinement) flows through `get_soc_info()` into `DashboardConfig`, replacing
  the hardcoded `8.0` literal in `config.py`. `DashboardConfig` also now
  carries `chip_name` and `gpu_core_count` so the TUI reads display identity
  from the config instead of reaching into `soc_info` directly.

### Added
- Richer per-fan telemetry (max RPM): `smc.py` now probes `F{n}Mx` alongside
  `F{n}Ac` (same `flt`/size-4 guard) and exposes a structured
  `SMCReader.read_fan_info()` returning `FanReading(current, max)` per fan
  (`max` is `None` when the key is absent or reports `<= 0`). This closes the
  only remaining 2/2-converged peer gap (both `mactop` and `macmon` read the
  max-RPM key). Stays strictly read-only — no fan-control writes (`F{n}Tg`/
  `F{n}Md`), which would require root.
- `SystemSnapshot.fans: list[FanReading]` (public API / `actop.FanReading`),
  threaded through `SampleResult`. The TUI "Fan" row now renders
  `current/max` per fan (e.g. `Fan 3200/6000 · 4100/6000 RPM`), joining fans
  with `·` so the separator never collides with the within-fan `/`, and falls
  back to bare current RPM when max is unknown.

### Changed
- `SMCReader.read_fan_rpms()` (bare `list[float]`) replaced by
  `read_fan_info()`. `SystemSnapshot.fan_rpms` is retained as a derived
  current-only convenience (`[f.current for f in fans]`), so the
  NDJSON/Prometheus export contract is unchanged.

## [1.2.2] - 2026-07-01

### Added
- Fan RPM via SMC (`docs/TODO-architecture-roadmap.md` Tier-1 item): `smc.py`
  discovers per-fan actual-RPM keys (`F{n}Ac`, `flt` type, count from `FNum`)
  alongside the existing temperature-key discovery, and `SMCReader` gains
  `read_fan_rpms()` / `fan_available`. Threaded through `SampleResult` →
  `SystemSnapshot.fan_rpms` / `fan_available` (public API and NDJSON/Prometheus
  export), and a new TUI "Fan" row that hides entirely on fanless Macs
  (mirrors the `bandwidth_available` hide-row pattern) instead of showing a
  phantom 0 RPM.

## [1.2.1] - 2026-07-01

### Added
- `docs/DESIGN-system.md` §3.7: documents `soc_profiles.py`'s SoC-profile
  resolution and fallback design (exact match → generation-agnostic tier
  fallback via `APPLE_M_SERIES_PATTERN` → generic catch-all), including why a
  dynamic voltage-state-derived power estimator was considered and rejected.
- `docs/DESIGN-system.md` §1.1: records the stand-alone-binary
  (Nuitka/PyInstaller) rejection rationale — PyPI + Homebrew already cover the
  "no Python hassle" audience.
- README: a `## Python API` section with a verified `Profiler`/`to_pandas()`
  snippet — previously only mentioned in prose, no runnable example existed.
- `docs/TODO-architecture-roadmap.md`: fresh roadmap. Prior round (kernel-offset
  pinning, memory-stability guard, memory-bandwidth sampling, cross-platform
  ctypes guards, headless export) shipped in full and is retired from tracking.
  New must-have items (fan RPM via SMC, net/disk I/O via native ctypes) and a
  deferred low-priority item (menu bar mode, explicitly after first
  market-promo push per `docs/RUNBOOK-launch-and-growth.md`).
- `.agents/skills/run-actop`: documents driving the TUI via tmux send-keys/
  capture-pane for manual verification (Homebrew binary and local `.venv` dev
  build), including the sampler-init ready marker and how to confirm live
  updates vs. a static frame.
- `docs/DESIGN-system.md` §3.5: folds the DCS-bandwidth spike findings in
  directly (PMP/DCS BW group, residency-histogram semantics, channel-to-agent
  mapping, the per-agent 32 GB/s cap finding, the state-extraction cost-control
  filter); the standalone spike doc is retired.

### Fixed
- `CLAUDE.md`, `README.md`, `SECURITY.md`: removed stale `psutil` references.
  The native-polling migration (RAM/swap/process enumeration via `native_sys.py`
  ctypes) was already complete in code; the docs never caught up. Also dropped
  `CLAUDE.md`'s dead pointer to the already-deleted `TODO-native-polling.md`.
- `docs/DESIGN-system.md`: fixed two dead cross-references to already-deleted
  or about-to-be-deleted TODO files (inlined the relevant facts instead).

## [1.2.0] - 2026-07-01

### Added
- **Per-process GPU attribution** — the `PWR` column now covers GPU, not just CPU.
  A new `gpu_registry.py` module reads per-pid `accumulatedGPUTime` off each
  `AGXDeviceUserClient` via IOKit, sudoless. `utils.get_top_processes()` exposes
  a `gpu_time_share` alongside the existing `cpu_time_share`, and
  `utils.attribute_power()` combines both into the final watts value used by
  `PWR` and `SORT_POWER`. Completes Tier 2 of the feature-gap roadmap. Documented
  in `DESIGN-system.md` §5.7.
- `scripts/ane_load.py` — a CoreML-based Apple Neural Engine load generator for
  verifying that the ANE gauge reports power/percent correctly (the ANE reads
  `0% (0.0W)` when idle because it is power-gated). Builds an fp16 conv stack in
  memory, pins compute units to CPU+ANE, and loops inference.
- New `ane` optional-dependencies extra (`coremltools`, `numpy`) for the load
  generator. Kept out of the `dev` extra so Linux CI (`pip install -e ".[dev]"`)
  stays lean and unaffected.
- README: DVFS residency comparison row, a Troubleshooting FAQ entry explaining
  the expected idle `ANE 0%` reading, and a Development note documenting the
  `ane` extra + `scripts/ane_load.py`.

### Fixed
- Guarded native `ctypes` library loads in `gpu_registry.py`, `smc.py`, and
  `ioreport.py` under `sys.platform == "darwin"` (matching `native_sys.py`'s
  existing pattern) — an unguarded load in `gpu_registry.py` was crashing
  `import actop` on non-Darwin, breaking CI's `ubuntu-latest` matrix.

## [1.1.0] - 2026-07-01

### Added
- **Thermal-throttle indicator (`THROTTLING:CPU`/`:GPU`)** — the last Tier-1
  differentiator. The status line now says explicitly when a silicon domain is being
  throttled *right now*: it fires per domain (P-cluster CPU, GPU) on a "busy + slow +
  hot" rule — utilization ≥ 80% AND current frequency below
  `--alert-throttle-freq-percent`% (default 90) of the domain's DVFS max frequency AND
  thermal pressure elevated (or die temp ≥ 90°C) — sustained over
  `--alert-sustain-samples` frames. Fully read-only. Documented in the `?` help overlay.
- New `--alert-throttle-freq-percent` CLI flag (default 90).
- `SystemSnapshot` gains `ecpu_max_freq_mhz` / `pcpu_max_freq_mhz` / `gpu_max_freq_mhz`
  (the per-domain DVFS ceiling, sourced from the frequency table the sampler already
  discovers), so the throttle ratio is computable through the public API.

### Changed
- Renamed the memory-bandwidth saturation alert token from `BW>N%` to
  **`MEM-BOUND>N%`** (status line + `?` help overlay) so the indicator reads as the
  "am I memory-bandwidth-bound?" decision signal it was designed to be. No change to
  the underlying threshold, sustain logic, or `--alert-bw-sat-percent` flag.
- Docs: marked roadmap feature #2 (bandwidth as % of SoC reference + `MEM-BOUND`
  indicator) as **shipped** in `docs/TODO-actop-feature-gap-roadmap.md`, recording the
  as-built normalisation (summed CPU+GPU channel refs, not a separate
  `peak_bandwidth_gbps` field) and the token rename. Updated the matching
  `docs/DESIGN-system.md` references.
- Docs: rewrote roadmap feature #3 (thermal-throttle indicator) into an
  implementation-ready spec — locked the "busy + slow + hot" detection rule, corrected
  the max-frequency source (DVFS table, not `soc_profiles.py`), flagged the
  `throttled_count` memory-counter red herring, and enumerated the required
  `SystemSnapshot`/config/CLI plumbing and functional test.

## [1.0.4] - 2026-07-01 08:16:26

### Changed
- Documentation only (no runtime code changes):
  - Renamed `docs/GUIDE-launch-and-growth.md` to `docs/RUNBOOK-launch-and-growth.md` to
    align with the `RUNBOOK-` doc prefix convention.
  - `DESIGN-system.md`: folded in the shipped per-process power (`PWR`) feature — new
    §5.7 (attribution model, reconciliation token, P-vs-E estimate caveat), corrected
    §3.5 (per-process CPU power *is* attributed since v1.0.2; GPU/ANE/true-energy are
    not), documented the `(pid, start_tvsec)` PID-reuse guard in §2.3, and updated the
    sort-cycle (`CPU% → PWR → RSS → PID`) and the layout mock.
  - `RUNBOOK-launch-and-growth.md`: reconciled the Homebrew-notability bar with the
    design doc (was a conflicting figure), noted the repo is pre-launch, marked the
    README Quick Start done, fixed the r/LocalLLaMA `brew tap` one-liner, and refreshed
    the release-cadence note to 1.0.x.
  - Deleted `docs/TODO-t1-per-process-power.md` (the flagship feature shipped in v1.0.2;
    its as-built design now lives in `DESIGN-system.md` §5.7 and the roadmap, with the
    decision trail preserved in git/PR #11). Rewired the roadmap's inbound links.

## [1.0.3] - 2026-07-01

### Changed
- Documentation & SDLC governance only (no runtime code changes):
  - Consolidated the release runbook into `docs/DESIGN-sdlc-cicd.md` (renamed
    from `GUIDE-cicd-release.md`) as the single SDLC + CI/CD + release design doc;
    documented both PyPI publish flows (OIDC default, token-driven fallback) and the CI
    validation matrix.
  - Baked branching + versioning rules into `CLAUDE.md` and the design doc: branch from
    `main`, PR strictly into `main` (no stacked branches); patch bump per PR merge,
    minor only for milestone PRs.
  - Marked the Tier-1 per-process-power feature shipped and corrected the roadmap.

### Added
- `.github/dependabot.yml` — weekly `pip` + `github-actions` dependency updates
  (grouped minor/patch to cut PR noise).
- `.github/PULL_REQUEST_TEMPLATE.md` — PR checklist mirroring the contributor guidelines
  (validation commands, Apple-Silicon run, functional-tests attestation).

### Security
- `SECURITY.md` — private vulnerability reporting policy scoped to actop's sudoless,
  in-process posture; documented enabling GitHub secret scanning + push protection as
  the redaction backstop for clones that never activated the local hooks.

## [1.0.2] - 2026-07-01

### Added
- **Per-process power (`PWR`) column** in the process table — attributes package
  CPU power to each process by its share of total CPU-time (a partition that sums
  to the package CPU figure), the sudoless in-process answer to "which process is
  drawing the watts." Adds a `SORT_POWER` sort mode and a `Σ shown / pkg CPU`
  reconciliation token. Labelled an estimate: attribution is by wall CPU-time, so
  P-core work is under- and E-core work over-attributed vs. true watts. First
  sample / just-resumed rows render `–`, never a wrong `0.0`.

## [1.0.1] - 2026-07-01

### Fixed
- **RAM readout no longer fabricates a fallback figure.** When the native memory
  read failed, `get_native_ram` returned a hardcoded `16 GB total / 8 GB
  available`, which the dashboard rendered as if it were real. It now returns a
  zero sentinel so the UI shows a visible `0/0 GB` ("unavailable") instead of a
  plausible-but-wrong value. (No divide-by-zero: the consumer already guards
  `total > 0`.)

### Changed
- Declare `rich` as a direct dependency (it is imported directly; previously it
  was only pulled in transitively via `textual`).

### Internal
- Remove unused `DashboardConfig` fields (`usage_track_window`,
  `core_history_window`, `max_media_bw`, `proc_filter_raw`) and the write-only
  `Monitor.backend_name`; name the chart-history buffer cap
  (`_CHART_HIST_MAXLEN`) and de-duplicate the bandwidth-percent calculation. No
  behavior change.

## [1.0.0] - 2026-06-30

### Changed
- **Renamed `agtop` → `actop`** ("Apple **C**hip top"). The previous name read as "Apple **G**PU top" and undersold a whole-chip monitor (CPU/GPU/ANE/memory/power/thermal); the rename also unblocks PyPI distribution (`pip install actop`), since the `agtop` name was unavailable. This is a clean break with **no backward-compatible `agtop` command, module, or formula** — the command, Python package, import path, Homebrew formula, and Prometheus metric prefix (`agtop_*` → `actop_*`) are now all `actop`. Existing Homebrew users: `brew uninstall agtop && brew untap binlecode/agtop`, then `brew tap binlecode/actop && brew install actop`.

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
