"""Public data model for actop hardware snapshots."""

from dataclasses import dataclass, field
from typing import NamedTuple, Optional

_EMPTY_RESIDENCY = {"idle": 0, "low": 0, "mid": 0, "high": 0}


class FanReading(NamedTuple):
    """Per-fan tachometer reading. `max` is None when the SMC exposes no
    max-RPM key for the fan (or reports max <= 0, which peers treat as
    unknown). Immutable to match the SampleResult fan payload contract."""

    current: float  # actual RPM (F{n}Ac); 0.0 is a legitimate idle reading
    max: Optional[float] = None  # max RPM (F{n}Mx); None when unknown


def _default_residency() -> dict:
    return dict(_EMPTY_RESIDENCY)


@dataclass
class CoreSample:
    index: int  # system CPU index (matches psutil percpu order)
    active_pct: int  # IOReport residency-weighted activity (0–100)
    freq_mhz: int  # IOReport residency-weighted frequency (MHz)


@dataclass
class SystemSnapshot:
    timestamp: float
    cpu_watts: float
    gpu_watts: float
    ane_watts: float
    package_watts: float
    ecpu_util_pct: float  # E-cluster average activity (0–100)
    pcpu_util_pct: float  # P-cluster average activity (0–100)
    gpu_util_pct: float  # GPU active (0–100)
    cpu_temp_c: float  # CPU die temperature (°C); 0.0 if unavailable
    gpu_temp_c: float  # GPU die temperature (°C); 0.0 if unavailable
    ecpu_freq_mhz: int
    pcpu_freq_mhz: int
    gpu_freq_mhz: int
    ram_used_gb: float
    swap_used_gb: float
    thermal_state: str  # "Nominal", "Fair", "Serious", "Critical"
    bandwidth_gbps: float  # Total memory bandwidth (read + write); 0.0 if unavailable
    bandwidth_available: bool
    # DVFS max (silicon ceiling) per domain, in MHz; 0 when unavailable. Defaulted so
    # existing SystemSnapshot(...) call sites stay valid. The throttle indicator
    # expresses current freq as a fraction of the ceiling.
    ecpu_max_freq_mhz: int = 0
    pcpu_max_freq_mhz: int = 0
    gpu_max_freq_mhz: int = 0
    # Fan tachometer, one entry per fan; empty + fan_available=False on
    # fanless Macs (mirrors the bandwidth_available hide-row pattern above).
    # `fans` carries structured current/max readings; `fan_rpms` is a derived
    # current-only convenience kept for the export.py NDJSON/Prometheus contract.
    fans: list = field(default_factory=list)  # list[FanReading]
    fan_rpms: list = field(
        default_factory=list
    )  # list[float] (== [f.current for f in fans])
    fan_available: bool = False
    e_cores: list = field(default_factory=list)  # list[CoreSample]
    p_cores: list = field(default_factory=list)  # list[CoreSample]
    # P-state residency distribution: percent of time (ints summing to ~100)
    # spent in idle/low/mid/high DVFS buckets since the last sample, per
    # domain. Bucketed relative to the domain's DVFS ceiling (see
    # sampler._compute_residency_distribution), not raw MHz, so it's
    # comparable across chips.
    ecpu_residency_pct: dict = field(default_factory=_default_residency)
    pcpu_residency_pct: dict = field(default_factory=_default_residency)
    gpu_residency_pct: dict = field(default_factory=_default_residency)
