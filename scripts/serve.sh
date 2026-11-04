#!/usr/bin/env bash
# Usage: scripts/serve.sh <model-alias> [--bg]
# Model aliases are defined in configs/models.yaml.
set -euo pipefail
cd "$(dirname "$0")/.."

ALIAS="${1:?usage: scripts/serve.sh <model-alias> [--bg]}"
MODE="${2:-}"
PORT="${PORT:-8000}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-16384}"
GPU_UTIL="${GPU_UTIL:-0.90}"
MAX_NUM_SEQS="${MAX_NUM_SEQS:-64}"

export PATH="$HOME/.local/bin:$PATH"
read -r REPO REVISION TOOL_PARSER QUANT < <(uv run --frozen python - "$ALIAS" <<'EOF'
import sys, yaml
m = yaml.safe_load(open("configs/models.yaml"))[sys.argv[1]]
print(m["repo"], m["revision"], m["tool_parser"], m.get("quantization", "none"))
EOF
)

# JIT kernels (FlashInfer, DeepGEMM) need an nvcc matching torch's CUDA 13.0; system nvcc is 12.1.
VENV_CUDA="$PWD/.venv/lib/python3.12/site-packages/nvidia/cu13"
if [[ -x "$VENV_CUDA/bin/nvcc" ]]; then
  export CUDA_HOME="$VENV_CUDA" PATH="$VENV_CUDA/bin:$PATH"
fi
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"

CMD=(uv run --frozen vllm serve "$REPO" --revision "$REVISION"
  --max-model-len "$MAX_MODEL_LEN" --gpu-memory-utilization "$GPU_UTIL"
  --enable-prefix-caching --max-num-seqs "$MAX_NUM_SEQS"
  --enable-auto-tool-choice --tool-call-parser "$TOOL_PARSER"
  --seed 0 --host 127.0.0.1 --port "$PORT")
[[ "$QUANT" == "fp8-runtime" ]] && CMD+=(--quantization fp8)

mkdir -p logs
echo "${CMD[*]}" > logs/serve_cmd.txt

if [[ "$MODE" != "--bg" ]]; then
  exec "${CMD[@]}"
fi

if [[ -f logs/vllm.pid ]] && kill -0 "$(cat logs/vllm.pid)" 2>/dev/null; then
  echo "vllm already running (pid $(cat logs/vllm.pid))"; exit 0
fi
nohup "${CMD[@]}" > logs/vllm.log 2>&1 &
echo $! > logs/vllm.pid
echo "vllm pid $!, waiting for http://127.0.0.1:$PORT/health"
for _ in $(seq 1 300); do
  curl -sf "http://127.0.0.1:$PORT/health" >/dev/null && { echo ready; exit 0; }
  kill -0 "$(cat logs/vllm.pid)" 2>/dev/null || { tail -40 logs/vllm.log; exit 1; }
  sleep 2
done
echo "timed out"; exit 1
