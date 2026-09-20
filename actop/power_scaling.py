"""Power chart scaling functions (legacy module, logic consolidated in actop.analytics)."""

from actop.analytics import (
    DEFAULT_AUTO_MULTIPLIER,
    DEFAULT_CPU_FLOOR_W,
    DEFAULT_GPU_FLOOR_W,
    clamp_percent,
    power_to_percent,
    resolve_power_denominator,
)

__all__ = [
    "DEFAULT_AUTO_MULTIPLIER",
    "DEFAULT_CPU_FLOOR_W",
    "DEFAULT_GPU_FLOOR_W",
    "clamp_percent",
    "power_to_percent",
    "resolve_power_denominator",
]
