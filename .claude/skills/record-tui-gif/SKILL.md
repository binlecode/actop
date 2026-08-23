---
name: record-tui-gif
description: Record the actop TUI as an animated GIF with vhs, optionally under a real GPU workload so the gauges move (for the README hero / launch demos). Covers the vhs tape (sizing, off-camera warm-up so the first frame is already live, on-camera glyph/per-core/process-table/theme toggles), the llama.cpp / ollama GPU driver that lights up GPU/ANE/bandwidth/power, and the record.sh orchestrator that starts the workload, records, and always stops it. Use when refreshing the hero GIF after a TUI/layout change or producing a launch capture. For a *still* ASCII diagram in the docs use capture-tui-diagram instead.
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

This drives a GPU workload against the llama.cpp router (llamacpp-first default;
the ollama-router is the fully supported fallback), records the bundled
`actop-demo.tape` → `images/actop-demo.gif`, and **always stops the workload** on
exit (EXIT/INT/TERM trap — verified no orphans). Useful env overrides:

| Env | Default | Purpose |
|---|---|---|
| `TAPES` | bundled `actop-demo.tape` | space-separated tapes to record in one session |
| `SKIP_WORKLOAD` | `0` | `1` = record with idle gauges (no router needed) |
| `ROUTER_URL` | `http://localhost:9040` | llamacpp router base URL (`:9040`); ollama fallback `:11433` |
| `MODEL` | `qwen3.6:35b-a3b` | model to drive (llamacpp serves the original weights) |
| `API` | `openai` | wire protocol — llama.cpp needs `openai`; ollama uses native `ollama` |
| `CONCURRENCY` | `2` | parallel streams — match the router's backend count |
| `NUM_PREDICT` | `4096` | tokens/request; long = sustained load |
| `RAMP_SECONDS` | `8` | GPU spin-up wait before recording |

Examples:

```bash
# Default: llama.cpp router, OpenAI protocol, original qwen model:
bash .claude/skills/record-tui-gif/record.sh
# Heavier load, longer generations:
CONCURRENCY=3 NUM_PREDICT=8192 bash .claude/skills/record-tui-gif/record.sh
# Ollama fallback (native /api/generate, agentic profile):
ROUTER_URL=http://localhost:11433 MODEL=qwen3.6:35b-a3b-agentic API=ollama \
  bash .claude/skills/record-tui-gif/record.sh
# Record a custom tape with idle gauges (e.g. a quick layout check):
SKIP_WORKLOAD=1 TAPES=tmp/mytape.tape bash .claude/skills/record-tui-gif/record.sh
```

## The GPU workload driver

`gpu_workload.py` (bundled) drives an Ollama- or OpenAI-compatible endpoint to
keep the GPU busy. Stdlib-only (urllib), so no venv installs. It streams long
generations across N concurrent workers until Ctrl-C or `--duration`.

```bash
# llama.cpp (default): OpenAI protocol against the llamacpp router
.venv/bin/python .claude/skills/record-tui-gif/gpu_workload.py \
    --url http://localhost:9040 --model qwen3.6:35b-a3b \
    --api openai --concurrency 2 --num-predict 4096
# ollama fallback: native /api/generate (llama.cpp 404s on this path)
.venv/bin/python .claude/skills/record-tui-gif/gpu_workload.py \
    --url http://localhost:11433 --model qwen3.6:35b-a3b-agentic \
    --api ollama --concurrency 2 --num-predict 4096
```

Notes:
- **Check the router first**: llamacpp — `./router.sh status` in
  `~/workspace_genai/snippets-genai/llm_llamacpp`; ollama —
  `.venv/bin/python llm_ollama/ollama_router.py status`. llama.cpp only serves
  the original `qwen3.6-35b-a3b` weights (sampling tuned to the agentic profile);
  the ollama router additionally exposes the `-agentic` model tag.
- With large `--num-predict`, the stats line can read `requests=0` for the whole
  window — that's expected: each generation runs longer than the capture, so both
  workers stay mid-generation (GPU pegged the entire time). `errors=0` is the
  health signal, not `requests`.
- **Thinking models** stream reasoning in a `thinking` field (empty `response`);
  the counter counts both. Supports `--api openai` for OpenAI-shaped routers.

## The vhs tape

`actop-demo.tape` (bundled) is the hero recipe. Key techniques:

- **Warm up off-camera** with `Hide` … `Sleep` … `Show` so the **first visible
  frame already shows non-zero gauges** (the acceptance check — an idle first
  frame reads as broken). The sleep must cover the whole **chart span**, not just
  "a few seconds": the sparklines are right-aligned across the full chart width,
  so a short warm-up shows a thin trace clinging to the right edge and still
  reads as a dashboard that just booted. At the 1s default interval the window is
  ~328 samples (`span 5m28s`), so the tape sleeps **330s** — 7s fills 2% of the
  chart and 40s only 12%. Quit (`Type "q"`) also goes under a trailing `Hide`.
- **Width 1800 @ FontSize 16 ≈ 212 cols.** Width is load-bearing because `p`
  splits the dashboard against the process table: at 1400px (~165 cols) the
  dashboard drops to ~80 cols and **silently degrades to stack mid-take**. 1800px
  leaves it ~122 cols, so `grid` survives the whole recording. Do not lower it.
- **Height sizes to the layout, and the layout is fixed for the take.**
  `Set Height 930` fits the tallest on-camera grid state (grid + per-core panels
  + process table ≈ 42 dashboard rows + chrome). `stack` at this width would need
  ~`Height 1240`.
- **Do NOT press `l` on camera.** The hero take stays in `grid` start to finish —
  grid is the default, so no layout switch is needed at all. Height is sized for
  grid, so a mid-take switch to `stack` clips or opens a large gap under Power.
  (Since v1.9.x `l` also raises a toast, which would land in the frame.) A
  stack-based tape is a *separate* tape with its own `Height`, not an `l` press
  bolted onto this one.
- Choreography (all on-camera, gapless): dots → `g` (block charts) → `c`
  (per-core panels) → `p` (process table + watt attribution) → `t` ×8 (theme
  cycle, ending back at the `textual-dark` default). The process-table frame —
  grid + block charts + per-process **watt attribution** (workload procs top the
  PWR column) — is the strongest hero frame.

### Authoring a new tape

Start from `actop-demo.tape`, change `Output`, and keep the off-camera warm-up.
Keybindings available to script (`Type "…"`): `g` glyph, `l` layout, `c` per-core
panels, `p` process table, `s` sort, `t` theme, `space` pause, `/` filter, `?`
help, `q` quit (see run-actop for the full table). Put custom tapes under `tmp/`
(repo convention).

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
