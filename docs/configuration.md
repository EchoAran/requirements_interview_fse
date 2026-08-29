# Elicitation Core Configuration Guide

This document provides a comprehensive technical reference for all configuration parameters in **Elicitation Core**, including schema types, constraints, default values, algorithm behaviors, and tuning recommendations.

---

## 1. Configuration Structure & Loading Rules

### 1.1 File Organization & Discovery
Configuration files reside in the `configs/` directory:
- **`configs/default.example.yaml`**: The canonical example template containing all valid keys, default values, and inline documentation;
- **`configs/default.yaml`**: The active local configuration file evaluated at runtime.

To get started, copy the template file to create your active configuration:
```bash
# Linux / macOS
cp configs/default.example.yaml configs/default.yaml

# Windows PowerShell
Copy-Item configs/default.example.yaml configs/default.yaml
```

### 1.2 Configuration Loading Hierarchy
The system uses strong typing via Pydantic V2 (`AppConfig.load_from_yaml()`). Configurations are resolved in the following order:
1. **Explicit CLI Argument**: Specified via `--config <path>`;
2. **Default File Path**: Falls back to `configs/default.yaml` if present;
3. **Built-in Safe Defaults**: If no file is found, the system instantiates default configuration instances safely in-memory.

The same rule applies when resuming an interrupted project. The configuration supplied to the current invocation is authoritative; initialization-time configuration is not restored from the run directory. Changing token budgets, model settings, timeouts, or retry counts affects only the pending and future work. Previously committed interview Turns, Evidence, StateEvents, and Decisions remain valid.

### 1.3 Security & Credential Protection
- **Environment-based Key Resolution**: We strongly advise configuring `api_key_env` (defaults to `"LLM_API_KEY"`) rather than hardcoding API secrets in plaintext.
- **Audit Log Sanitization**: `llm_calls.jsonl` must not contain plaintext `api_key` values. Runtime configuration is not persisted as a project-level snapshot.

---

## 2. Configuration Field Reference

### 2.1 `model` (Large Language Model Service)

Configures the remote/local Large Language Model endpoint, inference hyperparameters, and fault-tolerance retries.

| Field Name | Type | Default | Valid Range / Options | Description & Recommendations |
|---|---|---|---|---|
| `provider` | `str` | `"openai_compatible"` | Recommended: `"openai_compatible"` | LLM communication protocol provider. Compatible with any endpoint adhering to the OpenAI Chat Completions standard. |
| `api_url` | `str` | `"https://api.openai.com/v1/chat/completions"` | Valid HTTP/HTTPS URL | Full URL for the chat completions endpoint. |
| `api_key_env` | `str` | `"LLM_API_KEY"` | Environment variable name | Name of the environment variable used to retrieve the API key. Falls back to `OPENAI_API_KEY` if the designated variable is unset. |
| `api_key` | `str` | `""` | String | Explicit API key string. Leave empty to automatically resolve from `api_key_env`. |
| `model_name` | `str` | `"gpt-4o"` | e.g. `"gpt-4o"`, `"gpt-4o-mini"`, `"deepseek-chat"`, `"qwen-plus"` | Model identifier. Models with strong structured JSON parsing and long context recall are recommended. |
| `temperature` | `float` | `0.2` | `0.0` ~ `1.0` | Sampling temperature. Lower values (0.1 ~ 0.3) ensure deterministic JSON formatting and reliable strategy planning. |
| `timeout_seconds`| `float`| `60.0` | > `0.0` | HTTP request timeout in seconds. |
| `max_retries` | `int` | `3` | >= `0` | Maximum automatic retries with exponential backoff upon network timeouts, rate limiting (429), or server errors. |

#### Provider Configuration Examples

```yaml
# OpenAI Official Endpoint
model:
  provider: "openai_compatible"
  api_url: "https://api.openai.com/v1/chat/completions"
  api_key_env: "OPENAI_API_KEY"
  model_name: "gpt-4o"

# DeepSeek Official Endpoint
model:
  provider: "openai_compatible"
  api_url: "https://api.deepseek.com/v1/chat/completions"
  api_key_env: "DEEPSEEK_API_KEY"
  model_name: "deepseek-chat"

# Alibaba DashScope (OpenAI-compatible mode)
model:
  provider: "openai_compatible"
  api_url: "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
  api_key_env: "DASHSCOPE_API_KEY"
  model_name: "qwen-plus"

# Local Ollama Deployment
model:
  provider: "openai_compatible"
  api_url: "http://localhost:11434/v1/chat/completions"
  api_key: "ollama"
  model_name: "qwen2.5:14b"
```

---

### 2.2 `intent` (User Intent Control)

Manages the sensitivity and confirmation thresholds for explicit user conversational interventions.

| Field Name | Type | Default | Valid Range | Description & Recommendations |
|---|---|---|---|---|
| `confidence_threshold` | `float` | `0.60` | `0.0` ~ `1.0` | **Direct execution threshold**. When intent recognition confidence $\ge$ this value, the pipeline immediately triggers topic switching (`switch_existing_topic`), topic refusal (`refuse_topic`), backtracking (`return_previous`), or termination (`stop_interview`). |
| `confirmation_threshold` | `float` | `0.40` | `0.0` ~ `confidence_threshold` | **Confirmation threshold**. When confidence is within `[confirmation_threshold, confidence_threshold)`, the system triggers the `confirm_control` strategy to clarify the user's intent. Confidence below this value is treated as standard domain content. |

---

### 2.3 `scheduler.weights` (Seven-Factor Dynamic Scheduler Weights)

The scheduler computes a multi-dimensional utility score across all unfinished candidate topics when no explicit user override occurs. All 7 weights are automatically normalized upon loading ($\sum w_i = 1.0$).

$$\text{Score}(T) = \sum_{i=1}^7 w_i \cdot \text{Factor}_i(T)$$

| Weight Field Name | Type | Default | Factor Definition & Dynamic Influence |
|---|---|---|---|
| `initial_prior` | `float` | `0.15` | **Initial Prior**: Baseline importance derived from initial requirement outline extraction. |
| `dependency_readiness` | `float` | `0.20` | **Dependency Readiness**: Evaluates topological readiness (higher score if all predecessor topics are completed). |
| `unresolved_gap` | `float` | `0.20` | **Information Gap Ratio**: Proportion of mandatory slots remaining empty (higher gap prompts earlier focus). |
| `conflict_signal` | `float` | `0.15` | **Conflict Signal**: Flags topics containing conflicting/contradictory statements to reconcile them promptly. |
| `recent_emergence` | `float` | `0.10` | **Recent Emergence**: Recency bonus for newly emerged requirement topics. |
| `continuity` | `float` | `0.15` | **Conversational Continuity**: Encourages staying on the current topic until sufficiently complete to maintain dialogue flow. |
| `user_relevance` | `float` | `0.05` | **User Mention Relevance**: Semantic relevance score when the interviewee mentions topics outside the active one. |

---

### 2.4 `strategy` (Question Strategy Planning)

Governs high-level strategy selection (`explore` / `fill_gap` / `deepen` / `resolve_conflict` / `verify` / `confirm_control`) and question scoping.

| Field Name | Type | Default | Valid Range | Description & Recommendations |
|---|---|---|---|---|
| `max_target_slots` | `int` | `1` | >= `1` | Maximum number of target slots to focus on within a single interview turn. Keeping this at `1` prevents overwhelming the interviewee with compound questions. |
| `short_value_char_threshold` | `int` | `12` | >= `0` | **Shallow Answer Threshold**. If a slot value character length is below this number, the content is considered superficial, prompting the system toward the `deepen` strategy. |
| `emergence_deepen_turn_window` | `int` | `2` | >= `1` | Protection window (in turns) for newly created emergent topics to receive dedicated consecutive elaboration. |

---

### 2.5 `context_budget` (Context Budget & Token Management)

Restricts token budgets across distinct semantic prompt blocks, ensuring robust inference and preventing context window overflow.

| Field Name | Type | Default | Valid Range | Description & Recommendations |
|---|---|---|---|---|
| `max_prompt_tokens` | `int` | `3500` | `100` ~ `100000` | Global maximum token cap for the question generation prompt. If essential context still exceeds the budget after progressive trimming, question generation fails without committing the turn; increase the current budget and resume the pending input. |
| `max_recent_turn_tokens` | `int` | `800` | `50` ~ `50000` | Maximum token budget for the recent dialogue history block. |
| `max_target_evidence_tokens`| `int` | `800` | `50` ~ `50000` | Maximum token budget for the target evidence chain traceability block. |
| `max_known_info_tokens` | `int` | `600` | `50` ~ `50000` | Maximum token budget for cross-topic known facts block. |
| `max_current_slots_tokens` | `int` | `600` | `50` ~ `50000` | Maximum token budget for the active topic slots block. |
| `max_catalog_tokens` | `int` | `400` | `20` ~ `20000` | Maximum token budget for the global topic outline catalog block. |
| `history_window_size` | `int` | `6` | `0` ~ `100` | Maximum number of dialogue turn pairs (1 pair = 1 question + 1 answer) retained in the sliding history window. |
| `max_current_topic_slots` | `int` | `15` | `1` ~ `100` | Maximum number of slot definitions rendered into the prompt payload. |

#### Deterministic Progressive Degradation (P7 ~ P4)
When token counts exceed the configured limits, `ContextBudgetManager` trims context blocks according to strict deterministic priority levels:
1. **P7 Trim**: Topic catalog drops down to displaying only the active topic;
2. **P6 Trim**: Cross-topic known facts drop the oldest entries in chronological order;
3. **P5 Trim**: Conversation history trims the oldest turn pairs (guaranteeing at least 1 recent turn pair remains);
4. **P4 Trim**: Active topic slot definitions drop optional (non-mandatory) slots.

If the essential target, evidence, and required current-state blocks still exceed the configured limits, the system must raise `question_context_budget_exceeded`. It must not generate a fallback question or commit Evidence, StateEvents, Decisions, an Interviewer Turn, or a new state snapshot for that attempt. After changing the active configuration, resume the project to retry the original pending Interviewee Turn.

---

### 2.6 `runtime` (Runtime System Configuration)

Configures storage invariants, turn guardrails, logging detail, and topological prioritization.

| Field Name | Type | Default | Description & Recommendations |
|---|---|---|---|
| `max_turns` | `int` | `50` | Maximum turn safety circuit-breaker for a single interview session to prevent infinite execution. |
| `atomic_state_write` | `bool` | `true` | Enables atomic rename writes for `state.json` to prevent partial corruption during sudden termination or power loss. |
| `log_raw_llm_response` | `bool` | `true` | Persists verbatim LLM text responses into `llm_calls.jsonl` for exact offline auditing and replay. |
| `save_prompt_text` | `bool` | `true` | Records rendered prompts into `llm_calls.jsonl` for offline reproducibility. |
| `runs_dir` | `str` | `"runs"` | Root directory path for project runtime output artifacts (relative or absolute). |
| `prompts_dir` | `str` | `"prompts"` | Directory path containing system prompt templates. |
| `strategy_completion_threshold`| `float`| `0.60`| Completion threshold. When mandatory slots have $\ge 60\%$ fill rate with zero missing required slots, the pipeline transitions to `verify`. |
| `priority_dep_weight` | `float` | `0.60` | Weight for dependency depth and connectivity in static topological sorting. |
| `priority_section_weight` | `float` | `0.40` | Weight for natural section sequence in static topological sorting. |

---

## 3. Tuning Recommendations by Scenario

### Scenario A: Development & Debugging (Low Cost, Fast Iteration)
- Model: Use lightweight models such as `gpt-4o-mini` or `deepseek-chat`;
- Set `max_retries: 1` and `timeout_seconds: 30.0`;
- Keep `log_raw_llm_response: true` to easily inspect JSON extraction failures.

### Scenario B: Production Requirements Elicitation (High Precision & Deep Exploration)
- Set `strategy.short_value_char_threshold: 20` to aggressively detect shallow answers and prompt deeper clarification;
- Increase `scheduler.weights.unresolved_gap` and `scheduler.weights.conflict_signal` to `0.25` to prioritize unaddressed requirements and conflicting statements;
- Use frontier reasoning models (e.g. `gpt-4o`).

### Scenario C: Edge / Constrained Context Windows
- Reduce `context_budget.max_prompt_tokens` to `2000`;
- Reduce `context_budget.history_window_size` to `3`;
- The system will automatically rely on P7~P4 progressive trimming to keep payloads minimal while preserving critical requirement evidence.
