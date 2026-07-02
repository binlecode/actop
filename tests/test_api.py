"""Integration tests for the public actop Python API.

These tests require macOS with Apple Silicon hardware (marked local).
"""

import math
import time

import pytest

from actop import Monitor, Profiler, SystemSnapshot

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

    # Fan RPM — empty + unavailable on fanless Macs, otherwise physical RPMs
    assert isinstance(snapshot.fan_rpms, list)
    assert isinstance(snapshot.fan_available, bool)
    if snapshot.fan_available:
        assert len(snapshot.fan_rpms) > 0
        for rpm in snapshot.fan_rpms:
            assert 0.0 <= rpm < 20000.0
    else:
        assert snapshot.fan_rpms == []

    # Utilization percentages
    assert 0 <= snapshot.ecpu_util_pct <= 100
    assert 0 <= snapshot.pcpu_util_pct <= 100
    assert 0 <= snapshot.gpu_util_pct <= 100

    # Frequencies — must be positive on real hardware
    assert snapshot.ecpu_freq_mhz > 0
    assert snapshot.pcpu_freq_mhz > 0
    assert snapshot.gpu_freq_mhz >= 0  # GPU may be idle (0)

    # RAM
    assert snapshot.ram_used_gb > 0

    # Thermal state must be a non-empty string
    assert isinstance(snapshot.thermal_state, str)
    assert len(snapshot.thermal_state) > 0

    # Timestamp must be a recent Unix timestamp
    assert snapshot.timestamp > 0
    assert math.isfinite(snapshot.timestamp)


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
