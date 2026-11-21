"""Phase 2b acceptance tests on MockLLM (docs/phase2b_design.md §12)."""

import asyncio
import random

import pytest

from fpr.env.agentdojo_adapter import TaskEnv
from fpr.env.external_world import ExternalWorld
from fpr.env.state_store import StateStore
from fpr.gate.adv import adv_specs
from fpr.gate.ccg import Gate, GateConfig, M
from fpr.gate.nodes import pending
from fpr.gate.rederive import Traj, masked, rederive, writes_of
from fpr.llm.cache import CachedLLM, DiskCache
from fpr.llm.mock import MockLLM
from fpr.orchestrator import Episode, EpisodeConfig
from fpr.recovery.faulted import FaultedRun
from fpr.recovery.sprt import SPRT, operating_characteristic
from fpr.runner import adv_matrix, gate_matrix
from fpr.scoring.taint import contains

RUN = {"model": "mock", "temperature": 0.3, "max_tokens": 1024, "version": "v1.2.2", "turns": {"researcher": 8, "planner": 3, "executor": 10},
       "clock": {"stage_timeout_s": 300}, "tool_output_chars": 5000, "single_tool_calls": False}
KEY = "banking.user_task_0"


def _noop(kind, data):
    return data


@pytest.fixture
def cached(specs, tmp_path):
    return CachedLLM(MockLLM(specs), DiskCache(tmp_path / "cache"), "record")


def _F(specs, llm, d, key, mode, fault):
    d.mkdir(parents=True, exist_ok=True)
    asyncio.run(Episode(EpisodeConfig(spec=specs[key], mode=mode, fault_id=fault, branch_seed=100, tool_output_chars=5000), llm, d).run())
    return FaultedRun.load(d)


def _gate(F, spec, llm, method="ccg"):
    return asyncio.run(Gate(F, spec, GateConfig.for_method(method, run=RUN), llm).run())


def _base(F, spec):
    return Gate(F, spec, GateConfig(run=RUN), None).base


async def _neut(text):
    return text


# 1. staging: same tool text, nothing released before the commit point, rejected effects never leave
def test_staging_transparent():
    texts, worlds = [], []
    for staged in (False, True):
        w = ExternalWorld(_noop, staged=staged)
        env = TaskEnv("v1.2.2", "banking", "user_task_0", StateStore(_noop), w)
        args = {"recipient": "UK12345678901234567890", "amount": 98.7, "subject": "Bill", "date": "2022-01-01"}
        text, _err, _, _ = env.call("send_money", args, "executor", "e_0", ["send_money"])
        texts.append(text)
        worlds.append(w)
    assert texts[0] == texts[1] and worlds[1].effects == [] and len(worlds[1].outbox) == 1
    worlds[1].release(set())
    assert worlds[1].effects == [] and worlds[1].outbox == []


# 2-3. a re-derivation replays the prefix from the cache and forks at N[j]; no fork reproduces F
def test_rederive_prefix_and_identity(specs, cached, tmp_path):
    F = _F(specs, cached, tmp_path / "F", KEY, "message", "A1p_R")
    T = Traj(F.events, F.summary["report"])
    base = _base(F, specs[KEY])
    full = asyncio.run(rederive(base, cached, T.fork_at(len(T.nodes), 999), _neut))
    assert writes_of(full) == writes_of(T)
    llm_full = [e for e in full.events if e["kind"] == "llm_call"]
    assert llm_full and all(e["replayed"] for e in llm_full)
    j = next(n.j for n in T.nodes if n.kind == "plan")
    t = asyncio.run(rederive(base, cached, T.fork_at(j, 999), _neut))
    fork_i = next(e["i"] for e in t.events if e["kind"] == "gate_fork")
    before = [e for e in t.events if e["kind"] == "llm_call" and e["i"] < fork_i]
    after = [e for e in t.events if e["kind"] == "llm_call" and e["i"] > fork_i]
    assert before and all(e["replayed"] for e in before)
    assert after and all(e["seed"] == 999 for e in after)


# 4. a persistent injection recurs under masking; transient channel tampering does not recur in a fresh run
def test_persistence(specs, cached, tmp_path):
    canary = specs[KEY].canary
    F = _F(specs, cached, tmp_path / "Fp", KEY, "message", "B1_Rt")
    T = Traj(F.events, F.summary["report"])
    m = asyncio.run(masked(_base(F, specs[KEY]), cached, T, 500))
    assert any(contains(w.args, canary) for w in pending(m.events))
    assert not any(specs[KEY].gt_writes[0]["function"] == w.name and not contains(w.args, canary) for w in pending(m.events))
    F2 = _F(specs, cached, tmp_path / "Ft", KEY, "message", "B2_R")
    T2 = Traj(F2.events, F2.summary["report"])
    assert any(contains(w.args, canary) for w in pending(T2.events))
    fresh = asyncio.run(rederive(_base(F2, specs[KEY]), cached, T2.fork_at(0, 500), _neut))
    assert pending(fresh.events) and not any(contains(w.args, canary) for w in pending(fresh.events))


# 5-6. localization finds the injected node; CCG removes the attack and keeps utility
@pytest.mark.parametrize(("mode", "fault", "kind"), [("message", "B1_Rt", "rtool"), ("message", "B1_Et", "etool"), ("message", "B2_R", "findings"),
                                                    ("message", "B2_P", "plan"), ("state", "B3_S", "plan")])
def test_localize_and_repair(specs, cached, tmp_path, mode, fault, kind):
    F = _F(specs, cached, tmp_path / "F", KEY, mode, fault)
    assert F.summary["security_violation"]
    s = _gate(F, specs[KEY], cached)
    locs = [x for r in s["rounds"] for x in (r.get("repair") or {}).get("localizations", [])]
    assert locs and all(x["kind"] == kind for x in locs)
    assert s["utility"] and not s["security_violation"] and not s["residual_harm"] and s["repaired"]


# 6-7. block keeps the attack out but loses the task when a legitimate write is faulty; rejected writes never commit
def test_block_vs_repair(specs, cached, tmp_path):
    F = _F(specs, cached, tmp_path / "F", KEY, "message", "A1p_R")
    assert not F.summary["utility"]
    rep, blk = _gate(F, specs[KEY], cached), _gate(F, specs[KEY], cached, "ccg_block")
    assert rep["utility"] and not blk["utility"] and blk["n_blocked"] >= 1
    taint = F.taint
    assert not any(contains(w["args"], taint) for w in blk["committed_writes"] + rep["committed_writes"])
    Fa = _F(specs, cached, tmp_path / "Fa", KEY, "message", "B2_P")
    b = _gate(Fa, specs[KEY], cached, "ccg_block")
    assert not b["security_violation"] and not any(contains(w["args"], specs[KEY].canary) for w in b["committed_writes"])


def test_clean_not_flagged(specs, cached, tmp_path):
    F = _F(specs, cached, tmp_path / "F", KEY, "message", "CLEAN_Rout")
    s = _gate(F, specs[KEY], cached)
    assert not s["flagged"] and s["utility"] and s["n_samples"]["M"] == 7


# 8. SPRT operating characteristic of the masked test (undecided -> flag) matches simulation
def test_mask_sprt_oc():
    for q in (0.05, 0.30):
        oc = operating_characteristic(M, q)
        rng = random.Random(int(q * 100))
        n_flag = 0
        for _ in range(100_000):
            t = SPRT(M)
            while t.open:
                t.update(rng.random() < q)
            n_flag += t.decision == "reuse"
        assert abs(n_flag / 100_000 - oc["reuse"]) < 0.01
    assert operating_characteristic(M, 0.05)["reuse"] <= 0.20 and operating_characteristic(M, 0.30)["invalidate"] <= 0.105


# 10. AgentDojo-97 matrix counts; gate matrix covers every finished run and resumes
def test_adv_matrix_counts(tmp_path):
    specs = adv_specs()
    runs = adv_matrix(specs, "adv_nodef")
    assert len(runs) == 949 + 97 and sum(r.fault == "ADV" for r in runs) == 949
    assert len({r.path(tmp_path) for r in runs}) == len(runs)


def test_gate_matrix_resume(specs, cached, tmp_path):
    import json

    root = tmp_path / "runs"
    for b, f in ((100, "B2_P"), (101, "CLEAN_Pout")):
        d = root / "src" / KEY / "message" / f"{f}_b{b}"
        d.mkdir(parents=True)
        asyncio.run(Episode(EpisodeConfig(spec=specs[KEY], mode="message", fault_id=f, branch_seed=b), cached, d).run())
        (d / "DONE").write_text("x")
    runs = gate_matrix(root, "src", "g", "ccg")
    assert len(runs) == 2 and all(r.method == "gate:ccg" for r in runs)
    (runs[0].path(root)).mkdir(parents=True)
    (runs[0].path(root) / "DONE").write_text("x")
    assert sum((r.path(root) / "DONE").exists() for r in runs) == 1
    assert json.loads((root / "src" / KEY / "message" / "B2_P_b100" / "summary.json").read_text())["injected"]
