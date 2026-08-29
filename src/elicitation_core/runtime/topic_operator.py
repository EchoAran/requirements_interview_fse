import json
from pathlib import Path
from typing import Optional, Union
from ..llm.client import LLMClient
from ..models.event import StateEvent
from ..models.state import ProjectState, TopicState, SlotState
from ..services.event_factory import EventFactory
from ..services.id_factory import IdFactory
from .structure_evolver import StructureEvolver


class TopicOperator:
    """Executes state transitions and topic switching by producing immutable StateEvents through StructureEvolver integration."""

    def __init__(self, llm_client: LLMClient, prompts_dir: Path | str = "prompts"):
        self.llm_client = llm_client
        self.prompts_dir = Path(prompts_dir)
        self.structure_evolver = StructureEvolver(llm_client=llm_client, prompts_dir=prompts_dir)

    def _load_prompt(self, filename: str) -> str:
        prompt_path = self.prompts_dir / filename
        if not prompt_path.exists():
            raise FileNotFoundError(f"Prompt template not found at {prompt_path}")
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()

    def _find_next_pending_or_interrupted_topic(
        self,
        state: ProjectState,
    ) -> Optional[TopicState]:
        all_topics_map = {t.topic_number: t for t in state.get_all_topics()}

        for t_num in state.initial_order:
            topic = all_topics_map.get(t_num)
            if topic and topic.topic_status in ("Pending", "SystemInterrupted"):
                return topic

        for topic in state.get_all_topics():
            if topic.topic_status in ("Pending", "SystemInterrupted"):
                return topic

        return None

    def maintain_current_topic(self, current_topic: TopicState) -> tuple[TopicState, list[StateEvent]]:
        return current_topic, []

    async def switch_another_topic(
        self,
        current_topic: TopicState,
        conversation_record: list[dict],
        all_topics: list[TopicState],
        state: ProjectState,
        previous_status: str = "SystemInterrupted",
        turn_id: Optional[str] = None,
        evidence_refs: list[str] = [],
    ) -> tuple[Optional[TopicState], list[StateEvent]]:
        topics_list = [
            {"topic_number": t.topic_number, "topic_content": t.topic_content}
            for t in all_topics
            if t.topic_number != current_topic.topic_number
        ]
        if not topics_list:
            return current_topic, []

        template = self._load_prompt("topic_selection.txt")
        prompt = (
            template
            .replace("{current_topic_content}", str(current_topic.topic_content))
            .replace("{current_topic_conversation_record}", json.dumps(conversation_record, ensure_ascii=False))
            .replace("{topics_list}", json.dumps(topics_list, ensure_ascii=False))
        )

        raw = await self.llm_client.complete_json(prompt=prompt)
        selected_number = None
        if isinstance(raw, dict):
            selected_number = str(raw.get("topic_number", "")).strip()

        target_topic = state.find_topic_by_number(selected_number) if selected_number else None
        if not target_topic:
            target_topic = self._find_next_pending_or_interrupted_topic(state) or current_topic

        events: list[StateEvent] = []
        if current_topic.topic_id != target_topic.topic_id:
            events.append(
                EventFactory.create_topic_status_changed_event(
                    topic=current_topic,
                    new_status=previous_status,
                    turn_id=turn_id,
                    evidence_refs=evidence_refs,
                )
            )
            events.append(
                EventFactory.create_topic_status_changed_event(
                    topic=target_topic,
                    new_status="Ongoing",
                    turn_id=turn_id,
                    evidence_refs=evidence_refs,
                )
            )

        return target_topic, events

    async def create_new_topic(
        self,
        current_topic: TopicState,
        conversation_record: list[dict],
        all_topics: list[TopicState],
        state: ProjectState,
        previous_status: str = "SystemInterrupted",
        turn_id: Optional[str] = None,
        current_turn_idx: Optional[int] = None,
        evidence_refs: list[str] = [],
    ) -> tuple[TopicState, list[StateEvent]]:
        topics_list = [
            {"topic_number": t.topic_number, "topic_content": t.topic_content}
            for t in all_topics
        ]
        section_content = "通用章节"
        section_number = "section-1"
        section_id = current_topic.section_id
        for s in state.sections:
            if s.section_id == current_topic.section_id:
                section_content = s.section_content
                section_number = s.section_number
                section_id = s.section_id
                break

        template = self._load_prompt("topic_generation.txt")
        prompt = (
            template
            .replace("{current_topic_content}", str(current_topic.topic_content))
            .replace("{current_topic_conversation_record}", json.dumps(conversation_record, ensure_ascii=False))
            .replace("{topics_list}", json.dumps(topics_list, ensure_ascii=False))
            .replace("{section_content}", f"{section_number}: {section_content}")
        )

        raw = await self.llm_client.complete_json(prompt=prompt)
        new_top_num = f"topic-{len(all_topics) + 1}"
        new_top_content = "新建讨论主题"
        slots_data = []

        if isinstance(raw, dict):
            new_top_num = str(raw.get("topic_number", new_top_num)).strip()
            new_top_content = str(raw.get("topic_content", new_top_content)).strip()
            slots_data = raw.get("slots", [])

        actual_turn_idx = current_turn_idx if current_turn_idx is not None else state.turn_index

        # Delegate dynamic topic structure creation to StructureEvolver with accurate turn context and evidence linkage
        new_topic, struct_events = self.structure_evolver.create_topic_from_intent(
            topic_number=new_top_num,
            topic_content=new_top_content,
            slots_data=slots_data if isinstance(slots_data, list) else [],
            section_id=section_id,
            current_turn_idx=actual_turn_idx,
            turn_id=turn_id,
            evidence_refs=evidence_refs,
        )

        events: list[StateEvent] = [
            EventFactory.create_topic_status_changed_event(
                topic=current_topic,
                new_status=previous_status,
                turn_id=turn_id,
                evidence_refs=evidence_refs,
            )
        ] + struct_events

        return new_topic, events

    def end_current_topic(
        self,
        current_topic: TopicState,
        state: ProjectState,
        turn_id: Optional[str] = None,
        evidence_refs: list[str] = [],
    ) -> tuple[Optional[TopicState], list[StateEvent]]:
        events: list[StateEvent] = [
            EventFactory.create_topic_status_changed_event(
                topic=current_topic,
                new_status="Completed",
                turn_id=turn_id,
                evidence_refs=evidence_refs,
            )
        ]
        next_topic = self._find_next_pending_or_interrupted_topic(state)
        if next_topic:
            events.append(
                EventFactory.create_topic_status_changed_event(
                    topic=next_topic,
                    new_status="Ongoing",
                    turn_id=turn_id,
                    evidence_refs=evidence_refs,
                )
            )

        return next_topic, events

    def refuse_current_topic(
        self,
        current_topic: TopicState,
        state: ProjectState,
        turn_id: Optional[str] = None,
        evidence_refs: list[str] = [],
    ) -> tuple[Optional[TopicState], list[StateEvent]]:
        events: list[StateEvent] = [
            EventFactory.create_topic_status_changed_event(
                topic=current_topic,
                new_status="UserInterrupted",
                turn_id=turn_id,
                evidence_refs=evidence_refs,
            )
        ]
        next_topic = self._find_next_pending_or_interrupted_topic(state)
        if next_topic:
            events.append(
                EventFactory.create_topic_status_changed_event(
                    topic=next_topic,
                    new_status="Ongoing",
                    turn_id=turn_id,
                    evidence_refs=evidence_refs,
                )
            )

        return next_topic, events

    async def refuse_current_topic_and_switch_another_topic(
        self,
        current_topic: TopicState,
        conversation_record: list[dict],
        all_topics: list[TopicState],
        state: ProjectState,
        turn_id: Optional[str] = None,
        evidence_refs: list[str] = [],
    ) -> tuple[Optional[TopicState], list[StateEvent]]:
        return await self.switch_another_topic(
            current_topic=current_topic,
            conversation_record=conversation_record,
            all_topics=all_topics,
            state=state,
            previous_status="UserInterrupted",
            turn_id=turn_id,
            evidence_refs=evidence_refs,
        )

    async def refuse_current_topic_and_create_new_topic(
        self,
        current_topic: TopicState,
        conversation_record: list[dict],
        all_topics: list[TopicState],
        state: ProjectState,
        turn_id: Optional[str] = None,
        current_turn_idx: Optional[int] = None,
        evidence_refs: list[str] = [],
    ) -> tuple[TopicState, list[StateEvent]]:
        return await self.create_new_topic(
            current_topic=current_topic,
            conversation_record=conversation_record,
            all_topics=all_topics,
            state=state,
            previous_status="UserInterrupted",
            turn_id=turn_id,
            current_turn_idx=current_turn_idx,
            evidence_refs=evidence_refs,
        )
