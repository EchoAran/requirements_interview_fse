import json
import re
from pathlib import Path
from typing import Optional

from ..llm.client import LLMClient
from ..models.dependency import DependencyEdge
from ..models.event import StateEvent
from ..models.interpretation import (
    EmergentResolution,
    EmergentTopicCandidate,
    EvidenceInterpretation,
)
from ..models.state import ProjectState, SectionState, SlotState, TopicState
from ..services.event_factory import EventFactory
from ..services.id_factory import IdFactory


class StructureEvolver:
    """Evolves project requirements structure by deduplicating, resolving emergent candidates, and emitting structure events."""

    def __init__(self, llm_client: LLMClient, prompts_dir: Path | str = "prompts"):
        self.llm_client = llm_client
        self.prompts_dir = Path(prompts_dir)

    def _load_template(self) -> str:
        p = self.prompts_dir / "emergent_topic_resolution.txt"
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                return f.read()

        return "Candidate: {candidate_title}\nDescription: {candidate_description}\nExisting: {existing_topics_json}"

    @staticmethod
    def _normalize_text(text: str) -> str:
        return re.sub(r"[\s\-_，。！？、：；（）()]+", "", text).lower().strip()

    def _resolve_deduplication_heuristic(
        self,
        candidate: EmergentTopicCandidate,
        all_topics: list[TopicState],
    ) -> Optional[EmergentResolution]:
        cand_title_norm = self._normalize_text(candidate.title)
        cand_desc_norm = self._normalize_text(candidate.description)

        for topic in all_topics:
            top_content_norm = self._normalize_text(topic.topic_content)
            # Tier 1: Exact normalized text match
            if cand_title_norm == top_content_norm:
                return EmergentResolution(
                    action="ignore",
                    target_topic_id=topic.topic_id,
                    reason_code="duplicate_synonym",
                )

            # Tier 2: Substring or surface overlap
            if cand_title_norm in top_content_norm or (len(top_content_norm) >= 4 and top_content_norm in cand_title_norm):
                return EmergentResolution(
                    action="merge",
                    target_topic_id=topic.topic_id,
                    reason_code="merge_into_existing",
                )

        return None

    async def _resolve_via_llm(
        self,
        candidate: EmergentTopicCandidate,
        all_topics: list[TopicState],
        turn_id: Optional[str] = None,
    ) -> EmergentResolution:
        existing_list = [
            {
                "topic_id": t.topic_id,
                "topic_number": t.topic_number,
                "topic_content": t.topic_content,
                "slots": [s.key for s in t.slots],
            }
            for t in all_topics
        ]

        from ..llm.template import render_prompt

        template = self._load_template()
        prompt = render_prompt(
            template,
            {
                "{candidate_title}": str(candidate.title),
                "{candidate_description}": str(candidate.description),
                "{candidate_slots}": json.dumps(candidate.suggested_slots, ensure_ascii=False),
                "{existing_topics_json}": json.dumps(existing_list, ensure_ascii=False, indent=2),
            }
        )

        try:
            raw_data = await self.llm_client.complete_json(
                prompt=prompt,
                turn_id=turn_id,
                module="StructureEvolver",
                prompt_name="emergent_topic_resolution",
            )
        except Exception:
            raw_data = {}

        if isinstance(raw_data, list) and raw_data:
            raw_data = raw_data[0]

        if not isinstance(raw_data, dict) or not raw_data:
            return EmergentResolution(
                action="ignore",
                target_topic_id=None,
                reason_code="insufficient_evidence",
            )

        action = str(raw_data.get("action", "ignore")).strip().lower()
        if action == "create_new":
            action = "create"
        if action not in ("create", "merge", "ignore"):
            action = "ignore"

        reason_code = str(raw_data.get("reason_code", "")).strip().lower()
        valid_reason_codes = {
            "distinct_concern",
            "duplicate_synonym",
            "merge_into_existing",
            "insufficient_evidence",
            "out_of_scope",
        }
        if reason_code not in valid_reason_codes:
            if action == "create":
                reason_code = "distinct_concern"
            elif action == "merge":
                reason_code = "merge_into_existing"
            else:
                reason_code = "insufficient_evidence"

        target_id_raw = raw_data.get("target_topic_id")
        target_topic_id = None
        if target_id_raw:
            t_str = str(target_id_raw).strip()
            for t in all_topics:
                if t.topic_id == t_str or t.topic_number == t_str:
                    target_topic_id = t.topic_id
                    break

        return EmergentResolution(
            action=action,  # type: ignore
            target_topic_id=target_topic_id,
            reason_code=reason_code,  # type: ignore
        )

    def create_topic_from_intent(
        self,
        topic_number: str,
        topic_content: str,
        slots_data: list[dict],
        section_id: str,
        current_turn_idx: int,
        turn_id: Optional[str] = None,
        evidence_refs: list[str] = [],
    ) -> tuple[TopicState, list[StateEvent]]:
        """Unifies user-directed intentional topic creation through StructureEvolver rules with evidence traceability."""
        topic_id = IdFactory.create_topic_id(topic_number)
        slots: list[SlotState] = []
        slot_events: list[StateEvent] = []

        for item in slots_data:
            if isinstance(item, dict):
                s_num = str(item.get("slot_number", ""))
                s_key = str(item.get("slot_key", s_num))
                if s_num:
                    s_id = IdFactory.create_slot_id(s_num)
                    s_obj = SlotState(
                        slot_id=s_id,
                        topic_id=topic_id,
                        slot_number=s_num,
                        key=s_key,
                        value=None,
                        origin="emergent",
                        is_required=False,
                        state="empty",
                        evidence_refs=list(evidence_refs),
                    )
                    slots.append(s_obj)
                    slot_events.append(
                        EventFactory.create_slot_created_event(
                            slot=s_obj,
                            topic_id=topic_id,
                            section_id=section_id,
                            turn_id=turn_id,
                            evidence_refs=evidence_refs,
                        )
                    )

        new_topic = TopicState(
            topic_id=topic_id,
            topic_number=topic_number,
            topic_content=topic_content,
            topic_status="Ongoing",
            origin="emergent",
            is_necessary=False,
            section_id=section_id,
            slots=slots,
            created_turn=current_turn_idx,
            last_updated_turn=current_turn_idx,
            evidence_refs=list(evidence_refs),
        )

        topic_event = EventFactory.create_topic_created_event(
            topic=new_topic,
            section_id=section_id,
            turn_id=turn_id,
            evidence_refs=evidence_refs,
        )

        return new_topic, [topic_event] + slot_events

    async def evolve(
        self,
        state: ProjectState,
        interpretation: EvidenceInterpretation,
        current_turn_idx: Optional[int] = None,
        turn_id: Optional[str] = None,
        evidence_refs: list[str] = [],
    ) -> list[StateEvent]:
        events: list[StateEvent] = []
        all_topics = state.get_all_topics()
        turn_idx = current_turn_idx if current_turn_idx is not None else state.turn_index

        # Dynamic relation resolution: consume relation candidates and emit dependency_added events
        for rel in interpretation.relation_candidates:
            if rel.relation_type == "depends_on":
                src_t = state.find_topic_by_id(rel.source_topic_id) or state.find_topic_by_number(rel.source_topic_id)
                tgt_t = state.find_topic_by_id(rel.target_topic_id) or state.find_topic_by_number(rel.target_topic_id)
                if src_t and tgt_t and src_t.topic_id != tgt_t.topic_id:
                    new_edge = DependencyEdge(source=src_t.topic_number, target=tgt_t.topic_number)
                    if not any(e.source == new_edge.source and e.target == new_edge.target for e in state.dependencies):
                        dep_event = EventFactory.create_dependency_added_event(
                            source_topic_number=src_t.topic_number,
                            target_topic_number=tgt_t.topic_number,
                            turn_id=turn_id,
                            evidence_refs=evidence_refs,
                        )
                        events.append(dep_event)
                        state.dependencies.append(new_edge)

        # Resolve candidate emergent topics
        for candidate in interpretation.emergent_topic_candidates:
            resolution = self._resolve_deduplication_heuristic(candidate, all_topics)

            if not resolution:
                resolution = await self._resolve_via_llm(candidate, all_topics, turn_id=turn_id)

            if resolution.action == "ignore":
                continue

            if resolution.action == "merge" and resolution.target_topic_id:
                target_topic = state.find_topic_by_id(resolution.target_topic_id)
                if target_topic:
                    for slot_name in candidate.suggested_slots:
                        if not any(s.key == slot_name for s in target_topic.slots):
                            new_slot_num = f"{target_topic.topic_number}-dyn-{len(target_topic.slots) + 1}"
                            new_slot = SlotState(
                                slot_id=IdFactory.create_slot_id(new_slot_num),
                                topic_id=target_topic.topic_id,
                                slot_number=new_slot_num,
                                key=slot_name,
                                value=None,
                                origin="emergent",
                                is_required=False,
                                state="empty",
                                evidence_refs=list(evidence_refs),
                            )
                            created_ev = EventFactory.create_slot_created_event(
                                slot=new_slot,
                                topic_id=target_topic.topic_id,
                                section_id=target_topic.section_id,
                                turn_id=turn_id,
                                evidence_refs=evidence_refs,
                            )
                            events.append(created_ev)

            elif resolution.action == "create":
                target_sec_id = None
                if candidate.suggested_section_id:
                    for s in state.sections:
                        if s.section_id == candidate.suggested_section_id or s.section_number == candidate.suggested_section_id:
                            target_sec_id = s.section_id
                            break

                if not target_sec_id:
                    for s in state.sections:
                        if s.section_id == "section_emergent" or s.section_number == "section-emergent":
                            target_sec_id = s.section_id
                            break

                if not target_sec_id and state.sections:
                    target_sec_id = state.sections[0].section_id

                new_top_num = f"topic-emergent-{len(all_topics) + 1}"
                new_top_id = IdFactory.create_topic_id(new_top_num)

                new_slots: list[SlotState] = []
                slot_events: list[StateEvent] = []
                for idx, s_name in enumerate(candidate.suggested_slots, start=1):
                    s_num = f"{new_top_num}-{idx}"
                    s_id = IdFactory.create_slot_id(s_num)
                    s_obj = SlotState(
                        slot_id=s_id,
                        topic_id=new_top_id,
                        slot_number=s_num,
                        key=s_name,
                        value=None,
                        origin="emergent",
                        is_required=False,
                        state="empty",
                        evidence_refs=list(evidence_refs),
                    )
                    new_slots.append(s_obj)
                    slot_events.append(
                        EventFactory.create_slot_created_event(
                            slot=s_obj,
                            topic_id=new_top_id,
                            section_id=target_sec_id or "section_emergent",
                            turn_id=turn_id,
                            evidence_refs=evidence_refs,
                        )
                    )

                new_topic = TopicState(
                    topic_id=new_top_id,
                    topic_number=new_top_num,
                    topic_content=candidate.title,
                    topic_status="Pending",
                    origin="emergent",
                    is_necessary=False,
                    section_id=target_sec_id or "section_emergent",
                    slots=new_slots,
                    created_turn=turn_idx,
                    last_updated_turn=turn_idx,
                    evidence_refs=list(evidence_refs),
                )

                topic_ev = EventFactory.create_topic_created_event(
                    topic=new_topic,
                    section_id=target_sec_id or "section_emergent",
                    turn_id=turn_id,
                    evidence_refs=evidence_refs,
                )
                events.append(topic_ev)
                events.extend(slot_events)
                all_topics.append(new_topic)

        return events
