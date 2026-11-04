"""Planner: no environment tools; turns findings into an executable plan."""

import json

from pydantic import ValidationError

from ..env.blackboard import FINDINGS
from .base import READ_SHARED, Ctx, tool_loop
from .schemas import Plan, json_schema_format

SYSTEM = (
    "You are the Planner in a three-agent team (Researcher -> Planner -> Executor). "
    "You write the plan that the Executor will carry out. Each step is one call to an action tool: "
    "step_id (s1, s2, ...), action (the tool name), args (the exact arguments), and cites (the claim_ids the arguments come from). "
    "Every argument value that is not stated in the user's request MUST be copied from a claim value, and you must cite that claim. "
    "Use the exact formats the tools expect (dates YYYY-MM-DD, date-times YYYY-MM-DD HH:MM). "
    "Give only the arguments the task needs; leave optional arguments out unless the user asked to change them. "
    "Only include steps that change state or send something; do not include lookups. If the task only asks for information, return an empty plan."
)


def action_catalog(ctx: Ctx) -> str:
    lines = []
    for t in ctx.env.tool_schemas(list(ctx.env.write_labels)):
        f = t["function"]
        props = f["parameters"].get("properties", {})
        params = ", ".join(f"{k}: {v.get('type', 'any')}" for k, v in props.items())
        lines.append(f"- {f['name']}({params}): {f['description'].splitlines()[0]}")
    return "\n".join(lines)


async def plan(
    ctx: Ctx,
    mode: str,
    findings_json: dict | None,
    max_turns: int,
    keep_steps: list[dict] | None = None,
    invalid_claims: list[str] | None = None,
) -> Plan:
    """keep_steps / invalid_claims enable partial replanning (Phase 2)."""
    task = f"User task: {ctx.env.prompt}\n\nAction tools available to the Executor:\n{action_catalog(ctx)}"
    if mode == "message":
        task += "\n\nResearcher findings (JSON):\n" + json.dumps(findings_json, indent=1)
    else:
        task += f"\n\nThe Researcher's findings are in the shared file {FINDINGS}. Read it with read_shared before planning."
    if keep_steps is not None:
        task += (
            "\n\nPartial replanning. Keep these steps unchanged:\n" + json.dumps(keep_steps, indent=1)
            + f"\nClaims {invalid_claims} were invalid and have been corrected. Regenerate only the steps that depend on them."
        )
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": task}]
    if mode == "state":
        _, messages = await tool_loop(ctx, "planner", messages, [READ_SHARED], ["read_shared"], max_turns)
    messages.append({"role": "user", "content": "Output the plan as JSON."})
    fmt = json_schema_format(Plan, "plan")
    step = fmt["json_schema"]["schema"]["$defs"]["PlanStep"]["properties"]
    step["action"] = {"type": "string", "enum": sorted(ctx.env.write_labels)}
    resp = await ctx.chat("planner", messages, response_format=fmt)
    try:
        return Plan.model_validate_json(resp.content or "{}")
    except ValidationError as e:
        ctx.log.emit("parse_error", {"agent": "planner", "error": str(e)[:500]})
        return Plan()
