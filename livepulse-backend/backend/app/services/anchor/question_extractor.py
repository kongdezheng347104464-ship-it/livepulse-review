"""question_extractor — top viewer questions for the B4 Anchor Dashboard.

Pipeline:
  1. QUESTION_FILTER_RE  — keep only danmakus that look like questions
  2. normalize_question   — strip filler words + punctuation → canonical form
  3. _merge_substrings    — collapse "这款多少钱" into "多少钱"
  4. aggregate + rank     — (session_count DESC, session_ratio DESC, message_count DESC)

Design constraints (from product review 2026-06-01):
  - M1: rule-based only, no embedding / fuzzy matching
  - Normalize before grouping, not after, to avoid "多少钱" ≠ "多少钱？" split
  - Substring merge: shorter canonical wins; avoids "主播这款粉底多少钱" hiding "多少钱"
  - Empty strings after normalization are silently dropped
"""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Sequence

# ---------------------------------------------------------------------------
# Step 1 — question filter
# ---------------------------------------------------------------------------

# A danmaku is a candidate question if it matches ANY of these patterns.
# Intentionally broad: false positives get normalized away or ranked low.
QUESTION_FILTER_RE = re.compile(
    r"[？?]"
    r"|怎么|怎样|怎办|如何"
    r"|什么|啥"
    r"|哪[里个款种]?"
    r"|多少"
    r"|几[号个件天分]?"
    r"|为什么|为啥"
    r"|能不能|可以吗|行不行"
    r"|有没有|有没"
    r"|是不是|是否"
    r"|好不好|好用吗"
)


def is_question(text: str) -> bool:
    return bool(QUESTION_FILTER_RE.search(text))


# ---------------------------------------------------------------------------
# Step 2 — normalize
# ---------------------------------------------------------------------------

# Address / filler prefixes that carry no question meaning.
# Run twice to handle chains like "请问主播姐姐…".
_FILLER_PREFIX_RE = re.compile(
    r"^@[^\s，,、！!？?]*\s*"          # @mentions
    r"|^(主播|姐姐?|老板|亲|小仙女|美女|老师|博主|宝子)[，,、！!]?\s*"
    r"|^(请问|请教一下?|想问[一下]?|问[一下]?|麻烦问[一下]?)\s*"
)

# Punctuation that adds no semantic content.
_PUNCT_RE = re.compile(
    r"[，。！？!?,@＠.…、；;:：【】「」『』""''~～·•＋+\-－—_＿]+"
)


def normalize_question(raw: str) -> str | None:
    """Return a canonical question string, or None if nothing survives."""
    if not raw:
        return None
    text = raw.strip()
    # NFC normalisation keeps e.g. half-width ＿ consistent
    text = unicodedata.normalize("NFC", text)
    # Strip filler prefixes up to 3 times to handle stacked prefixes
    for _ in range(3):
        stripped = _FILLER_PREFIX_RE.sub("", text).strip()
        if stripped == text:
            break
        text = stripped
    # Remove punctuation throughout (including question marks — they are
    # already captured by is_question; keeping them would split "多少钱"
    # from "多少钱？").
    text = _PUNCT_RE.sub("", text)
    # Collapse whitespace
    text = " ".join(text.split())
    # Minimum two characters — single-char leftovers are noise
    if len(text) < 2:
        return None
    return text


# ---------------------------------------------------------------------------
# Step 3 — substring merge
# ---------------------------------------------------------------------------

def _merge_substrings(counts: dict[str, dict]) -> dict[str, dict]:
    """Merge longer form into shorter when shorter is a substring.

    "这款粉底多少钱" → absorbed by "多少钱" (if "多少钱" exists in counts).

    Only merges when shorter text appears as a contiguous substring of the
    longer text.  Does NOT do fuzzy matching.
    """
    # Sort by length ascending so shorter forms are processed first.
    keys_asc = sorted(counts.keys(), key=len)
    merged_into: dict[str, str] = {}  # long_key → short_key it was merged into

    for i, long_key in enumerate(keys_asc):
        if long_key in merged_into:
            continue
        for short_key in keys_asc[:i]:
            if short_key in merged_into:
                continue
            if short_key in long_key:
                # Merge long_key's counts into short_key
                counts[short_key]["message_count"] += counts[long_key]["message_count"]
                # Session union: a session that asked both forms counts only once
                counts[short_key]["session_ids"] |= counts[long_key]["session_ids"]
                merged_into[long_key] = short_key
                break

    return {k: v for k, v in counts.items() if k not in merged_into}


# ---------------------------------------------------------------------------
# Step 4 — public entry point
# ---------------------------------------------------------------------------

@dataclass
class QuestionItem:
    question: str
    session_count: int
    message_count: int
    session_ratio: float  # session_count / total_sessions, used for tiebreaking


def extract_top_questions(
    danmaku_rows: Sequence[tuple[str, str]],  # (session_id, content)
    total_sessions: int,
    limit: int = 10,
) -> list[dict]:
    """Rank viewer questions by recurrence across sessions.

    Args:
        danmaku_rows: iterable of (session_id, content) for speech danmakus
        total_sessions: total analyzed sessions for this anchor (for ratio)
        limit: max items to return

    Returns:
        list of dicts with keys: question, session_count, message_count
        sorted by session_count DESC, session_ratio DESC, message_count DESC
    """
    # {normalized_text: {"message_count": int, "session_ids": set}}
    counts: dict[str, dict] = defaultdict(lambda: {"message_count": 0, "session_ids": set()})

    for session_id, content in danmaku_rows:
        if not is_question(content):
            continue
        norm = normalize_question(content)
        if norm is None:
            continue
        counts[norm]["message_count"] += 1
        counts[norm]["session_ids"].add(session_id)

    if not counts:
        return []

    # Substring merge to collapse verbose variants into shorter canonical forms
    counts = _merge_substrings(dict(counts))

    denom = max(total_sessions, 1)
    items: list[QuestionItem] = [
        QuestionItem(
            question=text,
            session_count=len(v["session_ids"]),
            message_count=v["message_count"],
            session_ratio=len(v["session_ids"]) / denom,
        )
        for text, v in counts.items()
    ]

    items.sort(key=lambda x: (-x.session_count, -x.session_ratio, -x.message_count))

    return [
        {
            "question": item.question,
            "session_count": item.session_count,
            "message_count": item.message_count,
        }
        for item in items[:limit]
    ]
