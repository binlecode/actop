"""Functional contract tests for the native polling layer.

These guard the version-sensitive byte-offset parsing in
``get_native_processes`` and the DVFS table classification heuristics in
``get_dvfs_tables_native`` — both run directly against the live kernel, so a
future macOS struct-layout change (or a regression in the classification
buckets) surfaces here as garbage values rather than silently downstream.

Requires macOS with Apple Silicon (marked local).
"""

import os
import platform

import pytest

from actop.native_sys import (
    _classify_dvfs_tables,
    get_dvfs_tables_native,
    get_native_processes,
    get_thermal_pressure,
)

pytestmark = pytest.mark.local

_THERMAL_STATES = {"Nominal", "Fair", "Serious", "Critical", "Unknown"}


def test_native_processes_offsets_decode_sane_values():
    # The proc_taskallinfo offsets are verified on macOS Sonoma/Sequoia arm64.
    # Surfacing the running version makes a struct-layout drift on a newer
    # macOS read as a version-coverage gap rather than a bare assertion error.
    macos = platform.mac_ver()[0] or "unknown"

    procs = get_native_processes()

    assert isinstance(procs, list)
    assert len(procs) > 0, "proc_listpids returned no processes"

    for entry in procs:
        assert isinstance(entry["pid"], int) and entry["pid"] > 0
        assert isinstance(entry["name"], str)
        assert isinstance(entry["rss_bytes"], int) and entry["rss_bytes"] >= 0
        assert isinstance(entry["num_threads"], int) and entry["num_threads"] >= 0
        assert isinstance(entry["cpu_time_ns"], int) and entry["cpu_time_ns"] >= 0

    # If the offsets had drifted, rss/threads would be zero or absurd across the
    # board.  Real systems always have many resident, multi-threaded processes.
    rss_ok = sum(1 for p in procs if p["rss_bytes"] > 0)
    threads_ok = sum(1 for p in procs if p["num_threads"] >= 1)
    assert rss_ok >= 5, (
        f"offset drift on macOS {macos}: only {rss_ok} procs report non-zero RSS"
    )
    assert threads_ok >= 5, (
        f"offset drift on macOS {macos}: only {threads_ok} procs report >=1 thread"
    )
    # RSS must stay within a sane ceiling (1 TB) — a misaligned uint64 read
    # would produce values far larger than any real process.
    assert all(p["rss_bytes"] < 1 << 40 for p in procs), (
        f"offset drift on macOS {macos}: a process reports RSS above the 1 TB ceiling"
    )


def test_native_processes_include_current_process():
    procs = get_native_processes()
    by_pid = {p["pid"]: p for p in procs}

    me = by_pid.get(os.getpid())
    assert me is not None, "current process not present in get_native_processes()"

    assert me["name"] != "", "current process name failed to decode"
    assert me["name"].isprintable()
    assert me["rss_bytes"] > 0
    assert me["num_threads"] >= 1


def test_cpu_time_ns_is_nanoseconds_not_mach_ticks():
    # proc_pidinfo reports per-process CPU time in mach ticks, not nanoseconds
    # (Apple Silicon timebase 125/3, so ticks are ~41.7x smaller -- see Apple
    # openradar FB9546856). A unit regression reads ~1/41.7 of real CPU time
    # here, which the share-based test in test_per_process_power.py cannot see
    # (its numerator and denominator shrink together). Burning CPU and
    # comparing the raw cpu_time_ns delta against getrusage is the only way
    # the unit surfaces.
    import resource
    import time

    def native_cpu_ns():
        procs = get_native_processes()
        me = next(p for p in procs if p["pid"] == os.getpid())
        return me["cpu_time_ns"]

    def getrusage_ns():
        ru = resource.getrusage(resource.RUSAGE_SELF)
        ut = (
            ru.ru_utime
            if isinstance(ru.ru_utime, float)
            else (ru.ru_utime[0] + ru.ru_utime[1] / 1e6)
        )
        st = (
            ru.ru_stime
            if isinstance(ru.ru_stime, float)
            else (ru.ru_stime[0] + ru.ru_stime[1] / 1e6)
        )
        return int((ut + st) * 1e9)

    get_native_processes()  # warm the code path
    base_native, base_ru = native_cpu_ns(), getrusage_ns()
    end = time.time() + 1.2
    x = 0
    while time.time() < end:
        x += 1
    measured = native_cpu_ns() - base_native
    real = getrusage_ns() - base_ru

    assert real > 0
    # Mach ticks are ~2.4% of ns on Apple Silicon, so a unit regression puts
    # measured at ~2.4% of real. Requiring 60% tolerates scheduling noise.
    assert measured >= real * 0.6, (
        f"cpu_time_ns grew {measured / 1e9:.3f}s vs {real / 1e9:.3f}s of real CPU "
        f"({measured / real * 100:.1f}%) -- mach-tick unit regression in get_native_processes"
    )


def test_dvfs_tables_classify_into_plausible_buckets():
    tables = get_dvfs_tables_native()

    assert set(tables.keys()) == {"ecpu", "pcpu", "gpu"}
    for freqs in tables.values():
        assert isinstance(freqs, list)
        for mhz in freqs:
            assert isinstance(mhz, int)
            # Non-zero entries must fall in a plausible Apple Silicon range.
            assert 0 <= mhz < 10_000

    # Apple Silicon always exposes a P-core voltage-states table.
    assert tables["pcpu"], "no P-core DVFS table discovered"
    assert max(tables["pcpu"]) > 2_000, "P-core max frequency implausibly low"

    # The classification must not hand the same physical table to two buckets.
    if tables["ecpu"]:
        assert max(tables["pcpu"]) >= max(tables["ecpu"]) > 0
        assert tables["pcpu"] is not tables["ecpu"]


def test_thermal_pressure_returns_known_state():
    assert get_thermal_pressure() in _THERMAL_STATES


# pmgr voltage-states captured from a live t8103 (M1 Max). The pre-1.9.13
# shape heuristic mislabelled "voltage-states5-sram" — a second P-core SRAM
# table — as the GPU, because it was the first 10-20-entry table the CPU
# buckets had not claimed; every clock/TFLOPS reading derived from it was a
# CPU frequency wearing a GPU label. The 7-entry GPU table (voltage-states9,
# ceiling 1296 MHz) never matched the old 10-20-entry window at all.
_T8103_TABLES = {
    "voltage-states1-sram": [600, 972, 1332, 1704, 2064],
    "voltage-states5-sram": [
        600, 828, 1056, 1296, 1524, 1752, 1980, 2208,
        2448, 2676, 2904, 3036, 3132, 3168, 3228,
    ],
    "voltage-states13-sram": [
        600, 828, 1056, 1296, 1524, 1752, 1980, 2208,
        2448, 2676, 2904, 3036, 3132, 3168, 3228,
    ],
    "voltage-states8": [300, 600, 900, 1125, 1350, 1500],
    "voltage-states9": [0, 389, 486, 648, 778, 972, 1296],
    "voltage-states14": [0, 389, 486, 648, 778, 972, 1296],
    "voltage-states31": [1000, 0, 0],
}


def test_classifier_never_hands_a_pcore_mirror_to_the_gpu_bucket():
    out = _classify_dvfs_tables(_T8103_TABLES)
    assert out["pcpu"] == _T8103_TABLES["voltage-states5-sram"]
    assert out["ecpu"] == _T8103_TABLES["voltage-states1-sram"]
    # Either the GPU bucket stays empty (callers then degrade honestly:
    # IOAccelerator utilization fallback, frequency n/a) or it holds a real
    # accelerator table. A GPU clock at or above the P-core ceiling is
    # physically impossible, so it is always a misclassification.
    if out["gpu"]:
        assert max(out["gpu"]) < max(out["pcpu"])
        assert len(out["gpu"]) != len(out["pcpu"])


def test_m1_max_override_resolves_the_gpu_domain_table():
    import actop.native_sys as ns

    if ns._get_cpu_brand() != "Apple M1 Max":
        pytest.skip("override table mapping validated against t8103 pmgr dumps")
    tables = get_dvfs_tables_native()
    assert tables["ecpu"] == [600, 972, 1332, 1704, 2064]
    assert max(tables["pcpu"]) == 3228
    assert tables["gpu"], "M1 Max must resolve a GPU table via the override"
    # 1296 MHz ceiling matches the M1 Max GPU spec (10.4 TFLOPS @ ~1293 MHz);
    # tolerate ±50 MHz firmware drift but nothing CPU-shaped.
    assert 1200 <= max(tables["gpu"]) <= 1350
