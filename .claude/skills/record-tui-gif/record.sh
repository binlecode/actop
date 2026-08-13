#!/usr/bin/env bash
#
# Record the actop TUI to a GIF with vhs, optionally under a real GPU workload
# so the gauges actually move. Orchestrates the full sequence and ALWAYS stops
# the workload on exit (success, error, or Ctrl-C):
#   1. start a GPU workload against the llama.cpp router (unless SKIP_WORKLOAD=1)
#      — the ollama-router is the fully supported fallback, see env below
#   2. wait for the GPU to ramp
#   3. record each tape with vhs (retries the ttyd-startup race under load)
#   4. stop recording (vhs exits on its own)
#   5. stop the GPU workload
#
# Run from anywhere inside the repo:
#   bash .claude/skills/record-tui-gif/record.sh
#
# Config via env:
#   TAPES="tmp/a.tape tmp/b.tape"   tapes to record (default: bundled actop-demo.tape)
#   SKIP_WORKLOAD=1                  record without driving the GPU (idle gauges)
#   ROUTER_URL=http://localhost:11433  router base URL (ollama:11433, llamacpp:9040)
#   MODEL=qwen3.6:35b-a3b-agentic   model to drive
#   API=ollama|openai                wire protocol (llama.cpp backends need openai)
#   CONCURRENCY=2  NUM_PREDICT=4096  workload knobs (match router backend count)
#   RAMP_SECONDS=8                   GPU spin-up wait before recording
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$REPO_ROOT"

# ---- Config (override via env) --------------------------------------------
# Defaults are llama.cpp-first (OpenAI wire protocol on the llamacpp router at
# :9040, original-model weights). The ollama-router fallback is fully supported:
#   ROUTER_URL=http://localhost:11433 MODEL=qwen3.6:35b-a3b-agentic API=ollama
ROUTER_URL="${ROUTER_URL:-http://localhost:9040}"
MODEL="${MODEL:-qwen3.6:35b-a3b}"
API="${API:-openai}"                  # openai (/v1/chat/completions, llama.cpp) or ollama (/api/generate)
CONCURRENCY="${CONCURRENCY:-2}"
NUM_PREDICT="${NUM_PREDICT:-4096}"   # long generations = sustained GPU load
RAMP_SECONDS="${RAMP_SECONDS:-8}"    # let the model start generating before vhs
SKIP_WORKLOAD="${SKIP_WORKLOAD:-0}"
TAPES="${TAPES:-$SCRIPT_DIR/actop-demo.tape}"
WORKLOAD_LOG="${WORKLOAD_LOG:-tmp/gpu_workload.log}"

PY="$REPO_ROOT/.venv/bin/python"
WORKLOAD="$SCRIPT_DIR/gpu_workload.py"
WORKLOAD_PID=""

cleanup() {
    if [[ -n "$WORKLOAD_PID" ]] && kill -0 "$WORKLOAD_PID" 2>/dev/null; then
        echo ">> Stopping GPU workload (pid $WORKLOAD_PID)…"
        kill -INT "-$WORKLOAD_PID" 2>/dev/null || kill -INT "$WORKLOAD_PID" 2>/dev/null || true
        sleep 1
        kill -0 "$WORKLOAD_PID" 2>/dev/null && kill -TERM "$WORKLOAD_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

# ---- Preflight ------------------------------------------------------------
echo ">> Preflight checks…"
command -v vhs   >/dev/null || { echo "!! vhs not found (brew install vhs)"; exit 1; }
command -v actop >/dev/null || { echo "!! actop not found on PATH"; exit 1; }
for t in $TAPES; do [[ -f "$t" ]] || { echo "!! tape not found: $t"; exit 1; }; done
mkdir -p images tmp

# ---- 1-2. Start GPU workload (own process group) + ramp --------------------
if [[ "$SKIP_WORKLOAD" == 1 ]]; then
    echo ">> SKIP_WORKLOAD=1 — recording with idle gauges."
else
    [[ -x "$PY" ]] || { echo "!! .venv python missing at $PY (activate/create the repo venv)"; exit 1; }
    # Health probe: try the native Ollama /api/tags first, then fall back to
    # the router-level /health (the llama.cpp router's catch-all proxy 400s on
    # body-less GETs, so /v1/models is not a usable probe).
    HEALTH_URL="$ROUTER_URL/api/tags"
    if ! curl -sf "$HEALTH_URL" >/dev/null 2>&1; then
        HEALTH_URL="$ROUTER_URL/health"
        if ! curl -sf "$HEALTH_URL" >/dev/null 2>&1; then
            echo "!! router not reachable at $ROUTER_URL"
            echo "   start it, or re-run with SKIP_WORKLOAD=1 to record idle."
            exit 1
        fi
    fi
    echo "   router OK @ $ROUTER_URL, model=$MODEL"
    echo ">> Starting GPU workload (concurrency=$CONCURRENCY, num_predict=$NUM_PREDICT)…"
    set -m  # job control → child gets its own process group
    "$PY" "$WORKLOAD" \
        --url "$ROUTER_URL" --model "$MODEL" --api "$API" \
        --concurrency "$CONCURRENCY" --num-predict "$NUM_PREDICT" \
        >"$WORKLOAD_LOG" 2>&1 &
    WORKLOAD_PID=$!
    set +m
    echo "   workload pid=$WORKLOAD_PID  (log: $WORKLOAD_LOG)"
    echo ">> Ramping GPU for ${RAMP_SECONDS}s…"
    sleep "$RAMP_SECONDS"
    if ! kill -0 "$WORKLOAD_PID" 2>/dev/null; then
        echo "!! workload died during ramp — see $WORKLOAD_LOG"; tail -5 "$WORKLOAD_LOG"; exit 1
    fi
fi

# ---- 3-4. Record each tape (retry the ttyd startup race under load) --------
for TAPE in $TAPES; do
    echo ">> Recording with vhs ($TAPE)…"
    VHS_OK=0
    for attempt in 1 2 3; do
        if vhs "$TAPE"; then VHS_OK=1; break; fi
        echo "!! vhs attempt $attempt failed (likely ttyd startup race) — retrying…"
        sleep 3
    done
    [[ "$VHS_OK" == 1 ]] || { echo "!! vhs failed after 3 attempts for $TAPE"; exit 1; }
done

# ---- 5. cleanup() runs on EXIT and stops the workload ----------------------
echo ">> Done. GIFs:"
for TAPE in $TAPES; do
    GIF="$(awk '/^Output /{print $2; exit}' "$TAPE")"
    if [[ -n "$GIF" && -f "$GIF" ]]; then
        printf '   %-32s %s\n' "$GIF" "($(du -h "$GIF" | cut -f1))"
    else
        echo "   !! expected GIF missing for $TAPE ($GIF)"
    fi
done
command -v gifsicle >/dev/null && \
    echo "   (compress any large ones:  gifsicle -O3 --lossy=80 <gif> -o <gif>)"
exit 0
