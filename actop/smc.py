"""SMC (System Management Controller) reader via IOKit ctypes.

Reads temperature sensors and fan tachometers from Apple Silicon's AppleSMC
service without requiring sudo. Uses dynamic key discovery to find CPU
(Tp*/Te*), GPU (Tg*) temperature keys and per-fan actual-RPM keys (F{n}Ac,
one per fan reported by the `FNum` key) and classify them.

Temperature and fan-RPM keys both use SMC type "flt " (4-byte IEEE 754 float).
"""

import ctypes
import struct
import sys
from typing import NamedTuple

_DARWIN = sys.platform == "darwin"

# --- IOKit bindings ---

if _DARWIN:
    _iokit = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/IOKit.framework/IOKit")

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

    _iokit.IORegistryEntryGetName.restype = ctypes.c_int
    _iokit.IORegistryEntryGetName.argtypes = [ctypes.c_uint32, ctypes.c_char_p]

    _iokit.IOServiceOpen.restype = ctypes.c_int
    _iokit.IOServiceOpen.argtypes = [
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
    ]

    _iokit.IOServiceClose.restype = ctypes.c_int
    _iokit.IOServiceClose.argtypes = [ctypes.c_uint32]

    _iokit.IOConnectCallStructMethod.restype = ctypes.c_int
    _iokit.IOConnectCallStructMethod.argtypes = [
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_size_t),
    ]

    _iokit.IOObjectRelease.restype = ctypes.c_int
    _iokit.IOObjectRelease.argtypes = [ctypes.c_uint32]

    # libc for mach_task_self
    _libc = ctypes.cdll.LoadLibrary("/usr/lib/libSystem.B.dylib")
    _libc.mach_task_self.restype = ctypes.c_uint32
    _libc.mach_task_self.argtypes = []


# --- SMC KeyData structure ---
# Must use natural C alignment (not packed) to match the kernel's 80-byte layout.


class _SMCVersion(ctypes.Structure):
    _fields_ = [
        ("major", ctypes.c_uint8),
        ("minor", ctypes.c_uint8),
        ("build", ctypes.c_uint8),
        ("reserved", ctypes.c_uint8),
        ("release", ctypes.c_uint16),
    ]


class _SMCPLimitData(ctypes.Structure):
    _fields_ = [
        ("version", ctypes.c_uint16),
        ("length", ctypes.c_uint16),
        ("cpuPLimit", ctypes.c_uint32),
        ("gpuPLimit", ctypes.c_uint32),
        ("memPLimit", ctypes.c_uint32),
    ]


class _SMCKeyInfoData(ctypes.Structure):
    _fields_ = [
        ("dataSize", ctypes.c_uint32),
        ("dataType", ctypes.c_uint32),
        ("dataAttributes", ctypes.c_uint8),
    ]


class _SMCKeyData(ctypes.Structure):
    _fields_ = [
        ("key", ctypes.c_uint32),
        ("vers", _SMCVersion),
        ("pLimitData", _SMCPLimitData),
        ("keyInfo", _SMCKeyInfoData),
        ("result", ctypes.c_uint8),
        ("status", ctypes.c_uint8),
        ("data8", ctypes.c_uint8),
        ("data32", ctypes.c_uint32),
        ("bytes", ctypes.c_uint8 * 32),
    ]


_STRUCT_SIZE = ctypes.sizeof(_SMCKeyData)  # 80
_SELECTOR = 2  # kernel selector for SMC struct calls

# SMC command selectors (set in data8)
_CMD_READ_KEYINFO = 9
_CMD_READ_BYTES = 5
_CMD_READ_INDEX = 8

# SMC key type for temperature: "flt " as big-endian uint32
_TYPE_FLT = struct.unpack(">I", b"flt ")[0]


class TemperatureReading(NamedTuple):
    cpu_temps_c: list  # list of float, one per sensor
    gpu_temps_c: list  # list of float, one per sensor


def _key_to_uint32(key_str):
    """Convert a 4-char SMC key string to uint32 big-endian."""
    return struct.unpack(">I", key_str.encode("ascii"))[0]


def _uint32_to_key(val):
    """Convert a uint32 back to a 4-char key string."""
    return struct.pack(">I", val).decode("ascii", errors="replace")


def _open_smc():
    """Open a connection to AppleSMCKeysEndpoint.

    Returns the connection handle (uint32), or None on failure.
    """
    matching = _iokit.IOServiceMatching(b"AppleSMC")
    if not matching:
        return None

    iterator = ctypes.c_uint32()
    result = _iokit.IOServiceGetMatchingServices(
        0,  # kIOMainPortDefault
        matching,
        ctypes.byref(iterator),
    )
    if result != 0:
        return None

    conn = None
    while True:
        service = _iokit.IOIteratorNext(iterator.value)
        if service == 0:
            break
        name_buf = ctypes.create_string_buffer(128)
        _iokit.IORegistryEntryGetName(service, name_buf)
        name = name_buf.value.decode("utf-8", errors="replace")

        if "AppleSMCKeysEndpoint" in name:
            conn_handle = ctypes.c_uint32()
            task = _libc.mach_task_self()
            kr = _iokit.IOServiceOpen(service, task, 0, ctypes.byref(conn_handle))
            _iokit.IOObjectRelease(service)
            if kr == 0:
                conn = conn_handle.value
                break
        else:
            _iokit.IOObjectRelease(service)

    _iokit.IOObjectRelease(iterator.value)
    return conn


def _smc_call(conn, input_data):
    """Call SMC with a KeyData struct. Returns output _SMCKeyData or None."""
    output = _SMCKeyData()
    output_size = ctypes.c_size_t(_STRUCT_SIZE)
    kr = _iokit.IOConnectCallStructMethod(
        conn,
        _SELECTOR,
        ctypes.byref(input_data),
        _STRUCT_SIZE,
        ctypes.byref(output),
        ctypes.byref(output_size),
    )
    if kr != 0:
        return None
    return output


def _read_key_info(conn, key_uint32):
    """Read key info (data type and size) for a given SMC key.

    Returns (dataSize, dataType_uint32) or None.
    """
    inp = _SMCKeyData()
    inp.key = key_uint32
    inp.data8 = _CMD_READ_KEYINFO
    out = _smc_call(conn, inp)
    if out is None:
        return None
    return (out.keyInfo.dataSize, out.keyInfo.dataType)


def _read_key_bytes(conn, key_uint32, data_size, data_type):
    """Read the value bytes for an SMC key. Returns raw bytes or None."""
    inp = _SMCKeyData()
    inp.key = key_uint32
    inp.data8 = _CMD_READ_BYTES
    inp.keyInfo.dataSize = data_size
    inp.keyInfo.dataType = data_type
    out = _smc_call(conn, inp)
    if out is None:
        return None
    return bytes(out.bytes[:data_size])


def _get_key_count(conn):
    """Get the total number of SMC keys by reading the #KEY key."""
    key_uint32 = _key_to_uint32("#KEY")
    info = _read_key_info(conn, key_uint32)
    if info is None:
        return 0
    data_size, data_type = info
    raw = _read_key_bytes(conn, key_uint32, data_size, data_type)
    if raw is None or len(raw) < 4:
        return 0
    return struct.unpack(">I", raw)[0]


def _get_key_at_index(conn, index):
    """Get the SMC key (as uint32) at a given index."""
    inp = _SMCKeyData()
    inp.data8 = _CMD_READ_INDEX
    inp.data32 = index
    out = _smc_call(conn, inp)
    if out is None:
        return None
    return out.key


def _discover_temperature_keys(conn):
    """Discover all temperature sensor keys (flt type, Tp*/Te*/Tg* prefix).

    Returns dict: {
        "cpu": [(key_uint32, data_size, data_type), ...],
        "gpu": [(key_uint32, data_size, data_type), ...],
    }

    Key info is cached so subsequent reads only need a single SMC call per key.
    """
    cpu_keys = []
    gpu_keys = []

    key_count = _get_key_count(conn)
    for i in range(key_count):
        key_uint32 = _get_key_at_index(conn, i)
        if key_uint32 is None:
            continue
        key_str = _uint32_to_key(key_uint32)

        # Only interested in T* keys (temperature sensors)
        if not key_str.startswith("T"):
            continue

        info = _read_key_info(conn, key_uint32)
        if info is None:
            continue
        data_size, data_type = info
        if data_type != _TYPE_FLT or data_size != 4:
            continue

        entry = (key_uint32, data_size, data_type)

        # Classify by prefix
        if key_str.startswith("Tp") or key_str.startswith("Te"):
            cpu_keys.append(entry)
        elif key_str.startswith("Tg"):
            gpu_keys.append(entry)

    return {"cpu": cpu_keys, "gpu": gpu_keys}


def _discover_fan_keys(conn):
    """Discover per-fan actual-RPM keys (F{n}Ac, flt type).

    The `FNum` key (ui8) gives the fan count; fanless Macs (e.g. MacBook Air)
    report zero. Returns a list of (key_uint32, data_size, data_type) tuples,
    one per fan, in index order.
    """
    fan_count_info = _read_key_info(conn, _key_to_uint32("FNum"))
    if fan_count_info is None:
        return []
    data_size, data_type = fan_count_info
    raw = _read_key_bytes(conn, _key_to_uint32("FNum"), data_size, data_type)
    if raw is None or len(raw) < 1:
        return []
    fan_count = raw[0]

    fan_keys = []
    for i in range(fan_count):
        key_uint32 = _key_to_uint32("F{}Ac".format(i))
        info = _read_key_info(conn, key_uint32)
        if info is None:
            continue
        fan_size, fan_type = info
        if fan_type != _TYPE_FLT or fan_size != 4:
            continue
        fan_keys.append((key_uint32, fan_size, fan_type))

    return fan_keys


def _read_float_cached(conn, key_uint32, data_size, data_type):
    """Read a float SMC key using pre-cached key info. Returns float or None."""
    raw = _read_key_bytes(conn, key_uint32, data_size, data_type)
    if raw is None or len(raw) < 4:
        return None
    return struct.unpack("<f", raw)[0]


class SMCReader:
    """Persistent SMC connection for reading temperature sensors.

    Opens the AppleSMC connection once and discovers temperature keys
    on first use. Subsequent reads reuse the connection and cached keys.
    """

    def __init__(self):
        self._conn = None
        self._cpu_keys = None
        self._gpu_keys = None
        self._fan_keys = None
        self._available = None

    def _ensure_open(self):
        """Lazily open connection and discover keys."""
        if self._available is not None:
            return self._available
        if not _DARWIN:
            self._available = False
            return False
        self._conn = _open_smc()
        if self._conn is None:
            self._available = False
            return False
        keys = _discover_temperature_keys(self._conn)
        self._cpu_keys = keys["cpu"]
        self._gpu_keys = keys["gpu"]
        self._fan_keys = _discover_fan_keys(self._conn)
        self._available = bool(self._cpu_keys or self._gpu_keys or self._fan_keys)
        return self._available

    def read_temperatures(self):
        """Read current CPU and GPU temperatures.

        Returns TemperatureReading with lists of per-sensor Celsius values.
        Returns TemperatureReading([], []) if SMC is unavailable.
        Filters out non-physical readings (<=0 or >=150 C).
        """
        if not self._ensure_open():
            return TemperatureReading(cpu_temps_c=[], gpu_temps_c=[])

        cpu_temps = []
        for key_uint32, data_size, data_type in self._cpu_keys:
            val = _read_float_cached(self._conn, key_uint32, data_size, data_type)
            if val is not None and 0.0 < val < 150.0:
                cpu_temps.append(val)

        gpu_temps = []
        for key_uint32, data_size, data_type in self._gpu_keys:
            val = _read_float_cached(self._conn, key_uint32, data_size, data_type)
            if val is not None and 0.0 < val < 150.0:
                gpu_temps.append(val)

        return TemperatureReading(cpu_temps_c=cpu_temps, gpu_temps_c=gpu_temps)

    def read_fan_rpms(self):
        """Read current actual RPM for each discovered fan.

        Returns a list of floats, one per fan, in index order. Returns []
        when SMC is unavailable or the machine has no fan keys (e.g. a
        fanless MacBook Air) — 0 RPM is a legitimate idle reading and is not
        filtered out here (unlike temperature's invalid-sentinel handling).
        """
        if not self._ensure_open():
            return []

        rpms = []
        for key_uint32, data_size, data_type in self._fan_keys:
            val = _read_float_cached(self._conn, key_uint32, data_size, data_type)
            if val is not None and 0.0 <= val < 20000.0:
                rpms.append(val)
        return rpms

    @property
    def available(self):
        """Whether the SMC connection and temperature keys are available."""
        return self._ensure_open()

    @property
    def fan_available(self):
        """Whether SMC fan keys were discovered (False on fanless Macs)."""
        self._ensure_open()
        return bool(self._fan_keys)

    def close(self):
        """Close the SMC connection."""
        if self._conn is not None:
            _iokit.IOServiceClose(self._conn)
            self._conn = None
        self._available = None
        self._cpu_keys = None
        self._gpu_keys = None
        self._fan_keys = None
