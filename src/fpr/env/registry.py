"""Chooses the benchmark adapter for a suite name."""

from .agentdojo_adapter import TaskEnv
from .external_world import ExternalWorld
from .state_store import StateStore


def make_env(version: str, suite_name: str, task_id: str, store: StateStore, world: ExternalWorld, max_chars: int, injections: dict[str, str]):
    if suite_name.startswith("tau2_"):
        from .tau2_adapter import Tau2Env

        return Tau2Env(version, suite_name, task_id, store, world, max_chars, injections)
    return TaskEnv(version, suite_name, task_id, store, world, max_chars, injections)
