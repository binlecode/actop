# TODO: TUI Layout Redesign — Sectioned Presets (grid / stack)

Status: planned · Owner: TBD · Created: 2026-07-02
Scope: `actop/tui/` + CLI/config plumbing + docs/tests. No sampler/API changes.

---

## 1. Problem

A live capture of v1.2.x at 200×55 (`--show-processes`) shows five defects:

1. **One undifferentiated stack.** 13 metric rows (P-CPU → Fan) run together
   inside a single border with no visual grouping; CPU, GPU, memory, and power
   data blend into one wall.
2. **Height overflow.** The dashboard needs ~46–48 rows on an M4 Max (16
   cores); on a typical 40-row terminal it silently scrolls (`overflow-y:
   auto`) — hostile for a monitor whose job is at-a-glance state.
3. **Wasted width.** The 50/50 `Horizontal` split gives the process table
   ~100 cols but its 6 columns use ~60, leaving a dead right third. Meanwhile
   three separate power chart blocks (CPU / GPU / Package = 9 rows) plot
   largely redundant curves.
4. **Buried status.** thermal/alerts — the most decision-relevant signal — is
   the *last* line inside the scrollable dashboard.
5. **Blank-chart noise.** Near-idle ANE / GPU-power charts spend 2–4 rows each
   rendering mostly empty space.

## 2. Design (btop-derived)

Reviewed btop's layout system; three ideas adopted:

- **Boxes are first-class units** (btop `shown_boxes`): the dashboard becomes
  four titled *section containers* — `CPU`, `GPU · ANE`, `Memory`, `Power`.
- **Presets are arrangements of the same boxes** (btop `presets`, cycled with
  one key): actop ships two presets sharing identical section widgets, data
  flow, and rendering code — only container CSS differs:
  - **`grid`** (default): two-column grid; CPU section spans the full left
    column, GPU·ANE / Memory / Power stack in the right column. ~25 rows.
  - **`stack`**: all sections full-width in a single scrollable column —
    longest chart history span. ~47 rows.
- **Titles live in the border** (zero row cost): Textual `border_title` on
  each section, replacing both the outer dashboard border and any title rows.

Also adopted: status promoted out of the scrollable area into fixed app
chrome (full-width bar, visible in both presets).

Not adopted (recorded so nobody re-litigates): btop's full-width-CPU-on-top
default (actop's CPU section is tall-narrow: 2 clusters + up to 16 cores);
per-preset graph glyphs (global `g` toggle already covers it); btop-style
instantaneous block meters on summary rows (follow-up, §12).

### 2.1 Target: `grid` preset (default), table hidden

```
 actop  v1.4.0 · M4 Max · 4E+12P+40GPU                          07:58:47
╭─ CPU ────────────────────────────────╮╭─ GPU · ANE ────────────────────────╮
│ P-CPU  1% @2229MHz (78°C) avg 8% …   ││ GPU 14% @338MHz (50°C) avg 13% …   │
│ <BrailleChart 4 rows>                ││ <BrailleChart 2 rows>              │
│ P00  8% ⡀⡄⡀ │ P01  2% ⡀⡀⡀           ││ GPU  [░░░░░░░▒▒] idle85 low15 …    │
│ …core grid (2-col, N/2 rows)…        ││ ANE 0% (0.0W) avg 0% · max 0%      │
│ P-CPU [░░░░░░░░░] idle98 …           ││ <BrailleChart 2 rows>              │
│ E-CPU 17% @607MHz (78°C) avg 21% …   │╰────────────────────────────────────╯
│ <BrailleChart 4 rows>                │╭─ Memory ───────────────────────────╮
│ E00 28% ⡀⡄⡀ │ E01 19% ⡀⡀⡀           ││ RAM 102/128GB sw:6.1/7.0GB avg 79% │
│ E02 15% ⡀⡀⡀ │ E03  9% ⡀⡀⡀           ││ <BrailleChart 2 rows>              │
│ E-CPU [░░░░░▒▓▓] idle82 …            ││ Mem BW 32.5 GB/s avg 35.4 · max …  │
╰──────────────────────────────────────╯│ <BrailleChart 2 rows>              │
                                        ╰────────────────────────────────────╯
                                        ╭─ Power ────────────────────────────╮
                                        │ CPU 6.59W ⡀⡄⡆⡀⡀⡄  avg 4.3W · max… │
                                        │ GPU 0.35W ⡀⡀⡀⡀⡀⡀  avg 0.2W · max… │
                                        │ Package 6.95W  avg 4.5W · max 16.8W│
                                        │ <BrailleChart 2 rows>              │
                                        │ Fan 1348/1454 RPM                  │
                                        ╰────────────────────────────────────╯
 span 1m36s · energy 0.12Wh · thermal: Nominal · alerts: none
 q Quit  p Pause  s Sort  g Glyph  l Layout  t Processes  / Filter  ? Help
```

With `t` (process table shown): the table appears as a third, fixed-width
(~74-col) panel on the right; the dashboard keeps its two columns and absorbs
remaining width.

### 2.2 Target: `stack` preset

Same four bordered sections stacked full-width in a single scrollable column
(CPU → GPU·ANE → Memory → Power). Charts get full dashboard width (longest
history span). The status bar stays fixed at app level, so alerts remain
visible even while the stack scrolls.

## 3. Height & width budget

Rows include each section's 2 border rows. Core grid rows = ⌈cores/2⌉.

| Section | Contents | M4 Max (12P+4E) | Base (4P+4E) |
|---|---|---|---|
| CPU | 2×(summary 1 + chart 4 + cores + residency 1) | 12 + 8 + 2 = 22 | 8 + 8 + 2 = 18 |
| GPU · ANE | GPU label 1 + chart 2 + residency 1 + ANE label 1 + chart 2 | 9 | 9 |
| Memory | RAM label 1 + chart 2 + BW label 1 + chart 2 | 8 | 8 |
| Power | CPU row 1 + GPU row 1 + PKG label 1 + PKG chart 2 + Fan 1 | 8 | 8 |

- **grid**: `max(CPU, GPU·ANE + Memory + Power)` = max(22, 25) = **25 rows**
  → + header 1 + status 1 + footer 1 = 28. Fits a 30-row terminal.
- **stack**: 22 + 9 + 8 + 8 = **47 rows** → scrolls below ~50; acceptable by
  design (status bar no longer scrolls with it).
- **grid min width**: each column needs ≥ ~48 cols → dashboard ≥ **96 cols**.
  Below that the grid *auto-degrades* to stack (§5.4). With the process table
  (74 cols) shown, grid therefore needs a ≥ ~172-col terminal; narrower
  terminals with the table shown render the dashboard as stack.

Changes vs today baked into this budget: RAM chart 4→2 rows (slow-moving
signal); CPU/GPU power charts (2×3 rows) → single-line rows with inline
sparklines; Package Power keeps the only power chart; ANE/GPU/BW charts stay
2 rows. Net dashboard content: ~46 → ~41 rows stacked, ~23 in grid.

## 4. Public surface changes

| Surface | Change |
|---|---|
| CLI | `--layout {grid,stack}`, default `grid` (`build_parser()` in `actop/actop.py`) |
| Config | `DashboardConfig.layout: str` new field; merged in `create_dashboard_config` via `getattr(args, "layout", "grid")` |
| Keybinding | `l` — cycle layout preset (grid ⇄ stack); shown in footer as `Layout`; documented in `HELP_TEXT` |
| Widget API | `HardwareDashboard.layout_preset` (read) and `set_layout_preset(name: str)` (public — tests drive it); invalid names raise `ValueError` |
| Messages | New `AlertsComputed(Message)` posted by `HardwareDashboard` each frame carrying the formatted status string; `ActopApp` handles it and updates the app-level status bar |
| Removed | `#cpupwr-chart`, `#gpupwr-chart` widgets; `#status-line` moves from dashboard subtree to app level (same id, same string format — export/API surfaces untouched) |

No changes to `actop/api.py`, `actop/sampler.py`, `SystemSnapshot`, export
formats, or any metric computation.

## 5. Implementation detail, by file

### 5.1 `actop/tui/widgets.py`

1. **Sectionize `HardwareDashboard.compose()`.** Wrap existing children in
   four `Vertical` containers, ids `#section-cpu`, `#section-gpu-ane`,
   `#section-memory`, `#section-power`, all with class `dash-section` and
   `border_title` set to `CPU`, `GPU · ANE`, `Memory`, `Power`. **The CPU
   section already exists as `#cpu-section`** (it wraps the two `.cpu-half`
   verticals); rename it to `#section-cpu` (and its CSS rule, §5.1.2) rather
   than nesting a second container — the other three sections are new. Keep
   every existing child widget id unchanged so `update_metrics` query paths
   keep working. `#status-line` is removed from compose (moves to app, §5.2).
2. **Preset CSS.** Note: today *all* dashboard CSS lives in
   `ActopApp.DEFAULT_CSS` (`app.py`) — `HardwareDashboard` has no
   `DEFAULT_CSS` of its own. This step introduces one, and must **move** the
   existing dashboard rules out of `app.py` into it: the outer
   `HardwareDashboard {border/padding}`, `#pcpu-chart`/`#ecpu-chart`/`#ram-chart`
   heights, `#cpu-section` (→ `#section-cpu`), and `.cpu-half`. Add:

   ```css
   HardwareDashboard { padding: 0; }           /* outer border removed */
   .dash-section { border: round $accent; padding: 0 1; height: auto; }
   HardwareDashboard.layout-grid {
       layout: grid;
       grid-size: 2;
       grid-columns: 1fr 1fr;
       grid-rows: auto auto auto;
   }
   HardwareDashboard.layout-grid #section-cpu { row-span: 3; }
   HardwareDashboard.layout-stack { layout: vertical; overflow-y: auto; }
   ```

   `set_layout_preset(name)` swaps the `layout-grid`/`layout-stack` class;
   constructor applies the preset from `config.layout`.
3. **Auto-degrade** (§3): store the *requested* preset; in `on_resize`, if
   requested == `grid` and `self.size.width < _GRID_MIN_WIDTH` (constant, 96),
   apply the stack class; restore grid when width recovers. `layout_preset`
   reports the requested preset; add `effective_layout_preset` for the
   applied one (public, read-only — tests and the footer/status can show it).
4. **Power section compaction.** Replace the CPU-power and GPU-power
   label+chart pairs with single `Static` rows (`#cpupwr-row`, `#gpupwr-row`)
   rendered as `CPU {:.2f}W {spark}  avg … · max …` where `{spark}` =
   `_inline_spark(self._cpupwr_hist, width_chars=w, glyph_mode=self._chart_glyph)`;
   compute `w` from the row's available width the way `_format_core_entry`
   does (min 8, cap 24). Keep `_cpupwr_hist`/`_gpupwr_hist` percent deques and
   the watt-unit deques exactly as-is (they feed sparks + avg/max). Package
   Power keeps its label + 2-row `BrailleChart`. Update the `chart_data`
   tuple in `update_metrics` (drop the two removed chart ids). `set_chart_glyph`
   must re-render the two power rows (same as it does for core grids).
5. **RAM chart** `#ram-chart` height 4 → 2 (CSS only; the rule currently lives
   in `ActopApp.DEFAULT_CSS` and moves into `HardwareDashboard.DEFAULT_CSS`
   per item 2).
6. **Alerts → message.** As of LC-3 (v1.3.1) `_compute_alerts` is already a
   thin formatter: it calls `self._alert_engine.feed(s)` (L2
   `analytics.AlertEngine` → `AlertFrame`) and turns the frame into the status
   string. This step only redirects that string — end `_compute_alerts` with
   `self.post_message(AlertsComputed(status))` instead of
   `query_one("#status-line")`. Define `AlertsComputed` next to
   `MetricsUpdated`. (No alert/throttle/energy math to relocate — LC-3 already
   moved it to L2; this is a pure formatter move.)
7. Keep: residency rows, core grids, BW/fan availability gating (rows hide
   inside their sections; `height: auto` absorbs it), splash flow, span/energy
   tokens (span still derives from `#gpu-chart` width — correct per-preset by
   construction).

### 5.2 `actop/tui/app.py`

1. **Compose**: `yield Static(..., id="status-line")` between
   `#main-section` and the filter `Input` (fixed `height: 1`, full width).
   Add `on_alerts_computed` handler updating it.
2. **Binding**: `("l", "cycle_layout", "Layout")`; `action_cycle_layout`
   flips the dashboard's requested preset via `set_layout_preset`.
3. **CSS**: move dashboard-border styling out (now on sections); add
   `#process-table { width: 74; }` (fixed; columns total ≈ 70 + zebra
   padding; commands already truncate at 28 via `_process_display_name`).
   Dashboard keeps `width: 1fr` and absorbs leftover width.
4. **HELP_TEXT**: add `l` key line + one paragraph on presets ("grid: two
   columns, fits short terminals; stack: single column, longest chart
   history; grid auto-falls-back to stack under ~96 cols").
5. `check_action` unchanged (`/` still gated on the table).

### 5.3 `actop/actop.py` + `actop/config.py`

- `build_parser()`: `--layout`, `choices=("grid", "stack")`, `default="grid"`,
  help text mirroring HELP_TEXT phrasing. TUI-only (ignored by `--export`,
  like other display flags).
- `DashboardConfig`: add `layout: str`; `create_dashboard_config` passes
  `getattr(args, "layout", "grid")`.

### 5.4 Behavior contract (both presets)

- Identical metric values, identical update cadence, identical alert logic.
- `g` glyph toggle applies to all `BrailleChart`s *and* the inline power/core
  sparks in either preset.
- BW-unavailable and fanless machines: rows hidden, sections shrink; grid row
  heights are `auto` so no gaps.
- `--no-show-cores` / `--no-show-residency`: sections shrink accordingly.
- Preset changes never touch history deques — switching layouts mid-session
  loses no data.

## 6. Delivery plan — two sequential PRs (never stacked)

Both branch from `main` after the prior one merges (repo rule: no stacked
PRs). Each PR bumps version + CHANGELOG.

### PR1 — "Sectioned dashboard" (patch, v1.3.3)

Realizes the tidy-up on the existing single-column layout:
- §5.1 items 1, 4, 5, 6, 7 (sections + border titles, power compaction, RAM
  chart trim, AlertsComputed) with only the `layout-stack` arrangement.
- §5.2 items 1 (status bar) and 4 (HELP_TEXT metric-label updates).
- All test updates for moved/removed widgets (§7).
- Ships alone as a strict visual improvement; default look = tidied stack.

### PR2 — "Layout presets" (minor, v1.4.0 — milestone)

- §5.1 items 2, 3 (grid CSS, `set_layout_preset`, auto-degrade).
- §5.2 items 2, 3 (`l` binding, table width cap).
- §5.3 (`--layout` flag + config field).
- Default flips to `grid`. Docs sync (§9), README screenshot regenerated.

## 7. Test plan

Repo mandate: **functional tests only** — public/runtime entrypoints, no
private attrs, no mocks/fakes; a minimal Textual host `App` used purely as a
mount point is allowed. Follow the existing patterns:

- `tests/test_cli_contract.py`: `--layout` default is `grid`; `--layout stack`
  parses; `--layout foo` exits non-zero. (Pattern: `build_parser().parse_args`.)
- `tests/test_config.py`: `create_dashboard_config` maps `args.layout`;
  defaults to `grid` when the attr is absent.
- `tests/test_dashboard_metrics.py` (existing `_Host` mount pattern, real
  `SystemSnapshot`s through `update_metrics`):
  - Feed N snapshots in `grid`, call `set_layout_preset("stack")`, feed more:
    labels/charts keep updating in both presets; avg/max continuity holds
    across the switch (no history loss).
  - Power rows: after a snapshot with known `cpu_watts`/`gpu_watts`, the
    `#cpupwr-row`/`#gpupwr-row` rendered text contains the wattage and spark
    glyphs; Package chart still receives data.
  - `AlertsComputed` is received by the host app with the expected
    `thermal:`/`alerts:` tokens for a thermally-pressured snapshot (this
    replaces any status-line query previously aimed inside the dashboard).
  - Auto-degrade: `run_test(size=(80, 40))` with `grid` requested →
    `effective_layout_preset == "stack"`; resize wide → back to `grid`.
  - `set_layout_preset("bogus")` raises `ValueError`.
- `tests/test_tui_app.py`: `l` present in `BINDINGS`; pressing `l` toggles the
  dashboard's requested preset (via the public `layout_preset` property).
  ActopApp-mounting tests stay `@pytest.mark.local` (real SoC info), matching
  `test_per_process_power.py` — CI runs `-m "not local"`.
- Sweep for tests asserting the old structure (`#cpupwr-chart`,
  `#status-line` inside the dashboard): rewrite against the new public
  surface; delete any that were structural-only rather than porting them.

## 8. Manual verification runbook (per PR, on Apple Silicon)

Use the `run-actop` skill flow (tmux). Matrix:

| Size | Preset | Checks |
|---|---|---|
| 200×55 | grid + `t` | 3 panels; 4 titled sections; no dashboard scroll; table ≈74 cols; Σ subtitle intact |
| 200×55 | stack (`l`) | sections full-width; scrolls; status bar fixed while scrolling |
| 120×40 | grid, no table | 2 columns fit; no overflow |
| 120×40 | grid + `t` | dashboard auto-degrades to stack (width < 96) |
| 100×30 | grid | fits vertically (~28 rows) |

Each cell: `g` toggles glyphs everywhere (incl. inline power sparks), `?`
help lists `l`, `energy NmWh` strictly increases across two captures, `p`
pause freezes it, no traceback on `q`.

Pre-PR checklist (repo standard): `.venv/bin/ruff check --fix . &&
.venv/bin/ruff format .`, `.venv/bin/pytest -q`,
`.venv/bin/python -m actop.actop --help`, live run confirming gauges update.

## 9. Docs to sync (PR2 unless noted)

- `README.md`: features bullet (presets), key table (`l`), flags table
  (`--layout`), module table, regenerated screenshot/capture.
- `CLAUDE.md`: module table (sections, app-level status bar), data-flow
  sentence (`AlertsComputed`).
- `docs/DESIGN-system.md`: TUI rendering section — sections, presets,
  auto-degrade threshold, power-row compaction (PR1 parts land with PR1).
- `docs/REVIEW-tui-frameworks.md` §5 "As Built": single-view claim →
  preset-based layout.
- `.claude/skills/run-actop/SKILL.md`: key table (`l`), `--layout` flag,
  revised min-size guidance (grid+table ≥ ~172 cols; grid-only ≥ ~100),
  "what working looks like" (4 titled sections).
- `CHANGELOG.md`: per PR (patch / minor).

## 10. Risks & mitigations

| Risk | Mitigation |
|---|---|
| `row-span` / grid behavior at the `textual>=0.60` floor pin | Verify against 0.60 in the venv before PR2; if broken, raise the floor to the earliest working release (note in CHANGELOG) — dev env runs 8.2.8 |
| Grid halves chart history span vs today | Deliberate trade; `span` token in the status bar keeps it honest; `stack` preset preserves the long view |
| Fixed 74-col table clips a future extra column | Constant lives in one CSS rule; revisit if columns change |
| Width-adaptive code (`_update_cluster_summary_row`, core grid, span label) misbehaves at ~48-col columns | All already clamp/truncate; covered by the 120×40 grid cell in §8 |
| Status-line move breaks a hidden consumer | `grep -rn "status-line"` across repo + tests during PR1; id and string format are preserved |

## 11. Acceptance criteria

1. Default launch (`grid`) shows four titled sections, two columns, no
   dashboard scrolling at ≥30-row terminals, status bar fixed at bottom.
2. `l` and `--layout` switch presets live with zero data loss; help/footer
   document them.
3. Dashboard auto-degrades grid→stack below 96 cols and recovers.
4. Power section is 8 rows incl. borders (was 13 content rows + fan);
   CPU/GPU power readable as inline rows; package chart intact.
5. `.venv/bin/pytest -q` green locally; `-m "not local"` green (CI parity);
   ruff clean.
6. §8 matrix passes on Apple Silicon; captures attached to PRs (UI-visible
   change ⇒ terminal capture required by repo PR guidelines).

## 12. Out of scope / follow-ups

- btop-style instantaneous block meters on summary rows.
- Per-section show/hide keys (btop's `1`–`4`) — sectionization makes this
  cheap later.
- User-defined presets in a config file; persisting the last-used preset.
- Process-table column additions (e.g. GPU-share) enabled by the width cap.
