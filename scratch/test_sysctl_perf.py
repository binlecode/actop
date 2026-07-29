import os
import subprocess
import time

start = time.time()
for _ in range(100):
    os.popen("sysctl -n hw.perflevel0.logicalcpu").read()
print(f"os.popen: {time.time() - start:.3f}s")

start = time.time()
for _ in range(100):
    subprocess.run(["sysctl", "-n", "hw.perflevel0.logicalcpu"], capture_output=True)
print(f"subprocess: {time.time() - start:.3f}s")

import ctypes  # noqa: E402

libc = ctypes.cdll.LoadLibrary("/usr/lib/libSystem.B.dylib")
sysctlbyname = libc.sysctlbyname


def get_sysctl_int(name):
    size = ctypes.c_size_t(8)
    val = ctypes.c_uint64(0)
    if (
        sysctlbyname(
            name.encode("utf-8"), ctypes.byref(val), ctypes.byref(size), None, 0
        )
        == 0
    ):
        return val.value
    return None


start = time.time()
for _ in range(100):
    get_sysctl_int("hw.perflevel0.logicalcpu")
print(f"ctypes: {time.time() - start:.3f}s")
