import re
from typing import Mapping


def render_prompt(template: str, substitutions: Mapping[str, str]) -> str:
    """Safely renders a prompt template using single-pass regex replacement.
    
    This guarantees that placeholder values containing characters matching other placeholders
    (such as `{topics_list}` or `{current_topic_content}`) are not recursively replaced,
    avoiding data corruption and prompt injection via secondary substitution.
    """
    if not substitutions:
        return template
    
    # Sort keys by length in descending order to match longest placeholders first
    sorted_keys = sorted(substitutions.keys(), key=len, reverse=True)
    pattern = re.compile("|".join(re.escape(k) for k in sorted_keys))
    return pattern.sub(lambda m: str(substitutions.get(m.group(0), m.group(0))), template)
