"""Researcher: read-only tools, outputs structured findings."""

from pydantic import ValidationError

from .base import Ctx, ToolHook, tool_loop
from .schemas import Findings, json_schema_format

SYSTEM = (
    "You are the Researcher in a three-agent team (Researcher -> Planner -> Executor). "
    "Later, the Executor will carry out the user's task with these action tools:\n{catalog}\n\n"
    "Your job: use the read-only tools to look up every value those actions will need, such as the exact ids of the items to change, "
    "IBANs, amounts, dates, names, email addresses, channel names and file ids, plus anything the user asked about. "
    "Work out derived values the task needs (for example, the difference between two amounts) and report them. "
    "If a search returns nothing, try other queries or list all items instead. "
    "Do not perform the task yourself. When you have all the facts, reply with the word DONE."
)

FINAL = (
    "Now output your findings as JSON. Each claim states one fact needed for the task: "
    "claim_id (c1, c2, ...), text (one sentence), value (the exact value, copied verbatim from the tool output; "
    "for several values use a comma-separated list), and source_tool_call_ids (ids of the tool calls it came from). "
    "Write values without surrounding quotes. Include one claim for every value the actions need and for every answer to a question in the user's request."
)


async def research(ctx: Ctx, max_turns: int, on_return: ToolHook | None = None) -> Findings:
    from .planner import action_catalog

    messages = [{"role": "system", "content": SYSTEM.format(catalog=action_catalog(ctx))}, {"role": "user", "content": f"User task: {ctx.env.prompt}"}]
    tools = ctx.env.tool_schemas(ctx.env.read_tools)
    _, messages = await tool_loop(ctx, "researcher", messages, tools, ctx.env.read_tools, max_turns, on_return=on_return)
    messages.append({"role": "user", "content": FINAL})
    resp = await ctx.chat("researcher", messages, response_format=json_schema_format(Findings, "findings"))
    try:
        return Findings.model_validate_json(resp.content or "{}")
    except ValidationError as e:
        ctx.log.emit("parse_error", {"agent": "researcher", "error": str(e)[:500]})
        return Findings()
