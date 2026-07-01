# Launch & Growth Runbook

Your remaining, human-only work to give actop its best shot at adoption — and the
star/notability base that a future homebrew-core submission needs (self-submission
bar: ≥225 stars **or** ≥90 forks **or** ≥90 watchers; see `docs/DESIGN-sdlc-cicd-release.md`
context). Do the steps in order; the launch posts at the end are ready to paste.

> Note: this file is intentionally not part of the shipped product. Keep it private,
> or delete it before it ends up in a release if you'd rather not publish growth notes.

---

## Step 1 — Record the hero capture (the single highest-leverage task)

A static screenshot doesn't get shared; **motion does.** Goal: a 6–12s loop of the
dashboard live, ideally with a real workload pushing GPU/ANE/bandwidth.

**Recommended tool: [`vhs`](https://github.com/charmbracelet/vhs)** — scripts a
terminal recording to a GIF, reproducible and crisp (much better than a screen grab).

```shell
brew install vhs
```

1. Start a workload in another pane so the gauges actually move — e.g. an MLX or
   Ollama inference loop, or a `llama.cpp` run. (A busy machine sells the bandwidth /
   ANE / power story; an idle one looks dead.)
2. Create `tmp/actop-demo.tape`:
   ```
   Output images/actop-demo.gif
   Set FontSize 16
   Set Width 1400
   Set Height 900
   Set Theme "Dracula"
   Type "actop"
   Enter
   Sleep 10s
   ```
3. Render: `vhs tmp/actop-demo.tape`
4. If the GIF is large (>4–5 MB it loads slowly on GitHub), compress with
   [`gifsicle`](https://www.lcdf.org/gifsicle/): `gifsicle -O3 --lossy=80 images/actop-demo.gif -o images/actop-demo.gif`

**Alternatives:** `asciinema rec` + [`agg`](https://github.com/asciinema/agg) (→ GIF),
or QuickTime screen recording + [`gifski`](https://gif.ski/). vhs is preferred because
it's reproducible and you can re-render on every UI change.

**Acceptance check:** the first frame should already look alive (gauges non-zero), the
loop should be seamless, and the file should be < ~5 MB.

---

## Step 2 — Wire the GIF into the README

The README hero already has a `TODO` placeholder comment marking the spot.

1. Drop the file at `images/actop-demo.gif`.
2. Replace the static image line (the one tagged with the `<!-- TODO ... -->` comment)
   so the GIF is the hero; keep the static `images/actop.png` lower in the README (or
   in a "Screenshots" subsection) as a still fallback.
3. Keep the descriptive alt text — it's read by screen readers and indexed by search.
4. Commit: `git add images/actop-demo.gif README.md && git commit -m "docs: add animated hero capture"`
   then push.

---

## Step 3 — Pre-launch polish (½ day, do before posting anywhere)

These are the things a first-time visitor judges in 10 seconds:

- [ ] **GIF is the hero** (Step 2 done).
- [ ] **One-line install works from a clean machine** — actually test the Homebrew and
      `uv` one-liners on a fresh shell / another Mac. Broken install = lost star.
- [ ] **Repo description + topics** — already set (apple-silicon, mlx, llm, ollama, …);
      confirm they still read well.
- [ ] **A short "Quickstart"** near the top: install → `actop` → the 3 keys that matter
      (`t`, `/`, `?`). Lower the time-to-first-success.
- [ ] **A `Profiler` code snippet** in the README (5–8 lines) — the Python API is your
      differentiator; show it, don't just claim it. `Monitor`/`Profiler` → `to_pandas()`.
- [ ] **License + Background credit to asitop** — already present; keep it (good faith
      with the upstream community matters when they see your launch).
- [ ] **Issues enabled, a CONTRIBUTING note or at least a "PRs welcome" line.**

---

## Step 4 — Pick the launch window & sequence

Traffic comes from *off*-README channels; the README only converts it. Don't post
everywhere the same minute — stagger so you can fix anything that breaks.

Ordering (highest-fit audience first, so early feedback is friendly):

1. **r/LocalLLaMA** (Reddit) — best audience fit; they care about exactly your metrics.
2. **Hacker News — "Show HN"** — post Tue–Thu, ~8–10am US Eastern (weekday morning
   gets the most daytime traffic). Title format below. Add a first comment with
   context. Do **not** ask for upvotes (against HN rules).
3. **X/Twitter / Mastodon / Bluesky** — a thread with the GIF; tag the MLX / local-LLM
   community.
4. **r/macapps, r/apple** (smaller fit, do after the LLM crowd).
5. **Lobsters** (if you have an invite), **awesome-* lists** PRs (awesome-macos,
   awesome-apple-silicon), and **dev.to / a short blog post** for long-tail SEO.

On launch day: be present for the first ~3 hours to answer every comment fast —
responsiveness is the strongest signal that converts a visitor into a star/contributor.

**Force-multiplier:** find one or two **YouTubers/bloggers who benchmark LLMs on Macs**
and send a short, no-ask note ("built this; might make your benchmarking videos
easier"). One mention can outweigh every other channel combined.

---

## Step 5 — Launch post drafts (paste-ready; tweak the bracketed bits)

### A) Hacker News — Show HN

**Title:**
```
Show HN: actop – sudoless Apple Silicon monitor with a Python profiling API
```

**First comment (post immediately after submitting):**
```
I run a lot of local LLM inference on my Mac (MLX / Ollama / llama.cpp) and kept
wanting to know whether I was GPU-bound, memory-bandwidth-bound, or leaving the ANE
idle — without running powermetrics under sudo.

actop reads Apple Silicon power/frequency/residency in-process via ctypes bindings to
libIOReport.dylib (the same library powermetrics uses internally), so it runs
unprivileged, with no subprocesses or temp files. It shows per-core frequency, GPU/ANE
utilization, memory bandwidth, package power, and die temps in a Textual TUI.

The part I actually built it for: a Python API (Monitor / Profiler, to_pandas()) so I
can wrap my own workloads and get a dataframe of power/frequency/energy — SoC-accurate
power scaling against M1–M4 reference profiles, plus cumulative session energy.

It's an independent rewrite inspired by asitop. mactop (Go) and macmon (Rust) are great
sudoless TUIs too; actop's niche is being the programmable, Python-native one.

Feedback very welcome — especially on the metric accuracy and the API shape.
```

### B) r/LocalLLaMA

**Title:**
```
actop: a sudoless Apple Silicon monitor that shows if your LLM run is GPU- or memory-bandwidth-bound (+ a Python profiling API)
```

**Body:**
```
When running models locally on a Mac (MLX, llama.cpp, Ollama), I wanted a fast read on
the bottleneck: is the GPU pegged? am I memory-bandwidth-bound (the usual ceiling for
inference)? is the ANE doing anything? what's package power and energy per run?

actop is a small TUI that shows all of that — per-core CPU frequency, GPU/ANE
utilization, **memory bandwidth in GB/s**, power, and die temps — and it runs **without
sudo**. It reads the metrics in-process via ctypes to Apple's IOReport library (what
powermetrics uses under the hood), so there's no subprocess or temp-file overhead.

It also exposes a Python API: wrap an inference loop in Monitor/Profiler and get a
pandas dataframe of power/frequency/residency plus cumulative session energy — handy
for comparing quantizations or batch sizes with real power context.

[GIF]

Install (Homebrew or uv):
    brew tap --custom-remote binlecode/actop https://github.com/binlecode/actop.git
    brew install binlecode/actop/actop
    # or: uv tool install git+https://github.com/binlecode/actop.git

Repo: https://github.com/binlecode/actop

Built on M1–M4; would love feedback on bandwidth accuracy across chips and on what
metrics you'd want for inference profiling.
```

### C) X / Bluesky / Mastodon thread

```
1/ actop: a sudoless performance monitor for Apple Silicon (M1–M4).

Per-core freq, GPU/ANE %, memory bandwidth, power, temps — in a clean TUI, no sudo.
And a Python API to profile your own LLM/MLX runs. 🧵
[GIF]

2/ Why: running models locally on a Mac, I kept needing to know — GPU-bound? memory-
bandwidth-bound? ANE idle? — without powermetrics + sudo.

actop reads IOReport in-process via ctypes (what powermetrics uses internally). No
subprocess, no temp files, unprivileged.

3/ The part I built it for: a Python API.

    from actop import Profiler
    with Profiler() as p:
        run_my_inference()
    df = p.to_pandas()   # power, freq, residency, energy

SoC-accurate power scaling + cumulative session energy.

4/ Inspired by asitop; complements mactop (Go) and macmon (Rust). actop's niche: the
programmable, Python-native one.

Install + repo: https://github.com/binlecode/actop
Feedback very welcome.
```

> Verify the `Profiler` / `to_pandas()` snippet against the current public API in
> `actop/api.py` before posting, and swap `[GIF]` for the uploaded capture.

---

## After launch — keep the compounding loop going

- Reply to every issue/PR quickly for the first weeks (alive = trustworthy).
- Cut releases on a visible cadence (you already ship v0.9.x frequently — good).
- Add actop to relevant **awesome-*** lists via PR.
- Re-check notability every so often; once you clear ~225 stars (or 90 forks/watchers),
  the homebrew-core formula-audit side is already clean (`brew audit --strict --online`
  passes today), so submission becomes viable.
