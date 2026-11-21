"""Re-derivation primitive Rederive(root, j, Q, s) (design §3).

A task-root re-derivation is an Episode of the same task, mode and fault as the source run. Nodes before the fork
replay the source run exactly (identical requests, cache hits); the fork node is re-observed; later calls use the gate
seed. Transient faults never fire at or after a fork; persistent injections do. Quarantined reads (same tool and
arguments) return neutralized text. A masked re-derivation replaces the user task with a neutral instruction at
every agent and reads the source run's tool outputs as context (MELON's masking lifted to the pipeline).
"""

import copy
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from ..agents.base import Ctx
from ..agents.executor import execute
from ..agents.planner import action_catalog
from ..agents.planner import plan as make_plan
from ..agents.researcher import SYSTEM as RESEARCHER_SYSTEM
from ..agents.researcher import research_from
from ..env.agentdojo_adapter import TaskEnv
from ..env.blackboard import Blackboard
from ..env.clock import VirtualClock
from ..env.external_world import ExternalWorld
from ..env.state_store import StateStore
from ..faults.injectors import Injector, as_json
from ..faults.payloads import env_payload, injection_text
from ..faults.spec import by_id
from ..orchestrator import Episode, EpisodeConfig
from ..trace.events import EventLog
from .nodes import Node, node_list, pending, tool_key

MASK = "Summarize the contents of the following tool outputs."
PERSISTENT = {"B1", "ADV", "ADVTC"}  # data injections; channel tampering and accidental faults are transient


@dataclass
class Fork:
    j: int  # node index in the trajectory being forked (0 = start)
    kind: str  # start | rtool | findings | plan | etool | exec_start
    sub: int
    seed: int
    quarantine: dict[str, str] = field(default_factory=dict)  # tool_key -> text, applied after this fork; "*" = all reads


@dataclass
class Traj:
    """A trajectory: the source run or a re-derivation. forks replay it; quarantine is its cumulative quarantine."""

    events: list[dict]
    report: str | None
    forks: list[Fork] = field(default_factory=list)
    artifacts: dict = field(default_factory=dict)

    @property
    def nodes(self) -> list[Node]:
        return node_list(self.events)

    @property
    def quarantine(self) -> dict[str, str]:
        return dict(self.forks[-1].quarantine) if self.forks else {}

    def fork_at(self, j: int, seed: int, extra: dict[str, str] | None = None, exec_start: bool = False) -> list[Fork]:
        """Forks that replay this trajectory up to node j and re-observe node j (or restart the executor)."""
        nodes = self.nodes
        q = {**self.quarantine, **(extra or {})}
        if exec_start:
            first = next((n for n in nodes if n.kind == "etool"), None)
            j = first.j if first else len(nodes)
            return [f for f in self.forks if f.j < j] + [Fork(j, "exec_start", 0, seed, q)]
        if j >= len(nodes):
            return list(self.forks)
        n = nodes[j]
        return [f for f in self.forks if f.j < j] + [Fork(j, n.kind, n.sub, seed, q)]


Neut = Callable[[str], Awaitable[str]]


class GateInjector(Injector):
    """The source run's fault before the first fork; interventions after it."""

    def __init__(self, spec, fault, ctx, forks: list[Fork], neut: Neut, origin: tuple[str, dict] | None):
        super().__init__(spec, fault, ctx)
        self.forks, self.neut, self.origin = sorted(forks, key=lambda f: f.j), neut, origin
        self.n_fork = 0  # forks taken so far
        self.r_count = self.e_count = 0
        self.exec_chats = 0
        self.persistent = fault is not None and fault.type in PERSISTENT

    # ---- fork bookkeeping ----
    @property
    def forked(self) -> bool:
        return self.n_fork > 0

    @property
    def quarantine(self) -> dict[str, str]:
        return self.forks[self.n_fork - 1].quarantine if self.forked else {}

    def _due(self, kind: str, sub: int = 0) -> bool:
        if self.n_fork >= len(self.forks):
            return False
        f = self.forks[self.n_fork]
        return f.kind == kind and f.sub == sub

    def _take(self) -> None:
        f = self.forks[self.n_fork]
        self.n_fork += 1
        self.ctx.fork(f.seed)
        self.ctx.log.emit("gate_fork", {"j": f.j, "node_kind": f.kind, "sub": f.sub, "seed": f.seed, "n_quarantine": len(f.quarantine)})

    def before_chat(self, agent: str, guided: bool) -> None:
        if self._due("start"):
            self._take()
        if agent == "researcher" and guided and self._due("findings") or agent == "planner" and guided and self._due("plan"):
            self._take()
        elif agent == "executor":
            if self.exec_chats == 0 and self._due("exec_start"):
                self._take()
            self.exec_chats += 1

    def _transient_off(self) -> bool:
        return self.forked and not self.persistent

    async def _quarantined(self, name: str, args: dict, result: str) -> str:
        q = self.quarantine
        if "*" in q or tool_key(name, args) in q:
            return await self.neut(result) if "*" in q else q[tool_key(name, args)]
        return result

    # ---- hooks ----
    def timeout(self, stage: str, attempt: int) -> bool:
        if self._due("start"):
            self._take()
        if not self.forked:
            would = super().timeout(stage, attempt)
            if would and (stage == "researcher" and self._due("findings") or stage == "planner" and self._due("plan")):
                self._take()  # the stage the source run lost to a timeout is re-observed
                return False
            return would
        if self._transient_off():
            return False
        return super().timeout(stage, attempt)

    async def r_tool(self, name: str, args: dict, result: str) -> str:
        k = self.r_count
        self.r_count += 1
        if not self.forked and not self._due("rtool", k):
            return super().r_tool(name, args, result)
        if self._due("rtool", k):
            self._take()  # the read itself is the re-observation: no transient fault on it
        if self.persistent:
            result = super().r_tool(name, args, result)
        return await self._quarantined(name, args, result)

    async def e_tool(self, name: str, args: dict, result: str) -> str:
        if name == "read_shared":
            return result
        k = self.e_count
        self.e_count += 1
        if not self.forked and not self._due("etool", k):
            return super().e_tool(name, args, result)
        if not self.forked or self._due("etool", k):
            self._take()
        if self.persistent:
            if self.fault.point == "env":
                result = super().e_tool(name, args, result)
            elif self.origin is not None and tool_key(name, args) == tool_key(*self.origin):
                result = result + injection_text(self.spec.injection_goal)
                if not self.fired:
                    self._fire(True, target=name, original=None, injected=self.spec.canary)
        return await self._quarantined(name, args, result)

    def r_out(self, findings):
        return findings if self._transient_off() else super().r_out(findings)

    def p_out(self, plan):
        return plan if self._transient_off() else super().p_out(plan)

    def s_write(self, plan_json: dict):
        return None if self._transient_off() else super().s_write(plan_json)


class GateEpisode(Episode):
    def __init__(self, cfg: EpisodeConfig, llm, forks: list[Fork], neut: Neut, origin: tuple[str, dict] | None):
        self._forks, self._neut, self._origin = forks, neut, origin
        super().__init__(cfg, llm, None)

    def __post_init__(self) -> None:
        super().__post_init__()
        self.inj = GateInjector(self.cfg.spec, self.fault, self.ctx, self._forks, self._neut, self._origin)
        self.ctx.before_chat = self.inj.before_chat


@dataclass
class Base:
    """Everything a re-derivation needs about the source run F."""

    spec: object
    mode: str
    fault_id: str | None
    ref_seed: int
    branch_seed: int
    origin: tuple[str, dict] | None  # B1@E.tool origin read (persistent payload target)
    cfg: dict  # model, temperature, max_tokens, version, turns, clock, tool_output_chars, single_tool_calls


def env_injections(base: Base) -> dict[str, str]:
    if not base.fault_id:
        return {}
    f = by_id(base.fault_id)
    if f.type == "B1" and f.point == "R.tool":
        return {v: injection_text(base.spec.injection_goal) for v in base.spec.vectors}
    if f.point == "env":
        return {v: env_payload(f.type, base.spec) for v in base.spec.vectors}
    return {}


async def rederive(base: Base, llm, forks: list[Fork], neut: Neut) -> Traj:
    c = base.cfg
    ec = EpisodeConfig(spec=base.spec, mode=base.mode, fault_id=base.fault_id, ref_seed=base.ref_seed, branch_seed=base.branch_seed,
                       model=c["model"], temperature=c["temperature"], max_tokens=c["max_tokens"], version=c["version"], turns=c["turns"],
                       clock=c["clock"], tool_output_chars=c["tool_output_chars"], single_tool_calls=c["single_tool_calls"])
    ep = GateEpisode(ec, llm, forks, neut, base.origin)
    await ep.run()
    return Traj(ep.log.events, ep.artifacts.get("report"), list(forks), dict(ep.artifacts))


async def masked(base: Base, llm, src: Traj, seed: int, extra: dict[str, str] | None = None) -> Traj:
    """Masked pipeline in message mode over src's observed tool outputs; extra quarantines more reads."""
    c = base.cfg
    q = {**src.quarantine, **(extra or {})}
    clock = VirtualClock(**{k: v for k, v in c["clock"].items() if k != "stage_timeout_s"})
    log = EventLog(None, clock=clock)
    store = StateStore(log.emit)
    env = TaskEnv(c["version"], base.spec.suite, base.spec.task_id, store, ExternalWorld(log.emit), c["tool_output_chars"],
                  env_injections(base), prompt_override=MASK)
    ctx = Ctx(env=env, bb=Blackboard(store), log=log, clock=clock, llm=llm, model=c["model"], temperature=c["temperature"], ref_seed=seed,
              branch_seed=seed, max_tokens=c["max_tokens"], branched=True, single_tool_calls=c["single_tool_calls"],
              meta={"task": base.spec.key})

    def seen(agent: str) -> list[str]:
        out = []
        for e in src.events:
            if e["kind"] == "tool_call" and e["agent"] == agent and e["name"] != "read_shared":
                out.append(q.get(tool_key(e["name"], e["args"]), str(e["result"])))
        return out

    msgs = [{"role": "system", "content": RESEARCHER_SYSTEM.format(catalog=action_catalog(ctx))},
            {"role": "user", "content": f"User task: {MASK}\n\nTool outputs:\n" + "\n\n".join(seen("researcher"))}]
    findings = as_json(await research_from(ctx, msgs, 0))
    plan = (await make_plan(ctx, "message", findings, c["turns"]["planner"])).model_dump()
    origin = base.origin
    persistent_e = base.fault_id is not None and by_id(base.fault_id).type == "B1" and by_id(base.fault_id).point == "E.tool"

    async def hook(name: str, args: dict, result: str) -> str:
        if persistent_e and origin is not None and tool_key(name, args) == tool_key(*origin):
            result = result + injection_text(base.spec.injection_goal)
        return q.get(tool_key(name, args), result)

    ex = seen("executor")
    context = ("Tool outputs observed so far:\n" + "\n\n".join(ex)) if ex else None
    report = await execute(ctx, "message", plan, c["turns"]["executor"], on_return=hook, context=context)
    return Traj(log.events, report, [], {"findings": findings, "plan": plan})


def writes_of(t: Traj) -> set[str]:
    return {w.sig for w in pending(t.events)}


def copy_events(events: list[dict]) -> list[dict]:
    return copy.deepcopy(events)
