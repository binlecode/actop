"""Metrics export backends: NDJSON stream and a Prometheus `/metrics` endpoint.

These turn actop from an interactive viewer into an observability source. Both
backends reuse the public `Monitor` API; the formatting functions operate on a
plain `SystemSnapshot` and import nothing platform-specific, so they are testable
off Apple-Silicon hardware. `Monitor` is imported lazily inside the run loops so
this module imports cleanly on any platform.
"""

from __future__ import annotations

import dataclasses
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from actop.models import SystemSnapshot

# Scalar SystemSnapshot fields exported as Prometheus gauges: (field, suffix).
# Per-core lists are exported separately as labelled gauges.
_PROM_GAUGES = (
    ("cpu_watts", "cpu_power_watts"),
    ("gpu_watts", "gpu_power_watts"),
    ("ane_watts", "ane_power_watts"),
    ("package_watts", "package_power_watts"),
    ("ecpu_util_pct", "ecpu_utilization_percent"),
    ("pcpu_util_pct", "pcpu_utilization_percent"),
    ("gpu_util_pct", "gpu_utilization_percent"),
    # Driver-reported GPU utilization (IOAccelerator PerformanceStatistics),
    # exported alongside the residency-derived gpu_utilization_percent above so
    # a dashboard can compare the two. The companion `gpu_util_source` field is
    # a string, so it belongs in NDJSON only — gauges must be numeric.
    ("gpu_device_pct", "gpu_device_utilization_percent"),
    ("gpu_renderer_pct", "gpu_renderer_utilization_percent"),
    ("gpu_tiler_pct", "gpu_tiler_utilization_percent"),
    ("cpu_temp_c", "cpu_temperature_celsius"),
    ("gpu_temp_c", "gpu_temperature_celsius"),
    ("ecpu_freq_mhz", "ecpu_frequency_mhz"),
    ("pcpu_freq_mhz", "pcpu_frequency_mhz"),
    ("gpu_freq_mhz", "gpu_frequency_mhz"),
    # Byte counts are exported in the base unit, per Prometheus/OpenMetrics
    # naming convention ("use base units" — bytes, not megabytes; let the
    # dashboard format). This is also exact, unlike the rounded *_gb fields.
    ("ram_used_bytes", "ram_used_bytes"),
    ("ram_total_bytes", "ram_total_bytes"),
    ("swap_used_bytes", "swap_used_bytes"),
    ("swap_total_bytes", "swap_total_bytes"),
    # Deprecated: these carry rounded GiB values under a decimal-GB name. Kept
    # for one release so existing dashboards keep scraping; removed in 2.0.0.
    ("ram_used_gb", "ram_used_gigabytes"),
    ("swap_used_gb", "swap_used_gigabytes"),
    # Bandwidth stays decimal GB/s: that is Apple's own unit for the bus (546
    # GB/s on M4 Max) and the DCS bucket labels are literally "32GB/s".
    ("bandwidth_gbps", "memory_bandwidth_gbps"),
)


def snapshot_to_dict(snapshot: SystemSnapshot, *, alert_frame=None) -> dict:
    """Full snapshot as a JSON-serializable dict (per-core lists included).

    When `alert_frame` (an `analytics.AlertFrame`) is provided, its fields are
    merged under top-level keys (`alert_thermal`, `session_energy_j`, etc.) so
    every NDJSON consumer receives alert/throttle/energy judgments without a
    separate channel. The frame keys never collide with SystemSnapshot fields.
    """
    record = dataclasses.asdict(snapshot)
    if alert_frame is not None:
        record.update(
            {
                "alert_thermal": alert_frame.thermal_alert,
                "alert_cpu_throttle": alert_frame.cpu_throttle,
                "alert_gpu_throttle": alert_frame.gpu_throttle,
                "alert_mem_bound": alert_frame.bw_alert,
                "alert_package_power": alert_frame.pkg_alert,
                "alert_swap_rise": alert_frame.swap_alert,
                "alert_swap_rise_gib": alert_frame.swap_rise_gib,
                "session_energy_j": alert_frame.session_energy_j,
                "effective_max_bw_gbps": alert_frame.effective_max_bw,
                "effective_max_package_w": alert_frame.effective_max_package_w,
            }
        )
    return record


def snapshot_to_json(snapshot: SystemSnapshot, *, alert_frame=None) -> str:
    """Compact single-line JSON for one snapshot (NDJSON record)."""
    return json.dumps(
        snapshot_to_dict(snapshot, alert_frame=alert_frame), separators=(",", ":")
    )


def snapshot_to_prometheus(snapshot: SystemSnapshot, *, alert_frame=None) -> str:
    """Render a snapshot in Prometheus text exposition format (version 0.0.4).

    When `alert_frame` (an `analytics.AlertFrame`) is provided, alert verdicts
    and session energy are appended as labelled gauges after the scalar metrics.
    """
    lines: list[str] = []
    for field, suffix in _PROM_GAUGES:
        name = "actop_" + suffix
        value = float(getattr(snapshot, field))
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name} {_fmt_number(value)}")

    # Per-fan tachometer as a labelled gauge; omitted entirely on fanless Macs
    # (empty fan_rpms) rather than fabricating a phantom reading.
    if snapshot.fan_rpms:
        lines.append("# TYPE actop_fan_speed_rpm gauge")
        for idx, rpm in enumerate(snapshot.fan_rpms):
            lines.append(
                f'actop_fan_speed_rpm{{fan="{idx}"}} {_fmt_number(float(rpm))}'
            )

    # Per-core utilization/frequency as labelled gauges.
    lines.append("# TYPE actop_core_utilization_percent gauge")
    lines.append("# TYPE actop_core_frequency_mhz gauge")
    for cluster, cores in (("E", snapshot.e_cores), ("P", snapshot.p_cores)):
        for core in cores:
            labels = f'cluster="{cluster}",core="{core.index}"'
            lines.append(
                f"actop_core_utilization_percent{{{labels}}} {_fmt_number(float(core.active_pct))}"
            )
            lines.append(
                f"actop_core_frequency_mhz{{{labels}}} {_fmt_number(float(core.freq_mhz))}"
            )

    # Per-process gauges (emitted only when the caller opts in via
    # Monitor(include_processes=True)). Labelled by pid and command so a
    # Prometheus dashboard can join on pid across metrics.
    if snapshot.processes:
        lines.append("# TYPE actop_process_cpu_percent gauge")
        lines.append("# TYPE actop_process_cpu_time_share gauge")
        lines.append("# TYPE actop_process_gpu_time_share gauge")
        lines.append("# TYPE actop_process_attributed_watts gauge")
        lines.append("# TYPE actop_process_rss_bytes gauge")
        lines.append("# TYPE actop_process_num_threads gauge")
        for proc in snapshot.processes:
            cmd = proc.command.replace('"', '\\"')
            labels = f'pid="{proc.pid}",command="{cmd}"'
            lines.append(
                f"actop_process_cpu_percent{{{labels}}} {_fmt_number(float(proc.cpu_percent))}"
            )
            cts = proc.cpu_time_share
            lines.append(
                f"actop_process_cpu_time_share{{{labels}}} {_fmt_number(cts) if cts is not None else 'NaN'}"
            )
            gts = proc.gpu_time_share
            lines.append(
                f"actop_process_gpu_time_share{{{labels}}} {_fmt_number(gts) if gts is not None else 'NaN'}"
            )
            aw = proc.attributed_w
            lines.append(
                f"actop_process_attributed_watts{{{labels}}} {_fmt_number(aw) if aw is not None else 'NaN'}"
            )
            lines.append(
                f"actop_process_rss_bytes{{{labels}}} {_fmt_number(float(proc.rss_bytes))}"
            )
            lines.append(f"actop_process_num_threads{{{labels}}} {proc.num_threads}")

    # Alert verdicts and session energy — emitted only when an AlertFrame is
    # provided (the caller drives AlertEngine between snapshots). The boolean
    # alerts are 0/1 gauges; session_energy_j and effective_max_* are scalar
    # gauges naming the unit in the metric suffix.
    if alert_frame is not None:
        lines.append("# TYPE actop_alert_thermal gauge")
        lines.append(f"actop_alert_thermal {int(alert_frame.thermal_alert)}")
        lines.append("# TYPE actop_alert_cpu_throttle gauge")
        lines.append(f"actop_alert_cpu_throttle {int(alert_frame.cpu_throttle)}")
        lines.append("# TYPE actop_alert_gpu_throttle gauge")
        lines.append(f"actop_alert_gpu_throttle {int(alert_frame.gpu_throttle)}")
        lines.append("# TYPE actop_alert_mem_bound gauge")
        lines.append(f"actop_alert_mem_bound {int(alert_frame.bw_alert)}")
        lines.append("# TYPE actop_alert_package_power gauge")
        lines.append(f"actop_alert_package_power {int(alert_frame.pkg_alert)}")
        lines.append("# TYPE actop_alert_swap_rise gauge")
        lines.append(f"actop_alert_swap_rise {int(alert_frame.swap_alert)}")
        lines.append("# TYPE actop_alert_swap_rise_gib gauge")
        lines.append(
            f"actop_alert_swap_rise_gib {_fmt_number(alert_frame.swap_rise_gib)}"
        )
        lines.append("# TYPE actop_session_energy_joules gauge")
        lines.append(
            f"actop_session_energy_joules {_fmt_number(alert_frame.session_energy_j)}"
        )
        lines.append("# TYPE actop_effective_max_bw_gbps gauge")
        lines.append(
            f"actop_effective_max_bw_gbps {_fmt_number(alert_frame.effective_max_bw)}"
        )
        lines.append("# TYPE actop_effective_max_package_w gauge")
        lines.append(
            f"actop_effective_max_package_w {_fmt_number(alert_frame.effective_max_package_w)}"
        )

    return "\n".join(lines) + "\n"


def _fmt_number(value: float) -> str:
    """Render a float without trailing noise; integers stay integer-looking."""
    if value == int(value):
        return str(int(value))
    return repr(round(value, 4))


def run_json_stream(
    interval_s: int,
    subsamples: int,
    out=None,
    max_samples: int = 0,
    *,
    include_processes: bool = False,
    proc_filter: str = "",
    alert_engine_kwargs: dict | None = None,
) -> int:
    """Stream NDJSON snapshots to `out` (default stdout) until interrupted.

    `max_samples` > 0 stops after that many records (used by tests); 0 streams
    indefinitely. Returns the number of records emitted.

    When `alert_engine_kwargs` is provided it is unpacked into an
    `analytics.AlertEngine`; each snapshot is fed through the engine and the
    resulting `AlertFrame` is merged into the NDJSON record under top-level
    `alert_*` / `session_energy_j` keys.
    """
    from actop.api import Monitor

    stream = out if out is not None else sys.stdout
    monitor = Monitor(
        interval_s,
        subsamples,
        include_processes=include_processes,
        process_filter=proc_filter or None,
    )
    alert_engine = None
    if alert_engine_kwargs is not None:
        from actop.analytics import AlertEngine

        alert_engine = AlertEngine(**alert_engine_kwargs)

    emitted = 0
    try:
        while True:
            snapshot = monitor.get_snapshot()
            frame = alert_engine.feed(snapshot) if alert_engine is not None else None
            stream.write(snapshot_to_json(snapshot, alert_frame=frame) + "\n")
            stream.flush()
            emitted += 1
            if max_samples and emitted >= max_samples:
                break
    finally:
        monitor.close()
    return emitted


def _make_prometheus_handler(read_latest, read_frame=None):
    """Build a BaseHTTPRequestHandler serving the latest snapshot at /metrics.

    When `read_frame` is provided it returns an `AlertFrame` (or None) paired
    with the latest snapshot; the handler passes it into `snapshot_to_prometheus`
    so alert verdicts and session energy appear in the scrape output.
    """

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.rstrip("/") not in ("", "/metrics"):
                self.send_error(404, "not found")
                return
            snapshot = read_latest()
            if snapshot is None:
                self.send_error(503, "no sample yet")
                return
            frame = read_frame() if read_frame is not None else None
            body = snapshot_to_prometheus(snapshot, alert_frame=frame).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # silence per-request stderr logging
            pass

    return _Handler


def serve_prometheus(
    port: int,
    interval_s: int,
    subsamples: int,
    host: str = "0.0.0.0",
    *,
    include_processes: bool = False,
    proc_filter: str = "",
    alert_engine_kwargs: dict | None = None,
) -> None:
    """Serve Prometheus metrics on http://host:port/metrics until interrupted.

    A background thread keeps the latest snapshot warm so scrapes return
    immediately instead of blocking for a full sample interval.

    When `alert_engine_kwargs` is provided it is unpacked into an
    `analytics.AlertEngine`; the sample loop feeds each snapshot through it and
    the resulting `AlertFrame` is paired with the snapshot so every `/metrics`
    scrape includes alert verdicts and session energy.
    """
    from actop.api import Monitor

    monitor = Monitor(
        interval_s,
        subsamples,
        include_processes=include_processes,
        process_filter=proc_filter or None,
    )
    alert_engine = None
    if alert_engine_kwargs is not None:
        from actop.analytics import AlertEngine

        alert_engine = AlertEngine(**alert_engine_kwargs)

    state: dict = {"snapshot": None, "frame": None}
    lock = threading.Lock()
    stop = threading.Event()

    def _sample_loop():
        while not stop.is_set():
            snap = monitor.get_snapshot()
            frame = alert_engine.feed(snap) if alert_engine is not None else None
            with lock:
                state["snapshot"] = snap
                state["frame"] = frame

    def _read_latest():
        with lock:
            return state["snapshot"]

    def _read_frame():
        with lock:
            return state["frame"]

    sampler_thread = threading.Thread(target=_sample_loop, daemon=True)
    sampler_thread.start()

    handler = _make_prometheus_handler(_read_latest, read_frame=_read_frame)
    server = ThreadingHTTPServer((host, port), handler)
    print(
        f"actop: serving Prometheus metrics on http://{host}:{port}/metrics",
        file=sys.stderr,
        flush=True,
    )
    try:
        server.serve_forever()
    finally:
        stop.set()
        server.server_close()
        monitor.close()
