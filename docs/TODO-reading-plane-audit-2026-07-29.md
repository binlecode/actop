# TODO — Reading-plane audit (2026-07-29)

Implementation-ready remediation plan from a whole-tree audit of the **reading plane**
(L1 acquisition → sampler conversion → L2 normalization), performed on
**Apple M4 Max / Darwin 25.5.0** at `v1.4.16`.

Every claim below was verified against live hardware, not inferred from reading code.
Where an audit hypothesis was *disproved*, that is recorded too (§7) so nobody re-opens it.

**Audience constraint that drives several decisions here:** actop targets **AI engineers**
profiling local LLM / MLX / CoreML inference. They consume `Monitor` / `Profiler` and the
NDJSON / Prometheus export *programmatically*, and routinely divide these metrics against
each other (`tokens/s ≈ effective_bandwidth / bytes_read_per_token`; RAM headroom vs.
quantized weights). Correctness of API/export field names and units therefore outranks
TUI familiarity — see §3.

---

## Sequencing

One logical change per PR, each branched fresh from `main` **after** the previous merges.
Never stack branches (`CLAUDE.md`: "Never fork a feature branch off another feature branch").
Every PR bumps `pyproject.toml` version + moves `CHANGELOG.md` `[Unreleased]` into a dated
section, in the same PR.

| PR | Scope | Bump | Risk | User-visible |
|----|-------|------|------|--------------|
| A | §1 docstring contract + §2 Hz rounding | patch | low | freq ±1 MHz |
| B | §4 unresolvable-state consistency + §6 `bandwidth_available` | patch | medium | latent today |
| C | §5 rounding (`round()` except apportionment) | patch | medium | every % ±1 |
| D | §3 step 1 — additive `*_gib` fields | **minor** | low | new fields + `GiB` label |
| E | §8 GPU `IOAccelerator` fallback + Renderer/Tiler breakdown — **shipped v1.6.0** | **minor** | medium | new panel rows |
| F | §3 step 2 — remove deprecated `*_gb` | **major** | breaking | field removal |

PR F is deliberately deferred; it is the only breaking change and should ride a real 2.0.0.

---

## Testing mandate (binds every item below)

`CLAUDE.md` enforces **functional tests only**. This materially constrains verification here,
because most audit findings live in underscore-prefixed functions:

- **Forbidden:** calling `_resolve_state_freq`, `_channel_bandwidth_gbps`,
  `_compute_residency_metrics`, `_largest_remainder_percentages` etc. as the unit under test;
  reading/writing private attributes; mocks/fakes/monkeypatching; tests that exist only to
  raise coverage; standalone shape/bounds assertions when a behavioral test already covers
  the path.
- **Required route:** drive a public surface — `Monitor` / `Profiler`, `build_parser().parse_args`,
  `create_dashboard_config`, documented public functions (`power_to_percent`, `get_soc_profile`,
  `clamp_percent`), the real NDJSON/Prometheus export contract, or a real widget mounted via
  Textual `App.run_test()` and fed real `SystemSnapshot`s through `update_metrics`.
- Hardware-dependent tests **must** be marked `@pytest.mark.local` — CI runs
  `pytest -m "not local"`.

Consequence to accept up front: §4 is **not** unit-testable under this mandate. Its
verification is an invariant assertion added *inside* an existing behavioral `Monitor` test
(see §4.4), plus the one-off hardware probe recorded in §4.2.

---

## 1. GPU DVFS table is non-monotonic — three docstrings state the opposite

**Severity:** documentation / latent assumption. No wrong number today.

### 1.1 Evidence (live M4 Max)

The table actually consumed for GPU state resolution:

```
gpu: [0, 338, 618, 796, 924, 952, 1056, 1062, 1182, 1182, 1312, 1242, 1380, 1326, 1470, 1578]
                                              ^^^^  ^^^^        ^^^^  ^^^^        ^^^^  ^^^^
                                              dup         1312→1242 ↓      1380→1326 ↓
```

Not ascending, and `1182` appears twice. Split by index parity, each half *is* monotonic
(`[0,618,924,1056,1182,1312,1380,1470]` and `[338,796,952,1062,1182,1242,1326,1578]`), which
looks exactly like a stride bug — **it is not.** Verified by dumping raw `pmgr` bytes:

- `voltage-states14` / `15`: `bytes=128`, `128 / 8 = 16` entries under the `<II` stride.
- Second field of each pair reads as plausible voltages: `[125, 610, 650, 695, 725, 770, 770,
  815, 815, 865, 865, 905, 905, 945, 945, 1030]` mV.
- 16 entries maps exactly onto IOReport's GPU states `OFF` + `P1`–`P15`.

So the 8-byte `(freq_hz, voltage)` stride is correct and Apple's table genuinely has this shape.

### 1.2 Why it still matters

Positional lookup (`P{n} → table[n]`) is unaffected. But two derivations assume ordering:

- `max(freq_table)` as the DVFS ceiling — `sampler.py:266` (`E-Cluster_max_freq_MHz`),
  `sampler.py:269` (`P-Cluster_max_freq_MHz`), `sampler.py:322` (`gpu max_freq_MHz`).
  **All three already use `max()`, which is order-independent — so they are correct.**
- `_bucket_for_freq_ratio(freq_mhz / max_freq)` (`sampler.py:487-493`) — also ratio-based
  against `max()`, therefore order-independent.

The code is right; the docstrings are wrong and would lead a future maintainer to "optimize"
`max(freq_table)` into `freq_table[-1]`, which **would** silently break the ceiling
(`table[-1]` = 1578 here by luck, but not guaranteed).

### 1.3 Changes

**`actop/native_sys.py:294`** — in `get_dvfs_tables_native`'s docstring, replace:

```
    MHz values in ascending frequency order (indexed by V-state or P-state).
```

with:

```
    MHz values indexed by V-state / P-state position. NOT guaranteed monotonic:
    Apple's GPU voltage-states table on M4 Max reads
    [0, 338, ..., 1312, 1242, 1380, 1326, 1470, 1578] — non-ascending, with a
    duplicate. Positional lookup is still correct; anything needing the DVFS
    ceiling must use max(), never table[-1].
```

**`actop/sampler.py:451-453`** — in `_compute_residency_metrics`'s docstring, replace
"from lowest to highest frequency" with "indexed by state position (not necessarily
ascending — see native_sys.get_dvfs_tables_native)".

**`actop/sampler.py:496-507`** — in `_compute_residency_distribution`'s docstring, add after
the existing ceiling sentence: "`max(freq_table)` is used rather than the last element
precisely because the table is not guaranteed ordered."

Also add a one-line comment at each of `sampler.py:266`, `:269`, `:322`:
`# max(), not [-1]: the DVFS table is not guaranteed ordered.`

### 1.4 Verification

Docstring-only; no behavior change. `pytest -q` must stay green. No new test (a test asserting
docstring text would be structural and is forbidden).

---

## 2. `Hz → MHz` truncates instead of rounding

**Severity:** low, systematic downward bias ≤ 1 MHz.

**`actop/native_sys.py:361`**

```python
freqs.append(freq_hz // 1_000_000)  # current: floors
```

A 1,499,800,000 Hz state reports `1499` MHz, not `1500`. Change to:

```python
freqs.append(round(freq_hz / 1_000_000))
```

### 2.1 Interaction to check

`native_sys.py:362-363` filters candidate tables:

```python
real_count = sum(1 for f in freqs if f > 50)
if real_count >= max(1, len(freqs) // 2):
```

Rounding can only move a value up by <1 MHz, so a table classified before remains classified
(no entry crosses the `> 50` boundary from a sub-MHz change unless it sat at exactly 50.0–50.9,
which no observed table does). Confirm on hardware that `get_dvfs_tables_native()` returns the
same *set* of tables and the same lengths before/after.

### 2.2 Observed values on this machine

Truncation currently loses nothing on M4 Max — all real entries are exact multiples of 1 MHz
(`338, 618, 796, ...`). The fix is pre-emptive correctness for chips whose tables are not.
State this in the CHANGELOG so the entry isn't mistaken for a visible fix.

### 2.3 Verification

`@pytest.mark.local` test through the public surface: `Monitor().get_snapshot()` and assert
`ecpu_max_freq_mhz`/`pcpu_max_freq_mhz`/`gpu_max_freq_mhz` are all `> 0` and unchanged from
the pre-change values recorded in the PR description (M4 Max: 1470 / 2364 / 1578).

---

## 3. Memory reports GiB but labels it GB — while bandwidth is genuinely decimal

**Severity:** correctness of a public contract. **This is the highest-value item for actop's
actual audience.**

### 3.1 The defect

**`actop/utils.py:26-27`**

```python
def convert_to_GB(value):
    return round(value / 1024 / 1024 / 1024, 1)  # divides by 2^30 = GiB
```

Feeds `total_GB`, `free_GB`, `used_GB`, `swap_total_GB`, `swap_used_GB`, `swap_free_GB`
(`utils.py:46-52`) → `SystemSnapshot.ram_used_gb` / `ram_total_gb` / `swap_used_gb` /
`swap_total_gb` (`api.py:113-118`) → export keys `ram_used_gigabytes`, `swap_used_gigabytes`
(`export.py:33-34`) → TUI `RAM 66.7/128.0GB` (`widgets.py:1006`).

Per IEC 80000-13 / ISO: `1 GB = 10⁹`, `1 GiB = 2³⁰ = 1,073,741,824`. Dividing by 2³⁰ and
printing "GB" is wrong by standard — it is the one option with no defense.

Meanwhile `bandwidth_gbps` is **genuinely decimal**: the DCS bucket labels are literally
`"32GB/s"`, `"64GB/s"`, and Apple's published M4 Max figure is 546 GB/s decimal (= 508 GiB/s).

### 3.2 Why GiB (not decimal GB) is the correct fix for memory

Both relabeling and re-dividing are standards-compliant, but they are not equally *physical*:

- `hw.memsize` = 137,438,953,472 = `128 × 2³⁰` **exactly**; `hw.pagesize` = 16384 = `2¹⁴`.
  Memory is addressed and manufactured in powers of two, so `page_count × page_size` is
  natively binary. In GiB it is an exact `128`; in decimal GB it is `137.4`, a non-round
  number that obscures the physical quantity.
- Bandwidth has no power-of-two structure (clock × bus width) and Apple reports it decimally.

**Correct end state: GiB for memory, decimal GB/s for bandwidth.** The two panels using
different bases is correct — the quantities differ in base. Only the *label* is wrong today.

### 3.3 Why the fix must reach the API, not just the TUI

The cheap non-breaking option is to change only the display string. **Reject it.** For an
audience that pipes NDJSON into analysis, `ram_total_gb` holding GiB is a silent landmine:
an engineer computing `bandwidth_gbps / model_size_gb` mixes bases and picks up a **7.4%
error** with no indication. Fixing the label while leaving the field name wrong protects the
casual reader and misleads the actual user.

### 3.4 PR D (minor, additive, non-breaking)

1. **`actop/utils.py`** — rename the helper and add an explicit unit docstring:

```python
def convert_to_GiB(value):
    """Bytes → GiB (2^30). Distinct from the decimal GB/s used for bandwidth:
    memory is natively binary (hw.memsize is an exact multiple of 2^30), bus
    bandwidth is not. See docs/TODO-reading-plane-audit-2026-07-29.md §3."""
    return round(value / 1024 / 1024 / 1024, 1)


convert_to_GB = convert_to_GiB  # deprecated misnomer; removed in 2.0.0
```

2. **`actop/utils.py:45-53`** — emit **both** key sets from `get_ram_metrics_dict()`
   (`total_GiB` + `total_GB`, etc.), same values, `*_GB` marked deprecated in a comment.

3. **`actop/models.py:68-69, 82-84`** — add `ram_used_gib`, `ram_total_gib`, `swap_used_gib`,
   `swap_total_gib`, all defaulted (`= 0.0`), documented as the correct-unit fields; keep the
   `*_gb` fields, comment-marked deprecated. Follow the existing defaulted-field precedent
   already used for `*_max_freq_mhz`.

4. **`actop/api.py:113-118`** — populate both sets from the dict.

5. **`actop/export.py:33-34`** — add `("ram_used_gib", "ram_used_gibibytes")`,
   `("swap_used_gib", "swap_used_gibibytes")` to `_PROM_GAUGES`, keeping the existing
   `*_gigabytes` gauges. `snapshot_to_dict` (`export.py:39`) picks up the new fields
   automatically via dataclass iteration — **verify** that, since it changes NDJSON output
   shape and there is an existing export-contract test.

6. **`actop/tui/widgets.py:1006`** — label becomes `RAM 66.7/128.0GiB sw:.../...GiB`.
   Check width: `GiB` is one char wider per occurrence (two occurrences + two swap = +4
   chars). The `stack`/`grid` presets auto-degrade below ~96 cols
   (`widgets.py` `set_layout_preset`) — re-capture at 80, 96 and 200 cols and confirm no
   truncation. Use the `capture-tui-diagram` skill to refresh `docs/SPEC-system.md` §5 and
   the README if the frame changes.

7. **Docs** — update `CLAUDE.md` module table row for `utils.py` and `README.md` where memory
   units appear.

### 3.5 PR F (major, breaking — defer)

Remove `convert_to_GB` alias, the `*_GB` dict keys, the `*_gb` snapshot fields, and the
`*_gigabytes` Prometheus gauges. Requires a 2.0.0 per `CLAUDE.md` ("major reserved for
breaking API/CLI changes"). Announce in the PR D CHANGELOG entry so consumers get a window.

### 3.6 Verification

- Export contract test (already functional, drives real NDJSON/Prometheus): extend to assert
  both `ram_used_gigabytes` and `ram_used_gibibytes` present and numerically equal in PR D.
- `@pytest.mark.local`: `Monitor().get_snapshot()` → assert
  `ram_total_gib * 2**30` ≈ `hw.memsize` within one rounding step (0.1 GiB). This is the
  assertion that would have caught the mislabel, and it belongs inside the existing
  behavioral memory test rather than standing alone.
- Manual: `RAM` row against Activity Monitor (expect agreement — see §7.1).

---

## 4. `_resolve_state_freq` returns `0` vs `None` inconsistently; the two consumers disagree

**Severity:** latent on M1–M4. Triggers on any chip whose state count exceeds its DVFS table
— i.e. exactly the unknown-future-chip path `soc_profiles` tier fallback exists to serve.

### 4.1 The inconsistency

**`actop/sampler.py:545-572`** — `_resolve_state_freq` has three outcomes:

| Input | Returns | Line |
|-------|---------|------|
| plain int name (`"600"`) | that int | `552` |
| `V{n}P{m}` / `P{n}`, index **in** range | `freq_table[idx]` | `561`, `569` |
| `V{n}P{m}` / `P{n}`, index **out of** range | **`0`** | `562`, `570` |
| unrecognized name | `None` | `572` |

Consumers treat `0` and `None` differently:

**`_compute_residency_metrics` (`sampler.py:463-481`)**

```python
freq_mhz = _resolve_state_freq(name, freq_table)
if freq_mhz is None:  # line 470 — only None is skipped
    continue
active_ns += ns  # line 473 — a 0 MHz state counts as ACTIVE
weighted_freq_sum += freq_mhz * ns
```

→ an out-of-range state **inflates `active_pct`** while dragging `avg_freq` **down**.

**`_compute_residency_distribution` (`sampler.py:519-522`)**

```python
if freq_mhz is None or freq_mhz <= 0 or max_freq <= 0:
    bucket_ns["idle"] += ns  # both 0 and None bucket as IDLE
```

Same input, opposite classification. `gpu_util_pct` and `gpu_residency_pct` — both on the same
`SystemSnapshot`, both shown in the same TUI panel — can therefore contradict each other.

### 4.2 Hardware probe result (recorded so this isn't re-litigated)

Enumerated **all 316** real state entries across every `CPU Stats` / `GPU Stats` channel on
M4 Max:

```
idle-name : 33      (OFF / IDLE / DOWN — handled before resolution)
resolved  : 283
ZERO      : 0       <- the buggy path is never hit on this chip
None      : 0
```

DVFS table sizes `ecpu=7, pcpu=25, gpu=16` vs. state counts — no channel exceeds its table.
GPU states are `OFF, P1..P15`, so the `table[0] == 0` entry is **never referenced** (see §7.2).

### 4.3 Fix

Make "cannot resolve" a single outcome, and make "resolved to zero" idle in both consumers.

**`actop/sampler.py:562` and `:570`** — return `None` instead of `0` for an out-of-range index:

```python
m = _VP_PATTERN.match(name)
if m:
    idx = int(m.group(1))
    if 0 <= idx < len(freq_table):
        return freq_table[idx]
    return None  # was: return 0 — out of range is unresolvable, not 0 MHz
```

(same for the `_P_PATTERN` branch at `:569-570`), and update the docstring at `:546-548`
to state that `None` means unresolvable and that callers must treat a resolved `0` as idle.

**`actop/sampler.py:470`** — align the active test with the distribution's:

```python
if freq_mhz is None or freq_mhz <= 0:
    continue  # unresolvable OR a real 0 MHz state is not "active"
```

Both consumers now agree for every input. Keep `_compute_residency_distribution`'s condition
as-is (it is already correct).

### 4.4 Verification

Not unit-testable under the functional-tests-only mandate. Instead add this invariant
**inside the existing behavioral `Monitor` snapshot test** (`@pytest.mark.local`), for each
of the three domains:

```python
# active% and the residency distribution are two views of the same residency
# data and must not contradict: active ≈ 100 - idle, within integer rounding
# and the largest-remainder redistribution (≤2 points).
assert abs(snap.gpu_util_pct - (100 - snap.gpu_residency_pct["idle"])) <= 2
```

Tolerance rationale: `active_pct` uses `int()` (§5) while the buckets use largest-remainder
apportionment, so exact equality is not expected — but a ≥3-point gap indicates the two
functions have diverged, which is precisely the bug class.

Re-run the §4.2 probe on any newly supported chip.

---

## 5. Percentages truncate (`int()`), biasing every reading downward

**Severity:** low but pervasive and systematic.

### 5.1 The statistics

`floor` is a **biased estimator**: for a uniformly distributed fractional part its expected
error is **−0.5** units with max error 1.0. `round()` has expected error **0** and max error
0.5. Python's `round()` is round-half-to-even (IEEE 754 default), unbiased on ties — better
than round-half-up, which biases upward. So `round()` is strictly the better estimator; 99.9%
currently displays as `99%`.

### 5.2 Sites to change

| File:line | Expression |
|-----------|-----------|
| `power_scaling.py:7` | `return max(0, min(100, int(percent_value)))` |
| `sampler.py:479` | `avg_freq = int(weighted_freq_sum / active_ns)` |
| `sampler.py:480` | `active_pct = int(active_ns / total_ns * 100)` |
| `utils.py:36` | `min(100, int(used_bytes / total_bytes * 100))` |
| `utils.py:41` | `swap_used_percent = int(swap.used / swap.total * 100)` |

Change each `int(x)` → `round(x)`.

### 5.3 MUST NOT change — `_largest_remainder_percentages`

**`actop/sampler.py:530-538`** uses `int()` as **Hamilton's apportionment method**, not as
rounding: floor every share, then hand the leftover to the largest fractional parts so the
buckets sum to exactly 100.

```python
floors = {name: int(raw[name]) for name in order}  # line 533 — LEAVE AS int()
remainder = 100 - sum(floors.values())
```

Replacing these floors with `round()` breaks the sum-to-100 guarantee (`remainder` could go
negative and the `fracs[:remainder]` slice would silently distribute nothing). Add a comment
saying so, since a future sweep will otherwise "fix" it.

### 5.4 Behavior change to disclose

`clamp_percent` is a **documented public function** (`CLAUDE.md` lists it as a valid test
target) and is called from `power_to_percent` (`power_scaling.py:36`), `bandwidth_percent`
(`analytics.py:75`), `package_power_percent` (`analytics.py:84`), `api.py:71` (`ane_util_pct`)
and five TUI sites (`widgets.py:887-891`).

Consequence: `bandwidth_percent` / `package_power_percent` feed the `MEM-BOUND` and `PKG`
alert thresholds through `AlertEngine`. Rounding up where it previously truncated down means
an alert sitting **exactly** on its threshold can fire **one sample earlier**. Tiny, but it is
an alerting behavior change, not merely a display change — call it out in the CHANGELOG.

### 5.5 Verification

- `clamp_percent` and `power_to_percent` are public and already have functional tests; update
  expected values and add boundary cases (`99.5 → 100`, `0.4 → 0`, `-1 → 0`, `101 → 100`).
- Existing `AlertEngine` tests exercise threshold crossings through the public `feed()` —
  re-check any test asserting an exact fire sample index.
- `@pytest.mark.local` sanity: `Monitor().get_snapshot()` percentages remain within `[0, 100]`.

---

## 6. `bandwidth_available` reports presence of a channel, not presence of data

**Severity:** low, cosmetic.

**`actop/sampler.py:335`**

```python
"_available": bool(dram_bw_channels),
```

`dram_bw_channels` is truthy as soon as an `AMCC * RD+WR` channel was *found*, regardless of
whether it carried residency. A live-but-silent channel therefore surfaces
`bandwidth_available=True` with `bandwidth_gbps=0.0`, and the TUI shows `Mem BW 0.0 GB/s`
instead of hiding the row — the exact misleading-zero the hide-row logic
(`widgets.py:1019-1030`, and the `models.py:70-71` contract) exists to prevent.

### 6.1 Fix

```python
        total_residency = sum(ns for ch in dram_bw_channels for _name, ns in ch)
        bandwidth_metrics = {
            "total_gbps": total_gbps,
            # Available only when a channel exists AND carried residency: a
            # present-but-silent channel would otherwise surface a misleading
            # 0.0 GB/s instead of hiding the row.
            "_available": bool(dram_bw_channels) and total_residency > 0,
        }
```

### 6.2 Risk — do not make the row flicker

`_average_samples` (`sampler.py:157-160`) folds the bool across subsamples with `any()`, so a
single good subsample keeps it available; that is the desired direction. Confirm the row does
not blink on the first frame after start (`--interval 1 --avg 30`, watch for one cycle) or
when the machine is fully idle — if `total_residency` is ever legitimately 0 on an idle
machine the row would vanish, which is worse than showing 0.0.

Hardware note: on M4 Max at idle, total residency was **4396 ticks** (99% in the bottom
bucket), i.e. comfortably non-zero — so this is safe here, but re-check on a fanless/low-power
Mac before merging.

### 6.3 Verification

`@pytest.mark.local`: `Monitor().get_snapshot()` → assert
`snap.bandwidth_available == (snap.bandwidth_gbps > 0)` on a machine with a DCS channel.
Fold into the existing bandwidth behavioral test rather than adding a standalone bounds test.

---

## 7. Hypotheses investigated and DISPROVED — do not re-open

### 7.1 Memory formula is **correct**

actop's `AppMem + Wired + Compressed` (`native_sys.py:489-493`) was cross-checked against
menubar-load-runner's independent `total − (free + purgeable + external)`:

```
actop:  AppMem(40.0) + Wired(26.3) + Compressed(0.4) = 66.7 GiB (52.1%)
mblr:   total − (free+purgeable+external)            = 67.4 GiB (52.7%)
```

Agreement within 1%. (An earlier apparent 94 GiB discrepancy was a hand-rolled
`vm_statistics64` struct with wrong field offsets in the audit probe — not an actop defect.)
The formula matches Activity Monitor's "Memory Used" by construction. **No change needed.**

### 7.2 GPU `table[0] == 0` is **never referenced**

IOReport GPU states are `OFF, P1, P2, ..., P15` — there is no `P0`. `P{n} → table[n]` means
indices 1–15 map to 338–1578 MHz and the 0 MHz entry at index 0 is unreachable. **Not a bug.**

### 7.3 DVFS `<II` stride is **correct** — see §1.1. Not a stride bug.

### 7.4 Watts pipeline is **correct**

`sampler.py:255` scales by `interval / elapsed_s`; `api.py:68-70, 98` divides by `interval_s`.
These cancel to `energy_J / elapsed_s`. Verified algebraically for both single-sample and
`subsamples > 1` (each part scales by `N`, averaging over `N` parts recovers `P·T`).
`_energy_to_joules` (`sampler.py:392-404`) maps `nJ`/`µJ`/`mJ`/`J` correctly.
**No change needed.**

### 7.5 `Device Utilization %` is **not** a better primary GPU metric

Side-by-side, idle, M4 Max:

```
actop= 11.0% @ 338MHz   IOAccel Device=7%  Renderer=7%  Tiler=3%
actop= 15.0% @ 359MHz   IOAccel Device=28% Renderer=28% Tiler=8%
actop= 39.0% @ 465MHz   IOAccel Device=37% Renderer=37% Tiler=8%
actop= 40.0% @1232MHz   IOAccel Device=91% Renderer=9%  Tiler=1%
```

They track loosely but diverge hard per-sample because they measure different things: actop
integrates power-state residency **over the sampling interval**; `Device Utilization %` is a
driver-reported **point read** with no interval. Swapping the primary metric would trade an
interval-averaged value for an instantaneous one — a regression in sampling semantics for a
sampling monitor. Adopt it as **fallback + breakdown** only (§8).

---

## 8. Adopt `IOAccelerator` PerformanceStatistics — fallback + Renderer/Tiler breakdown

**Status: SHIPPED in v1.6.0.** As-built design is documented in `docs/SPEC-system.md` §3.8;
the plan below is retained as the originating spec. Two deviations from it, both deliberate:

* **No service caching.** §8.3 called for caching the matched `io_object_t` the way `SMCReader`
  does. Measured cost is **0.025 ms/call** — 33× cheaper than the `get_gpu_time_by_pid()` walk
  already running every frame — so the reader stays a stateless function matching its
  neighbour in the same module. Caching would have added a stale-handle failure mode to buy
  nothing.
* **The fallback branch has no automated test.** §8.5 asked for one. It is unreachable on
  M1–M4 (every shipped chip's DVFS table classifies), and forcing it would require a mock,
  which the testing mandate forbids. Verified by inspection; the provenance field
  (`gpu_util_source`) is asserted on the reachable branch instead.

**Severity:** enhancement (robustness + a metric actop cannot currently express).

### 8.1 Motivation

Two independent wins:

1. **Robustness.** `gpu_util_pct` and `gpu_freq_mhz` both depend on the GPU DVFS table being
   correctly classified by `_classify_dvfs_tables` (`native_sys.py:371-395`), whose heuristics
   are frankly fragile — "P-core: `len >= 15` and highest max", "E-core: `5 <= len <= 12`",
   "GPU: 10-20 entries". If classification picks the wrong table or none, GPU metrics silently
   degrade to 0 with no signal. `Device Utilization %` needs no DVFS table at all.
2. **New signal.** `Renderer Utilization %` vs `Tiler Utilization %` distinguishes
   shader/compute work from geometry work. For the ML/inference audience this is genuinely
   informative: MLX/CoreML compute shows as **Renderer** with Tiler near zero, which is
   visible in the §7.5 trace (`Device=91% Renderer=9% Tiler=1%` — a non-render-bound frame).

### 8.2 Data source, confirmed present

`ioreg -r -c IOAccelerator -d 1 -w0` exposes, inside the `PerformanceStatistics` dict:

```
"Device Utilization %"=12, "Renderer Utilization %"=12, "Tiler Utilization %"=5,
"In use system memory"=2528591872, "Alloc system memory"=31297290240, ...
```

Multiple accelerator nodes match; some report 0. Take the entry with the **highest**
`Device Utilization %` (mirrors menubar-load-runner's "max across matched accelerators"), and
match `"IOAccelerator"` first with `"AGXAccelerator"` as fallback.

### 8.3 Where the code goes

**Do not shell out to `ioreg`** — a subprocess per frame blows the idle-CPU budget the sampler
docstrings explicitly protect (`sampler.py:37-38`, `:575-580`). Use ctypes.

`actop/gpu_registry.py` **already has every primitive needed**, so no new bindings:

| Available | Line |
|-----------|------|
| `IOServiceMatching`, `IOServiceGetMatchingServices` | `30-34` |
| `IORegistryEntryCreateCFProperty` | `53-54` |
| `CFDictionaryGetValue` | `85-86` |
| `_cfstr`, `_from_cfstr`, `_cfnumber_to_int` | `94`, `98`, `107` |
| existing `IOAccelerator` class-match precedent + comment | `165-168` |

Add to `actop/gpu_registry.py`:

```python
GPUPerfStats = namedtuple("GPUPerfStats", "device_pct renderer_pct tiler_pct available")


def get_gpu_perf_stats():
    """Device/Renderer/Tiler utilization % from IOAccelerator PerformanceStatistics.

    Driver-reported point reads (no interval integration) — complementary to the
    IOReport power-state residency in sampler.py, not a replacement for it. Takes
    the accelerator reporting the highest Device Utilization %, since several
    nodes match and idle ones report 0. Returns available=False off-Darwin or
    when the property is absent.
    """
```

Cache the matched service like `SMCReader` does (`smc.py:364-380`: discover once, reuse) —
re-enumerating IOKit every frame is the cost to avoid. Measure with
`--interval 1` and confirm idle CPU does not regress; if enumeration proves too costly, cache
the `io_object_t` and only re-enumerate on read failure.

### 8.4 Wiring

- **`actop/models.py`** — add defaulted fields (follow the `*_max_freq_mhz` precedent):

```python
    # GPU utilization from IOAccelerator PerformanceStatistics (driver point
    # reads, no interval integration — see docs/TODO-reading-plane-audit §8).
    # gpu_util_pct above remains the interval-integrated primary metric.
    gpu_device_pct: float = 0.0
    gpu_renderer_pct: float = 0.0
    gpu_tiler_pct: float = 0.0
    gpu_perf_stats_available: bool = False
    # "residency" (IOReport, preferred) or "ioaccelerator" (fallback when the
    # GPU DVFS table could not be classified).
    gpu_util_source: str = "residency"
```

- **`actop/api.py`** — read via `get_gpu_perf_stats()` alongside the RAM dict in
  `Monitor.get_snapshot()`, pass into `_sample_to_snapshot` (`api.py:53-58`) as a new
  parameter, populate the fields near `gpu_util_pct` (`api.py:101`).
- **Fallback rule**, in `_sample_to_snapshot` (source selection is adapter-level, not an L2
  domain judgment, so it does **not** belong in `analytics.py`):

```python
    # Fall back to the driver's utilization only when residency is unusable —
    # i.e. the DVFS table was not classified, so gm["max_freq_MHz"] is 0 and
    # gpu_util_pct/gpu_freq_mhz are meaningless.
    gpu_util = float(gm["active"])
    gpu_util_source = "residency"
    if int(gm.get("max_freq_MHz", 0)) <= 0 and perf.available:
        gpu_util = float(perf.device_pct)
        gpu_util_source = "ioaccelerator"
```

- **`actop/export.py:33`** — add `("gpu_device_pct", "gpu_device_utilization_percent")`,
  `("gpu_renderer_pct", "gpu_renderer_utilization_percent")`,
  `("gpu_tiler_pct", "gpu_tiler_utilization_percent")` to `_PROM_GAUGES`.
  `gpu_util_source` is a **string** — it must NOT go in `_PROM_GAUGES` (gauges are numeric);
  it lands in `snapshot_to_dict` / NDJSON only, or as a Prometheus label if wanted later.
- **`actop/tui/widgets.py:710-720`** — extend the `GPU · ANE` section with a
  `Renderer/Tiler` row, displayed only when `gpu_perf_stats_available`, mirroring the
  `bandwidth_available` hide-row pattern (`widgets.py:1019-1030`). Width budget is tight in
  the `grid` preset; verify at 80/96/200 cols and prefer a compact form
  (`GPU R 12% · T 5%`). When `gpu_util_source == "ioaccelerator"`, mark the GPU label so the
  user knows the number's provenance changed.
- **`CLAUDE.md`** — update the `gpu_registry.py` module-table row (currently "Per-process GPU
  time via IOKit") to cover device-level perf stats, and extend the data-flow paragraph.
- **`docs/SPEC-system.md`** — document the dual GPU source and the fallback rule.

### 8.5 Verification

- `@pytest.mark.local` through `Monitor().get_snapshot()`: assert the three new percentages
  are in `[0, 100]`, `gpu_perf_stats_available is True` on Apple Silicon, and
  `gpu_util_source == "residency"` on a chip whose DVFS table classifies (M1–M4).
- Export contract test: the three new gauges appear in Prometheus output and the new fields in
  NDJSON; `gpu_util_source` present in NDJSON and **absent** from Prometheus gauges.
- TUI: mount `HardwareDashboard` via `App.run_test()` and feed real `SystemSnapshot`s with
  `gpu_perf_stats_available` both True and False; assert the row shows/hides. This is the
  sanctioned widget route (a minimal host `App` is a mount point, not a fake).
- Under real load (`scripts/ane_load.py`, or the `record-tui-gif` skill's ollama driver),
  confirm Renderer rises while Tiler stays low for compute workloads.
- Idle-CPU regression check before/after, since this adds a per-frame IOKit read.

---

## Out of scope / not defects

- **Bucket midpoint estimator** — fixed in v1.4.15; the residual ~16 GB/s idle floor is a
  hardware limit (bottom bucket is 32 GB/s wide), not a code defect. Documented in that
  CHANGELOG entry.
- **`Monitor.__init__` coerces `interval_s` via `max(1, int(interval_s))`** (`api.py:143`) —
  silently floors fractional intervals, so `interval_s=1.5` becomes `1`. The
  `interval/elapsed` cancellation still holds (both sides use the same int), so **no wrong
  reading results**; it is an input-handling wart, not a measurement error. Worth a separate
  ticket if sub-second sampling is ever wanted.
- **`_delta_ns` clamps counter resets to `≥ 0`** (`utils.py:136`) — loses time across a PID's
  counter reset rather than reporting a negative rate. Deliberate and correct.
- **`_parse_core_index` strips a trailing zero** (`sampler.py:434-435`) — correct for the
  observed `ECPU000`/`PCPU130` format on M1–M4; revisit only if a chip appears with ≥100
  cores per cluster.
