#!/usr/bin/env bash
# Reordered tail (2026-11-22): CausalArmor first (vLLM at 0.80, Amendment 12), then the last two queue-B ablations at 0.90.
set -u
cd "$(dirname "$0")/.."
R="uv run python -m fpr.runner"
serve() {  # restart our vLLM with the given gpu-memory-utilization
  tmux kill-window -t fpr-runs:vllm 2>/dev/null
  for _ in $(seq 1 60); do curl -sf http://127.0.0.1:8000/health >/dev/null || break; sleep 5; done
  for _ in $(seq 1 60); do [ "$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits)" -lt 2000 ] && break; sleep 5; done
  tmux new-window -d -t fpr-runs -n vllm "cd $PWD && GPU_UTIL=$1 scripts/serve.sh qwen3-8b 2>&1 | tee logs/vllm_p2b_u$1.log"
  for _ in $(seq 1 120); do curl -sf http://127.0.0.1:8000/health >/dev/null && break; sleep 5; done
}
serve 0.80
scripts/phase2b_queue_c.sh
serve 0.90
for m in ccg_mnzro ccg_nremove; do $R gate --src adv_nodef --method "$m"; done
echo QUEUE_B_DONE
