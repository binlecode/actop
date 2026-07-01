import argparse
import re

from actop import __version__


def build_parser():
    parser = argparse.ArgumentParser(
        description="actop: Performance monitoring CLI tool for Apple Silicon"
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
        default=True,
        help="Enable per-core panels (disable with --no-show_cores)",
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
        "--proc-filter",
        type=_validate_proc_filter,
        default="",
        help='Regex filter for process panel command names (example: "python|ollama|vllm|docker|mlx")',
    )
    parser.add_argument(
        "--show-processes",
        action="store_true",
        default=False,
        help="Show top process panel at startup (default: off)",
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
        "--alert-swap-rise-gb",
        type=_validate_swap_rise_gb,
        default=0.3,
        help="Alert when swap rises by at least this many GB over sustained samples",
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
        "--serve",
        type=_validate_port,
        default=None,
        metavar="PORT",
        help="Serve Prometheus metrics on http://0.0.0.0:PORT/metrics (no TUI)",
    )
    return parser


def _validate_proc_filter(value):
    if value in (None, ""):
        return ""
    try:
        re.compile(value, re.IGNORECASE)
    except re.error as error:
        raise argparse.ArgumentTypeError(
            "invalid --proc-filter regex: {}".format(error)
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


def _validate_swap_rise_gb(value):
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

    interval_s = max(1, int(args.interval))
    subsamples = max(1, int(args.subsamples))
    try:
        if args.serve is not None:
            export.serve_prometheus(args.serve, interval_s, subsamples)
        else:
            export.run_json_stream(interval_s, subsamples)
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
