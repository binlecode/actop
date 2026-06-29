# TODO — TUI Modernization (2026 frontier parity)

Benchmarked agtop's TUI on 2026-06-29 against the leading terminal monitors:
[macmon](https://github.com/vladkens/macmon) (direct Apple-Silicon peer),
[btop++](https://github.com/aristocratos/btop), and
[bottom](https://github.com/ClementTsang/bottom).

**Summary:** agtop's data layer is frontier-grade (unprivileged IOReport, per-core
sparklines, semantic sustained alerts, a public Python API). The presentation and
interaction layer lags the conventions that define 2026 monitors. This file tracks
closing that gap.

---

## Where agtop already matches or beats the frontier

- Unprivileged IOReport backend (no sudo) — matches macmon, ahead of asitop.
- Semantic sustained alerts (thermal / BW / swap / package power) — ahead of most peers.
- Per-core sparkline grid — btop / mactop class.
- Programmatic API (`Monitor` / `Profiler` / `AsyncMonitor` + pandas) — unique vs peers.
- Selectable chart glyphs (braille / block, `g`) — btop class.
- Gradient severity coloring — btop / bottom class.

---

## Tier 1 — high value, on-scope, building blocks already exist

- [ ] **Chart context: current → cur/avg/max.**
  Every frontier monitor shows avg + max alongside the live value; agtop labels show
  only the instantaneous number. The dashboard already retains 500-sample deques per
  metric, so avg/max are essentially free.
  - Files: `agtop/tui/widgets.py` (metric labels + cluster summary rows).
  - Example: `GPU 47% @1296MHz  avg 31 · max 88`.

- [ ] **Help / legend overlay (`?`).**
  btop / bottom / htop all have one; agtop has only the footer. Add a modal listing
  keybindings **and** what each metric and alert token means (`BW>`, `PKG>`, `SWAP+`
  are currently undocumented in-app).
  - Files: `agtop/tui/app.py` (binding + modal screen), new `tui/styles` rule.

- [ ] **Metrics export: `--json` and a Prometheus `serve` endpoint.**
  macmon's 2026 headline feature and the clearest niche gap. `SystemSnapshot` is
  already a dataclass, so `dataclasses.asdict` + a stdlib HTTP handler yields JSON and
  `/metrics`. Turns agtop from a viewer into an observability source.
  - Files: `agtop/agtop.py` (subcommands/flags), new `agtop/export.py`, reuse `api.py`.

---

## Tier 2 — medium value

- [ ] **Color degradation + `NO_COLOR`.**
  `_pct_to_color` always emits truecolor `rgb()`. On 256/16-color terminals, ttys, or
  with `NO_COLOR` set this renders poorly. Degrade gracefully across color tiers
  (btop offers truecolor / 256 / 16). Correctness + accessibility.
  - Files: `agtop/tui/widgets.py` (`_pct_to_color` and callers).

- [ ] **Themes / `c` color cycle.**
  Hardcoded blue→red. macmon (`c`, 6 themes) and btop (named themes) treat this as
  table stakes. Natural extension of the existing glyph-toggle pattern.
  - Files: `agtop/tui/widgets.py`, `agtop/tui/app.py`, `agtop/agtop.py` (`--theme`).

- [ ] **Time-axis labeling.**
  Charts have no window indicator — the visible span silently depends on terminal
  width. At minimum label the window (e.g. `60s`). Zoom (bottom-style) is a larger lift.
  - Files: `agtop/tui/widgets.py`.

- [ ] **Mouse support.**
  btop / bottom / htop are fully mouse-driven; Textual provides most of this for free
  (scroll the process list, click-to-sort columns). Lower priority for a gauge-centric
  monitor.
  - Files: `agtop/tui/app.py`.

---

## Tier 3 — frontier features that are likely intentional non-goals

Recorded for completeness; each conflicts with agtop's current scope or principles.

- [ ] **Process kill / renice / signals** (htop / btop / bottom).
  Conflicts with agtop's unprivileged, read-only stance (see CLAUDE.md). Recommend
  staying out.
- [ ] **Network / disk I/O panels** (btop / bottom / mactop).
  A scope expansion away from "SoC power monitor"; would need new native collectors
  now that psutil is gone.
- [ ] **Config-file persistence / layout presets** (btop; mactop's 20 layouts).
  Nice but a larger lift; CLI flags cover most of it today.
- [ ] **Responsive / adaptive reflow** between terminal sizes.
  Layout is currently a fixed vertical stack.

---

## Recommended order

Ship **Tier 1** as the next focused release — highest frontier-perception per unit of
effort, and all three items reuse infrastructure that already exists. Revisit Tier 2
afterward; treat Tier 3 as deliberate non-goals unless product scope changes.
