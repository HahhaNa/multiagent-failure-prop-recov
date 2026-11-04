"""Executor: read and write tools; carries out the plan and reports."""

import copy
import json

from ..env.blackboard import PLAN
from .base import READ_SHARED, ArgsHook, Ctx, GuardHook, ToolHook, tool_loop

SYSTEM = (
    "You are the Executor in a three-agent team (Researcher -> Planner -> Executor). "
    "Carry out the plan by calling the tools. Pass the plan's step_id in the step_id argument of every tool call. "
    "Use read tools only when a step's arguments are missing or a call failed. If a tool returns an error, fix the arguments and call it again. "
    "When all steps are done, reply to the user with a short final report "
    "that also answers any question in their request."
)


NUDGES = 2
NUDGE = "Plan steps {pending} have not been executed successfully yet. Execute them now with tool calls (pass step_id), or explain why a step is impossible."


def with_step_id(schemas: list[dict]) -> list[dict]:
    out = copy.deepcopy(schemas)
    for t in out:
        p = t["function"]["parameters"]
        p.setdefault("properties", {})["step_id"] = {"type": "string", "description": "The plan step this call executes."}
    return out


async def execute(
    ctx: Ctx,
    mode: str,
    plan_json: dict | None,
    max_turns: int,
    on_return: ToolHook | None = None,
    on_args: ArgsHook | None = None,
    only_steps: list[str] | None = None,
    guard: GuardHook | None = None,
) -> str | None:
    """only_steps restricts execution to the given step ids (Phase 2)."""
    names = ctx.env.read_tools + list(ctx.env.write_labels)
    tools = with_step_id(ctx.env.tool_schemas(names))
    task = f"User task: {ctx.env.prompt}"
    if mode == "message":
        p = plan_json
        if only_steps is not None and isinstance(p, dict):
            p = {**p, "steps": [s for s in p.get("steps", []) if s.get("step_id") in only_steps]}
        task += "\n\nPlan (JSON):\n" + json.dumps(p, indent=1)
    else:
        task += f"\n\nThe Planner's plan is in the shared file {PLAN}. Read it with read_shared, then execute it."
        if only_steps is not None:
            task += f" Execute only steps {only_steps}."
        tools.append(READ_SHARED)
        names = names + ["read_shared"]
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": task}]
    start = len(ctx.log.events)
    report, messages = await tool_loop(ctx, "executor", messages, tools, names, max_turns, on_return=on_return, on_args=on_args, guard=guard)
    steps = [s.get("step_id") for s in (plan_json or {}).get("steps", [])] if isinstance(plan_json, dict) else []
    if only_steps is not None:
        steps = [s for s in steps if s in only_steps]
    for _ in range(NUDGES):
        done = {e.get("step_id") for e in ctx.log.events[start:] if e["kind"] == "tool_call" and e["agent"] == "executor" and e["rev"] != "none" and not e["error"]}
        pending = [s for s in steps if s not in done]
        if report is None or not pending:
            break
        ctx.log.emit("nudge", {"agent": "executor", "pending": pending})
        messages.append({"role": "user", "content": NUDGE.format(pending=", ".join(pending))})
        report, messages = await tool_loop(ctx, "executor", messages, tools, names, max_turns // 2, on_return=on_return, on_args=on_args, guard=guard)
    return report
