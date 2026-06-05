from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .base_models import Alarm
from .csv_loader import load_alarms
from .template_miner import alarm_signature, alarm_template
from .type_aliases import TypeAliasMap, canonicalize_type, load_type_aliases, pair_base_from_signature


START_WORDS = (
    "出现",
    "产生",
    "投入",
    "合上",
    "合闸",
    "合位",
    "发出",
    "启动",
    "动作",
    "有效",
    "置位",
)
END_WORDS = (
    "消失",
    "恢复",
    "复归",
    "退出",
    "拉开",
    "分闸",
    "分位",
    "断开",
    "无效",
    "停止",
)
STATUS_WORDS = (
    "运行",
    "备用",
    "在远控位置",
    "远方控制",
    "就地控制",
    "状态合",
    "状态分",
    "开到位",
    "关到位",
)
def validate_run(
    *,
    csv_path: Path,
    audit_path: Path,
    output_path: Path,
    sample_limit: int = 200,
    type_alias_path: Path | None = None,
) -> dict[str, Any]:
    output_path.mkdir(parents=True, exist_ok=True)
    alarms = load_alarms(csv_path)
    alarms_by_row = {alarm.row_index: alarm for alarm in alarms}
    decisions = _load_decisions(audit_path)
    type_aliases = load_type_aliases(type_alias_path)

    role_rows = _role_silver_rows(alarms_by_row, decisions, type_aliases)
    pair_groups = _pair_groups(alarms_by_row, decisions, type_aliases)
    signature_groups = _signature_groups(alarms_by_row, decisions, type_aliases)
    level_noise = _level_noise_rows(alarms_by_row, decisions, type_aliases)
    orphan_end = [item for item in decisions if item.get("tag") == "E" and _classification_source(item) == "fallback"]

    role_total = len(role_rows)
    role_matches = sum(1 for item in role_rows if item["silver_role"] == item["predicted_role"])
    pair_total = len(pair_groups)
    pair_type_matches = sum(1 for item in pair_groups if item["type_count"] == 1)
    signature_total = len(signature_groups)
    signature_conflicts = [item for item in signature_groups if item["type_count"] > 1 or item["role_count"] > 1]

    review_items = _review_items(
        role_rows=role_rows,
        pair_groups=pair_groups,
        signature_conflicts=signature_conflicts,
        level_noise=level_noise,
        sample_limit=sample_limit,
    )

    summary = {
        "csv_path": str(csv_path),
        "audit_path": str(audit_path),
        "type_alias_path": str(type_alias_path) if type_alias_path else None,
        "type_alias_count": len(type_aliases),
        "decisions": len(decisions),
        "unique_signatures": signature_total,
        "role_silver_rows": role_total,
        "role_silver_matches": role_matches,
        "role_silver_accuracy": _ratio(role_matches, role_total),
        "pair_groups": pair_total,
        "pair_type_consistent": pair_type_matches,
        "pair_type_consistency": _ratio(pair_type_matches, pair_total),
        "signature_conflict_groups": len(signature_conflicts),
        "signature_conflict_rate": _ratio(len(signature_conflicts), signature_total),
        "level_1_2_noise_rows": len(level_noise),
        "fallback_orphan_end_rows": len(orphan_end),
        "source_counts": dict(Counter(_classification_source(item) for item in decisions)),
        "tag_counts": dict(Counter(item.get("tag") for item in decisions)),
        "role_counts": dict(Counter(_semantic_role(item) for item in decisions)),
        "type_counts_top20": dict(
            Counter(_event_type(item, type_aliases) for item in decisions).most_common(20)
        ),
        "review_items_path": str(output_path / "review_items.jsonl"),
        "pair_issues_path": str(output_path / "pair_issues.jsonl"),
        "signature_conflicts_path": str(output_path / "signature_conflicts.jsonl"),
    }

    _write_json(output_path / "summary.json", summary)
    _write_jsonl(output_path / "role_mismatches.jsonl", _role_mismatches(role_rows, sample_limit))
    _write_jsonl(output_path / "pair_issues.jsonl", _pair_issues(pair_groups))
    _write_jsonl(output_path / "signature_conflicts.jsonl", signature_conflicts[:sample_limit])
    _write_jsonl(output_path / "review_items.jsonl", review_items)
    return summary


def _load_decisions(path: Path) -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            item = json.loads(line)
            decisions.extend(item.get("decisions", []))
    return decisions


def _role_silver_rows(
    alarms_by_row: dict[int, Alarm],
    decisions: list[dict[str, Any]],
    type_aliases: TypeAliasMap,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for decision in decisions:
        alarm = alarms_by_row.get(int(decision["row_index"]))
        if alarm is None:
            continue
        silver = _silver_role(alarm)
        if silver is None:
            continue
        rows.append(
            {
                "row_index": alarm.row_index,
                "signature": alarm_signature(alarm),
                "content": alarm.content,
                "event_status": alarm.event_status,
                "silver_role": silver,
                "predicted_role": _semantic_role(decision),
                "predicted_type": _event_type(decision, type_aliases, scope=alarm_signature(alarm)),
                "source": _classification_source(decision),
                "level": alarm.level,
            }
        )
    return rows


def _silver_role(alarm: Alarm) -> str | None:
    text = f"{alarm.event_status} {alarm.content}"
    if alarm.event_status == "产生":
        return "start"
    if alarm.event_status == "消失":
        return "end"
    if _contains_any(text, START_WORDS) and not _contains_any(text, END_WORDS):
        return "start"
    if _contains_any(text, END_WORDS) and not _contains_any(text, START_WORDS):
        return "end"
    if _contains_any(text, STATUS_WORDS):
        return "status"
    return None


def _pair_groups(
    alarms_by_row: dict[int, Alarm],
    decisions: list[dict[str, Any]],
    type_aliases: TypeAliasMap,
) -> list[dict[str, Any]]:
    by_base: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for decision in decisions:
        alarm = alarms_by_row.get(int(decision["row_index"]))
        if alarm is None:
            continue
        role = _silver_role(alarm)
        if role not in {"start", "end"}:
            continue
        base = _pair_base(alarm)
        by_base[base].append(
            {
                "row_index": alarm.row_index,
                "signature": alarm_signature(alarm),
                "content": alarm.content,
                "role": role,
                "predicted_type": _event_type(decision, type_aliases, scope=base),
                "predicted_role": _semantic_role(decision),
                "source": _classification_source(decision),
            }
        )

    groups: list[dict[str, Any]] = []
    for base, items in by_base.items():
        roles = {item["role"] for item in items}
        if not {"start", "end"}.issubset(roles):
            continue
        type_counts = Counter(item["predicted_type"] for item in items)
        groups.append(
            {
                "base": base,
                "rows": len(items),
                "type_count": len(type_counts),
                "types": dict(type_counts),
                "examples": items[:8],
            }
        )
    return groups


def _signature_groups(
    alarms_by_row: dict[int, Alarm],
    decisions: list[dict[str, Any]],
    type_aliases: TypeAliasMap,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for decision in decisions:
        alarm = alarms_by_row.get(int(decision["row_index"]))
        if alarm is None:
            continue
        base = _pair_base(alarm)
        grouped[alarm_signature(alarm)].append(
            {
                "row_index": alarm.row_index,
                "content": alarm.content,
                "predicted_type": _event_type(decision, type_aliases, scope=base),
                "predicted_role": _semantic_role(decision),
                "source": _classification_source(decision),
            }
        )
    result: list[dict[str, Any]] = []
    for signature, items in grouped.items():
        type_counts = Counter(item["predicted_type"] for item in items)
        role_counts = Counter(item["predicted_role"] for item in items)
        result.append(
            {
                "signature": signature,
                "template": alarm_template(alarms_by_row[items[0]["row_index"]]),
                "rows": len(items),
                "type_count": len(type_counts),
                "role_count": len(role_counts),
                "types": dict(type_counts),
                "roles": dict(role_counts),
                "examples": items[:5],
            }
        )
    return result


def _level_noise_rows(
    alarms_by_row: dict[int, Alarm],
    decisions: list[dict[str, Any]],
    type_aliases: TypeAliasMap,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for decision in decisions:
        alarm = alarms_by_row.get(int(decision["row_index"]))
        if alarm is None:
            continue
        if alarm.level in {"1", "2"} and decision.get("tag") == "N":
            rows.append(
                {
                    "row_index": alarm.row_index,
                    "level": alarm.level,
                    "content": alarm.content,
                    "predicted_type": _event_type(decision, type_aliases, scope=alarm_signature(alarm)),
                    "predicted_role": _semantic_role(decision),
                    "source": _classification_source(decision),
                }
            )
    return rows


def _review_items(
    *,
    role_rows: list[dict[str, Any]],
    pair_groups: list[dict[str, Any]],
    signature_conflicts: list[dict[str, Any]],
    level_noise: list[dict[str, Any]],
    sample_limit: int,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for row in _role_mismatches(role_rows, sample_limit):
        items.append({"reason": "role_silver_mismatch", **row})
    for group in _pair_issues(pair_groups)[:sample_limit]:
        items.append({"reason": "start_end_pair_type_conflict", **group})
    for group in signature_conflicts[:sample_limit]:
        items.append({"reason": "signature_label_conflict", **group})
    for row in level_noise[:sample_limit]:
        items.append({"reason": "high_level_noise", **row})
    return items[:sample_limit]


def _role_mismatches(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    return [row for row in rows if row["silver_role"] != row["predicted_role"]][:limit]


def _pair_issues(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [group for group in groups if group["type_count"] > 1]


def _pair_base(alarm: Alarm) -> str:
    return pair_base_from_signature(alarm_signature(alarm))


def _event_type(
    decision: dict[str, Any],
    type_aliases: TypeAliasMap | None = None,
    *,
    scope: str | None = None,
) -> str:
    classification = decision.get("classification") or {}
    event_type = classification.get("type") or decision.get("type") or ""
    if type_aliases is None:
        return event_type
    return canonicalize_type(event_type, type_aliases, scope=scope)


def _semantic_role(decision: dict[str, Any]) -> str:
    classification = decision.get("classification") or {}
    return classification.get("role") or decision.get("semantic_role") or ""


def _classification_source(decision: dict[str, Any]) -> str:
    classification = decision.get("classification") or {}
    return classification.get("source") or ""


def _contains_any(text: str, words: tuple[str, ...]) -> bool:
    return any(word in text for word in words)


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 6)


def _write_json(path: Path, item: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, items: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for item in items:
            file.write(json.dumps(item, ensure_ascii=False))
            file.write("\n")
