"""Public data model for actop hardware snapshots."""

from dataclasses import dataclass, field
from typing import NamedTuple

_EMPTY_RESIDENCY = {"idle": 0, "low": 0, "mid": 0, "high": 0}


class FanReading(NamedTuple):
    """Per-fan tachometer reading. `max` is None when the SMC exposes no
    max-RPM key for the fan (or reports max <= 0, which peers treat as
    unknown). Immutable to match the SampleResult fan payload contract."""

    current: float  # actual RPM (F{n}Ac); 0.0 is a legitimate idle reading
    max: float | None = None  # max RPM (F{n}Mx); None when unknown


def _default_residency() -> dict:
    return dict(_EMPTY_RESIDENCY)


@dataclass
class CoreSample:
    index: int  # system CPU index (matches psutil percpu order)
    active_pct: int  # IOReport residency-weighted activity (0–100)
    freq_mhz: int  # IOReport residency-weighted frequency (MHz)


@dataclass
class ProcessSample:
    """One process's per-frame resource use (public API type).

    Collected only when the caller opts in (Monitor(include_processes=True)).
    `attributed_w` is the process's share of package CPU+GPU watts, computed
    in L2 (api) via analytics.attribute_power; it is None when there is no CPU
    delta yet (a first sample after launch/resume) — the TUI renders that as
    "–" rather than a misleading 0.0. `cpu_time_share`/`gpu_time_share` are the
    underlying [0, 1] partitions of total CPU/GPU time, likewise None while a
    domain's first delta is still pending.
    """

    pid: int
    command: str
    cpu_percent: float  # Δ CPU-time over the interval, as a percent
    cpu_time_share: float | None  # fraction of total CPU time, or None
    gpu_time_share: float | None  # fraction of total GPU time, or None
    num_threads: int
    attributed_w: float | None  # CPU+GPU watts, or None (no CPU delta yet)
    # Resident set size in raw bytes — the canonical, exact field. Defaulted so
    # existing ProcessSample(...) call sites stay valid.
    rss_bytes: int = 0


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
    thermal_state: str  # "Nominal", "Fair", "Serious", "Critical"
    bandwidth_gbps: float  # Total memory bandwidth (read + write); 0.0 if unavailable
    bandwidth_available: bool
    # DVFS max (silicon ceiling) per domain, in MHz; 0 when unavailable. Defaulted so
    # existing SystemSnapshot(...) call sites stay valid. The throttle indicator
    # expresses current freq as a fraction of the ceiling.
    ecpu_max_freq_mhz: int = 0
    pcpu_max_freq_mhz: int = 0
    gpu_max_freq_mhz: int = 0
    # RAM/swap used-percent, completing the frame contract so the TUI
    # (and any API consumer) reads memory from the snapshot alone rather than a
    # second get_ram_metrics_dict() call. Defaulted for construction-site
    # compatibility, matching the *_max_freq_mhz precedent above.
    ram_used_percent: float = 0.0
    # Memory in raw bytes — the canonical, exact fields. Bytes are prefix-free
    # (no GB-vs-GiB ambiguity), lossless, and the base unit Prometheus/OpenMetrics
    # naming expects. Formatting into GiB is a presentation concern and lives in
    # the TUI. Note bandwidth_gbps above stays genuinely decimal GB/s — that is
    # Apple's own unit for the bus, not an inconsistency. Defaulted for
    # construction-site compatibility, matching the *_max_freq_mhz precedent above.
    ram_used_bytes: int = 0
    ram_total_bytes: int = 0
    swap_used_bytes: int = 0
    swap_total_bytes: int = 0
    # ANE utilization as a percent of the SoC's ANE reference power
    # (soc_profiles.ane_max_w), computed in L2 so it is a data point rather
    # than a render-time derivation.
    ane_util_pct: float = 0.0
    # GPU utilization from IOAccelerator PerformanceStatistics (driver point
    # reads, no interval integration — see docs/SPEC-system.md §3.8).
    # gpu_util_pct above remains the interval-integrated primary metric.
    # Renderer vs Tiler separates shader/compute work from geometry work: an
    # MLX/CoreML compute frame shows Renderer high with Tiler near zero.
    gpu_device_pct: float = 0.0
    gpu_renderer_pct: float = 0.0
    gpu_tiler_pct: float = 0.0
    gpu_perf_stats_available: bool = False
    # Provenance of gpu_util_pct: "residency" (IOReport, preferred) or
    # "ioaccelerator" (fallback used when the GPU DVFS table could not be
    # classified, so residency and gpu_freq_mhz are meaningless).
    gpu_util_source: str = "residency"
    # Fan tachometer, one entry per fan; empty + fan_available=False on
    # fanless Macs (mirrors the bandwidth_available hide-row pattern above).
    # `fans` carries structured current/max readings; `fan_rpms` is a derived
    # current-only convenience kept for the export.py NDJSON/Prometheus contract.
    fans: list = field(default_factory=list)  # list[FanReading]
    fan_rpms: list = field(
        default_factory=list
    )  # list[float] (== [f.current for f in fans])
    fan_available: bool = False
    # Network I/O rates (bytes/s), delta-over-interval from getifaddrs()
    # if_data counters. net_available is False on non-Darwin or when
    # getifaddrs() returns no usable AF_LINK entries.
    net_rx_bps: float = 0.0
    net_tx_bps: float = 0.0
    net_available: bool = False
    # Disk I/O rates (bytes/s), delta-over-interval from IOKit
    # AppleAPFSVolume (primary) or IOBlockStorageDriver (fallback)
    # Statistics. disk_available is False when no volume exposes counters.
    disk_read_bps: float = 0.0
    disk_write_bps: float = 0.0
    disk_available: bool = False
    e_cores: list = field(default_factory=list)  # list[CoreSample]
    p_cores: list = field(default_factory=list)  # list[CoreSample]
    # Per-process resource use, CPU-sorted; empty unless the caller opted in
    # (Monitor(include_processes=True)). Watt attribution happens in L2, so a
    # public-API user gets the same PWR data the TUI shows without touching L1.
    processes: list = field(default_factory=list)  # list[ProcessSample]
    # P-state residency distribution: percent of time (ints summing to ~100)
    # spent in idle/low/mid/high DVFS buckets since the last sample, per
    # domain. Bucketed relative to the domain's DVFS ceiling (see
    # sampler._compute_residency_distribution), not raw MHz, so it's
    # comparable across chips.
    ecpu_residency_pct: dict = field(default_factory=_default_residency)
    pcpu_residency_pct: dict = field(default_factory=_default_residency)
    gpu_residency_pct: dict = field(default_factory=_default_residency)
