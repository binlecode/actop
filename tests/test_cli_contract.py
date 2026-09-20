import subprocess
import sys

from actop.actop import build_parser


def test_cli_help_runs_and_exposes_agent_flags():
    result = subprocess.run(
        [sys.executable, "-m", "actop.actop", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "--compact" in result.stdout
    assert "--once" in result.stdout
    assert "--proc-list" in result.stdout
    assert "--proc-filter PROC_FILTER" in result.stdout
    assert "--alert-bw-sat-percent ALERT_BW_SAT_PERCENT" in result.stdout
    assert "--alert-package-power-percent ALERT_PACKAGE_POWER_PERCENT" in result.stdout
    assert "--alert-swap-rise-gib ALERT_SWAP_RISE_GIB" in result.stdout
    assert "--alert-sustain-samples ALERT_SUSTAIN_SAMPLES" in result.stdout
    assert "--subsamples SUBSAMPLES" in result.stdout
    assert "--json" in result.stdout
    assert "--serve PORT" in result.stdout
    # Dead TUI options must not appear
    assert "--theme" not in result.stdout
    assert "--palette" not in result.stdout
    assert "--layout" not in result.stdout
    assert "--chart-glyph" not in result.stdout


def test_cli_rejects_removed_swap_rise_gb_alias():
    result = subprocess.run(
        [sys.executable, "-m", "actop.actop", "--alert-swap-rise-gb", "1.5"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "unrecognized arguments" in result.stderr


def test_cli_rejects_dead_tui_options():
    # Dead TUI options are cleanly rejected as unrecognized arguments
    for opt in ("--theme", "--palette", "--layout", "--chart-glyph"):
        result = subprocess.run(
            [sys.executable, "-m", "actop.actop", opt, "foo"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 2
        assert "unrecognized arguments" in result.stderr


def test_cli_accepts_compact_and_once():
    parser = build_parser()
    args = parser.parse_args(["--compact"])
    assert args.compact is True
    assert args.once is False

    args_once = parser.parse_args(["--once"])
    assert args_once.once is True
    assert args_once.compact is False


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
        [sys.executable, "-m", "actop.actop", "--alert-swap-rise-gib", "-0.1"],
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
