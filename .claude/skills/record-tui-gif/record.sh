#!/usr/bin/env bash
#
# Record the actop TUI to a GIF with vhs, optionally under a real GPU workload
# so the gauges actually move. Orchestrates the full sequence and ALWAYS stops
# the workload on exit (success, error, or Ctrl-C):
#   1. start a GPU workload against whatever local LLM endpoint is up
#      (unless SKIP_WORKLOAD=1) — discovered at runtime, see below
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
#   LLM_URL=http://127.0.0.1:8081    pin the endpoint (default: auto-discover)
#   MODEL=<id>                       model to drive (default: ask the endpoint)
#   API=ollama|openai                wire protocol (default: from discovery)
#   CONCURRENCY=2  NUM_PREDICT=4096  workload knobs (>1 only helps if the target
#                                    serves >1 instance; otherwise requests queue
#                                    — which still keeps the GPU busy, the point)
#   RAMP_SECONDS=8                   GPU spin-up wait before recording
#
# The LLM is only a way to make the GPU gauges move — nothing about actop
# depends on which model or port answers. So this script BINDS TO NOTHING:
# it checks at runtime which local ports actually speak an OpenAI or Ollama
# model-listing API and picks the first that does. Hardcoded ports rot (the
# local stack renumbered twice in 2026-08 alone) and a recording script has no
# business tracking someone else's topology.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$REPO_ROOT"

# ---- Config (override via env) --------------------------------------------
# No endpoint defaults on purpose — see the header. LLM_URL/API/MODEL stay empty
# unless pinned, and get filled in by discover_endpoint() at runtime.
# ROUTER_URL is the pre-2026-08 name, still honoured so old invocations work.
LLM_URL="${LLM_URL:-${ROUTER_URL:-}}"
MODEL="${MODEL:-}"
API="${API:-}"                        # openai (/v1/chat/completions) or ollama (/api/generate)
CONCURRENCY="${CONCURRENCY:-2}"
NUM_PREDICT="${NUM_PREDICT:-4096}"   # long generations = sustained GPU load
RAMP_SECONDS="${RAMP_SECONDS:-8}"    # let the model start generating before vhs
SKIP_WORKLOAD="${SKIP_WORKLOAD:-0}"
TAPES="${TAPES:-$SCRIPT_DIR/actop-demo.tape}"
WORKLOAD_LOG="${WORKLOAD_LOG:-tmp/gpu_workload.log}"

PY="$REPO_ROOT/.venv/bin/python"
WORKLOAD="$SCRIPT_DIR/gpu_workload.py"
WORKLOAD_PID=""

# ---- Runtime endpoint discovery -------------------------------------------
# Probe every locally listening TCP port for a model-listing API. First one that
# answers wins; nothing is assumed about port numbers, model names, or which
# stack is running. Sets LLM_URL / API / MODEL as a side effect.
probe_endpoint() {
    local base="$1"
    if curl -sf --max-time 1 "$base/v1/models" 2>/dev/null | grep -q '"id"'; then
        API="${API:-openai}"; return 0
    fi
    if curl -sf --max-time 1 "$base/api/tags" 2>/dev/null | grep -q '"models"'; then
        API="${API:-ollama}"; return 0
    fi
    return 1
}

discover_endpoint() {
    local port base
    # Ports only, deduped. Probing an unrelated service costs one 1s GET that it
    # answers with 404 — harmless, and cheaper than maintaining a port registry.
    for port in $(lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null \
                  | sed -n 's/.*:\([0-9][0-9]*\) (LISTEN).*/\1/p' | sort -un); do
        base="http://127.0.0.1:$port"
        if probe_endpoint "$base"; then LLM_URL="$base"; return 0; fi
    done
    return 1
}

# Ask the endpoint what it serves rather than guessing. For llama.cpp the id is
# the GGUF path, which is fine — the endpoint ignores the field anyway.
discover_model() {
    if [[ "$API" == ollama ]]; then
        curl -sf --max-time 2 "$LLM_URL/api/tags" 2>/dev/null \
            | "$PY" -c "import sys,json;print(json.load(sys.stdin)['models'][0]['name'])" 2>/dev/null
    else
        curl -sf --max-time 2 "$LLM_URL/v1/models" 2>/dev/null \
            | "$PY" -c "import sys,json;print(json.load(sys.stdin)['data'][0]['id'])" 2>/dev/null
    fi
}

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

    if [[ -n "$LLM_URL" ]]; then
        probe_endpoint "$LLM_URL" || {
            echo "!! pinned endpoint not answering: $LLM_URL"
            echo "   unset LLM_URL to auto-discover, or SKIP_WORKLOAD=1 to record idle."
            exit 1
        }
    else
        echo ">> Looking for a local LLM endpoint…"
        discover_endpoint || {
            # A missing workload degrades the GIF (flat gauges); it does not
            # invalidate it. Failing the whole recording here would be worse.
            echo "!! no local LLM endpoint found — recording with idle gauges."
            echo "   pin one with LLM_URL=http://127.0.0.1:PORT if that's wrong."
            SKIP_WORKLOAD=1
        }
    fi
fi

if [[ "$SKIP_WORKLOAD" != 1 ]]; then
    MODEL="${MODEL:-$(discover_model)}"
    MODEL="${MODEL:-local}"
    echo "   endpoint OK @ $LLM_URL  api=$API  model=$MODEL"
    echo ">> Starting GPU workload (concurrency=$CONCURRENCY, num_predict=$NUM_PREDICT)…"
    set -m  # job control → child gets its own process group
    "$PY" "$WORKLOAD" \
        --url "$LLM_URL" --model "$MODEL" --api "$API" \
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
