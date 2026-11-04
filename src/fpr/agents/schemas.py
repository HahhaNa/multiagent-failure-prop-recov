"""Structured outputs exchanged between agents."""

from typing import Any

from pydantic import BaseModel, Field


class Claim(BaseModel):
    claim_id: str
    text: str
    value: str
    source_tool_call_ids: list[str] = Field(default_factory=list)


class Findings(BaseModel):
    claims: list[Claim] = Field(default_factory=list)


class PlanStep(BaseModel):
    step_id: str
    action: str
    args: dict[str, Any] = Field(default_factory=dict)
    cites: list[str] = Field(default_factory=list)


class Plan(BaseModel):
    steps: list[PlanStep] = Field(default_factory=list)


UNAVAILABLE = {"status": "unavailable"}


def json_schema_format(model: type[BaseModel], name: str) -> dict:
    return {"type": "json_schema", "json_schema": {"name": name, "schema": model.model_json_schema(), "strict": True}}
