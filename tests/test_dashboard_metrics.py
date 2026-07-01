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
from actop.models import SystemSnapshot
from actop.tui.widgets import BrailleChart, HardwareDashboard, MetricsUpdated


def _config() -> DashboardConfig:
    return DashboardConfig(
        sample_interval=1,
        avg_window=30,
        cpu_chart_ref_w=20.0,
        gpu_chart_ref_w=30.0,
        ane_max_power=8.0,
        package_ref_w=58.0,
        max_cpu_bw=100.0,
        max_gpu_bw=100.0,
        e_core_count=4,
        p_core_count=4,
        power_scale="profile",
        chart_glyph="dots",
        show_cores=False,
        alert_bw_sat_percent=85,
        alert_package_power_percent=85,
        alert_swap_rise_gb=1.0,
        alert_sustain_samples=3,
        subsamples=1,
        process_display_count=50,
        show_processes=False,
        process_filter_pattern=None,
    )


def _snapshot(
    bandwidth_gbps: float, bandwidth_available: bool, package_watts: float = 21.5
) -> SystemSnapshot:
    return SystemSnapshot(
        timestamp=0.0,
        cpu_watts=8.0,
        gpu_watts=12.0,
        ane_watts=0.0,
        package_watts=package_watts,
        ecpu_util_pct=10.0,
        pcpu_util_pct=20.0,
        gpu_util_pct=30.0,
        cpu_temp_c=0.0,
        gpu_temp_c=0.0,
        ecpu_freq_mhz=1200,
        pcpu_freq_mhz=3200,
        gpu_freq_mhz=900,
        ram_used_gb=18.0,
        swap_used_gb=0.0,
        thermal_state="Nominal",
        bandwidth_gbps=bandwidth_gbps,
        bandwidth_available=bandwidth_available,
    )


_RAM = {
    "used_percent": 56.0,
    "used_GB": 18.0,
    "total_GB": 32.0,
    "swap_used_GB": 0.0,
    "swap_total_GB": 0.0,
}


class _Host(App):
    """Minimal mount point so the real dashboard widget can be laid out."""

    def __init__(self, dashboard: HardwareDashboard) -> None:
        super().__init__()
        self._dashboard = dashboard

    def compose(self) -> ComposeResult:
        yield self._dashboard


async def _drive(snapshots):
    """Mount the dashboard, push each snapshot, return the final widget state."""
    dash = HardwareDashboard(config=_config())
    app = _Host(dash)
    async with app.run_test() as pilot:
        for snap in snapshots:
            dash.update_metrics(
                MetricsUpdated(snap, dict(_RAM), {"cpu": [], "memory": []})
            )
            await pilot.pause()
        return {
            "pkg_label": str(dash.query_one("#pkgpwr-label", Static).render()),
            "bw_label": str(dash.query_one("#bw-label", Static).render()),
            "bw_label_display": dash.query_one("#bw-label", Static).display,
            "bw_chart_display": dash.query_one("#bw-chart", BrailleChart).display,
            "status": str(dash.query_one("#status-line", Static).render()),
        }


def test_package_power_headline_renders_total_soc_watts():
    state = asyncio.run(_drive([_snapshot(120.0, True)]))
    # The total-SoC figure (package_watts) must reach the headline label.
    assert "Package Power" in state["pkg_label"]
    assert "21.5" in state["pkg_label"]


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
    # Session energy integrates package_watts × interval each frame. With
    # interval=1s and package draws of 50W then 70W: 120 J = 120/3600 Wh ≈
    # 33 mWh (sub-0.1Wh renders in mWh).
    state = asyncio.run(
        _drive(
            [
                _snapshot(120.0, True, package_watts=50.0),
                _snapshot(120.0, True, package_watts=70.0),
            ]
        )
    )
    assert "energy 33mWh" in state["status"]


def test_status_line_surfaces_chart_time_window_span():
    # The charts' visible time span scales silently with terminal width, so the
    # dashboard surfaces it as a `span` token on the status line. Once laid out,
    # a well-formed span (seconds/minutes/hours) must appear — otherwise the
    # window the charts cover is ambiguous to the user.
    state = asyncio.run(_drive([_snapshot(120.0, True)]))
    assert re.search(r"span \d+(?:s|m(?:\d{2}s)?|h(?:\d{2}m)?)", state["status"])
