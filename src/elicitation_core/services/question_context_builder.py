from typing import Any, Optional
from ..config import ContextBudgetConfig
from ..models.event import EvidenceRef
from ..models.interpretation import TopicDigest
from ..models.state import ProjectState, SlotState, TopicState
from ..models.strategy import (
    ConflictClaim,
    EvidenceSnippet,
    KnownInfoDigest,
    QuestionGenerationInput,
    QuestionPlan,
    QuestionTransition,
    SlotDigest,
    TargetContext,
    TargetSlotContext,
)
from ..models.turn import TurnRecord
from .state_view import StateView


class QuestionContextBuilder:
    """Pure Python builder service that constructs QuestionGenerationInput from ProjectState, QuestionPlan, turns, evidences, and transition."""

    def _extract_evidence_snippets(
        self,
        evidence_ids: list[str],
        evidences: list[EvidenceRef],
        max_count: int = 2,
    ) -> list[EvidenceSnippet]:
        ev_map = {e.evidence_id: e for e in evidences}
        snippets: list[EvidenceSnippet] = []
        # Select newest evidence first (traverse in reverse order)
        for eid in reversed(evidence_ids):
            if eid in ev_map:
                ev = ev_map[eid]
                snippets.append(
                    EvidenceSnippet(
                        evidence_id=ev.evidence_id,
                        turn_id=ev.turn_id,
                        speaker="Interviewee" if ev.source_type == "interview_turn" else None,
                        content=ev.content,
                    )
                )
                if len(snippets) >= max_count:
                    break
        return snippets

    def _build_target_context(
        self,
        state: ProjectState,
        plan: QuestionPlan,
        target_topic: TopicState,
        evidences: list[EvidenceRef],
    ) -> TargetContext:
        strat = plan.strategy

        # Project relations if any target_relation_ids or matching topic dependencies
        target_relations: list[dict[str, Any]] = []
        if plan.target_relation_ids:
            rel_set = set(plan.target_relation_ids)
            for dep in state.dependencies:
                src_topic = state.find_topic_by_id(dep.source) or state.find_topic_by_number(dep.source)
                tgt_topic = state.find_topic_by_id(dep.target) or state.find_topic_by_number(dep.target)
                src_title = src_topic.topic_content if src_topic else dep.source
                tgt_title = tgt_topic.topic_content if tgt_topic else dep.target

                edge_repr = f"{dep.source}->{dep.target}"
                if (
                    edge_repr in rel_set
                    or dep.source in rel_set
                    or dep.target in rel_set
                    or (src_topic and src_topic.topic_id in rel_set)
                    or (tgt_topic and tgt_topic.topic_id in rel_set)
                ):
                    target_relations.append({
                        "source": src_title,
                        "target": tgt_title,
                        "description": f"【{tgt_title}】依赖于【{src_title}】",
                    })


        if strat == "explore":
            return TargetContext(target_relations=target_relations)

        if strat == "confirm_control":
            target_topics: list[TopicDigest] = []
            if plan.target_topic_id:
                tgt_t = state.find_topic_by_id(plan.target_topic_id) or state.find_topic_by_number(plan.target_topic_id)
                if tgt_t:
                    target_topics.append(
                        TopicDigest(
                            topic_id=tgt_t.topic_id,
                            topic_number=tgt_t.topic_number,
                            topic_content=tgt_t.topic_content,
                            section_id=tgt_t.section_id,
                            section_number=tgt_t.section_id,
                            slots_keys=[s.key for s in tgt_t.slots],
                            status=tgt_t.topic_status,
                        )
                    )
            return TargetContext(target_relations=target_relations, target_topics=target_topics)

        if strat == "fill_gap":
            target_slot: Optional[SlotState] = None
            if plan.target_slot_ids:
                # Strictly validate that requested target slot exists
                requested_id = plan.target_slot_ids[0]
                target_slot = target_topic.find_slot(requested_id)
                if not target_slot:
                    raise ValueError(
                        f"Target slot_id '{requested_id}' not found in topic '{target_topic.topic_id}' for fill_gap."
                    )
            else:
                # If plan has no target_slot_ids, deterministically find first empty slot
                for s in target_topic.slots:
                    if s.is_required and (s.value is None or str(s.value).strip() == ""):
                        target_slot = s
                        break
                if not target_slot:
                    for s in target_topic.slots:
                        if s.value is None or str(s.value).strip() == "":
                            target_slot = s
                            break
                if not target_slot:
                    raise ValueError(
                        f"No empty slot available in topic '{target_topic.topic_id}' for fill_gap strategy."
                    )

            snippets = self._extract_evidence_snippets(target_slot.evidence_refs, evidences, max_count=2)
            target_slots = [
                TargetSlotContext(
                    slot_id=target_slot.slot_id,
                    semantic_key=target_slot.key,
                    current_value=target_slot.value,
                    state=target_slot.state,
                    is_required=target_slot.is_required,
                    evidence_snippets=snippets,
                )
            ]
            return TargetContext(target_slots=target_slots, target_relations=target_relations)

        if strat == "deepen":
            target_slot: Optional[SlotState] = None
            if plan.target_slot_ids:
                requested_id = plan.target_slot_ids[0]
                target_slot = target_topic.find_slot(requested_id)
                if not target_slot:
                    raise ValueError(
                        f"Target slot_id '{requested_id}' not found in topic '{target_topic.topic_id}' for deepen."
                    )
            else:
                # Deterministically select first filled or uncertain slot that has evidence
                ev_id_set = {e.evidence_id for e in evidences}
                for s in target_topic.slots:
                    if (s.state in ("uncertain", "filled") or (s.value is not None and str(s.value).strip() != "")) and any(
                        eid in ev_id_set for eid in s.evidence_refs
                    ):
                        target_slot = s
                        break
                if not target_slot:
                    for s in target_topic.slots:
                        if s.state in ("uncertain", "filled") or (s.value is not None and str(s.value).strip() != ""):
                            target_slot = s
                            break
                if not target_slot:
                    raise ValueError(
                        f"No filled or uncertain slot available in topic '{target_topic.topic_id}' for deepen strategy."
                    )

            snippets = self._extract_evidence_snippets(target_slot.evidence_refs, evidences, max_count=2)
            target_slots = [
                TargetSlotContext(
                    slot_id=target_slot.slot_id,
                    semantic_key=target_slot.key,
                    current_value=target_slot.value,
                    state=target_slot.state,
                    is_required=target_slot.is_required,
                    evidence_snippets=snippets,
                )
            ]
            return TargetContext(target_slots=target_slots, target_relations=target_relations)

        if strat == "resolve_conflict":
            target_slots: list[TargetSlotContext] = []
            conflict_claims: dict[str, list[ConflictClaim]] = {}

            c_slots: list[SlotState] = []
            if plan.target_conflict_slot_ids:
                for sid in plan.target_conflict_slot_ids:
                    s = target_topic.find_slot(sid)
                    if not s:
                        raise ValueError(
                            f"Target conflict slot_id '{sid}' not found in topic '{target_topic.topic_id}'."
                        )
                    c_slots.append(s)
            else:
                c_slots = [s for s in target_topic.slots if s.state == "conflict"]

            if not c_slots:
                raise ValueError(
                    f"No conflict slots found in topic '{target_topic.topic_id}' for resolve_conflict."
                )

            for cs in c_slots:
                snippets = self._extract_evidence_snippets(cs.evidence_refs, evidences, max_count=2)
                target_slots.append(
                    TargetSlotContext(
                        slot_id=cs.slot_id,
                        semantic_key=cs.key,
                        current_value=cs.value,
                        state=cs.state,
                        is_required=cs.is_required,
                        evidence_snippets=snippets,
                    )
                )

                # Extract distinct candidates with independent evidence
                claims_by_value: dict[str, list[EvidenceSnippet]] = {}
                used_evidence_ids: set[str] = set()

                # 1. Collect candidate values and their supporting evidence from revisions
                for rev in reversed(cs.revisions):
                    rev_ev_snippets = self._extract_evidence_snippets(rev.evidence_refs, evidences, max_count=2)
                    if rev.new_value and str(rev.new_value).strip() != "":
                        val_str = str(rev.new_value).strip()
                        if val_str not in claims_by_value and rev_ev_snippets:
                            valid_snips = [s for s in rev_ev_snippets if s.evidence_id not in used_evidence_ids]
                            if valid_snips:
                                claims_by_value[val_str] = valid_snips
                                for s in valid_snips:
                                    used_evidence_ids.add(s.evidence_id)

                    if rev.old_value and str(rev.old_value).strip() != "":
                        val_str = str(rev.old_value).strip()
                        if val_str not in claims_by_value and rev_ev_snippets:
                            valid_snips = [s for s in rev_ev_snippets if s.evidence_id not in used_evidence_ids]
                            if valid_snips:
                                claims_by_value[val_str] = valid_snips
                                for s in valid_snips:
                                    used_evidence_ids.add(s.evidence_id)

                # 2. Check current slot.value if not yet captured
                if cs.value and str(cs.value).strip() != "":
                    val_str = str(cs.value).strip()
                    if val_str not in claims_by_value:
                        slot_ev_snippets = self._extract_evidence_snippets(cs.evidence_refs, evidences, max_count=2)
                        valid_snips = [s for s in slot_ev_snippets if s.evidence_id not in used_evidence_ids]
                        if valid_snips:
                            claims_by_value[val_str] = valid_snips
                            for s in valid_snips:
                                used_evidence_ids.add(s.evidence_id)

                claims = [
                    ConflictClaim(value=v, evidence_snippets=snips)
                    for v, snips in claims_by_value.items()
                ]

                if len(claims) < 2:
                    raise ValueError(
                        f"Conflict slot '{cs.key}' does not have at least 2 distinct evidenced claims."
                    )

                conflict_claims[cs.key] = claims

            return TargetContext(
                target_slots=target_slots,
                conflict_claims=conflict_claims,
                target_relations=target_relations,
            )

        if strat == "verify":
            ev_id_set = {e.evidence_id for e in evidences}
            verify_facts = [
                {"key": s.key, "value": s.value}
                for s in target_topic.slots
                if s.state == "filled"
                and s.value is not None
                and str(s.value).strip() != ""
                and any(eid in ev_id_set for eid in s.evidence_refs)
            ]
            return TargetContext(verify_facts=verify_facts, target_relations=target_relations)

        return TargetContext(target_relations=target_relations)

    def _select_bounded_recent_turns(
        self,
        turns: list[TurnRecord],
        window_size: int = 6,
    ) -> list[TurnRecord]:
        """Selects bounded recent conversation turns while strictly ensuring the latest Q&A pair is preserved.

        1. Preserves chronological order.
        2. Deduplicates by turn_id.
        3. If window_size <= 0, still preserves the latest Q&A pair.
        4. Always guarantees the latest Interviewee turn and its preceding Interviewer turn exist if turns are present.
        5. Does not re-include excessive intermediate turns when roles are non-alternating.
        """
        if not turns:
            return []

        seen_ids = set()
        deduped: list[TurnRecord] = []
        for t in turns:
            if t.turn_id not in seen_ids:
                seen_ids.add(t.turn_id)
                deduped.append(t)

        effective_limit = max(2, window_size) if window_size > 0 else 2
        if len(deduped) <= effective_limit:
            return deduped

        latest_interviewee_idx = None
        for i in range(len(deduped) - 1, -1, -1):
            if deduped[i].role == "Interviewee":
                latest_interviewee_idx = i
                break

        must_have_indices = set()
        if latest_interviewee_idx is not None:
            must_have_indices.add(latest_interviewee_idx)
            for i in range(latest_interviewee_idx - 1, -1, -1):
                if deduped[i].role == "Interviewer":
                    must_have_indices.add(i)
                    break
        else:
            must_have_indices.add(len(deduped) - 1)

        selected_indices = set(must_have_indices)
        for i in range(len(deduped) - 1, -1, -1):
            if len(selected_indices) >= effective_limit:
                break
            selected_indices.add(i)

        selected_ordered = [deduped[i] for i in sorted(selected_indices)]
        return selected_ordered

    def _select_relevant_cross_topic_info(
        self,
        state: ProjectState,
        target_topic: TopicState,
        target_context: TargetContext,
        project_known_info: list[KnownInfoDigest],
    ) -> list[KnownInfoDigest]:
        """Selects relevant cross-topic known facts while strictly excluding current topic facts."""
        current_topic_id = target_topic.topic_id
        current_topic_num = target_topic.topic_number
        current_topic_title = target_topic.topic_content

        # 1. Collect relevant topic identifiers from dependencies
        relevant_identifiers: set[str] = set()
        for dep in state.dependencies:
            if dep.target in (current_topic_id, current_topic_num):
                relevant_identifiers.add(dep.source)
            elif dep.source in (current_topic_id, current_topic_num):
                relevant_identifiers.add(dep.target)

        # 2. Collect relevant topic identifiers from TargetContext
        if target_context.target_topics:
            for tt in target_context.target_topics:
                relevant_identifiers.add(tt.topic_id)
                relevant_identifiers.add(tt.topic_number)
                relevant_identifiers.add(tt.topic_content)

        if target_context.target_relations:
            for rel in target_context.target_relations:
                if rel.get("source"):
                    relevant_identifiers.add(rel["source"])
                if rel.get("target"):
                    relevant_identifiers.add(rel["target"])

        # 3. Filter project_known_info
        filtered_known_info: list[KnownInfoDigest] = []
        for info in project_known_info:
            # ALWAYS exclude current topic facts (already in current state block)
            if (
                info.topic_id in (current_topic_id, current_topic_num)
                or info.topic_content == current_topic_title
            ):
                continue

            # Include if topic is in relevant_identifiers
            is_relevant = (
                info.topic_id in relevant_identifiers
                or info.topic_content in relevant_identifiers
            )
            if is_relevant:
                filtered_known_info.append(info)

        return filtered_known_info

    def _project_current_topic_slots(
        self,
        topic_slots: list[SlotDigest],
        target_context: TargetContext,
    ) -> tuple[list[SlotDigest], int]:
        """Projects current topic slots strictly preserving target, conflict, uncertain and verify slots.

        All critical slots (target, conflict, uncertain, verify) are ALWAYS preserved without truncation.
        Non-critical other slots are omitted, and their count is returned as omitted_count.
        """
        target_keys = set()
        if target_context.target_slots:
            target_keys = {ts.semantic_key for ts in target_context.target_slots}

        verify_keys = set()
        if target_context.verify_facts:
            verify_keys = {f.get("key") for f in target_context.verify_facts if isinstance(f, dict)}

        target_slots = [s for s in topic_slots if s.key in target_keys]
        conflict_uncertain_slots = [
            s for s in topic_slots if s.key not in target_keys and s.state in ("conflict", "uncertain")
        ]
        verify_slots = [
            s for s in topic_slots
            if s.key not in target_keys
            and s.state not in ("conflict", "uncertain")
            and s.key in verify_keys
        ]
        other_slots = [
            s for s in topic_slots
            if s.key not in target_keys
            and s.state not in ("conflict", "uncertain")
            and s.key not in verify_keys
        ]

        critical_slots = target_slots + conflict_uncertain_slots + verify_slots
        omitted_count = len(other_slots)
        return critical_slots, omitted_count

    def build(
        self,
        state: ProjectState,
        plan: QuestionPlan,
        turns: list[TurnRecord],
        evidences: list[EvidenceRef],
        transition: Optional[QuestionTransition] = None,
        context_budget: Optional[ContextBudgetConfig] = None,
    ) -> QuestionGenerationInput:
        target_topic = state.find_topic_by_id(plan.topic_id) or state.find_topic_by_number(plan.topic_id)
        if not target_topic:
            raise ValueError(f"Plan topic_id '{plan.topic_id}' not found in project state.")

        budget_cfg = context_budget or ContextBudgetConfig()

        view = StateView(state, evidence_refs=evidences)
        topic_catalog = view.get_topic_catalog_items()
        if not topic_catalog:
            raise ValueError("Project state has no topics to form a topic_catalog.")

        raw_topic_slots = view.get_slot_digests(target_topic.topic_id)
        raw_project_known_info = view.get_project_known_info()

        target_section = None
        for sec in state.sections:
            if sec.section_id == target_topic.section_id or any(t.topic_id == target_topic.topic_id for t in sec.topics):
                target_section = sec
                break

        section_id = target_section.section_id if target_section else target_topic.section_id
        section_number = target_section.section_number if target_section else target_topic.section_id

        target_context = self._build_target_context(state, plan, target_topic, evidences)

        # 1. Bounded recent turns (default 6, guarantees latest Q&A pair)
        bounded_turns = self._select_bounded_recent_turns(
            turns, window_size=budget_cfg.history_window_size
        )

        # 2. Selected relevant cross-topic facts (excludes current topic facts)
        filtered_known_info = self._select_relevant_cross_topic_info(
            state=state,
            target_topic=target_topic,
            target_context=target_context,
            project_known_info=raw_project_known_info,
        )

        # 3. Projected current topic slots (critical slots only + omitted count)
        projected_slots, omitted_slots_count = self._project_current_topic_slots(
            topic_slots=raw_topic_slots,
            target_context=target_context,
        )

        topic_digest = TopicDigest(
            topic_id=target_topic.topic_id,
            topic_number=target_topic.topic_number,
            topic_content=target_topic.topic_content,
            section_id=section_id,
            section_number=section_number,
            slots_keys=[s.key for s in projected_slots],
            status=target_topic.topic_status,
        )

        return QuestionGenerationInput(
            plan=plan,
            topic=topic_digest,
            topic_catalog=topic_catalog,
            target_context=target_context,
            topic_slots=projected_slots,
            recent_turns=bounded_turns,
            project_known_info=filtered_known_info,
            scheduler_transition=transition,
            other_slots_omitted_count=omitted_slots_count,
        )



