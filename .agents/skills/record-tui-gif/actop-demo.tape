# actop hero capture — the README/hero GIF.
# One continuous, gapless take in the stack layout that tells the whole story:
#   off-camera:  start actop -> l (stack layout)
#   on-camera:   stack + braille dots  ->  g (block charts)  ->  p (process table)
#                ->  t (cycle theme, several times) -> t back to dark
# Layout switch is off-camera (grid would leave a gap at this height); the glyph,
# process, and theme toggles are on-camera and stay full because stack fills the height.
# Render from the repo root:  vhs .claude/skills/record-tui-gif/actop-demo.tape
# (or via record.sh, which drives a GPU workload so the gauges move).

Output images/actop-demo.gif

Require actop

Set Shell "zsh"
Set FontSize 16
# 1400px @ FontSize 16 ≈ 165 cols — well above the ~96-col grid-degrade floor.
Set Width 1400
# Sized to the stack layout's natural column height so there's no bottom gap.
Set Height 1050
Set Theme "Dracula"
Set Padding 20
Set Framerate 24

# --- Warm up + switch to stack layout OFF-camera ---
Hide
Type "actop"
Enter
Sleep 6s
Type "l"
Sleep 1s
Show

# --- 1) stack layout, classic braille-dot charts ---
Sleep 5s

# --- 2) toggle chart glyph dots -> modern solid blocks ---
Type "g"
Sleep 5s

# --- 3) turn on the process table (needs a poll cycle to populate) ---
Type "p"
Sleep 8s

# --- 4) cycle through themes live ---
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
