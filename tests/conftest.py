import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# On non-macOS platforms, skip collecting test modules that import macOS-only
# libraries (IOReport, CoreFoundation via ctypes).  These modules carry the
# pytest.mark.local marker, but the top-level imports fail at *collection*
# time before pytest can evaluate the marker.
collect_ignore_glob = []
if sys.platform != "darwin":
    collect_ignore_glob += [
        "test_ioreport.py",
        "test_sampler.py",
        "test_smc.py",
        "test_runtime_contracts.py",
        "test_api.py",
        "test_native_sys.py",
    ]
