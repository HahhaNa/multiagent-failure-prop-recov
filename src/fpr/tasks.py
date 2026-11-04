"""Task annotation: critical facts, noncritical facts and stale values, derived from AgentDojo ground truth."""

import copy
import json
import random
import zlib
from pathlib import Path
from typing import Any

import yaml
from agentdojo.agent_pipeline.tool_execution import tool_result_to_str
from agentdojo.functions_runtime import FunctionCall, FunctionsRuntime
from pydantic import BaseModel, Field

from .env.agentdojo_adapter import ROOT, suite, tools_cfg
from .faults.payloads import atoms, perturb_implausible, perturb_plausible, vtype
from .scoring.taint import contains, leaves

SKIP_ARGS = {"subject", "body", "description", "content", "title", "date", "location", "permission", "recurring"}


class Fact(BaseModel):
    name: str
    value: Any
    type: str
    call: int = -1
    path: list = Field(default_factory=list)
    a1p: Any = None
    a1i: Any = None
    stale: Any = None  # pre_task_update: earlier version of the fact


class TaskSpec(BaseModel):
    key: str
    suite: str
    task_id: str
    prompt: str
    gt_reads: list[dict]
    gt_writes: list[dict]
    write_index: list[int] = Field(default_factory=list)
    gt_output: str = ""
    critical_facts: list[Fact] = Field(default_factory=list)
    noncritical_facts: list[Fact] = Field(default_factory=list)
    injection_task: str = ""
    canary: str = ""
    injection_goal: str = ""
    injection_calls: list[dict] = Field(default_factory=list)
    vectors: list[str] = Field(default_factory=list)


def faults_cfg() -> dict:
    return yaml.safe_load((ROOT / "configs/faults.yaml").read_text())


def _rng(*parts) -> random.Random:
    return random.Random(zlib.crc32("|".join(map(str, parts)).encode()))


def _get(args: dict, path: list):
    x = args
    for p in path:
        x = x[p]
    return x


def _set(args: dict, path: list, v) -> dict:
    a = copy.deepcopy(args)
    x = a
    for p in path[:-1]:
        x = x[p]
    x[path[-1]] = v
    return a


def _arg_leaves(obj, path=()):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _arg_leaves(v, path + (k,))
    elif isinstance(obj, list) and obj and all(isinstance(x, str) for x in obj):
        yield path, obj
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _arg_leaves(v, path + (i,))
    else:
        yield path, obj


def _shape(s: str) -> tuple:
    return (" " in s, s[:1].isupper(), any(c.isdigit() for c in s), "_" in s, "@" in s, "." in s)


def _pool(env_json: Any, v) -> list:
    if isinstance(v, list):
        v = v[0]
    t = vtype(v)
    out = set()
    for leaf in leaves(env_json):
        if isinstance(leaf, bool) or leaf is None:
            continue
        if t == "number":
            if isinstance(leaf, (int, float)) and leaf != v and leaf > 0:
                out.add(leaf)
            continue
        s = str(leaf)
        cands = atoms(s) if len(s) > 40 else [s]
        for c in cands:
            if c and c != v and vtype(c) == t and (t != "text" or (len(c) <= 30 and _shape(c) == _shape(str(v)))):
                out.add(c)
    return sorted(out, key=str)


def _utility(s, task, calls: list[dict], output: str) -> bool:
    env = task.init_environment(s.load_and_inject_default_environment({}))
    pre = env.model_copy(deep=True)
    rt = FunctionsRuntime(s.tools)
    trace = []
    for c in calls:
        rt.run_function(env, c["function"], copy.deepcopy(c["args"]))
        trace.append(FunctionCall(function=c["function"], args=copy.deepcopy(c["args"])))
    r = task.utility_from_traces(output, pre, env, trace)
    return bool(task.utility(output, pre, env) if r is None else r)


def annotate(version: str, suite_name: str, task_id: str) -> TaskSpec | None:
    s = suite(version, suite_name)
    task = s.get_user_task_by_id(task_id)
    cfg = tools_cfg()[suite_name]
    env = task.init_environment(s.load_and_inject_default_environment({}))
    env_json = env.model_dump(mode="json")
    env_text = json.dumps(env_json)
    gt = [{"function": c.function, "args": dict(c.args)} for c in task.ground_truth(env.model_copy(deep=True))]
    reads = [c for c in gt if c["function"] in cfg["read"]]
    writes = [c for c in gt if c["function"] in cfg["write"]]
    out = task.GROUND_TRUTH_OUTPUT or ""
    if not writes or not _utility(s, task, gt, out):
        return None
    prompt = task.PROMPT
    facts: list[Fact] = []
    for ci, call in enumerate(gt):
        if call["function"] not in cfg["write"]:
            continue
        for path, v in _arg_leaves(call["args"]):
            if not path or path[0] in SKIP_ARGS or v is None or isinstance(v, bool):
                continue
            items = v if isinstance(v, list) else [v]
            if any(isinstance(x, str) and (len(x) > 60 or "\n" in x or x.startswith("<") or x == "...") for x in items):
                continue
            items = [x for x in items if not contains(prompt, x) and contains(env_text, x)]
            if not items:
                continue
            val = items if isinstance(v, list) else items[0]
            rng = _rng(suite_name, task_id, ci, path)
            pool = _pool(env_json, val)
            a1p = perturb_plausible(val, rng, pool)
            if a1p == val or a1p in ("", None) or any(_same(f.value, val) for f in facts):
                continue
            bad = _set(call["args"], list(path), _merge(v, val, a1p))
            if _utility(s, task, gt[:ci] + [{"function": call["function"], "args": bad}] + gt[ci + 1 :], out):
                continue  # not outcome-relevant
            stale_pool = [p for p in pool if p != a1p and not contains(prompt, p)]
            stale = None
            if stale_pool:
                pick = _rng("stale", suite_name, task_id, ci, path).choice(stale_pool)
                stale = [pick] + list(val[1:]) if isinstance(val, list) else pick
            facts.append(
                Fact(
                    name=f"{call['function']}.{'.'.join(map(str, path))}",
                    value=val,
                    type=vtype(val),
                    call=ci,
                    path=list(path),
                    a1p=a1p,
                    a1i=perturb_implausible(val, rng),
                    stale=stale,
                )
            )
    if not facts:
        return None
    crit_vals = [x for f in facts for x in (f.value if isinstance(f.value, list) else [f.value])]
    nc: list[Fact] = []
    rt = FunctionsRuntime(s.tools)
    e2 = env.model_copy(deep=True)
    seen: list = []
    read_texts: list[str] = []
    for c in reads:
        r, _ = rt.run_function(e2, c["function"], copy.deepcopy(c["args"]))
        read_texts.append(tool_result_to_str(r))
        for a in atoms(read_texts[-1]):
            if a not in seen and not any(contains(a, cv) or contains(cv, a) for cv in crit_vals) and not contains(prompt, a):
                seen.append(a)
    if not seen:
        read_text = " ".join(read_texts)
        for f in facts:
            for p in _pool(env_json, f.value):
                if p not in seen and contains(read_text, p) and not contains(prompt, p) and not any(contains(p, cv) for cv in crit_vals):
                    seen.append(p)
    for i, a in enumerate(seen[:3]):
        rng = _rng("nc", suite_name, task_id, i)
        nc.append(Fact(name=f"nc{i}", value=a, type=vtype(a), a1p=perturb_plausible(a, rng, _pool(env_json, a))))
    fc = faults_cfg()
    it_id = fc["injection_tasks"][suite_name]
    it = s.get_injection_task_by_id(it_id)
    icalls = [{"function": c.function, "args": dict(c.args)} for c in it.ground_truth(env.model_copy(deep=True))]
    return TaskSpec(
        key=f"{suite_name}.{task_id}",
        suite=suite_name,
        task_id=task_id,
        prompt=prompt,
        gt_reads=reads,
        gt_writes=writes,
        write_index=[i for i, c in enumerate(gt) if c["function"] in cfg["write"]],
        gt_output=out,
        critical_facts=facts,
        noncritical_facts=nc,
        injection_task=it_id,
        canary=fc["canaries"][suite_name],
        injection_goal=it.GOAL,
        injection_calls=icalls,
        vectors=_visible_vectors(s, task, gt),
    )


def _merge(orig, researched, perturbed):
    if isinstance(orig, list):
        m = dict(zip(researched, perturbed))
        return [m.get(x, x) for x in orig]
    return perturbed


def _visible_vectors(s, task, gt: list[dict]) -> list[str]:
    """Injection vectors whose content appears in ground-truth read outputs."""
    defaults = s.get_injection_vector_defaults()
    marks = {k: f"ZZVEC{i}ZZ" for i, k in enumerate(defaults)}
    env = task.init_environment(s.load_and_inject_default_environment(marks))
    rt = FunctionsRuntime(s.tools)
    seen = set()
    for c in gt:
        r, _ = rt.run_function(env, c["function"], copy.deepcopy(c["args"]))
        text = tool_result_to_str(r)
        seen |= {k for k, m in marks.items() if m in text}
    return sorted(seen)


def annotate_all(version: str, suites: list[str]) -> list[TaskSpec]:
    out = []
    for sn in suites:
        for tid in suite(version, sn).user_tasks:
            spec = annotate(version, sn, tid)
            if spec is not None:
                out.append(spec)
    return out


def save_specs(specs: list[TaskSpec], path: Path) -> None:
    path.write_text(yaml.safe_dump([s.model_dump() for s in specs], sort_keys=False, allow_unicode=True, width=120))


def load_specs(path: Path) -> dict[str, TaskSpec]:
    return {d["key"]: TaskSpec.model_validate(d) for d in yaml.safe_load(Path(path).read_text())}


def _same(a, b) -> bool:
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
