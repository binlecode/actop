---
name: run-actop
description: Launch and drive the actop TUI dashboard (Textual-based Apple Silicon performance monitor) via tmux send-keys/capture-pane. Covers the Homebrew-installed binary and the local dev .venv build, keybindings, the sampler-init ready marker, and how to confirm gauges/charts/process-table update live rather than just rendering a static frame.
---

# run-actop

actop is a Textual TUI — it takes over the terminal, so drive it inside
tmux rather than calling it directly with the Bash tool.

## Run (direct, for humans)

Homebrew install:

```bash
actop --show-processes
```

Local dev build (from repo root, uses the `.venv`):

```bash
.venv/bin/python -m actop.actop --show-processes
```

Press `q` to quit.

## Run (interactive, for agents)

Start inside a detached tmux session at a size wide enough for the
two-column layout (dashboard + process table):

```bash
tmux new-session -d -s actop_verify -x 200 -y 55 'actop --show-processes'
```

To exercise a local dev build instead of the installed binary, swap the
command: `'.venv/bin/python -m actop.actop --show-processes'` (run from
the repo root so the venv path resolves).

Poll for ready rather than a fixed sleep — sampler init takes ~2-3s and
shows a splash screen ("Initializing sampler…") until the dashboard
renders:

```bash
timeout 15 bash -c 'until tmux capture-pane -t actop_verify -p | grep -q "PWR\|E-CPU\|P-CPU"; do sleep 0.5; done'
tmux capture-pane -t actop_verify -p
```

Confirm it's actually live (not a frozen frame) — the session-energy
accumulator in the status line only increases while sampling runs:

```bash
tmux capture-pane -t actop_verify -p | grep -oE 'energy [0-9]+mWh'
sleep 6
tmux capture-pane -t actop_verify -p | grep -oE 'energy [0-9]+mWh'   # value should have increased
```

Exercise interactivity:

```bash
tmux send-keys -t actop_verify 's'   # cycle sort mode; the active sort column gets a leading *
tmux send-keys -t actop_verify 'p'   # toggle pause
tmux send-keys -t actop_verify 't'   # toggle the process table panel
tmux send-keys -t actop_verify '?'   # help overlay
tmux capture-pane -t actop_verify -p
```

Quit:

```bash
tmux send-keys -t actop_verify 'q'
tmux kill-session -t actop_verify 2>/dev/null || true
```

### Key reference

| Key | Action |
|---|---|
| `q` | Quit |
| `p` | Pause / resume sampling |
| `s` | Cycle sort mode (CPU% / PWR / Memory / PID) |
| `g` | Cycle chart glyph style (dots / block) |
| `l` | Cycle layout preset (grid ⇄ stack) |
| `t` | Toggle process table panel |
| `/` | Filter processes by regex |
| `?` | Help overlay |

### Useful launch flags

| Flag | Effect |
|---|---|
| `--show-processes` | Show the process table panel at startup (off by default) |
| `--no-show-residency` | Hide the per-cluster DVFS residency distribution rows (on by default) |
| `--interval N` | Display/sampling interval in seconds |
| `--power-scale {auto,profile}` | Power chart scaling mode |
| `--chart-glyph {dots,block}` | Chart glyph style |
| `--layout {grid,stack}` | Dashboard layout preset (default `grid`; cycle live with `l`) |

## What "working" looks like

- No crash/traceback; the splash screen is replaced by live gauges within ~3s.
- Four titled sections render: `CPU`, `GPU · ANE`, `Memory`, `Power` (titles sit in
  the section borders). In `grid` (default) they form two columns — CPU spanning the
  left, the other three stacked on the right; in `stack` (`l` or `--layout stack`)
  they are one full-width scrolling column. The thermal/alerts status bar stays fixed
  at the bottom in both.
- CPU/GPU/ANE utilization, per-core panels, RAM, Mem BW, and CPU/GPU/Package power
  sparklines are populated with non-placeholder values.
- DVFS residency bars render for P-CPU/E-CPU/GPU, e.g.:
  `P-CPU  [░░░░░░░░░░░░░░░░]  idle97 low1 mid2 high0`.
- With `--show-processes`: the process table's `PWR` column is populated per row, and
  the border subtitle reads `Σ shown N.NW / pkg CPU+GPU M.MW · est CPU+GPU time share`
  (combined CPU+GPU attribution, shipped v1.2.0).
- The session-energy accumulator (`energy NmWh`) increases across successive polls.

## Notes

- Terminal size: for the `grid` preset alone use at least `-x 100`; with the process
  table also shown (`t` / `--show-processes`) use `-x 172` or wider (the table is a
  fixed 74-col panel, so the dashboard's two columns need the rest). Below ~96 cols a
  requested `grid` auto-degrades to `stack` — expect a single full-width column there,
  not a bug. `-y 40` comfortably fits either preset; `grid` fits ~30 rows.
- No special environment setup, packages, or patches are needed on macOS
  (Apple Silicon) — actop is unprivileged, no sudo, no subprocess dependency.
