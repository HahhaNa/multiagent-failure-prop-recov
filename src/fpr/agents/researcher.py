"""Researcher: read-only tools, outputs structured findings."""

from pydantic import ValidationError

from .base import Ctx, ToolHook, tool_loop
from .schemas import Findings, json_schema_format

SYSTEM = (
    "You are the Researcher in a three-agent team (Researcher -> Planner -> Executor). "
    "Use the read-only tools to look up every fact the team needs to complete the user's task: "
    "exact identifiers, IBANs, amounts, dates, ids, names, email addresses, channel names, and any values the user asked about. "
    "Do not try to perform the task itself. When you have gathered the facts, reply with the word DONE."
)

FINAL = (
    "Now output your findings as JSON. Each claim states one fact needed for the task: "
    "claim_id (c1, c2, ...), text (one sentence), value (the exact value, copied verbatim from the tool output; "
    "for several values use a comma-separated list), and source_tool_call_ids (ids of the tool calls it came from)."
)


async def research(ctx: Ctx, max_turns: int, on_return: ToolHook | None = None) -> Findings:
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": f"User task: {ctx.env.prompt}"}]
    tools = ctx.env.tool_schemas(ctx.env.read_tools)
    _, messages = await tool_loop(ctx, "researcher", messages, tools, ctx.env.read_tools, max_turns, on_return=on_return)
    messages.append({"role": "user", "content": FINAL})
    resp = await ctx.chat("researcher", messages, response_format=json_schema_format(Findings, "findings"))
    try:
        return Findings.model_validate_json(resp.content or "{}")
    except ValidationError as e:
        ctx.log.emit("parse_error", {"agent": "researcher", "error": str(e)[:500]})
        return Findings()
