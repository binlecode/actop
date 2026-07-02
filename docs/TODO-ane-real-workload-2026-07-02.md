# TODO — realistic ANE workload script (Option A)

**Goal:** ship a script that represents a **real ANE use case** — on-device **image classification** with
a pre-converted CoreML vision model — so the actop ANE gauge can be validated against a production-shaped
workload, not just the synthetic conv stress in `scripts/ane_load.py`.
**Date:** 2026-07-02 · **Decision:** Option A (load a pre-converted CoreML model, no PyTorch) — ratified.

---

## Priority & sequencing — near-future (ideally *before* launch)

This is **near-future work**, and it is the one roadmap item with a launch tie-in, so it is
sequenced differently from the post-launch hardware items (convergence quick wins —
shipped v1.2.3/v1.4.1 — and `TODO-net-disk-io`):

-   It is a **dev/demo utility**, not a shipped-product feature — no new runtime dependency
    lands in the core (`pillow`/`coremltools` stay in the optional `ane` extra), so it does
    not carry the same "harden before ship" weight as core sampling code.
-   **Launch leverage:** a real MobileNetV2-on-ANE classification loop is the ideal workload
    for the hero GIF and the r/LocalLLaMA post (`docs/RUNBOOK-launch-and-growth.md` Steps 1
    & 4) — it makes the ANE gauge visibly move on a *representative* workload rather than the
    synthetic conv stress in `scripts/ane_load.py`. Landing this **before** recording the
    hero capture strengthens the launch story materially. That is the reason to do it soon,
    even though it is not on the hardware-coverage critical path.
-   **Blocks nothing:** it is independent of the convergence quick wins and net/disk I/O, so
    it can be done opportunistically in any gap; it does not gate them and they do not gate
    it.

Recommended slot: **after the doc refresh, before the hero-GIF recording** — so the capture
uses the realistic workload. If launch timing forces a choice, the synthetic
`scripts/ane_load.py` is a sufficient (if less compelling) fallback for the GIF, and this
can follow immediately after launch.

**Deliverable:** `scripts/ane_classify.py` (Python-native, self-contained). Keep `scripts/ane_load.py`
as the synthetic stress/soak tool; the two are complementary:

| Script | Workload | Virtue |
|--------|----------|--------|
| `ane_load.py` (exists) | synthetic random conv stack (`NeuralNetworkBuilder`) | zero download, deterministic, tunable size — stress/soak |
| `ane_classify.py` (new) | **real pretrained classifier on a real image** | authentic ANE use case — "does the gauge reflect real ANE use" |

Both pin `compute_units=CPU_AND_NE` by default so work lands on the ANE and is visible on actop.

---

## Design summary (grounded before tasks)

- **Model:** Apple's **MobileNetV2** CoreML classifier (`.mlmodel`, ~24 MB) — small, license-clean, a
  *classifier* model so its output already carries `classLabel` + a probability dict (no separate ImageNet
  labels file needed). FastViT (`apple/coreml-FastViT`) is a more modern, more ANE-optimized alternative —
  note it as an upgrade, default to MobileNetV2 for reliability.
- **Deps:** `coremltools` + `numpy` (already in the `ane` extra) + **`pillow`** (new — image load/resize).
  Still **no PyTorch**.
- **Flow:** fetch+cache model → load an image (real via `--image`, else a generated RGB array) → resize to
  the model's input (224×224 RGB) → `model.predict({...})` in a loop → print top-1 label + confidence +
  throughput. `--compute-unit {cpu_and_ne,all,cpu_only}` mirrors `ane_load.py`.
- **Realism note:** the ANE load is the same regardless of image content; the real-image classification is
  what makes it a *representative* workload and gives a human-readable "it's really running" signal.

---

## Tasks (implementation-ready)

### TASK-1 — Model acquisition + cache
- `files`: `scripts/ane_classify.py` (a `_ensure_model()` helper), `.gitignore` (ignore the cache dir).
- Download MobileNetV2 CoreML model on first run via `urllib` to a cache dir (`~/.cache/actop/models/`
  or `scripts/models/`, **gitignored**); reuse if present. Verify size/checksum after download.
  **⚠ VERIFY at impl time:** the exact Apple asset URL (historically
  `https://ml-assets.apple.com/coreml/models/Image/ImageClassification/MobileNetV2/MobileNetV2.mlmodel`) —
  confirm it resolves; if not, fall back to a Hugging Face `apple/coreml-*` repo or document a manual
  download step.
- `done_when`: first run fetches + caches the `.mlmodel`; a second run loads from cache with no network.
- `success_signal`: `coremltools` loads the cached model without error and reports a classifier spec.

### TASK-2 — Image preprocessing + single-inference classification
- `files`: `scripts/ane_classify.py`
- `--image PATH` loads via Pillow; default (no image) generates a deterministic RGB `np.ndarray`. Resize
  to the model's declared input (224×224). Confirm whether the converted model takes an **image input**
  (pass a `PIL.Image` directly) or a multiarray (feed the normalized array) and branch accordingly.
- Map output to top-1: for the classifier model, read `classLabel` + the probability dict; print top-1
  label + confidence (and top-3 if easy).
- `done_when`: one inference on a real image prints a plausible top-1 label + confidence.
- `success_signal`: classifying a clear real photo (e.g. a cat/dog/car) returns a sensible label.

### TASK-3 — Sustained ANE loop + compute-unit control
- `files`: `scripts/ane_classify.py`
- Argparse: `--duration` (default ~30s), `--image`, `--compute-unit {cpu_and_ne,all,cpu_only}` (default
  `cpu_and_ne`), `--model {mobilenetv2,fastvit}` (optional). Warm up once, then loop `model.predict` for
  the duration; print periodic throughput (inferences/s) like `ane_load.py`.
- Reuse the guard/patterns from `ane_load.py`: `sys.platform == "darwin"` check, lazy dep import with the
  `pip install -e ".[ane]"` hint, `time.monotonic()` loop.
- `done_when`: sustained loop runs for `--duration` and prints throughput; `cpu_and_ne` is the default.
- `success_signal`: runs clean for the full duration without error.

### TASK-4 — ANE-landing verification (the whole point)
- `files`: none (procedure + a short block in the script docstring)
- Confirm the work actually hits the ANE via the CPU-only-vs-CPU+ANE throughput A/B (as done for
  `ane_load.py`): `--compute-unit cpu_only` should be markedly slower than `cpu_and_ne`.
- `done_when`: measured `cpu_and_ne` throughput ≫ `cpu_only` on the same model/image (record the ratio in
  the PR description).
- `success_signal`: operator watches actop and sees the **ANE gauge rise** during `cpu_and_ne`, and the
  **GPU** (not ANE) rise under `--compute-unit all` — documenting the placement difference.

### TASK-5 — Deps, docs, versioning
- `files`: `pyproject.toml` (`ane` extra), `README.md`, `CHANGELOG.md`, `pyproject.toml` (version).
- Add **`pillow>=9`** to the `ane` extra: `ane = ["coremltools>=7.0", "numpy>=1.21", "pillow>=9"]`.
- README: extend the "Exercising the ANE gauge" Development note + the ANE Troubleshooting FAQ to mention
  `ane_classify.py` as the *realistic* workload (vs `ane_load.py` synthetic).
- Version + CHANGELOG per the repo convention (**patch bump**, `[Unreleased]` → dated section, same PR).
- `done_when`: `pip install -e ".[ane]"` pulls Pillow; docs mention both scripts; version bumped.
- `success_signal`: `.venv/bin/ruff check .` + `.venv/bin/ruff format --check .` clean.

---

## Testing note (respect the functional-tests-only mandate)

**No automated test for this script.** Per `CLAUDE.md`, tests must drive a public surface and exercise a
real failure mode; this is a host- and model-download-dependent dev/demo utility (like `ane_load.py`,
which also has none). Adding a structural test (e.g. asserting argparse shape or a mocked predict) would
violate the mandate and must not be added. Verification is TASK-4's manual A/B + gauge observation. If any
lightweight check is ever wanted, it would be a `@pytest.mark.local` functional run gated off CI — not a
structural stand-in.

---

## Open items to confirm at implementation time (**⚠ VERIFY**)

1. **Model URL** — Apple `ml-assets` MobileNetV2 URL still resolves (TASK-1); else HF fallback / manual.
2. **Input type** — whether the shipped MobileNetV2 `.mlmodel` expects an image or multiarray input
   (drives the `predict` call in TASK-2).
3. **ANE placement** — MobileNetV2 convs land on the ANE under `CPU_AND_NE` on this machine (TASK-4 A/B
   is the proof; if placement is poor, try FastViT which Apple tuned for the ANE).
4. **Install + run once before calling it done** — same discipline as `ane_load.py`: install the `ane`
   extra, run the A/B, confirm the gauge, *then* ship.

---

## Suggested order

`TASK-1` (model) → `TASK-2` (single inference proves the pipeline) → `TASK-3` (sustained loop) →
`TASK-4` (verify ANE landing) → `TASK-5` (deps/docs/version). TASK-1→2 are the risk; once a real image
classifies on the ANE, the rest is mechanical.
