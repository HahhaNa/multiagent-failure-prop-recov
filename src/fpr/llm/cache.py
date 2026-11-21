"""Record/replay cache keyed by sha1 of the full request."""

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .client import LLM, ChatRequest, ChatResponse, ScoreRequest, ScoreResponse


class CacheMiss(RuntimeError):
    pass


@dataclass
class DiskCache:
    root: Path

    def _path(self, key: str) -> Path:
        return Path(self.root) / key[:2] / f"{key}.json"

    def get(self, key: str) -> ChatResponse | None:
        p = self._path(key)
        if not p.exists():
            return None
        return ChatResponse.model_validate_json(p.read_text())

    def put(self, key: str, resp: ChatResponse) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(f".tmp{os.getpid()}{id(resp)}")
        tmp.write_text(resp.model_dump_json())
        os.replace(tmp, p)


class CachedLLM:
    """mode: record (hit or live+store), replay (hit or CacheMiss), live (always live+store)."""

    def __init__(self, inner: LLM | None, cache: DiskCache, mode: str = "record"):
        assert mode in ("record", "replay", "live")
        self.inner, self.cache, self.mode = inner, cache, mode
        self.live_calls = 0
        self.replayed_calls = 0

    async def chat(self, req: ChatRequest, meta: dict) -> ChatResponse:
        key = req.key()
        if self.mode != "live":
            hit = self.cache.get(key)
            if hit is not None:
                self.replayed_calls += 1
                return hit.model_copy(update={"replayed": True})
            if self.mode == "replay":
                raise CacheMiss(key)
        assert self.inner is not None
        resp = await self.inner.chat(req, meta)
        self.live_calls += 1
        self.cache.put(key, resp)
        return resp


    async def score(self, req: ScoreRequest, meta: dict) -> ScoreResponse:
        key = req.key()
        p = self.cache._path(key)
        if self.mode != "live" and p.exists():
            self.replayed_calls += 1
            return ScoreResponse.model_validate_json(p.read_text()).model_copy(update={"replayed": True})
        if self.mode == "replay":
            raise CacheMiss(key)
        resp = await self.inner.score(req, meta)
        self.live_calls += 1
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(f".tmp{os.getpid()}{id(resp)}")
        tmp.write_text(resp.model_dump_json())
        os.replace(tmp, p)
        return resp


def cache_stats(root: Path) -> dict:
    n = sum(1 for _ in Path(root).rglob("*.json"))
    return {"entries": n}


def dumps(resp: ChatResponse) -> str:
    return json.dumps(resp.model_dump())
