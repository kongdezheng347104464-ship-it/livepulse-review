"""Segment scoring module.

Scores each speech segment based on:
- Category value (teaching/product_intro valued highest)
- Text length and information density
- Engagement prompts present
- Closing tactics present
- Speech rate quality
"""
import re
from decimal import Decimal

# Category base scores (reflects value to live stream performance)
CATEGORY_BASE_SCORES = {
    "teaching": 40,
    "product_intro": 35,
    "promotion": 30,
    "interaction": 25,
    "chitchat": 10,
}


def score_segment(
    text: str,
    categories: list[str],
    speech_rate: float,
    engagement_types: list[str] | None = None,
    closing_types: list[str] | None = None,
) -> Decimal:
    """Score a script segment.

    Args:
        text: Segment text content.
        categories: Top-2 classification results.
        speech_rate: Characters per minute.
        engagement_types: Types of engagement prompts found.
        closing_types: Types of closing tactics found.

    Returns:
        Score 0-95 as Decimal.
    """
    score = 0.0
    engagement_types = engagement_types or []
    closing_types = closing_types or []

    # Category base score
    primary_cat = categories[0] if categories else "chitchat"
    score += CATEGORY_BASE_SCORES.get(primary_cat, 10)

    # Bonus for multi-label (versatile segment)
    if len(categories) >= 2:
        score += 5

    # Text length / info density
    word_count = len(re.sub(r"\s+", "", text))
    if word_count >= 30:
        score += 10
    elif word_count >= 10:
        score += 5

    # Engagement bonus
    score += len(engagement_types) * 5

    # Closing tactic bonus
    score += len(closing_types) * 5

    # Speech rate quality
    if 120 <= speech_rate <= 220:
        score += 10
    elif 80 <= speech_rate <= 280:
        score += 5

    return Decimal(str(round(min(score, 95), 1)))
