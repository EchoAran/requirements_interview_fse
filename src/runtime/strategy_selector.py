from typing import Optional, Set
from config import StrategyConfig
from models.event import EvidenceRef
from models.scheduling import IntentDecision, SchedulerDecision
from models.state import ProjectState, TopicState
from models.strategy import QuestionPlan
from services.state_view import StateView

STRATEGY_INSTRUCTIONS: dict[str, str] = {
    "explore": (
        "Phase: Initial Exploration;\n"
        "Core Objective: Guide the interviewee to give an overarching narrative of the topic based on their actual business context, without enumerating discrete slot lists upfront;\n"
        "Key Guidelines: Use open-ended questions, avoid leading suggestions or technical jargon, and maintain a natural conversational dialog."
    ),
    "fill_gap": (
        "Phase: Information Gap Filling;\n"
        "Core Objective: Focus directly on the most critical missing requirement slot and prompt the user to supply the missing factual information;\n"
        "Key Guidelines: Explicitly point to the target slot, keep the question concise, concrete, and easily understood, and avoid compound questionnaire lists."
    ),
    "deepen": (
        "Phase: Targeted Deepening Within Active Topic;\n"
        "Core Objective: Clarify the specific missing nuance, tentative rule, or boundary condition for the designated target item without straying to other topics or slots;\n"
        "Key Guidelines: Ground the question strictly in existing evidence, ask only about the single designated target item and clarification reason, and avoid repeating recently covered rules or boundaries."
    ),
    "resolve_conflict": (
        "Phase: Conflict Resolution;\n"
        "Core Objective: Address two diverging or contradictory statements recorded for a requirement item, guiding the interviewee to clarify the authoritative business rule or applicable conditions;\n"
        "Key Guidelines: Neutrally and objectively present both recorded viewpoints, politely asking the interviewee to confirm which rule applies or under what circumstances."
    ),
    "verify": (
        "Phase: Synthesis & Verification;\n"
        "Core Objective: Concisely synthesize both confirmed requirement points and pending tentative items under the active topic, verifying completeness with the interviewee;\n"
        "Key Guidelines: Explicitly present uncertain items as pending rather than confirmed facts, and ask if anything needs revision or addition before concluding the topic."
    ),
    "confirm_control": (
        "Phase: Control Intent Confirmation;\n"
        "Core Objective: Clarify whether the interviewee wishes to switch, skip, or conclude the current discussion, without introducing new domain requirement questions;\n"
        "Key Guidelines: Ask a clear confirmation or choice question with a polite tone, avoiding unilateral assumptions."
    ),
}


class StrategySelector:
    """Selects question strategy and produces structured QuestionPlan based on Topic state signals and conversation intent."""

    def __init__(self, config: Optional[StrategyConfig] = None):
        self.config = config or StrategyConfig()

    def select_plan(
        self,
        state: ProjectState,
        topic: TopicState,
        intent_decision: Optional[IntentDecision] = None,
        scheduler_decision: Optional[SchedulerDecision] = None,
        transition_from_topic_id: Optional[str] = None,
        evidence_refs: Optional[list[EvidenceRef]] = None,
        forced_deepen_target: Optional[tuple[str, str]] = None,
        allow_explore: bool = True,
        explored_topic_ids: Optional[Set[str]] = None,
    ) -> QuestionPlan:
        """Determines the appropriate strategy and target slots based on state signals."""
        view = StateView(state, evidence_refs=evidence_refs)
        topic_id = topic.topic_id

        if intent_decision and intent_decision.needs_confirmation:
            return QuestionPlan(
                strategy="confirm_control",
                topic_id=topic_id,
                target_topic_id=intent_decision.target_topic_id,
                control_intent=intent_decision.intent,
                transition_from_topic_id=transition_from_topic_id,
            )

        conflict_slots = view.get_conflict_slots(topic_id)
        if conflict_slots:
            return QuestionPlan(
                strategy="resolve_conflict",
                topic_id=topic_id,
                target_conflict_slot_ids=[s.slot_id for s in conflict_slots],
                transition_from_topic_id=transition_from_topic_id,
            )

        if forced_deepen_target:
            forced_slot_id, forced_reason = forced_deepen_target
            if topic.find_slot_by_id(forced_slot_id):
                return QuestionPlan(
                    strategy="deepen",
                    topic_id=topic_id,
                    target_slot_ids=[forced_slot_id],
                    deepening_reason=forced_reason,
                    transition_from_topic_id=transition_from_topic_id,
                )

        if (
            allow_explore
            and (explored_topic_ids is None or topic_id not in explored_topic_ids)
            and view.interview_evidence_count(topic_id) == 0
        ):
            return QuestionPlan(
                strategy="explore",
                topic_id=topic_id,
                transition_from_topic_id=transition_from_topic_id,
            )

        empty_req_slots = view.get_empty_required_slots(topic_id)
        if empty_req_slots:
            target_ids = [s.slot_id for s in empty_req_slots[:self.config.max_target_slots]]
            return QuestionPlan(
                strategy="fill_gap",
                topic_id=topic_id,
                target_slot_ids=target_ids,
                transition_from_topic_id=transition_from_topic_id,
            )

        deepen_candidates = view.get_deepening_target_slots(topic_id)
        if deepen_candidates:
            selected = deepen_candidates[:self.config.max_target_slots]
            target_ids = [slot.slot_id for slot, _ in selected]
            reason = selected[0][1]
            return QuestionPlan(
                strategy="deepen",
                topic_id=topic_id,
                target_slot_ids=target_ids,
                deepening_reason=reason,
                transition_from_topic_id=transition_from_topic_id,
            )

        return QuestionPlan(
            strategy="verify",
            topic_id=topic_id,
            transition_from_topic_id=transition_from_topic_id,
        )
