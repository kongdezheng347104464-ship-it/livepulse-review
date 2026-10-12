"""Pain point extractor for beauty/makeup live streams."""
from ..utils.config_loader import load_json_config

# Lazy-loaded flat lookup
_pain_point_lookup: dict[str, str] | None = None


def _get_pain_point_lookup() -> dict[str, str]:
    """Build flat keyword→category lookup."""
    global _pain_point_lookup
    if _pain_point_lookup is not None:
        return _pain_point_lookup

    config = load_json_config("pain_points.json")
    lookup = {}
    for cat_name, cat_data in config.items():
        if cat_name.startswith("_"):
            continue
        for kw in cat_data.get("keywords", []):
            lookup[kw] = cat_name
    _pain_point_lookup = lookup
    return lookup


def extract_pain_points(text: str) -> tuple[list[str], list[str]]:
    """Extract pain point keywords from text.

    Args:
        text: Segment text.

    Returns:
        (matched_keywords, matched_categories)
    """
    text_lower = text.lower()
    lookup = _get_pain_point_lookup()
    matched = []
    categories = set()

    for kw, cat in lookup.items():
        if kw in text_lower:
            matched.append(kw)
            categories.add(cat)

    return matched, list(categories)
