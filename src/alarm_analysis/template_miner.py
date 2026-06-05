from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .base_models import Alarm
from .decisions import Classification
from .models import CandidateRule, TemplateObservation
from .normalize import clean_text


STATE_WORDS = (
    "出现",
    "消失",
    "产生",
    "恢复",
    "正常",
    "异常",
    "报警",
    "复归",
    "投入",
    "退出",
    "运行",
    "停止",
    "备用",
    "Off",
    "Normal",
    "Failure",
    "Alarm",
    "Recover",
)


@dataclass
class TemplateMiner:
    min_support: int = 3
    max_examples: int = 5
    observations: list[TemplateObservation] = field(default_factory=list)

    def observe(self, alarm: Alarm, classification: Classification) -> None:
        self.observations.append(
            TemplateObservation(
                template=alarm_template(alarm),
                signature=alarm_signature(alarm),
                row_index=alarm.row_index,
                type=classification.type,
                role=classification.role,
                source=classification.source,
                confidence=classification.confidence,
                rule_reason=classification.reason,
                alarm_device=alarm.alarm_device,
                content=alarm.content,
            )
        )

    def candidate_rules(self) -> list[CandidateRule]:
        by_signature: dict[str, list[TemplateObservation]] = defaultdict(list)
        for observation in self.observations:
            by_signature[observation.signature].append(observation)

        candidates: list[CandidateRule] = []
        for signature, items in by_signature.items():
            if len(items) < self.min_support:
                continue
            type_counts = Counter(item.type for item in items)
            role_counts = Counter(item.role for item in items)
            suggested_type, type_support = type_counts.most_common(1)[0]
            suggested_role, role_support = role_counts.most_common(1)[0]
            type_agreement = type_support / len(items)
            role_agreement = role_support / len(items)
            agreement = min(type_agreement, role_agreement)
            candidates.append(
                CandidateRule(
                    template=items[0].template,
                    signature=signature,
                    suggested_type=suggested_type,
                    suggested_role=suggested_role,
                    stage=_candidate_stage(items, agreement),
                    support=len(items),
                    agreement=round(agreement, 4),
                    type_agreement=round(type_agreement, 4),
                    role_agreement=round(role_agreement, 4),
                    avg_confidence=round(sum(item.confidence for item in items) / len(items), 4),
                    source_counts=dict(Counter(item.source for item in items)),
                    rule_reasons=dict(Counter(item.rule_reason for item in items).most_common(8)),
                    examples=[
                        {
                            "row_index": item.row_index,
                            "alarm_device": item.alarm_device,
                            "content": item.content,
                            "type": item.type,
                            "role": item.role,
                            "source": item.source,
                            "confidence": item.confidence,
                        }
                        for item in items[: self.max_examples]
                    ],
                )
            )
        return sorted(
            candidates,
            key=lambda item: (
                _stage_rank(item.stage),
                -item.support,
                item.template,
            ),
        )


def alarm_template(alarm: Alarm) -> str:
    return _normalize_alarm_text(alarm, collapse_state=True)


def alarm_signature(alarm: Alarm) -> str:
    return _normalize_alarm_text(alarm, collapse_state=False)


def _normalize_alarm_text(alarm: Alarm, *, collapse_state: bool) -> str:
    text = clean_text(f"{alarm.alarm_device}_{alarm.content}")
    text = re.sub(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?", "{time}", text)
    text = re.sub(r"\b\d+(?:\.\d+)?\s*(?:MW/min|MW/Min|MW|kV|A|V)\b", "{value}", text)
    text = re.sub(r"CT\d+", "CT{n}", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<![A-Za-z])\d+(?![A-Za-z])", "{n}", text)
    text = re.sub(r"[ABC]相", "{phase}相", text)
    text = re.sub(r"\b[A-Z]\d+(?:\.[A-Z]?\d+)*\b", "{code}", text)
    if collapse_state:
        for word in STATE_WORDS:
            text = text.replace(word, "{state}")
        text = re.sub(r"\{state\}(?:\s+\{state\})+", "{state}", text)
    text = re.sub(r"\{n\}(?:,\{n\})+", "{n_list}", text)
    return text


def _candidate_stage(items: list[TemplateObservation], agreement: float) -> str:
    if all(item.source == "fallback" for item in items):
        return "candidate"
    if agreement >= 0.98 and len(items) >= 5 and all(item.confidence >= 0.8 for item in items):
        return "strong"
    if agreement >= 0.8:
        return "weak"
    return "candidate"


def _stage_rank(stage: str) -> int:
    return {
        "candidate": 0,
        "weak": 1,
        "strong": 2,
        "deprecated": 3,
    }.get(stage, 4)
