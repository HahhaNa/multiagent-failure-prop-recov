#!/usr/bin/env bash
# Pilot: 5 selected tasks x all conditions (+PC) x k=1, then the go/no-go report.
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
uv run --frozen python -m fpr.runner pilot "$@"
uv run --frozen python analysis/pilot_gonogo.py "$@"
