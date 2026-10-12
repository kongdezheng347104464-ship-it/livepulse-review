"""Opening speech detector."""
from ..utils.config_loader import load_json_config

# Minimum text length to count as a meaningful warm-up speech segment.
# Below this we treat as silence / noise (e.g., single-word ASR artifacts).
_MIN_WARMUP_CHARS = 6


def detect_opening(text: str, start_sec: float, _segment_idx: int = 0) -> bool:
    """Detect if a segment is an opening speech.

    Two-window detection:
      1. **Warm-up window** (0 to `warmup_max_sec`, default 60s) — any
         non-trivial speech counts as opening. Captures broadcasters who
         spend 30-60s on casual chitchat ("大家来了吗", "等等再开始啊")
         before formally introducing the session. The previous detector
         missed these because chitchat doesn't match opening keywords.
      2. **Formal window** (warmup_max_sec to `max_start_sec`, default
         60-180s) — must contain a greeting or intro keyword.

    Beyond max_start_sec, never counted as opening.
    """
    config = load_json_config("opening.json")
    warmup_max = config.get("warmup_max_sec", 60)
    max_start = config.get("max_start_sec", 180)

    if start_sec > max_start:
        return False

    if start_sec <= warmup_max:
        # Warm-up: any segment with meaningful content counts. Strip
        # whitespace for the length check so blank ASR doesn't qualify.
        return len(text.strip()) >= _MIN_WARMUP_CHARS

    # Formal window: must match a known opening keyword.
    text_lower = text.lower()
    all_words = config.get("greeting", []) + config.get("intro", [])
    return any(kw in text_lower for kw in all_words)
