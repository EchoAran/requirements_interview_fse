from pathlib import Path
from typing import Any, Optional
import os
import yaml
from pydantic import BaseModel, Field, model_validator
from .models.scheduling import SchedulerWeights


class ModelConfig(BaseModel):
    provider: str = Field(default="openai_compatible")
    api_url: str = Field(default="https://api.rcouyi.com/v1/chat/completions")
    api_key_env: str = Field(default="LLM_API_KEY")
    api_key: str = Field(default="")
    model_name: str = Field(default="gpt-4o")
    temperature: float = Field(default=0.2)
    timeout_seconds: float = Field(default=60.0)
    max_retries: int = Field(default=3)

    def get_effective_api_key(self) -> str:
        if self.api_key and self.api_key.strip():
            return self.api_key.strip()
        env_val = os.getenv(self.api_key_env, "")
        if env_val and env_val.strip():
            return env_val.strip()
        return os.getenv("OPENAI_API_KEY", "").strip()

    def sanitized_dict(self) -> dict[str, Any]:
        """Returns model configuration with API keys masked/removed."""
        d = self.model_dump()
        d["api_key"] = ""
        return d


class IntentConfig(BaseModel):
    confidence_threshold: float = Field(default=0.60)
    confirmation_threshold: float = Field(default=0.40)


class SchedulerConfig(BaseModel):
    weights: SchedulerWeights = Field(default_factory=SchedulerWeights)

    @model_validator(mode="after")
    def validate_and_normalize_weights(self) -> "SchedulerConfig":
        total = (
            self.weights.initial_prior
            + self.weights.dependency_readiness
            + self.weights.unresolved_gap
            + self.weights.conflict_signal
            + self.weights.recent_emergence
            + self.weights.continuity
            + self.weights.user_relevance
        )
        if total > 0 and abs(total - 1.0) > 1e-4:
            # Normalize automatically
            self.weights = SchedulerWeights(
                initial_prior=self.weights.initial_prior / total,
                dependency_readiness=self.weights.dependency_readiness / total,
                unresolved_gap=self.weights.unresolved_gap / total,
                conflict_signal=self.weights.conflict_signal / total,
                recent_emergence=self.weights.recent_emergence / total,
                continuity=self.weights.continuity / total,
                user_relevance=self.weights.user_relevance / total,
            )
        return self


class StrategyConfig(BaseModel):
    max_target_slots: int = Field(default=1)
    short_value_char_threshold: int = Field(default=12)
    emergence_deepen_turn_window: int = Field(default=2)


class ContextBudgetConfig(BaseModel):
    max_prompt_tokens: int = Field(default=3500, ge=100, le=100000)
    max_recent_turn_tokens: int = Field(default=800, ge=50, le=50000)
    max_target_evidence_tokens: int = Field(default=800, ge=50, le=50000)
    max_known_info_tokens: int = Field(default=600, ge=50, le=50000)
    max_current_slots_tokens: int = Field(default=600, ge=50, le=50000)
    max_catalog_tokens: int = Field(default=400, ge=20, le=20000)
    history_window_size: int = Field(default=6, ge=0, le=100)
    max_current_topic_slots: int = Field(default=15, ge=1, le=100)


class RuntimeConfig(BaseModel):
    max_turns: int = Field(default=50)
    atomic_state_write: bool = Field(default=True)
    log_raw_llm_response: bool = Field(default=True)
    save_prompt_text: bool = Field(default=True)
    runs_dir: str = Field(default="runs")
    prompts_dir: str = Field(default="prompts")
    strategy_completion_threshold: float = Field(default=0.6)
    operation_selection_theta: float = Field(default=0.6)
    priority_dep_weight: float = Field(default=0.6)
    priority_section_weight: float = Field(default=0.4)
    intent: IntentConfig = Field(default_factory=IntentConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    context_budget: ContextBudgetConfig = Field(default_factory=ContextBudgetConfig)


class AppConfig(BaseModel):
    model: ModelConfig = Field(default_factory=ModelConfig)
    intent: IntentConfig = Field(default_factory=IntentConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    context_budget: ContextBudgetConfig = Field(default_factory=ContextBudgetConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)

    @model_validator(mode="before")
    @classmethod
    def sync_nested_configs(cls, values: Any) -> Any:
        if isinstance(values, dict):
            # Sync top-level intent/scheduler/context_budget to runtime if provided at top-level
            if "intent" in values and "runtime" in values and isinstance(values["runtime"], dict):
                values["runtime"].setdefault("intent", values["intent"])
            elif "runtime" in values and isinstance(values["runtime"], dict) and "intent" in values["runtime"]:
                values.setdefault("intent", values["runtime"]["intent"])

            if "scheduler" in values and "runtime" in values and isinstance(values["runtime"], dict):
                values["runtime"].setdefault("scheduler", values["scheduler"])
            elif "runtime" in values and isinstance(values["runtime"], dict) and "scheduler" in values["runtime"]:
                values.setdefault("scheduler", values["runtime"]["scheduler"])

            if "context_budget" in values and "runtime" in values and isinstance(values["runtime"], dict):
                values["runtime"].setdefault("context_budget", values["context_budget"])
            elif "runtime" in values and isinstance(values["runtime"], dict) and "context_budget" in values["runtime"]:
                values.setdefault("context_budget", values["runtime"]["context_budget"])
        return values

    @classmethod
    def load_from_yaml(cls, config_path: str | Path) -> "AppConfig":
        path = Path(config_path)
        if not path.exists():
            return cls()
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls(**data)

    def to_sanitized_yaml(self) -> str:
        """Serializes full configuration to YAML without exposing raw API keys."""
        data = self.model_dump()
        if "model" in data:
            data["model"]["api_key"] = ""
        return yaml.dump(data, allow_unicode=True, default_flow_style=False)

    def get_runs_path(self, base_dir: Path | None = None) -> Path:
        base = base_dir or Path.cwd()
        runs_p = Path(self.runtime.runs_dir)
        if runs_p.is_absolute():
            return runs_p
        return base / runs_p

    def get_prompts_path(self, base_dir: Path | None = None) -> Path:
        base = base_dir or Path.cwd()
        prompts_p = Path(self.runtime.prompts_dir)
        if prompts_p.is_absolute():
            return prompts_p
        return base / prompts_p
