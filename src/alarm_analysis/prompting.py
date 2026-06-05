from __future__ import annotations

import json

from .models import LabelInput


SYSTEM_PROMPT = (
    "你是电力告警语义标注 API。你只能输出 JSON 数组，不能输出解释、Markdown、代码块或额外字段。"
)


def build_user_prompt(label_input: LabelInput) -> str:
    payload = label_input.model_dump()
    return (
        "任务：为每个告警模板簇标注两个语义标签：type 和 role。\n\n"
        "最小先验：\n"
        "- 每条告警属于某种事件类型 type。\n"
        "- 每条告警在语义上像某个角色 role。\n"
        "- role 只能是 start、status、end、noise 四选一。\n\n"
        "type 标注原则：\n"
        "- type 表示业务事件对象/业务状态类型，不能表达生命周期方向。\n"
        "- 优先复用 known_type_catalog 中已有的稳定命名；如果已有类型都不合适，可以创建新的英文 kebab-case type，例如 cooling-fan-operation。\n"
        "- 同类设备、同类动作、只差编号/相别/状态词的模板应使用同一个 type。\n"
        "- 同一状态的出现/消失、投入/退出、合/分、有效/无效，必须共享同一个 type，由 role 区分 start/end。\n"
        "- 不要把 start、end、出现、消失、投入、退出、run、stop、open、close 这类生命周期方向写进 type；只有告警对象本身明确是开到位/关到位时，type 才可包含 open/closed。\n"
        "- 候选命名优先使用中性名词：operation、status、fault、alarm、command、trigger、abnormal。\n"
        "- 不要输出中文 type，不要把 role 写进 type。\n\n"
        "role 标注原则：\n"
        "- start：语义上表示事件开始、动作投入、异常/故障/告警出现、指令发出、状态进入。\n"
        "- status：语义上表示过程状态、运行中、位置状态、连续反馈、不能独立判定开始或结束。\n"
        "- end：语义上表示事件结束、动作退出、异常/故障/告警消失、复归、状态离开。\n"
        "- noise：普通遥测/控制方式/背景信息，或无法形成事件语义的信息。\n\n"
        "输出要求：\n"
        "- 只能输出 JSON 数组。\n"
        "- 每个 clusters 输入项必须输出一个对象。\n"
        "- 字段只能是 cluster_id、type、role、confidence、summary。\n"
        "- confidence 为 0 到 1；summary 用一句简短中文描述模板语义。\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )
