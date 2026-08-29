from typing import Optional
from ..models.event import EvidenceRef
from ..models.scheduling import IntentDecision, SchedulerDecision
from ..models.state import ProjectState, TopicState
from ..models.strategy import QuestionPlan, StrategyCode
from ..services.state_view import StateView

STRATEGY_INSTRUCTIONS: dict[str, str] = {
    "explore": (
        "阶段：初始探索阶段；\n"
        "核心目标：引导受访者结合实际场景自然概述主题核心，不预先罗列具体槽位；\n"
        "提问要点：采用开放式提问，避免引导性暗示或技术术语，保持自然对话风格。"
    ),
    "fill_gap": (
        "阶段：填补缺口阶段；\n"
        "核心目标：聚焦当前最关键的目标空缺信息点，引导用户补充缺失事实；\n"
        "提问要点：明确指向目标空缺槽位，提问简洁具体，通俗易懂，避免一次性抛出清单式问题。"
    ),
    "deepen": (
        "阶段：深度挖掘阶段；\n"
        "核心目标：围绕已有信息深挖隐性需求、澄清模糊边界与异常场景，补充具体细节；\n"
        "提问要点：紧扣已有回答提问，聚焦使用场景细节、特殊处理边界或不确定项。"
    ),
    "resolve_conflict": (
        "阶段：冲突化解阶段；\n"
        "核心目标：针对已记录的两种不同或矛盾说法，引导受访者澄清实际规则或适用条件；\n"
        "提问要点：中立客观地呈现已有两种说法，礼貌请受访者确认哪种为准或在何种条件下适用。"
    ),
    "verify": (
        "阶段：闭环确认阶段；\n"
        "核心目标：简短总结当前主题已收集确认的关键需求信息，向受访者确认是否完整准确；\n"
        "提问要点：简洁汇总主要信息点，询问是否还有需要修正或补充的内容，没有补充则准备过渡。"
    ),
    "confirm_control": (
        "阶段：控制意图确认阶段；\n"
        "核心目标：向受访者确认是否希望切换/拒绝/结束当前主题，不混入新的业务需求问题；\n"
        "提问要点：提出明确的二选一或确认提问，语气礼貌客气，避免直接擅自决定。"
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
