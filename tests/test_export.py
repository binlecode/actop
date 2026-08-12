"""Export-backend tests: NDJSON and Prometheus formats + run loops.

Functional-only: every test verifies that given specific inputs the system
produces correct outputs through its actual logic path. No format-property
tests (single-line, well-formed, TYPE header) — those check the output shape,
not whether the output is correct.

The hardware-backed run loops are exercised end-to-end and marked local.
"""

from __future__ import annotations

import dataclasses
import io
import json
import subprocess
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

from actop.export import (
    run_json_stream,
    snapshot_to_json,
    snapshot_to_prometheus,
)
from actop.models import CoreSample, ProcessSample, SystemSnapshot

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _sample_snapshot(
    fan_rpms: list | None = None,
    fan_available: bool = False,
    gpu_util_source: str = "residency",
) -> SystemSnapshot:
    fan_rpms = [] if fan_rpms is None else fan_rpms
    return SystemSnapshot(
        timestamp=1700000000.0,
        cpu_watts=12.5,
        gpu_watts=3.0,
        ane_watts=0.5,
        package_watts=16.0,
        ecpu_util_pct=20.0,
        pcpu_util_pct=55.5,
        gpu_util_pct=40.0,
        gpu_device_pct=61.0,
        gpu_renderer_pct=58.0,
        gpu_tiler_pct=7.0,
        gpu_perf_stats_available=True,
        gpu_util_source=gpu_util_source,
        cpu_temp_c=48.0,
        gpu_temp_c=45.0,
        ecpu_freq_mhz=1200,
        pcpu_freq_mhz=3200,
        gpu_freq_mhz=1296,
        ram_used_bytes=19_542_236_365,
        ram_total_bytes=137_438_953_472,
        swap_used_bytes=1_610_612_736,
        swap_total_bytes=2_147_483_648,
        ram_used_gb=17.4,
        swap_used_gb=1.4,
        thermal_state="Nominal",
        bandwidth_gbps=42.0,
        bandwidth_available=True,
        fan_rpms=fan_rpms,
        fan_available=fan_available,
        e_cores=[CoreSample(index=0, active_pct=10, freq_mhz=1100)],
        p_cores=[CoreSample(index=4, active_pct=80, freq_mhz=3200)],
    )


def _sample_snapshot_with_processes() -> SystemSnapshot:
    snap = _sample_snapshot()
    return SystemSnapshot(
        timestamp=snap.timestamp,
        cpu_watts=snap.cpu_watts,
        gpu_watts=snap.gpu_watts,
        ane_watts=snap.ane_watts,
        package_watts=snap.package_watts,
        ecpu_util_pct=snap.ecpu_util_pct,
        pcpu_util_pct=snap.pcpu_util_pct,
        gpu_util_pct=snap.gpu_util_pct,
        gpu_device_pct=snap.gpu_device_pct,
        gpu_renderer_pct=snap.gpu_renderer_pct,
        gpu_tiler_pct=snap.gpu_tiler_pct,
        gpu_perf_stats_available=snap.gpu_perf_stats_available,
        gpu_util_source=snap.gpu_util_source,
        cpu_temp_c=snap.cpu_temp_c,
        gpu_temp_c=snap.gpu_temp_c,
        ecpu_freq_mhz=snap.ecpu_freq_mhz,
        pcpu_freq_mhz=snap.pcpu_freq_mhz,
        gpu_freq_mhz=snap.gpu_freq_mhz,
        ram_used_bytes=snap.ram_used_bytes,
        ram_total_bytes=snap.ram_total_bytes,
        swap_used_bytes=snap.swap_used_bytes,
        swap_total_bytes=snap.swap_total_bytes,
        ram_used_gb=snap.ram_used_gb,
        swap_used_gb=snap.swap_used_gb,
        thermal_state=snap.thermal_state,
        bandwidth_gbps=snap.bandwidth_gbps,
        bandwidth_available=snap.bandwidth_available,
        fan_rpms=snap.fan_rpms,
        fan_available=snap.fan_available,
        e_cores=snap.e_cores,
        p_cores=snap.p_cores,
        processes=[
            ProcessSample(
                pid=1234,
                command="python",
                cpu_percent=45.5,
                cpu_time_share=0.35,
                gpu_time_share=0.12,
                rss_mb=2048.0,
                rss_bytes=2_147_483_648,
                num_threads=8,
                attributed_w=5.6,
            ),
            ProcessSample(
                pid=5678,
                command="ollama",
                cpu_percent=12.0,
                cpu_time_share=0.09,
                gpu_time_share=0.55,
                rss_mb=4096.0,
                rss_bytes=4_294_967_296,
                num_threads=4,
                attributed_w=1.8,
            ),
        ],
    )


# ---------------------------------------------------------------------------
# GPU driver stats: both backends, gpu_util_source excluded from Prometheus
# ---------------------------------------------------------------------------


def test_gpu_driver_stats_reach_both_backends():
    """IOAccelerator Renderer/Tiler/Device utilization must reach both export
    backends. gpu_util_source (a string) must NOT appear in Prometheus — its
    value does not parse as a number and would break the scrape."""
    snap = _sample_snapshot(gpu_util_source="ioaccelerator")

    # NDJSON carries the string field.
    rec = json.loads(snapshot_to_json(snap))
    assert rec["gpu_util_source"] == "ioaccelerator"
    assert rec["gpu_renderer_pct"] == 58.0
    assert rec["gpu_tiler_pct"] == 7.0

    # Prometheus carries only numeric gauges.
    body = snapshot_to_prometheus(snap)
    assert "actop_gpu_renderer_utilization_percent 58" in body
    assert "actop_gpu_tiler_utilization_percent 7" in body
    assert "actop_gpu_device_utilization_percent 61" in body
    assert "actop_gpu_utilization_percent 40" in body
    assert "gpu_util_source" not in body


# ---------------------------------------------------------------------------
# Fan gauge: present/absent based on fan_available
# ---------------------------------------------------------------------------


def test_fan_gauge_only_when_available():
    """Fan gauges must be labelled per-fan when available, and entirely absent
    on fanless Macs — no phantom 0 RPM gauge."""
    body = snapshot_to_prometheus(
        _sample_snapshot(fan_rpms=[1200.0, 980.0], fan_available=True)
    )
    assert 'actop_fan_speed_rpm{fan="0"} 1200' in body
    assert 'actop_fan_speed_rpm{fan="1"} 980' in body

    body = snapshot_to_prometheus(_sample_snapshot(fan_rpms=(), fan_available=False))
    assert "actop_fan_speed_rpm" not in body


# ---------------------------------------------------------------------------
# Process data in both backends
# ---------------------------------------------------------------------------


def test_process_data_flows_to_both_backends_when_present():
    """When SystemSnapshot carries processes, both NDJSON and Prometheus must
    emit every field with the correct values — no field-wiring error."""
    snap = _sample_snapshot_with_processes()

    rec = json.loads(snapshot_to_json(snap))
    assert len(rec["processes"]) == 2
    p0 = rec["processes"][0]
    assert p0["pid"] == 1234
    assert p0["command"] == "python"
    assert p0["cpu_time_share"] == 0.35
    assert p0["gpu_time_share"] == 0.12
    assert p0["attributed_w"] == 5.6
    assert p0["rss_bytes"] == 2_147_483_648

    body = snapshot_to_prometheus(snap)
    assert 'actop_process_cpu_percent{pid="1234",command="python"} 45.5' in body
    assert 'actop_process_attributed_watts{pid="1234",command="python"} 5.6' in body
    assert 'actop_process_rss_bytes{pid="1234",command="python"} 2147483648' in body
    assert 'actop_process_cpu_percent{pid="5678",command="ollama"} 12' in body


def test_prometheus_omits_process_gauges_when_no_processes():
    """An empty process list must not emit any process gauge."""
    body = snapshot_to_prometheus(_sample_snapshot())
    assert "actop_process_cpu_percent" not in body
    assert "actop_process_gpu_time_share" not in body


# ---------------------------------------------------------------------------
# AlertEngine integration (E track)
# ---------------------------------------------------------------------------


def test_run_json_stream_integrates_alert_engine():
    """Full pipeline: Monitor → AlertEngine → NDJSON with correct alert fields.

    Alert config: bw_sat_percent=85, max_total_bw=400, sustain_samples=3.
    Threshold = 85% × 400 = 340 GB/s. Snapshots 2-4 are above → alert fires
    on record 4 after the sustain window. Session energy accumulates across
    the real dt between timestamps (package_watts=16W × dt).
    """
    base = _sample_snapshot()
    snaps = [
        dataclasses.replace(base, timestamp=1000.0, bandwidth_gbps=42.0),
        dataclasses.replace(base, timestamp=1002.0, bandwidth_gbps=350.0),
        dataclasses.replace(base, timestamp=1004.0, bandwidth_gbps=350.0),
        dataclasses.replace(base, timestamp=1006.0, bandwidth_gbps=350.0),
    ]

    mock_monitor = MagicMock()
    mock_monitor.get_snapshot.side_effect = snaps
    mock_monitor.close = MagicMock()

    buffer = io.StringIO()

    with patch("actop.api.Monitor", return_value=mock_monitor):
        count = run_json_stream(
            interval_s=1,
            subsamples=1,
            out=buffer,
            max_samples=4,
            alert_engine_kwargs={
                "bw_sat_percent": 85,
                "pkg_power_percent": 85,
                "throttle_freq_percent": 90,
                "swap_rise_gib": 0.3,
                "sustain_samples": 3,
                "max_total_bw": 400.0,
                "package_ref_w": 50.0,
            },
        )

    assert count == 4
    records = [json.loads(ln) for ln in buffer.getvalue().strip().splitlines()]

    # Record 1: first feed, no prior timestamp → 0 J, no alert.
    r0 = records[0]
    assert r0["alert_mem_bound"] is False
    assert r0["session_energy_j"] == 0.0

    # Record 2: count=1, not sustained. Energy = 16W × 2s = 32J.
    r1 = records[1]
    assert r1["alert_mem_bound"] is False
    assert r1["session_energy_j"] == pytest.approx(32.0, abs=0.5)

    # Record 3: count=2. Energy = 64J.
    r2 = records[2]
    assert r2["alert_mem_bound"] is False
    assert r2["session_energy_j"] == pytest.approx(64.0, abs=0.5)

    # Record 4: count=3 → sustained alert fires. Energy = 96J.
    r3 = records[3]
    assert r3["alert_mem_bound"] is True
    assert r3["session_energy_j"] == pytest.approx(96.0, abs=0.5)

    # Every record carries the full set of 10 alert keys.
    for rec in records:
        for key in (
            "alert_thermal",
            "alert_cpu_throttle",
            "alert_gpu_throttle",
            "alert_mem_bound",
            "alert_package_power",
            "alert_swap_rise",
            "alert_swap_rise_gib",
            "session_energy_j",
            "effective_max_bw_gbps",
            "effective_max_package_w",
        ):
            assert key in rec, f"missing alert key {key!r} in record"


def test_run_json_stream_no_alert_keys_without_alert_engine():
    """Without alert_engine_kwargs the output carries no alert keys."""
    mock_monitor = MagicMock()
    mock_monitor.get_snapshot.return_value = _sample_snapshot()
    mock_monitor.close = MagicMock()

    buffer = io.StringIO()

    with patch("actop.api.Monitor", return_value=mock_monitor):
        run_json_stream(interval_s=1, subsamples=1, out=buffer, max_samples=1)

    rec = json.loads(buffer.getvalue().strip())
    assert "alert_thermal" not in rec
    assert "session_energy_j" not in rec


# ---------------------------------------------------------------------------
# Local: end-to-end with real hardware
# ---------------------------------------------------------------------------


@pytest.mark.local
def test_run_json_stream_emits_parseable_records():
    buffer = io.StringIO()
    count = run_json_stream(interval_s=1, subsamples=1, out=buffer, max_samples=2)

    assert count == 2
    lines = [ln for ln in buffer.getvalue().splitlines() if ln.strip()]
    assert len(lines) == 2

    record = json.loads(lines[0])
    assert "cpu_watts" in record
    assert record["cpu_watts"] >= 0


@pytest.mark.local
def test_serve_prometheus_endpoint_responds():
    import urllib.request

    port = 19991
    process = subprocess.Popen(
        [sys.executable, "-m", "actop.actop", "--serve", str(port), "--interval", "1"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        body = None
        for _ in range(20):
            time.sleep(0.5)
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/metrics", timeout=1
                ) as response:
                    if response.status == 200:
                        body = response.read().decode()
                        break
            except Exception:
                continue

        assert body is not None, "metrics endpoint never returned 200"
        assert "actop_cpu_power_watts" in body
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


@pytest.mark.local
def test_json_stream_includes_processes_when_show_processes_flag_used():
    import signal

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "actop.actop",
            "--json",
            "--interval",
            "1",
            "--show-processes",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, _ = process.communicate(timeout=8)
    except subprocess.TimeoutExpired:
        process.send_signal(signal.SIGINT)
        stdout, _ = process.communicate(timeout=2)
    finally:
        if process.poll() is None:
            process.kill()

    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    assert len(lines) >= 1
    for line in lines:
        record = json.loads(line)
        assert "processes" in record
        assert isinstance(record["processes"], list)


@pytest.mark.local
def test_json_stream_proc_filter_implies_show_processes():
    import signal

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "actop.actop",
            "--json",
            "--interval",
            "1",
            "--proc-filter",
            "kernel|launchd",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, _ = process.communicate(timeout=8)
    except subprocess.TimeoutExpired:
        process.send_signal(signal.SIGINT)
        stdout, _ = process.communicate(timeout=2)
    finally:
        if process.poll() is None:
            process.kill()

    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    assert len(lines) >= 1
    for line in lines:
        record = json.loads(line)
        assert "processes" in record
        assert len(record["processes"]) > 0, (
            "processes empty — --proc-filter did not imply --show-processes"
        )


@pytest.mark.local
def test_json_samples_limits_records_and_exits_zero():
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "actop.actop",
            "--json",
            "--samples",
            "2",
            "--interval",
            "1",
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )

    lines = [ln for ln in process.stdout.splitlines() if ln.strip()]
    assert process.returncode == 0, process.stderr
    assert len(lines) == 2, f"expected exactly 2 records, got {len(lines)}"
    record = json.loads(lines[0])
    assert "cpu_watts" in record


@pytest.mark.local
def test_serve_prometheus_includes_process_gauges_with_show_processes():
    import urllib.request

    port = 19992
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "actop.actop",
            "--serve",
            str(port),
            "--interval",
            "1",
            "--show-processes",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        body = None
        for _ in range(20):
            time.sleep(0.5)
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/metrics", timeout=1
                ) as response:
                    if response.status == 200:
                        body = response.read().decode()
                        break
            except Exception:
                continue

        assert body is not None, "metrics endpoint never returned 200"
        assert "# TYPE actop_process_cpu_percent gauge" in body
        assert "actop_process_cpu_percent{" in body
        assert "actop_process_gpu_time_share{" in body
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
