"""Async OpenAI-compatible client for the local vLLM server."""

import asyncio
import hashlib
import json
import time
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, Field

NO_THINK = {"chat_template_kwargs": {"enable_thinking": False}}


class ChatRequest(BaseModel):
    model: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None = None
    response_format: dict[str, Any] | None = None
    temperature: float = 0.3
    seed: int = 0
    max_tokens: int = 1024

    def key(self) -> str:
        blob = json.dumps(self.model_dump(), sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha1(blob.encode()).hexdigest()


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: str


class ChatResponse(BaseModel):
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0
    finish_reason: str | None = None
    replayed: bool = False


class LLM(Protocol):
    async def chat(self, req: ChatRequest, meta: dict) -> ChatResponse: ...


class OpenAIClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8000/v1", concurrency: int = 64, timeout_s: float = 900):
        self.base_url = base_url.rstrip("/")
        self.sem = asyncio.Semaphore(concurrency)
        self.http = httpx.AsyncClient(timeout=timeout_s, limits=httpx.Limits(max_connections=concurrency + 8))

    async def chat(self, req: ChatRequest, meta: dict) -> ChatResponse:
        body: dict[str, Any] = {
            "model": req.model,
            "messages": req.messages,
            "temperature": req.temperature,
            "seed": req.seed,
            "max_tokens": req.max_tokens,
            **NO_THINK,
        }
        if req.tools:
            body["tools"] = req.tools
        if req.response_format:
            body["response_format"] = req.response_format
        async with self.sem:
            last: Exception | None = None
            for attempt in range(4):
                t0 = time.perf_counter()
                try:
                    r = await self.http.post(f"{self.base_url}/chat/completions", json=body)
                    if r.status_code >= 500:
                        raise httpx.HTTPStatusError(r.text[:300], request=r.request, response=r)
                    r.raise_for_status()
                    data = r.json()
                    break
                except (httpx.TransportError, httpx.HTTPStatusError) as e:
                    last = e
                    if isinstance(e, httpx.HTTPStatusError) and e.response.status_code < 500:
                        raise
                    await asyncio.sleep(2 * (attempt + 1))  # network backoff only
            else:
                raise RuntimeError(f"LLM request failed: {last}")
        msg = data["choices"][0]["message"]
        calls = [
            ToolCall(id=c.get("id", ""), name=c["function"]["name"], arguments=c["function"].get("arguments") or "{}")
            for c in (msg.get("tool_calls") or [])
        ]
        u = data.get("usage") or {}
        return ChatResponse(
            content=msg.get("content"),
            tool_calls=calls,
            prompt_tokens=u.get("prompt_tokens", 0),
            completion_tokens=u.get("completion_tokens", 0),
            latency_s=time.perf_counter() - t0,
            finish_reason=data["choices"][0].get("finish_reason"),
        )

    async def aclose(self) -> None:
        await self.http.aclose()
