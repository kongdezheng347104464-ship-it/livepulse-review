"""Closing tactic detector - identifies conversion/promotion tactics."""
from ..utils.config_loader import load_json_config


def detect_closing_tactics(text: str) -> tuple[bool, list[str]]:
    """Detect closing/promotion tactics in a segment.

    Finds: time urgency, stock urgency, price comparison, guarantee.

    Args:
        text: Segment text.

    Returns:
        (has_tactic, list_of_tactic_types)
    """
    text_lower = text.lower()
    config = load_json_config("closing.json")
    found = []

    for tactic_type, patterns in config.items():
        if tactic_type.startswith("_"):
            continue
        if any(p in text_lower for p in patterns):
            found.append(tactic_type)

    return len(found) > 0, found
