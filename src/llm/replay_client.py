import json
from typing import Any, Optional
from config import ModelConfig
from .client import LLMClient, extract_json_str
from .exceptions import LLMOutputError
from .schemas import LLMCallRecord


class ReplayLLMClient(LLMClient):
    """Replays recorded LLM call responses offline without performing actual network requests."""

    def __init__(
        self,
        call_records: list[LLMCallRecord],
        config: Optional[ModelConfig] = None,
    ):
        super().__init__(config=config or ModelConfig())
        self.call_records = list(call_records)
        self.current_index = 0
        self.playback_log: list[dict[str, Any]] = []

    async def complete_text(
        self,
        prompt: str,
        query: str = "",
        turn_id: Optional[str] = None,
        module: Optional[str] = None,
        prompt_name: Optional[str] = None,
        record_completed: bool = True,
        metadata: Optional[dict] = None,
        **kwargs: Any,
    ) -> str:
        if self.current_index >= len(self.call_records):
            raise RuntimeError(
                f"Replay exhausted: attempted call #{self.current_index + 1} (module={module}, prompt_name={prompt_name}), "
                f"but only {len(self.call_records)} calls were recorded."
            )

        record = self.call_records[self.current_index]
        self.current_index += 1

        self.playback_log.append({
            "index": self.current_index - 1,
            "call_id": record.call_id,
            "module": record.module or module,
            "prompt_name": record.prompt_name or prompt_name,
            "status": record.status,
        })

        if isinstance(record.raw_response, str) and record.raw_response.strip():
            return record.raw_response.strip()
        elif record.parsed_response is not None:
            if isinstance(record.parsed_response, (dict, list)):
                return json.dumps(record.parsed_response, ensure_ascii=False)
            return str(record.parsed_response)
        raise LLMOutputError(
            f"Replay record #{self.current_index - 1} (module={module}, prompt_name={prompt_name}) contains no response"
        )

    async def complete_json(
        self,
        prompt: str,
        query: str = "",
        turn_id: Optional[str] = None,
        module: Optional[str] = None,
        prompt_name: Optional[str] = None,
        **kwargs: Any,
    ) -> Any:
        if self.current_index >= len(self.call_records):
            raise RuntimeError(
                f"Replay exhausted: attempted JSON call #{self.current_index + 1} (module={module}, prompt_name={prompt_name}), "
                f"but only {len(self.call_records)} calls were recorded."
            )

        record = self.call_records[self.current_index]
        self.current_index += 1

        self.playback_log.append({
            "index": self.current_index - 1,
            "call_id": record.call_id,
            "module": record.module or module,
            "prompt_name": record.prompt_name or prompt_name,
            "status": record.status,
        })

        if record.parsed_response is not None:
            return record.parsed_response

        if isinstance(record.raw_response, str) and record.raw_response.strip():
            return json.loads(extract_json_str(record.raw_response))

        raise LLMOutputError(
            f"Replay record #{self.current_index - 1} (module={module}, prompt_name={prompt_name}) contains no response"
        )
