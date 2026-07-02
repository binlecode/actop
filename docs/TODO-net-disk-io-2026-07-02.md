# TODO — Net / disk I/O via native ctypes (impl-ready)

**Date:** 2026-07-02 · **Priority:** the sole open must-have on the roadmap, **post-launch**
(the convergence quick wins shipped — fan telemetry v1.2.3, `--palette` v1.4.1). See the
net/disk item's rationale in `docs/TODO-architecture-roadmap.md` for why: net/disk is
**single-peer breadth** (mactop-only; macmon deliberately omits it), a conscious bet on
narrowing the gap to the breadth leader — not a converged peer expectation. Tracked openly
here rather than silently declined.

**Status: feasibility spike complete (2026-07-02).** Verified on-device (M4 Max,
unprivileged) and cross-checked against `mactop`'s (`metaspartan/mactop`) shipped
implementation. `asitop` was also checked and has neither (it only wraps `psutil` for
RAM/swap), so it gave no reference. **The approach below is verified, not candidate.** The
originally-guessed network MIB (`net.link.generic.system.stats`) and disk ordering
(`IOBlockStorageDriver` first) were both wrong and are corrected here.

**Constraint (non-negotiable):** built via **native `ctypes`**, matching `native_sys.py`'s
existing pattern — **not** by reintroducing `psutil`. This is the condition under which the
roadmap overrides `DESIGN-system.md` §3.6's non-goal framing.

This is a **planning doc, not shipped code.** Implement as a normal PR (patch bump,
`[Unreleased]` → dated section, `ruff check --fix .` + `ruff format .`, functional tests
only).

---

## Verified data sources

### Network — `getifaddrs()` / `freeifaddrs()`, NOT a `sysctl` MIB

The originally-guessed OID `net.link.generic.system.stats` **does not exist on-device**.
The working approach:

-   Call `getifaddrs(&ifap)` → walk the `struct ifaddrs` linked list via `ifa_next`.
-   Keep entries where `ifa_addr->sa_family == AF_LINK` (link-layer stats live on the
    `AF_LINK` address of each interface); **drop** entries with the `IFF_LOOPBACK` flag set
    in `ifa_flags` (skip `lo0`).
-   For each kept entry, cast `ifa_data` → `struct if_data` and read the cumulative counters
    `ifi_ibytes` / `ifi_obytes` / `ifi_ipackets` / `ifi_opackets`.
-   `freeifaddrs(ifap)` when done (one allocation per call — free it every tick).

This is `mactop`'s exact approach (`internal/app/native_stats.go:GetNativeNetworkMetrics`).

**New bindings needed in `native_sys.py`** (which today binds only `sysctlbyname`):
`getifaddrs` / `freeifaddrs` from libc, plus the `struct ifaddrs` and `struct if_data`
ctypes layouts. **⚠ VERIFY at impl time:** the exact byte offsets/field order of
`struct if_data` on the current macOS SDK (`<net/if_var.h>` / `<net/if.h>`) — get them from
the live headers, do not hand-transcribe from memory; the struct has version-sensitive
fields. Confirm `AF_LINK` (18) and `IFF_LOOPBACK` (0x8) constants against `<sys/socket.h>` /
`<net/if.h>`.

### Disk — IOKit `AppleAPFSVolume` first, `IOBlockStorageDriver` fallback

Reverse of the originally-guessed ordering. Verified live and non-zero, unprivileged, via
`ioreg -c AppleAPFSVolume -r -w0`:

-   `IOServiceMatching("AppleAPFSVolume")` → for each matched volume, read its `Statistics`
    dict and sum:
    -   `"Bytes read from block device"` / `"Bytes written to block device"`
    -   `"Read requests sent to block device"` / `"Write requests sent to block device"`
-   **Fallback only when no `AppleAPFSVolume` entries are found** (older non-APFS systems):
    `IOServiceMatching("IOBlockStorageDriver")`, whose `Statistics` uses differently-named
    keys — `"Bytes (Read)"` / `"Bytes (Write)"` / `"Operations (Read)"` /
    `"Operations (Write)"`.

This is `mactop`'s exact fallback order. Read the **same way `gpu_registry.py` already walks
`IOAccelerator`** — `IOServiceGetMatchingServices` / `IORegistryEntryCreateCFProperty` /
iterator traversal — so **no new IOKit binding classes are needed**, just a different
matching string and property keys. Reuse the existing CoreFoundation → Python dict
conversion path from `gpu_registry.py`.

### Aggregation (same shape as memory bandwidth)

Both metrics are **cumulative counters**; the sampler already does exactly this delta for
DRAM bandwidth. Per tick:

1.  Sum raw counters across all non-loopback interfaces (net) / all matched volumes (disk)
    into one running total each.
2.  `rate = (current_total - previous_total) / elapsed_seconds`.

`elapsed_seconds` is already computed in the sampler loop (`sampler.py:93`,
`new_time - self._prev_time` via `time.monotonic()`), and the first-tick "no previous
sample → return baseline" guard already exists (`sampler.py:87-89`). This is the identical
delta-over-interval pattern as `_compute_bandwidth_gbps` (`sampler.py:587`, DESIGN §3.5) and
matches `mactop`'s `getNetDiskMetrics` exactly — a drop-in fit for the existing poll loop,
not a new pattern to design. Expose rates in **bytes/s** (or MB/s) for net rx/tx and disk
read/write; keep packet/op counts if cheap.

---

## Where each piece plugs in (verified against current code)

| Layer | File | Change |
| :--- | :--- | :--- |
| Native syscall | `actop/native_sys.py` | New `getifaddrs`/`freeifaddrs` bindings + `ifaddrs`/`if_data` struct layouts; a `read_network_totals()` returning summed rx/tx bytes+packets. |
| IOKit read | new helper or `actop/gpu_registry.py`-style module | `read_disk_totals()` walking `AppleAPFSVolume`→`IOBlockStorageDriver` `Statistics`, reusing the existing IOKit/CF traversal. |
| Delta → rate | `actop/sampler.py` | Store prev net/disk totals alongside `_prev_time` (`sampler.py:44-45,85-99`); compute rates in `_convert` (`sampler.py:181`), add fields to `SampleResult` (`sampler.py:11`). |
| Public model | `actop/models.py` | New `SystemSnapshot` fields: `net_rx_bps` / `net_tx_bps` / `disk_read_bps` / `disk_write_bps` (+ availability flags mirroring `fan_available`, since a locked-down or unusual host may yield nothing). |
| API mapping | `actop/api.py` | Map the new `SampleResult` fields → `SystemSnapshot` (mirror `api.py:64-65` fan mapping). |
| TUI | `actop/tui/widgets.py` | New Net and Disk rows/charts, using the `bandwidth_available` hide-row pattern (`widgets.py:677-688`) for hosts where the metric is unavailable. |
| Export | `actop/export.py` | Add `_PROM_GAUGES` entries (`export.py:20-36`) — `net_rx_bps`→`network_receive_bytes_per_second`, etc.; NDJSON picks them up automatically via `dataclasses.asdict` (`export.py:41`). |
| SoC scaling | `actop/power_scaling.py` / `soc_profiles.py` | N/A — net/disk are rates, not power; no SoC reference values needed. |

---

## Tasks (implementation-ready)

-   **T1 — Network binding + reader.** `native_sys.py`: bind `getifaddrs`/`freeifaddrs`, add
    `ifaddrs`/`if_data` structs (offsets confirmed from live SDK headers), implement
    `read_network_totals()` (walk, filter `AF_LINK`, drop `IFF_LOOPBACK`, sum). Guard with
    the existing `sys.platform == "darwin"` pattern. `done_when`: returns plausible non-zero
    cumulative rx/tx on an active machine; `[]`/zeroed + available=False on failure. Free the
    list every call (no leak over a long run).
-   **T2 — Disk reader.** Reuse the `gpu_registry.py` IOKit/CF traversal; implement
    `read_disk_totals()` with `AppleAPFSVolume` primary + `IOBlockStorageDriver` fallback and
    the two key-name sets. `done_when`: non-zero read/write byte totals on-device,
    unprivileged; fallback exercised on (or reasoned about for) non-APFS.
-   **T3 — Sampler delta → rate.** Thread prev-totals through the sampler loop; add rate
    fields to `SampleResult`; compute in `_convert` using the existing `elapsed_s`. `done_when`:
    `IOReportSampler` emits net/disk **rates** that track a real transfer (e.g. a large
    `dd`/download makes disk-write / net-rx spike).
-   **T4 — Public model + API + export.** Add `SystemSnapshot` fields (+ availability flags),
    map in `api.py`, add `_PROM_GAUGES` rows; confirm NDJSON includes them. `done_when`:
    `Monitor().get_snapshot()` carries the rates; `--json` and `--serve` both surface them.
-   **T5 — TUI rows.** Net + Disk rows/sparklines with the hide-row pattern for unavailable
    hosts. `done_when`: rows update live under load and hide cleanly when unavailable.
-   **T6 — Docs + version.** Replace `DESIGN-system.md` §3.6's non-goal bullet with the
    as-built design; check off the roadmap item; update `README.md` metric list and the
    comparison doc (§3 table "Network / Disk I/O" row → ✅). Patch bump + CHANGELOG.

**Suggested order:** T1 and T2 are independent and each independently verifiable on-device
(the risk is entirely in the two readers) → T3 wires the delta → T4/T5 surface it → T6 docs.

---

## Testing (functional only — per `CLAUDE.md`)

These are **host-dependent** hardware/OS reads, so the real coverage is
`@pytest.mark.local` functional runs (gated off CI per the CI local-test-marking
convention), driving the **public** surface:

-   `Monitor().get_snapshot()` on a real machine → assert net/disk rate fields are present
    and non-negative, and (for a stronger check) that generating real I/O in the test moves
    the rate above idle. This exercises the whole native→sampler→API path, not a mocked stub.
-   Export contract: feed a real `SystemSnapshot` to `snapshot_to_prometheus` /
    `snapshot_to_json` and assert the new metric names/format appear — a real format contract,
    per the "real export/format contracts" allowance.

**Do not** add a mocked-`getifaddrs` / mocked-IOKit structural test, a test that asserts the
struct offsets in isolation, or a test that pokes the sampler's private prev-total
attributes — all three violate the functional-tests-only mandate and must not be added.
