"""L2 domain analytics: derived judgments over acquired data points.

This module sits above acquisition (`ioreport`/`smc`/`gpu_registry`/
`native_sys`/`utils`) and below presentation (`tui/*`, `export`). It imports
only `models`/`power_scaling` types — never `tui/*` — so hardware judgments
(power attribution today; alerts/throttling/session energy in a later step)
are data points any API or export consumer can obtain, not render-time math
trapped in the view.
"""


def attribute_power(share_cpu, share_gpu, cpu_watts, gpu_watts):
    """Watts attributed to a process from its CPU/GPU time shares.

    A None share (first sample, no delta yet) contributes 0 rather than
    blocking the other domain's contribution.
    """
    watts = 0.0
    if share_cpu is not None:
        watts += share_cpu * cpu_watts
    if share_gpu is not None:
        watts += share_gpu * gpu_watts
    return watts
