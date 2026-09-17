from typing import Any, Optional
from pydantic import BaseModel, Field


class LLMCallRecord(BaseModel):
    call_id: str
    turn_id: Optional[str] = None
    module: Optional[str] = None
    prompt_name: Optional[str] = None
    model: str = "default"
    temperature: float = 0.2
    prompt: str = ""
    query: Optional[str] = None
    request: dict[str, Any] = Field(default_factory=dict)
    raw_response: Optional[str] = None
    parsed_response: Optional[Any] = None
    latency_ms: float = 0.0
    status: str = "ok"  # "ok" | "success" | "error"
    error_message: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
