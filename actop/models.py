"""Public data model for actop hardware snapshots."""

from dataclasses import dataclass, field


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
    e_cores: list = field(default_factory=list)  # list[CoreSample]
    p_cores: list = field(default_factory=list)  # list[CoreSample]
