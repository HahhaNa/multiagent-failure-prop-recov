"""Shared LLM call and tool loop used by all agents."""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..env.agentdojo_adapter import TaskEnv
from ..env.blackboard import Blackboard
from ..env.clock import VirtualClock
from ..llm.client import LLM, ChatRequest, ChatResponse
from ..trace.events import EventLog

READ_SHARED = {
    "type": "function",
    "function": {
        "name": "read_shared",
        "description": "Read a shared coordination file written by another agent, e.g. /shared/findings.json or /shared/plan.json.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    },
}


@dataclass
class Ctx:
    env: TaskEnv
    bb: Blackboard
    log: EventLog
    clock: VirtualClock
    llm: LLM
    model: str
    temperature: float
    ref_seed: int
    branch_seed: int
    max_tokens: int
    branched: bool = False
    expect_replay: bool = False
    single_tool_calls: bool = False  # chat template accepts one tool call per assistant turn
    counters: dict[str, int] = field(default_factory=dict)
    usage: dict[str, int] = field(default_factory=lambda: {"prompt": 0, "completion": 0, "calls": 0, "live": 0})
    transcripts: dict[str, list[list[dict]]] = field(default_factory=dict)

    def _record_transcript(self, agent: str, messages: list[dict], resp: ChatResponse) -> None:
        """Keep each distinct conversation once, extended in place as it grows."""
        conv = [*messages, assistant_msg(resp)]
        convs = self.transcripts.setdefault(agent, [])
        if convs and len(convs[-1]) <= len(messages) and messages[: len(convs[-1]) - 1] == convs[-1][:-1]:
            convs[-1] = conv
        else:
            convs.append(conv)

    def branch(self) -> None:
        if not self.branched:
            self.branched = True
            self.log.emit("branch", {"seed": self.branch_seed})

    def next_id(self, prefix: str) -> str:
        n = self.counters.get(prefix, 0)
        self.counters[prefix] = n + 1
        return f"{prefix}_{n}"

    async def chat(self, agent: str, messages: list[dict], tools: list[dict] | None = None, response_format: dict | None = None) -> ChatResponse:
        seed = self.branch_seed if self.branched else self.ref_seed
        req = ChatRequest(
            model=self.model,
            messages=messages,
            tools=tools or None,
            response_format=response_format,
            temperature=self.temperature,
            seed=seed,
            max_tokens=self.max_tokens,
        )
        resp = await self.llm.chat(req, {"agent": agent, "mode": "guided" if response_format else "tools"})
        for tc in resp.tool_calls:
            tc.id = self.next_id(f"{agent[0]}call")
        self.clock.charge_llm(resp.prompt_tokens, resp.completion_tokens)
        self._record_transcript(agent, messages, resp)
        self.usage["prompt"] += resp.prompt_tokens
        self.usage["completion"] += resp.completion_tokens
        self.usage["calls"] += 1
        self.usage["live"] += 0 if resp.replayed else 1
        if self.expect_replay and not self.branched and not resp.replayed:
            self.log.emit("prefix_miss", {"agent": agent, "key": req.key()})
        self.log.emit(
            "llm_call",
            {
                "agent": agent,
                "key": req.key(),
                "seed": seed,
                "branched": self.branched,
                "replayed": resp.replayed,
                "prompt_tokens": resp.prompt_tokens,
                "completion_tokens": resp.completion_tokens,
                "latency_s": round(resp.latency_s, 4),
                "finish_reason": resp.finish_reason,
                "content": resp.content,
                "tool_calls": [tc.model_dump() for tc in resp.tool_calls],
            },
        )
        return resp


def assistant_msg(resp: ChatResponse) -> dict:
    m: dict[str, Any] = {"role": "assistant", "content": resp.content or ""}
    if resp.tool_calls:
        m["tool_calls"] = [
            {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": tc.arguments}} for tc in resp.tool_calls
        ]
    return m


def parse_args(raw: str) -> tuple[dict, str | None]:
    try:
        a = json.loads(raw or "{}")
        return (a, None) if isinstance(a, dict) else ({}, "ValueError: arguments must be a JSON object")
    except json.JSONDecodeError as e:
        return {}, f"JSONDecodeError: {e}"


ToolHook = Callable[[str, dict, str], str]
ArgsHook = Callable[[str, dict], dict]


async def tool_loop(
    ctx: Ctx,
    agent: str,
    messages: list[dict],
    tools: list[dict],
    allowed: list[str],
    max_turns: int,
    on_return: ToolHook | None = None,
    on_args: ArgsHook | None = None,
) -> tuple[str | None, list[dict]]:
    """Run tool turns until the model answers without tool calls. Returns (final text or None, messages)."""
    for _ in range(max_turns):
        resp = await ctx.chat(agent, messages, tools)
        if not resp.tool_calls:
            messages.append(assistant_msg(resp))
            return resp.content or "", messages
        if not ctx.single_tool_calls:
            messages.append(assistant_msg(resp))
        for k, tc in enumerate(resp.tool_calls):
            if ctx.single_tool_calls:
                messages.append(assistant_msg(resp.model_copy(update={"tool_calls": [tc], "content": resp.content if k == 0 else ""})))
            args, err = parse_args(tc.arguments)
            step_id = args.pop("step_id", None) if isinstance(args, dict) else None
            if on_args is not None and err is None:
                args = on_args(tc.name, args)
            reads: list[str] = []
            writes: list[str] = []
            if err:
                result = err
            elif tc.name == "read_shared":
                path = str(args.get("path", ""))
                result = ctx.bb.read_text(path, agent) if "read_shared" in allowed else "ToolNotFoundError"
                key = f"bb:{path}"
                if key in ctx.bb.store.versions:
                    reads = [ctx.bb.store.current(key).vid]
            else:
                result, err, reads, writes = ctx.env.call(tc.name, args, agent, tc.id, allowed)
            ctx.clock.charge_tool()
            if on_return is not None:
                result = on_return(tc.name, args, result)
            ctx.log.emit(
                "tool_call",
                {
                    "agent": agent,
                    "call_id": tc.id,
                    "name": tc.name,
                    "args": args,
                    "step_id": step_id,
                    "error": err,
                    "result": result,
                    "reads": reads,
                    "writes": writes,
                    "rev": ctx.env.reversibility(tc.name),
                },
            )
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": f"[tool_call_id: {tc.id}]\n{result}"})
    return None, messages
