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
    assert "--alert-swap-rise-gib ALERT_SWAP_RISE_GIB" in result.stdout
    assert "--alert-sustain-samples ALERT_SUSTAIN_SAMPLES" in result.stdout
    assert "--subsamples SUBSAMPLES" in result.stdout
    assert "--chart-glyph {dots,block}" in result.stdout
    assert "--layout {grid,stack}" in result.stdout
    assert "--palette {thermal,viridis,mono}" in result.stdout
    assert "--theme " in result.stdout
    assert "textual-dark" in result.stdout
    assert "--json" in result.stdout
    assert "--serve PORT" in result.stdout
    # --help surfaces every option's default (ArgumentDefaultsHelpFormatter), so
    # the supported value set + default is documented for all args.
    assert "(default:" in result.stdout
    assert "(default: 2)" in result.stdout  # --interval


def test_cli_rejects_removed_swap_rise_gb_alias():
    # --alert-swap-rise-gb was the deprecated alias for --alert-swap-rise-gib
    # (the threshold was always compared against GiB, so the old name was a
    # misnomer, not a different unit). It rode one release cycle as a working
    # alias and was removed in 1.8.0.
    result = subprocess.run(
        [sys.executable, "-m", "actop.actop", "--alert-swap-rise-gb", "1.5"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "unrecognized arguments" in result.stderr


def test_cli_rejects_legacy_show_cores_value_form():
    result = subprocess.run(
        [sys.executable, "-m", "actop.actop", "--show_cores", "true"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "unrecognized arguments: true" in result.stderr


def test_cli_rejects_unknown_layout():
    result = subprocess.run(
        [sys.executable, "-m", "actop.actop", "--layout", "foo"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "invalid choice: 'foo'" in result.stderr


def test_cli_rejects_unknown_palette():
    result = subprocess.run(
        [sys.executable, "-m", "actop.actop", "--palette", "rainbow"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "invalid choice: 'rainbow'" in result.stderr


def test_cli_rejects_unknown_theme():
    result = subprocess.run(
        [sys.executable, "-m", "actop.actop", "--theme", "nonexistent"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "invalid choice: 'nonexistent'" in result.stderr


def test_cli_accepts_known_themes():
    parser = build_parser()
    for theme in (
        "textual-dark",
        "textual-light",
        "nord",
        "dracula",
        "tokyo-night",
        "monokai",
        "gruvbox",
        "catppuccin-mocha",
    ):
        args = parser.parse_args(["--theme", theme])
        assert args.theme == theme
    # Default when not specified
    args = parser.parse_args([])
    assert args.theme == "textual-dark"


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
