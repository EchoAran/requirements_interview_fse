import json
from pathlib import Path
from typing import Optional

from llm.client import LLMClient
from llm.exceptions import LLMOutputError
from models.interpretation import (
    AffectedTopic,
    EmergentTopicCandidate,
    EvidenceInterpretation,
    EvidenceInterpretationInput,
    RelationCandidate,
)


class EvidenceInterpreter:
    """Interprets latest interviewee response to identify affected existing topics, emergent topic candidates, conflicts, and relations."""

    def __init__(self, llm_client: LLMClient, prompts_dir: Path | str = "prompts"):
        self.llm_client = llm_client
        self.prompts_dir = Path(prompts_dir)

    def _load_template(self) -> str:
        prompt_path = self.prompts_dir / "evidence_interpretation.txt"
        if not prompt_path.exists():
            raise FileNotFoundError(f"Prompt template not found at {prompt_path}")
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()

    @staticmethod
    def _resolve_topic_number(
        raw_value: object,
        number_to_id: dict[str, str],
        field_label: str,
    ) -> str:
        """Converts an LLM-emitted topic_number into the internal topic id, failing loudly on unknown references."""
        if not isinstance(raw_value, str) or not raw_value.strip():
            raise LLMOutputError(f"'{field_label}' entry must contain a non-empty 'topic_number' string, got {raw_value!r}")
        topic_number = raw_value.strip()
        topic_id = number_to_id.get(topic_number)
        if topic_id is None:
            raise LLMOutputError(f"'{field_label}' entry references unknown topic_number '{topic_number}'")
        return topic_id

    async def interpret(
        self,
        interpretation_input: EvidenceInterpretationInput,
    ) -> EvidenceInterpretation:
        catalog = interpretation_input.topic_catalog

        # LLM-facing identifiers are topic numbers; map each one to its internal topic id exactly once.
        number_to_id: dict[str, str] = {}
        for digest in catalog:
            if digest.topic_number in number_to_id:
                raise LLMOutputError(f"Topic catalog contains duplicate topic_number '{digest.topic_number}'")
            number_to_id[digest.topic_number] = digest.topic_id

        active_number = next(
            (d.topic_number for d in catalog if d.topic_id == interpretation_input.current_topic_id),
            None,
        )
        if active_number is None:
            raise LLMOutputError(
                f"Active topic '{interpretation_input.current_topic_id}' is absent from the topic catalog"
            )

        catalog_dicts = [
            {
                "topic_number": t.topic_number,
                "topic_content": t.topic_content,
                "slots": t.slots_keys,
                "status": t.status,
            }
            for t in catalog
        ]

        template = self._load_template()

        from llm.template import render_prompt

        prompt = render_prompt(
            template,
            {
                "{current_topic_number}": active_number,
                "{interviewer_message}": str(interpretation_input.latest_turn.interviewer_message),
                "{interviewee_message}": str(interpretation_input.latest_turn.interviewee_message),
                "{user_turn_id}": str(interpretation_input.latest_turn.user_turn_id),
                "{topic_catalog_json}": json.dumps(catalog_dicts, ensure_ascii=False, indent=2),
            }
        )

        turn_id = getattr(interpretation_input.latest_turn, "user_turn_id", None)
        raw_data = await self.llm_client.complete_json(
            prompt=prompt,
            turn_id=turn_id,
            module="EvidenceInterpreter",
            prompt_name="evidence_interpretation",
        )

        if not isinstance(raw_data, dict):
            raise LLMOutputError(f"EvidenceInterpreter expected a JSON object, got {type(raw_data).__name__}")

        # 1. Parse affected existing topics
        raw_affected = raw_data.get("affected_existing_topics")
        if not isinstance(raw_affected, list):
            raise LLMOutputError("EvidenceInterpreter field 'affected_existing_topics' must be a list")

        clean_affected: list[AffectedTopic] = []
        seen_affected_topics: set[str] = set()

        for item in raw_affected:
            if not isinstance(item, dict):
                raise LLMOutputError(f"Each item in 'affected_existing_topics' must be a dict, got {type(item).__name__}")
            topic_id = self._resolve_topic_number(item.get("topic_number"), number_to_id, "affected_existing_topics")
            relevance = item.get("relevance")
            if isinstance(relevance, bool) or not isinstance(relevance, (int, float)):
                raise LLMOutputError(
                    f"Affected topic '{item.get('topic_number')}' must contain a numeric 'relevance'"
                )
            if topic_id in seen_affected_topics:
                continue
            seen_affected_topics.add(topic_id)
            clean_affected.append(
                AffectedTopic(
                    topic_id=topic_id,
                    evidence_message_ids=[interpretation_input.latest_turn.user_turn_id],
                    relevance=float(relevance),
                )
            )

        # 2. Parse emergent topic candidates
        raw_emergent = raw_data.get("emergent_topic_candidates")
        if not isinstance(raw_emergent, list):
            raise LLMOutputError("EvidenceInterpreter field 'emergent_topic_candidates' must be a list")

        clean_emergent: list[EmergentTopicCandidate] = []
        for item in raw_emergent:
            if not isinstance(item, dict):
                raise LLMOutputError(f"Each item in 'emergent_topic_candidates' must be a dict, got {type(item).__name__}")
            title = item.get("title")
            if not isinstance(title, str) or not title.strip():
                raise LLMOutputError("Emergent topic candidate must contain a non-empty 'title' string")
            description = item.get("description")
            if not isinstance(description, str) or not description.strip():
                raise LLMOutputError(
                    f"Emergent topic candidate '{title}' must contain a non-empty 'description' string"
                )
            slots_raw = item.get("suggested_slots")
            if not isinstance(slots_raw, list):
                raise LLMOutputError(f"Emergent topic candidate '{title}' must contain a 'suggested_slots' array")
            confidence = item.get("confidence")
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                raise LLMOutputError(f"Emergent topic candidate '{title}' must contain a numeric 'confidence'")

            clean_emergent.append(
                EmergentTopicCandidate(
                    title=title.strip(),
                    description=description.strip(),
                    suggested_slots=[str(s).strip() for s in slots_raw if str(s).strip()],
                    evidence_message_ids=[interpretation_input.latest_turn.user_turn_id],
                    confidence=float(confidence),
                )
            )

        # 3. Parse depends_on topic relations
        raw_relations = raw_data.get("relation_candidates")
        if not isinstance(raw_relations, list):
            raise LLMOutputError("EvidenceInterpreter field 'relation_candidates' must be a list")

        clean_relations: list[RelationCandidate] = []
        for item in raw_relations:
            if not isinstance(item, dict):
                raise LLMOutputError(f"Each item in 'relation_candidates' must be a dict, got {type(item).__name__}")
            if item.get("relation_type") != "depends_on":
                raise LLMOutputError(
                    f"Relation candidate has invalid 'relation_type' {item.get('relation_type')!r}, expected 'depends_on'"
                )
            source_id = self._resolve_topic_number(item.get("source_topic_number"), number_to_id, "relation_candidates")
            target_id = self._resolve_topic_number(item.get("target_topic_number"), number_to_id, "relation_candidates")
            if source_id == target_id:
                raise LLMOutputError("Relation candidate must connect two distinct topics")
            clean_relations.append(
                RelationCandidate(
                    source_topic_id=source_id,
                    target_topic_id=target_id,
                    relation_type="depends_on",
                    evidence_message_ids=[interpretation_input.latest_turn.user_turn_id],
                )
            )

        return EvidenceInterpretation(
            affected_existing_topics=clean_affected,
            emergent_topic_candidates=clean_emergent,
            relation_candidates=clean_relations,
        )
