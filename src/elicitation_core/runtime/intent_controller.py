import json
from pathlib import Path
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, Field

from ..llm.client import LLMClient
from ..llm.exceptions import LLMOutputError
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
    needs_confirmation: Optional[bool] = None
    verification_outcome: Optional[
        Literal[
            "not_applicable",
            "accepted",
            "revisions_requested",
            "ambiguous",
        ]
    ] = None
    verification_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    verification_explanation: Optional[str] = None


class IntentController:
    """Detects explicit conversational control intentions (stop, refuse, switch, return) and topic verification outcomes with confidence calibration."""

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

    def _emit_output_error(
        self, message: str, raw_response: Optional[str] = None, turn_id: Optional[str] = None
    ) -> LLMOutputError:
        """Records the LLM output validation error to errors.jsonl via llm_client callback and returns LLMOutputError."""
        if getattr(self.llm_client, "on_error", None):
            from ..services.id_factory import IdFactory
            from ..models.run_record import RunError

            run_err = RunError(
                error_id=IdFactory.create_event_id(),
                turn_id=turn_id,
                module="IntentController",
                error_type="llm_output_error",
                message=message,
                recoverable=True,
            )
            self.llm_client.on_error(run_err)
        return LLMOutputError(message, raw_response=raw_response)

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
        previous_question_strategy: Optional[str] = None,
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

        from ..llm.template import render_prompt

        template = self._load_template()
        prompt = render_prompt(
            template,
            {
                "{current_topic_content}": str(current_topic.topic_content),
                "{current_topic_id}": str(current_topic.topic_id),
                "{previous_question_strategy}": str(previous_question_strategy or "none"),
                "{interviewer_message}": str(latest_turn.interviewer_message),
                "{interviewee_message}": str(latest_turn.interviewee_message),
                "{topic_catalog_json}": json.dumps(catalog_dicts, ensure_ascii=False, indent=2),
            }
        )

        effective_turn_id = turn_id or getattr(latest_turn, "user_turn_id", None)
        raw_data = await self.llm_client.complete_json(
            prompt=prompt,
            turn_id=effective_turn_id,
            module="IntentController",
            prompt_name="intent_detection",
        )

        if not isinstance(raw_data, dict):
            raise self._emit_output_error(
                f"Intent detection output format invalid: expected dict, got {type(raw_data).__name__}",
                raw_response=str(raw_data),
                turn_id=effective_turn_id,
            )

        try:
            validated = IntentDetectionLLMOutput.model_validate(raw_data)
        except Exception as ex:
            raise self._emit_output_error(
                f"Intent detection output schema validation failed: {ex}",
                raw_response=json.dumps(raw_data, ensure_ascii=False),
                turn_id=effective_turn_id,
            ) from ex

        raw_intent = validated.intent
        raw_confidence = validated.confidence
        raw_target = validated.target_topic_number or validated.target_topic_id
        explanation = validated.explanation

        # Process verification outcome: strictly enforce required verification fields on verify strategy
        if previous_question_strategy == "verify":
            if validated.verification_outcome is None or validated.verification_outcome == "not_applicable":
                raise self._emit_output_error(
                    f"When previous question strategy is 'verify', LLM must output verification_outcome in ['accepted', 'revisions_requested', 'ambiguous'], got '{validated.verification_outcome}'",
                    raw_response=json.dumps(raw_data, ensure_ascii=False),
                    turn_id=effective_turn_id,
                )
            if validated.verification_confidence is None:
                raise self._emit_output_error(
                    "When previous question strategy is 'verify', LLM must output verification_confidence",
                    raw_response=json.dumps(raw_data, ensure_ascii=False),
                    turn_id=effective_turn_id,
                )
            verification_outcome = validated.verification_outcome
            verification_confidence = max(0.0, min(1.0, validated.verification_confidence))
            verification_explanation = validated.verification_explanation
        else:
            verification_outcome = "not_applicable"
            verification_confidence = 0.0
            verification_explanation = None

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
                    raw_explanation=f"User requested topic switch/return but target was not found in catalog: {raw_target}",
                    verification_outcome=verification_outcome,
                    verification_confidence=verification_confidence,
                    verification_explanation=verification_explanation,
                )
            else:
                return IntentDecision(
                    intent="none",
                    confidence=raw_confidence,
                    target_topic_id=None,
                    target_topic_number=None,
                    needs_confirmation=False,
                    raw_explanation="Topic target was unmatched and confidence is below threshold; treating as standard content",
                    verification_outcome=verification_outcome,
                    verification_confidence=verification_confidence,
                    verification_explanation=verification_explanation,
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
                verification_outcome=verification_outcome,
                verification_confidence=verification_confidence,
                verification_explanation=verification_explanation,
            )

        if raw_confidence >= self.confidence_threshold:
            return IntentDecision(
                intent=raw_intent,  # type: ignore
                confidence=raw_confidence,
                target_topic_id=target_topic_id,
                target_topic_number=target_topic_number,
                needs_confirmation=False,
                raw_explanation=explanation,
                verification_outcome=verification_outcome,
                verification_confidence=verification_confidence,
                verification_explanation=verification_explanation,
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
                verification_outcome=verification_outcome,
                verification_confidence=verification_confidence,
                verification_explanation=verification_explanation,
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
                verification_outcome=verification_outcome,
                verification_confidence=verification_confidence,
                verification_explanation=verification_explanation,
            )
