"""Textual widgets for the actop hardware dashboard."""

import os
from collections import deque

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Static

from actop.models import SystemSnapshot
from actop.power_scaling import (
    DEFAULT_CPU_FLOOR_W,
    DEFAULT_GPU_FLOOR_W,
    clamp_percent,
    power_to_percent,
)


_COOL_RGB = (66, 135, 245)  # blue
_HOT_RGB = (240, 70, 64)  # red

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


def _pct_to_rgb(pct: float) -> tuple[int, int, int]:
    """Interpolate 0-100 percent along the blue->red gradient to an RGB triple."""
    p = min(100.0, max(0.0, float(pct))) / 100.0
    r = round(_COOL_RGB[0] + (_HOT_RGB[0] - _COOL_RGB[0]) * p)
    g = round(_COOL_RGB[1] + (_HOT_RGB[1] - _COOL_RGB[1]) * p)
    b = round(_COOL_RGB[2] + (_HOT_RGB[2] - _COOL_RGB[2]) * p)
    return (r, g, b)


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


def _pct_to_color(pct: float, mode: str = "truecolor") -> str:
    """Map 0-100 percent to a Rich style string for the given color tier.

    Degrades the blue->red truecolor gradient across terminal capabilities:
    truecolor -> `rgb()`, 256-color -> nearest `color()` cube index, 16-color ->
    a named severity ramp, and `none` -> no style (NO_COLOR / dumb terminals).
    """
    if mode == "none":
        return ""
    if mode == "16":
        p = min(100.0, max(0.0, float(pct)))
        for threshold, name in _ANSI16_SEVERITY:
            if p < threshold:
                return name
        return _ANSI16_HOT
    r, g, b = _pct_to_rgb(pct)
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
        self, glyph_mode: str = "dots", color_mode: str = None, **kwargs
    ) -> None:
        super().__init__(**kwargs)
        self._data = []
        self._glyph_mode = _normalize_chart_glyph_mode(glyph_mode)
        # None => resolve lazily from the running app's console (and NO_COLOR)
        # once mounted; falls back to environment detection before then.
        self._color_mode = color_mode

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
                line_color = _pct_to_color(v, color_mode)
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

    def __init__(self, snapshot: SystemSnapshot, ram: dict, processes: dict) -> None:
        self.snapshot = snapshot
        self.ram = ram  # from get_ram_metrics_dict()
        self.processes = processes  # {"cpu": [...], "memory": [...]}
        super().__init__()


# Throttle detection gates (heuristics). A cluster is only "throttling" when it is
# working hard yet held below its DVFS ceiling while hot — an idle or power-capped
# cluster at low freq is not throttling. The thermal-pressure signal is the primary
# "hot" test; the die-temp gate is a fallback for machines whose SMC temps read 0.
_THROTTLE_UTIL_GATE = 80.0  # percent: cluster must be at least this busy
_THROTTLE_TEMP_C = 90.0  # °C: die-temp fallback when thermal_state stays Nominal


def _domain_throttling(util, freq, max_freq, temp, thermal_state, cfg) -> bool:
    """True when a silicon domain is busy + slow + hot (see gates above).

    slow = current freq below `alert_throttle_freq_percent`% of the DVFS ceiling.
    Returns False when the ceiling is unknown (max_freq <= 0) — the ratio is
    uncomputable, so we cannot claim throttling.
    """
    if max_freq <= 0:
        return False
    busy = util >= _THROTTLE_UTIL_GATE
    slow = freq < (cfg.alert_throttle_freq_percent / 100.0) * max_freq
    hot = thermal_state not in ("Nominal", "Unknown") or temp >= _THROTTLE_TEMP_C
    return busy and slow and hot


def _bandwidth_percent(snapshot, cfg) -> float:
    """Memory bandwidth as a percent of summed CPU+GPU channel capacity.

    Returns 0 when bandwidth is unavailable. Shared by the chart and the
    saturation alert so both normalise against the same reference.
    """
    total_bw_ref = max(cfg.max_cpu_bw + cfg.max_gpu_bw, 1.0)
    if not snapshot.bandwidth_available:
        return 0
    return clamp_percent(snapshot.bandwidth_gbps / total_bw_ref * 100)


def _package_power_percent(snapshot, cfg) -> float:
    """Package power as a percent of the SoC reference rail.

    Shared by the chart and the PKG alert so both normalise against the
    same reference.
    """
    return clamp_percent(snapshot.package_watts / max(cfg.package_ref_w, 1.0) * 100)


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

    def __init__(self, config, **kwargs) -> None:
        super().__init__(**kwargs)
        self._config = config
        cfg = config
        self._chart_glyph = getattr(cfg, "chart_glyph", "dots")

        maxlen = self._CHART_HIST_MAXLEN
        swap_maxlen = max(2, cfg.alert_sustain_samples + 1)

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

        self._swap_hist: deque = deque([], maxlen=swap_maxlen)

        self._cpu_peak_w: float = 0.0
        self._gpu_peak_w: float = 0.0

        self._high_bw_counter: int = 0
        self._high_pkg_counter: int = 0
        self._throttle_cpu_counter: int = 0
        self._throttle_gpu_counter: int = 0

        # Cumulative session energy (joules), integrated as package_watts ×
        # interval each frame — the "what did this run cost" readout, mirroring
        # Profiler.total_package_joules for the live TUI.
        self._session_joules: float = 0.0

        # Per-core history (dict: index -> deque)
        self._core_hist: dict = {}
        self._last_p_cores: list = []
        self._last_e_cores: list = []

    def compose(self) -> ComposeResult:
        cfg = self._config

        with Vertical(id="cpu-section"):
            with Vertical(classes="cpu-half"):
                yield Static(
                    "P-CPU   0% @0MHz",
                    id="pcpu-summary-row",
                    classes="cpu-summary-row",
                )
                yield BrailleChart(
                    glyph_mode=self._chart_glyph,
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
                    id="ecpu-chart",
                    classes="metric-chart",
                )
                if cfg.show_cores:
                    yield Static("", id="ecores-grid", classes="core-grid")
                if cfg.show_residency:
                    yield Static("", id="ecpu-residency-row", classes="residency-row")

        yield Static("GPU 0% @0MHz", id="gpu-label", classes="metric-label")
        yield BrailleChart(
            glyph_mode=self._chart_glyph, id="gpu-chart", classes="metric-chart"
        )
        if cfg.show_residency:
            yield Static("", id="gpu-residency-row", classes="residency-row")

        yield Static("ANE 0%", id="ane-label", classes="metric-label")
        yield BrailleChart(
            glyph_mode=self._chart_glyph, id="ane-chart", classes="metric-chart"
        )

        yield Static("RAM 0%", id="ram-label", classes="metric-label")
        yield BrailleChart(
            glyph_mode=self._chart_glyph, id="ram-chart", classes="metric-chart"
        )

        # Memory bandwidth: shown only when the sampler exposes a DCS channel
        # (gated per-snapshot in update_metrics via SystemSnapshot.bandwidth_available).
        yield Static("Mem BW 0 GB/s", id="bw-label", classes="metric-label")
        yield BrailleChart(
            glyph_mode=self._chart_glyph, id="bw-chart", classes="metric-chart"
        )

        yield Static("CPU Power 0W", id="cpupwr-label", classes="metric-label")
        yield BrailleChart(
            glyph_mode=self._chart_glyph, id="cpupwr-chart", classes="metric-chart"
        )

        yield Static("GPU Power 0W", id="gpupwr-label", classes="metric-label")
        yield BrailleChart(
            glyph_mode=self._chart_glyph, id="gpupwr-chart", classes="metric-chart"
        )

        yield Static("Package Power 0W", id="pkgpwr-label", classes="metric-label")
        yield BrailleChart(
            glyph_mode=self._chart_glyph, id="pkgpwr-chart", classes="metric-chart"
        )

        # Fan RPM: hidden entirely on fanless Macs (no chart — a single
        # tachometer reading doesn't warrant a sparkline like the power/BW
        # rows), gated per-snapshot via SystemSnapshot.fan_available.
        yield Static("Fan 0 RPM", id="fan-label", classes="metric-label")

        yield Static(
            "thermal: Nominal  alerts: none", id="status-line", classes="status-line"
        )

    @property
    def chart_glyph(self) -> str:
        return self._chart_glyph

    def set_chart_glyph(self, glyph_mode: str) -> None:
        self._chart_glyph = _normalize_chart_glyph_mode(glyph_mode)
        for chart in self.query(BrailleChart):
            chart.set_glyph_mode(self._chart_glyph)
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
        ram = message.ram
        cfg = self._config

        ecpu = clamp_percent(s.ecpu_util_pct)
        pcpu = clamp_percent(s.pcpu_util_pct)
        gpu = clamp_percent(s.gpu_util_pct)
        ane_pct = clamp_percent(s.ane_watts / cfg.ane_max_power * 100)
        ram_pct = clamp_percent(ram.get("used_percent", 0))

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

        # Package power chart percent (vs SoC reference rail), mirroring the
        # PKG alert normalisation in _compute_alerts.
        pkg_pwr_pct = _package_power_percent(s, cfg)
        if s.package_watts > 0 and pkg_pwr_pct == 0:
            pkg_pwr_pct = 1
        self._pkgpwr_hist.append(pkg_pwr_pct)
        self._pkg_w_hist.append(s.package_watts)
        self._session_joules += max(0.0, s.package_watts) * max(
            1, int(getattr(cfg, "sample_interval", 1))
        )

        # Memory bandwidth chart percent (vs summed CPU+GPU channel capacity),
        # mirroring the BW alert normalisation in _compute_alerts.
        bw_pct = _bandwidth_percent(s, cfg)
        if s.bandwidth_available and s.bandwidth_gbps > 0 and bw_pct == 0:
            bw_pct = 1  # nudge a tiny-but-nonzero draw off the floor for the chart
        self._bw_hist.append(bw_pct)
        self._bw_gbps_hist.append(s.bandwidth_gbps if s.bandwidth_available else 0.0)

        self._swap_hist.append(max(0.0, float(ram.get("swap_used_GB", 0.0) or 0.0)))

        # Update charts
        chart_data = (
            ("#pcpu-chart", self._pcpu_hist),
            ("#ecpu-chart", self._ecpu_hist),
            ("#gpu-chart", self._gpu_hist),
            ("#ane-chart", self._ane_hist),
            ("#ram-chart", self._ram_hist),
            ("#bw-chart", self._bw_hist),
            ("#cpupwr-chart", self._cpupwr_hist),
            ("#gpupwr-chart", self._gpupwr_hist),
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

        used_gb = ram.get("used_GB", 0.0)
        total_gb = ram.get("total_GB", 0.0)
        swap_used = ram.get("swap_used_GB", 0.0)
        swap_total = ram.get("swap_total_GB", 0.0)
        if (swap_total or 0.0) >= 0.1:
            ram_label = "RAM {}/{}GB sw:{}/{}GB".format(
                used_gb, total_gb, swap_used, swap_total
            )
        else:
            ram_label = "RAM {}/{}GB".format(used_gb, total_gb)
        ram_label += self._pct_stats_suffix(self._ram_hist)
        self.query_one("#ram-label", Static).update(ram_label)

        self.query_one("#cpupwr-label", Static).update(
            "CPU Power {:.2f}W{}".format(
                s.cpu_watts, self._watt_stats_suffix(self._cpu_w_hist)
            )
        )
        self.query_one("#gpupwr-label", Static).update(
            "GPU Power {:.2f}W{}".format(
                s.gpu_watts, self._watt_stats_suffix(self._gpu_w_hist)
            )
        )
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
            rpm_text = (
                "/".join("{:.0f}".format(rpm) for rpm in s.fan_rpms)
                if s.fan_rpms
                else "0"
            )
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
        self._compute_alerts(s, ram)

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

    def _compute_alerts(self, s: SystemSnapshot, ram: dict) -> None:
        """Compute alert flags and update the status line."""
        cfg = self._config

        # Bandwidth saturation: compare total BW to total capacity (cpu + gpu refs).
        # The old dashing TUI fired on the hottest individual channel; since
        # SystemSnapshot only exposes the aggregate total, we normalise against
        # the sum of cpu and gpu channel references — the closest equivalent.
        bw_pct = _bandwidth_percent(s, cfg)
        if s.bandwidth_available and bw_pct >= cfg.alert_bw_sat_percent:
            self._high_bw_counter += 1
        else:
            self._high_bw_counter = 0
        bw_alert = self._high_bw_counter >= cfg.alert_sustain_samples

        # Package power
        pkg_pct = _package_power_percent(s, cfg)
        if pkg_pct >= cfg.alert_package_power_percent:
            self._high_pkg_counter += 1
        else:
            self._high_pkg_counter = 0
        pkg_alert = self._high_pkg_counter >= cfg.alert_sustain_samples

        # Thermal throttle, per silicon domain (P-cluster CPU, GPU): busy + held
        # below the DVFS ceiling + hot. Sustained like the other alerts.
        if _domain_throttling(
            s.pcpu_util_pct,
            s.pcpu_freq_mhz,
            s.pcpu_max_freq_mhz,
            s.cpu_temp_c,
            s.thermal_state,
            cfg,
        ):
            self._throttle_cpu_counter += 1
        else:
            self._throttle_cpu_counter = 0
        cpu_throttle = self._throttle_cpu_counter >= cfg.alert_sustain_samples

        if _domain_throttling(
            s.gpu_util_pct,
            s.gpu_freq_mhz,
            s.gpu_max_freq_mhz,
            s.gpu_temp_c,
            s.thermal_state,
            cfg,
        ):
            self._throttle_gpu_counter += 1
        else:
            self._throttle_gpu_counter = 0
        gpu_throttle = self._throttle_gpu_counter >= cfg.alert_sustain_samples

        # Swap rise
        swap_history_points = cfg.alert_sustain_samples + 1
        swap_rise = (
            max(0.0, self._swap_hist[-1] - self._swap_hist[0])
            if len(self._swap_hist) > 1
            else 0.0
        )
        swap_total = float(ram.get("swap_total_GB", 0.0) or 0.0)
        swap_alert = (
            swap_total >= 0.1
            and len(self._swap_hist) >= swap_history_points
            and swap_rise >= cfg.alert_swap_rise_gb
        )

        # Chart time window: charts plot one sample per character, so the
        # visible span scales silently with terminal width. Surface it.
        span_label = self._chart_window_label()

        # Thermal
        thermal_alert = s.thermal_state not in ("Nominal", "Unknown")

        active_alerts = []
        if thermal_alert:
            active_alerts.append("THERMAL")
        throttled = [
            name for name, on in (("CPU", cpu_throttle), ("GPU", gpu_throttle)) if on
        ]
        if throttled:
            active_alerts.append("THROTTLING:{}".format(",".join(throttled)))
        if bw_alert:
            active_alerts.append("MEM-BOUND>{}%".format(cfg.alert_bw_sat_percent))
        if swap_alert:
            active_alerts.append("SWAP+{:.1f}G".format(swap_rise))
        if pkg_alert:
            active_alerts.append("PKG>{}%".format(cfg.alert_package_power_percent))
        alerts_str = ", ".join(active_alerts) if active_alerts else "none"

        status = "thermal: {}  alerts: {}".format(s.thermal_state, alerts_str)
        meta = []
        if span_label:
            meta.append("span {}".format(span_label))
        meta.append("energy {}".format(self._format_session_energy()))
        if meta:
            status = "{}  ·  {}".format("  ·  ".join(meta), status)
        self.query_one("#status-line", Static).update(status)

    def _format_session_energy(self) -> str:
        """Cumulative session energy as `N.NWh` (or `N mWh` while still small)."""
        wh = self._session_joules / 3600.0
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
