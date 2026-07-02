"""Dashboard configuration dataclass."""

import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class DashboardConfig:
    """Immutable values computed once from args + soc_info_dict."""

    sample_interval: int
    avg_window: int

    cpu_chart_ref_w: float
    gpu_chart_ref_w: float
    ane_max_power: float
    package_ref_w: float
    max_cpu_bw: float
    max_gpu_bw: float

    chip_name: str
    e_core_count: int
    p_core_count: int
    gpu_core_count: int

    power_scale: str
    chart_glyph: str
    layout: str
    show_cores: bool
    show_residency: bool

    alert_bw_sat_percent: int
    alert_package_power_percent: int
    alert_throttle_freq_percent: int
    alert_swap_rise_gb: float
    alert_sustain_samples: int

    subsamples: int

    process_display_count: int
    show_processes: bool
    process_filter_pattern: Optional[object]  # compiled regex or None


def create_dashboard_config(args, soc_info_dict):
    """Build an immutable DashboardConfig from parsed CLI args and SoC info."""
    sample_interval = max(1, args.interval)
    avg_window = max(1, int(args.avg / sample_interval))

    cpu_chart_ref_w = soc_info_dict["cpu_chart_ref_w"]
    gpu_chart_ref_w = soc_info_dict["gpu_chart_ref_w"]
    ane_max_power = float(soc_info_dict.get("ane_max_w", 8.0))
    package_ref_w = max(cpu_chart_ref_w + gpu_chart_ref_w + ane_max_power, 1.0)
    max_cpu_bw = max(float(soc_info_dict.get("cpu_max_bw", 0.0)), 1.0)
    max_gpu_bw = max(float(soc_info_dict.get("gpu_max_bw", 0.0)), 1.0)

    chip_name = soc_info_dict.get("name", "Apple Silicon")
    e_core_count = max(0, int(soc_info_dict["e_core_count"]))
    p_core_count = max(0, int(soc_info_dict["p_core_count"]))
    gpu_core_count = max(0, int(soc_info_dict.get("gpu_core_count", 0) or 0))

    process_filter_pattern = (
        re.compile(args.proc_filter, re.IGNORECASE)
        if getattr(args, "proc_filter", "")
        else None
    )

    return DashboardConfig(
        sample_interval=sample_interval,
        avg_window=avg_window,
        cpu_chart_ref_w=cpu_chart_ref_w,
        gpu_chart_ref_w=gpu_chart_ref_w,
        ane_max_power=ane_max_power,
        package_ref_w=package_ref_w,
        max_cpu_bw=max_cpu_bw,
        max_gpu_bw=max_gpu_bw,
        chip_name=chip_name,
        e_core_count=e_core_count,
        p_core_count=p_core_count,
        gpu_core_count=gpu_core_count,
        power_scale=args.power_scale,
        chart_glyph=getattr(args, "chart_glyph", "dots"),
        layout=getattr(args, "layout", "grid"),
        show_cores=args.show_cores,
        show_residency=bool(getattr(args, "show_residency", True)),
        alert_bw_sat_percent=args.alert_bw_sat_percent,
        alert_package_power_percent=args.alert_package_power_percent,
        alert_throttle_freq_percent=args.alert_throttle_freq_percent,
        alert_swap_rise_gb=args.alert_swap_rise_gb,
        alert_sustain_samples=max(1, int(args.alert_sustain_samples)),
        process_display_count=50,
        show_processes=bool(getattr(args, "show_processes", False)),
        subsamples=max(1, int(args.subsamples)),
        process_filter_pattern=process_filter_pattern,
    )
