from pathlib import Path

from alarm_analysis.base_models import Alarm
from alarm_analysis.decisions import Classification
from alarm_analysis.models import LabelInput, LabelOutput
from alarm_analysis.runner import run_adaptive_batches
from alarm_analysis.template_miner import TemplateMiner, alarm_template
from alarm_analysis.type_aliases import (
    OnlineTypeAliasLearner,
    build_type_aliases_from_judges,
    canonicalize_type,
    load_type_aliases,
    write_type_aliases,
)


def _alarm(row_index: int, device: str, content: str) -> Alarm:
    return Alarm(
        row_index=row_index,
        time=f"2026-04-13T18:00:{row_index:02d}",
        station="TestStation",
        host="HOST",
        system_alarm="A",
        suite="A",
        alarm_device=device,
        content=content,
        level="3",
        alarm_key=str(row_index),
    )


def test_alarm_template_normalizes_numbers_and_states() -> None:
    template = alarm_template(
        _alarm(1, "DeviceA", "DeviceA_SensorCT2异常出现 出现")
    )

    assert "CT{n}" in template
    assert "{state}" in template


def test_template_miner_proposes_candidate_rules() -> None:
    miner = TemplateMiner(min_support=2)
    alarms = [
        _alarm(1, "DeviceA", "DeviceA_SensorCT2异常出现 出现"),
        _alarm(2, "DeviceA", "DeviceA_SensorCT3异常出现 出现"),
        _alarm(3, "DeviceA", "DeviceA_SensorCT2异常消失 消失"),
        _alarm(4, "DeviceA", "DeviceA_SensorCT3异常消失 消失"),
    ]
    for alarm in alarms:
        role = "start" if "出现" in alarm.content else "end"
        miner.observe(
            alarm,
            Classification(
                row_index=alarm.row_index,
                type="learned-measurement-error",
                role=role,
                confidence=0.9,
                source="llm",
                reason="test label",
                summary="传感通道异常",
            ),
        )

    candidates = miner.candidate_rules()

    assert candidates
    assert {candidate.suggested_role for candidate in candidates} == {"start", "end"}


class FakeLabeler:
    def __init__(self) -> None:
        self.inputs: list[LabelInput] = []

    def label(self, label_input: LabelInput) -> list[LabelOutput]:
        self.inputs.append(label_input)
        outputs: list[LabelOutput] = []
        for cluster in label_input.clusters:
            role = "end" if "消失" in cluster.signature else "start"
            outputs.append(
                LabelOutput(
                    cluster_id=cluster.cluster_id,
                    type="learned-measurement-error",
                    role=role,
                    confidence=0.92,
                    summary="传感通道异常",
                )
            )
        return outputs


def test_runner_writes_candidates(tmp_path: Path) -> None:
    csv_path = tmp_path / "alarms.csv"
    csv_path.write_text(
        "\n".join(
                [
                    "stationname,systemid,redundantsystem,equip_name,type,content,level,time,pointid,id,eventstatus",
                    "TestStation,KMU,-,DeviceA,,DeviceA_SensorCT2异常出现 出现,3,2026-04-13 18:00:01,p1,1,产生",
                    "TestStation,KMU,-,DeviceA,,DeviceA_SensorCT3异常出现 出现,3,2026-04-13 18:00:02,p2,2,产生",
                    "TestStation,KMU,-,DeviceA,,DeviceA_SensorCT2异常消失 消失,3,2026-04-13 18:00:03,p3,3,消失",
                    "TestStation,KMU,-,DeviceA,,DeviceA_SensorCT3异常消失 消失,3,2026-04-13 18:00:04,p4,4,消失",
                ]
            ),
        encoding="utf-8",
    )

    summary = run_adaptive_batches(
        csv_path=csv_path,
        output_dir=tmp_path / "out",
        min_template_support=2,
        write_updated_kb=True,
        use_llm=True,
        llama_client=FakeLabeler(),  # type: ignore[arg-type]
    )

    assert Path(summary["candidates_path"]).exists()
    assert summary["candidate_rules"] >= 1
    assert Path(summary["knowledge_base_path"]).exists()
    assert summary["llm_labeled_clusters"] == 2
    assert Path(summary["type_aliases_path"]).exists()


def test_type_alias_learning_from_pair_judges(tmp_path: Path) -> None:
    judge_path = tmp_path / "judge.jsonl"
    judge_path.write_text(
        "\n".join(
            [
                '{"issue_id":"i1","action":"merge_type","canonical_type":"cooling-fan-operation","confidence":0.96,"source_issue":{"types":{"cooling-fan-run":3,"cooling-fan-stop":2},"base":"fan {state}"}}',
                '{"issue_id":"i2","action":"keep_types","canonical_type":null,"confidence":0.98,"source_issue":{"types":{"a":1,"b":1}}}',
            ]
        ),
        encoding="utf-8",
    )

    aliases, evidence, conflicts = build_type_aliases_from_judges([judge_path])

    assert aliases == {
        "cooling-fan-run": "cooling-fan-operation",
        "cooling-fan-stop": "cooling-fan-operation",
    }
    assert evidence
    assert conflicts == []


def test_type_aliases_can_be_written_and_chained(tmp_path: Path) -> None:
    output_path = tmp_path / "aliases.json"
    write_type_aliases(
        output_path,
        global_aliases={"cooling-fan-run": "cooling-fan-operation", "fan-op": "cooling-fan-run"},
        evidence=[],
        conflicts=[],
        source_paths=[],
        min_confidence=0.9,
    )

    assert output_path.exists()
    assert (
        canonicalize_type(
            "fan-op",
            {"cooling-fan-run": "cooling-fan-operation", "fan-op": "cooling-fan-run"},
        )
        == "cooling-fan-operation"
    )


def test_type_aliases_support_scoped_aliases(tmp_path: Path) -> None:
    output_path = tmp_path / "aliases.json"
    write_type_aliases(
        output_path,
        global_aliases={},
        scoped_aliases={"fan {state}": {"cooling-fan-stop": "cooling-fan-operation"}},
        evidence=[],
        conflicts=[],
        source_paths=[],
        min_confidence=1.0,
    )
    aliases = load_type_aliases(output_path)

    assert canonicalize_type("cooling-fan-stop", aliases, scope="fan {state}") == "cooling-fan-operation"
    assert canonicalize_type("cooling-fan-stop", aliases, scope="other") == "cooling-fan-stop"


def test_online_alias_learns_structural_near_synonyms() -> None:
    learner = OnlineTypeAliasLearner(min_structural_support=4)
    base = "DeviceB_启动处理泵P9{n} {state}"

    for row_index in range(1, 4):
        learner.observe(
            base=base,
            event_type="chemical-pump-operation",
            role="start",
            row_index=row_index,
            content="启动处理泵P91 出现",
        )
    aliased = learner.observe(
        base=base,
        event_type="pump-operation",
        role="end",
        row_index=4,
        content="启动处理泵P91 消失",
    )

    assert aliased == "pump-operation"
    assert learner.canonicalize("chemical-pump-operation", base=base) == "pump-operation"
    assert learner.aliases.scoped_aliases[base] == {"chemical-pump-operation": "pump-operation"}


def test_online_alias_keeps_mutually_exclusive_direction_types_separate() -> None:
    learner = OnlineTypeAliasLearner(min_structural_support=4)
    base = "阀门状态 {state}"

    for row_index in range(1, 4):
        learner.observe(
            base=base,
            event_type="valve-open",
            role="start",
            row_index=row_index,
            content="阀门开到位 出现",
        )
    learner.observe(
        base=base,
        event_type="valve-close",
        role="end",
        row_index=4,
        content="阀门关到位 消失",
    )

    assert learner.aliases.scoped_aliases == {}


class FragmentingLabeler:
    def label(self, label_input: LabelInput) -> list[LabelOutput]:
        outputs: list[LabelOutput] = []
        for cluster in label_input.clusters:
            is_end = "消失" in cluster.signature
            outputs.append(
                LabelOutput(
                    cluster_id=cluster.cluster_id,
                    type="cooling-fan-stop" if is_end else "cooling-fan-run",
                    role="end" if is_end else "start",
                    confidence=0.92,
                    summary="Fan运行状态",
                )
            )
        return outputs


def test_runner_learns_scoped_alias_online(tmp_path: Path) -> None:
    csv_path = tmp_path / "alarms.csv"
    csv_path.write_text(
        "\n".join(
            [
                "stationname,systemid,redundantsystem,equip_name,type,content,level,time,pointid,id,eventstatus",
                "TestStation,VCC,A,CoolingDevice,,G811Fan运行 出现,3,2026-04-13 18:00:01,p1,1,产生",
                "TestStation,VCC,A,CoolingDevice,,G811Fan运行 消失,3,2026-04-13 18:00:02,p2,2,消失",
                "TestStation,VCC,A,CoolingDevice,,G812Fan运行 出现,3,2026-04-13 18:00:03,p3,3,产生",
                "TestStation,VCC,A,CoolingDevice,,G812Fan运行 消失,3,2026-04-13 18:00:04,p4,4,消失",
            ]
        ),
        encoding="utf-8",
    )

    summary = run_adaptive_batches(
        csv_path=csv_path,
        output_dir=tmp_path / "out",
        batch_size=4,
        use_llm=True,
        llama_client=FragmentingLabeler(),  # type: ignore[arg-type]
    )

    assert summary["online_scoped_aliases"] >= 1
    aliases = load_type_aliases(Path(summary["type_aliases_path"]))
    assert aliases.scoped_aliases


class AliasSeedLabeler:
    def label(self, label_input: LabelInput) -> list[LabelOutput]:
        return [
            LabelOutput(
                cluster_id=cluster.cluster_id,
                type="filter-backwash",
                role="end",
                confidence=0.9,
                summary="过滤冲洗结束",
            )
            for cluster in label_input.clusters
        ]


def test_runner_loads_existing_type_aliases(tmp_path: Path) -> None:
    csv_path = tmp_path / "alarms.csv"
    csv_path.write_text(
        "\n".join(
            [
                "stationname,systemid,redundantsystem,equip_name,type,content,level,time,pointid,id,eventstatus",
                "TestStation,WTR,A,DeviceB,,Filter冲洗状态 消失,3,2026-04-13 18:00:01,p1,1,消失",
            ]
        ),
        encoding="utf-8",
    )
    alias_path = tmp_path / "aliases.json"
    write_type_aliases(
        alias_path,
        global_aliases={},
        scoped_aliases={
            "DeviceB_Filter冲洗状态 {state}": {
                "filter-backwash": "filter-operation"
            }
        },
        evidence=[],
        conflicts=[],
        source_paths=[],
        min_confidence=1.0,
    )

    summary = run_adaptive_batches(
        csv_path=csv_path,
        output_dir=tmp_path / "out",
        use_llm=True,
        llama_client=AliasSeedLabeler(),  # type: ignore[arg-type]
        type_alias_path=alias_path,
    )

    assert summary["loaded_type_aliases"] == 1
    output_text = Path(summary["outputs_path"]).read_text(encoding="utf-8")
    assert "filter-operation" in output_text
    assert "filter-backwash" not in output_text
