"""Agent-written coordination files, stored in the versioned state store."""

import json
from dataclasses import dataclass
from typing import Any

from .state_store import StateStore

FINDINGS = "/shared/findings.json"
PLAN = "/shared/plan.json"


@dataclass
class Blackboard:
    store: StateStore

    def write(self, path: str, content: Any, writer: str) -> str:
        return self.store.write(f"bb:{path}", content, writer).vid

    def read(self, path: str, reader: str) -> tuple[Any, str] | None:
        key = f"bb:{path}"
        if key not in self.store.versions:
            return None
        v = self.store.read(key, reader)
        return v.value, v.vid

    def read_text(self, path: str, reader: str) -> str:
        r = self.read(path, reader)
        if r is None:
            return f"FileNotFoundError: {path} does not exist. Available: {self.paths()}"
        return json.dumps(r[0], indent=1)

    def paths(self) -> list[str]:
        return sorted(k[3:] for k in self.store.versions if k.startswith("bb:"))
