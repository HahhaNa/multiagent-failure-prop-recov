"""Virtual clock. Time advances only by explicit charges, never by wall time."""

from dataclasses import dataclass, field


@dataclass
class VirtualClock:
    prompt_tok_s: float = 0.0004
    completion_tok_s: float = 0.02
    tool_s: float = 0.05
    now: float = 0.0
    ticks: list[tuple[float, str]] = field(default_factory=list)

    def advance(self, seconds: float, reason: str) -> float:
        self.now = round(self.now + seconds, 6)
        self.ticks.append((self.now, reason))
        return self.now

    def charge_llm(self, prompt_tokens: int, completion_tokens: int) -> float:
        return self.advance(prompt_tokens * self.prompt_tok_s + completion_tokens * self.completion_tok_s, "llm")

    def charge_tool(self) -> float:
        return self.advance(self.tool_s, "tool")
