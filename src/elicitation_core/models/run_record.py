from datetime import datetime, timezone
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field


class RunError(BaseModel):
    error_id: str
    turn_id: Optional[str] = None
    module: str
    error_type: Literal[
        "transport_error",
        "empty_output",
        "prompt_build_error",
        "question_context_budget_exceeded",
        "llm_transport_error",
        "llm_output_error",
        "llm_configuration_error",
        "schema_validation_error",
        "state_invariant_error",
        "storage_error",
        "runtime_error",
    ]
    message: str
    recoverable: bool = True
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class UnifiedDecisionRecord(BaseModel):
    decision_id: str
    turn_id: str
    intent: dict[str, Any] = Field(default_factory=dict)
    scheduler: dict[str, Any] = Field(default_factory=dict)
    strategy: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def selected_topic_id(self) -> Optional[str]:
        return self.scheduler.get("selected_topic_id")

    @property
    def selected_topic_number(self) -> Optional[str]:
        return self.scheduler.get("selected_topic_number")

    @property
    def candidate_scores(self) -> list[Any]:
        raw_scores = self.scheduler.get("candidate_scores", [])
        if raw_scores and isinstance(raw_scores[0], dict):
            from .scheduling import TopicScore
            return [TopicScore(**s) for s in raw_scores]
        return raw_scores
