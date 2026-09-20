import argparse
import re
import sys
import time

from actop import __version__


def build_parser():
    parser = argparse.ArgumentParser(
        description="actop: Apple Silicon performance & energy monitor (Agent-first CLI & ASCII view)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        default=False,
        help="Emit single-line high-density JSON object for AI agents and exit",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        default=False,
        help="Print a single ASCII snapshot and exit (like nvidia-smi)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="Display and sampling interval in seconds",
    )
    parser.add_argument(
        "--subsamples",
        type=_validate_subsamples,
        default=1,
        help="Number of internal sampler deltas per interval (>=1)",
    )
    parser.add_argument(
        "--proc-filter",
        type=_validate_proc_filter,
        default="",
        help='Regex filter for process panel command names (example: "python|ollama|vllm|docker|mlx")',
    )
    parser.add_argument(
        "-p",
        "--show-processes",
        action="store_true",
        default=False,
        help="Include top power-consuming processes (disabled by default to minimize overhead)",
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
        "--alert-swap-rise-gib",
        dest="alert_swap_rise_gib",
        type=_validate_swap_rise_gib,
        default=0.3,
        help="Alert when swap rises by at least this many GiB over sustained samples",
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
    from actop.api import Monitor
    from actop.ascii_view import print_ascii_screen
    from actop.utils import get_soc_info

    # For single-shot execution (--once or non-tty), use a fast 0.2s warmup unless --interval was specified
    is_single_shot = bool(getattr(args, "once", False) or not sys.stdout.isatty())
    interval_s = (
        float(args.interval)
        if ("--interval" in sys.argv or not is_single_shot)
        else 0.2
    )
    subsamples = max(1, int(args.subsamples))
    proc_filter = str(getattr(args, "proc_filter", "") or "")
    include_processes = bool(getattr(args, "show_processes", False)) or bool(
        proc_filter
    )

    soc_info = get_soc_info()
    monitor = Monitor(
        interval_s=interval_s,
        subsamples=subsamples,
        include_processes=include_processes,
        process_filter=proc_filter,
    )
    runtime_state["monitor"] = monitor

    # Collect initial sample
    snapshot = monitor.get_snapshot()

    # Single-shot mode: --once or non-interactive pipe
    if is_single_shot:
        print_ascii_screen(
            snapshot, soc_info, snapshot.processes, max_processes=10, clear=False
        )
        return

    # Continuous watch loop
    max_samples = max(0, int(getattr(args, "samples", 0) or 0))
    sample_count = 0
    print_ascii_screen(
        snapshot, soc_info, snapshot.processes, max_processes=10, clear=False
    )
    sample_count += 1
    if max_samples > 0 and sample_count >= max_samples:
        return

    loop_interval = max(0.5, float(args.interval))
    while True:
        time.sleep(loop_interval)
        snapshot = monitor.get_snapshot()
        print_ascii_screen(
            snapshot, soc_info, snapshot.processes, max_processes=10, clear=True
        )
        sample_count += 1
        if max_samples > 0 and sample_count >= max_samples:
            break


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

    # Resolve the SoC profile for the alert engine. `get_soc_info()` returns
    # pre-computed `package_ref_w` / `max_total_bw` — single source of truth,
    # shared with config.py.
    soc_info = get_soc_info()
    package_ref_w = float(soc_info["package_ref_w"])
    max_total_bw = float(soc_info["max_total_bw"])

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

    # Agent-first one-shot compact JSON
    if getattr(args, "compact", False):
        from actop.api import Monitor
        from actop.ascii_view import format_compact_json
        from actop.utils import get_soc_info

        soc_info = get_soc_info()
        proc_filter = str(getattr(args, "proc_filter", "") or "")
        include_processes = bool(getattr(args, "show_processes", False)) or bool(
            proc_filter
        )
        # Fast 0.2s snapshot unless explicitly overridden with --interval
        sample_interval = float(args.interval) if "--interval" in sys.argv else 0.2
        monitor = Monitor(
            interval_s=sample_interval,
            subsamples=max(1, int(args.subsamples)),
            include_processes=include_processes,
            process_filter=proc_filter,
        )
        try:
            snapshot = monitor.get_snapshot()
            print(format_compact_json(snapshot, soc_info, snapshot.processes))
            return 0
        finally:
            monitor.close()

    if getattr(args, "json", False) or getattr(args, "serve", None) is not None:
        return _run_export(args)
    runtime_state = {"monitor": None, "cursor_hidden": False}
    try:
        _run_dashboard(args, runtime_state)
        return 0
    except KeyboardInterrupt:
        return 130
    finally:
        monitor = runtime_state.get("monitor")
        if monitor is not None:
            monitor.close()


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
