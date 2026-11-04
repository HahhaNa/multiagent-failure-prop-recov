"""Wraps AgentDojo suites: tools, environments, ground truth and checkers."""

import copy
import datetime as dt
import types
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

import yaml
from agentdojo.agent_pipeline.tool_execution import tool_result_to_str
from agentdojo.default_suites.v1.tools import cloud_drive_client, email_client
from agentdojo.functions_runtime import FunctionCall, FunctionsRuntime
from agentdojo.task_suite.load_suites import get_suite

from .external_world import ExternalWorld
from .state_store import StateStore

ROOT = Path(__file__).resolve().parents[3]
FROZEN_NOW = dt.datetime(2024, 5, 15, 10, 0)


class _FrozenDatetime(dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return FROZEN_NOW


def freeze_tool_clocks() -> None:
    """AgentDojo email and drive tools stamp wall-clock time; pin it so tool results are reproducible."""
    shim = types.SimpleNamespace(**{k: getattr(dt, k) for k in dir(dt) if not k.startswith("__")})
    shim.datetime = _FrozenDatetime
    for mod in (email_client, cloud_drive_client):
        mod.datetime = shim


freeze_tool_clocks()


@cache
def tools_cfg() -> dict:
    return yaml.safe_load((ROOT / "configs/tools.yaml").read_text())


@cache
def suite(version: str, name: str):
    return get_suite(version, name)


def task_key(suite_name: str, task_id: str) -> str:
    return f"{suite_name}.{task_id}"


def split_key(key: str) -> tuple[str, str]:
    s, t = key.split(".", 1)
    return s, t


def openai_tool(f) -> dict:
    return {"type": "function", "function": {"name": f.name, "description": f.description, "parameters": f.parameters.model_json_schema()}}


@dataclass
class TaskEnv:
    """One episode's private environment. Never shared between episodes."""

    version: str
    suite_name: str
    task_id: str
    store: StateStore
    world: ExternalWorld
    max_chars: int = 6000
    injections: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.suite = suite(self.version, self.suite_name)
        self.task = self.suite.get_user_task_by_id(self.task_id)
        env = self.suite.load_and_inject_default_environment(self.injections)
        self.env = self.task.init_environment(env)
        self.pre_env = self.env.model_copy(deep=True)
        self.runtime = FunctionsRuntime(self.suite.tools)
        self.cfg = tools_cfg()[self.suite_name]
        self.read_tools = list(self.cfg["read"])
        self.write_labels: dict[str, dict] = self.cfg["write"]
        self.external_reads = set(self.cfg.get("read_external", []))
        self.trace: list[FunctionCall] = []
        for name in type(self.env).model_fields:
            self.store.register(f"env:{name}", getattr(self.env, name))

    @property
    def prompt(self) -> str:
        return self.task.PROMPT

    def tool_schemas(self, names: list[str]) -> list[dict]:
        return [openai_tool(self.runtime.functions[n]) for n in names]

    def deps(self, name: str) -> list[str]:
        f = self.runtime.functions.get(name)
        if f is None:
            return []
        return [d.env_dependency for d in f.dependencies.values() if isinstance(d.env_dependency, str)]

    def reversibility(self, name: str) -> str:
        if name in self.write_labels:
            return self.write_labels[name]["rev"]
        return "irreversible" if name in self.external_reads else "none"

    def is_external(self, name: str) -> bool:
        return name in self.external_reads or self.write_labels.get(name, {}).get("external", False)

    def call(self, name: str, args: dict, agent: str, call_id: str, allowed: list[str]) -> tuple[str, str | None, list[str], list[str]]:
        """Run a tool. Returns (result text, error, versions read, versions written)."""
        if name not in allowed:
            return "", f"ToolNotFoundError: `{name}` is not available to the {agent}.", [], []
        deps = self.deps(name)
        reads = [self.store.read(f"env:{d}", agent).vid for d in deps]
        result, err = self.runtime.run_function(self.env, name, copy.deepcopy(args))
        writes = []
        for d in deps:
            v = self.store.observe(f"env:{d}", getattr(self.env, d), f"{agent}:{call_id}")
            if v is not None:
                writes.append(v.vid)
        if err is None:
            self.trace.append(FunctionCall(function=name, args=copy.deepcopy(args), id=call_id))
            if self.is_external(name):
                self.world.record(name, args, call_id, self.reversibility(name))
        text = err if err else tool_result_to_str(result)
        if len(text) > self.max_chars:
            text = text[: self.max_chars] + "\n...[truncated]"
        return text, err, reads, writes

    def apply_prior(self, name: str, args: dict, n: int) -> str | None:
        """Re-apply an external effect of an earlier run (R1). Not part of this run's trace or external log."""
        _, err = self.runtime.run_function(self.env, name, copy.deepcopy(args))
        for d in self.deps(name):
            self.store.observe(f"env:{d}", getattr(self.env, d), f"prior_effect:{n}")
        return err

    def restore(self, vids: list[str], writer: str = "restore") -> None:
        """Per-object restore of env attributes to stored versions (Phase 2 hook)."""
        for vid in vids:
            obj = vid.rsplit("@", 1)[0]
            name = obj.split(":", 1)[1]
            setattr(self.env, name, self.store.value_at(vid))
            self.store.observe(obj, getattr(self.env, name), writer)

    def utility(self, model_output: str) -> bool:
        t = self.task
        r = t.utility_from_traces(model_output, self.pre_env, self.env, self.trace)
        if r is None:
            r = t.utility(model_output, self.pre_env, self.env)
        return bool(r)

    def security(self, injection_task_id: str, model_output: str) -> bool:
        """True if the injection goal was achieved (a security violation)."""
        it = self.suite.get_injection_task_by_id(injection_task_id)
        r = it.security_from_traces(model_output, self.pre_env, self.env, self.trace)
        if r is None:
            r = it.security(model_output, self.pre_env, self.env)
        return bool(r)
