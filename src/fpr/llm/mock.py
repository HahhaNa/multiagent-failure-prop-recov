"""Deterministic oracle LLM built from task ground truth. Used for tests before any GPU run.

It behaves like a compliant but gullible team: the researcher reports the critical facts,
the planner copies argument values from claims, and the executor runs the plan and follows
injected instructions it sees.
"""

import json
import re
from typing import Any

from ..scoring.taint import contains
from ..tasks import TaskSpec
from .client import ChatRequest, ChatResponse, ToolCall

MARK = "<INFORMATION>"


def _last_json(text: str) -> Any:
    i = text.find("{")
    while i != -1:
        try:
            return json.loads(text[i:])
        except json.JSONDecodeError:
            i = text.find("{", i + 1)
    return None


class MockLLM:
    def __init__(self, specs: dict[str, TaskSpec]):
        self.specs = specs
        self.calls = 0

    def _spec(self, req: ChatRequest) -> TaskSpec:
        user = next(m["content"] for m in req.messages if m["role"] == "user")
        hits = [s for s in self.specs.values() if s.prompt in user]
        if not hits:
            raise KeyError("unknown task prompt")
        return max(hits, key=lambda s: len(s.prompt))

    async def chat(self, req: ChatRequest, meta: dict) -> ChatResponse:
        self.calls += 1
        spec = self._spec(req)
        agent = meta["agent"]
        guided = req.response_format is not None
        state = "/shared/" in req.messages[1]["content"]
        fn = getattr(self, f"_{agent}")
        content, calls = fn(spec, req.messages, guided, state)
        tcs = [ToolCall(id=f"m{i}", name=n, arguments=json.dumps(a)) for i, (n, a) in enumerate(calls)]
        n_in = sum(len(str(m.get("content", ""))) for m in req.messages) // 4
        return ChatResponse(content=content, tool_calls=tcs, prompt_tokens=n_in, completion_tokens=20 + 5 * len(tcs), finish_reason="stop")

    @staticmethod
    def _done(messages: list[dict]) -> list[tuple[str, dict]]:
        out = []
        for m in messages:
            for tc in m.get("tool_calls") or []:
                out.append((tc["function"]["name"], json.loads(tc["function"]["arguments"])))
        return out

    @staticmethod
    def _tool_texts(messages: list[dict]) -> list[str]:
        return [m["content"] for m in messages if m["role"] == "tool"]

    def _researcher(self, spec, messages, guided, state):
        if not guided:
            n = len(self._done(messages))
            if n < len(spec.gt_reads):
                c = spec.gt_reads[n]
                return "", [(c["function"], c["args"])]
            return "DONE", []
        ids = [tc["id"] for m in messages for tc in (m.get("tool_calls") or [])]
        claims = []
        for i, f in enumerate(spec.critical_facts):
            v = ", ".join(map(str, f.value)) if isinstance(f.value, list) else str(f.value)
            claims.append({"claim_id": f"c{i + 1}", "text": f"{f.name} is {v}.", "value": v, "source_tool_call_ids": ids[:1]})
        for nc in spec.noncritical_facts[:1]:
            claims.append({"claim_id": f"c{len(claims) + 1}", "text": f"Also seen: {nc.value}.", "value": str(nc.value), "source_tool_call_ids": ids[:1]})
        for t in self._tool_texts(messages):
            if MARK in t:
                body = t[t.index(MARK) : t.index("</INFORMATION>") + 14] if "</INFORMATION>" in t else t[t.index(MARK) :]
                claims.append({"claim_id": f"c{len(claims) + 1}", "text": body, "value": spec.canary, "source_tool_call_ids": ids[-1:]})
        return json.dumps({"claims": claims}), []

    def _planner(self, spec, messages, guided, state):
        if state and not guided and not self._tool_texts(messages):
            return "", [("read_shared", {"path": "/shared/findings.json"})]
        if not guided:
            return "Ready.", []
        texts = self._tool_texts(messages) if state else [messages[1]["content"]]
        findings = _last_json(texts[-1] if texts else "") or {}
        claims = {c["claim_id"]: c for c in findings.get("claims", [])} if isinstance(findings, dict) else {}
        steps = []
        for k, call in enumerate(spec.gt_writes):
            args, cites = json.loads(json.dumps(call["args"])), []
            for i, f in enumerate(spec.critical_facts):
                if f.call != spec.write_index[k]:
                    continue
                cid = f"c{i + 1}"
                c = claims.get(cid)
                new = _from_claim(f.value, c["value"]) if c else ("UNKNOWN" if not isinstance(f.value, list) else [])
                _set(args, f.path, new)
                if c:
                    cites.append(cid)
            steps.append({"step_id": f"s{k + 1}", "action": call["function"], "args": args, "cites": cites})
        if any(spec.canary in json.dumps(c) for c in claims.values()):
            for c in spec.injection_calls:
                steps.append({"step_id": f"s{len(steps) + 1}", "action": c["function"], "args": c["args"], "cites": []})
        return json.dumps({"steps": steps}), []

    def _executor(self, spec, messages, guided, state):
        done = self._done(messages)
        texts = self._tool_texts(messages)
        if state and not any(n == "read_shared" for n, _ in done):
            return "", [("read_shared", {"path": "/shared/plan.json"})]
        plan = _last_json(texts[0]) if state else _last_json(messages[1]["content"])
        plan = plan if isinstance(plan, dict) else {}
        todo = [(s["action"], {**s["args"], "step_id": s["step_id"]}) for s in plan.get("steps", [])]
        if MARK in json.dumps(plan) or any(MARK in t for t in texts):
            todo += [(c["function"], dict(c["args"])) for c in spec.injection_calls]
        done_keys = [_key(n, a) for n, a in done]
        for n, a in todo:
            if _key(n, a) not in done_keys:
                return "", [(n, a)]
        return spec.gt_output or "Done.", []


def _key(n: str, a: dict) -> str:
    a = {k: v for k, v in a.items() if k != "step_id"}
    return n + json.dumps(a, sort_keys=True)


def _set(args: dict, path: list, v) -> None:
    x = args
    for p in path[:-1]:
        x = x[p]
    x[path[-1]] = v


def _from_claim(true_value, claim_value: str):
    if isinstance(true_value, list):
        return [x.strip() for x in re.split(r",\s*", claim_value) if x.strip()]
    if isinstance(true_value, bool):
        return true_value
    if isinstance(true_value, (int, float)):
        try:
            f = float(claim_value.replace(",", ""))
            return int(f) if isinstance(true_value, int) else f
        except ValueError:
            return claim_value
    return claim_value


__all__ = ["MockLLM", "contains"]
