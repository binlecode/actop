"""L2 domain analytics: derived judgments over acquired data points.

This module sits above acquisition (`ioreport`/`smc`/`gpu_registry`/
`native_sys`/`utils`) and below presentation (`tui/*`, `export`). It imports
only `models`/`power_scaling` types — never `tui/*` — so hardware judgments
(per-process power attribution, throttle detection, alert sustain logic, and
cumulative session energy) are data points any API or export consumer can
obtain, not render-time math trapped in the view.
"""

from collections import deque
from dataclasses import dataclass

from actop.power_scaling import clamp_percent


def attribute_power(share_cpu, share_gpu, cpu_watts, gpu_watts):
    """Watts attributed to a process from its CPU/GPU time shares.

    A None share (first sample, no delta yet) contributes 0 rather than
    blocking the other domain's contribution.
    """
    watts = 0.0
    if share_cpu is not None:
        watts += share_cpu * cpu_watts
    if share_gpu is not None:
        watts += share_gpu * gpu_watts
    return watts


# Throttle detection gates (heuristics). A cluster is only "throttling" when it
# is working hard yet held below its DVFS ceiling while hot — an idle or
# power-capped cluster at low freq is not throttling. The thermal-pressure
# signal is the primary "hot" test; the die-temp gate is a fallback for machines
# whose SMC temps read 0.
_THROTTLE_UTIL_GATE = 80.0  # percent: cluster must be at least this busy
_THROTTLE_TEMP_C = 90.0  # °C: die-temp fallback when thermal_state stays Nominal


def domain_throttling(
    util,
    freq,
    max_freq,
    temp,
    thermal_state,
    *,
    freq_percent,
    util_gate=_THROTTLE_UTIL_GATE,
    temp_gate=_THROTTLE_TEMP_C,
):
    """True when a silicon domain is busy + slow + hot (see gates above).

    slow = current freq below `freq_percent`% of the DVFS ceiling. Returns
    False when the ceiling is unknown (max_freq <= 0) — the ratio is
    uncomputable, so we cannot claim throttling.
    """
    if max_freq <= 0:
        return False
    busy = util >= util_gate
    slow = freq < (freq_percent / 100.0) * max_freq
    hot = thermal_state not in ("Nominal", "Unknown") or temp >= temp_gate
    return busy and slow and hot


def bandwidth_percent(snapshot, max_total_bw):
    """Memory bandwidth as a percent of summed CPU+GPU channel capacity.

    Returns 0 when bandwidth is unavailable. Shared by the chart and the
    saturation alert so both normalise against the same reference.
    """
    if not snapshot.bandwidth_available:
        return 0
    return clamp_percent(snapshot.bandwidth_gbps / max(max_total_bw, 1.0) * 100)


def package_power_percent(snapshot, package_ref_w):
    """Package power as a percent of the SoC reference rail.

    Shared by the chart and the PKG alert so both normalise against the same
    reference.
    """
    return clamp_percent(snapshot.package_watts / max(package_ref_w, 1.0) * 100)


@dataclass(frozen=True)
class AlertFrame:
    """One frame's alert/throttle/energy verdicts (L2 data point).

    A small frozen bundle the presentation layer formats into tokens; every
    field is a derived judgment, not raw hardware data. `swap_rise_gb` is the
    swap growth over the sustain window (surfaced even when below the alert
    threshold); `session_energy_j` is the cumulative package energy integrated
    since the engine was constructed.
    """

    thermal_alert: bool
    cpu_throttle: bool
    gpu_throttle: bool
    bw_alert: bool
    pkg_alert: bool
    swap_alert: bool
    swap_rise_gb: float
    session_energy_j: float


class AlertEngine:
    """Stateful per-frame alert / throttle / session-energy analytics (L2).

    Constructed from threshold *values* (not a `DashboardConfig`) so this module
    stays presentation-agnostic. `feed(snapshot)` advances the per-alert sustain
    counters, the swap-rise window, and the cumulative energy integral, then
    returns an `AlertFrame`. An alert fires only once its condition has held for
    `sustain_samples` consecutive frames.

    Session energy is integrated over the real inter-frame dt derived from
    `snapshot.timestamp` (more honest than a fixed-interval approximation). The
    first `feed()` has no prior timestamp, so it contributes 0 J.
    """

    def __init__(
        self,
        *,
        bw_sat_percent,
        pkg_power_percent,
        throttle_freq_percent,
        swap_rise_gb,
        sustain_samples,
        max_total_bw,
        package_ref_w,
    ):
        self._bw_sat_percent = bw_sat_percent
        self._pkg_power_percent = pkg_power_percent
        self._throttle_freq_percent = throttle_freq_percent
        self._swap_rise_gb = swap_rise_gb
        self._sustain_samples = max(1, int(sustain_samples))
        self._max_total_bw = max_total_bw
        self._package_ref_w = package_ref_w

        self._high_bw_counter = 0
        self._high_pkg_counter = 0
        self._throttle_cpu_counter = 0
        self._throttle_gpu_counter = 0
        # One more slot than the sustain window so a rise across the full window
        # is measurable (oldest vs newest); mirrors the retired widget deque.
        self._swap_hist: deque = deque([], maxlen=max(2, self._sustain_samples + 1))
        self._session_joules = 0.0
        self._last_timestamp = None

    def feed(self, snapshot) -> AlertFrame:
        """Advance the engine one frame and return its alert verdicts."""
        s = snapshot

        # Bandwidth saturation vs. summed CPU+GPU capacity.
        bw_pct = bandwidth_percent(s, self._max_total_bw)
        if s.bandwidth_available and bw_pct >= self._bw_sat_percent:
            self._high_bw_counter += 1
        else:
            self._high_bw_counter = 0
        bw_alert = self._high_bw_counter >= self._sustain_samples

        # Package power vs. SoC reference rail.
        pkg_pct = package_power_percent(s, self._package_ref_w)
        if pkg_pct >= self._pkg_power_percent:
            self._high_pkg_counter += 1
        else:
            self._high_pkg_counter = 0
        pkg_alert = self._high_pkg_counter >= self._sustain_samples

        # Thermal throttle, per silicon domain (P-cluster CPU, GPU): busy +
        # held below the DVFS ceiling + hot. Sustained like the other alerts.
        if domain_throttling(
            s.pcpu_util_pct,
            s.pcpu_freq_mhz,
            s.pcpu_max_freq_mhz,
            s.cpu_temp_c,
            s.thermal_state,
            freq_percent=self._throttle_freq_percent,
        ):
            self._throttle_cpu_counter += 1
        else:
            self._throttle_cpu_counter = 0
        cpu_throttle = self._throttle_cpu_counter >= self._sustain_samples

        if domain_throttling(
            s.gpu_util_pct,
            s.gpu_freq_mhz,
            s.gpu_max_freq_mhz,
            s.gpu_temp_c,
            s.thermal_state,
            freq_percent=self._throttle_freq_percent,
        ):
            self._throttle_gpu_counter += 1
        else:
            self._throttle_gpu_counter = 0
        gpu_throttle = self._throttle_gpu_counter >= self._sustain_samples

        # Swap rise over the sustain window (oldest vs. newest retained sample).
        self._swap_hist.append(max(0.0, float(s.swap_used_gb or 0.0)))
        swap_rise = (
            max(0.0, self._swap_hist[-1] - self._swap_hist[0])
            if len(self._swap_hist) > 1
            else 0.0
        )
        swap_total = float(s.swap_total_gb or 0.0)
        swap_alert = (
            swap_total >= 0.1
            and len(self._swap_hist) >= self._sustain_samples + 1
            and swap_rise >= self._swap_rise_gb
        )

        # Cumulative session energy: integrate package watts over the real
        # inter-frame dt. The first feed() has no prior timestamp → 0 J.
        if self._last_timestamp is not None:
            dt = max(0.0, s.timestamp - self._last_timestamp)
            self._session_joules += max(0.0, s.package_watts) * dt
        self._last_timestamp = s.timestamp

        thermal_alert = s.thermal_state not in ("Nominal", "Unknown")

        return AlertFrame(
            thermal_alert=thermal_alert,
            cpu_throttle=cpu_throttle,
            gpu_throttle=gpu_throttle,
            bw_alert=bw_alert,
            pkg_alert=pkg_alert,
            swap_alert=swap_alert,
            swap_rise_gb=swap_rise,
            session_energy_j=self._session_joules,
        )
