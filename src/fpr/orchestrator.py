"""Runs one researcher -> planner -> executor episode with optional fault injection."""

import gzip
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from .agents.base import Ctx
from .agents.executor import execute
from .agents.planner import plan as make_plan
from .agents.researcher import research
from .agents.schemas import UNAVAILABLE
from .agents.verifier import evidence, guard, verify_findings, verify_plan
from .env.agentdojo_adapter import TaskEnv
from .env.blackboard import FINDINGS, PLAN, Blackboard
from .env.clock import VirtualClock
from .env.external_world import ExternalWorld
from .env.snapshots import dump
from .env.state_store import StateStore
from .faults.injectors import Injector, as_json
from .faults.payloads import injection_text
from .faults.spec import by_id
from .llm.cache import CacheMiss
from .llm.client import LLM
from .scoring.outcome import score
from .scoring.recovery import recovery_metrics
from .tasks import TaskSpec
from .trace.events import EventLog
from .trace.graph import build as build_graphs


@dataclass
class EpisodeConfig:
    spec: TaskSpec
    mode: str
    fault_id: str | None = None
    ref_seed: int = 0
    branch_seed: int = 0
    model: str = "mock"
    temperature: float = 0.3
    max_tokens: int = 1024
    version: str = "v1.2.2"
    turns: dict = field(default_factory=lambda: {"researcher": 8, "planner": 3, "executor": 10})
    clock: dict = field(default_factory=lambda: {"stage_timeout_s": 300})
    tool_output_chars: int = 6000
    expect_replay: bool = False
    single_tool_calls: bool = False
    verify: bool = False  # R3 verify-all mode
    prior_effects: list[dict] | None = None  # R1: external effects of the faulted run, re-applied first


@dataclass
class Episode:
    cfg: EpisodeConfig
    llm: LLM
    run_dir: Path | None = None

    def __post_init__(self) -> None:
        c = self.cfg
        clock_kw = {k: v for k, v in c.clock.items() if k != "stage_timeout_s"}
        self.timeout_s = c.clock.get("stage_timeout_s", 300)
        self.clock = VirtualClock(**clock_kw)
        self.log = EventLog(self.run_dir / "events.jsonl" if self.run_dir else None, clock=self.clock)
        self.store = StateStore(self.log.emit)
        self.world = ExternalWorld(self.log.emit)
        self.fault = by_id(c.fault_id) if c.fault_id else None
        inj_env = {}
        if self.fault and self.fault.type == "B1" and self.fault.point == "R.tool":
            inj_env = {v: injection_text(c.spec.injection_goal) for v in c.spec.vectors}
        self.env = TaskEnv(c.version, c.spec.suite, c.spec.task_id, self.store, self.world, c.tool_output_chars, inj_env)
        self.bb = Blackboard(self.store)
        self.ctx = Ctx(
            env=self.env, bb=self.bb, log=self.log, clock=self.clock, llm=self.llm, model=c.model,
            temperature=c.temperature, ref_seed=c.ref_seed, branch_seed=c.branch_seed, max_tokens=c.max_tokens,
            expect_replay=c.expect_replay, single_tool_calls=c.single_tool_calls,
        )
        self.inj = Injector(c.spec, self.fault, self.ctx)
        self.artifacts: dict = {}
        for n, e in enumerate(c.prior_effects or []):
            err = self.env.apply_prior(e["tool"], e["args"], n)
            self.log.emit("prior_effect", {"n": n, "tool": e["tool"], "args": e["args"], "rev": e["rev"], "tainted": e.get("tainted", False), "error": err})

    def _artifact(self, name: str, agent: str, content, vid: str | None = None) -> None:
        self.artifacts[name] = content
        self.log.emit("artifact", {"name": name, "agent": agent, "content": content, "version": vid})

    async def _researcher(self):
        for attempt in (1, 2):
            t0 = self.clock.now
            if self.inj.timeout("researcher", attempt):
                self.clock.advance(self.timeout_s, "timeout")
                self.log.emit("stage_timeout", {"stage": "researcher", "attempt": attempt, "injected": True})
                continue
            f = await research(self.ctx, self.cfg.turns["researcher"], on_return=self.inj.r_tool)
            if self.clock.now - t0 > self.timeout_s:
                self.log.emit("stage_timeout", {"stage": "researcher", "attempt": attempt, "injected": False})
                continue
            return f
        self.log.emit("unavailable", {"stage": "researcher"})
        return dict(UNAVAILABLE)

    async def _planner(self, mode: str, fj: dict):
        for attempt in (1, 2):
            if self.inj.timeout("planner", attempt):
                self.clock.advance(self.timeout_s, "timeout")
                self.log.emit("stage_timeout", {"stage": "planner", "attempt": attempt, "injected": True})
                continue
            return await make_plan(self.ctx, mode, fj, self.cfg.turns["planner"])
        self.log.emit("unavailable", {"stage": "planner"})
        return None

    def _gate(self, plan_json: dict):
        async def gate(name: str, args: dict) -> str | None:
            v = await guard(self.ctx, plan_json, name, args)
            return None if v.allow else (v.reason or "denied")

        return gate

    async def run(self) -> dict:
        c, mode = self.cfg, self.cfg.mode
        t_wall = time.perf_counter()
        report, error = None, None
        self.log.emit("episode_start", {"task": c.spec.key, "mode": mode, "fault": c.fault_id, "ref_seed": c.ref_seed, "branch_seed": c.branch_seed})
        try:
            findings = await self._researcher()
            findings = self.inj.r_out(findings)
            fj = as_json(findings)
            vid = self.bb.write(FINDINGS, fj, "researcher")
            self._artifact("findings", "researcher", fj, vid)
            ev = evidence(self.log.events) if c.verify else ""
            if c.verify:
                if mode == "state":
                    self.bb.read(FINDINGS, "verifier")
                fj, rec = await verify_findings(self.ctx, fj, ev)
                self.log.emit("verify", rec)
                if rec["changed"]:
                    vid = self.bb.write(FINDINGS, fj, "verifier")
                self._artifact("findings_verified", "verifier", fj, vid)
            if mode == "message":
                self.log.emit("message", {"from": "verifier" if c.verify else "researcher", "to": "planner", "artifact": "findings", "version": vid})
            plan = await self._planner(mode, fj)
            if plan is None:
                pj = dict(UNAVAILABLE)
                self._artifact("plan_raw", "planner", pj)
            else:
                self._artifact("plan_raw", "planner", plan.model_dump())
                plan = self.inj.p_out(plan)
                pj = as_json(plan)
            vid = self.bb.write(PLAN, pj, "planner")
            self._artifact("plan", "planner", pj, vid)
            if mode == "state":
                poisoned = self.inj.s_write(pj)
                if poisoned is not None:
                    pvid = self.bb.write(PLAN, poisoned, "attacker" if self.fault.family == "adversarial" else "corruption")
                    self.log.emit("poison", {"obj": "bb:" + PLAN, "version": pvid})
            gate = None
            if c.verify:
                cur = self.bb.read(PLAN, "verifier")[0] if mode == "state" else pj
                pj, rec = await verify_plan(self.ctx, cur, fj, ev)
                self.log.emit("verify", rec)
                if mode == "state" and rec["changed"]:
                    vid = self.bb.write(PLAN, pj, "verifier")
                self._artifact("plan_verified", "verifier", pj, vid)
                gate = self._gate(pj)
            if mode == "message":
                self.log.emit("message", {"from": "verifier" if c.verify else "planner", "to": "executor", "artifact": "plan", "version": vid})
            report = await execute(self.ctx, mode, pj, c.turns["executor"], on_return=self.inj.e_tool, on_args=self.inj.e_args, guard=gate)
        except CacheMiss:
            raise
        except Exception as e:  # noqa: BLE001 - recorded as a stall; the run is kept
            error = f"{type(e).__name__}: {str(e)[:500]}"
            self.log.emit("error", {"error": error})
        if self.fault and not self.inj.fired:
            self.inj.record = {"cond": self.fault.id, "type": self.fault.type, "point": self.fault.point, "applied": False, "reason": "not_reached"}
            self.log.emit("fault", self.inj.record)
        self._artifact("report", "executor", report)
        self.log.emit("episode_end", {"stalled": report is None, "error": error})
        summary = score(self, report, error)
        if c.prior_effects is not None:
            summary.update(recovery_metrics(c.prior_effects, summary["external_effects"], summary["security_violation"]))
        summary["wall_s"] = round(time.perf_counter() - t_wall, 3)
        summary["overhead"] = {k: round(v, 6) for k, v in self.store.timers.items()}
        if self.run_dir:
            self.log.flush()
            (self.run_dir / "artifacts.json").write_text(json.dumps(self.artifacts, indent=1, default=str))
            (self.run_dir / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
            (self.run_dir / "transcripts.json").write_text(json.dumps(self.ctx.transcripts, default=str))
            snaps = {o: [{"vid": v.vid, "writer": v.writer, "digest": v.digest, "bytes": v.size, "value": dump(v.value)} for v in vs] for o, vs in self.store.versions.items()}
            with gzip.open(self.run_dir / "snapshots.json.gz", "wt") as fh:
                json.dump(snaps, fh, default=str)
            g = build_graphs(self.log.events, self.inj.taint_values())
            (self.run_dir / "graphs.json").write_text(json.dumps(g, default=str))
        return summary
