# 自学习告警分析实验复盘

本文复盘当前自学习告警分析系统在两套脱敏告警数据上的实验过程和结论：

- 数据集 A：约 2w 条告警。
- 数据集 B：约 10w 条告警。

实验目标不是把 27b 或其他模型输出当作标准答案，而是验证当前设计是否能在少先验条件下在线学习：

1. 对每条告警抽象出 `type` 和 `role`。
2. LLM 只负责未知模板簇的语义标注。
3. 生命周期与 `S/R/E/N` 由状态机维护。
4. 模板知识沉淀为 KB。
5. 同一业务状态的 type 细碎化通过 scoped alias 在线归并。

## 当前工作机理

当前架构的数据流如下：

```text
CSV 告警
  -> 模板/签名归一化
  -> 查 KB 中已有 signature
  -> 未知 signature cluster 调用 LLM 标注 type/role
  -> 在线 scoped alias 归并局部近义 type
  -> EventStateMachine 生成 S/R/E/N
  -> TemplateMiner 沉淀候选规则/KB
  -> silver validation 自动检查一致性
```

其中几个概念要区分：

- `type`：告警所属的业务事件类型，例如 `valve-position`、`cooling-fan-operation`。
- `role`：告警语义角色，例如 `start`、`status`、`end`、`noise`。
- `tag`：最终输出动作，由状态机生成，例如 `S`、`R`、`E`、`N`。
- `scoped alias`：只在某个 `pair_base` 下生效的 type 归并，不做全局合并。

也就是说，LLM 不再维护 active group 或 pending event。它只是给模板簇提供语义标签；跨批次生命周期由 `EventStateMachine` 接管。

## 本轮代码变化

本轮为了支持迁移实验和在线 type 归并，做了以下改动：

1. 增强 `OnlineTypeAliasLearner`：
   - 记录 `base -> type -> role` 矩阵。
   - 在同一 `pair_base` 内，如果 start/end 两侧出现近义 type，学习 scoped alias。
   - 对 `open/close`、`block/deblock` 等互斥方向做保护，避免明显错误合并。

2. 新增运行时加载已有 alias：
   - CLI 新增 `--type-alias-path`。
   - `run_adaptive_batches` 可用已有 `.type_aliases.json` 初始化在线 learner。

3. 修正 validation 口径：
   - signature conflict 检查也按 `pair_base` scope 应用 scoped alias，和运行时保持一致。

4. 修正 summary 记录：
   - `loaded_type_aliases` 应记录初始加载数量，不能被后续在线学习增长污染。

测试结果：

```text
uv run pytest
10 passed
```

## 实验一：数据集 A 冷启动学习

### 输入与输出

输入数据：

```text
dataset-a.csv
```

主要输出：

```text
outputs-learn-a/<session>.summary.json
outputs-learn-a/<session>.knowledge.json
outputs-learn-a/<session>.type_aliases.json
outputs-silver-a/summary.json
```

### 运行结果

```text
processed                 20000
batches                   200
unique_signatures         363
llm_labeled_clusters      363
fallback_clusters         0
candidate_rules           247
strong / weak / candidate 169 / 77 / 1
closed_groups             7876
active_groups             0
online_scoped_aliases     2
```

数据集 A 是冷启动，没有输入已有 KB，因此 363 个 signature cluster 都由 LLM 标注。之后这些标注被沉淀为候选规则和 KB。

### 数据集 A Silver Validation

```text
decisions                 20000
unique_signatures         363
role_silver_accuracy      0.9377
pair_groups               146
pair_type_consistency     1.0
signature_conflict_rate   0.0
level_1_2_noise_rows      0
fallback_orphan_end_rows  0
```

tag 分布：

```text
S                         7876
R                         5497
E                         6615
N                         12
```

role 分布：

```text
start                     9879
end                       9515
status                    594
noise                     12
```

### 数据集 A 学到的 Scoped Alias

数据集 A 最终学到 2 个 scoped aliases：

```text
filter-backwash -> filter-operation
system-standby  -> water-supply-status
```

它们都不是全局 alias，而是限定在具体 `pair_base` 下。

例 1：

```text
base:
设备族A_状态信号A{state}

filter-backwash -> filter-operation
```

证据：

```text
filter-operation  start 3
filter-backwash   end   1
```

解释：同一个状态信号的出现/消失，被 LLM 起成了两个相关但不同的 type；系统在该局部 base 下归并。

例 2：

```text
base:
设备族B_状态信号B {state}

system-standby -> water-supply-status
```

证据：

```text
system-standby       end   3
water-supply-status  start 1
```

解释：同一个状态模式的出现/消失被分成两个 type，局部归并后 pair type consistency 达到 1.0。

### 数据集 A 结论

数据集 A 冷启动验证了自学习系统的基本闭环：

- LLM 能把未知模板簇标成可用的 `type/role`。
- 状态机能稳定生成 `S/R/E/N`。
- 没有未闭合 active group。
- 没有 fallback。
- scoped alias 能解决局部 type 细碎化问题。

因此，数据集 A 可作为后续迁移实验的初始 KB 与 alias 来源。

## 实验二：用数据集 A 的 KB + Alias 迁移到数据集 B

### 输入与输出

输入数据：

```text
dataset-b.csv
```

加载的数据集 A 产物：

```text
outputs-learn-a/<session>.knowledge.json
outputs-learn-a/<session>.type_aliases.json
```

主要输出：

```text
outputs-transfer-b/<session>.summary.json
outputs-transfer-b/<session>.knowledge.json
outputs-transfer-b/<session>.type_aliases.json
outputs-silver-b/summary.json
outputs-analysis-b/analysis.json
```

### 运行结果

```text
processed                 100000
batches                   1000
loaded_knowledge_rules    247
initial_loaded_aliases    2
new learned signatures    3730
llm_labeled_clusters      3730
fallback_clusters         0
candidate_rules           2276
strong / weak / candidate 1502 / 708 / 66
closed_groups             36426
active_groups             0
final scoped aliases      167
```

说明：

- `initial_loaded_aliases` 实际是 2。
- 本轮运行产物里的旧 summary 曾显示 `loaded_type_aliases = 167`，这是因为运行时对象继续学习后污染了加载数量统计。代码已修正，后续运行会正确记录初始加载数量。

### 数据集 B Silver Validation

```text
decisions                 100000
unique_signatures         3968
role_silver_accuracy      0.87568
pair_groups               1279
pair_type_consistency     0.951525
signature_conflict_rate   0.0
level_1_2_noise_rows      0
fallback_orphan_end_rows  0
```

source 分布：

```text
knowledge                 65648
llm                       34352
```

tag 分布：

```text
S                         36426
R                         34516
E                         28963
N                         95
```

role 分布：

```text
start                     45462
end                       48239
status                    6204
noise                     95
```

### 数据集 B 迁移覆盖情况

数据集 B 中有 65,648 行来自 knowledge 命中，34,352 行由 LLM 标注。这说明数据集 A 的 KB 对数据集 B 有明显迁移价值，但数据集 B 后半段存在大量数据集 A 未覆盖的新模板。

按 100 批统计的学习曲线：

| batch range | unknown clusters | clusters | unknown rate | knowledge-only batches | llm batches | elapsed seconds | aliases end |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1-100 | 0 | 210 | 0.0000 | 100 | 0 | 0.724 | 2 |
| 101-200 | 0 | 263 | 0.0000 | 100 | 0 | 0.855 | 2 |
| 201-300 | 2 | 425 | 0.0047 | 99 | 1 | 3.090 | 2 |
| 301-400 | 219 | 2375 | 0.0922 | 69 | 31 | 185.423 | 3 |
| 401-500 | 379 | 3522 | 0.1076 | 59 | 41 | 318.180 | 11 |
| 501-600 | 225 | 3655 | 0.0616 | 76 | 24 | 194.211 | 11 |
| 601-700 | 822 | 3490 | 0.2355 | 28 | 72 | 692.567 | 41 |
| 701-800 | 608 | 4115 | 0.1478 | 33 | 67 | 550.347 | 88 |
| 801-900 | 720 | 4903 | 0.1468 | 36 | 64 | 613.038 | 137 |
| 901-1000 | 755 | 5164 | 0.1462 | 23 | 77 | 661.432 | 167 |

这个曲线有两个重要信息：

1. 前 300 批几乎完全被数据集 A 的 KB 覆盖。
2. 600 批以后数据分布明显变化，新模板大量出现，LLM 调用重新增加。

### 性能情况

数据集 B 运行耗时统计：

```text
total_elapsed_seconds     3219.867
mean_batch_seconds        3.220
median_batch_seconds      0.009
max_batch_seconds         40.356
knowledge_only_batches    623
llm_batches               377
unknown_clusters_total    3730
clusters_total            28122
```

中位批耗时只有 0.009 秒，说明 knowledge-only 路径非常快。整体耗时主要由未知模板簇的 LLM 标注决定。

### 数据集 B 在线 Alias 增长

数据集 B 从初始 2 个 scoped aliases 增长到 167 个 scoped aliases。

alias 目标 type 的 Top 10：

| canonical type | alias count |
|---|---:|
| communication-fault | 26 |
| device-status | 19 |
| cooling-system-alarm | 18 |
| power-supply-status | 13 |
| converter-valve-group-status | 12 |
| power-supply-fault | 11 |
| communication-status | 8 |
| device-fault | 7 |
| minor-fault | 6 |
| commutation-status | 5 |

这说明在线归并器在数据集 B 上持续发挥作用，尤其集中在通信、设备状态、冷却系统、电源状态等类型。

但 167 个 alias 也说明当前在线归并策略可能偏积极，需要进一步审查。

### 数据集 B 剩余 Pair Issues

数据集 B validation 中仍有 62 个 pair issue。

Top type pair：

| type pair | count |
|---|---:|
| `alarm` / `valve-bypass-signal-fault` | 6 |
| `converter-valve-control-signal-abnormal` / `valve-unlock-status` | 6 |
| `converter-valve-charging-operation` / `converter-valve-control-signal-abnormal` | 6 |
| `converter-valve-control-signal-abnormal` / `recording-trigger` | 6 |
| `converter-valve-control-signal-abnormal` / `valve-bypass-operation` | 6 |
| `cooling-fan-operation` / `cooling-system-alarm` | 4 |
| `power-supply-fault` / `voltage-alarm` | 4 |
| `optical-ct-power-abnormal` / `optical-ct-power-supply-abnormal` | 4 |
| `commutation-status` / `converter-valve-control-signal-abnormal` | 4 |
| `valve-bypass-operation` / `valve-operation` | 3 |

这些剩余问题与数据集 A 中较简单的状态类信号不同，更多集中在复杂设备动作、控制信号异常、触发类信号等复杂业务上。

这意味着：

- 当前基于 `pair_base + start/end 对偶 + type 近义` 的 alias 策略能解决一部分细碎化问题。
- 但复杂控保/阀组类信号中，type 之间可能不是简单近义，而是存在因果、伴随、触发、保护动作等关系。
- 这些问题不应该全部靠 alias 合并解决，否则容易过度归并。

## 对 Silver Validation 的理解

silver validation 是自动一致性检查，不是人工金标。

它主要检查：

1. `role_silver_accuracy`  
   根据 `出现/消失/产生/复归` 等状态词推断一个粗略 silver role，并与模型 role 对比。

2. `pair_type_consistency`  
   同一 `pair_base` 下，start/end 对偶信号理论上应具有相同 type。

3. `signature_conflict_rate`  
   同一个 signature 的 type/role 是否稳定。

4. `level_1_2_noise_rows`  
   高等级告警被标为噪声时认为可疑。

5. `fallback_orphan_end_rows`  
   状态机是否出现无法合理处理的孤立 end。

需要注意：`role_silver_accuracy` 不能直接当真实准确率。比如一些 `产生` 信号在 silver 规则中会偏向 `start`，但业务语义上可能更像 `status`。因此 role mismatch 应作为复查队列，而不是直接等价于错误。

## 总体结论

### 成立的部分

自学习架构方向成立：

- 少先验条件下能启动。
- LLM 只做模板级语义分类，减少调用次数。
- 状态机负责生命周期，结果稳定。
- KB 对后续数据有明显迁移效果。
- scoped alias 能在线修正局部 type 细碎化。
- 数据集 A 到数据集 B 的迁移覆盖率达到约 65.6% 行级 knowledge 命中。

### 暴露的问题

1. 数据集 B 的分布明显不同于数据集 A。  
   前 300 批覆盖很好，600 批后出现大量新模板。

2. 在线 alias 增长过快。  
   数据集 B 最终从 2 个增长到 167 个，说明策略有效但可能过度积极。

3. 剩余 pair issue 更复杂。  
   很多不是简单近义 type，而可能是伴随信号、保护动作、控制异常、录波触发等链式关系。

4. role silver 指标下降。  
   数据集 B 上 `role_silver_accuracy = 0.87568`，需要抽样判断哪些是真错，哪些是 silver 规则粗糙导致。

## 建议的下一步

### 1. 把 alias 改成两阶段确认

当前 alias 是发现后立即生效。建议改成：

```text
candidate_alias -> confirmed_alias
```

第一次发现同 base 下 start/end type 分裂时，只进入 pending。只有满足更强证据后才正式生效。

可选确认条件：

- 同一 base 下 start 和 end 两侧各至少 2 条。
- 两个 type 的 dominant role 互补且稳定。
- 同一 alias 在多个相似 base 中重复出现。
- canonical type 不过于泛化，例如避免轻易归到 `alarm`、`device-status`。

### 2. 对复杂控保类信号引入关系建模

数据集 B 剩余 pair issue 中，很多不适合靠 alias 合并。建议增加关系层：

```text
same-event
caused-by
triggered-by
accompanying
recovery-of
noise
```

这样 `recording-trigger`、`control-signal-abnormal`、`valve-bypass-operation` 不一定要合成一个 type，而可以作为同一事件链中的不同关系。

### 3. 改进 silver validation

当前 silver role 太依赖状态词。后续可以增加：

- 对 `运行/停止/备用/合/分/开到位/关到位` 的 status 识别。
- 对 `产生` 不强行视为 start。
- 对 `消失` 区分“状态结束”和“告警复归”。

### 4. 建立回归实验集

建议固定以下几组实验：

1. 数据集 A 冷启动。
2. 数据集 A KB 迁移到数据集 B。
3. 数据集 B 冷启动。
4. 数据集 B 前半训练，后半验证。
5. alias 两阶段策略前后对比。

每轮至少比较：

```text
knowledge source ratio
unknown cluster rate
pair_type_consistency
signature_conflict_rate
role_silver_accuracy
fallback count
active group count
alias count
pair issue type pairs
runtime
```

## 当前结论一句话

当前实现已经证明了“少先验 + LLM 模板标注 + 状态机生命周期 + 在线 KB/alias 学习”的路线可行；下一阶段的重点不是继续增加 LLM 能力，而是把在线 alias 从即时归并升级为更稳的证据驱动确认机制，并为复杂控保信号增加事件关系层。
