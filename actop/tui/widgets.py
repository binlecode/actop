"""Textual widgets for the actop hardware dashboard."""

import os
from collections import deque

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Static

from actop.analytics import AlertEngine, bandwidth_percent, package_power_percent
from actop.models import SystemSnapshot
from actop.power_scaling import (
    DEFAULT_CPU_FLOOR_W,
    DEFAULT_GPU_FLOOR_W,
    clamp_percent,
    power_to_percent,
)


_COOL_RGB = (66, 135, 245)  # blue
_HOT_RGB = (240, 70, 64)  # red

# Chart color palettes selected by --palette / the `palette` config field. Each
# value is a list of RGB control points that _pct_to_rgb interpolates
# piecewise-linearly (every entry must have >= 2 stops). `thermal` (the default)
# is literally [_COOL_RGB, _HOT_RGB], so it reproduces the pre-palette blue->red
# gradient byte-for-byte; `viridis` is a colorblind-safe perceptual ramp; `mono`
# is grayscale intensity for a monochrome / high-contrast preference. Dict
# insertion order is the order a future runtime cycle keybind would advance
# through (deliberately deferred — see docs/DESIGN-system.md §5.2).
_PALETTES = {
    "thermal": [_COOL_RGB, _HOT_RGB],
    "viridis": [
        (68, 1, 84),
        (59, 82, 139),
        (33, 145, 140),
        (94, 201, 98),
        (253, 231, 37),
    ],
    "mono": [(30, 30, 30), (230, 230, 230)],
}
_DEFAULT_PALETTE = "thermal"

# Color tiers, coolest-to-hottest, used when the terminal cannot render the
# truecolor gradient. The 16-color tier is a conventional severity ramp (the
# blue->red interpolation has no faithful 16-color analogue), keyed by percent.
_ANSI16_SEVERITY = (
    (25.0, "blue"),
    (50.0, "green"),
    (75.0, "yellow"),
)
_ANSI16_HOT = "red"

# Maps a Rich/Textual console.color_system to our internal tier names.
_COLOR_SYSTEM_TO_MODE = {
    "truecolor": "truecolor",
    "256": "256",
    "standard": "16",
    "windows": "16",
}

# Cumulative braille fill bits for a left-column vertical pole, indexed 0 (bottom
# dot only) to 3 (all 4 dots filled): dots 7 / 7+3 / 7+3+2 / 7+3+2+1.
_BRAILLE_FILL_BITS = [0x40, 0x44, 0x46, 0x47]
_BRAILLE_FULL = 0x47  # all 4 left-column dots
_BRAILLE_BLANK = "\u2800"
_BLOCK_FILL_GLYPHS = ["\u2582", "\u2584", "\u2586", "\u2588"]
_BLOCK_FULL_GLYPH = "\u2588"
_BLOCK_BLANK = " "


def _pct_to_rgb(pct: float, palette: str = _DEFAULT_PALETTE) -> tuple[int, int, int]:
    """Interpolate 0-100 percent piecewise-linearly across a palette's RGB stops.

    A 2-stop palette (e.g. the default `thermal` = [_COOL_RGB, _HOT_RGB]) reduces
    to a plain blue->red lerp identical to the pre-palette behavior. Unknown
    names fall back to the default palette.
    """
    stops = _PALETTES.get(palette) or _PALETTES[_DEFAULT_PALETTE]
    p = min(100.0, max(0.0, float(pct))) / 100.0
    seg = p * (len(stops) - 1)  # position along the (len-1) segments
    i = min(int(seg), len(stops) - 2)  # segment index, clamped for p == 1.0
    t = seg - i
    a, b = stops[i], stops[i + 1]
    r = round(a[0] + (b[0] - a[0]) * t)
    g = round(a[1] + (b[1] - a[1]) * t)
    b_ = round(a[2] + (b[2] - a[2]) * t)
    return (r, g, b_)


def resolve_color_mode(console=None, env=None) -> str:
    """Resolve the active color tier: 'none' | '16' | '256' | 'truecolor'.

    NO_COLOR (https://no-color.org) wins unconditionally. Otherwise the
    terminal's detected color system is preferred (when a Rich/Textual console
    is supplied), falling back to COLORTERM / TERM inspection so the function is
    still meaningful before the app is mounted (e.g. in tests).
    """
    env = os.environ if env is None else env
    if env.get("NO_COLOR", "") != "":
        return "none"
    if console is not None:
        system = getattr(console, "color_system", None)
        if system in _COLOR_SYSTEM_TO_MODE:
            return _COLOR_SYSTEM_TO_MODE[system]
        if system is None:
            return "none"
    if env.get("COLORTERM", "").lower() in ("truecolor", "24bit"):
        return "truecolor"
    term = env.get("TERM", "")
    if term in ("", "dumb"):
        return "none"
    if "truecolor" in term:
        return "truecolor"
    if "256color" in term or "256" in term:
        return "256"
    return "16"


def _pct_to_color(
    pct: float, mode: str = "truecolor", palette: str = _DEFAULT_PALETTE
) -> str:
    """Map 0-100 percent to a Rich style string for the given color tier.

    Degrades the truecolor gradient across terminal capabilities: truecolor ->
    `rgb()`, 256-color -> nearest `color()` cube index, 16-color -> a named
    severity ramp, and `none` -> no style (NO_COLOR / dumb terminals). The
    palette selects the gradient stops and applies at the truecolor and 256
    tiers (256 follows automatically, since its cube index quantizes the palette
    RGB); the 16-color severity ramp and `none` are palette-independent.
    """
    if mode == "none":
        return ""
    if mode == "16":
        p = min(100.0, max(0.0, float(pct)))
        for threshold, name in _ANSI16_SEVERITY:
            if p < threshold:
                return name
        return _ANSI16_HOT
    r, g, b = _pct_to_rgb(pct, palette)
    if mode == "256":
        idx = 16 + 36 * round(r / 255 * 5) + 6 * round(g / 255 * 5) + round(b / 255 * 5)
        return "color({})".format(idx)
    return "rgb({},{},{})".format(r, g, b)


def _format_window_span(seconds: float) -> str:
    """Format a chart's visible time span (e.g. `45s`, `2m08s`, `1h05m`)."""
    seconds = int(max(0, seconds))
    if seconds < 60:
        return "{}s".format(seconds)
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return "{}m{:02d}s".format(minutes, secs) if secs else "{}m".format(minutes)
    hours, minutes = divmod(minutes, 60)
    return "{}h{:02d}m".format(hours, minutes) if minutes else "{}h".format(hours)


def _normalize_chart_glyph_mode(value: str) -> str:
    return "block" if str(value).strip().lower() == "block" else "dots"


def _glyph_set_for_mode(mode: str) -> tuple[str, str, list[str]]:
    normalized = _normalize_chart_glyph_mode(mode)
    if normalized == "block":
        return (_BLOCK_BLANK, _BLOCK_FULL_GLYPH, _BLOCK_FILL_GLYPHS)
    return (
        _BRAILLE_BLANK,
        chr(0x2800 | _BRAILLE_FULL),
        [chr(0x2800 | bits) for bits in _BRAILLE_FILL_BITS],
    )


def _clamped_value_and_level(value: float, total_levels: int) -> tuple[float, int]:
    v = min(100.0, max(0.0, float(value)))
    level = max(0, min(total_levels, round(v / 100 * total_levels)))
    if v > 0 and level == 0:
        level = 1
    return (v, level)


def _value_to_cell_glyph(value: float, glyph_mode: str) -> str:
    blank_glyph, _, partial_glyphs = _glyph_set_for_mode(glyph_mode)
    _, level = _clamped_value_and_level(value, total_levels=4)
    if level <= 0:
        return blank_glyph
    return partial_glyphs[level - 1]


def _inline_spark(history, width_chars: int = 8, glyph_mode: str = "dots") -> str:
    """Inline sparkline with shared glyph logic used by BrailleChart."""
    if width_chars <= 0:
        return ""
    n = width_chars
    vals = list(history)[-n:]
    vals = [0.0] * (n - len(vals)) + vals
    return "".join(_value_to_cell_glyph(v, glyph_mode) for v in vals)


class BrailleChart(Widget):
    """Sparkline chart with `dots` (braille) or `block` glyph modes.

    Each character is one time sample. The dot position encodes the value:
    4 dot levels per terminal row, so height=2 gives 8 levels, height=4 gives 16.
    """

    DEFAULT_CSS = """
    BrailleChart {
        height: 2;
    }
    """

    def __init__(
        self,
        glyph_mode: str = "dots",
        color_mode: str = None,
        palette: str = _DEFAULT_PALETTE,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._data = []
        self._glyph_mode = _normalize_chart_glyph_mode(glyph_mode)
        # None => resolve lazily from the running app's console (and NO_COLOR)
        # once mounted; falls back to environment detection before then.
        self._color_mode = color_mode
        # Gradient palette (set once at construction from config — there is no
        # runtime cycle in the MVP, so no mutator is needed).
        self._palette = palette

    def on_mount(self) -> None:
        if self._color_mode is None:
            self._color_mode = resolve_color_mode(getattr(self.app, "console", None))

    def _active_color_mode(self) -> str:
        if self._color_mode is not None:
            return self._color_mode
        return resolve_color_mode()

    @staticmethod
    def _normalize_glyph_mode(value: str) -> str:
        return _normalize_chart_glyph_mode(value)

    @property
    def data(self):
        return self._data

    @data.setter
    def data(self, values) -> None:
        self._data = values
        self.refresh()

    @property
    def glyph_mode(self) -> str:
        return self._glyph_mode

    def set_glyph_mode(self, glyph_mode: str) -> None:
        normalized = _normalize_chart_glyph_mode(glyph_mode)
        if normalized == self._glyph_mode:
            return
        self._glyph_mode = normalized
        self.refresh()

    def render(self):
        return self._render_text(self.size.width, self.size.height)

    def _render_text(self, width: int, height: int):
        """Render the chart into a Rich `Text` for the given cell dimensions.

        Split out from `render()` so the colored output can be exercised without
        a live terminal layout; `render()` is a thin wrapper over it.
        """
        if width <= 0 or height <= 0:
            return ""
        color_mode = self._active_color_mode()
        blank_glyph, full_glyph, partial_glyphs = _glyph_set_for_mode(self._glyph_mode)
        n = width  # 1 sample per character
        dlen = len(self._data)
        offset = dlen - n
        total = height * 4  # 4 dot positions per terminal row
        out = Text()
        for row in range(height):
            for col in range(width):
                i = offset + col
                raw_v = float(self._data[i]) if i >= 0 else 0.0
                v, level = _clamped_value_and_level(raw_v, total_levels=total)
                line_color = _pct_to_color(v, color_mode, self._palette)
                if level > 0:
                    dot_row = height - 1 - (level - 1) // 4
                    if row > dot_row:
                        # below the peak row: fully filled segment
                        out.append(full_glyph, style=line_color)
                    elif row == dot_row:
                        # peak row: partial fill
                        pos = (level - 1) % 4  # 0 = bottom dot, 3 = top dot
                        out.append(partial_glyphs[pos], style=line_color)
                    else:
                        out.append(blank_glyph)
                else:
                    out.append(blank_glyph)
            if row < height - 1:
                out.append("\n")
        return out


class MetricsUpdated(Message):
    """Posted by ActopApp when a new hardware snapshot is ready."""

    def __init__(self, snapshot: SystemSnapshot) -> None:
        self.snapshot = snapshot  # sole frame contract (RAM/swap/processes on it)
        super().__init__()


class AlertsComputed(Message):
    """Posted by HardwareDashboard each frame with the formatted status string.

    The status line lives in fixed app chrome (not the dashboard subtree, so it
    stays visible while a stacked dashboard scrolls). The dashboard composes the
    thermal/alerts/span/energy tokens and hands the string up to ActopApp, which
    renders it into the app-level #status-line bar.
    """

    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__()


_RESIDENCY_ORDER = ("idle", "low", "mid", "high")
_RESIDENCY_GLYPHS = {"idle": "░", "low": "▒", "mid": "▓", "high": "█"}


def _residency_bar_widths(percentages: dict, bar_width: int) -> dict:
    """Largest-remainder allocation of `bar_width` chars across buckets.

    Plain per-bucket rounding can under/overshoot the total width (gaps or
    overflow) when percentages don't divide evenly; this guarantees the
    allocated widths sum to exactly `bar_width`.
    """
    if bar_width <= 0:
        return {name: 0 for name in _RESIDENCY_ORDER}
    raw = {
        name: percentages.get(name, 0) / 100.0 * bar_width for name in _RESIDENCY_ORDER
    }
    floors = {name: int(raw[name]) for name in _RESIDENCY_ORDER}
    remainder = bar_width - sum(floors.values())
    fracs = sorted(_RESIDENCY_ORDER, key=lambda n: raw[n] - floors[n], reverse=True)
    for name in fracs[: max(0, remainder)]:
        floors[name] += 1
    return floors


def _format_residency_bar(percentages: dict, bar_width: int = 16) -> str:
    """Fixed-width proportional block-density bar for one cluster/domain."""
    widths = _residency_bar_widths(percentages, bar_width)
    return "".join(_RESIDENCY_GLYPHS[name] * widths[name] for name in _RESIDENCY_ORDER)


def _format_residency_row(label: str, percentages: dict, bar_width: int = 16) -> str:
    """`P-CPU  [bar]  idleN lowN midN highN` DVFS residency summary line."""
    bar = _format_residency_bar(percentages, bar_width)
    breakdown = " ".join(
        "{}{}".format(name, percentages.get(name, 0)) for name in _RESIDENCY_ORDER
    )
    return "{:<6} [{}]  {}".format(label, bar, breakdown)


class HardwareDashboard(Widget):
    """Hardware metrics panel: CPU/GPU/ANE/RAM/Power charts + status line."""

    # Dashboard CSS lives here (scoped to this widget), not in ActopApp: the two
    # layout presets are just a class swap on this widget. `grid` is a two-column
    # grid with the tall CPU section spanning all three right-column rows; `stack`
    # is the single scrollable column (only the stack preset scrolls — grid is
    # sized to fit). Below `_GRID_MIN_WIDTH` cols grid auto-degrades to stack
    # (`on_resize`), so a grid never squeezes its columns below readability.
    DEFAULT_CSS = """
    HardwareDashboard {
        width: 1fr;
        height: 1fr;
        padding: 0;
    }
    HardwareDashboard.layout-stack {
        layout: vertical;
        overflow-y: auto;
    }
    HardwareDashboard.layout-grid {
        layout: grid;
        grid-size: 2;
        grid-columns: 1fr 1fr;
        grid-rows: auto auto auto;
    }
    HardwareDashboard.layout-grid #section-cpu {
        row-span: 3;
        /* Fill the 3-row span (not just content height) so the CPU box's bottom
           border aligns with the lowest right-column box (Power) instead of
           closing early and leaving dead space below it. */
        height: 100%;
    }
    .dash-section {
        border: round $accent;
        padding: 0 1;
        height: auto;
    }
    .metric-label {
        height: 1;
        color: $text-muted;
    }
    .metric-chart {
        height: 2;
    }
    #pcpu-chart {
        height: 4;
    }
    #ecpu-chart {
        height: 4;
    }
    #ram-chart {
        height: 2;
    }
    .cpu-summary-row {
        height: 1;
        color: $text-muted;
    }
    .residency-row {
        height: 1;
        color: $text-muted;
    }
    .core-grid {
        height: auto;
    }
    .cpu-half {
        height: auto;
    }
    """

    _VALID_PRESETS = ("grid", "stack")
    # Below this width the grid's two columns fall under ~48 cols each and stop
    # being readable, so grid silently renders as stack until width recovers.
    _GRID_MIN_WIDTH = 96

    def __init__(self, config, **kwargs) -> None:
        super().__init__(**kwargs)
        self._config = config
        cfg = config
        self._chart_glyph = getattr(cfg, "chart_glyph", "dots")
        # Gradient palette, fixed for the session (--palette). Passed eagerly to
        # every chart at compose time, exactly like _chart_glyph.
        self._palette = getattr(cfg, "palette", _DEFAULT_PALETTE)

        requested = getattr(cfg, "layout", "grid")
        if requested not in self._VALID_PRESETS:
            raise ValueError(
                "layout preset must be one of {}, got {!r}".format(
                    self._VALID_PRESETS, requested
                )
            )
        # Requested preset is what the user/CLI asked for; effective is what is
        # actually applied after the width auto-degrade. They differ only when a
        # grid is squeezed below _GRID_MIN_WIDTH.
        self._requested_preset = requested
        self._effective_preset = requested
        self.add_class("layout-{}".format(requested))

        maxlen = self._CHART_HIST_MAXLEN

        self._ecpu_hist: deque = deque([0] * maxlen, maxlen=maxlen)
        self._pcpu_hist: deque = deque([0] * maxlen, maxlen=maxlen)
        self._gpu_hist: deque = deque([0] * maxlen, maxlen=maxlen)
        self._ane_hist: deque = deque([0] * maxlen, maxlen=maxlen)
        self._ram_hist: deque = deque([0] * maxlen, maxlen=maxlen)
        self._cpupwr_hist: deque = deque([0] * maxlen, maxlen=maxlen)
        self._gpupwr_hist: deque = deque([0] * maxlen, maxlen=maxlen)
        self._pkgpwr_hist: deque = deque([0] * maxlen, maxlen=maxlen)
        self._bw_hist: deque = deque([0] * maxlen, maxlen=maxlen)

        # Native-unit histories for the cur/avg/max label context (watts / GB/s).
        # The *pwr* / *bw* deques above hold chart percents; these hold real
        # units so the avg/max shown next to "CPU Power 12.3W" or "Mem BW
        # 120 GB/s" are in watts / GB/s, not percent.
        self._cpu_w_hist: deque = deque([0] * maxlen, maxlen=maxlen)
        self._gpu_w_hist: deque = deque([0] * maxlen, maxlen=maxlen)
        self._pkg_w_hist: deque = deque([0] * maxlen, maxlen=maxlen)
        self._bw_gbps_hist: deque = deque([0] * maxlen, maxlen=maxlen)

        # Count of real samples appended; histories are zero-padded for chart
        # right-alignment, so avg/max must ignore the leading padding.
        self._sample_count: int = 0

        self._cpu_peak_w: float = 0.0
        self._gpu_peak_w: float = 0.0

        # L2 alert / throttle / session-energy analytics. Owns the sustain
        # counters, swap-rise window, and cumulative energy integral formerly
        # kept in this widget; constructed from threshold values so analytics
        # stays TUI-config-agnostic.
        self._alert_engine = AlertEngine(
            bw_sat_percent=cfg.alert_bw_sat_percent,
            pkg_power_percent=cfg.alert_package_power_percent,
            throttle_freq_percent=cfg.alert_throttle_freq_percent,
            swap_rise_gb=cfg.alert_swap_rise_gb,
            sustain_samples=cfg.alert_sustain_samples,
            max_total_bw=cfg.max_cpu_bw + cfg.max_gpu_bw,
            package_ref_w=cfg.package_ref_w,
        )

        # Per-core history (dict: index -> deque)
        self._core_hist: dict = {}
        self._last_p_cores: list = []
        self._last_e_cores: list = []

    def compose(self) -> ComposeResult:
        cfg = self._config

        # Four titled section containers (border_title lives in the border, so
        # it costs no content row). Every child widget id is unchanged so the
        # update_metrics query paths keep working after sectionizing.
        with Vertical(id="section-cpu", classes="dash-section") as cpu_sec:
            cpu_sec.border_title = "CPU"
            with Vertical(classes="cpu-half"):
                yield Static(
                    "P-CPU   0% @0MHz",
                    id="pcpu-summary-row",
                    classes="cpu-summary-row",
                )
                yield BrailleChart(
                    glyph_mode=self._chart_glyph,
                    palette=self._palette,
                    id="pcpu-chart",
                    classes="metric-chart",
                )
                if cfg.show_cores:
                    yield Static("", id="pcores-grid", classes="core-grid")
                if cfg.show_residency:
                    yield Static("", id="pcpu-residency-row", classes="residency-row")
            with Vertical(classes="cpu-half"):
                yield Static(
                    "E-CPU   0% @0MHz",
                    id="ecpu-summary-row",
                    classes="cpu-summary-row",
                )
                yield BrailleChart(
                    glyph_mode=self._chart_glyph,
                    palette=self._palette,
                    id="ecpu-chart",
                    classes="metric-chart",
                )
                if cfg.show_cores:
                    yield Static("", id="ecores-grid", classes="core-grid")
                if cfg.show_residency:
                    yield Static("", id="ecpu-residency-row", classes="residency-row")

        with Vertical(id="section-gpu-ane", classes="dash-section") as gpu_sec:
            gpu_sec.border_title = "GPU · ANE"
            yield Static("GPU 0% @0MHz", id="gpu-label", classes="metric-label")
            yield BrailleChart(
                glyph_mode=self._chart_glyph,
                palette=self._palette,
                id="gpu-chart",
                classes="metric-chart",
            )
            if cfg.show_residency:
                yield Static("", id="gpu-residency-row", classes="residency-row")
            yield Static("ANE 0%", id="ane-label", classes="metric-label")
            yield BrailleChart(
                glyph_mode=self._chart_glyph,
                palette=self._palette,
                id="ane-chart",
                classes="metric-chart",
            )

        with Vertical(id="section-memory", classes="dash-section") as mem_sec:
            mem_sec.border_title = "Memory"
            yield Static("RAM 0%", id="ram-label", classes="metric-label")
            yield BrailleChart(
                glyph_mode=self._chart_glyph,
                palette=self._palette,
                id="ram-chart",
                classes="metric-chart",
            )
            # Memory bandwidth: shown only when the sampler exposes a DCS channel
            # (gated per-snapshot via SystemSnapshot.bandwidth_available).
            yield Static("Mem BW 0 GB/s", id="bw-label", classes="metric-label")
            yield BrailleChart(
                glyph_mode=self._chart_glyph,
                palette=self._palette,
                id="bw-chart",
                classes="metric-chart",
            )

        with Vertical(id="section-power", classes="dash-section") as pwr_sec:
            pwr_sec.border_title = "Power"
            # CPU/GPU power are single inline-spark rows (compact); Package keeps
            # the full 2-row chart. The *_hist percent deques still feed the
            # sparks and the *_w_hist watt deques still feed the avg/max suffix.
            yield Static("CPU 0.00W", id="cpupwr-row", classes="metric-label")
            yield Static("GPU 0.00W", id="gpupwr-row", classes="metric-label")
            yield Static("Package Power 0W", id="pkgpwr-label", classes="metric-label")
            yield BrailleChart(
                glyph_mode=self._chart_glyph,
                palette=self._palette,
                id="pkgpwr-chart",
                classes="metric-chart",
            )
            # Fan RPM: hidden entirely on fanless Macs (no chart — a single
            # tachometer reading doesn't warrant a sparkline like the power/BW
            # rows), gated per-snapshot via SystemSnapshot.fan_available.
            yield Static("Fan 0 RPM", id="fan-label", classes="metric-label")

    @property
    def layout_preset(self) -> str:
        """The requested preset (`grid` or `stack`), independent of width."""
        return self._requested_preset

    @property
    def effective_layout_preset(self) -> str:
        """The preset actually applied — `stack` when a requested grid is
        auto-degraded below `_GRID_MIN_WIDTH`, else same as `layout_preset`."""
        return self._effective_preset

    def set_layout_preset(self, name: str) -> None:
        """Switch the requested layout preset. Raises ValueError on unknown
        names. Never touches history deques, so switching mid-session loses no
        data. The effective preset is re-derived (width auto-degrade still
        applies)."""
        if name not in self._VALID_PRESETS:
            raise ValueError(
                "layout preset must be one of {}, got {!r}".format(
                    self._VALID_PRESETS, name
                )
            )
        self._requested_preset = name
        self._reconcile_layout()
        # A grid<->stack swap changes column widths; re-render the width-adaptive
        # rows once the new layout settles (see _refresh_width_adaptive_rows).
        self.call_after_refresh(self._refresh_width_adaptive_rows)

    def _reconcile_layout(self) -> None:
        """Apply the layout class for the requested preset, degrading a grid to
        stack when the widget is narrower than `_GRID_MIN_WIDTH`. Width is 0
        before the first layout pass; treat unknown width as wide (keep grid)."""
        preset = self._requested_preset
        width = self.size.width
        if preset == "grid" and 0 < width < self._GRID_MIN_WIDTH:
            preset = "stack"
        if preset != self._effective_preset:
            self.remove_class("layout-grid", "layout-stack")
            self.add_class("layout-{}".format(preset))
            self._effective_preset = preset

    def on_resize(self, event) -> None:
        self._reconcile_layout()
        # Adapt spark widths to the new terminal/column width immediately (incl.
        # a grid<->stack auto-degrade) rather than waiting for the next sample.
        self.call_after_refresh(self._refresh_width_adaptive_rows)

    def _refresh_width_adaptive_rows(self) -> None:
        """Re-render the Static rows whose spark width tracks the row width.

        BrailleChart re-renders itself on resize; the power sparks and core
        grids are imperatively-updated Static rows, so a width change (terminal
        resize or preset swap) leaves them at a stale width until this re-renders
        them. Safe before any sample: histories are zero-padded."""
        if not self.is_mounted:
            return
        self._render_power_rows()
        if getattr(self._config, "show_cores", False):
            self._update_core_two_col(
                "#pcores-grid", self._last_p_cores, "P", append_sample=False
            )
            self._update_core_two_col(
                "#ecores-grid", self._last_e_cores, "E", append_sample=False
            )

    @property
    def chart_glyph(self) -> str:
        return self._chart_glyph

    def set_chart_glyph(self, glyph_mode: str) -> None:
        self._chart_glyph = _normalize_chart_glyph_mode(glyph_mode)
        for chart in self.query(BrailleChart):
            chart.set_glyph_mode(self._chart_glyph)
        # The CPU/GPU power rows carry inline sparks (not BrailleChart widgets),
        # so re-render them here the same way the core grids are re-rendered.
        self._render_power_rows()
        if getattr(self._config, "show_cores", False):
            self._update_core_two_col(
                "#pcores-grid", self._last_p_cores, "P", append_sample=False
            )
            self._update_core_two_col(
                "#ecores-grid", self._last_e_cores, "E", append_sample=False
            )

    def update_metrics(self, message: MetricsUpdated) -> None:
        """Update all dashboard widgets from new metrics. Called by ActopApp."""
        s = message.snapshot
        cfg = self._config

        ecpu = clamp_percent(s.ecpu_util_pct)
        pcpu = clamp_percent(s.pcpu_util_pct)
        gpu = clamp_percent(s.gpu_util_pct)
        ane_pct = clamp_percent(s.ane_util_pct)
        ram_pct = clamp_percent(s.ram_used_percent)

        self._ecpu_hist.append(ecpu)
        self._pcpu_hist.append(pcpu)
        self._gpu_hist.append(gpu)
        self._ane_hist.append(ane_pct)
        self._ram_hist.append(ram_pct)
        self._cpu_w_hist.append(s.cpu_watts)
        self._gpu_w_hist.append(s.gpu_watts)
        self._sample_count += 1

        # Power percents
        self._cpu_peak_w = max(self._cpu_peak_w, s.cpu_watts)
        self._gpu_peak_w = max(self._gpu_peak_w, s.gpu_watts)
        cpu_pwr_pct = power_to_percent(
            power_w=s.cpu_watts,
            mode=cfg.power_scale,
            profile_ref_w=cfg.cpu_chart_ref_w,
            peak_w=self._cpu_peak_w,
            floor_w=DEFAULT_CPU_FLOOR_W,
        )
        if s.cpu_watts > 0 and cpu_pwr_pct == 0:
            cpu_pwr_pct = 1
        gpu_pwr_pct = power_to_percent(
            power_w=s.gpu_watts,
            mode=cfg.power_scale,
            profile_ref_w=cfg.gpu_chart_ref_w,
            peak_w=self._gpu_peak_w,
            floor_w=DEFAULT_GPU_FLOOR_W,
        )
        if s.gpu_watts > 0 and gpu_pwr_pct == 0:
            gpu_pwr_pct = 1
        self._cpupwr_hist.append(cpu_pwr_pct)
        self._gpupwr_hist.append(gpu_pwr_pct)

        # Package power chart percent (vs SoC reference rail); the same L2
        # normalisation the AlertEngine's PKG alert uses.
        pkg_pwr_pct = package_power_percent(s, cfg.package_ref_w)
        if s.package_watts > 0 and pkg_pwr_pct == 0:
            pkg_pwr_pct = 1
        self._pkgpwr_hist.append(pkg_pwr_pct)
        self._pkg_w_hist.append(s.package_watts)

        # Memory bandwidth chart percent (vs summed CPU+GPU channel capacity);
        # the same L2 normalisation the AlertEngine's BW alert uses.
        bw_pct = bandwidth_percent(s, cfg.max_cpu_bw + cfg.max_gpu_bw)
        if s.bandwidth_available and s.bandwidth_gbps > 0 and bw_pct == 0:
            bw_pct = 1  # nudge a tiny-but-nonzero draw off the floor for the chart
        self._bw_hist.append(bw_pct)
        self._bw_gbps_hist.append(s.bandwidth_gbps if s.bandwidth_available else 0.0)

        # Update charts
        chart_data = (
            ("#pcpu-chart", self._pcpu_hist),
            ("#ecpu-chart", self._ecpu_hist),
            ("#gpu-chart", self._gpu_hist),
            ("#ane-chart", self._ane_hist),
            ("#ram-chart", self._ram_hist),
            ("#bw-chart", self._bw_hist),
            ("#pkgpwr-chart", self._pkgpwr_hist),
        )
        for widget_id, data in chart_data:
            self.query_one(widget_id, BrailleChart).data = data

        # Update labels
        cpu_temp = " ({:.0f}°C)".format(s.cpu_temp_c) if s.cpu_temp_c > 0 else ""
        gpu_temp = " ({:.0f}°C)".format(s.gpu_temp_c) if s.gpu_temp_c > 0 else ""
        self._update_cluster_summary_row(
            "#pcpu-summary-row",
            "P-CPU",
            pcpu,
            s.pcpu_freq_mhz,
            cpu_temp,
            self._pct_stats_suffix(self._pcpu_hist),
        )
        self._update_cluster_summary_row(
            "#ecpu-summary-row",
            "E-CPU",
            ecpu,
            s.ecpu_freq_mhz,
            cpu_temp,
            self._pct_stats_suffix(self._ecpu_hist),
        )
        if cfg.show_residency:
            self.query_one("#pcpu-residency-row", Static).update(
                _format_residency_row("P-CPU", s.pcpu_residency_pct)
            )
            self.query_one("#ecpu-residency-row", Static).update(
                _format_residency_row("E-CPU", s.ecpu_residency_pct)
            )
        self.query_one("#gpu-label", Static).update(
            "GPU {}% @{}MHz{}{}".format(
                gpu, s.gpu_freq_mhz, gpu_temp, self._pct_stats_suffix(self._gpu_hist)
            )
        )
        if cfg.show_residency:
            self.query_one("#gpu-residency-row", Static).update(
                _format_residency_row("GPU", s.gpu_residency_pct)
            )
        self.query_one("#ane-label", Static).update(
            "ANE {}% ({:.1f}W){}".format(
                ane_pct, s.ane_watts, self._pct_stats_suffix(self._ane_hist)
            )
        )

        used_gb = s.ram_used_gb
        total_gb = s.ram_total_gb
        swap_used = s.swap_used_gb
        swap_total = s.swap_total_gb
        if (swap_total or 0.0) >= 0.1:
            ram_label = "RAM {}/{}GB sw:{}/{}GB".format(
                used_gb, total_gb, swap_used, swap_total
            )
        else:
            ram_label = "RAM {}/{}GB".format(used_gb, total_gb)
        ram_label += self._pct_stats_suffix(self._ram_hist)
        self.query_one("#ram-label", Static).update(ram_label)

        self._render_power_rows()
        self.query_one("#pkgpwr-label", Static).update(
            "Package Power {:.2f}W{}".format(
                s.package_watts, self._watt_stats_suffix(self._pkg_w_hist)
            )
        )

        # Memory bandwidth: hide the row entirely when the platform exposes no
        # DCS channel; otherwise show GB/s with rolling context. Availability is
        # effectively constant per session, so toggle display only on change.
        bw_label = self.query_one("#bw-label", Static)
        bw_chart = self.query_one("#bw-chart", BrailleChart)
        if bw_chart.display != s.bandwidth_available:
            bw_label.display = s.bandwidth_available
            bw_chart.display = s.bandwidth_available
        if s.bandwidth_available:
            bw_label.update(
                "Mem BW {:.1f} GB/s{}".format(
                    s.bandwidth_gbps, self._gbps_stats_suffix(self._bw_gbps_hist)
                )
            )

        # Fan RPM: hide the row entirely on fanless Macs (no SMC fan keys),
        # mirroring the Mem BW hide-on-unavailable pattern above.
        fan_label = self.query_one("#fan-label", Static)
        if fan_label.display != s.fan_available:
            fan_label.display = s.fan_available
        if s.fan_available:
            # Per fan: "current/max" when max is known, else bare "current".
            # Fans are joined with " · " so the inter-fan separator never
            # collides with the "/" inside a single fan's current/max.
            if s.fans:
                rpm_text = " · ".join(
                    "{:.0f}/{:.0f}".format(f.current, f.max)
                    if f.max
                    else "{:.0f}".format(f.current)
                    for f in s.fans
                )
            else:
                rpm_text = "0"
            fan_label.update("Fan {} RPM".format(rpm_text))

        # Update per-core rows
        if cfg.show_cores:
            self._last_p_cores = list(s.p_cores)
            self._last_e_cores = list(s.e_cores)
            self._update_core_two_col(
                "#pcores-grid", self._last_p_cores, "P", append_sample=True
            )
            self._update_core_two_col(
                "#ecores-grid", self._last_e_cores, "E", append_sample=True
            )

        # Compute and update status/alerts
        self._compute_alerts(s)

    _CORE_GRID_SEP = " │ "
    # History buffer depth (samples retained per metric/core). Must be >= the
    # widest a chart can render (one sample per terminal column) so a very wide
    # terminal never starves the sparkline. This is a space/width cap, not a
    # time window — deliberately independent of --avg. Bump it if you expect
    # terminals wider than this many columns.
    _CHART_HIST_MAXLEN = 500
    _CORE_HIST_MAXLEN = _CHART_HIST_MAXLEN
    _CORE_MIN_SPARK_CHARS = 3

    def _avg_max(self, hist) -> tuple[float, float]:
        """Rolling average (over avg_window) and session max for a history deque.

        Histories are zero-padded to a fixed length for chart right-alignment, so
        only the last `_sample_count` entries are real readings. Avg is taken over
        the configured `avg_window`; max is the peak across all real samples.
        """
        count = self._sample_count
        if count <= 0:
            return (0.0, 0.0)
        vals = list(hist)
        real_n = min(count, len(vals))
        if real_n <= 0:
            return (0.0, 0.0)
        avg_window = max(1, int(getattr(self._config, "avg_window", real_n)))
        avg_n = min(real_n, avg_window)
        avg_vals = vals[-avg_n:]
        peak_vals = vals[-real_n:]
        return (sum(avg_vals) / len(avg_vals), max(peak_vals))

    def _pct_stats_suffix(self, hist) -> str:
        """`  avg N% · max N%` context string for a percent-valued history.

        The unit is appended because the headline reading often carries a
        different unit (MHz, GB, W), so a bare number would be ambiguous — or,
        for the RAM row, read as GB instead of percent.
        """
        avg, mx = self._avg_max(hist)
        return "  avg {:.0f}% · max {:.0f}%".format(avg, mx)

    def _watt_stats_suffix(self, hist) -> str:
        """`  avg N.NW · max N.NW` context string for a watt-valued history."""
        avg, mx = self._avg_max(hist)
        return "  avg {:.1f}W · max {:.1f}W".format(avg, mx)

    def _gbps_stats_suffix(self, hist) -> str:
        """`  avg N.N · max N.N GB/s` context string for a bandwidth history."""
        avg, mx = self._avg_max(hist)
        return "  avg {:.1f} · max {:.1f} GB/s".format(avg, mx)

    def _update_cluster_summary_row(
        self,
        widget_id: str,
        label: str,
        util_pct: int,
        freq_mhz: int,
        cpu_temp: str,
        stats_suffix: str = "",
    ) -> None:
        """Render one full-width cluster summary line."""
        widget = self.query_one(widget_id, Static)
        avail = max(widget.size.width, 1)
        line = "{} {:3d}% @{}MHz{}{}".format(
            label, util_pct, freq_mhz, cpu_temp, stats_suffix
        )
        widget.update(line[:avail].ljust(avail))

    # Inline power spark bounds: keep the spark legible (>= 8 chars) but never
    # let a wide terminal turn a one-line row into a full chart (cap 24), the
    # same width discipline _format_core_entry applies to core sparks.
    _POWER_SPARK_MIN = 8
    _POWER_SPARK_MAX = 24

    def _render_power_rows(self) -> None:
        """Re-render both compact CPU/GPU power rows from current histories.

        Shared by update_metrics (fresh sample) and set_chart_glyph (glyph
        toggle); the headline watt value is the newest sample in each watt
        history deque (the value update_metrics just appended).
        """
        self._render_power_row(
            "#cpupwr-row",
            "CPU",
            self._cpu_w_hist[-1],
            self._cpupwr_hist,
            self._cpu_w_hist,
        )
        self._render_power_row(
            "#gpupwr-row",
            "GPU",
            self._gpu_w_hist[-1],
            self._gpupwr_hist,
            self._gpu_w_hist,
        )

    def _render_power_row(
        self, widget_id: str, label: str, watts: float, pct_hist, watt_hist
    ) -> None:
        """`CPU 6.59W <spark>  avg N.NW · max N.NW` compact power row.

        The spark fills the gap between the headline and the avg/max suffix,
        clamped to [_POWER_SPARK_MIN, _POWER_SPARK_MAX]; below the minimum the
        row drops the spark and keeps the numbers.
        """
        widget = self.query_one(widget_id, Static)
        avail = max(widget.size.width, 1)
        head = "{} {:.2f}W".format(label, watts)
        suffix = self._watt_stats_suffix(watt_hist)
        room = avail - len(head) - 1 - len(suffix)  # -1 for the space after head
        spark_w = max(0, min(self._POWER_SPARK_MAX, room))
        if spark_w >= self._POWER_SPARK_MIN:
            spark = _inline_spark(
                history=pct_hist, width_chars=spark_w, glyph_mode=self._chart_glyph
            )
            line = "{} {}{}".format(head, spark, suffix)
        else:
            line = "{}{}".format(head, suffix)
        widget.update(line[:avail].ljust(avail))

    def _format_core_entry(
        self, prefix: str, core, col_width: int, append_sample: bool = True
    ) -> str:
        """Format one core row, adapting spark width to the column."""
        if col_width <= 0:
            return ""
        hist = self._core_hist.setdefault(
            (prefix, core.index),
            deque(
                [0] * self._CORE_MIN_SPARK_CHARS,
                maxlen=self._CORE_HIST_MAXLEN,
            ),
        )
        if append_sample:
            hist.append(core.active_pct)
        base = "{}{:02d} {:3d}%".format(prefix, core.index, core.active_pct)
        if col_width <= len(base):
            return base[:col_width].ljust(col_width)
        max_spark_w = col_width - len(base) - 1
        spark_w = max(1, max_spark_w)
        spark = _inline_spark(
            history=hist, width_chars=spark_w, glyph_mode=self._chart_glyph
        )
        entry = "{} {}".format(base, spark)
        return entry[:col_width].ljust(col_width)

    def _update_core_two_col(
        self, widget_id: str, cores: list, prefix: str, append_sample: bool = True
    ) -> None:
        """Render one cluster's cores as two vertical columns with one divider."""
        widget = self.query_one(widget_id, Static)
        if not cores:
            widget.update("")
            return

        avail = max(widget.size.width, len(self._CORE_GRID_SEP) + 2)
        left_w = max(1, (avail - len(self._CORE_GRID_SEP)) // 2)
        right_w = max(1, avail - len(self._CORE_GRID_SEP) - left_w)

        rows = []
        for i in range(0, len(cores), 2):
            left = self._format_core_entry(
                prefix, cores[i], left_w, append_sample=append_sample
            )
            right = (
                self._format_core_entry(
                    prefix, cores[i + 1], right_w, append_sample=append_sample
                )
                if i + 1 < len(cores)
                else "".ljust(right_w)
            )
            rows.append("{}{}{}".format(left, self._CORE_GRID_SEP, right))
        widget.update("\n".join(rows))

    def _compute_alerts(self, s: SystemSnapshot) -> None:
        """Format the L2 alert frame into the status line (presentation only).

        All alert/throttle/energy math lives in analytics.AlertEngine; this
        method turns its AlertFrame into user-facing tokens.
        """
        cfg = self._config
        frame = self._alert_engine.feed(s)

        # Chart time window: charts plot one sample per character, so the
        # visible span scales silently with terminal width. Surface it.
        span_label = self._chart_window_label()

        active_alerts = []
        if frame.thermal_alert:
            active_alerts.append("THERMAL")
        throttled = [
            name
            for name, on in (("CPU", frame.cpu_throttle), ("GPU", frame.gpu_throttle))
            if on
        ]
        if throttled:
            active_alerts.append("THROTTLING:{}".format(",".join(throttled)))
        if frame.bw_alert:
            active_alerts.append("MEM-BOUND>{}%".format(cfg.alert_bw_sat_percent))
        if frame.swap_alert:
            active_alerts.append("SWAP+{:.1f}G".format(frame.swap_rise_gb))
        if frame.pkg_alert:
            active_alerts.append("PKG>{}%".format(cfg.alert_package_power_percent))
        alerts_str = ", ".join(active_alerts) if active_alerts else "none"

        status = "thermal: {}  alerts: {}".format(s.thermal_state, alerts_str)
        meta = []
        if span_label:
            meta.append("span {}".format(span_label))
        meta.append(
            "energy {}".format(self._format_session_energy(frame.session_energy_j))
        )
        if meta:
            status = "{}  ·  {}".format("  ·  ".join(meta), status)
        # Status line lives in app chrome now; hand the string up to ActopApp.
        self.post_message(AlertsComputed(status))

    def _format_session_energy(self, joules: float) -> str:
        """Cumulative session energy as `N.NWh` (or `N mWh` while still small)."""
        wh = joules / 3600.0
        if wh < 0.1:
            return "{:.0f}mWh".format(wh * 1000)
        return "{:.2f}Wh".format(wh)

    def _chart_window_label(self) -> str:
        """Visible time span of the charts, derived from a representative chart.

        All charts share one width and the sampling interval, so a single span
        token (placed on the status line) describes the whole grid. Returns ""
        before layout, when the chart width is not yet known.
        """
        try:
            width = self.query_one("#gpu-chart", BrailleChart).size.width
        except Exception:
            return ""
        if width <= 0:
            return ""
        interval = max(1, int(getattr(self._config, "sample_interval", 1)))
        return _format_window_span(width * interval)
