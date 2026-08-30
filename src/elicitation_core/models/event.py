from typing import Any, Literal, Optional
from pydantic import BaseModel, Field


class EvidenceRef(BaseModel):
    evidence_id: str
    source_type: Literal["initial", "interview_turn"]
    turn_id: Optional[str] = None
    message_id: Optional[str] = None
    content: str


class StateEvent(BaseModel):
    event_id: str
    turn_id: Optional[str] = None
    event_type: Literal[
        "slot_value_changed",
        "slot_created",
        "topic_status_changed",
        "topic_created",
        "project_status_changed",
        "turn_advanced",
        "dependency_added",
        "dependency_removed",
    ]
    entity_type: Literal["slot", "topic", "project", "section", "dependency"]
    entity_id: str
    before: dict[str, Any] = Field(default_factory=dict)
    after: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
