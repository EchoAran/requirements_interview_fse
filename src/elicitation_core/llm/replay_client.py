import json
from typing import Any, Optional
from ..config import ModelConfig
from .client import LLMClient
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
        return ""

    async def complete_json(
        self,
        prompt: str,
        query: str = "",
        turn_id: Optional[str] = None,
        module: Optional[str] = None,
        prompt_name: Optional[str] = None,
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
            try:
                clean_text = record.raw_response.strip()
                if clean_text.startswith("```"):
                    lines = clean_text.splitlines()
                    if len(lines) >= 2 and lines[0].startswith("```"):
                        lines = lines[1:]
                    if len(lines) >= 1 and lines[-1].startswith("```"):
                        lines = lines[:-1]
                    clean_text = "\n".join(lines).strip()
                return json.loads(clean_text)
            except Exception:
                return {}
        return {}
