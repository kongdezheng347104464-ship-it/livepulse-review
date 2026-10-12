"""SourceType — schema-level event provenance label.

Why this is an Enum (not a free string column):
  source_type is on the critical path of the Q1 conversion invariant
  ("conversion insights MUST only reference source_type='speech' events").
  If it were a free string, we'd accumulate "gift" / "Gift" / "gift_message"
  / "system_msg" drift, and the invariant would degrade silently into
  a partial filter. Making it a PostgreSQL ENUM forces every write site
  through the same vocabulary and rejects typos at the DB level.

Value catalog (intentionally small — extending is `ALTER TYPE ADD VALUE`
which is non-trivial in PG, so we only add when there's a real need):

  speech   — Human speech: ASR transcript OR display-region chat danmaku.
             The ONLY source_type allowed in Q1 conversion insights.
  gift     — Virtual gift / donation events from the interaction feed
             (prefix "送出了 ..." or known gift name patterns).
  system   — Platform-generated notices: "进入直播间", "关注了主播",
             "为主播点赞了", leaderboard / badge / promotional banners.
  unknown  — OCR row that the source_classifier regex didn't match.
             Acts as a dead-letter bucket — auditing the unknown rate is
             the primary signal that classifier rules need extending.
"""
from __future__ import annotations

import enum


class SourceType(str, enum.Enum):
    """Provenance tag for any row in `danmakus` (semantically: live_events).

    Inherits from `str` so:
      - `SourceType.SPEECH == "speech"` is True (works in plain comparisons)
      - JSON serialization is natural (`json.dumps(SourceType.SPEECH)` → `"speech"`)
      - SQLAlchemy ENUM column accepts the member directly.
    """

    SPEECH = "speech"
    GIFT = "gift"
    SYSTEM = "system"
    UNKNOWN = "unknown"


# Convenience: ordered list for migrations / enum type creation.
# Order matters for PG ENUM (defines sort order) — speech first because
# it's the most common and frequently filtered value.
SOURCE_TYPE_VALUES: tuple[str, ...] = tuple(s.value for s in SourceType)
