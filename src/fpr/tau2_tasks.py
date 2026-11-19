"""Task annotation for tau2-bench: the Phase 1 critical-fact rule applied to the gold actions."""

import json
from functools import cache

from .env.agentdojo_adapter import tools_cfg
from .env.external_world import ExternalWorld
from .env.state_store import StateStore
from .env.tau2_adapter import (
    INJECTION,
    SUITES,
    Tau2Env,
    _domain,
    customer_id,
    gold_actions,
    task_ids,
)
from .faults.payloads import atoms, perturb_implausible, perturb_plausible, vtype
from .scoring.taint import contains, leaves
from .tasks import (
    SKIP_ARGS,
    Fact,
    TaskSpec,
    _arg_leaves,
    _merge,
    _pool,
    _rng,
    _same,
    _set,
)

SKIP = SKIP_ARGS | {"summary"}


def scratch_env(suite_name: str, task_id: str) -> Tau2Env:
    return Tau2Env("", suite_name, task_id, StateStore(lambda k, d: None), ExternalWorld(lambda k, d: None))


def replay(suite_name: str, task_id: str, calls: list[dict], output: str) -> bool:
    """Utility of a fresh environment after running the given calls."""
    env = scratch_env(suite_name, task_id)
    names = list(env.dom["schemas"])
    for i, c in enumerate(calls):
        env.call(c["function"], c["args"], "gt", f"gt{i}", names)
    return env.utility(output)


def _fields(obj, key=None):
    """(field name, scalar) pairs of a JSON value; list items carry the name of the field that holds the list."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _fields(v, k)
    elif isinstance(obj, list):
        for v in obj:
            yield from _fields(v, key)
    elif obj is not None and not isinstance(obj, bool):
        yield key, obj


def _index(obj) -> tuple[dict, dict]:
    by_field: dict = {}
    by_value: dict = {}
    for k, x in _fields(obj):
        by_field.setdefault(k, set()).add(x)
        by_value.setdefault(x, set()).add(k)
    return by_field, by_value


def _field_pools(reads: tuple[dict, dict], db: tuple[dict, dict], val, arg: str | None) -> list[list]:
    """Candidate pools in order of preference: other values of the same field, nearest source first."""
    v = val[0] if isinstance(val, list) else val
    named = db[0].get(arg, set()) if arg else set()  # values the argument's own field takes in the database

    def others(index, fields):
        return sorted({x for k in fields for x in index[0].get(k, ()) if x != v and type(x) is type(v)}, key=str)

    by_arg = [arg] if v in named else []
    near = [x for x in others(reads, reads[1].get(v, ())) if not by_arg or x in named]
    return [others(reads, by_arg), near, others(db, by_arg), others(db, db[1].get(v, ()))]


@cache
def _db_view(suite_name: str) -> tuple[tuple[dict, dict], str]:
    j = _domain(suite_name)["db"].model_dump(mode="json")
    return _index(j), json.dumps(j)


def gold_output(task: dict) -> str:
    return " ".join(str(x) for x in task["evaluation_criteria"].get("communicate_info") or [])


def annotate(suite_name: str, task_id: str) -> TaskSpec | None:
    cfg = tools_cfg()[suite_name]
    task = _domain(suite_name)["tasks"][task_id]
    gt = gold_actions(task)
    reads = [c for c in gt if c["function"] in cfg["read"]]
    writes = [c for c in gt if c["function"] in cfg["write"]]
    out = gold_output(task)
    if not writes or not replay(suite_name, task_id, gt, out):
        return None
    env = scratch_env(suite_name, task_id)
    prompt = env.prompt
    db_index, db_text = _db_view(suite_name)
    read_texts = [env.call(c["function"], c["args"], "gt", f"r{i}", cfg["read"])[0] for i, c in enumerate(reads)]
    read_json = [_parse(t) for t in read_texts]
    read_index = _index(read_json)
    used = [x for c in writes for x in leaves(c["args"]) if x is not None and not isinstance(x, bool)]

    def free(p) -> bool:
        return not contains(prompt, p) and not any(contains(u, p) and contains(p, u) for u in used)

    def pool_for(val, arg) -> list:
        for cands in (*_field_pools(read_index, db_index, val, arg), _pool(read_json, val)):
            ok = [p for p in cands if free(p)]
            if ok:
                return ok
        return []

    facts: list[Fact] = []
    for ci, call in enumerate(gt):
        if call["function"] not in cfg["write"]:
            continue
        for path, v in _arg_leaves(call["args"]):
            if not path or path[0] in SKIP or v is None or isinstance(v, bool):
                continue
            items = v if isinstance(v, list) else [v]
            if any(isinstance(x, str) and (len(x) > 60 or "\n" in x) for x in items):
                continue
            items = [x for x in items if not contains(prompt, x) and contains(db_text, x)]
            if not items:
                continue
            val = items if isinstance(v, list) else items[0]
            rng = _rng(suite_name, task_id, ci, path)
            pool = pool_for(val, next((p for p in reversed(path) if isinstance(p, str)), None))
            a1p = perturb_plausible(val, rng, pool)
            if a1p == val or a1p in ("", None) or any(_same(f.value, val) for f in facts):
                continue
            bad = _set(call["args"], list(path), _merge(v, val, a1p))
            if replay(suite_name, task_id, gt[:ci] + [{"function": call["function"], "args": bad}] + gt[ci + 1 :], out):
                continue  # not outcome-relevant
            stale_pool = [p for p in pool if p != a1p]
            stale = None
            if stale_pool:
                pick = _rng("stale", suite_name, task_id, ci, path).choice(stale_pool)
                stale = [pick] + list(val[1:]) if isinstance(val, list) else pick
            facts.append(
                Fact(
                    name=f"{call['function']}.{'.'.join(map(str, path))}", value=val, type=vtype(val), call=ci, path=list(path),
                    a1p=a1p, a1i=_implausible(val, rng), stale=stale,
                )
            )
    if not facts:
        return None
    crit_vals = [x for f in facts for x in (f.value if isinstance(f.value, list) else [f.value])]
    seen: list = []
    for t in read_texts:
        for a in atoms(t):
            if a not in seen and not any(contains(a, cv) or contains(cv, a) for cv in crit_vals) and not contains(prompt, a):
                seen.append(a)
    if not seen:
        read_text = " ".join(read_texts)
        for f in facts:
            for p in _pool(read_json, f.value):
                if p not in seen and contains(read_text, p) and not contains(prompt, p) and not any(contains(p, cv) for cv in crit_vals):
                    seen.append(p)
    crit_types = {f.type if f.type != "list" else vtype(f.value[0]) for f in facts}
    seen = sorted(seen, key=lambda a: vtype(a) in crit_types)  # prefer types that cannot fill a critical arg
    nc: list[Fact] = []
    for i, a in enumerate(seen[:3]):
        rng = _rng("nc", suite_name, task_id, i)
        nc_pool = [p for p in _pool(read_json, a) if free(p) and not any(contains(p, cv) for cv in crit_vals)]
        a1p = perturb_plausible(a, rng, nc_pool)
        if a1p != a and free(a1p):
            nc.append(Fact(name=f"nc{i}", value=a, type=vtype(a), a1p=a1p))
    inj = INJECTION[suite_name]
    icall = {"function": inj["call"]["function"], "args": dict(inj["call"]["args"])}
    if suite_name == "tau2_retail":
        icall["args"] = {"user_id": customer_id(suite_name, task_id), **icall["args"]}
    return TaskSpec(
        key=f"{suite_name}.{task_id}", suite=suite_name, task_id=task_id, prompt=prompt, gt_reads=reads, gt_writes=writes,
        write_index=[i for i, c in enumerate(gt) if c["function"] in cfg["write"]], gt_output=out, critical_facts=facts,
        noncritical_facts=nc, injection_task=inj["task"], canary=inj["canary"], injection_goal=inj["goal"],
        injection_calls=[icall] if icall["args"].get("user_id") else [], vectors=["tool_return"],
    )


def _implausible(val, rng):
    out = perturb_implausible(val, rng)
    return 9999 if out == val and vtype(val) == "number" else out  # the Phase 1 rule (x100) leaves 0 unchanged


def _parse(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def annotate_all() -> tuple[list[TaskSpec], dict]:
    """All candidate specs and the selection funnel per domain."""
    specs, funnel = [], {}
    for sn in SUITES:
        cfg = tools_cfg()[sn]
        f = {"tasks": 0, "with_action_calls": 0, "gold_replay_passes": 0, "with_critical_fact": 0, "dropped": {}}
        for tid in task_ids(sn):
            task = _domain(sn)["tasks"][tid]
            gt = gold_actions(task)
            f["tasks"] += 1
            if not any(c["function"] in cfg["write"] for c in gt):
                f["dropped"][tid] = "no_action"
                continue
            f["with_action_calls"] += 1
            if not replay(sn, tid, gt, gold_output(task)):
                f["dropped"][tid] = "gold_fails"
                continue
            f["gold_replay_passes"] += 1
            spec = annotate(sn, tid)
            if spec is None:
                f["dropped"][tid] = "no_critical_fact"
                continue
            f["with_critical_fact"] += 1
            specs.append(spec)
        funnel[sn] = f
    return specs, funnel


if __name__ == "__main__":
    from .env.agentdojo_adapter import ROOT
    from .tasks import save_specs

    specs, funnel = annotate_all()
    save_specs(specs, ROOT / "configs/tau2_candidates.yaml")
    (ROOT / "configs/tau2_funnel.yaml").write_text(__import__("yaml").safe_dump(funnel, sort_keys=False))
    print(json.dumps({k: {x: y for x, y in v.items() if x != "dropped"} for k, v in funnel.items()}))
