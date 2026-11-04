import asyncio

import pytest
from conftest import TASKS

from fpr.faults.spec import conditions
from fpr.orchestrator import Episode, EpisodeConfig

# the agent whose LLM calls must all come before the injection (prefix) / at least one after it
PREFIX = {"R.start": None, "R.tool": "researcher", "R.out": "researcher", "P.out": "planner", "S.write": "planner", "E.tool": "executor"}


def run(spec, mode, fid, mock):
    ep = Episode(EpisodeConfig(spec=spec, mode=mode, fault_id=fid, branch_seed=100), mock)
    s = asyncio.run(ep.run())
    return ep, s


@pytest.mark.parametrize("key", TASKS)
def test_each_condition_injects_at_point(specs, mock, key):
    for mode in ("message", "state"):
        for c in conditions():
            if mode not in c.modes:
                continue
            ep, s = run(specs[key], mode, c.id, mock)
            fev = [e for e in ep.log.events if e["kind"] == "fault"]
            assert len(fev) == 1 and fev[0]["point"] == c.point, (c.id, mode)
            fi = fev[0]["i"]
            kinds_before = ep.log.events[:fi]
            llm_before = [e["agent"] for e in kinds_before if e["kind"] == "llm_call"]
            if c.point == "R.start":
                assert not llm_before
            elif c.point in ("R.out", "R.tool"):
                assert set(llm_before) == {"researcher"}
            elif c.point in ("P.out", "S.write"):
                assert "planner" in llm_before and "executor" not in llm_before
            elif c.point == "E.tool":
                assert llm_before[-1] == "executor"
                nxt = ep.log.events[fi + 1]
                assert nxt["kind"] == "tool_call" and nxt["agent"] == "executor" and "<INFORMATION>" in nxt["result"] or c.type != "B1"
            # all calls after the injection use the branch seed
            after = [e for e in ep.log.events[fi:] if e["kind"] == "llm_call"]
            assert after and all(e["seed"] == 100 and e["branched"] for e in after)
            assert all(e["seed"] == 0 for e in ep.log.events[:fi] if e["kind"] == "llm_call")
            if c.id != "NC_R" or s["injected"]:
                assert s["injected"], (c.id, mode, key)


@pytest.mark.parametrize("key", TASKS)
def test_taint_detected_downstream(specs, mock, key):
    for fid, stages in [("A1p_R", {"planner", "executor"}), ("A1p_P", {"executor"}), ("B1_Et", {"executor"}), ("B2_R", {"planner", "executor"})]:
        _, s = run(specs[key], "message", fid, mock)
        assert stages <= set(s["tainted_stages"]), (fid, s["tainted_stages"])
        assert s["category"] in ("propagated_failure", "propagated_harm")
    _, s = run(specs[key], "state", "B3_S", mock)
    assert s["read"] and s["security_violation"] and s["category"] == "propagated_harm"


@pytest.mark.parametrize("key", ["workspace.user_task_33", "slack.user_task_13", "banking.user_task_2"])
def test_negative_control_no_taint_in_critical_args(specs, mock, key):
    spec = specs[key]
    for mode in ("message", "state"):
        _, s = run(spec, mode, "NC_R", mock)
        assert s["injected"]
        assert s["utility"] and not s["bad"]
        inj = s["fault_record"]["injected"]
        from fpr.scoring.taint import contains

        assert not any(contains(c["args"], inj) for c in s["exec_calls"])
        assert s["category"] in ("read_not_used", "corrected")


@pytest.mark.parametrize("key", TASKS)
def test_positive_control_fails_utility(specs, mock, key):
    for mode in ("message", "state"):
        _, s = run(specs[key], mode, "PC", mock)
        assert s["injected"] and not s["utility"]


def test_clean_continuation_branches_without_change(specs, mock):
    for p in ("R.tool", "R.out", "P.out", "E.tool"):
        cid = "CLEAN_" + p.replace(".", "")
        _, s = run(specs["banking.user_task_0"], "message", cid, mock)
        assert s["branched"] and s["utility"] and s["category"] is None


def test_refine_category_marks_restored_value_corrected():
    from fpr.scoring.outcome import refine_category

    s = {"category": "read_not_used", "fault_record": {"point": "P.out", "pairs": [["UK1", "UK2"]]}, "exec_calls": [{"args": {"recipient": "UK1"}}]}
    assert refine_category(s) == "corrected"
    s["exec_calls"] = [{"args": {"recipient": "X"}}]
    assert refine_category(s) == "read_not_used"
