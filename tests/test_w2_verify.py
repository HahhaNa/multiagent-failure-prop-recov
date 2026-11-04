import asyncio
import json

import pytest
from conftest import TASKS

from fpr.faults.spec import conditions
from fpr.orchestrator import Episode, EpisodeConfig
from fpr.scoring.taint import contains


def run(spec, mode, fid, mock, verify=True):
    ep = Episode(EpisodeConfig(spec=spec, mode=mode, fault_id=fid, branch_seed=100, verify=verify), mock)
    return ep, asyncio.run(ep.run())


def _verify_events(ep, stage):
    return [e for e in ep.log.events if e["kind"] == "verify" and e["stage"] == stage]


@pytest.mark.parametrize("key", TASKS)
def test_clean_verify_keeps_success(specs, mock, key):
    for mode in ("message", "state"):
        _ep, s = run(specs[key], mode, None, mock)
        assert s["utility"] and not s["security_violation"], mode
        assert s["verifier_calls"] >= 2 and s["verify_stages"]["V1"]["n"] == 1 and s["verify_stages"]["V2"]["n"] == 1
        assert s["n_blocked"] == 0


@pytest.mark.parametrize("key", TASKS)
def test_value_faults_fixed_by_v1(specs, mock, key):
    for mode in ("message", "state"):
        for fid in ("A1p_R", "A1i_R", "A2_R"):
            ep, s = run(specs[key], mode, fid, mock)
            if not s["injected"]:
                continue
            assert _verify_events(ep, "V1")[0]["changed"], (fid, mode)
            bad = ep.inj.taint_values()
            assert not contains(ep.artifacts["findings_verified"], bad), (fid, mode)
            assert not contains([c["args"] for c in s["exec_calls"]], bad), (fid, mode)
            assert s["utility"], (fid, mode)
            ep0, s0 = run(specs[key], mode, fid, mock, verify=False)
            assert not s0["utility"] or not ep0.inj.applied, (fid, mode)


@pytest.mark.parametrize("key", TASKS)
def test_adversarial_caught(specs, mock, key):
    spec = specs[key]
    for mode in ("message", "state"):
        ep, s = run(spec, mode, "B2_R", mock)
        assert s["injected"] and spec.canary not in json.dumps(ep.artifacts["findings_verified"])
        assert not s["security_violation"] and s["utility"]
        ep, s = run(spec, mode, "B2_P", mock)
        assert s["injected"] and spec.canary not in json.dumps(ep.artifacts["plan_verified"]) and not s["security_violation"]
        ep, s = run(spec, mode, "B1_Et", mock)
        assert s["injected"] and s["n_blocked"] >= 1, mode
        assert not s["security_violation"] and not contains([c["args"] for c in s["exec_calls"]], spec.canary)
        blocked = [e for e in ep.log.events if e["kind"] == "tool_call" and e["error"] == "blocked_by_verifier"]
        assert blocked and blocked[0]["result"].startswith("Blocked by verifier:")
    ep, s = run(spec, "state", "B3_S", mock)
    assert s["injected"] and "notes" not in ep.artifacts["plan_verified"]
    reads = [e for e in ep.log.events if e["kind"] == "state_read" and e["obj"] == "bb:/shared/plan.json" and e["reader"] == "executor"]
    writers = {v.vid: v.writer for v in ep.store.versions["bb:/shared/plan.json"]}
    assert reads and all(writers[e["version"]] == "verifier" for e in reads)
    assert not s["security_violation"]


@pytest.mark.parametrize("key", TASKS)
def test_verifier_never_sees_fault_metadata(specs, mock, key):
    ids = [c.id for c in conditions(include_controls=True)]
    for mode in ("message", "state"):
        for c in conditions(include_controls=True):
            if mode not in c.modes:
                continue
            ep, s = run(specs[key], mode, c.id, mock)
            assert not s["stalled"], (c.id, mode)
            for conv in ep.ctx.transcripts.get("verifier", []):
                text = json.dumps(conv[:-1])
                assert not any(f'"{i}"' in text or f" {i} " in text for i in ids), (c.id, mode)
                assert "fault" not in text.lower() and "applied" not in text.lower(), (c.id, mode)


@pytest.mark.parametrize("key", TASKS)
def test_plan_value_faults_fixed_by_v2(specs, mock, key):
    n = 0
    for mode, fid in (("message", "A1p_P"), ("state", "A1p_P"), ("state", "A1p_S")):
        ep, s = run(specs[key], mode, fid, mock)
        if not s["injected"]:
            continue
        n += 1
        assert _verify_events(ep, "V2")[0]["changed"], (fid, mode)
        assert not contains([c["args"] for c in s["exec_calls"]], ep.inj.taint_values()), (fid, mode)
        assert s["utility"], (fid, mode)
    assert n >= 1


def test_verify_runner_prefix_replay(specs, mock, tmp_path):
    from fpr.llm.cache import CachedLLM, DiskCache
    from fpr.runner import Runner, expand, load_cfg

    conds = [c for c in conditions(include_controls=True) if c.id in ("A1p_R", "A1p_P", "B3_S", "B1_Et", "PC")]
    runs = expand("v", TASKS, ["message", "state"], [0], conds, ["R.out", "P.out", "E.tool"], [100])
    llm = CachedLLM(mock, DiskCache(tmp_path / "cache"), "record")
    r = Runner(llm, load_cfg(), specs, "mock", "mock", 16, tmp_path, verify=True)
    asyncio.run(r.run_all(runs))
    assert r.stats["failed"] == 0 and r.stats["done"] == len(runs)
    for p in (tmp_path / "v").glob("*/*/*/summary.json"):
        s = json.loads(p.read_text())
        assert s["verify"] and s["prefix_misses"] == 0, p
        if s["fault"] == "PC":
            assert not s["utility"] or s["n_blocked"] >= 1, p
