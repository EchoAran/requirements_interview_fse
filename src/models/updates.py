from typing import Any, Optional, Literal
from pydantic import BaseModel, ConfigDict, Field
from .event import StateEvent


class StructuredTargetContext(BaseModel):
    strategy: Optional[str] = None
    target_slot_ids: list[str] = Field(default_factory=list)
    deepening_reason: Optional[str] = None
    target_slots_snapshot: list[dict[str, Any]] = Field(default_factory=list)




class StepResult(BaseModel):
    project_id: str
    turn_index: int
    current_topic_id: Optional[str] = None
    current_topic_number: Optional[str] = None
    current_topic_content: Optional[str] = None
    next_question: str
    is_finished: bool = False
    finish_message: Optional[str] = None
    termination_reason: Optional[Literal["max_turns_reached"]] = None
    termination_message: Optional[str] = None
    selected_strategy: Optional[str] = None
    selected_operation: Optional[str] = None
    state_events: list[StateEvent] = Field(default_factory=list)
