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


def test_prefix_matrix_replays_other_seed_prefix(specs, mock, tmp_path):
    import asyncio
    import json

    from conftest import TASKS

    from fpr.llm.cache import CachedLLM, DiskCache
    from fpr.runner import Runner, load_cfg, prefix_matrix

    runs = prefix_matrix(TASKS, 1, ["message", "state"], "p", k=1)
    assert {r.ref_seed for r in runs} == {1}
    r = Runner(CachedLLM(mock, DiskCache(tmp_path / "cache"), "record"), load_cfg(), specs, "mock", "mock", 64, tmp_path)
    asyncio.run(r.run_all(runs))
    assert r.stats["failed"] == 0
    ev = lambda d: [json.loads(x) for x in (d / "events.jsonl").read_text().splitlines()]
    for t in TASKS:
        for m in ("message", "state"):
            ref = [e for e in ev(tmp_path / "p" / t / m / "ref_s1") if e["kind"] == "llm_call"]
            for d in (tmp_path / "p" / t / m).iterdir():
                if d.name == "ref_s1":
                    continue
                pre = [e for e in ev(d) if e["kind"] == "llm_call" and not e["branched"]]
                assert all(e["replayed"] for e in pre), d
                assert [e["key"] for e in pre] == [e["key"] for e in ref[: len(pre)]], d


def test_thinking_kwarg_only_when_flagged():
    import asyncio
    import json

    import httpx

    from fpr.llm.client import ChatRequest, OpenAIClient

    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {}})

    for flag in (True, False):
        c = OpenAIClient(thinking_flag=flag)
        c.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        asyncio.run(c.chat(ChatRequest(model="m", messages=[{"role": "user", "content": "x"}]), {}))
    assert "chat_template_kwargs" in seen[0]
    assert "chat_template_kwargs" not in seen[1]
