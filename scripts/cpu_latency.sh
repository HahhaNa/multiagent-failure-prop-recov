#!/usr/bin/env bash
# CPU latency sample: llama.cpp server (CPU only) + 20 clean runs. Usage: scripts/cpu_latency.sh
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
LLAMA_BIN="${LLAMA_BIN:-$HOME/models/llamacpp-b11640/llama-b11640/llama-server}"
GGUF="${GGUF:-$HOME/models/qwen3-4b-gguf/Qwen3-4B-Q4_K_M.gguf}"
THREADS="${THREADS:-10}"
CMD=("$LLAMA_BIN" -m "$GGUF" --jinja -c 32768 --parallel 2 -t "$THREADS" --reasoning-budget 0 --seed 0 --port 8080 --host 127.0.0.1)
mkdir -p logs
echo "${CMD[*]}" > logs/serve_cmd_qwen3-4b-cpu.txt
CUDA_VISIBLE_DEVICES="" "${CMD[@]}" > logs/llama_server.log 2>&1 &
SRV=$!
trap 'kill $SRV' EXIT
until curl -sf http://127.0.0.1:8080/health > /dev/null; do sleep 2; done
uv run --frozen python -m fpr.runner cpu --model qwen3-4b-cpu "$@"
