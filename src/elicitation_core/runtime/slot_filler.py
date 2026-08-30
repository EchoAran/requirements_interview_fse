import json
from pathlib import Path
from typing import Any, Optional
from ..llm.client import LLMClient
from ..llm.exceptions import LLMOutputError
from ..models.event import EvidenceRef, StateEvent
from ..models.state import ProjectState, SlotState, TopicState
from ..services.event_factory import EventFactory
from ..services.id_factory import IdFactory


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
    ) -> list[StateEvent]:
        current_slots_data = [
            {
                "slot_number": s.slot_number,
                "slot_key": s.key,
                "slot_value": s.value,
                "state": s.state,
                "is_necessary": s.is_required,
            }
            for s in target_topic.slots
        ]

        from ..llm.template import render_prompt

        entire_info = self._build_entire_interview_info_slots(state)
        template = self._load_template()
        prompt = render_prompt(
            template,
            {
                "{current_topic_content}": str(target_topic.topic_content),
                "{current_topic_conversation_record}": json.dumps(conversation_record, ensure_ascii=False),
                "{current_topic_info_slots}": json.dumps(current_slots_data, ensure_ascii=False),
                "{entire_interview_info_slots}": json.dumps(entire_info, ensure_ascii=False),
            }
        )

        raw_result = await self.llm_client.complete_json(
            prompt=prompt,
            turn_id=user_turn_id,
            module="SlotFiller",
            prompt_name="slots_filling",
        )
        for attempt in range(2):
            if not isinstance(raw_result, list):
                return []

            proposals: list[dict[str, Any]] = []
            seen_proposal_keys: set[str] = set()
            duplicate_slots: set[str] = set()
            for item_index, item in enumerate(raw_result):
                if not isinstance(item, dict):
                    continue
                s_num = str(item.get("slot_number", "")).strip()
                s_key = str(item.get("slot_key", "")).strip() or None
                s_val_raw = item.get("new_value") if "new_value" in item else item.get("slot_value")
                proposed_op = str(item.get("operation", "")).strip().lower() or None

                if s_val_raw in (None, "None", ""):
                    val = None
                elif isinstance(s_val_raw, (list, dict)):
                    val = json.dumps(s_val_raw, ensure_ascii=False)
                else:
                    val = str(s_val_raw).strip()
                    if val.lower() == "none" or val == "":
                        val = None

                # Skip None or empty value to strictly prevent clearing existing data
                if val is None:
                    continue

                existing_slot = target_topic.find_slot(s_num) if s_num else None
                if not existing_slot and s_key:
                    for s in target_topic.slots:
                        if s.key == s_key:
                            existing_slot = s
                            s_num = s.slot_number
                            break

                if existing_slot:
                    proposal_key = f"existing:{existing_slot.slot_id}"
                    slot_label = existing_slot.slot_number
                elif s_num:
                    proposal_key = f"new-number:{s_num}"
                    slot_label = s_num
                elif s_key:
                    proposal_key = f"new-key:{s_key}"
                    slot_label = s_key
                else:
                    proposal_key = f"new-anonymous:{item_index}"
                    slot_label = proposal_key

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
                if not s_num:
                    created_count = len([e for e in events if e.event_type == "slot_created"])
                    s_num = f"slot-{target_topic.topic_number.replace('topic-', '')}-{len(target_topic.slots) + created_count + 1}"

                # Prevent cross-topic slot leakage by filtering out slot numbers that belong to other existing topics
                belongs_to_other_topic = False
                for other_t in state.get_all_topics():
                    if other_t.topic_id != target_topic.topic_id and other_t.find_slot(s_num) is not None:
                        belongs_to_other_topic = True
                        break

                if belongs_to_other_topic:
                    continue

                # Emit slot_created event for genuine dynamic slot on target_topic
                new_slot_id = IdFactory.create_slot_id(s_num)
                new_slot = SlotState(
                    slot_id=new_slot_id,
                    topic_id=target_topic.topic_id,
                    slot_number=s_num,
                    key=s_key or s_num,
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
                    proposed_operation=proposed_op or "add",
                    is_llm_proposed=True,
                )
                events.append(val_event)

        return events
