"""Per-process power attribution (Tier-1 #1, Tier-2 #5) through public surfaces.

Covers the CPU-time-share signal end to end:

  * `utils.get_top_processes` exposes a bounded `cpu_time_share` per PID that is
    a partition of total CPU time (Σ ≤ 1.0), and it visibly tracks real compute
    under a self-induced busy loop;
  * the public `sort_processes` orders by that share for `SORT_POWER`; and
  * the real process table (`ActopApp`, mounted headless and fed a real
    `MetricsUpdated` through its message handler) renders the `PWR` column as
    `share × cpu_watts`, renders `–` for a first-sample `None` share, and shows
    the Σ-reconciliation token — with Σ(shown PWR) ≈ cpu_watts.

And the GPU extension (Tier-2 #5):

  * `gpu_registry.get_gpu_time_by_pid()` attributes real, ongoing GPU work to
    the right pid, observed against WindowServer -- a real, always-running
    Metal compositor -- rather than a self-induced load (no GPU-workload
    library is a project dependency, so unlike the CPU test above this can't
    drive its own controlled GPU work);
  * `utils.get_top_processes` folds that into a `gpu_time_share` that is also
    a bounded partition (Σ ≤ 1.0) alongside `cpu_time_share`; and
  * `sort_processes`/the process table combine both shares into one `PWR`
    value via `utils.attribute_power`, so a GPU-dominant process outranks a
    CPU-dominant one under `SORT_POWER` and the reconciliation token covers
    package CPU+GPU watts.

Functional only: drives public functions, the real config merge, and a real
widget through its public update path. No private attrs, no mocked data/logic.
"""

import asyncio
import time

import pytest

from actop import gpu_registry, utils
from actop.actop import build_parser
from actop.models import SystemSnapshot
from actop.native_sys import get_native_processes
from actop.tui.app import (
    SORT_POWER,
    ActopApp,
    sort_processes,
)
from actop.tui.widgets import MetricsUpdated


def _snapshot(cpu_watts: float, gpu_watts: float = 0.0) -> SystemSnapshot:
    return SystemSnapshot(
        timestamp=0.0,
        cpu_watts=cpu_watts,
        gpu_watts=gpu_watts,
        ane_watts=0.0,
        package_watts=cpu_watts + gpu_watts,
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


@pytest.mark.local  # needs real processes (get_native_processes is Darwin-only)
def test_get_top_processes_exposes_bounded_cpu_time_share():
    # Every non-first-sample share is a valid fraction, and the shares form a
    # partition of total CPU time (Σ ≤ 1.0 — a short-lived PID vanishing mid
    # poll only ever removes mass, never adds it). gpu_time_share rides along
    # on the same real polls and must satisfy the identical partition bound —
    # this is the wiring-level counterpart to the gpu_registry-level test
    # below, not a standalone shape check on its own.
    metrics = utils.get_top_processes(limit=1000)
    metrics = utils.get_top_processes(limit=1000)  # 2nd poll: deltas exist

    total_cpu = 0.0
    total_gpu = 0.0
    seen_cpu_value = False
    for row in metrics["cpu"]:
        assert "cpu_time_share" in row
        assert "gpu_time_share" in row

        cpu_share = row["cpu_time_share"]
        if cpu_share is not None:
            seen_cpu_value = True
            assert 0.0 <= cpu_share <= 1.0
            total_cpu += cpu_share

        gpu_share = row["gpu_time_share"]
        if gpu_share is not None:
            assert 0.0 <= gpu_share <= 1.0
            total_gpu += gpu_share

    assert seen_cpu_value, "expected at least one attributed CPU share on the 2nd poll"
    assert total_cpu <= 1.0 + 1e-9
    assert total_gpu <= 1.0 + 1e-9


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


@pytest.mark.local  # observes a real macOS system process (WindowServer)
def test_gpu_registry_tracks_windowserver_gpu_time():
    # No GPU-workload library is a project dependency (checked: no torch,
    # mlx, numpy, or pyobjc-Metal in pyproject.toml), so unlike the CPU test
    # above this can't drive its own controlled GPU load. WindowServer is a
    # real, always-running Metal compositor -- observing its real
    # accumulatedGPUTime counter (actual IOKit registry data, no mocks)
    # proves the pid-parsing and per-client summation in gpu_registry.py
    # against real, ongoing GPU work, not just a well-shaped return value.
    import subprocess

    pid = int(subprocess.check_output(["pgrep", "-x", "WindowServer"]).split()[0])

    first = gpu_registry.get_gpu_time_by_pid()
    assert pid in first, "WindowServer should always show up as a live GPU client"
    assert first[pid] > 0

    time.sleep(0.5)
    second = gpu_registry.get_gpu_time_by_pid()
    assert pid in second
    assert second[pid] >= first[pid]  # accumulatedGPUTime is monotonic


@pytest.mark.local  # get_native_processes is Darwin-only
def test_get_native_processes_cannot_see_windowserver():
    # Documents the permission gap that drives the exclusion in
    # utils.get_top_processes's GPU pass: gpu_registry reads the IOKit
    # registry (no UID check) and sees WindowServer just fine (previous
    # test), but get_native_processes uses libproc's PROC_PIDTASKALLINFO,
    # which only succeeds for same-UID processes -- so WindowServer (running
    # as a different user) never gets a row in the process table at all.
    import subprocess

    pid = int(subprocess.check_output(["pgrep", "-x", "WindowServer"]).split()[0])
    native_pids = {p["pid"] for p in get_native_processes()}
    assert pid not in native_pids


def test_sort_power_orders_by_cpu_time_share():
    procs = {
        "cpu": [
            {"pid": 1, "command": "a", "cpu_time_share": 0.10, "gpu_time_share": 0.0},
            {"pid": 2, "command": "b", "cpu_time_share": 0.60, "gpu_time_share": 0.0},
            {"pid": 3, "command": "c", "cpu_time_share": None, "gpu_time_share": None},
            {"pid": 4, "command": "d", "cpu_time_share": 0.25, "gpu_time_share": 0.0},
        ],
        "memory": [],
    }
    ordered = sort_processes(procs, SORT_POWER, limit=10, cpu_watts=8.0, gpu_watts=4.0)
    assert [p["pid"] for p in ordered] == [2, 4, 1, 3]  # None sinks to bottom


def test_sort_power_ranks_a_gpu_dominant_process_above_a_cpu_dominant_one():
    # pid 5 has a small CPU share but a large GPU share; pid 6 is the mirror
    # image. With gpu_watts > cpu_watts, pid 5's attributed watts outrank
    # pid 6's -- proving SORT_POWER uses the combined attribute_power value,
    # not cpu_time_share alone (which would rank these the other way).
    procs = {
        "cpu": [
            {
                "pid": 5,
                "command": "gpu-heavy",
                "cpu_time_share": 0.05,
                "gpu_time_share": 0.9,
            },
            {
                "pid": 6,
                "command": "cpu-heavy",
                "cpu_time_share": 0.5,
                "gpu_time_share": 0.0,
            },
        ],
        "memory": [],
    }
    ordered = sort_processes(procs, SORT_POWER, limit=10, cpu_watts=2.0, gpu_watts=20.0)
    assert [p["pid"] for p in ordered] == [5, 6]


def _proc(pid, command, cpu_share, gpu_share=0.0):
    return {
        "pid": pid,
        "command": command,
        "cpu_percent": (cpu_share or 0.0) * 100,
        "cpu_time_share": cpu_share,
        "gpu_time_share": gpu_share,
        "rss_mb": 100.0,
        "memory_percent": 1.0,
        "num_threads": 2,
    }


async def _render_process_table(cpu_watts, processes, gpu_watts=0.0):
    args = build_parser().parse_args(["--show-processes", "--interval", "600"])
    app = ActopApp(args)
    async with app.run_test() as pilot:
        app.action_toggle_pause()  # stop the live poll worker; drive it ourselves
        app.post_message(MetricsUpdated(_snapshot(cpu_watts, gpu_watts), processes))
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


@pytest.mark.local  # ActopApp reads real SoC info (int("?") on non-Darwin)
def test_process_table_renders_combined_cpu_gpu_pwr():
    # A GPU-dominant process must show PWR reflecting both domains, and the
    # reconciliation token must cover package CPU+GPU watts -- proving the
    # render path (not just sort_processes) actually calls attribute_power
    # rather than the old CPU-only "share * cpu_watts".
    processes = {
        "cpu": [
            _proc(444, "gpu-heavy", cpu_share=0.05, gpu_share=0.9),
        ],
        "memory": [],
    }
    columns, rows, subtitle = asyncio.run(
        _render_process_table(2.0, processes, gpu_watts=20.0)
    )
    pwr_idx = next(i for i, c in enumerate(columns) if "PWR" in c)

    cells = {row[0]: row[pwr_idx] for row in rows}
    assert cells["444"] == "18.10W"  # 0.05*2.0 + 0.9*20.0

    assert "18.1W" in subtitle  # Σ shown
    assert "22.0W" in subtitle  # pkg CPU+GPU = 2.0 + 20.0
