from pathlib import Path
from typing import Optional
from ..llm.client import LLMClient
from ..llm.exceptions import LLMOutputError
from ..models.state import SectionState, TopicState, SlotState
from ..services.id_factory import IdFactory


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
        for s_idx, sec_data in enumerate(data, start=1):
            sec_num = str(sec_data.get("section_number", f"section-{s_idx}"))
            sec_content = str(sec_data.get("section_content", f"Section {s_idx}"))
            sec_id = IdFactory.create_section_id(sec_num)

            topics: list[TopicState] = []
            raw_topics = sec_data.get("topics", [])
            for t_idx, top_data in enumerate(raw_topics, start=1):
                top_num = str(top_data.get("topic_number", f"topic-{s_idx}-{t_idx}"))
                top_content = str(top_data.get("topic_content", f"Topic {t_idx}"))
                top_id = IdFactory.create_topic_id(top_num)

                slots: list[SlotState] = []
                raw_slots = top_data.get("slots", [])
                for sl_idx, slot_data in enumerate(raw_slots, start=1):
                    slot_num = str(slot_data.get("slot_number", f"slot-{s_idx}-{t_idx}-{sl_idx}"))
                    slot_key = str(slot_data.get("slot_key") or slot_data.get("key") or f"Slot {sl_idx}")
                    slot_id = IdFactory.create_slot_id(slot_num)

                    slots.append(
                        SlotState(
                            slot_id=slot_id,
                            topic_id=top_id,
                            slot_number=slot_num,
                            key=slot_key,
                            value=None,
                            origin="seed",
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
                        origin="seed",
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
        if not any(s.section_id == "section_emergent" or s.section_number == "section-emergent" for s in sections):
            sections.append(
                SectionState(
                    section_id="section_emergent",
                    section_number="section-emergent",
                    section_content="运行时新增关注点",
                    topics=[],
                )
            )

        return sections
