"""Script type classifier with weighted keyword voting.

Returns top-2 categories for multi-label classification.
Live streaming speech often spans multiple categories:
  "教学 + 产品介绍"
  "促销 + 互动"
  "教学 + 逼单"

Single classification loses information.
"""
from collections import defaultdict
from ..utils.config_loader import load_json_config


def classify_script(text: str) -> list[str]:
    """Classify a text segment into top-2 script categories.

    Uses weighted keyword voting with hard-rule priority for teaching.

    Args:
        text: The transcribed speech text.

    Returns:
        List of 1-2 category names, ordered by confidence.
        Returns ["chitchat"] if no keywords match.
    """
    text_lower = text.lower()

    # ---- Hard rule: strong teaching signals ----#
    teaching_config = load_json_config("teaching.json")
    strong_signals = teaching_config.get("_strong_signals", [])
    if any(sig in text_lower for sig in strong_signals):
        return ["teaching"]

    # ---- Weighted voting ----#
    scores = defaultdict(float)

    # Teaching
    for kw, weight in teaching_config.get("keywords", {}).items():
        if kw in text_lower:
            scores["teaching"] += weight

    # Product intro
    product_config = load_json_config("product_intro.json")
    for kw, weight in product_config.get("keywords", {}).items():
        if kw in text_lower:
            scores["product_intro"] += weight

    # Promotion
    promo_config = load_json_config("promotion.json")
    for kw, weight in promo_config.get("keywords", {}).items():
        if kw in text_lower:
            scores["promotion"] += weight

    # Interaction
    inter_config = load_json_config("interaction.json")
    for kw, weight in inter_config.get("keywords", {}).items():
        if kw in text_lower:
            scores["interaction"] += weight

    if not scores:
        return ["chitchat"]

    # Top-2
    sorted_cats = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [cat for cat, _ in sorted_cats[:2]]
