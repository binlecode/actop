"""Chart color degradation and NO_COLOR handling.

Two presentation contracts, both driven through public surfaces:

  * the public ``resolve_color_mode`` env resolver picks the right color tier
    (and disables color under NO_COLOR / dumb terminals), and
  * the resolved tier actually shapes the chart's rendered styles — verified by
    mounting a real ``BrailleChart`` and inspecting ``render()`` output, so the
    blue->red gradient degrades per tier and disappears entirely with no color.
"""

import asyncio

from rich.text import Text
from textual.app import App, ComposeResult

from actop.tui.widgets import BrailleChart, resolve_color_mode

# --- NO_COLOR / tier resolution (https://no-color.org external contract) -----


def test_no_color_overrides_a_truecolor_terminal():
    # Presence of NO_COLOR disables color regardless of terminal capability.
    assert resolve_color_mode(env={"NO_COLOR": "1", "COLORTERM": "truecolor"}) == "none"


def test_color_tier_detected_from_colorterm_and_term():
    assert resolve_color_mode(env={"COLORTERM": "truecolor"}) == "truecolor"
    assert resolve_color_mode(env={"COLORTERM": "24bit"}) == "truecolor"
    assert resolve_color_mode(env={"TERM": "xterm-256color"}) == "256"
    assert resolve_color_mode(env={"TERM": "xterm"}) == "16"


def test_dumb_and_unknown_terminals_get_no_color():
    assert resolve_color_mode(env={"TERM": "dumb"}) == "none"
    assert resolve_color_mode(env={}) == "none"


def test_console_color_system_is_preferred_when_present():
    from rich.console import Console

    # A detected console color system wins over env heuristics...
    assert (
        resolve_color_mode(Console(color_system="256"), env={"COLORTERM": "truecolor"})
        == "256"
    )

    # ...but NO_COLOR still overrides even a truecolor console.
    assert (
        resolve_color_mode(Console(color_system="truecolor"), env={"NO_COLOR": "1"})
        == "none"
    )


# --- the resolved tier actually shapes rendered output -----------------------


class _ChartHost(App):
    """Mounts one BrailleChart pinned to an exact cell size and color tier."""

    def __init__(self, color_mode, width=8, height=2) -> None:
        super().__init__()
        self._color_mode = color_mode
        self._w = width
        self._h = height
        self.chart = None

    def compose(self) -> ComposeResult:
        chart = BrailleChart(color_mode=self._color_mode)
        chart.styles.width = self._w
        chart.styles.height = self._h
        self.chart = chart
        yield chart


def _render_styles(color_mode: str, value: float = 80.0, width: int = 8):
    """Style strings emitted by a fully-active chart in the given color tier."""

    async def _run() -> Text:
        app = _ChartHost(color_mode, width=width)
        async with app.run_test(size=(width + 8, 10)) as pilot:
            app.chart.data = [value] * width  # every column non-zero -> styled
            await pilot.pause()
            return app.chart.render()

    rendered = asyncio.run(_run())
    return [span.style for span in rendered.spans]


def test_truecolor_render_emits_rgb_styles():
    styles = _render_styles("truecolor")
    assert styles  # cells are styled
    assert all(s.startswith("rgb(") for s in styles)


def test_256_render_degrades_to_color_cube_indices():
    styles = _render_styles("256")
    assert styles
    assert all(s.startswith("color(") for s in styles)


def test_16_render_degrades_to_named_severity_ramp():
    styles = _render_styles("16")
    assert styles
    assert set(styles) <= {"blue", "green", "yellow", "red"}


def test_none_render_emits_no_color_styles():
    # NO_COLOR / dumb terminals: not a single color escape is produced.
    assert resolve_color_mode(env={"NO_COLOR": "1"}) == "none"
    assert _render_styles("none") == []


def test_16_ramp_is_cool_to_hot():
    # Low utilization reads cool, high utilization reads hot — the ramp must not
    # invert, or the severity cue would be backwards.
    assert set(_render_styles("16", value=10.0)) == {"blue"}
    assert set(_render_styles("16", value=95.0)) == {"red"}
