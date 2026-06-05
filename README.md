# Alarm Analysis

自学习告警事件化分析原型。

## 功能

- 读取告警 CSV，按时间排序并按批处理。
- 对告警内容做模板/签名归一化，沉淀可复用的模板知识库。
- 对未知模板簇调用本地 llama.cpp `llama-server` 标注 `type` 和 `role`。
- 由状态机维护事件生命周期并输出 `S/R/E/N`。
- 在线学习 scoped type alias，缓解同一业务状态被拆成多个 type 的问题。
- 提供 silver validation、GPT judge、alias 学习等实验辅助命令。

## 快速运行

```bash
uv run alarm-agent run path/to/input.csv --limit-batches 1
```

默认参数：

- 每批 `100` 条。
- 默认不调用 LLM；加 `--llm` 后调用 llama。
- llama 地址：`http://127.0.0.1:8090/v1/chat/completions`。
- 输出目录：`outputs/`。

调用 LLM：

```bash
uv run alarm-agent run path/to/input.csv --limit-batches 1 --llm
```

如果服务启用了 API key：

```bash
export LLAMA_API_KEY="你的 key"
uv run alarm-agent run path/to/input.csv --limit-batches 1 --llm
```

也可以用 key 文件：

```bash
uv run alarm-agent run path/to/input.csv --llm --api-key-file path/to/api_key.txt
```

## 验证与分析

对一次运行的 audit 输出做 silver validation：

```bash
uv run alarm-agent validate path/to/input.csv outputs/<session>.audit.jsonl
```

从 pair issue 中自动学习 scoped aliases：

```bash
uv run alarm-agent learn-scoped-aliases outputs-validation/pair_issues.jsonl
```

实验复盘见 [docs/experiment_review.md](docs/experiment_review.md)。
