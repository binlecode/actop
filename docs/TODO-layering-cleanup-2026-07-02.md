# TODO: Layering Cleanup — remaining items

Status: **DONE** · Created: 2026-07-02 · Closed: 2026-08-11 (v1.7.2)

> **LC-1 through LC-3 shipped** in v1.2.4–v1.3.0. **LC-4 dropped** — pure
> code hygiene, not a user-visible defect. **Export parity (E track) shipped**
> in v1.7.2: AlertEngine integrated into `run_json_stream` / `serve_prometheus`,
> `--json-processes` / `--serve-processes` flags added, processes/alerts/
> session energy reach both export backends. No remaining items. This TODO
> is closed.
>
> The design decision: option (1) — AlertEngine lives in the export run loops,
> not inside `Monitor`/`SystemSnapshot`. Alert verdicts merge into NDJSON under
> top-level `alert_*` keys and into Prometheus as `actop_alert_*` gauges.

---

## 1. Export parity — shipped v1.7.2

All three data points now reach `--json` and `--serve`:

| Data point | How it shipped |
|---|---|
| Processes | `--json-processes` / `--serve-processes` CLI flags; the underlying `include_processes` pipe existed since a mid-cycle update |
| Alerts / throttle | `run_json_stream` / `serve_prometheus` build an `AlertEngine` from the same CLI threshold flags the TUI uses; each snapshot is fed through it |
| Session energy | Accumulated by the same `AlertEngine.feed()` call, merged into every NDJSON record and Prometheus scrape |

→ Roadmap **E** track — shipped.

## 2. §8-1's literal grep does not pass — by design, not a defect

`grep -n "from actop.utils import" actop/tui/*.py` still matches:
`tui/app.py` imports `get_soc_info` for the one call at construction that builds
`DashboardConfig`. The acceptance criterion was written to kill **per-frame** L1
acquisition in the view, and that is gone. **Satisfied in substance.**
