import asyncio
import json

from conftest import TASKS

from fpr.llm.cache import CachedLLM, DiskCache
from fpr.orchestrator import Episode, EpisodeConfig
from fpr.runner import Runner, expand, load_cfg, restart_matrix
from fpr.scoring.recovery import mark_tainted, taint_from_record

BANK = "banking.user_task_0"


def faulted(spec, mode, fid, mock):
    ep = Episode(EpisodeConfig(spec=spec, mode=mode, fault_id=fid, branch_seed=100), mock)
    return asyncio.run(ep.run())


def restart(spec, mode, d, mock):
    prior = mark_tainted(d["external_effects"], taint_from_record(d["fault_record"]))
    ep = Episode(EpisodeConfig(spec=spec, mode=mode, ref_seed=200, branch_seed=200, prior_effects=prior), mock)
    return ep, asyncio.run(ep.run())


def test_a1p_restart_restores_utility_but_wrong_transfer_persists(specs, mock):
    for mode in ("message", "state"):
        d = faulted(specs[BANK], mode, "A1p_R", mock)
        assert d["injected"] and not d["utility"]
        ep, r = restart(specs[BANK], mode, d, mock)
        assert r["utility"], mode
        assert r["prior_tainted_compensable"] == 1 and r["n_prior_effects"] == 1
        txs = ep.env.env.bank_account.transactions
        bad = taint_from_record(d["fault_record"])
        from fpr.scoring.taint import contains

        assert any(contains(t.model_dump(), bad) for t in txs)  # the wrong transfer is still in the account
        assert next(e for e in ep.log.events if e["kind"] == "prior_effect")["tainted"]
        assert len([c for c in ep.env.trace if c.function == "send_money"]) == 1  # prior effects are not in this run's trace
        assert r["n_external"] == 1  # only the re-executed correct transfer is this run's effect
        assert r["duplicates"] == 0


def test_b2p_canary_transfer_persists_as_residual_harm(specs, mock):
    d = faulted(specs[BANK], "message", "B2_P", mock)
    assert d["injected"] and d["security_violation"]
    _ep, r = restart(specs[BANK], "message", d, mock)
    assert r["utility"] and r["security_violation"] and r["residual_harm"]
    # the faulted run also made the correct transfer; the restart repeats it
    assert r["duplicates"] == 1 and r["duplicate_effects"][0]["tool"] == "send_money"


def test_restart_matrix_from_runs(specs, mock, tmp_path):
    from fpr.faults.spec import conditions

    conds = [c for c in conditions() if c.id in ("A1p_R", "B2_P", "A4_R", "B3_S")]
    runs = expand("main", TASKS, ["message", "state"], [0], conds, ["R.out"], [100, 101])
    llm = CachedLLM(mock, DiskCache(tmp_path / "cache"), "record")
    r = Runner(llm, load_cfg(), specs, "mock", "mock", 16, tmp_path)
    asyncio.run(r.run_all(runs))
    rm = restart_matrix(tmp_path, "main", "restart")
    n_inj = sum(1 for p in (tmp_path / "main").glob("*/*/*/summary.json") if (s := json.loads(p.read_text()))["fault"] and not s["fault"].startswith("CLEAN_") and s["injected"])
    assert len(rm) == n_inj > 0
    r = Runner(llm, load_cfg(), specs, "mock", "mock", 16, tmp_path)  # one event loop per runner
    asyncio.run(r.run_all(rm))
    assert r.stats["failed"] == 0 and r.stats["done"] == len(rm)
    for x in rm:
        d = x.path(tmp_path)
        s = json.loads((d / "summary.json").read_text())
        man = json.loads((d / "manifest.json").read_text())
        assert s["fault"] is None and s["recovery"] == "R1" and s["ref_seed"] in (200, 201)
        # identical restart requests (same task, mode, seed, prior effects) may replay; all calls count as recomputation
        assert s["n_llm_calls"] > 0 and s["prefix_misses"] == 0 and man["run"]["recover_from"]
        src = json.loads((tmp_path / "main" / x.task / x.mode / x.name / "summary.json").read_text())
        assert s["n_prior_effects"] == len(src["external_effects"])


def test_prior_effects_fall_back_to_events(specs, mock, tmp_path):
    from fpr.runner import prior_effects

    d = tmp_path / "run"
    d.mkdir()
    ep = Episode(EpisodeConfig(spec=specs[BANK], mode="message", fault_id="B2_P", branch_seed=100), mock, d)
    s = asyncio.run(ep.run())
    full = prior_effects(d)
    s.pop("external_effects")
    (d / "summary.json").write_text(json.dumps(s, default=str))
    assert prior_effects(d) == full and len(full) == 2 and any(e["tainted"] for e in full)
