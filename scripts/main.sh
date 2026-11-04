#!/usr/bin/env bash
# Main matrix: k=5, all 17 conditions (resumable).
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
uv run --frozen python -m fpr.runner main "$@"
