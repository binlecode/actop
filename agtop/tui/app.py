"""Textual TUI application for agtop."""

import os
import re
import threading

from textual.app import App, ComposeResult
from textual import work
from textual.containers import Horizontal
from textual.widgets import DataTable, Footer, Header, Input, Static

from agtop import __version__
from agtop.api import Monitor
from agtop.config import create_dashboard_config
from agtop.tui.widgets import HardwareDashboard, MetricsUpdated
from agtop.utils import get_ram_metrics_dict, get_soc_info, get_top_processes

_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

SORT_CPU = "cpu"
SORT_MEMORY = "memory"
SORT_PID = "pid"
SORT_LABELS = {SORT_CPU: "CPU%", SORT_MEMORY: "RSS", SORT_PID: "PID"}

_SORT_CYCLE = [SORT_CPU, SORT_MEMORY, SORT_PID]


def sort_processes(process_metrics, sort_mode, limit):
    """Return a sorted process list based on the active sort mode."""
    if sort_mode == SORT_MEMORY:
        return process_metrics.get("memory", [])[:limit]
    elif sort_mode == SORT_PID:
        cpu_list = list(process_metrics.get("cpu", []))
        cpu_list.sort(key=lambda proc: proc.get("pid", 0))
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


class AgtopApp(App):
    ENABLE_COMMAND_PALETTE = False
    DEFAULT_CSS = """
    AgtopApp {
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
    """
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("p", "toggle_pause", "Pause"),
        ("s", "cycle_sort", "Sort"),
        ("g", "toggle_chart_glyph", "Glyph"),
        ("t", "toggle_processes", "Processes"),
        ("/", "toggle_filter", "Filter"),
    ]

    def __init__(self, args) -> None:
        super().__init__()
        soc_info = get_soc_info()
        self._config = create_dashboard_config(args, soc_info)
        self._chip_name = soc_info.get("name", "Apple Silicon")
        self.title = "agtop"
        self.sub_title = f"v{__version__} · {self._chip_name}"
        self._stop_polling = threading.Event()
        self._sort_mode = SORT_CPU
        self._filter_regex = self._config.process_filter_pattern
        self._last_processes = {"cpu": [], "memory": []}
        self._show_processes = bool(self._config.show_processes)
        self._splash_frame = 0
        self._sampler_ready = False
        self._splash_timer = None
        self._last_sort_mode = None

    def _build_splash(self) -> str:
        cfg = self._config
        return (
            f"agtop v{__version__}\n\n"
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
                ram = get_ram_metrics_dict()
                if self._show_processes:
                    processes = get_top_processes(
                        limit=self._config.process_display_count,
                        proc_filter=self._filter_regex,
                    )
                else:
                    processes = {"cpu": [], "memory": []}
                self.post_message(MetricsUpdated(snapshot, ram, processes))
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

    def action_toggle_chart_glyph(self) -> None:
        dash = self.query_one("#hardware-dash", HardwareDashboard)
        next_mode = "block" if dash.chart_glyph == "dots" else "dots"
        dash.set_chart_glyph(next_mode)

    def action_toggle_processes(self) -> None:
        table = self.query_one("#process-table", DataTable)
        self._show_processes = not self._show_processes
        table.display = self._show_processes
        self._refresh_process_table()

    def action_toggle_filter(self) -> None:
        inp = self.query_one("#filter-input", Input)
        if inp.display:
            inp.display = False
            if self._show_processes:
                self.set_focus(self.query_one("#process-table", DataTable))
            else:
                self.set_focus(self.query_one("#hardware-dash", HardwareDashboard))
        else:
            inp.display = True
            inp.focus()

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
            event.input.display = False
            if self._show_processes:
                self.set_focus(self.query_one("#process-table", DataTable))
            else:
                self.set_focus(self.query_one("#hardware-dash", HardwareDashboard))
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
            cols = ["PID", "Command", "CPU%", "MEM (MB)", "Threads"]
            if self._sort_mode == SORT_PID:
                cols[0] = "*PID"
            elif self._sort_mode == SORT_CPU:
                cols[2] = "*CPU%"
            elif self._sort_mode == SORT_MEMORY:
                cols[3] = "*MEM (MB)"
            table.add_columns(*cols)
        else:
            table.clear()

        try:
            # -1 for the header row; fall back to config if not yet laid out
            limit = max(5, table.content_size.height - 1)
        except Exception:
            limit = self._config.process_display_count
        sorted_procs = sort_processes(self._last_processes, self._sort_mode, limit)
        for proc in sorted_procs:
            table.add_row(
                str(proc.get("pid", "")),
                _process_display_name(proc.get("command", ""), max_len=28),
                "{:.1f}".format(proc.get("cpu_percent", 0.0) or 0.0),
                "{:.1f}".format(proc.get("rss_mb", 0.0) or 0.0),
                str(proc.get("num_threads", "")),
            )
