import re
from typing import Optional, Set
from pydantic import ValidationError
from models.dependency import DependencyEdge
from models.event import StateEvent
from models.state import ProjectState, SectionState, SlotRevision, SlotState, TopicState


class StateReducerValidationError(ValueError):
    """Raised when an event fails schema, invariants, or before-state verification."""
    pass


class StateReducer:
    """Pure, deterministic reducer transforming ProjectState via sequential StateEvents with 2-phase transactional validation."""

    @classmethod
    def apply(
        cls,
        state: ProjectState,
        events: list[StateEvent],
        known_evidence_ids: Optional[Set[str]] = None,
        strict_validation: bool = True,
    ) -> ProjectState:
        if not events:
            return state

        # Transactional dry-run verification on working copy (Pre-commit)
        working_copy = ProjectState.model_validate(state.model_dump())
        seen_in_batch: Set[str] = set()
        for event in events:
            if event.event_id in state.applied_event_ids:
                # Event was already applied previously, idempotently skip
                continue

            if event.event_id in seen_in_batch:
                if strict_validation:
                    raise StateReducerValidationError(f"Duplicate event_id detected in batch: {event.event_id}")
                continue
            seen_in_batch.add(event.event_id)

            cls._apply_single_event(
                working_copy,
                event,
                known_evidence_ids=known_evidence_ids,
                strict_validation=strict_validation,
            )

        # In-place commit to target state object (Commit)
        for event in events:
            if event.event_id in state.applied_event_ids:
                continue

            cls._apply_single_event(
                state,
                event,
                known_evidence_ids=known_evidence_ids,
                strict_validation=False,
            )

        return state

    @classmethod
    def _apply_single_event(
        cls,
        state: ProjectState,
        event: StateEvent,
        known_evidence_ids: Optional[Set[str]],
        strict_validation: bool,
    ) -> None:
        if strict_validation and known_evidence_ids is not None:
            for ref in event.evidence_refs:
                if ref not in known_evidence_ids:
                    raise StateReducerValidationError(
                        f"Event {event.event_id} references nonexistent evidence_id: '{ref}'"
                    )

        if event.event_type == "turn_advanced":
            cls._apply_turn_advanced(state, event, strict_validation=strict_validation)
        elif event.event_type == "slot_created":
            cls._apply_slot_created(state, event, strict_validation=strict_validation)
        elif event.event_type == "slot_value_changed":
            cls._apply_slot_value_changed(state, event, strict_validation=strict_validation)
        elif event.event_type == "topic_status_changed":
            cls._apply_topic_status_changed(state, event, strict_validation=strict_validation)
        elif event.event_type == "topic_created":
            cls._apply_topic_created(state, event, strict_validation=strict_validation)
        elif event.event_type == "project_status_changed":
            cls._apply_project_status_changed(state, event, strict_validation=strict_validation)
        elif event.event_type == "dependency_added":
            cls._apply_dependency_added(state, event, strict_validation=strict_validation)
        elif event.event_type == "dependency_removed":
            cls._apply_dependency_removed(state, event, strict_validation=strict_validation)

        if event.turn_id:
            turn_match = re.search(r"turn_(\d+)", event.turn_id)
            if turn_match:
                t_idx = int(turn_match.group(1))
                if t_idx > state.turn_index:
                    state.turn_index = t_idx

        state.applied_event_ids.append(event.event_id)

    @classmethod
    def _apply_turn_advanced(cls, state: ProjectState, event: StateEvent, strict_validation: bool = True) -> None:
        if strict_validation and event.before and "turn_index" in event.before:
            expected_idx = event.before["turn_index"]
            if state.turn_index != expected_idx:
                raise StateReducerValidationError(
                    f"Turn index before-state mismatch in event {event.event_id}: "
                    f"expected '{expected_idx}', current state is '{state.turn_index}'"
                )

        if "turn_index" not in event.after:
            raise StateReducerValidationError(
                f"Event {event.event_id} missing 'turn_index' in after-state for turn_advanced."
            )
        state.turn_index = int(event.after["turn_index"])

    @classmethod
    def _apply_slot_created(cls, state: ProjectState, event: StateEvent, strict_validation: bool = True) -> None:
        slot_data = event.after.get("slot")
        topic_id = event.after.get("topic_id")
        if not slot_data or not isinstance(slot_data, dict):
            raise StateReducerValidationError(f"Event {event.event_id} missing slot payload in after.")

        try:
            new_slot = SlotState(**slot_data)
        except ValidationError as e:
            raise StateReducerValidationError(f"Invalid SlotState schema in event {event.event_id}: {e}") from e

        # Invariants checking
        if new_slot.state == "empty" and new_slot.value is not None:
            raise StateReducerValidationError(f"Slot {new_slot.slot_number} state='empty' but value='{new_slot.value}'")
        if new_slot.state == "filled" and (new_slot.value is None or str(new_slot.value).strip() == ""):
            raise StateReducerValidationError(f"Slot {new_slot.slot_number} state='filled' but value is empty")

        target_topic = state.find_topic_by_id(topic_id)
        if not target_topic:
            if strict_validation:
                raise StateReducerValidationError(f"Topic '{topic_id}' not found for slot_created event {event.event_id}")
            return

        existing = target_topic.find_slot_by_number(new_slot.slot_number)
        if not existing:
            target_topic.slots.append(new_slot)

    @classmethod
    def _apply_slot_value_changed(cls, state: ProjectState, event: StateEvent, strict_validation: bool = True) -> None:
        target_slot: Optional[SlotState] = None
        for sec in state.sections:
            for top in sec.topics:
                for sl in top.slots:
                    if sl.slot_id == event.entity_id:
                        target_slot = sl
                        break
                if target_slot:
                    break
            if target_slot:
                break

        if not target_slot:
            if strict_validation:
                raise StateReducerValidationError(f"Slot '{event.entity_id}' not found for event {event.event_id}")
            return

        if strict_validation and event.before:
            if "value" in event.before and target_slot.value != event.before["value"]:
                raise StateReducerValidationError(
                    f"Slot {target_slot.slot_number} before-value mismatch in event {event.event_id}: "
                    f"expected '{event.before['value']}', current is '{target_slot.value}'"
                )
            if "state" in event.before and target_slot.state != event.before["state"]:
                raise StateReducerValidationError(
                    f"Slot {target_slot.slot_number} before-state mismatch in event {event.event_id}: "
                    f"expected '{event.before['state']}', current is '{target_slot.state}'"
                )
            if "revisions_count" in event.before and len(target_slot.revisions) != event.before["revisions_count"]:
                raise StateReducerValidationError(
                    f"Slot {target_slot.slot_number} before revisions count mismatch (before-revisions_count mismatch) in event {event.event_id}: "
                    f"expected {event.before['revisions_count']}, current is {len(target_slot.revisions)}"
                )

        new_value = event.after.get("value")
        new_state = event.after.get("state", target_slot.state)
        revision_data = event.after.get("revision")

        target_slot.value = new_value
        target_slot.state = new_state
        if "deferred" in event.after:
            target_slot.deferred = bool(event.after["deferred"])
        if event.evidence_refs:
            for ref in event.evidence_refs:
                if ref not in target_slot.evidence_refs:
                    target_slot.evidence_refs.append(ref)

        if revision_data and isinstance(revision_data, dict):
            try:
                rev_obj = SlotRevision(**revision_data)
            except ValidationError as e:
                raise StateReducerValidationError(f"Invalid SlotRevision schema in event {event.event_id}: {e}") from e

            if not any(r.revision_id == rev_obj.revision_id for r in target_slot.revisions):
                target_slot.revisions.append(rev_obj)

        # Invariants enforcement
        if target_slot.state == "empty" and target_slot.value is not None:
            raise StateReducerValidationError(f"Slot {target_slot.slot_number} state='empty' but value='{target_slot.value}'")
        if target_slot.state == "filled" and (target_slot.value is None or str(target_slot.value).strip() == ""):
            raise StateReducerValidationError(f"Slot {target_slot.slot_number} state='filled' but value is empty")
        if target_slot.state == "uncertain":
            if target_slot.value is None or str(target_slot.value).strip() == "":
                raise StateReducerValidationError(f"Slot {target_slot.slot_number} state='uncertain' but value is empty")
            has_uncertain_rev = any(r.operation in ("mark_uncertain", "defer_uncertain") for r in target_slot.revisions)
            if not has_uncertain_rev:
                raise StateReducerValidationError(f"Slot {target_slot.slot_number} is in uncertain state but no mark_uncertain/defer_uncertain revision exists")
        if target_slot.state == "conflict":
            has_conflict_rev = any(r.operation == "conflict" for r in target_slot.revisions)
            if not has_conflict_rev:
                raise StateReducerValidationError(f"Slot {target_slot.slot_number} is in conflict state but no conflict revision exists")

    @classmethod
    def _apply_topic_status_changed(cls, state: ProjectState, event: StateEvent, strict_validation: bool = True) -> None:
        target_topic = state.find_topic_by_id(event.entity_id)
        if not target_topic:
            if strict_validation:
                raise StateReducerValidationError(f"Topic '{event.entity_id}' not found for event {event.event_id}")
            return

        if strict_validation and event.before and "topic_status" in event.before:
            expected_status = event.before["topic_status"]
            if target_topic.topic_status != expected_status:
                raise StateReducerValidationError(
                    f"Topic {target_topic.topic_number} before-status mismatch in event {event.event_id}: "
                    f"expected '{expected_status}', current is '{target_topic.topic_status}'"
                )

        new_status = str(event.after.get("topic_status", target_topic.topic_status))
        target_topic.topic_status = new_status

        if new_status == "Ongoing":
            for sec in state.sections:
                for top in sec.topics:
                    if top.topic_id != target_topic.topic_id and top.topic_status == "Ongoing":
                        top.topic_status = "SystemInterrupted"
            state.current_topic_id = target_topic.topic_id
        elif state.current_topic_id == target_topic.topic_id:
            state.current_topic_id = None

    @classmethod
    def _apply_topic_created(cls, state: ProjectState, event: StateEvent, strict_validation: bool = True) -> None:
        topic_data = event.after.get("topic")
        section_id = event.after.get("section_id")
        if not topic_data or not isinstance(topic_data, dict):
            raise StateReducerValidationError(f"Event {event.event_id} missing topic payload in after.")

        try:
            new_topic = TopicState(**topic_data)
        except ValidationError as e:
            raise StateReducerValidationError(f"Invalid TopicState schema in event {event.event_id}: {e}") from e

        target_section: Optional[SectionState] = None
        for sec in state.sections:
            if sec.section_id == section_id:
                target_section = sec
                break

        if not target_section and state.sections:
            target_section = state.sections[0]

        if target_section:
            if not any(t.topic_id == new_topic.topic_id for t in target_section.topics):
                target_section.topics.append(new_topic)

        if new_topic.topic_status == "Ongoing":
            for sec in state.sections:
                for top in sec.topics:
                    if top.topic_id != new_topic.topic_id and top.topic_status == "Ongoing":
                        top.topic_status = "SystemInterrupted"
            state.current_topic_id = new_topic.topic_id

    @classmethod
    def _apply_project_status_changed(cls, state: ProjectState, event: StateEvent, strict_validation: bool = True) -> None:
        new_status = str(event.after.get("project_status", state.project_status))
        state.project_status = new_status
        if new_status == "Completed":
            state.current_topic_id = None

    @classmethod
    def _apply_dependency_added(cls, state: ProjectState, event: StateEvent, strict_validation: bool = True) -> None:
        source = event.after.get("source")
        target = event.after.get("target")
        if not source or not target:
            if strict_validation:
                raise StateReducerValidationError(f"Event {event.event_id} missing source/target in dependency_added payload.")
            return

        edge = DependencyEdge(source=str(source), target=str(target))
        if not any(e.source == edge.source and e.target == edge.target for e in state.dependencies):
            state.dependencies.append(edge)

    @classmethod
    def _apply_dependency_removed(cls, state: ProjectState, event: StateEvent, strict_validation: bool = True) -> None:
        source = event.before.get("source")
        target = event.before.get("target")
        if not source or not target:
            return
        state.dependencies = [
            e for e in state.dependencies
            if not (e.source == str(source) and e.target == str(target))
        ]

    @classmethod
    def replay(
        cls,
        initial_state: ProjectState,
        events: list[StateEvent],
        known_evidence_ids: Optional[Set[str]] = None,
    ) -> ProjectState:
        """Reconstruct state deterministically from initial state and sequential immutable events."""
        reconstructed = ProjectState.model_validate(initial_state.model_dump())
        reconstructed.applied_event_ids = []
        return cls.apply(reconstructed, events, known_evidence_ids=known_evidence_ids, strict_validation=True)
