from pathlib import Path
from typing import Optional
import json
import logging
from ..config import ContextBudgetConfig
from ..llm.client import LLMClient
from ..llm.exceptions import LLMConfigurationError, LLMOutputError, LLMTransportError
from ..models.interpretation import TopicDigest
from ..models.run_record import RunError
from ..models.state import ProjectState, TopicState
from ..models.strategy import (
    KnownInfoDigest,
    QuestionGenerationInput,
    QuestionPlan,
    SlotDigest,
    StrategyCode,
    TargetContext,
    TopicCatalogItem,
)
from ..services.context_budget_manager import ContextBudgetManager
from ..services.id_factory import IdFactory
from .strategy_selector import STRATEGY_INSTRUCTIONS, StrategySelector

logger = logging.getLogger(__name__)


class QuestionGenerator:
    """Generates interviewer remarks / questions based on QuestionPlan, Topic state, and conversation history."""

    def __init__(
        self,
        llm_client: LLMClient,
        strategy_selector: StrategySelector,
        prompts_dir: Path | str = "prompts",
        context_budget: Optional[ContextBudgetConfig] = None,
    ):
        self.llm_client = llm_client
        self.strategy_selector = strategy_selector
        self.prompts_dir = Path(prompts_dir)
        self.budget_manager = ContextBudgetManager(context_budget)

    def _load_template(self) -> str:
        prompt_path = self.prompts_dir / "remarks_generation.txt"
        if not prompt_path.exists():
            raise FileNotFoundError(f"Prompt template 'remarks_generation.txt' not found in {self.prompts_dir}")
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()


    def _build_entire_interview_info_slots_from_known_info(
        self,
        project_known_info: list[KnownInfoDigest],
    ) -> dict[str, dict]:
        entire_info: dict[str, dict] = {}
        for item in project_known_info:
            topic_key = item.topic_content
            slots_repr = [
                {
                    "item_name": s.key,
                    "recorded_value": s.value if s.value is not None else "（未提供）",
                }
                for s in item.filled_slots
            ]
            entire_info[topic_key] = {"items": slots_repr}
        return entire_info

    def _format_target_and_evidence(self, input_data: QuestionGenerationInput) -> tuple[str, str]:
        """Formats target context and supporting evidence into separate clear, ID-free semantic instructions for the LLM."""
        plan = input_data.plan
        strat = plan.strategy
        tc = input_data.target_context

        target_desc = "推动访谈顺利开展。"
        evidence_desc = "（无指定证据）"

        if strat == "fill_gap":
            if tc and tc.target_slots:
                target_slot = tc.target_slots[0]
                target_desc = f"针对缺失信息项【{target_slot.semantic_key}】进行自然询问。"
                if target_slot.evidence_snippets:
                    evidence_desc = "\n".join(f'- "{snip.content}"' for snip in target_slot.evidence_snippets)
            else:
                target_desc = "针对当前主题的关键缺失信息进行补充提问。"

        elif strat == "deepen":
            if tc and tc.target_slots:
                target_slot = tc.target_slots[0]
                val_repr = f"（当前已知内容: {target_slot.current_value}）" if target_slot.current_value else ""
                target_desc = f"针对已知信息【{target_slot.semantic_key}】{val_repr}进一步深入挖掘具体使用场景、细化要求或边界。"
                if target_slot.evidence_snippets:
                    evidence_desc = "\n".join(f'- "{snip.content}"' for snip in target_slot.evidence_snippets)
            else:
                target_desc = "针对当前主题已有的信息进一步深挖细节与实际场景。"

        elif strat == "resolve_conflict":
            if tc and tc.conflict_claims:
                lines = ["针对存在矛盾冲突的说法进行中立呈现与澄清，请受访者确认哪种为实际规则。"]
                ev_lines = []
                for key, claims in tc.conflict_claims.items():
                    lines.append(f"关于【{key}】存在不同记录：")
                    for idx, claim in enumerate(claims, 1):
                        lines.append(f"  说法{idx}: {claim.value}")
                        for snip in claim.evidence_snippets:
                            ev_lines.append(f'- 说法{idx}支持证据: "{snip.content}"')
                target_desc = "\n".join(lines)
                if ev_lines:
                    evidence_desc = "\n".join(ev_lines)
            else:
                target_desc = "针对当前主题中记录的矛盾表述，客观中立地向受访者请求澄清实际规则。"

        elif strat == "verify":
            if tc and tc.verify_facts:
                lines = ["对当前主题中已梳理且有证据支持的核心事实进行总结确认："]
                for f in tc.verify_facts:
                    lines.append(f"  - {f['key']}: {f['value']}")
                target_desc = "\n".join(lines)
                evidence_desc = "（上述事实均已在前序轮次中获取有效证据支持）"
            else:
                target_desc = "对当前主题的核心需求进行简要总结，并询问受访者是否还有遗漏或补充。"

        elif strat == "confirm_control":
            intent_desc = plan.control_intent or "调整访谈流程"
            target_topic_title = None
            if tc and tc.target_topics:
                target_topic_title = tc.target_topics[0].topic_content
            if target_topic_title:
                target_desc = f"检测到用户疑似希望切换到【{target_topic_title}】，请向受访者进行二选一礼貌确认（切换到该主题或继续当前主题），不要引入新业务提问。"
            else:
                target_desc = f"检测到用户疑似希望【{intent_desc}】，请向受访者进行二选一礼貌确认，不要引入新业务提问。"

        elif strat == "explore":
            target_desc = "围绕当前主题的核心场景、主要痛点和业务诉求进行开放式探索。"

        # If relations are present, append to target_desc
        if tc and tc.target_relations:
            rel_lines = ["[关联依赖目标]: 请针对主题间的依赖关联进行确认与讨论："]
            for rel in tc.target_relations:
                desc = rel.get("description")
                if not desc:
                    desc = f"【{rel.get('target', '')}】依赖于【{rel.get('source', '')}】"
                rel_lines.append(f"  - {desc}")
            if target_desc == "推动访谈顺利开展。":
                target_desc = "\n".join(rel_lines)
            else:
                target_desc = target_desc + "\n" + "\n".join(rel_lines)

        return target_desc, evidence_desc

    def _build_prompt(
        self,
        input_data: QuestionGenerationInput,
    ) -> str:
        """Builds prompt strictly from QuestionGenerationInput, without bypassing through state and without technical IDs."""
        plan = input_data.plan
        strat = plan.strategy
        inst = STRATEGY_INSTRUCTIONS.get(strat, STRATEGY_INSTRUCTIONS["fill_gap"])

        target_text, evidence_text = self._format_target_and_evidence(input_data)

        # ID-free current slots data formatted with business language
        current_slots_data = [
            {
                "item_name": s.key,
                "recorded_value": s.value if s.value is not None else "（未提供）",
                "is_required": "必需" if s.is_required else "选填",
                "status": "已确认" if s.state == "filled" else ("待澄清" if s.state == "conflict" else ("待细化" if s.state == "uncertain" else "未提供")),
            }
            for s in input_data.topic_slots
        ]
        if input_data.other_slots_omitted_count > 0:
            current_slots_data.append({
                "omitted_other_items_count": input_data.other_slots_omitted_count
            })

        # ID-free topic catalog list
        topics_list = [
            {"topic_title": k.topic_content}
            for k in input_data.topic_catalog
        ]

        entire_info = self._build_entire_interview_info_slots_from_known_info(
            input_data.project_known_info
        )
        template = self._load_template()

        convo_records = []
        for t in input_data.recent_turns:
            convo_records.append({
                "role": t.role,
                "content": t.message_content,
            })

        latest_user_answer = "（访谈初始轮次，暂无用户回答）"
        for t in reversed(input_data.recent_turns):
            if t.role == "Interviewee" and t.message_content:
                latest_user_answer = t.message_content
                break

        transition_text = ""
        if input_data.scheduler_transition and input_data.scheduler_transition.user_facing_reason:
            transition_text = input_data.scheduler_transition.user_facing_reason

        return (
            template
            .replace("{current_user_answer}", latest_user_answer)
            .replace("{current_topic_content}", str(input_data.topic.topic_content))
            .replace("{target}", target_text)
            .replace("{target_evidence}", evidence_text)
            .replace("{current_topic_info_slots}", json.dumps(current_slots_data, ensure_ascii=False))
            .replace("{topics_list}", json.dumps(topics_list, ensure_ascii=False))
            .replace("{entire_interview_info_slots}", json.dumps(entire_info, ensure_ascii=False))
            .replace("{transition}", transition_text)
            .replace("{current_topic_conversation_record}", json.dumps(convo_records, ensure_ascii=False))
            .replace("{strategy}", inst)
        )

    async def generate_from_input(
        self,
        input_data: QuestionGenerationInput,
        turn_id: Optional[str] = None,
    ) -> tuple[str, str]:
        """Main structured generation entrypoint consuming QuestionGenerationInput with context budgeting."""
        plan = input_data.plan

        effective_turn_id = turn_id
        if not effective_turn_id and input_data.recent_turns:
            effective_turn_id = getattr(input_data.recent_turns[-1], "turn_id", None)

        def _emit_error(error_type: str, message: str) -> None:
            if getattr(self.llm_client, "on_error", None):
                try:
                    run_err = RunError(
                        error_id=IdFactory.create_event_id(),
                        turn_id=effective_turn_id,
                        module="QuestionGenerator",
                        error_type=error_type,
                        message=message,
                        recoverable=True,
                    )
                    self.llm_client.on_error(run_err)
                except Exception as ex:
                    logger.warning("Failed to emit RunError: %s", ex)

        # 1. Prompt Construction and Context Budgeting
        try:
            target_text, evidence_text = self._format_target_and_evidence(input_data)
            inst = STRATEGY_INSTRUCTIONS.get(plan.strategy, STRATEGY_INSTRUCTIONS["fill_gap"])
            template = self._load_template()

            budget_res = self.budget_manager.apply_budget(
                input_data=input_data,
                prompt_template=template,
                strategy_instruction=inst,
                target_text=target_text,
                evidence_text=evidence_text,
            )
        except Exception as exc:
            err_msg = f"Prompt construction failed: {exc}"
            logger.error("prompt_build_error: %s", err_msg)
            _emit_error("prompt_build_error", err_msg)
            raise RuntimeError(err_msg) from exc

        # 2. Context Budget Check
        if budget_res.is_exceeded:
            err_msg = (
                f"Context budget exceeded: total estimated tokens {budget_res.total_tokens} > "
                f"max_prompt_tokens {self.budget_manager.config.max_prompt_tokens} "
                f"(trim reasons: {', '.join(budget_res.trim_reasons)})."
            )
            logger.error("question_context_budget_exceeded: %s", err_msg)
            _emit_error("question_context_budget_exceeded", err_msg)
            raise RuntimeError(err_msg)

        metadata = {
            "budget": {
                "total_tokens": budget_res.total_tokens,
                "block_tokens": budget_res.block_tokens,
                "block_token_ratios": budget_res.block_token_ratios,
                "trimmed_items": budget_res.trimmed_items,
                "trim_reasons": budget_res.trim_reasons,
                "estimator": budget_res.estimator,
            }
        }

        # 3. LLM Completion Call
        try:
            try:
                response = await self.llm_client.complete_text(
                    prompt=budget_res.final_prompt,
                    turn_id=effective_turn_id,
                    module="QuestionGenerator",
                    prompt_name="remarks_generation",
                    metadata=metadata,
                )
            except TypeError:
                response = await self.llm_client.complete_text(prompt=budget_res.final_prompt)
        except LLMOutputError as exc:
            err_msg = str(exc)
            logger.error("llm_output_error: %s", err_msg)
            # If not an LLMClient instance that already emitted on_error, emit it here
            if type(self.llm_client) is not LLMClient:
                _emit_error("llm_output_error", err_msg)
            raise
        except LLMConfigurationError as exc:
            err_msg = str(exc)
            logger.error("llm_configuration_error: %s", err_msg)
            if type(self.llm_client) is not LLMClient:
                _emit_error("llm_configuration_error", err_msg)
            raise
        except (LLMTransportError, ConnectionError, TimeoutError, OSError) as exc:
            err_msg = str(exc)
            logger.error("transport_error: %s", err_msg)
            if type(self.llm_client) is not LLMClient:
                _emit_error("transport_error", err_msg)
            raise
        except Exception as exc:
            err_msg = str(exc)
            logger.error("transport_error: %s", err_msg)
            if type(self.llm_client) is not LLMClient:
                _emit_error("transport_error", err_msg)
            raise

        # 4. Output Validation & Direct Return (No linguistic/regex filter on non-empty output)
        if not response or not response.strip():
            err_msg = "LLM returned empty or whitespace response."
            logger.warning("llm_output_error: %s", err_msg)
            _emit_error("llm_output_error", err_msg)
            raise LLMOutputError(err_msg)

        return response, plan.strategy

