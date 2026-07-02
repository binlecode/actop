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
import re

from textual.app import App, ComposeResult
from textual.widgets import Static

from actop.config import DashboardConfig
from actop.models import FanReading, SystemSnapshot
from actop.tui.widgets import BrailleChart, HardwareDashboard, MetricsUpdated


def _config(show_residency: bool = True) -> DashboardConfig:
    return DashboardConfig(
        sample_interval=1,
        avg_window=30,
        cpu_chart_ref_w=20.0,
        gpu_chart_ref_w=30.0,
        ane_max_power=8.0,
        package_ref_w=58.0,
        max_cpu_bw=100.0,
        max_gpu_bw=100.0,
        chip_name="Apple M-test",
        e_core_count=4,
        p_core_count=4,
        gpu_core_count=10,
        power_scale="profile",
        chart_glyph="dots",
        show_cores=False,
        show_residency=show_residency,
        alert_bw_sat_percent=85,
        alert_package_power_percent=85,
        alert_throttle_freq_percent=90,
        alert_swap_rise_gb=1.0,
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
    ecpu_residency_pct: dict = None,
    pcpu_residency_pct: dict = None,
    gpu_residency_pct: dict = None,
    fans: list = None,
    fan_available: bool = False,
    timestamp: float = 0.0,
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
        ram_used_gb=18.0,
        swap_used_gb=0.0,
        ram_total_gb=32.0,
        ram_used_percent=56.0,
        swap_total_gb=0.0,
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
    )


class _Host(App):
    """Minimal mount point so the real dashboard widget can be laid out."""

    def __init__(self, dashboard: HardwareDashboard) -> None:
        super().__init__()
        self._dashboard = dashboard

    def compose(self) -> ComposeResult:
        yield self._dashboard


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
            "ram_label": str(dash.query_one("#ram-label", Static).render()),
            "bw_label": str(dash.query_one("#bw-label", Static).render()),
            "bw_label_display": dash.query_one("#bw-label", Static).display,
            "bw_chart_display": dash.query_one("#bw-chart", BrailleChart).display,
            "fan_label": str(dash.query_one("#fan-label", Static).render()),
            "fan_label_display": dash.query_one("#fan-label", Static).display,
            "status": str(dash.query_one("#status-line", Static).render()),
        }
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


def test_ram_row_renders_from_snapshot_fields():
    # LC-1: RAM/swap are carried on the SystemSnapshot (ram_used_gb /
    # ram_total_gb), not a second get_ram_metrics_dict() call. The row must
    # render the snapshot's used/total GB through the real update path.
    state = asyncio.run(_drive([_snapshot(0.0, False)]))
    assert "RAM 18.0/32.0GB" in state["ram_label"]


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
    assert "1200/6000 · 980/6000 RPM" in state["fan_label"]


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
    assert "Fan 1200 RPM" in state["fan_label"]


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


def test_residency_bar_has_no_gaps_or_overflow_at_fixed_width():
    # Largest-remainder allocation must always fill the bar exactly: the
    # glyph count inside the brackets must equal the configured bar width,
    # even for percentages that don't divide evenly.
    skewed = _snapshot(
        0.0,
        False,
        gpu_residency_pct={"idle": 33, "low": 34, "mid": 17, "high": 16},
    )
    state = asyncio.run(_drive([skewed]))
    bar = re.search(r"\[(.*?)\]", state["gpu_residency_row"]).group(1)
    assert len(bar) == 16


def test_residency_rows_hidden_when_show_residency_disabled():
    # show_residency is a startup-only density choice baked into compose(),
    # like show_cores — disabled means the rows never exist at all.
    state = asyncio.run(
        _drive([_snapshot(0.0, False)], config=_config(show_residency=False))
    )
    assert state["pcpu_residency_row"] is None
    assert state["ecpu_residency_row"] is None
    assert state["gpu_residency_row"] is None


def test_status_line_surfaces_chart_time_window_span():
    # The charts' visible time span scales silently with terminal width, so the
    # dashboard surfaces it as a `span` token on the status line. Once laid out,
    # a well-formed span (seconds/minutes/hours) must appear — otherwise the
    # window the charts cover is ambiguous to the user.
    state = asyncio.run(_drive([_snapshot(120.0, True)]))
    assert re.search(r"span \d+(?:s|m(?:\d{2}s)?|h(?:\d{2}m)?)", state["status"])
