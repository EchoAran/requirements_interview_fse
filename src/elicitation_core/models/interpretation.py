from typing import Any, Literal, Optional
from pydantic import BaseModel, Field


class TurnPair(BaseModel):
    round_index: int
    interviewer_message: str
    interviewee_message: str
    user_turn_id: str


class TopicDigest(BaseModel):
    topic_id: str
    topic_number: str
    topic_content: str
    section_id: str
    section_number: str
    slots_keys: list[str] = Field(default_factory=list)
    status: str = "Pending"


class ProjectDigest(BaseModel):
    project_id: str
    project_name: str
    turn_index: int
    current_topic_id: Optional[str] = None
    total_topics: int = 0
    total_slots: int = 0


class EvidenceInterpretationInput(BaseModel):
    current_topic_id: str
    latest_turn: TurnPair
    topic_catalog: list[TopicDigest]
    project_state_digest: ProjectDigest


class AffectedTopic(BaseModel):
    topic_id: str
    topic_number: Optional[str] = None
    evidence_message_ids: list[str] = Field(default_factory=list)
    relevance: float = Field(default=1.0, ge=0.0, le=1.0)


class EmergentTopicCandidate(BaseModel):
    title: str
    description: str
    suggested_section_id: Optional[str] = None
    suggested_slots: list[str] = Field(default_factory=list)
    evidence_message_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class RelationCandidate(BaseModel):
    source_topic_id: str
    target_topic_id: str
    relation_type: Literal["depends_on"] = "depends_on"
    evidence_message_ids: list[str] = Field(default_factory=list)


class EvidenceInterpretation(BaseModel):
    affected_existing_topics: list[AffectedTopic] = Field(default_factory=list)
    emergent_topic_candidates: list[EmergentTopicCandidate] = Field(default_factory=list)
    relation_candidates: list[RelationCandidate] = Field(default_factory=list)


class EmergentResolution(BaseModel):
    action: Literal["create", "merge", "ignore"]
    target_topic_id: Optional[str] = None
    reason_code: Literal[
        "distinct_concern",
        "duplicate_synonym",
        "merge_into_existing",
        "insufficient_evidence",
        "out_of_scope"
    ]
