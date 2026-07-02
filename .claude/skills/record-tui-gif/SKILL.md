---
name: record-tui-gif
description: Record the actop TUI as an animated GIF with vhs, optionally under a real GPU workload so the gauges move (for the README hero / launch demos). Covers the vhs tape (sizing, off-camera warm-up so the first frame is already live, on-camera glyph/layout/process toggles), the ollama-router GPU driver that lights up GPU/ANE/bandwidth/power, and the record.sh orchestrator that starts the workload, records, and always stops it. Use when refreshing the hero GIF after a TUI/layout change or producing a launch capture. For a *still* ASCII diagram in the docs use capture-tui-diagram instead.
---

# record-tui-gif

Produce an animated GIF of actop for the README hero or a launch post. Motion
sells the product (a static screenshot doesn't get shared), and an *idle*
dashboard looks dead — so this skill records with **vhs** while a real GPU
workload pushes the GPU/ANE/bandwidth/power gauges.

Two related skills: **run-actop** (launch/keybindings/ready marker) and
**capture-tui-diagram** (still ASCII frames for docs). Use *this* one only for
animated GIFs.

## Prereqs (one-time)

```bash
brew install vhs        # scripts a terminal recording → reproducible, crisp GIF
brew install gifsicle   # optional: compress/inspect frames
```

vhs renders in its **own headless pty** (ttyd + headless Chrome), isolated from
your shell — so the workload must run as a separate host process, not inside the
tape. actop monitors the whole system, so that's fine.

## One-command path (recommended)

From anywhere in the repo:

```bash
bash .claude/skills/record-tui-gif/record.sh
```

This drives a GPU workload against the ollama-router, records the bundled
`actop-demo.tape` → `images/actop-demo.gif`, and **always stops the workload** on
exit (EXIT/INT/TERM trap — verified no orphans). Useful env overrides:

| Env | Default | Purpose |
|---|---|---|
| `TAPES` | bundled `actop-demo.tape` | space-separated tapes to record in one session |
| `SKIP_WORKLOAD` | `0` | `1` = record with idle gauges (no router needed) |
| `ROUTER_URL` | `http://localhost:11433` | ollama-router base URL |
| `MODEL` | `qwen3.6:35b-a3b-agentic` | model to drive |
| `CONCURRENCY` | `2` | parallel streams — match the router's backend count |
| `NUM_PREDICT` | `4096` | tokens/request; long = sustained load |
| `RAMP_SECONDS` | `8` | GPU spin-up wait before recording |

Examples:

```bash
# Heavier load, longer generations:
CONCURRENCY=3 NUM_PREDICT=8192 bash .claude/skills/record-tui-gif/record.sh
# Record a custom tape with idle gauges (e.g. a quick layout check):
SKIP_WORKLOAD=1 TAPES=tmp/mytape.tape bash .claude/skills/record-tui-gif/record.sh
```

## The GPU workload driver

`gpu_workload.py` (bundled) drives an Ollama-compatible endpoint to keep the GPU
busy. Stdlib-only (urllib), so no venv installs. It streams long generations
across N concurrent workers until Ctrl-C or `--duration`.

```bash
.venv/bin/python .claude/skills/record-tui-gif/gpu_workload.py \
    --url http://localhost:11433 --model qwen3.6:35b-a3b-agentic \
    --concurrency 2 --num-predict 4096
```

Notes:
- **Check the router first**: `(cd ~/workspace_genai/snippets-genai && .venv/bin/python llm_ollama/ollama_router.py status)` — the router fans across backends on 11434/11435, so `--concurrency 2` lights up both.
- With large `--num-predict`, the stats line can read `requests=0` for the whole
  window — that's expected: each generation runs longer than the capture, so both
  workers stay mid-generation (GPU pegged the entire time). `errors=0` is the
  health signal, not `requests`.
- **Thinking models** stream reasoning in a `thinking` field (empty `response`);
  the counter counts both. Supports `--api openai` for OpenAI-shaped routers.

## The vhs tape

`actop-demo.tape` (bundled) is the hero recipe. Key techniques:

- **Warm up off-camera** with `Hide` … `Sleep 6s` … `Show` so the **first visible
  frame already shows non-zero gauges** (the acceptance check — an idle first
  frame reads as broken). Quit (`Type "q"`) also goes under a trailing `Hide`.
- **Width 1400 @ FontSize 16 ≈ 165 cols** — well above the ~96-col floor where
  the `grid` preset auto-degrades to `stack` (see run-actop).
- **Height sizes to the layout.** `stack` is tall (~47 rows); `Set Height 1050`
  fits the stacked column + process table with **no bottom gap**. A `grid`-only
  tape uses ~`Height 900`.
- **Gapless layout switch**: do the `l` (grid→stack) switch *off-camera* — `grid`
  at stack's height leaves a large empty gap. `g` (glyph) and `t` (processes)
  toggles are safe *on-camera* because `stack` already fills the height.
- Choreography: `l` (stack, hidden) → show dots → `g` (blocks) → `t` (processes).
  The final frame — stack + block charts + per-process **watt attribution**
  (workload procs top the PWR column) — is the strongest hero frame.

### Authoring a new tape

Start from `actop-demo.tape`, change `Output`, and keep the off-camera warm-up.
Keybindings available to script (`Type "…"`): `g` glyph, `l` layout, `t`
processes, `s` sort, `p` pause, `/` filter, `?` help, `q` quit (see run-actop for
the full table). Put custom tapes under `tmp/` (repo convention).

## Verify the result

```bash
gifsicle --info images/actop-demo.gif | grep -c 'image #'   # frame count
# eyeball the FINAL frame (optimized frames need --unoptimize to extract standalone):
FR=$(gifsicle --info images/actop-demo.gif | grep -c 'image #')
gifsicle --unoptimize images/actop-demo.gif "#$((FR-1))" -o tmp/last.gif   # then Read it
```

Acceptance: first frame already live (non-zero gauges), no empty gap, nothing
clipped, seamless loop, file < ~5 MB (GitHub loads larger ones slowly). Compress
if needed: `gifsicle -O3 --lossy=80 images/actop-demo.gif -o images/actop-demo.gif`.

## Gotchas

- **ttyd `ERR_CONNECTION_REFUSED`**: under heavy GPU/CPU load ttyd can be slow to
  bind and headless Chrome connects too early. `record.sh` retries 3× per tape;
  standalone `vhs` calls just re-run.
- **Empty first frame** = didn't warm up before `Show`. Always warm up off-camera.
- **Empty bottom gap** = `grid` (or a short-history stack) at a stack-sized
  height. Do layout switches off-camera and size Height to the layout you show.
- **Sparklines look sparse** = capture window shorter than actop's history buffer
  fills; increase the on-camera `Sleep`, or let the workload run longer pre-record.
- Scratch tapes/frames go under `tmp/`, never the repo root.
