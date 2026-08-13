# actop hero capture — the README/hero GIF.
# One continuous, gapless take in the DEFAULT grid layout that tells the whole
# v1.9.x story:
#   off-camera:  start actop (grid is the default — no layout switch needed)
#   on-camera:   grid + braille dots  ->  g (block charts)  ->  c (per-core panels)
#                ->  p (process table + watt attribution)  ->  t (theme cycle)
#
# Why grid rather than stack: since v1.9.0 the dashboard has seven sections, and
# grid is the only layout that shows the mirrored Network / Disk pair side by
# side without a ~57-row column. Stack would need Height ~1240 here, which
# leaves a large gap under Power once the process table is on.
#
# Width is load-bearing: `p` splits the dashboard against the process table, and
# below ~96 cols the grid preset auto-degrades to stack. At 1400px (~165 cols)
# the dashboard drops to ~80 cols and silently degrades mid-take; 1800px
# (~212 cols) leaves it ~122 cols, so grid survives the whole recording.
#
# Render from the repo root:  vhs .claude/skills/record-tui-gif/actop-demo.tape
# (or via record.sh, which drives a GPU workload so the gauges move).

Output images/actop-demo.gif

# Record the REPO build, not whatever `actop` happens to be on PATH. Typing a
# bare `actop` picks up the Homebrew/pipx install, which silently recorded a
# stale TUI (a v1.9.x hero was captured off a v1.8.0 binary before this was
# caught — no Network/Disk sections at all). Require the venv entry point.
Require .venv/bin/actop

Set Shell "zsh"
Set FontSize 16
# 1800px @ FontSize 16 ≈ 212 cols — keeps the dashboard above the 96-col
# grid-degrade floor even after `p` splits it against the process table.
Set Width 1800
# Sized to the tallest on-camera state (grid + per-core panels + process table
# ≈ 42 dashboard rows + chrome), so nothing clips and no gap opens under Power.
Set Height 930
Set Theme "Dracula"
Set Padding 20
Set Framerate 24

# --- Warm up OFF-camera so the first visible frame already has live gauges ---
Hide
Type ".venv/bin/actop"
Enter
Sleep 7s
Show

# --- 1) default grid layout, classic braille-dot charts ---
#     Seven sections: P-CPU | E-CPU, GPU·ANE | Memory, Network | Disk, Power.
Sleep 6s

# --- 2) toggle chart glyph dots -> modern solid blocks ---
Type "g"
Sleep 5s

# --- 3) reveal the per-core panels inside both cluster boxes ---
Type "c"
Sleep 5s

# --- 4) turn on the process table (needs a poll cycle to populate) ---
Type "p"
Sleep 8s

# --- 5) cycle through themes live ---
Type "t"
Sleep 2s
Type "t"
Sleep 2s
Type "t"
Sleep 2s
Type "t"
Sleep 2s
Type "t"
Sleep 2s
Type "t"
Sleep 2s
Type "t"
Sleep 2s
# Back to textual-dark (default)
Type "t"
Sleep 3s

# --- Clean exit, off-camera ---
Hide
Type "q"
Sleep 1s
