"""IOReport and CoreFoundation ctypes bindings for Apple Silicon metrics.

Wraps the private IOReport C library and CoreFoundation to provide in-process
access to Apple Silicon power, frequency, and residency metrics without
requiring sudo or a powermetrics subprocess.
"""

import ctypes
from collections import namedtuple

# --- CoreFoundation bindings ---

_cf = ctypes.cdll.LoadLibrary(
    "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
)

kCFStringEncodingUTF8 = 0x08000100

_cf.CFRelease.argtypes = [ctypes.c_void_p]
_cf.CFRelease.restype = None

_cf.CFStringCreateWithCString.argtypes = [
    ctypes.c_void_p,
    ctypes.c_char_p,
    ctypes.c_uint32,
]
_cf.CFStringCreateWithCString.restype = ctypes.c_void_p

_cf.CFStringGetCString.argtypes = [
    ctypes.c_void_p,
    ctypes.c_char_p,
    ctypes.c_long,
    ctypes.c_uint32,
]
_cf.CFStringGetCString.restype = ctypes.c_bool

_cf.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_cf.CFDictionaryGetValue.restype = ctypes.c_void_p

_cf.CFDictionaryCreateMutableCopy.argtypes = [
    ctypes.c_void_p,
    ctypes.c_long,
    ctypes.c_void_p,
]
_cf.CFDictionaryCreateMutableCopy.restype = ctypes.c_void_p

_cf.CFArrayGetCount.argtypes = [ctypes.c_void_p]
_cf.CFArrayGetCount.restype = ctypes.c_long

_cf.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
_cf.CFArrayGetValueAtIndex.restype = ctypes.c_void_p

# --- IOReport bindings ---

_ior = ctypes.cdll.LoadLibrary("/usr/lib/libIOReport.dylib")

_ior.IOReportCopyChannelsInGroup.argtypes = [
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_uint64,
    ctypes.c_uint64,
    ctypes.c_uint64,
]
_ior.IOReportCopyChannelsInGroup.restype = ctypes.c_void_p

_ior.IOReportMergeChannels.argtypes = [
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_void_p,
]
_ior.IOReportMergeChannels.restype = None

_ior.IOReportCreateSubscription.argtypes = [
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_void_p),
    ctypes.c_uint64,
    ctypes.c_void_p,
]
_ior.IOReportCreateSubscription.restype = ctypes.c_void_p

_ior.IOReportCreateSamples.argtypes = [
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_void_p,
]
_ior.IOReportCreateSamples.restype = ctypes.c_void_p

_ior.IOReportCreateSamplesDelta.argtypes = [
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_void_p,
]
_ior.IOReportCreateSamplesDelta.restype = ctypes.c_void_p

_ior.IOReportChannelGetGroup.argtypes = [ctypes.c_void_p]
_ior.IOReportChannelGetGroup.restype = ctypes.c_void_p

_ior.IOReportChannelGetSubGroup.argtypes = [ctypes.c_void_p]
_ior.IOReportChannelGetSubGroup.restype = ctypes.c_void_p

_ior.IOReportChannelGetChannelName.argtypes = [ctypes.c_void_p]
_ior.IOReportChannelGetChannelName.restype = ctypes.c_void_p

_ior.IOReportChannelGetUnitLabel.argtypes = [ctypes.c_void_p]
_ior.IOReportChannelGetUnitLabel.restype = ctypes.c_void_p

_ior.IOReportSimpleGetIntegerValue.argtypes = [ctypes.c_void_p, ctypes.c_int]
_ior.IOReportSimpleGetIntegerValue.restype = ctypes.c_int64

_ior.IOReportStateGetCount.argtypes = [ctypes.c_void_p]
_ior.IOReportStateGetCount.restype = ctypes.c_int32

_ior.IOReportStateGetNameForIndex.argtypes = [
    ctypes.c_void_p,
    ctypes.c_int32,
]
_ior.IOReportStateGetNameForIndex.restype = ctypes.c_void_p

_ior.IOReportStateGetResidency.argtypes = [ctypes.c_void_p, ctypes.c_int32]
_ior.IOReportStateGetResidency.restype = ctypes.c_int64


# --- Public helpers ---


def cfstr(s):
    """Create a CFStringRef from a Python string. Caller must cf_release()."""
    return _cf.CFStringCreateWithCString(None, s.encode("utf-8"), kCFStringEncodingUTF8)


def from_cfstr(ref):
    """Convert a CFStringRef to a Python string. Does not release the ref."""
    if not ref:
        return ""
    buf = ctypes.create_string_buffer(1024)
    if _cf.CFStringGetCString(ref, buf, 1024, kCFStringEncodingUTF8):
        return buf.value.decode("utf-8")
    return ""


def cf_release(ref):
    """Release a CoreFoundation object reference."""
    if ref is None:
        return
    if isinstance(ref, ctypes.c_void_p):
        if ref.value is None:
            return
    _cf.CFRelease(ref)


IOReportItem = namedtuple(
    "IOReportItem",
    ["group", "subgroup", "channel", "unit", "integer_value", "state_residencies"],
)


def get_residencies(item_raw):
    """Get state residencies from a raw IOReport channel item pointer.

    Returns list of (state_name, residency_ns) tuples.
    """
    count = _ior.IOReportStateGetCount(item_raw)
    result = []
    for i in range(count):
        name_ref = _ior.IOReportStateGetNameForIndex(item_raw, i)
        name = from_cfstr(name_ref)
        residency = _ior.IOReportStateGetResidency(item_raw, i)
        result.append((name, residency))
    return result


class IOReportSubscription:
    """Manages IOReport channel subscription lifecycle."""

    def __init__(self, channels):
        """Create a subscription for the given channel groups.

        Args:
            channels: list of (group_name, subgroup_name_or_None) tuples.
        """
        merged = None
        for group_name, subgroup_name in channels:
            group_cf = cfstr(group_name)
            subgroup_cf = cfstr(subgroup_name) if subgroup_name else None
            try:
                ch = _ior.IOReportCopyChannelsInGroup(group_cf, subgroup_cf, 0, 0, 0)
            finally:
                cf_release(group_cf)
                if subgroup_cf is not None:
                    cf_release(subgroup_cf)
            if not ch:
                continue
            if merged is None:
                merged = _cf.CFDictionaryCreateMutableCopy(None, 0, ch)
                cf_release(ch)
            else:
                _ior.IOReportMergeChannels(merged, ch, None)
                cf_release(ch)

        if not merged:
            raise RuntimeError("IOReport: no channels found for requested groups")

        self._channels = merged
        sub_ref = ctypes.c_void_p()
        result = _ior.IOReportCreateSubscription(
            None, self._channels, ctypes.byref(sub_ref), 0, None
        )
        if not result:
            cf_release(self._channels)
            self._channels = None
            raise RuntimeError("IOReport: failed to create subscription")
        # IOReportCreateSamples needs the return value, not the output param
        self._subscription = result

    def sample(self):
        """Take a sample snapshot. Returns a raw CFDictRef.

        Caller must cf_release() the returned reference when done.
        """
        return _ior.IOReportCreateSamples(self._subscription, self._channels, None)

    def delta(self, s1, s2, extract_states=None):
        """Compute delta between two samples.

        All data is extracted into Python objects before the C delta reference
        is released, so the returned IOReportItem list is safe to use.

        Args:
            extract_states: optional callable (group, subgroup, channel) -> bool.
                When provided, per-state residencies are read only for channels
                it accepts; rejected channels return an empty ``state_residencies``
                list. This skips the expensive per-state ctypes round-trips for
                channels we don't parse (e.g. the ~90 PMP/DCS BW channels we
                ignore), keeping the sampler within its idle-CPU budget. When
                None (default), states are extracted for every channel.

        Returns list of IOReportItem namedtuples.
        """
        delta_ref = _ior.IOReportCreateSamplesDelta(s1, s2, None)
        if not delta_ref:
            return []

        items = []
        try:
            key_cf = cfstr("IOReportChannels")
            try:
                array_ref = _cf.CFDictionaryGetValue(delta_ref, key_cf)
            finally:
                cf_release(key_cf)

            if not array_ref:
                return items

            count = _cf.CFArrayGetCount(array_ref)
            for i in range(count):
                item_ref = _cf.CFArrayGetValueAtIndex(array_ref, i)
                if not item_ref:
                    continue

                group = from_cfstr(_ior.IOReportChannelGetGroup(item_ref))
                subgroup = from_cfstr(_ior.IOReportChannelGetSubGroup(item_ref))
                channel = from_cfstr(_ior.IOReportChannelGetChannelName(item_ref))
                unit = from_cfstr(_ior.IOReportChannelGetUnitLabel(item_ref))
                integer_value = _ior.IOReportSimpleGetIntegerValue(item_ref, 0)

                state_residencies = []
                if extract_states is None or extract_states(group, subgroup, channel):
                    state_count = _ior.IOReportStateGetCount(item_ref)
                    for j in range(state_count):
                        name_ref = _ior.IOReportStateGetNameForIndex(item_ref, j)
                        name = from_cfstr(name_ref)
                        residency = _ior.IOReportStateGetResidency(item_ref, j)
                        state_residencies.append((name, residency))

                items.append(
                    IOReportItem(
                        group=group,
                        subgroup=subgroup,
                        channel=channel,
                        unit=unit,
                        integer_value=integer_value,
                        state_residencies=state_residencies,
                    )
                )
        finally:
            cf_release(delta_ref)

        return items

    def close(self):
        """Release subscription resources."""
        if self._subscription is not None:
            cf_release(self._subscription)
            self._subscription = None
        if self._channels is not None:
            cf_release(self._channels)
            self._channels = None
