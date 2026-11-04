#!/usr/bin/env bash
# Primary matrix (resumable). Extra args go to the runner, e.g. --k 2 --core-only.
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
uv run --frozen python -m fpr.runner primary "$@"
