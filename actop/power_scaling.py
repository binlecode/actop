DEFAULT_CPU_FLOOR_W = 20.0
DEFAULT_GPU_FLOOR_W = 15.0
DEFAULT_AUTO_MULTIPLIER = 1.25


def clamp_percent(percent_value):
    """Clamp a percentage into [0, 100] as an int, rounding rather than flooring.

    round() is the unbiased estimator: floor has an expected error of -0.5 units
    (max 1.0) for a uniform fractional part, which systematically under-reported
    every gauge and displayed 99.9% as 99%. Python's round-half-to-even is also
    unbiased on ties, unlike round-half-up.
    """
    return max(0, min(100, round(percent_value)))


def resolve_power_denominator(
    mode, profile_ref_w, peak_w, floor_w, auto_multiplier=DEFAULT_AUTO_MULTIPLIER
):
    if mode == "profile":
        return max(float(floor_w), float(profile_ref_w or 0.0))
    auto_peak_ref = float(peak_w or 0.0) * float(auto_multiplier)
    return max(float(floor_w), auto_peak_ref)


def power_to_percent(
    power_w,
    mode,
    profile_ref_w,
    peak_w,
    floor_w,
    auto_multiplier=DEFAULT_AUTO_MULTIPLIER,
):
    denominator = resolve_power_denominator(
        mode=mode,
        profile_ref_w=profile_ref_w,
        peak_w=peak_w,
        floor_w=floor_w,
        auto_multiplier=auto_multiplier,
    )
    if denominator <= 0:
        return 0
    return clamp_percent(float(power_w or 0.0) / denominator * 100.0)
