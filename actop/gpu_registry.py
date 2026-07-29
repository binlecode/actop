"""GPU accelerator registry reads via IOKit ctypes bindings.

Two independent reads off the chip's `IOAccelerator`-class services, both
unprivileged:

* `get_gpu_time_by_pid()` -- per-process accumulated GPU-busy time. Each open
  Metal context shows up as an `AGXDeviceUserClient` child of the accelerator,
  exposing `IOUserClientCreator` ("pid <N>, <name>") and an `AppUsage` array
  with a monotonic `accumulatedGPUTime` nanosecond counter per command queue --
  the GPU analogue of the `cpu_time_ns` counter `native_sys.py` reads for CPU.
* `get_gpu_perf_stats()` -- device-level Device/Renderer/Tiler utilization
  percentages off the accelerator's own `PerformanceStatistics` dict.

Self-contained by design: loads its own IOKit/CoreFoundation bindings rather
than importing from `smc.py` or `ioreport.py`, matching this codebase's
convention of independent, non-cross-importing native ctypes modules.
"""

import ctypes
import re
import sys
from typing import NamedTuple

_DARWIN = sys.platform == "darwin"

if _DARWIN:
    _iokit = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/IOKit.framework/IOKit")
    _cf = ctypes.cdll.LoadLibrary(
        "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
    )

    kCFStringEncodingUTF8 = 0x08000100
    kCFNumberSInt64Type = 4

    _iokit.IOServiceMatching.restype = ctypes.c_void_p
    _iokit.IOServiceMatching.argtypes = [ctypes.c_char_p]

    _iokit.IOServiceGetMatchingServices.restype = ctypes.c_int
    _iokit.IOServiceGetMatchingServices.argtypes = [
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32),
    ]

    _iokit.IOIteratorNext.restype = ctypes.c_uint32
    _iokit.IOIteratorNext.argtypes = [ctypes.c_uint32]

    _iokit.IOObjectRelease.restype = ctypes.c_int
    _iokit.IOObjectRelease.argtypes = [ctypes.c_uint32]

    _iokit.IORegistryEntryGetChildIterator.restype = ctypes.c_int
    _iokit.IORegistryEntryGetChildIterator.argtypes = [
        ctypes.c_uint32,
        ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_uint32),
    ]

    _iokit.IORegistryEntryCreateCFProperty.restype = ctypes.c_void_p
    _iokit.IORegistryEntryCreateCFProperty.argtypes = [
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]

    _cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    _cf.CFStringCreateWithCString.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_uint32,
    ]

    _cf.CFStringGetCString.restype = ctypes.c_bool
    _cf.CFStringGetCString.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_long,
        ctypes.c_uint32,
    ]

    _cf.CFRelease.restype = None
    _cf.CFRelease.argtypes = [ctypes.c_void_p]

    _cf.CFArrayGetCount.restype = ctypes.c_long
    _cf.CFArrayGetCount.argtypes = [ctypes.c_void_p]

    _cf.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
    _cf.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]

    _cf.CFDictionaryGetValue.restype = ctypes.c_void_p
    _cf.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

    _cf.CFNumberGetValue.restype = ctypes.c_bool
    _cf.CFNumberGetValue.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]

_CREATOR_PID_RE = re.compile(r"pid (\d+)")

# Accelerator classes tried in order by get_gpu_perf_stats. IOServiceMatching
# matches by class inheritance, so "IOAccelerator" already reaches the
# chip-specific subclass (e.g. AGXAcceleratorG16X) without a per-chip table;
# "AGXAccelerator" is a narrower fallback for the case where the base-class
# match returns nothing.
_ACCELERATOR_CLASSES = (b"IOAccelerator", b"AGXAccelerator")

# Keys read out of the accelerator's PerformanceStatistics dict, in
# GPUPerfStats field order. "Device Utilization %" is the one that must be
# present for the read to count as a real accelerator reading.
_PERF_STAT_KEYS = (
    "Device Utilization %",
    "Renderer Utilization %",
    "Tiler Utilization %",
)


class GPUPerfStats(NamedTuple):
    """Driver-reported GPU utilization percentages (point reads).

    These are instantaneous values the accelerator driver maintains, with no
    interval integration -- complementary to the IOReport power-state residency
    in `sampler.py`, not a replacement for it. `available` is False off-Darwin
    and when no matched accelerator exposes the statistics.
    """

    device_pct: float = 0.0
    renderer_pct: float = 0.0
    tiler_pct: float = 0.0
    available: bool = False


def _cfstr(s):
    return _cf.CFStringCreateWithCString(None, s.encode("utf-8"), kCFStringEncodingUTF8)


def _from_cfstr(ref):
    if not ref:
        return ""
    buf = ctypes.create_string_buffer(1024)
    if _cf.CFStringGetCString(ref, buf, 1024, kCFStringEncodingUTF8):
        return buf.value.decode("utf-8", errors="replace")
    return ""


def _cfnumber_to_int(ref):
    if not ref:
        return 0
    out = ctypes.c_int64(0)
    if _cf.CFNumberGetValue(ref, kCFNumberSInt64Type, ctypes.byref(out)):
        return out.value
    return 0


def _registry_prop(entry, name):
    """Copy one registry property off `entry` by name (caller CFReleases it).

    Returns a NULL-equivalent falsy ref when the entry has no such property.
    """
    key = _cfstr(name)
    ref = _iokit.IORegistryEntryCreateCFProperty(entry, key, None, 0)
    _cf.CFRelease(key)
    return ref


def _dict_get(dict_ref, name):
    """Borrowed value for a string key in a CFDictionary (do NOT CFRelease)."""
    key = _cfstr(name)
    value = _cf.CFDictionaryGetValue(dict_ref, key)
    _cf.CFRelease(key)
    return value


def _iter_matching_services(class_name):
    """Yield each io_object_t whose class matches `class_name`.

    Each entry is released as the generator moves past it, so callers must not
    retain the handle beyond one loop iteration.
    """
    matching = _iokit.IOServiceMatching(class_name)
    if not matching:
        return

    service_iter = ctypes.c_uint32()
    # IOServiceGetMatchingServices consumes the matching dict -- do not
    # CFRelease it.
    kr = _iokit.IOServiceGetMatchingServices(0, matching, ctypes.byref(service_iter))
    if kr != 0:
        return

    try:
        while True:
            service = _iokit.IOIteratorNext(service_iter.value)
            if service == 0:
                break
            try:
                yield service
            finally:
                _iokit.IOObjectRelease(service)
    finally:
        _iokit.IOObjectRelease(service_iter.value)


def _client_gpu_time_and_pid(client):
    """Read (pid, accumulated_ns) off one accelerator child entry.

    pid is None when the entry has no IOUserClientCreator (not a Metal
    client), or when its value doesn't parse -- callers skip those.
    """
    creator_ref = _registry_prop(client, "IOUserClientCreator")

    pid = None
    if creator_ref:
        match = _CREATOR_PID_RE.search(_from_cfstr(creator_ref))
        if match:
            pid = int(match.group(1))
        _cf.CFRelease(creator_ref)

    usage_ref = _registry_prop(client, "AppUsage")

    total_ns = 0
    if usage_ref:
        accum_key = _cfstr("accumulatedGPUTime")
        for i in range(_cf.CFArrayGetCount(usage_ref)):
            entry = _cf.CFArrayGetValueAtIndex(usage_ref, i)
            total_ns += _cfnumber_to_int(_cf.CFDictionaryGetValue(entry, accum_key))
        _cf.CFRelease(accum_key)
        _cf.CFRelease(usage_ref)

    return pid, total_ns


def get_gpu_time_by_pid():
    """pid -> cumulative accumulatedGPUTime (ns) right now.

    Sums across every live Metal client for that pid, across every matched
    GPU accelerator service (a multi-die chip like M1/M2 Ultra may expose
    more than one). No caching -- callers delta this against a previous
    poll themselves, the same way native_sys.get_native_processes() exposes
    raw cpu_time_ns for utils.py to delta.

    Returns {} if no GPU accelerator service is found, or on non-Darwin
    platforms where IOKit is unavailable.
    """
    result = {}
    if not _DARWIN:
        return result

    for accel in _iter_matching_services(b"IOAccelerator"):
        client_iter = ctypes.c_uint32()
        kr = _iokit.IORegistryEntryGetChildIterator(
            accel, b"IOService", ctypes.byref(client_iter)
        )
        if kr != 0:
            continue
        while True:
            client = _iokit.IOIteratorNext(client_iter.value)
            if client == 0:
                break
            pid, gpu_ns = _client_gpu_time_and_pid(client)
            if pid is not None and gpu_ns > 0:
                result[pid] = result.get(pid, 0) + gpu_ns
            _iokit.IOObjectRelease(client)
        _iokit.IOObjectRelease(client_iter.value)

    return result


def _accelerator_perf_stats(accel):
    """(device, renderer, tiler) percents off one accelerator, or None.

    None when the entry exposes no PerformanceStatistics dict, or one without a
    Device Utilization key -- those are not usable readings. Renderer/Tiler are
    read as 0 when individually absent.
    """
    stats_ref = _registry_prop(accel, "PerformanceStatistics")
    if not stats_ref:
        return None
    try:
        values = [_dict_get(stats_ref, key) for key in _PERF_STAT_KEYS]
        if not values[0]:
            return None
        return tuple(_cfnumber_to_int(v) if v else 0 for v in values)
    finally:
        _cf.CFRelease(stats_ref)


def get_gpu_perf_stats():
    """Device/Renderer/Tiler utilization % from IOAccelerator PerformanceStatistics.

    Driver-reported point reads (no interval integration) -- complementary to
    the IOReport power-state residency in `sampler.py`, not a replacement for
    it. Takes the accelerator reporting the highest Device Utilization %, since
    several nodes can match and idle ones report 0. Percentages are returned as
    the driver reports them: this is L1 acquisition, so no clamping or derived
    math happens here.

    Returns available=False off-Darwin or when no matched accelerator exposes
    the statistics.
    """
    if not _DARWIN:
        return GPUPerfStats()

    best = None
    for class_name in _ACCELERATOR_CLASSES:
        for accel in _iter_matching_services(class_name):
            stats = _accelerator_perf_stats(accel)
            if stats is not None and (best is None or stats[0] > best[0]):
                best = stats
        if best is not None:
            break

    if best is None:
        return GPUPerfStats()
    return GPUPerfStats(float(best[0]), float(best[1]), float(best[2]), True)
