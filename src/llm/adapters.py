"""Payload adapters for the model families served through an OpenAI-compatible gateway.

A gateway such as new-api rewrites the OpenAI ``messages`` payload into the target
provider's native protocol. Families like Gemini and Claude keep the system prompt in a
separate top-level field, so a payload holding only system messages is rewritten into an
empty conversation and rejected with HTTP 400. For those families a system-only payload
is promoted to a single user turn; OpenAI-style families (gpt, deepseek, qwen, ...) accept
it unchanged.
"""

SYSTEM_ROLE = "system"
USER_ROLE = "user"

# Model-name fragments of families that keep the system prompt outside the conversation
# and therefore require at least one user turn.
USER_TURN_REQUIRED_FAMILIES = ("claude", "gemini", "glm")


def normalize_messages(messages: list[dict[str, str]], model: str) -> list[dict[str, str]]:
    """Adapt an OpenAI-style message list to the payload shape the target family accepts."""
    if any(msg["role"] != SYSTEM_ROLE for msg in messages):
        return messages
    if not any(family in model.lower() for family in USER_TURN_REQUIRED_FAMILIES):
        return messages
    return [{"role": USER_ROLE, "content": msg["content"]} for msg in messages]