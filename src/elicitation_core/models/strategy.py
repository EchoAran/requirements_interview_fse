from typing import Any, Literal, Optional
from pydantic import BaseModel, Field
from .interpretation import TopicDigest
from .turn import TurnRecord

StrategyCode = Literal[
    "explore",
    "fill_gap",
    "deepen",
    "resolve_conflict",
    "verify",
    "confirm_control",
]


class SlotDigest(BaseModel):
    slot_id: str
    slot_number: str
    key: str
    value: Optional[Any] = None
    is_required: bool = True
    state: str = "empty"
    origin: str = "seed"


class TopicCatalogItem(BaseModel):
    topic_id: str
    topic_number: str
    topic_content: str
    status: str
    origin: str = "seed"


class KnownSlotFact(BaseModel):
    slot_id: str
    key: str
    value: Optional[Any] = None
    state: str = "empty"
    evidence_refs: list[str] = Field(default_factory=list)


class KnownInfoDigest(BaseModel):
    topic_id: str
    topic_number: str
    topic_content: str
    filled_slots: list[KnownSlotFact] = Field(default_factory=list)


class QuestionTransition(BaseModel):
    kind: Literal[
        "maintain",
        "user_refused_and_switched",
        "user_requested_switch",
        "scheduler_switched",
        "confirm_control",
    ]
    from_topic_title: Optional[str] = None
    to_topic_title: Optional[str] = None
    user_facing_reason: Optional[str] = None


class EvidenceSnippet(BaseModel):
    evidence_id: str
    turn_id: Optional[str] = None
    speaker: Optional[str] = None
    content: str


class ConflictClaim(BaseModel):
    value: Any
    evidence_snippets: list[EvidenceSnippet] = Field(default_factory=list)


class TargetSlotContext(BaseModel):
    slot_id: str
    semantic_key: str
    current_value: Optional[Any] = None
    state: str = "empty"
    is_required: bool = True
    evidence_snippets: list[EvidenceSnippet] = Field(default_factory=list)


class TargetContext(BaseModel):
    target_slots: list[TargetSlotContext] = Field(default_factory=list)
    target_topics: list[TopicDigest] = Field(default_factory=list)
    conflict_claims: dict[str, list[ConflictClaim]] = Field(default_factory=dict)
    target_relations: list[dict[str, Any]] = Field(default_factory=list)
    verify_facts: list[dict[str, Any]] = Field(default_factory=list)


class QuestionPlan(BaseModel):
    strategy: StrategyCode
    topic_id: str
    target_slot_ids: list[str] = Field(default_factory=list)
    target_relation_ids: list[str] = Field(default_factory=list)
    target_conflict_slot_ids: list[str] = Field(default_factory=list)
    target_topic_id: Optional[str] = None
    transition_from_topic_id: Optional[str] = None
    control_intent: Optional[str] = None


class QuestionGenerationInput(BaseModel):
    plan: QuestionPlan
    topic: TopicDigest
    topic_catalog: list[TopicCatalogItem] = Field(..., min_length=1)
    target_context: TargetContext
    topic_slots: list[SlotDigest] = Field(default_factory=list)
    recent_turns: list[TurnRecord] = Field(default_factory=list)
    project_known_info: list[KnownInfoDigest] = Field(default_factory=list)
    scheduler_transition: Optional[QuestionTransition] = None
    other_slots_omitted_count: int = Field(default=0, ge=0)




