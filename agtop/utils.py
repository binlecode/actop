import re
import time
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


def _normalize_process_command(cmdline, fallback_name):
    if isinstance(cmdline, (list, tuple)):
        command = " ".join(str(part) for part in cmdline if part)
    else:
        command = ""
    command = command.strip()
    if command:
        return command
    fallback = str(fallback_name or "").strip()
    return fallback if fallback else "?"


_PROCESS_CPU_CACHE = {}


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
    entries = []

    current_pids = set()
    for proc in native_procs:
        pid = proc["pid"]
        current_pids.add(pid)
        command = proc["name"]
        if pattern:
            if not pattern.search(command):
                cmdline = get_process_cmdline(pid)
                if cmdline and pattern.search(cmdline):
                    command = cmdline
                else:
                    continue

        cpu_time_ns = proc["cpu_time_ns"]
        cpu_percent = 0.0

        if pid in _PROCESS_CPU_CACHE:
            prev_cpu, prev_time = _PROCESS_CPU_CACHE[pid]
            time_delta = current_time - prev_time
            if time_delta > 0:
                cpu_delta_ns = cpu_time_ns - prev_cpu
                cpu_percent = max(
                    0.0, (cpu_delta_ns / 1_000_000_000) / time_delta * 100
                )

        _PROCESS_CPU_CACHE[pid] = (cpu_time_ns, current_time)

        rss_bytes = proc["rss_bytes"]
        rss_mb = rss_bytes / 1024 / 1024
        memory_percent = (rss_bytes / total_ram * 100) if total_ram > 0 else 0.0

        entries.append(
            {
                "pid": pid,
                "command": command,
                "cpu_percent": round(cpu_percent, 1),
                "rss_mb": round(rss_mb, 1),
                "memory_percent": round(memory_percent, 1),
                "num_threads": proc["num_threads"],
            }
        )

    # Clean up dead PIDs from cache
    for dead_pid in list(_PROCESS_CPU_CACHE.keys()):
        if dead_pid not in current_pids:
            _PROCESS_CPU_CACHE.pop(dead_pid, None)

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
