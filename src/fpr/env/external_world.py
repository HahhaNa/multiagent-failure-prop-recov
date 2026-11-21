"""Append-only log of effects that left the system. Never restored.

Staged mode (Phase 2b): effects wait in an outbox until released at the commit point; rejected ones are dropped.
"""

from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class ExternalWorld:
    emit: Callable[[str, dict], None]
    effects: list[dict] = field(default_factory=list)
    staged: bool = False
    outbox: list[dict] = field(default_factory=list)

    def record(self, tool: str, args: dict, call_id: str, rev: str) -> None:
        if self.staged:
            self.outbox.append({"tool": tool, "args": args, "call_id": call_id, "rev": rev})
            self.emit("staged_effect", self.outbox[-1])
            return
        e = {"n": len(self.effects), "tool": tool, "args": args, "call_id": call_id, "rev": rev}
        self.effects.append(e)
        self.emit("external_effect", e)

    def release(self, call_ids: set[str]) -> None:
        """Commit point: release the accepted outbox effects in order; drop the rest."""
        keep = [e for e in self.outbox if e["call_id"] in call_ids]
        self.outbox = []
        self.staged = False
        for e in keep:
            self.record(e["tool"], e["args"], e["call_id"], e["rev"])
