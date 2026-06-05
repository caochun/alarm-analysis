from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .base_models import TYPE_TO_GZLX, AgentOutput, Alarm
from .decisions import Classification, Decision
from .keys import event_key


TYPE_THRESHOLDS_SECONDS = {
    "else": 10 * 60,
}


@dataclass
class ActiveEvent:
    event_key: str
    type: str
    start_time: datetime
    end_time: datetime
    row_indexes: list[int] = field(default_factory=list)


class EventStateMachine:
    def __init__(self) -> None:
        self.active: dict[str, ActiveEvent] = {}
        self.closed: list[ActiveEvent] = []

    def decide(
        self,
        alarm: Alarm,
        classification: Classification,
        *,
        llm_output: AgentOutput | None = None,
        next_alarm: Alarm | None = None,
    ) -> Decision:
        del next_alarm
        if classification.role == "noise":
            return Decision(
                row_index=alarm.row_index,
                tag="N",
                decision_source="state-machine",
                reason=classification.reason,
                llm_output=llm_output,
                classification=classification,
            )

        key = event_key(alarm, classification.type)
        now = alarm.dt
        active = self.active.get(key)
        within = active is not None and _elapsed_seconds(active.end_time, now) <= threshold_seconds(
            classification.type
        )

        if classification.role == "end":
            tag = "E" if within else "S"
            reason = "semantic end within active group" if within else "orphan end starts and closes group"
        elif within:
            tag = "R"
            reason = "same event key active within threshold"
        else:
            tag = "S"
            reason = "new event key or threshold exceeded"

        decision = Decision(
            row_index=alarm.row_index,
            tag=tag,
            type=classification.type,
            event_key=key,
            semantic_role=classification.role,
            gzlx=TYPE_TO_GZLX.get(classification.type) if tag == "S" else None,
            sjms=classification.summary,
            decision_source="state-machine",
            reason=reason,
            llm_output=llm_output,
            classification=classification,
        )
        self.apply_decision(alarm, decision)
        return decision

    def apply_decision(self, alarm: Alarm, decision: Decision) -> None:
        if not decision.type or not decision.event_key or decision.tag == "N":
            return
        now = alarm.dt
        if decision.tag == "S":
            old = self.active.pop(decision.event_key, None)
            if old:
                self.closed.append(old)
            self.active[decision.event_key] = ActiveEvent(
                event_key=decision.event_key,
                type=decision.type,
                start_time=now,
                end_time=now,
                row_indexes=[alarm.row_index],
            )
        elif decision.tag == "R":
            active = self.active.get(decision.event_key)
            if active:
                active.end_time = now
                active.row_indexes.append(alarm.row_index)
        elif decision.tag == "E":
            active = self.active.pop(decision.event_key, None)
            if active:
                active.end_time = now
                active.row_indexes.append(alarm.row_index)
                self.closed.append(active)
            else:
                self.closed.append(
                    ActiveEvent(
                        event_key=decision.event_key,
                        type=decision.type,
                        start_time=now,
                        end_time=now,
                        row_indexes=[alarm.row_index],
                    )
                )

    def finalize(self) -> list[ActiveEvent]:
        for active in list(self.active.values()):
            self.closed.append(active)
        self.active.clear()
        return self.closed


def threshold_seconds(event_type: str) -> int:
    return TYPE_THRESHOLDS_SECONDS.get(event_type, TYPE_THRESHOLDS_SECONDS["else"])


def _elapsed_seconds(previous: datetime, current: datetime) -> float:
    return abs((current - previous).total_seconds())
