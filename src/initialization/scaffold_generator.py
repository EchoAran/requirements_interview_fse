from pathlib import Path
from typing import Optional
from llm.client import LLMClient
from llm.exceptions import LLMOutputError
from models.state import SectionState, TopicState, SlotState
from services.id_factory import IdFactory


class ScaffoldGenerator:
    """Generates the initial hierarchical Section -> Topic -> Slot scaffold from initial requirements."""

    def __init__(self, llm_client: LLMClient, prompts_dir: Path | str = "prompts"):
        self.llm_client = llm_client
        self.prompts_dir = Path(prompts_dir)

    def _load_prompt_template(self) -> str:
        prompt_path = self.prompts_dir / "framework_generation.txt"
        if not prompt_path.exists():
            raise FileNotFoundError(f"Prompt template not found at {prompt_path}")
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()

    @staticmethod
    def _require_text(raw: dict, key: str, label: str) -> str:
        """Reads a mandatory non-empty string field from the LLM scaffold payload."""
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip():
            raise LLMOutputError(f"Scaffold {label} must contain a non-empty '{key}' string")
        return value.strip()

    async def generate(
        self,
        initial_requirements: str,
    ) -> list[SectionState]:
        prompt = self._load_prompt_template()
        query = f"User's input: {initial_requirements}"
        data = await self.llm_client.complete_json(
            prompt=prompt,
            query=query,
            turn_id="turn_0000",
            module="ScaffoldGenerator",
            prompt_name="framework_generation",
        )
        if not isinstance(data, list):
            raise LLMOutputError("LLM response for framework generation must be a JSON array of sections.")

        sections: list[SectionState] = []
        seen_topic_numbers: set[str] = set()
        for sec_data in data:
            if not isinstance(sec_data, dict):
                raise LLMOutputError(f"Each scaffold section must be a JSON object, got {type(sec_data).__name__}")
            sec_num = self._require_text(sec_data, "section_number", "section")
            sec_content = self._require_text(sec_data, "section_content", f"section '{sec_num}'")
            sec_id = IdFactory.create_section_id(sec_num)

            topics: list[TopicState] = []
            raw_topics = sec_data.get("topics")
            if not isinstance(raw_topics, list):
                raise LLMOutputError(f"Scaffold section '{sec_num}' must contain a 'topics' array")
            for top_data in raw_topics:
                if not isinstance(top_data, dict):
                    raise LLMOutputError(f"Each topic in section '{sec_num}' must be a JSON object, got {type(top_data).__name__}")
                top_num = self._require_text(top_data, "topic_number", "topic")
                if top_num in seen_topic_numbers:
                    raise LLMOutputError(f"Scaffold contains duplicate topic_number '{top_num}'")
                seen_topic_numbers.add(top_num)
                top_content = self._require_text(top_data, "topic_content", f"topic '{top_num}'")
                top_id = IdFactory.create_topic_id(top_num)

                slots: list[SlotState] = []
                raw_slots = top_data.get("slots")
                if not isinstance(raw_slots, list):
                    raise LLMOutputError(f"Scaffold topic '{top_num}' must contain a 'slots' array")
                for slot_data in raw_slots:
                    if not isinstance(slot_data, dict):
                        raise LLMOutputError(f"Each slot in topic '{top_num}' must be a JSON object, got {type(slot_data).__name__}")
                    slot_num = self._require_text(slot_data, "slot_number", f"slot under topic '{top_num}'")
                    slot_key = self._require_text(slot_data, "slot_key", f"slot '{slot_num}'")
                    slot_id = IdFactory.create_slot_id(slot_num)

                    slots.append(
                        SlotState(
                            slot_id=slot_id,
                            topic_id=top_id,
                            slot_number=slot_num,
                            key=slot_key,
                            value=None,
                            origin="initial",
                            is_required=True,
                            state="empty",
                            evidence_refs=[],
                            revisions=[],
                        )
                    )

                topics.append(
                    TopicState(
                        topic_id=top_id,
                        topic_number=top_num,
                        topic_content=top_content,
                        topic_status="Pending",
                        origin="initial",
                        is_necessary=True,
                        section_id=sec_id,
                        slots=slots,
                        created_turn=0,
                        last_updated_turn=0,
                        evidence_refs=[],
                    )
                )

            sections.append(
                SectionState(
                    section_id=sec_id,
                    section_number=sec_num,
                    section_content=sec_content,
                    topics=topics,
                )
            )

        # Section 13: Automatically include section_emergent for runtime emergent topics
        if not any(s.section_id == "section_emergent" for s in sections):
            sections.append(
                SectionState(
                    section_id="section_emergent",
                    section_number="section-emergent",
                    section_content="Runtime Emergent Concerns",
                    topics=[],
                )
            )

        return sections
