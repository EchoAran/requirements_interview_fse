import json
from pathlib import Path
from typing import Optional

from ..llm.client import LLMClient
from ..models.interpretation import (
    AffectedTopic,
    ConflictCandidate,
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
        for name in [
            "evidence_interpretation.txt",
            "affected_topic_detection.txt",
        ]:
            p = self.prompts_dir / name
            if p.exists():
                with open(p, "r", encoding="utf-8") as f:
                    return f.read()

        raise FileNotFoundError(f"Prompt template not found in {self.prompts_dir}")

    async def interpret(
        self,
        interpretation_input: EvidenceInterpretationInput,
    ) -> EvidenceInterpretation:
        catalog_dicts = [
            {
                "topic_id": t.topic_id,
                "topic_number": t.topic_number,
                "topic_content": t.topic_content,
                "section_id": t.section_id,
                "section_number": t.section_number,
                "slots": t.slots_keys,
                "status": t.status,
            }
            for t in interpretation_input.topic_catalog
        ]

        valid_topic_map = {t.topic_id: t.topic_id for t in interpretation_input.topic_catalog}
        for t in interpretation_input.topic_catalog:
            valid_topic_map[t.topic_number] = t.topic_id

        template = self._load_template()
        topics_list_simple = [
            {"topic_number": t.topic_number, "topic_content": t.topic_content}
            for t in interpretation_input.topic_catalog
        ]

        from ..llm.template import render_prompt

        prompt = render_prompt(
            template,
            {
                "{current_topic_id}": str(interpretation_input.current_topic_id),
                "{current_topic_content}": str(interpretation_input.current_topic_id),
                "{current_topic_conversation_record}": json.dumps([{
                    "interviewer": interpretation_input.latest_turn.interviewer_message,
                    "interviewee": interpretation_input.latest_turn.interviewee_message,
                }], ensure_ascii=False),
                "{topics_list}": json.dumps(topics_list_simple, ensure_ascii=False),
                "{interviewer_message}": str(interpretation_input.latest_turn.interviewer_message),
                "{interviewee_message}": str(interpretation_input.latest_turn.interviewee_message),
                "{user_turn_id}": str(interpretation_input.latest_turn.user_turn_id),
                "{topic_catalog_json}": json.dumps(catalog_dicts, ensure_ascii=False, indent=2),
            }
        )

        turn_id = getattr(interpretation_input.latest_turn, "user_turn_id", None)
        try:
            raw_data = await self.llm_client.complete_json(
                prompt=prompt,
                turn_id=turn_id,
                module="EvidenceInterpreter",
                prompt_name="evidence_interpretation",
            )
        except Exception:
            raw_data = {}

        if isinstance(raw_data, list):
            raw_data = {
                "affected_existing_topics": [
                    {"topic_id": str(t), "relevance": 1.0}
                    for t in raw_data
                ]
            }
        elif not isinstance(raw_data, dict):
            raw_data = {}

        # 1. Parse affected existing topics
        raw_affected = raw_data.get("affected_existing_topics", [])
        clean_affected: list[AffectedTopic] = []
        seen_affected_topics: set[str] = set()

        if isinstance(raw_affected, list):
            for item in raw_affected:
                if isinstance(item, str):
                    item = {"topic_id": item, "relevance": 1.0}
                if not isinstance(item, dict):
                    continue
                t_raw = str(item.get("topic_id", "")).strip()
                if not t_raw:
                    continue
                real_topic_id = valid_topic_map.get(t_raw)
                if real_topic_id and real_topic_id not in seen_affected_topics:
                    seen_affected_topics.add(real_topic_id)
                    rel = float(item.get("relevance", 1.0))
                    rel = max(0.0, min(1.0, rel))
                    ev_ids = item.get("evidence_message_ids", [])
                    if not isinstance(ev_ids, list) or not ev_ids:
                        ev_ids = [interpretation_input.latest_turn.user_turn_id]

                    clean_affected.append(
                        AffectedTopic(
                            topic_id=real_topic_id,
                            evidence_message_ids=[str(e) for e in ev_ids],
                            relevance=rel,
                        )
                    )

        # Fallback: ensure current_topic_id is included if no existing topics were parsed
        if not clean_affected:
            curr_id = valid_topic_map.get(interpretation_input.current_topic_id, interpretation_input.current_topic_id)
            clean_affected.append(
                AffectedTopic(
                    topic_id=curr_id,
                    evidence_message_ids=[interpretation_input.latest_turn.user_turn_id],
                    relevance=1.0,
                )
            )

        # 2. Parse emergent topic candidates
        raw_emergent = raw_data.get("emergent_topic_candidates", [])
        clean_emergent: list[EmergentTopicCandidate] = []

        if isinstance(raw_emergent, list):
            for item in raw_emergent:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or item.get("proposed_title") or "").strip()
                desc = str(item.get("description") or item.get("reason") or "").strip()
                if not title:
                    continue
                conf = float(item.get("confidence", 1.0))
                conf = max(0.0, min(1.0, conf))
                slots_raw = item.get("suggested_slots", [])
                slots = [str(s).strip() for s in slots_raw if str(s).strip()] if isinstance(slots_raw, list) else []
                ev_ids = item.get("evidence_message_ids", [])
                if not isinstance(ev_ids, list) or not ev_ids:
                    ev_ids = [interpretation_input.latest_turn.user_turn_id]

                clean_emergent.append(
                    EmergentTopicCandidate(
                        title=title,
                        description=desc or title,
                        suggested_section_id=item.get("suggested_section_id"),
                        suggested_slots=slots,
                        evidence_message_ids=[str(e) for e in ev_ids],
                        confidence=conf,
                    )
                )

        # 3. Parse conflicts and relations
        raw_conflicts = raw_data.get("explicit_conflicts", [])
        clean_conflicts: list[ConflictCandidate] = []
        if isinstance(raw_conflicts, list):
            for item in raw_conflicts:
                if isinstance(item, dict):
                    t_id = valid_topic_map.get(str(item.get("topic_id", "")))
                    if t_id:
                        clean_conflicts.append(
                            ConflictCandidate(
                                topic_id=t_id,
                                related_slot_ids=[str(s) for s in item.get("related_slot_ids", []) if isinstance(item.get("related_slot_ids"), list)],
                                evidence_message_ids=[str(e) for e in item.get("evidence_message_ids", [interpretation_input.latest_turn.user_turn_id])],
                                description=item.get("description"),
                            )
                        )

        raw_relations = raw_data.get("relation_candidates", [])
        clean_relations: list[RelationCandidate] = []
        if isinstance(raw_relations, list):
            for item in raw_relations:
                if isinstance(item, dict):
                    s_id = valid_topic_map.get(str(item.get("source_topic_id", "")))
                    tgt_id = valid_topic_map.get(str(item.get("target_topic_id", "")))
                    rel_type = item.get("relation_type", "depends_on")
                    if s_id and tgt_id and s_id != tgt_id and rel_type in ("depends_on", "related_to"):
                        clean_relations.append(
                            RelationCandidate(
                                source_topic_id=s_id,
                                target_topic_id=tgt_id,
                                relation_type=rel_type,
                                evidence_message_ids=[str(e) for e in item.get("evidence_message_ids", [interpretation_input.latest_turn.user_turn_id])],
                            )
                        )

        return EvidenceInterpretation(
            affected_existing_topics=clean_affected,
            emergent_topic_candidates=clean_emergent,
            explicit_conflicts=clean_conflicts,
            relation_candidates=clean_relations,
        )
