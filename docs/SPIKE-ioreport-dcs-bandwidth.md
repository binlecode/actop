# SPIKE — In-process IOReport DCS memory bandwidth

**Status:** ✅ DONE — **GO** (resolved on M-series, macOS 26.5.1) · **Gates:** Tier 1 "Memory bandwidth sampling" in [TODO-architecture-roadmap.md](TODO-architecture-roadmap.md)

> **Outcome:** Total DRAM bandwidth is read in-process and unprivileged from the **`PMP` / `DCS BW`** IOReport group. The data is **not a byte counter** — each channel is a **residency histogram** over bandwidth buckets, so GB/s = Σ(bucket·time)/Σ(time). The full-range total (`AMCC RD+WR`) is implemented and live; the per-agent breakdown was **dropped** because those channels hard-cap at 32 GB/s and cannot attribute high bandwidth. See **Findings** below; the planning sections that follow describe the original spike and assume a byte counter (kept for provenance).

## Question

Can `agtop` read DRAM (DCS) memory bandwidth **in-process, unprivileged, via IOReport** — the same backend it already uses for power/frequency/residency — and at a per-sample cost that holds the Tier 1 `<0.5% idle CPU` budget?

This is a feasibility gate, not an implementation task. There is **no in-process precedent** for bandwidth in this codebase or its lineage: upstream `asitop` read the `DCS RD`/`DCS WR` rows from `powermetrics` plist output (a privileged subprocess). The hardcoded keys in `sampler.py` (`ECPU DCS RD`, `PCPU DCS RD`, `GFX DCS RD`, `MEDIA DCS`, `DCS RD`/`DCS WR`) are copied from that `powermetrics` schema — they are the *target shape*, not proof the data exists over IOReport.

## Exit criteria (go / no-go)

The spike is **done** when each of these is answered with evidence captured in this doc:

1. **Existence** — Is there an IOReport group whose channels expose DRAM bandwidth? Record the exact **group** and **subgroup** strings, and the **channel names**.
2. **Identity** — Do the channel names map to the agents we want (ECPU / PCPU / GFX / MEDIA), and is there a usable total (either a `DCS RD`/`DCS WR` channel or the sum of agents)?
3. **Units & semantics** — What does `IOReportChannelGetUnitLabel` report, and is the value a **cumulative byte counter** (→ bandwidth = Δbytes / Δt) or something else? Confirm a delta over a known workload produces a plausible GB/s figure.
4. **Privilege** — Does it work as the current unprivileged user (no `sudo`, no entitlement)? The existing three groups do; confirm DCS is not gated differently.
5. **Cost** — With the DCS group **added to the real subscription**, does the sampler's idle CPU stay within the `<0.5%` budget vs. today's three-group baseline?

**GO** → proceed to implementation step (b). **NO-GO** (no in-process source, requires privilege, or busts the budget) → the bandwidth item drops to Tier 3 or is cut; record which and why.

## Step 1 — Enumerate channels (existence + identity + units)

The bindings in `agtop/ioreport.py` already cover everything except a whole-registry enumeration call. Add one binding for the spike:

```python
# IOReportCopyAllChannels(uint64, uint64) -> CFDictionaryRef (all channels, all groups)
_ior.IOReportCopyAllChannels.argtypes = [ctypes.c_uint64, ctypes.c_uint64]
_ior.IOReportCopyAllChannels.restype = ctypes.c_void_p
```

Discovery script — **save under `tmp/` (scratch)**, run on real hardware:

```python
# tmp/dcs_discover.py
import ctypes, time
from agtop import ioreport as ir  # reuse _ior/_cf, cfstr, from_cfstr, cf_release

ir._ior.IOReportCopyAllChannels.argtypes = [ctypes.c_uint64, ctypes.c_uint64]
ir._ior.IOReportCopyAllChannels.restype = ctypes.c_void_p

chans = ir._ior.IOReportCopyAllChannels(0, 0)
mutable = ir._cf.CFDictionaryCreateMutableCopy(None, 0, chans)
ir.cf_release(chans)

sub_ref = ctypes.c_void_p()
sub = ir._ior.IOReportCreateSubscription(None, mutable, ctypes.byref(sub_ref), 0, None)

s1 = ir._ior.IOReportCreateSamples(sub, mutable, None)
time.sleep(1.0)                      # generate memory traffic in another shell meanwhile
s2 = ir._ior.IOReportCreateSamples(sub, mutable, None)

delta = ir._ior.IOReportCreateSamplesDelta(s1, s2, None)
arr = ir._cf.CFDictionaryGetValue(delta, ir.cfstr("IOReportChannels"))
n = ir._cf.CFArrayGetCount(arr)

seen_groups = set()
for i in range(n):
    it = ir._cf.CFArrayGetValueAtIndex(arr, i)
    g  = ir.from_cfstr(ir._ior.IOReportChannelGetGroup(it))
    sg = ir.from_cfstr(ir._ior.IOReportChannelGetSubGroup(it))
    ch = ir.from_cfstr(ir._ior.IOReportChannelGetChannelName(it))
    un = ir.from_cfstr(ir._ior.IOReportChannelGetUnitLabel(it))
    iv = ir._ior.IOReportSimpleGetIntegerValue(it, 0)
    seen_groups.add((g, sg))
    blob = f"{g}/{sg}/{ch}".lower()
    if any(k in blob for k in ("dcs", "dram", "amc", "dmc", "mcc", "bandwidth", "mem")):
        print(f"[HIT] grp={g!r} sub={sg!r} ch={ch!r} unit={un!r} delta={iv}")

print("\n--- all (group, subgroup) pairs ---")
for g, sg in sorted(seen_groups):
    print(f"  {g!r} / {sg!r}")
```

**Candidate groups to look for** (unknown until enumerated): names containing `AMC` (Apple Memory Controller), `DCS`, `DRAM`, `DMC`, `MCC`. The match must produce the per-agent channel names we want.

**Record in this doc:** the full `(group, subgroup)` list, every `[HIT]` line, and the unit label. The `delta=` value over a 1 s window with active memory traffic tells us the counter is live and roughly byte-scaled.

## Step 2 — Validate units & semantics

With the group/subgroup from Step 1, subscribe to **just that group** (reuse `IOReportSubscription([(group, subgroup)])`) and:

- Run a known memory-bound workload (e.g. a large `numpy` copy, or `memory_pressure`/`stress` in another shell) and confirm summed `DCS RD + DCS WR` Δ / elapsed yields a believable GB/s — sane vs. the SoC's `soc_profiles.py` bandwidth ceiling (e.g. ≤ ~100 GB/s base, ~400 GB/s Max-tier).
- Confirm idle Δ is near-zero (counter is cumulative, not a fixed gauge).
- Confirm the value comes through `IOReportSimpleGetIntegerValue` (simple counter), **not** state residencies — the existing `delta()` already extracts `integer_value`, so the current parse path works unchanged if so.

**Decision recorded:** unit is bytes / pages / something else, and the divisor (`/ elapsed_s`, matching `api.py:22`) that converts to GB/s.

## Step 3 — Privilege check

Run Steps 1–2 as the normal user with no `sudo`. If channels are present but values are zero or the subscription fails where the three existing groups succeed, the group is privilege-gated → **NO-GO** for the unprivileged thesis. Record the observed behavior.

## Step 4 — Cost measurement (the budget caveat)

This is the part the old TODO got wrong: the kernel snapshot cost is paid as soon as DCS channels are in the subscription, regardless of whether Python parses them. So measure the **subscription cost**, not the parse cost.

- Baseline: run the real sampler loop (three groups, `interval=1`) idle for ~60 s; record process CPU time (`time.process_time()` before/after, or `psutil.Process().cpu_percent()` over the window).
- Treatment: same loop with the DCS group added to `IOReportSubscription.__init__`'s channel list; record again.
- Δ must keep idle CPU `< 0.5%`. Record both numbers and the delta.

If the snapshot cost alone busts the budget, note whether dynamic add/remove of the DCS group on row-visibility toggle is worth the complexity, or whether the item is cut.

## Deliverable

Fill in the **Findings** section below and flip the gate in the TODO. Discovery script stays in `tmp/` (throwaway); the only production change this spike justifies is the one-line `IOReportCopyAllChannels` binding *if* we choose to keep an enumeration helper — otherwise implementation step (b) just adds the confirmed `(group, subgroup)` to the existing subscription list.

## Findings (resolved on real hardware)

- **Hardware / OS:** Apple Silicon (Max/Ultra-tier, ~1 TB/s AMCC bucket range), macOS 26.5.1, unprivileged user.
- **Group / subgroup:** **`PMP` / `DCS BW`** (found by enumerating all 11,434 channels via `IOReportCopyAllChannels(0,0)`). Energy `DCS`/`DRAM`/`AMCC` channels exist too but are **mJ energy**, not bandwidth; the `Bandwidth` group is **PCIe** only.
- **Channel names → agent mapping:** `AMCC RD/WR/RD+WR` = total DRAM controller; `EACC0` = E-cores; `PACC0`/`PACC1` = P-clusters; `AGX` = GPU; `ANE0 L0/L1` = Neural Engine; `AVE*/AVD*/PRORES*/SCODEC*/JPEG*` = Media; plus `ISP*`, `DISP*`, `ATC*` (Thunderbolt), `ANS` (storage).
- **Unit label & semantics:** unit string is `events`, but `IOReportSimpleGetIntegerValue` returns `INT64_MIN` (sentinel) — these are **state/residency channels, not simple counters**. Each has 32 states whose *names* are bandwidth buckets (`32GB/s`, `64GB/s`, …) and whose *values* are residency time. **GB/s = Σ(bucket·time)/Σ(time)** — already GB/s, no interval division. Structurally identical to the DVFS residency the sampler already parses.
- **Bucket ranges (critical):** `AMCC` steps 32 GB/s → 1024 (full range, authoritative total). `EACC/PACC/AGX/AVE` step 1 GB/s → **hard cap 32**. `ANE` steps 2 → 64. Under load both P-clusters pegged at 32 while AMCC read 350 → **per-agent channels cannot attribute high bandwidth**; only the AMCC total is trustworthy.
- **Sample GB/s under load vs. ceiling:** idle ≈ 32 GB/s (lowest bucket floor); 8 forked `memcpy` workers → **AMCC RD+WR = 350 GB/s**, residency concentrated in 320–352 GB/s buckets. Responsive and accurate.
- **Unprivileged?** **Yes** — no `sudo`, same as the existing three groups.
- **Idle CPU (marginal cost of the feature, one sample/cycle @1s):** baseline 3 groups ≈ 0.54%; **+DCS with state-extraction allowlist (production) → +0.39%** (under the 0.5% budget); +DCS *unfiltered* → +0.70% (over budget). The `delta()` allowlist filter (extract states only for `AMCC*`) is what keeps it in budget.
- **Verdict:** **GO.** Implemented: total only (`AMCC RD+WR` summed across dies). Per-agent breakdown dropped — the 32 GB/s cap makes it misleading at the bandwidths that matter.
