import resource
import sys
import time

import pytest

from actop.sampler import SampleResult, create_sampler

pytestmark = pytest.mark.local


def test_create_sampler_returns_sampler_and_backend():
    sampler, backend = create_sampler(1)
    try:
        assert backend == "ioreport"
        assert hasattr(sampler, "sample")
        assert hasattr(sampler, "close")
    finally:
        sampler.close()


def test_create_sampler_supports_subsamples_mode():
    sampler, backend = create_sampler(1, subsamples=2)
    try:
        assert backend == "ioreport"
        assert getattr(sampler, "manages_timing", False) is True
    finally:
        sampler.close()


def test_sampler_sample_returns_valid_metrics():
    sampler, _ = create_sampler(1)
    try:
        # First call primes the delta (returns None)
        first = sampler.sample()
        assert first is None

        time.sleep(1)

        # Retry until a valid result or timeout
        result = None
        deadline = time.monotonic() + 5.0
        while result is None and time.monotonic() < deadline:
            result = sampler.sample()
            if result is None:
                time.sleep(0.5)

        assert result is not None, "Sampler did not produce a reading within timeout"
        assert isinstance(result, SampleResult)

        cpu = result.cpu_metrics
        gpu = result.gpu_metrics
        thermal = result.thermal_pressure
        bw = result.bandwidth_metrics
        ts = result.timestamp

        # CPU metrics contract — keys consumed by actop.py
        assert isinstance(cpu, dict)
        for key in [
            "E-Cluster_active",
            "E-Cluster_freq_MHz",
            "P-Cluster_active",
            "P-Cluster_freq_MHz",
            "ane_W",
            "cpu_W",
            "gpu_W",
            "package_W",
            "e_core",
            "p_core",
        ]:
            assert key in cpu, "Missing CPU metric key: {}".format(key)
        assert isinstance(cpu["e_core"], list)
        assert isinstance(cpu["p_core"], list)
        assert cpu["cpu_W"] >= 0
        assert cpu["gpu_W"] >= 0
        assert cpu["ane_W"] >= 0
        assert cpu["package_W"] >= 0

        # GPU metrics contract
        assert isinstance(gpu, dict)
        assert "freq_MHz" in gpu
        assert "active" in gpu
        assert isinstance(gpu["active"], int)
        assert 0 <= gpu["active"] <= 100

        # Thermal pressure
        assert isinstance(thermal, str)

        # Bandwidth metrics contract: residency-weighted total DRAM bandwidth
        # in GB/s (not a byte counter). When the platform exposes the PMP/DCS BW
        # channel, the total is live and within a sane hardware ceiling.
        assert isinstance(bw, dict)
        assert "_available" in bw
        assert isinstance(bw["_available"], bool)
        assert "total_gbps" in bw
        assert isinstance(bw["total_gbps"], float)
        assert bw["total_gbps"] >= 0.0
        if bw["_available"]:
            # Idle systems still show a nonzero floor; no SoC exceeds ~2 TB/s.
            assert 0.0 < bw["total_gbps"] < 2000.0

        # Timestamp
        assert isinstance(ts, (int, float))
        assert ts > 0
        assert result.cpu_temp_c >= 0.0
        assert result.gpu_temp_c >= 0.0
    finally:
        sampler.close()


def test_sampler_resident_memory_stays_flat_over_many_cycles():
    """Resident memory must not grow over a long sampling run.

    Each ``sample()`` allocates CoreFoundation dictionaries (the IOReport
    samples snapshot and the delta) via ctypes. Those refs are invisible to
    Python's GC and are reclaimed only by the explicit ``cf_release`` calls in
    ``IOReportSampler._sample_once`` and ``IOReportSubscription.delta``. A
    dropped release leaks an entire CFDict (tens of KB) per cycle — silent in
    every functional test, but unbounded growth in actop's long-running
    monitor use case.

    Drives the real sampler (no sleep on the ``subsamples<=1`` path). Each
    cycle is a real kernel round-trip (~37 ms), so the count is tuned for a
    decisive signal rather than raw volume: empirically a clean run grows
    ~0.03 MB / 3000 cycles, so 2000 cycles against a 5 MB bound separates a
    real leak (tens of KB/cycle → tens of MB) from allocator noise by >100x
    while keeping the run near a minute. Catches any leak >= ~2.5 KB/cycle;
    sub-KB/cycle leaks are out of scope for an RSS-based guard.
    """
    sampler, _ = create_sampler(1)
    try:
        # Warm up so CPython and allocator arenas reach steady state before
        # baselining: ru_maxrss is a high-water mark, so warmup growth would
        # otherwise be misread as a leak. Measured flat by cycle ~200.
        for _ in range(200):
            sampler.sample()

        baseline = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

        for _ in range(2000):
            sampler.sample()

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    finally:
        sampler.close()

    # ru_maxrss is bytes on macOS, KiB on Linux. This test is macOS-only
    # (pytest.mark.local), but normalise so the bound is unambiguous.
    unit = 1 if sys.platform == "darwin" else 1024
    growth_mb = (peak - baseline) * unit / (1024 * 1024)

    assert growth_mb < 5, (
        f"resident memory grew {growth_mb:.1f} MB over 2000 sample cycles — "
        "likely a dropped cf_release in the sample/delta path"
    )
