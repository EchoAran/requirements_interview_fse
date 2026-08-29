import json
from pathlib import Path
from typing import Optional
from ..llm.client import LLMClient
from ..models.state import TopicState
from ..models.updates import OperationConfidence, OperationSelectionResult


class OperationSelector:
    """Classifies interviewee intent into one of 7 operations with confidence scores."""

    def __init__(self, llm_client: LLMClient, prompts_dir: Path | str = "prompts"):
        self.llm_client = llm_client
        self.prompts_dir = Path(prompts_dir)

    def _load_template(self) -> str:
        prompt_path = self.prompts_dir / "operation_selection.txt"
        if not prompt_path.exists():
            raise FileNotFoundError(f"Prompt template not found at {prompt_path}")
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()

    async def select_operation(
        self,
        current_topic: TopicState,
        conversation_record: list[dict],
        all_topics: list[TopicState],
    ) -> OperationSelectionResult:
        topics_list = [
            {"topic_number": t.topic_number, "topic_content": t.topic_content}
            for t in all_topics
        ]

        template = self._load_template()
        prompt = (
            template
            .replace("{current_topic_content}", str(current_topic.topic_content))
            .replace("{current_topic_conversation_record}", json.dumps(conversation_record, ensure_ascii=False))
            .replace("{topics_list}", json.dumps(topics_list, ensure_ascii=False))
        )

        try:
            raw_data = await self.llm_client.complete_json(prompt=prompt)
            if isinstance(raw_data, dict):
                best = str(raw_data.get("best_operation", "maintain_current_topic")).strip()
                scores_raw = raw_data.get("confidence_scores", [])
                conf_scores: list[OperationConfidence] = []
                for s_item in scores_raw:
                    if isinstance(s_item, dict):
                        op = str(s_item.get("operation", "")).strip()
                        score = float(s_item.get("score", 0.0))
                        if op:
                            conf_scores.append(OperationConfidence(operation=op, score=score))
                if best and conf_scores:
                    return OperationSelectionResult(best_operation=best, confidence_scores=conf_scores)
        except Exception:
            pass

        return OperationSelectionResult(
            best_operation="maintain_current_topic",
            confidence_scores=[OperationConfidence(operation="maintain_current_topic", score=1.0)],
        )
