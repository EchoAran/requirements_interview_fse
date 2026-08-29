import json
from dataclasses import dataclass, field
from typing import Any, Optional
from ..config import ContextBudgetConfig
from ..llm.template import render_prompt
from ..models.strategy import (
    ConflictClaim,
    EvidenceSnippet,
    KnownInfoDigest,
    QuestionGenerationInput,
    QuestionPlan,
    SlotDigest,
    TargetContext,
    TargetSlotContext,
    TopicCatalogItem,
)
from ..models.turn import TurnRecord


def estimate_tokens(text: str) -> int:
    """Conservative token estimator for mixed CJK, English, and JSON punctuation.

    Estimator name: cjk_ascii_conservative_v1
    - Non-ASCII (CJK characters): ~1.2 tokens/char
    - ASCII characters: ~3.5 chars/token (~0.285 token/char)
    """
    if not text:
        return 0
    non_ascii = sum(1 for c in text if ord(c) > 127)
    ascii_chars = len(text) - non_ascii
    return int(ascii_chars / 3.5 + non_ascii * 1.2) + 1


def format_target_and_evidence(plan: QuestionPlan, tc: TargetContext) -> tuple[str, str]:
    """Formats target context and supporting evidence into separate ID-free semantic instructions for the LLM."""
    strat = plan.strategy
    target_desc = "Advance the interview dialogue smoothly."
    evidence_desc = "(No specific evidence attached)"

    if strat == "fill_gap":
        if tc and tc.target_slots:
            target_slot = tc.target_slots[0]
            target_desc = f"Inquire naturally about the missing information item '{target_slot.semantic_key}'."
            if target_slot.evidence_snippets:
                evidence_desc = "\n".join(f'- "{snip.content}"' for snip in target_slot.evidence_snippets)
        else:
            target_desc = "Inquire about key missing information items under the active topic."

    elif strat == "deepen":
        if tc and tc.target_slots:
            target_slot = tc.target_slots[0]
            val_repr = f" (Current known value: {target_slot.current_value})" if target_slot.current_value else ""
            target_desc = f"Probe deeper into concrete use scenarios, refined constraints, or operational boundaries for known item '{target_slot.semantic_key}'{val_repr}."
            if target_slot.evidence_snippets:
                evidence_desc = "\n".join(f'- "{snip.content}"' for snip in target_slot.evidence_snippets)
        else:
            target_desc = "Probe deeper into specific details and operational scenarios for existing information under the active topic."

    elif strat == "resolve_conflict":
        if tc and tc.conflict_claims:
            lines = ["Objectively present and clarify conflicting statements, asking the interviewee to confirm the authoritative rule."]
            ev_lines = []
            for key, claims in tc.conflict_claims.items():
                lines.append(f"Diverging records for '{key}':")
                for idx, claim in enumerate(claims, 1):
                    lines.append(f"  Statement {idx}: {claim.value}")
                    for snip in claim.evidence_snippets:
                        ev_lines.append(f'- Statement {idx} supporting evidence: "{snip.content}"')
            target_desc = "\n".join(lines)
            if ev_lines:
                evidence_desc = "\n".join(ev_lines)
        else:
            target_desc = "Objectively request the interviewee to clarify conflicting statements recorded under the active topic."

    elif strat == "verify":
        if tc and tc.verify_facts:
            lines = ["Synthesize and verify core confirmed requirement facts under the active topic:"]
            for f in tc.verify_facts:
                lines.append(f"  - {f['key']}: {f['value']}")
            target_desc = "\n".join(lines)
            evidence_desc = "(The above facts have been supported by verified evidence in preceding turns)"
        else:
            target_desc = "Briefly summarize key requirements under the active topic and ask the interviewee if anything is missing or needs revision."

    elif strat == "confirm_control":
        intent_desc = plan.control_intent or "modify interview flow"
        target_topic_title = None
        if tc and tc.target_topics:
            target_topic_title = tc.target_topics[0].topic_content
        if target_topic_title:
            target_desc = f"Detected user intention to switch to '{target_topic_title}'. Politely ask a confirmation choice (switch to that topic or continue the active topic) without introducing new requirement inquiries."
        else:
            target_desc = f"Detected user intention to '{intent_desc}'. Politely confirm their choice without introducing new requirement inquiries."

    elif strat == "explore":
        target_desc = "Conduct open-ended exploration around core business scenarios, key objectives, and operational scope of the active topic."

    if tc and tc.target_relations:
        rel_lines = ["[Associated Dependencies]: Please confirm and discuss the topic dependencies:"]
        for rel in tc.target_relations:
            desc = rel.get("description")
            if not desc:
                desc = f"'{rel.get('target', '')}' depends on '{rel.get('source', '')}'"
            rel_lines.append(f"  - {desc}")
        if target_desc == "Advance the interview dialogue smoothly.":
            target_desc = "\n".join(rel_lines)
        else:
            target_desc = target_desc + "\n" + "\n".join(rel_lines)

    return target_desc, evidence_desc


def trim_target_context_evidence(tc: TargetContext) -> tuple[TargetContext, int]:
    """Trims extra evidence snippets while strictly guaranteeing every target slot and conflict candidate retains at least 1 evidence snippet.

    Never drops a conflict candidate claim and never leaves any claim with 0 evidence.
    """
    trimmed_snippets_count = 0

    new_target_slots = []
    for ts in tc.target_slots:
        if len(ts.evidence_snippets) > 1:
            trimmed_snippets_count += len(ts.evidence_snippets) - 1
            new_target_slots.append(ts.model_copy(update={"evidence_snippets": ts.evidence_snippets[:1]}))
        else:
            new_target_slots.append(ts)

    new_conflict_claims = {}
    for k, claims in tc.conflict_claims.items():
        new_claims = []
        for claim in claims:
            if len(claim.evidence_snippets) > 1:
                trimmed_snippets_count += len(claim.evidence_snippets) - 1
                new_claims.append(claim.model_copy(update={"evidence_snippets": claim.evidence_snippets[:1]}))
            else:
                new_claims.append(claim)
        new_conflict_claims[k] = new_claims

    new_tc = tc.model_copy(update={
        "target_slots": new_target_slots,
        "conflict_claims": new_conflict_claims,
    })
    return new_tc, trimmed_snippets_count


@dataclass
class BudgetResult:
    trimmed_input: QuestionGenerationInput
    final_prompt: str
    is_exceeded: bool
    total_tokens: int
    block_tokens: dict[str, int] = field(default_factory=dict)
    block_token_ratios: dict[str, float] = field(default_factory=dict)
    trimmed_items: dict[str, int] = field(default_factory=dict)
    trim_reasons: list[str] = field(default_factory=list)
    estimator: str = "cjk_ascii_conservative_v1"


class ContextBudgetManager:
    """Pure-function context budget and block-level trimming service for prompt generation."""

    def __init__(self, config: Optional[ContextBudgetConfig] = None):
        self.config = config or ContextBudgetConfig()

    def estimate_text_tokens(self, text: str) -> int:
        return estimate_tokens(text)

    def apply_budget(
        self,
        input_data: QuestionGenerationInput,
        prompt_template: str,
        strategy_instruction: str,
        target_text: Optional[str] = None,
        evidence_text: Optional[str] = None,
    ) -> BudgetResult:
        """Applies context budgeting and block-level trimming in a strictly pure functional manner.

        Does not mutate the input_data object. Trims complete turns, complete topics, or complete slots
        strictly from lowest to highest priority (P7 -> P6 -> P5 -> P4).
        Never drops essential target, conflict, uncertain, or verify slots.
        Never drops conflict candidate claims or their evidence.
        """
        trimmed_items: dict[str, int] = {
            "trimmed_turns": 0,
            "trimmed_known_info_topics": 0,
            "trimmed_catalog_items": 0,
            "trimmed_slots": input_data.other_slots_omitted_count,
            "trimmed_evidence_snippets": 0,
        }
        trim_reasons: list[str] = []

        # 1. Extract and clone working collections
        working_turns: list[TurnRecord] = list(input_data.recent_turns)
        working_known_info: list[KnownInfoDigest] = list(input_data.project_known_info)
        working_slots: list[SlotDigest] = list(input_data.topic_slots)
        working_catalog: list[TopicCatalogItem] = list(input_data.topic_catalog)
        working_tc: TargetContext = input_data.target_context.model_copy(deep=True)

        working_target_text, working_evidence_text = format_target_and_evidence(input_data.plan, working_tc)
        if target_text is not None and not input_data.target_context.conflict_claims:
            working_target_text = target_text
        if evidence_text is not None and not input_data.target_context.conflict_claims:
            working_evidence_text = evidence_text

        # Identify critical semantic slot keys
        target_keys = set()
        if input_data.target_context and input_data.target_context.target_slots:
            target_keys = {ts.semantic_key for ts in input_data.target_context.target_slots}

        verify_keys = set()
        if input_data.target_context and input_data.target_context.verify_facts:
            verify_keys = {f.get("key") for f in input_data.target_context.verify_facts if isinstance(f, dict)}

        # Essential slots: target slots, conflict slots, uncertain slots, verify slots
        essential_slots = [
            s for s in working_slots
            if s.key in target_keys or s.state in ("conflict", "uncertain") or s.key in verify_keys
        ]
        if not essential_slots and working_slots:
            essential_slots = [working_slots[0]]

        # Compute P1: Latest user answer
        latest_user_answer = "[Initial interview round, no user response yet]"
        for t in reversed(working_turns):
            if t.role == "Interviewee" and t.message_content:
                latest_user_answer = t.message_content
                break

        # Compute P2: Transition text
        transition_text = ""
        if input_data.scheduler_transition and input_data.scheduler_transition.user_facing_reason:
            transition_text = input_data.scheduler_transition.user_facing_reason

        topic_title = str(input_data.topic.topic_content)

        def _render_and_measure(
            turns: list[TurnRecord],
            known_info: list[KnownInfoDigest],
            slots: list[SlotDigest],
            catalog: list[TopicCatalogItem],
            tgt_text: str,
            ev_text: str,
        ) -> tuple[str, dict[str, int], int]:
            convo_records = [{"role": t.role, "content": t.message_content} for t in turns]
            current_slots_data = [
                {
                    "item_name": s.key,
                    "recorded_value": s.value if s.value is not None else "[Not provided]",
                    "is_required": "Required" if s.is_required else "Optional",
                    "status": "Confirmed" if s.state == "filled" else ("Conflict" if s.state == "conflict" else ("Uncertain" if s.state == "uncertain" else "Not provided")),
                }
                for s in slots
            ]
            if input_data.other_slots_omitted_count > 0:
                current_slots_data.append({
                    "omitted_other_items_count": input_data.other_slots_omitted_count
                })

            topics_list = [{"topic_title": k.topic_content} for k in catalog]

            entire_info: dict[str, dict] = {}
            for item in known_info:
                topic_key = item.topic_content
                slots_repr = [
                    {"item_name": s.key, "recorded_value": s.value if s.value is not None else "[Not provided]"}
                    for s in item.filled_slots
                ]
                entire_info[topic_key] = {"items": slots_repr}

            s_current_user_answer = latest_user_answer
            s_topic_and_transition = f"{topic_title}\n{transition_text}"
            s_target_and_evidence = f"{tgt_text}\n{ev_text}"
            s_slots = json.dumps(current_slots_data, ensure_ascii=False)
            s_turns = json.dumps(convo_records, ensure_ascii=False)
            s_known_info = json.dumps(entire_info, ensure_ascii=False)
            s_catalog = json.dumps(topics_list, ensure_ascii=False)
            s_strategy = strategy_instruction

            rendered_prompt = render_prompt(
                prompt_template,
                {
                    "{current_user_answer}": s_current_user_answer,
                    "{current_topic_content}": topic_title,
                    "{target}": tgt_text,
                    "{target_evidence}": ev_text,
                    "{current_topic_info_slots}": s_slots,
                    "{topics_list}": s_catalog,
                    "{entire_interview_info_slots}": s_known_info,
                    "{transition}": transition_text,
                    "{current_topic_conversation_record}": s_turns,
                    "{strategy}": s_strategy,
                }
            )

            blocks_tokens = {
                "current_user_answer": estimate_tokens(s_current_user_answer),
                "topic_and_transition": estimate_tokens(s_topic_and_transition),
                "target_and_evidence": estimate_tokens(s_target_and_evidence),
                "current_topic_slots": estimate_tokens(s_slots),
                "recent_turns": estimate_tokens(s_turns),
                "cross_topic_known_info": estimate_tokens(s_known_info),
                "topic_catalog": estimate_tokens(s_catalog),
                "strategy_instruction": estimate_tokens(s_strategy),
            }
            total_prompt_tokens = estimate_tokens(rendered_prompt)
            return rendered_prompt, blocks_tokens, total_prompt_tokens

        prompt, block_tokens, total_tokens = _render_and_measure(
            working_turns, working_known_info, working_slots, working_catalog, working_target_text, working_evidence_text
        )

        max_tokens = self.config.max_prompt_tokens

        # 2. Block-specific cap checks
        # 2.1 Target evidence block cap: trim extra snippets structurally while preserving all candidates
        if block_tokens["target_and_evidence"] > self.config.max_target_evidence_tokens:
            working_tc, trimmed_snips = trim_target_context_evidence(working_tc)
            if trimmed_snips > 0:
                trimmed_items["trimmed_evidence_snippets"] += trimmed_snips
                trim_reasons.append("target_evidence_trimmed_by_block_cap")
                working_target_text, working_evidence_text = format_target_and_evidence(input_data.plan, working_tc)
                prompt, block_tokens, total_tokens = _render_and_measure(
                    working_turns, working_known_info, working_slots, working_catalog, working_target_text, working_evidence_text
                )
            if block_tokens["target_and_evidence"] > self.config.max_target_evidence_tokens:
                trim_reasons.append("essential_evidence_exceeds_block_cap")

        # 2.2 Topic catalog block cap
        if block_tokens["topic_catalog"] > self.config.max_catalog_tokens and len(working_catalog) > 3:
            initial_cat_len = len(working_catalog)
            working_catalog = working_catalog[:3]
            trimmed_items["trimmed_catalog_items"] += initial_cat_len - len(working_catalog)
            trim_reasons.append("catalog_trimmed_by_block_cap")
            prompt, block_tokens, total_tokens = _render_and_measure(
                working_turns, working_known_info, working_slots, working_catalog, working_target_text, working_evidence_text
            )

        # 2.3 Cross-topic known info block cap
        while block_tokens["cross_topic_known_info"] > self.config.max_known_info_tokens and working_known_info:
            working_known_info.pop()
            trimmed_items["trimmed_known_info_topics"] += 1
            if "known_info_trimmed_by_block_cap" not in trim_reasons:
                trim_reasons.append("known_info_trimmed_by_block_cap")
            prompt, block_tokens, total_tokens = _render_and_measure(
                working_turns, working_known_info, working_slots, working_catalog, working_target_text, working_evidence_text
            )

        # 2.4 Recent turns block cap (keep at least latest Q&A pair)
        while block_tokens["recent_turns"] > self.config.max_recent_turn_tokens and len(working_turns) > 2:
            working_turns.pop(0)
            trimmed_items["trimmed_turns"] += 1
            if "recent_turns_trimmed_by_block_cap" not in trim_reasons:
                trim_reasons.append("recent_turns_trimmed_by_block_cap")
            prompt, block_tokens, total_tokens = _render_and_measure(
                working_turns, working_known_info, working_slots, working_catalog, working_target_text, working_evidence_text
            )

        # 2.5 Current topic slots block cap
        if block_tokens["current_topic_slots"] > self.config.max_current_slots_tokens:
            if len(working_slots) > len(essential_slots):
                trimmed_items["trimmed_slots"] += len(working_slots) - len(essential_slots)
                working_slots = list(essential_slots)
                trim_reasons.append("current_slots_trimmed_by_block_cap")
                prompt, block_tokens, total_tokens = _render_and_measure(
                    working_turns, working_known_info, working_slots, working_catalog, working_target_text, working_evidence_text
                )
            if block_tokens["current_topic_slots"] > self.config.max_current_slots_tokens:
                trim_reasons.append("essential_slots_exceed_block_cap")

        # Priority-based global context shedding if total_tokens exceeds maximum token budget
        # Priority 7 shedding: Topic Catalog (retain only current topic metadata)
        if total_tokens > max_tokens and len(working_catalog) > 1:
            initial_cat_len = len(working_catalog)
            current_top_id = input_data.topic.topic_id
            working_catalog = [k for k in working_catalog if k.topic_id == current_top_id] or working_catalog[:1]
            trimmed_items["trimmed_catalog_items"] += initial_cat_len - len(working_catalog)
            trim_reasons.append("catalog_trimmed_by_global_budget")
            prompt, block_tokens, total_tokens = _render_and_measure(
                working_turns, working_known_info, working_slots, working_catalog, working_target_text, working_evidence_text
            )

        # Priority 6 shedding: Cross-topic known info items
        while total_tokens > max_tokens and working_known_info:
            working_known_info.pop()
            trimmed_items["trimmed_known_info_topics"] += 1
            if "known_info_trimmed_by_global_budget" not in trim_reasons:
                trim_reasons.append("known_info_trimmed_by_global_budget")
            prompt, block_tokens, total_tokens = _render_and_measure(
                working_turns, working_known_info, working_slots, working_catalog, working_target_text, working_evidence_text
            )

        # Priority 5 shedding: Historical conversation turns (sliding window down to latest Q&A pair)
        while total_tokens > max_tokens and len(working_turns) > 2:
            working_turns.pop(0)
            trimmed_items["trimmed_turns"] += 1
            if "recent_turns_trimmed_by_global_budget" not in trim_reasons:
                trim_reasons.append("recent_turns_trimmed_by_global_budget")
            prompt, block_tokens, total_tokens = _render_and_measure(
                working_turns, working_known_info, working_slots, working_catalog, working_target_text, working_evidence_text
            )

        # Priority 4 shedding: Optional/non-essential current topic slots
        if total_tokens > max_tokens and len(working_slots) > len(essential_slots):
            trimmed_items["trimmed_slots"] += len(working_slots) - len(essential_slots)
            working_slots = list(essential_slots)
            trim_reasons.append("current_slots_trimmed_by_global_budget")
            prompt, block_tokens, total_tokens = _render_and_measure(
                working_turns, working_known_info, working_slots, working_catalog, working_target_text, working_evidence_text
            )

        # Final verification: check if essential blocks (P1-P3 and required P4) satisfy budget constraints
        is_exceeded = (
            total_tokens > max_tokens
            or block_tokens["target_and_evidence"] > self.config.max_target_evidence_tokens
            or block_tokens["current_topic_slots"] > self.config.max_current_slots_tokens
        )
        if is_exceeded and "essential_context_exceeds_budget" not in trim_reasons:
            trim_reasons.append("essential_context_exceeds_budget")

        # Compute block token ratios
        block_token_ratios = {
            k: round(v / max(1, total_tokens), 4) for k, v in block_tokens.items()
        }

        # Create trimmed clone of QuestionGenerationInput
        trimmed_input = input_data.model_copy(
            update={
                "target_context": working_tc,
                "recent_turns": working_turns,
                "project_known_info": working_known_info,
                "topic_slots": working_slots,
                "topic_catalog": working_catalog,
            }
        )

        return BudgetResult(
            trimmed_input=trimmed_input,
            final_prompt=prompt,
            is_exceeded=is_exceeded,
            total_tokens=total_tokens,
            block_tokens=block_tokens,
            block_token_ratios=block_token_ratios,
            trimmed_items=trimmed_items,
            trim_reasons=trim_reasons,
            estimator="cjk_ascii_conservative_v1",
        )
