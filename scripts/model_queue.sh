#!/usr/bin/env bash
# Extra-model queue: two more 8B families (first two that pass go/no-go), then the Qwen3 scale ladder (4B, 14B),
# then serve qwen3-8b again. Waits for each model's download (logs/dl_models.log) before serving it.
set -uo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"

wait_dl() {
  local repo
  repo=$(uv run --frozen python -c "import yaml,sys; print(yaml.safe_load(open('configs/models.yaml'))[sys.argv[1]]['repo'])" "$1")
  until grep -q -E "^(ok|FAIL) $repo" logs/dl_models.log 2>/dev/null; do sleep 20; done
  ! grep -q "^FAIL $repo" logs/dl_models.log
}

accepted=0
for m in llama3.1-8b granite3.3-8b ministral-8b; do
  [[ $accepted -ge 2 ]] && break
  wait_dl "$m" || { echo "[$m] download failed"; continue; }
  scripts/model_suite.sh "$m" && accepted=$((accepted + 1))
done
echo "families accepted: $accepted"

for m in qwen3-4b qwen3-14b; do
  wait_dl "$m" || { echo "[$m] download failed"; continue; }
  MIN_CLEAN=0 scripts/model_suite.sh "$m"
done

if [[ -f logs/vllm.pid ]] && kill -0 "$(cat logs/vllm.pid)" 2>/dev/null; then
  kill "$(cat logs/vllm.pid)"
  while kill -0 "$(cat logs/vllm.pid)" 2>/dev/null; do sleep 2; done
fi
until [[ $(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | head -1) -lt 3000 ]]; do sleep 2; done
scripts/serve.sh qwen3-8b --bg
cp logs/serve_cmd.txt logs/serve_cmd_qwen3-8b.txt
echo "QUEUE DONE $(date +%T)"
