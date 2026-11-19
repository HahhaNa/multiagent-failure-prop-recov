"""Probe requests rebuilt from F's transcripts with corrected parents substituted (design §4 step 4).

With no correction and the original seed, a rebuilt request equals the original byte for byte (acceptance test 6).
"""

import copy
import json

from ..agents.executor import PLAN_MARK, executor_tools
from ..agents.planner import FINDINGS_MARK, OUTPUT, plan_format
from ..agents.schemas import Plan
from ..env.blackboard import FINDINGS, PLAN
from ..llm.client import ChatRequest, ChatResponse
from .faulted import FaultedRun, sig


def _read_shared_ids(messages: list[dict], path: str) -> set[str]:
    ids = set()
    for m in messages:
        for tc in m.get("tool_calls") or []:
            if tc["function"]["name"] != "read_shared":
                continue
            try:
                p = json.loads(tc["function"]["arguments"] or "{}").get("path", "")
            except (ValueError, AttributeError):
                continue
            if p == path:
                ids.add(tc["id"])
    return ids


def _sub_shared(messages: list[dict], path: str, value) -> None:
    ids = _read_shared_ids(messages, path)
    for m in messages:
        if m.get("role") == "tool" and m.get("tool_call_id") in ids:
            m["content"] = f"[tool_call_id: {m['tool_call_id']}]\n" + json.dumps(value, indent=1)


def _sub_user(messages: list[dict], mark: str, value) -> None:
    m = messages[1]
    if m.get("role") == "user" and mark in m["content"]:
        m["content"] = m["content"].split(mark, 1)[0] + mark + json.dumps(value, indent=1)


def _seed_of(F: FaultedRun, agent: str, pred) -> int:
    for e in F.events:
        if e["kind"] == "llm_call" and e["agent"] == agent and pred(e):
            return e["seed"]
    return 0


class ProbeBuilder:
    def __init__(self, F: FaultedRun, env, model: str, temperature: float, max_tokens: int):
        self.F, self.env = F, env
        self.model, self.temperature, self.max_tokens = model, temperature, max_tokens

    def _req(self, messages, tools, fmt, seed) -> ChatRequest:
        return ChatRequest(model=self.model, messages=messages, tools=tools or None, response_format=fmt, temperature=self.temperature,
                           seed=seed, max_tokens=self.max_tokens)

    # ---- planner (L2) ----
    def planner_conv(self) -> list[dict] | None:
        convs = self.F.transcripts.get("planner") or []
        for conv in reversed(convs):
            if len(conv) >= 2 and conv[-2].get("role") == "user" and conv[-2].get("content") == OUTPUT:
                return conv
        return None

    def planner_request(self, findings: dict | None, seed: int | None = None) -> ChatRequest | None:
        """The planner's final guided request; findings=None keeps F's input."""
        conv = self.planner_conv()
        if conv is None:
            return None
        msgs = copy.deepcopy(conv[:-1])
        if findings is not None:
            if self.F.mode == "message":
                _sub_user(msgs, FINDINGS_MARK, findings)
            else:
                _sub_shared(msgs, FINDINGS, findings)
        if seed is None:
            seed = _seed_of(self.F, "planner", lambda e: e.get("content") == conv[-1].get("content") and not e["tool_calls"])
        return self._req(msgs, None, plan_format(self.env), seed)

    @staticmethod
    def plan_sigs(resp: ChatResponse) -> set[str]:
        try:
            p = Plan.model_validate_json(resp.content or "{}")
        except ValueError:
            return set()
        return {sig(s.action, s.args) for s in p.steps}

    # ---- executor (L3) ----
    def executor_request(self, call_id: str, plan: dict | None, origin_text: str | None = None, seed: int | None = None) -> ChatRequest | None:
        """The executor request whose response issued call_id; plan / origin_text substitute the corrected parents."""
        for conv in self.F.transcripts.get("executor") or []:
            for k, m in enumerate(conv):
                if m.get("role") == "assistant" and any(tc["id"] == call_id for tc in m.get("tool_calls") or []):
                    msgs = copy.deepcopy(conv[:k])
                    if plan is not None:
                        if self.F.mode == "message":
                            _sub_user(msgs, PLAN_MARK, plan)
                        else:
                            _sub_shared(msgs, PLAN, plan)
                    o = self.F.origin_call()
                    if origin_text is not None and o is not None and self.F.point == "E.tool":
                        for x in msgs:
                            if x.get("role") == "tool" and x.get("tool_call_id") == o["call_id"]:
                                x["content"] = f"[tool_call_id: {o['call_id']}]\n{origin_text}"
                    tools, _ = executor_tools(self.env, self.F.mode)
                    if seed is None:
                        seed = _seed_of(self.F, "executor", lambda e: any(tc["id"] == call_id for tc in e["tool_calls"]))
                    return self._req(msgs, tools, None, seed)
        return None

    @staticmethod
    def call_sigs(resp: ChatResponse) -> set[str]:
        out = set()
        for tc in resp.tool_calls:
            try:
                a = json.loads(tc.arguments or "{}")
            except ValueError:
                continue
            if isinstance(a, dict):
                out.add(sig(tc.name, a))
        return out
