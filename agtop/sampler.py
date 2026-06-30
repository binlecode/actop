"""Unified metrics sampler with IOReport backend."""

import re
import time
from typing import NamedTuple

from .native_sys import get_dvfs_tables_native, get_thermal_pressure


class SampleResult(NamedTuple):
    cpu_metrics: dict
    gpu_metrics: dict
    thermal_pressure: str
    bandwidth_metrics: dict
    timestamp: float
    cpu_temp_c: float = 0.0  # max CPU die temperature (Celsius), 0 if unavailable
    gpu_temp_c: float = 0.0  # max GPU die temperature (Celsius), 0 if unavailable


class IOReportSampler:
    """Direct IOReport sampling. No sudo required."""

    def __init__(self, interval, subsamples=1):
        from .ioreport import IOReportSubscription
        from .smc import SMCReader

        self._sub = IOReportSubscription(
            [
                ("Energy Model", None),
                ("CPU Stats", "CPU Core Performance States"),
                ("GPU Stats", "GPU Performance States"),
                # DRAM bandwidth: PMP/DCS BW exposes per-agent residency
                # histograms. We only parse the full-range AMCC total; the
                # ~90 other channels are subscribed (group-level) but their
                # states are skipped in delta() to hold the idle-CPU budget.
                ("PMP", "DCS BW"),
            ]
        )
        self._interval = interval
        self._subsamples = max(1, int(subsamples))
        self._prev_sample = None
        self._prev_time = None
        self._core_counts = _get_core_counts()
        self._dvfs = get_dvfs_tables_native()
        self._smc = SMCReader()

    def sample(self):
        if self._subsamples <= 1:
            return self._sample_once(include_temperatures=True)

        if self._prev_sample is None:
            self._sample_once(include_temperatures=False)
            return None

        step_s = self._interval / float(self._subsamples)
        parts = []
        for _ in range(self._subsamples):
            time.sleep(step_s)
            part = self._sample_once(include_temperatures=False)
            if part is not None:
                parts.append(part)

        if not parts:
            return None

        cpu_temp, gpu_temp = self._read_temperatures()
        return self._average_samples(parts)._replace(
            cpu_temp_c=cpu_temp,
            gpu_temp_c=gpu_temp,
        )

    @property
    def manages_timing(self):
        return self._subsamples > 1

    def _sample_once(self, include_temperatures):
        from .ioreport import cf_release

        new_sample = self._sub.sample()
        new_time = time.monotonic()

        if self._prev_sample is None:
            self._prev_sample = new_sample
            self._prev_time = new_time
            return None

        items = self._sub.delta(self._prev_sample, new_sample, _keep_states)
        elapsed_s = new_time - self._prev_time

        cf_release(self._prev_sample)
        self._prev_sample = new_sample
        self._prev_time = new_time

        if elapsed_s <= 0:
            return None

        if include_temperatures:
            cpu_temp, gpu_temp = self._read_temperatures()
        else:
            cpu_temp = 0.0
            gpu_temp = 0.0

        return self._convert(items, elapsed_s, cpu_temp, gpu_temp)

    def _read_temperatures(self):
        temps = self._smc.read_temperatures()
        cpu_temps = temps.cpu_temps_c
        gpu_temps = temps.gpu_temps_c
        cpu_temp = max(cpu_temps) if cpu_temps else 0.0
        gpu_temp = max(gpu_temps) if gpu_temps else 0.0
        return (cpu_temp, gpu_temp)

    def _average_samples(self, samples):
        count = len(samples)
        base = samples[-1]

        cpu_metrics = {}
        for key, value in base.cpu_metrics.items():
            if isinstance(value, list):
                cpu_metrics[key] = list(value)
            elif isinstance(value, bool):
                cpu_metrics[key] = value
            elif isinstance(value, (int, float)):
                avg = sum(float(s.cpu_metrics.get(key, 0.0)) for s in samples) / count
                if _is_int_cpu_metric(key):
                    cpu_metrics[key] = int(avg)
                else:
                    cpu_metrics[key] = avg
            else:
                cpu_metrics[key] = value

        gpu_metrics = {}
        for key, value in base.gpu_metrics.items():
            if isinstance(value, bool):
                gpu_metrics[key] = value
            elif isinstance(value, (int, float)):
                avg = sum(float(s.gpu_metrics.get(key, 0.0)) for s in samples) / count
                if key in ("freq_MHz", "active"):
                    gpu_metrics[key] = int(avg)
                else:
                    gpu_metrics[key] = avg
            else:
                gpu_metrics[key] = value

        bandwidth_metrics = {}
        for key, value in base.bandwidth_metrics.items():
            if isinstance(value, bool):
                bandwidth_metrics[key] = any(
                    bool(s.bandwidth_metrics.get(key, False)) for s in samples
                )
            elif isinstance(value, (int, float)):
                bandwidth_metrics[key] = (
                    sum(float(s.bandwidth_metrics.get(key, 0.0)) for s in samples)
                    / count
                )
            else:
                bandwidth_metrics[key] = value

        return SampleResult(
            cpu_metrics=cpu_metrics,
            gpu_metrics=gpu_metrics,
            thermal_pressure=base.thermal_pressure,
            bandwidth_metrics=bandwidth_metrics,
            timestamp=base.timestamp,
            cpu_temp_c=base.cpu_temp_c,
            gpu_temp_c=base.gpu_temp_c,
        )

    def _convert(self, items, elapsed_s, cpu_temp_c=0.0, gpu_temp_c=0.0):
        """Convert IOReport items to the same dict format as parsers.py output."""
        cpu_energy_j = 0.0
        gpu_energy_j = 0.0
        ane_energy_j = 0.0

        e_core_data = {}
        p_core_data = {}
        gpu_freq_mhz = 0
        gpu_active_pct = 0
        dram_bw_residencies = []

        ecpu_freqs = self._dvfs.get("ecpu", [])
        pcpu_freqs = self._dvfs.get("pcpu", [])
        gpu_freqs = self._dvfs.get("gpu", [])

        for item in items:
            if item.group == "Energy Model":
                energy_j = _energy_to_joules(item.integer_value, item.unit)
                if "CPU Energy" in item.channel:
                    cpu_energy_j += energy_j
                elif "GPU Energy" in item.channel:
                    gpu_energy_j += energy_j
                elif "ANE" in item.channel:
                    ane_energy_j += energy_j

            elif (
                item.group == "CPU Stats"
                and item.subgroup == "CPU Core Performance States"
            ):
                if "ECPU" in item.channel:
                    freq, active = _compute_residency_metrics(
                        item.state_residencies, ecpu_freqs
                    )
                    idx = _parse_core_index(item.channel, "ECPU")
                    if idx is not None:
                        e_core_data[idx] = (freq, active)
                elif "PCPU" in item.channel:
                    freq, active = _compute_residency_metrics(
                        item.state_residencies, pcpu_freqs
                    )
                    idx = _parse_core_index(item.channel, "PCPU")
                    if idx is not None:
                        p_core_data[idx] = (freq, active)

            elif (
                item.group == "GPU Stats" and item.subgroup == "GPU Performance States"
            ):
                if "GPUPH" in item.channel:
                    gpu_freq_mhz, gpu_active_pct = _compute_residency_metrics(
                        item.state_residencies, gpu_freqs
                    )

            elif item.group == "PMP" and item.subgroup == "DCS BW":
                # Total DRAM bandwidth = sum over all AMCC RD+WR instances
                # (one per memory-controller die; multi-die SoCs expose
                # several). Per-agent channels (EACC/PACC/AGX/...) are skipped
                # in delta() and intentionally not parsed: they hard-cap at
                # 32 GB/s and cannot attribute high bandwidth correctly.
                if item.channel.startswith("AMCC") and item.channel.endswith("RD+WR"):
                    dram_bw_residencies.extend(item.state_residencies)

        # Scale energy to match parsers.py convention:
        # parsers.py returns cpu_W = energy_mJ / 1000 = energy_J
        # agtop.py does cpu_power_W = cpu_W / sample_interval
        # Scale so that dividing by sample_interval gives correct watts.
        scale = self._interval / elapsed_s if elapsed_s > 0 else 1.0
        cpu_e = cpu_energy_j * scale
        gpu_e = gpu_energy_j * scale
        ane_e = ane_energy_j * scale
        package_e = cpu_e + gpu_e + ane_e

        cpu_metrics = {
            "E-Cluster_active": 0,
            "E-Cluster_freq_Mhz": 0,
            "P-Cluster_active": 0,
            "P-Cluster_freq_Mhz": 0,
            "ane_W": ane_e,
            "cpu_W": cpu_e,
            "gpu_W": gpu_e,
            "package_W": package_e,
            "e_core": [],
            "p_core": [],
        }

        e_freqs = []
        e_actives = []
        for cluster_idx in sorted(e_core_data):
            freq, active = e_core_data[cluster_idx]
            sys_idx = cluster_idx
            cpu_metrics["e_core"].append(sys_idx)
            cpu_metrics["E-Cluster" + str(sys_idx) + "_active"] = active
            cpu_metrics["E-Cluster" + str(sys_idx) + "_freq_Mhz"] = freq
            e_freqs.append(freq)
            e_actives.append(active)

        if e_freqs:
            cpu_metrics["E-Cluster_freq_Mhz"] = max(e_freqs)
        if e_actives:
            cpu_metrics["E-Cluster_active"] = int(sum(e_actives) / len(e_actives))

        p_freqs = []
        p_actives = []
        for cluster_idx in sorted(p_core_data):
            freq, active = p_core_data[cluster_idx]
            sys_idx = cluster_idx
            cpu_metrics["p_core"].append(sys_idx)
            cpu_metrics["P-Cluster" + str(sys_idx) + "_active"] = active
            cpu_metrics["P-Cluster" + str(sys_idx) + "_freq_Mhz"] = freq
            p_freqs.append(freq)
            p_actives.append(active)

        if p_freqs:
            cpu_metrics["P-Cluster_freq_Mhz"] = max(p_freqs)
        if p_actives:
            cpu_metrics["P-Cluster_active"] = int(sum(p_actives) / len(p_actives))

        gpu_metrics = {
            "freq_MHz": gpu_freq_mhz,
            "active": gpu_active_pct,
        }

        total_gbps = _compute_bandwidth_gbps(dram_bw_residencies)
        bandwidth_metrics = {
            "total_gbps": total_gbps,
            "_available": bool(dram_bw_residencies),
        }

        return SampleResult(
            cpu_metrics=cpu_metrics,
            gpu_metrics=gpu_metrics,
            thermal_pressure=get_thermal_pressure(),
            bandwidth_metrics=bandwidth_metrics,
            timestamp=time.time(),
            cpu_temp_c=cpu_temp_c,
            gpu_temp_c=gpu_temp_c,
        )

    def close(self):
        from .ioreport import cf_release

        if self._prev_sample is not None:
            cf_release(self._prev_sample)
            self._prev_sample = None
        self._sub.close()
        self._smc.close()


def create_sampler(interval, subsamples=1):
    """Create an IOReport sampler.

    Returns (sampler, backend_name) where backend_name is always 'ioreport'.
    """
    return (IOReportSampler(interval, subsamples=subsamples), "ioreport")


# --- Private helpers ---


def _get_core_counts():
    """Get P-core and E-core counts from sysctl."""
    from .native_sys import get_sysctl_int

    p_count = 0
    e_count = 0
    try:
        val = get_sysctl_int("hw.perflevel0.logicalcpu")
        if val is not None:
            p_count = int(val)
    except (ValueError, OSError):
        pass
    try:
        val = get_sysctl_int("hw.perflevel1.logicalcpu")
        if val is not None:
            e_count = int(val)
    except (ValueError, OSError):
        pass
    return {"p_count": p_count, "e_count": e_count}


def _energy_to_joules(value, unit):
    """Convert energy value to joules based on unit label."""
    unit_lower = unit.lower().strip()
    if unit_lower == "nj":
        return value / 1e9
    if unit_lower in ("uj", "\u00b5j"):
        return value / 1e6
    if unit_lower == "mj":
        return value / 1e3
    if unit_lower == "j":
        return float(value)
    # Unknown unit — assume nJ (common on Apple Silicon)
    return value / 1e9


_CORE_INDEX_PATTERN = re.compile(r"^[EP]CPU(\d+)")


def _is_int_cpu_metric(key):
    if key in (
        "E-Cluster_active",
        "E-Cluster_freq_Mhz",
        "P-Cluster_active",
        "P-Cluster_freq_Mhz",
    ):
        return True
    return key.endswith("_active") or key.endswith("_freq_Mhz")


def _parse_core_index(channel_name, prefix):
    """Extract core index from channel name like 'ECPU000' or 'PCPU130'.

    Channel names use zero-padded multi-digit format. The first digits
    after the prefix encode the core number (e.g. ECPU010 → core 1,
    PCPU130 → core 13).
    """
    m = _CORE_INDEX_PATTERN.match(channel_name)
    if not m:
        return None
    raw = m.group(1)
    # Strip trailing zero that IOReport appends (e.g. "000" → core 0,
    # "010" → core 1, "130" → core 13). The last digit is always 0.
    if len(raw) >= 2:
        raw = raw[:-1]
    try:
        return int(raw)
    except ValueError:
        return None


def _compute_residency_metrics(residencies, freq_table=None):
    """Compute weighted average frequency and active percentage.

    State names can be:
    - Plain integers like "600" (frequency in MHz directly)
    - "V{n}P{m}" (CPU DVFS state — n is the table index)
    - "P{n}" (GPU performance state — n is the table index)
    - "IDLE", "DOWN", "OFF" (inactive states)

    freq_table: list of MHz values indexed by state position, from
    lowest to highest frequency. Used to resolve V{n}P{m} / P{n} names.

    Returns (freq_mhz, active_percent) as (int, int).
    """
    if freq_table is None:
        freq_table = []

    total_ns = 0
    active_ns = 0
    weighted_freq_sum = 0

    for name, ns in residencies:
        total_ns += ns
        name_upper = name.upper()
        if name_upper in ("IDLE", "DOWN", "OFF", "UNKNOWN", ""):
            continue

        freq_mhz = _resolve_state_freq(name, freq_table)
        if freq_mhz is None:
            continue

        active_ns += ns
        weighted_freq_sum += freq_mhz * ns

    if total_ns <= 0 or active_ns <= 0:
        return (0, 0)

    avg_freq = int(weighted_freq_sum / active_ns)
    active_pct = int(active_ns / total_ns * 100)
    return (avg_freq, active_pct)


_VP_PATTERN = re.compile(r"^V(\d+)P\d+$")
_P_PATTERN = re.compile(r"^P(\d+)$")


def _resolve_state_freq(name, freq_table):
    """Resolve a P-state name to a frequency in MHz.

    Returns frequency as int, or None if the state is unrecognized.
    """
    # Try plain integer (e.g. "600" for 600 MHz)
    try:
        return int(name)
    except ValueError:
        pass

    # Try V{n}P{m} pattern — n is the index into the freq table
    m = _VP_PATTERN.match(name)
    if m:
        idx = int(m.group(1))
        if 0 <= idx < len(freq_table):
            return freq_table[idx]
        return 0

    # Try P{n} pattern (GPU) — n is the index into the freq table
    m = _P_PATTERN.match(name)
    if m:
        idx = int(m.group(1))
        if 0 <= idx < len(freq_table):
            return freq_table[idx]
        return 0

    return None


def _keep_states(group, subgroup, channel):
    """Decide whether delta() should extract per-state residencies for a channel.

    The PMP/DCS BW group has ~90 channels of 32 buckets each, but we parse only
    the AMCC totals. Skipping state extraction for the rest avoids thousands of
    per-cycle ctypes round-trips. All non-PMP groups extract states as before.
    """
    if group == "PMP":
        return channel.startswith("AMCC")
    return True


_GBPS_PATTERN = re.compile(r"(\d+)\s*GB/s")


def _compute_bandwidth_gbps(residencies):
    """Residency-weighted average bandwidth (GB/s) from a DCS BW histogram.

    Each state name is a bandwidth bucket ("32GB/s", "64GB/s", …) and its value
    is the time spent at that level. The weighted mean Σ(level·time)/Σ(time) is
    already in GB/s — no division by the sample interval. Returns 0.0 when the
    histogram is empty (no DCS channel on this platform).
    """
    weighted_sum = 0.0
    total = 0.0
    for name, residency in residencies:
        m = _GBPS_PATTERN.search(name)
        if not m:
            continue
        weighted_sum += float(m.group(1)) * residency
        total += residency
    if total <= 0:
        return 0.0
    return weighted_sum / total
