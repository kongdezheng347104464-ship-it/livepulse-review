"""Engagement prompt detector - identifies audience interaction prompts."""
from ..utils.config_loader import load_json_config


def detect_engagement_prompts(text: str) -> tuple[bool, list[str]]:
    """Detect engagement prompts in a segment.

    Finds: like prompts, follow prompts, comment prompts, QA prompts.

    Args:
        text: Segment text.

    Returns:
        (has_prompt, list_of_prompt_types) e.g. (True, ["like", "comment"])
    """
    text_lower = text.lower()
    config = load_json_config("engagement.json")
    found = []

    for prompt_type, patterns in config.items():
        if prompt_type.startswith("_"):
            continue
        if any(p in text_lower for p in patterns):
            found.append(prompt_type)

    return len(found) > 0, found
