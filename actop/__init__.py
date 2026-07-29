import importlib.metadata

from .api import AsyncMonitor, Monitor, Profiler
from .models import CoreSample, FanReading, SystemSnapshot

try:
    __version__ = importlib.metadata.version("actop")
except importlib.metadata.PackageNotFoundError:
    __version__ = "dev"

__all__ = [
    "AsyncMonitor",
    "CoreSample",
    "FanReading",
    "Monitor",
    "Profiler",
    "SystemSnapshot",
    "__version__",
]
