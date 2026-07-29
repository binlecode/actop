#!/usr/bin/env python3
"""Drive the Apple Neural Engine (ANE) with a sustained CoreML workload.

Purpose
-------
The ANE is power-gated when idle, so actop reports ``ANE 0% (0.0W)`` unless
some app is actively running neural-network inference. This script generates a
deterministic, repeatable ANE load so you can watch the actop ANE gauge move
and validate that ANE power/percent reporting works end to end.

How it works
------------
It builds a small convolutional network *in memory* with coremltools'
``NeuralNetworkBuilder`` (no PyTorch/TensorFlow, no external .mlmodel file),
restricts the compute units to CPU + Neural Engine, and runs inference in a
tight loop. Convolutions are the op type the ANE accepts most readily, so with
GPU excluded the work lands on the ANE. Weights are converted to float16, which
is what the ANE runs internally and further nudges placement onto it.

Requirements
------------
coremltools and numpy (NOT actop runtime deps -- macOS only, install ad hoc)::

    .venv/bin/python -m pip install -e ".[ane]"

Usage
-----
    .venv/bin/python scripts/ane_load.py                 # ~30s default load
    .venv/bin/python scripts/ane_load.py --duration 120  # run for 2 minutes
    .venv/bin/python scripts/ane_load.py --size 320 --channels 96 --layers 16

Run actop in another terminal and watch the ANE gauge climb. If ANE stays at 0
but GPU moves, retry with ``--compute-unit cpu_and_ne`` (the default) -- passing
``all`` lets CoreML place the work on the GPU instead.

Note
----
On-device LLM stacks (MLX, Ollama, llama.cpp, LM Studio) use the GPU (Metal),
NOT the ANE. CoreML inference like this is the reliable way to exercise the ANE.
"""

from __future__ import annotations

import argparse
import sys
import time


def _fail(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(1)


def _require_deps():
    """Import heavy deps lazily with a friendly install hint on failure."""
    try:
        import coremltools as ct
        import numpy as np
    except ImportError as exc:
        _fail(
            f"missing dependency ({exc.name}). These are not actop runtime deps; "
            "install the ane extra (macOS only):\n"
            '    .venv/bin/python -m pip install -e ".[ane]"'
        )
    return np, ct


def build_model(ct, np, channels: int, layers: int, size: int, compute_unit: str):
    """Hand-build an fp16 conv stack and compile it for the requested backend."""
    from coremltools.models import datatypes
    from coremltools.models.neural_network import NeuralNetworkBuilder
    from coremltools.models.neural_network.quantization_utils import quantize_weights

    in_channels = 3
    input_features = [("image", datatypes.Array(in_channels, size, size))]
    output_features = [("output", None)]
    builder = NeuralNetworkBuilder(input_features, output_features)

    prev_name = "image"
    prev_channels = in_channels
    for i in range(layers):
        out_channels = channels
        w = np.random.rand(out_channels, prev_channels, 3, 3).astype(np.float32)
        out_name = f"conv{i}_out"
        builder.add_convolution(
            name=f"conv{i}",
            kernel_channels=prev_channels,
            output_channels=out_channels,
            height=3,
            width=3,
            stride_height=1,
            stride_width=1,
            border_mode="same",
            groups=1,
            W=w,
            b=None,
            has_bias=False,
            input_name=prev_name,
            output_name=out_name,
        )
        builder.add_activation(
            name=f"relu{i}",
            non_linearity="RELU",
            input_name=out_name,
            output_name=f"relu{i}_out",
        )
        prev_name = f"relu{i}_out"
        prev_channels = out_channels

    # Rename the final activation output to the declared model output.
    builder.spec.neuralNetwork.layers[-1].output[0] = "output"

    spec = builder.spec
    unit_map = {
        "cpu_and_ne": ct.ComputeUnit.CPU_AND_NE,
        "all": ct.ComputeUnit.ALL,
        "cpu_only": ct.ComputeUnit.CPU_ONLY,
    }
    model = ct.models.MLModel(spec, compute_units=unit_map[compute_unit])
    # float16 is the ANE's native precision; quantizing nudges placement onto it.
    try:
        fp16_spec = quantize_weights(model, nbits=16)
        model = ct.models.MLModel(
            fp16_spec if hasattr(fp16_spec, "neuralNetwork") else fp16_spec.get_spec(),
            compute_units=unit_map[compute_unit],
        )
    except Exception:
        # Quantization is best-effort; fp32 convs still run on the ANE.
        pass
    return model


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a sustained Apple Neural Engine load via CoreML.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--duration", type=float, default=30.0, help="seconds to run the load loop"
    )
    parser.add_argument(
        "--size", type=int, default=256, help="input spatial size (HxW)"
    )
    parser.add_argument(
        "--channels", type=int, default=64, help="channels per conv layer"
    )
    parser.add_argument("--layers", type=int, default=12, help="number of conv layers")
    parser.add_argument(
        "--compute-unit",
        choices=("cpu_and_ne", "all", "cpu_only"),
        default="cpu_and_ne",
        help="CoreML backend; cpu_and_ne excludes the GPU so work lands on the ANE",
    )
    args = parser.parse_args(argv)

    if sys.platform != "darwin":
        _fail("this script only runs on macOS (Apple Silicon).")

    np, ct = _require_deps()

    print(
        f"building conv model: {args.layers} layers x {args.channels}ch "
        f"@ {args.size}x{args.size}, compute_unit={args.compute_unit} ...",
        flush=True,
    )
    model = build_model(
        ct, np, args.channels, args.layers, args.size, args.compute_unit
    )

    x = np.random.rand(3, args.size, args.size).astype(np.float32)
    feed = {"image": x}

    # Warm up (first predict compiles/loads the model).
    model.predict(feed)

    print(
        f"running ANE load for {args.duration:.0f}s -- watch the actop ANE gauge "
        "in another terminal. Ctrl-C to stop early.",
        flush=True,
    )
    start = time.monotonic()
    iters = 0
    last_report = start
    try:
        while time.monotonic() - start < args.duration:
            model.predict(feed)
            iters += 1
            now = time.monotonic()
            if now - last_report >= 2.0:
                rate = iters / (now - start)
                print(f"  {iters} inferences  ({rate:.1f}/s)", flush=True)
                last_report = now
    except KeyboardInterrupt:
        print("\ninterrupted.", flush=True)

    elapsed = time.monotonic() - start
    rate = iters / elapsed if elapsed else 0.0
    print(f"done: {iters} inferences in {elapsed:.1f}s ({rate:.1f}/s).", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
