import json
from pathlib import Path
from typing import Any, Optional
from llm.client import LLMClient
from llm.exceptions import LLMOutputError
from models.event import EvidenceRef, StateEvent
from models.state import ProjectState, SlotState, TopicState
from models.updates import StructuredTargetContext
from services.event_factory import EventFactory
from services.id_factory import IdFactory

VALID_SLOT_OPERATIONS = frozenset({
    "add",
    "update",
    "refine",
    "mark_uncertain",
    "defer_uncertain",
    "conflict",
})


class SlotFiller:
    """Proposes and generates StateEvents for slot value changes and dynamic slot creations."""

    def __init__(self, llm_client: LLMClient, prompts_dir: Path | str = "prompts"):
        self.llm_client = llm_client
        self.prompts_dir = Path(prompts_dir)

    def _load_template(self) -> str:
        prompt_path = self.prompts_dir / "slots_filling.txt"
        if not prompt_path.exists():
            raise FileNotFoundError(f"Prompt template not found at {prompt_path}")
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()

    def _build_entire_interview_info_slots(self, state: ProjectState) -> dict[str, dict]:
        entire_info: dict[str, dict] = {}
        for topic in state.get_all_topics():
            filled_slots = [
                {
                    "slot_number": s.slot_number,
                    "slot_key": s.key,
                    "slot_value": s.value,
                    "state": s.state,
                    "deferred": s.deferred,
                }
                for s in topic.slots
                if s.value is not None and s.value.strip() != ""
            ]
            if filled_slots:
                topic_key = f"{topic.topic_number}: {topic.topic_content}"
                entire_info[topic_key] = {"slots": filled_slots}
        return entire_info

    async def fill(
        self,
        target_topic: TopicState,
        conversation_record: list[dict],
        state: ProjectState,
        user_turn_id: Optional[str] = None,
        evidence_ref_ids: list[str] = [],
        target_context: Optional[StructuredTargetContext] = None,
    ) -> list[StateEvent]:
        current_slots_data = [
            {
                "slot_number": s.slot_number,
                "slot_key": s.key,
                "slot_value": s.value,
                "state": s.state,
                "deferred": s.deferred,
                "is_necessary": s.is_required,
            }
            for s in target_topic.slots
        ]

        from llm.template import render_prompt

        if target_context is not None:
            target_context_str = json.dumps(target_context.model_dump(), ensure_ascii=False)
        else:
            target_context_str = "None"

        entire_info = self._build_entire_interview_info_slots(state)
        template = self._load_template()
        prompt = render_prompt(
            template,
            {
                "{current_topic_content}": str(target_topic.topic_content),
                "{current_topic_conversation_record}": json.dumps(conversation_record, ensure_ascii=False),
                "{current_topic_info_slots}": json.dumps(current_slots_data, ensure_ascii=False),
                "{entire_interview_info_slots}": json.dumps(entire_info, ensure_ascii=False),
                "{target_context}": target_context_str,
            }
        )

        raw_result = await self.llm_client.complete_json(
            prompt=prompt,
            turn_id=user_turn_id,
            module="SlotFiller",
            prompt_name="slots_filling",
        )
        foreign_slot_keys: dict[str, str] = {
            s.slot_number: s.key
            for t in state.get_all_topics()
            if t.topic_id != target_topic.topic_id
            for s in t.slots
        }

        for attempt in range(2):
            if not isinstance(raw_result, list):
                raise LLMOutputError(f"SlotFiller expected a JSON array, got {type(raw_result).__name__}")

            proposals: list[dict[str, Any]] = []
            seen_proposal_keys: set[str] = set()
            duplicate_slots: set[str] = set()
            for item_index, item in enumerate(raw_result):
                if not isinstance(item, dict):
                    raise LLMOutputError(f"Each item in SlotFiller array must be a JSON object, got {type(item).__name__}")
                if "operation" not in item:
                    raise LLMOutputError("Slot proposal must contain 'operation'")
                proposed_op = str(item.get("operation", "")).strip().lower()
                if proposed_op not in VALID_SLOT_OPERATIONS:
                    raise LLMOutputError(
                        f"Slot proposal invalid operation '{proposed_op}', must be one of {sorted(VALID_SLOT_OPERATIONS)}"
                    )
                if "slot_value" not in item:
                    raise LLMOutputError("Slot proposal must contain 'slot_value'")
                s_num_raw = item.get("slot_number")
                s_num = s_num_raw.strip() if isinstance(s_num_raw, str) else ""
                s_key_raw = item.get("slot_key")
                s_key = s_key_raw.strip() if isinstance(s_key_raw, str) and s_key_raw.strip() else None
                s_val_raw = item.get("slot_value")

                if s_val_raw is None:
                    val = None
                elif isinstance(s_val_raw, (list, dict)):
                    val = json.dumps(s_val_raw, ensure_ascii=False)
                elif isinstance(s_val_raw, str) and s_val_raw.strip():
                    val = s_val_raw.strip()
                else:
                    raise LLMOutputError(
                        f"Slot proposal for '{s_num or s_key}' must provide a non-empty 'slot_value' string, array, object, or explicit null"
                    )

                # A null slot_value is only meaningful for an explicit deferral
                if val is None and proposed_op != "defer_uncertain":
                    raise LLMOutputError(
                        f"Slot proposal for '{s_num or s_key}' must not have an empty slot_value unless operation is 'defer_uncertain'"
                    )

                existing_slot = target_topic.find_slot_by_number(s_num) if s_num else None
                if not existing_slot and s_key:
                    for s in target_topic.slots:
                        if s.key == s_key:
                            existing_slot = s
                            s_num = s.slot_number
                            break

                if not existing_slot and s_num:
                    # A slot_number is only meaningful inside its owning topic: a number taken from
                    # [other_topics_summary] either restates a slot that the owning topic fills on its
                    # own fill call (drop it) or is an unreliable label for a genuinely new slot (new
                    # slots are always numbered by the system, never by the model).
                    if foreign_slot_keys.get(s_num) == s_key:
                        continue
                    s_num = ""

                if existing_slot:
                    proposal_key = f"existing:{existing_slot.slot_id}"
                    slot_label = existing_slot.slot_number
                elif s_key:
                    proposal_key = f"new-key:{s_key}"
                    slot_label = s_key
                else:
                    raise LLMOutputError(
                        f"Slot proposal #{item_index} must reference an existing slot_number or provide a non-empty slot_key"
                    )

                if proposal_key in seen_proposal_keys:
                    duplicate_slots.add(slot_label)
                seen_proposal_keys.add(proposal_key)
                proposals.append(
                    {
                        "slot_number": s_num,
                        "slot_key": s_key,
                        "existing_slot": existing_slot,
                        "value": val,
                        "operation": proposed_op,
                    }
                )

            if not duplicate_slots:
                break
            if attempt == 1:
                raise LLMOutputError(
                    "SlotFiller returned multiple proposals for the same slot after retry: "
                    + ", ".join(sorted(duplicate_slots))
                )

            correction_prompt = (
                f"{prompt}\n\n# CORRECTION REQUIRED\n"
                "Your previous output contained multiple proposals for the same slot(s): "
                f"{', '.join(sorted(duplicate_slots))}. Return a corrected JSON array with exactly one proposal "
                "per slot. Semantically combine all compatible grounded facts for each slot; do not discard facts "
                "and do not treat earlier output items as state updates.\n"
                f"Previous invalid output:\n{json.dumps(raw_result, ensure_ascii=False)}"
            )
            raw_result = await self.llm_client.complete_json(
                prompt=correction_prompt,
                turn_id=user_turn_id,
                module="SlotFiller",
                prompt_name="slots_filling",
            )

        events: list[StateEvent] = []
        for proposal in proposals:
            s_num = proposal["slot_number"]
            s_key = proposal["slot_key"]
            existing_slot = proposal["existing_slot"]
            val = proposal["value"]
            proposed_op = proposal["operation"]

            if existing_slot:
                event, _ = EventFactory.create_slot_value_changed_event(
                    slot=existing_slot,
                    new_value=val,
                    turn_id=user_turn_id,
                    evidence_refs=evidence_ref_ids,
                    proposed_operation=proposed_op,
                    is_llm_proposed=True,
                )
                events.append(event)
            else:
                created_count = len([e for e in events if e.event_type == "slot_created"])
                s_num = f"{target_topic.topic_number}-dyn-{len(target_topic.slots) + created_count + 1}"

                # Emit slot_created event for genuine dynamic slot on target_topic
                new_slot_id = IdFactory.create_slot_id(s_num)
                new_slot = SlotState(
                    slot_id=new_slot_id,
                    topic_id=target_topic.topic_id,
                    slot_number=s_num,
                    key=s_key,
                    value=None,
                    origin="added",
                    is_required=False,
                    state="empty",
                    evidence_refs=list(evidence_ref_ids),
                )
                created_event = EventFactory.create_slot_created_event(
                    slot=new_slot,
                    topic_id=target_topic.topic_id,
                    section_id=target_topic.section_id,
                    turn_id=user_turn_id,
                    evidence_refs=evidence_ref_ids,
                )
                events.append(created_event)

                val_event, _ = EventFactory.create_slot_value_changed_event(
                    slot=new_slot,
                    new_value=val,
                    turn_id=user_turn_id,
                    evidence_refs=evidence_ref_ids,
                    proposed_operation=proposed_op,
                    is_llm_proposed=True,
                )
                events.append(val_event)

        return events
