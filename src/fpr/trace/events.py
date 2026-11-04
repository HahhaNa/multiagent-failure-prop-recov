"""Append-only per-run event log."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class EventLog:
    path: Path | None = None
    events: list[dict] = field(default_factory=list)
    clock: Any = None

    def emit(self, kind: str, data: dict) -> dict:
        e = {"i": len(self.events), "kind": kind, "t": self.clock.now if self.clock else 0.0, **data}
        self.events.append(e)
        return e

    def flush(self) -> None:
        if self.path is not None:
            with open(self.path, "w") as f:
                f.writelines(json.dumps(e, default=str, sort_keys=True) + "\n" for e in self.events)

    def of(self, kind: str) -> list[dict]:
        return [e for e in self.events if e["kind"] == kind]


def load_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
