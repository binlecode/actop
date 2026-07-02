"""Per-process power attribution (Tier-1 #1, Tier-2 #5) through public surfaces.

Covers the CPU-time-share signal end to end after LC-2 moved attribution into
L2 (processes now ride on `SystemSnapshot.processes` as typed `ProcessSample`s):

  * `utils.get_top_processes` (L1) exposes a bounded `cpu_time_share` per PID
    that is a partition of total CPU time (Σ ≤ 1.0), and it visibly tracks real
    compute under a self-induced busy loop;
  * `analytics.attribute_power` combines CPU + GPU shares into watts;
  * `Monitor(include_processes=True)` (L2) surfaces watt-attributed
    `ProcessSample`s so an API consumer gets the same PWR data the TUI shows;
  * the public `sort_processes` orders those samples for `SORT_POWER`; and
  * the real process table (`ActopApp`, mounted headless and fed a real
    `SystemSnapshot` with `ProcessSample`s through its message handler) renders
    the `PWR` column from `attributed_w`, renders `–` for a `None`
    (first-sample) attribution, and shows the Σ-reconciliation token.

And the GPU extension (Tier-2 #5):

  * `gpu_registry.get_gpu_time_by_pid()` attributes real, ongoing GPU work to
    the right pid, observed against WindowServer -- a real, always-running
    Metal compositor;
  * `utils.get_top_processes` folds that into a `gpu_time_share` that is also a
    bounded partition (Σ ≤ 1.0) alongside `cpu_time_share`; and
  * `attribute_power` combines both shares into one `PWR` value, so a
    GPU-dominant process outranks a CPU-dominant one under `SORT_POWER`.

Functional only: drives public functions, the public Monitor API, and a real
widget through its public update path. No private attrs, no mocked data/logic.
"""

import asyncio
import time

import pytest

from actop import gpu_registry, utils
from actop.actop import build_parser
from actop.analytics import attribute_power
from actop.api import Monitor
from actop.models import ProcessSample, SystemSnapshot
from actop.native_sys import get_native_processes
from actop.tui.app import (
    SORT_POWER,
    ActopApp,
    sort_processes,
)
from actop.tui.widgets import MetricsUpdated


def _sample(pid, command, attributed_w, cpu_percent=0.0, rss_mb=100.0):
    """A ProcessSample as L2 would emit it (attributed_w precomputed)."""
    return ProcessSample(
        pid=pid,
        command=command,
        cpu_percent=cpu_percent,
        cpu_time_share=None if attributed_w is None else 0.0,
        gpu_time_share=0.0,
        rss_mb=rss_mb,
        num_threads=2,
        attributed_w=attributed_w,
    )


def _snapshot(cpu_watts, gpu_watts=0.0, processes=None):
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
        processes=processes or [],
    )


def test_attribute_power_combines_cpu_and_gpu_shares():
    # A GPU-dominant process's watts must reflect both domains, weighted by
    # each rail's magnitude -- this is the combination the PWR column and the
    # SORT_POWER ordering both build on.
    assert attribute_power(0.05, 0.9, 2.0, 20.0) == pytest.approx(
        0.05 * 2.0 + 0.9 * 20.0
    )
    # A None share (pending first delta) contributes 0, not a crash, and does
    # not block the other domain's contribution.
    assert attribute_power(None, 0.5, 8.0, 4.0) == pytest.approx(0.5 * 4.0)
    assert attribute_power(0.5, None, 8.0, 4.0) == pytest.approx(0.5 * 8.0)


@pytest.mark.local  # needs real processes (get_native_processes is Darwin-only)
def test_get_top_processes_exposes_bounded_cpu_time_share():
    # Every non-first-sample share is a valid fraction, and the shares form a
    # partition of total CPU time (Σ ≤ 1.0 — a short-lived PID vanishing mid
    # poll only ever removes mass, never adds it). gpu_time_share rides along
    # on the same real polls and must satisfy the identical partition bound.
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


@pytest.mark.local  # Monitor.get_snapshot needs real Apple-Silicon sampling
def test_monitor_surfaces_watt_attributed_process_samples():
    # The public L2 path: opting into process collection yields typed
    # ProcessSamples with watts already attributed, so an API consumer never
    # touches L1 or re-does the join. Two snapshots so CPU deltas exist.
    with Monitor(interval_s=1, include_processes=True) as mon:
        mon.get_snapshot()
        snap = mon.get_snapshot()

    assert isinstance(snap.processes, list)
    assert snap.processes, "expected a non-empty process list on the 2nd snapshot"
    assert all(isinstance(p, ProcessSample) for p in snap.processes)

    # A live self-process (this test) must appear.
    import os

    assert any(p.pid == os.getpid() for p in snap.processes)

    # Partition property asserted inside the behavioral test (per CLAUDE.md):
    # the watts attributed across all sampled processes never exceed package
    # CPU+GPU watts, since each is a fractional share of that budget.
    total_attributed = sum(
        p.attributed_w for p in snap.processes if p.attributed_w is not None
    )
    assert total_attributed <= snap.cpu_watts + snap.gpu_watts + 1e-6


@pytest.mark.local  # Monitor collection is opt-in; off by default
def test_monitor_omits_processes_by_default():
    # Default Monitor stays cheap: no process walk, empty list.
    with Monitor(interval_s=1) as mon:
        snap = mon.get_snapshot()
    assert snap.processes == []


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


def test_sort_power_orders_by_attributed_watts():
    # SORT_POWER orders by the L2-computed attributed_w; a sample with no
    # attribution yet (None) sinks to the bottom rather than jumping to the top.
    procs = [
        _sample(1, "a", attributed_w=1.0),
        _sample(2, "b", attributed_w=6.0),
        _sample(3, "c", attributed_w=None),
        _sample(4, "d", attributed_w=2.5),
    ]
    ordered = sort_processes(procs, SORT_POWER, limit=10)
    assert [p.pid for p in ordered] == [2, 4, 1, 3]  # None sinks to bottom


def test_sort_power_ranks_a_gpu_dominant_sample_above_a_cpu_dominant_one():
    # pid 5 draws more attributed watts than pid 6 (its GPU-heavy share landed
    # on the larger GPU rail); SORT_POWER must rank it first. The combination
    # itself is exercised in test_attribute_power_combines_cpu_and_gpu_shares.
    gpu_heavy = _sample(
        5, "gpu-heavy", attributed_w=attribute_power(0.05, 0.9, 2.0, 20.0)
    )
    cpu_heavy = _sample(
        6, "cpu-heavy", attributed_w=attribute_power(0.5, 0.0, 2.0, 20.0)
    )
    ordered = sort_processes([cpu_heavy, gpu_heavy], SORT_POWER, limit=10)
    assert [p.pid for p in ordered] == [5, 6]


async def _render_process_table(snapshot):
    args = build_parser().parse_args(["--show-processes", "--interval", "600"])
    app = ActopApp(args)
    async with app.run_test() as pilot:
        app.action_toggle_pause()  # stop the live poll worker; drive it ourselves
        app.post_message(MetricsUpdated(snapshot))
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
    snapshot = _snapshot(
        8.0,
        processes=[
            _sample(111, "busy", attributed_w=6.00, cpu_percent=75.0),
            _sample(222, "idle", attributed_w=0.40, cpu_percent=5.0),
            _sample(333, "fresh", attributed_w=None),  # first sample: no share yet
        ],
    )
    columns, rows, subtitle = asyncio.run(_render_process_table(snapshot))

    # PWR column exists (may carry the active-sort "*" marker).
    assert any("PWR" in c for c in columns), columns
    pwr_idx = next(i for i, c in enumerate(columns) if "PWR" in c)

    cells = {row[0]: row[pwr_idx] for row in rows}
    assert cells["111"] == "6.00W"
    assert cells["222"] == "0.40W"
    assert cells["333"] == "–"  # None attribution -> em dash, never a wrong 0.0

    # Reconciliation token: Σ shown vs package CPU watts (a partition of it).
    assert "6.4W" in subtitle  # 6.00 + 0.40 shown
    assert "8.0W" in subtitle  # pkg CPU watts


@pytest.mark.local  # ActopApp reads real SoC info (int("?") on non-Darwin)
def test_process_table_renders_combined_cpu_gpu_pwr():
    # A GPU-dominant sample must render PWR reflecting both domains, and the
    # reconciliation token must cover package CPU+GPU watts.
    combined = attribute_power(0.05, 0.9, 2.0, 20.0)  # == 18.1
    snapshot = _snapshot(
        2.0,
        gpu_watts=20.0,
        processes=[_sample(444, "gpu-heavy", attributed_w=combined)],
    )
    columns, rows, subtitle = asyncio.run(_render_process_table(snapshot))
    pwr_idx = next(i for i, c in enumerate(columns) if "PWR" in c)

    cells = {row[0]: row[pwr_idx] for row in rows}
    assert cells["444"] == "18.10W"

    assert "18.1W" in subtitle  # Σ shown
    assert "22.0W" in subtitle  # pkg CPU+GPU = 2.0 + 20.0
