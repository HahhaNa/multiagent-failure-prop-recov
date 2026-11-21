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


class ScoreRequest(BaseModel):
    """Log-probability of an assistant turn given a conversation (CausalArmor-style attribution, Phase 2b)."""

    model: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None = None
    assistant: dict[str, Any]

    def key(self) -> str:
        blob = json.dumps({"score": self.model_dump()}, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha1(blob.encode()).hexdigest()


class ScoreResponse(BaseModel):
    logprob: float
    n_tokens: int = 0
    prompt_tokens: int = 0
    replayed: bool = False


def render_assistant(a: dict[str, Any]) -> str:
    """Fixed text form of an assistant tool-call turn (hermes format); the same form is scored under every ablation."""
    parts = [a.get("content") or ""]
    for tc in a.get("tool_calls") or []:
        f = tc["function"]
        args = f.get("arguments")
        try:
            args = json.loads(args) if isinstance(args, str) else args
        except ValueError:
            pass
        parts.append("<tool_call>\n" + json.dumps({"name": f["name"], "arguments": args}) + "\n</tool_call>")
    return "\n".join(p for p in parts if p) + "<|im_end|>"


def alnum9_ids(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rewrite tool call ids to 9 alphanumeric chars (required by Mistral chat templates); deterministic per id."""
    h = lambda s: hashlib.sha1(s.encode()).hexdigest()[:9]
    out = []
    for m in messages:
        m = dict(m)
        if m.get("tool_calls"):
            m["tool_calls"] = [{**tc, "id": h(tc["id"])} for tc in m["tool_calls"]]
        if m.get("tool_call_id"):
            m["tool_call_id"] = h(m["tool_call_id"])
        out.append(m)
    return out


class OpenAIClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8000/v1", concurrency: int = 64, timeout_s: float = 900, alnum9: bool = False,
                 thinking_flag: bool = True, revision: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.alnum9 = alnum9
        self.thinking_flag = thinking_flag  # send the Qwen3 enable_thinking=False template kwarg
        self.sem = asyncio.Semaphore(concurrency)
        self.http = httpx.AsyncClient(timeout=timeout_s, limits=httpx.Limits(max_connections=concurrency + 8))
        self.revision = revision
        self._tok = None

    def tokenizer(self, model: str):
        if self._tok is None:
            from transformers import AutoTokenizer

            self._tok = AutoTokenizer.from_pretrained(model, revision=self.revision, local_files_only=True)
        return self._tok

    async def score(self, req: ScoreRequest, meta: dict) -> ScoreResponse:
        tok = self.tokenizer(req.model)
        msgs = []
        for m in req.messages:  # chat templates expect tool-call arguments as objects
            m = dict(m)
            if m.get("tool_calls"):
                m["tool_calls"] = [{**tc, "function": {**tc["function"], "arguments": _obj(tc["function"].get("arguments"))}} for tc in m["tool_calls"]]
            msgs.append(m)
        prefix = tok.apply_chat_template(msgs, tools=req.tools, add_generation_prompt=True, tokenize=False, enable_thinking=False)
        ids_p = tok(prefix, add_special_tokens=False).input_ids
        ids = ids_p + tok(render_assistant(req.assistant), add_special_tokens=False).input_ids
        body = {"model": req.model, "prompt": ids, "max_tokens": 1, "temperature": 0.0, "prompt_logprobs": 0}
        async with self.sem:
            r = await self.http.post(f"{self.base_url}/completions", json=body)
            r.raise_for_status()
            data = r.json()
        plp = data["choices"][0]["prompt_logprobs"]
        lp = 0.0
        for t in range(len(ids_p), len(ids)):
            e = plp[t] or {}
            lp += float((e.get(str(ids[t])) or next(iter(e.values()), {"logprob": 0.0}))["logprob"])
        return ScoreResponse(logprob=lp, n_tokens=len(ids) - len(ids_p), prompt_tokens=len(ids))

    async def chat(self, req: ChatRequest, meta: dict) -> ChatResponse:
        body: dict[str, Any] = {
            "model": req.model,
            "messages": alnum9_ids(req.messages) if self.alnum9 else req.messages,
            "temperature": req.temperature,
            "seed": req.seed,
            "max_tokens": req.max_tokens,
            **(NO_THINK if self.thinking_flag else {}),
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
                    if r.status_code >= 400:
                        raise httpx.HTTPStatusError(f"{r.status_code}: {r.text[:300]}", request=r.request, response=r)
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


def _obj(x):
    try:
        return json.loads(x) if isinstance(x, str) else x
    except ValueError:
        return x
