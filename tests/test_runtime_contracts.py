import re

import pytest

from actop import utils

pytestmark = pytest.mark.local


def test_ram_metrics_reflect_a_sane_live_reading():
    """get_native_ram()'s raw total/available must be physically sane on a real Mac.

    Guards native_sys.py RAM page-math (page-size / byte-offset / units
    regressions). The dict's derived fields (used = total - available, free =
    available) make used+free==total tautological, so those add up regardless of
    what the syscall returned; the load-bearing checks are that the *raw* reading
    is physical — a real machine reports non-zero total RAM in a plausible range,
    and 'available' never exceeds 'total' (so used_bytes stays within [0, total]).
    A units or offset bug surfaces as an absurd total or used > total.
    """
    ram = utils.get_ram_metrics_dict()
    total = ram["total_bytes"]
    used = ram["used_bytes"]

    # Plausible Mac RAM in bytes (1 GiB - 4 TiB); catches units/parse bugs.
    assert 1024**3 <= total <= 4096 * 1024**3
    assert 0 <= used <= total  # 'available' in [0, total]; catches offset/sign bugs


def test_top_processes_with_filter_contract():
    # pytest runs as a Python process, so "python" always matches at least one
    process_metrics = utils.get_top_processes(limit=100, proc_filter="python")

    assert "cpu" in process_metrics
    assert "memory" in process_metrics
    assert len(process_metrics["cpu"]) >= 1

    pattern = re.compile("python", re.IGNORECASE)
    for proc in process_metrics["cpu"]:
        assert pattern.search(proc["command"]), (
            "Process command {!r} does not match filter".format(proc["command"])
        )
