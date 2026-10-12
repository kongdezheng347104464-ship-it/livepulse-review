"""Rule-based username normalization for OCR-captured display names.

Why this exists (2026-05-28):
  After the full-frame OCR rebuild, source_classifier successfully
  extracts username:content pairs from the interaction list. BUT the
  OCR engine occasionally clusters multiple visual regions into one
  "line", which prepends UI chunks onto the actual username:

    "◆2 a*****"                    ← diamond badge + name
    "讲解中滋润双唇 ◆1 (主播) 唐***"   ← live UI state + 主播 tag + name
    "全部商品 X*****"               ← tab label + name
    "￥69.9 抢 ◆4 *****"            ← product price chunk + name

  Left raw, these turn into separate values for COUNT(DISTINCT
  username) — every chat from "唐***" gets attributed to a different
  ghost identity, and Engaged Audience Rate (= unique_speakers /
  viewer_count) blows up.

What this MODULE IS:
  Deterministic rule-based cleanup of those visual prefixes/suffixes.
  Outputs a normalized form that's stable enough for cardinality
  comparison, NOT a recovery of the user's true platform identity.

What this MODULE IS NOT:
  - Fuzzy matching ("小美" ≈ "大美"). That's identity-layer territory
    and is forbidden here — silent merges across distinct users are
    far more dangerous than missing dedup opportunities.
  - Embedding similarity dedup. Same reason.
  - Identity recovery. user_id is the slot reserved for true platform
    UIDs (currently unused, waiting for SDK integration).

Design invariants (DO NOT BREAK):
  - raw `username` is preserved in DB; this function only writes
    `normalized_username`. Debug "why did this person get cleaned to
    empty" 6 months from now still has source data.
  - Empty / too-short / pure-digit / anonymous-pattern → return None.
    Aggregation queries use `WHERE normalized_username IS NOT NULL`
    as the single rule for "valid for cardinality counting".
  - Adding new patterns is just appending to STRIP_PATTERNS — DO NOT
    add validator branches in the body of normalize_username.

Maintenance / drift detection:
  pipeline._step_danmaku emits a per-session line:
    [normalize][<sid>] raw=N invalid=X anonymous=Y valid=Z (samples: ...)
  If valid/N drops sharply across sessions (e.g. 80% → 30%), Douyin
  UI changed and STRIP_PATTERNS needs an update. The samples in the
  log give you the actual offending text without DB lookup.
"""
from __future__ import annotations

import re


# ----------------------------------------------------------------------
# UI-prefix / UI-token strip patterns.
#
# Each pattern is applied in order. They're all `re.sub` calls against
# the raw username string with empty replacement. Order matters: more-
# specific patterns first so they don't get partially eaten by greedy
# generic ones below.
#
# To extend: add a new (regex, comment) tuple. Test with the unit suite.
# To remove a noisy pattern: comment it out + add a test case so we
# remember why we removed it.
# ----------------------------------------------------------------------

STRIP_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Diamond / star / bullet user-level badge: "◆10", "◆ 13", "●3", "○ 7"
    # These prepend the badge of nearly every chatter. ANCHORED at start.
    (re.compile(r"^\s*[◆●○⬥◇✦★☆◊]\s*\d+\s*"),
     "diamond/badge prefix"),

    # 抖音 directive prefix "讲解中..." — UI labels for live state.
    # Match aggressively up to the next [◆●○] or end of string.
    (re.compile(r"^\s*讲解中[^◆●○]*"),
     "live-state label '讲解中'"),

    # "全部商品" tab label leak
    (re.compile(r"^\s*全部商品\s*"),
     "tab label '全部商品'"),

    # Product price chunk like "￥69.9 抢" — the "抢" button leaks into
    # rows whose layout has the price card on the left. The pattern
    # tolerates spaces between price and "抢".
    (re.compile(r"^\s*￥[\d.,]+\s*抢?\s*"),
     "product price chunk '￥XX 抢'"),

    # Standalone "(主播)" / "（主播）" / "主播" markers — the host's
    # own messages get tagged with this in the interaction list.
    # Strip the tag but keep the name on either side.
    (re.compile(r"[\(（]\s*主播\s*[\)）]\s*"),
     "(主播) host tag"),

    # 抖音商城 / 链接 / 福袋 / LIVE — common UI chrome words that get
    # OCR'd onto username row tops. Also catches promotional banner
    # text like "大促价" / "促销价" / "限时秒杀" that precedes a price
    # chunk on the row.
    (re.compile(r"^\s*(?:抖音商城|抖音电商|福袋|LIVE|直播间|关注|榜单|小时榜|礼物展馆|大促价|促销价|秒杀价|限时秒杀|限时|抢购|福利价|今日特价)\s*"),
     "common UI chrome word"),

    # Bracketed-content prefix: "【荣邦瑞明】片区" → " 片区".
    # Browser bookmark bar leaks (and Douyin promotional badges) often
    # appear as a complete 【...】 bracketed string before the actual
    # name. Strip the entire bracketed group, including contents.
    # Half-width [...] and full-width 「...」 handled by the same
    # pattern for completeness.
    (re.compile(r"^\s*[\[【「『]\s*[^\]】」』]*[\]】」』]\s*"),
     "bracketed-content prefix '【...】'"),

    # Stray brackets / parens / digits orphaned at the very start
    # AFTER all the above. e.g. "】 c*****" → "c*****",
    # "【荣邦瑞明】片区" → "片区" (the bookmark-bar leak case).
    # Includes BOTH half- and full-width opening AND closing brackets
    # because OCR can leave either side as a stray prefix.
    (re.compile(r"^\s*[\[\]【】\(\)（）\{\}「」『』《》<>«»\s\d/]+"),
     "leading orphan punctuation/digits"),
]


# Match anonymous Douyin "手机用户12345" pattern. Multi-person shared
# identifier (the platform assigns it to viewers who haven't set a
# nickname), so it must NOT count toward unique_speakers cardinality.
# Allow surrounding whitespace and accept Latin-digit variants.
ANONYMOUS_RE: re.Pattern = re.compile(r"^\s*手机用户\d+\s*$")


# Minimum length AFTER normalization. 1-character names are
# overwhelmingly OCR truncations (a partial Chinese name reduced to
# a single character) or noise. 2 is a defensible floor — real-world
# 1-char nicknames are rare and the false-positive cost of accepting
# all 1-char results is high.
MIN_USERNAME_LEN = 2


def normalize_username(raw: str | None) -> str | None:
    """Strip OCR-induced UI prefixes; classify invalid/anonymous → None.

    Args:
        raw: The OCR-captured username (the `username` column on
            danmakus). Can be None, empty, or a polluted string like
            "讲解中滋润双唇 ◆1 (主播) 唐***".

    Returns:
        Cleaned username if it passes validation, else None.

        None is the SINGLE signal for "do not count this in
        unique_speakers cardinality". Callers MUST aggregate with
        `WHERE normalized_username IS NOT NULL` rather than trying
        to inspect the cleaning outcome themselves — that would
        duplicate the validation logic across query sites.
    """
    if not raw:
        return None

    cleaned = raw

    # Apply strip patterns in fixed-point loop: some pollution forms
    # nest — e.g. "讲解中产品介绍 ◆10 小明" has the live-state prefix
    # SHIELDING the diamond badge from the anchored ^\s*[◆…]\d+ rule.
    # After step 2 strips "讲解中产品介绍 ", the diamond ends up at
    # the start and needs another pass through step 1 to remove.
    #
    # Termination is guaranteed: every pattern consumes ≥1 char on
    # match (the character classes / literals have no nullable
    # alternations), so cleaned strictly shrinks each iteration until
    # no pattern matches. Bounded retry as a belt-and-braces safety
    # net against future pattern additions.
    MAX_ITER = 8
    for _ in range(MAX_ITER):
        before = cleaned
        for pattern, _description in STRIP_PATTERNS:
            cleaned = pattern.sub("", cleaned)
        if cleaned == before:
            break

    # Final whitespace trim. Inner whitespace can be meaningful in
    # Chinese names (rare but possible — handle: "唐 唐" might be a
    # real nickname), so collapse internal whitespace gently.
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    # --- validation ---
    if not cleaned:
        return None
    if ANONYMOUS_RE.match(cleaned):
        # Anonymous platform-assigned identifier; multi-person shared,
        # not a real identity for cardinality.
        return None
    if len(cleaned) < MIN_USERNAME_LEN:
        # Too short — overwhelmingly OCR truncation.
        return None
    if cleaned.isdigit():
        # Pure digit means OCR caught a number (likely some badge count
        # or order-rank artifact) and lost everything else.
        return None

    return cleaned


def classify_outcome(raw: str | None, normalized: str | None) -> str:
    """Tag a (raw, normalized) pair with one of {valid, invalid, anonymous}.

    Used by pipeline._step_danmaku for the per-session monitoring log
    line. Keep this in sync with the actual control flow in
    normalize_username — if the function grows new None-returning
    branches, this needs corresponding cases.

    Returns:
        "valid"      — normalized is a usable username
        "anonymous"  — raw matches the anonymous pattern
        "invalid"    — anything else that normalized to None
                       (empty, too short, pure digits, OCR garbage
                       that fully stripped to empty)
    """
    if normalized is not None:
        return "valid"
    if not raw:
        return "invalid"
    # Check anonymous before invalid — anonymous IS a kind of invalid
    # for cardinality, but it's a more informative diagnostic label.
    cleaned_for_anon_check = raw.strip() if raw else ""
    if ANONYMOUS_RE.match(cleaned_for_anon_check):
        return "anonymous"
    return "invalid"
