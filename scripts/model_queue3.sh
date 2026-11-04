#!/usr/bin/env bash
# Third queue: after model_queue2 (qwen3-14b, then qwen3-8b served), run the Qwen3-8B validation stages
# (cf10: k=10 clean continuations; prefix_s1/s2: other reference prefixes), then hermes3-8b and ministral-8b-mt
# through the same clean-success gate, then serve qwen3-8b again.
set -uo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
RUN=(uv run --frozen python -m fpr.runner)

until grep -q "QUEUE DONE" logs/model_queue2.log 2>/dev/null; do sleep 30; done
echo "[validation] cf10 $(date +%T)"
"${RUN[@]}" cf10
for s in 1 2; do
  echo "[validation] prefix_s$s $(date +%T)"
  "${RUN[@]}" prefix --prefix-seed "$s"
done
SKIP_SMOKE=1 scripts/model_suite.sh hermes3-8b
SKIP_SMOKE=1 scripts/model_suite.sh ministral-8b-mt

if [[ -f logs/vllm.pid ]] && kill -0 "$(cat logs/vllm.pid)" 2>/dev/null; then
  kill "$(cat logs/vllm.pid)"
  while kill -0 "$(cat logs/vllm.pid)" 2>/dev/null; do sleep 2; done
fi
until [[ $(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | head -1) -lt 3000 ]]; do sleep 2; done
scripts/serve.sh qwen3-8b --bg
cp logs/serve_cmd.txt logs/serve_cmd_qwen3-8b.txt
echo "QUEUE DONE $(date +%T)"
