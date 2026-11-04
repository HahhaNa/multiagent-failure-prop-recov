"""Append-only log of effects that left the system. Never restored."""

from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class ExternalWorld:
    emit: Callable[[str, dict], None]
    effects: list[dict] = field(default_factory=list)

    def record(self, tool: str, args: dict, call_id: str, rev: str) -> None:
        e = {"n": len(self.effects), "tool": tool, "args": args, "call_id": call_id, "rev": rev}
        self.effects.append(e)
        self.emit("external_effect", e)
