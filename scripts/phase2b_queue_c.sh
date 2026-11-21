#!/usr/bin/env bash
# Phase 2b GPU queue C (Amendment 12): CausalArmor-exec with vLLM at gpu-memory-utilization 0.80; run after queues A and B.
set -u
cd "$(dirname "$0")/.."
R="uv run python -m fpr.runner"
$R base --src adv_nodef --method causalarmor --exp base_causalarmor_adv_nodef_u80
$R base --src main_h2 --method causalarmor --seeds 100,101 --exp base_causalarmor_main_h2_u80
echo QUEUE_C_DONE
