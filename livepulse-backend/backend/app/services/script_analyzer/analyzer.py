"""Script analysis orchestrator.

Coordinates classifiers, extractors, and scoring modules.
The main entry point: analyze_script_segments(segments).
"""
import logging
import re

from .classifiers.script_classifier import classify_script
from .classifiers.opening_detector import detect_opening
from .classifiers.engagement_detector import detect_engagement_prompts
from .classifiers.closing_detector import detect_closing_tactics
from .extractors.keyword_extractor import extract_keywords
from .extractors.pain_point_extractor import extract_pain_points
from .scoring.segment_scorer import score_segment

logger = logging.getLogger(__name__)

# Module-level cache: loaded once, reused across all segments
_product_config = None


def _get_product_config():
    global _product_config
    if _product_config is None:
        from .utils.config_loader import load_json_config
        _product_config = load_json_config("product_intro.json")
    return _product_config


def calculate_speech_rate(text: str, duration_sec: float) -> float:
    """Calculate characters-per-minute speech rate."""
    if duration_sec <= 0:
        logger.warning("speech_rate received zero or negative duration: %.2f", duration_sec)
        return 0.0
    chars = len(re.sub(r"\s+", "", text))
    return round(chars / duration_sec * 60, 1)


def get_primary_category(categories: list[str]) -> str:
    """Extract primary category from multi-label output.

    For backward compatibility with the pipeline's existing
    script_type field (single value).
    """
    if not categories:
        return "chitchat"
    return categories[0]


def analyze_script_segments(segments: list[dict]) -> list[dict]:
    """Analyze transcribed speech segments.

    For each segment, runs:
    - Category classification (top-2)
    - Opening detection
    - Engagement prompt detection
    - Closing tactic detection
    - Pain point extraction
    - Keyword extraction
    - Quality scoring

    Args:
        segments: List of {"start": float, "end": float, "text": str}.

    Returns:
        List with added fields: script_type, categories, speech_rate,
        keywords, product_mentions, score, analysis_tags.
    """
    results = []
    for idx, seg in enumerate(segments):
        text = seg["text"]
        start = seg["start"]
        end = seg["end"]
        duration = end - start

        # Classification (top-2)
        categories = classify_script(text)
        primary = get_primary_category(categories)

        # Speech rate
        speech_rate = calculate_speech_rate(text, duration)

        # Feature detectors
        is_opening = detect_opening(text, start, idx)
        has_engagement, engagement_types = detect_engagement_prompts(text)
        has_closing, closing_types = detect_closing_tactics(text)
        pain_points, pain_categories = extract_pain_points(text)

        # Extractors
        keywords = extract_keywords(text)

        # Product mentions (cached config — loaded once)
        product_config = _get_product_config()
        product_mentions = [
            kw for kw in product_config.get("keywords", {})
            if kw in text.lower()
        ]

        # Scoring
        score = score_segment(text, categories, speech_rate, engagement_types, closing_types)

        results.append({
            "start_sec": start,
            "end_sec": end,
            "text": text,
            "script_type": primary,
            "categories": categories,
            "speech_rate": speech_rate,
            "keywords": keywords,
            "product_mentions": product_mentions,
            "score": score,
            "analysis_tags": {
                "is_opening": is_opening,
                "has_engagement_prompt": has_engagement,
                "engagement_prompt_types": engagement_types,
                "has_closing_tactic": has_closing,
                "closing_tactic_types": closing_types,
                "pain_points": pain_points,
                "pain_point_categories": pain_categories,
            },
        })
    return results
