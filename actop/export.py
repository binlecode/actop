"""Metrics export backends: NDJSON stream and a Prometheus `/metrics` endpoint.

These turn actop from an interactive viewer into an observability source. Both
backends reuse the public `Monitor` API; the formatting functions operate on a
plain `SystemSnapshot` and import nothing platform-specific, so they are testable
off Apple-Silicon hardware. `Monitor` is imported lazily inside the run loops so
this module imports cleanly on any platform.
"""

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
    ("cpu_temp_c", "cpu_temperature_celsius"),
    ("gpu_temp_c", "gpu_temperature_celsius"),
    ("ecpu_freq_mhz", "ecpu_frequency_mhz"),
    ("pcpu_freq_mhz", "pcpu_frequency_mhz"),
    ("gpu_freq_mhz", "gpu_frequency_mhz"),
    ("ram_used_gb", "ram_used_gigabytes"),
    ("swap_used_gb", "swap_used_gigabytes"),
    ("bandwidth_gbps", "memory_bandwidth_gbps"),
)


def snapshot_to_dict(snapshot: SystemSnapshot) -> dict:
    """Full snapshot as a JSON-serializable dict (per-core lists included)."""
    return dataclasses.asdict(snapshot)


def snapshot_to_json(snapshot: SystemSnapshot) -> str:
    """Compact single-line JSON for one snapshot (NDJSON record)."""
    return json.dumps(snapshot_to_dict(snapshot), separators=(",", ":"))


def snapshot_to_prometheus(snapshot: SystemSnapshot) -> str:
    """Render a snapshot in Prometheus text exposition format (version 0.0.4)."""
    lines: list[str] = []
    for field, suffix in _PROM_GAUGES:
        name = "actop_" + suffix
        value = float(getattr(snapshot, field))
        lines.append("# TYPE {} gauge".format(name))
        lines.append("{} {}".format(name, _fmt_number(value)))

    # Per-fan tachometer as a labelled gauge; omitted entirely on fanless Macs
    # (empty fan_rpms) rather than fabricating a phantom reading.
    if snapshot.fan_rpms:
        lines.append("# TYPE actop_fan_speed_rpm gauge")
        for idx, rpm in enumerate(snapshot.fan_rpms):
            lines.append(
                'actop_fan_speed_rpm{{fan="{}"}} {}'.format(
                    idx, _fmt_number(float(rpm))
                )
            )

    # Per-core utilization/frequency as labelled gauges.
    lines.append("# TYPE actop_core_utilization_percent gauge")
    lines.append("# TYPE actop_core_frequency_mhz gauge")
    for cluster, cores in (("E", snapshot.e_cores), ("P", snapshot.p_cores)):
        for core in cores:
            labels = 'cluster="{}",core="{}"'.format(cluster, core.index)
            lines.append(
                "actop_core_utilization_percent{{{}}} {}".format(
                    labels, _fmt_number(float(core.active_pct))
                )
            )
            lines.append(
                "actop_core_frequency_mhz{{{}}} {}".format(
                    labels, _fmt_number(float(core.freq_mhz))
                )
            )
    return "\n".join(lines) + "\n"


def _fmt_number(value: float) -> str:
    """Render a float without trailing noise; integers stay integer-looking."""
    if value == int(value):
        return str(int(value))
    return repr(round(value, 4))


def run_json_stream(
    interval_s: int, subsamples: int, out=None, max_samples: int = 0
) -> int:
    """Stream NDJSON snapshots to `out` (default stdout) until interrupted.

    `max_samples` > 0 stops after that many records (used by tests); 0 streams
    indefinitely. Returns the number of records emitted.
    """
    from actop.api import Monitor

    stream = out if out is not None else sys.stdout
    monitor = Monitor(interval_s, subsamples)
    emitted = 0
    try:
        while True:
            snapshot = monitor.get_snapshot()
            stream.write(snapshot_to_json(snapshot) + "\n")
            stream.flush()
            emitted += 1
            if max_samples and emitted >= max_samples:
                break
    finally:
        monitor.close()
    return emitted


def _make_prometheus_handler(read_latest):
    """Build a BaseHTTPRequestHandler serving the latest snapshot at /metrics."""

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (stdlib-mandated name)
            if self.path.rstrip("/") not in ("", "/metrics"):
                self.send_error(404, "not found")
                return
            snapshot = read_latest()
            if snapshot is None:
                self.send_error(503, "no sample yet")
                return
            body = snapshot_to_prometheus(snapshot).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # silence per-request stderr logging
            pass

    return _Handler


def serve_prometheus(
    port: int, interval_s: int, subsamples: int, host: str = "0.0.0.0"
) -> None:
    """Serve Prometheus metrics on http://host:port/metrics until interrupted.

    A background thread keeps the latest snapshot warm so scrapes return
    immediately instead of blocking for a full sample interval.
    """
    from actop.api import Monitor

    monitor = Monitor(interval_s, subsamples)
    state = {"snapshot": None}
    lock = threading.Lock()
    stop = threading.Event()

    def _sample_loop():
        while not stop.is_set():
            snap = monitor.get_snapshot()
            with lock:
                state["snapshot"] = snap

    def _read_latest():
        with lock:
            return state["snapshot"]

    sampler_thread = threading.Thread(target=_sample_loop, daemon=True)
    sampler_thread.start()

    handler = _make_prometheus_handler(_read_latest)
    server = ThreadingHTTPServer((host, port), handler)
    print(
        "actop: serving Prometheus metrics on http://{}:{}/metrics".format(host, port),
        file=sys.stderr,
        flush=True,
    )
    try:
        server.serve_forever()
    finally:
        stop.set()
        server.server_close()
        monitor.close()
