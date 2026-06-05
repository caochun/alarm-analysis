from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from .gpt_judge import GptJudgeClient, run_judge_file
from .llm_labeler import LlmLabeler
from .runner import run_adaptive_batches
from .silver_validation import validate_run
from .type_aliases import (
    build_scoped_type_aliases_from_pair_issues,
    build_type_aliases_from_judges,
    load_type_aliases,
    write_type_aliases,
)

app = typer.Typer(help="自学习告警事件化分析 CLI")
console = Console()


@app.command()
def run(
    csv_path: Annotated[Path, typer.Argument(help="原始告警 CSV 文件")],
    batch_size: Annotated[int, typer.Option(help="每批告警数量")] = 100,
    start_offset: Annotated[int, typer.Option(help="跳过排序后的前 N 条告警")] = 0,
    limit_batches: Annotated[int | None, typer.Option(help="最多处理多少批")] = None,
    output_dir: Annotated[Path, typer.Option(help="输出目录")] = Path("outputs"),
    base_url: Annotated[str, typer.Option(help="llama-server base URL")] = "http://127.0.0.1:8090",
    model: Annotated[str, typer.Option(help="模型名称")] = "Qwen3.5-9B-Q4_K_M-imatrix-mtp.gguf",
    api_key: Annotated[str | None, typer.Option(help="llama-server API key")] = None,
    api_key_file: Annotated[Path | None, typer.Option(help="llama-server API key 文件")] = None,
    timeout: Annotated[float, typer.Option(help="单次请求超时秒数")] = 600.0,
    llm: Annotated[bool, typer.Option(help="调用 LLM 为未知模板簇标注 type/role")] = False,
    min_template_support: Annotated[int, typer.Option(help="候选模板最小支持数")] = 3,
    knowledge_base_path: Annotated[Path | None, typer.Option(help="已有知识库 JSON")] = None,
    type_alias_path: Annotated[Path | None, typer.Option(help="已有 scoped/global type alias JSON")] = None,
    write_updated_kb: Annotated[bool, typer.Option(help="输出本轮知识库快照")] = True,
) -> None:
    """执行自学习流程：模板聚类 + LLM 标注 type/role + 候选知识沉淀。"""
    client = None
    if llm:
        client = LlmLabeler(
            base_url=base_url,
            model=model,
            api_key=api_key,
            api_key_file=api_key_file,
            timeout=timeout,
        )
    summary = run_adaptive_batches(
        csv_path=csv_path,
        output_dir=output_dir,
        batch_size=batch_size,
        start_offset=start_offset,
        limit_batches=limit_batches,
        use_llm=llm,
        llama_client=client,
        min_template_support=min_template_support,
        knowledge_base_path=knowledge_base_path,
        type_alias_path=type_alias_path,
        write_updated_kb=write_updated_kb,
    )
    console.print_json(data=summary)


@app.command()
def validate(
    csv_path: Annotated[Path, typer.Argument(help="原始告警 CSV 文件")],
    audit_path: Annotated[Path, typer.Argument(help="audit.jsonl 文件")],
    output_dir: Annotated[Path, typer.Option(help="silver validation 输出目录")] = Path(
        "outputs-validation"
    ),
    sample_limit: Annotated[int, typer.Option(help="样本包最大条数")] = 200,
    type_alias_path: Annotated[Path | None, typer.Option(help="type alias/canonical map JSON")] = None,
) -> None:
    """对运行结果做自动 silver validation，并生成 GPT 评判样本包。"""
    summary = validate_run(
        csv_path=csv_path,
        audit_path=audit_path,
        output_path=output_dir,
        sample_limit=sample_limit,
        type_alias_path=type_alias_path,
    )
    console.print_json(data=summary)


@app.command()
def judge(
    input_path: Annotated[Path, typer.Argument(help="pair_issues.jsonl 或 review_items.jsonl")],
    output_dir: Annotated[Path, typer.Option(help="GPT judge 输出目录")] = Path(
        "outputs-gpt-judge"
    ),
    base_url: Annotated[str, typer.Option(help="OpenAI-compatible base URL")] = "https://code-cli.cn/v1",
    model: Annotated[str, typer.Option(help="裁判模型名称")] = "gpt-5.5",
    api_key_file: Annotated[Path | None, typer.Option(help="API key 文件")] = None,
    timeout: Annotated[float, typer.Option(help="单次请求超时秒数")] = 600.0,
    batch_size: Annotated[int, typer.Option(help="每次请求评判多少个 issue")] = 8,
    limit: Annotated[int | None, typer.Option(help="最多评判多少个 issue")] = None,
) -> None:
    """调用 GPT teacher/judge 评判 silver validation 样本。"""
    client = GptJudgeClient(
        base_url=base_url,
        model=model,
        api_key_file=api_key_file,
        timeout=timeout,
    )
    summary = run_judge_file(
        input_path=input_path,
        output_dir=output_dir,
        client=client,
        batch_size=batch_size,
        limit=limit,
    )
    console.print_json(data=summary)


@app.command("learn-aliases")
def learn_aliases(
    judge_paths: Annotated[list[Path], typer.Argument(help="GPT judge jsonl 文件，可传多个")],
    output_path: Annotated[Path, typer.Option(help="输出 type alias JSON")] = Path(
        "outputs-type-aliases/type_aliases.json"
    ),
    min_confidence: Annotated[float, typer.Option(help="纳入 alias 学习的最低 judge 置信度")] = 0.9,
    existing_alias_path: Annotated[Path | None, typer.Option(help="已有 alias JSON")] = None,
) -> None:
    """从 GPT judge 结果中学习 type canonical alias。"""
    existing_aliases = load_type_aliases(existing_alias_path)
    global_aliases, evidence, conflicts = build_type_aliases_from_judges(
        judge_paths,
        min_confidence=min_confidence,
        existing_aliases=existing_aliases,
    )
    summary = write_type_aliases(
        output_path,
        global_aliases=global_aliases,
        evidence=evidence,
        conflicts=conflicts,
        source_paths=judge_paths,
        min_confidence=min_confidence,
        scoped_aliases=existing_aliases.scoped_aliases,
    )
    summary["output_path"] = str(output_path)
    console.print_json(data=summary)


@app.command("learn-scoped-aliases")
def learn_scoped_aliases(
    pair_issue_paths: Annotated[list[Path], typer.Argument(help="silver validation pair_issues.jsonl，可传多个")],
    output_path: Annotated[Path, typer.Option(help="输出 scoped type alias JSON")] = Path(
        "outputs-type-aliases/type_aliases.json"
    ),
    existing_alias_path: Annotated[Path | None, typer.Option(help="已有 alias JSON")] = None,
) -> None:
    """从 pair_issue 自动学习 base/template 作用域内的 type alias。"""
    existing = load_type_aliases(existing_alias_path)
    scoped_aliases, evidence = build_scoped_type_aliases_from_pair_issues(pair_issue_paths)
    merged_scoped = {base: dict(items) for base, items in existing.scoped_aliases.items()}
    for base, aliases in scoped_aliases.items():
        merged_scoped.setdefault(base, {}).update(aliases)
    summary = write_type_aliases(
        output_path,
        global_aliases=existing.global_aliases,
        scoped_aliases=merged_scoped,
        evidence=evidence,
        conflicts=[],
        source_paths=pair_issue_paths,
        min_confidence=1.0,
    )
    summary["output_path"] = str(output_path)
    console.print_json(data=summary)


if __name__ == "__main__":
    app()
