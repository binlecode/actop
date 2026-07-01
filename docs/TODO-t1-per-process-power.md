# TODO — Tier 1 #1: Per-process power / energy attribution ⭐

**Status:** not started · **Effort:** M–L · **Parent:** [`TODO-actop-feature-gap-roadmap.md`](TODO-actop-feature-gap-roadmap.md) Tier 1 #1 (the flagship differentiator).

## Goal

Add a **per-process power column** (`PWR`, watts) to the process table — "which
process is drawing the watts." This is Activity Monitor's "Energy Impact," but in a
**sudoless in-process TUI**. No direct peer (asitop / mactop / macmon / silitop) does
per-process power/energy; they show system-total power + a CPU%/RSS list. This is the
white space.

Scope is **CPU energy attribution** (the sudoless-reachable lineage). Per-process
GPU/ANE is the Tier 2 stretch (#5) and is explicitly out of scope here — surfaced in
the UI as a labelled caveat, not a blank promise.

## Acceptance criteria

1. The process table shows a per-process `PWR` figure (W) that visibly tracks a known
   busy process (e.g. an `ollama` / inference run climbs to the top).
2. **Reconciliation:** Σ(per-process CPU power) is within a sane margin of the sampled
   **package CPU power** (`SystemSnapshot.cpu_watts`) — target ≥ ~85–95%; the gap is
   kernel/unattributed. A status-line token shows this: `Σ per-proc CPU N.NW ≈ pkg CPU M.MW (P%)`.
3. Processes the user cannot query (root/other-user) render `–`, never a wrong 0.0.
4. `.venv/bin/pytest -q` green; `actop` runs on Apple Silicon with the column live and
   no per-frame exceptions.

## Native source decision (why `proc_pid_rusage`, not `task_power_info`)

| Option | Sudoless for *other* PIDs? | Verdict |
|---|---|---|
| `task_info(TASK_POWER_INFO_V2)` | ❌ needs a task port via `task_for_pid` → root/entitlement | rejected |
| **`proc_pid_rusage(pid, RUSAGE_INFO_V4+)`** | ✅ for **same-user** processes (own UID); EPERM otherwise | **chosen** |

`proc_pid_rusage` (libSystem) exposes `ri_billed_energy` + `ri_serviced_energy` — an
energy counter in **nanojoules** — which is the counter behind Activity Monitor's
energy lineage. Power = ΔenergyNJ / Δt / 1e9, using the **same delta-cache pattern**
already proven for per-process CPU% in `utils.get_top_processes` (`_PROCESS_CPU_CACHE`).

> **Coverage caveat (must document in UI + `--help`):** unprivileged `proc_pid_rusage`
> succeeds for processes owned by the current user; system/root processes typically
> return EPERM. Those rows show `–`. This is a sudoless-tradeoff, not a bug — state it.
> `ri_*_energy` units/semantics are community-established, not formally documented, so
> **validate empirically against `cpu_watts`** (acceptance #2) rather than trusting a
> constant.

## ctypes binding (add to `actop/native_sys.py`)

Bind `proc_pid_rusage` and define the **full** `rusage_info_v4` struct (all preceding
fields are required for correct offsets — do not shortcut to the energy fields):

```python
_RUSAGE_INFO_V4 = 4  # flavor

class RUsageInfoV4(ctypes.Structure):
    _fields_ = [
        ("ri_uuid", ctypes.c_uint8 * 16),
        ("ri_user_time", ctypes.c_uint64),
        ("ri_system_time", ctypes.c_uint64),
        ("ri_pkg_idle_wkups", ctypes.c_uint64),
        ("ri_interrupt_wkups", ctypes.c_uint64),
        ("ri_pageins", ctypes.c_uint64),
        ("ri_wired_size", ctypes.c_uint64),
        ("ri_resident_size", ctypes.c_uint64),
        ("ri_phys_footprint", ctypes.c_uint64),
        ("ri_proc_start_abstime", ctypes.c_uint64),
        ("ri_proc_exit_abstime", ctypes.c_uint64),
        ("ri_child_user_time", ctypes.c_uint64),
        ("ri_child_system_time", ctypes.c_uint64),
        ("ri_child_pkg_idle_wkups", ctypes.c_uint64),
        ("ri_child_interrupt_wkups", ctypes.c_uint64),
        ("ri_child_pageins", ctypes.c_uint64),
        ("ri_child_elapsed_abstime", ctypes.c_uint64),
        ("ri_diskio_bytesread", ctypes.c_uint64),
        ("ri_diskio_byteswritten", ctypes.c_uint64),
        ("ri_cpu_time_qos_default", ctypes.c_uint64),
        ("ri_cpu_time_qos_maintenance", ctypes.c_uint64),
        ("ri_cpu_time_qos_background", ctypes.c_uint64),
        ("ri_cpu_time_qos_utility", ctypes.c_uint64),
        ("ri_cpu_time_qos_legacy", ctypes.c_uint64),
        ("ri_cpu_time_qos_user_initiated", ctypes.c_uint64),
        ("ri_cpu_time_qos_user_interactive", ctypes.c_uint64),
        ("ri_billed_system_time", ctypes.c_uint64),
        ("ri_serviced_system_time", ctypes.c_uint64),
        ("ri_logical_writes", ctypes.c_uint64),
        ("ri_lifetime_max_phys_footprint", ctypes.c_uint64),
        ("ri_instructions", ctypes.c_uint64),
        ("ri_cycles", ctypes.c_uint64),
        ("ri_billed_energy", ctypes.c_uint64),
        ("ri_serviced_energy", ctypes.c_uint64),
        ("ri_interval_max_phys_footprint", ctypes.c_uint64),
        ("ri_runnable_time", ctypes.c_uint64),
    ]

# int proc_pid_rusage(int pid, int flavor, rusage_info_t *buffer);
_proc_pid_rusage = _libc.proc_pid_rusage
_proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
_proc_pid_rusage.restype = ctypes.c_int

def get_process_energy_nj(pid: int) -> int | None:
    """Cumulative CPU energy (nanojoules) for a PID, or None if not permitted."""
    if not _DARWIN:
        return None
    info = RUsageInfoV4()
    ret = _proc_pid_rusage(pid, _RUSAGE_INFO_V4, ctypes.byref(info))
    if ret != 0:
        return None  # EPERM (root/other-user) or dead PID
    return info.ri_billed_energy + info.ri_serviced_energy
```

Match the surrounding style: guarded by `_DARWIN`, `try/except`-safe, no shell-outs.
Verify the field list against the running SDK's `<sys/resource.h>` before landing — if
Apple ships `_v6` with the same trailing energy fields, prefer the highest available
flavor and fall back to v4.

## Data flow (real symbols, current line refs)

```
native_sys.get_process_energy_nj(pid)            # NEW — ctypes rusage read
   ↓
utils.get_top_processes()  (utils.py:120)        # add energy delta via _PROCESS_ENERGY_CACHE
   → each proc dict gains  "cpu_power_w": float | None
   ↓
tui/app.py sort + render                         # new SORT_POWER; PWR column + share bar
tui/widgets.py process rows                      # column formatter + reconciliation token
   ↓
export.py (optional)                             # actop_proc_cpu_watts{pid,comm} gauge
```

## Implementation checklist

- [ ] **`native_sys.py`** — bind `proc_pid_rusage` + `RUsageInfoV4`; add
      `get_process_energy_nj(pid) -> int | None` (above). Add its `_OFF_*`-style doc
      comment noting the flavor and units.
- [ ] **`utils.py`** — in `get_top_processes` (line 120), add a module-level
      `_PROCESS_ENERGY_CACHE: dict[int, tuple[int, float]]` mirroring
      `_PROCESS_CPU_CACHE` (line 150–159). Per PID: read energy NJ, compute
      `cpu_power_w = (Δnj) / Δt / 1e9` (clamp ≥ 0; `None` if the read failed). Add
      `"cpu_power_w"` to the entry dict (line 165–174). Prune dead PIDs alongside the
      existing cleanup (line 176–179). Guard against **counter reset / PID reuse**
      (Δ < 0 → treat as first sample, emit `None`).
- [ ] **`tui/app.py`** — add `SORT_POWER = "power"`, extend `SORT_LABELS` (line 25),
      `_SORT_CYCLE` (line 27), and `sort_processes` (line 30) to sort by `cpu_power_w`
      (None sorts last). Optional dedicated key `e` to jump to the power view.
- [ ] **`tui/widgets.py`** — add the `PWR` column + heatmapped `share` bar (reuse the
      `BrailleChart` gradient / `_pct_to_color`, do not add a new color path). Render
      `–` for `None`. Add the reconciliation status token (acceptance #2), computed as
      `Σ cpu_power_w / snapshot.cpu_watts`.
- [ ] **`export.py`** (optional, do after TUI) — processes are **not** in the export
      today (`snapshot_to_*` is `SystemSnapshot`-only, export.py:39–75). If exporting,
      add a labelled Prometheus gauge `actop_proc_cpu_watts{pid,comm}` and a `processes`
      array in the NDJSON path — behind the existing `--json` / `--serve` routes.
- [ ] **`--help` / `HelpScreen`** — document the `PWR` column, the `e` sort, and the
      sudoless coverage caveat (own-user only; `–` = not permitted).
- [ ] **Docs** — after landing, fold the shipped design into `DESIGN-system.md` (§2.x
      process enumeration gains an energy field; §5.x process-table columns) and tick
      the parent roadmap.

## TUI presentation (target)

```
│  PID    COMMAND       CPU%    PWR   ▏share  THD  │
│  ──────────────────────────────────────────     │
│  2041  ollama       1180.2  18.7W ███████▏74%  22 │
│  1025  python          92.4   3.1W ██▏12%      8 │
│   734  Xcode           41.0   1.4W █▏6%      14 │
│   502  WindowServer     8.7    –            3 │   # root → not permitted
│ ─────────────────────────────────────────────  │
│ Σ per-proc CPU 24.1W ≈ pkg CPU 25.3W (95%) · GPU/ANE power system-wide (T2) │
```

## Edge cases

- **Permission (EPERM):** other-user/root PIDs → `None` → render `–`. Never 0.0.
- **Counter reset / PID reuse:** Δenergy < 0 → discard, treat as first sample.
- **First sample after launch:** no prior cache entry → `None` until the second poll
  (same as CPU% today).
- **Pause (`p`):** stale cache across a long pause inflates the first Δ; on resume,
  reset the energy cache timestamp the same way CPU% is handled.
- **Units unverified:** gate the display behind acceptance #2 passing on real hardware;
  if `ri_*_energy` turns out not to be nanojoules on some SoC, the reconciliation ratio
  exposes it immediately.

## Testing (functional only — per CLAUDE.md)

Drive **public surfaces**; no private-attr or mock-the-data tests.

- [ ] `utils.get_top_processes()` on the real host returns entries that include
      `cpu_power_w`, and every non-`None` value is `≥ 0.0` (physical bound), mirroring
      the existing CPU%/bounds contract in `tests/test_cli_contract.py`.
- [ ] The current process (`os.getpid()`, own-user) yields a non-`None` `cpu_power_w`
      after two polls — proves the sudoless read path works end-to-end.
- [ ] `sort_processes(..., SORT_POWER, ...)` (public in `tui/app.py`) orders by power
      with `None` last.
- [ ] `HardwareDashboard` mounted via `App.run_test()`, fed real `SystemSnapshot`s +
      process lists through its public update path, renders the `PWR` column and the
      reconciliation token without raising.
- [ ] If export lands: NDJSON line / Prometheus text contains `actop_proc_cpu_watts`
      with correct labels (real export contract).

## Risks

- **Units/semantics of `ri_*_energy`** are the main unknown — acceptance #2 is the
  guardrail; do not ship the column if Σ doesn't track `cpu_watts`.
- **Sampling cost:** one extra `proc_pid_rusage` syscall per listed PID per poll. The
  table is already capped (`limit`), so bound the energy read to the displayed set, not
  all PIDs, to stay within the <0.5% idle-CPU budget.
