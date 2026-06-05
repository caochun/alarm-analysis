一级 Agent 入参字段注释说明书

适用范围：生产事件化告警管理平台一级事件化分析入参。

| 说明：本文档按当前项目一级 Agent 入参结构整理。顶层字段用于描述一次批次分析任务；alarm_batch_json 是本批必须标注的告警；active_groups_json 和 pending_events_json 是只读上下文，用于跨批续接，不要求模型对其中历史告警逐条输出。 |
| --- |

# 1. 整体入参结构

一级 Agent 每次只处理当前批次告警，但为了避免事件被批次边界切断，后端会把未关闭事件组、预告事件等历史状态摘要一并传入。

| 字段 | 中文名称 | 类型 | 是否必填 | 业务含义 | 使用说明 |
| --- | --- | --- | --- | --- | --- |
| session_id | 批次/会话 ID | String | 是 | 标识一次连续分析任务。 | 同一次分析会话下的多个批次共用该会话标识，用于串联批次、日志和结果。 |
| batch_index | 批次编号 | String/Number | 是 | 标识当前批次在本次会话中的顺序。 | 示例为 10。当前代码传给工作流时可能转为字符串。 |
| alarm_batch_json | 当前批次数据 | Array 或 JSON 字符串 | 是 | 本批需要一级 Agent 逐条标注的原始告警列表。 | Agent 只能标注这里面的 row_index，不能返回不存在的行号。 |
| skill_prompt | 提示词 | String | 是 | 当前启用的一级事件化规则和角色说明。 | 用于告诉模型如何识别事件开始、关联、结束、预告和噪声。 |
| active_groups_json | 激活事件组 | Array 或 JSON 字符串 | 否 | 上一批尚未关闭的事件组摘要。 | 只读上下文，用于判断当前告警是否归入已有事件组。 |
| pending_events_json | 待激活事件组 | Array 或 JSON 字符串 | 否 | 之前已识别但尚未真正发生的预告类事件。 | 当本批出现匹配真实动作时，可衔接为事件开始。 |
| active_chains_json | 未完成链路上下文 | Array 或 JSON 字符串 | 可选 | 未完成或需复核的链路摘要。 | 当前示例未传入；用于指令触发、过程、结果跨批补齐。 |

# 2. 当前批次数据 alarm_batch_json

该字段是一级 Agent 的核心处理对象。每条告警都应有唯一 row_index，模型输出时必须原样返回。

| 字段路径 | 中文名称 | 类型 | 示例 | 字段说明 | 注意事项 |
| --- | --- | --- | --- | --- | --- |
| alarm_batch_json[].row_index | 原始事件行号/全局行号 | Number | 1000 | 标识原始告警在会话中的全局位置。 | 模型输出必须原样返回；不能新增、遗漏或重复。 |
| alarm_batch_json[].time | 事件时间 | String/ISO 时间 | 2026-01-20T16:25:24.178 | 当前告警发生时间。 | 用于判断同组时间间隔、事件开始结束顺序。 |
| alarm_batch_json[].station | 所属站 | String | 山阳换流站 | 告警所属站点。 | 用于同站事件判断和三级连续事件链分析。 |
| alarm_batch_json[].host | 主机 | String | S1ASC | 告警来源主机或系统节点。 | 用于辅助区分来源系统。 |
| alarm_batch_json[].system_alarm | 系统告警/套别原始值 | String | B | 外部告警中的系统告警标识或原始套别值。 | 保留原始信息，不等同于告警等级。 |
| alarm_batch_json[].suite | 套数 | String | B | 告警所属套别，常见为 A/B。 | 用于区分 A/B 套设备或系统。 |
| alarm_batch_json[].alarm_device | 所属装置 | String | 辅助系统开入 | 产生告警的设备或装置名称。 | 事件归组时的重要判断依据。 |
| alarm_batch_json[].content | 事件内容 | String | 220kV交流场故障录波器II屏_录波启动 出现 | 告警正文，是模型判断事件类型和动作语义的关键文本。 | 识别出现/消失、操作完成、故障信息等主要依赖该字段。 |
| alarm_batch_json[].level | 告警等级 | String/Number | 3 | 原始告警等级。0=紧急，1=严重，2=轻微，3=正常。 | 等级优先级为 0 > 1 > 2 > 3；不要与 system_alarm 的 A/B 混淆。 |

# 3. 激活事件组 active_groups_json

active_groups_json 表示上一批没有关闭、需要继续观察的事件组。它不是本批要重新标注的告警，而是帮助模型判断“当前告警是否应继续归入旧事件组”的上下文。

| 字段路径 | 中文名称 | 类型 | 示例 | 字段说明 | 注意事项 |
| --- | --- | --- | --- | --- | --- |
| active_groups_json[].type | 事件类型 | String | transformer-tap | 活跃事件组的标准类型。 | 当前告警若同类型、同设备且时间符合规则，可标为关联事件。 |
| active_groups_json[].group_seq | 事件组序号 | Number | 31 | 当前会话内事件组的顺序编号。 | 用于区分同类型多个事件组。 |
| active_groups_json[].start_time | 开始时间 | String/ISO 时间 | 2026-01-20T16:19:25.536 | 该事件组第一条关键告警时间。 | 用于判断事件持续时长。 |
| active_groups_json[].end_time | 最新时间/结束时间 | String/ISO 时间 | 2026-01-20T16:25:16.133 | 该事件组目前最新一条告警时间。 | 当前告警通常与 end_time 比较间隔。 |
| active_groups_json[].stations | 涉及站点 | Array<String> | ["山阳换流站"] | 事件组涉及的站点列表。 | 用于判断当前告警是否属于同站事件。 |
| active_groups_json[].group_devices | 涉及设备/装置 | Array<String> | ["换流变区开入"] | 事件组涉及的装置或设备。 | 用于同设备、同装置续接判断。 |
| active_groups_json[].key_alarms | 关键告警列表 | Array<Object> | start/latest | 该事件组的起点告警和最新告警摘要。 | 帮助模型快速理解历史事件组，不需要重标其中 row_index。 |

# 4. 关键告警 key_alarms

key_alarms 是 active_groups_json 内部的关键证据。通常保留事件组起点和最新告警两类角色。

| 字段路径 | 中文名称 | 类型 | 示例 | 字段说明 | 注意事项 |
| --- | --- | --- | --- | --- | --- |
| key_alarms[].row_index | 关键告警行号 | Number | 528 | 该关键告警在原始告警中的全局行号。 | 只作为上下文，不要求模型输出历史行号。 |
| key_alarms[].time | 关键告警时间 | String/ISO 时间 | 2026-01-20T16:19:25.536 | 关键告警发生时间。 | 用于理解事件起点或最新进展。 |
| key_alarms[].station | 所属站 | String | 山阳换流站 | 关键告警所属站点。 | 与当前告警 station 对比。 |
| key_alarms[].host | 主机 | String | S1P1PCP1 | 关键告警来源主机。 | 辅助判断来源系统。 |
| key_alarms[].system_alarm | 系统告警/套别原始值 | String | B | 关键告警中的原始系统告警标识。 | 保留原始上下文。 |
| key_alarms[].suite | 套数 | String | B | 关键告警所属套别。 | 用于 A/B 套区分。 |
| key_alarms[].alarm_device | 所属装置 | String | 换流变区开入 | 关键告警所属设备或装置。 | 与当前告警设备对比。 |
| key_alarms[].content | 事件内容 | String | Y/Y换流变221B C相汇控柜_有载开关操作中 出现 | 关键告警正文。 | 用于理解已有事件组的语义。 |
| key_alarms[].level | 告警等级 | String/Number | 3 | 关键告警原始等级。 | 用于判断历史事件严重程度。 |
| key_alarms[].role | 关键角色 | String | start/latest | start 表示起点告警，latest 表示最新告警。 | 帮助模型区分事件起点与当前最新状态。 |

# 5. 待激活事件组 pending_events_json

pending_events_json 用于预告类事件。示例中为空数组，表示当前没有待激活事件。

| 字段路径 | 中文名称 | 类型 | 示例 | 字段说明 | 注意事项 |
| --- | --- | --- | --- | --- | --- |
| pending_events_json[].type | 预告事件类型 | String | ac-filter | 之前识别到的预告事件类型。 | 后续真实动作出现时用于匹配。 |
| pending_events_json[].effective_time | 预计生效时间 | String/ISO 时间 | 2026-01-20T16:30:00 | 预告信号预计生效的时间。 | 当前批次告警与该时间和类型匹配时，可作为事件开始。 |
| pending_events_json[].trigger_alarm_index | 触发告警行号 | Number | 900 | 触发预告的原始告警行号。 | 用于追溯预告来源。 |
| pending_events_json[].stations | 涉及站点 | Array<String> | ["山阳换流站"] | 预告涉及的站点。 | 用于同站匹配。 |
| pending_events_json[].group_devices | 涉及设备/装置 | Array<String> | ["交流滤波器"] | 预告涉及的设备或装置。 | 用于同设备匹配。 |

# 6. 提示词 skill_prompt

| 字段 | 中文名称 | 类型 | 字段说明 | 注意事项 |
| --- | --- | --- | --- | --- |
| skill_prompt | 提示词 | String | 定义模型角色、事件类型、标注规则、输出格式和约束。 | 该字段内容较长，通常由系统配置注入；示例中只展示开头。 |

# 7. 字段使用关系

| 数据块 | 模型是否要逐条输出 | 主要作用 | 一句话说明 |
| --- | --- | --- | --- |
| alarm_batch_json | 是 | 当前批次待标注对象 | 模型只对这里面的 row_index 输出 S/R/E/F/N 标注。 |
| active_groups_json | 否 | 跨批续接上下文 | 帮助判断当前告警是否继续归入上批未关闭事件组。 |
| pending_events_json | 否 | 预告事件上下文 | 帮助把之前的预告信号与本批真实动作衔接。 |
| skill_prompt | 否 | 规则说明 | 告诉模型按哪些规则输出标注结果。 |
| session_id / batch_index | 否 | 任务追溯 | 帮助系统记录这是哪一次会话、哪一个批次。 |

# 8. 示例结构整理版

以下为去掉长数组后的结构化示例，用于说明字段层级。

{  
  "session_id": "e49272813ee54ad29bb253912ccb5c23",  
  "batch_index": 10,  
  "alarm_batch_json": [  
    {  
      "row_index": 1000,  
      "time": "2026-01-20T16:25:24.178",  
      "station": "山阳换流站",  
      "host": "S1ASC",  
      "system_alarm": "B",  
      "suite": "B",  
      "alarm_device": "辅助系统开入",  
      "content": "220kV交流场故障录波器II屏_录波启动 出现",  
      "level": "3"  
    }  
  ],  
  "skill_prompt": "一级事件化提示词...",  
  "active_groups_json": [  
    {  
      "type": "transformer-tap",  
      "group_seq": 31,  
      "start_time": "2026-01-20T16:19:25.536",  
      "end_time": "2026-01-20T16:25:16.133",  
      "stations": ["山阳换流站"],  
      "group_devices": ["换流变区开入"],  
      "key_alarms": [  
        {"row_index": 528, "role": "start"},  
        {"row_index": 985, "role": "latest"}  
      ]  
    }  
  ],  
  "pending_events_json": []  
}

# 9. 汇报口径

| 可以这样解释：一级 Agent 的入参由三部分组成：第一部分是本批真实告警，必须逐条判断；第二部分是上一批未关闭事件组，帮助跨批续接；第三部分是提示词和会话信息，用于约束模型按统一规则输出。 |
| --- |

# 10. 关键注意事项

• alarm_batch_json 是唯一需要逐条标注的数据来源。

• active_groups_json 和 pending_events_json 是只读上下文，不能把其中历史 row_index 当作本批输出。

• row_index 是结果回写和追溯的关键字段，必须原样返回。

• level 是数字告警等级，0 紧急、1 严重、2 轻微、3 正常；不要与 A/B 套或 system_alarm 混用。

• active_groups_json 在接口传输中可能是 JSON 字符串，但业务理解上可按数组对象阅读。
