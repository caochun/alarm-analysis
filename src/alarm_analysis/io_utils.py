from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from .base_models import AgentOutput
from .decisions import Decision


def write_jsonl(path: Path, item: object) -> None:
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(item, ensure_ascii=False, default=json_default))
        file.write("\n")


def decision_to_agent_output(decision: Decision) -> AgentOutput:
    return AgentOutput(
        row_index=decision.row_index,
        tag=decision.tag,  # type: ignore[arg-type]
        type=decision.type,
        gzlx=decision.gzlx,
        sjms=decision.sjms,
    )


def next_alarm_by_row(alarms: list[object]) -> dict[int, object]:
    return {
        alarm.row_index: alarms[index + 1]
        for index, alarm in enumerate(alarms[:-1])
    }


def json_default(value: object) -> object:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return str(value)
