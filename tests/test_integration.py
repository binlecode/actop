import pytest

from actop import Monitor, SystemSnapshot

pytestmark = pytest.mark.local


def test_monitor_subsamples_mode_produces_snapshot():
    # subsamples > 1 selects the sampler that manages its own sleep timing,
    # a distinct runtime path from the default single-sample Monitor used in
    # test_api.  get_snapshot() must not double-sleep and must still return a
    # well-formed snapshot built from the aggregated subsamples.
    with Monitor(interval_s=1, subsamples=3) as monitor:
        assert monitor.manages_timing is True

        snapshot = monitor.get_snapshot()

    assert isinstance(snapshot, SystemSnapshot)
    assert snapshot.cpu_watts >= 0.0
    assert snapshot.package_watts >= 0.0
    assert 0 <= snapshot.ecpu_util_pct <= 100
    assert 0 <= snapshot.pcpu_util_pct <= 100
    assert snapshot.ram_used_bytes > 0
    assert snapshot.timestamp > 0
