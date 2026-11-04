"""Deep-copy snapshots and JSON diffs of pydantic objects."""

import copy
from typing import Any

from pydantic import BaseModel


def dump(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, list):
        return [dump(x) for x in obj]
    if isinstance(obj, dict):
        return {k: dump(v) for k, v in obj.items()}
    return obj


def snapshot(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_copy(deep=True)
    return copy.deepcopy(obj)


def diff(a: Any, b: Any, path: str = "") -> list[dict]:
    """Minimal structural diff between two JSON-like values."""
    if type(a) is not type(b):
        return [{"op": "replace", "path": path, "old": a, "new": b}]
    if isinstance(a, dict):
        out: list[dict] = []
        for k in sorted(set(a) | set(b), key=str):
            p = f"{path}/{k}"
            if k not in a:
                out.append({"op": "add", "path": p, "new": b[k]})
            elif k not in b:
                out.append({"op": "remove", "path": p, "old": a[k]})
            else:
                out.extend(diff(a[k], b[k], p))
        return out
    if isinstance(a, list):
        if len(a) != len(b):
            if len(b) > len(a) and a == b[: len(a)]:
                return [{"op": "add", "path": f"{path}/{i}", "new": b[i]} for i in range(len(a), len(b))]
            return [{"op": "replace", "path": path, "old": a, "new": b}]
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            out.extend(diff(x, y, f"{path}/{i}"))
        return out
    return [] if a == b else [{"op": "replace", "path": path, "old": a, "new": b}]
