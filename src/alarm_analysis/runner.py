from __future__ import annotations

import json
import time
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .base_models import Alarm
from .csv_loader import batches, load_alarms
from .decisions import Classification, Decision
from .io_utils import decision_to_agent_output, json_default, next_alarm_by_row, write_jsonl
from .knowledge_base import (
    known_type_catalog,
    load_knowledge_base,
    lookup_signature,
    write_candidates,
    write_knowledge_base,
)
from .llm_labeler import LlmLabeler
from .models import CandidateRule, AlarmExample, LabelInput, LabelOutput, TemplateCluster
from .state import EventStateMachine
from .template_miner import TemplateMiner, alarm_signature, alarm_template
from .type_aliases import OnlineTypeAliasLearner, load_type_aliases, pair_base_from_signature


def run_adaptive_batches(
    *,
    csv_path: Path,
    output_dir: Path,
    batch_size: int = 100,
    start_offset: int = 0,
    limit_batches: int | None = None,
    use_llm: bool = False,
    llama_client: LlmLabeler | None = None,
    min_template_support: int = 3,
    knowledge_base_path: Path | None = None,
    type_alias_path: Path | None = None,
    write_updated_kb: bool = False,
    max_examples_per_cluster: int = 3,
) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    session_id = uuid.uuid4().hex
    knowledge_base = load_knowledge_base(knowledge_base_path)
    loaded_type_aliases = load_type_aliases(type_alias_path)
    loaded_type_alias_count = len(loaded_type_aliases)
    type_catalog = known_type_catalog(knowledge_base)

    alarms = load_alarms(csv_path)
    if start_offset:
        alarms = alarms[start_offset:]
    alarm_batches = batches(alarms, batch_size)
    if limit_batches is not None:
        alarm_batches = alarm_batches[:limit_batches]
    all_batch_alarms = [alarm for batch in alarm_batches for alarm in batch]
    next_alarm_lookup = next_alarm_by_row(all_batch_alarms)

    state = EventStateMachine()
    miner = TemplateMiner(min_support=min_template_support)
    learned_by_signature: dict[str, LearnedLabel] = {}

    inputs_path = output_dir / f"{session_id}.inputs.jsonl"
    outputs_path = output_dir / f"{session_id}.outputs.jsonl"
    metrics_path = output_dir / f"{session_id}.metrics.jsonl"
    audit_path = output_dir / f"{session_id}.audit.jsonl"
    candidates_path = output_dir / f"{session_id}.candidates.json"
    kb_path = output_dir / f"{session_id}.knowledge.json"
    type_aliases_path = output_dir / f"{session_id}.type_aliases.json"

    processed = 0
    llm_cluster_count = 0
    fallback_cluster_count = 0
    batch_metrics: list[dict[str, object]] = []
    alias_learner = OnlineTypeAliasLearner(aliases=loaded_type_aliases)
    for batch_index, batch_alarms in enumerate(alarm_batches, start=1):
        started_at = datetime.now().isoformat(timespec="seconds")
        batch_start = time.perf_counter()

        clusters = _clusters_for_batch(
            batch_alarms,
            max_examples_per_cluster=max_examples_per_cluster,
        )
        labels_by_signature: dict[str, LearnedLabel] = {}
        unknown_clusters: list[TemplateCluster] = []

        for cluster in clusters:
            kb_rule = lookup_signature(knowledge_base, cluster.signature)
            if kb_rule is not None:
                labels_by_signature[cluster.signature] = LearnedLabel.from_candidate(kb_rule)
            elif cluster.signature in learned_by_signature:
                labels_by_signature[cluster.signature] = learned_by_signature[cluster.signature]
            else:
                unknown_clusters.append(cluster)

        llm_labels: dict[str, LearnedLabel] = {}
        used_llm = False
        if use_llm and unknown_clusters:
            if llama_client is None:
                raise ValueError("llama_client is required when use_llm=True")
            label_input = LabelInput(
                session_id=session_id,
                batch_index=batch_index,
                known_type_catalog=type_catalog,
                clusters=unknown_clusters,
            )
            write_jsonl(inputs_path, label_input.model_dump())
            raw_labels = llama_client.label(label_input)
            llm_labels = _labels_from_llm(unknown_clusters, raw_labels)
            labels_by_signature.update(llm_labels)
            learned_by_signature.update(llm_labels)
            type_catalog = sorted(set(type_catalog) | {label.type for label in llm_labels.values()})
            used_llm = True
        else:
            fallback_labels = {
                cluster.signature: LearnedLabel(
                    type="else",
                    role="status",
                    confidence=0.2,
                    source="fallback",
                    reason="unlabeled template; run with --llm to learn type/role",
                    summary="未学习模板，暂按其他告警状态处理",
                )
                for cluster in unknown_clusters
            }
            labels_by_signature.update(fallback_labels)
            learned_by_signature.update(fallback_labels)
            fallback_cluster_count += len(fallback_labels)
            write_jsonl(
                inputs_path,
                {
                    "session_id": session_id,
                    "batch_index": batch_index,
                    "mode": "knowledge-only" if use_llm else "no-llm-fallback",
                    "clusters": [cluster.model_dump() for cluster in clusters],
                    "unknown_cluster_count": len(unknown_clusters),
                },
            )

        decisions: list[Decision] = []
        for alarm in batch_alarms:
            signature = alarm_signature(alarm)
            label = labels_by_signature[signature]
            classification = label.to_classification(alarm.row_index)
            base = pair_base_from_signature(signature)
            aliased_type = alias_learner.observe(
                base=base,
                event_type=classification.type,
                role=classification.role,
                row_index=alarm.row_index,
                content=alarm.content,
            )
            if aliased_type != classification.type:
                classification = classification.model_copy(
                    update={
                        "type": aliased_type,
                        "reason": f"{classification.reason}; online scoped alias applied",
                    }
                )
            miner.observe(alarm, classification)
            decisions.append(
                state.decide(
                    alarm,
                    classification,
                    next_alarm=next_alarm_lookup.get(alarm.row_index),
                )
            )

        elapsed_seconds = round(time.perf_counter() - batch_start, 3)
        llm_cluster_count += len(llm_labels)
        metric = {
            "session_id": session_id,
            "batch_index": batch_index,
            "rows": len(batch_alarms),
            "clusters": len(clusters),
            "unknown_clusters": len(unknown_clusters),
            "llm_labeled_clusters": len(llm_labels),
            "mode": "llm-template-labeling"
            if used_llm
            else ("knowledge-only" if use_llm else "no-llm-fallback"),
            "started_at": started_at,
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "elapsed_seconds": elapsed_seconds,
            "online_scoped_aliases": sum(
                len(item) for item in alias_learner.aliases.scoped_aliases.values()
            ),
            "online_scoped_alias_bases": len(alias_learner.aliases.scoped_aliases),
        }
        batch_metrics.append(metric)
        write_jsonl(metrics_path, metric)
        write_jsonl(
            outputs_path,
            {
                "session_id": session_id,
                "batch_index": batch_index,
                "metrics": metric,
                "outputs": [
                    decision_to_agent_output(decision).model_dump(exclude_none=True)
                    for decision in decisions
                ],
            },
        )
        write_jsonl(
            audit_path,
            {
                "session_id": session_id,
                "batch_index": batch_index,
                "decisions": [decision.model_dump(exclude_none=True) for decision in decisions],
            },
        )
        processed += len(batch_alarms)

    closed = state.finalize()
    candidates = miner.candidate_rules()
    write_candidates(candidates_path, candidates)
    if write_updated_kb:
        write_knowledge_base(kb_path, candidates)
    type_aliases_path.write_text(
        json.dumps(alias_learner.snapshot(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    summary = {
        "session_id": session_id,
        "processed": processed,
        "batch_size": batch_size,
        "start_offset": start_offset,
        "batches": len(alarm_batches),
        "inputs_path": str(inputs_path),
        "outputs_path": str(outputs_path),
        "metrics_path": str(metrics_path),
        "audit_path": str(audit_path),
        "candidates_path": str(candidates_path),
        "knowledge_base_path": str(kb_path) if write_updated_kb else None,
        "type_aliases_path": str(type_aliases_path),
        "loaded_knowledge_rules": len(knowledge_base.rules),
        "loaded_type_aliases": loaded_type_alias_count,
        "session_learned_signatures": len(learned_by_signature),
        "llm_labeled_clusters": llm_cluster_count,
        "fallback_clusters": fallback_cluster_count,
        "candidate_rules": len(candidates),
        "candidate_stage_counts": _stage_counts(candidates),
        "closed_groups": len(closed),
        "active_groups": len(state.active),
        "online_scoped_aliases": sum(
            len(item) for item in alias_learner.aliases.scoped_aliases.values()
        ),
        "online_scoped_alias_bases": len(alias_learner.aliases.scoped_aliases),
        "batch_metrics": batch_metrics,
    }
    (output_dir / f"{session_id}.summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=json_default),
        encoding="utf-8",
    )
    return summary


class LearnedLabel:
    def __init__(
        self,
        *,
        type: str,
        role: str,
        confidence: float,
        source: str,
        reason: str,
        summary: str,
    ) -> None:
        self.type = type
        self.role = role
        self.confidence = confidence
        self.source = source
        self.reason = reason
        self.summary = summary

    @classmethod
    def from_candidate(cls, rule: CandidateRule) -> LearnedLabel:
        return cls(
            type=rule.suggested_type,
            role=rule.suggested_role,
            confidence=max(0.75, min(0.99, rule.avg_confidence)),
            source="knowledge",
            reason=f"knowledge base matched signature; stage={rule.stage}; support={rule.support}",
            summary=_default_summary(rule.suggested_type, rule.suggested_role),
        )

    @classmethod
    def from_llm(cls, output: LabelOutput) -> LearnedLabel:
        return cls(
            type=output.type,
            role=output.role,
            confidence=max(0.0, min(1.0, output.confidence)),
            source="llm",
            reason="llm labeled template cluster",
            summary=output.summary,
        )

    def to_classification(self, row_index: int) -> Classification:
        return Classification(
            row_index=row_index,
            type=self.type,
            role=self.role,  # type: ignore[arg-type]
            confidence=self.confidence,
            source=self.source,  # type: ignore[arg-type]
            reason=self.reason,
            summary=self.summary,
        )


def _clusters_for_batch(
    batch_alarms: list[Alarm],
    *,
    max_examples_per_cluster: int,
) -> list[TemplateCluster]:
    by_signature: dict[str, list[Alarm]] = defaultdict(list)
    for alarm in batch_alarms:
        by_signature[alarm_signature(alarm)].append(alarm)
    clusters: list[TemplateCluster] = []
    for index, (signature, items) in enumerate(sorted(by_signature.items()), start=1):
        clusters.append(
            TemplateCluster(
                cluster_id=f"c{index}",
                template=alarm_template(items[0]),
                signature=signature,
                row_indexes=[item.row_index for item in items],
                examples=[
                    AlarmExample(
                        row_index=item.row_index,
                        time=item.time,
                        station=item.station,
                        host=item.host,
                        system_alarm=item.system_alarm,
                        suite=item.suite,
                        alarm_device=item.alarm_device,
                        content=item.content,
                        level=item.level,
                    )
                    for item in items[:max_examples_per_cluster]
                ],
            )
        )
    return clusters


def _labels_from_llm(
    clusters: list[TemplateCluster],
    outputs: list[LabelOutput],
) -> dict[str, LearnedLabel]:
    cluster_by_id = {cluster.cluster_id: cluster for cluster in clusters}
    output_by_id = {output.cluster_id: output for output in outputs}
    labels: dict[str, LearnedLabel] = {}
    for cluster in clusters:
        output = output_by_id.get(cluster.cluster_id)
        if output is None:
            labels[cluster.signature] = LearnedLabel(
                type="else",
                role="status",
                confidence=0.2,
                source="fallback",
                reason="llm omitted cluster label",
                summary="模型未返回该模板簇，暂按其他告警状态处理",
            )
            continue
        if output.cluster_id not in cluster_by_id:
            continue
        labels[cluster.signature] = LearnedLabel.from_llm(output)
    return labels


def _default_summary(event_type: str, role: str) -> str:
    return f"{event_type} 模板语义角色为 {role}"


def _stage_counts(candidates: list[object]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for candidate in candidates:
        stage = getattr(candidate, "stage")
        counts[stage] = counts.get(stage, 0) + 1
    return counts
