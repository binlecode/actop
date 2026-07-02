"""AlertEngine behavior driven through its public feed() surface.

LC-3 moved alert/throttle/session-energy math out of the TUI widget into
`actop.analytics.AlertEngine` (L2). These tests exercise that engine directly
with sequences of real-shaped `SystemSnapshot`s — the same data any API/export
consumer would build — asserting the sustain-threshold, swap-rise, and energy
contracts on the returned `AlertFrame`s. No mocks, no private access: `feed()`
is the public entrypoint and `AlertFrame` fields are the public output.
"""

from actop.analytics import AlertEngine, AlertFrame
from actop.models import SystemSnapshot

_IDLE_RESIDENCY = {"idle": 100, "low": 0, "mid": 0, "high": 0}


def _snapshot(
    *,
    timestamp: float = 0.0,
    package_watts: float = 10.0,
    bandwidth_gbps: float = 0.0,
    bandwidth_available: bool = True,
    pcpu_util_pct: float = 5.0,
    pcpu_freq_mhz: int = 3200,
    pcpu_max_freq_mhz: int = 3200,
    cpu_temp_c: float = 0.0,
    thermal_state: str = "Nominal",
    swap_used_gb: float = 0.0,
    swap_total_gb: float = 0.0,
) -> SystemSnapshot:
    return SystemSnapshot(
        timestamp=timestamp,
        cpu_watts=4.0,
        gpu_watts=2.0,
        ane_watts=0.0,
        package_watts=package_watts,
        ecpu_util_pct=5.0,
        pcpu_util_pct=pcpu_util_pct,
        gpu_util_pct=5.0,
        cpu_temp_c=cpu_temp_c,
        gpu_temp_c=0.0,
        ecpu_freq_mhz=1000,
        pcpu_freq_mhz=pcpu_freq_mhz,
        gpu_freq_mhz=800,
        ram_used_gb=8.0,
        swap_used_gb=swap_used_gb,
        ram_total_gb=32.0,
        ram_used_percent=25.0,
        swap_total_gb=swap_total_gb,
        thermal_state=thermal_state,
        bandwidth_gbps=bandwidth_gbps,
        bandwidth_available=bandwidth_available,
        pcpu_max_freq_mhz=pcpu_max_freq_mhz,
        gpu_max_freq_mhz=1000,
        ecpu_residency_pct=dict(_IDLE_RESIDENCY),
        pcpu_residency_pct=dict(_IDLE_RESIDENCY),
        gpu_residency_pct=dict(_IDLE_RESIDENCY),
    )


def _engine(**overrides) -> AlertEngine:
    kwargs = dict(
        bw_sat_percent=85,
        pkg_power_percent=85,
        throttle_freq_percent=90,
        swap_rise_gb=1.0,
        sustain_samples=3,
        max_total_bw=200.0,
        package_ref_w=58.0,
    )
    kwargs.update(overrides)
    return AlertEngine(**kwargs)


def test_throttle_alert_fires_only_after_sustain_threshold():
    # A busy P-cluster held well below its DVFS ceiling while hot must NOT alert
    # until the condition has held for sustain_samples consecutive frames:
    # N-1 hot frames → no alert; the Nth → alert.
    engine = _engine(sustain_samples=3)
    hot = dict(
        pcpu_util_pct=95.0,
        pcpu_freq_mhz=2000,  # 62% of 3200 < 90%
        pcpu_max_freq_mhz=3200,
        thermal_state="Serious",
    )
    frames = [engine.feed(_snapshot(**hot)) for _ in range(3)]
    assert [f.cpu_throttle for f in frames] == [False, False, True]


def test_throttle_counter_resets_when_condition_clears():
    # A single cool frame in the middle must reset the sustain counter, so the
    # alert does not fire on the very next hot frame.
    engine = _engine(sustain_samples=3)
    hot = dict(
        pcpu_util_pct=95.0,
        pcpu_freq_mhz=2000,
        pcpu_max_freq_mhz=3200,
        thermal_state="Serious",
    )
    cool = dict(pcpu_util_pct=5.0, pcpu_freq_mhz=3200, thermal_state="Nominal")
    seq = [hot, hot, cool, hot, hot]  # never 3 hot in a row
    frames = [engine.feed(_snapshot(**kw)) for kw in seq]
    assert not any(f.cpu_throttle for f in frames)


def test_swap_rise_alert_fires_on_growth_across_window():
    # Swap climbing by >= alert_swap_rise_gb across the sustain window must
    # raise the swap alert; a flat swap footprint must not.
    engine = _engine(sustain_samples=3, swap_rise_gb=1.0)
    rising = [0.0, 0.5, 1.0, 1.5]  # rise of 1.5 GB across 4 retained samples
    frames = [engine.feed(_snapshot(swap_used_gb=v, swap_total_gb=8.0)) for v in rising]
    assert frames[-1].swap_alert is True
    assert frames[-1].swap_rise_gb >= 1.0

    flat = _engine(sustain_samples=3, swap_rise_gb=1.0)
    flat_frames = [
        flat.feed(_snapshot(swap_used_gb=2.0, swap_total_gb=8.0)) for _ in range(4)
    ]
    assert not any(f.swap_alert for f in flat_frames)


def test_session_energy_is_monotonic_and_timestamp_integrated():
    # First feed() has no prior timestamp → 0 J. Subsequent frames add
    # package_watts × dt (from snapshot.timestamp), so the cumulative energy is
    # non-decreasing and matches the hand-computed integral.
    engine = _engine()
    frames = [
        engine.feed(_snapshot(timestamp=t, package_watts=60.0))
        for t in (0.0, 1.0, 2.0, 4.0)
    ]
    energies = [f.session_energy_j for f in frames]
    assert energies[0] == 0.0  # no prior timestamp
    assert energies == sorted(energies)  # monotonic non-decreasing
    # 60W over dt of 1s, 1s, 2s → 60 + 60 + 120 = 240 J.
    assert energies[-1] == 240.0


def test_feed_returns_alert_frame_with_thermal_verdict():
    # A non-Nominal thermal state surfaces immediately (no sustain) as a
    # thermal_alert on the returned frame.
    engine = _engine()
    frame = engine.feed(_snapshot(thermal_state="Serious"))
    assert isinstance(frame, AlertFrame)
    assert frame.thermal_alert is True
