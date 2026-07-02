"""Textual TUI application for actop."""

import os
import re
import threading

from textual.app import App, ComposeResult
from textual import work
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Input, Static

from actop import __version__
from actop.api import Monitor
from actop.config import create_dashboard_config
from actop.tui.widgets import HardwareDashboard, MetricsUpdated
from actop.utils import (
    attribute_power,
    get_soc_info,
    get_top_processes,
)

_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

SORT_CPU = "cpu"
SORT_POWER = "power"
SORT_MEMORY = "memory"
SORT_PID = "pid"
SORT_LABELS = {
    SORT_CPU: "CPU%",
    SORT_POWER: "PWR",
    SORT_MEMORY: "RSS",
    SORT_PID: "PID",
}

_SORT_CYCLE = [SORT_CPU, SORT_POWER, SORT_MEMORY, SORT_PID]


def sort_processes(process_metrics, sort_mode, limit, cpu_watts=0.0, gpu_watts=0.0):
    """Return a sorted process list based on the active sort mode.

    cpu_watts/gpu_watts are only used by SORT_POWER: ordering by attributed
    watts isn't the same as ordering by cpu_time_share alone once GPU is
    involved (a process can have a high GPU share and a low CPU share, and
    cpu_watts/gpu_watts differ in magnitude), so the actual watts values are
    needed, not just the CPU-time proxy.
    """
    if sort_mode == SORT_MEMORY:
        return process_metrics.get("memory", [])[:limit]
    elif sort_mode == SORT_PID:
        cpu_list = list(process_metrics.get("cpu", []))
        cpu_list.sort(key=lambda proc: proc.get("pid", 0))
        return cpu_list[:limit]
    elif sort_mode == SORT_POWER:
        # PWR is attributed CPU+GPU watts; sort explicitly so the label is
        # honest (processes with no delta yet in either domain sink to the
        # bottom).
        cpu_list = list(process_metrics.get("cpu", []))
        cpu_list.sort(
            key=lambda proc: attribute_power(
                proc.get("cpu_time_share"),
                proc.get("gpu_time_share"),
                cpu_watts,
                gpu_watts,
            ),
            reverse=True,
        )
        return cpu_list[:limit]
    else:
        # Default: CPU sort (already sorted by get_top_processes)
        return process_metrics.get("cpu", [])[:limit]


def _shorten_process_command(command, max_len=30):
    """Truncate a process command string with ellipsis if too long."""
    if command is None:
        return "?"
    command = str(command).strip()
    if not command:
        return "?"
    if len(command) <= max_len:
        return command
    return command[: max_len - 3] + "..."


def _process_display_name(command, max_len=24):
    """Extract a short display name from a process command string."""
    command = str(command or "").strip()
    if not command:
        return "?"
    app_match = re.search(r"([^/]+)\.app(?:/| |$)", command)
    if app_match:
        return _shorten_process_command(app_match.group(1), max_len=max_len)
    executable = command.split(" ", 1)[0]
    executable_name = os.path.basename(executable) or executable
    return _shorten_process_command(executable_name, max_len=max_len)


HELP_TEXT = """\
[b]actop — keybindings[/b]

  q          Quit
  p          Pause / resume sampling
  s          Cycle process sort (CPU% → PWR → RSS → PID)
  g          Toggle chart glyph (braille dots / blocks)
  t          Toggle the process table
  /          Filter processes by regex (when table shown)
  ?          Show / hide this help
  esc        Cancel filter / close help

[b]Metric labels[/b]

  P-CPU / E-CPU   Performance / Efficiency core cluster: util% @freq, die °C
  GPU             GPU util% @freq, die °C
  ANE             Apple Neural Engine util% (estimated) and power
  RAM             Used / total memory (and swap when active)
  Mem BW          Unified-memory bandwidth in GB/s (hidden if unavailable)
  CPU/GPU Power   Live package-rail power draw in watts
  Package Power   Total SoC power draw in watts (CPU + GPU + ANE + other rails)
  idle/low/mid/high  DVFS residency: % of time since the last sample spent
             idle vs. below 40% / 40-74% / ≥75% of the cluster's max
             frequency (not just the instantaneous clock). Hidden with
             --no-show-residency

[b]Process table[/b]

  CPU%       Per-process CPU utilization (Δ CPU-time over the interval)
  PWR        Estimated per-process CPU+GPU power: the process's share of
             total CPU-time × package CPU watts, plus its share of total
             GPU-time (from Metal command-queue usage) × package GPU watts.
             Not ANE. An estimate: a P-core-second draws more than an
             E-core-second, so E-core-bound work is over-attributed and vice
             versa. "–" means no CPU reading yet (first sample after launch
             or resume).
  Σ shown    Reconciliation token below the table: watts the visible rows
             account for vs total package CPU watts (a partition of it).

  avg · max       Rolling average (over the --avg window) and session peak,
                  shown next to each live reading.

[b]Status line[/b]

  span Ns    Visible chart time window (one sample per column × --interval);
             it scales with terminal width, so widen the window to see further back.
  energy     Cumulative session energy (∫ package power dt since launch), in
             mWh / Wh — the "what did this run cost" figure.
  THERMAL    Thermal pressure above Nominal (Fair / Serious / Critical)
  THROTTLING:CPU/GPU  A busy, hot cluster is held below its DVFS max
             frequency right now (you are losing performance to heat)
  MEM-BOUND>N%  Memory bandwidth sustained above N% of SoC capacity
             (you are memory-bandwidth-bound)
  PKG>N%     Package power sustained above N% of the SoC reference
  SWAP+N.NG  Swap grew by N.N GB over the sustain window

Alerts fire only after the threshold holds for --alert-sustain-samples frames.
Chart colors degrade to 256/16-color terminals and honor NO_COLOR.
"""


class HelpScreen(ModalScreen):
    """Modal overlay documenting keybindings, metrics, and alert tokens."""

    BINDINGS = [
        ("escape", "close", "Close"),
        ("question_mark", "close", "Close"),
        ("q", "close", "Close"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="help-dialog"):
            yield Static(HELP_TEXT, id="help-body")

    def action_close(self) -> None:
        self.dismiss()


class ActopApp(App):
    ENABLE_COMMAND_PALETTE = False
    DEFAULT_CSS = """
    ActopApp {
        layout: vertical;
    }
    #main-section {
        height: 1fr;
    }
    HardwareDashboard {
        width: 1fr;
        height: 1fr;
        overflow-y: auto;
        layout: vertical;
        border: round $accent;
        padding: 0 1;
    }
    .metric-label {
        height: 1;
        color: $text-muted;
    }
    .metric-chart {
        height: 2;
    }
    #pcpu-chart {
        height: 4;
    }
    #ecpu-chart {
        height: 4;
    }
    #ram-chart {
        height: 4;
    }
    .status-line {
        height: 1;
        color: $text-muted;
    }
    .cpu-summary-row {
        height: 1;
        color: $text-muted;
    }
    .residency-row {
        height: 1;
        color: $text-muted;
    }
    .core-grid {
        height: auto;
    }
    #cpu-section {
        height: auto;
    }
    .cpu-half {
        height: auto;
    }
    #process-table {
        width: 1fr;
        height: 1fr;
        border: round $accent;
    }
    #loading-splash {
        height: 1fr;
        content-align: center middle;
        color: $accent;
        border: round $accent;
    }
    HelpScreen {
        align: center middle;
        background: $background 60%;
    }
    #help-dialog {
        width: auto;
        max-width: 90%;
        height: auto;
        max-height: 90%;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }
    #help-body {
        width: auto;
        height: auto;
    }
    """
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("p", "toggle_pause", "Pause"),
        ("s", "cycle_sort", "Sort"),
        ("g", "toggle_chart_glyph", "Glyph"),
        ("t", "toggle_processes", "Processes"),
        ("/", "toggle_filter", "Filter"),
        ("question_mark", "show_help", "Help"),
        Binding("escape", "cancel_filter", "Cancel filter", show=False),
    ]

    def __init__(self, args) -> None:
        super().__init__()
        self._config = create_dashboard_config(args, get_soc_info())
        self._chip_name = self._config.chip_name
        self.title = "actop"
        g = self._config.gpu_core_count
        topo = f"{self._config.e_core_count}E+{self._config.p_core_count}P"
        if g:
            topo += f"+{g}GPU"
        self.sub_title = f"v{__version__} · {self._chip_name} · {topo}"
        self._stop_polling = threading.Event()
        self._sort_mode = SORT_CPU
        self._filter_regex = self._config.process_filter_pattern
        self._filter_regex_before_edit = self._config.process_filter_pattern
        self._filter_text_before_edit = ""
        self._last_processes = {"cpu": [], "memory": []}
        self._last_cpu_watts = 0.0
        self._last_gpu_watts = 0.0
        self._show_processes = bool(self._config.show_processes)
        self._splash_frame = 0
        self._sampler_ready = False
        self._splash_timer = None
        self._last_sort_mode = None

    def _build_splash(self) -> str:
        cfg = self._config
        return (
            f"actop v{__version__}\n\n"
            f"{self._chip_name}\n"
            f"E-cores: {cfg.e_core_count}   P-cores: {cfg.p_core_count}\n"
            f"interval: {cfg.sample_interval}s   subsamples: {cfg.subsamples}\n\n"
            f"{_SPINNER_FRAMES[self._splash_frame]} Initializing sampler…"
        )

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(self._build_splash(), id="loading-splash")
        with Horizontal(id="main-section"):
            yield HardwareDashboard(config=self._config, id="hardware-dash")
            yield DataTable(id="process-table", zebra_stripes=True, cursor_type="row")
        filter_input = Input(placeholder="Regex filter...", id="filter-input")
        filter_input.display = False
        yield filter_input
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#main-section").display = False
        self.query_one("#process-table", DataTable).display = self._show_processes
        self._refresh_process_table()  # initialises columns without advancing sort
        self._splash_timer = self.set_interval(0.1, self._tick_splash)
        self.poll_metrics()

    @work(thread=True, exclusive=True)
    def poll_metrics(self) -> None:
        monitor = Monitor(self._config.sample_interval, self._config.subsamples)
        try:
            while not self._stop_polling.is_set():
                snapshot = monitor.get_snapshot()
                if self._show_processes:
                    processes = get_top_processes(
                        limit=self._config.process_display_count,
                        proc_filter=self._filter_regex,
                    )
                else:
                    processes = {"cpu": [], "memory": []}
                self.post_message(MetricsUpdated(snapshot, processes))
        finally:
            monitor.close()

    def on_unmount(self) -> None:
        self._stop_polling.set()

    def _tick_splash(self) -> None:
        self._splash_frame = (self._splash_frame + 1) % len(_SPINNER_FRAMES)
        self.query_one("#loading-splash", Static).update(self._build_splash())

    def on_metrics_updated(self, message: MetricsUpdated) -> None:
        if not self._sampler_ready:
            self._sampler_ready = True
            self._splash_timer.stop()
            self.query_one("#loading-splash").display = False
            self.query_one("#main-section").display = True
        self.query_one("#hardware-dash", HardwareDashboard).update_metrics(message)
        self._last_processes = message.processes
        self._last_cpu_watts = message.snapshot.cpu_watts
        self._last_gpu_watts = message.snapshot.gpu_watts
        self._refresh_process_table()

    def action_toggle_pause(self) -> None:
        if self._stop_polling.is_set():
            self._stop_polling.clear()
            self.poll_metrics()
        else:
            self._stop_polling.set()

    def action_cycle_sort(self) -> None:
        idx = (_SORT_CYCLE.index(self._sort_mode) + 1) % len(_SORT_CYCLE)
        self._sort_mode = _SORT_CYCLE[idx]
        self._refresh_process_table()

    def action_show_help(self) -> None:
        if isinstance(self.screen, HelpScreen):
            self.pop_screen()
        else:
            self.push_screen(HelpScreen())

    def action_toggle_chart_glyph(self) -> None:
        dash = self.query_one("#hardware-dash", HardwareDashboard)
        next_mode = "block" if dash.chart_glyph == "dots" else "dots"
        dash.set_chart_glyph(next_mode)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "toggle_filter":
            # The regex filter only applies to the process table; when the table
            # is hidden the binding is hidden + inert (False), reappearing on `t`.
            return self._show_processes
        return True

    def action_toggle_processes(self) -> None:
        table = self.query_one("#process-table", DataTable)
        self._show_processes = not self._show_processes
        table.display = self._show_processes
        self._refresh_process_table()
        # Re-evaluate check_action so the footer shows/hides `/  Filter` at once.
        self.refresh_bindings()

    def _close_filter_input(self, inp) -> None:
        inp.display = False
        if self._show_processes:
            self.set_focus(self.query_one("#process-table", DataTable))
        else:
            self.set_focus(self.query_one("#hardware-dash", HardwareDashboard))

    def action_toggle_filter(self) -> None:
        if not self._show_processes:
            return  # filter applies only to the process table; no-op while hidden
        inp = self.query_one("#filter-input", Input)
        if inp.display:
            self._close_filter_input(inp)
        else:
            self._filter_regex_before_edit = self._filter_regex
            self._filter_text_before_edit = inp.value
            inp.display = True
            inp.focus()

    def action_cancel_filter(self) -> None:
        inp = self.query_one("#filter-input", Input)
        if not inp.display:
            return  # esc does nothing outside filter mode
        # Discard the in-progress edit and revert the live-applied filter to the
        # value active before the field was opened. Setting inp.value re-runs
        # on_input_changed (recomputing _filter_regex from the restored text);
        # the explicit assignment that follows keeps the intent unambiguous.
        inp.value = self._filter_text_before_edit
        self._filter_regex = self._filter_regex_before_edit
        self._close_filter_input(inp)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "filter-input":
            val = event.value.strip()
            if val:
                try:
                    self._filter_regex = re.compile(val, re.IGNORECASE)
                except re.error:
                    pass
            else:
                self._filter_regex = self._config.process_filter_pattern
            self._close_filter_input(event.input)
            # _filter_regex is read by the polling loop on each iteration;
            # no need to restart the worker.

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter-input":
            val = event.value.strip()
            if val:
                try:
                    self._filter_regex = re.compile(val, re.IGNORECASE)
                except re.error:
                    pass
            else:
                self._filter_regex = self._config.process_filter_pattern

    def _refresh_process_table(self) -> None:
        try:
            table = self.query_one("#process-table", DataTable)
        except Exception:
            return

        if not self._show_processes:
            table.clear()
            return

        # Rebuild columns only when sort mode changes
        if self._sort_mode != self._last_sort_mode:
            self._last_sort_mode = self._sort_mode
            table.clear(columns=True)
            cols = ["PID", "Command", "CPU%", "PWR", "MEM (MB)", "Threads"]
            if self._sort_mode == SORT_PID:
                cols[0] = "*PID"
            elif self._sort_mode == SORT_CPU:
                cols[2] = "*CPU%"
            elif self._sort_mode == SORT_POWER:
                cols[3] = "*PWR"
            elif self._sort_mode == SORT_MEMORY:
                cols[4] = "*MEM (MB)"
            table.add_columns(*cols)
        else:
            table.clear()

        try:
            # -1 for the header row; fall back to config if not yet laid out
            limit = max(5, table.content_size.height - 1)
        except Exception:
            limit = self._config.process_display_count
        cpu_watts = self._last_cpu_watts
        gpu_watts = self._last_gpu_watts
        sorted_procs = sort_processes(
            self._last_processes, self._sort_mode, limit, cpu_watts, gpu_watts
        )
        shown_pwr = 0.0
        for proc in sorted_procs:
            # PWR is a CPU+GPU time-share partition of package watts, computed
            # here because the TUI owns cpu_watts/gpu_watts. CPU is the
            # primary signal (every process eventually gets a cpu_time_share;
            # gpu_time_share is 0.0, not None, for the common case of a
            # process that never touches the GPU) -- "–" triggers on a
            # pending first CPU sample alone. A pending GPU sample (share_gpu
            # is None because a brand-new Metal client has no delta yet)
            # just contributes 0 for this tick via attribute_power rather
            # than blanking an otherwise-known CPU wattage.
            share_cpu = proc.get("cpu_time_share")
            share_gpu = proc.get("gpu_time_share")
            if share_cpu is None:
                pwr_cell = "–"
            else:
                pwr_w = attribute_power(share_cpu, share_gpu, cpu_watts, gpu_watts)
                shown_pwr += pwr_w
                pwr_cell = "{:.2f}W".format(pwr_w)
            table.add_row(
                str(proc.get("pid", "")),
                _process_display_name(proc.get("command", ""), max_len=28),
                "{:.1f}".format(proc.get("cpu_percent", 0.0) or 0.0),
                pwr_cell,
                "{:.1f}".format(proc.get("rss_mb", 0.0) or 0.0),
                str(proc.get("num_threads", "")),
            )

        # Reconciliation token: how much of package CPU+GPU power the visible
        # rows account for. Σ over *all* PIDs equals cpu_watts + gpu_watts by
        # construction; the shown subset is a lower bound. Flagged an
        # estimate (P/E-core skew, CPU+GPU — not ANE).
        if cpu_watts > 0 or gpu_watts > 0:
            table.border_subtitle = (
                "Σ shown {:.1f}W / pkg CPU+GPU {:.1f}W · est CPU+GPU time share".format(
                    shown_pwr, cpu_watts + gpu_watts
                )
            )
        else:
            table.border_subtitle = ""
