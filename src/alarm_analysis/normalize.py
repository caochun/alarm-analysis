from __future__ import annotations

ACTION_WORDS = ("出现", "消失", "产生", "恢复", "合", "分", "投入", "退出")


def clean_text(value: str | None) -> str:
    return " ".join((value or "").strip().split())


def normalize_content(content: str | None) -> str:
    value = clean_text(content)
    changed = True
    while changed:
        changed = False
        for word in ACTION_WORDS:
            if value.endswith(word):
                value = value[: -len(word)].strip()
                changed = True
    return value


def normalize_suite(value: str | None) -> str:
    value = clean_text(value)
    return "" if value in {"-", ""} else value


def iso_time(value: str) -> str:
    value = clean_text(value)
    return value.replace(" ", "T", 1)


def stable_alarm_key(
    *,
    pointid: str,
    station: str,
    host: str,
    alarm_device: str,
    content: str,
) -> str:
    pointid = clean_text(pointid)
    if pointid:
        return pointid
    parts = [station, host, alarm_device, normalize_content(content)]
    return "|".join(clean_text(part) for part in parts)
