#!/usr/bin/env bash
# Screening: all candidates, both modes, seed 0; passing tasks again with seed 1; then select 20.
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
uv run --frozen python -m fpr.runner screen0 "$@"
uv run --frozen python -m fpr.runner screen1 "$@"
uv run --frozen python -m fpr.runner select "$@"
