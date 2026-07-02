import subprocess
import sys

from actop.actop import build_parser


def test_cli_help_runs_and_exposes_show_cores_as_flag():
    result = subprocess.run(
        [sys.executable, "-m", "actop.actop", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "--show_cores" in result.stdout
    assert "--show_cores SHOW_CORES" not in result.stdout
    assert "--show-processes" in result.stdout
    assert "--proc-filter PROC_FILTER" in result.stdout
    assert "--alert-bw-sat-percent ALERT_BW_SAT_PERCENT" in result.stdout
    assert "--alert-package-power-percent ALERT_PACKAGE_POWER_PERCENT" in result.stdout
    assert "--alert-swap-rise-gb ALERT_SWAP_RISE_GB" in result.stdout
    assert "--alert-sustain-samples ALERT_SUSTAIN_SAMPLES" in result.stdout
    assert "--subsamples SUBSAMPLES" in result.stdout
    assert "--chart-glyph {dots,block}" in result.stdout
    assert "--layout {grid,stack}" in result.stdout


def test_cli_rejects_legacy_show_cores_value_form():
    result = subprocess.run(
        [sys.executable, "-m", "actop.actop", "--show_cores", "true"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "unrecognized arguments: true" in result.stderr


def test_cli_accepts_show_processes_flag():
    result = subprocess.run(
        [sys.executable, "-m", "actop.actop", "--show-processes", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0


def test_cli_show_processes_default_is_off():
    args = build_parser().parse_args([])
    assert args.show_processes is False


def test_cli_show_processes_flag_turns_on_panel():
    args = build_parser().parse_args(["--show-processes"])
    assert args.show_processes is True


def test_cli_chart_glyph_default_is_dots():
    args = build_parser().parse_args([])
    assert args.chart_glyph == "dots"


def test_cli_chart_glyph_accepts_block():
    args = build_parser().parse_args(["--chart-glyph", "block"])
    assert args.chart_glyph == "block"


def test_cli_layout_default_is_grid():
    args = build_parser().parse_args([])
    assert args.layout == "grid"


def test_cli_layout_accepts_stack():
    args = build_parser().parse_args(["--layout", "stack"])
    assert args.layout == "stack"


def test_cli_rejects_unknown_layout():
    result = subprocess.run(
        [sys.executable, "-m", "actop.actop", "--layout", "foo"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "invalid choice: 'foo'" in result.stderr


def test_cli_help_exposes_export_flags():
    result = subprocess.run(
        [sys.executable, "-m", "actop.actop", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "--json" in result.stdout
    assert "--serve PORT" in result.stdout


def test_cli_export_flags_have_inactive_defaults():
    args = build_parser().parse_args([])
    assert args.json is False
    assert args.serve is None


def test_cli_json_flag_sets_true():
    args = build_parser().parse_args(["--json"])
    assert args.json is True


def test_cli_serve_accepts_valid_port():
    args = build_parser().parse_args(["--serve", "9095"])
    assert args.serve == 9095


def test_cli_rejects_serve_port_out_of_range():
    result = subprocess.run(
        [sys.executable, "-m", "actop.actop", "--serve", "70000"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "port must be in the range 1-65535" in result.stderr


def test_cli_rejects_invalid_proc_filter_regex():
    result = subprocess.run(
        [sys.executable, "-m", "actop.actop", "--proc-filter", "["],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "invalid --proc-filter regex" in result.stderr


def test_cli_rejects_invalid_alert_bw_threshold():
    result = subprocess.run(
        [sys.executable, "-m", "actop.actop", "--alert-bw-sat-percent", "101"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "threshold must be in the range 1-100" in result.stderr


def test_cli_rejects_invalid_alert_swap_rise_value():
    result = subprocess.run(
        [sys.executable, "-m", "actop.actop", "--alert-swap-rise-gb", "-0.1"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "swap rise threshold must be >= 0" in result.stderr


def test_cli_rejects_invalid_alert_sustain_samples():
    result = subprocess.run(
        [sys.executable, "-m", "actop.actop", "--alert-sustain-samples", "0"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "sustain samples must be >= 1" in result.stderr


def test_cli_rejects_removed_max_count_flag():
    result = subprocess.run(
        [sys.executable, "-m", "actop.actop", "--max_count", "10"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "unrecognized arguments" in result.stderr


def test_cli_rejects_invalid_subsamples_value():
    result = subprocess.run(
        [sys.executable, "-m", "actop.actop", "--subsamples", "0"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "subsamples must be >= 1" in result.stderr


def test_cli_version_reports_package_version():
    from actop import __version__

    result = subprocess.run(
        [sys.executable, "-m", "actop.actop", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    # argparse prints --version to stdout; it must carry the real package version.
    assert __version__ in result.stdout
    assert __version__ != "dev"


def test_module_import_is_safe_with_unrelated_argv():
    script = (
        "import sys; "
        "sys.argv=['prog', '--not-a-real-flag']; "
        "import actop.actop; "
        "print('import-ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "import-ok" in result.stdout
