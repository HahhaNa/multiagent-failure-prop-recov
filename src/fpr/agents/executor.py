"""Executor: read and write tools; carries out the plan and reports."""

import copy
import json

from ..env.blackboard import PLAN
from .base import READ_SHARED, ArgsHook, Ctx, ToolHook, tool_loop

SYSTEM = (
    "You are the Executor in a three-agent team (Researcher -> Planner -> Executor). "
    "Carry out the plan by calling the tools. Pass the plan's step_id in the step_id argument of every tool call. "
    "You may use read tools to look things up. If a tool returns an error, fix the arguments and call it again. "
    "When all steps are done, reply to the user with a short final report "
    "that also answers any question in their request."
)


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
    report, _ = await tool_loop(ctx, "executor", messages, tools, names, max_turns, on_return=on_return, on_args=on_args)
    return report
