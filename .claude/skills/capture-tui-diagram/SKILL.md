---
name: capture-tui-diagram
description: Capture live actop TUI frames via tmux and embed them as faithful ASCII diagrams in the docs (DESIGN-system.md §5, README). Covers picking a width/preset for each view (grid / stack / process+filter), the poll-cycle wait that avoids stale frames, cleaning a raw capture with clean_capture.py, and splicing it into a doc via a Python replace instead of hand-transcribing braille. Use when a doc's terminal mockup is stale after a TUI/layout change.
---

# capture-tui-diagram

Refresh the ASCII diagrams in the docs from **real** actop frames instead of
hand-drawing them. Hand-drawn mockups drift from the code (wrong version, old
layout, invented labels) and can't reproduce the Braille sparklines. This skill
captures a live frame, cleans it, and splices it into the doc verbatim.

For the basics of launching/driving actop in tmux (session setup, the ready
marker, keybindings), see the **run-actop** skill — this skill builds on it and
focuses on capture → clean → splice.

## When to use

- After a TUI or layout change makes a doc diagram stale (e.g. DESIGN-system.md
  §5, README hero block).
- When you need side-by-side "here's preset A vs preset B" captures.

## 1. Pick the view and terminal size

One capture = one `tmux` session at a chosen size. Width drives which layout you
get (the grid auto-degrades to stack under ~96 dashboard cols):

| View | Command flags | Good size |
|---|---|---|
| `grid` (default) | *(none)* | `-x 132 -y 30` (dashboard-only) |
| `stack` | `--layout stack` (or press `l`) | `-x 132 -y 54` (tall — stack is ~47 rows) |
| process table + filter | `--show-processes` | `-x 132 -y 34` |

Notes:
- Keep all captures for one doc at the **same width** (132 works for all three)
  so the diagrams line up visually.
- The process table is a fixed 74 cols; at 132 the dashboard beside it is < 96,
  so it auto-degrades to `stack` — that's expected and worth showing.
- 132-wide fenced blocks scroll horizontally in rendered Markdown. Acceptable
  for a reference doc; use ~100 wide if you want no scroll and don't need the
  table.

## 2. Launch, wait for a real frame, capture

```bash
S=cap
tmux kill-session -t $S 2>/dev/null
tmux new-session -d -s $S -x 132 -y 30 '.venv/bin/python -m actop.actop'
# ready marker (see run-actop)
timeout 15 bash -c "until tmux capture-pane -t $S -p | grep -q 'P-CPU'; do sleep 0.5; done"
sleep 6          # CRITICAL: wait >= one full poll interval (default 2s) so charts
                 # and any live filter populate — capturing sooner grabs a stale
                 # or empty frame (the #1 mistake here).
tmux capture-pane -t $S -p > tmp/raw.txt
tmux kill-session -t $S 2>/dev/null
```

For an interactive **filter** frame, open it and type the pattern *literally*,
then wait a poll cycle before capturing:

```bash
tmux send-keys -t $S '/'; sleep 0.8
tmux send-keys -t $S -l 'ollama'   # -l = literal; the filter matches the FULL
                                   # command line, not the prettified name
sleep 6                            # let the next poll apply it, THEN capture
```

## 3. Clean the raw capture

`clean_capture.py` (next to this file) strips the splash spinner, trailing blank
lines, trailing pad whitespace, and scrollbar thumb artifacts:

```bash
python .claude/skills/capture-tui-diagram/clean_capture.py tmp/raw.txt > tmp/grid.txt
```

For a **single-column** capture (the `stack` preset) add `--compress` to collapse
runs of blank chart rows so a ~47-row frame fits a doc:

```bash
python .claude/skills/capture-tui-diagram/clean_capture.py tmp/stack_raw.txt --compress > tmp/stack.txt
```

**Never `--compress` a side-by-side frame** (grid, or dashboard+table): the two
columns share text lines, so dropping an interior row on one side tears the other
side's box borders. Side-by-side frames go into the doc **verbatim**.

## 4. Splice into the doc (do not hand-transcribe)

Braille + box-drawing + column alignment make manual transcription error-prone.
Replace the region between two stable markers with a small Python edit:

```bash
python - <<'PY'
import pathlib
DOC = pathlib.Path("docs/DESIGN-system.md")
grid  = pathlib.Path("tmp/grid.txt").read_text().rstrip("\n")
stack = pathlib.Path("tmp/stack.txt").read_text().rstrip("\n")
proc  = pathlib.Path("tmp/proc.txt").read_text().rstrip("\n")
block = f"## 5. TUI Layout & Rendering Engine (`tui/`)\n\n...intro...\n\n```\n{grid}\n```\n\n```\n{stack}\n```\n\n```\n{proc}\n```\n\n"
t = DOC.read_text()
a = t.index("## 5. TUI Layout & Rendering Engine")
b = t.index("### 5.1 ")          # next stable heading
DOC.write_text(t[:a] + block + t[b:])
PY
```

Then sanity-check the fences are balanced and headings survived:

```bash
grep -c '^```' docs/DESIGN-system.md         # must be even
grep -n '^## \|^### ' docs/DESIGN-system.md   # headings intact
```

## Gotchas

- **Stale frame**: always `sleep` past one poll interval after launch / after a
  keypress before capturing. This is the most common failure.
- **Filter matches the full command line**, not the displayed short name, so a
  row like `zsh` or `Visual Studio Code` can legitimately match `python|claude`
  (its argv contains the pattern). Pick a filter (e.g. `ollama`) whose matches
  read cleanly if you want an obvious diagram.
- **Alignment**: only single-column frames may be compressed; side-by-side go in
  verbatim.
- **Version in the header** reflects the *installed* dist metadata. After a
  version bump, `.venv/bin/python -m pip install -e . -q` so captures show the
  new version.
- Put all scratch captures under `tmp/` (repo convention), not the repo root.
