from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


Tag = Literal["S", "E", "R", "F", "N"]


class Alarm(BaseModel):
    row_index: int
    time: str
    station: str
    host: str
    system_alarm: str
    suite: str
    alarm_device: str
    content: str
    level: str
    alarm_key: str = Field(exclude=True)
    source_id: str = Field(default="", exclude=True)
    event_status: str = Field(default="", exclude=True)

    @property
    def dt(self) -> datetime:
        return datetime.fromisoformat(self.time)


class AgentOutput(BaseModel):
    row_index: int
    tag: Tag
    type: str | None = None
    gzlx: str | None = None
    sjms: str | None = None
    effective_time: str | None = None


TYPE_TO_GZLX = {
    "else": "其他告警信息",
}
