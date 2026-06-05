from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from .base_models import AgentOutput


SemanticRole = Literal["start", "status", "end", "noise"]


class Classification(BaseModel):
    row_index: int
    type: str
    role: SemanticRole
    confidence: float
    source: Literal["rule", "knowledge", "llm", "fallback"]
    reason: str
    summary: str


class Decision(BaseModel):
    row_index: int
    tag: str
    type: str | None = None
    event_key: str | None = None
    semantic_role: SemanticRole | None = None
    gzlx: str | None = None
    sjms: str | None = None
    decision_source: str
    reason: str
    llm_output: AgentOutput | None = None
    classification: Classification | None = None
