from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


MERGE_ACTIONS = {"merge_type", "fix_type_and_role"}
DIRECTIONAL_SUFFIX_ALIASES = (
    ("-run", "-operation"),
    ("-stop", "-operation"),
    ("-start", "-operation"),
    ("-end", "-operation"),
)
PAIR_STATE_REPLACEMENTS = (
    ("出现", "{state}"),
    ("产生", "{state}"),
    ("消失", "{state}"),
    ("投入", "{state}"),
    ("退出", "{state}"),
    ("合上", "{state}"),
    ("拉开", "{state}"),
    ("合闸", "{state}"),
    ("分闸", "{state}"),
    ("合位", "{state}"),
    ("分位", "{state}"),
    ("有效", "{state}"),
    ("无效", "{state}"),
    ("动作", "{state}"),
    ("复归", "{state}"),
)


class TypeAliasMap:
    def __init__(
        self,
        *,
        global_aliases: dict[str, str] | None = None,
        scoped_aliases: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self.global_aliases = global_aliases or {}
        self.scoped_aliases = scoped_aliases or {}

    def __len__(self) -> int:
        return len(self.global_aliases) + sum(len(item) for item in self.scoped_aliases.values())


class OnlineTypeAliasLearner:
    def __init__(
        self,
        *,
        min_support: int = 2,
        min_structural_support: int = 4,
        aliases: TypeAliasMap | None = None,
    ) -> None:
        self.min_support = min_support
        self.min_structural_support = min_structural_support
        self.aliases = aliases or TypeAliasMap()
        self.base_type_counts: dict[str, Counter[str]] = defaultdict(Counter)
        self.base_role_counts: dict[str, Counter[str]] = defaultdict(Counter)
        self.base_type_role_counts: dict[str, dict[str, Counter[str]]] = defaultdict(
            lambda: defaultdict(Counter)
        )
        self.evidence: list[dict[str, Any]] = []

    def canonicalize(self, event_type: str, *, base: str | None) -> str:
        return canonicalize_type(event_type, self.aliases, scope=base)

    def observe(
        self,
        *,
        base: str,
        event_type: str,
        role: str,
        row_index: int,
        content: str,
    ) -> str:
        current_type = self.canonicalize(event_type, base=base)
        self.base_type_counts[base][current_type] += 1
        self.base_role_counts[base][role] += 1
        self.base_type_role_counts[base][current_type][role] += 1
        learned = self._maybe_learn(base, row_index=row_index, content=content)
        if learned:
            current_type = self.canonicalize(current_type, base=base)
        return current_type

    def snapshot(self) -> dict[str, Any]:
        return {
            "version": 2,
            "global_aliases": self.aliases.global_aliases,
            "scoped_aliases": self.aliases.scoped_aliases,
            "evidence": self.evidence,
            "summary": {
                "global_alias_count": len(self.aliases.global_aliases),
                "scoped_alias_count": sum(
                    len(item) for item in self.aliases.scoped_aliases.values()
                ),
                "scoped_base_count": len(self.aliases.scoped_aliases),
                "evidence_count": len(self.evidence),
            },
        }

    def _maybe_learn(self, base: str, *, row_index: int, content: str) -> bool:
        role_counts = self.base_role_counts[base]
        if not role_counts["start"] or not role_counts["end"]:
            return False
        type_counts = self.base_type_counts[base]
        if sum(type_counts.values()) < self.min_support or len(type_counts) < 2:
            return False
        types = list(type_counts)
        type_role_counts = self.base_type_role_counts[base]
        canonical = _choose_scoped_canonical(types, dict(type_counts))
        if canonical is None:
            return False
        aliases = {
            event_type: canonical
            for event_type in types
            if event_type != canonical
            and (
                _can_scope_alias(event_type, canonical)
                or _has_structural_pair_evidence(
                    source=event_type,
                    target=canonical,
                    type_counts=type_counts,
                    type_role_counts=type_role_counts,
                    min_support=self.min_structural_support,
                )
            )
        }
        if not aliases:
            return False
        existing = self.aliases.scoped_aliases.setdefault(base, {})
        new_aliases = {key: value for key, value in aliases.items() if existing.get(key) != value}
        if not new_aliases:
            return False
        existing.update(new_aliases)
        self.evidence.append(
            {
                "base": base,
                "canonical_type": canonical,
                "aliases": new_aliases,
                "types": dict(type_counts),
                "roles": dict(role_counts),
                "type_roles": {
                    event_type: dict(counter)
                    for event_type, counter in type_role_counts.items()
                    if event_type in types
                },
                "row_index": row_index,
                "content": content,
            }
        )
        self._collapse_base_counts(base, canonical, new_aliases)
        return True

    def _collapse_base_counts(
        self,
        base: str,
        canonical: str,
        aliases: dict[str, str],
    ) -> None:
        type_counts = self.base_type_counts[base]
        type_role_counts = self.base_type_role_counts[base]
        for source in aliases:
            if source == canonical:
                continue
            type_counts[canonical] += type_counts.pop(source, 0)
            source_roles = type_role_counts.pop(source, Counter())
            type_role_counts[canonical].update(source_roles)


def load_type_aliases(path: Path | None) -> TypeAliasMap:
    if path is None or not path.exists():
        return TypeAliasMap()
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and data.get("version") == 2:
        global_aliases = {
            str(key): str(value)
            for key, value in (data.get("global_aliases") or {}).items()
        }
        scoped_aliases = {
            str(base): {str(key): str(value) for key, value in aliases.items()}
            for base, aliases in (data.get("scoped_aliases") or {}).items()
            if isinstance(aliases, dict)
        }
        return TypeAliasMap(global_aliases=global_aliases, scoped_aliases=scoped_aliases)
    raise ValueError(f"unsupported type alias file: {path}")


def canonicalize_type(
    event_type: str,
    aliases: dict[str, str] | TypeAliasMap,
    *,
    scope: str | None = None,
) -> str:
    if isinstance(aliases, TypeAliasMap):
        scoped = aliases.scoped_aliases.get(scope or "", {})
        current = _canonicalize_type(event_type, scoped)
        return _canonicalize_type(current, aliases.global_aliases)
    return _canonicalize_type(event_type, aliases)


def pair_base_from_signature(signature: str) -> str:
    text = signature
    for source, target in PAIR_STATE_REPLACEMENTS:
        text = text.replace(source, target)
    while "{state} {state}" in text:
        text = text.replace("{state} {state}", "{state}")
    return text


def normalize_type_name(event_type: str) -> str:
    for source, target in DIRECTIONAL_SUFFIX_ALIASES:
        if event_type.endswith(source):
            return event_type[: -len(source)] + target
    return event_type


def write_type_aliases(
    path: Path,
    *,
    global_aliases: dict[str, str],
    evidence: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
    source_paths: list[Path],
    min_confidence: float,
    scoped_aliases: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    scoped_aliases = scoped_aliases or {}
    payload = {
        "version": 2,
        "min_confidence": min_confidence,
        "source_paths": [str(item) for item in source_paths],
        "global_aliases": dict(sorted(global_aliases.items())),
        "scoped_aliases": {
            base: dict(sorted(items.items())) for base, items in sorted(scoped_aliases.items())
        },
        "evidence": evidence,
        "conflicts": conflicts,
        "summary": {
            "global_alias_count": len(global_aliases),
            "scoped_alias_count": sum(len(item) for item in scoped_aliases.values()),
            "scoped_base_count": len(scoped_aliases),
            "evidence_count": len(evidence),
            "conflict_count": len(conflicts),
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload["summary"]


def build_scoped_type_aliases_from_pair_issues(
    pair_issue_paths: list[Path],
) -> tuple[dict[str, dict[str, str]], list[dict[str, Any]]]:
    scoped_aliases: dict[str, dict[str, str]] = {}
    evidence: list[dict[str, Any]] = []
    for path in pair_issue_paths:
        for row in _iter_jsonl(path):
            base = str(row.get("base") or "")
            raw_types = row.get("types")
            if not base or not isinstance(raw_types, dict):
                continue
            types = [str(item) for item in raw_types if item]
            canonical = _choose_scoped_canonical(types, raw_types)
            if canonical is None:
                continue
            aliases = {
                event_type: canonical
                for event_type in types
                if event_type != canonical and _can_scope_alias(event_type, canonical)
            }
            if not aliases:
                continue
            scoped_aliases.setdefault(base, {}).update(aliases)
            evidence.append(
                {
                    "base": base,
                    "canonical_type": canonical,
                    "aliases": aliases,
                    "types": raw_types,
                    "rows": row.get("rows"),
                }
            )
    return scoped_aliases, evidence


def _canonicalize_type(event_type: str, aliases: dict[str, str]) -> str:
    current = event_type
    seen: set[str] = set()
    while current in aliases and current not in seen:
        seen.add(current)
        current = aliases[current]
    return current


def build_type_aliases_from_judges(
    judge_paths: list[Path],
    *,
    min_confidence: float = 0.9,
    existing_aliases: TypeAliasMap | None = None,
) -> tuple[dict[str, str], list[dict[str, Any]], list[dict[str, Any]]]:
    votes: dict[str, Counter[str]] = defaultdict(Counter)
    vote_examples: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    evidence: list[dict[str, Any]] = []
    aliases = dict(existing_aliases.global_aliases if existing_aliases else {})

    for path in judge_paths:
        for row in _iter_jsonl(path):
            action = row.get("action")
            canonical_type = row.get("canonical_type")
            confidence = float(row.get("confidence") or 0.0)
            if action not in MERGE_ACTIONS or not canonical_type or confidence < min_confidence:
                continue
            source_issue = row.get("source_issue") or {}
            source_types = _source_types(source_issue)
            if not source_types:
                continue
            canonical_type = canonicalize_type(str(canonical_type), aliases)
            for source_type in source_types:
                source_type = canonicalize_type(source_type, aliases)
                if source_type == canonical_type:
                    continue
                votes[source_type][canonical_type] += 1
                vote_examples[(source_type, canonical_type)].append(
                    {
                        "judge_path": str(path),
                        "issue_id": row.get("issue_id"),
                        "action": action,
                        "confidence": confidence,
                        "reason": row.get("reason"),
                        "base": source_issue.get("base"),
                        "source_types": source_issue.get("types"),
                    }
                )

    conflicts: list[dict[str, Any]] = []
    for source_type, counter in sorted(votes.items()):
        ranked = counter.most_common()
        target, support = ranked[0]
        if len(ranked) > 1 and ranked[1][1] == support:
            conflicts.append(
                {
                    "source_type": source_type,
                    "votes": dict(counter),
                    "reason": "tie between canonical type votes",
                }
            )
            continue
        aliases[source_type] = target
        examples = vote_examples[(source_type, target)]
        evidence.append(
            {
                "source_type": source_type,
                "canonical_type": target,
                "support": support,
                "avg_confidence": round(
                    sum(float(item["confidence"]) for item in examples) / len(examples), 4
                ),
                "examples": examples[:5],
            }
        )

    aliases = _flatten_aliases(aliases)
    return aliases, evidence, conflicts


def _flatten_aliases(aliases: dict[str, str]) -> dict[str, str]:
    return {
        source: target
        for source, target in (
            (source, canonicalize_type(target, aliases)) for source, target in aliases.items()
        )
        if source != target
    }


def _source_types(source_issue: dict[str, Any]) -> list[str]:
    raw_types = source_issue.get("types")
    if isinstance(raw_types, dict):
        return [str(item) for item in raw_types if item]
    examples = source_issue.get("examples")
    if isinstance(examples, list):
        return sorted(
            {
                str(example.get("predicted_type"))
                for example in examples
                if isinstance(example, dict) and example.get("predicted_type")
            }
        )
    return []


def _choose_scoped_canonical(types: list[str], counts: dict[str, Any]) -> str | None:
    normalized = {event_type: normalize_type_name(event_type) for event_type in types}
    if len(set(normalized.values())) == 1:
        return next(iter(normalized.values()))
    ranked = sorted(
        types,
        key=lambda item: (
            _canonical_name_score(item),
            -len(item),
            int(counts.get(item) or 0),
        ),
        reverse=True,
    )
    best = ranked[0] if ranked else None
    return best


def _can_scope_alias(source: str, target: str) -> bool:
    if normalize_type_name(source) == normalize_type_name(target):
        return True
    source_parts = set(source.split("-"))
    target_parts = set(target.split("-"))
    overlap = source_parts & target_parts
    return len(overlap) >= 2 and (
        bool({"run", "stop", "start", "end", "open", "close", "closed"} & source_parts)
        or bool({"run", "stop", "start", "end", "open", "close", "closed"} & target_parts)
    )


def _has_structural_pair_evidence(
    *,
    source: str,
    target: str,
    type_counts: Counter[str],
    type_role_counts: dict[str, Counter[str]],
    min_support: int,
) -> bool:
    if _looks_mutually_exclusive_types(source, target):
        return False
    if type_counts[source] + type_counts[target] < min_support:
        return False
    source_role = _dominant_lifecycle_role(type_role_counts[source])
    target_role = _dominant_lifecycle_role(type_role_counts[target])
    if source_role is None or target_role is None or source_role == target_role:
        return False
    source_parts = set(source.split("-"))
    target_parts = set(target.split("-"))
    if source_parts & target_parts:
        return True
    source_tail = source.rsplit("-", 1)[-1]
    target_tail = target.rsplit("-", 1)[-1]
    if source_tail == target_tail:
        return True
    return _is_generic_event_category(source_tail) or _is_generic_event_category(target_tail)


def _dominant_lifecycle_role(role_counts: Counter[str]) -> str | None:
    total = sum(role_counts.values())
    if total <= 0:
        return None
    role, support = role_counts.most_common(1)[0]
    if role not in {"start", "end"}:
        return None
    if support / total < 0.75:
        return None
    return role


def _looks_mutually_exclusive_types(source: str, target: str) -> bool:
    source_parts = set(source.split("-"))
    target_parts = set(target.split("-"))
    exclusive_pairs = (
        ("open", "close"),
        ("open", "closed"),
        ("on", "off"),
        ("enable", "disable"),
        ("lock", "unlock"),
        ("block", "deblock"),
    )
    return any(
        (left in source_parts and right in target_parts)
        or (right in source_parts and left in target_parts)
        for left, right in exclusive_pairs
    )


def _is_generic_event_category(part: str) -> bool:
    return part in {
        "operation",
        "status",
        "command",
        "fault",
        "alarm",
        "abnormal",
        "trigger",
        "protection",
    }


def _canonical_name_score(event_type: str) -> int:
    score = 0
    if event_type.endswith(("-operation", "-status", "-fault", "-alarm", "-command", "-trigger", "-abnormal")):
        score += 4
    if event_type.endswith(("-run", "-stop", "-start", "-end")):
        score -= 3
    if event_type.endswith(("-open", "-close", "-closed")):
        score -= 1
    return score


def _iter_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as file:
        for line in file:
            if line.strip():
                rows.append(json.loads(line))
    return rows
