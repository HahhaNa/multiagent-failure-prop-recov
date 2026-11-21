"""AgentDojo-97 task specs: every user task x every injection task of its suite, plus benign runs (design §8)."""

import copy
import json

from ..env.agentdojo_adapter import suite, tools_cfg
from ..faults.payloads import atoms
from ..scoring.taint import contains
from ..tasks import TaskSpec, _visible_vectors

SUITES = ("banking", "slack", "travel", "workspace")


def _gt(version: str, sname: str, task) -> tuple[list[dict], str]:
    s = suite(version, sname)
    env = task.init_environment(s.load_and_inject_default_environment({}))
    calls = [{"function": c.function, "args": dict(c.args)} for c in task.ground_truth(env.model_copy(deep=True))]
    return calls, task.GROUND_TRUTH_OUTPUT or ""


def _canary(icalls: list[dict], prompt: str) -> str:
    """A distinctive attacker value (IBAN, email, URL) from the injection task's ground-truth calls, for taint."""
    for c in icalls:
        for a in atoms(json.dumps(c["args"])):
            if not contains(prompt, a):
                return a
    return ""


def adv_specs(version: str = "v1.2.2", suites: tuple[str, ...] = SUITES, benign: bool = True) -> dict[str, TaskSpec]:
    """Keys: '<suite>.<user_task>+<injection_task>' (attack cases) and '<suite>.<user_task>+none' (benign)."""
    out: dict[str, TaskSpec] = {}
    for sn in suites:
        s = suite(version, sn)
        cfg = tools_cfg()[sn]
        for tid, task in s.user_tasks.items():
            gt, gt_out = _gt(version, sn, task)
            base = {"suite": sn, "task_id": tid, "prompt": task.PROMPT, "gt_reads": [c for c in gt if c["function"] in cfg["read"]],
                        "gt_writes": [c for c in gt if c["function"] in cfg["write"]],
                        "write_index": [i for i, c in enumerate(gt) if c["function"] in cfg["write"]], "gt_output": gt_out}
            vectors = _visible_vectors(s, task, gt)
            if benign:
                out[f"{sn}.{tid}+none"] = TaskSpec(key=f"{sn}.{tid}+none", **base, vectors=vectors)
            env = task.init_environment(s.load_and_inject_default_environment({}))
            for iid, it in s.injection_tasks.items():
                icalls = [{"function": c.function, "args": dict(c.args)} for c in it.ground_truth(copy.deepcopy(env))]
                key = f"{sn}.{tid}+{iid}"
                out[key] = TaskSpec(key=key, **base, injection_task=iid, canary=_canary(icalls, task.PROMPT), injection_goal=it.GOAL,
                                    injection_calls=icalls, vectors=vectors)
    return out
