from typing import Optional
from pydantic import BaseModel, Field


class DependencyEdge(BaseModel):
    source: str = Field(description="Source topic_number that target depends on")
    target: str = Field(description="Target topic_number")


class TopicPriorityItem(BaseModel):
    topic_number: str
    topic_content: Optional[str] = None
    status: Optional[str] = None
    core: float = 0.0


class PriorityResult(BaseModel):
    edges: list[DependencyEdge] = Field(default_factory=list)
    order: list[str] = Field(default_factory=list, description="Ordered topic numbers by priority")
    ranked_items: list[TopicPriorityItem] = Field(default_factory=list)
