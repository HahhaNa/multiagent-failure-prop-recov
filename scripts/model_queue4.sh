#!/usr/bin/env bash
# Fourth queue: re-run the clean-success gate for the four non-Qwen 8B candidates after the harness stopped sending
# the Qwen3-only thinking kwarg to them (experiments suffixed -v4, separate cache), full matrix for any that pass,
# then serve qwen3-8b again.
set -uo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
for m in llama3.1-8b granite3.3-8b hermes3-8b ministral-8b-mt; do
  SKIP_SMOKE=1 SUFFIX=-v4 scripts/model_suite.sh "$m"
done
if [[ -f logs/vllm.pid ]] && kill -0 "$(cat logs/vllm.pid)" 2>/dev/null; then
  kill "$(cat logs/vllm.pid)"
  while kill -0 "$(cat logs/vllm.pid)" 2>/dev/null; do sleep 2; done
fi
until [[ $(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | head -1) -lt 3000 ]]; do sleep 2; done
scripts/serve.sh qwen3-8b --bg
cp logs/serve_cmd.txt logs/serve_cmd_qwen3-8b.txt
echo "QUEUE DONE $(date +%T)"
