from typing import Optional
from ..models.event import EvidenceRef, StateEvent
from ..models.state import SlotRevision, SlotState, TopicState
from ..services.id_factory import IdFactory


class EventFactory:
    """Pure factory constructing immutable StateEvent and EvidenceRef objects."""

    @staticmethod
    def determine_slot_operation(
        old_value: Optional[str],
        new_value: Optional[str],
        proposed_op: Optional[str] = None,
        is_llm_proposed: bool = False,
    ) -> str:
        old_empty = old_value is None or str(old_value).strip() == ""
        new_empty = new_value is None or str(new_value).strip() == ""

        if proposed_op == "conflict":
            return "conflict"
        if old_empty and not new_empty:
            return "add"
        if not old_empty and new_empty:
            return "invalidate"
        if is_llm_proposed and proposed_op == "clear":
            return "add" if old_empty else "update"
        if not old_empty and not new_empty:
            if proposed_op == "add":
                if str(old_value) in str(new_value) or str(new_value) in str(old_value):
                    return "refine"
                return "update"
            if proposed_op in ("refine", "update"):
                return proposed_op
            return "update"
        return "add"

    @staticmethod
    def create_evidence(
        source_type: str,
        content: str,
        turn_id: Optional[str] = None,
        message_id: Optional[str] = None,
    ) -> EvidenceRef:
        return EvidenceRef(
            evidence_id=IdFactory.create_evidence_id(),
            source_type=source_type,  # type: ignore
            turn_id=turn_id,
            message_id=message_id,
            content=content,
        )

    @staticmethod
    def create_slot_created_event(
        slot: SlotState,
        topic_id: str,
        section_id: str,
        turn_id: Optional[str] = None,
        evidence_refs: list[str] = [],
    ) -> StateEvent:
        return StateEvent(
            event_id=IdFactory.create_event_id(),
            turn_id=turn_id,
            event_type="slot_created",
            entity_type="slot",
            entity_id=slot.slot_id,
            before={},
            after={
                "slot": slot.model_dump(),
                "topic_id": topic_id,
                "section_id": section_id,
            },
            evidence_refs=list(evidence_refs),
        )

    @classmethod
    def create_slot_value_changed_event(
        cls,
        slot: SlotState,
        new_value: Optional[str],
        turn_id: Optional[str] = None,
        evidence_refs: list[str] = [],
        proposed_operation: Optional[str] = None,
        is_llm_proposed: bool = False,
    ) -> tuple[StateEvent, SlotRevision]:
        rev_id = IdFactory.create_revision_id(len(slot.revisions) + 1)
        old_value = slot.value

        op = cls.determine_slot_operation(
            old_value=slot.value,
            new_value=new_value,
            proposed_op=proposed_operation,
            is_llm_proposed=is_llm_proposed,
        )

        effective_new_val = new_value
        if is_llm_proposed and (effective_new_val is None or str(effective_new_val).strip() == "" or proposed_operation == "clear"):
            if slot.value is not None and str(slot.value).strip() != "":
                effective_new_val = slot.value

        if effective_new_val is None or effective_new_val.strip() == "":
            new_state = "empty"
            effective_val = None
        elif op == "conflict":
            new_state = "conflict"
            effective_val = slot.value  # Retain previous candidate in value while marking conflict
        else:
            new_state = "filled"
            effective_val = effective_new_val

        revision = SlotRevision(
            revision_id=rev_id,
            turn_id=turn_id,
            operation=op,
            old_value=old_value,
            new_value=new_value,
            evidence_refs=list(evidence_refs),
        )

        before_dict = {
            "value": slot.value,
            "state": slot.state,
            "revisions_count": len(slot.revisions),
        }
        after_dict = {
            "value": effective_val,
            "state": new_state,
            "revision": revision.model_dump(),
        }

        event = StateEvent(
            event_id=IdFactory.create_event_id(),
            turn_id=turn_id,
            event_type="slot_value_changed",
            entity_type="slot",
            entity_id=slot.slot_id,
            before=before_dict,
            after=after_dict,
            evidence_refs=list(evidence_refs),
        )

        return event, revision

    @staticmethod
    def create_topic_status_changed_event(
        topic: TopicState,
        new_status: str,
        turn_id: Optional[str] = None,
        evidence_refs: list[str] = [],
    ) -> StateEvent:
        return StateEvent(
            event_id=IdFactory.create_event_id(),
            turn_id=turn_id,
            event_type="topic_status_changed",
            entity_type="topic",
            entity_id=topic.topic_id,
            before={"topic_status": topic.topic_status},
            after={"topic_status": new_status},
            evidence_refs=list(evidence_refs),
        )

    @staticmethod
    def create_topic_created_event(
        topic: TopicState,
        section_id: str,
        turn_id: Optional[str] = None,
        evidence_refs: list[str] = [],
    ) -> StateEvent:
        return StateEvent(
            event_id=IdFactory.create_event_id(),
            turn_id=turn_id,
            event_type="topic_created",
            entity_type="topic",
            entity_id=topic.topic_id,
            before={},
            after={"topic": topic.model_dump(), "section_id": section_id},
            evidence_refs=list(evidence_refs),
        )

    @staticmethod
    def create_project_status_changed_event(
        project_id: str,
        old_status: str,
        new_status: str,
        turn_id: Optional[str] = None,
    ) -> StateEvent:
        return StateEvent(
            event_id=IdFactory.create_event_id(),
            turn_id=turn_id,
            event_type="project_status_changed",
            entity_type="project",
            entity_id=project_id,
            before={"project_status": old_status},
            after={"project_status": new_status},
            evidence_refs=[],
        )

    @staticmethod
    def create_turn_advanced_event(
        project_id: str,
        old_turn_index: int,
        new_turn_index: int,
        turn_id: str,
    ) -> StateEvent:
        return StateEvent(
            event_id=IdFactory.create_event_id(),
            turn_id=turn_id,
            event_type="turn_advanced",
            entity_type="project",
            entity_id=project_id,
            before={"turn_index": old_turn_index},
            after={"turn_index": new_turn_index},
            evidence_refs=[],
        )

    @staticmethod
    def create_dependency_added_event(
        source_topic_number: str,
        target_topic_number: str,
        turn_id: Optional[str] = None,
        evidence_refs: list[str] = [],
    ) -> StateEvent:
        return StateEvent(
            event_id=IdFactory.create_event_id(),
            turn_id=turn_id,
            event_type="dependency_added",
            entity_type="dependency",
            entity_id=f"{source_topic_number}->{target_topic_number}",
            before={},
            after={
                "source": source_topic_number,
                "target": target_topic_number,
            },
            evidence_refs=list(evidence_refs),
        )

    @staticmethod
    def create_dependency_removed_event(
        source_topic_number: str,
        target_topic_number: str,
        turn_id: Optional[str] = None,
        evidence_refs: list[str] = [],
    ) -> StateEvent:
        return StateEvent(
            event_id=IdFactory.create_event_id(),
            turn_id=turn_id,
            event_type="dependency_removed",
            entity_type="dependency",
            entity_id=f"{source_topic_number}->{target_topic_number}",
            before={
                "source": source_topic_number,
                "target": target_topic_number,
            },
            after={},
            evidence_refs=list(evidence_refs),
        )
