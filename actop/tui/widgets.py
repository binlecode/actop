"""Textual widgets for the actop hardware dashboard."""

import os
from collections import deque

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Static

from actop.analytics import (
    DISK_RATE_FLOOR_BPS,
    NET_RATE_FLOOR_BPS,
    AlertEngine,
    bandwidth_percent,
    io_rate_percent,
    package_power_percent,
)
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
# through (deliberately deferred — see docs/SPEC-system.md §5.2).
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
# Same cumulative pole for the right column: dots 8 / 8+6 / 8+6+5 / 8+6+5+4. Used
# so `dots` mode can pack a second time sample into each cell's right column,
# doubling horizontal density (btop-style) instead of leaving it blank.
_BRAILLE_FILL_BITS_R = [0x80, 0xA0, 0xB0, 0xB8]
_BRAILLE_FULL_R = 0xB8  # all 4 right-column dots
# Mirror poles for a top-down fill (the `down` half of the I/O mirror charts):
# the same cumulative ramps accumulating from the top dot instead of the bottom
# \u2014 left dots 1 / 1+2 / 1+2+3 / 1+2+3+7, right dots 4 / 4+5 / 4+5+6 / 4+5+6+8.
# The all-4-dots value is necessarily the same in both directions.
_BRAILLE_FILL_BITS_DOWN = [0x01, 0x03, 0x07, 0x47]
_BRAILLE_FILL_BITS_DOWN_R = [0x08, 0x18, 0x38, 0xB8]
_BRAILLE_BLANK = "\u2800"
_BLOCK_FILL_GLYPHS = ["\u2582", "\u2584", "\u2586", "\u2588"]
# Top-down block ramp. Block Elements only ships two upper-fill glyphs that are
# universally available in terminal fonts \u2014 \u2580 (half) and \u2588 (full) \u2014 so the
# downward half quantizes to 2 levels per row instead of 4, mapped to the
# nearest available fill. The 1/4 and 3/4 upper blocks live in Symbols for
# Legacy Computing (U+1FB82/U+1FB85), which many fonts render as tofu; a coarser
# but always-legible ramp beats a finer one that may not draw at all.
_BLOCK_FILL_GLYPHS_DOWN = ["\u2580", "\u2580", "\u2588", "\u2588"]
_BLOCK_FULL_GLYPH = "\u2588"
_BLOCK_BLANK = " "
# Fill directions a chart can render in. `up` is the normal bottom-anchored
# sparkline; `down` hangs the trace from the top edge so a `down` chart placed
# directly beneath an `up` one forms a mirrored pair sharing an implicit zero
# axis where they meet (see the Network / Disk sections).
_FILL_UP = "up"
_FILL_DOWN = "down"

# Fan row spinner: one braille-cascade glyph per fan, prefixed to its RPM
# reading (same 10-frame cascade as the splash screen's _SPINNER_FRAMES in
# tui/app.py, so the two spinners in the app share one glyph family). Spin
# rate is directly proportional to RPM (steps/sec = rpm / _FAN_SPINNER_RPM_PER_STEP)
# via its own timer (HardwareDashboard._tick_fan_spinners), decoupled from the
# sampler poll cadence so a fan spinning twice as fast visibly cycles twice as
# fast instead of jumping once per multi-second sample.
_FAN_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
_FAN_SPINNER_RPM_PER_STEP = 1000.0
_FAN_SPINNER_TICK_SECONDS = 0.2


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
        return f"color({idx})"
    return f"rgb({r},{g},{b})"


def _to_gib(byte_count) -> float:
    """Bytes → GiB (2^30) at one decimal, for display.

    The snapshot carries exact byte counts; prefixing is a presentation choice
    and binary prefixes are what modern monitors use for memory (btop, bottom,
    `free -h`, nvidia-smi). Bandwidth is the deliberate exception — it stays
    decimal GB/s because that is how Apple specifies the bus.
    """
    return round((byte_count or 0) / 1024 / 1024 / 1024, 1)


def _format_bps(bps: float) -> str:
    """Bytes/s → human-readable (B/s, KB/s, MB/s, GB/s) at 1 decimal.

    Uses decimal prefixes (1 KB = 1000 B) consistently with how network and
    disk tools report throughput (ifconfig, iostat, Activity Monitor).
    """
    bps = max(0.0, bps)
    if bps < 1000:
        return f"{bps:.0f} B/s"
    if bps < 1_000_000:
        return f"{bps / 1_000:.1f} KB/s"
    if bps < 1_000_000_000:
        return f"{bps / 1_000_000:.1f} MB/s"
    return f"{bps / 1_000_000_000:.2f} GB/s"


def _format_window_span(seconds: float) -> str:
    """Format a chart's visible time span (e.g. `45s`, `2m08s`, `1h05m`)."""
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{secs:02d}s" if secs else f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m" if minutes else f"{hours}h"


def _normalize_chart_glyph_mode(value: str) -> str:
    return "block" if str(value).strip().lower() == "block" else "dots"


def _glyph_set_for_mode(mode: str, fill: str = _FILL_UP) -> tuple[str, str, list[str]]:
    normalized = _normalize_chart_glyph_mode(mode)
    down = fill == _FILL_DOWN
    if normalized == "block":
        glyphs = _BLOCK_FILL_GLYPHS_DOWN if down else _BLOCK_FILL_GLYPHS
        return (_BLOCK_BLANK, _BLOCK_FULL_GLYPH, glyphs)
    bits = _BRAILLE_FILL_BITS_DOWN if down else _BRAILLE_FILL_BITS
    return (
        _BRAILLE_BLANK,
        chr(0x2800 | _BRAILLE_FULL),
        [chr(0x2800 | b) for b in bits],
    )


def _clamped_value_and_level(value: float, total_levels: int) -> tuple[float, int]:
    v = min(100.0, max(0.0, float(value)))
    level = max(0, min(total_levels, round(v / 100 * total_levels)))
    if v > 0 and level == 0:
        level = 1
    return (v, level)


def _braille_column_bits(
    level: int, row: int, height: int, fill, full: int, down: bool = False
) -> int:
    """Braille bits for one vertical dot column of the cell at terminal `row`.

    `level` is the sample's 0..height*4 fill height. `fill`/`full` are the
    cumulative-partial list and all-4-dots value for the target column
    (left = `_BRAILLE_FILL_BITS`/`_BRAILLE_FULL`, right = the `*_R` pair).
    `down` anchors the fill at the top edge instead of the bottom — the trace
    hangs downward, mirroring the normal sparkline about its top row.
    """
    if level <= 0:
        return 0
    if down:
        dot_row = (level - 1) // 4
        if row < dot_row:
            return full
    else:
        dot_row = height - 1 - (level - 1) // 4
        if row > dot_row:
            return full
    if row == dot_row:
        return fill[(level - 1) % 4]
    return 0


def _braille_cell_bits(
    llevel: int, rlevel: int, row: int, height: int, down: bool = False
) -> int:
    """Braille bits for one dense cell: left column = earlier sample `llevel`,
    right column = later sample `rlevel`. Single source of truth for the 2-sample
    packing shared by BrailleChart._render_dots (height rows) and _inline_spark
    (height 1). `llevel`/`rlevel` are 0..height*4 fill heights; `down` renders
    the mirrored, top-anchored fill.
    """
    lfill = _BRAILLE_FILL_BITS_DOWN if down else _BRAILLE_FILL_BITS
    rfill = _BRAILLE_FILL_BITS_DOWN_R if down else _BRAILLE_FILL_BITS_R
    return _braille_column_bits(
        llevel, row, height, lfill, _BRAILLE_FULL, down
    ) | _braille_column_bits(rlevel, row, height, rfill, _BRAILLE_FULL_R, down)


def _value_to_cell_glyph(value: float, glyph_mode: str) -> str:
    blank_glyph, _, partial_glyphs = _glyph_set_for_mode(glyph_mode)
    _, level = _clamped_value_and_level(value, total_levels=4)
    if level <= 0:
        return blank_glyph
    return partial_glyphs[level - 1]


def _inline_spark(history, width_chars: int = 8, glyph_mode: str = "dots") -> str:
    """Inline single-row sparkline sharing BrailleChart's glyph logic.

    `block` keeps 1 sample per character (block glyphs can't split horizontally).
    `dots` packs 2 samples per character — left/right braille dot column — for
    the same btop-style density as BrailleChart._render_dots, so `width_chars`
    characters show `width_chars * 2` samples.
    """
    if width_chars <= 0:
        return ""
    if _normalize_chart_glyph_mode(glyph_mode) == "block":
        n = width_chars
        vals = list(history)[-n:]
        vals = [0.0] * (n - len(vals)) + vals
        return "".join(_value_to_cell_glyph(v, glyph_mode) for v in vals)
    n = width_chars * 2
    vals = list(history)[-n:]
    vals = [0.0] * (n - len(vals)) + vals
    out = []
    for i in range(0, len(vals), 2):
        _, llevel = _clamped_value_and_level(vals[i], total_levels=4)
        _, rlevel = _clamped_value_and_level(vals[i + 1], total_levels=4)
        bits = _braille_cell_bits(llevel, rlevel, 0, 1)
        out.append(chr(0x2800 | bits) if bits else _BRAILLE_BLANK)
    return "".join(out)


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
        color_mode: str | None = None,
        palette: str = _DEFAULT_PALETTE,
        fill: str = _FILL_UP,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._data = []
        self._glyph_mode = _normalize_chart_glyph_mode(glyph_mode)
        # Fill direction, fixed at construction: `down` hangs the trace from the
        # top edge so this chart mirrors an `up` chart stacked above it.
        if fill not in (_FILL_UP, _FILL_DOWN):
            raise ValueError(
                f"fill must be {_FILL_UP!r} or {_FILL_DOWN!r}, got {fill!r}"
            )
        self._fill = fill
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
        if self._glyph_mode == "block":
            return self._render_block(width, height, color_mode)
        return self._render_dots(width, height, color_mode)

    def _render_dots(self, width: int, height: int, color_mode: str):
        """Braille render packing 2 time samples per character (btop density).

        Each cell's left dot column carries the earlier sample and its right
        column the later one, so `width` characters show `width * 2` samples with
        no blank gap between them. The cell takes the color of its hotter sample.
        """
        n = width * 2  # 2 samples per character
        dlen = len(self._data)
        offset = dlen - n
        total = height * 4  # 4 dot positions per terminal row
        out = Text()
        for row in range(height):
            for col in range(width):
                li = offset + 2 * col
                ri = li + 1
                lv, llevel = _clamped_value_and_level(
                    float(self._data[li]) if li >= 0 else 0.0, total_levels=total
                )
                rv, rlevel = _clamped_value_and_level(
                    float(self._data[ri]) if ri >= 0 else 0.0, total_levels=total
                )
                bits = _braille_cell_bits(
                    llevel, rlevel, row, height, self._fill == _FILL_DOWN
                )
                if bits:
                    line_color = _pct_to_color(max(lv, rv), color_mode, self._palette)
                    out.append(chr(0x2800 | bits), style=line_color)
                else:
                    out.append(_BRAILLE_BLANK)
            if row < height - 1:
                out.append("\n")
        return out

    def _render_block(self, width: int, height: int, color_mode: str):
        """Block-glyph render: 1 time sample per character (no column packing)."""
        down = self._fill == _FILL_DOWN
        blank_glyph, full_glyph, partial_glyphs = _glyph_set_for_mode(
            self._glyph_mode, self._fill
        )
        n = width  # 1 sample per character
        dlen = len(self._data)
        offset = dlen - n
        total = height * 4  # 4 sub-rows per terminal row
        out = Text()
        for row in range(height):
            for col in range(width):
                i = offset + col
                raw_v = float(self._data[i]) if i >= 0 else 0.0
                v, level = _clamped_value_and_level(raw_v, total_levels=total)
                line_color = _pct_to_color(v, color_mode, self._palette)
                if level > 0:
                    dot_row = (
                        (level - 1) // 4 if down else height - 1 - (level - 1) // 4
                    )
                    if (row < dot_row) if down else (row > dot_row):
                        # between the peak row and the anchored edge: full cell
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


# Rolling-context suffix glyphs for the compact tiers: ⌀ = rolling average,
# ▲ = session peak. The spelled-out `avg N · max N` stays the widest tier (what
# any roomy terminal shows); the glyphs appear only once a column is too narrow
# for it. Documented in the `?` help overlay under "Metric labels".
_AVG_GLYPH = "⌀"
_MAX_GLYPH = "▲"


def _fit_suffix(measured: tuple[int, ...], tiers: tuple[str, ...], width: int) -> str:
    """Widest rolling-context tier that fits beside the headline.

    A 96-col grid gives each box ~44 content columns — too few for
    `  avg N% · max N%` next to a full `P-CPU  41% @3200MHz (52°C)` headline,
    which used to cost the row its trailing characters (a clipped `%`, a
    residency row losing `high37`). Degrade the context instead: the headline is
    the reading, avg/max is its annotation, so the annotation gives up width
    first and drops out entirely before the reading is ever touched.

    `measured` is `(head_width, *tier_widths)` — the widths the row is fitted
    against, which are the high-water marks rather than this frame's actual
    lengths (see `_stable_widths`); the returned tier is still rendered with the
    live values. `width <= 0` means the row has no layout to measure yet — a
    section that this very frame is revealing (Network / Disk start hidden).
    Take the narrowest tier there: it cannot overflow whatever width the row
    turns out to have, and the next sample replaces it with the real fit.
    """
    if width <= 0:
        return tiers[-1]
    head_width = measured[0]
    for tier, tier_width in zip(tiers, measured[1:]):
        if head_width + tier_width <= width:
            return tier
    return ""


_RESIDENCY_ORDER = ("idle", "low", "mid", "high")
_RESIDENCY_GLYPHS = {"idle": "░", "low": "▒", "mid": "▓", "high": "█"}
# The residency bar shrinks from _RESIDENCY_BAR_WIDTH toward _RESIDENCY_BAR_MIN
# to fit its column, and is dropped below that (a 4-6 char density bar reads as
# noise, not a distribution). The four percentages are the data and the bar is
# only their picture, so the picture pays for the width — the breakdown is never
# truncated.
_RESIDENCY_BAR_WIDTH = 16
_RESIDENCY_BAR_MIN = 8
# The bar is sized against the widest the breakdown can ever get, not against
# this frame's: the four buckets sum to 100, so the worst case is four two-digit
# values (a 100 forces the other three to 0). Budgeting for it keeps the bar a
# fixed width per row width, instead of breathing a column or two every time a
# percentage crosses 10 and dragging the numbers sideways with it.
_RESIDENCY_BREAKDOWN_MAX = len("idle25 low25 mid25 high25")


def _residency_bar_widths(percentages: dict, bar_width: int) -> dict:
    """Largest-remainder allocation of `bar_width` chars across buckets.

    Plain per-bucket rounding can under/overshoot the total width (gaps or
    overflow) when percentages don't divide evenly; this guarantees the
    allocated widths sum to exactly `bar_width`.
    """
    if bar_width <= 0:
        return dict.fromkeys(_RESIDENCY_ORDER, 0)
    raw = {
        name: percentages.get(name, 0) / 100.0 * bar_width for name in _RESIDENCY_ORDER
    }
    floors = {name: int(raw[name]) for name in _RESIDENCY_ORDER}
    remainder = bar_width - sum(floors.values())
    fracs = sorted(_RESIDENCY_ORDER, key=lambda n: raw[n] - floors[n], reverse=True)
    for name in fracs[: max(0, remainder)]:
        floors[name] += 1
    return floors


def _format_residency_bar(percentages: dict, bar_width: int) -> str:
    """Fixed-width proportional block-density bar for one cluster/domain."""
    widths = _residency_bar_widths(percentages, bar_width)
    return "".join(_RESIDENCY_GLYPHS[name] * widths[name] for name in _RESIDENCY_ORDER)


def _format_residency_row(label: str, percentages: dict, width: int = 0) -> str:
    """`P-CPU  [bar]  idleN lowN midN highN` DVFS residency summary line.

    `width` is the row's available columns (0 when it has not been laid out yet,
    which takes the full bar). The bar gets whatever a worst-case breakdown
    leaves behind and is dropped below `_RESIDENCY_BAR_MIN`, so the four
    percentages survive a 44-column grid cell intact instead of losing `high37`
    to a clip.
    """
    breakdown = " ".join(
        f"{name}{percentages.get(name, 0)}" for name in _RESIDENCY_ORDER
    )
    prefix = f"{label:<6} "
    bar_width = _RESIDENCY_BAR_WIDTH
    if width > 0:
        # 4 = the brackets around the bar plus the two spaces before breakdown.
        room = width - len(prefix) - _RESIDENCY_BREAKDOWN_MAX - 4
        bar_width = min(bar_width, room)
    if bar_width < _RESIDENCY_BAR_MIN:
        return f"{prefix}{breakdown}"
    bar = _format_residency_bar(percentages, bar_width)
    return f"{prefix}[{bar}]  {breakdown}"


class HardwareDashboard(Widget):
    """Hardware metrics panel: CPU/GPU/ANE/RAM/Power charts + status line."""

    # Dashboard CSS lives here (scoped to this widget), not in ActopApp: the two
    # layout presets are just a class swap on this widget. `grid` is a two-column
    # grid — the P-CPU / E-CPU cluster boxes share the top row, GPU·ANE / Memory
    # the second, Network / Disk the third (either or both hidden when the
    # platform exposes no counters), and Power spans the full width beneath
    # them; `stack` is the
    # single scrollable column (only the stack preset scrolls — grid is sized to
    # fit). Below `_GRID_MIN_WIDTH` cols grid auto-degrades to stack (`on_resize`),
    # so a grid never squeezes its columns below readability.
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
        grid-rows: auto auto auto auto;
    }
    /* Power is a single wide chart, so it spans both columns on the bottom row
       instead of leaving a half-empty cell beside it. */
    HardwareDashboard.layout-grid #section-power {
        column-span: 2;
    }
    /* Keep each row's paired boxes vertically boundary-aligned: fill the row
       height (which the grid sizes to the taller box) so both bottom borders
       line up instead of the shorter box closing early with a ragged edge.
       The cost is blank space inside the shorter box — preferred over a
       ragged grid, and matches how Power's row already spans full width. */
    HardwareDashboard.layout-grid #section-pcpu,
    HardwareDashboard.layout-grid #section-ecpu,
    HardwareDashboard.layout-grid #section-gpu-ane,
    HardwareDashboard.layout-grid #section-memory,
    HardwareDashboard.layout-grid #section-net,
    HardwareDashboard.layout-grid #section-disk {
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
        # Per-core panels are a runtime toggle (`c` key), not compose-time only:
        # the core grids are always composed but start hidden unless --show_cores
        # was passed, so the P-CPU / E-CPU / GPU charts read as the prominent
        # sibling boxes by default and cores can be summoned on demand.
        self._show_cores = bool(getattr(cfg, "show_cores", False))
        # Gradient palette, fixed for the session (--palette). Passed eagerly to
        # every chart at compose time, exactly like _chart_glyph.
        self._palette = getattr(cfg, "palette", _DEFAULT_PALETTE)
        # Color tier for the %-readout and fan-glyph tinting. None => resolve
        # lazily from the running app's console once mounted (like BrailleChart),
        # falling back to environment detection before then.
        self._color_mode = None

        requested = getattr(cfg, "layout", "grid")
        if requested not in self._VALID_PRESETS:
            raise ValueError(
                f"layout preset must be one of {self._VALID_PRESETS}, got {requested!r}"
            )
        # Requested preset is what the user/CLI asked for; effective is what is
        # actually applied after the width auto-degrade. They differ only when a
        # grid is squeezed below _GRID_MIN_WIDTH.
        self._requested_preset = requested
        self._effective_preset = requested
        self.add_class(f"layout-{requested}")

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
        self._net_rx_hist: deque = deque([0] * maxlen, maxlen=maxlen)
        self._net_tx_hist: deque = deque([0] * maxlen, maxlen=maxlen)
        self._disk_read_hist: deque = deque([0] * maxlen, maxlen=maxlen)
        self._disk_write_hist: deque = deque([0] * maxlen, maxlen=maxlen)

        # Chart percents for the I/O rates above, normalised against a rolling
        # peak (see _append_io_percents) — the same native/percent deque pairing
        # as _bw_gbps_hist / _bw_hist.
        self._net_rx_pct_hist: deque = deque([0] * maxlen, maxlen=maxlen)
        self._net_tx_pct_hist: deque = deque([0] * maxlen, maxlen=maxlen)
        self._disk_read_pct_hist: deque = deque([0] * maxlen, maxlen=maxlen)
        self._disk_write_pct_hist: deque = deque([0] * maxlen, maxlen=maxlen)

        # Count of real samples appended; histories are zero-padded for chart
        # right-alignment, so avg/max must ignore the leading padding.
        self._sample_count: int = 0

        # L2 alert / throttle / session-energy analytics. Owns the sustain
        # counters, swap-rise window, and cumulative energy integral formerly
        # kept in this widget; constructed from threshold values so analytics
        # stays TUI-config-agnostic.
        self._alert_engine = AlertEngine(
            bw_sat_percent=cfg.alert_bw_sat_percent,
            pkg_power_percent=cfg.alert_package_power_percent,
            throttle_freq_percent=cfg.alert_throttle_freq_percent,
            swap_rise_gib=cfg.alert_swap_rise_gib,
            sustain_samples=cfg.alert_sustain_samples,
            max_total_bw=cfg.max_mem_bw,
            package_ref_w=cfg.package_ref_w,
        )

        # High-water (head, *tier) widths per metric row, keyed by widget id —
        # what the width-adaptive avg/max context is fitted against so a row
        # holds its form instead of flipping on a digit (see _stable_widths).
        self._row_widths: dict = {}

        # Per-core history (dict: index -> deque)
        self._core_hist: dict = {}
        self._last_p_cores: list = []
        self._last_e_cores: list = []

        # Fan spinner state: one entry per fan in the latest snapshot. Advanced
        # by its own timer (on_mount) rather than update_metrics, so spin rate
        # tracks RPM continuously instead of jumping once per sample.
        self._last_fans: list = []
        self._fan_spin_idx: list = []
        self._fan_spin_acc: list = []

    def on_mount(self) -> None:
        self.set_interval(_FAN_SPINNER_TICK_SECONDS, self._tick_fan_spinners)
        if self._color_mode is None:
            self._color_mode = resolve_color_mode(getattr(self.app, "console", None))

    def _active_color_mode(self) -> str:
        if self._color_mode is not None:
            return self._color_mode
        return resolve_color_mode()

    def _util_color(self, pct: float) -> str:
        """Chart color for a percent, for the readout text that names that value.

        The headline % in each metric row (and the fan spinner glyph) wears the
        same color as the sparkline that traces it — the chart paints the
        hottest sample's color, so a 90% reading shows the same red the chart
        does for that column, and an idle 5% stays at the cool end. NO_COLOR /
        dumb terminals degrade to an empty style (no tint), like the charts.
        """
        return _pct_to_color(pct, self._active_color_mode(), self._palette)

    def _tick_fan_spinners(self) -> None:
        if not self._last_fans:
            return
        frames = _FAN_SPINNER_FRAMES
        n = len(frames)
        advanced = False
        for i, fan in enumerate(self._last_fans):
            self._fan_spin_acc[i] += (
                fan.current * _FAN_SPINNER_TICK_SECONDS / _FAN_SPINNER_RPM_PER_STEP
            )
            steps = int(self._fan_spin_acc[i])
            if steps:
                self._fan_spin_acc[i] -= steps
                self._fan_spin_idx[i] = (self._fan_spin_idx[i] + steps) % n
                advanced = True
        if advanced:
            self._render_fan_label()

    def _fan_util_percent(self) -> float | None:
        """Average fan speed as a percent of max across fans that report a max.

        Fans without a max-RPM key contribute nothing (a lone unknown-max fan
        must not floor the colour to 0). None when no fan exposes a max — the
        glyph then renders untinted rather than inventing a severity.
        """
        ratios = [
            fan.current / fan.max for fan in self._last_fans if fan.max and fan.max > 0
        ]
        if not ratios:
            return None
        return sum(ratios) / len(ratios) * 100.0

    def _render_fan_label(self) -> None:
        frames = _FAN_SPINNER_FRAMES
        util = self._fan_util_percent()
        glyph_style = self._util_color(util) if util is not None else ""
        if self._last_fans:
            rpm_text = Text()
            for i, fan in enumerate(self._last_fans):
                if i:
                    rpm_text.append(" · ")
                # The spinner glyph wears the chart's color for the fan's
                # utilization (high RPM = red, idle = cool), matching the % and
                # sparkline treatment; the RPM figures stay plain.
                rpm_text.append(
                    frames[self._fan_spin_idx[i] % len(frames)], style=glyph_style
                )
                rpm_text.append(
                    f" {fan.current:.0f}/{fan.max:.0f}"
                    if fan.max
                    else f" {fan.current:.0f}"
                )
        else:
            rpm_text = Text("0")
        line = Text("Fan ")
        line.append_text(rpm_text)
        line.append(" RPM")
        self.query_one("#fan-label", Static).update(line)

    def compose(self) -> ComposeResult:
        cfg = self._config

        # Five titled section containers (border_title lives in the border, so
        # it costs no content row). The P-CPU and E-CPU clusters are now separate
        # sibling boxes (not two halves of one "CPU" box) so each cluster's chart
        # stands out next to GPU. Every child widget id is unchanged so the
        # update_metrics query paths keep working; the core grids are always
        # composed and their display is toggled by `_show_cores` (the `c` key).
        with Vertical(id="section-pcpu", classes="dash-section") as pcpu_sec:
            pcpu_sec.border_title = "P-CPU"
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
            pcores = Static("", id="pcores-grid", classes="core-grid")
            pcores.display = self._show_cores
            yield pcores
            if cfg.show_residency:
                yield Static("", id="pcpu-residency-row", classes="residency-row")

        with Vertical(id="section-ecpu", classes="dash-section") as ecpu_sec:
            ecpu_sec.border_title = "E-CPU"
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
            ecores = Static("", id="ecores-grid", classes="core-grid")
            ecores.display = self._show_cores
            yield ecores
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
            # Renderer/Tiler split from the driver's IOAccelerator statistics:
            # shader/compute work vs geometry work. Hidden entirely when the
            # accelerator exposes no statistics, gated per-snapshot via
            # SystemSnapshot.gpu_perf_stats_available (the same hide-row pattern
            # as Mem BW / Fan below). Device Utilization % is deliberately not
            # shown — the GPU row above already carries the headline number, and
            # a second, differently-measured whole-GPU percent next to it reads
            # as a contradiction. It stays available via the API and exports.
            # Starts hidden rather than showing a placeholder: the first frame
            # reveals it if the accelerator reports statistics (an empty muted
            # line is pure noise, unlike the "Mem BW 0 GB/s" placeholder below).
            rt_row = Static("", id="gpu-rt-row", classes="metric-label")
            rt_row.display = False
            yield rt_row
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

        # Network / disk I/O each own a section rather than trailing the Memory
        # box: network throughput is not a memory metric, and the rates deserve
        # the same history rendering every other metric family gets. Both start
        # hidden and are revealed by the first snapshot that reports counters
        # (SystemSnapshot.net_available / disk_available) — the same
        # start-hidden treatment as #gpu-rt-row, so a machine with no counters
        # never shows an empty box.
        # Each I/O box is a mirror pair: the inbound chart fills upward, the
        # outbound one directly beneath it fills downward, so the seam where
        # they meet reads as a shared zero axis (btop/vnstat convention) without
        # spending a row on drawing one. The labels sandwich the pair — inbound
        # above, outbound below — which keeps each direction's own avg/max at
        # the same total height as two separate label+chart stacks.
        with Vertical(id="section-net", classes="dash-section") as net_sec:
            net_sec.border_title = "Network"
            net_sec.display = False
            yield Static("↓ In 0 B/s", id="net-rx-label", classes="metric-label")
            yield BrailleChart(
                glyph_mode=self._chart_glyph,
                palette=self._palette,
                id="net-rx-chart",
                classes="metric-chart",
            )
            yield BrailleChart(
                glyph_mode=self._chart_glyph,
                palette=self._palette,
                fill=_FILL_DOWN,
                id="net-tx-chart",
                classes="metric-chart",
            )
            yield Static("↑ Out 0 B/s", id="net-tx-label", classes="metric-label")

        with Vertical(id="section-disk", classes="dash-section") as disk_sec:
            disk_sec.border_title = "Disk"
            disk_sec.display = False
            yield Static("↓ Read 0 B/s", id="disk-read-label", classes="metric-label")
            yield BrailleChart(
                glyph_mode=self._chart_glyph,
                palette=self._palette,
                id="disk-read-chart",
                classes="metric-chart",
            )
            yield BrailleChart(
                glyph_mode=self._chart_glyph,
                palette=self._palette,
                fill=_FILL_DOWN,
                id="disk-write-chart",
                classes="metric-chart",
            )
            yield Static("↑ Write 0 B/s", id="disk-write-label", classes="metric-label")

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
                f"layout preset must be one of {self._VALID_PRESETS}, got {name!r}"
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
            self.add_class(f"layout-{preset}")
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
        self._repaint_core_grids()

    def _repaint_core_grids(self) -> None:
        """Re-render both core grids from the last snapshot, without advancing
        their per-core spark histories (`append_sample=False`).

        The single repaint path shared by every non-sample trigger — glyph
        toggle, width/preset change, and the `c` show/hide toggle. No-op while
        cores are hidden (the grids carry no visible content then)."""
        if not self._show_cores:
            return
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
        self._repaint_core_grids()

    @property
    def show_cores(self) -> bool:
        """Whether the per-core panels are currently visible."""
        return self._show_cores

    def set_show_cores(self, show: bool) -> None:
        """Show or hide the per-core panels inside the P-CPU / E-CPU boxes.

        A runtime toggle (the `c` key), independent of the compose-time
        `--show_cores` startup default. Never touches history deques, so
        toggling loses no per-core spark history; painting the grids on show
        (rather than waiting for the next sample) makes the toggle feel instant."""
        show = bool(show)
        if show == self._show_cores:
            return
        self._show_cores = show
        for widget_id in ("#pcores-grid", "#ecores-grid"):
            self.query_one(widget_id, Static).display = show
        # A just-unhidden grid has no width until the next layout pass, so defer
        # the paint (a synchronous one would read width 0 and truncate the row).
        self.call_after_refresh(self._repaint_core_grids)

    def update_metrics(self, message: MetricsUpdated) -> None:
        """Update all dashboard widgets from new metrics. Called by ActopApp."""
        s = message.snapshot
        cfg = self._config

        # Feed the alert engine once per frame (it mutates sustain counters,
        # the session-energy integral, and the bandwidth/package ceiling
        # ratchets — calling it twice would double-count all of these). The
        # Power and Mem BW charts below read the ratcheted ceilings off this
        # same frame, so each chart and its alert stay normalised against the
        # same reference.
        frame = self._alert_engine.feed(s)

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

        # Power percents. In auto mode the denominator is a *rolling* peak over
        # the retained wattage history (already appended above), not an all-time
        # max — so a one-off spike decays out of the window instead of
        # compressing the chart for the rest of the session.
        cpu_pwr_pct = power_to_percent(
            power_w=s.cpu_watts,
            mode=cfg.power_scale,
            profile_ref_w=cfg.cpu_chart_ref_w,
            peak_w=max(self._cpu_w_hist),
            floor_w=DEFAULT_CPU_FLOOR_W,
        )
        if s.cpu_watts > 0 and cpu_pwr_pct == 0:
            cpu_pwr_pct = 1
        gpu_pwr_pct = power_to_percent(
            power_w=s.gpu_watts,
            mode=cfg.power_scale,
            profile_ref_w=cfg.gpu_chart_ref_w,
            peak_w=max(self._gpu_w_hist),
            floor_w=DEFAULT_GPU_FLOOR_W,
        )
        if s.gpu_watts > 0 and gpu_pwr_pct == 0:
            gpu_pwr_pct = 1
        self._cpupwr_hist.append(cpu_pwr_pct)
        self._gpupwr_hist.append(gpu_pwr_pct)

        # Package power chart percent (vs the SoC power ceiling, ratcheted up
        # if this session has observed higher — see AlertFrame.
        # effective_max_package_w); the same L2 normalisation the AlertEngine's
        # PKG alert uses.
        pkg_pwr_pct = package_power_percent(s, frame.effective_max_package_w)
        if s.package_watts > 0 and pkg_pwr_pct == 0:
            pkg_pwr_pct = 1
        self._pkgpwr_hist.append(pkg_pwr_pct)
        self._pkg_w_hist.append(s.package_watts)

        # Memory bandwidth chart percent (vs the SoC unified-memory bandwidth,
        # ratcheted up if this session has observed higher — see
        # AlertFrame.effective_max_bw); the same L2 normalisation the
        # AlertEngine's BW alert uses.
        bw_pct = bandwidth_percent(s, frame.effective_max_bw)
        if s.bandwidth_available and s.bandwidth_gbps > 0 and bw_pct == 0:
            bw_pct = 1  # nudge a tiny-but-nonzero draw off the floor for the chart
        self._bw_hist.append(bw_pct)
        self._bw_gbps_hist.append(s.bandwidth_gbps if s.bandwidth_available else 0.0)
        self._net_rx_hist.append(s.net_rx_bps if s.net_available else 0.0)
        self._net_tx_hist.append(s.net_tx_bps if s.net_available else 0.0)
        self._disk_read_hist.append(s.disk_read_bps if s.disk_available else 0.0)
        self._disk_write_hist.append(s.disk_write_bps if s.disk_available else 0.0)
        self._append_io_percents()

        # Update charts
        chart_data = (
            ("#pcpu-chart", self._pcpu_hist),
            ("#ecpu-chart", self._ecpu_hist),
            ("#gpu-chart", self._gpu_hist),
            ("#ane-chart", self._ane_hist),
            ("#ram-chart", self._ram_hist),
            ("#bw-chart", self._bw_hist),
            ("#net-rx-chart", self._net_rx_pct_hist),
            ("#net-tx-chart", self._net_tx_pct_hist),
            ("#disk-read-chart", self._disk_read_pct_hist),
            ("#disk-write-chart", self._disk_write_pct_hist),
            ("#pkgpwr-chart", self._pkgpwr_hist),
        )
        for widget_id, data in chart_data:
            self.query_one(widget_id, BrailleChart).data = data

        # Update labels
        cpu_temp = f" ({s.cpu_temp_c:.0f}°C)" if s.cpu_temp_c > 0 else ""
        gpu_temp = f" ({s.gpu_temp_c:.0f}°C)" if s.gpu_temp_c > 0 else ""
        self._update_cluster_summary_row(
            "#pcpu-summary-row",
            "P-CPU",
            pcpu,
            s.pcpu_freq_mhz,
            cpu_temp,
            self._pct_stats_tiers(self._pcpu_hist),
        )
        self._update_cluster_summary_row(
            "#ecpu-summary-row",
            "E-CPU",
            ecpu,
            s.ecpu_freq_mhz,
            cpu_temp,
            self._pct_stats_tiers(self._ecpu_hist),
        )
        if cfg.show_residency:
            self._update_residency_row(
                "#pcpu-residency-row", "P-CPU", s.pcpu_residency_pct
            )
            self._update_residency_row(
                "#ecpu-residency-row", "E-CPU", s.ecpu_residency_pct
            )
        # "(drv)" instead of "@NMHz" when gpu_util_pct came from the driver
        # rather than IOReport residency: in that case the GPU DVFS table could
        # not be classified, so there is no trustworthy frequency to print and
        # the percent's provenance differs from every other gauge here.
        gpu_label = Text("GPU ")
        gpu_label.append(f"{gpu}%", style=self._util_color(gpu))
        if s.gpu_util_source == "ioaccelerator":
            gpu_label.append(" (drv)")
        else:
            gpu_label.append(f" @{s.gpu_freq_mhz}MHz")
        gpu_label.append(gpu_temp)
        self._update_stat_row(
            "#gpu-label", gpu_label, self._pct_stats_tiers(self._gpu_hist)
        )
        if cfg.show_residency:
            self._update_residency_row("#gpu-residency-row", "GPU", s.gpu_residency_pct)

        # Renderer/Tiler detail: hidden when the accelerator exposes no
        # statistics. Availability is effectively constant per session, so
        # toggle display only on change (as with Mem BW below).
        rt_row = self.query_one("#gpu-rt-row", Static)
        if rt_row.display != s.gpu_perf_stats_available:
            rt_row.display = s.gpu_perf_stats_available
        if s.gpu_perf_stats_available:
            rt_row.update(
                f"Rend {clamp_percent(s.gpu_renderer_pct)}%"
                f" · Tiler {clamp_percent(s.gpu_tiler_pct)}%"
            )
        ane_label = Text("ANE ")
        ane_label.append(f"{ane_pct}%", style=self._util_color(ane_pct))
        ane_label.append(f" ({s.ane_watts:.1f}W)")
        self._update_stat_row(
            "#ane-label", ane_label, self._pct_stats_tiers(self._ane_hist)
        )

        # The snapshot carries raw bytes; formatting into GiB is presentation, so
        # it happens here. GiB (not GB) because these are 2^30 divisions — the
        # Mem BW row below stays decimal GB/s, which is Apple's own unit for the
        # bus (§2.1 of docs/SPEC-system.md).
        used_gib = _to_gib(s.ram_used_bytes)
        total_gib = _to_gib(s.ram_total_bytes)
        swap_used = _to_gib(s.swap_used_bytes)
        swap_total = _to_gib(s.swap_total_bytes)
        if swap_total >= 0.1:
            ram_label = f"RAM {used_gib}/{total_gib}GiB sw:{swap_used}/{swap_total}GiB"
        else:
            ram_label = f"RAM {used_gib}/{total_gib}GiB"
        self._update_stat_row(
            "#ram-label", ram_label, self._pct_stats_tiers(self._ram_hist)
        )

        self._render_power_rows()
        self._update_stat_row(
            "#pkgpwr-label",
            f"Package Power {s.package_watts:.2f}W",
            self._watt_stats_tiers(self._pkg_w_hist),
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
            self._update_stat_row(
                "#bw-label",
                f"Mem BW {s.bandwidth_gbps:.1f} GB/s",
                self._gbps_stats_tiers(self._bw_gbps_hist),
            )

        # Network I/O: hide the section when getifaddrs returns no usable
        # counters. Rates are bytes/s; displayed as human-readable with rolling
        # context.
        self._update_io_section(
            "#section-net",
            s.net_available,
            (
                ("#net-rx-label", "↓ In", s.net_rx_bps, self._net_rx_hist),
                ("#net-tx-label", "↑ Out", s.net_tx_bps, self._net_tx_hist),
            ),
        )

        # Disk I/O: hide the section when no volume/driver exposes Statistics.
        # Read takes the upward half and write the downward one, the same
        # inbound/outbound axis the network mirror uses.
        self._update_io_section(
            "#section-disk",
            s.disk_available,
            (
                ("#disk-read-label", "↓ Read", s.disk_read_bps, self._disk_read_hist),
                (
                    "#disk-write-label",
                    "↑ Write",
                    s.disk_write_bps,
                    self._disk_write_hist,
                ),
            ),
        )

        # Fan RPM: hide the row entirely on fanless Macs (no SMC fan keys),
        # mirroring the Mem BW hide-on-unavailable pattern above. Per-fan
        # "current/max" (or bare "current" when max is unknown) is rendered by
        # _render_fan_label, which also prefixes each fan's spinner glyph;
        # fans are joined with " · " so the inter-fan separator never collides
        # with the "/" inside a single fan's current/max.
        fan_label = self.query_one("#fan-label", Static)
        if fan_label.display != s.fan_available:
            fan_label.display = s.fan_available
        if s.fan_available:
            self._last_fans = list(s.fans)
            if len(self._fan_spin_idx) != len(self._last_fans):
                self._fan_spin_idx = [0] * len(self._last_fans)
                self._fan_spin_acc = [0.0] * len(self._last_fans)
            self._render_fan_label()

        # Update per-core rows. Always capture the latest core lists (a cheap
        # list copy) so a runtime toggle-on (`c`) can render immediately; only
        # append to the per-core spark histories + repaint the grids while shown.
        self._last_p_cores = list(s.p_cores)
        self._last_e_cores = list(s.e_cores)
        if self._show_cores:
            self._update_core_two_col(
                "#pcores-grid", self._last_p_cores, "P", append_sample=True
            )
            self._update_core_two_col(
                "#ecores-grid", self._last_e_cores, "E", append_sample=True
            )

        # Compute and update status/alerts
        self._compute_alerts(s, frame)

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

    def _pct_stats_tiers(self, hist) -> tuple[str, ...]:
        """Rolling-context tiers for a percent-valued history, widest first.

        The unit is appended because the headline reading often carries a
        different unit (MHz, GB, W), so a bare number would be ambiguous — or,
        for the RAM row, read as GB instead of percent. Only the widest tier
        spells `avg`/`max` out; the narrow ones fall back to the ⌀/▲ glyphs (see
        `_fit_suffix`), and the peak outlives the average because it is the
        figure a session is usually judged on.
        """
        avg, mx = self._avg_max(hist)
        return (
            f"  avg {avg:.0f}% · max {mx:.0f}%",
            f"  {_AVG_GLYPH}{avg:.0f} {_MAX_GLYPH}{mx:.0f}%",
            f"  {_MAX_GLYPH}{mx:.0f}%",
        )

    def _watt_stats_tiers(self, hist) -> tuple[str, ...]:
        """Rolling-context tiers for a watt-valued history, widest first."""
        avg, mx = self._avg_max(hist)
        return (
            f"  avg {avg:.1f}W · max {mx:.1f}W",
            f"  {_AVG_GLYPH}{avg:.1f} {_MAX_GLYPH}{mx:.1f}W",
            f"  {_MAX_GLYPH}{mx:.1f}W",
        )

    def _gbps_stats_tiers(self, hist) -> tuple[str, ...]:
        """Rolling-context tiers for a bandwidth history, widest first."""
        avg, mx = self._avg_max(hist)
        return (
            f"  avg {avg:.1f} · max {mx:.1f} GB/s",
            f"  {_AVG_GLYPH}{avg:.1f} {_MAX_GLYPH}{mx:.1f} GB/s",
            f"  {_MAX_GLYPH}{mx:.1f} GB/s",
        )

    def _bps_stats_tiers(self, hist) -> tuple[str, ...]:
        """Rolling-context tiers for a byte-rate history, widest first.

        One direction per call: each direction owns a labelled chart row, so the
        suffix carries that direction's own rolling context (matching every
        other stats suffix here) instead of a combined rx+tx figure. Both values
        keep their unit even in the compact tiers — unlike the percent/watt/GB/s
        rows, an average and a peak here can land in different prefixes (KB/s vs
        MB/s), so a shared trailing unit would be a lie.
        """
        avg, mx = self._avg_max(hist)
        return (
            f"  avg {_format_bps(avg)} · max {_format_bps(mx)}",
            f"  {_AVG_GLYPH}{_format_bps(avg)} {_MAX_GLYPH}{_format_bps(mx)}",
            f"  {_MAX_GLYPH}{_format_bps(mx)}",
        )

    def _update_stat_row(self, widget_id: str, head, tiers: tuple[str, ...]) -> None:
        """Render `head` plus the widest rolling context its width affords.

        Every metric label goes through here so a narrow grid column degrades
        the avg/max annotation (`_fit_suffix`) rather than losing the tail of
        the line to a hard clip. `head` is a plain str or a styled `Text`; the
        trailing pad keeps the row's background uniform across its full width.
        """
        widget = self.query_one(widget_id, Static)
        avail = widget.size.width
        is_text = isinstance(head, Text)
        head_len = head.cell_len if is_text else len(head)
        suffix = _fit_suffix(
            self._stable_widths(widget_id, head_len, tiers), tiers, avail
        )
        if is_text:
            line = head.copy()
            line.append(suffix)
            if avail > 0:
                line = line[:avail]
                line.pad_right(max(0, avail - line.cell_len))
        else:
            line = head + suffix
            if avail > 0:
                line = line[:avail].ljust(avail)
        widget.update(line)

    def _stable_widths(
        self, key: str, head_len: int, tiers: tuple[str, ...]
    ) -> tuple[int, ...]:
        """High-water `(head, *tiers)` widths for one row, for a steady fit.

        Fitting against this frame's exact lengths makes a row sitting on a tier
        boundary flip shape every time a digit appears or leaves — `avg 8%` to
        `avg 10%`, `987MHz` to `1987MHz` — which is far more distracting than the
        changing digits themselves. Fit against the widest this row has rendered
        instead, so the form it settles on holds until the data genuinely reaches
        a wider shape or the terminal resizes. Widths only ratchet up, exactly
        like the session peak the suffix already reports: a row that briefly hits
        three digits keeps the room for them.
        """
        widths = (head_len, *(len(tier) for tier in tiers))
        seen = self._row_widths.get(key)
        if seen is not None:
            widths = tuple(max(pair) for pair in zip(seen, widths))
        self._row_widths[key] = widths
        return widths

    def _update_residency_row(self, widget_id: str, label: str, percentages) -> None:
        """Render one DVFS residency row, bar sized to the row's own width."""
        widget = self.query_one(widget_id, Static)
        widget.update(_format_residency_row(label, percentages, widget.size.width))

    def _append_io_percents(self) -> None:
        """Append this frame's chart percents for the four I/O rate histories.

        Both directions of a box share one denominator (the rolling peak across
        rx+tx / read+write), so an upload trickle never renders as tall as a
        saturating download — the whole point of stacking them in one box is
        comparison. Called once per frame, after the native-unit deques have
        been appended, so this frame's rate is included in its own peak.
        """
        for pct_hist, rate_hist, other_hist, floor in (
            (
                self._net_rx_pct_hist,
                self._net_rx_hist,
                self._net_tx_hist,
                NET_RATE_FLOOR_BPS,
            ),
            (
                self._net_tx_pct_hist,
                self._net_tx_hist,
                self._net_rx_hist,
                NET_RATE_FLOOR_BPS,
            ),
            (
                self._disk_read_pct_hist,
                self._disk_read_hist,
                self._disk_write_hist,
                DISK_RATE_FLOOR_BPS,
            ),
            (
                self._disk_write_pct_hist,
                self._disk_write_hist,
                self._disk_read_hist,
                DISK_RATE_FLOOR_BPS,
            ),
        ):
            peak = max(max(rate_hist), max(other_hist))
            pct_hist.append(io_rate_percent(rate_hist[-1], peak, floor))

    def _update_io_section(self, section_id: str, available: bool, rows) -> None:
        """Show/hide one I/O section and refresh its labelled rate rows.

        `rows` is a sequence of `(label_id, prefix, rate_bps, native_hist)`.
        Availability is effectively constant per session, so `display` is only
        written on change (the same pattern as Mem BW / Fan); the section starts
        hidden, so the first available frame is what reveals it.
        """
        section = self.query_one(section_id, Vertical)
        if section.display != available:
            section.display = available
        if not available:
            return
        for label_id, prefix, rate_bps, native_hist in rows:
            self._update_stat_row(
                label_id,
                f"{prefix} {_format_bps(rate_bps)}",
                self._bps_stats_tiers(native_hist),
            )

    def _update_cluster_summary_row(
        self,
        widget_id: str,
        label: str,
        util_pct: int,
        freq_mhz: int,
        cpu_temp: str,
        tiers: tuple[str, ...],
    ) -> None:
        """Render one full-width cluster summary line."""
        head = Text(f"{label} ")
        # The % wears the chart's color for that value (see _util_color) so the
        # readout and its tracer agree at a glance; the MHz/temp/avg/max context
        # stays muted plain text.
        head.append(f"{util_pct:3d}%", style=self._util_color(util_pct))
        head.append(f" @{freq_mhz}MHz{cpu_temp}")
        self._update_stat_row(widget_id, head, tiers)

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
        row drops the spark and keeps the numbers. The suffix is fitted first
        (`_fit_suffix`) so the spark competes for width against whatever
        annotation the row can actually afford, not against the widest one.
        """
        widget = self.query_one(widget_id, Static)
        avail = widget.size.width
        head = f"{label} {watts:.2f}W"
        tiers = self._watt_stats_tiers(watt_hist)
        suffix = _fit_suffix(
            self._stable_widths(widget_id, len(head), tiers), tiers, avail
        )
        room = avail - len(head) - 1 - len(suffix)  # -1 for the space after head
        spark_w = max(0, min(self._POWER_SPARK_MAX, room))
        if spark_w >= self._POWER_SPARK_MIN:
            spark = _inline_spark(
                history=pct_hist, width_chars=spark_w, glyph_mode=self._chart_glyph
            )
            line = f"{head} {spark}{suffix}"
        else:
            line = f"{head}{suffix}"
        widget.update(line[:avail].ljust(avail) if avail > 0 else line)

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
        base = f"{prefix}{core.index:02d} {core.active_pct:3d}%"
        if col_width <= len(base):
            return base[:col_width].ljust(col_width)
        max_spark_w = col_width - len(base) - 1
        spark_w = max(1, max_spark_w)
        spark = _inline_spark(
            history=hist, width_chars=spark_w, glyph_mode=self._chart_glyph
        )
        entry = f"{base} {spark}"
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
            rows.append(f"{left}{self._CORE_GRID_SEP}{right}")
        widget.update("\n".join(rows))

    def _compute_alerts(self, s: SystemSnapshot, frame) -> None:
        """Format an already-computed L2 alert frame into the status line
        (presentation only).

        All alert/throttle/energy math lives in analytics.AlertEngine; this
        method turns its AlertFrame into user-facing tokens. `frame` comes
        from the single per-frame `AlertEngine.feed()` call in
        `update_metrics` — this method must not call `feed()` itself, or the
        sustain counters/energy integral/bandwidth ratchet would double-count.
        """
        cfg = self._config

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
            active_alerts.append(f"MEM-BOUND>{cfg.alert_bw_sat_percent}%")
        if frame.swap_alert:
            active_alerts.append(f"SWAP+{frame.swap_rise_gib:.1f}Gi")
        if frame.pkg_alert:
            active_alerts.append(f"PKG>{cfg.alert_package_power_percent}%")
        alerts_str = ", ".join(active_alerts) if active_alerts else "none"

        status = f"thermal: {s.thermal_state}  alerts: {alerts_str}"
        meta = []
        if span_label:
            meta.append(f"span {span_label}")
        meta.append(f"energy {self._format_session_energy(frame.session_energy_j)}")
        if meta:
            status = "{}  ·  {}".format("  ·  ".join(meta), status)
        # Status line lives in app chrome now; hand the string up to ActopApp.
        self.post_message(AlertsComputed(status))

    def _format_session_energy(self, joules: float) -> str:
        """Cumulative session energy as `N.NWh` (or `N mWh` while still small)."""
        wh = joules / 3600.0
        if wh < 0.1:
            return f"{wh * 1000:.0f}mWh"
        return f"{wh:.2f}Wh"

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
        # `dots` packs 2 samples per character (see BrailleChart._render_dots),
        # so it covers twice the time span of `block` for the same width.
        samples_per_char = 1 if self._chart_glyph == "block" else 2
        return _format_window_span(width * samples_per_char * interval)
