from __future__ import annotations

from .base_models import Alarm
from .normalize import clean_text


def event_key(alarm: Alarm, event_type: str) -> str:
    return "|".join(
        clean_text(item)
        for item in (alarm.station, event_type, alarm.alarm_device or alarm.host)
        if clean_text(item)
    )
