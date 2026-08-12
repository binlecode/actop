import argparse
import re

from actop import __version__


def build_parser():
    parser = argparse.ArgumentParser(
        description="actop: Performance monitoring CLI tool for Apple Silicon",
        # Surface every option's default in --help (appended as "(default: X)");
        # choices already render as "{a,b,c}", so together --help documents the
        # full supported value set + default for every argument.
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=2,
        help="Display and sampling interval in seconds",
    )
    parser.add_argument(
        "--avg", type=int, default=30, help="Interval for averaged values (seconds)"
    )
    parser.add_argument(
        "--subsamples",
        type=_validate_subsamples,
        default=1,
        help="Number of internal sampler deltas per interval (>=1)",
    )
    parser.add_argument(
        "--show_cores",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Show per-core panels inside the P-CPU/E-CPU boxes at startup "
        "(hidden by default; toggle live with the 'c' key)",
    )
    parser.add_argument(
        "--show-residency",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable per-cluster DVFS residency distribution rows "
        "(disable with --no-show-residency)",
    )
    parser.add_argument(
        "--power-scale",
        choices=["auto", "profile"],
        default="profile",
        help="Power chart scaling mode: profile uses SoC reference, auto uses rolling peak",
    )
    parser.add_argument(
        "--chart-glyph",
        choices=["dots", "block"],
        default="dots",
        help="Chart glyph style: dots (braille) or block (square)",
    )
    parser.add_argument(
        "--layout",
        choices=["grid", "stack"],
        default="grid",
        help=(
            "Dashboard layout preset: grid (two columns, fits short terminals, "
            "auto-falls-back to stack under ~96 cols) or stack (single column, "
            "longest chart history). Cycle live with the 'l' key."
        ),
    )
    parser.add_argument(
        "--palette",
        choices=["thermal", "viridis", "mono"],
        default="thermal",
        help=(
            "Chart color palette: thermal (blue->red), "
            "viridis (colorblind-safe), or mono (grayscale)"
        ),
    )
    parser.add_argument(
        "--theme",
        choices=[
            "textual-dark",
            "textual-light",
            "nord",
            "dracula",
            "tokyo-night",
            "monokai",
            "gruvbox",
            "catppuccin-mocha",
        ],
        default="textual-dark",
        help=(
            "Textual app theme for UI chrome (header, footer, borders, "
            "background). Orthogonal to --palette which controls chart "
            "gradient colors. Cycle live with the 't' key."
        ),
    )
    parser.add_argument(
        "--proc-filter",
        type=_validate_proc_filter,
        default="",
        help='Regex filter for process panel command names (example: "python|ollama|vllm|docker|mlx")',
    )
    parser.add_argument(
        "--show-processes",
        action="store_true",
        default=False,
        help="Show top process panel at startup",
    )
    parser.add_argument(
        "--alert-bw-sat-percent",
        type=_validate_percent_threshold,
        default=85,
        help="Bandwidth saturation alert threshold percent (1-100)",
    )
    parser.add_argument(
        "--alert-package-power-percent",
        type=_validate_percent_threshold,
        default=85,
        help="Package power alert threshold percent (1-100, profile-relative)",
    )
    parser.add_argument(
        "--alert-throttle-freq-percent",
        type=_validate_percent_threshold,
        default=90,
        help="Throttle alert: flag when a busy, hot cluster holds below this "
        "percent of its DVFS max frequency (1-100)",
    )
    parser.add_argument(
        # --alert-swap-rise-gb is the original spelling, kept as a working alias:
        # the threshold was always compared against GiB values, so the old name
        # was a misnomer, not a different unit. Removed in 2.0.0.
        "--alert-swap-rise-gib",
        "--alert-swap-rise-gb",
        dest="alert_swap_rise_gib",
        type=_validate_swap_rise_gib,
        default=0.3,
        help="Alert when swap rises by at least this many GiB over sustained "
        "samples (--alert-swap-rise-gb is a deprecated alias)",
    )
    parser.add_argument(
        "--alert-sustain-samples",
        type=_validate_sustain_samples,
        default=3,
        help="Consecutive samples required for sustained alerts",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Stream metrics as NDJSON to stdout instead of launching the TUI",
    )
    parser.add_argument(
        "--json-processes",
        action="store_true",
        default=False,
        help="With --json: include per-process rows in each NDJSON record",
    )
    parser.add_argument(
        "--serve",
        type=_validate_port,
        default=None,
        metavar="PORT",
        help="Serve Prometheus metrics on http://0.0.0.0:PORT/metrics (no TUI)",
    )
    parser.add_argument(
        "--serve-processes",
        action="store_true",
        default=False,
        help="With --serve: include per-process rows in Prometheus /metrics output",
    )
    parser.add_argument(
        "--samples",
        type=_validate_samples,
        default=0,
        metavar="N",
        help="With --json: emit N snapshot records then exit (0 = stream "
        "indefinitely until interrupted)",
    )
    return parser


def _validate_proc_filter(value):
    if value in (None, ""):
        return ""
    try:
        re.compile(value, re.IGNORECASE)
    except re.error as error:
        raise argparse.ArgumentTypeError(
            f"invalid --proc-filter regex: {error}"
        ) from error
    return value


def _validate_percent_threshold(value):
    try:
        threshold = int(value)
    except (TypeError, ValueError) as error:
        raise argparse.ArgumentTypeError("threshold must be an integer") from error
    if threshold < 1 or threshold > 100:
        raise argparse.ArgumentTypeError("threshold must be in the range 1-100")
    return threshold


def _validate_swap_rise_gib(value):
    try:
        swap_rise = float(value)
    except (TypeError, ValueError) as error:
        raise argparse.ArgumentTypeError(
            "swap rise threshold must be a number"
        ) from error
    if swap_rise < 0:
        raise argparse.ArgumentTypeError("swap rise threshold must be >= 0")
    return swap_rise


def _validate_sustain_samples(value):
    try:
        samples = int(value)
    except (TypeError, ValueError) as error:
        raise argparse.ArgumentTypeError(
            "sustain samples must be an integer"
        ) from error
    if samples < 1:
        raise argparse.ArgumentTypeError("sustain samples must be >= 1")
    return samples


def _validate_samples(value):
    try:
        samples = int(value)
    except (TypeError, ValueError) as error:
        raise argparse.ArgumentTypeError("samples must be an integer") from error
    if samples < 0:
        raise argparse.ArgumentTypeError("samples must be >= 0")
    return samples


def _validate_port(value):
    try:
        port = int(value)
    except (TypeError, ValueError) as error:
        raise argparse.ArgumentTypeError("port must be an integer") from error
    if port < 1 or port > 65535:
        raise argparse.ArgumentTypeError("port must be in the range 1-65535")
    return port


def _validate_subsamples(value):
    try:
        subsamples = int(value)
    except (TypeError, ValueError) as error:
        raise argparse.ArgumentTypeError("subsamples must be an integer") from error
    if subsamples < 1:
        raise argparse.ArgumentTypeError("subsamples must be >= 1")
    return subsamples


def _run_dashboard(args, runtime_state):
    from actop.tui.app import ActopApp

    app = ActopApp(args)
    app.run()


def _run_export(args):
    """Route to a non-TUI export backend. Returns an exit code."""
    from actop import export
    from actop.utils import get_soc_info

    interval_s = max(1, int(args.interval))
    subsamples = max(1, int(args.subsamples))
    include_processes = (
        bool(getattr(args, "show_processes", False))
        or bool(getattr(args, "json_processes", False))
        or bool(getattr(args, "serve_processes", False))
    )
    proc_filter = str(getattr(args, "proc_filter", "") or "")
    if not include_processes and proc_filter:
        include_processes = True

    # Resolve the SoC profile for the alert engine — the same path the TUI
    # takes. `get_soc_info()` is idempotent (cached native calls) so calling
    # it in the export path is zero-cost when the TUI already resolved it.
    soc_info = get_soc_info()
    cpu_chart_ref = float(soc_info.get("cpu_chart_ref_w", 30.0))
    gpu_chart_ref = float(soc_info.get("gpu_chart_ref_w", 30.0))
    ane_max_power = float(soc_info.get("ane_max_w", 8.0))
    max_total_bw = max(float(soc_info.get("max_mem_bw", 0.0)), 1.0)
    package_ref_w = max(cpu_chart_ref + gpu_chart_ref + ane_max_power, 1.0)

    alert_engine_kwargs = {
        "bw_sat_percent": int(getattr(args, "alert_bw_sat_percent", 85)),
        "pkg_power_percent": int(getattr(args, "alert_package_power_percent", 85)),
        "throttle_freq_percent": int(getattr(args, "alert_throttle_freq_percent", 90)),
        "swap_rise_gib": float(getattr(args, "alert_swap_rise_gib", 0.3)),
        "sustain_samples": int(getattr(args, "alert_sustain_samples", 3)),
        "max_total_bw": max_total_bw,
        "package_ref_w": package_ref_w,
    }

    try:
        if args.serve is not None:
            export.serve_prometheus(
                args.serve,
                interval_s,
                subsamples,
                include_processes=include_processes,
                proc_filter=proc_filter,
                alert_engine_kwargs=alert_engine_kwargs,
            )
        else:
            export.run_json_stream(
                interval_s,
                subsamples,
                max_samples=max(0, int(getattr(args, "samples", 0) or 0)),
                include_processes=include_processes,
                proc_filter=proc_filter,
                alert_engine_kwargs=alert_engine_kwargs,
            )
        return 0
    except KeyboardInterrupt:
        return 130


def main(args=None):
    if args is None:
        args = build_parser().parse_args()
    if getattr(args, "json", False) or getattr(args, "serve", None) is not None:
        return _run_export(args)
    runtime_state = {"monitor": None, "cursor_hidden": False}
    try:
        _run_dashboard(args, runtime_state)
        return 0
    except KeyboardInterrupt:
        print("Stopping...")
        return 130
    finally:
        monitor = runtime_state.get("monitor")
        if monitor is not None:
            monitor.close()
        if runtime_state["cursor_hidden"]:
            print("\033[?25h")


def cli(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return main(args)
    except Exception as e:
        print(e)
        return 1


if __name__ == "__main__":
    raise SystemExit(cli())
