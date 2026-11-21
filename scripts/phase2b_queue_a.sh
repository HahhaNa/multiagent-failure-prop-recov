#!/usr/bin/env bash
# Phase 2b GPU queue A: runs that do not depend on the MELON port selection (Amendment 10).
set -u
cd "$(dirname "$0")/.."
R="uv run python -m fpr.runner"
until [ "$(find ~/fpr-runs/adv_nodef -name DONE | wc -l)" -ge 1046 ]; do sleep 30; done
$R base --src adv_nodef --method nodef
$R a2
$R advtc
$R base --src a2_nodef --method nodef
$R base --src advtc_nodef --method nodef
$R base --src main_h2 --method nodef --seeds 100,101
$R base --src adv_nodef --method causalarmor
for d in spotlight repeat toolfilter promptarmor pidetector ipiguard; do
  $R base --src adv_nodef --method "$d"
done
$R base --src main_h2 --method causalarmor --seeds 100,101
echo QUEUE_A_DONE
