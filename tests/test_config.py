"""Functional tests for the args -> DashboardConfig merge.

``create_dashboard_config`` is the single point where parsed CLI flags and live
SoC info are combined into the immutable config the whole dashboard reads from.
These exercise the real parse -> soc_info -> config flow (no synthetic inputs);
a regression here silently misconfigures every gauge, alert, and chart.

Uses real ``get_soc_info`` (native sysctl), so marked local.
"""

import re

import pytest

from actop.actop import build_parser
from actop.config import DashboardConfig, create_dashboard_config
from actop.utils import get_soc_info

pytestmark = pytest.mark.local


def _make_config(argv):
    args = build_parser().parse_args(argv)
    return create_dashboard_config(args, get_soc_info())


def test_defaults_produce_consistent_config():
    cfg = _make_config([])

    assert isinstance(cfg, DashboardConfig)
    assert cfg.sample_interval >= 1
    assert cfg.avg_window >= 1
    assert cfg.power_scale == "profile"
    assert cfg.chart_glyph == "dots"
    assert cfg.show_processes is False
    assert cfg.process_filter_pattern is None
    assert cfg.subsamples >= 1
    assert cfg.alert_sustain_samples >= 1
    assert cfg.e_core_count >= 0
    assert cfg.p_core_count >= 0
    # LC-1: display identity now flows through the config (the TUI no longer
    # reaches into soc_info directly), so it must survive the real merge.
    assert isinstance(cfg.chip_name, str) and cfg.chip_name
    assert cfg.gpu_core_count >= 0


def test_package_ref_combines_cpu_gpu_and_ane_headroom():
    cfg = _make_config([])

    # Documented composition: package reference = cpu + gpu chart refs + ANE max.
    assert cfg.package_ref_w == pytest.approx(
        cfg.cpu_chart_ref_w + cfg.gpu_chart_ref_w + cfg.ane_max_power
    )
    assert cfg.package_ref_w >= cfg.cpu_chart_ref_w
    assert cfg.max_cpu_bw >= 1.0 and cfg.max_gpu_bw >= 1.0


def test_proc_filter_is_compiled_into_usable_regex():
    cfg = _make_config(["--proc-filter", "python|ollama"])

    assert isinstance(cfg.process_filter_pattern, re.Pattern)
    assert cfg.process_filter_pattern.search("PYTHON3.12")  # case-insensitive
    assert not cfg.process_filter_pattern.search("finder")


def test_flags_propagate_into_config():
    cfg = _make_config(
        [
            "--interval",
            "2",
            "--avg",
            "60",
            "--power-scale",
            "auto",
            "--chart-glyph",
            "block",
            "--show-processes",
            "--subsamples",
            "4",
            "--alert-sustain-samples",
            "5",
        ]
    )

    assert cfg.sample_interval == 2
    assert cfg.avg_window == 30  # avg / interval
    assert cfg.power_scale == "auto"
    assert cfg.chart_glyph == "block"
    assert cfg.show_processes is True
    assert cfg.subsamples == 4
    assert cfg.alert_sustain_samples == 5


def test_config_is_immutable():
    cfg = _make_config([])
    with pytest.raises(Exception):
        cfg.sample_interval = 999
