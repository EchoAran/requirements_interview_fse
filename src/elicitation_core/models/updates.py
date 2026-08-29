from typing import Optional, Literal
from pydantic import BaseModel, ConfigDict, Field
from .event import StateEvent


class SlotUpdateProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic_number: str
    slot_number: str
    slot_key: Optional[str] = None
    new_value: Optional[str] = None
    operation: Optional[Literal["add", "update", "refine", "conflict", "invalidate", "clear"]] = None
    evidence_message_ids: list[str] = Field(default_factory=list)


class OperationConfidence(BaseModel):
    operation: str
    score: float


class OperationSelectionResult(BaseModel):
    best_operation: str
    confidence_scores: list[OperationConfidence] = Field(default_factory=list)


class StepResult(BaseModel):
    project_id: str
    turn_index: int
    current_topic_id: Optional[str] = None
    current_topic_number: Optional[str] = None
    current_topic_content: Optional[str] = None
    next_question: str
    is_finished: bool = False
    finish_message: Optional[str] = None
    selected_strategy: Optional[str] = None
    selected_operation: Optional[str] = None
    state_events: list[StateEvent] = Field(default_factory=list)
