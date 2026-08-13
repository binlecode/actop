"""End-to-end Mem BW / Package Power surfacing through the real update path.

Drives the production `HardwareDashboard.update_metrics` against real
`SystemSnapshot` / `DashboardConfig` values inside Textual's headless harness —
no mocks. Validates the two Tier-1 surfacing contracts:

  * Package Power renders its watt headline (the total-SoC figure asitop shows),
    and
  * the Mem BW row carries GB/s when the platform exposes bandwidth, but hides
    itself entirely when `SystemSnapshot.bandwidth_available` is false (no DCS
    channel), so the user never sees a misleading 0 GB/s.
"""

import asyncio
import dataclasses
import re

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from actop.config import DashboardConfig
from actop.models import CoreSample, FanReading, SystemSnapshot
from actop.tui.widgets import (
    AlertsComputed,
    BrailleChart,
    HardwareDashboard,
    MetricsUpdated,
)


def _config(show_residency: bool = True, show_cores: bool = False) -> DashboardConfig:
    return DashboardConfig(
        sample_interval=1,
        avg_window=30,
        cpu_chart_ref_w=20.0,
        gpu_chart_ref_w=30.0,
        ane_max_power=8.0,
        package_ref_w=58.0,
        max_mem_bw=200.0,
        chip_name="Apple M-test",
        e_core_count=4,
        p_core_count=4,
        gpu_core_count=10,
        power_scale="profile",
        chart_glyph="dots",
        palette="thermal",
        theme="textual-dark",
        layout="grid",
        show_cores=show_cores,
        show_residency=show_residency,
        alert_bw_sat_percent=85,
        alert_package_power_percent=85,
        alert_throttle_freq_percent=90,
        alert_swap_rise_gib=1.0,
        alert_sustain_samples=3,
        subsamples=1,
        process_display_count=50,
        show_processes=False,
        process_filter_pattern=None,
    )


def _snapshot(
    bandwidth_gbps: float,
    bandwidth_available: bool,
    package_watts: float = 21.5,
    *,
    pcpu_util_pct: float = 20.0,
    gpu_util_pct: float = 30.0,
    pcpu_freq_mhz: int = 3200,
    gpu_freq_mhz: int = 900,
    pcpu_max_freq_mhz: int = 3200,
    gpu_max_freq_mhz: int = 1000,
    cpu_temp_c: float = 0.0,
    gpu_temp_c: float = 0.0,
    thermal_state: str = "Nominal",
    ecpu_residency_pct: dict | None = None,
    pcpu_residency_pct: dict | None = None,
    gpu_residency_pct: dict | None = None,
    fans: list | None = None,
    fan_available: bool = False,
    p_cores: list | None = None,
    e_cores: list | None = None,
    timestamp: float = 0.0,
    gpu_renderer_pct: float = 0.0,
    gpu_tiler_pct: float = 0.0,
    gpu_perf_stats_available: bool = False,
    gpu_util_source: str = "residency",
    net_rx_bps: float = 0.0,
    net_tx_bps: float = 0.0,
    net_available: bool = False,
    disk_read_bps: float = 0.0,
    disk_write_bps: float = 0.0,
    disk_available: bool = False,
) -> SystemSnapshot:
    fans = [] if fans is None else fans
    idle_residency = {"idle": 100, "low": 0, "mid": 0, "high": 0}
    return SystemSnapshot(
        timestamp=timestamp,
        cpu_watts=8.0,
        gpu_watts=12.0,
        ane_watts=0.0,
        package_watts=package_watts,
        ecpu_util_pct=10.0,
        pcpu_util_pct=pcpu_util_pct,
        gpu_util_pct=gpu_util_pct,
        cpu_temp_c=cpu_temp_c,
        gpu_temp_c=gpu_temp_c,
        ecpu_freq_mhz=1200,
        pcpu_freq_mhz=pcpu_freq_mhz,
        gpu_freq_mhz=gpu_freq_mhz,
        # Bytes are canonical, mirroring what api.py populates; the TUI formats
        # GiB from these at render time.
        ram_used_bytes=18 * 1024**3,
        ram_total_bytes=32 * 1024**3,
        swap_used_bytes=0,
        swap_total_bytes=0,
        ram_used_percent=56.0,
        thermal_state=thermal_state,
        bandwidth_gbps=bandwidth_gbps,
        bandwidth_available=bandwidth_available,
        fans=fans,
        fan_rpms=[f.current for f in fans],
        fan_available=fan_available,
        pcpu_max_freq_mhz=pcpu_max_freq_mhz,
        gpu_max_freq_mhz=gpu_max_freq_mhz,
        ecpu_residency_pct=dict(ecpu_residency_pct or idle_residency),
        pcpu_residency_pct=dict(pcpu_residency_pct or idle_residency),
        gpu_residency_pct=dict(gpu_residency_pct or idle_residency),
        p_cores=[] if p_cores is None else list(p_cores),
        e_cores=[] if e_cores is None else list(e_cores),
        gpu_device_pct=gpu_util_pct,
        gpu_renderer_pct=gpu_renderer_pct,
        gpu_tiler_pct=gpu_tiler_pct,
        gpu_perf_stats_available=gpu_perf_stats_available,
        gpu_util_source=gpu_util_source,
        net_rx_bps=net_rx_bps,
        net_tx_bps=net_tx_bps,
        net_available=net_available,
        disk_read_bps=disk_read_bps,
        disk_write_bps=disk_write_bps,
        disk_available=disk_available,
    )


class _Host(App):
    """Minimal mount point so the real dashboard widget can be laid out.

    Also captures the dashboard's AlertsComputed message: the status string now
    lives in app chrome (not the dashboard subtree), so tests read it off the
    host the way the real ActopApp does, rather than querying a #status-line
    inside the dashboard.
    """

    def __init__(self, dashboard: HardwareDashboard) -> None:
        super().__init__()
        self._dashboard = dashboard
        self.last_status = ""

    def compose(self) -> ComposeResult:
        yield self._dashboard

    def on_alerts_computed(self, message: AlertsComputed) -> None:
        self.last_status = message.status


async def _drive(snapshots, config=None):
    """Mount the dashboard, push each snapshot, return the final widget state."""
    dash = HardwareDashboard(config=config or _config())
    app = _Host(dash)
    async with app.run_test() as pilot:
        for snap in snapshots:
            dash.update_metrics(MetricsUpdated(snap))
            await pilot.pause()
        state = {
            "pkg_label": str(dash.query_one("#pkgpwr-label", Static).render()),
            "cpupwr_row": str(dash.query_one("#cpupwr-row", Static).render()),
            "gpupwr_row": str(dash.query_one("#gpupwr-row", Static).render()),
            "ram_label": str(dash.query_one("#ram-label", Static).render()),
            "bw_label": str(dash.query_one("#bw-label", Static).render()),
            "bw_label_display": dash.query_one("#bw-label", Static).display,
            "bw_chart_display": dash.query_one("#bw-chart", BrailleChart).display,
            "net_rx_label": str(dash.query_one("#net-rx-label", Static).render()),
            "net_tx_label": str(dash.query_one("#net-tx-label", Static).render()),
            "net_section_display": dash.query_one("#section-net").display,
            "net_rx_chart": list(dash.query_one("#net-rx-chart", BrailleChart).data),
            "disk_read_label": str(dash.query_one("#disk-read-label", Static).render()),
            "disk_write_label": str(
                dash.query_one("#disk-write-label", Static).render()
            ),
            "disk_section_display": dash.query_one("#section-disk").display,
            "fan_label": str(dash.query_one("#fan-label", Static).render()),
            "fan_label_display": dash.query_one("#fan-label", Static).display,
            "gpu_label": str(dash.query_one("#gpu-label", Static).render()),
            "gpu_rt_row": str(dash.query_one("#gpu-rt-row", Static).render()),
            "gpu_rt_row_display": dash.query_one("#gpu-rt-row", Static).display,
            "status": app.last_status,
        }
        # Raw renderables (not str) for the styled-span assertions in the
        # coloring tests — the str form strips the severity tint the span tests
        # need to see.
        state["pcpu_row"] = dash.query_one("#pcpu-summary-row", Static).render()
        state["ecpu_row"] = dash.query_one("#ecpu-summary-row", Static).render()
        state["fan_label_content"] = dash.query_one("#fan-label", Static).render()
        residency_ids = (
            "pcpu-residency-row",
            "ecpu-residency-row",
            "gpu-residency-row",
        )
        for widget_id in residency_ids:
            try:
                widget = dash.query_one("#" + widget_id, Static)
            except Exception:
                state[widget_id.replace("-", "_")] = None
            else:
                state[widget_id.replace("-", "_")] = str(widget.render())
        return state


def test_package_power_headline_renders_total_soc_watts():
    state = asyncio.run(_drive([_snapshot(120.0, True)]))
    # The total-SoC figure (package_watts) must reach the headline label.
    assert "Package Power" in state["pkg_label"]
    assert "21.5" in state["pkg_label"]


def test_power_rows_render_cpu_gpu_watts_with_inline_spark():
    # PR1 compaction: CPU/GPU power collapse from label+chart pairs into single
    # inline-spark rows. Each row must still carry its live wattage headline
    # (cpu_watts=8.0, gpu_watts=12.0 from _snapshot) and its watt-unit avg/max
    # context through the real update path, plus an inline braille spark.
    state = asyncio.run(_drive([_snapshot(120.0, True)]))
    assert "CPU 8.00W" in state["cpupwr_row"]
    assert "avg 8.0W · max 8.0W" in state["cpupwr_row"]
    assert "GPU 12.00W" in state["gpupwr_row"]
    assert "avg 12.0W · max 12.0W" in state["gpupwr_row"]
    # A dots-mode inline spark rendered between headline and suffix (blank
    # braille cell U+2800 is present when the spark region is drawn).
    assert "⠀" in state["cpupwr_row"]


def test_ram_row_renders_from_snapshot_fields():
    # LC-1: RAM/swap are carried on the SystemSnapshot (ram_used_bytes /
    # ram_total_bytes), not a second get_ram_metrics_dict() call. The row must
    # render the snapshot's used/total through the real update path, formatted as
    # GiB — a 2^30 division, unlike the decimal Mem BW row below.
    state = asyncio.run(_drive([_snapshot(0.0, False)]))
    assert "RAM 18.0/32.0GiB" in state["ram_label"]


def test_mem_bw_row_shows_gbps_when_available():
    state = asyncio.run(_drive([_snapshot(120.0, True)]))
    assert state["bw_label_display"] is True
    assert state["bw_chart_display"] is True
    assert "120.0 GB/s" in state["bw_label"]


def test_mem_bw_row_hidden_when_bandwidth_unavailable():
    # No DCS channel: the row is hidden so the user never reads a phantom 0 GB/s.
    state = asyncio.run(_drive([_snapshot(0.0, False)]))
    assert state["bw_label_display"] is False
    assert state["bw_chart_display"] is False


def test_net_disk_sections_show_rates_and_scaled_charts_when_available():
    # Net/disk own titled sections with a chart per direction. Two samples: the
    # first sets the rolling peak (100 MB/s rx), the second drops to a quarter
    # of it. The labels must carry the live rate + session max in decimal
    # throughput, and the charts must carry *percents* against the shared
    # peak-based denominator (peak x1.25) — 100 MB/s -> 80%, 25 MB/s -> 20%.
    # A chart wired to the native-unit deque instead of the percent deque would
    # ship 1e8 here and render permanently full-scale; only a two-sample
    # assertion catches that.
    def snap(net_rx, net_tx, disk_r, disk_w):
        return _snapshot(
            120.0,
            True,
            net_rx_bps=net_rx,
            net_tx_bps=net_tx,
            net_available=True,
            disk_read_bps=disk_r,
            disk_write_bps=disk_w,
            disk_available=True,
        )

    state = asyncio.run(
        _drive(
            [
                snap(100_000_000, 750_000, 400_000_000, 500_000),
                snap(25_000_000, 750_000, 100_000_000, 500_000),
            ]
        )
    )
    assert state["net_section_display"] is True
    assert "↓ In 25.0 MB/s" in state["net_rx_label"]
    assert "max 100.0 MB/s" in state["net_rx_label"]
    assert "↑ Out 750.0 KB/s" in state["net_tx_label"]
    assert state["disk_section_display"] is True
    assert "↓ Read 100.0 MB/s" in state["disk_read_label"]
    assert "max 400.0 MB/s" in state["disk_read_label"]
    assert "↑ Write 500.0 KB/s" in state["disk_write_label"]
    assert list(state["net_rx_chart"])[-2:] == [80, 20]


def test_net_disk_sections_hidden_when_unavailable():
    # No usable counters: hide the whole section rather than show a phantom
    # 0 B/s box, the same hide-on-unavailable contract Mem BW / Fan honour.
    state = asyncio.run(_drive([_snapshot(120.0, True)]))
    assert state["net_section_display"] is False
    assert state["disk_section_display"] is False


def test_renderer_tiler_row_shows_driver_split_when_available():
    # The compute-vs-geometry split is the reason this row exists: an MLX/CoreML
    # frame runs Renderer high with Tiler near zero. Both numbers must reach the
    # row from their own fields — a swap would read as a render-bound workload.
    state = asyncio.run(
        _drive(
            [
                _snapshot(
                    120.0,
                    True,
                    gpu_renderer_pct=91.0,
                    gpu_tiler_pct=2.0,
                    gpu_perf_stats_available=True,
                )
            ]
        )
    )
    assert state["gpu_rt_row_display"] is True
    assert "Rend 91%" in state["gpu_rt_row"]
    assert "Tiler 2%" in state["gpu_rt_row"]


def test_renderer_tiler_row_hidden_when_perf_stats_unavailable():
    # No accelerator statistics: hide the row rather than show a phantom 0/0,
    # the same contract the Mem BW row honours above.
    state = asyncio.run(_drive([_snapshot(120.0, True)]))
    assert state["gpu_rt_row_display"] is False


def test_gpu_label_marks_driver_provenance_and_drops_meaningless_freq():
    # When the GPU DVFS table cannot be classified, api.py falls back to the
    # driver's utilization. The frequency is then unknown, so printing "@0MHz"
    # would assert an idle clock that was never measured — the row must say
    # where the percent came from instead.
    state = asyncio.run(
        _drive(
            [
                _snapshot(
                    120.0,
                    True,
                    gpu_util_pct=77.0,
                    gpu_freq_mhz=0,
                    gpu_max_freq_mhz=0,
                    gpu_perf_stats_available=True,
                    gpu_util_source="ioaccelerator",
                )
            ]
        )
    )
    assert "GPU 77% (drv)" in state["gpu_label"]
    assert "MHz" not in state["gpu_label"]

    # The residency path keeps the frequency and carries no provenance marker.
    residency = asyncio.run(_drive([_snapshot(120.0, True, gpu_freq_mhz=900)]))
    assert "GPU 30% @900MHz" in residency["gpu_label"]
    assert "(drv)" not in residency["gpu_label"]


_FAN_SPINNER_CHARS = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _styled_segment(content, predicate):
    """Style string of the first span matching `predicate`, or None.

    The rendered `Content` carries a `spans`/`plain` pair (Textual's wrapper
    over Rich's styled Text); the plain string the rest of the suite asserts on
    is the same text with the severity tint stripped.
    """
    for span in content.spans:
        if predicate(content.plain[span.start : span.end]):
            return str(span.style)
    return None


def _fan_glyph_style(content):
    return _styled_segment(
        content, lambda s: re.fullmatch(rf"[{_FAN_SPINNER_CHARS}]", s) is not None
    )


def test_util_percent_wears_chart_color_high_vs_low():
    # The % readout must wear the chart's severity color for that value (the
    # number and its tracer agree), so a hot reading reads red-hot and an idle
    # one stays cool. A regression back to a plain string leaves every span
    # unstyled and this test fails — the substring assertions elsewhere would
    # keep passing.
    state = asyncio.run(_drive([_snapshot(0.0, False, pcpu_util_pct=90.0)]))
    pcpu_style = _styled_segment(state["pcpu_row"], lambda s: s.strip() == "90%")
    ecpu_style = _styled_segment(state["ecpu_row"], lambda s: s.strip() == "10%")
    assert pcpu_style  # colored, not the empty plain-text style
    assert ecpu_style
    assert pcpu_style != ecpu_style  # high/low read different colors


def test_fan_spinner_glyph_wears_utilization_color():
    # The fan spinner glyph tints by fan load the same way the % readouts do:
    # a fan near its max spins in the hot color, a nearly-idle fan in the cool
    # one. max-less fans stay untinted (there is no utilization to colour by).
    high = asyncio.run(
        _drive(
            [
                _snapshot(
                    0.0,
                    False,
                    fans=[FanReading(5400.0, 6000.0)],
                    fan_available=True,
                )
            ]
        )
    )
    low = asyncio.run(
        _drive(
            [
                _snapshot(
                    0.0,
                    False,
                    fans=[FanReading(1200.0, 6000.0)],
                    fan_available=True,
                )
            ]
        )
    )
    no_max = asyncio.run(
        _drive(
            [
                _snapshot(
                    0.0,
                    False,
                    fans=[FanReading(5400.0, None)],
                    fan_available=True,
                )
            ]
        )
    )
    high_style = _fan_glyph_style(high["fan_label_content"])
    low_style = _fan_glyph_style(low["fan_label_content"])
    no_max_style = _fan_glyph_style(no_max["fan_label_content"])
    assert high_style
    assert low_style
    assert high_style != low_style
    assert no_max_style is None  # no max key -> no invented severity


def test_fan_row_shows_current_and_max_when_available():
    # Two fans, each with a known max: render "current/max" per fan, joined by
    # " · " so the inter-fan separator never collides with the current/max "/".
    state = asyncio.run(
        _drive(
            [
                _snapshot(
                    0.0,
                    False,
                    fans=[FanReading(1200.0, 6000.0), FanReading(980.0, 6000.0)],
                    fan_available=True,
                )
            ]
        )
    )
    assert state["fan_label_display"] is True
    # Each fan's reading is prefixed with a braille-cascade spinner glyph,
    # whichever frame the fan's own timer landed on.
    assert re.search(
        r"Fan [⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏] 1200/6000 · [⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏] 980/6000 RPM",
        state["fan_label"],
    )


def test_fan_row_falls_back_to_bare_rpm_when_max_unknown():
    # A fan whose max key is absent (max=None) renders bare current RPM.
    state = asyncio.run(
        _drive(
            [
                _snapshot(
                    0.0,
                    False,
                    fans=[FanReading(1200.0, None)],
                    fan_available=True,
                )
            ]
        )
    )
    assert state["fan_label_display"] is True
    assert re.search(r"Fan [⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏] 1200 RPM", state["fan_label"])


def test_fan_row_hidden_when_fan_unavailable():
    # Fanless Mac (e.g. MacBook Air): no SMC fan keys, so the row is hidden
    # entirely rather than showing a phantom 0 RPM.
    state = asyncio.run(_drive([_snapshot(0.0, False, fan_available=False)]))
    assert state["fan_label_display"] is False


def test_label_avg_is_windowed_and_max_is_session_peak():
    # The rolling avg/max context beside each reading must reflect only real
    # samples (no leading zero-padding) — avg over the window, max as the
    # session peak. Drive two real frames and read it off the rendered label.
    state = asyncio.run(
        _drive(
            [
                _snapshot(120.0, True, package_watts=50.0),
                _snapshot(120.0, True, package_watts=70.0),
            ]
        )
    )
    # avg of (50, 70) = 60 — padding zeros must not drag it down; max = 70.
    assert "avg 60.0W · max 70.0W" in state["pkg_label"]


def test_status_line_reports_cumulative_session_energy():
    # LC-3: session energy integrates package_watts over the real inter-frame
    # dt from snapshot.timestamp (not a fixed interval). The first frame has no
    # prior timestamp, so it contributes 0 J. With timestamps 0s/1s/2s and a
    # steady 60W draw: frame1 → 0 J, frame2 → 60W·1s, frame3 → +60W·1s = 120 J
    # total = 120/3600 Wh ≈ 33 mWh (sub-0.1Wh renders in mWh).
    state = asyncio.run(
        _drive(
            [
                _snapshot(120.0, True, package_watts=60.0, timestamp=0.0),
                _snapshot(120.0, True, package_watts=60.0, timestamp=1.0),
                _snapshot(120.0, True, package_watts=60.0, timestamp=2.0),
            ]
        )
    )
    assert "energy 33mWh" in state["status"]


def test_throttling_fires_when_busy_slow_and_hot():
    # A busy P-cluster held well below its DVFS ceiling while thermals are
    # elevated must raise THROTTLING:CPU once sustained past alert_sustain_samples.
    busy_slow_hot = [
        _snapshot(
            0.0,
            False,
            pcpu_util_pct=95.0,
            pcpu_freq_mhz=2000,  # 2000/3200 = 62% < 90%
            pcpu_max_freq_mhz=3200,
            thermal_state="Serious",
        )
        for _ in range(4)  # > alert_sustain_samples (3)
    ]
    state = asyncio.run(_drive(busy_slow_hot))
    assert "THROTTLING:CPU" in state["status"]


def test_throttling_clears_when_frequency_recovers():
    # After sustained throttling, a frame where the clock returns to the ceiling
    # must clear the indicator (counter resets, so the token disappears).
    busy_slow_hot = _snapshot(
        0.0,
        False,
        pcpu_util_pct=95.0,
        pcpu_freq_mhz=2000,
        pcpu_max_freq_mhz=3200,
        thermal_state="Serious",
    )
    recovered = _snapshot(
        0.0,
        False,
        pcpu_util_pct=95.0,
        pcpu_freq_mhz=3200,  # back at ceiling -> not "slow"
        pcpu_max_freq_mhz=3200,
        thermal_state="Nominal",
    )
    state = asyncio.run(_drive([busy_slow_hot] * 4 + [recovered]))
    assert "THROTTLING" not in state["status"]


def test_throttling_does_not_fire_when_idle_at_low_freq():
    # Low frequency at low utilization is normal idle behaviour, not throttling —
    # the load gate must suppress a false positive even across many frames.
    idle_low_freq = [
        _snapshot(
            0.0,
            False,
            pcpu_util_pct=5.0,  # below the load gate
            pcpu_freq_mhz=600,
            pcpu_max_freq_mhz=3200,
            thermal_state="Nominal",
        )
        for _ in range(5)
    ]
    state = asyncio.run(_drive(idle_low_freq))
    assert "THROTTLING" not in state["status"]


def test_residency_row_leans_high_under_sustained_load():
    # Acceptance: "residency distribution shifts toward high-freq states
    # under load." A cluster pinned mostly in the high bucket must render a
    # dominant 'high' share, not idle.
    busy = _snapshot(
        0.0,
        False,
        pcpu_residency_pct={"idle": 2, "low": 3, "mid": 10, "high": 85},
    )
    state = asyncio.run(_drive([busy]))
    assert "high85" in state["pcpu_residency_row"]
    assert "P-CPU" in state["pcpu_residency_row"]


def test_residency_row_leans_idle_at_rest():
    # Acceptance: "... and idle states at rest." An at-rest cluster must
    # render a dominant 'idle' share.
    idle = _snapshot(
        0.0,
        False,
        ecpu_residency_pct={"idle": 92, "low": 8, "mid": 0, "high": 0},
    )
    state = asyncio.run(_drive([idle]))
    assert "idle92" in state["ecpu_residency_row"]


def test_residency_bar_has_no_gaps_or_overflow_at_full_width():
    # Largest-remainder allocation must always fill the bar exactly: the
    # glyph count inside the brackets must equal the allocated bar width,
    # even for percentages that don't divide evenly. Two samples so the second
    # renders against a settled layout — a row sizes its bar to the width it
    # measured, and the first frame after mount still carries the pre-layout
    # width, where the bar would legitimately be narrower.
    skewed = _snapshot(
        0.0,
        False,
        gpu_residency_pct={"idle": 33, "low": 34, "mid": 17, "high": 16},
    )
    state = asyncio.run(_drive([skewed, skewed]))
    bar = re.search(r"\[(.*?)\]", state["gpu_residency_row"]).group(1)
    assert len(bar) == 16  # full bar: an 80-col row affords it


def test_residency_rows_hidden_when_show_residency_disabled():
    # show_residency is a startup-only density choice baked into compose(),
    # like show_cores — disabled means the rows never exist at all.
    state = asyncio.run(
        _drive([_snapshot(0.0, False)], config=_config(show_residency=False))
    )
    assert state["pcpu_residency_row"] is None
    assert state["ecpu_residency_row"] is None
    assert state["gpu_residency_row"] is None


def test_per_core_panels_hidden_by_default_and_toggle_on_via_public_api():
    # Cores are hidden by default so the P-CPU / E-CPU / GPU charts read as the
    # prominent sibling boxes. The `c`-key path (set_show_cores) must reveal the
    # grids and paint the current per-core readings immediately — without waiting
    # for the next sample — from data captured on every frame.
    async def _run():
        dash = HardwareDashboard(config=_config())  # show_cores defaults False
        app = _Host(dash)
        async with app.run_test(size=(160, 50)) as pilot:
            snap = _snapshot(
                0.0,
                False,
                p_cores=[CoreSample(index=0, active_pct=73, freq_mhz=3200)],
                e_cores=[CoreSample(index=1, active_pct=12, freq_mhz=1400)],
            )
            dash.update_metrics(MetricsUpdated(snap))
            await pilot.pause()
            pgrid = dash.query_one("#pcores-grid", Static)
            # Composed but hidden, and carrying no visible core content yet.
            assert pgrid.display is False
            assert dash.show_cores is False

            dash.set_show_cores(True)
            await pilot.pause()
            assert dash.show_cores is True
            assert pgrid.display is True
            # The captured P-core reading is painted on toggle-on, not deferred.
            assert "P00" in str(pgrid.render())
            assert "73%" in str(pgrid.render())

            dash.set_show_cores(False)
            await pilot.pause()
            assert dash.query_one("#pcores-grid", Static).display is False

    asyncio.run(_run())


def test_pcpu_and_ecpu_are_separate_sibling_boxes():
    # The clusters render as two independent titled boxes (not two halves of one
    # "CPU" box), so their charts stand out as siblings alongside GPU.
    async def _run():
        dash = HardwareDashboard(config=_config())
        app = _Host(dash)
        async with app.run_test(size=(160, 50)) as pilot:
            dash.update_metrics(MetricsUpdated(_snapshot(0.0, False)))
            await pilot.pause()
            from textual.containers import Vertical

            pbox = dash.query_one("#section-pcpu", Vertical)
            ebox = dash.query_one("#section-ecpu", Vertical)
            return pbox.border_title, ebox.border_title

    p_title, e_title = asyncio.run(_run())
    assert p_title == "P-CPU"
    assert e_title == "E-CPU"


def test_status_line_surfaces_chart_time_window_span():
    # The charts' visible time span scales silently with terminal width, so the
    # dashboard surfaces it as a `span` token on the status line. Once laid out,
    # a well-formed span (seconds/minutes/hours) must appear — otherwise the
    # window the charts cover is ambiguous to the user.
    state = asyncio.run(_drive([_snapshot(120.0, True)]))
    assert re.search(r"span \d+(?:s|m(?:\d{2}s)?|h(?:\d{2}m)?)", state["status"])


# --- Layout presets (PR2) --------------------------------------------------


def test_layout_switch_preserves_history_and_keeps_updating():
    # Switching preset mid-session must not reset the history deques: the
    # session peak carried in the avg/max suffix has to survive the swap, and
    # labels must keep updating in the new preset. Drive in grid (wide enough
    # that it does not auto-degrade), switch to stack, then feed a lower reading.
    async def _run():
        dash = HardwareDashboard(config=_config())
        app = _Host(dash)
        async with app.run_test(size=(160, 50)) as pilot:
            for _ in range(2):
                dash.update_metrics(
                    MetricsUpdated(_snapshot(120.0, True, package_watts=50.0))
                )
                await pilot.pause()
            assert dash.effective_layout_preset == "grid"
            dash.set_layout_preset("stack")
            await pilot.pause()
            assert dash.effective_layout_preset == "stack"
            # A lower reading after the switch: headline follows it (still
            # updating), but the session peak from before the switch persists.
            dash.update_metrics(
                MetricsUpdated(_snapshot(120.0, True, package_watts=20.0))
            )
            await pilot.pause()
            return str(dash.query_one("#pkgpwr-label", Static).render())

    label = asyncio.run(_run())
    assert "20.0" in label  # newest value rendered post-switch
    assert "max 50.0W" in label  # pre-switch session peak survived the swap


def test_power_row_renders_spark_in_true_grid_preset():
    # In a genuinely wide terminal the grid preset holds (no auto-degrade), so
    # the power section sits in the ~half-width right column. The inline spark
    # must still render there — proving the width-adaptive rows re-render for
    # the grid column width, not only the full-width stack.
    async def _run():
        dash = HardwareDashboard(config=_config())
        app = _Host(dash)
        async with app.run_test(size=(200, 55)) as pilot:
            dash.update_metrics(MetricsUpdated(_snapshot(120.0, True)))
            await pilot.pause()
            assert dash.effective_layout_preset == "grid"
            return str(dash.query_one("#cpupwr-row", Static).render())

    row = asyncio.run(_run())
    assert "CPU 8.00W" in row
    assert "⠀" in row  # braille spark region drawn in the grid column


def test_grid_auto_degrades_below_min_width_and_recovers():
    # A requested grid narrower than _GRID_MIN_WIDTH must render as stack (the
    # two columns would fall below readability), and recover to grid once the
    # terminal widens again — without changing what was *requested*.
    async def _run():
        dash = HardwareDashboard(config=_config())  # requests grid by default
        app = _Host(dash)
        async with app.run_test(size=(80, 40)) as pilot:
            await pilot.pause()
            narrow = dash.effective_layout_preset
            requested_when_narrow = dash.layout_preset
            await pilot.resize_terminal(160, 40)
            await pilot.pause()
            wide = dash.effective_layout_preset
            return narrow, requested_when_narrow, wide

    narrow, requested_when_narrow, wide = asyncio.run(_run())
    assert narrow == "stack"  # auto-degraded under 96 cols
    assert requested_when_narrow == "grid"  # request is unchanged by degrade
    assert wide == "grid"  # recovers when width returns


def test_narrow_grid_columns_degrade_context_instead_of_clipping_rows():
    # At exactly _GRID_MIN_WIDTH (96 cols) each box gets ~44 content columns —
    # narrower than `P-CPU  41% @3200MHz (52°C)  avg 41% · max 41%` (45 cols) or
    # the residency row's full bar plus breakdown (50 cols). Those rows used to
    # lose their tail to a hard clip: a P-CPU headline missing its `%`, a
    # residency row missing `high37` entirely. Every row must now end on a whole
    # reading, and a wide terminal must still get the spelled-out avg/max form
    # (the degrade is width-driven, not unconditional).
    busy = dataclasses.replace(
        _snapshot(
            120.5,
            True,
            pcpu_util_pct=41.0,
            pcpu_freq_mhz=3200,
            cpu_temp_c=52.0,
            pcpu_residency_pct={"idle": 60, "low": 1, "mid": 2, "high": 37},
            net_rx_bps=12_000_000.0,
            net_tx_bps=340_000.0,
            net_available=True,
        ),
        swap_used_bytes=int(1.2 * 1024**3),
        swap_total_bytes=4 * 1024**3,
    )

    async def _run(width):
        dash = HardwareDashboard(config=_config())
        app = _Host(dash)
        async with app.run_test(size=(width, 45)) as pilot:
            # Two samples: the Network box is hidden until the first snapshot
            # reveals it, so its rows only have a measurable width from the
            # second frame on — the same one-sample settle a real session has.
            for _ in range(2):
                dash.update_metrics(MetricsUpdated(busy))
                await pilot.pause()
            rows = {}
            for row_id in (
                "#pcpu-summary-row",
                "#pcpu-residency-row",
                "#ram-label",
                "#bw-label",
                "#net-rx-label",
            ):
                widget = dash.query_one(row_id, Static)
                rows[row_id] = (str(widget.render()), widget.size.width)
            return dash.effective_layout_preset, rows

    preset, narrow = asyncio.run(_run(96))
    assert preset == "grid"  # 96 is the grid floor, not a degrade to stack
    for text, width in narrow.values():
        assert len(text.rstrip()) <= width  # nothing overflows its column
    # Each row still ends on a complete reading rather than a severed one.
    assert narrow["#pcpu-summary-row"][0].rstrip().endswith("%")
    assert "high37" in narrow["#pcpu-residency-row"][0]
    assert narrow["#ram-label"][0].rstrip().endswith("%")
    assert narrow["#bw-label"][0].rstrip().endswith("GB/s")
    assert narrow["#net-rx-label"][0].rstrip().endswith("B/s")
    # The swap figures are part of the headline and are never traded away.
    assert "sw:1.2/4.0GiB" in narrow["#ram-label"][0]

    _, wide = asyncio.run(_run(160))
    assert "avg 41% · max 41%" in wide["#pcpu-summary-row"][0]
    assert "avg 120.5 · max 120.5 GB/s" in wide["#bw-label"][0]


def test_set_layout_preset_rejects_unknown_name():
    dash = HardwareDashboard(config=_config())
    with pytest.raises(ValueError):
        dash.set_layout_preset("bogus")
