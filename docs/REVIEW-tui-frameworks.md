# REVIEW: TUI Framework Evaluation for `actop`

> **Status: decided and shipped.** This document recorded the evaluation that led
> `actop` to adopt **Textual** for its terminal UI. The migration is complete: the
> former `blessed` + `dashing` UI has been removed and `actop` now ships a Textual
> `App` (`actop/tui/`). Section 5 ("As Built") reconciles the original proposal with
> what actually landed — read it for the current architecture; sections 1–4 remain as
> the historical rationale.

## 1. Context and Objective

In `REVIEW-architecture-comparison.md`, it was identified that the reference Go
implementation (`mactop`) held an advantage in UI richness, interactivity, and
rendering concurrency. `mactop` uses `gotui` to provide grid layouts, mouse support,
and background goroutines that prevent the terminal UI from freezing during expensive
system calls.

At the time of this evaluation, `actop` used a combination of `blessed` and `dashing`
for its UI layer. While lightweight and functional, that stack lacked native reactive
components, structured application state management, and built-in asynchronous UI
updates required to close the UX gap.

This document evaluates the top production-grade Terminal User Interface (TUI)
libraries in the Python ecosystem to determine the best path forward.

---

## 2. Core Requirements for `actop`

To match or exceed `mactop`, the chosen Python TUI framework must support:
1. **Asynchronous/Concurrent Rendering:** The UI main loop must not block while hardware metrics are polled via `ctypes` or `sysctl`.
2. **Rich Interactive Widgets:** Native support for Data Tables (for process lists), interactive charts/gauges, and modal/overlay surfaces.
3. **Mouse Support:** Users should be able to select processes or scroll without keyboard binding hacks.
4. **Adaptive Layouts:** A robust layout system that handles window resizing dynamically.
5. **Theming:** First-class TrueColor support and easy styling.

---

## 3. Top TUI Candidates Evaluated

### 1. Textual (Selected)
Built by Textualize (the creators of `Rich`), Textual is a Rapid Application Development framework for Python TUIs. It is the most advanced and actively maintained TUI framework in the Python ecosystem.

*   **Concurrency Model:** Textual is built entirely on `asyncio`. It uses a reactive message-passing architecture with `Workers` (background tasks). This is the conceptual equivalent of `mactop`'s goroutine/channel architecture. `actop` runs its sampler in a background thread worker and streams results to the main UI thread safely.
*   **Styling & Layout:** It uses a CSS-like dialect (`.tcss`, also usable inline via `DEFAULT_CSS`) for styling, padding, layouts, and colors, decoupling UI logic from presentation.
*   **Widgets:** Provides out-of-the-box `DataTable`, `Header`/`Footer`, `Input`, `Static`, `ModalScreen`, and containers (`Horizontal`/`Vertical`), plus a base `Widget` class for custom rendering.
*   **Interactivity:** Full mouse support (hover, click, scroll) and robust keyboard focus management.
*   **Verdict:** **The clear choice.** Textual provides the feature set needed to make `actop` look and feel like a native, premium dashboard, surpassing `gotui` in developer ergonomics and aesthetics.

### 2. Urwid
Urwid is a classic, battle-tested console UI library used in many complex production CLI tools (e.g., `mitmproxy`).
*   **Pros:** Extremely fast screen redraws, handles high-frequency updates efficiently. Supports rich layouts and mouse events. Integrates well with external event loops (`asyncio`, `glib`).
*   **Cons:** The API is dated, highly verbose, and has a steep learning curve. It requires significant manual effort to build modern-looking widgets (gauges, sparklines) and lacks the CSS-like theming engine of Textual.
*   **Verdict:** Viable but archaic. It would take substantially more development time to achieve a modern aesthetic.

### 3. Prompt Toolkit
Famous for powering interactive REPLs (`ptpython`, `ipython`), Prompt Toolkit also includes a full-screen application framework.
*   **Pros:** Excellent cross-platform compatibility, raw performance, async support, and solid mouse handling.
*   **Cons:** Primarily optimized for complex text input and autocompletion rather than data-heavy monitoring dashboards. Building custom grids, tables, and hardware metric visualizations requires writing low-level layout rendering logic.
*   **Verdict:** Better suited for CLI prompts than full-screen system monitors.

### 4. Rich (Standalone)
`Rich` is the underlying rendering engine for Textual, but it can be used on its own (`rich.layout`, `rich.live`).
*   **Pros:** Beautiful rendering, TrueColor support, easy to construct static grids and panels.
*   **Cons:** `Rich` is strictly an output/formatting library. It does **not** handle input (mouse clicks, keyboard navigation, focus switching) or complex application state.
*   **Verdict:** Insufficient on its own for the interactive UX required. (Note: `actop` still depends on `rich` directly for text/table formatting inside custom widgets — `pyproject.toml` pins it explicitly rather than relying on Textual's transitive pin.)

---

## 4. Original Proposed Migration Architecture (Textual + `actop`)

The migration plan at decision time was:

1. **Application Loop:** Replace the blocking `blessed` `while True:` loop with `textual.app.App`.
2. **Data Polling (Workers):** Wrap the sampler with a `@work(thread=True)` worker that polls Apple's `libIOReport` and `sysctl` natively and emits results to the UI.
3. **Process List:** Replace manual string truncation and sorting with `textual.widgets.DataTable` for sticky headers, click-to-sort, and scrolling.
4. **Layout:** Use `TabbedContent` to separate an Overview tab, a Processes tab, and a Config/Themes tab.

Sections 2 and 3 landed as proposed. Section 1 landed. Section 4 (the tabbed layout) was **not** adopted — see below.

---

## 5. As Built (current architecture)

The shipped implementation lives in `actop/tui/` and diverges from the original
proposal in a few deliberate ways. This section is the source of truth for the current
UI; treat section 4 as historical intent.

**Modules**
*   `actop/tui/app.py` — `ActopApp(App)`: the Textual application. Owns the polling worker, keybindings, splash/loading screen, process `DataTable`, sort/filter/pause state, and a `HelpScreen(ModalScreen)` overlay.
*   `actop/tui/widgets.py` — `HardwareDashboard(Widget)` (the gauges/cores/alerts panel) and `BrailleChart(Widget)` (a custom sparkline widget).
*   **Styling is inline** via each component's `DEFAULT_CSS` string. There is **no separate `styles.tcss` file** — any reference to one is stale.

**Concurrency** — as proposed. The sampler runs under `@work(thread=True, exclusive=True)`; a `threading.Event` (`_stop_polling`) coordinates shutdown. The UI thread stays responsive during IOReport/SMC/`libproc` syscalls.

**Layout — preset-based, not tabs.** `compose()` yields a `Header` (with clock), a splash `Static` shown until the sampler is ready, a `Horizontal` main section containing the `HardwareDashboard` beside the process `DataTable`, a fixed app-level status bar, an `Input` for the regex filter, and a `Footer`. The `TabbedContent` design was dropped in favor of one always-on dashboard with a toggleable process table — fewer clicks to see everything at once, closer to `top`/`htop` muscle memory. The "Config/Themes" tab (Catppuccin/Nord toggles) was never built. The dashboard itself is four titled section containers (`CPU`, `GPU · ANE`, `Memory`, `Power`) rendered under two interchangeable **layout presets** — `grid` (two columns, fits short terminals, the default) and `stack` (single full-width scrolling column) — selected by `--layout` and cycled live with `l`. This is Textual's box/preset idea (borrowed from btop): the presets share identical section widgets, data flow, and rendering code — only the container CSS class differs, and a `grid` narrower than ~96 cols auto-degrades to `stack`. It exercises exactly the framework strength Section 1's "Adaptive Layouts" requirement anticipated.

**Charts — custom `BrailleChart`, not `textual.widgets.Sparkline`.** `actop` renders its own braille/block sparklines to control glyph mode, per-core history, and alert coloring more tightly than the stock widget allows. The chart glyph is switchable at runtime (`g`).

**Process table** — `DataTable` with `zebra_stripes` and row cursor, as proposed; sorting is driven by the `s` keybinding cycling sort modes rather than click-to-sort on headers.

**Interactivity — keyboard-first `BINDINGS`:**

| Key | Action |
|-----|--------|
| `q` | Quit |
| `p` | Pause/resume polling |
| `s` | Cycle process sort mode |
| `g` | Toggle chart glyph (braille/block) |
| `t` | Toggle process table |
| `/` | Focus regex filter input |
| `?` | Show help modal |
| `esc` | Cancel filter edit |

Mouse support (scroll, row selection) comes free from Textual on top of these.

---

## 6. Conclusion

Adopting **Textual** resolved the "UI Library & UX" gap identified against `mactop`.
`actop` keeps its superior low-level Python `ctypes` bindings while presenting a
concurrent, interactive terminal dashboard. The shipped UI favors a single-view,
keyboard-first layout over the originally-proposed tabbed design, and uses a custom
braille chart widget in place of Textual's stock `Sparkline` — both choices made
during implementation to better fit a live hardware monitor. See
`docs/DESIGN-system.md` for the TUI rendering contract and `docs/REVIEW-architecture-comparison.md`
for the current `actop`-vs-`mactop` comparison.
