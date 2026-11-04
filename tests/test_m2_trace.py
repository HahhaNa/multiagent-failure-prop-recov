import asyncio
import json

from conftest import TASKS

from fpr.env.snapshots import dump
from fpr.llm.cache import CachedLLM, DiskCache
from fpr.orchestrator import Episode, EpisodeConfig

VOLATILE = {"latency_s", "replayed", "t"}


def _clean(events):
    return [{k: v for k, v in e.items() if k not in VOLATILE} for e in events]


def test_events_complete(specs, mock):
    for key in TASKS:
        for mode in ("message", "state"):
            ep = Episode(EpisodeConfig(spec=specs[key], mode=mode), mock)
            asyncio.run(ep.run())
            kinds = {e["kind"] for e in ep.log.events}
            assert {"llm_call", "tool_call", "state_read", "state_write", "artifact", "episode_end"} <= kinds
            if mode == "message":
                assert "message" in kinds
            calls = [e for e in ep.log.events if e["kind"] == "tool_call"]
            assert all({"args", "result", "rev", "step_id", "reads", "writes"} <= set(e) for e in calls)
            ex = [e for e in calls if e["agent"] == "executor" and e["rev"] != "none"]
            assert ex and all(e["step_id"] for e in ex)
            assert all(e["writes"] for e in ex if not e["error"])


def test_external_world_logged(specs, mock):
    ep = Episode(EpisodeConfig(spec=specs["banking.user_task_0"], mode="message"), mock)
    asyncio.run(ep.run())
    assert [e["tool"] for e in ep.world.effects] == ["send_money"]


def test_snapshot_restore_reproduces_env(specs, mock):
    for key in TASKS:
        ep = Episode(EpisodeConfig(spec=specs[key], mode="message"), mock)
        asyncio.run(ep.run())
        env_objs = [o for o in ep.store.versions if o.startswith("env:")]
        changed = [o for o in env_objs if len(ep.store.versions[o]) > 1]
        assert changed
        # every intermediate version equals the recorded snapshot after restore
        for o in changed:
            for v in list(ep.store.versions[o]):
                ep.env.restore([v.vid])
                assert dump(getattr(ep.env.env, o.split(":", 1)[1])) == dump(v.value)
        ep.env.restore([ep.store.versions[o][0].vid for o in env_objs])
        assert ep.env.env.model_dump(mode="json") == ep.env.pre_env.model_dump(mode="json")


def test_replay_zero_live_calls_identical_trace(specs, mock, tmp_path):
    cache = DiskCache(tmp_path / "cache")
    for key in TASKS:
        for mode in ("message", "state"):
            rec = CachedLLM(mock, cache, "record")
            ep1 = Episode(EpisodeConfig(spec=specs[key], mode=mode), rec)
            s1 = asyncio.run(ep1.run())
            assert rec.live_calls + rec.replayed_calls == s1["n_llm_calls"] > 0
            rep = CachedLLM(None, cache, "replay")
            ep2 = Episode(EpisodeConfig(spec=specs[key], mode=mode), rep)
            s2 = asyncio.run(ep2.run())
            assert rep.live_calls == 0 and rep.replayed_calls == s1["n_llm_calls"]
            assert json.dumps(_clean(ep1.log.events), default=str) == json.dumps(_clean(ep2.log.events), default=str)
            assert s1["utility"] == s2["utility"]
