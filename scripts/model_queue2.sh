#!/usr/bin/env bash
# Second extra-model queue (replaces the tail of model_queue.sh): wait for the running qwen3-4b suite, then the two 8B
# families (granite: smoke checks value fidelity, so it is skipped; ministral: rerun as -v2 after the tool call id fix),
# then qwen3-14b, then serve qwen3-8b again.
set -uo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"

until [[ -f logs/suite_qwen3-4b.done || -f logs/suite_qwen3-4b.nogo ]]; do sleep 30; done
SKIP_SMOKE=1 scripts/model_suite.sh granite3.3-8b
SUFFIX=-v2 scripts/model_suite.sh ministral-8b
MIN_CLEAN=0 scripts/model_suite.sh qwen3-14b

if [[ -f logs/vllm.pid ]] && kill -0 "$(cat logs/vllm.pid)" 2>/dev/null; then
  kill "$(cat logs/vllm.pid)"
  while kill -0 "$(cat logs/vllm.pid)" 2>/dev/null; do sleep 2; done
fi
until [[ $(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | head -1) -lt 3000 ]]; do sleep 2; done
scripts/serve.sh qwen3-8b --bg
cp logs/serve_cmd.txt logs/serve_cmd_qwen3-8b.txt
echo "QUEUE DONE $(date +%T)"
