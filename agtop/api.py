"""Public Python API for agtop hardware profiling."""

import dataclasses
import threading
import time

from .models import CoreSample, SystemSnapshot
from .sampler import SampleResult, create_sampler
from .utils import get_ram_metrics_dict


def _sample_to_snapshot(
    sample: SampleResult, ram: dict, interval_s: float
) -> SystemSnapshot:
    """Map raw SampleResult + RAM dict to a clean SystemSnapshot."""
    cm = sample.cpu_metrics
    gm = sample.gpu_metrics
    bw = sample.bandwidth_metrics
    bw_avail = bool(isinstance(bw, dict) and bw.get("_available", False))
    # total_gbps is a residency-weighted average already in GB/s — not a
    # byte counter, so it is not divided by the sample interval.
    total_bw = float(bw.get("total_gbps", 0.0)) if bw_avail else 0.0
    e_cores = [
        CoreSample(
            index=sys_idx,
            active_pct=int(cm.get("E-Cluster" + str(sys_idx) + "_active", 0)),
            freq_mhz=int(cm.get("E-Cluster" + str(sys_idx) + "_freq_Mhz", 0)),
        )
        for sys_idx in cm.get("e_core", [])
    ]
    p_cores = [
        CoreSample(
            index=sys_idx,
            active_pct=int(cm.get("P-Cluster" + str(sys_idx) + "_active", 0)),
            freq_mhz=int(cm.get("P-Cluster" + str(sys_idx) + "_freq_Mhz", 0)),
        )
        for sys_idx in cm.get("p_core", [])
    ]
    return SystemSnapshot(
        timestamp=sample.timestamp,
        cpu_watts=cm["cpu_W"] / interval_s,
        gpu_watts=cm["gpu_W"] / interval_s,
        ane_watts=cm["ane_W"] / interval_s,
        package_watts=cm["package_W"] / interval_s,
        ecpu_util_pct=float(cm["E-Cluster_active"]),
        pcpu_util_pct=float(cm["P-Cluster_active"]),
        gpu_util_pct=float(gm["active"]),
        cpu_temp_c=sample.cpu_temp_c,
        gpu_temp_c=sample.gpu_temp_c,
        ecpu_freq_mhz=int(cm["E-Cluster_freq_Mhz"]),
        pcpu_freq_mhz=int(cm["P-Cluster_freq_Mhz"]),
        gpu_freq_mhz=int(gm["freq_MHz"]),
        ram_used_gb=float(ram.get("used_GB", 0.0)),
        swap_used_gb=float(ram.get("swap_used_GB", 0.0)),
        thermal_state=sample.thermal_pressure,
        bandwidth_gbps=total_bw,
        bandwidth_available=bw_avail,
        e_cores=e_cores,
        p_cores=p_cores,
    )


class Monitor:
    """Synchronous, single-sample hardware monitor."""

    def __init__(self, interval_s: float = 1.0, subsamples: int = 1):
        self._interval_s = max(1, int(interval_s))
        self._sampler, self.backend_name = create_sampler(
            self._interval_s, subsamples=subsamples
        )
        # Prime delta: first sample() always returns None
        self._sampler.sample()

    @property
    def manages_timing(self) -> bool:
        """True if the underlying sampler manages its own sleep timing."""
        return bool(getattr(self._sampler, "manages_timing", False))

    def get_snapshot(self) -> SystemSnapshot:
        """Block for interval_s (unless sampler manages timing), return SystemSnapshot."""
        if not self.manages_timing:
            time.sleep(self._interval_s)
        sample = self._sampler.sample()
        while sample is None:
            # A None sample means the delta interval was non-positive; sleep
            # briefly so the re-sample sees a meaningful elapsed time (avoids a
            # frame with an inflated interval/elapsed power scale).
            time.sleep(0.01)
            sample = self._sampler.sample()
        ram = get_ram_metrics_dict()
        return _sample_to_snapshot(sample, ram, self._interval_s)

    def close(self):
        self._sampler.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class Profiler:
    """Threaded background collector. Use as a context manager."""

    def __init__(self, interval_s: float = 1.0):
        self._interval_s = interval_s
        self._monitor = Monitor(interval_s)
        self._samples: list = []
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._alerts: list = []  # list of (metric, threshold, callback)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()

    def start(self):
        with self._lock:
            self._samples.clear()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join()
        self._monitor.close()

    def _run_loop(self):
        while not self._stop_event.is_set():
            snapshot = self._monitor.get_snapshot()  # blocks for interval_s
            with self._lock:
                self._samples.append(snapshot)
            for metric, threshold, callback in self._alerts:
                val = getattr(snapshot, metric, None)
                if val is not None and val >= threshold:
                    try:
                        callback(val)
                    except Exception:
                        pass

    def register_alert(self, metric: str, threshold: float, callback):
        """Fire callback(value) when snapshot.metric >= threshold."""
        if metric not in SystemSnapshot.__dataclass_fields__:
            raise ValueError(f"Unknown SystemSnapshot field: {metric!r}")
        self._alerts.append((metric, threshold, callback))

    def get_summary(self) -> dict:
        with self._lock:
            samples = list(self._samples)
        if not samples:
            return {}
        duration_s = (
            samples[-1].timestamp - samples[0].timestamp if len(samples) > 1 else 0.0
        )
        cpu_w = [s.cpu_watts for s in samples]
        gpu_w = [s.gpu_watts for s in samples]
        pkg_w = [s.package_watts for s in samples]
        avg_cpu = sum(cpu_w) / len(cpu_w)
        avg_gpu = sum(gpu_w) / len(gpu_w)
        avg_pkg = sum(pkg_w) / len(pkg_w)
        return {
            "sample_count": len(samples),
            "duration_s": duration_s,
            "avg_cpu_watts": avg_cpu,
            "avg_gpu_watts": avg_gpu,
            "avg_package_watts": avg_pkg,
            "peak_cpu_watts": max(cpu_w),
            "peak_gpu_watts": max(gpu_w),
            "peak_package_watts": max(pkg_w),
            "total_cpu_joules": avg_cpu * duration_s,
            "total_gpu_joules": avg_gpu * duration_s,
            "total_package_joules": avg_pkg * duration_s,
        }

    def to_pandas(self):
        try:
            import pandas as pd
        except ImportError:
            raise ImportError("pandas is required: pip install agtop[pandas]")
        with self._lock:
            samples = list(self._samples)
        df = pd.DataFrame([dataclasses.asdict(s) for s in samples])
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="s")
        df.set_index("datetime", inplace=True)
        return df


class AsyncMonitor(Monitor):
    """Async wrapper around Monitor; runs blocking get_snapshot in a thread pool."""

    async def get_snapshot_async(self) -> SystemSnapshot:
        import asyncio

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.get_snapshot)
