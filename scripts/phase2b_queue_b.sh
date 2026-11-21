#!/usr/bin/env bash
# Phase 2b GPU queue B: runs that use the selected masked instruction (configs/phase2b.yaml mask_text, Amendment 10).
# Main-table rows first; ablations reuse cached detection samples.
set -u
cd "$(dirname "$0")/.."
R="uv run python -m fpr.runner"
S="--seeds 100,101"
$R gate --src adv_nodef --method ccg
for m in melon_exec melon_all agentsentry; do $R base --src adv_nodef --method "$m"; done
for m in ccg_block ccg_1shot ccg_purify ccg_m ccg_ro; do $R gate --src adv_nodef --method "$m"; done
$R gate --src main_h2 --method ccg $S
for m in melon_exec melon_all agentsentry; do $R base --src main_h2 --method "$m" $S; done
for m in ccg_block ccg_1shot ccg_purify ccg_m ccg_ro; do $R gate --src main_h2 --method "$m" $S; done
for m in ccg ccg_mnzro; do $R gate --src advtc_nodef --method "$m"; done
for m in ccg ccg_mnzro ccg_nz ccg_nz_remove; do $R gate --src a2_nodef --method "$m"; done
for m in ccg_mnzro ccg_nremove; do $R gate --src adv_nodef --method "$m"; done
echo QUEUE_B_DONE
