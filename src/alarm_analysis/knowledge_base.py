from __future__ import annotations

import json
from pathlib import Path

from .models import CandidateRule, KnowledgeBase
from .type_aliases import normalize_type_name


def load_knowledge_base(path: Path | None) -> KnowledgeBase:
    if path is None or not path.exists():
        return KnowledgeBase()
    return KnowledgeBase.model_validate_json(path.read_text(encoding="utf-8"))


def lookup_signature(kb: KnowledgeBase, signature: str) -> CandidateRule | None:
    matches = [
        rule
        for rule in kb.rules
        if rule.signature == signature and rule.stage in {"weak", "strong"}
    ]
    if not matches:
        return None
    return sorted(matches, key=lambda item: (_stage_rank(item.stage), item.support), reverse=True)[0]


def known_type_catalog(kb: KnowledgeBase) -> list[str]:
    types = {
        normalize_type_name(rule.suggested_type)
        for rule in kb.rules
        if rule.stage != "deprecated"
    }
    return sorted(types)


def write_knowledge_base(path: Path, rules: list[CandidateRule]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    kb = KnowledgeBase(rules=rules)
    path.write_text(kb.model_dump_json(indent=2), encoding="utf-8")


def write_candidates(path: Path, rules: list[CandidateRule]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([rule.model_dump() for rule in rules], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _stage_rank(stage: str) -> int:
    return {"candidate": 0, "weak": 1, "strong": 2}.get(stage, -1)
