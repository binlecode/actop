#!/usr/bin/env python3
"""Drive an Ollama-compatible router to keep the Apple Silicon GPU busy.

Purpose: generate sustained GPU / memory-bandwidth load so the actop gauges
actually move while recording the hero GIF (tmp/actop-demo.tape).

Standard-library only (urllib) — no pip installs needed in .venv.

Examples:
    # Native Ollama router on localhost, run until Ctrl-C:
    python tmp/gpu_workload.py --model llama3

    # Custom router URL + model, run for 30s with 2 concurrent streams:
    python tmp/gpu_workload.py \
        --url http://localhost:11434 --model qwen2.5:7b \
        --duration 30 --concurrency 2

    # OpenAI-compatible router that needs a key:
    python tmp/gpu_workload.py --api openai \
        --url http://localhost:8080 --model my-model \
        --api-key "$(cat ~/env-secrets/ollama-router-key)"

Config can also come from env: OLLAMA_ROUTER_URL, OLLAMA_ROUTER_MODEL,
OLLAMA_ROUTER_API_KEY.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

# A prompt that reliably produces long output → sustained generation → GPU load.
DEFAULT_PROMPT = (
    "Write an extremely detailed, multi-section technical deep-dive on how "
    "modern GPUs schedule and execute massively parallel workloads. Cover warps, "
    "occupancy, memory hierarchies, and bandwidth bottlenecks. Do not stop early."
)

# Shared counters across worker threads.
_lock = threading.Lock()
_stats = {"requests": 0, "tokens": 0, "errors": 0}
_stop = threading.Event()


def _post_stream(url: str, payload: dict, api_key: str | None):
    """POST a JSON body and yield each streamed line (bytes). Raises on HTTP error."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(req, timeout=300) as resp:
        for raw in resp:
            line = raw.strip()
            if line:
                yield line


def _count_tokens_ollama(line: bytes) -> int:
    """One /api/generate stream chunk → rough token count (1 per chunk).

    Thinking models stream reasoning tokens in a `thinking` field (empty
    `response`) before the answer — count both so stats reflect real work.
    """
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return 0
    return 1 if (obj.get("response") or obj.get("thinking")) else 0


def _count_tokens_openai(line: bytes) -> int:
    """One SSE 'data: {...}' chunk from /v1/chat/completions → rough token count."""
    if line.startswith(b"data:"):
        line = line[len(b"data:") :].strip()
    if line == b"[DONE]":
        return 0
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return 0
    choices = obj.get("choices") or [{}]
    delta = choices[0].get("delta") or {}
    return 1 if delta.get("content") else 0


def _one_request(args) -> None:
    """Fire a single streaming generation and drain it, updating counters."""
    if args.api == "ollama":
        endpoint = f"{args.url.rstrip('/')}/api/generate"
        payload = {
            "model": args.model,
            "prompt": args.prompt,
            "stream": True,
            "options": {"num_predict": args.num_predict},
        }
        counter = _count_tokens_ollama
    else:  # openai
        endpoint = f"{args.url.rstrip('/')}/v1/chat/completions"
        payload = {
            "model": args.model,
            "messages": [{"role": "user", "content": args.prompt}],
            "stream": True,
            "max_tokens": args.num_predict,
        }
        counter = _count_tokens_openai

    tokens = 0
    for line in _post_stream(endpoint, payload, args.api_key):
        if _stop.is_set():
            break
        tokens += counter(line)
    with _lock:
        _stats["requests"] += 1
        _stats["tokens"] += tokens


def _worker(args) -> None:
    while not _stop.is_set():
        try:
            _one_request(args)
        except urllib.error.HTTPError as e:
            with _lock:
                _stats["errors"] += 1
            body = e.read().decode("utf-8", "replace")[:200]
            print(f"[worker] HTTP {e.code}: {body}", file=sys.stderr)
            time.sleep(1)
        except urllib.error.URLError as e:
            with _lock:
                _stats["errors"] += 1
            print(f"[worker] connection error: {e.reason}", file=sys.stderr)
            time.sleep(1)


def _reporter(start: float) -> None:
    while not _stop.is_set():
        time.sleep(2)
        with _lock:
            reqs, toks, errs = _stats["requests"], _stats["tokens"], _stats["errors"]
        elapsed = time.time() - start
        rate = toks / elapsed if elapsed else 0
        print(
            f"[{elapsed:5.1f}s] requests={reqs} tokens={toks} "
            f"({rate:5.1f} tok/s) errors={errs}",
            file=sys.stderr,
        )


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--url",
        default=os.environ.get("OLLAMA_ROUTER_URL", "http://localhost:11434"),
        help="Router base URL (env: OLLAMA_ROUTER_URL; default http://localhost:11434)",
    )
    p.add_argument(
        "--model",
        default=os.environ.get("OLLAMA_ROUTER_MODEL", "llama3"),
        help="Model name (env: OLLAMA_ROUTER_MODEL; default llama3)",
    )
    p.add_argument(
        "--api",
        choices=["ollama", "openai"],
        default="ollama",
        help="Wire protocol: native Ollama /api/generate or OpenAI /v1/chat/completions",
    )
    p.add_argument(
        "--api-key",
        default=os.environ.get("OLLAMA_ROUTER_API_KEY"),
        help="Bearer token if the router requires one (env: OLLAMA_ROUTER_API_KEY)",
    )
    p.add_argument("--prompt", default=DEFAULT_PROMPT, help="Prompt text to send")
    p.add_argument(
        "--num-predict",
        type=int,
        default=1024,
        help="Max tokens per request — higher = longer sustained GPU load",
    )
    p.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Parallel streaming requests (raise to push GPU/bandwidth harder)",
    )
    p.add_argument(
        "--duration",
        type=float,
        default=0.0,
        help="Seconds to run; 0 = until Ctrl-C (default 0)",
    )
    args = p.parse_args()

    print(
        f"Driving GPU via {args.api} @ {args.url} model={args.model} "
        f"concurrency={args.concurrency} num_predict={args.num_predict}",
        file=sys.stderr,
    )
    print("Watch actop in another pane — Ctrl-C to stop.\n", file=sys.stderr)

    start = time.time()
    threads = [
        threading.Thread(target=_worker, args=(args,), daemon=True)
        for _ in range(args.concurrency)
    ]
    reporter = threading.Thread(target=_reporter, args=(start,), daemon=True)
    for t in threads:
        t.start()
    reporter.start()

    try:
        if args.duration > 0:
            _stop.wait(timeout=args.duration)
        else:
            while not _stop.is_set():
                _stop.wait(timeout=1)
    except KeyboardInterrupt:
        pass
    finally:
        _stop.set()

    elapsed = time.time() - start
    print(
        f"\nDone: {_stats['requests']} requests, {_stats['tokens']} tokens in "
        f"{elapsed:.1f}s, {_stats['errors']} errors.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
