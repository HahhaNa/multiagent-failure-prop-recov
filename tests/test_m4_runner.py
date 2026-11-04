import asyncio
import json
from collections import Counter

from conftest import TASKS

from fpr.llm.cache import CachedLLM, DiskCache
from fpr.runner import Runner, expand, load_cfg, primary_matrix


def test_matrix_counts_match_plan():
    tasks = [f"t.{i}" for i in range(20)]
    runs = primary_matrix(tasks)
    c = Counter("ref" if r.fault is None else ("clean" if r.fault.startswith("CLEAN_") else "fault") for r in runs)
    assert c["ref"] == 20 * 2 * 3
    # PLAN 10: 20 x 12 x 2 x 3, minus B3 in message mode (no blackboard reader there)
    assert c["fault"] == 20 * 12 * 2 * 3 - 20 * 3
    assert c["clean"] == 20 * 4 * 2 * 3
    assert len({r.path(__import__("pathlib").Path("/x")) for r in runs}) == len(runs)
    core = primary_matrix(tasks, k=2, core_only=True, modes=["message"])
    assert sum(1 for r in core if r.fault and not r.fault.startswith("CLEAN_")) == 20 * 7 * 2  # B3 core but state-only


def _runner(specs, mock, root, conc=64):
    llm = CachedLLM(mock, DiskCache(root / "cache"), "record")
    return Runner(llm, load_cfg(), specs, "mock", "mock", conc, root), llm


def _events(d):
    return [json.loads(x) for x in (d / "events.jsonl").read_text().splitlines()]


def test_fault_and_clean_share_identical_prefix(specs, mock, tmp_path):
    runs = expand("e", TASKS, ["message", "state"], [0], [c for c in __import__("fpr.faults.spec", fromlist=["x"]).conditions()], ["R.out", "P.out", "E.tool"], [100])
    r, _ = _runner(specs, mock, tmp_path)
    asyncio.run(r.run_all(runs))
    assert r.stats["failed"] == 0
    for t in TASKS:
        for m in ("message", "state"):
            ref = [e for e in _events(tmp_path / "e" / t / m / "ref_s0") if e["kind"] == "llm_call"]
            for d in (tmp_path / "e" / t / m).iterdir():
                if d.name == "ref_s0":
                    continue
                ev = _events(d)
                pre = [e for e in ev if e["kind"] == "llm_call" and not e["branched"]]
                assert all(e["replayed"] for e in pre), d
                assert [(e["key"], e["content"]) for e in pre] == [(e["key"], e["content"]) for e in ref[: len(pre)]], d
                assert not [e for e in ev if e["kind"] == "prefix_miss"], d


def test_kill_and_restart_resumes_without_duplicates(specs, mock, tmp_path):
    runs = expand("e", TASKS, ["message", "state"], [0, 1], [], [], [])
    r1, _ = _runner(specs, mock, tmp_path, conc=2)

    async def killed():
        t = asyncio.create_task(r1.run_all(runs))
        while r1.stats["done"] < 4:
            await asyncio.sleep(0)
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass

    asyncio.run(killed())
    first = set(r1.executed)
    assert 4 <= len(first) < len(runs)
    r2, _ = _runner(specs, mock, tmp_path)
    asyncio.run(r2.run_all(runs))
    second = set(r2.executed)
    assert not first & second
    assert len(first | second) == len(runs)
    assert all((x.path(tmp_path) / "DONE").exists() for x in runs)
    assert r2.stats["skipped"] == len(first)
    # interrupted attempts are kept and labeled, never deleted
    leftovers = list(tmp_path.glob("e/*/*/*.incomplete*"))
    assert all(not (p / "DONE").exists() for p in leftovers)


def test_concurrent_episodes_do_not_share_env(specs, mock, tmp_path, monkeypatch):
    import fpr.orchestrator as orch

    seen = []
    orig = orch.Episode.__post_init__

    def spy(self):
        orig(self)
        seen.append((self.env.env, self.store, self.bb, self.world))
        live.append(1)

    orig_run = orch.Episode.run

    async def run_spy(self):
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        try:
            return await orig_run(self)
        finally:
            active[0] -= 1

    live, active, peak = [], [0], [0]
    monkeypatch.setattr(orch.Episode, "__post_init__", spy)
    monkeypatch.setattr(orch.Episode, "run", run_spy)
    tasks = TASKS * 22  # 66 episodes
    runs = [x for i, t in enumerate(tasks) for x in expand(f"c{i}", [t], ["message"], [0], [], [], [])]
    r, _ = _runner(specs, mock, tmp_path, conc=64)
    asyncio.run(r.run_all(runs))
    assert r.stats["done"] == len(runs) and r.stats["failed"] == 0
    assert peak[0] >= 60
    for col in zip(*seen):
        assert len({id(x) for x in col}) == len(seen)
    for i, t in enumerate(tasks):
        s = json.loads((tmp_path / f"c{i}" / t / "message" / "ref_s0" / "summary.json").read_text())
        assert s["utility"], t


def test_manifest_fields(specs, mock, tmp_path):
    runs = expand("e", TASKS[:1], ["message"], [0], [], [], [])
    r, _ = _runner(specs, mock, tmp_path)
    asyncio.run(r.run_all(runs))
    m = json.loads((runs[0].path(tmp_path) / "manifest.json").read_text())
    for k in ("git_commit", "git_dirty", "config_hash", "model_repo", "model_revision", "quantization", "vllm_version",
              "agentdojo_version", "benchmark_version", "driver_version", "cuda_version", "seed", "temperature", "serve_command", "start", "end"):
        assert k in m, k
