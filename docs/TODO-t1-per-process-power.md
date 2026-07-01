# TODO — Tier 1 #1: Per-process power / energy attribution ⭐

**Status:** implemented (pending live visual check + PR) · **Effort:** S–M (revised down after Phase-0) · **Parent:** [`TODO-actop-feature-gap-roadmap.md`](TODO-actop-feature-gap-roadmap.md) Tier 1 #1 (the flagship differentiator).

## Goal

Add a **per-process power column** (`PWR`, watts) to the process table — "which
process is drawing the watts." This is Activity Monitor's "Energy Impact," but in a
**sudoless in-process TUI**. No direct peer (asitop / mactop / macmon / silitop) does
per-process power/energy; they show system-total power + a CPU%/RSS list. This is the
white space.

Scope is **CPU power attribution** (the sudoless-reachable signal). Per-process GPU/ANE
is the Tier 2 stretch (#5), out of scope here — surfaced as a labelled caveat.

---

## Phase 0 — validate the energy signal (DONE ✅, findings below)

> Spike: [`tmp/spike_rusage_energy.py`](../tmp/spike_rusage_energy.py), run 2026-06-30.

The original plan read `proc_pid_rusage`'s `ri_billed_energy` + `ri_serviced_energy` as
per-process energy. **The spike disproved that assumption:**

- With a core **pegged for 2.5s** (Δcycles = 10.36B ≈ 3 core-seconds of real work),
  **`ri_billed_energy` and `ri_serviced_energy` stayed flat at 0.** They are *not* a
  measure of a process's own CPU energy (they track cross-process XPC "billing", which
  is ~0 for ordinary compute). Summing them ⇒ ~0 W for a busy process. **Dead end.**
- `proc_pid_rusage(pid=1)` → **EPERM**: root-owned processes are unreadable sudoless.
- `ri_user_time` is in **mach-absolute units, not nanoseconds** (60M ticks × ~41.7ns ≈
  2.49s) — the old struct-based ns math would have been wrong too.
- What *does* track compute: `ri_cycles` (rusage) and, crucially, **`cpu_time_ns` from
  `PROC_PIDTASKALLINFO`** — which `get_native_processes()` already collects for **every**
  PID, with **no EPERM**.

### Decision: proportional attribution, no new native binding

```
proc_power_w = (proc CPU-time Δ / Σ all-procs CPU-time Δ) × SystemSnapshot.cpu_watts
```

This is strictly better than the rusage-energy path on every axis:

| | rusage energy (rejected) | CPU-time share (chosen) |
|---|---|---|
| Tracks compute | ❌ flat 0 (spike) | ✅ (existing `cpu_time_ns`) |
| Coverage | own-user only (EPERM) | **all PIDs** (PROC_PIDTASKALLINFO) |
| Reconciles to `cpu_watts` | no | **by construction** (it's a partition) |
| New syscall / binding | yes | **none** — reuses data already gathered |

`task_info(TASK_POWER_INFO_V2)` is also rejected (needs `task_for_pid` → root).

**Known model caveat (must document in UI):** power is attributed by **wall CPU-time**,
but a P-core-second draws more watts than an E-core-second (and DVFS scales it further).
So a process pinned to E-cores is *over*-attributed and vice-versa. This is an estimate,
labelled as such. A cycle- or per-core-power-weighted refinement is a later improvement
(would need per-proc, per-cluster residency — Tier-2-adjacent).

## Acceptance criteria

1. Process table shows a per-process `PWR` (W) that visibly tracks a known busy process
   (e.g. an `ollama` / inference run climbs to the top).
2. **Σ(per-proc `PWR`) == `SystemSnapshot.cpu_watts`** (exact — it's a partition of it;
   a small "unattributed" remainder row is acceptable only if short-lived PIDs are
   dropped mid-poll). This is the reconciliation token: `Σ proc = pkg CPU N.NW`.
3. First-sample / dead-PID / paused rows render `–`, never a wrong 0.0.
4. `.venv/bin/pytest -q` green; `actop` runs on Apple Silicon with the column live and
   no per-frame exceptions.

## Data flow (real symbols, current line refs)

```
utils.get_native_processes()  (native_sys.py:588)   # already returns cpu_time_ns per PID — unchanged
   ↓
utils.get_top_processes()     (utils.py:120)         # reuse _PROCESS_CPU_CACHE deltas (line 150-159)
   → each proc dict gains  "cpu_time_share": float   # = proc Δ / Σ all Δ  (0..1), decoupled from watts
   ↓
tui/widgets.py process rows                          # PWR = cpu_time_share * snapshot.cpu_watts; share bar
tui/app.py sort                                      # new SORT_POWER (== SORT_CPU order; share ∝ cpu_time)
   ↓
export.py (optional)                                 # see cardinality caveat below
```

> **No `native_sys.py` change and no `proc_pid_rusage` binding** — the CPU-time deltas
> already computed for the `CPU%` column carry all the information needed.

## Implementation checklist

> **Note (as-built):** the process table lives in **`tui/app.py`** (a Textual
> `DataTable` built in `_refresh_process_table`), not `tui/widgets.py` as the data
> flow above sketched. The `PWR` column, `SORT_POWER`, and the reconciliation token
> were added there. Everything else landed as planned.

- [x] **`utils.py` `get_top_processes` (line 120)** — the loop already computes each
      PID's `cpu_delta_ns` for `cpu_percent` (line 150-159). Accumulate `total_delta_ns`
      across all PIDs, then set `cpu_time_share = cpu_delta_ns / total_delta_ns` (0 if
      total is 0). Add `"cpu_time_share"` to the entry dict (line 165-174). Keep watts
      **out** of utils — the TUI owns `cpu_watts`.
- [x] **PID-reuse hardening** — the current `_PROCESS_CPU_CACHE` keys on `pid` alone; a
      reused PID can yield a bogus positive Δ. Key the cache on **`(pid, pbi_start_tvsec)`**.
      Surface the process start time from `get_native_processes`: `pbi_start_tvsec` is a
      `uint64` at **offset 120** in `PROC_PIDTASKALLINFO` (verified against the SDK
      `proc_bsdinfo`; cross-checked by the existing `_OFF_PROC_METRICS = 136`, the end of
      the 136-byte `proc_bsdinfo`). Add `_OFF_START_TVSEC = 120` and one
      `struct.unpack_from("<Q", raw, 120)`. A changed start time for the same PID ⇒ treat
      as first sample.
- [x] **`tui/app.py` process table** (not `widgets.py` — see as-built note) — added the
      `PWR` column = `share * snapshot.cpu_watts`. Renders `–` when `cpu_time_share is
      None` (first sample / just resumed). Reconciliation token in the table's
      `border_subtitle` (acceptance #2): `Σ shown N.NW / pkg CPU M.MW · est CPU-time
      share`. The `est` marker carries the P-vs-E model caveat. _(Deferred: the
      heatmapped `share` bar — CPU% already conveys ordering in the `DataTable`; can add
      later as a follow-up.)_
- [x] **`tui/app.py`** — added `SORT_POWER = "power"` to `SORT_LABELS` / `_SORT_CYCLE` /
      `sort_processes`. Sorts by `cpu_time_share` desc (None sinks to the bottom); since
      `PWR ∝ cpu_time_share ∝ cpu_percent` this matches `SORT_CPU` order but is honest to
      the label.
- [x] **`HelpScreen`** — added a "Process table" section documenting `CPU%`, `PWR` (a
      CPU-time-share **estimate** of package CPU power, not GPU/ANE, with the P-vs-E
      caveat), and the `Σ shown` reconciliation token. Sort-cycle line updated to
      `CPU% → PWR → RSS → PID`.
- [ ] **`export.py`** (optional, after TUI — NOT yet done) — processes are **not** exported today
      (`snapshot_to_*` is `SystemSnapshot`-only, export.py:39-75). If added: **Prometheus
      per-PID labels are a cardinality anti-pattern** (churny short-lived PIDs explode
      series). Restrict to top-N, drop the `pid` label (use `comm` only) or gate behind an
      explicit opt-in flag. NDJSON can carry a bounded `processes` array.
- [ ] **Docs** — fold shipped design into `DESIGN-system.md` (§2.3 process enumeration
      gains `start_abstime` + share; §5.x process-table columns) and tick the roadmap.

## TUI presentation (target)

```
│  PID    COMMAND       CPU%    PWR   ▏share  THD  │
│  ──────────────────────────────────────────     │
│  2041  ollama       1180.2  18.7W ███████▏74%  22 │
│  1025  python          92.4   3.1W ██▏12%      8 │
│   734  Xcode           41.0   1.4W █▏6%      14 │
│   502  WindowServer     8.7   0.6W ▏2%        3 │   # covered — PROC_PIDTASKALLINFO sees all PIDs
│ ─────────────────────────────────────────────  │
│ Σ proc = pkg CPU 25.3W (est · CPU-time share, not GPU/ANE) │
```

## Edge cases

- **First sample after launch / resume from `p`:** no prior cache delta ⇒
  `cpu_time_share = None` ⇒ render `–` (same lifecycle as `CPU%` today).
- **PID reuse:** handled by the `(pid, start_abstime)` cache key.
- **Σ CPU-time Δ == 0** (fully idle poll): all shares 0 ⇒ all `PWR` 0.0, no divide-by-zero.
- **Short-lived PID vanishing mid-poll:** excluded from the denominator on the next poll;
  its share simply disappears (acceptable; note in acceptance #2).
- **Model skew (P vs E cores):** documented estimate, not a bug — flagged `est.` in UI.

## Testing (functional only — per CLAUDE.md)

Drive **public surfaces**; no private-attr or mock-the-data tests.
All in `tests/test_per_process_power.py` (4 tests, green).

- [x] `utils.get_top_processes()` on the real host returns entries with
      `cpu_time_share`, every non-`None` value in `[0.0, 1.0]`, and the sum over all
      returned procs ≤ 1.0 (partition bound).
- [x] Under a self-induced busy loop, the current process's `cpu_time_share` rises on the
      second poll — proves the attribution tracks real compute end-to-end.
- [x] `sort_processes(..., SORT_POWER, ...)` (public, `tui/app.py`) orders by share.
- [x] `ActopApp` mounted via `App.run_test()`, fed a real `MetricsUpdated` (snapshot +
      process list) through its message handler, renders the `PWR` column + reconciliation
      token, `–` for a `None` share, and **Σ shown ≈ cpu_watts**, without raising.
      _(Table is in `ActopApp`, not `HardwareDashboard` — see as-built note.)_
- [ ] If export lands: bounded Prometheus/NDJSON proc output with no unbounded `pid` label.

## Risks

- **Model accuracy (P vs E / DVFS)** is now the main risk, not the data source — mitigated
  by labelling `PWR` an estimate and by exact Σ→`cpu_watts` reconciliation. A cycle- or
  per-core-weighted refinement can follow if users need finer fidelity.
- ~~`start_abstime` offset unknown~~ **Resolved:** `pbi_start_tvsec` @ offset 120,
  verified against the SDK and cross-checked by the existing `_OFF_PROC_METRICS = 136`.
  No open native-layout unknowns remain.
