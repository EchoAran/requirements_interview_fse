import json
from pathlib import Path
from typing import Any, Optional
from ..llm.client import LLMClient
from ..models.event import EvidenceRef, StateEvent
from ..models.state import ProjectState, SlotState
from ..models.updates import SlotUpdateProposal
from ..services.event_factory import EventFactory


class ProjectPrefiller:
    """Prefills existing seed slots from initial requirements and generates initial Evidence and StateEvents."""

    def __init__(self, llm_client: LLMClient, prompts_dir: Path | str = "prompts"):
        self.llm_client = llm_client
        self.prompts_dir = Path(prompts_dir)

    def _load_prompt_template(self) -> str:
        prompt_path = self.prompts_dir / "initial_slots_filling.txt"
        if not prompt_path.exists():
            raise FileNotFoundError(f"Prompt template not found at {prompt_path}")
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()

    async def prefill(
        self,
        initial_requirements: str,
        state: ProjectState,
    ) -> tuple[list[EvidenceRef], list[StateEvent]]:
        req = initial_requirements.strip()
        if not req:
            return [], []

        all_topics = state.get_all_topics()
        if not all_topics:
            return [], []

        topics_list = [
            {"topic_number": t.topic_number, "topic_content": t.topic_content}
            for t in all_topics
        ]

        slots_by_topic: dict[str, dict[str, Any]] = {}
        slots_map: dict[str, dict[str, SlotState]] = {}

        for t in all_topics:
            topic_key = f"{t.topic_number}: {t.topic_content}"
            slots_data = [
                {
                    "slot_number": s.slot_number,
                    "slot_key": s.key,
                    "slot_value": s.value,
                    "is_necessary": s.is_required,
                }
                for s in t.slots
            ]
            slots_by_topic[topic_key] = {"slots": slots_data}
            slots_map[t.topic_number] = {s.slot_number: s for s in t.slots}

        from ..llm.template import render_prompt

        template = self._load_prompt_template()
        prompt = render_prompt(
            template,
            {
                "{initial_requirements}": req,
                "{topics_list}": json.dumps(topics_list, ensure_ascii=False),
                "{slots_by_topic}": json.dumps(slots_by_topic, ensure_ascii=False),
            }
        )

        raw_updates = await self.llm_client.complete_json(
            prompt=prompt,
            turn_id="turn_0000",
            module="ProjectPrefiller",
            prompt_name="initial_slots_filling",
        )
        if not isinstance(raw_updates, list):
            return [], []

        evidences: list[EvidenceRef] = []
        events: list[StateEvent] = []

        # Create one overarching initial requirement evidence ref
        init_ev = EventFactory.create_evidence(
            source_type="initial",
            content=req,
            turn_id=None,
            message_id=None,
        )
        evidences.append(init_ev)

        for item in raw_updates:
            if not isinstance(item, dict):
                continue
            t_num = str(item.get("topic_number", "")).strip()
            s_num = str(item.get("slot_number", "")).strip()
            s_val_raw = item.get("slot_value")

            if not t_num or not s_num or t_num not in slots_map:
                continue
            if s_num not in slots_map[t_num]:
                continue

            if s_val_raw in (None, "None", ""):
                val = None
            elif isinstance(s_val_raw, (list, dict)):
                val = json.dumps(s_val_raw, ensure_ascii=False)
            else:
                val = str(s_val_raw).strip()
                if val.lower() == "none":
                    val = None

            if val is not None:
                target_slot = slots_map[t_num][s_num]
                event, _ = EventFactory.create_slot_value_changed_event(
                    slot=target_slot,
                    new_value=val,
                    turn_id=None,
                    evidence_refs=[init_ev.evidence_id],
                    proposed_operation="add",
                )
                events.append(event)

        return evidences, events
