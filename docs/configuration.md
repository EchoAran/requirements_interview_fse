# Elicitation Core 配置指南 (Configuration Reference)

本文档提供 **Elicitation Core (需求半结构化访谈核心机制引擎)** 所有配置项的完整规范、类型约束、默认值说明以及调优建议。

---

## 1. 配置文件组织与加载规则

### 1.1 文件结构与位置
项目配置文件位于 `configs/` 目录下：
- **`configs/default.example.yaml`**：全量配置项模板文件，包含所有允许配置的字段、合理默认值及行内注释；
- **`configs/default.yaml`**：默认生效的运行时配置文件。

首次使用时，建议复制模板文件为 `configs/default.yaml` 并根据需要修改：
```bash
# Linux / macOS
cp configs/default.example.yaml configs/default.yaml

# Windows PowerShell
Copy-Item configs/default.example.yaml configs/default.yaml
```

### 1.2 配置加载优先级
系统通过 `AppConfig.load_from_yaml()` 进行强类型（Pydantic V2）加载，支持以下覆盖与查找逻辑：
1. **CLI 参数指定**：通过 `--config <path>` 显式指定配置文件路径；
2. **默认路径**：未指定时默认加载 `configs/default.yaml`；
3. **缺省兜底**：若文件不存在，系统将自动使用代码内置的安全默认值初始化配置。

### 1.3 敏感凭证保护
- **禁止明文提交密钥**：推荐配置 `api_key_env`（默认为 `LLM_API_KEY`），通过系统环境变量传递密钥；
- **审计日志脱敏**：系统在持久化 `config_snapshot.yaml` 及 `llm_calls.jsonl` 时，会自动过滤并遮蔽所有明文 `api_key`。

---

## 2. 配置字段详细参考

### 2.1 `model` (大语言模型服务配置)

用于配置与大语言模型（LLM）通信的接口协议、连接参数及重试策略。

| 字段名 | 类型 | 默认值 | 约束 / 可选值 | 功能说明与调优建议 |
|---|---|---|---|---|
| `provider` | `str` | `"openai_compatible"` | 推荐 `"openai_compatible"` | 大模型服务提供方协议类型。默认兼容任何遵循 OpenAI Chat Completions 规范的 API 服务。 |
| `api_url` | `str` | `"https://api.openai.com/v1/chat/completions"` | 有效 URL 字符串 | 大模型 Chat Completions 接口的完整请求 URL。 |
| `api_key_env` | `str` | `"LLM_API_KEY"` | 环境变量名称 | 用于获取 API 密钥的环境变量名称。系统优先读取此环境变量，若未设置则回退检查 `OPENAI_API_KEY`。 |
| `api_key` | `str` | `""` | 字符串 | 显式指定的 API 密钥。若留空，则自动通过 `api_key_env` 环境变量解析。 |
| `model_name` | `str` | `"gpt-4o"` | 例如 `"gpt-4o"`, `"gpt-4o-mini"`, `"deepseek-chat"`, `"qwen-plus"` | 调用的模型型号标识。建议使用具备良好结构化 JSON 输出能力和长上下文理解能力的模型。 |
| `temperature` | `float` | `0.2` | `0.0` ~ `1.0` | 采样温度。较低的温度（如 0.1~0.3）能显著提高结构化提取和策略规划的确定性。 |
| `timeout_seconds`| `float`| `60.0` | > `0.0` | 单次 HTTP 请求超时上限（单位：秒）。 |
| `max_retries` | `int` | `3` | >= `0` | 遭遇网络超时、断连或 429 限流时的最大自动指数退避重试次数。 |

#### 常用平台配置示例

```yaml
# OpenAI 官方服务
model:
  provider: "openai_compatible"
  api_url: "https://api.openai.com/v1/chat/completions"
  api_key_env: "OPENAI_API_KEY"
  model_name: "gpt-4o"

# DeepSeek 官方服务
model:
  provider: "openai_compatible"
  api_url: "https://api.deepseek.com/v1/chat/completions"
  api_key_env: "DEEPSEEK_API_KEY"
  model_name: "deepseek-chat"

# 阿里通义千问 (DashScope 兼容端点)
model:
  provider: "openai_compatible"
  api_url: "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
  api_key_env: "DASHSCOPE_API_KEY"
  model_name: "qwen-plus"

# 本地 Ollama 部署
model:
  provider: "openai_compatible"
  api_url: "http://localhost:11434/v1/chat/completions"
  api_key: "ollama"
  model_name: "qwen2.5:14b"
```

---

### 2.2 `intent` (用户交互意图控制配置)

用于调整受访者控制意图识别的灵敏度与安全确认机制。

| 字段名 | 类型 | 默认值 | 约束 | 功能说明与调优建议 |
|---|---|---|---|---|
| `confidence_threshold` | `float` | `0.60` | `0.0` ~ `1.0` | **直接生效阈值**。当意图识别置信度 $\ge$ 此阈值时，系统直接执行话题跳转（`switch_existing_topic`）、拒绝（`refuse_topic`）、返回上一话题（`return_previous`）或结束访谈（`stop_interview`）。 |
| `confirmation_threshold` | `float` | `0.40` | `0.0` ~ `confidence_threshold` | **二次确认门限**。当置信度介于 `[confirmation_threshold, confidence_threshold)` 之间时，系统触发 `confirm_control` 策略生成澄清式求证提问。低于此值时视为普通业务回答。 |

---

### 2.3 `scheduler.weights` (七因子动态调度器权重配置)

调度器用于在受访者未显式指定跳转时，对所有未完成话题计算综合效用评分并选取全局最优先目标。系统在加载时会自动执行**权重自动归一化**（$\sum w_i = 1.0$）。

$$\text{Score}(T) = \sum_{i=1}^7 w_i \cdot \text{Factor}_i(T)$$

| 权重字段名 | 类型 | 默认值 | 因子含义与作用 |
|---|---|---|---|
| `initial_prior` | `float` | `0.15` | **初始先验权重**：基于大纲结构生成的静态业务优先级。 |
| `dependency_readiness` | `float` | `0.20` | **依赖就绪度权重**：前序依赖话题是否全部完成（已满足前置条件者得分更高）。 |
| `unresolved_gap` | `float` | `0.20` | **信息缺口率权重**：主题内必填槽位空白比例（缺口越大，越需要优先调度）。 |
| `conflict_signal` | `float` | `0.15` | **冲突信号权重**：主题内是否存在相互矛盾的槽位值（存在冲突时提升优先级以尽早化解）。 |
| `recent_emergence` | `float` | `0.10` | **最新涌现新鲜度权重**：最新对话中动态涌现出的新主题方向（给予适度探索倾向）。 |
| `continuity` | `float` | `0.15` | **会话连续性权重**：对当前正在进行中的主题给予连续性加分，避免话题频繁跳跃。 |
| `user_relevance` | `float` | `0.05` | **用户相关度权重**：受访者最新发言中主动提及该主题时的相关度加分。 |

---

### 2.4 `strategy` (提问策略规划配置)

指导 `StrategySelector` 规划高层访谈策略（`explore` / `fill_gap` / `deepen` / `resolve_conflict` / `verify` / `confirm_control`）。

| 字段名 | 类型 | 默认值 | 约束 | 功能说明与调优建议 |
|---|---|---|---|---|
| `max_target_slots` | `int` | `1` | >= `1` | 单轮提问聚焦填补的最大槽位数量。保持为 `1` 可保证提问精准聚焦，防止一次性追问多个问题引发用户认知过载。 |
| `short_value_char_threshold` | `int` | `12` | >= `0` | **粗略槽位字数门限**。槽位内容字符数低于此值时，判定受访者描述可能过于简略，系统倾向于触发 `deepen` 策略深入追问细节。 |
| `emergence_deepen_turn_window` | `int` | `2` | >= `1` | 动态涌现话题创建后的连续深挖保护轮次窗口。新话题创建后在此窗口内优先深挖其核心槽位。 |

---

### 2.5 `context_budget` (上下文预算与 Token 裁剪配置)

管理组装到生成 Prompt 中的各语义区块 Token 载荷上限，保障长会话不发生上下文溢出与超支。

| 字段名 | 类型 | 默认值 | 约束 | 功能说明与调优建议 |
|---|---|---|---|---|
| `max_prompt_tokens` | `int` | `3500` | `100` ~ `100000` | 提问生成 Prompt 的全局最大 Token 上限。超出且梯度裁剪后仍超限时，将安全触发保底规则问句。 |
| `max_recent_turn_tokens` | `int` | `800` | `50` ~ `50000` | 历史对话记录区块的最大 Token 预算。 |
| `max_target_evidence_tokens`| `int` | `800` | `50` ~ `50000` | 目标槽位关联证据溯源区块的最大 Token 预算。 |
| `max_known_info_tokens` | `int` | `600` | `50` ~ `50000` | 跨主题已知需求事实清单区块的最大 Token 预算。 |
| `max_current_slots_tokens` | `int` | `600` | `50` ~ `50000` | 当前主题槽位清单区块的最大 Token 预算。 |
| `max_catalog_tokens` | `int` | `400` | `20` ~ `20000` | 全局主题目录区块的最大 Token 预算。 |
| `history_window_size` | `int` | `6` | `0` ~ `100` | 历史对话保留的最大轮次对数量（1 轮对 = 1 问 + 1 答）。 |
| `max_current_topic_slots` | `int` | `15` | `1` ~ `100` | 单个主题装配到 Prompt 中的最大槽位数量。 |

#### 梯度裁剪降级机制说明
当总 Token 超过 `max_prompt_tokens` 或单区块超限时，`ContextBudgetManager` 按以下确定性优先级实施削减：
1. **P7 裁剪**：全量主题目录降级至仅显示当前主题；
2. **P6 裁剪**：按时间顺序淘汰最旧的跨主题已知事实；
3. **P5 裁剪**：对话历史窗口由远及近滑动裁剪（确保始终保留最新一轮 Q&A）；
4. **P4 裁剪**：剔除当前主题中的非必需（可选）槽位定义。

---

### 2.6 `runtime` (运行时系统行为配置)

控制系统底层存储、日志记录、轮次安全及结构演化参数。

| 字段名 | 类型 | 默认值 | 功能说明与调优建议 |
|---|---|---|---|
| `max_turns` | `int` | `50` | 单次访谈的最大轮次安全熔断上限，防止异常对话进入死循环。 |
| `atomic_state_write` | `bool` | `true` | 是否启用原子重命名方式写入 `state.json`。生产环境建议保持 `true`，防止意外断电导致文件写入中途损坏。 |
| `log_raw_llm_response` | `bool` | `true` | 是否在 `llm_calls.jsonl` 中完整记录大模型的原始文本返回。方便全链路离线审计与重放。 |
| `save_prompt_text` | `bool` | `true` | 是否在 `llm_calls.jsonl` 中记录渲染后的完整 Prompt。 |
| `runs_dir` | `str` | `"runs"` | 访谈运行产物的根目录路径（支持相对路径与绝对路径）。 |
| `prompts_dir` | `str` | `"prompts"` | 提示词模板文件的存放目录。 |
| `strategy_completion_threshold`| `float`| `0.60`| 槽位完成度达标门限。当必填槽位填充率高于此值且无空缺时，系统触发 `verify` 策略进入确认收敛阶段。 |
| `priority_dep_weight` | `float` | `0.60` | 初始静态拓扑排序中，依赖关系深度与连通度的权重。 |
| `priority_section_weight` | `float` | `0.40` | 初始静态拓扑排序中，章节自然顺序的权重。 |

---

## 3. 典型调优场景建议

### 场景 A：开发与调试模式（追求低成本与快速反馈）
- 模型可配置为轻量级模型（如 `gpt-4o-mini` 或 `deepseek-chat`）；
- `max_retries` 设为 `1`，`timeout_seconds` 设为 `30.0`；
- 保持 `log_raw_llm_response: true` 以便排查 LLM 返回格式问题。

### 场景 B：严谨需求访谈（追求高完备度与深挖细节）
- 调高 `strategy.short_value_char_threshold` 至 `20`，引导系统对简短回答发起更深入的追问；
- 调高 `scheduler.weights.unresolved_gap` 与 `scheduler.weights.conflict_signal` 至 `0.25`，确保遗漏槽位与矛盾点优先得到彻底澄清；
- 使用长上下文能力强的大模型（如 `gpt-4o`）。

### 场景 C：边缘/本地环境（严格限制 Context 长度）
- 调低 `context_budget.max_prompt_tokens` 至 `2000`；
- 将 `context_budget.history_window_size` 缩减至 `3`；
- 系统将自动依靠 P7~P4 优先级策略保持核心上下文紧凑。
