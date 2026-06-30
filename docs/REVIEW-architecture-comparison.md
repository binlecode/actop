# REVIEW: Architecture and Feature Comparison

`agtop` vs. the current Apple Silicon CLI-monitor field — **mactop** (Go), **macmon** (Rust), and **asitop** (Python, agtop's ancestor).

> Refreshed 2026-06-29 against `agtop` **0.9.4**, `mactop` **v2** (`metaspartan/mactop`), `macmon` **v0.7.2** (2026-05), and `asitop` (no tagged releases). Competitor facts are from their public repos as of this date and will drift; re-verify before quoting.

This supersedes the prior agtop-vs-mactop-only review, which described an `agtop` that no longer exists (`blessed`/`dashing` UI, `psutil` process polling, `powermetrics`, `0.4.x` features). The corrections matter: most of `agtop`'s former disadvantages have been closed.

---

## 1. The field today

| Tool | Lang / runtime | Backend | Sudo? | Niche |
| :--- | :--- | :--- | :--- | :--- |
| **agtop** | Python + Textual | In-process IOReport/IOKit/SMC via `ctypes` | **No** | Python-native, programmable profiler |
| **mactop** (v2) | Go + `cgo` (Obj-C/C) | In-process IOReport via `cgo` | **No** (fan control needs root) | Feature-broadest TUI + DevOps |
| **macmon** | Rust + `ratatui` | In-process private API | **No** | Lean, fast, single-binary |
| **asitop** | Python | `sudo powermetrics` subprocess | **Yes** | The original; now superseded |

The single biggest shift since the last review: **the whole serious field is now sudoless and in-process.** `asitop`'s `powermetrics`-subprocess-requiring-root model is the outlier, and `agtop` was built specifically to replace it. So `agtop`'s real competition is `mactop` and `macmon`, not its ancestor.

---

## 2. Architectural notes

### mactop (v2, `metaspartan/mactop`)
The most feature-complete tool in the field. Compiled Go with `cgo` bindings to Apple frameworks (IOReport, IOKit/SMC, `libproc`, AppKit). The v2 line moved to a custom `gotui` framework and added breadth no one else matches: **network I/O, disk I/O, per-process GPU usage, fan RPM**, DRAM read/write bandwidth, a native **menu-bar mode** and an **overlay HUD** (with FPS), five export formats (JSON/YAML/XML/CSV/TOON) plus a Prometheus server, `theme.json` theming with light/dark auto-detect, and ~20 layouts. Single static binary; instant startup. (Original `context-labs/mactop` is Go/cgo too; v2 is the active line.)

### macmon
Rust + `ratatui`, in-process via a private macOS API — same sudoless philosophy as `agtop`. Tracks CPU/GPU/ANE **power**, per-cluster usage + frequency, RAM/swap, CPU/GPU temps, **fan RPM**, and residency, with avg/max history charts and six themes. Headless `pipe` (JSON) and `serve` (Prometheus) subcommands, launchd install, and a built-in stress tester. Distributed via Homebrew, Cargo, MacPorts, Nix; also usable as a Rust **library**. Lean and fast.

### asitop (the ancestor)
Python, shells out to **`sudo powermetrics`** plus `psutil`/`sysctl`/`system_profiler`. Tracks CPU/GPU/ANE power, memory bandwidth, package power, basic charts. No tagged releases; effectively in maintenance. `agtop` is a hard fork that kept the metric vocabulary and threw out the architecture.

### agtop
Python, but with **zero heavyweight runtime deps** — the only third-party requirement is **Textual** (the `blessed`+`dashing`+`psutil` stack is gone). All hardware data comes from in-process `ctypes` bindings: IOReport (power/frequency/residency, now including DRAM bandwidth), IOKit/SMC (die temperatures), `libproc` (`proc_listpids`/`proc_pidinfo`) for **native process polling**, and `sysctl` for memory/SoC config. A Textual `App` drives braille-sparkline charts with a polling worker. Distinctively, it ships a first-class **public Python API** (`Monitor` / `AsyncMonitor` / `Profiler`, `to_pandas()`, `total_package_joules`) and **16 built-in M1–M4 SoC reference profiles** for hardware-accurate power-chart scaling.

---

## 3. Feature comparison

Winner marks reflect the current state, not the old review.

| Capability | agtop | mactop v2 | macmon | asitop | Best |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Distribution** | Homebrew (custom tap), `uv`, `pip` | Single static binary | Homebrew/Cargo/MacPorts/Nix | `pip` | 🏆 mactop / macmon |
| **No sudo / in-process** | ✅ IOReport ctypes | ✅ IOReport cgo | ✅ private API | ❌ `sudo powermetrics` | 🤝 agtop/mactop/macmon |
| **Startup / overhead** | Python interpreter start; light steady-state | Instant; lowest | Instant; very low | Interpreter + subprocess | 🏆 mactop / macmon |
| **Process monitoring** | Native `libproc` ctypes | Native `libproc` + per-process **GPU** | (not a focus) | `psutil` | 🏆 mactop |
| **Core metrics** (CPU/GPU/ANE/RAM/swap/temps/power) | ✅ all + per-core freq/util | ✅ all | ✅ all | ✅ all | 🤝 Tie |
| **Memory bandwidth** | ✅ total DRAM (0.9.4) | ✅ DRAM **read/write** | — | ✅ total | 🏆 mactop |
| **Network / Disk I/O** | ❌ | ✅ both | ❌ | ❌ | 🏆 mactop |
| **Fan RPM** | ❌ | ✅ (+control) | ✅ | ❌ | 🏆 mactop / macmon |
| **SoC-aware power scaling** | ✅ 16 M1–M4 profiles | dynamic (rolling peak) | dynamic | static | 🏆 **agtop** |
| **Session energy integral** | ✅ `total_package_joules` | — | — | — | 🏆 **agtop** |
| **Headless export** | NDJSON + Prometheus | JSON/YAML/XML/CSV/TOON + Prometheus | JSON + Prometheus | — | 🏆 mactop |
| **Programmatic API** | ✅ sync/async/threaded Python + `to_pandas()` | CLI only | Rust **library** | — | 🏆 **agtop** (Python) / macmon (Rust) |
| **Desktop integration** | terminal only | **menu bar + overlay HUD** | small-window mode | terminal only | 🏆 mactop |
| **Theming / color** | adaptive truecolor, `NO_COLOR`, tier degradation | `theme.json`, 20 layouts, light/dark | 6 themes | basic | 🏆 mactop |
| **Runtime interactivity** | sort/filter/pause, alerts (BW/PKG/swap/thermal) | rich grids, mouse, tabs | charts, theme switch | minimal | 🤝 agtop / mactop |
| **Maintenance** | active (0.9.x) | active (v2) | active (0.7.x) | dormant | 🤝 agtop/mactop/macmon |

### Tally
- **mactop:** broadest — wins distribution-portability, overhead, process/GPU, bandwidth detail, net/disk, desktop integration, export breadth, theming.
- **agtop:** wins SoC-aware scaling, session-energy integration, and the Python programmatic API.
- **macmon:** no outright category wins but is the efficiency/portability sweet spot and ties the sudoless trio.
- **asitop:** no wins; superseded on every axis, and uniquely still needs root.

---

## 4. Verdict & niches

**mactop (v2) is the feature king.** If you want the most metrics on screen (net/disk I/O, fan, per-process GPU, DRAM read/write), a menu-bar/overlay presence, the widest export menu, and a zero-dependency binary, it wins for general power users and DevOps. The cost is that extending it means Go + `cgo`.

**macmon is the minimalist's pick.** Rust + `ratatui` gives the lowest overhead and the cleanest single-binary install, with JSON/Prometheus for scripting and a Rust library for embedding. If you live in the terminal and want fast + lean, it's hard to beat.

**asitop is effectively retired.** It still requires `sudo` (its `powermetrics` dependency), uses `psutil`, and has no releases. `agtop` is the drop-in successor — same metric language, none of the root requirement or subprocess cost.

**agtop's defensible niche is being the *programmable, Python-native, ML-aware* profiler — not the broadest TUI.** Its honest differentiators:

1. **First-class Python API.** Alone in this field, `agtop` exposes `Monitor`/`AsyncMonitor`/`Profiler` with threshold callbacks and `to_pandas()`. You can instrument a training loop, profile a CoreML/MLX inference run, and pull the result straight into a DataFrame — no parsing a CLI's JSON. (`macmon` offers a Rust library; `agtop` offers the Python one data scientists actually work in.)
2. **SoC-accurate power context.** 16 M1–M4 reference profiles mean the power charts scale to *your* chip's real ceilings out of the box, rather than to a rolling observed peak. On an M4 Max you immediately see how hard you're pushing an M4 Max.
3. **Session energy as a metric.** Cumulative ∫(package power)·dt over a run (`total_package_joules`, surfaced live in the TUI) — a profiling primitive the others don't expose.

**Where agtop honestly trails:** no network/disk I/O, no fan RPM, no per-process GPU, no menu-bar/overlay, fewer export formats, and a Python interpreter's startup/footprint versus a Go or Rust binary. ANE wattage and memory bandwidth — once cited as agtop differentiators — are now table stakes (all four track ANE; mactop/asitop also report bandwidth, mactop in more detail).

**Bottom line:** pick **mactop** for breadth and desktop integration, **macmon** for a lean Rust binary, and **agtop** when you want to *program against* Apple Silicon telemetry from Python — profiling ML workloads with SoC-accurate context and a pandas-friendly API — without sudo.
