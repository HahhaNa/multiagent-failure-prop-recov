#!/usr/bin/env bash
# Phase 2 Tier 1 recovery matrix on Qwen3-8B (design §11), resumable. Probes are shared through the cache, so B = inf runs first.
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
p2() { uv run --frozen python -m fpr.runner "$@" >> logs/p2_methods.log 2>&1; }
p2 p2 --method sprt
for b in 32 16 8 4; do p2 p2 --method sprt --params "budget=$b"; done
for m in gb0 oneshot support judge all; do p2 p2 --method "$m"; done
for x in compensation=0 snapshot=1 tau1=0.2 tau1=0.5 alpha=0.01 alpha=0.1; do p2 p2 --method sprt --params "$x"; done
p2 p2cal
for d in verifier melon; do
  p2 p2detect --method "$d"
  for m in all sprt; do p2 p2 --method "$m" --params "detector=$d"; done
done
until grep -q '^finished' logs/gt_h2.log; do sleep 30; done
p2 p2 --method oracle
echo done > logs/p2_methods.done
