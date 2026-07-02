"""Tests for the documented SoC fallback contract.

CLAUDE.md promises: explicit M1-M4 recognition, and "unknown future chips fall
back to tier defaults (base/Pro/Max/Ultra) using the latest known generation's
reference values."  ``get_soc_profile`` is the public function that encodes this
product behavior; these verify the routing, not the magic numbers (those are
imported and compared, never hard-coded).

Pure name -> profile mapping, no I/O — runs on every platform.
"""

from actop.soc_profiles import (
    GENERIC_APPLE_SILICON_PROFILE,
    KNOWN_SOC_PROFILES,
    TIER_FALLBACKS,
    get_soc_profile,
)


def test_known_chip_returns_exact_profile():
    profile = get_soc_profile("Apple M2 Max")
    assert profile is KNOWN_SOC_PROFILES["Apple M2 Max"]


def test_whitespace_in_name_is_normalized():
    assert get_soc_profile("  Apple   M1  ") is KNOWN_SOC_PROFILES["Apple M1"]


def test_unknown_future_chip_falls_back_to_matching_tier():
    # A generation beyond the built-in profiles must still route by tier suffix.
    for suffix, tier in (("Ultra", "Ultra"), ("Max", "Max"), ("Pro", "Pro")):
        name = f"Apple M9 {suffix}"
        profile = get_soc_profile(name)
        expected = TIER_FALLBACKS[tier]
        assert profile.name == name  # preserves the reported chip name
        assert profile.cpu_chart_ref_w == expected.cpu_chart_ref_w
        assert profile.gpu_chart_ref_w == expected.gpu_chart_ref_w
        assert profile.cpu_max_bw == expected.cpu_max_bw
        assert profile.gpu_max_bw == expected.gpu_max_bw
        # LC-1: ANE reference power is a profile field that must survive the
        # tier-fallback copy (it is the denominator for ane_util_pct).
        assert profile.ane_max_w == expected.ane_max_w
        assert profile.ane_max_w > 0


def test_unknown_base_chip_falls_back_to_base_tier():
    profile = get_soc_profile("Apple M9")
    base = TIER_FALLBACKS["base"]
    assert profile.name == "Apple M9"
    assert profile.cpu_chart_ref_w == base.cpu_chart_ref_w
    assert profile.gpu_max_bw == base.gpu_max_bw


def test_non_apple_name_falls_back_to_generic_profile():
    profile = get_soc_profile("Some Unknown CPU")
    assert profile.name == "Some Unknown CPU"
    assert profile.cpu_chart_ref_w == GENERIC_APPLE_SILICON_PROFILE.cpu_chart_ref_w
    assert profile.gpu_max_bw == GENERIC_APPLE_SILICON_PROFILE.gpu_max_bw


def test_empty_name_falls_back_to_generic_apple_silicon():
    profile = get_soc_profile("")
    assert profile.name == "Apple Silicon"
    assert profile.cpu_chart_ref_w == GENERIC_APPLE_SILICON_PROFILE.cpu_chart_ref_w
