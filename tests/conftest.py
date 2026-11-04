from pathlib import Path

import pytest

from fpr.llm.mock import MockLLM
from fpr.tasks import load_specs

ROOT = Path(__file__).resolve().parents[1]
TASKS = ["banking.user_task_0", "workspace.user_task_33", "slack.user_task_13"]


@pytest.fixture(scope="session")
def specs():
    return load_specs(ROOT / "configs/tasks_candidates.yaml")


@pytest.fixture
def mock(specs):
    return MockLLM(specs)
