#!/usr/bin/env bash
# Phase 1 tables for one extra model (after scripts/model_suite.sh), then the cross-model comparison.
# Usage: scripts/analyze_model.sh <model-alias> [...]
set -euo pipefail
cd "$(dirname "$0")/../analysis"
for M in "$@"; do
  uv run --frozen python phase1_tables.py --exp "main_$M"
  uv run --frozen python phase1_counterfactual.py --exp "main_$M"
  uv run --frozen python phase1_counterfactual.py --exp "verify_$M"
  uv run --frozen python phase1_recovery.py --main "main_$M" --restart "restart_$M" --verify "verify_$M" --tag "recovery_$M"
done
uv run --frozen python phase1_models.py
