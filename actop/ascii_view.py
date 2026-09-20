"""Ultra-lightweight ASCII dashboard & Agent-first views for actop (apple-smi style).

Zero heavy event loops, zero async DOM, zero curses. Renders dense, high-signal
hardware and energy attribution in milliseconds.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

from rich.box import ROUNDED
from rich.console import Console
from rich.table import Table
from rich.text import Text

from actop import __version__
from actop.analytics import (
    SORT_POWER,
    domain_throttling,
    sort_processes,
)

if TYPE_CHECKING:
    from actop.models import ProcessSample, SystemSnapshot


def _to_gib(bytes_val: float) -> float:
    return round(bytes_val / (1024**3), 1)


def _to_mib(bytes_val: float) -> int:
    return round(bytes_val / (1024**2))


def _clean_command(command: str, max_len: int = 26) -> str:
    """Extract a concise executable name from a potentially long command string."""
    if not command:
        return "–"
    token = command.strip().split()[0]
    name = token.rsplit("/", 1)[-1]
    if len(name) > max_len:
        return name[: max_len - 1] + "…"
    return name


def build_ascii_table(
    snapshot: SystemSnapshot,
    soc_info: dict,
    processes: list[ProcessSample] | None = None,
    *,
    max_processes: int = 8,
) -> Table:
    """Construct a self-contained Rich Table displaying the full SoC status."""
    chip_name = soc_info.get("name", "Apple Silicon")
    e_cores = int(soc_info.get("e_core_count", 0))
    p_cores = int(soc_info.get("p_core_count", 0))
    gpu_cores = int(soc_info.get("gpu_core_count", 0))
    package_ref_w = float(soc_info.get("package_ref_w", 30.0))
    max_total_bw = float(soc_info.get("max_total_bw", 100.0))

    # Master outer container
    master = Table(
        box=ROUNDED,
        show_header=True,
        header_style="bold cyan",
        title=f"[bold white]actop v{__version__}[/] · [bold green]{chip_name}[/] ({p_cores}P+{e_cores}E CPU, {gpu_cores}-core GPU)",
        title_justify="left",
        expand=True,
    )
    master.add_column("Domain", style="bold yellow", width=18)
    master.add_column("Hardware & Energy Telemetry")

    # 1. Power & Clocks
    pkg_w = snapshot.package_watts
    pkg_pct = round(pkg_w / package_ref_w * 100) if package_ref_w > 0 else 0
    pkg_color = "red" if pkg_pct >= 85 else "yellow" if pkg_pct >= 60 else "green"

    cpu_w = snapshot.cpu_watts
    gpu_w = snapshot.gpu_watts
    ane_w = snapshot.ane_watts

    p_freq = f"{snapshot.pcpu_freq_mhz}MHz" if snapshot.pcpu_freq_mhz > 0 else "idle"
    e_freq = f"{snapshot.ecpu_freq_mhz}MHz" if snapshot.ecpu_freq_mhz > 0 else "idle"
    gpu_freq = f"@{snapshot.gpu_freq_mhz}MHz" if snapshot.gpu_freq_mhz > 0 else ""

    p_util = f"{round(snapshot.pcpu_util_pct)}%"
    e_util = f"{round(snapshot.ecpu_util_pct)}%"
    gpu_util = f"{round(snapshot.gpu_util_pct)}%"

    power_text = Text()
    power_text.append(f"Package: {pkg_w:.2f}W / {package_ref_w:.0f}W ", style="bold")
    power_text.append(f"({pkg_pct}%)\n", style=pkg_color)
    power_text.append(
        f"CPU:     {cpu_w:.2f}W  P-Cluster: {p_util} ({p_freq})  E-Cluster: {e_util} ({e_freq})\n"
    )
    power_text.append(
        f"GPU:     {gpu_w:.2f}W  Util: {gpu_util} {gpu_freq} (residency: {snapshot.gpu_util_source})\n"
    )
    power_text.append(f"ANE:     {ane_w:.2f}W  Neural Engine power")

    master.add_row("Power & Compute", power_text)

    # 2. Thermal & Health
    cpu_t = f"{snapshot.cpu_temp_c:.0f}°C" if snapshot.cpu_temp_c > 0 else "N/A"
    gpu_t = f"{snapshot.gpu_temp_c:.0f}°C" if snapshot.gpu_temp_c > 0 else "N/A"
    thermal_state = snapshot.thermal_state or "Nominal"

    p_throttled = domain_throttling(
        snapshot.pcpu_util_pct,
        snapshot.pcpu_freq_mhz,
        soc_info.get("max_p_freq_mhz", 4000),
        snapshot.cpu_temp_c,
        thermal_state,
        freq_percent=90,
    )
    gpu_throttled = domain_throttling(
        snapshot.gpu_util_pct,
        snapshot.gpu_freq_mhz,
        soc_info.get("max_gpu_freq_mhz", 1500),
        snapshot.gpu_temp_c,
        thermal_state,
        freq_percent=90,
    )
    throttle_status = (
        "YES (Throttled)" if (p_throttled or gpu_throttled) else "None (Full Clock)"
    )
    throttle_style = "bold red" if (p_throttled or gpu_throttled) else "green"

    thermal_text = Text()
    thermal_text.append(
        f"State:   {thermal_state}  |  CPU Die: {cpu_t}  |  GPU Die: {gpu_t}\n"
    )
    thermal_text.append("Throttle: ")
    thermal_text.append(throttle_status, style=throttle_style)
    master.add_row("Thermal & Clocks", thermal_text)

    # 3. Unified Memory & Bandwidth
    ram_used = _to_gib(snapshot.ram_used_bytes)
    ram_total = _to_gib(snapshot.ram_total_bytes)
    ram_pct = round(ram_used / ram_total * 100) if ram_total > 0 else 0
    swap_used = _to_gib(snapshot.swap_used_bytes)
    swap_total = _to_gib(snapshot.swap_total_bytes)

    bw_val = snapshot.bandwidth_gbps
    bw_pct = round(bw_val / max_total_bw * 100) if max_total_bw > 0 else 0
    bw_bound = " [bold red][MEM-BOUND][/]" if bw_pct >= 85 else ""

    mem_text = Text()
    mem_text.append(
        f"RAM:     {ram_used:.1f} / {ram_total:.1f} GiB ({ram_pct}%)   Swap: {swap_used:.1f} / {swap_total:.1f} GiB\n"
    )
    mem_text.append(
        f"Bus:     {bw_val:.1f} / {max_total_bw:.0f} GB/s ({bw_pct}%){bw_bound}"
    )
    master.add_row("Unified Memory", mem_text)

    # 4. Top Process Power Attribution
    procs = processes if processes is not None else getattr(snapshot, "processes", None)
    if procs:
        top_procs = sort_processes(procs, sort_mode=SORT_POWER, limit=max_processes)
        proc_table = Table(
            box=None,
            show_header=True,
            header_style="bold magenta",
            expand=True,
            padding=(0, 1),
        )
        proc_table.add_column("PID", justify="right", style="dim")
        proc_table.add_column("Command", style="white", ratio=2)
        proc_table.add_column("CPU%", justify="right")
        proc_table.add_column("GPU%", justify="right")
        proc_table.add_column("Power", justify="right", style="bold yellow")
        proc_table.add_column("RAM", justify="right", style="dim")

        for p in top_procs:
            pwr_str = f"{p.attributed_w:.2f}W" if p.attributed_w is not None else "–"
            gpu_str = (
                f"{round((p.gpu_time_share or 0) * 100)}%"
                if p.gpu_time_share is not None
                else "–"
            )
            proc_table.add_row(
                str(p.pid),
                _clean_command(p.command),
                f"{p.cpu_percent:.1f}%",
                gpu_str,
                pwr_str,
                f"{_to_mib(p.rss_bytes)}MiB",
            )
        master.add_row("Top Power (W)", proc_table)

    return master


def render_ascii_to_string(
    snapshot: SystemSnapshot,
    soc_info: dict,
    processes: list[ProcessSample] | None = None,
    *,
    max_processes: int = 8,
    color: bool = True,
) -> str:
    """Render the dashboard table as an ANSI or plain text string."""
    console = Console(color_system="auto" if color else None, width=90, record=True)
    table = build_ascii_table(
        snapshot, soc_info, processes, max_processes=max_processes
    )
    console.print(table)
    return (
        console.export_text(clear=False)
        if not color
        else console.export_text(styles=True, clear=False)
    )


def print_ascii_screen(
    snapshot: SystemSnapshot,
    soc_info: dict,
    processes: list[ProcessSample] | None = None,
    *,
    max_processes: int = 8,
    clear: bool = False,
) -> None:
    """Print the dashboard to stdout (optionally clearing screen first)."""
    if clear and sys.stdout.isatty():
        sys.stdout.write("\033[H\033[2J")
        sys.stdout.flush()
    console = Console()
    table = build_ascii_table(
        snapshot, soc_info, processes, max_processes=max_processes
    )
    console.print(table)


def format_compact_json(
    snapshot: SystemSnapshot,
    soc_info: dict,
    processes: list[ProcessSample] | None = None,
    *,
    max_processes: int = 5,
) -> str:
    """Generate high-density, low-token JSON representation for AI agents (< 400 tokens)."""
    chip_name = soc_info.get("name", "Apple Silicon")
    package_ref_w = float(soc_info.get("package_ref_w", 30.0))
    max_total_bw = float(soc_info.get("max_total_bw", 100.0))
    thermal_state = snapshot.thermal_state or "Nominal"

    p_throttled = domain_throttling(
        snapshot.pcpu_util_pct,
        snapshot.pcpu_freq_mhz,
        soc_info.get("max_p_freq_mhz", 4000),
        snapshot.cpu_temp_c,
        thermal_state,
        freq_percent=90,
    )
    gpu_throttled = domain_throttling(
        snapshot.gpu_util_pct,
        snapshot.gpu_freq_mhz,
        soc_info.get("max_gpu_freq_mhz", 1500),
        snapshot.gpu_temp_c,
        thermal_state,
        freq_percent=90,
    )

    data = {
        "soc": chip_name,
        "power_w": {
            "package": round(snapshot.package_watts, 2),
            "cpu": round(snapshot.cpu_watts, 2),
            "gpu": round(snapshot.gpu_watts, 2),
            "ane": round(snapshot.ane_watts, 2),
            "ref_w": package_ref_w,
        },
        "util_pct": {
            "pcpu": round(snapshot.pcpu_util_pct, 1),
            "ecpu": round(snapshot.ecpu_util_pct, 1),
            "gpu": round(snapshot.gpu_util_pct, 1),
        },
        "clocks_mhz": {
            "pcpu": snapshot.pcpu_freq_mhz,
            "ecpu": snapshot.ecpu_freq_mhz,
            "gpu": snapshot.gpu_freq_mhz,
        },
        "thermal": {
            "state": thermal_state,
            "cpu_c": round(snapshot.cpu_temp_c, 1),
            "gpu_c": round(snapshot.gpu_temp_c, 1),
            "throttled": bool(p_throttled or gpu_throttled),
        },
        "memory": {
            "ram_used_gib": _to_gib(snapshot.ram_used_bytes),
            "ram_total_gib": _to_gib(snapshot.ram_total_bytes),
            "swap_used_gib": _to_gib(snapshot.swap_used_bytes),
            "bandwidth_gbps": round(snapshot.bandwidth_gbps, 1),
            "bandwidth_peak_gbps": max_total_bw,
            "bandwidth_saturated": bool(
                snapshot.bandwidth_gbps / max_total_bw >= 0.85
                if max_total_bw > 0
                else False
            ),
        },
    }

    procs = processes if processes is not None else getattr(snapshot, "processes", None)
    if procs:
        top_procs = sort_processes(procs, sort_mode=SORT_POWER, limit=max_processes)
        data["top_processes"] = [
            {
                "pid": p.pid,
                "command": _clean_command(p.command),
                "watts": (
                    round(p.attributed_w, 2) if p.attributed_w is not None else None
                ),
                "cpu_pct": round(p.cpu_percent, 1),
                "gpu_pct": round((p.gpu_time_share or 0.0) * 100, 1),
                "rss_mib": _to_mib(p.rss_bytes),
            }
            for p in top_procs
        ]

    return json.dumps(data, separators=(",", ":"))
