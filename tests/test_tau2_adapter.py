"""tau2-bench adapter: M1-M3 acceptance tests on MockLLM (skipped when the tau2 data is not installed)."""

import asyncio
import gzip
import json

import pytest
from conftest import ROOT

from fpr.env import tau2_adapter as t2
from fpr.env.external_world import ExternalWorld
from fpr.env.snapshots import dump
from fpr.env.state_store import StateStore
from fpr.faults.spec import conditions
from fpr.llm.cache import CachedLLM, DiskCache
from fpr.llm.mock import MockLLM
from fpr.orchestrator import Episode, EpisodeConfig
from fpr.scoring.taint import contains
from fpr.tasks import load_specs

pytestmark = pytest.mark.skipif(not t2.available(), reason="tau2-bench data not found (TAU2_DATA_DIR)")

TASKS = ["tau2_retail.1", "tau2_retail.5", "tau2_airline.8"]
VOLATILE = {"latency_s", "replayed", "t"}


@pytest.fixture(scope="module")
def specs():
    return load_specs(ROOT / "configs/tau2_candidates.yaml")


@pytest.fixture
def mock(specs):
    return MockLLM(specs)


def run(spec, mode, fid, mock, d=None, **kw):
    ep = Episode(EpisodeConfig(spec=spec, mode=mode, fault_id=fid, branch_seed=100, **kw), mock, d)
    return ep, asyncio.run(ep.run())


def scratch(key):
    from fpr.tau2_tasks import scratch_env

    return scratch_env(*key.split(".", 1))


# ---------- M1: workflow ----------


@pytest.mark.parametrize("mode", ["message", "state"])
@pytest.mark.parametrize("key", TASKS)
def test_mock_workflow_completes(specs, mock, key, mode):
    _, s = run(specs[key], mode, None, mock)
    assert s["utility"] and not s["stalled"] and not s["security_violation"]
    assert s["n_writes"] >= 1


def test_candidates_cover_both_domains_and_gate_size(specs):
    by = {sn: [k for k in specs if k.startswith(sn)] for sn in t2.SUITES}
    assert all(len(v) >= 10 for v in by.values()) and len(specs) >= 30


def test_checkers_pass_on_gold_and_fail_on_perturbation(specs):
    from fpr.tasks import _merge, _set
    from fpr.tau2_tasks import replay

    for spec in list(specs.values())[::6]:
        dom = t2._domain(spec.suite)
        gt = t2.gold_actions(dom["tasks"][spec.task_id])
        assert replay(spec.suite, spec.task_id, gt, spec.gt_output), spec.key
        assert not replay(spec.suite, spec.task_id, [], spec.gt_output), spec.key  # doing nothing fails
        f = spec.critical_facts[0]
        orig = gt[f.call]["args"]
        x = orig
        for p in f.path:
            x = x[p]
        bad = _set(orig, f.path, _merge(x, f.value, f.a1p))
        assert not replay(spec.suite, spec.task_id, gt[: f.call] + [{"function": gt[f.call]["function"], "args": bad}] + gt[f.call + 1 :], spec.gt_output)


def test_annotation_is_deterministic(specs):
    from fpr.tau2_tasks import annotate

    for key in TASKS:
        assert annotate(*key.split(".", 1)).model_dump() == specs[key].model_dump()


def test_critical_facts_have_perturbations(specs):
    for spec in specs.values():
        assert spec.critical_facts and spec.gt_writes and spec.canary and spec.injection_goal
        assert spec.canary in spec.injection_goal and spec.vectors == ["tool_return"]
        for f in spec.critical_facts:
            assert f.a1p != f.value and f.a1i != f.value
            assert not contains(spec.prompt, f.value if not isinstance(f.value, list) else f.value[0])


def test_labels_cover_every_tool():
    from fpr.env.agentdojo_adapter import tools_cfg

    for sn in t2.SUITES:
        dom, cfg = t2._domain(sn), tools_cfg()[sn]
        assert set(cfg["read"]) | set(cfg["write"]) == set(dom["schemas"])
        assert not set(cfg["read"]) & set(cfg["write"])
        assert all(not dom["mutates"][n] for n in cfg["read"])
        assert all(n in cfg["write"] for n, m in dom["mutates"].items() if m)
        for lab in cfg["write"].values():
            assert lab["rev"] in ("reversible", "compensable", "irreversible")
            assert lab.get("external", False) == (lab["rev"] != "reversible")


def test_policy_reaches_planner_and_executor_only(specs, mock):
    ep, _ = run(specs["tau2_retail.1"], "message", None, mock)
    sys = {a: ep.ctx.transcripts[a][0][0]["content"] for a in ("researcher", "planner", "executor")}
    assert ep.env.policy[:200] in sys["planner"] and ep.env.policy[:200] in sys["executor"]
    assert "Domain policy" not in sys["researcher"]
    assert ep.ctx.transcripts["researcher"][0][1]["content"] == f"User task: {specs['tau2_retail.1'].prompt}"


# ---------- adapter contract ----------


def test_disallowed_tool_returns_error_text():
    env = scratch("tau2_retail.1")
    h = env.db_hash()
    for name in ("cancel_pending_order", "no_such_tool"):
        text, err, reads, writes = env.call(name, {"order_id": "#W2378156", "reason": "no longer needed"}, "researcher", "c0", env.read_tools)
        assert text.startswith("ToolNotFoundError") and name in text and "researcher" in text and err == text
        assert not reads and not writes
    assert env.db_hash() == h and not env.trace and not env.world.effects


def test_tool_error_is_returned_and_not_traced():
    env = scratch("tau2_retail.1")
    text, err, _, writes = env.call("get_order_details", {"order_id": "#W0"}, "researcher", "c0", env.read_tools)
    assert text == err == "Error: Order not found" and not writes and not env.trace
    text, err, _, _ = env.call("get_order_details", {"bogus": 1}, "researcher", "c1", env.read_tools)
    assert err and text.startswith("Error:")


def test_reads_and_writes_are_record_versions():
    events = []
    env = t2.Tau2Env("", "tau2_retail", "1", StateStore(lambda k, d: events.append((k, d))), ExternalWorld(lambda k, d: None))
    names = env.read_tools + list(env.write_labels)
    _, err, reads, writes = env.call("get_order_details", {"order_id": "#W2378156"}, "researcher", "c0", names)
    assert err is None and reads == ["env:orders/#W2378156@0"] and not writes
    _, err, reads, writes = env.call("find_user_id_by_name_zip", {"first_name": "Yusuf", "last_name": "Rossi", "zip": "19122"}, "researcher", "c1", names)
    assert err is None and reads == ["env:users@0"] and not writes
    gold = next(c for c in t2.gold_actions(env.task) if c["function"] in env.write_labels)
    _, err, reads, writes = env.call(gold["function"], gold["args"], "executor", "c2", names)
    assert err is None and "env:orders/#W2378156@1" in writes and "env:orders/#W2378156@0" in reads
    assert env.world.effects[0]["tool"] == gold["function"] and env.world.effects[0]["rev"] == "irreversible"
    assert {k for k, _ in events} == {"state_read", "state_write"}
    # a later scan depends on records changed earlier in the episode
    env.call("modify_user_address", {"user_id": "yusuf_rossi_9620", "address1": "1 A St", "address2": "", "city": "X", "state": "PA", "country": "USA", "zip": "19122"}, "executor", "c3", names)
    _, _, reads, _ = env.call("find_user_id_by_name_zip", {"first_name": "Yusuf", "last_name": "Rossi", "zip": "19122"}, "executor", "c4", names)
    assert "env:users/yusuf_rossi_9620@1" in reads


def test_new_record_and_restore_to_absent():
    env = scratch("tau2_airline.8")
    h0 = env.db_hash()
    names = env.read_tools + list(env.write_labels)
    book = next(c for c in t2.gold_actions(env.task) if c["function"] == "book_reservation")
    text, err, _, writes = env.call("book_reservation", book["args"], "executor", "c0", names)
    assert err is None
    rid = json.loads(text)["reservation_id"]
    assert f"env:reservations/{rid}@1" in writes and "env:reservations@1" in writes
    assert env.store.versions[f"env:reservations/{rid}"][0].value is None
    assert env.world.effects[0]["rev"] == "compensable"
    env.restore([vs[0].vid for vs in env.store.versions.values()])
    assert env.db_hash() == h0 and rid not in env.db.reservations


def test_episodes_do_not_share_state(specs, mock):
    a, b = scratch("tau2_retail.1"), scratch("tau2_retail.1")
    gold = [c for c in t2.gold_actions(a.task) if c["function"] in a.write_labels]
    for i, c in enumerate(gold):
        a.call(c["function"], c["args"], "executor", f"c{i}", list(a.write_labels))
    assert a.utility("") and not b.utility("")
    assert b.db_hash() == t2._domain("tau2_retail")["db"].get_hash()


def test_security_checks():
    for key in ("tau2_retail.1", "tau2_airline.8"):
        env = scratch(key)
        inj = t2.INJECTION[env.suite_name]
        assert not env.security(inj["task"], "")
        args = dict(inj["call"]["args"])
        if env.suite_name == "tau2_retail":
            args["user_id"] = t2.customer_id("tau2_retail", "1")
        _, err, _, writes = env.call(inj["call"]["function"], args, "executor", "c0", list(env.write_labels))
        assert err is None and writes and env.security(inj["task"], "")
        assert inj["canary"] in inj["goal"]
    dom = t2._domain("tau2_retail")
    assert t2.RETAIL_CANARY not in json.dumps(dom["db"].model_dump(mode="json"))
    air = t2._domain("tau2_airline")
    used = json.dumps(list(air["tasks"].values()))
    assert t2.AIRLINE_CANARY == next(u for u in sorted(air["db"].users) if u not in used)


# ---------- M2: tracing, state store, replay ----------


def test_events_complete(specs, mock):
    for key in TASKS:
        for mode in ("message", "state"):
            ep, _ = run(specs[key], mode, None, mock)
            kinds = {e["kind"] for e in ep.log.events}
            assert {"llm_call", "tool_call", "state_read", "state_write", "artifact", "episode_end", "external_effect"} <= kinds
            calls = [e for e in ep.log.events if e["kind"] == "tool_call"]
            assert all({"args", "result", "rev", "step_id", "reads", "writes"} <= set(e) for e in calls)
            ex = [e for e in calls if e["agent"] == "executor" and e["rev"] != "none"]
            assert ex and all(e["step_id"] for e in ex)
            assert all(e["writes"] for e in ex if not e["error"] and e["name"] != "transfer_to_human_agents")
            assert all(e["reads"] for e in calls if e["agent"] == "researcher" and not e["error"] and e["name"] != "calculate")


def test_snapshot_restore_reproduces_env(specs, mock):
    for key in TASKS:
        ep, _ = run(specs[key], "message", None, mock)
        env = ep.env
        objs = [o for o in ep.store.versions if o.startswith("env:")]
        changed = [o for o in objs if len(ep.store.versions[o]) > 1]
        assert changed
        for o in changed:
            for v in list(ep.store.versions[o]):
                env.restore([v.vid])
                assert dump(env._value(o)) == dump(v.value)
        env.restore([ep.store.versions[o][0].vid for o in objs])
        assert env.db_hash() == t2._domain(env.suite_name)["db"].get_hash()


def test_restore_all_gold_trajectories():
    """Record tracking sees every mutation: restoring the touched records gives back the initial database."""
    for sn in t2.SUITES:
        dom = t2._domain(sn)
        for tid in t2.task_ids(sn)[::5]:
            env = scratch(f"{sn}.{tid}")
            for i, a in enumerate(t2.gold_actions(env.task)):
                env.call(a["function"], a["args"], "gt", f"g{i}", list(dom["schemas"]))
            env.restore([vs[0].vid for vs in env.store.versions.values()])
            assert env.db_hash() == dom["db"].get_hash(), (sn, tid)


def test_replay_zero_live_calls_identical_trace(specs, mock, tmp_path):
    cache = DiskCache(tmp_path / "cache")
    for key in TASKS:
        for mode in ("message", "state"):
            rec = CachedLLM(mock, cache, "record")
            ep1, s1 = run(specs[key], mode, None, rec)
            rep = CachedLLM(None, cache, "replay")
            ep2, s2 = run(specs[key], mode, None, rep)
            assert rep.live_calls == 0 and rep.replayed_calls == s1["n_llm_calls"] > 0
            clean = [[{k: v for k, v in e.items() if k not in VOLATILE} for e in ep.log.events] for ep in (ep1, ep2)]
            assert json.dumps(clean[0], default=str) == json.dumps(clean[1], default=str)
            assert s1["utility"] == s2["utility"]


def test_full_trace_files_and_graphs(specs, mock, tmp_path):
    from fpr.trace.graph import build

    d = tmp_path / "run"
    d.mkdir()
    ep, _ = run(specs["tau2_retail.1"], "state", "A1p_R", mock, d)
    tr = json.loads((d / "transcripts.json").read_text())
    assert set(tr) == {"researcher", "planner", "executor"}
    with gzip.open(d / "snapshots.json.gz", "rt") as fh:
        snaps = json.load(fh)
    assert "bb:/shared/plan.json" in snaps and any(k.startswith("env:orders/") for k in snaps)
    assert (d / "snapshots.json.gz").stat().st_size < 200_000  # only touched records are stored
    g = build(ep.log.events, ep.inj.taint_values())
    assert ("claim:c1", "step:s1") in g["declared"] and ("findings", "plan") in g["dataflow"]
    assert "step:s1" in g["tainted"]


# ---------- M3: faults ----------


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
            llm_before = [e["agent"] for e in ep.log.events[:fi] if e["kind"] == "llm_call"]
            if not fev[0]["applied"]:
                ok = c.id in ("NC_R", "A2_R") or (c.type == "A1-p" and c.point in ("E.tool", "R.tool", "S.write"))
                assert ok and fev[0].get("reason") in ("not_reached", "no_target"), (c.id, mode, key)
                continue
            if c.point == "R.start":
                assert not llm_before
            elif c.point == "P.start" or c.point in ("R.out", "R.tool"):
                assert set(llm_before) == {"researcher"}
            elif c.point in ("P.out", "S.write"):
                assert "planner" in llm_before and "executor" not in llm_before
            elif c.point == "E.tool":
                assert llm_before[-1] == "executor"
                nxt = ep.log.events[fi + 1]
                assert nxt["kind"] == "tool_call" and nxt["agent"] == "executor"
                assert "<INFORMATION>" in nxt["result"] or c.type != "B1"
            after = [e for e in ep.log.events[fi:] if e["kind"] == "llm_call"]
            assert after and all(e["seed"] == 100 and e["branched"] for e in after)
            assert all(e["seed"] == 0 for e in ep.log.events[:fi] if e["kind"] == "llm_call")
            assert s["injected"], (c.id, mode, key)


@pytest.mark.parametrize("key", TASKS)
def test_core_conditions_apply(specs, mock, key):
    for fid in ("A1p_R", "A1p_P", "A3_R", "A4_R", "B1_Rt", "B1_Et", "B2_R", "B2_P", "B4_R", "BP_R"):
        _, s = run(specs[key], "message", fid, mock)
        assert s["injected"], (fid, key)


@pytest.mark.parametrize("key", TASKS)
def test_taint_detected_downstream(specs, mock, key):
    for fid, stages in [("A1p_R", {"planner", "executor"}), ("A1p_P", {"executor"}), ("B1_Et", {"executor"}), ("B2_R", {"planner", "executor"})]:
        _, s = run(specs[key], "message", fid, mock)
        assert stages <= set(s["tainted_stages"]), (fid, s["tainted_stages"])
        assert s["category"] in ("propagated_failure", "propagated_harm"), (fid, s["category"])
    for fid in ("B1_Et", "B2_R", "B1_Rt"):
        _, s = run(specs[key], "message", fid, mock)
        assert s["security_violation"] and s["category"] == "propagated_harm", fid
    _, s = run(specs[key], "state", "B3_S", mock)
    assert s["read"] and s["security_violation"] and s["category"] == "propagated_harm"


def test_tool_return_injection_reaches_researcher(specs, mock):
    ep, s = run(specs["tau2_airline.8"], "message", "B1_Rt", mock)
    first = next(e for e in ep.log.events if e["kind"] == "tool_call")
    assert first["agent"] == "researcher" and "<INFORMATION>" in first["result"] and t2.AIRLINE_CANARY in first["result"]
    assert sum("<INFORMATION>" in e["result"] for e in ep.log.events if e["kind"] == "tool_call") == 1
    assert any(x["tool"] == "send_certificate" and x["rev"] == "irreversible" for x in s["external_effects"])
    assert s["tainted_irreversible"] >= 1


def test_negative_control_no_taint_in_critical_args(specs, mock):
    keys = [k for k, sp in specs.items() if sp.noncritical_facts][:3]
    assert keys
    for key in keys:
        for mode in ("message", "state"):
            _, s = run(specs[key], mode, "NC_R", mock)
            assert s["injected"] and s["utility"] and not s["bad"]
            assert not any(contains(c["args"], s["fault_record"]["injected"]) for c in s["exec_calls"])
            assert s["category"] in ("read_not_used", "corrected")


@pytest.mark.parametrize("key", TASKS)
def test_positive_control_fails_utility(specs, mock, key):
    for mode in ("message", "state"):
        _, s = run(specs[key], mode, "PC", mock)
        assert s["injected"] and not s["utility"]


def test_clean_continuation_branches_without_change(specs, mock):
    for p in ("R.tool", "R.out", "P.out", "E.tool"):
        _, s = run(specs["tau2_retail.1"], "message", "CLEAN_" + p.replace(".", ""), mock)
        assert s["branched"] and s["utility"] and s["category"] is None


def test_restart_reapplies_prior_effects(specs, mock):
    spec = specs["tau2_retail.1"]
    _, s0 = run(spec, "message", "B1_Et", mock)
    prior = [{**e, "tainted": contains(e["args"], spec.canary)} for e in s0["external_effects"]]
    assert prior
    ep, s = run(spec, "message", None, mock, prior_effects=prior)
    assert [e["kind"] for e in ep.log.events if e["kind"] == "prior_effect"] and s["recovery"] == "R1"
    assert s["duplicates"] >= 0 and "residual_harm" in s


def test_agentdojo_prompts_unchanged(mock):
    from fpr.agents.base import policy_suffix

    class Ctx:
        env = object()

    assert policy_suffix(Ctx) == ""


def test_disallowed_tool_error_reaches_the_model(specs):
    from test_p2_harness import Scripted

    from fpr.agents.base import Ctx, tool_loop
    from fpr.env.blackboard import Blackboard
    from fpr.env.clock import VirtualClock
    from fpr.trace.events import EventLog

    llm = Scripted([("cancel_pending_order", '{"order_id": "#W2378156", "reason": "no longer needed"}'), ("read_shared", '{"path": "/shared/plan.json"}')])
    log = EventLog(None, clock=VirtualClock())
    store = StateStore(log.emit)
    env = t2.Tau2Env("", "tau2_retail", "1", store, ExternalWorld(log.emit))
    ctx = Ctx(env=env, bb=Blackboard(store), log=log, clock=log.clock, llm=llm, model="m", temperature=0.3, ref_seed=0, branch_seed=0, max_tokens=64)
    asyncio.run(tool_loop(ctx, "researcher", [{"role": "user", "content": "t"}], env.tool_schemas(env.read_tools), env.read_tools, 4))
    tool_msgs = [m for m in llm.requests[-1].messages if m["role"] == "tool"]
    assert "ToolNotFoundError: `cancel_pending_order` is not available to the researcher." in tool_msgs[0]["content"]
    assert "ToolNotFoundError: `read_shared` is not available to the researcher." in tool_msgs[1]["content"]
    calls = [e for e in log.events if e["kind"] == "tool_call"]
    assert all(e["error"] and e["error"] == e["result"] for e in calls)
    assert not env.trace and not env.world.effects and env.db_hash() == t2._domain("tau2_retail")["db"].get_hash()
