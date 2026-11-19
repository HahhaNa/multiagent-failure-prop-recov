"""P2.0b: a call to a tool outside the agent's allowed list returns the error text to the model."""

import asyncio

from conftest import TASKS

from fpr.agents.base import Ctx, tool_loop
from fpr.env.agentdojo_adapter import TaskEnv
from fpr.env.blackboard import Blackboard
from fpr.env.clock import VirtualClock
from fpr.env.external_world import ExternalWorld
from fpr.env.state_store import StateStore
from fpr.llm.client import ChatRequest, ChatResponse, ToolCall
from fpr.trace.events import EventLog


class Scripted:
    def __init__(self, calls: list[tuple[str, str]]):
        self.calls, self.requests = calls, []

    async def chat(self, req: ChatRequest, meta: dict) -> ChatResponse:
        self.requests.append(req)
        if len(self.requests) <= len(self.calls):
            name, args = self.calls[len(self.requests) - 1]
            return ChatResponse(tool_calls=[ToolCall(id="x", name=name, arguments=args)])
        return ChatResponse(content="DONE")


def _ctx(specs, llm) -> Ctx:
    spec = specs[TASKS[0]]
    log = EventLog(None, clock=VirtualClock())
    store = StateStore(log.emit)
    env = TaskEnv("v1.2.2", spec.suite, spec.task_id, store, ExternalWorld(log.emit))
    return Ctx(env=env, bb=Blackboard(store), log=log, clock=log.clock, llm=llm, model="m", temperature=0.3, ref_seed=0, branch_seed=0, max_tokens=64)


def test_disallowed_tool_returns_error_text(specs):
    llm = Scripted([("send_money", '{"recipient": "X", "amount": 1, "subject": "s", "date": "2022-01-01"}'), ("read_shared", '{"path": "/shared/plan.json"}')])
    ctx = _ctx(specs, llm)
    allowed = ctx.env.read_tools
    asyncio.run(tool_loop(ctx, "researcher", [{"role": "user", "content": "t"}], ctx.env.tool_schemas(allowed), allowed, 4))
    tool_msgs = [m for m in llm.requests[-1].messages if m["role"] == "tool"]
    assert len(tool_msgs) == 2
    assert "ToolNotFoundError: `send_money` is not available to the researcher." in tool_msgs[0]["content"]
    assert "ToolNotFoundError: `read_shared` is not available to the researcher." in tool_msgs[1]["content"]
    calls = [e for e in ctx.log.events if e["kind"] == "tool_call"]
    assert all(e["error"] and e["error"] == e["result"] for e in calls)
    assert not ctx.env.trace and not ctx.env.world.effects
