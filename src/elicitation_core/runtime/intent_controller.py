import json
from pathlib import Path
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, Field

from ..llm.client import LLMClient
from ..models.interpretation import TopicDigest, TurnPair
from ..models.scheduling import IntentDecision
from ..models.state import TopicState


class IntentDetectionLLMOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Literal[
        "none",
        "stop_interview",
        "refuse_current_topic",
        "switch_existing_topic",
        "return_previous_topic",
    ]
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    target_topic_number: Optional[str] = None
    target_topic_id: Optional[str] = None
    explanation: Optional[str] = None


class IntentController:
    """Detects explicit conversational control intentions (stop, refuse, switch, return) with confidence calibration."""

    def __init__(
        self,
        llm_client: LLMClient,
        prompts_dir: Path | str = "prompts",
        confidence_threshold: float = 0.8,
        confirmation_threshold: float = 0.4,
    ):
        self.llm_client = llm_client
        self.prompts_dir = Path(prompts_dir)
        self.confidence_threshold = confidence_threshold
        self.confirmation_threshold = confirmation_threshold

    def _load_template(self) -> str:
        p = self.prompts_dir / "intent_detection.txt"
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                return f.read()

        raise FileNotFoundError(f"Intent detection prompt not found in {self.prompts_dir}")

    async def detect(
        self,
        current_topic: TopicState,
        latest_turn: TurnPair,
        topic_catalog: list[TopicDigest],
        turn_id: Optional[str] = None,
    ) -> IntentDecision:
        catalog_dicts = [
            {
                "topic_id": t.topic_id,
                "topic_number": t.topic_number,
                "topic_content": t.topic_content,
                "status": t.status,
            }
            for t in topic_catalog
        ]

        template = self._load_template()
        prompt = (
            template
            .replace("{current_topic_content}", str(current_topic.topic_content))
            .replace("{current_topic_id}", str(current_topic.topic_id))
            .replace("{interviewer_message}", str(latest_turn.interviewer_message))
            .replace("{interviewee_message}", str(latest_turn.interviewee_message))
            .replace("{topic_catalog_json}", json.dumps(catalog_dicts, ensure_ascii=False, indent=2))
        )

        effective_turn_id = turn_id or getattr(latest_turn, "user_turn_id", None)
        try:
            raw_data = await self.llm_client.complete_json(
                prompt=prompt,
                turn_id=effective_turn_id,
                module="IntentController",
                prompt_name="intent_detection",
            )
        except Exception as ex:
            return IntentDecision(
                intent="none",
                confidence=0.0,
                target_topic_id=None,
                target_topic_number=None,
                needs_confirmation=False,
                raw_explanation=f"Intent detection transport/completion failed: {ex}",
            )

        if not isinstance(raw_data, dict):
            return IntentDecision(
                intent="none",
                confidence=0.0,
                target_topic_id=None,
                target_topic_number=None,
                needs_confirmation=False,
                raw_explanation=f"Intent detection output format invalid: expected dict, got {type(raw_data).__name__}",
            )

        try:
            validated = IntentDetectionLLMOutput.model_validate(raw_data)
        except Exception as ex:
            return IntentDecision(
                intent="none",
                confidence=0.0,
                target_topic_id=None,
                target_topic_number=None,
                needs_confirmation=False,
                raw_explanation=f"Intent detection output schema validation failed: {ex}",
            )

        raw_intent = validated.intent
        raw_confidence = validated.confidence
        raw_target = validated.target_topic_number or validated.target_topic_id
        explanation = validated.explanation

        raw_confidence = max(0.0, min(1.0, raw_confidence))
        valid_intents = {
            "none",
            "stop_interview",
            "refuse_current_topic",
            "switch_existing_topic",
            "return_previous_topic",
        }
        if raw_intent not in valid_intents:
            raw_intent = "none"

        target_topic_id = None
        target_topic_number = None
        target_matched = False
        if raw_target:
            t_str = str(raw_target).strip()
            for t in topic_catalog:
                if t.topic_id == t_str or t.topic_number == t_str:
                    target_topic_id = t.topic_id
                    target_topic_number = t.topic_number
                    target_matched = True
                    break

        # If user intended to switch or return to a topic, but target does not exist or is invalid:
        # Do NOT guess or silently fallback. Downgrade to confirmation or none!
        if raw_intent in ("switch_existing_topic", "return_previous_topic") and not target_matched:
            if raw_confidence >= self.confirmation_threshold:
                return IntentDecision(
                    intent=raw_intent,  # type: ignore
                    confidence=raw_confidence,
                    target_topic_id=None,
                    target_topic_number=str(raw_target) if raw_target else None,
                    needs_confirmation=True,
                    raw_explanation=f"用户请求换题但未匹配到目录中有效主题: {raw_target}",
                )
            else:
                return IntentDecision(
                    intent="none",
                    confidence=raw_confidence,
                    target_topic_id=None,
                    target_topic_number=None,
                    needs_confirmation=False,
                    raw_explanation="换题目标未匹配且置信度低，按普通内容处理",
                )

        # Confidence Calibration & Thresholding
        if raw_intent == "none":
            return IntentDecision(
                intent="none",
                confidence=raw_confidence,
                target_topic_id=None,
                target_topic_number=None,
                needs_confirmation=False,
                raw_explanation=explanation,
            )

        if raw_confidence >= self.confidence_threshold:
            return IntentDecision(
                intent=raw_intent,  # type: ignore
                confidence=raw_confidence,
                target_topic_id=target_topic_id,
                target_topic_number=target_topic_number,
                needs_confirmation=False,
                raw_explanation=explanation,
            )
        elif raw_confidence >= self.confirmation_threshold:
            # Ambiguous / Low confidence control intent -> trigger confirmation
            return IntentDecision(
                intent=raw_intent,  # type: ignore
                confidence=raw_confidence,
                target_topic_id=target_topic_id,
                target_topic_number=target_topic_number,
                needs_confirmation=True,
                raw_explanation=explanation,
            )
        else:
            # Very low confidence -> treat as normal content answer
            return IntentDecision(
                intent="none",
                confidence=raw_confidence,
                target_topic_id=None,
                target_topic_number=None,
                needs_confirmation=False,
                raw_explanation=explanation,
            )
