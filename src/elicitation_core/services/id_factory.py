import uuid


class IdFactory:
    """Factory for generating predictable, clean, and unique entity identifiers."""

    @staticmethod
    def create_project_id(prefix: str = "proj") -> str:
        return f"{prefix}_{uuid.uuid4().hex[:8]}"

    @staticmethod
    def create_section_id(section_number: str) -> str:
        clean = section_number.replace("-", "_").lower()
        return f"sec_{clean}_{uuid.uuid4().hex[:6]}"

    @staticmethod
    def create_topic_id(topic_number: str) -> str:
        clean = topic_number.replace("-", "_").lower()
        return f"top_{clean}_{uuid.uuid4().hex[:6]}"

    @staticmethod
    def create_slot_id(slot_number: str) -> str:
        clean = slot_number.replace("-", "_").lower()
        return f"slt_{clean}_{uuid.uuid4().hex[:6]}"

    @staticmethod
    def create_turn_id(turn_index: int) -> str:
        return f"turn_{turn_index:04d}_{uuid.uuid4().hex[:6]}"

    @staticmethod
    def create_decision_id(turn_index: int | None = None) -> str:
        if turn_index is not None:
            return f"dec_{turn_index:04d}_{uuid.uuid4().hex[:6]}"
        return f"dec_{uuid.uuid4().hex[:8]}"

    @staticmethod
    def create_message_id(turn_index: int | None = None) -> str:
        if turn_index is not None:
            return f"msg_{turn_index:04d}_{uuid.uuid4().hex[:6]}"
        return f"msg_{uuid.uuid4().hex[:8]}"

    @staticmethod
    def create_event_id(index: int | None = None) -> str:
        if index is not None:
            return f"evt_{index:06d}_{uuid.uuid4().hex[:4]}"
        return f"evt_{uuid.uuid4().hex[:8]}"

    @staticmethod
    def create_evidence_id(index: int | None = None) -> str:
        if index is not None:
            return f"ev_{index:06d}_{uuid.uuid4().hex[:4]}"
        return f"ev_{uuid.uuid4().hex[:8]}"

    @staticmethod
    def create_revision_id(index: int | None = None) -> str:
        if index is not None:
            return f"rev_{index:04d}_{uuid.uuid4().hex[:4]}"
        return f"rev_{uuid.uuid4().hex[:8]}"
