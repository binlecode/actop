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
from actop.models import CoreSample, ProcessSample, SystemSnapshot


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
        # Driver-reported GPU stats. Every value is distinct from every other
        # (and from gpu_util_pct) so a gauge wired to the wrong field fails
        # rather than passing by coincidence.
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
        # Bytes are canonical; *_gb are the deprecated rounded views. Values are
        # deliberately NOT consistent with each other, so a gauge wired to the
        # wrong field is caught rather than passing by luck.
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


def test_snapshot_to_json_is_single_line_and_round_trips():
    snapshot = _sample_snapshot()
    line = snapshot_to_json(snapshot)

    assert "\n" not in line
    record = json.loads(line)

    assert record == snapshot_to_dict(snapshot)
    assert record["cpu_watts"] == 12.5
    assert record["thermal_state"] == "Nominal"
    # Memory rides the NDJSON record as exact byte counts — no GB-vs-GiB
    # ambiguity and no rounding loss — alongside the deprecated rounded *_gb
    # keys. A consumer dividing against the genuinely decimal bandwidth_gbps
    # can therefore pick its own base explicitly.
    assert record["ram_used_bytes"] == 19_542_236_365
    assert record["ram_total_bytes"] == 137_438_953_472
    assert record["swap_used_bytes"] == 1_610_612_736
    assert record["ram_used_gb"] == 17.4
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

    # Memory is exposed in base units (bytes), per Prometheus naming convention,
    # with the deprecated gigabytes gauges kept alongside so existing scrape
    # configs keep working until 2.0.0. Each must read its own field, not the
    # other's — the fixture's byte and *_gb values disagree on purpose.
    assert "actop_ram_used_bytes 19542236365" in lines
    assert "actop_ram_total_bytes 137438953472" in lines
    assert "actop_swap_used_bytes 1610612736" in lines
    assert "actop_ram_used_gigabytes 17.4" in lines

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


def test_gpu_driver_stats_export_as_gauges_but_source_stays_out_of_prometheus():
    # The Renderer/Tiler split is the metric actop cannot express from IOReport
    # residency alone, so it has to reach both observability backends.
    snapshot = _sample_snapshot(gpu_util_source="ioaccelerator")
    lines = snapshot_to_prometheus(snapshot).strip().splitlines()

    assert "actop_gpu_device_utilization_percent 61" in lines
    assert "actop_gpu_renderer_utilization_percent 58" in lines
    assert "actop_gpu_tiler_utilization_percent 7" in lines
    # The residency-derived headline metric stays distinct from the driver's.
    assert "actop_gpu_utilization_percent 40" in lines

    # gpu_util_source is a string. Emitting it as a gauge would produce a line
    # whose value does not parse as a number, breaking the whole scrape — so it
    # must reach consumers through NDJSON only (or a label, if ever wanted).
    assert "gpu_util_source" not in snapshot_to_prometheus(snapshot)

    record = json.loads(snapshot_to_json(snapshot))
    assert record["gpu_util_source"] == "ioaccelerator"
    assert record["gpu_perf_stats_available"] is True
    assert record["gpu_renderer_pct"] == 58.0
    assert record["gpu_tiler_pct"] == 7.0


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


def test_snapshot_to_json_includes_processes_when_present():
    snapshot = _sample_snapshot_with_processes()
    record = json.loads(snapshot_to_json(snapshot))

    assert "processes" in record
    assert len(record["processes"]) == 2
    assert record["processes"][0]["pid"] == 1234
    assert record["processes"][0]["command"] == "python"
    assert record["processes"][0]["cpu_time_share"] == 0.35
    assert record["processes"][0]["gpu_time_share"] == 0.12
    assert record["processes"][0]["attributed_w"] == 5.6
    assert record["processes"][0]["rss_bytes"] == 2_147_483_648
    assert record["processes"][1]["pid"] == 5678
    assert record["processes"][1]["command"] == "ollama"


def test_prometheus_includes_process_gauges_when_processes_present():
    body = snapshot_to_prometheus(_sample_snapshot_with_processes())
    lines = body.strip().splitlines()

    assert "# TYPE actop_process_cpu_percent gauge" in lines
    assert "# TYPE actop_process_cpu_time_share gauge"
    assert "# TYPE actop_process_gpu_time_share gauge"
    assert "# TYPE actop_process_attributed_watts gauge"
    assert "# TYPE actop_process_rss_bytes gauge"
    assert "# TYPE actop_process_num_threads gauge"

    assert 'actop_process_cpu_percent{pid="1234",command="python"} 45.5' in lines
    assert 'actop_process_cpu_time_share{pid="1234",command="python"} 0.35' in lines
    assert 'actop_process_gpu_time_share{pid="1234",command="python"} 0.12' in lines
    assert 'actop_process_attributed_watts{pid="1234",command="python"} 5.6' in lines
    assert 'actop_process_rss_bytes{pid="1234",command="python"} 2147483648' in lines
    assert 'actop_process_num_threads{pid="1234",command="python"} 8' in lines

    assert 'actop_process_cpu_percent{pid="5678",command="ollama"} 12' in lines


def test_prometheus_omits_process_gauges_when_no_processes():
    body = snapshot_to_prometheus(_sample_snapshot())

    assert "actop_process_cpu_percent" not in body
    assert "actop_process_gpu_time_share" not in body


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
        assert isinstance(record["processes"], list)
        assert len(record["processes"]) > 0, (
            "processes empty — --proc-filter did not imply --show-processes"
        )


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
