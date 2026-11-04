"""Versioned store over named state objects (env attributes and blackboard files).

Every write creates a new version id; every read records the version it saw.
Per-object restore is supported for Phase 2 recovery.
"""

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .snapshots import diff, dump, snapshot


def _digest(data: Any) -> str:
    return hashlib.sha1(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:12]


@dataclass
class Version:
    vid: str
    obj: str
    n: int
    digest: str
    writer: str
    value: Any  # deep copy
    size: int


@dataclass
class StateStore:
    emit: Callable[[str, dict], None]
    versions: dict[str, list[Version]] = field(default_factory=dict)
    timers: dict[str, float] = field(default_factory=lambda: {"snapshot_s": 0.0, "snapshot_bytes": 0})

    def register(self, obj: str, value: Any, writer: str = "init") -> Version:
        return self._new_version(obj, value, writer, log=False)

    def _new_version(self, obj: str, value: Any, writer: str, log: bool = True, d: list | None = None) -> Version:
        t0 = time.perf_counter()
        data = dump(value)
        raw = json.dumps(data, sort_keys=True, default=str)
        hist = self.versions.setdefault(obj, [])
        v = Version(f"{obj}@{len(hist)}", obj, len(hist), _digest(data), writer, snapshot(value), len(raw))
        hist.append(v)
        self.timers["snapshot_s"] += time.perf_counter() - t0
        self.timers["snapshot_bytes"] += v.size
        if log:
            self.emit("state_write", {"obj": obj, "version": v.vid, "writer": writer, "diff": d or [], "bytes": v.size})
        return v

    def current(self, obj: str) -> Version:
        return self.versions[obj][-1]

    def read(self, obj: str, reader: str) -> Version:
        v = self.current(obj)
        self.emit("state_read", {"obj": obj, "version": v.vid, "reader": reader})
        return v

    def observe(self, obj: str, value: Any, writer: str) -> Version | None:
        """Record a new version if value changed since the current version."""
        cur = self.current(obj)
        data = dump(value)
        if _digest(data) == cur.digest:
            return None
        return self._new_version(obj, value, writer, d=diff(dump(cur.value), data))

    def write(self, obj: str, value: Any, writer: str) -> Version:
        if obj not in self.versions:
            v = self._new_version(obj, value, writer, log=False)
            self.emit("state_write", {"obj": obj, "version": v.vid, "writer": writer, "diff": [{"op": "add", "path": "", "new": dump(value)}], "bytes": v.size})
            return v
        return self.observe(obj, value, writer) or self.current(obj)

    def value_at(self, vid: str) -> Any:
        obj, n = vid.rsplit("@", 1)
        return snapshot(self.versions[obj][int(n)].value)
