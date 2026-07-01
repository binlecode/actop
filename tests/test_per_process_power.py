"""Per-process power attribution (Tier-1 #1) through public surfaces.

Covers the CPU-time-share signal end to end:

  * `utils.get_top_processes` exposes a bounded `cpu_time_share` per PID that is
    a partition of total CPU time (Σ ≤ 1.0), and it visibly tracks real compute
    under a self-induced busy loop;
  * the public `sort_processes` orders by that share for `SORT_POWER`; and
  * the real process table (`ActopApp`, mounted headless and fed a real
    `MetricsUpdated` through its message handler) renders the `PWR` column as
    `share × cpu_watts`, renders `–` for a first-sample `None` share, and shows
    the Σ-reconciliation token — with Σ(shown PWR) ≈ cpu_watts.

Functional only: drives public functions, the real config merge, and a real
widget through its public update path. No private attrs, no mocked data/logic.
"""

import asyncio
import time

import pytest

from actop import utils
from actop.actop import build_parser
from actop.models import SystemSnapshot
from actop.tui.app import (
    SORT_POWER,
    ActopApp,
    sort_processes,
)
from actop.tui.widgets import MetricsUpdated


def _snapshot(cpu_watts: float) -> SystemSnapshot:
    return SystemSnapshot(
        timestamp=0.0,
        cpu_watts=cpu_watts,
        gpu_watts=0.0,
        ane_watts=0.0,
        package_watts=cpu_watts,
        ecpu_util_pct=0.0,
        pcpu_util_pct=0.0,
        gpu_util_pct=0.0,
        cpu_temp_c=0.0,
        gpu_temp_c=0.0,
        ecpu_freq_mhz=0,
        pcpu_freq_mhz=0,
        gpu_freq_mhz=0,
        ram_used_gb=0.0,
        swap_used_gb=0.0,
        thermal_state="Nominal",
        bandwidth_gbps=0.0,
        bandwidth_available=False,
    )


_RAM = {
    "used_percent": 50.0,
    "used_GB": 8.0,
    "total_GB": 16.0,
    "swap_used_GB": 0.0,
    "swap_total_GB": 0.0,
}


@pytest.mark.local  # needs real processes (get_native_processes is Darwin-only)
def test_get_top_processes_exposes_bounded_cpu_time_share():
    # Every non-first-sample share is a valid fraction, and the shares form a
    # partition of total CPU time (Σ ≤ 1.0 — a short-lived PID vanishing mid
    # poll only ever removes mass, never adds it).
    metrics = utils.get_top_processes(limit=1000)
    metrics = utils.get_top_processes(limit=1000)  # 2nd poll: deltas exist

    total = 0.0
    seen_value = False
    for row in metrics["cpu"]:
        assert "cpu_time_share" in row
        share = row["cpu_time_share"]
        if share is None:
            continue
        seen_value = True
        assert 0.0 <= share <= 1.0
        total += share
    assert seen_value, "expected at least one attributed share on the 2nd poll"
    assert total <= 1.0 + 1e-9


@pytest.mark.local  # needs real CPU-time deltas from live processes
def test_cpu_time_share_tracks_busy_loop():
    # A process burning CPU must climb in attributed share — this proves the
    # attribution tracks real compute, not just liveness.
    import os

    me = os.getpid()

    def my_share(metrics):
        for row in metrics["cpu"]:
            if row["pid"] == me:
                return row["cpu_time_share"]
        return None

    utils.get_top_processes(limit=5000)  # prime the cache
    end = time.time() + 1.2
    x = 0
    while time.time() < end:
        x += 1  # busy loop
    after = my_share(utils.get_top_processes(limit=5000))

    assert after is not None
    assert after > 0.1, f"busy process share unexpectedly low: {after}"


def test_sort_power_orders_by_cpu_time_share():
    procs = {
        "cpu": [
            {"pid": 1, "command": "a", "cpu_time_share": 0.10},
            {"pid": 2, "command": "b", "cpu_time_share": 0.60},
            {"pid": 3, "command": "c", "cpu_time_share": None},
            {"pid": 4, "command": "d", "cpu_time_share": 0.25},
        ],
        "memory": [],
    }
    ordered = sort_processes(procs, SORT_POWER, limit=10)
    assert [p["pid"] for p in ordered] == [2, 4, 1, 3]  # None sinks to bottom


def _proc(pid, command, share):
    return {
        "pid": pid,
        "command": command,
        "cpu_percent": (share or 0.0) * 100,
        "cpu_time_share": share,
        "rss_mb": 100.0,
        "memory_percent": 1.0,
        "num_threads": 2,
    }


async def _render_process_table(cpu_watts, processes):
    args = build_parser().parse_args(["--show-processes", "--interval", "600"])
    app = ActopApp(args)
    async with app.run_test() as pilot:
        app.action_toggle_pause()  # stop the live poll worker; drive it ourselves
        app.post_message(MetricsUpdated(_snapshot(cpu_watts), dict(_RAM), processes))
        await pilot.pause()
        await pilot.pause()

        from textual.widgets import DataTable

        table = app.query_one("#process-table", DataTable)
        columns = [str(col.label) for col in table.columns.values()]
        rows = [
            [str(cell) for cell in table.get_row_at(i)] for i in range(table.row_count)
        ]
        return columns, rows, str(table.border_subtitle or "")


@pytest.mark.local  # ActopApp reads real SoC info (int("?") on non-Darwin)
def test_process_table_renders_pwr_column_and_reconciliation_token():
    processes = {
        "cpu": [
            _proc(111, "busy", 0.75),
            _proc(222, "idle", 0.05),
            _proc(333, "fresh", None),  # first sample: no share yet
        ],
        "memory": [],
    }
    columns, rows, subtitle = asyncio.run(_render_process_table(8.0, processes))

    # PWR column exists (may carry the active-sort "*" marker).
    assert any("PWR" in c for c in columns), columns
    pwr_idx = next(i for i, c in enumerate(columns) if "PWR" in c)

    cells = {row[0]: row[pwr_idx] for row in rows}
    assert cells["111"] == "6.00W"  # 0.75 * 8.0
    assert cells["222"] == "0.40W"  # 0.05 * 8.0
    assert cells["333"] == "–"  # None share -> em dash, never a wrong 0.0

    # Reconciliation token: Σ shown vs package CPU watts (a partition of it).
    assert "6.4W" in subtitle  # 6.00 + 0.40 shown
    assert "8.0W" in subtitle  # pkg CPU watts
