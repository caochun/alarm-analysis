from __future__ import annotations

import csv
from pathlib import Path

from .base_models import Alarm
from .normalize import clean_text, iso_time, normalize_suite, stable_alarm_key


def load_alarms(path: Path) -> list[Alarm]:
    alarms: list[Alarm] = []
    with path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row_index, row in enumerate(reader, start=1):
            station = clean_text(row.get("stationname"))
            host = clean_text(row.get("systemid"))
            suite = normalize_suite(row.get("redundantsystem"))
            alarm_device = clean_text(row.get("equip_name")) or clean_text(row.get("type"))
            content = clean_text(row.get("content"))
            key = stable_alarm_key(
                pointid=row.get("pointid", ""),
                station=station,
                host=host,
                alarm_device=alarm_device,
                content=content,
            )
            alarms.append(
                Alarm(
                    row_index=row_index,
                    time=iso_time(row["time"]),
                    station=station,
                    host=host,
                    system_alarm=suite or clean_text(row.get("redundantsystem")),
                    suite=suite,
                    alarm_device=alarm_device,
                    content=content,
                    level=clean_text(row.get("level")),
                    alarm_key=key,
                    source_id=clean_text(row.get("id")),
                    event_status=clean_text(row.get("eventstatus")),
                )
            )
    return sorted(alarms, key=lambda alarm: (alarm.dt, alarm.row_index))


def batches(items: list[Alarm], batch_size: int) -> list[list[Alarm]]:
    return [items[index : index + batch_size] for index in range(0, len(items), batch_size)]
