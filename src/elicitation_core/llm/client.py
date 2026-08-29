import asyncio
import json
import re
import time
import uuid
from typing import Any, Callable, Optional, Type, TypeVar
import httpx
from pydantic import BaseModel

from ..config import ModelConfig
from ..models.run_record import RunError
from .exceptions import LLMConfigurationError, LLMOutputError, LLMTransportError
from .schemas import LLMCallRecord

T = TypeVar("T", bound=BaseModel)


def extract_json_str(raw: str) -> str:
    """Extract clean JSON string from potential markdown fences or surrounding noise."""
    s = raw.strip()
    if s.startswith("```"):
        lines = s.split("\n")
        if len(lines) >= 2 and lines[0].startswith("```"):
            if lines[-1].strip() == "```":
                s = "\n".join(lines[1:-1]).strip()
            else:
                s = "\n".join(lines[1:]).strip()

    try:
        json.loads(s)
        return s
    except Exception:
        pass

    list_start = s.find("[")
    list_end = s.rfind("]")
    obj_start = s.find("{")
    obj_end = s.rfind("}")

    if list_start != -1 and list_end != -1 and list_end > list_start:
        candidate = s[list_start:list_end + 1]
        try:
            json.loads(candidate)
            return candidate
        except Exception:
            pass

    if obj_start != -1 and obj_end != -1 and obj_end > obj_start:
        candidate = s[obj_start:obj_end + 1]
        try:
            json.loads(candidate)
            return candidate
        except Exception:
            pass

    return s


class LLMClient:
    """Unified asynchronous LLM client with structured output parsing and call auditing."""

    def __init__(
        self,
        config: ModelConfig,
        on_call_completed: Optional[Callable[[LLMCallRecord], None]] = None,
        on_error: Optional[Callable[[RunError], None]] = None,
    ):
        self.config = config
        self.on_call_completed = on_call_completed
        self.on_error = on_error

    def _validate_config(self) -> None:
        api_key = self.config.get_effective_api_key()
        if not self.config.api_url or not self.config.api_url.strip():
            raise LLMConfigurationError("LLM API URL is not configured.")
        if not api_key:
            raise LLMConfigurationError("LLM API Key is missing. Set it in config or environment.")
        if not self.config.model_name or not self.config.model_name.strip():
            raise LLMConfigurationError("LLM model name is not configured.")

    async def complete_text(
        self,
        prompt: str,
        query: str = "",
        turn_id: Optional[str] = None,
        module: Optional[str] = None,
        prompt_name: Optional[str] = None,
        record_completed: bool = True,
        **kwargs: Any,
    ) -> str:
        """Call LLM and return raw response string."""
        try:
            self._validate_config()
        except LLMConfigurationError as cfg_err:
            if self.on_error:
                run_err = RunError(
                    error_id=f"err_{uuid.uuid4().hex[:8]}",
                    turn_id=turn_id,
                    module=module or "LLMClient",
                    error_type="llm_configuration_error",
                    message=str(cfg_err),
                    recoverable=False,
                )
                self.on_error(run_err)
            raise

        call_id = f"call_{uuid.uuid4().hex[:12]}"
        api_key = self.config.get_effective_api_key()

        messages = [{"role": "system", "content": prompt}]
        if query and query.strip():
            messages.append({"role": "user", "content": query.strip()})

        # Sanitize request without API keys
        request_data = {
            "model": self.config.model_name,
            "messages": messages,
            "temperature": self.config.temperature,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        attempts = max(1, self.config.max_retries)
        base_delay = 1.0
        last_exception: Optional[Exception] = None
        start_time = time.perf_counter()

        for attempt in range(attempts):
            try:
                async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                    response = await client.post(
                        self.config.api_url,
                        json=request_data,
                        headers=headers,
                    )

                if response.status_code == 200:
                    result = response.json()
                    choices = result.get("choices", [])
                    if choices and len(choices) > 0:
                        content = choices[0]["message"]["content"].strip()
                        if not content:
                            raise LLMOutputError("LLM returned empty or whitespace response.")
                        latency_ms = (time.perf_counter() - start_time) * 1000.0

                        if record_completed and self.on_call_completed:
                            record = LLMCallRecord(
                                call_id=call_id,
                                turn_id=turn_id,
                                module=module,
                                prompt_name=prompt_name,
                                model=self.config.model_name,
                                temperature=self.config.temperature,
                                prompt=prompt,
                                query=query if query else None,
                                request=request_data,
                                raw_response=content,
                                parsed_response=None,
                                latency_ms=latency_ms,
                                status="ok",
                                metadata=kwargs.get("metadata", {}),
                            )
                            self.on_call_completed(record)

                        return content
                    else:
                        raise LLMOutputError(f"Unexpected response payload format: {result}")
                else:
                    error_detail = f"HTTP {response.status_code}: {response.text}"
                    raise LLMTransportError(f"LLM API request failed: {error_detail}")

            except (httpx.ConnectError, httpx.TimeoutException) as transport_err:
                last_exception = LLMTransportError(f"LLM transport error: {str(transport_err)}")
            except LLMTransportError as transport_err:
                last_exception = transport_err
            except LLMOutputError as output_err:
                last_exception = output_err
            except Exception as unk_err:
                last_exception = LLMTransportError(f"Unexpected LLM call failure: {str(unk_err)}")

            if attempt < attempts - 1:
                await asyncio.sleep(base_delay * (2 ** attempt))

        latency_ms = (time.perf_counter() - start_time) * 1000.0
        if self.on_call_completed:
            err_record = LLMCallRecord(
                call_id=call_id,
                turn_id=turn_id,
                module=module,
                prompt_name=prompt_name,
                model=self.config.model_name,
                temperature=self.config.temperature,
                prompt=prompt,
                query=query if query else None,
                request=request_data,
                raw_response=None,
                parsed_response=None,
                latency_ms=latency_ms,
                status="error",
                error_message=str(last_exception),
                metadata=kwargs.get("metadata", {}),
            )
            self.on_call_completed(err_record)

        if self.on_error:
            if isinstance(last_exception, LLMConfigurationError):
                err_type = "llm_configuration_error"
            elif isinstance(last_exception, LLMOutputError):
                err_type = "llm_output_error"
            elif isinstance(last_exception, LLMTransportError):
                err_type = "transport_error"
            else:
                err_type = "transport_error"

            run_err = RunError(
                error_id=f"err_{uuid.uuid4().hex[:8]}",
                turn_id=turn_id,
                module=module or "LLMClient",
                error_type=err_type,
                message=str(last_exception),
                recoverable=True,
            )
            self.on_error(run_err)

        raise last_exception or LLMTransportError("LLM call failed after retries.")

    async def complete_json(
        self,
        prompt: str,
        query: str = "",
        turn_id: Optional[str] = None,
        module: Optional[str] = None,
        prompt_name: Optional[str] = None,
        **kwargs: Any,
    ) -> Any:
        """Call LLM and parse output as JSON (list or dict) with audit recording of parsed response."""
        start_time = time.perf_counter()
        call_id = f"call_{uuid.uuid4().hex[:12]}"
        try:
            raw = await self.complete_text(
                prompt=prompt,
                query=query,
                turn_id=turn_id,
                module=module,
                prompt_name=prompt_name,
                record_completed=False,
                **kwargs,
            )
        except TypeError:
            try:
                raw = await self.complete_text(prompt=prompt, query=query)
            except TypeError:
                raw = await self.complete_text(prompt=prompt)

        json_str = extract_json_str(raw)
        try:
            parsed = json.loads(json_str)
            latency_ms = (time.perf_counter() - start_time) * 1000.0

            if self.on_call_completed:
                request_data = {
                    "model": self.config.model_name,
                    "messages": [{"role": "system", "content": prompt}] + ([{"role": "user", "content": query}] if query else []),
                    "temperature": self.config.temperature,
                }
                record = LLMCallRecord(
                    call_id=call_id,
                    turn_id=turn_id,
                    module=module,
                    prompt_name=prompt_name,
                    model=self.config.model_name,
                    temperature=self.config.temperature,
                    prompt=prompt,
                    query=query if query else None,
                    request=request_data,
                    raw_response=raw,
                    parsed_response=parsed,
                    latency_ms=latency_ms,
                    status="ok",
                )
                self.on_call_completed(record)

            return parsed
        except Exception as e:
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            if self.on_call_completed:
                request_data = {
                    "model": self.config.model_name,
                    "messages": [{"role": "system", "content": prompt}] + ([{"role": "user", "content": query}] if query else []),
                    "temperature": self.config.temperature,
                }
                record = LLMCallRecord(
                    call_id=call_id,
                    turn_id=turn_id,
                    module=module,
                    prompt_name=prompt_name,
                    model=self.config.model_name,
                    temperature=self.config.temperature,
                    prompt=prompt,
                    query=query if query else None,
                    request=request_data,
                    raw_response=raw,
                    parsed_response=None,
                    latency_ms=latency_ms,
                    status="error",
                    error_message=f"JSON parse error: {str(e)}",
                )
                self.on_call_completed(record)

            if self.on_error:
                run_err = RunError(
                    error_id=f"err_{uuid.uuid4().hex[:8]}",
                    turn_id=turn_id,
                    module=module or "LLMClient.complete_json",
                    error_type="llm_output_error",
                    message=f"JSON parse error from raw output: {raw[:200]}",
                    recoverable=True,
                )
                self.on_error(run_err)

            raise LLMOutputError(f"Failed to parse LLM response as JSON: {raw}") from e

    async def complete_model(
        self,
        model_cls: Type[T],
        prompt: str,
        query: str = "",
        turn_id: Optional[str] = None,
        module: Optional[str] = None,
        prompt_name: Optional[str] = None,
        **kwargs: Any,
    ) -> T:
        """Call LLM and parse output directly into target Pydantic model."""
        parsed_data = await self.complete_json(
            prompt=prompt,
            query=query,
            turn_id=turn_id,
            module=module,
            prompt_name=prompt_name,
            **kwargs,
        )
        if isinstance(parsed_data, dict):
            return model_cls(**parsed_data)
        elif isinstance(parsed_data, list):
            raise LLMOutputError(f"Expected JSON object for {model_cls.__name__}, got list: {parsed_data}")
        else:
            raise LLMOutputError(f"Expected JSON object for {model_cls.__name__}, got {type(parsed_data)}")
