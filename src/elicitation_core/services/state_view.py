from typing import Any, Optional
from ..models.event import EvidenceRef
from ..models.interpretation import AffectedTopic, ProjectDigest, TopicDigest
from ..models.scheduling import TopicSchedulingView
from ..models.state import ProjectState, SectionState, SlotState, TopicState
from ..models.strategy import KnownInfoDigest, KnownSlotFact, SlotDigest, TopicCatalogItem


class StateView:
    """Read-only facade exposing queried views and metrics of ProjectState without exposing internal mutation methods."""

    def __init__(
        self,
        state: ProjectState,
        evidence_refs: Optional[list[EvidenceRef]] = None,
    ):
        self._state = state
        self._evidence_source_map: dict[str, str] = {}
        if evidence_refs:
            for ev in evidence_refs:
                self._evidence_source_map[ev.evidence_id] = ev.source_type

    @property
    def project_id(self) -> str:
        return self._state.project_id

    @property
    def project_name(self) -> str:
        return self._state.project_name

    @property
    def turn_index(self) -> int:
        return self._state.turn_index

    @property
    def current_topic_id(self) -> Optional[str]:
        return self._state.current_topic_id

    @property
    def project_status(self) -> str:
        return self._state.project_status

    def get_topic(self, topic_id_or_number: str) -> Optional[TopicState]:
        return self._state.find_topic_by_id(topic_id_or_number) or self._state.find_topic_by_number(topic_id_or_number)

    def get_all_topics(self) -> list[TopicState]:
        return self._state.get_all_topics()

    def get_seed_topics(self) -> list[TopicState]:
        return [t for t in self.get_all_topics() if t.origin == "initial"]

    def get_emergent_topics(self) -> list[TopicState]:
        return [t for t in self.get_all_topics() if t.origin == "added"]

    def get_topic_slots(self, topic_id_or_number: str) -> list[SlotState]:
        topic = self.get_topic(topic_id_or_number)
        return list(topic.slots) if topic else []

    def get_filled_slots(self, topic_id_or_number: str) -> list[SlotState]:
        slots = self.get_topic_slots(topic_id_or_number)
        return [s for s in slots if s.value is not None and str(s.value).strip() != ""]

    def get_empty_required_slots(self, topic_id_or_number: str) -> list[SlotState]:
        slots = self.get_topic_slots(topic_id_or_number)
        return [
            s for s in slots
            if s.is_required and (s.value is None or str(s.value).strip() == "")
        ]

    def get_conflict_slots(self, topic_id_or_number: str) -> list[SlotState]:
        slots = self.get_topic_slots(topic_id_or_number)
        return [s for s in slots if s.state == "conflict"]

    def get_uncertain_slots(self, topic_id_or_number: str) -> list[SlotState]:
        slots = self.get_topic_slots(topic_id_or_number)
        return [s for s in slots if s.state == "uncertain"]

    def has_conflict(self, topic_id_or_number: str) -> bool:
        return len(self.get_conflict_slots(topic_id_or_number)) > 0

    def interview_evidence_count(self, topic_id_or_number: str) -> int:
        """Counts only genuine interview evidence references (excluding initial/prefill seed evidences).

        Conservatively returns 0 if no evidence source mapping is provided.
        """
        topic = self.get_topic(topic_id_or_number)
        if not topic:
            return 0

        topic_evs = set(topic.evidence_refs)
        for s in topic.slots:
            topic_evs.update(s.evidence_refs)
            for rev in s.revisions:
                topic_evs.update(rev.evidence_refs)

        if not topic_evs:
            return 0

        if not self._evidence_source_map:
            # Conservative: When source mapping is not provided, unknown evidences are NOT counted as interview evidence
            return 0

        interview_evs = [
            eid for eid in topic_evs
            if self._evidence_source_map.get(eid) == "interview_turn"
        ]
        return len(interview_evs)

    def slot_interview_evidence_count(self, slot: SlotState) -> int:
        """Counts only genuine interview evidence references for a given slot.

        Falls back to raw evidence count if no evidence source mapping is provided.
        """
        slot_evs = set(slot.evidence_refs)
        for rev in slot.revisions:
            slot_evs.update(rev.evidence_refs)
        if not slot_evs:
            return 0
        if not self._evidence_source_map:
            return len(slot_evs)
        return sum(
            1 for eid in slot_evs
            if self._evidence_source_map.get(eid) == "interview_turn"
        )

    def get_deepening_target_slots(
        self,
        topic_id_or_number: str,
        deferred_slot_ids: Optional[set[str]] = None,
    ) -> list[tuple[SlotState, str]]:
        """Identifies concrete candidate slots that require deepening, ordered by priority.

        Priority order:
        1. Required slots marked uncertain (reason: 'uncertain_value').
        2. Optional slots marked uncertain (reason: 'uncertain_value').
        3. Dynamically added slots with a populated value and at most one interview evidence (reason: 'added_slot_needs_clarification').

        Slots in deferred_slot_ids are excluded as their deepening has already been addressed or explicitly deferred.
        """
        topic = self.get_topic(topic_id_or_number)
        if not topic:
            return []

        deferred = deferred_slot_ids or set()
        candidates: list[tuple[SlotState, str]] = []

        for s in topic.slots:
            is_deferred = s.slot_id in deferred or bool(getattr(s, "deferred", False))
            if not is_deferred and s.state == "uncertain" and s.is_required:
                candidates.append((s, "uncertain_value"))

        for s in topic.slots:
            is_deferred = s.slot_id in deferred or bool(getattr(s, "deferred", False))
            if not is_deferred and s.state == "uncertain" and not s.is_required:
                candidates.append((s, "uncertain_value"))

        for s in topic.slots:
            is_deferred = s.slot_id in deferred or bool(getattr(s, "deferred", False))
            if not is_deferred and s.origin == "added" and s.state != "uncertain":
                if s.value is not None and str(s.value).strip() != "":
                    ev_count = self.slot_interview_evidence_count(s)
                    if ev_count <= 1:
                        candidates.append((s, "added_slot_needs_clarification"))

        return candidates

    def needs_deepening(
        self,
        topic_id_or_number: str,
        deferred_slot_ids: Optional[set[str]] = None,
    ) -> bool:
        """Determines if the topic contains any slot requiring deepening."""
        return len(self.get_deepening_target_slots(topic_id_or_number, deferred_slot_ids=deferred_slot_ids)) > 0

    def is_ready_for_verification(
        self,
        topic_id_or_number: str,
        deferred_slot_ids: Optional[set[str]] = None,
    ) -> bool:
        """Determines if a topic has fulfilled prerequisites for verification without pending gaps or conflicts."""
        topic = self.get_topic(topic_id_or_number)
        if not topic:
            return False
        if len(self.get_empty_required_slots(topic_id_or_number)) > 0:
            return False
        if self.has_conflict(topic_id_or_number):
            return False
        if self.needs_deepening(topic_id_or_number, deferred_slot_ids=deferred_slot_ids):
            return False
        filled = self.get_filled_slots(topic_id_or_number)
        return len(filled) > 0 or self.interview_evidence_count(topic_id_or_number) > 0

    def get_topic_completion(self, topic_id_or_number: str) -> float:
        slots = self.get_topic_slots(topic_id_or_number)
        if not slots:
            return 0.0
        target = [s for s in slots if s.is_required] or slots
        filled = [s for s in target if s.value is not None and str(s.value).strip() != ""]
        return len(filled) / len(target)

    def get_topic_catalog(self) -> list[TopicDigest]:
        catalog: list[TopicDigest] = []
        for sec in self._state.sections:
            for top in sec.topics:
                catalog.append(
                    TopicDigest(
                        topic_id=top.topic_id,
                        topic_number=top.topic_number,
                        topic_content=top.topic_content,
                        section_id=sec.section_id,
                        section_number=sec.section_number,
                        slots_keys=[s.key for s in top.slots],
                        status=top.topic_status,
                    )
                )
        return catalog

    def get_topic_catalog_items(self) -> list[TopicCatalogItem]:
        catalog: list[TopicCatalogItem] = []
        for top in self.get_all_topics():
            catalog.append(
                TopicCatalogItem(
                    topic_id=top.topic_id,
                    topic_number=top.topic_number,
                    topic_content=top.topic_content,
                    status=top.topic_status,
                    origin=top.origin,
                )
            )
        return catalog

    def get_slot_digests(self, topic_id_or_number: str) -> list[SlotDigest]:
        slots = self.get_topic_slots(topic_id_or_number)
        return [
            SlotDigest(
                slot_id=s.slot_id,
                slot_number=s.slot_number,
                key=s.key,
                value=s.value,
                is_required=s.is_required,
                state=s.state,
                origin=s.origin,
            )
            for s in slots
        ]

    def get_project_known_info(self) -> list[KnownInfoDigest]:
        known_info: list[KnownInfoDigest] = []
        for t in self.get_all_topics():
            filled = [
                KnownSlotFact(
                    slot_id=s.slot_id,
                    key=s.key,
                    value=s.value,
                    state=s.state,
                    evidence_refs=list(s.evidence_refs),
                )
                for s in t.slots
                if s.value is not None and str(s.value).strip() != ""
            ]
            if filled:
                known_info.append(
                    KnownInfoDigest(
                        topic_id=t.topic_id,
                        topic_number=t.topic_number,
                        topic_content=t.topic_content,
                        filled_slots=filled,
                    )
                )
        return known_info

    def get_project_digest(self) -> ProjectDigest:
        topics = self.get_all_topics()
        total_slots = sum(len(t.slots) for t in topics)
        return ProjectDigest(
            project_id=self._state.project_id,
            project_name=self._state.project_name,
            turn_index=self._state.turn_index,
            current_topic_id=self._state.current_topic_id,
            total_topics=len(topics),
            total_slots=total_slots,
        )

    def get_scheduling_views(
        self,
        current_turn_idx: Optional[int] = None,
        affected_topics: list[AffectedTopic] = [],
    ) -> list[TopicSchedulingView]:
        """Calculates normalized scheduling factors for all eligible topics in state."""
        turn_idx = current_turn_idx if current_turn_idx is not None else self._state.turn_index
        all_topics = self.get_all_topics()
        relevance_map: dict[str, float] = {}
        for aff in affected_topics:
            relevance_map[aff.topic_id] = aff.relevance

        completed_topic_numbers = {
            t.topic_number for t in all_topics if t.topic_status == "Completed"
        }

        init_order = self._state.initial_order
        max_rank = max(len(init_order), 1)

        views: list[TopicSchedulingView] = []
        for topic in all_topics:
            # 1. Initial prior
            if topic.topic_number in init_order:
                rank = init_order.index(topic.topic_number)
                initial_prior = max(0.0, 1.0 - (rank / max_rank))
            else:
                initial_prior = 0.0

            # 2. Dependency readiness
            prereq_edges = [
                e for e in self._state.dependencies
                if e.target == topic.topic_number or e.target == topic.topic_id
            ]
            if not prereq_edges:
                dependency_readiness = 1.0
            else:
                satisfied = sum(1 for e in prereq_edges if e.source in completed_topic_numbers)
                dependency_readiness = satisfied / len(prereq_edges)

            # 3. Unresolved gap (required_empty_ratio)
            req_slots = [s for s in topic.slots if s.is_required] or topic.slots
            if req_slots:
                empty_slots = [s for s in req_slots if s.value is None or str(s.value).strip() == ""]
                required_empty_ratio = len(empty_slots) / len(req_slots)
            else:
                required_empty_ratio = 0.0

            # 4. Conflict signal
            has_conflict = any(s.state == "conflict" for s in topic.slots)
            conflict_ratio = 1.0 if has_conflict else 0.0

            # 5. Recent emergence
            if topic.origin == "added":
                diff = max(0, turn_idx - topic.created_turn)
                if diff == 0:
                    recent_emergence = 1.0
                elif diff == 1:
                    recent_emergence = 0.7
                elif diff == 2:
                    recent_emergence = 0.4
                else:
                    recent_emergence = 0.0
            else:
                recent_emergence = 0.0

            # 6. Continuity
            if topic.topic_id == self._state.current_topic_id:
                continuity = 1.0 if required_empty_ratio > 0 else 0.5
            else:
                continuity = 0.0

            # 7. User relevance
            user_relevance = relevance_map.get(topic.topic_id, relevance_map.get(topic.topic_number, 0.0))
            if topic.origin == "added" and recent_emergence == 1.0 and user_relevance == 0.0:
                user_relevance = 1.0
            if topic.topic_id == self._state.current_topic_id and user_relevance == 0.0:
                user_relevance = 0.5

            views.append(
                TopicSchedulingView(
                    topic_id=topic.topic_id,
                    topic_number=topic.topic_number,
                    topic_content=topic.topic_content,
                    status=topic.topic_status,
                    origin=topic.origin,
                    required_empty_ratio=required_empty_ratio,
                    conflict_ratio=conflict_ratio,
                    uncertain_ratio=0.0,
                    dependency_readiness=dependency_readiness,
                    initial_prior=initial_prior,
                    recent_emergence=recent_emergence,
                    continuity=continuity,
                    user_relevance=user_relevance,
                )
            )

        return views

    def get_project_summary(self) -> dict[str, Any]:
        all_topics = self.get_all_topics()
        total_slots = sum(len(t.slots) for t in all_topics)
        filled_slots = sum(
            len([s for s in t.slots if s.value is not None and str(s.value).strip() != ""])
            for t in all_topics
        )
        ready_topics = [
            t.topic_id for t in all_topics
            if t.topic_status not in ("Completed", "UserInterrupted") and self.is_ready_for_verification(t.topic_id)
        ]
        return {
            "project_id": self._state.project_id,
            "project_name": self._state.project_name,
            "project_status": self._state.project_status,
            "turn_index": self._state.turn_index,
            "total_topics": len(all_topics),
            "seed_topics": len(self.get_seed_topics()),
            "emergent_topics": len(self.get_emergent_topics()),
            "total_slots": total_slots,
            "filled_slots": filled_slots,
            "overall_slot_coverage": (filled_slots / total_slots) if total_slots > 0 else 0.0,
            "ready_for_verification_topics": ready_topics,
            "ready_for_verification_count": len(ready_topics),
        }
