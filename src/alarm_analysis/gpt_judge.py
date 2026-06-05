from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel


SYSTEM_PROMPT = (
    "你是电力告警事件类型归并与语义角色裁判 API。你只能输出 JSON 数组，"
    "不能输出解释、Markdown、代码块或额外字段。"
)


class JudgeInputItem(BaseModel):
    issue_id: str
    issue_kind: str
    payload: dict[str, Any]


class JudgeResult(BaseModel):
    issue_id: str
    issue_kind: str
    action: str
    canonical_type: str | None = None
    corrected_role: str | None = None
    confidence: float
    reason: str


class GptJudgeClient:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        api_key_file: Path | None = None,
        timeout: float = 600.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = (
            api_key
            or os.getenv("GPT_JUDGE_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or self._read_key(api_key_file)
        )
        if not self.api_key:
            raise ValueError("missing GPT judge API key; set GPT_JUDGE_API_KEY or pass api_key_file")
        self.timeout = timeout

    @staticmethod
    def _read_key(path: Path | None) -> str | None:
        if not path:
            return None
        expanded = path.expanduser()
        if not expanded.exists():
            return None
        for line in expanded.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return line
        return None

    def judge(self, items: list[JudgeInputItem]) -> list[JudgeResult]:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(items)},
            ],
            "temperature": 0.0,
            "top_p": 0.8,
            "stream": False,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                body = response.text[:1000]
                raise RuntimeError(f"GPT judge request failed: {exc}; body={body}") from exc
            data = response.json()
        content = data["choices"][0]["message"]["content"]
        return parse_judge_results(content)


def run_judge_file(
    *,
    input_path: Path,
    output_dir: Path,
    client: GptJudgeClient,
    batch_size: int = 10,
    limit: int | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    session_id = uuid.uuid4().hex
    output_path = output_dir / f"{session_id}.judge.jsonl"
    metrics_path = output_dir / f"{session_id}.metrics.jsonl"
    items = load_judge_items(input_path, limit=limit)
    processed = 0
    started = time.perf_counter()
    for batch_index, batch in enumerate(_chunks(items, batch_size), start=1):
        batch_start = time.perf_counter()
        results = client.judge(batch)
        _append_jsonl(
            output_path,
            [
                {
                    **result.model_dump(),
                    "source_issue": batch_by_id(batch).get(result.issue_id),
                }
                for result in results
            ],
        )
        processed += len(batch)
        _append_jsonl(
            metrics_path,
            [
                {
                    "session_id": session_id,
                    "batch_index": batch_index,
                    "items": len(batch),
                    "elapsed_seconds": round(time.perf_counter() - batch_start, 3),
                }
            ],
        )
    summary = {
        "session_id": session_id,
        "input_path": str(input_path),
        "output_path": str(output_path),
        "metrics_path": str(metrics_path),
        "processed": processed,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    (output_dir / f"{session_id}.summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def build_user_prompt(items: list[JudgeInputItem]) -> str:
    return (
        "任务：裁判自动学习产出的告警语义问题。\n\n"
        "每个输入项可能是两类：\n"
        "- pair_issue：同一 base template 的 start/end 样本被标成多个 type。判断这些 type 是否应该合并，"
        "并给出 canonical_type。\n"
        "- review_item：silver validation 发现的 role/type 可疑样本。判断 role 是否应修正，或 type 是否应换名/合并。\n\n"
        "业务约束：\n"
        "- type 表示事件类型，不应包含 start/end 方向；同一事件的出现/消失通常应共享同一个 type。\n"
        "- role 只能是 start、status、end、noise。\n"
        "- 如果只是状态进入/离开，type 应表达状态对象，例如 cooling-fan-operation，而不是拆成 run/stop 两个事件类型。\n"
        "- 如果当前类型已经合理，无需强行改名。\n\n"
        "输出要求：\n"
        "- 只能输出 JSON 数组。\n"
        "- 每个输入 issue 必须输出一个对象。\n"
        "- 字段只能是 issue_id、issue_kind、action、canonical_type、corrected_role、confidence、reason。\n"
        "- action 取值：merge_type、keep_types、fix_role、fix_type_and_role、keep、uncertain。\n"
        "- confidence 为 0 到 1。\n\n"
        f"{json.dumps([item.model_dump() for item in items], ensure_ascii=False, indent=2)}"
    )


def parse_judge_results(content: str) -> list[JudgeResult]:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    raw = json.loads(text)
    if not isinstance(raw, list):
        raise ValueError("GPT judge output must be a JSON array")
    return [JudgeResult.model_validate(item) for item in raw]


def load_judge_items(path: Path, *, limit: int | None = None) -> list[JudgeInputItem]:
    items: list[JudgeInputItem] = []
    kind = "pair_issue" if "pair" in path.name else "review_item"
    with path.open(encoding="utf-8") as file:
        for index, line in enumerate(file, start=1):
            if not line.strip():
                continue
            payload = json.loads(line)
            items.append(
                JudgeInputItem(
                    issue_id=f"{path.stem}-{index}",
                    issue_kind=payload.get("reason") or kind,
                    payload=payload,
                )
            )
            if limit is not None and len(items) >= limit:
                break
    return items


def batch_by_id(items: list[JudgeInputItem]) -> dict[str, dict[str, Any]]:
    return {item.issue_id: item.payload for item in items}


def _chunks(items: list[JudgeInputItem], size: int) -> list[list[JudgeInputItem]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _append_jsonl(path: Path, items: list[dict[str, Any]]) -> None:
    with path.open("a", encoding="utf-8") as file:
        for item in items:
            file.write(json.dumps(item, ensure_ascii=False))
            file.write("\n")
