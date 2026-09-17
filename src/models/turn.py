from typing import Any, Literal, Optional
from pydantic import BaseModel, Field


class TurnRecord(BaseModel):
    turn_id: str
    message_id: Optional[str] = None
    turn_index: int
    topic_id: str
    role: Literal["Interviewer", "Interviewee"]
    message_content: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        if not self.message_id:
            self.message_id = f"msg_{self.turn_id}"

    @property
    def content(self) -> str:
        return self.message_content
