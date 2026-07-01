import re
import time
from .gpu_registry import get_gpu_time_by_pid
from .native_sys import (
    get_gpu_cores_native,
    get_sysctl_int,
    get_sysctl_string,
    get_native_ram,
    get_native_swap,
    get_native_processes,
    get_process_cmdline,
)
from .soc_profiles import get_soc_profile


def convert_to_GB(value):
    return round(value / 1024 / 1024 / 1024, 1)


def get_ram_metrics_dict():
    vm = get_native_ram()
    total_bytes = vm.total
    used_bytes = vm.total - vm.available
    free_bytes = vm.available
    used_percent = (
        min(100, int(used_bytes / total_bytes * 100)) if total_bytes > 0 else 0
    )

    swap = get_native_swap()
    if swap.total > 0:
        swap_used_percent = int(swap.used / swap.total * 100)
    else:
        swap_used_percent = None

    return {
        "total_GB": convert_to_GB(total_bytes),
        "free_GB": convert_to_GB(free_bytes),
        "used_GB": convert_to_GB(used_bytes),
        "used_percent": used_percent,
        "swap_total_GB": convert_to_GB(swap.total),
        "swap_used_GB": convert_to_GB(swap.used),
        "swap_free_GB": convert_to_GB(swap.total - swap.used),
        "swap_used_percent": swap_used_percent,
    }


def get_cpu_info():
    cpu_info_dict = {}
    brand = get_sysctl_string("machdep.cpu.brand_string")
    if brand:
        cpu_info_dict["machdep.cpu.brand_string"] = brand
    core_count = get_sysctl_int("machdep.cpu.core_count")
    if core_count is not None:
        cpu_info_dict["machdep.cpu.core_count"] = str(core_count)
    return cpu_info_dict


def get_core_counts():
    cores_info_dict = {}
    p_cores = get_sysctl_int("hw.perflevel0.logicalcpu")
    if p_cores is not None:
        cores_info_dict["hw.perflevel0.logicalcpu"] = int(p_cores)
    e_cores = get_sysctl_int("hw.perflevel1.logicalcpu")
    if e_cores is not None:
        cores_info_dict["hw.perflevel1.logicalcpu"] = int(e_cores)
    return cores_info_dict


def get_gpu_cores():
    try:
        cores = get_gpu_cores_native()
        if cores and cores > 0:
            return cores
    except Exception:
        pass
    return "?"


def get_soc_info():
    cpu_info_dict = get_cpu_info()
    core_counts_dict = get_core_counts()
    name = cpu_info_dict.get("machdep.cpu.brand_string", "Apple Silicon")
    profile = get_soc_profile(name)
    e_core_count = int(core_counts_dict.get("hw.perflevel1.logicalcpu", 0))
    p_core_count = int(core_counts_dict.get("hw.perflevel0.logicalcpu", 0))
    core_count = int(cpu_info_dict.get("machdep.cpu.core_count", 0) or 0)
    if p_core_count == 0 and core_count > 0:
        p_core_count = core_count

    soc_info = {
        "name": profile.name,
        "core_count": core_count,
        "cpu_chart_ref_w": profile.cpu_chart_ref_w,
        "gpu_chart_ref_w": profile.gpu_chart_ref_w,
        "cpu_max_power": profile.cpu_chart_ref_w,
        "gpu_max_power": profile.gpu_chart_ref_w,
        "cpu_max_bw": profile.cpu_max_bw,
        "gpu_max_bw": profile.gpu_max_bw,
        "e_core_count": e_core_count,
        "p_core_count": p_core_count,
        "gpu_core_count": get_gpu_cores(),
    }
    return soc_info


_PROCESS_CPU_CACHE = {}
_PROCESS_GPU_CACHE = {}


def _delta_ns(cache, key, raw_value_ns, current_time):
    """Delta raw_value_ns against the previous sample cached under key.

    Returns (delta_ns, time_delta): delta_ns is None on a first sample for
    this key (nothing to delta against yet), otherwise clamped to >= 0 (a
    counter can reset mid-poll, e.g. a process closing one GPU client and
    opening a new one). Updates the cache in place with (raw_value_ns,
    current_time). Shared by the CPU and GPU per-process time passes below
    so both use one delta/eviction algorithm instead of two copies of it.
    """
    delta_ns = None
    time_delta = 0.0
    if key in cache:
        prev_value, prev_time = cache[key]
        time_delta = current_time - prev_time
        if time_delta > 0:
            delta_ns = max(0, raw_value_ns - prev_value)
    cache[key] = (raw_value_ns, current_time)
    return delta_ns, time_delta


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


def get_top_processes(limit=3, proc_filter=None):
    pattern = None
    if proc_filter:
        if hasattr(proc_filter, "search"):
            pattern = proc_filter
        else:
            pattern = re.compile(str(proc_filter), re.IGNORECASE)

    current_time = time.time()
    total_ram = get_sysctl_int("hw.memsize") or (16 * 1024 * 1024 * 1024)

    native_procs = get_native_processes()

    # Pass 1: compute CPU-time deltas for *every* PID (independent of any
    # filter) so per-process power can later be attributed as a partition of
    # the total CPU watts. The cache is keyed on (pid, start_tvsec) so a reused
    # PID with a fresh start time is treated as a first sample, not a bogus
    # delta. total_delta_ns is the denominator of the CPU-time share.
    current_keys = set()
    proc_stats = {}  # pid -> (cpu_percent, cpu_delta_ns or None)
    start_tvsec_by_pid = {}
    total_delta_ns = 0
    for proc in native_procs:
        pid = proc["pid"]
        start_tvsec = proc.get("start_tvsec", 0)
        key = (pid, start_tvsec)
        current_keys.add(key)
        start_tvsec_by_pid[pid] = start_tvsec

        cpu_delta_ns, time_delta = _delta_ns(
            _PROCESS_CPU_CACHE, key, proc["cpu_time_ns"], current_time
        )
        cpu_percent = 0.0
        if cpu_delta_ns is not None:
            cpu_percent = (cpu_delta_ns / 1_000_000_000) / time_delta * 100
            total_delta_ns += cpu_delta_ns
        proc_stats[pid] = (cpu_percent, cpu_delta_ns)

    # Pass 1b: same delta treatment for GPU time, sourced from the IOKit
    # accelerator registry (gpu_registry.py) instead of libproc. A pid absent
    # from get_gpu_time_by_pid() has never opened a GPU client -- that's a
    # real, immediate 0.0, not a pending first sample, so it's handled in
    # Pass 2 rather than seeded here.
    #
    # gpu_registry reads the IOKit registry directly, which (unlike libproc)
    # doesn't require matching UID -- it can see privileged system processes
    # (e.g. WindowServer) that get_native_processes() silently drops. Those
    # pids never get a row (Pass 2 only iterates native_procs), so they're
    # skipped here too: including them in total_gpu_delta_ns would dilute
    # every visible pid's share against GPU time that can never be attributed
    # to a row, breaking the same "numerator and denominator drawn from the
    # same visible set" invariant the CPU pass already relies on.
    gpu_time_by_pid = get_gpu_time_by_pid()
    gpu_delta_by_pid = {}  # pid -> gpu_delta_ns or None (pending first sample)
    total_gpu_delta_ns = 0
    for pid, gpu_time_ns in gpu_time_by_pid.items():
        if pid not in start_tvsec_by_pid:
            continue
        key = (pid, start_tvsec_by_pid[pid])
        gpu_delta_ns, _ = _delta_ns(_PROCESS_GPU_CACHE, key, gpu_time_ns, current_time)
        gpu_delta_by_pid[pid] = gpu_delta_ns
        if gpu_delta_ns is not None:
            total_gpu_delta_ns += gpu_delta_ns

    # Clean up dead (pid, start) pairs from both caches
    for cache in (_PROCESS_CPU_CACHE, _PROCESS_GPU_CACHE):
        for dead_key in list(cache.keys()):
            if dead_key not in current_keys:
                cache.pop(dead_key, None)

    # Pass 2: build entries (applying the filter) and turn each PID's delta
    # into a time share in [0, 1]. Shares are deliberately kept decoupled
    # from watts — the TUI owns cpu_watts/gpu_watts and multiplies
    # (utils.attribute_power).
    entries = []
    for proc in native_procs:
        pid = proc["pid"]
        command = proc["name"]
        if pattern:
            if not pattern.search(command):
                cmdline = get_process_cmdline(pid)
                if cmdline and pattern.search(cmdline):
                    command = cmdline
                else:
                    continue

        cpu_percent, cpu_delta_ns = proc_stats.get(pid, (0.0, None))
        if cpu_delta_ns is None:
            cpu_time_share = None  # first sample: no delta yet
        elif total_delta_ns > 0:
            cpu_time_share = cpu_delta_ns / total_delta_ns
        else:
            cpu_time_share = 0.0  # fully idle poll: no divide-by-zero

        if pid not in gpu_time_by_pid:
            gpu_time_share = 0.0  # never opened a GPU client: real zero
        else:
            gpu_delta_ns = gpu_delta_by_pid.get(pid)
            if gpu_delta_ns is None:
                gpu_time_share = None  # has a client, first sample pending
            elif total_gpu_delta_ns > 0:
                gpu_time_share = gpu_delta_ns / total_gpu_delta_ns
            else:
                gpu_time_share = 0.0

        rss_bytes = proc["rss_bytes"]
        rss_mb = rss_bytes / 1024 / 1024
        memory_percent = (rss_bytes / total_ram * 100) if total_ram > 0 else 0.0

        entries.append(
            {
                "pid": pid,
                "command": command,
                "cpu_percent": round(cpu_percent, 1),
                "cpu_time_share": cpu_time_share,
                "gpu_time_share": gpu_time_share,
                "rss_mb": round(rss_mb, 1),
                "memory_percent": round(memory_percent, 1),
                "num_threads": proc["num_threads"],
            }
        )

    top_cpu = sorted(
        entries,
        key=lambda item: (item["cpu_percent"], item["rss_mb"]),
        reverse=True,
    )[:limit]

    top_memory = sorted(
        entries,
        key=lambda item: (item["rss_mb"], item["memory_percent"]),
        reverse=True,
    )[:limit]

    # Resolve full cmdlines for the top candidate processes on-demand
    for item in top_cpu:
        if not pattern or item["command"] == proc_name_by_pid(
            native_procs, item["pid"]
        ):
            cmdline = get_process_cmdline(item["pid"])
            if cmdline:
                item["command"] = cmdline

    for item in top_memory:
        if not pattern or item["command"] == proc_name_by_pid(
            native_procs, item["pid"]
        ):
            cmdline = get_process_cmdline(item["pid"])
            if cmdline:
                item["command"] = cmdline

    return {"cpu": top_cpu, "memory": top_memory}


def proc_name_by_pid(native_procs, pid):
    for p in native_procs:
        if p["pid"] == pid:
            return p["name"]
    return ""
