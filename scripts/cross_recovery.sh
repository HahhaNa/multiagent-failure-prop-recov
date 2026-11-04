#!/usr/bin/env bash
# W6.1 cross-family recovery: swap vLLM to Llama-3.2-3B, run R1 restart over cross_llama3.2-3b and R3 verify
# (core conditions, message mode, k=2), then serve Qwen3-8B again. Run only when no Qwen job is active.
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
M=llama3.2-3b

stop_vllm() {
  if [[ -f logs/vllm.pid ]] && kill -0 "$(cat logs/vllm.pid)" 2>/dev/null; then
    kill "$(cat logs/vllm.pid)"
    while kill -0 "$(cat logs/vllm.pid)" 2>/dev/null; do sleep 2; done
  fi
  until [[ $(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | head -1) -lt 3000 ]]; do sleep 2; done
}

stop_vllm
scripts/serve.sh "$M" --bg
cp logs/serve_cmd.txt "logs/serve_cmd_$M.txt"
uv run --frozen python -m fpr.runner restart --model "$M" --src "cross_$M" --exp "restart_$M" || true
uv run --frozen python -m fpr.runner verify --model "$M" --core-only --modes message --k 2 --exp "verify_$M" || true
stop_vllm
scripts/serve.sh qwen3-8b --bg
cp logs/serve_cmd.txt logs/serve_cmd_qwen3-8b.txt
uv run --frozen python -m fpr.runner latency --model qwen3-8b --concurrency 1 || true
