"""Integration tests for the public actop Python API.

These tests require macOS with Apple Silicon hardware (marked local).
"""

import math
import time

import pytest

from actop import AsyncMonitor, Monitor, Profiler, SystemSnapshot
from actop.native_sys import get_sysctl_int

pytestmark = pytest.mark.local


def test_monitor_get_snapshot_returns_valid_snapshot():
    with Monitor(interval_s=1) as m:
        snapshot = m.get_snapshot()

    assert isinstance(snapshot, SystemSnapshot)

    # Power — must be non-negative
    assert snapshot.cpu_watts >= 0
    assert snapshot.gpu_watts >= 0
    assert snapshot.ane_watts >= 0
    assert snapshot.package_watts >= 0

    # All power values must be finite
    for field in ("cpu_watts", "gpu_watts", "ane_watts", "package_watts"):
        assert math.isfinite(getattr(snapshot, field)), f"{field} is not finite"

    # Temperature — 0.0 means unavailable, otherwise physical range
    assert snapshot.cpu_temp_c == 0.0 or 0 < snapshot.cpu_temp_c < 150
    assert snapshot.gpu_temp_c == 0.0 or 0 < snapshot.gpu_temp_c < 150

    # Fan — empty + unavailable on fanless Macs, otherwise structured
    # FanReading(current, max) with fan_rpms as the derived current-only view.
    assert isinstance(snapshot.fans, list)
    assert isinstance(snapshot.fan_rpms, list)
    assert isinstance(snapshot.fan_available, bool)
    assert snapshot.fan_rpms == [f.current for f in snapshot.fans]
    if snapshot.fan_available:
        assert len(snapshot.fans) > 0
        for fan in snapshot.fans:
            assert 0.0 <= fan.current < 20000.0
            assert fan.max is None or 0.0 < fan.max < 20000.0
    else:
        assert snapshot.fans == []

    # Utilization percentages
    assert 0 <= snapshot.ecpu_util_pct <= 100
    assert 0 <= snapshot.pcpu_util_pct <= 100
    assert 0 <= snapshot.gpu_util_pct <= 100

    # active% and the residency distribution are two views of the same residency
    # data and must not contradict: active ≈ 100 - idle, within integer rounding
    # and the largest-remainder redistribution (≤2 points). A ≥3-point gap means
    # the two consumers of _resolve_state_freq have diverged on how they classify
    # a state — e.g. one counting an unresolvable state as active while the other
    # buckets it as idle.
    for active_pct, residency in (
        (snapshot.ecpu_util_pct, snapshot.ecpu_residency_pct),
        (snapshot.pcpu_util_pct, snapshot.pcpu_residency_pct),
        (snapshot.gpu_util_pct, snapshot.gpu_residency_pct),
    ):
        assert abs(active_pct - (100 - residency["idle"])) <= 2

    # Bandwidth availability must mean "we have a real reading", not merely "a
    # DCS channel exists": a present-but-silent channel reporting available with
    # 0.0 GB/s is the misleading zero the hide-row logic exists to prevent. This
    # also cross-checks the bucket-name parse against raw residency — if Apple
    # renamed the buckets, gbps would read 0 while the channel still had time.
    assert snapshot.bandwidth_available == (snapshot.bandwidth_gbps > 0)

    # Frequencies — must be positive on real hardware
    assert snapshot.ecpu_freq_mhz > 0
    assert snapshot.pcpu_freq_mhz > 0
    assert snapshot.gpu_freq_mhz >= 0  # GPU may be idle (0)

    # DVFS ceilings are max() over the pmgr voltage-states table (the tables are
    # not ordered, so table[-1] would be wrong) with Hz→MHz rounded, not floored.
    # A units/stride regression surfaces as an implausible ceiling; a bad
    # positional lookup as a current frequency above the ceiling.
    assert 500 < snapshot.ecpu_max_freq_mhz < 10000
    assert 500 < snapshot.pcpu_max_freq_mhz < 10000
    assert 500 < snapshot.gpu_max_freq_mhz < 10000
    assert snapshot.ecpu_freq_mhz <= snapshot.ecpu_max_freq_mhz
    assert snapshot.pcpu_freq_mhz <= snapshot.pcpu_max_freq_mhz
    assert snapshot.gpu_freq_mhz <= snapshot.gpu_max_freq_mhz

    # Driver-reported GPU utilization (IOAccelerator PerformanceStatistics) — a
    # second, independent view of GPU busyness. Every Apple Silicon GPU exposes
    # the statistics dict, so unavailable here means the class match or the
    # property read regressed, not that the hardware lacks the counters.
    assert snapshot.gpu_perf_stats_available is True
    for field in ("gpu_device_pct", "gpu_renderer_pct", "gpu_tiler_pct"):
        value = getattr(snapshot, field)
        assert 0 <= value <= 100, f"{field} out of physical range: {value}"

    # Provenance contract: the GPU DVFS table classified (gpu_max_freq_mhz > 0,
    # asserted above), so the interval-integrated residency reading must remain
    # the primary metric. If the fallback condition ever inverts, gpu_util_pct
    # silently becomes the driver's instantaneous device_pct — a regression in
    # sampling semantics that the active ≈ 100 - idle check above would also
    # start failing, since the two measures diverge per-sample by design.
    assert snapshot.gpu_util_source == "residency"

    # RAM — LC-1 completes the frame contract: totals + used-percent now ride
    # on the snapshot so the TUI needs no second get_ram_metrics_dict() call.
    assert snapshot.ram_used_bytes > 0
    assert snapshot.ram_total_bytes > 0
    assert snapshot.ram_used_bytes <= snapshot.ram_total_bytes
    assert 0 <= snapshot.ram_used_percent <= 100
    assert snapshot.swap_total_bytes >= 0

    # Net/disk I/O — every Apple Silicon Mac has at least one AF_LINK interface
    # (getifaddrs) and APFS volumes (IOKit Statistics), so both readers must
    # report available on real hardware; rates are deltas over the interval and
    # must be finite and non-negative. The unavailable->zero coherence keeps a
    # hidden row honest: a platform with no counters must not carry phantom
    # rates (mirrors the bandwidth_available contract above).
    assert isinstance(snapshot.net_available, bool)
    assert isinstance(snapshot.disk_available, bool)
    assert snapshot.net_available is True
    assert snapshot.disk_available is True
    for field in (
        "net_rx_bps",
        "net_tx_bps",
        "disk_read_bps",
        "disk_write_bps",
    ):
        value = getattr(snapshot, field)
        assert math.isfinite(value), f"{field} is not finite"
        assert value >= 0, f"{field} is negative: {value}"

    # Memory crosses the API as exact bytes, so ram_total_bytes must equal
    # hw.memsize outright — no rounding tolerance needed. This is the assertion
    # that catches a unit regression: a quantity divided by 2^30 while naming
    # itself decimal GB would silently drift 7.4% against the genuinely decimal
    # bandwidth_gbps.
    memsize_bytes = get_sysctl_int("hw.memsize")
    assert memsize_bytes and memsize_bytes > 0
    assert snapshot.ram_total_bytes == memsize_bytes

    # ANE utilization is a data point (L2), computed from ane_watts against the
    # SoC's ANE reference power — consistent with the raw watts it derives from.
    assert 0 <= snapshot.ane_util_pct <= 100
    if snapshot.ane_watts == 0:
        assert snapshot.ane_util_pct == 0

    # Thermal state must be a non-empty string
    assert isinstance(snapshot.thermal_state, str)
    assert len(snapshot.thermal_state) > 0

    # Timestamp must be a recent Unix timestamp
    assert snapshot.timestamp > 0
    assert math.isfinite(snapshot.timestamp)


@pytest.mark.local  # needs AppleAPFSVolume Statistics on real hardware
def test_disk_write_rate_tracks_real_io():
    """The disk rate must track real transfers, not just well-shaped fields.

    A background writer paces ~100 MB/s of fsync'd writes while get_snapshot()
    sleeps through its interval and samples — the APFS volume's cumulative
    "Bytes written to block device" counter must climb inside that window, so
    the delta-over-interval surfaces as a disk_write_bps well above idle. This
    exercises the whole native IOKit read → sampler delta → API path against
    real, ongoing I/O, per the net/disk TODO's testing contract.
    """
    import os
    import tempfile
    import threading

    stop = threading.Event()
    written = []

    def writer():
        fd, path = tempfile.mkstemp(prefix="actop-disk-io-test-")
        try:
            with os.fdopen(fd, "wb") as f:
                chunk = b"x" * (2 * 1024 * 1024)
                total = 0
                while not stop.is_set():
                    f.write(chunk)
                    total += len(chunk)
                    time.sleep(0.02)  # pace ~100 MB/s: in-flight across the window
                f.flush()
                os.fsync(f.fileno())
                written.append(total)
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    with Monitor(interval_s=1) as m:
        m.get_snapshot()  # prime the delta
        thread = threading.Thread(target=writer)
        thread.start()
        try:
            snap = m.get_snapshot()  # sleeps interval_s, then samples the window
        finally:
            stop.set()
            thread.join(timeout=30)
        assert written, "writer thread did not complete"

    assert snap.disk_available, "no disk Statistics counters reported"
    # The paced writer moves >100 MB inside the ~1 s window; 1 MB/s is a wide
    # margin above idle noise (~0) while still catching a counter/delta bug
    # that froze the rate at 0.
    assert snap.disk_write_bps > 1_000_000, (
        f"disk_write_bps too low: {snap.disk_write_bps}"
    )


def test_async_monitor_get_snapshot_async_returns_snapshot():
    # AsyncMonitor is the documented async surface of the public API; a broken
    # thread-pool wrapper (e.g. never awaiting the executor) would surface as a
    # missing/broken snapshot here, not in any sync test.
    import asyncio

    async def _run():
        with AsyncMonitor(interval_s=1) as mon:
            snap = await mon.get_snapshot_async()
        return snap

    snapshot = asyncio.run(_run())
    assert isinstance(snapshot, SystemSnapshot)
    assert snapshot.cpu_watts >= 0
    assert snapshot.package_watts >= 0
    assert snapshot.timestamp > 0


def test_profiler_collects_samples_and_summarizes():
    with Profiler(interval_s=1) as p:
        time.sleep(3)

    summary = p.get_summary()

    assert summary, "get_summary() returned empty dict"
    assert summary["sample_count"] >= 2

    expected_keys = {
        "sample_count",
        "duration_s",
        "avg_cpu_watts",
        "avg_gpu_watts",
        "avg_package_watts",
        "peak_cpu_watts",
        "peak_gpu_watts",
        "peak_package_watts",
        "total_cpu_joules",
        "total_gpu_joules",
        "total_package_joules",
    }
    assert expected_keys.issubset(summary.keys())

    # All numeric values must be non-negative
    for key, val in summary.items():
        if isinstance(val, (int, float)):
            assert val >= 0, f"summary[{key!r}] = {val} is negative"

    assert summary["duration_s"] > 0

    assert summary["peak_package_watts"] >= summary["avg_package_watts"]
    assert summary["peak_cpu_watts"] >= summary["avg_cpu_watts"]
    assert summary["peak_gpu_watts"] >= summary["avg_gpu_watts"]
