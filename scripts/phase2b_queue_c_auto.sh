#!/usr/bin/env bash
# After queue B: restart vLLM at gpu-memory-utilization 0.80 (Amendment 12) and run queue C (CausalArmor).
set -u
cd "$(dirname "$0")/.."
until grep -q QUEUE_B_DONE logs/queue_b.log 2>/dev/null; do sleep 60; done
tmux kill-window -t fpr-runs:vllm 2>/dev/null
for _ in $(seq 1 60); do curl -sf http://127.0.0.1:8000/health >/dev/null || break; sleep 5; done
for _ in $(seq 1 60); do [ "$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits)" -lt 2000 ] && break; sleep 5; done
tmux new-window -d -t fpr-runs -n vllm "cd $PWD && GPU_UTIL=0.80 scripts/serve.sh qwen3-8b 2>&1 | tee logs/vllm_p2b_u80.log"
for _ in $(seq 1 120); do curl -sf http://127.0.0.1:8000/health >/dev/null && break; sleep 5; done
scripts/phase2b_queue_c.sh
