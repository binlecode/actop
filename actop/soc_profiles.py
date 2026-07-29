import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SocProfile:
    name: str
    cpu_chart_ref_w: float
    gpu_chart_ref_w: float
    # Peak unified-memory bandwidth (GB/s) for the whole SoC. Apple Silicon has
    # a single shared DRAM bus, so this is one figure — the denominator the
    # bandwidth chart and the MEM-BOUND saturation alert normalise against.
    # Values are the vendor headline specs for the full (unbinned) die.
    max_mem_bw: float
    # ANE reference power (W) used as the denominator for ANE utilization %.
    # Defaulted to 8.0 across M1–M4 for now (behavior-preserving; the field
    # creates the per-SoC slot — per-generation refinements are a separate
    # research task). Trailing + defaulted so all profile literals stay valid.
    ane_max_w: float = 8.0


KNOWN_SOC_PROFILES = {
    "Apple M1": SocProfile(
        "Apple M1",
        cpu_chart_ref_w=20.0,
        gpu_chart_ref_w=20.0,
        max_mem_bw=68.0,
    ),
    "Apple M1 Pro": SocProfile(
        "Apple M1 Pro",
        cpu_chart_ref_w=30.0,
        gpu_chart_ref_w=30.0,
        max_mem_bw=200.0,
    ),
    "Apple M1 Max": SocProfile(
        "Apple M1 Max",
        cpu_chart_ref_w=30.0,
        gpu_chart_ref_w=60.0,
        max_mem_bw=400.0,
    ),
    "Apple M1 Ultra": SocProfile(
        "Apple M1 Ultra",
        cpu_chart_ref_w=60.0,
        gpu_chart_ref_w=120.0,
        max_mem_bw=800.0,
    ),
    "Apple M2": SocProfile(
        "Apple M2",
        cpu_chart_ref_w=25.0,
        gpu_chart_ref_w=15.0,
        max_mem_bw=100.0,
    ),
    "Apple M2 Pro": SocProfile(
        "Apple M2 Pro",
        cpu_chart_ref_w=35.0,
        gpu_chart_ref_w=30.0,
        max_mem_bw=200.0,
    ),
    "Apple M2 Max": SocProfile(
        "Apple M2 Max",
        cpu_chart_ref_w=40.0,
        gpu_chart_ref_w=65.0,
        max_mem_bw=400.0,
    ),
    "Apple M2 Ultra": SocProfile(
        "Apple M2 Ultra",
        cpu_chart_ref_w=80.0,
        gpu_chart_ref_w=130.0,
        max_mem_bw=800.0,
    ),
    "Apple M3": SocProfile(
        "Apple M3",
        cpu_chart_ref_w=25.0,
        gpu_chart_ref_w=20.0,
        max_mem_bw=100.0,
    ),
    "Apple M3 Pro": SocProfile(
        "Apple M3 Pro",
        cpu_chart_ref_w=35.0,
        gpu_chart_ref_w=30.0,
        max_mem_bw=150.0,
    ),
    "Apple M3 Max": SocProfile(
        "Apple M3 Max",
        cpu_chart_ref_w=45.0,
        gpu_chart_ref_w=75.0,
        max_mem_bw=400.0,
    ),
    "Apple M3 Ultra": SocProfile(
        "Apple M3 Ultra",
        cpu_chart_ref_w=90.0,
        gpu_chart_ref_w=150.0,
        max_mem_bw=800.0,
    ),
    "Apple M4": SocProfile(
        "Apple M4",
        cpu_chart_ref_w=30.0,
        gpu_chart_ref_w=20.0,
        max_mem_bw=120.0,
    ),
    "Apple M4 Pro": SocProfile(
        "Apple M4 Pro",
        cpu_chart_ref_w=40.0,
        gpu_chart_ref_w=35.0,
        max_mem_bw=273.0,
    ),
    "Apple M4 Max": SocProfile(
        "Apple M4 Max",
        cpu_chart_ref_w=55.0,
        gpu_chart_ref_w=90.0,
        max_mem_bw=546.0,
    ),
    "Apple M4 Ultra": SocProfile(
        "Apple M4 Ultra",
        cpu_chart_ref_w=110.0,
        gpu_chart_ref_w=180.0,
        max_mem_bw=1092.0,
    ),
}

GENERIC_APPLE_SILICON_PROFILE = SocProfile(
    "Apple Silicon",
    cpu_chart_ref_w=30.0,
    gpu_chart_ref_w=30.0,
    max_mem_bw=100.0,
)

TIER_FALLBACKS = {
    "Ultra": SocProfile(
        "Apple Silicon Ultra",
        cpu_chart_ref_w=110.0,
        gpu_chart_ref_w=180.0,
        max_mem_bw=1092.0,
    ),
    "Max": SocProfile(
        "Apple Silicon Max",
        cpu_chart_ref_w=55.0,
        gpu_chart_ref_w=90.0,
        max_mem_bw=546.0,
    ),
    "Pro": SocProfile(
        "Apple Silicon Pro",
        cpu_chart_ref_w=40.0,
        gpu_chart_ref_w=35.0,
        max_mem_bw=273.0,
    ),
    "base": SocProfile(
        "Apple Silicon",
        cpu_chart_ref_w=30.0,
        gpu_chart_ref_w=20.0,
        max_mem_bw=120.0,
    ),
}

APPLE_M_SERIES_PATTERN = re.compile(r"^Apple M\d+")


def normalize_soc_name(raw_name):
    if not raw_name:
        return "Apple Silicon"
    return " ".join(str(raw_name).strip().split())


def _copy_with_name(profile, new_name):
    return SocProfile(
        name=new_name,
        cpu_chart_ref_w=profile.cpu_chart_ref_w,
        gpu_chart_ref_w=profile.gpu_chart_ref_w,
        max_mem_bw=profile.max_mem_bw,
        ane_max_w=profile.ane_max_w,
    )


def get_soc_profile(raw_name):
    normalized_name = normalize_soc_name(raw_name)
    if normalized_name in KNOWN_SOC_PROFILES:
        return KNOWN_SOC_PROFILES[normalized_name]

    if APPLE_M_SERIES_PATTERN.match(normalized_name):
        if "Ultra" in normalized_name:
            return _copy_with_name(TIER_FALLBACKS["Ultra"], normalized_name)
        if "Max" in normalized_name:
            return _copy_with_name(TIER_FALLBACKS["Max"], normalized_name)
        if "Pro" in normalized_name:
            return _copy_with_name(TIER_FALLBACKS["Pro"], normalized_name)
        return _copy_with_name(TIER_FALLBACKS["base"], normalized_name)

    return _copy_with_name(GENERIC_APPLE_SILICON_PROFILE, normalized_name)
