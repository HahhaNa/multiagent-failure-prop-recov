"""Phase 2 hook: legitimate external edits applied between a faulted run and recovery."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorldActor:
    changes: list[Callable[[Any], str]] = field(default_factory=list)

    def apply(self, env: Any) -> list[str]:
        return [change(env) for change in self.changes]
