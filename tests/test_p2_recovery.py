"""Phase 2 acceptance tests on MockLLM (docs/phase2_design.md §10)."""

import asyncio
import json
import math
import random

import pytest
from conftest import TASKS

from fpr.env.agentdojo_adapter import TaskEnv
from fpr.env.external_world import ExternalWorld
from fpr.env.snapshots import dump
from fpr.env.state_store import StateStore
from fpr.env.world_actor import refund
from fpr.faults.spec import conditions
from fpr.orchestrator import Episode, EpisodeConfig
from fpr.recovery.episode import Recovery, RecoveryConfig
from fpr.recovery.faulted import FaultedRun, sig
from fpr.recovery.gt import gt_counts, label
from fpr.recovery.probes import ProbeBuilder
from fpr.recovery.sprt import SPRT, SPRTParams


def _run(specs, mock, d, key, mode, fault, seed=100):
    d.mkdir(parents=True, exist_ok=True)
    s = asyncio.run(Episode(EpisodeConfig(spec=specs[key], mode=mode, fault_id=fault, branch_seed=seed), mock, d).run())
    (d / "DONE").write_text("x")
    return s


def _recover(specs, mock, F, key, out=None, **kw):
    return asyncio.run(Recovery(F, specs[key], RecoveryConfig(**kw), mock, out).run())


def _norm(x):
    return json.loads(json.dumps(x, sort_keys=True, default=str))


# 1. "none" reproduces R0's final state; "all" (R2) equals a clean continuation when every effect is reversible
@pytest.mark.parametrize("mode", ["message", "state"])
def test_none_reproduces_r0(specs, mock, tmp_path, mode):
    key = "slack.user_task_11"
    _run(specs, mock, tmp_path / "F", key, mode, "A1p_P")
    F = FaultedRun.load(tmp_path / "F")
    (tmp_path / "R").mkdir()
    s = _recover(specs, mock, F, key, tmp_path / "R", method="none")
    assert s["recovery_calls"] == 0 and not s["ran_executor"]
    final = json.loads((tmp_path / "R" / "final_env.json").read_text())
    assert _norm({f"env:{k}": v for k, v in final.items()}) == _norm(F.final_env_dump())


@pytest.mark.parametrize("mode", ["message", "state"])
def test_all_equals_rollback(specs, mock, tmp_path, mode):
    key = "workspace.user_task_9"  # one reversible write
    _run(specs, mock, tmp_path / "F", key, mode, "A1p_P")
    _run(specs, mock, tmp_path / "C", key, mode, "CLEAN_Pout")
    F = FaultedRun.load(tmp_path / "F")
    (tmp_path / "R").mkdir()
    s = _recover(specs, mock, F, key, tmp_path / "R", method="all")
    assert s["utility"] and s["n_invalidated"] == len(F.calls()) and s["restored"]
    final = json.loads((tmp_path / "R" / "final_env.json").read_text())
    assert _norm({f"env:{k}": v for k, v in final.items()}) == _norm(FaultedRun.load(tmp_path / "C").final_env_dump())


# 2. no LLM call before the branch point; recovery tokens are exactly the recovery's own calls
def test_recovery_tokens_post_branch(specs, mock, tmp_path):
    key = TASKS[0]
    _run(specs, mock, tmp_path / "F", key, "message", "A3_R")
    F = FaultedRun.load(tmp_path / "F")
    (tmp_path / "R").mkdir()
    s = _recover(specs, mock, F, key, tmp_path / "R", method="sprt")
    ev = [json.loads(x) for x in (tmp_path / "R" / "events.jsonl").read_text().splitlines()]
    llm = [e for e in ev if e["kind"] == "llm_call"]
    assert llm and s["branched_only"] and all(e["branched"] for e in llm)
    assert s["recovery_tokens"] == sum(e["prompt_tokens"] + e["completion_tokens"] for e in llm)
    assert s["recovery_calls"] == len(llm) and s["n_probes"] == sum(1 for e in llm if e["agent"].startswith("probe_"))


# 3. per-object restore equals a run that never made the invalidated writes
def test_per_object_restore(specs, mock, tmp_path):
    key = "slack.user_task_11"  # invite + two add_user_to_channel
    _run(specs, mock, tmp_path / "F", key, "message", "CLEAN_Etool")
    F = FaultedRun.load(tmp_path / "F")
    calls = [a for a in F.calls() if a.ok]
    assert len(calls) == 2 and all(a.rev == "reversible" for a in calls)
    drop = calls[-1]
    gt = {"n": 3, "steps": {}, "calls": {a.sig: 3 for a in calls if a is not drop}}
    s = _recover(specs, mock, F, key, method="oracle", gt=gt)
    assert s["restored"] == [drop.call_id]
    R = Recovery(F, specs[key], RecoveryConfig(method="oracle", gt=gt), mock)
    env, _ = R._build([e for e in F.post_calls() if e["call_id"] != drop.call_id], [], log=False)
    ref = TaskEnv("v1.2.2", specs[key].suite, specs[key].task_id, StateStore(lambda k, d: d), ExternalWorld(lambda k, d: d))
    for e in [e for e in F.env_calls() if e["call_id"] != drop.call_id]:
        ref.runtime.run_function(ref.env, e["name"], dict(e["args"]))
    assert _norm(dump(env.env)) == _norm(dump(ref.env))


# 4. compensation succeeds for a known IBAN and fails for the canary
def test_compensation(specs):
    spec = specs["banking.user_task_0"]
    env = TaskEnv("v1.2.2", "banking", spec.task_id, StateStore(lambda k, d: d), ExternalWorld(lambda k, d: d))
    known = next(t.recipient for t in env.pre_env.bank_account.transactions if t.recipient != env.env.bank_account.iban)
    n = len(env.env.bank_account.transactions)
    assert refund(env, {"recipient": known, "amount": 10.0, "subject": "x", "date": "2022-01-01"}, "c") is None
    assert len(env.env.bank_account.transactions) == n + 1 and env.env.bank_account.transactions[-1].sender == known
    assert refund(env, {"recipient": spec.canary, "amount": 10.0, "subject": "x", "date": "2022-01-01"}, "c") is not None
    assert len(env.env.bank_account.transactions) == n + 1


# 5. zero duplicate external effects on every mock branch (R4p, oracle)
def test_no_duplicates_on_mock_branches(specs, mock, tmp_path):
    root = tmp_path / "runs"
    for key in TASKS:
        for mode in ("message", "state"):
            for p in ("R.tool", "R.out", "P.out", "E.tool"):
                for b in (300, 301, 302):
                    _run(specs, mock, root / "gt" / key / mode / f"CLEAN_{p.replace('.', '')}_b{b}", key, mode, f"CLEAN_{p.replace('.', '')}", b)
            for b in (300, 301, 302):
                d = root / "gt" / key / mode / f"ref_s{b}"
                d.mkdir(parents=True)
                asyncio.run(Episode(EpisodeConfig(spec=specs[key], mode=mode, ref_seed=b, branch_seed=b), mock, d).run())
                (d / "DONE").write_text("x")
    n = 0
    for key in TASKS:
        for mode in ("message", "state"):
            for c in conditions():
                if mode not in c.modes:
                    continue
                d = root / "f" / key / mode / c.id
                if not _run(specs, mock, d, key, mode, c.id).get("injected"):
                    continue
                F = FaultedRun.load(d)
                gt = gt_counts(root, "gt", key, mode, F.point, range(300, 303))
                for m in ("sprt", "oracle"):
                    s = _recover(specs, mock, F, key, method=m, gt=gt)
                    assert s["duplicates"] == 0, (key, mode, c.id, m, s["duplicate_effects"])
                    n += 1
    assert n > 100


# 6. a probe rebuilt from transcripts with no correction equals the original request byte for byte
@pytest.mark.parametrize("mode", ["message", "state"])
@pytest.mark.parametrize("fault", ["A1p_R", "B1_Et"])
def test_probe_request_identity(specs, mock, tmp_path, mode, fault):
    key = TASKS[0]
    _run(specs, mock, tmp_path / "F", key, mode, fault)
    F = FaultedRun.load(tmp_path / "F")
    R = Recovery(F, specs[key], RecoveryConfig(method="sprt"), mock)
    env, _ = R._build(F.post_calls(), [], log=False)
    pb = ProbeBuilder(F, env, "mock", 0.3, 1024)
    keys = {e["key"] for e in F.events if e["kind"] == "llm_call"}
    pr = pb.planner_request(None)
    assert pr is not None and pr.key() in keys
    calls = F.calls()
    assert calls
    for a in calls:
        req = pb.executor_request(a.call_id, None, None)
        assert req is not None and req.key() in keys
    if fault == "A1p_R":  # a substitution changes the request
        assert pb.planner_request(F.findings_uninjected()).key() != pr.key()


# 7. SPRT matches a reference implementation; empirical error rates within alpha, beta + 0.01
def _reference(stream, tau0=0.05, tau1=0.30, alpha=0.05, beta=0.20, n_max=8):
    up, low = math.log((1 - beta) / alpha), math.log(beta / (1 - alpha))
    s = 0.0
    for n, x in enumerate(stream, 1):
        s += math.log(tau1 / tau0) if x else math.log((1 - tau1) / (1 - tau0))
        if s >= up - 1e-12:
            return "reuse", n
        if s <= low + 1e-12:
            return "invalidate", n
        if n >= n_max:
            return "invalidate", n
    return None, len(stream)


def _sprt(stream, p):
    t = SPRT(p)
    for x in stream:
        if t.update(x) is not None:
            break
    return t.decision, t.n


def test_sprt_reference_and_error_rates():
    rng = random.Random(0)
    p8 = SPRTParams()
    for _ in range(2000):
        q = rng.random()
        stream = [rng.random() < q for _ in range(8)]
        assert _sprt(stream, p8) == _reference(stream)
    big = SPRTParams(n_max=10_000)
    for q, err, bound, params in ((0.05, "reuse", 0.05, big), (0.30, "invalidate", 0.20, big), (0.05, "reuse", 0.05, p8)):
        n_err = 0
        for _ in range(100_000):
            t = SPRT(params)
            while t.open:
                t.update(rng.random() < q)
            n_err += t.decision == err
        assert n_err / 100_000 <= bound + 0.01, (q, err, n_err)


# 8. budget: B = 4 uses at most 4 probe calls; B = inf tests every candidate
def test_budget(specs, mock, tmp_path):
    key = "slack.user_task_11"
    _run(specs, mock, tmp_path / "F", key, "message", "A4_R")
    F = FaultedRun.load(tmp_path / "F")
    s4 = _recover(specs, mock, F, key, method="sprt", budget=4)
    assert s4["n_probes"] <= 4
    si = _recover(specs, mock, F, key, method="sprt")
    cand = [d for d in si["decisions"] if not d["flagged"]]
    assert cand and all(d["n"] >= 1 for d in cand) and si["unprobed_reuse"] == 0
    assert s4["unprobed_reuse"] > 0 or s4["n_probes"] == si["n_probes"]


def test_gt_label_and_sig():
    assert label(0, 30) == "dependent" and label(1, 30) == "dependent" and label(5, 30) == "indifference" and label(9, 30) == "reusable"
    assert sig("f", {"a": "X ", "step_id": "s1"}) == sig("f", {"a": "x"})


# 9. matrix counts and resume (skip DONE), as in M4
def test_p2_matrix_counts_and_resume(specs, mock, tmp_path):
    from fpr.llm.cache import CachedLLM, DiskCache
    from fpr.runner import Runner, expand, gt_matrix, load_cfg, p2_matrix, p2cal_matrix

    g = gt_matrix([f"t.{i}" for i in range(14)], ["message", "state"], "gt")
    assert sum(1 for r in g if r.fault is None) == 14 * 2 * 30 and sum(1 for r in g if r.fault) == 14 * 2 * 4 * 30
    assert {r.ref_seed for r in g if r.fault} == {0} and {r.branch_seed for r in g if r.fault} == set(range(300, 330))
    root = tmp_path
    llm = CachedLLM(mock, DiskCache(root / "cache"), "record")
    run = Runner(llm, load_cfg(), specs, "mock", "mock", 8, root)
    src = expand("src", TASKS[:1], ["message"], [0], [c for c in conditions() if c.id in ("A1p_R", "B2_P", "NC_R")], ["R.out"], [100, 101])
    asyncio.run(run.run_all(src))
    runs = p2_matrix(root, "src", "p2_x", "sprt", "budget=4")
    from fpr.runner import summaries

    n_inj = sum(1 for x in summaries(root, "src") if x.get("injected") and not x["fault"].startswith("CLEAN_"))
    assert len(runs) == n_inj >= 4 and all(r.recover_from and r.method == "sprt" for r in runs)
    asyncio.run(run.run_all(runs))
    assert run.stats["failed"] == 0 and all((r.path(root) / "DONE").exists() for r in runs)
    m = json.loads((runs[0].path(root) / "manifest.json").read_text())
    assert m["recovery"]["budget"] == "4.0" and m["seed"] in (200, 201)
    done = run.stats["done"]
    asyncio.run(run.run_all(runs))
    assert run.stats["done"] == done and run.stats["skipped"] >= len(runs)
    cal = p2cal_matrix(root, "src", "p2cal")
    assert len(cal) == 2 and all("decide_only=1" in r.params for r in cal)
    asyncio.run(run.run_all(cal))
    assert run.stats["failed"] == 0
    s = json.loads((cal[0].path(root) / "summary.json").read_text())
    assert s["decide_only"] and s["n_invalidated"] == 0  # deterministic mock: every clean artifact is reproduced
