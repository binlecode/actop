"""Tests for the ultra-lightweight ASCII dashboard and Agent-first compact views."""

import json

from actop.ascii_view import (
    _clean_command,
    build_ascii_table,
    format_compact_json,
    render_ascii_to_string,
)
from actop.models import ProcessSample, SystemSnapshot


def _mock_snapshot(
    pkg_w=25.0,
    cpu_w=12.0,
    gpu_w=10.0,
    ane_w=1.0,
    cpu_temp=65.0,
    gpu_temp=58.0,
    thermal_state="Nominal",
    bw=45.0,
    processes=None,
):
    return SystemSnapshot(
        timestamp=1000.0,
        cpu_watts=cpu_w,
        gpu_watts=gpu_w,
        ane_watts=ane_w,
        package_watts=pkg_w,
        ecpu_util_pct=15.0,
        pcpu_util_pct=45.0,
        gpu_util_pct=30.0,
        ane_util_pct=10.0,
        ecpu_freq_mhz=1200,
        pcpu_freq_mhz=2800,
        gpu_freq_mhz=900,
        cpu_temp_c=cpu_temp,
        gpu_temp_c=gpu_temp,
        thermal_state=thermal_state,
        ram_total_bytes=64 * 1024**3,
        ram_used_bytes=24 * 1024**3,
        swap_total_bytes=4 * 1024**3,
        swap_used_bytes=0,
        bandwidth_gbps=bw,
        bandwidth_available=True,
        processes=processes or [],
    )


def _mock_soc():
    return {
        "name": "Apple M4 Max",
        "e_core_count": 4,
        "p_core_count": 12,
        "gpu_core_count": 40,
        "package_ref_w": 100.0,
        "max_total_bw": 500.0,
        "max_p_freq_mhz": 4000,
        "max_gpu_freq_mhz": 1500,
    }


def test_clean_command_helper():
    assert _clean_command("") == "–"
    assert (
        _clean_command("/opt/homebrew/bin/llama-server -m model.gguf") == "llama-server"
    )
    assert (
        _clean_command("/Applications/Brave Browser.app/Contents/MacOS/Brave Browser")
        == "Brave"
    )
    long_cmd = "a" * 50
    cleaned = _clean_command(long_cmd, max_len=10)
    assert len(cleaned) <= 10
    assert cleaned.endswith("…")


def test_build_ascii_table_contains_all_domains():
    snap = _mock_snapshot()
    soc = _mock_soc()
    table = build_ascii_table(snap, soc)
    assert table.row_count >= 3


def test_render_ascii_to_string_structure():
    procs = [
        ProcessSample(
            pid=123,
            command="python -m mlx_lm",
            cpu_percent=120.0,
            cpu_time_share=0.5,
            gpu_time_share=0.8,
            num_threads=8,
            attributed_w=18.5,
            rss_bytes=4 * 1024**3,
        )
    ]
    snap = _mock_snapshot(processes=procs)
    soc = _mock_soc()

    out = render_ascii_to_string(snap, soc, snap.processes, color=False)
    assert "Apple M4 Max" in out
    assert "Package:" in out
    assert "25.00W" in out
    assert "RAM:" in out
    assert "24.0 / 64.0 GiB" in out
    assert "Bus:" in out
    assert "python" in out
    assert "18.50W" in out


def test_format_compact_json_schema_and_values():
    procs = [
        ProcessSample(
            pid=456,
            command="/usr/bin/python3 train.py",
            cpu_percent=95.0,
            cpu_time_share=0.4,
            gpu_time_share=0.2,
            num_threads=4,
            attributed_w=12.4,
            rss_bytes=2 * 1024**3,
        )
    ]
    snap = _mock_snapshot(pkg_w=40.0, processes=procs)
    soc = _mock_soc()

    json_str = format_compact_json(snap, soc, snap.processes)
    data = json.loads(json_str)

    assert data["soc"] == "Apple M4 Max"
    assert data["power_w"]["package"] == 40.0
    assert data["power_w"]["cpu"] == 12.0
    assert data["util_pct"]["pcpu"] == 45.0
    assert data["thermal"]["state"] == "Nominal"
    assert data["thermal"]["throttled"] is False
    assert data["memory"]["ram_total_gib"] == 64.0
    assert len(data["top_processes"]) == 1
    assert data["top_processes"][0]["pid"] == 456
    assert data["top_processes"][0]["command"] == "python3"
    assert data["top_processes"][0]["watts"] == 12.4


def test_thermal_throttling_flagged():
    # Hot and busy at low frequency triggers throttling
    snap = _mock_snapshot(
        cpu_temp=98.0,
        thermal_state="Critical",
    )
    # PCPU util is 45% by default, let's make it 90% and slow freq (1000MHz out of 4000MHz)
    snap.pcpu_util_pct = 95.0
    snap.pcpu_freq_mhz = 1000
    soc = _mock_soc()

    json_str = format_compact_json(snap, soc)
    data = json.loads(json_str)
    assert data["thermal"]["throttled"] is True

    out = render_ascii_to_string(snap, soc, color=False)
    assert "YES (Throttled)" in out
