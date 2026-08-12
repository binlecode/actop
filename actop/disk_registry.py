"""Disk I/O statistics via IOKit — unprivileged, cumulative counter reads.

Walk ``AppleAPFSVolume`` entries (primary), fall back to ``IOBlockStorageDriver``
for non-APFS or older systems.  Match ``mactop``'s exact order — verified live
on-device (M4 Max, 2026-07-02) via ``ioreg -c AppleAPFSVolume -r -w0``.

Self-contained by design: owns its IOKit/CoreFoundation bindings so it does not
cross-import from ``smc.py``, ``ioreport.py``, or ``gpu_registry.py``, matching
this codebase's convention.
"""

import ctypes
import sys

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

    _cf.CFDictionaryGetValue.restype = ctypes.c_void_p
    _cf.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

    _cf.CFNumberGetValue.restype = ctypes.c_bool
    _cf.CFNumberGetValue.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
    ]

    _cf.CFRelease.restype = None
    _cf.CFRelease.argtypes = [ctypes.c_void_p]


# AppleAPFSVolume Statistics keys (primary, modern macOS).
_APFS_KEYS = (
    "Bytes read from block device",
    "Bytes written to block device",
    "Read requests sent to block device",
    "Write requests sent to block device",
)

# IOBlockStorageDriver Statistics keys (fallback, older / non-APFS).
_BLOCK_KEYS = (
    "Bytes (Read)",
    "Bytes (Write)",
    "Operations (Read)",
    "Operations (Write)",
)


def _cfstr(s):
    return _cf.CFStringCreateWithCString(None, s.encode("utf-8"), kCFStringEncodingUTF8)


def _dict_get_uint64(dict_ref, key_name):
    """Read a uint64 value for *key_name* from a CFDictionary ref.

    Returns 0 when the key is absent or conversion fails — never raises.
    """
    key = _cfstr(key_name)
    value = _cf.CFDictionaryGetValue(dict_ref, key)
    _cf.CFRelease(key)
    if not value:
        return 0
    out = ctypes.c_int64(0)
    if _cf.CFNumberGetValue(value, kCFNumberSInt64Type, ctypes.byref(out)):
        return out.value
    return 0


def _iter_matching_services(class_name):
    """Yield each io_object_t whose class matches *class_name*.

    Each entry is released as the generator moves past it — callers must not
    retain the handle beyond one loop iteration.  Same pattern as
    ``gpu_registry._iter_matching_services``.
    """
    matching = _iokit.IOServiceMatching(class_name)
    if not matching:
        return

    service_iter = ctypes.c_uint32()
    # IOServiceGetMatchingServices consumes the matching dict — do not CFRelease.
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


def _sum_statistics(service, keys):
    """Sum Statistics values for *keys* off one IOKit service entry.

    Returns (sum[0], sum[1], sum[2], sum[3]) or None when the entry has no
    Statistics dict at all.
    """
    prop_key = _cfstr("Statistics")
    stats_ref = _iokit.IORegistryEntryCreateCFProperty(service, prop_key, None, 0)
    _cf.CFRelease(prop_key)

    if not stats_ref:
        return None

    try:
        return tuple(_dict_get_uint64(stats_ref, k) for k in keys)
    finally:
        _cf.CFRelease(stats_ref)


def read_disk_totals():
    """Summed cumulative disk byte and operation counters.

    Primary: ``IOServiceMatching("AppleAPFSVolume")`` → ``Statistics`` dict
    with keys ``"Bytes read from block device"`` etc.
    Fallback: ``IOServiceMatching("IOBlockStorageDriver")`` with keys
    ``"Bytes (Read)"`` etc. for non-APFS or older systems.

    Returns ``(read_bytes, write_bytes, read_ops, write_ops, available)``.
    ``available=False`` on non-Darwin or when no volume/driver was found;
    totals are zeroed in that case.  Callers delta these against a previous
    poll and divide by the elapsed interval to get a rate.
    """
    if not _DARWIN:
        return (0, 0, 0, 0, False)

    try:
        # Primary: AppleAPFSVolume
        found = False
        read_bytes = 0
        write_bytes = 0
        read_ops = 0
        write_ops = 0

        for service in _iter_matching_services(b"AppleAPFSVolume"):
            totals = _sum_statistics(service, _APFS_KEYS)
            if totals is not None:
                found = True
                read_bytes += totals[0]
                write_bytes += totals[1]
                read_ops += totals[2]
                write_ops += totals[3]

        if found:
            return (read_bytes, write_bytes, read_ops, write_ops, True)

        # Fallback: IOBlockStorageDriver
        for service in _iter_matching_services(b"IOBlockStorageDriver"):
            totals = _sum_statistics(service, _BLOCK_KEYS)
            if totals is not None:
                read_bytes += totals[0]
                write_bytes += totals[1]
                read_ops += totals[2]
                write_ops += totals[3]

        available = read_bytes > 0 or write_bytes > 0
        return (read_bytes, write_bytes, read_ops, write_ops, available)

    except Exception:
        return (0, 0, 0, 0, False)
