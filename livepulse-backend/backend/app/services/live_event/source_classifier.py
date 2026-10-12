"""Classify a single OCR line from the right-side interaction list.

Why the interaction list is the right input layer:
  The video's floating-danmaku region overlay has no structure — it
  shows chat text alongside gift animations, badge icons, leaderboard
  toasts, and system messages all in the same visual stream. OCR
  there can't tell speech from gift from system. The right-side
  interaction list, on the other hand, is rendered by Douyin from a
  half-structured event protocol:

      <username>：<body>           ← speech (chat)
      <username>：送出了 <gift> ×N ← gift
      <username>：为主播点赞了      ← system (like)
      <username>：关注了主播        ← system (follow)
      <username>：进入直播间        ← system (enter)

  So the classifier only needs to know that "username + 「：」"
  separates identity from event, and recognize the small handful of
  protocol-fixed suffixes / prefixes for gift and system events.

Why empirical-first regex (not 4 separate PATTERN_* regexes):
  Cost of being wrong: a row classified as `unknown` lands in the
  unknown bucket and gets manually reviewed — no harm, just a TODO
  to extend rules. Cost of pre-designing 4 regexes for failure modes
  we haven't observed: maintenance burden + brittle matching.

  Decision (2026-05-28): start with one structural regex
  (username : content) plus content-side substring checks. Audit the
  `unknown` rate on the backfill run; only escalate to per-pattern
  regexes if `unknown` > 5%.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.models.source_type import SourceType


# Chinese full-width colon is what Douyin actually uses; the half-width
# ASCII colon would never appear in their UI. Compiled at module load.
# Non-greedy on the username side: usernames containing further colons
# (rare but possible — e.g. a nickname like "x:y") are matched to the
# FIRST colon, which is the conventional separator.
_INTERACTION_LINE = re.compile(r"^(?P<user>[^：]{1,80}?)：(?P<content>.+)$")

# Substrings whose presence implies the event is platform-generated
# rather than human speech. Order matters: GIFT_PREFIXES are checked
# first because "送出了" can't be a chat message body in any chat
# the protocol allows.
_GIFT_PREFIXES = (
    "送出了",   # e.g. "送出了 🎁 ×1" — the canonical gift event line
)

# Suffix-style markers: these phrases end a system-event line. Using
# endswith() avoids the false positive where a user types a question
# like "进入直播间需要什么" — that would start with "进入直播间" but
# never end with it, so endswith() rejects it correctly.
_SYSTEM_SUFFIXES = (
    "为主播点赞了",
    "关注了主播",
    "进入直播间",
)


@dataclass(frozen=True)
class ClassifiedEvent:
    """Parsed and classified single OCR line.

    Fields:
      username    — display name as captured. None when the regex
                    didn't find a "<user>：<content>" structure
                    (i.e. source_type is UNKNOWN).
      content     — text after the colon, stripped. For unknown
                    classifications this is the whole raw line.
      source_type — SPEECH / GIFT / SYSTEM / UNKNOWN, per
                    SourceType enum (DB-validated).
      raw_ocr_text — Verbatim original line, kept so a downstream
                    auditor can reconstruct what the OCR saw before
                    parsing decisions were applied.
    """

    username: str | None
    content: str
    source_type: SourceType
    raw_ocr_text: str


def classify_line(raw: str) -> ClassifiedEvent:
    """Single-line classifier — pure function, no I/O, no side effects.

    Always returns a ClassifiedEvent (never raises). Unparseable input
    lands in the UNKNOWN bucket with username=None, content=raw —
    callers can audit the unknown rate to decide if rules need to grow.

    Edge cases handled here (do not remove without replacement):
      * Empty / whitespace-only input → UNKNOWN with empty content.
      * Line missing the structural colon → UNKNOWN (don't guess).
      * Mixed-language content body → fine, we never inspect language.
      * Body that LOOKS like a system message but starts with the
        wrong word ("我刚进入直播间") → SPEECH; the endswith() check
        means substring-in-middle doesn't trigger SYSTEM.
    """
    if raw is None:
        return ClassifiedEvent(None, "", SourceType.UNKNOWN, "")

    stripped = raw.strip()
    if not stripped:
        return ClassifiedEvent(None, "", SourceType.UNKNOWN, raw)

    m = _INTERACTION_LINE.match(stripped)
    if m is None:
        # The line doesn't carry the username：content structure.
        # This is informative — it's almost always either OCR noise
        # (badge text, partial reads) or a floating-danmaku tail
        # that leaked into the cropped region. Keep raw text so the
        # auditor can decide.
        return ClassifiedEvent(None, stripped, SourceType.UNKNOWN, raw)

    user = m.group("user").strip() or None
    content = m.group("content").strip()

    if not content:
        # Structurally complete but body is empty — treat as unknown
        # because a chat / gift / system event always has SOME body.
        return ClassifiedEvent(user, "", SourceType.UNKNOWN, raw)

    # Gift detection (prefix-based — "送出了" is reserved by Douyin
    # for the post-colon body of gift events).
    for prefix in _GIFT_PREFIXES:
        if content.startswith(prefix):
            return ClassifiedEvent(user, content, SourceType.GIFT, raw)

    # System detection (suffix-based — the platform UI line ENDS
    # with these phrases; substring-match would over-fire on chat).
    for suffix in _SYSTEM_SUFFIXES:
        if content.endswith(suffix):
            return ClassifiedEvent(user, content, SourceType.SYSTEM, raw)

    # Everything else is human speech in the chat stream.
    return ClassifiedEvent(user, content, SourceType.SPEECH, raw)


def classify_lines(raw_lines: list[str]) -> list[ClassifiedEvent]:
    """Convenience batch wrapper around classify_line.

    Caller is expected to have already split OCR output into one
    interaction-list line per element. This function does not attempt
    to re-join wrapped lines (lines that overflow visually in the
    interaction list); that's the OCR layer's job.
    """
    return [classify_line(line) for line in raw_lines]
