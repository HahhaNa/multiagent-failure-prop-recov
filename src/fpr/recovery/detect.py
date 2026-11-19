"""Realistic detection (design §6, Amendment 8): offline detectors on a finished faulted run F.

A detector returns the first event index of F that would not have happened (recovery starts there), or None.
"""

import json

from ..agents.base import Ctx
from ..agents.executor import SYSTEM as EXECUTOR_SYSTEM
from ..agents.executor import executor_tools
from ..agents.verifier import evidence, guard, verify_findings, verify_plan
from ..env.blackboard import Blackboard
from ..llm.client import ChatRequest
from .faulted import FREE_TEXT, Artifact, FaultedRun, sig

MASK = "Summarize the contents of the following tool outputs."


def _art_pos(F: FaultedRun, name: str) -> int | None:
    return next((e["i"] for e in F.events if e["kind"] == "artifact" and e["name"] == name), None)


def _e2(a: Artifact | tuple[str, dict]) -> str:
    name, args = (a.name, a.args) if isinstance(a, Artifact) else a
    return sig(name, {k: v for k, v in (args or {}).items() if k not in FREE_TEXT})


async def verifier(F: FaultedRun, ctx: Ctx) -> dict:
    """R3's V1/V2/V3 prompts on F's findings, plan and writes after the fault; first flag wins."""
    point = F.point
    ev = evidence(F.events)
    findings = F.findings_seen() if isinstance(F.findings_seen(), dict) else {}
    plan = F.plan_seen() if isinstance(F.plan_seen(), dict) else {"steps": []}
    if point in ("R.tool", "R.start", "R.out"):
        _, rec = await verify_findings(ctx, findings, ev)
        if rec.get("issues") or rec.get("changed"):
            return {"detected": True, "stage": "V1", "detect_at": (_art_pos(F, "findings") or 0) + 1, "record": rec}
    if point in ("R.tool", "R.start", "R.out", "P.start", "P.out", "S.write"):
        _, rec = await verify_plan(ctx, plan, findings, ev)
        if rec.get("issues") or rec.get("changed"):
            pos = _art_pos(F, "plan") or 0
            if point == "S.write":
                pos = next((e["i"] for e in F.events if e["kind"] == "poison"), pos)
            return {"detected": True, "stage": "V2", "detect_at": pos + 1, "record": rec}
    for a in F.calls():
        if a.name not in ctx.env.write_labels:
            continue
        v = await guard(ctx, plan, a.name, a.args)
        if not v.allow:
            return {"detected": True, "stage": "V3", "detect_at": a.pos, "record": {"call": a.id, "reason": v.reason}}
    return {"detected": False}


def melon_request(F: FaultedRun, a: Artifact, env, model: str, temperature: float, max_tokens: int, seed: int) -> ChatRequest | None:
    """The executor request that issued `a`, with the user task and plan masked and only the tool outputs kept."""
    for conv in F.transcripts.get("executor") or []:
        for k, m in enumerate(conv):
            if m.get("role") == "assistant" and any(tc["id"] == a.call_id for tc in m.get("tool_calls") or []):
                outs = [x["content"] for x in conv[:k] if x.get("role") == "tool"]
                msgs = [{"role": "system", "content": EXECUTOR_SYSTEM}, {"role": "user", "content": MASK + "\n\n" + "\n\n".join(outs)}]
                tools, _ = executor_tools(env, "message")
                return ChatRequest(model=model, messages=msgs, tools=tools, temperature=temperature, seed=seed, max_tokens=max_tokens)
    return None


async def melon(F: FaultedRun, ctx: Ctx, raw) -> dict:
    """MELON-style: a write that recurs when the task is masked is driven by tool outputs, i.e. an injection."""
    for a in F.calls():
        if a.name not in ctx.env.write_labels:
            continue
        req = melon_request(F, a, ctx.env, ctx.model, ctx.temperature, ctx.max_tokens, ctx.branch_seed)
        if req is None:
            continue
        resp = await raw("melon", "melon", req)
        got = set()
        for tc in resp.tool_calls:
            try:
                got.add(_e2((tc.name, json.loads(tc.arguments or "{}"))))
            except (ValueError, AttributeError):
                continue
        if _e2(a) in got:
            return {"detected": True, "stage": "MELON", "detect_at": a.pos, "record": {"call": a.id}}
    return {"detected": False}


async def run_detector(rec, name: str) -> dict:
    """rec: a Recovery instance (provides env, ctx and the accounted raw LLM call)."""
    F = rec.F
    env, _ = rec._build(F.post_calls(), [], log=False)
    rec.ctx = ctx = rec._ctx(env)
    ctx.bb = Blackboard(env.store)
    out = await (verifier(F, ctx) if name == "verifier" else melon(F, ctx, rec._raw))
    u = ctx.usage
    return {**out, "detector": name, "detector_tokens": u["prompt"] + u["completion"], "detector_calls": u["calls"]}
