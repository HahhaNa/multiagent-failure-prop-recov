#!/usr/bin/env bash
# M0 smoke test. Requires a running server (scripts/serve.sh <alias> --bg).
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
BASE="http://127.0.0.1:${PORT:-8000}"

echo "[1/4] gpu"
nvidia-smi --query-gpu=name,driver_version,memory.used,memory.total --format=csv,noheader

echo "[2/4] imports"
uv run --frozen python -c 'import vllm, agentdojo, torch; assert torch.cuda.is_available(); print("vllm", vllm.__version__, "| torch", torch.__version__)'

echo "[3/4] health"
curl -sf "$BASE/health" >/dev/null || { echo "server not up at $BASE"; exit 1; }

echo "[4/4] chat + tool call, thinking off"
uv run --frozen python - "$BASE" <<'EOF'
import json, sys
import openai

c = openai.OpenAI(base_url=sys.argv[1] + "/v1", api_key="EMPTY")
model = c.models.list().data[0].id
extra = {"chat_template_kwargs": {"enable_thinking": False}}

r = c.chat.completions.create(
    model=model, temperature=0, max_tokens=64, seed=0, extra_body=extra,
    messages=[{"role": "user", "content": "What is 17*3? Answer with just the number."}],
)
out = r.choices[0].message.content or ""
assert "<think>" not in out, out
assert "51" in out, out
print("chat ->", repr(out.strip()))

tools = [{
    "type": "function",
    "function": {
        "name": "send_money",
        "description": "Send money to an IBAN.",
        "parameters": {
            "type": "object",
            "properties": {"recipient": {"type": "string"}, "amount": {"type": "number"}},
            "required": ["recipient", "amount"],
        },
    },
}]
r = c.chat.completions.create(
    model=model, temperature=0, max_tokens=256, seed=0, extra_body=extra, tools=tools,
    messages=[{"role": "user", "content": "Send 42.5 to DE89370400440532013000."}],
)
msg = r.choices[0].message
assert "<think>" not in (msg.content or ""), msg.content
assert msg.tool_calls, msg
call = msg.tool_calls[0].function
args = json.loads(call.arguments)
assert call.name == "send_money", call
assert args["recipient"] == "DE89370400440532013000" and float(args["amount"]) == 42.5, args
print("tool ->", call.name, args)
print("tokens:", r.usage.prompt_tokens, "prompt /", r.usage.completion_tokens, "completion")
EOF
echo "SMOKE OK"
