"""Export-backend tests: NDJSON and Prometheus formats + run loops.

The format functions are validated cross-platform against a real SystemSnapshot
(the public model type) — these are the external observability contracts. The
hardware-backed run loops are exercised end-to-end and marked local.
"""

import io
import json
import subprocess
import sys
import time

import pytest

from actop.export import (
    run_json_stream,
    snapshot_to_dict,
    snapshot_to_json,
    snapshot_to_prometheus,
)
from actop.models import CoreSample, SystemSnapshot


def _sample_snapshot(
    fan_rpms: list = None, fan_available: bool = False
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
        cpu_temp_c=48.0,
        gpu_temp_c=45.0,
        ecpu_freq_mhz=1200,
        pcpu_freq_mhz=3200,
        gpu_freq_mhz=1296,
        ram_used_gb=18.2,
        swap_used_gb=0.0,
        thermal_state="Nominal",
        bandwidth_gbps=42.0,
        bandwidth_available=True,
        fan_rpms=fan_rpms,
        fan_available=fan_available,
        e_cores=[CoreSample(index=0, active_pct=10, freq_mhz=1100)],
        p_cores=[CoreSample(index=4, active_pct=80, freq_mhz=3200)],
    )


def test_snapshot_to_json_is_single_line_and_round_trips():
    snapshot = _sample_snapshot()
    line = snapshot_to_json(snapshot)

    assert "\n" not in line
    record = json.loads(line)

    assert record == snapshot_to_dict(snapshot)
    assert record["cpu_watts"] == 12.5
    assert record["thermal_state"] == "Nominal"
    # Per-core lists must survive serialization for downstream consumers.
    assert record["p_cores"][0]["index"] == 4
    assert record["p_cores"][0]["active_pct"] == 80


def test_prometheus_exposition_is_well_formed():
    body = snapshot_to_prometheus(_sample_snapshot())

    assert body.endswith("\n")
    lines = body.strip().splitlines()

    # Scalar gauges carry a TYPE line and a value line.
    assert "# TYPE actop_cpu_power_watts gauge" in lines
    assert "actop_cpu_power_watts 12.5" in lines
    assert "actop_package_power_watts 16" in lines
    assert "actop_pcpu_utilization_percent 55.5" in lines

    # Per-core gauges are labelled by cluster + core index.
    assert 'actop_core_utilization_percent{cluster="P",core="4"} 80' in lines
    assert 'actop_core_frequency_mhz{cluster="E",core="0"} 1100' in lines

    # Every non-comment line must be `name value` (with optional {labels}).
    for line in lines:
        if line.startswith("#"):
            continue
        parts = line.rsplit(" ", 1)
        assert len(parts) == 2, f"malformed metric line: {line!r}"
        float(parts[1])  # value parses as a number


def test_prometheus_fan_gauge_labelled_per_fan_when_available():
    body = snapshot_to_prometheus(
        _sample_snapshot(fan_rpms=[1200.0, 980.0], fan_available=True)
    )
    lines = body.strip().splitlines()

    assert "# TYPE actop_fan_speed_rpm gauge" in lines
    assert 'actop_fan_speed_rpm{fan="0"} 1200' in lines
    assert 'actop_fan_speed_rpm{fan="1"} 980' in lines


def test_prometheus_fan_gauge_omitted_when_unavailable():
    # Fanless Mac: no SMC fan keys, so no phantom 0 RPM gauge is emitted.
    body = snapshot_to_prometheus(_sample_snapshot(fan_rpms=(), fan_available=False))

    assert "actop_fan_speed_rpm" not in body


@pytest.mark.local
def test_run_json_stream_emits_parseable_records():
    buffer = io.StringIO()
    count = run_json_stream(interval_s=1, subsamples=1, out=buffer, max_samples=2)

    assert count == 2
    lines = [ln for ln in buffer.getvalue().splitlines() if ln.strip()]
    assert len(lines) == 2

    record = json.loads(lines[0])
    assert "cpu_watts" in record
    assert "p_cores" in record
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
        assert "actop_core_utilization_percent{" in body
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
