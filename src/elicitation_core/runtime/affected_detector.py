import json
from pathlib import Path
from typing import Optional
from ..llm.client import LLMClient
from ..models.state import TopicState


class AffectedTopicDetector:
    """Detects which topics are affected by the latest interviewee response."""

    def __init__(self, llm_client: LLMClient, prompts_dir: Path | str = "prompts"):
        self.llm_client = llm_client
        self.prompts_dir = Path(prompts_dir)

    def _load_template(self) -> str:
        prompt_path = self.prompts_dir / "affected_topic_detection.txt"
        if not prompt_path.exists():
            raise FileNotFoundError(f"Prompt template not found at {prompt_path}")
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()

    async def detect(
        self,
        current_topic: TopicState,
        conversation_record: list[dict],
        all_topics: list[TopicState],
    ) -> list[str]:
        valid_topic_numbers = {t.topic_number for t in all_topics}
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
            raw_result = await self.llm_client.complete_json(prompt=prompt)
            affected: list[str] = []
            if isinstance(raw_result, list):
                for item in raw_result:
                    t_str = str(item).strip()
                    if t_str in valid_topic_numbers and t_str not in affected:
                        affected.append(t_str)
        except Exception:
            affected = []

        if current_topic.topic_number not in affected:
            affected.insert(0, current_topic.topic_number)

        return affected
