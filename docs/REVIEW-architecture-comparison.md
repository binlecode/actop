# REVIEW: Architecture and Feature Comparison

`actop` vs. the current Apple Silicon CLI-monitor field — **mactop** (Go), **macmon** (Rust), and **asitop** (Python, actop's ancestor).

> Refreshed 2026-07-02 against `actop` **1.2.2**, `mactop` **v2.1.5** (`metaspartan/mactop`), `macmon` **v0.7.2** (`vladkens/macmon`), and `asitop` (no tagged releases). Both sides re-verified this round: `actop` facts from the current codebase (`pyproject.toml`, `CHANGELOG.md`, `smc.py`, `gpu_registry.py`, `utils.py`, `models.py`, `tui/app.py`, `actop.py`, `docs/TODO-architecture-roadmap.md`); competitor facts pulled fresh from each project's GitHub `readme.md` via `gh api`. mactop in particular has grown substantially since the 2026-06-29 pass — re-check before quoting either side, as this is a fast-moving field.

This supersedes the prior actop-vs-mactop-only review, which described an `actop` that no longer exists (`blessed`/`dashing` UI, `psutil` process polling, `powermetrics`, `0.4.x` features). The corrections matter: most of `actop`'s former disadvantages have been closed — and as of 1.2.0–1.2.2, two more (per-process GPU, fan RPM) just closed too.

---

## 1. The field today

| Tool | Lang / runtime | Backend | Sudo? | Niche |
| :--- | :--- | :--- | :--- | :--- |
| **actop** | Python + Textual | In-process IOReport/IOKit/SMC via `ctypes` | **No** | Python-native, programmable profiler |
| **mactop** (v2.1.x) | Go + `cgo` (Obj-C/C) | In-process IOReport via `cgo` | **No** (fan control needs root) | Feature-broadest TUI + DevOps |
| **macmon** | Rust + `ratatui` | In-process private API | **No** | Lean, fast, single-binary |
| **asitop** | Python | `sudo powermetrics` subprocess | **Yes** | The original; now superseded |

The single biggest shift since the last review: **the whole serious field is now sudoless and in-process.** `asitop`'s `powermetrics`-subprocess-requiring-root model is the outlier, and `actop` was built specifically to replace it. So `actop`'s real competition is `mactop` and `macmon`, not its ancestor.

---

## 2. Architectural notes

### mactop (v2.1.5, `metaspartan/mactop`)
The most feature-complete tool in the field, and still expanding fast. Compiled Go with `cgo` bindings to Apple frameworks (IOReport, IOKit/SMC, `libproc`, IOHIDEventSystemClient, AppKit). The v2 line moved to a custom `gotui` framework and keeps adding breadth no one else matches: **network I/O, disk I/O, Thunderbolt bandwidth + device tree, RDMA detection, battery monitoring**, per-process GPU usage (experimental), **fan RPM with target speed/mode + optional fan control** (root-gated), DRAM read/write bandwidth (power-based estimation on M5+), a native **menu-bar mode** and an **overlay HUD** (with FPS, Screen-Recording-gated), **process kill from the UI (F9)** and process filtering, **persistent config/theme files** (XDG-aware), **20-language i18n**, five headless export formats (JSON/YAML/XML/CSV/TOON) plus a Prometheus server, `theme.json` theming with light/dark auto-detect, and ~20 layouts. Single static binary; instant startup. (Original `context-labs/mactop` is Go/cgo too; v2 is the active line.)

### macmon
Rust + `ratatui`, in-process via a private macOS API — same sudoless philosophy as `actop`. Tracks CPU/GPU/ANE **power**, per-cluster (and experimental per-core) usage split into frequency-scaled usage *and* active-residency ratios, RAM/swap, CPU/GPU temps, **structured per-fan RPM (name + current + max)**, with avg/max history charts and **six runtime-switchable themes** (`c` key). Headless `pipe` (JSON) and `serve` (JSON *and* Prometheus on one process, with `launchd --install`) subcommands, and a built-in `stress` load generator (CPU and/or GPU, tunable workers/duration) for verifying metric behavior. Distributed via Homebrew, Cargo, MacPorts, Nix; also usable as a Rust **library** (`Sampler::get_metrics` / `get_metrics_now`). Lean and fast.

### asitop (the ancestor)
Python, shells out to **`sudo powermetrics`** plus `psutil`/`sysctl`/`system_profiler`. Tracks CPU/GPU/ANE power, memory bandwidth, package power, basic charts. No tagged releases; effectively in maintenance. `actop` is a hard fork that kept the metric vocabulary and threw out the architecture.

### actop
Python, but with **zero heavyweight runtime deps** — the only third-party requirements are **Textual** (the TUI framework) and **Rich** (declared as a direct dependency and imported directly in `tui/widgets.py`, not relied on as a Textual transitive pin); the `blessed`+`dashing`+`psutil` stack is gone. All hardware data comes from in-process `ctypes` bindings: IOReport (power/frequency/residency, DRAM bandwidth), IOKit/SMC (die temperatures **and now fan tachometers**, v1.2.2), IOKit's `AGXDeviceUserClient` for **per-process GPU time** (`gpu_registry.py`, v1.2.0), `libproc` (`proc_listpids`/`proc_pidinfo`) for **native process polling**, and `sysctl` for memory/SoC config. A Textual `App` drives braille-sparkline charts with a polling worker. Distinctively, it ships a first-class **public Python API** (`Monitor` / `AsyncMonitor` / `Profiler`, `to_pandas()`, `total_package_joules`) and **16 built-in M1–M4 SoC reference profiles** for hardware-accurate power-chart scaling.

---

## 3. Feature comparison

Winner marks reflect the current state, not the old review.

| Capability | actop | mactop v2.1 | macmon | asitop | Best |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Distribution** | Homebrew (custom tap), `uv`, `pip` | Single static binary | Homebrew/Cargo/MacPorts/Nix | `pip` | 🏆 mactop / macmon |
| **No sudo / in-process** | ✅ IOReport ctypes | ✅ IOReport cgo | ✅ private API | ❌ `sudo powermetrics` | 🤝 actop/mactop/macmon |
| **Startup / overhead** | Python interpreter start; light steady-state | Instant; lowest | Instant; very low | Interpreter + subprocess | 🏆 mactop / macmon |
| **Process monitoring** | Native `libproc` ctypes + per-process **GPU** (v1.2.0) | Native `libproc` + per-process **GPU** | (not a focus) | `psutil` | 🤝 actop / mactop |
| **Core metrics** (CPU/GPU/ANE/RAM/swap/temps/power) | ✅ all + per-core freq/util | ✅ all | ✅ all | ✅ all | 🤝 Tie |
| **Memory bandwidth** | ✅ total DRAM | ✅ DRAM **read/write** | — | ✅ total | 🏆 mactop |
| **Network / Disk I/O** | ❌ (spiked, not shipped — roadmap must-have) | ✅ both | ❌ | ❌ | 🏆 mactop |
| **Fan RPM** | ✅ current + max_rpm per fan (v1.2.3) | ✅ RPM + target + mode (Auto/Manual) + write control | ✅ RPM + name + max_rpm per fan | ❌ | tie actop/macmon (current+max, read-only); mactop adds target/mode + write control |
| **SoC-aware power scaling** | ✅ 16 M1–M4 profiles | dynamic (rolling peak) | dynamic | static | 🏆 **actop** |
| **Session energy integral** | ✅ `total_package_joules` | — | — | — | 🏆 **actop** |
| **Headless export** | NDJSON + Prometheus | JSON/YAML/XML/CSV/TOON + Prometheus | JSON + Prometheus | — | 🏆 mactop |
| **Programmatic API** | ✅ sync/async/threaded Python + `to_pandas()` | CLI only | Rust **library** | — | 🏆 **actop** (Python) / macmon (Rust) |
| **Desktop integration** | terminal only | **menu bar + overlay HUD** | small-window mode | terminal only | 🏆 mactop |
| **Theming / color** | adaptive truecolor, `NO_COLOR`, tier degradation + **accessibility palettes** (`--palette` thermal/viridis-colorblind-safe/mono, v1.4.1); no runtime cycle | `theme.json`, 20 layouts, light/dark auto-detect, runtime cycle (`c`/`b`/party mode) | 6 themes, runtime cycle (`c`) | basic, static `--color` choice | split — peers 🏆 *runtime decorative* cycling; **actop 🏆 *accessibility* palettes** (only tool with colorblind-safe/mono) |
| **Runtime interactivity** | sort/filter/pause, alerts (BW/PKG/swap/thermal) | rich grids, mouse, tabs, process kill (F9), vim nav | charts/detail toggle, theme switch | minimal | 🤝 actop / mactop |
| **Maintenance** | active (1.2.x) | active (v2.1.x) | active (0.7.x) | dormant | 🤝 actop/mactop/macmon |

### Tally
- **mactop:** broadest — wins distribution-portability, overhead, bandwidth detail, net/disk (+Thunderbolt/RDMA/battery, mactop-exclusive), desktop integration, export breadth, theming. Process/GPU and fan RPM (current+max) are now ties with actop; mactop still leads fan only on the root-gated target/mode + write-control breadth.
- **actop:** wins SoC-aware scaling, session-energy integration, and the Python programmatic API; per-process GPU and fan RPM (current+max, v1.2.3) are no longer gaps, just ties.
- **macmon:** no outright category wins but is the efficiency/portability sweet spot, ties the sudoless trio (including read-only current+max fan data), and edges actop only on *runtime decorative* theme cycling (actop instead ships startup accessibility palettes — a different answer; see §4).
- **asitop:** no wins; superseded on every axis, and uniquely still needs root.

---

## 4. Convergence Gap Analysis

The tally above mixes two different kinds of gap: features only one peer has (a differentiator, not yet an expectation) versus features **two or more independent peers have converged on** (a signal the market has decided this is table stakes, worth prioritizing over a single-peer nice-to-have). This section separates the two, scoped to the peers still actively developed (`mactop`, `macmon`); `asitop` is legacy and contributes no convergence signal — it doesn't even have fan, network, disk, or process-listing.

**Method:** for each fine-grained capability below, count how many of {mactop, macmon} implement it. A count of 2 = converged/table-stakes; a count of 1 = single-peer differentiation (real, but not yet a convergence signal); a dash = actop already has it (no gap).

| Capability | mactop | macmon | Convergence | actop status |
| :--- | :--- | :--- | :--- | :--- |
| Runtime *decorative* theme/color cycling via keybind | ✅ (`c`/`b`, party mode) | ✅ (`c`, 6 themes) | **2/2 — converged (decorative)** | ➖ **answered differently in v1.4.1:** startup `--palette` (thermal/viridis/mono) instead of a runtime cycle. The converged feature is *decorative*; actop's is *accessibility* (colorblind-safe + mono) — deliberately not the same feature, so not a tie on this exact row. Runtime cycle keybind deferred as optional. |
| Structured fan data — the genuinely 2/2-converged attribute is **max_rpm** (target/mode are mactop-only breadth) | ✅ reads max_rpm (+ target/mode + write control) | ✅ name + max_rpm per fan | **2/2 — converged** | ✅ **closed v1.2.3** — current + max per fan (`FanReading`), read-only |
| Historical avg/max alongside live charts | (implicit via history charts) | ✅ explicit avg/max | 1/2 | — actop already has this (`_avg_max`, `tui/widgets.py`) |
| Concurrent structured-snapshot + Prometheus export from one running process | ➖ (JSON to stdout *or* separate Prometheus server) | ✅ `serve` exposes `/json` and `/metrics` together | 1/2, weakly converged | ⚠️ `--json` (NDJSON stdout) and `--serve` (Prometheus) are mutually exclusive run modes |
| Network I/O | ✅ | ❌ (not a focus) | 1/2 | ❌ roadmap must-have, spiked not shipped |
| Disk I/O | ✅ | ❌ | 1/2 | ❌ roadmap must-have, spiked not shipped |
| Process kill from the UI | ✅ (F9) | ➖ (no process focus) | 1/2 | ❌ |
| Persistent config/theme across restarts | ✅ | ❌ (flags/keybinds only, session-scoped) | 1/2 | ❌ |
| Built-in synthetic load generator exposed via main CLI | ➖ (none) | ✅ `macmon stress` (CPU+GPU, tunable) | 1/2 | ⚠️ `scripts/ane_load.py` exists but is a separate dev script (ANE-only, not a CLI subcommand) |
| Thunderbolt / RDMA / battery monitoring | ✅ | ❌ | 1/2 | ❌ (mactop-exclusive breadth, not a convergence signal) |
| Menu-bar / overlay desktop presence | ✅ | ➖ (small-window mode only, different concept) | 1/2 | ❌ (mactop-exclusive, roadmap explicitly defers this) |
| i18n / multi-language | ✅ (20 languages) | ❌ | 1/2 | ❌ (mactop-exclusive) |

**Finding:** convergence pressure on actop is now effectively closed. Both capabilities that were genuinely converged (implemented independently by both actively-developed peers) and still missing/partial in actop have been addressed:

1. **Color** — ✅ **addressed in v1.4.1, on actop's own terms.** The converged peer feature is a *decorative* runtime cycle (mactop party mode, macmon 6 themes). An ROI review found that chasing it verbatim added little to actop's thesis, and reframed the real gap as *accessibility*: actop shipped a startup `--palette {thermal,viridis,mono}` flag (thermal = the unchanged gradient; viridis colorblind-safe; mono grayscale) — the only tool in the field with colorblind-safe/mono palettes. The *runtime cycle keybind* itself was deliberately deferred as optional (set-once is the right model for an accessibility preference; the keybind is a purely additive follow-on).
2. **Richer fan telemetry** — ✅ **closed in v1.2.3.** `smc.py`'s discovery now also reads `F{n}Mx` (max RPM) alongside `F{n}Ac`, and `read_fan_info()` returns `FanReading(current, max)` per fan (`docs/DESIGN-system.md` §4.3), matching macmon's read-only current+max. `F{n}Mn` (min) is deliberately skipped — peers use it only to clamp fan-set *writes* actop doesn't perform.

Everything else mactop has that actop doesn't (network/disk, Thunderbolt/RDMA, battery, i18n, menu-bar/overlay, process-kill, config persistence) is **single-peer breadth**, not a converged expectation — mactop is simply pursuing a broader "do everything" scope that neither macmon nor actop shares. This reframes `docs/TODO-architecture-roadmap.md`'s net/disk-I/O item: it's a deliberate bet on catching up to the breadth leader for its own sake (the roadmap doc says as much — "deliberately widens scope"), not a response to peer convergence. With both converged gaps now addressed (fan telemetry v1.2.3; color via the v1.4.1 accessibility `--palette` MVP, with the decorative runtime-cycle keybind deferred as optional), there is no remaining converged gap — net/disk I/O is the next roadmap item purely as a breadth bet.

---

## 5. Verdict & niches

**mactop (v2.1.x) is the feature king.** If you want the most metrics on screen (net/disk I/O, Thunderbolt/RDMA, battery, DRAM read/write detail), a menu-bar/overlay presence, the widest export menu, i18n, and a zero-dependency binary, it wins for general power users and DevOps. The cost is that extending it means Go + `cgo`. (Fan RPM with max, and per-process GPU — once mactop-only — are now table stakes: actop shipped both across v1.2.x. The last converged gap, color, was answered in v1.4.1 on actop's own terms — accessibility `--palette` rather than the peers' decorative runtime cycle — so no converged gap remains per §4.)

**macmon is the minimalist's pick.** Rust + `ratatui` gives the lowest overhead and the cleanest single-binary install, with JSON/Prometheus for scripting and a Rust library for embedding. If you live in the terminal and want fast + lean, it's hard to beat.

**asitop is effectively retired.** It still requires `sudo` (its `powermetrics` dependency), uses `psutil`, and has no releases. `actop` is the drop-in successor — same metric language, none of the root requirement or subprocess cost.

**actop's defensible niche is being the *programmable, Python-native, ML-aware* profiler — not the broadest TUI.** Its honest differentiators:

1. **First-class Python API.** Alone in this field, `actop` exposes `Monitor`/`AsyncMonitor`/`Profiler` with threshold callbacks and `to_pandas()`. You can instrument a training loop, profile a CoreML/MLX inference run, and pull the result straight into a DataFrame — no parsing a CLI's JSON. (`macmon` offers a Rust library; `actop` offers the Python one data scientists actually work in.)
2. **SoC-accurate power context.** 16 M1–M4 reference profiles mean the power charts scale to *your* chip's real ceilings out of the box, rather than to a rolling observed peak. On an M4 Max you immediately see how hard you're pushing an M4 Max.
3. **Session energy as a metric.** Cumulative ∫(package power)·dt over a run (`total_package_joules`, surfaced live in the TUI) — a profiling primitive the others don't expose.

**Where actop honestly trails:** no network/disk I/O (a feasibility spike against `mactop`'s implementation is done, per `docs/TODO-architecture-roadmap.md`, but nothing has shipped), no menu-bar/overlay, no i18n, no process-kill, fewer export formats, and a Python interpreter's startup/footprint versus a Go or Rust binary — but per §4, all of these are single-peer (mactop) breadth, not convergence pressure. No genuinely converged gap (mactop *and* macmon both do it) remains: the last one, color, was addressed in v1.4.1 via accessibility palettes (`--palette`), with the peers' *decorative* runtime cycle keybind deliberately deferred as optional. ANE wattage and memory bandwidth — once cited as actop differentiators — are now table stakes (all four track ANE; mactop/asitop also report bandwidth, mactop in more detail). Fan RPM (current+max) and per-process GPU, formerly on this list, shipped in v1.2.3/v1.2.2 and v1.2.0 respectively and are no longer gaps.

**Bottom line:** pick **mactop** for breadth and desktop integration, **macmon** for a lean Rust binary, and **actop** when you want to *program against* Apple Silicon telemetry from Python — profiling ML workloads with SoC-accurate context and a pandas-friendly API — without sudo.
