from typing import Optional
from ..models.event import EvidenceRef
from ..models.scheduling import IntentDecision, SchedulerDecision
from ..models.state import ProjectState, TopicState
from ..models.strategy import QuestionPlan, StrategyCode
from ..services.state_view import StateView

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
        "Phase: Deep Elicitation & Edge Case Exploration;\n"
        "Core Objective: Probe deeper into implicit requirements, clarify ambiguous operational boundaries, and uncover exception handling scenarios based on existing facts;\n"
        "Key Guidelines: Ground the question firmly in previously given answers, focusing on concrete workflow nuances, boundary rules, or uncertainties."
    ),
    "resolve_conflict": (
        "Phase: Conflict Resolution;\n"
        "Core Objective: Address two diverging or contradictory statements recorded for a requirement item, guiding the interviewee to clarify the authoritative business rule or applicable conditions;\n"
        "Key Guidelines: Neutrally and objectively present both recorded viewpoints, politely asking the interviewee to confirm which rule applies or under what circumstances."
    ),
    "verify": (
        "Phase: Synthesis & Verification;\n"
        "Core Objective: Concisely synthesize the key confirmed requirement points under the active topic and verify accuracy and completeness with the interviewee;\n"
        "Key Guidelines: Briefly summarize established points, asking if anything needs revision or addition before transitioning forward."
    ),
    "confirm_control": (
        "Phase: Control Intent Confirmation;\n"
        "Core Objective: Clarify whether the interviewee wishes to switch, skip, or conclude the current discussion, without introducing new domain requirement questions;\n"
        "Key Guidelines: Ask a clear confirmation or choice question with a polite tone, avoiding unilateral assumptions."
    ),
}

QUESTION_STRATEGY_INSTRUCTIONS = STRATEGY_INSTRUCTIONS


class StrategySelector:
    """Selects question strategy and produces structured QuestionPlan based on Topic state signals and conversation intent."""

    def __init__(self, completion_threshold: float = 0.6):
        self.completion_threshold = completion_threshold

    def select_plan(
        self,
        state: ProjectState,
        topic: TopicState,
        intent_decision: Optional[IntentDecision] = None,
        scheduler_decision: Optional[SchedulerDecision] = None,
        transition_from_topic_id: Optional[str] = None,
        evidence_refs: Optional[list[EvidenceRef]] = None,
    ) -> QuestionPlan:
        """Determines the appropriate strategy and target slots based on state signals."""
        view = StateView(state, evidence_refs=evidence_refs)
        topic_id = topic.topic_id

        # 1. Highest Priority: Intent confirmation
        if intent_decision and intent_decision.needs_confirmation:
            tgt_topic_id = getattr(intent_decision, "target_topic_id", None) or getattr(intent_decision, "target_topic_number", None)
            return QuestionPlan(
                strategy="confirm_control",
                topic_id=topic_id,
                target_topic_id=tgt_topic_id,
                control_intent=intent_decision.intent,
                transition_from_topic_id=transition_from_topic_id,
            )

        # 2. Priority 2: Conflict resolution
        conflict_slots = view.get_conflict_slots(topic_id)
        if conflict_slots:
            return QuestionPlan(
                strategy="resolve_conflict",
                topic_id=topic_id,
                target_conflict_slot_ids=[s.slot_id for s in conflict_slots],
                transition_from_topic_id=transition_from_topic_id,
            )

        # 3. Priority 3: Initial exploration (no genuine interview evidence on this topic)
        if view.interview_evidence_count(topic_id) == 0:
            return QuestionPlan(
                strategy="explore",
                topic_id=topic_id,
                transition_from_topic_id=transition_from_topic_id,
            )

        # 4. Priority 4: Fill gap (empty required slots)
        empty_req_slots = view.get_empty_required_slots(topic_id)
        if empty_req_slots:
            target_ids = [s.slot_id for s in empty_req_slots[:1]]
            return QuestionPlan(
                strategy="fill_gap",
                topic_id=topic_id,
                target_slot_ids=target_ids,
                transition_from_topic_id=transition_from_topic_id,
            )

        # 5. Priority 5: Deepen (uncertain slots or heuristic deepening needed)
        uncertain_slots = view.get_uncertain_slots(topic_id)
        if uncertain_slots or view.needs_deepening(topic_id):
            target_ids = [s.slot_id for s in uncertain_slots] if uncertain_slots else []
            return QuestionPlan(
                strategy="deepen",
                topic_id=topic_id,
                target_slot_ids=target_ids,
                transition_from_topic_id=transition_from_topic_id,
            )

        # 6. Priority 6: Verify (all required information collected and clear)
        return QuestionPlan(
            strategy="verify",
            topic_id=topic_id,
            transition_from_topic_id=transition_from_topic_id,
        )

    @staticmethod
    def compute_completion(topic: TopicState) -> float:
        if not topic.slots:
            return 0.0

        target_slots = [s for s in topic.slots if s.is_required] or topic.slots
        filled = [
            s for s in target_slots
            if s.value is not None and str(s.value).strip() != ""
        ]
        return len(filled) / len(target_slots)

    def select(self, topic: TopicState) -> tuple[str, str, float]:
        """Calculates completion and maps to exploration code."""
        c = self.compute_completion(topic)
        if c == 0.0:
            code = "S1"
        elif 0.0 < c < self.completion_threshold:
            code = "S2"
        elif self.completion_threshold <= c < 1.0:
            code = "S3"
        else:
            code = "S4"

        legacy_map = {"S1": "explore", "S2": "fill_gap", "S3": "deepen", "S4": "verify"}
        inst = STRATEGY_INSTRUCTIONS.get(code, STRATEGY_INSTRUCTIONS.get(legacy_map.get(code, "fill_gap"), ""))
        return code, inst, c
