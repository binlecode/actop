"""Tests for the power-chart scaling behavior.

CLAUDE.md documents two user-facing modes: ``profile`` (scale against the SoC
reference wattage) and ``auto`` (scale against the rolling peak x1.25).  These
verify the mode semantics and the clamping contract that keeps the gauge in
0-100 — the visible behavior, not the constants behind it.

Pure arithmetic, no I/O — runs on every platform.
"""

from actop.power_scaling import power_to_percent


def test_profile_mode_scales_against_reference_ignoring_peak():
    # Profile mode must use the SoC reference and ignore the observed peak.
    pct = power_to_percent(
        power_w=10.0, mode="profile", profile_ref_w=20.0, peak_w=999.0, floor_w=1.0
    )
    assert pct == 50


def test_auto_mode_scales_against_peak_ignoring_reference():
    # Auto mode must use peak * multiplier and ignore the profile reference.
    pct = power_to_percent(
        power_w=10.0,
        mode="auto",
        profile_ref_w=999.0,
        peak_w=10.0,
        floor_w=1.0,
        auto_multiplier=2.0,
    )
    assert pct == 50


def test_floor_prevents_tiny_denominator_blowups():
    # With a near-zero peak, auto mode must fall back to the floor as the
    # denominator instead of producing a runaway / divide-by-zero percentage.
    pct = power_to_percent(
        power_w=5.0, mode="auto", profile_ref_w=0.0, peak_w=0.0, floor_w=10.0
    )
    assert pct == 50


def test_percent_is_rounded_and_clamped_to_0_100():
    over = power_to_percent(
        power_w=100.0, mode="profile", profile_ref_w=10.0, peak_w=0.0, floor_w=1.0
    )
    assert over == 100

    none_power = power_to_percent(
        power_w=None, mode="profile", profile_ref_w=10.0, peak_w=0.0, floor_w=1.0
    )
    assert none_power == 0

    negative = power_to_percent(
        power_w=-1.0, mode="profile", profile_ref_w=10.0, peak_w=0.0, floor_w=1.0
    )
    assert negative == 0

    # Percentages round rather than truncate: flooring biased every gauge down by
    # an expected half point and rendered a near-saturated 99.6% as 99%.
    nearly_full = power_to_percent(
        power_w=9.96, mode="profile", profile_ref_w=10.0, peak_w=0.0, floor_w=1.0
    )
    assert nearly_full == 100

    nearly_zero = power_to_percent(
        power_w=0.04, mode="profile", profile_ref_w=10.0, peak_w=0.0, floor_w=1.0
    )
    assert nearly_zero == 0


def test_non_positive_denominator_yields_zero():
    pct = power_to_percent(
        power_w=10.0, mode="profile", profile_ref_w=0.0, peak_w=0.0, floor_w=0.0
    )
    assert pct == 0
