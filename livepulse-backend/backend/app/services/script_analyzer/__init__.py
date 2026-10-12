"""Script analyzer package.

Core analysis modules:
  classifiers/  - category, opening, engagement, closing detection
  extractors/   - keywords, pain points
  scoring/      - segment quality scoring
  config/       - JSON keyword dictionaries (editable without code changes)
  utils/        - config loader, text utilities
"""
from .analyzer import analyze_script_segments, calculate_speech_rate
from .classifiers.script_classifier import classify_script
from .classifiers.opening_detector import detect_opening
from .classifiers.engagement_detector import detect_engagement_prompts
from .classifiers.closing_detector import detect_closing_tactics
from .extractors.keyword_extractor import extract_keywords
from .extractors.pain_point_extractor import extract_pain_points
from .scoring.segment_scorer import score_segment

__all__ = [
    "analyze_script_segments",
    "classify_script",
    "calculate_speech_rate",
    "detect_opening",
    "detect_engagement_prompts",
    "detect_closing_tactics",
    "extract_keywords",
    "extract_pain_points",
    "score_segment",
]
