#!/usr/bin/env bash
# Harness-fixed Qwen3-8B reruns (design 7.1): main_h2 and verify_h2 in parallel, then restart_h2 over main_h2.
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
mkdir -p logs
uv run --frozen python -m fpr.runner main --exp main_h2 > logs/main_h2.log 2>&1 &
p1=$!
uv run --frozen python -m fpr.runner verify --exp verify_h2 > logs/verify_h2.log 2>&1 &
p2=$!
wait "$p1"
uv run --frozen python -m fpr.runner restart --src main_h2 --exp restart_h2 > logs/restart_h2.log 2>&1
wait "$p2"
echo "h2 done" > logs/h2.done
