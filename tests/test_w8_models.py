import re

import yaml
from conftest import ROOT

from fpr.llm.client import alnum9_ids


def test_alnum9_ids_rewrites_pairs_consistently():
    msgs = [
        {"role": "user", "content": "x"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "rcall_0", "type": "function", "function": {"name": "f", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "rcall_0", "content": "[tool_call_id: rcall_0]\nok"},
    ]
    out = alnum9_ids(msgs)
    new = out[1]["tool_calls"][0]["id"]
    assert re.fullmatch(r"[a-zA-Z0-9]{9}", new)
    assert out[2]["tool_call_id"] == new
    assert msgs[1]["tool_calls"][0]["id"] == "rcall_0"  # input untouched
    assert alnum9_ids(msgs) == out


def test_extra_models_are_pinned():
    models = yaml.safe_load((ROOT / "configs/models.yaml").read_text())
    for alias in ("llama3.1-8b", "granite3.3-8b", "ministral-8b", "qwen3-4b", "qwen3-14b"):
        m = models[alias]
        assert re.fullmatch(r"[0-9a-f]{40}", m["revision"]), alias
        assert m["tool_parser"]
