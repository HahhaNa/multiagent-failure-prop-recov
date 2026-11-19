#!/usr/bin/env bash
# Real-model pilot of the Phase 2 methods on two held-out tasks (design §11).
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
T=banking.user_task_0,slack.user_task_11
for m in all gb0 sprt oneshot support judge; do
  uv run --frozen python -m fpr.runner p2 --method "$m" --tasks "$T" --exp "p2pilot_$m" >> logs/p2pilot.log 2>&1
done
echo done > logs/p2pilot.done
