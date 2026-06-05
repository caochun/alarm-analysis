from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


RuleStage = Literal["candidate", "weak", "strong", "deprecated"]
Role = Literal["start", "status", "end", "noise"]


class TemplateObservation(BaseModel):
    template: str
    signature: str
    row_index: int
    type: str
    role: Role
    source: str
    confidence: float
    rule_reason: str
    alarm_device: str
    content: str


class CandidateRule(BaseModel):
    template: str
    signature: str | None = None
    suggested_type: str
    suggested_role: Role
    stage: RuleStage
    support: int
    agreement: float
    type_agreement: float | None = None
    role_agreement: float | None = None
    avg_confidence: float
    source_counts: dict[str, int]
    rule_reasons: dict[str, int]
    examples: list[dict[str, object]]


class KnowledgeBase(BaseModel):
    version: int = 1
    rules: list[CandidateRule] = []


class AlarmExample(BaseModel):
    row_index: int
    time: str
    station: str
    host: str
    system_alarm: str
    suite: str
    alarm_device: str
    content: str
    level: str


class TemplateCluster(BaseModel):
    cluster_id: str
    template: str
    signature: str
    row_indexes: list[int]
    examples: list[AlarmExample]


class LabelInput(BaseModel):
    session_id: str
    batch_index: int
    known_type_catalog: list[str]
    clusters: list[TemplateCluster]


class LabelOutput(BaseModel):
    cluster_id: str
    type: str
    role: Role
    confidence: float = 0.75
    summary: str
