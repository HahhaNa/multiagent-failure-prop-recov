import asyncio

import pytest
from conftest import TASKS

from fpr.env.agentdojo_adapter import suite
from fpr.orchestrator import Episode, EpisodeConfig
from fpr.tasks import _utility, annotate


@pytest.mark.parametrize("mode", ["message", "state"])
@pytest.mark.parametrize("key", TASKS)
def test_mock_workflow_completes(specs, mock, key, mode):
    s = asyncio.run(Episode(EpisodeConfig(spec=specs[key], mode=mode), mock).run())
    assert s["utility"] and not s["stalled"] and not s["security_violation"]
    assert s["n_writes"] >= 1


def test_checkers_pass_on_ground_truth(specs):
    for spec in specs.values():
        st = suite("v1.2.2", spec.suite)
        task = st.get_user_task_by_id(spec.task_id)
        gt = [{"function": c.function, "args": dict(c.args)} for c in task.ground_truth(task.init_environment(st.load_and_inject_default_environment({})))]
        assert _utility(st, task, gt, spec.gt_output), spec.key


def test_annotation_is_deterministic(specs):
    a = annotate("v1.2.2", "banking", "user_task_0")
    assert a.model_dump() == specs["banking.user_task_0"].model_dump()


def test_critical_facts_have_perturbations(specs):
    for spec in specs.values():
        assert spec.critical_facts
        for f in spec.critical_facts:
            assert f.a1p != f.value and f.a1i != f.value
